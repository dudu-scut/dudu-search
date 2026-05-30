"""ARQ Worker — 异步任务队列执行器。

启动方式:
    uv run arq app.worker.WorkerSettings
"""

from arq.connections import RedisSettings
from arq.worker import create_worker
from app.config import settings as app_settings
from app.logging_config import get_logger

logger = get_logger("worker")


async def startup(ctx):
    """Worker 启动时初始化资源。"""
    logger.info("Worker 启动中...")


async def shutdown(ctx):
    """Worker 关闭时清理资源。"""
    logger.info("Worker 关闭")


async def run_agent_task(ctx, query: str, thread_id: str, user_id: str, group_id: int):
    """后台执行 Agent 任务。

    Args:
        query: 用户查询
        thread_id: 会话 ID
        user_id: 用户 UUID
        group_id: 用户组 ID
    """
    from app.api.context import generate_trace_id, set_current_user_id, set_current_group_id, set_session_context
    from app.storage.db import get_pool
    from app.logging_config import get_logger

    task_logger = get_logger("agent_task").bind(thread_id=thread_id)
    task_logger.info("任务开始执行", query=query[:100])

    # 设置上下文
    generate_trace_id()
    set_current_user_id(user_id)
    set_current_group_id(group_id)
    set_session_context(thread_id)

    try:
        # 更新状态为 running
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE sessions SET status = 'running' WHERE thread_id = $1",
                thread_id,
            )

        # 执行 Agent
        from app.agent.main_agent import run_deep_agent
        await run_deep_agent(query, thread_id)

        # 标记完成
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE sessions SET status = 'completed' WHERE thread_id = $1",
                thread_id,
            )
        task_logger.info("任务执行完成")

    except Exception as e:
        task_logger.error("任务执行失败", exc_info=True)
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE sessions SET status = 'failed' WHERE thread_id = $1",
                    thread_id,
                )
        except Exception:
            task_logger.error("无法更新任务失败状态")
        raise  # ARQ 会根据 max_tries 决定是否重试
    finally:
        # 释放用户并发配额
        try:
            from app.storage.redis_client import get_redis_client
            redis = await get_redis_client()
            await redis.decr(f"user_tasks:{user_id}")
        except Exception:
            pass


# ── ARQ Worker 配置 ──

class WorkerSettings:
    """ARQ Worker 配置。

    通过环境变量覆盖:
        REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB
    """

    functions = [run_agent_task]

    redis_settings = RedisSettings(
        host=app_settings.REDIS_HOST,
        port=app_settings.REDIS_PORT,
        password=app_settings.REDIS_PASSWORD,
        database=app_settings.REDIS_DB,
    )

    # 并发与超时
    max_jobs = 10  # 同时最多 10 个任务
    job_timeout = app_settings.TASK_TIMEOUT_SECONDS  # 单任务超时 300s
    keep_result = 3600  # 结果保留 1 小时
    max_tries = 3  # 最多执行 3 次（1 次原始 + 2 次重试）
    retry_jitter = False
    health_check_interval = 10

    # 启动和关闭
    on_startup = startup
    on_shutdown = shutdown
