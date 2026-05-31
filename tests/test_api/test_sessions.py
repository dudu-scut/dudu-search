"""Session API 端点测试 — 会话列表 / 删除。"""

import pytest


def _mock_conn_for_db(mock_db_pool):
    """获取 mock 连接对象。"""
    return mock_db_pool.acquire.return_value.__aenter__.return_value


class TestListSessions:
    """GET /api/sessions 相关测试。"""

    async def test_list_sessions_requires_auth(self, test_app):
        """未认证时返回 422（Header 依赖为必填）。"""
        resp = await test_app.get("/api/sessions")
        assert resp.status_code == 422

    async def test_list_sessions_authenticated(self, test_app, mock_db_pool, auth_headers):
        """已认证用户可获取会话列表。"""
        conn = _mock_conn_for_db(mock_db_pool)
        # 模拟返回一组会话行
        conn.fetch.return_value = [
            {
                "thread_id": "thread-001",
                "title": "测试会话",
                "status": "completed",
                "started_at": None,
                "completed_at": None,
                "message_count": 3,
            },
            {
                "thread_id": "thread-002",
                "title": "另一个会话",
                "status": "running",
                "started_at": None,
                "completed_at": None,
                "message_count": 1,
            },
        ]

        resp = await test_app.get("/api/sessions", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert len(data["sessions"]) == 2
        assert data["sessions"][0]["thread_id"] == "thread-001"
        assert data["sessions"][0]["title"] == "测试会话"
        assert data["sessions"][0]["status"] == "completed"

    async def test_list_sessions_empty(self, test_app, mock_db_pool, auth_headers):
        """无会话时返回空列表。"""
        conn = _mock_conn_for_db(mock_db_pool)
        conn.fetch.return_value = []

        resp = await test_app.get("/api/sessions", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert len(data["sessions"]) == 0

    async def test_list_sessions_with_limit_offset(self, test_app, mock_db_pool, auth_headers):
        """支持分页参数 limit / offset。"""
        conn = _mock_conn_for_db(mock_db_pool)
        conn.fetch.return_value = []

        resp = await test_app.get(
            "/api/sessions?limit=10&offset=5", headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["sessions"] == []


class TestDeleteSession:
    """DELETE /api/sessions/{thread_id} 相关测试。"""

    async def test_delete_session_requires_auth(self, test_app):
        """未认证时返回 422。"""
        resp = await test_app.delete("/api/sessions/thread-001")
        assert resp.status_code == 422

    async def test_delete_session_authenticated(self, test_app, mock_db_pool, auth_headers):
        """已认证用户可删除会话。"""
        conn = _mock_conn_for_db(mock_db_pool)
        conn.execute.return_value = "DELETE 1"

        resp = await test_app.delete(
            "/api/sessions/thread-001", headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["thread_id"] == "thread-001"

    async def test_delete_session_not_found(self, test_app, mock_db_pool, auth_headers):
        """删除不存在的会话返回 404。"""
        conn = _mock_conn_for_db(mock_db_pool)
        conn.execute.return_value = "DELETE 0"

        resp = await test_app.delete(
            "/api/sessions/nonexistent", headers=auth_headers
        )

        assert resp.status_code == 404
