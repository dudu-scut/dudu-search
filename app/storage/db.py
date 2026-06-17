"""
PostgreSQL 连接池管理与 Schema 初始化。

提供 asyncpg 连接池的创建、关闭，以及启动时自动建表。
"""
import asyncio
from pathlib import Path

import asyncpg

from app.config import settings
from app.logging_config import get_logger

logger = get_logger("db")

_pool: asyncpg.Pool | None = None
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """懒初始化 Lock，避免模块加载时在事件循环外创建。"""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def get_pool() -> asyncpg.Pool:
    """获取或创建 asyncpg 连接池（懒初始化 + 双重检查锁）。"""
    global _pool
    if _pool is not None:
        return _pool
    async with _get_lock():
        if _pool is not None:
            return _pool
        _pool = await asyncpg.create_pool(
            settings.POSTGRES_URI,
            min_size=settings.DB_POOL_MIN_SIZE,
            max_size=settings.DB_POOL_MAX_SIZE,
            command_timeout=settings.DB_COMMAND_TIMEOUT,
        )
        return _pool


async def close_pool() -> None:
    """关闭连接池（加锁防止与 get_pool 竞态）。"""
    global _pool
    async with _get_lock():
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
                embedding vector(512),
                source_thread_id VARCHAR(64),
                user_id VARCHAR(64),
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

        # 迁移：确保 embedding 列维度为 512（修复旧数据库的 1536 维度问题）
        # 注意：旧版本会静默删除所有记忆数据，现在改为仅提示手动迁移
        await conn.execute("""
            DO $$
            DECLARE
                current_dim integer;
            BEGIN
                SELECT a.atttypmod INTO current_dim
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                WHERE c.relname = 'long_term_memories'
                AND a.attname = 'embedding'
                AND a.attnum > 0;

                -- vector(1536) has atttypmod = 1536 + 4 = 1540
                -- vector(512)  has atttypmod = 512  + 4 = 516
                IF current_dim IS NOT NULL AND current_dim > 0 AND current_dim != 516 THEN
                    RAISE WARNING 'Embedding dimension mismatch (current=%, expected=512). '
                        'Use SET embed_dim=%%; then DELETE FROM long_term_memories; '
                        'ALTER TABLE long_term_memories ALTER COLUMN embedding TYPE vector(%%); '
                        'to migrate manually.',
                        current_dim - 4;
                END IF;
            END $$;
        """)

        # 迁移：添加 sessions 巩固状态列（幂等）
        await conn.execute("""
            ALTER TABLE sessions ADD COLUMN IF NOT EXISTS consolidation_status VARCHAR(20) DEFAULT NULL;
        """)
        await conn.execute("""
            ALTER TABLE sessions ADD COLUMN IF NOT EXISTS consolidation_error TEXT DEFAULT NULL;
        """)
        await conn.execute("""
            ALTER TABLE sessions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
        """)

        # 迁移：为长期记忆表添加用户隔离列（幂等）
        await conn.execute("""
            ALTER TABLE long_term_memories ADD COLUMN IF NOT EXISTS user_id VARCHAR(64);
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_user_id
            ON long_term_memories(user_id);
        """)

        # 用户组表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_groups (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL,
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        # 用户表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(128) NOT NULL,
                email VARCHAR(255),
                group_id INTEGER REFERENCES user_groups(id) ON DELETE SET NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'user',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        # 迁移：users 表加 auth_source 列（幂等）
        await conn.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_source VARCHAR(10) NOT NULL DEFAULT 'local';
        """)

        # 迁移：sessions 表加 group_id（幂等）
        await conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'sessions' AND column_name = 'group_id'
                ) THEN
                    ALTER TABLE sessions ADD COLUMN group_id INTEGER REFERENCES user_groups(id);
                    UPDATE sessions SET group_id = 1 WHERE group_id IS NULL;
                END IF;
            END $$;
        """)

        # 创建默认用户组
        await conn.execute("""
            INSERT INTO user_groups (id, name, description)
            VALUES (1, '默认组', '系统默认用户组')
            ON CONFLICT (id) DO NOTHING;
        """)

        # 补充 agent_events 外键约束（幂等）
        await conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE constraint_name = 'fk_events_session'
                ) THEN
                    -- 先清理孤儿记录
                    DELETE FROM agent_events
                    WHERE thread_id NOT IN (SELECT thread_id FROM sessions);

                    ALTER TABLE agent_events
                    ADD CONSTRAINT fk_events_session
                    FOREIGN KEY (thread_id) REFERENCES sessions(thread_id)
                    ON DELETE CASCADE;
                END IF;
            END $$;
        """)

        logger.info("Schema 初始化完成")
