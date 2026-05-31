"""Auth API 端点测试 — 注册 / 登录 / 获取当前用户信息。"""

import pytest
from app.auth.jwt import hash_password


def _mock_conn_for_db(mock_db_pool):
    """获取 mock 连接对象，方便配置 fetchval / fetchrow 返回值。"""
    return mock_db_pool.acquire.return_value.__aenter__.return_value


class TestRegister:
    """POST /api/auth/register 相关测试。"""

    async def test_register_success(self, test_app, mock_db_pool):
        """注册成功：返回 JWT token 和用户信息。"""
        conn = _mock_conn_for_db(mock_db_pool)
        # 第一次 fetchval 检查用户名是否已存在 → None
        # 第二次 fetchval 插入用户 → 返回 user_id
        conn.fetchval.side_effect = [None, "new-user-uuid-123"]
        conn.fetchrow.return_value = None

        resp = await test_app.post(
            "/api/auth/register",
            json={"username": "newuser", "password": "pass123", "email": "a@b.com"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["user"]["username"] == "newuser"
        assert data["user"]["role"] == "user"
        assert data["user"]["group_id"] == 1

    async def test_register_username_too_short(self, test_app, mock_db_pool):
        """用户名少于 3 个字符被拒绝。"""
        resp = await test_app.post(
            "/api/auth/register",
            json={"username": "ab", "password": "pass123"},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "VALIDATION_ERROR"
        assert "3" in data["error"]["message"]

    async def test_register_password_too_short(self, test_app, mock_db_pool):
        """密码少于 6 个字符被拒绝。"""
        resp = await test_app.post(
            "/api/auth/register",
            json={"username": "validuser", "password": "12345"},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "VALIDATION_ERROR"
        assert "6" in data["error"]["message"]

    async def test_register_invalid_username_chars(self, test_app, mock_db_pool):
        """用户名包含非法字符被拒绝。"""
        resp = await test_app.post(
            "/api/auth/register",
            json={"username": "user@name", "password": "pass123"},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "VALIDATION_ERROR"

    async def test_register_duplicate_username(self, test_app, mock_db_pool):
        """重复用户名被拒绝。"""
        conn = _mock_conn_for_db(mock_db_pool)
        # 第一次 fetchval 返回已有用户 ID
        conn.fetchval.return_value = "existing-user-id"

        resp = await test_app.post(
            "/api/auth/register",
            json={"username": "existinguser", "password": "pass123"},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "VALIDATION_ERROR"
        assert "已存在" in data["error"]["message"]


class TestLogin:
    """POST /api/auth/login 相关测试。"""

    async def test_login_success(self, test_app, mock_db_pool):
        """登录成功返回 JWT token。"""
        password = "correct-password"
        password_hash_val = hash_password(password)

        conn = _mock_conn_for_db(mock_db_pool)
        conn.fetchrow.return_value = {
            "id": "user-id-1",
            "username": "testuser",
            "password_hash": password_hash_val,
            "role": "user",
            "group_id": 1,
            "is_active": True,
        }

        resp = await test_app.post(
            "/api/auth/login",
            json={"username": "testuser", "password": password},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["user"]["username"] == "testuser"
        assert data["user"]["role"] == "user"

    async def test_login_wrong_password(self, test_app, mock_db_pool):
        """密码错误返回 401。"""
        password_hash_val = hash_password("correct-password")

        conn = _mock_conn_for_db(mock_db_pool)
        conn.fetchrow.return_value = {
            "id": "user-id-1",
            "username": "testuser",
            "password_hash": password_hash_val,
            "role": "user",
            "group_id": 1,
            "is_active": True,
        }

        resp = await test_app.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "wrong-password"},
        )

        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "AUTH_ERROR"

    async def test_login_user_not_found(self, test_app, mock_db_pool):
        """用户不存在返回 401。"""
        conn = _mock_conn_for_db(mock_db_pool)
        conn.fetchrow.return_value = None

        resp = await test_app.post(
            "/api/auth/login",
            json={"username": "nonexistent", "password": "somepass"},
        )

        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "AUTH_ERROR"

    async def test_login_inactive_user(self, test_app, mock_db_pool):
        """已禁用账户返回 401。"""
        password_hash_val = hash_password("somepass")

        conn = _mock_conn_for_db(mock_db_pool)
        conn.fetchrow.return_value = {
            "id": "user-id-1",
            "username": "disableduser",
            "password_hash": password_hash_val,
            "role": "user",
            "group_id": 1,
            "is_active": False,
        }

        resp = await test_app.post(
            "/api/auth/login",
            json={"username": "disableduser", "password": "somepass"},
        )

        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "AUTH_ERROR"
        assert "禁用" in data["error"]["message"]


class TestGetMe:
    """GET /api/auth/me 相关测试。"""

    async def test_me_without_token(self, test_app):
        """无 token 时 FastAPI 返回 422（Header 依赖为必填）。"""
        resp = await test_app.get("/api/auth/me")
        # FastAPI 对缺少必填 Header 依赖返回 422
        assert resp.status_code == 422

    async def test_me_invalid_token(self, test_app):
        """无效 token 返回 401。"""
        resp = await test_app.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "AUTH_ERROR"

    async def test_me_with_valid_token(self, test_app, auth_headers):
        """有效 token 返回用户信息。"""
        resp = await test_app.get("/api/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "testuser"
        assert data["role"] == "user"
        assert data["group_id"] == 1

    async def test_me_admin_token(self, test_app, admin_headers):
        """管理员 token 返回管理员信息。"""
        resp = await test_app.get("/api/auth/me", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"
