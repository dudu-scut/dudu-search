"""
PostgreSQL 连接池管理与 Schema 初始化。

提供 asyncpg 连接池的创建、关闭，以及启动时自动建表。
"""
import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

POSTGRES_URI = os.getenv(
    "POSTGRES_URI",
    "postgresql://deepagents:deepagents@localhost:5432/deepagents",
)

_pool: asyncpg.Pool | None = None
_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    """获取或创建 asyncpg 连接池（懒初始化 + 双重检查锁）。"""
    global _pool
    if _pool is not None:
        return _pool
    async with _lock:
        if _pool is not None:
            return _pool
        _pool = await asyncpg.create_pool(
            POSTGRES_URI,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        return _pool


async def close_pool() -> None:
    """关闭连接池。"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def init_schema() -> None:
    """初始化数据库表结构（幂等，使用 IF NOT EXISTS）。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 启用 pgvector 扩展
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

        # 会话表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                thread_id VARCHAR(64) UNIQUE NOT NULL,
                title VARCHAR(256),
                summary TEXT,
                status VARCHAR(32) DEFAULT 'running',
                user_id VARCHAR(64) DEFAULT 'default',
                metadata JSONB DEFAULT '{}',
                started_at TIMESTAMPTZ DEFAULT NOW(),
                completed_at TIMESTAMPTZ
            );
        """)

        # 对话消息表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY,
                thread_id VARCHAR(64) NOT NULL REFERENCES sessions(thread_id) ON DELETE CASCADE,
                role VARCHAR(32) NOT NULL,
                content TEXT,
                tool_calls JSONB,
                token_count INTEGER,
                message_metadata JSONB DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_thread
            ON messages(thread_id, created_at);
        """)

        # Agent 事件表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_events (
                id BIGSERIAL PRIMARY KEY,
                thread_id VARCHAR(64) NOT NULL,
                event_type VARCHAR(64) NOT NULL,
                message TEXT,
                payload JSONB DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_thread
            ON agent_events(thread_id, created_at);
        """)

        # 长期记忆表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS long_term_memories (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                memory_type VARCHAR(32) NOT NULL,
                content TEXT NOT NULL,
                embedding vector(1536),
                source_thread_id VARCHAR(64),
                importance FLOAT DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                last_accessed TIMESTAMPTZ,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_type
            ON long_term_memories(memory_type);
        """)

        print("[DB] Schema initialized successfully")
