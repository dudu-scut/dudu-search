"""定时清理过期会话。通过 ARQ cron job 执行。"""

from app.logging_config import get_logger
from app.config import settings

logger = get_logger("cleanup")


async def cleanup_expired_sessions():
    """删除超过保留期的已完成/失败/取消的会话，同时清理孤儿记忆。

    Returns:
        int: 删除的会话数量
    """
    from app.storage.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
      async with conn.transaction():
        result = await conn.execute(
            """
            DELETE FROM sessions
            WHERE status IN ('completed', 'failed', 'cancelled')
              AND updated_at < NOW() - INTERVAL '1 day' * $1
            """,
            settings.SESSION_RETENTION_DAYS,
        )

        # 解析删除行数
        deleted = int(result.split()[-1]) if result else 0
        if deleted > 0:
            logger.info(
                "过期会话已清理",
                deleted=deleted,
                retention_days=settings.SESSION_RETENTION_DAYS,
            )

        # 清理孤儿记忆：source_thread_id 指向已不存在的会话
        orphan_result = await conn.execute(
            """
            DELETE FROM long_term_memories
            WHERE source_thread_id IS NOT NULL
              AND source_thread_id NOT IN (SELECT thread_id FROM sessions)
            """
        )
        orphan_deleted = int(orphan_result.split()[-1]) if orphan_result else 0
        if orphan_deleted > 0:
            logger.info("孤儿记忆已清理", deleted=orphan_deleted)

        return deleted
