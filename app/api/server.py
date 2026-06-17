"""
FastAPI 接口层与项目闭环入口

负责承接前端的任务提交、任务取消、文件上传/下载、输出文件列表查询和
WebSocket 长连接。HTTP 接口只做轻量调度，真正的 DeepAgents 执行放到后台
任务中；执行进度、工具调用和最终结果由 monitor 按 thread_id 推送给前端。
"""

import asyncio
import json
import mimetypes
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import List, Optional

import jwt as pyjwt
import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from arq import create_pool
from arq.connections import RedisSettings

from app.agent.main_agent import run_deep_agent
from app.api.monitor import manager
from app.auth.dependencies import UserInfo, get_current_user, get_group_filter, require_admin
from app.auth.jwt import create_access_token, decode_token, hash_password, verify_password
from app.auth.ldap_client import LDAPClient
from app.auth.oidc_client import OIDCClient
from app.config import settings
from app.exceptions import AuthError, DeepAgentsError, PermissionDeniedError, ValidationError
from app.logging_config import get_logger, setup_logging
from app.self_rag.config import DOC_STORE_DIR
from app.self_rag.engine import get_rag_engine
from app.storage.redis_client import (
    get_redis,
    register_active_task,
    unregister_active_task,
    get_active_task_ids,
    publish_session_event,
    publish_file_event,
    subscribe_sse_sessions,
    subscribe_sse_files,
)
from app.storage.db import get_pool, init_schema, close_pool
from app.storage.storage import get_output_storage, get_upload_storage, get_doc_storage
from app.tracing import init_tracing, shutdown_tracing, TracingMiddleware

logger = get_logger("server")


def _log_task_exception(task: asyncio.Task) -> None:
    """asyncio Task 异常回调：记录 fire-and-forget 任务中未捕获的异常。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.warning("后台任务异常", error=str(exc), exc_info=(type(exc), exc, exc.__traceback__))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    服务生命周期入口。

    启动时绑定当前事件循环到 WebSocket 管理器，确保后台 Agent 任务可以把
    monitor 事件投递回 FastAPI 所在的 loop。
    """
    # 初始化结构化日志
    setup_logging(settings.LOG_FORMAT)
    logger.info("应用启动中...", version=settings.APP_VERSION)

    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    logger.info("WebSocket Manager 已绑定到事件循环", loop_id=id(loop))

    # 初始化存储层
    await init_schema()
    redis = await get_redis()
    logger.info("存储层初始化完成", components="PostgreSQL+Redis")

    # 初始化 ARQ 任务队列客户端
    arq_client = await create_pool(RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        database=settings.REDIS_DB,
    ))
    _app.state.arq_client = arq_client
    logger.info("ARQ 任务队列客户端已初始化")

    # 初始化 OpenTelemetry 链路追踪（OTEL_ENABLED=False 时自动跳过）
    tracing_enabled = init_tracing(settings)
    if tracing_enabled:
        logger.info("OpenTelemetry 链路追踪已启用", endpoint=settings.OTEL_EXPORTER_ENDPOINT)

    yield  # 服务运行中

    # 服务关闭时：清理资源
    logger.info("正在关闭服务...")

    # 1. 取消所有正在执行的后台任务（通过 Redis 信号 + ARQ job abort）
    active_ids = []
    try:
        active_ids = await get_active_task_ids()
    except Exception:
        pass
    logger.info("正在取消活跃任务", count=len(active_ids))
    for thread_id in active_ids:
        # 通过 Redis 取消信号通知 Worker
        try:
            await redis.set(f"cancel:{thread_id}", "1", ex=60)
        except Exception:
            pass
        # 尝试通过 ARQ 中止任务
        try:
            arq_client = _app.state.arq_client
            if arq_client:
                job_id = await redis.get(f"task_job:{thread_id}")
                if job_id:
                    job = await arq_client.get_job(job_id)
                    if job:
                        await job.abort()
        except Exception:
            pass

    # 2. 清理本地任务注册表
    active_tasks.clear()

    # 3. 关闭所有 WebSocket 连接
    try:
        await manager.disconnect_all()
    except Exception as e:
        logger.warning("关闭 WebSocket 连接失败", exc_info=True)

    # 4. 关闭 ARQ 客户端
    try:
        arq_client = _app.state.arq_client
        if arq_client:
            await arq_client.close()
            logger.info("ARQ 客户端已关闭")
    except Exception as e:
        logger.warning("关闭 ARQ 客户端失败", exc_info=True)

    # 5. 关闭存储连接
    try:
        await close_pool()
    except Exception as e:
        logger.warning("关闭 PostgreSQL 连接池失败", exc_info=True)
    try:
        from app.storage.redis_client import close_redis
        await close_redis()
    except Exception as e:
        logger.warning("关闭 Redis 连接失败", exc_info=True)

    # 6. 关闭 OpenTelemetry（刷新缓冲的 span）
    try:
        await shutdown_tracing()
    except Exception:
        pass

    logger.info("服务已关闭")


# 当前文件位于 app/api/server.py，运行时目录统一收敛到 app 目录
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent

app = FastAPI(title="DeepAgents API", lifespan=lifespan)


# ── 统一异常处理 ──

@app.exception_handler(DeepAgentsError)
async def deepagents_exception_handler(request: Request, exc: DeepAgentsError):
    """统一处理所有 DeepAgents 异常，返回结构化 JSON。"""
    logger.warning("业务异常", code=exc.code, message=exc.message)
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """兜底处理未捕获的异常，不泄露内部细节。"""
    logger.error("未处理异常", exc_type=type(exc).__name__, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "服务器内部错误，请稍后重试",
                "details": {},
            }
        },
    )


# ── 任务管理 ──

# 保存 thread_id -> 后台 Agent 任务，用于同一会话任务替换和主动取消
active_tasks: dict[str, asyncio.Task] = {}

# output 保存每个会话最终工作区，前端只允许从这里浏览和下载生成文件
output_dir = project_root / "output"
output_dir.mkdir(exist_ok=True)

# updated 暂存用户上传文件，run_deep_agent 启动时会复制到对应 output/session_xxx
updated_dir = project_root / "updated"
updated_dir.mkdir(exist_ok=True)

# 教学项目通常前后端分别本地启动，这里根据配置收紧跨域
origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ── Trace ID 中间件 ──

@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    """每个请求入站时生成 trace_id 并写入 ContextVar，便于全链路日志串联。"""
    from app.api.context import generate_trace_id, set_current_user_id
    generate_trace_id()
    response = await call_next(request)
    return response


# ── OpenTelemetry 链路追踪中间件 ──
# 必须在 trace_id_middleware 之后注册，这样 app.trace_id 才能注入到 span 属性中

app.add_middleware(TracingMiddleware)


