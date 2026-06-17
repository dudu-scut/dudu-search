"""ARQ Worker — 异步任务队列执行器。

启动方式:
    uv run arq app.worker.WorkerSettings

多 Worker 部署:
    可以同时启动多个 Worker 进程（甚至跨机器），它们共享同一个 Redis 队列，
    ARQ 自动分配任务。Worker 通过 Redis 心跳上报状态，Server 提供
    /api/workers/health 端点查询在线 Worker 列表。
"""

from arq.connections import RedisSettings
from arq.cron import CronJob
from app.config import settings as app_settings
from app.logging_config import get_logger
from app.tasks.cleanup import cleanup_expired_sessions

logger = get_logger("worker")


async def startup(ctx):
    """Worker 启动时初始化资源。"""
    from app.storage.redis_client import get_worker_id
    logger.info("Worker 启动中...", worker_id=get_worker_id())


async def shutdown(ctx):
    """Worker 关闭时清理资源。"""
    from app.agent.main_agent import close_checkpointer
    await close_checkpointer()
    logger.info("Worker 关闭")


async def _heartbeat_cron(ctx):
    """定期上报 Worker 心跳到 Redis，供 Server 端健康检查查询。"""
    from app.storage.redis_client import update_worker_heartbeat
    worker = ctx.get("worker")
    active = len(worker.job_tasks) if worker else 0
    await update_worker_heartbeat(active_jobs=active)


async def run_agent_task(ctx, query: str, thread_id: str, user_id: str, group_id: int):
    """后台执行 Agent 任务。

    Args:
        query: 用户查询
        thread_id: 会话 ID
        user_id: 用户 UUID
        group_id: 用户组 ID
    """
    from app.api.context import (
        generate_trace_id,
        reset_worker_context,
        set_current_group_id,
        set_current_user_id,
        set_session_context,
        set_thread_context,
    )
    from app.storage.db import get_pool
    from app.logging_config import get_logger
    from app.metrics import ACTIVE_TASKS, TASK_DURATION, TASK_TOTAL

    task_logger = get_logger("agent_task").bind(thread_id=thread_id)
    task_logger.info("任务开始执行", query=query[:100])

    # 设置上下文并保存 token，finally 中统一 reset 防止跨任务泄漏
    _, trace_token = generate_trace_id()
    user_token = set_current_user_id(user_id)
    group_token = set_current_group_id(group_id)
    session_token = set_session_context(thread_id)
    thread_token = set_thread_context(thread_id)
    ctx_tokens = {
        "trace_id": trace_token,
        "user_id": user_token,
        "group_id": group_token,
        "session_dir": session_token,
        "thread_id": thread_token,
    }

    import asyncio as _asyncio

    async def _check_cancelled():
        """轮询 Redis 取消信号。"""
        from app.storage.redis_client import get_redis_client
        redis = await get_redis_client()
        while True:
            cancelled = await redis.get(f"cancel:{thread_id}")
            if cancelled:
                return True
            await _asyncio.sleep(0.5)

    TASK_TOTAL.labels(status="started").inc()
    ACTIVE_TASKS.inc()

    # 创建链路追踪根 span — 覆盖整个任务生命周期
    from app.tracing import get_tracer
    from opentelemetry.trace import StatusCode
    tracer = get_tracer("worker")

    with tracer.start_as_current_span("agent_task") as root_span:
        root_span.set_attribute("task.thread_id", thread_id)
        root_span.set_attribute("task.user_id", user_id)
        root_span.set_attribute("task.group_id", group_id)
        root_span.set_attribute("task.query_preview", query[:100])

        with TASK_DURATION.time():
            cancel_event = _asyncio.Event()

            async def _watch_cancel():
                if await _check_cancelled():
                    cancel_event.set()

            cancel_watcher = _asyncio.create_task(_watch_cancel())

            try:
                # 更新状态为 running
                pool = await get_pool()
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE sessions SET status = 'running' WHERE thread_id = $1",
                        thread_id,
                    )

                # 启动前再次检查取消信号
                from app.storage.redis_client import get_redis_client
                redis = await get_redis_client()
                if await redis.get(f"cancel:{thread_id}"):
                    raise _asyncio.CancelledError("任务已被用户取消")

                # 执行 Agent（带取消监听）
                from app.agent.main_agent import run_deep_agent
                agent_task = _asyncio.create_task(
                    run_deep_agent(query, thread_id, group_id=group_id, user_id=user_id)
                )

                # 等待 agent 完成或取消信号
                cancel_waiter = _asyncio.create_task(cancel_event.wait())
                done, pending = await _asyncio.wait(
                    [agent_task, cancel_waiter],
                    return_when=_asyncio.FIRST_COMPLETED,
                )

                # 取消信号先到达 → 取消 agent
                if cancel_event.is_set():
                    agent_task.cancel()
                    try:
                        await agent_task
                    except _asyncio.CancelledError:
                        pass
                    raise _asyncio.CancelledError("任务已被用户取消")

                # agent 先完成 → 清理 cancel_waiter，避免任务泄漏
                if not cancel_waiter.done():
                    cancel_waiter.cancel()
                    try:
                        await cancel_waiter
                    except _asyncio.CancelledError:
                        pass

                # agent 正常完成
                await agent_task  # 获取可能的异常

                # 标记完成
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE sessions SET status = 'completed' WHERE thread_id = $1",
                        thread_id,
                    )
                TASK_TOTAL.labels(status="completed").inc()
                root_span.set_attribute("task.status", "completed")
                task_logger.info("任务执行完成")

            except _asyncio.CancelledError as e:
                TASK_TOTAL.labels(status="cancelled").inc()
                root_span.set_attribute("task.status", "cancelled")
                root_span.set_status(StatusCode.OK, "任务已取消")
                task_logger.info("任务已被取消", reason=str(e))
                try:
                    pool = await get_pool()
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE sessions SET status = 'cancelled' WHERE thread_id = $1",
                            thread_id,
                        )
                except Exception:
                    task_logger.error("无法更新任务取消状态")

            except Exception as e:
                TASK_TOTAL.labels(status="failed").inc()
                root_span.set_attribute("task.status", "failed")
                root_span.set_status(StatusCode.ERROR, str(e))
                root_span.record_exception(e)
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
                cancel_watcher.cancel()
                ACTIVE_TASKS.dec()
                # 释放用户并发配额
                try:
                    from app.storage.redis_client import get_redis_client
                    redis = await get_redis_client()
                    await redis.decr(f"user_tasks:{user_id}")
                    # 清理取消信号
                    await redis.delete(f"cancel:{thread_id}")
                    await redis.delete(f"task_job:{thread_id}")
                except Exception:
                    pass
                # 重置所有 ContextVar，防止跨任务上下文泄漏
                reset_worker_context(ctx_tokens)


