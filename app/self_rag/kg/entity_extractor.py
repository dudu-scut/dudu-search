"""LLM-driven entity and relation extraction from document chunks."""

import asyncio
import json
import logging
from typing import Optional

from openai import OpenAI

from app.self_rag.config import (
    KG_EXTRACT_MODEL,
    KG_MAX_ENTITIES_PER_CHUNK,
    LLM_API_KEY,
    LLM_BASE_URL,
)

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = (
    "你是一个知识图谱构建专家。从以下文档片段中抽取实体和关系。\n\n"
    "要求：\n"
    "- 实体类型包括但不限于：人物、公司、产品、技术、概念、事件、地点、时间\n"
    "- 关系包括但不限于：属于、创建、收购、合作、竞争、影响、包含、位于\n"
    "- 最多抽取{max_entities}个实体\n\n"
    "请严格按以下JSON格式输出（不要加任何其他文字）：\n"
    '{{"entities":[{{"name":"实体名","type":"类型","attributes":{{"属性":"值"}}}}],'
    '"relations":[{{"subject":"主体","predicate":"关系","object":"客体"}}]}}\n\n'
    "文档片段：\n{text}"
)

ENTITIES_ONLY_PROMPT = (
    "从以下问题中提取提到的实体名称（人名、公司名、产品名、概念等）。\n"
    "请严格按以下JSON格式输出：\n"
    '{{"entities":["实体1","实体2"]}}\n\n'
    "问题：{query}"
)


class EntityExtractor:
    """Extracts entities and relations from text using LLM."""

    def __init__(self, model: str | None = None) -> None:
        self._model: str = model or KG_EXTRACT_MODEL
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
        return self._client

    async def extract_from_chunk(self, text: str) -> dict:
        """Extract entities and relations from a document chunk.

        Returns:
            Dict with ``"entities"`` and ``"relations"`` keys, or empty dict on failure.
        """
        prompt = EXTRACT_PROMPT.format(
            max_entities=KG_MAX_ENTITIES_PER_CHUNK,
            text=text[:3000],
        )
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.client.chat.completions.create(
                        model=self._model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=1000,
                    )
                ),
                timeout=10.0,
            )
            content = response.choices[0].message.content or "{}"
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            return json.loads(content.strip())
        except Exception:
            logger.warning("Entity extraction failed for chunk", exc_info=True)
            return {"entities": [], "relations": []}

    async def extract_from_query(self, query: str) -> list[str]:
        """Extract entity names from a query for entity linking.

        Returns:
            List of entity name strings.
        """
        prompt = ENTITIES_ONLY_PROMPT.format(query=query)
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.client.chat.completions.create(
                        model=self._model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.0,
                        max_tokens=200,
                    )
                ),
                timeout=5.0,
            )
            content = response.choices[0].message.content or "{}"
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            data = json.loads(content.strip())
            return data.get("entities", [])
        except Exception:
            logger.warning("Query entity extraction failed", exc_info=True)
            return []
