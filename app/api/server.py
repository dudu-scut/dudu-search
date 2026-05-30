"""
FastAPI 接口层与项目闭环入口

负责承接前端的任务提交、任务取消、文件上传/下载、输出文件列表查询和
WebSocket 长连接。HTTP 接口只做轻量调度，真正的 DeepAgents 执行放到后台
任务中；执行进度、工具调用和最终结果由 monitor 按 thread_id 推送给前端。
"""

import asyncio
import mimetypes
import os
import re
import shutil
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
from fastapi.responses import FileResponse, JSONResponse
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
)
from app.storage.db import get_pool, init_schema, close_pool

logger = get_logger("server")

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

    yield  # 服务运行中

    # 服务关闭时：清理资源
    logger.info("正在关闭服务...")

    # 1. 取消所有正在执行的后台任务
    active_ids = []
    try:
        active_ids = await get_active_task_ids()
    except Exception:
        pass
    logger.info("正在取消活跃任务", count=len(active_ids))
    for thread_id in active_ids:
        if thread_id in active_tasks:
            task = active_tasks[thread_id]
            if not task.done():
                try:
                    task.cancel()
                except Exception as e:
                    logger.warning("取消任务失败", thread_id=thread_id, exc_info=True)

    if active_tasks:
        try:
            await asyncio.sleep(0.5)
        except:
            pass

    # 2. 清理任务注册表
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


# ── 全局 QPS 限流中间件 ──

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """基于 Redis 滑动窗口的全局 QPS 限流。

    跳过: /api/health, /live, /ready, /metrics, /ws, OPTIONS 请求
    """
    path = request.url.path

    # 跳过不需要限流的路径
    if path in ("/api/health", "/live", "/ready", "/metrics"):
        return await call_next(request)
    if path.startswith("/ws") or request.method == "OPTIONS":
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
    """用户登录（限流：每 IP 每分钟 5 次）。"""
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

    if row is None:
        raise AuthError("用户名或密码错误")
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
    thread_id: str = None


def _forget_task(thread_id: str, task: asyncio.Task) -> None:
    """
    清理已结束任务的登记关系。

    done_callback 触发时，active_tasks 中可能已经被新任务替换；只有仍是同一个
    task 时才删除，避免误清理同 thread_id 下刚启动的新任务。
    """
    if active_tasks.get(thread_id) is task:
        active_tasks.pop(thread_id, None)
        # 异步从 Redis 移除
        asyncio.ensure_future(unregister_active_task(thread_id))


@app.post("/api/task")
async def run_task(request: TaskRequest, user: UserInfo = Depends(get_current_user)):
    """
    启动一次 DeepAgents 后台任务。

    任务通过 ARQ 入队到 Worker 异步执行，HTTP 请求只负责提交并立即返回。
    后续执行轨迹、子智能体调用和最终答案都会由 monitor 通过 `/ws/{thread_id}`
    推送给同一会话的前端。
    """
    thread_id = request.thread_id or str(uuid.uuid4())

    if not request.query.strip():
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
            thread_id, request.query[:100], user.id, user.group_id,
        )

    # 入队到 ARQ Worker
    arq_client = request.app.state.arq_client
    job = await arq_client.enqueue_job(
        "run_agent_task", request.query, thread_id, user.id, user.group_id,
    )

    logger.info("任务已入队", thread_id=thread_id, job_id=job.job_id)

    return {
        "thread_id": thread_id,
        "task_id": job.job_id,
        "status": "queued",
    }


