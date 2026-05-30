"""定时清理过期会话。通过 ARQ cron job 执行。"""

from app.logging_config import get_logger
from app.config import settings

logger = get_logger("cleanup")


async def cleanup_expired_sessions():
    """删除超过保留期的已完成/失败/取消的会话。

    Returns:
        int: 删除的会话数量
    """
    from app.storage.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
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

        return deleted
