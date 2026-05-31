"""WebSocket 端点测试 — 连接 / 心跳。"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def ws_test_client():
    """创建支持 WebSocket 的 FastAPI TestClient。

    TestClient 不通过完整的 ASGI lifespan，因此需要显式 mock 依赖。
    """
    with patch("app.storage.db.asyncpg.create_pool") as mock_create_db:
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="OK")
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetchval = AsyncMock(return_value=None)
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_pool.acquire.return_value.__aexit__.return_value = None
        mock_create_db.return_value = mock_pool

        with patch("app.storage.db.get_pool", AsyncMock(return_value=mock_pool)):
            with patch("app.storage.redis_client.get_redis") as mock_get_redis:
                mock_redis = AsyncMock()
                mock_redis.ping = AsyncMock(return_value=True)
                mock_redis.get = AsyncMock(return_value=None)
                mock_redis.set = AsyncMock(return_value=True)
                mock_redis.lrange = AsyncMock(return_value=[])
                mock_redis.zadd = AsyncMock(return_value=1)
                mock_redis.zcard = AsyncMock(return_value=0)
                mock_redis.zremrangebyscore = AsyncMock(return_value=0)
                mock_redis.expire = AsyncMock(return_value=True)
                mock_get_redis.return_value = mock_redis

                with patch("app.api.server.create_pool", AsyncMock(return_value=AsyncMock())):
                    # Mock slowapi Limiter — 避免连接 Redis
                    import slowapi
                    import slowapi.extension
                    _Base = slowapi.extension.Limiter

                    class _NoopLimiter(_Base):
                        def __init__(self, *args, **kwargs):
                            pass

                        def limit(self, *a, **kw):
                            def _d(f):
                                return f
                            return _d

                        def shared_limit(self, *a, **kw):
                            def _d(f):
                                return f
                            return _d

                        def exempt(self, *a, **kw):
                            def _d(f):
                                return f
                            return _d

                    with patch("slowapi.Limiter", _NoopLimiter), \
                         patch.object(slowapi.extension, "Limiter", _NoopLimiter):
                        from fastapi.testclient import TestClient
                        from app.api.server import app

                        client = TestClient(app)
                        yield client


class TestWebSocket:
    """/ws/{thread_id} 端点测试。"""

    def test_websocket_connects_and_pong(self, ws_test_client):
        """WebSocket 连接成功，发送 ping 可收到 pong。"""
        thread_id = "test-ws-thread-001"

        with ws_test_client.websocket_connect(f"/ws/{thread_id}") as ws:
            # 连接已建立
            ws.send_text("ping")
            response = ws.receive_text()
            assert response == "pong"

    def test_websocket_accepts_connection(self, ws_test_client):
        """不同 thread_id 的 WebSocket 均能连接。"""
        thread_id = "test-ws-thread-002"

        with ws_test_client.websocket_connect(f"/ws/{thread_id}") as ws:
            # 验证连接成功（无异常即为成功）
            assert ws