# ── 全局 QPS 限流中间件 ──

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """基于 Redis 滑动窗口的全局 QPS 限流。

    跳过: /api/health, /live, /ready, /metrics, /ws, OPTIONS 请求
    """
    path = request.url.path

    # 跳过不需要限流的路径
    if path in ("/api/health", "/live", "/ready", "/metrics", "/api/workers/health"):
        return await call_next(request)
    if path.startswith("/ws") or path.startswith("/api/events") or path.startswith("/api/shared") or request.method == "OPTIONS":
        return await call_next(request)

    try:
        from app.storage.redis_client import get_redis_client
        redis = await get_redis_client()
        now_ms = int(time.time() * 1000)
        window_ms = 1000  # 1 秒窗口
        max_req_per_window = 100  # 每秒最多 100 请求

        key = "global_qps"
        # 移除窗口外的旧记录
        await redis.zremrangebyscore(key, 0, now_ms - window_ms)
        # 统计当前窗口内的请求数
        count = await redis.zcard(key)

        if count >= max_req_per_window:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "请求过于频繁，请稍后重试",
                        "details": {"retry_after": 1},
                    }
                },
                headers={"Retry-After": "1"},
            )

        # 记录当前请求
        await redis.zadd(key, {f"{now_ms}:{uuid.uuid4().hex[:8]}": now_ms})
        await redis.expire(key, 2)

    except Exception:
        # Redis 不可用时跳过限流，不阻塞业务
        pass

    return await call_next(request)


# ── HTTP 请求指标中间件 ──

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """HTTP 请求指标收集（Prometheus）。"""
    from app.metrics import HTTP_REQUEST_DURATION, HTTP_REQUEST_TOTAL

    start = time.time()
    response = await call_next(request)
    duration = time.time() - start

    path = request.url.path
    # 泛化路径（将 ID 替换为 {id} 避免指标基数爆炸）
    # 1. UUID（36 位，大小写兼容）
    generic_path = re.sub(r'/[a-fA-F0-9\-]{36}', '/{id}', path)
    # 2. 纯数字 ID
    generic_path = re.sub(r'/\d+', '/{id}', generic_path)
    # 3. 长 hex 字符串（12+ 位，如 thread_id / trace_id）
    generic_path = re.sub(r'/[a-f0-9]{12,}', '/{id}', generic_path)

    HTTP_REQUEST_TOTAL.labels(
        method=request.method,
        path=generic_path,
        status=response.status_code,
    ).inc()
    HTTP_REQUEST_DURATION.labels(
        method=request.method,
        path=generic_path,
    ).observe(duration)

    return response


# ── 限流器 ──
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60/minute"],
    storage_uri=settings.REDIS_URI,
)
app.state.limiter = limiter

# slowapi 限流超限时的异常处理
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"error": {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后重试", "details": {}}},
    )


# ── 认证 API ──


@app.post("/api/auth/register")
@limiter.limit("3/hour")
async def register(request: Request):
    """用户注册（限流：每 IP 每小时 3 次）。"""
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or len(username) < 3 or len(username) > 50:
        raise ValidationError("用户名需为 3-50 个字符")
    if not re.match(r"^[a-zA-Z0-9_一-鿿]+$", username):
        raise ValidationError("用户名只能包含字母、数字、下划线、中文")
    if len(password) < 6:
        raise ValidationError("密码至少需要 6 个字符")

    from app.storage.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM users WHERE username = $1", username
        )
        if existing:
            raise ValidationError("用户名已存在")

        password_hash_val = hash_password(password)
        user_id = await conn.fetchval(
            """
            INSERT INTO users (username, password_hash, email, group_id, role)
            VALUES ($1, $2, $3, 1, 'user')
            RETURNING id::text
            """,
            username,
            password_hash_val,
            body.get("email", ""),
        )

    token = create_access_token(user_id=user_id, username=username, group_id=1)
    return {
        "token": token,
        "user": {"id": user_id, "username": username, "role": "user", "group_id": 1},
    }


@app.post("/api/auth/login")
@limiter.limit("5/minute")
async def login(request: Request):
    """用户登录（限流：每 IP 每分钟 5 次）。

    认证顺序：
    1. 查本地用户表 → bcrypt 验证 → 返回 JWT
    2. 未找到 + LDAP 已启用 → LDAP bind → 自动创建用户 → 返回 JWT
    3. 均失败 → 401
    """
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        raise AuthError("用户名和密码不能为空")

    from app.storage.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text, username, password_hash, role, group_id, is_active "
            "FROM users WHERE username = $1",
            username,
        )

    # ── 路径 1: 本地用户 ──
    if row is not None:
        if not row["is_active"]:
            raise AuthError("账户已被禁用")
        if not verify_password(password, row["password_hash"]):
            raise AuthError("用户名或密码错误")
        token = create_access_token(
            user_id=row["id"],
            username=row["username"],
            role=row["role"],
            group_id=row["group_id"],
        )
        return {
            "token": token,
            "user": {
                "id": row["id"],
                "username": row["username"],
                "role": row["role"],
                "group_id": row["group_id"],
            },
        }

    # ── 路径 2: LDAP fallback ──
    if LDAPClient.is_enabled():
        ldap_user = await asyncio.to_thread(LDAPClient.authenticate, username, password)
        if ldap_user is not None:
            async with pool.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT id::text, username, role, group_id, is_active "
                    "FROM users WHERE username = $1",
                    username,
                )
                if existing is not None:
                    if not existing["is_active"]:
                        raise AuthError("账户已被禁用")
                    token = create_access_token(
                        user_id=existing["id"],
                        username=existing["username"],
                        role=existing["role"],
                        group_id=existing["group_id"],
                    )
                    return {
                        "token": token,
                        "user": {
                            "id": existing["id"],
                            "username": existing["username"],
                            "role": existing["role"],
                            "group_id": existing["group_id"],
                        },
                    }

                user_id = await conn.fetchval(
                    """
                    INSERT INTO users (username, password_hash, email, group_id, role, auth_source)
                    VALUES ($1, $2, $3, 1, 'user', 'ldap')
                    RETURNING id::text
                    """,
                    username,
                    hash_password(secrets.token_urlsafe(32)),
                    ldap_user.email or "",
                )

            token = create_access_token(
                user_id=user_id, username=username, role="user", group_id=1
            )
            return {
                "token": token,
                "user": {"id": user_id, "username": username, "role": "user", "group_id": 1},
            }

    raise AuthError("用户名或密码错误")


@app.get("/api/auth/sso/login")
@limiter.limit("3/minute")
async def sso_login(request: Request):
    """OIDC SSO 登录入口 — 重定向到身份提供者。"""
    if not OIDCClient.is_enabled():
        raise HTTPException(status_code=404, detail="SSO not configured")

    state = OIDCClient.generate_state()

    redis = await get_redis()
    await redis.set(f"sso:state:{state}", "1", ex=300)

    auth_url = await OIDCClient.get_authorize_url(state)
    return RedirectResponse(url=auth_url, status_code=302)


@app.get("/api/auth/sso/callback")
@limiter.limit("3/minute")
async def sso_callback(code: str, state: str, request: Request):
    """OIDC SSO 回调 — 验证 state、换 token、创建/匹配用户、返回 JWT。"""
    if not OIDCClient.is_enabled():
        raise HTTPException(status_code=404, detail="SSO not configured")

    redis = await get_redis()
    stored = await redis.get(f"sso:state:{state}")
    if stored is None:
        raise AuthError("SSO state 无效或已过期")
    await redis.delete(f"sso:state:{state}")

    oidc_user = await OIDCClient.exchange_code(code)
    if oidc_user is None:
        raise AuthError("SSO 认证失败，无法获取用户信息")

    from app.storage.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id::text, username, role, group_id, is_active "
            "FROM users WHERE username = $1",
            oidc_user.username,
        )

        if row is not None:
            if not row["is_active"]:
                raise AuthError("账户已被禁用")
            token = create_access_token(
                user_id=row["id"],
                username=row["username"],
                role=row["role"],
                group_id=row["group_id"],
            )
        else:
            user_id = await conn.fetchval(
                """
                INSERT INTO users (username, password_hash, email, group_id, role, auth_source)
                VALUES ($1, $2, $3, 1, 'user', 'oidc')
                RETURNING id::text
                """,
                oidc_user.username,
                hash_password(secrets.token_urlsafe(32)),
                oidc_user.email or "",
            )
            token = create_access_token(
                user_id=user_id, username=oidc_user.username, role="user", group_id=1
            )

    frontend_origin = settings.CORS_ORIGINS.split(",")[0].strip()
    # Use hash fragment to avoid JWT in server logs / browser history / Referer
    redirect_url = f"{frontend_origin}/#token={token}"
    return RedirectResponse(url=redirect_url, status_code=302)


