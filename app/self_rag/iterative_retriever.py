"""
Iterative Retriever — Self-RAG style retrieval with sufficiency judgment.

After initial retrieval, an LLM judge evaluates whether results are sufficient.
If not, the query is rewritten and retrieval is retried (up to MAX_ROUNDS).
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

from app.self_rag.config import (
    ITERATIVE_MAX_ROUNDS,
    ITERATIVE_OVERALL_TIMEOUT,
    ITERATIVE_SUFFICIENCY_MIN_SCORE,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
)

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """Result from iterative retrieval.

    Attributes:
        parent_ids: Final list of parent document IDs.
        rounds: Number of retrieval rounds executed.
        sufficient: Whether the final results were judged sufficient.
        retrieval_log: List of per-round diagnostics.
    """

    parent_ids: list[str] = field(default_factory=list)
    rounds: int = 0
    sufficient: bool = True
    retrieval_log: list[dict] = field(default_factory=list)


class IterativeRetriever:
    """Wraps retrieval with sufficiency judgment and automatic query rewriting.

    Flow::

        Retrieve → Judge → [sufficient] → Return
                        → [insufficient] → Rewrite → Retrieve → ...

    Hard cap at ``MAX_ROUNDS``. On any LLM failure in the judge/rewrite
    steps, treats results as sufficient to avoid infinite loops.
    """

    JUDGE_PROMPT = (
        "你是一个检索质量评判专家。请根据以下信息判断检索结果是否充分回答了用户问题。\n\n"
        "评判维度（每项1-5分）：\n"
        "1. 相关性：检索到的片段与问题相关吗？\n"
        "2. 完整性：检索结果覆盖了问题的所有方面吗？\n"
        "3. 信息量：检索到的内容包含足够的细节吗？\n\n"
        "请严格按以下JSON格式回复：\n"
        '{{"relevance": 4, "completeness": 3, "informativeness": 4, '
        '"sufficient": true, "reason": "理由", "rewrite_suggestion": ""}}\n\n'
        "如果任一维度低于{min_score}分，sufficient应为false，并提供rewrite_suggestion。"
    )

    REWRITE_PROMPT = (
        "原始查询没有检索到足够的信息，请改写查询以获得更好的检索结果。\n\n"
        "原始查询：{original_query}\n"
        "检索到的不完整信息：{retrieved_snippets}\n"
        "改写原因：{reason}\n"
        "改写建议方向：{suggestion}\n\n"
        "请输出改写后的查询（只输出查询文本，不要加其他内容）："
    )

    def __init__(self, max_rounds: int | None = None) -> None:
        self._max_rounds: int = max_rounds or ITERATIVE_MAX_ROUNDS
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=30)
        return self._client

    async def retrieve_with_judgment(
        self,
        query: str,
        do_retrieve,  # async callable: (str) -> list[str]
        do_get_texts,  # callable: (list[str]) -> list[str]
    ) -> RetrievalResult:
        """Run iterative retrieval with sufficiency judgment.

        Args:
            query: Original user query.
            do_retrieve: Async callable that takes a query string and returns
                a list of parent IDs.
            do_get_texts: Sync callable that takes a list of parent IDs and
                returns their full text.
        """
        result = RetrievalResult()

        current_query = query
        all_ids: list[str] = []
        all_snippets: list[str] = []
        seen_ids: set[str] = set()

        async def _run_loop() -> None:
            nonlocal current_query
            for round_num in range(1, self._max_rounds + 1):
                result.rounds = round_num

                ids = await do_retrieve(current_query)
                new_ids = [i for i in ids if i not in seen_ids]
                if new_ids:
                    texts = do_get_texts(new_ids)
                else:
                    texts = []

                all_ids.extend(new_ids)
                all_snippets.extend(texts)
                for i in new_ids:
                    seen_ids.add(i)

                try:
                    judgment = await self._judge(query, all_snippets[:10])
                except Exception:
                    logger.warning("Judge LLM call failed — treating as sufficient", exc_info=True)
                    result.sufficient = True
                    break

                result.retrieval_log.append({
                    "round": round_num,
                    "query": current_query,
                    "new_ids": new_ids,
                    "judgment": judgment,
                })

                if judgment.get("sufficient", True):
                    result.sufficient = True
                    break

                if round_num < self._max_rounds:
                    try:
                        current_query = await self._rewrite(
                            original_query=query,
                            retrieved_snippets="\n".join(all_snippets[:5]),
                            reason=judgment.get("reason", ""),
                            suggestion=judgment.get("rewrite_suggestion", ""),
                        )
                    except Exception:
                        logger.warning("Rewrite LLM call failed", exc_info=True)
                        result.sufficient = False
                        break
                else:
                    result.sufficient = False
                    logger.info(
                        "Max rounds reached without sufficient results",
                        rounds=round_num,
                    )

        try:
            await asyncio.wait_for(_run_loop(), timeout=ITERATIVE_OVERALL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(
                "Iterative retrieval timed out",
                timeout=ITERATIVE_OVERALL_TIMEOUT,
                rounds_completed=result.rounds,
            )
            result.sufficient = False

        result.parent_ids = all_ids
        return result

    async def _judge(self, query: str, snippets: list[str]) -> dict:
        """LLM judge evaluates retrieval sufficiency."""
        snippets_text = "\n---\n".join(
            f"[{i+1}] {s}" for i, s in enumerate(snippets)
        )
        prompt = self.JUDGE_PROMPT.format(min_score=ITERATIVE_SUFFICIENCY_MIN_SCORE)
        prompt += f"\n\n用户问题：{query}\n检索结果：\n{snippets_text}"

        response = await asyncio.to_thread(
            lambda: self.client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=300,
            )
        )
        content = response.choices[0].message.content or "{}"
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            return {"sufficient": True, "reason": "JSON parse failed"}

    async def _rewrite(
        self,
        original_query: str,
        retrieved_snippets: str,
        reason: str,
        suggestion: str,
    ) -> str:
        """LLM rewrites query for better retrieval."""
        prompt = self.REWRITE_PROMPT.format(
            original_query=original_query,
            retrieved_snippets=retrieved_snippets,
            reason=reason,
            suggestion=suggestion,
        )

        response = await asyncio.to_thread(
            lambda: self.client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
            )
        )
        return (response.choices[0].message.content or original_query).strip()
