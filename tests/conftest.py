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
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="OK")
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetchval = AsyncMock(return_value=None)
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_pool.acquire.return_value.__aexit__.return_value = None
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
async def test_app(mock_db_pool, mock_redis):
    """创建 FastAPI TestClient（异步）。

    依赖 mock_db_pool / mock_redis，确保服务器初始化时不连接真实服务。
    """
    from httpx import ASGITransport, AsyncClient

    from app.api.server import app

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