@app.get("/api/auth/sso/providers")
async def sso_providers():
    """返回已启用的 SSO provider 列表。"""
    providers = []
    if OIDCClient.is_enabled():
        providers.append("oidc")
    return {"providers": providers}


@app.get("/api/auth/me")
async def get_me(user: UserInfo = Depends(get_current_user)):
    """获取当前登录用户信息。"""
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "group_id": user.group_id,
    }


class TaskRequest(BaseModel):
    """前端启动任务时提交的请求体。"""

    query: str
    thread_id: str | None = None


def _forget_task(thread_id: str, task: asyncio.Task) -> None:
    """
    清理已结束任务的登记关系。

    done_callback 触发时，active_tasks 中可能已经被新任务替换；只有仍是同一个
    task 时才删除，避免误清理同 thread_id 下刚启动的新任务。
    """
    if active_tasks.get(thread_id) is task:
        active_tasks.pop(thread_id, None)
        # 异步从 Redis 移除，添加异常回调防止静默失败
        _task = asyncio.ensure_future(unregister_active_task(thread_id))
        _task.add_done_callback(_log_task_exception)


@app.post("/api/task")
async def run_task(body: TaskRequest, request: Request, user: UserInfo = Depends(get_current_user)):
    """
    启动一次 DeepAgents 后台任务。

    任务通过 ARQ 入队到 Worker 异步执行，HTTP 请求只负责提交并立即返回。
    后续执行轨迹、子智能体调用和最终答案都会由 monitor 通过 `/ws/{thread_id}`
    推送给同一会话的前端。
    """
    thread_id = body.thread_id or str(uuid.uuid4())

    if not body.query.strip():
        raise HTTPException(status_code=422, detail="查询内容不能为空")

    # 用户并发检查（Redis 计数器）
    from app.storage.redis_client import get_redis_client
    redis = await get_redis_client()
    user_task_key = f"user_tasks:{user.id}"
    current_count = await redis.incr(user_task_key)
    await redis.expire(user_task_key, 600)  # 10 分钟过期防止泄漏

    if current_count > settings.MAX_CONCURRENT_TASKS_PER_USER:
        await redis.decr(user_task_key)
        raise HTTPException(
            status_code=429,
            detail=f"并行任务已达上限({settings.MAX_CONCURRENT_TASKS_PER_USER})，请等待当前任务完成",
        )

    # 确保 session 记录存在（状态初始为 queued）
    from app.storage.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (thread_id, title, status, user_id, group_id)
            VALUES ($1, $2, 'queued', $3, $4)
            ON CONFLICT (thread_id) DO UPDATE SET status = 'queued'
            """,
            thread_id, body.query[:100], user.id, user.group_id,
        )

    # SSE 通知：会话列表变更
    await publish_session_event(user.group_id, {
        "event": "session_created", "thread_id": thread_id,
    })

    # 入队到 ARQ Worker（try-finally 保证入队失败时释放并发配额）
    try:
        arq_client = request.app.state.arq_client
        job = await arq_client.enqueue_job(
            "run_agent_task", body.query, thread_id, user.id, user.group_id,
        )
    except Exception:
        await redis.decr(user_task_key)
        raise

    # 存储 thread_id → job_id 映射，方便取消时查找 ARQ job
    await redis.set(f"task_job:{thread_id}", job.job_id, ex=3600)

    logger.info("任务已入队", thread_id=thread_id, job_id=job.job_id)

    return {
        "thread_id": thread_id,
        "task_id": job.job_id,
        "status": "queued",
    }


@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(thread_id: str, request: Request, user: UserInfo = Depends(get_current_user)):
    """
    取消指定 thread_id 对应的后台 Agent 任务。

    支持两种场景：
    1. 任务还在队列中（queued）→ 通过 ARQ abort 从队列移除
    2. 任务已在执行中（running）→ 设置 Redis 取消信号，Worker 定期检查
    """
    # ── 所有权校验：非本人且非管理员不得取消他人任务 ──
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id FROM sessions WHERE thread_id = $1", thread_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在或已结束")
    if not user.is_admin and str(row["user_id"]) != str(user.id):
        raise PermissionDeniedError("无权取消此任务：该任务属于其他用户")

    from app.storage.redis_client import get_redis_client
    redis = await get_redis_client()

    # 1. 查找 ARQ job_id
    job_id = await redis.get(f"task_job:{thread_id}")
    if not job_id:
        # 兼容旧逻辑：检查 active_tasks
        task = active_tasks.get(thread_id)
        if task and not task.done():
            task.cancel()
            return {"status": "cancelled", "thread_id": thread_id}
        raise HTTPException(status_code=404, detail="任务不存在或已结束")

    # 2. 设置 Redis 取消信号（Worker 会检查并主动抛出 CancelledError）
    await redis.set(f"cancel:{thread_id}", "1", ex=3600)

    # 3. 尝试从 ARQ 队列中 abort（如果任务尚未被执行）
    arq_client = request.app.state.arq_client
    try:
        job = await arq_client.get_job(job_id)
        if job:
            await job.abort()
    except Exception:
        pass  # job 可能已经不在队列中了

    # 4. 更新 session 状态
    try:
        from app.storage.db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE sessions SET status = 'cancelled' WHERE thread_id = $1",
                thread_id,
            )
    except Exception:
        pass

    # 5. 清理映射
    await redis.delete(f"task_job:{thread_id}")

    # 6. SSE 通知：会话状态变更
    await publish_session_event(user.group_id, {
        "event": "session_status_changed", "thread_id": thread_id, "status": "cancelled",
    })

    return {"status": "cancelled", "thread_id": thread_id}


@app.get("/api/task/{task_id}/status")
async def get_task_status(
    task_id: str,
    request: Request,
    user: UserInfo = Depends(get_current_user),
):
    """查询 ARQ 任务队列状态。"""
    arq_client = request.app.state.arq_client
    job = await arq_client.get_job(task_id)

    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")

    job_status = await job.status()
    return {
        "task_id": task_id,
        "status": str(job_status),
        "enqueue_time": job.enqueue_time.isoformat() if job.enqueue_time else None,
        "start_time": job.start_time.isoformat() if job.start_time else None,
        "finish_time": job.finish_time.isoformat() if job.finish_time else None,
    }


# ── 文件上传安全校验 ──

ALLOWED_EXTENSIONS = {
    ext.strip().lower()
    for ext in settings.ALLOWED_UPLOAD_EXTENSIONS.split(",")
    if ext.strip()
}

ALLOWED_MIME_TYPES = {
    "text/plain",
    "text/csv",
    "text/markdown",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "image/png",
    "image/jpeg",
    "image/gif",
    "application/json",
}

MAX_UPLOAD_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


def validate_upload_file(filename: str, content_type: str, file_size: int) -> str:
    """校验上传文件，返回错误信息字符串或空字符串表示通过。"""
    # 检查扩展名
    ext = os.path.splitext(filename)[1].lower()
    if not ext or ext not in ALLOWED_EXTENSIONS:
        return f"不支持的文件类型: {ext}。允许: {', '.join(sorted(ALLOWED_EXTENSIONS))}"

    # 检查 MIME 类型（宽松匹配，允许 office 系列通配）
    if content_type:
        mime_matched = content_type in ALLOWED_MIME_TYPES
        if not mime_matched:
            if not content_type.startswith("application/vnd.openxmlformats-officedocument"):
                return f"不支持的文件格式: {content_type}"

    # 检查大小
    if file_size > MAX_UPLOAD_BYTES:
        size_mb = file_size / (1024 * 1024)
        return f"文件过大 ({size_mb:.1f}MB)，上限 {settings.MAX_UPLOAD_SIZE_MB}MB"

    return ""


@app.post("/api/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    thread_id: str = Form(...),
    user: UserInfo = Depends(get_current_user),
):
    """
    文件上传接口 (File Upload)。

    目标：
    1. 接收用户上传的一个或多个文件。
    2. 保存到 `updated/session_{thread_id}` 目录。
    3. 供 Agent 在后续任务中读取和分析。

    Args:
        files (List[UploadFile]): 文件对象列表。
        thread_id (str): 关联的任务会话 ID。
    """
    # 上传文件先按会话隔离保存，避免不同任务读取到彼此的附件
    upload_storage = get_upload_storage()

    saved_files = []
    for file in files:
        # 安全校验：读取并检查文件
        content = await file.read()
        error = validate_upload_file(
            file.filename or "", file.content_type or "", len(content)
        )
        if error:
            raise ValidationError(error)

        # 防御路径穿越：提取纯文件名，拒绝含目录组件的恶意文件名
        safe_filename = Path(file.filename).name
        if safe_filename != file.filename:
            raise ValidationError(f"文件名包含非法路径字符: {file.filename}")
        if not safe_filename or safe_filename in (".", ".."):
            raise ValidationError("无效的文件名")

        storage_path = f"session_{thread_id}/{safe_filename}"
        await upload_storage.save(storage_path, content)
        saved_files.append(safe_filename)

    # SSE 通知：文件列表变更
    await publish_file_event(thread_id)

    return {"status": "uploaded", "files": saved_files}


@app.delete("/api/upload/{thread_id}/{filename}")
async def delete_uploaded_file(
    thread_id: str,
    filename: str,
    user: UserInfo = Depends(get_current_user),
):
    """
    删除已上传文件接口 (Delete Uploaded File)。

    目标：
    1. 删除指定会话的上传文件。
    2. 严格的安全检查，防止路径遍历攻击。

    Args:
        thread_id (str): 会话 ID。
        filename (str): 要删除的文件名。
    """
    # 防御路径穿越
    safe_filename = Path(filename).name
    if safe_filename != filename:
        raise ValidationError("文件名包含非法路径字符")
    if not safe_filename or safe_filename in (".", ".."):
        raise ValidationError("无效的文件名")

    upload_storage = get_upload_storage()
    storage_path = f"session_{thread_id}/{safe_filename}"

    if not await upload_storage.exists(storage_path):
        raise DeepAgentsError(f"文件不存在: {safe_filename}")

    await upload_storage.delete(storage_path)
    logger.info("已删除上传文件", thread_id=thread_id, filename=safe_filename)

    # 如果目录为空，清理目录
    await upload_storage.remove_dir(f"session_{thread_id}")

    # SSE 通知：文件列表变更
    await publish_file_event(thread_id)

    return {"status": "deleted", "filename": safe_filename}


@app.get("/api/download")
async def download_file(
    path: str,
    user: UserInfo = Depends(get_current_user),
):
    """
    文件下载接口 (File Download)。

    目标：
    1. 根据相对路径（相对于 output 目录）下载文件。
    2. 严格的安全检查，防止越权访问。

    Args:
        path (str): 文件相对于 output 目录的路径 (由 list_files 接口返回)。
    """
    try:
        output_storage = get_output_storage()
        abs_path = await output_storage.get_absolute_path(path)
    except PermissionError:
        raise PermissionDeniedError("拒绝访问: 只能下载输出目录下的文件")
    except Exception:
        return {"error": "无效的路径参数"}

    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    # FileResponse 会以流式响应返回文件内容，并让浏览器使用原文件名下载
    return FileResponse(abs_path, filename=abs_path.name)


def generate_download_url(file_path: str, user_id: str) -> str:
    """生成带鉴权的下载 URL（1小时有效）。

    前端调用此函数获取安全的临时下载链接。
    """
    import urllib.parse
    expires = int(time.time()) + 3600
    token = create_access_token(
        user_id=user_id,
        username="download",
        role="user",
        expires_delta=timedelta(hours=1),
    )
    encoded_path = urllib.parse.quote(file_path, safe="/")
    return f"/api/download?path={encoded_path}&token={token}&expires={expires}"


@app.get("/api/files")
async def list_files(path: str, user: UserInfo = Depends(get_current_user)):
    """
    文件列表查询接口 (File Explorer)。

    目标：
    1. 列出指定目录下的所有生成文件。
    2. 提供文件元数据（大小、修改时间、下载所需路径）。
    3. 严格的安全检查，防止路径遍历攻击。

    Args:
        path (str): 目标目录的绝对路径 (必须在 output 目录下)。
    """
    logger.debug("请求文件列表", path=path)

    try:
        output_storage = get_output_storage()
        # 验证路径安全性 — get_absolute_path 内部做路径遍历检查
        abs_path = await output_storage.get_absolute_path(path)
    except PermissionError:
        logger.warning("拒绝访问: 路径不在 output 目录下", path=path)
        return {"error": "拒绝访问: 只能访问输出目录下的文件"}
    except Exception as e:
        logger.warning("路径解析失败", error=str(e))
        return {"error": f"路径无效: {e}"}

    if not abs_path.exists():
        return {"error": "目录不存在"}

    try:
        file_objects = await output_storage.list_files(path)
        files = [
            {
                "name": fo.name,
                "type": "file",
                "path": fo.path,
                "size": fo.size,
                "mtime": fo.mtime,
            }
            for fo in file_objects
        ]
    except Exception as e:
        logger.warning("遍历文件失败", error=str(e))
        return {"error": str(e)}

    logger.debug("文件列表查询完成", count=len(files))
    return {"files": files}


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """
    WebSocket 实时通讯核心接口 (Real-time Communication)。

    连接建立后，先推送历史事件（断线重连恢复），再进入实时推送循环。
    ConnectionManager 用 thread_id 保存 WebSocket。monitor 后续发送事件时
    只需要按 thread_id 查找连接，就能把进度推给对应页面。
    """
    logger.info("WebSocket 连接请求", thread_id=thread_id)

    # ── WebSocket 认证：通过 query 参数传递 token ──
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401, reason="Missing token")
        return
    try:
        payload = decode_token(token)
        ws_user_id = payload.get("sub", "")
    except Exception:
        await websocket.close(code=4401, reason="Invalid or expired token")
        return

    # 会话所有权校验：确保连接者是该会话的拥有者或管理员
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id FROM sessions WHERE thread_id = $1", thread_id
            )
        if row:
            session_owner = str(row["user_id"])
            if session_owner != str(ws_user_id):
                # 检查是否为管理员
                try:
                    user_row = None
                    async with pool.acquire() as conn:
                        user_row = await conn.fetchrow(
                            "SELECT role FROM users WHERE id = $1", ws_user_id
                        )
                    if not user_row or user_row["role"] != "admin":
                        await websocket.close(code=4403, reason="Forbidden")
                        return
                except Exception:
                    await websocket.close(code=4403, reason="Forbidden")
                    return
    except Exception:
        pass  # DB 查询失败时不阻塞连接，但记录日志

    # 先接受连接
    await websocket.accept()

    # 1. 发送历史事件（断线重连恢复）
    try:
        from app.storage.redis_client import get_redis
        redis = await get_redis()
        key = f"ws:events:{thread_id}"

        # 先从 Redis 拉缓存事件
        cached = await redis.lrange(key, 0, -1)
        if cached:
            for event_json in reversed(cached):  # 最旧在前
                event = json.loads(event_json)
                await websocket.send_json({"type": "replay", "event": event})

        # Redis 无数据，从 PostgreSQL 查
        if not cached:
            from app.storage.db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT event_type, message, payload, created_at
                       FROM agent_events
                       WHERE thread_id = $1
                       ORDER BY created_at ASC""",
                    thread_id,
                )
                for row in rows:
                    await websocket.send_json({
                        "type": "replay",
                        "event": {
                            "type": row["event_type"],
                            "message": row["message"],
                            "payload": json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
                            "timestamp": row["created_at"].isoformat(),
                        },
                    })

        if cached:
            logger.info("历史事件已推送", thread_id=thread_id, count=len(cached))

    except Exception as e:
        logger.warning("历史事件推送失败", thread_id=thread_id, exc_info=True)

    # 2. 通过 ConnectionManager 注册连接，保证原子性和后续定向推送
    # (websocket 已在上面 accept，这里只做注册)
    await manager.register(websocket, thread_id)

    async def _receive_loop():
        """接收前端心跳 ping，维持连接活跃。"""
        try:
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            pass

    async def _redis_forward_loop():
        """订阅 Redis Pub/Sub 频道，实时转发 Worker 事件到前端。"""
        try:
            from app.storage.redis_client import subscribe_ws_events
            logger.info("Redis Pub/Sub 订阅已启动", thread_id=thread_id)
            async for event_json in subscribe_ws_events(thread_id):
                try:
                    event = json.loads(event_json)
                    await websocket.send_json(event)
                    logger.info("事件已转发到前端", thread_id=thread_id, event_type=event.get("event", "?"))
                except Exception as e:
                    logger.warning("事件转发失败", thread_id=thread_id, error=str(e))
        except Exception as e:
            logger.warning("Redis Pub/Sub 订阅异常退出", thread_id=thread_id, error=str(e), exc_info=True)

    # 并行运行接收循环和 Redis 转发，任一结束即断开
    try:
        receive_task = asyncio.create_task(_receive_loop())
        redis_task = asyncio.create_task(_redis_forward_loop())
        done, pending = await asyncio.wait(
            [receive_task, redis_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, thread_id)
        logger.info("WebSocket 客户端已断开", thread_id=thread_id)


# ── SSE 实时事件推送端点 ──


@app.get("/api/events/stream")
async def sse_stream(
    request: Request,
    thread_id: str = "",
):
    """SSE 实时事件推送端点，替代前端 HTTP 轮询。

    通过 Redis Pub/Sub 订阅两类通知频道：
    - 全局会话变更（sse:sessions）：任务创建/完成/取消时触发
    - 文件变更（sse:files:{thread_id}）：文件上传/生成/删除时触发

    前端使用 EventSource 连接此端点，接收 "session_updated" 和
    "files_updated" 事件后主动刷新对应列表。
    """
    # 认证：从 query 参数获取 JWT（EventSource API 不支持自定义请求头）
    token = request.query_params.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    async def event_generator():
        """SSE 事件生成器：订阅 Redis 频道并持续推送通知。"""
        # 初始连接确认
        yield f"data: {json.dumps({'event': 'connected'})}\n\n"

        try:
            # 并行订阅两个频道
            session_gen = subscribe_sse_sessions()
            session_task = asyncio.ensure_future(session_gen.__anext__())

            file_task = None
            file_gen = None
            if thread_id:
                file_gen = subscribe_sse_files(thread_id)
                file_task = asyncio.ensure_future(file_gen.__anext__())

            pending = {session_task: "session"}
            if file_task:
                pending[file_task] = "file"

            while True:
                # 检查客户端是否断开
                if await request.is_disconnected():
                    break

                done_set, _ = await asyncio.wait(
                    list(pending.keys()),
                    timeout=15,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if not done_set:
                    # 超时发送心跳，维持连接活跃
                    yield ": heartbeat\n\n"
                    continue

                for completed in done_set:
                    kind = pending.pop(completed)
                    try:
                        data = completed.result()
                        if kind == "session":
                            yield f"event: session_updated\ndata: {data}\n\n"
                            session_task = asyncio.ensure_future(session_gen.__anext__())
                            pending[session_task] = "session"
                        else:
                            yield f"event: files_updated\ndata: {data}\n\n"
                            file_task = asyncio.ensure_future(file_gen.__anext__())
                            pending[file_task] = "file"
                    except (StopAsyncIteration, asyncio.CancelledError):
                        pass
                    except Exception as exc:
                        logger.warning("SSE 订阅异常", kind=kind, error=str(exc))

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("SSE 流异常退出", error=str(exc))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )


# ---- 会话历史管理 API ----

class SessionSummary(BaseModel):
    thread_id: str
    title: str | None = None
    status: str
    message_count: int = 0
    started_at: str | None = None
    completed_at: str | None = None


@app.get("/api/sessions")
async def list_sessions(
    limit: int = 20,
    offset: int = 0,
    user: UserInfo = Depends(get_current_user),
):
    """列出历史会话（按开始时间倒序），仅返回本组数据。"""
    try:
        group_id, group_suffix = get_group_filter(user)
        pool = await get_pool()
        async with pool.acquire() as conn:
            # 构建参数列表：group_id（非管理员时） + limit + offset
            params = []
            if group_id is not None:
                params.append(group_id)
            params.extend([limit, offset])

            rows = await conn.fetch(
                f"""SELECT s.thread_id, s.title, s.status, s.started_at, s.completed_at,
                          COUNT(m.id) AS message_count
                   FROM sessions s
                   LEFT JOIN messages m ON s.thread_id = m.thread_id
                   WHERE {group_suffix(params)}
                   GROUP BY s.thread_id, s.title, s.status, s.started_at, s.completed_at
                   ORDER BY s.started_at DESC
                   LIMIT ${len(params) - 1} OFFSET ${len(params)}""",
                *params,
            )
            sessions = []
            for row in rows:
                sessions.append({
                    "thread_id": row["thread_id"],
                    "title": row["title"] or row["thread_id"][:8],
                    "status": row["status"],
                    "message_count": row["message_count"],
                    "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                    "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
                })
            return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询会话列表失败: {e}")


@app.get("/api/sessions/{thread_id}")
async def get_session_detail(
    thread_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """获取单个会话详情，包含消息历史和事件。非本组会话不可见。"""
    try:
        group_id, group_suffix = get_group_filter(user)
        pool = await get_pool()
        async with pool.acquire() as conn:
            # 会话基本信息（带组过滤）
            params = [thread_id]
            if group_id is not None:
                params.append(group_id)
                group_clause = f"AND {group_suffix([thread_id])}"
            else:
                group_clause = ""

            session = await conn.fetchrow(
                f"SELECT s.* FROM sessions s WHERE s.thread_id = $1 {group_clause}",
                *params,
            )
            if not session:
                raise HTTPException(status_code=404, detail="会话不存在或无权访问")

            # 消息列表
            msg_rows = await conn.fetch(
                "SELECT role, content, tool_calls, created_at FROM messages "
                "WHERE thread_id = $1 ORDER BY created_at ASC",
                thread_id,
            )
            messages = [
                {
                    "role": row["role"],
                    "content": row["content"],
                    "tool_calls": row["tool_calls"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in msg_rows
            ]

            # 事件列表
            event_rows = await conn.fetch(
                "SELECT event_type, message, payload, created_at FROM agent_events "
                "WHERE thread_id = $1 ORDER BY created_at ASC",
                thread_id,
            )
            events = [
                {
                    "event_type": row["event_type"],
                    "message": row["message"],
                    "payload": row["payload"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in event_rows
            ]

            return {
                "thread_id": session["thread_id"],
                "title": session["title"],
                "status": session["status"],
                "started_at": session["started_at"].isoformat() if session["started_at"] else None,
                "completed_at": session["completed_at"].isoformat() if session["completed_at"] else None,
                "messages": messages,
                "events": events,
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询会话详情失败: {e}")


@app.delete("/api/sessions/{thread_id}")
async def delete_session(
    thread_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """删除指定会话及其关联数据。非本组会话不可删除。"""
    try:
        group_id, group_suffix = get_group_filter(user)
        pool = await get_pool()
        async with pool.acquire() as conn:
            params = [thread_id]
            if group_id is not None:
                params.append(group_id)
                group_clause = f"AND {group_suffix([thread_id])}"
            else:
                group_clause = ""

            result = await conn.execute(
                f"DELETE FROM sessions WHERE thread_id = $1 {group_clause}",
                *params,
            )
            if result == "DELETE 0":
                raise HTTPException(status_code=404, detail="会话不存在或无权访问")
            return {"status": "deleted", "thread_id": thread_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除会话失败: {e}")


# ── 会话分享 API ──


class ShareCreateRequest(BaseModel):
    title: Optional[str] = None
    expires_hours: Optional[int] = None  # 过期时间（小时），None 表示永不过期


@app.post("/api/sessions/{thread_id}/share")
async def create_share_link(
    thread_id: str,
    body: ShareCreateRequest,
    user: UserInfo = Depends(get_current_user),
):
    """为指定会话创建分享链接（只读）。

    只有会话所有者或管理员可以创建分享链接。
    生成唯一 share_token，访问者通过 /api/shared/{token} 查看。
    """
    from app.storage.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 验证会话存在且属于当前用户
        row = await conn.fetchrow(
            "SELECT thread_id, title FROM sessions WHERE thread_id = $1",
            thread_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="会话不存在")

        # 所有权检查（非管理员只能分享自己的会话）
        owner_row = await conn.fetchrow(
            "SELECT user_id FROM sessions WHERE thread_id = $1",
            thread_id,
        )
        if not user.is_admin and owner_row and str(owner_row["user_id"]) != str(user.id):
            raise HTTPException(status_code=403, detail="无权分享此会话")

        # 生成分享 token
        share_token = secrets.token_urlsafe(24)
        title = body.title or row["title"] or f"分享的会话 {thread_id[:8]}"
        expires_at = None
        if body.expires_hours:
            from datetime import datetime, timezone, timedelta
            expires_at = datetime.now(timezone.utc) + timedelta(hours=body.expires_hours)

        await conn.execute(
            """INSERT INTO session_shares (share_token, thread_id, created_by, title, expires_at)
               VALUES ($1, $2, $3, $4, $5)""",
            share_token,
            thread_id,
            str(user.id),
            title,
            expires_at,
        )

        return {
            "share_token": share_token,
            "thread_id": thread_id,
            "title": title,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "share_url": f"/shared/{share_token}",
        }


@app.get("/api/sessions/{thread_id}/shares")
async def list_share_links(
    thread_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """列出指定会话的所有分享链接。"""
    from app.storage.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT share_token, title, is_active, view_count, expires_at, created_at
               FROM session_shares
               WHERE thread_id = $1
               ORDER BY created_at DESC""",
            thread_id,
        )
        shares = []
        for row in rows:
            shares.append({
                "share_token": row["share_token"],
                "title": row["title"],
                "is_active": row["is_active"],
                "view_count": row["view_count"],
                "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "share_url": f"/shared/{row['share_token']}",
            })
        return {"shares": shares, "total": len(shares)}


@app.get("/api/shared/{share_token}")
async def get_shared_session(share_token: str):
    """公开访问分享会话（只读，无需认证）。

    返回会话基本信息和消息列表，不包含敏感的事件数据。
    """
    from app.storage.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 查询分享记录
        share = await conn.fetchrow(
            """SELECT s.thread_id, s.title, s.is_active, s.expires_at
               FROM session_shares s
               WHERE s.share_token = $1""",
            share_token,
        )
        if not share:
            raise HTTPException(status_code=404, detail="分享链接不存在")

        if not share["is_active"]:
            raise HTTPException(status_code=410, detail="分享链接已失效")

        # 检查过期
        if share["expires_at"]:
            from datetime import datetime, timezone
            if datetime.now(timezone.utc) > share["expires_at"]:
                raise HTTPException(status_code=410, detail="分享链接已过期")

        # 获取会话详情
        session = await conn.fetchrow(
            "SELECT thread_id, title, status, started_at, completed_at FROM sessions WHERE thread_id = $1",
            share["thread_id"],
        )
        if not session:
            raise HTTPException(status_code=404, detail="原始会话已被删除")

        # 获取消息列表（只返回 user 和 assistant 消息，隐藏 system/tool 消息）
        messages = await conn.fetch(
            """SELECT role, content, created_at
               FROM messages
               WHERE thread_id = $1 AND role IN ('user', 'assistant')
               ORDER BY created_at ASC""",
            share["thread_id"],
        )

        # 递增访问计数
        await conn.execute(
            "UPDATE session_shares SET view_count = view_count + 1 WHERE share_token = $1",
            share_token,
        )

        return {
            "thread_id": session["thread_id"],
            "title": share["title"] or session["title"],
            "status": session["status"],
            "started_at": session["started_at"].isoformat() if session["started_at"] else None,
            "completed_at": session["completed_at"].isoformat() if session["completed_at"] else None,
            "messages": [
                {
                    "role": msg["role"],
                    "content": msg["content"],
                    "created_at": msg["created_at"].isoformat() if msg["created_at"] else None,
                }
                for msg in messages
            ],
            "shared_by": True,  # 标记这是分享视图
        }


@app.delete("/api/shared/{share_token}")
async def revoke_share_link(
    share_token: str,
    user: UserInfo = Depends(get_current_user),
):
    """撤销分享链接（软删除，设为 is_active=False）。"""
    from app.storage.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 只有创建者或管理员可以撤销
        share = await conn.fetchrow(
            "SELECT created_by, thread_id FROM session_shares WHERE share_token = $1",
            share_token,
        )
        if not share:
            raise HTTPException(status_code=404, detail="分享链接不存在")

        if not user.is_admin and str(share["created_by"]) != str(user.id):
            raise HTTPException(status_code=403, detail="无权撤销此分享链接")

        await conn.execute(
            "UPDATE session_shares SET is_active = FALSE WHERE share_token = $1",
            share_token,
        )
        return {"status": "revoked", "share_token": share_token}


# ---- 记忆管理 API ----

class MemoryCreateRequest(BaseModel):
    content: str
    memory_type: str = "fact"
    importance: float = 0.5


@app.get("/api/memories")
async def list_memories(
    memory_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: UserInfo = Depends(get_current_user),
):
    """列出长期记忆（按用户隔离，管理员可查看全部）。"""
    try:
        pool = await get_pool()
        user_id_str = str(user.id)
        async with pool.acquire() as conn:
            if user.is_admin and memory_type:
                rows = await conn.fetch(
                    """SELECT id, memory_type, content, importance, access_count,
                              last_accessed, source_thread_id, created_at
                       FROM long_term_memories
                       WHERE memory_type = $1
                       ORDER BY created_at DESC
                       LIMIT $2 OFFSET $3""",
                    memory_type, limit, offset,
                )
            elif user.is_admin:
                rows = await conn.fetch(
                    """SELECT id, memory_type, content, importance, access_count,
                              last_accessed, source_thread_id, created_at
                       FROM long_term_memories
                       ORDER BY created_at DESC
                       LIMIT $1 OFFSET $2""",
                    limit, offset,
                )
            elif memory_type:
                rows = await conn.fetch(
                    """SELECT id, memory_type, content, importance, access_count,
                              last_accessed, source_thread_id, created_at
                       FROM long_term_memories
                       WHERE memory_type = $1 AND user_id = $2
                       ORDER BY created_at DESC
                       LIMIT $3 OFFSET $4""",
                    memory_type, user_id_str, limit, offset,
                )
            else:
                rows = await conn.fetch(
                    """SELECT id, memory_type, content, importance, access_count,
                              last_accessed, source_thread_id, created_at
                       FROM long_term_memories
                       WHERE user_id = $1
                       ORDER BY created_at DESC
                       LIMIT $2 OFFSET $3""",
                    user_id_str, limit, offset,
                )
            memories = [
                {
                    "id": str(row["id"]),
                    "memory_type": row["memory_type"],
                    "content": row["content"],
                    "importance": row["importance"],
                    "access_count": row["access_count"],
                    "last_accessed": row["last_accessed"].isoformat() if row["last_accessed"] else None,
                    "source_thread_id": row["source_thread_id"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]
            return {"memories": memories}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询记忆失败: {e}")


@app.delete("/api/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """删除指定记忆（按用户隔离，管理员可删除任意记忆）。"""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if user.is_admin:
                result = await conn.execute(
                    "DELETE FROM long_term_memories WHERE id = $1::uuid", memory_id
                )
            else:
                result = await conn.execute(
                    "DELETE FROM long_term_memories WHERE id = $1::uuid AND user_id = $2",
                    memory_id, str(user.id),
                )
            if result == "DELETE 0":
                raise HTTPException(status_code=404, detail="记忆不存在或无权删除")
            return {"status": "deleted", "id": memory_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除记忆失败: {e}")


@app.post("/api/memories")
async def create_memory(
    request: MemoryCreateRequest,
    user: UserInfo = Depends(get_current_user),
):
    """手动创建记忆。"""
    from app.storage.memory_service import get_memory_service
    memory_service = get_memory_service()
    memory_id = await memory_service.store_memory(
        content=request.content,
        memory_type=request.memory_type,
        importance=request.importance,
        user_id=str(user.id),
    )
    if not memory_id:
        raise HTTPException(status_code=500, detail="记忆创建失败")
    return {"status": "created", "id": memory_id}

# ---- 自建 RAG 知识库管理 API ----

class KBCreateRequest(BaseModel):
    """创建知识库的请求体。"""
    name: str
    description: str = ""


@app.post("/api/kb/create")
async def create_kb(
    request: KBCreateRequest,
    user: UserInfo = Depends(get_current_user),
):
    """创建自建 RAG 知识库，写入当前用户组 ID。"""
    try:
        engine = get_rag_engine()
        kb_id = engine.create_kb(request.name, request.description, group_id=user.group_id)
        return {"status": "created", "kb_id": kb_id, "name": request.name}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/api/kb/list")
async def list_kbs(
    user: UserInfo = Depends(get_current_user),
):
    """列出当前用户组可见的自建 RAG 知识库。管理员可见全部。"""
    engine = get_rag_engine()
    # 防御性兜底：未分配组的用户默认归入组 1，避免看到全量数据
    filter_group_id = None if user.is_admin else (user.group_id if user.group_id is not None else 1)
    return {"knowledge_bases": engine.list_kbs(group_id=filter_group_id)}


@app.delete("/api/kb/{kb_name}")
async def delete_kb(
    kb_name: str,
    user: UserInfo = Depends(get_current_user),
):
    """删除指定名称的知识库。校验组所有权（管理员可删除任意）。"""
    engine = get_rag_engine()
    if engine.get_kb(kb_name) is None:
        raise HTTPException(status_code=404, detail=f"知识库 '{kb_name}' 不存在")
    try:
        filter_group_id = None if user.is_admin else user.group_id
        engine.delete_kb(kb_name, group_id=filter_group_id)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"status": "deleted", "name": kb_name}


@app.post("/api/kb/ingest")
async def ingest_files(
    files: list[UploadFile] = File(...),
    kb_name: str = Form(...),
    user: UserInfo = Depends(get_current_user),
):
    """向指定知识库摄入文档文件（支持 PDF/DOCX/MD/TXT）。校验组所有权。"""
    engine = get_rag_engine()
    if engine.get_kb(kb_name) is None:
        raise HTTPException(status_code=404, detail=f"知识库 '{kb_name}' 不存在，请先创建")

    # 校验组所有权
    if not user.is_admin and not engine.check_kb_access(kb_name, user.group_id):
        raise HTTPException(status_code=403, detail=f"无权访问知识库 '{kb_name}'")

    doc_storage = get_doc_storage()
    await doc_storage.ensure_dir(kb_name)

    results = {}
    for file in files:
        # 防御路径穿越：提取纯文件名
        safe_filename = Path(file.filename).name
        if safe_filename != file.filename or safe_filename in (".", "..") or not safe_filename:
            results[file.filename] = "文件名包含非法路径字符，已拒绝"
            continue

        storage_path = f"{kb_name}/{safe_filename}"
        await doc_storage.save_stream(storage_path, file.file)
        try:
            abs_path = await doc_storage.get_absolute_path(storage_path)
            n = engine.ingest_file(kb_name, str(abs_path))
            results[file.filename] = f"摄入成功，共 {n} 个文本块"
        except Exception as e:
            results[file.filename] = f"摄入失败: {str(e)}"

    return {"status": "done", "kb_name": kb_name, "results": results}


# ── 自定义提示词模板 ──


class PromptTemplateCreateRequest(BaseModel):
    """创建提示词模板请求体。"""
    name: str
    scope: str = "group"  # "group" 或 "user"
    agent_type: str = "main"
    system_prompt: str
    is_active: bool = True


class PromptTemplateUpdateRequest(BaseModel):
    """更新提示词模板请求体。"""
    name: str | None = None
    system_prompt: str | None = None
    is_active: bool | None = None


@app.get("/api/prompt-templates")
async def list_prompt_templates(user: UserInfo = Depends(get_current_user)):
    """列出当前用户可见的提示词模板（组级 + 用户级）。"""
    from app.storage.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, scope, owner_id, group_id, agent_type,
                   system_prompt, is_active, created_at, updated_at
            FROM prompt_templates
            WHERE (scope = 'group' AND group_id = $1)
               OR (scope = 'user' AND owner_id = $2)
            ORDER BY scope, name
            """,
            user.group_id, user.id,
        )
    templates = [
        {
            "id": str(row["id"]),
            "name": row["name"],
            "scope": row["scope"],
            "owner_id": row["owner_id"],
            "group_id": row["group_id"],
            "agent_type": row["agent_type"],
            "system_prompt": row["system_prompt"],
            "is_active": row["is_active"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]
    return {"templates": templates, "total": len(templates)}


@app.post("/api/prompt-templates")
async def create_prompt_template(body: PromptTemplateCreateRequest, user: UserInfo = Depends(get_current_user)):
    """创建一个新的提示词模板。scope=user 时仅自己可见，scope=group 时组内可见（仅管理员）。"""
    if body.scope == "group" and user.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可创建组级模板")
    if body.scope not in ("group", "user"):
        raise HTTPException(status_code=422, detail="scope 必须为 group 或 user")
    if not body.system_prompt.strip():
        raise HTTPException(status_code=422, detail="提示词内容不能为空")

    from app.storage.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO prompt_templates (name, scope, owner_id, group_id, agent_type, system_prompt, is_active)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, name, scope, agent_type, is_active, created_at
            """,
            body.name, body.scope,
            user.id if body.scope == "user" else None,
            user.group_id if body.scope == "group" else None,
            body.agent_type, body.system_prompt, body.is_active,
        )
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "scope": row["scope"],
        "agent_type": row["agent_type"],
        "is_active": row["is_active"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


@app.put("/api/prompt-templates/{template_id}")
async def update_prompt_template(
    template_id: str,
    body: PromptTemplateUpdateRequest,
    user: UserInfo = Depends(get_current_user),
):
    """更新指定的提示词模板。组级模板仅管理员可修改，用户级模板仅创建者可修改。"""
    from app.storage.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, scope, owner_id, group_id FROM prompt_templates WHERE id = $1::uuid",
            template_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="模板不存在")
        # 权限校验
        if existing["scope"] == "group" and user.role != "admin":
            raise HTTPException(status_code=403, detail="仅管理员可修改组级模板")
        if existing["scope"] == "user" and existing["owner_id"] != user.id:
            raise HTTPException(status_code=403, detail="只能修改自己的模板")

        # 动态构建 UPDATE
        set_clauses = []
        params = []
        idx = 1
        if body.name is not None:
            set_clauses.append(f"name = ${idx}")
            params.append(body.name)
            idx += 1
        if body.system_prompt is not None:
            set_clauses.append(f"system_prompt = ${idx}")
            params.append(body.system_prompt)
            idx += 1
        if body.is_active is not None:
            set_clauses.append(f"is_active = ${idx}")
            params.append(body.is_active)
            idx += 1
        if not set_clauses:
            raise HTTPException(status_code=422, detail="至少需要提供一个更新字段")
        set_clauses.append(f"updated_at = NOW()")
        params.append(template_id)

        await conn.execute(
            f"UPDATE prompt_templates SET {', '.join(set_clauses)} WHERE id = ${idx}::uuid",
            *params,
        )
    return {"status": "updated"}


@app.delete("/api/prompt-templates/{template_id}")
async def delete_prompt_template(
    template_id: str,
    user: UserInfo = Depends(get_current_user),
):
    """删除指定的提示词模板（硬删除）。权限规则同更新。"""
    from app.storage.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT scope, owner_id FROM prompt_templates WHERE id = $1::uuid",
            template_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="模板不存在")
        if existing["scope"] == "group" and user.role != "admin":
            raise HTTPException(status_code=403, detail="仅管理员可删除组级模板")
        if existing["scope"] == "user" and existing["owner_id"] != user.id:
            raise HTTPException(status_code=403, detail="只能删除自己的模板")

        await conn.execute(
            "DELETE FROM prompt_templates WHERE id = $1::uuid",
            template_id,
        )
    return {"status": "deleted"}


@app.get("/api/prompt-templates/default")
async def get_default_prompt(user: UserInfo = Depends(get_current_user)):
    """获取当前用户实际使用的系统提示词（用于预览和调试）。

    返回活跃模板的提示词，若无自定义模板则返回默认 YAML 提示词。
    """
    from app.agent.prompts import main_agent_content
    from app.agent.main_agent import _get_custom_system_prompt

    custom = await _get_custom_system_prompt(user.id, user.group_id)
    if custom:
        return {"source": "custom", "system_prompt": custom}
    return {"source": "default", "system_prompt": main_agent_content["system_prompt"]}


# ── 健康检查端点 ──


@app.get("/api/health")
async def health_check():
    """健康检查（公开，无需认证）。"""
    db_status = "error"
    redis_status = "error"
    overall = "down"

    # 检查 DB
    try:
        from app.storage.db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
        db_status = "connected"
    except Exception:
        pass

    # 检查 Redis
    try:
        from app.storage.redis_client import get_redis_client
        redis = await get_redis_client()
        await redis.ping()
        redis_status = "connected"
    except Exception:
        pass

    # 综合判断
    if db_status == "connected" and redis_status == "connected":
        overall = "ok"
    elif db_status == "connected" or redis_status == "connected":
        overall = "degraded"

    http_status = 200 if overall in ("ok", "degraded") else 503

    return JSONResponse(
        status_code=http_status,
        content={
            "status": overall,
            "db": db_status,
            "redis": redis_status,
            "version": settings.APP_VERSION,
        },
    )


@app.get("/live")
async def liveness():
    """存活探针 — K8s livenessProbe。仅返回 200，不检查任何依赖。"""
    return {"status": "alive"}


@app.get("/ready")
async def readiness():
    """就绪探针 — K8s readinessProbe。检查所有依赖。"""
    result = await health_check()
    if result.status_code == 503:
        raise HTTPException(status_code=503, detail="服务未就绪")
    return result


# ── Worker 健康检查 ──


@app.get("/api/workers/health")
async def worker_health(user: UserInfo = Depends(require_admin)):
    """查询所有在线 ARQ Worker 进程的状态（仅管理员可访问）。

    每个 Worker 进程通过 Redis 心跳定期上报状态，TTL 过期自动消失。
    可用于监控多 Worker 分布式部署的健康状况。
    """
    from app.storage.redis_client import get_worker_health, get_active_task_ids

    workers = await get_worker_health()
    active_task_ids = await get_active_task_ids()

    total_active_jobs = sum(w.get("active_jobs", 0) for w in workers)
    total_capacity = len(workers) * settings.WORKER_MAX_JOBS

    return {
        "workers": workers,
        "worker_count": len(workers),
        "total_active_jobs": total_active_jobs,
        "total_capacity": total_capacity,
        "utilization": round(total_active_jobs / total_capacity, 2) if total_capacity > 0 else 0,
        "active_task_ids": active_task_ids,
        "redis_mode": "sentinel" if settings.REDIS_SENTINEL_HOST_LIST else "standalone",
    }


# ── Prometheus 指标端点 ──


@app.get("/metrics")
async def metrics(user: UserInfo = Depends(require_admin)):
    """Prometheus 指标端点（仅管理员可访问）。"""
    from app.metrics import get_metrics
    return Response(
        content=get_metrics(),
        media_type="text/plain; charset=utf-8",
    )


if __name__ == "__main__":
    uvicorn.run("app.api.server:app", host="0.0.0.0", port=8000, reload=True)
