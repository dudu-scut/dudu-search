"""
记忆服务：统一管理短期/中期/长期记忆的存储、检索与巩固。

- 短期记忆：当前会话上下文注入
- 中期记忆：会话结束后自动摘要
- 长期记忆：跨会话事实提取 + pgvector 语义检索
"""
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.config import settings
from app.logging_config import get_logger

logger = get_logger("memory_service")

# embedding 维度：BAAI/bge-small-zh-v1.5 默认 512
EMBEDDING_DIM = settings.EMBEDDING_DIM


class MemoryService:
    """记忆服务：检索 + 写入 + 巩固。"""

    def __init__(self):
        self._embedding_model = None

    def _get_embedding_model(self):
        """懒加载 embedding 模型（复用 RAG 基础设施）。"""
        if self._embedding_model is None:
            from sentence_transformers import SentenceTransformer
            model_name = settings.EMBEDDING_MODEL
            self._embedding_model = SentenceTransformer(model_name)
        return self._embedding_model

    async def _embed(self, text: str) -> list[float]:
        """将文本转换为向量（在 executor 中运行，不阻塞事件循环）。"""
        import asyncio
        model = self._get_embedding_model()
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(None, model.encode, text)
        return embedding.tolist()

    # ---- 检索 ----

    async def retrieve_relevant(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.6,
    ) -> list[dict]:
        """语义检索最相关的长期记忆。"""
        try:
            query_embedding = await self._embed(query)
            from app.storage.db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, memory_type, content, importance,
                              1 - (embedding <=> $1) AS similarity
                       FROM long_term_memories
                       WHERE embedding IS NOT NULL
                         AND 1 - (embedding <=> $1) > $3
                       ORDER BY similarity DESC
                       LIMIT $2""",
                    json.dumps(query_embedding),
                    top_k,
                    threshold,
                )
                return [
                    {
                        "id": str(row["id"]),
                        "memory_type": row["memory_type"],
                        "content": row["content"],
                        "importance": row["importance"],
                        "similarity": row["similarity"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.warning("检索失败", exc_info=True)
            return []

    async def retrieve_recent_summaries(self, limit: int = 3) -> list[dict]:
        """获取最近会话摘要。"""
        try:
            from app.storage.db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT thread_id, title, summary, started_at
                       FROM sessions
                       WHERE summary IS NOT NULL
                       ORDER BY started_at DESC
                       LIMIT $1""",
                    limit,
                )
                return [
                    {
                        "thread_id": row["thread_id"],
                        "title": row["title"],
                        "summary": row["summary"],
                        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.warning("摘要检索失败", exc_info=True)
            return []

    # ---- 上下文构建 ----

    async def build_context(self, thread_id: str, user_message: str) -> str:
        """为新对话构建记忆上下文块，注入 Agent 系统提示词。"""
        parts = []

        # 1. 相关长期记忆
        relevant = await self.retrieve_relevant(user_message, top_k=5)
        if relevant:
            lines = ["## 相关历史记忆"]
            type_label_map = {
                "fact": "事实", "preference": "偏好",
                "episodic": "经历", "semantic": "知识",
            }
            for m in relevant:
                label = type_label_map.get(m["memory_type"], m["memory_type"])
                lines.append(f"- [{label}] {m['content']}")
            parts.append("\n".join(lines))

        # 2. 近期会话摘要
        recent = await self.retrieve_recent_summaries(limit=3)
        if recent:
            lines = ["## 近期对话摘要"]
            for s in recent:
                date_str = s["started_at"][:10] if s["started_at"] else "未知"
                title = s["title"] or s["thread_id"][:8]
                lines.append(f"- [{date_str}] {title}: {s['summary']}")
            parts.append("\n".join(lines))

        return "\n\n".join(parts) if parts else ""

    # ---- 写入 ----

    async def store_memory(
        self,
        content: str,
        memory_type: str = "fact",
        importance: float = 0.5,
        source_thread_id: str | None = None,
        metadata: dict | None = None,
    ) -> str | None:
        """存储一条长期记忆（含向量嵌入）。"""
        try:
            embedding = await self._embed(content)
            from app.storage.db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO long_term_memories
                       (memory_type, content, embedding, source_thread_id, importance, metadata)
                       VALUES ($1, $2, $3, $4, $5, $6)
                       RETURNING id""",
                    memory_type,
                    content,
                    json.dumps(embedding),
                    source_thread_id,
                    importance,
                    json.dumps(metadata or {}, ensure_ascii=False),
                )
                return str(row["id"]) if row else None
        except Exception as e:
            logger.warning("记忆存储失败", exc_info=True)
            return None

    async def consolidate_session(self, thread_id: str) -> dict:
        """会话记忆巩固：摘要 + 事实提取。

        从 messages 表中读取完整对话，调用 LLM 进行：
        1. 会话摘要
        2. 关键事实提取
        返回: {"summary": str, "facts": list[dict], "title": str}
        """
        try:
            from app.storage.db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT role, content FROM messages "
                    "WHERE thread_id = $1 ORDER BY created_at ASC",
                    thread_id,
                )

            if not rows:
                return {"summary": "", "facts": [], "title": ""}

            conversation = "\n".join(
                f"[{row['role']}]: {row['content'] or ''}" for row in rows
            )

            # 调用 LLM 进行摘要和事实提取
            summary, title, facts = await self._llm_extract(conversation)

            # 存储摘要到 sessions 表
            if summary or title:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE sessions SET summary = $1, title = $2 WHERE thread_id = $3",
                        summary, title, thread_id,
                    )

            # 存储提取的事实到长期记忆
            for fact in facts:
                await self.store_memory(
                    content=fact.get("content", ""),
                    memory_type=fact.get("type", "fact"),
                    importance=fact.get("importance", 0.5),
                    source_thread_id=thread_id,
                    metadata=fact.get("metadata", {}),
                )

            return {"summary": summary or "", "title": title or "", "facts": facts}

        except Exception as e:
            logger.warning("会话巩固失败", thread_id=thread_id, exc_info=True)
            # 标记 session 巩固状态为失败
            try:
                await self._mark_consolidation_failed(thread_id, str(e))
            except Exception:
                logger.warning("无法更新巩固失败状态", exc_info=True)
            # 不抛出，巩固失败不影响主流程
            return {"summary": "", "facts": [], "title": ""}

    async def _mark_consolidation_failed(self, thread_id: str, error_msg: str) -> None:
        """标记会话记忆巩固失败。"""
        from app.storage.db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE sessions
                SET consolidation_status = 'failed',
                    consolidation_error = $2,
                    updated_at = NOW()
                WHERE thread_id = $1
                """,
                thread_id,
                error_msg[:500],
            )

    async def _llm_extract(self, conversation: str) -> tuple[str | None, str | None, list[dict]]:
        """调用 LLM 提取摘要和事实（使用项目中已有的 model）。"""
        from app.agent.llm import model

        # 截断对话内容，保留最后 N 字符以确保不超出模型上下文窗口
        max_conv_chars = 8000
        truncated = conversation if len(conversation) <= max_conv_chars else "…[对话较长，仅展示尾部]…\n" + conversation[-max_conv_chars:]

        prompt = f"""分析以下对话，完成三个任务：

1. 生成一句标题（不超过20字）
2. 生成一段摘要（100-200字，概括对话核心内容）
3. 提取值得长期记住的关键事实（用户偏好、重要决策、新知识等）

对话内容：
---
{truncated}
---

请严格按照以下 JSON 格式输出（不要输出其他内容）：
{{"title": "标题", "summary": "摘要", "facts": [{{"content": "事实内容", "type": "fact|preference|episodic|semantic", "importance": 0.0-1.0}}]}}"""

        try:
            response = await model.ainvoke(prompt)
            text = response.content if hasattr(response, "content") else str(response)
            # 提取 JSON 块
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                result = json.loads(match.group())
                return (
                    result.get("title"),
                    result.get("summary"),
                    result.get("facts", []),
                )
        except Exception as e:
            logger.warning("LLM 提取失败", exc_info=True)

        return None, None, []


# 单例
_memory_service: MemoryService | None = None


def get_memory_service() -> MemoryService:
    """获取 MemoryService 单例。"""
    global _memory_service
    if _memory_service is None:
        _memory_service = MemoryService()
    return _memory_service