# ── ARQ Worker 配置 ──

class WorkerSettings:
    """ARQ Worker 配置。

    通过环境变量覆盖:
        REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB
        WORKER_MAX_JOBS (单 Worker 并发数)

    多 Worker 部署:
        启动多个 `uv run arq app.worker.WorkerSettings` 进程即可。
        ARQ 会自动在共享队列上分配任务，CronJob(unique=True) 防止重复执行。
    """

    functions = [run_agent_task]

    redis_settings = RedisSettings(
        host=app_settings.REDIS_HOST,
        port=app_settings.REDIS_PORT,
        password=app_settings.REDIS_PASSWORD,
        database=app_settings.REDIS_DB,
    )

    # 并发与超时
    max_jobs = app_settings.WORKER_MAX_JOBS
    job_timeout = app_settings.TASK_TIMEOUT_SECONDS  # 单任务超时 300s
    keep_result = 3600  # 结果保留 1 小时
    max_tries = 3  # 最多执行 3 次（1 次原始 + 2 次重试）
    retry_jitter = False
    health_check_interval = 10

    # 定时任务
    cron_jobs = [
        # Worker 心跳上报（每 10 秒），Server 通过 /api/workers/health 查询
        CronJob(
            name="worker_heartbeat",
            coroutine=_heartbeat_cron,
            month=None,
            day=None,
            weekday=None,
            hour=None,
            minute=None,
            second={0, 10, 20, 30, 40, 50},
            microsecond=0,
            run_at_startup=True,
            unique=False,  # 每个 Worker 各自执行
            job_id=None,
            timeout_s=5,
            keep_result_s=None,
            keep_result_forever=None,
            max_tries=1,
        ),
        # 每天凌晨 3 点执行过期会话清理（unique=True，多 Worker 只有一个执行）
        CronJob(
            name="cleanup_expired_sessions",
            coroutine=cleanup_expired_sessions,
            month=None,
            day=None,
            weekday=None,
            hour=3,
            minute=0,
            second=0,
            microsecond=0,
            run_at_startup=False,
            unique=True,
            job_id=None,
            timeout_s=None,
            keep_result_s=None,
            keep_result_forever=None,
            max_tries=None,
        ),
    ]

    # 启动和关闭
    on_startup = startup
    on_shutdown = shutdown
