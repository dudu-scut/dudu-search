"""
Query Processor for RAG — keyword expansion, decomposition, HyDE, metadata filtering.

All sub-modules share one LLM client. Each sub-module has independent error
handling — failure in one does not affect others or the base retrieval.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

from app.self_rag.config import (
    HYDE_ENABLED,
    LLM_API_KEY,
    LLM_BASE_URL,
    METADATA_FILTER_ENABLED,
    QUERY_DECOMPOSITION_ENABLED,
    QUERY_EXPANSION_ENABLED,
    QUERY_PROCESSOR_MODEL,
    QUERY_PROCESSOR_TIMEOUT,
)

logger = logging.getLogger(__name__)


@dataclass
class ProcessedQuery:
    """Result of query processing.

    Attributes:
        original: The original user query.
        expanded: Original query with expanded keywords appended (for embedding/BM25).
        sub_queries: Decomposed sub-queries (empty list if decomposition disabled/failed).
        hyde_text: Hypothetical answer text (empty string if HyDE disabled/failed).
        metadata_filter: ChromaDB ``where`` clause dict (``None`` if no filters extracted).
    """

    original: str
    expanded: str = ""
    sub_queries: list[str] = field(default_factory=list)
    hyde_text: str = ""
    metadata_filter: Optional[dict] = None


class QueryProcessor:
    """Processes user queries before retrieval.

    Four sub-modules, each independently toggle-able:

    * **KeywordExpander** — LLM extracts keywords/synonyms, appends to query.
    * **QueryDecomposer** — LLM judges complexity, splits into sub-queries.
    * **HyDEGenerator** — LLM generates hypothetical answer for embedding.
    * **MetadataFilter** — LLM extracts structured ChromaDB filter from query.

    Keyword expansion and metadata filtering are merged into one LLM call
    for efficiency when both are enabled.
    """

    def __init__(
        self,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self._model: str = model or QUERY_PROCESSOR_MODEL
        self._timeout: int = timeout or QUERY_PROCESSOR_TIMEOUT
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
        return self._client

    async def process(self, query: str) -> ProcessedQuery:
        """Run enabled sub-modules and return a :class:`ProcessedQuery`.

        Each sub-module runs with independent error handling.
        """
        result = ProcessedQuery(original=query, expanded=query)

        expand_task = None
        hyde_task = None
        decompose_task = None

        if QUERY_EXPANSION_ENABLED or METADATA_FILTER_ENABLED:
            expand_task = asyncio.create_task(
                self._expand_and_filter(query)
            )

        if HYDE_ENABLED:
            hyde_task = asyncio.create_task(self._generate_hyde(query))

        if QUERY_DECOMPOSITION_ENABLED:
            decompose_task = asyncio.create_task(self._decompose(query))

        if expand_task is not None:
            try:
                expanded, meta_filter = await expand_task
                result.expanded = expanded
                result.metadata_filter = meta_filter
            except Exception:
                logger.warning("Keyword expansion / metadata filter failed", exc_info=True)

        if hyde_task is not None:
            try:
                result.hyde_text = await hyde_task
            except Exception:
                logger.warning("HyDE generation failed", exc_info=True)

        if decompose_task is not None:
            try:
                result.sub_queries = await decompose_task
            except Exception:
                logger.warning("Query decomposition failed", exc_info=True)

        return result

    async def _expand_and_filter(self, query: str) -> tuple[str, Optional[dict]]:
        """Combined LLM call for keyword expansion + metadata filter extraction.

        Returns:
            (expanded_query, metadata_filter_dict_or_None)
        """
        prompt = (
            "你是一个查询分析助手。对用户问题做两件事：\n"
            "1. 提取关键词和同义词（逗号分隔，5-10个关键词）\n"
            "2. 如果问题中包含时间、文档类型、实体名称等过滤条件，提取出来\n\n"
            "请严格按以下JSON格式回复：\n"
            '{"keywords": "关键词1, 关键词2, ...", "filters": {"year": "2024", "doc_type": "report"}}'
            "\n\n"
            f"用户问题：{query}"
        )

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.client.chat.completions.create(
                        model=self._model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=200,
                    )
                ),
                timeout=self._timeout,
            )
            content = response.choices[0].message.content or "{}"
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            data = json.loads(content.strip())
        except Exception:
            logger.warning("Expand+filter LLM call failed", exc_info=True)
            return query, None

        keywords = data.get("keywords", "")
        filters = data.get("filters") if METADATA_FILTER_ENABLED else None

        if filters is not None and not filters:
            filters = None

        expanded = f"{query} {keywords}" if keywords else query
        return expanded.strip(), filters

    async def _generate_hyde(self, query: str) -> str:
        """Generate a hypothetical answer document for HyDE retrieval."""
        prompt = (
            "你是一个知识库助手。请根据以下问题，写一段假设性的答案（200-400字），"
            "用文档报告的风格来写，就像这个答案来自知识库中的一篇文档。\n\n"
            f"问题：{query}"
        )

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.client.chat.completions.create(
                        model=self._model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3,
                        max_tokens=500,
                    )
                ),
                timeout=self._timeout,
            )
            return response.choices[0].message.content or ""
        except Exception:
            logger.warning("HyDE generation LLM call failed", exc_info=True)
            return ""

    async def _decompose(self, query: str) -> list[str]:
        """Decompose a complex query into 2-3 sub-queries, or return empty list
        if the query is simple.
        """
        prompt = (
            "判断以下问题是否需要拆分成多个子问题来回答。如果需要拆分，返回2-3个子问题（每行一个）。"
            "如果问题很简单不需要拆分，返回空。\n\n"
            "需要拆分的情况：包含多个子问题、对比类问题、因果类问题、需要多角度回答的问题。\n\n"
            f"问题：{query}\n\n"
            "请按以下格式回复（不需要拆分的返回空行）：\n"
            "子问题1\n子问题2\n子问题3"
        )

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.client.chat.completions.create(
                        model=self._model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=200,
                    )
                ),
                timeout=self._timeout,
            )
            content = response.choices[0].message.content or ""
            lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
            return [l for l in lines if len(l) > 2 and l != query]
        except Exception:
            logger.warning("Query decomposition LLM call failed", exc_info=True)
            return []