@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(thread_id: str, user: UserInfo = Depends(get_current_user)):
    """
    取消指定 thread_id 对应的后台 Agent 任务。

    注意：取消会向 asyncio.Task 注入 CancelledError。若底层第三方工具正在执行不可中断
    的同步阻塞调用，任务可能需要等该调用返回后才会真正结束。
    """
    task = active_tasks.get(thread_id)
    if not task or task.done():
        active_tasks.pop(thread_id, None)
        raise HTTPException(status_code=404, detail="任务不存在或已结束")

    # 先发出取消信号，再短暂等待协程响应；若底层阻塞中，则返回 cancelling 给前端继续展示状态
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        _forget_task(thread_id, task)
        return {"status": "cancelled", "thread_id": thread_id}
    except asyncio.TimeoutError:
        return {"status": "cancelling", "thread_id": thread_id}
    except Exception as e:
        _forget_task(thread_id, task)
        return {"status": "cancelled", "thread_id": thread_id, "message": str(e)}

    _forget_task(thread_id, task)
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
    target_dir = updated_dir / f"session_{thread_id}"
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for file in files:
        # 安全校验：读取并检查文件
        content = await file.read()
        error = validate_upload_file(
            file.filename or "", file.content_type or "", len(content)
        )
        if error:
            raise ValidationError(error)

        file_path = target_dir / file.filename
        with file_path.open("wb") as buffer:
            buffer.write(content)
        saved_files.append(file.filename)

    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(
    path: str,
    user: UserInfo = Depends(get_current_user),
):
    """
    文件下载接口 (File Download)。

    目标：
    1. 根据绝对路径下载文件。
    2. 严格的安全检查，防止越权访问。

    Args:
        path (str): 文件的绝对路径 (通常从 list_files 接口获取)。
    """
    try:
        # resolve 后再做 is_relative_to，防止 `../` 之类的路径穿越到 output 之外
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        if not abs_path.is_relative_to(output_abs):
            raise PermissionDeniedError("拒绝访问: 只能下载输出目录下的文件")
    except PermissionDeniedError:
        raise
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
    expires = int(time.time()) + 3600
    token = create_access_token(
        user_id=user_id,
        username="download",
        role="user",
        expires_delta=timedelta(hours=1),
    )
    return f"/api/download?path={file_path}&token={token}&expires={expires}"


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
        # 和下载接口保持同一条安全边界：前端只能查看 output 目录内部内容
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        if not abs_path.is_relative_to(output_abs):
            logger.warning("拒绝访问: 路径不在 output 目录下", abs_path=str(abs_path), output_abs=str(output_abs))
            return {"error": "拒绝访问: 只能访问输出目录下的文件"}

    except Exception as e:
        logger.warning("路径解析失败", error=str(e))
        return {"error": f"路径无效: {e}"}

    if not abs_path.exists():
        return {"error": "目录不存在"}

    files = []
    try:
        # 递归返回文件元数据，前端据此渲染文件列表并发起下载请求
        for file_path in abs_path.rglob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                files.append(
                    {
                        "name": file_path.name,
                        "type": "file",
                        "path": str(file_path),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
                )

    except Exception as e:
        logger.warning("遍历文件失败", error=str(e))
        return {"error": str(e)}

    # 最新生成的文件排在前面，方便用户优先看到本次任务产物
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    logger.debug("文件列表查询完成", count=len(files))
    return {"files": files}


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """
    WebSocket 实时通讯核心接口 (Real-time Communication)。

    连接建立后，ConnectionManager 会用 thread_id 保存 WebSocket。monitor 后续
    发送事件时只需要按 thread_id 查找连接，就能把进度推给对应页面。循环中的
    receive_text 用于接收前端心跳，避免连接空闲断开。
    """
    logger.info("WebSocket 连接请求", thread_id=thread_id)

    # 连接建立后立即按 thread_id 注册，monitor 后续才能把事件定向推给当前页面
    await manager.connect(websocket, thread_id)

    try:
        while True:
            # 前端通常发送 ping 心跳；服务端回复 pong，顺便维持连接活跃
            data = await websocket.receive_text()
            await websocket.send_json(
                {"type": "pong", "message": f"服务端已收到: {data}"}
            )

    except WebSocketDisconnect:
        # 只移除当前 WebSocket 实例，避免旧连接断开时误删同 thread_id 的新连接
        manager.disconnect(websocket, thread_id)
        logger.info("WebSocket 客户端已断开", thread_id=thread_id)

    except Exception as e:
        logger.warning("WebSocket 连接异常", exc_info=True)
        manager.disconnect(websocket, thread_id)


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
        group_id, group_filter = get_group_filter(user)
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
                   WHERE {group_filter}
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
        group_id, group_filter = get_group_filter(user)
        pool = await get_pool()
        async with pool.acquire() as conn:
            # 会话基本信息（带组过滤）
            params = [thread_id]
            if group_id is not None:
                params.append(group_id)
                group_clause = f"AND s.{group_filter.replace('$1', f'${len(params)}')}"
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
        group_id, group_filter = get_group_filter(user)
        pool = await get_pool()
        async with pool.acquire() as conn:
            params = [thread_id]
            if group_id is not None:
                params.append(group_id)
                group_clause = f"AND {group_filter.replace('$1', f'${len(params)}')}"
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
    """列出长期记忆。"""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if memory_type:
                rows = await conn.fetch(
                    """SELECT id, memory_type, content, importance, access_count,
                               last_accessed, source_thread_id, created_at
                       FROM long_term_memories
                       WHERE memory_type = $1
                       ORDER BY created_at DESC
                       LIMIT $2 OFFSET $3""",
                    memory_type, limit, offset,
                )
            else:
                rows = await conn.fetch(
                    """SELECT id, memory_type, content, importance, access_count,
                               last_accessed, source_thread_id, created_at
                       FROM long_term_memories
                       ORDER BY created_at DESC
                       LIMIT $1 OFFSET $2""",
                    limit, offset,
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
    """删除指定记忆。"""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM long_term_memories WHERE id = $1::uuid", memory_id
            )
            if result == "DELETE 0":
                raise HTTPException(status_code=404, detail="记忆不存在")
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
    filter_group_id = None if user.is_admin else user.group_id
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

    doc_dir = Path(DOC_STORE_DIR) / kb_name
    doc_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for file in files:
        file_path = doc_dir / file.filename
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        try:
            n = engine.ingest_file(kb_name, str(file_path))
            results[file.filename] = f"摄入成功，共 {n} 个文本块"
        except Exception as e:
            results[file.filename] = f"摄入失败: {str(e)}"

    return {"status": "done", "kb_name": kb_name, "results": results}


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


if __name__ == "__main__":
    uvicorn.run("app.api.server:app", host="0.0.0.0", port=8000, reload=True)
