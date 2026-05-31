"""Pytest fixtures — 测试数据库、Redis、FastAPI 客户端。

所有外部依赖通过 Mock 隔离，单元测试不连真实服务。
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── 环境变量：必须在任何 app 导入之前设置 ──
# pydantic-settings 在 Settings() 构造时读取环境变量，且 _validate_required
# 会在 OPENAI_API_KEY 缺失时触发 SystemExit，因此必须在模块顶层注入。
os.environ.setdefault("OPENAI_API_KEY", "test-api-key-for-tests")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-tests")
os.environ.setdefault("TAVILY_API_KEY", "test-tavily-key-for-tests")


@pytest.fixture(scope="session")
def event_loop():
    """创建 session 级别的 event loop。"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_db_pool():
    """Mock asyncpg 连接池 — 替代真实的 PostgreSQL 连接。"""
    with patch("app.storage.db.asyncpg.create_pool") as mock_create:
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="OK")
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetchval = AsyncMock(return_value=None)

        # asyncpg.Pool.acquire() 不是协程函数，是返回异步上下文管理器的普通函数
        _ctx_mgr = MagicMock()
        _ctx_mgr.__aenter__ = AsyncMock(return_value=mock_conn)
        _ctx_mgr.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire = MagicMock(return_value=_ctx_mgr)

        mock_create.return_value = mock_pool

        # 使 get_pool() 返回 mock pool
        with patch("app.storage.db.get_pool", AsyncMock(return_value=mock_pool)):
            yield mock_pool


@pytest.fixture
def mock_redis():
    """Mock Redis 客户端 — 替代真实的 Redis 连接。"""
    with patch("app.storage.redis_client.get_redis") as mock_get:
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(return_value=True)
        mock_client.get = AsyncMock(return_value=None)
        mock_client.set = AsyncMock(return_value=True)
        mock_client.delete = AsyncMock(return_value=1)
        mock_client.incr = AsyncMock(return_value=1)
        mock_client.decr = AsyncMock(return_value=0)
        mock_client.expire = AsyncMock(return_value=True)
        mock_client.lpush = AsyncMock(return_value=1)
        mock_client.lrange = AsyncMock(return_value=[])
        mock_client.ltrim = AsyncMock(return_value=True)
        mock_client.zadd = AsyncMock(return_value=1)
        mock_client.zcard = AsyncMock(return_value=0)
        mock_client.zremrangebyscore = AsyncMock(return_value=0)
        mock_client.pipeline.return_value = mock_client
        mock_get.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_limiter():
    """Mock slowapi Limiter — 测试中完全禁用限流检查。

    必须 patch ``slowapi.Limiter``（server.py 的 import 来源）以及
    ``slowapi.extension.Limiter``（slowapi 内部的存储查找路径）。
    """
    import slowapi
    import slowapi.extension as slowapi_ext

    # 保存原始类以便继承
    _Base = slowapi_ext.Limiter

    class NoopLimiter(_Base):
        """无操作限流器：limit() / shared_limit() / exempt() 均返回原函数。"""

        def __init__(self, *args, **kwargs):
            pass

        def limit(self, *args, **kwargs):
            def _decorator(func):
                return func
            return _decorator

        def shared_limit(self, *args, **kwargs):
            def _decorator(func):
                return func
            return _decorator

        def exempt(self, *args, **kwargs):
            def _decorator(func):
                return func
            return _decorator

        def reset(self, *args, **kwargs):
            pass

    with patch("slowapi.Limiter", NoopLimiter), \
         patch.object(slowapi_ext, "Limiter", NoopLimiter):
        yield


@pytest.fixture
def mock_arq():
    """Mock ARQ 任务队列客户端 — 替代真实的 Redis 连接。"""
    mock_arq_pool = AsyncMock()
    mock_arq_pool.enqueue_job = AsyncMock(return_value=AsyncMock(job_id="mock-job-id"))
    mock_arq_pool.get_job = AsyncMock(return_value=None)
    mock_arq_pool.close = AsyncMock()

    with patch("app.api.server.create_pool", AsyncMock(return_value=mock_arq_pool)):
        yield mock_arq_pool


@pytest.fixture
async def test_app(mock_db_pool, mock_redis, mock_limiter, mock_arq):
    """创建 FastAPI TestClient（异步）。

    依赖 mock_db_pool / mock_redis / mock_arq，确保服务器初始化时不连接真实服务。
    同时重新打补丁 app.api.server.get_pool，避免模块级 import 缓存指向旧 mock。
    """
    from unittest.mock import AsyncMock, patch
    from httpx import ASGITransport, AsyncClient

    from app.api.server import app
    import app.api.server as server_module

    # server.py 在模块级执行了 ``from app.storage.db import get_pool``，
    # 首个 test 导入时能抓到 mock_db_pool 的正确 mock，但后续 test 中
    # Python 模块缓存会让 server.get_pool 仍指向第一个 fixture 的 mock。
    # 这里在每次 test 开始时都把 server.get_pool 重新指向当前 mock。
    with patch.object(server_module, "get_pool", AsyncMock(return_value=mock_db_pool)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
def auth_headers():
    """生成测试用 JWT Authorization header（普通用户）。"""
    from app.auth.jwt import create_access_token

    token = create_access_token(
        user_id="test-user-id",
        username="testuser",
        role="user",
        group_id=1,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers():
    """生成管理员测试用 JWT Authorization header。"""
    from app.auth.jwt import create_access_token

    token = create_access_token(
        user_id="admin-id",
        username="admin",
        role="admin",
        group_id=1,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_llm_response():
    """Mock DeepSeek LLM 响应 — 避免测试中真实调用 LLM API。"""
    mock_response = AsyncMock()
    mock_response.content = "这是模拟的 LLM 回复"
    mock_response.tool_calls = []

    with patch(
        "app.agent.main_agent._retryable_llm_invoke",
        AsyncMock(return_value=mock_response),
    ):
        yield mock_response
