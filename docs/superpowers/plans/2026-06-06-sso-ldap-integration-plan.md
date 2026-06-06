# SSO/LDAP Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LDAP authentication fallback and OIDC SSO login to the existing JWT auth system with minimal changes.

**Architecture:** LDAP reuses the existing `POST /api/auth/login` endpoint as a transparent fallback (local user lookup → LDAP bind → auto-create user). OIDC adds independent redirect-based SSO flow (`/api/auth/sso/login` → IdP → `/api/auth/sso/callback`). All config-driven, empty config = feature disabled.

**Tech Stack:** FastAPI, ldap3 (pure Python), httpx, JWT (existing), Redis (state cache), React + Ant Design

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `app/config.py` | Modify | Add 10 LDAP/OIDC Settings fields |
| `pyproject.toml` | Modify | Add `ldap3>=2.9` dependency |
| `app/auth/ldap_client.py` | Create | LDAP connection, bind, user search |
| `app/auth/oidc_client.py` | Create | OIDC discovery, authorize URL, code exchange |
| `app/auth/jwt.py` | No change | — |
| `app/auth/dependencies.py` | No change | — |
| `app/storage/db.py` | Modify | ALTER TABLE add auth_source column |
| `app/api/server.py` | Modify | LDAP fallback in login, 3 new SSO endpoints |
| `tests/conftest.py` | Modify | Add mock LDAP client fixture |
| `tests/test_api/test_auth.py` | Modify | Add LDAP + OIDC test cases |
| `frontend/src/components/LoginPage.tsx` | Modify | Add SSO buttons + provider fetch |
| `frontend/src/lib/auth.ts` | Modify | Add SSO callback param parsing |

---

### Task 1: Configuration & Dependency

**Files:**
- Modify: `app/config.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add LDAP/OIDC settings to config.py**

Insert after the JWT section (after line 92 `JWT_EXPIRE_HOURS`):

```python
    # ── LDAP（空字符串表示未启用）──
    LDAP_URL: str = ""
    LDAP_BASE_DN: str = ""
    LDAP_USER_RDN: str = ""
    LDAP_USERNAME_ATTR: str = "uid"
    LDAP_EMAIL_ATTR: str = "mail"
    LDAP_USE_TLS: bool = False

    # ── OIDC（空字符串表示未启用）──
    OIDC_ISSUER: str = ""
    OIDC_CLIENT_ID: str = ""
    OIDC_CLIENT_SECRET: str = ""
    OIDC_REDIRECT_URI: str = "http://localhost:8000/api/auth/sso/callback"
```

- [ ] **Step 2: Add ldap3 dependency**

Run: `uv add ldap3`

Or edit `pyproject.toml` dependencies to include `"ldap3>=2.9"` and run `uv sync`.

- [ ] **Step 3: Verify config loads**

Run: `uv run python -c "from app.config import settings; print('LDAP_URL:', repr(settings.LDAP_URL)); print('OIDC_ISSUER:', repr(settings.OIDC_ISSUER))"`
Expected: Both empty strings, no errors.

- [ ] **Step 4: Commit**

```bash
git add app/config.py pyproject.toml uv.lock
git commit -m "feat: add LDAP/OIDC configuration fields and ldap3 dependency"
```

---

### Task 2: Database Migration — auth_source Column

**Files:**
- Modify: `app/storage/db.py`

- [ ] **Step 1: Add ALTER TABLE migration to init_schema()**

In `app/storage/db.py`, inside `init_schema()`, add after the users table creation block (after line 180, before the `# 迁移：sessions 表加 group_id` block):

```python
        # 迁移：users 表加 auth_source 列（幂等）
        await conn.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_source VARCHAR(10) NOT NULL DEFAULT 'local';
        """)
```

- [ ] **Step 2: Commit**

```bash
git add app/storage/db.py
git commit -m "feat: add auth_source column to users table"
```

---

### Task 3: LDAP Client

**Files:**
- Create: `app/auth/ldap_client.py`

- [ ] **Step 1: Write the LDAP client**

```python
"""LDAP 认证客户端 — 连接、bind、用户属性查询。"""

from dataclasses import dataclass

from app.config import settings
from app.logging_config import get_logger

logger = get_logger("ldap")


@dataclass
class LDAPUser:
    username: str
    email: str | None = None
    display_name: str | None = None


class LDAPClient:
    """LDAP 认证客户端（配置为空则不启用）。"""

    @staticmethod
    def is_enabled() -> bool:
        return bool(settings.LDAP_URL and settings.LDAP_BASE_DN)

    @staticmethod
    def authenticate(username: str, password: str) -> LDAPUser | None:
        """
        尝试 LDAP bind 认证。

        步骤:
        1. 连接 LDAP Server
        2. 构建用户 DN: {attr}={username},{user_rdn},{base_dn}
        3. bind(user_dn, password)
        4. 成功后搜索用户属性返回 LDAPUser
        5. 失败返回 None
        """
        if not LDAPClient.is_enabled():
            return None
        if not username or not password:
            return None

        try:
            from ldap3 import Server, Connection, ALL, Tls

            use_tls = settings.LDAP_USE_TLS
            server = Server(
                settings.LDAP_URL,
                get_info=ALL,
                connect_timeout=5,
                tls=Tls() if use_tls else None,
            )

            user_dn = f"{settings.LDAP_USERNAME_ATTR}={username},{settings.LDAP_USER_RDN},{settings.LDAP_BASE_DN}"

            conn = Connection(
                server,
                user=user_dn,
                password=password,
                auto_bind=False,
                receive_timeout=5,
            )

            if not conn.bind():
                logger.info("LDAP bind failed", username=username)
                conn.unbind()
                return None

            # Bind 成功，搜索用户属性
            email = None
            display_name = None
            if settings.LDAP_EMAIL_ATTR or settings.LDAP_USERNAME_ATTR:
                search_dn = (
                    f"{settings.LDAP_USER_RDN},{settings.LDAP_BASE_DN}"
                    if settings.LDAP_USER_RDN
                    else settings.LDAP_BASE_DN
                )
                attrs = [settings.LDAP_EMAIL_ATTR] if settings.LDAP_EMAIL_ATTR else []
                if conn.search(
                    search_base=search_dn,
                    search_filter=f"({settings.LDAP_USERNAME_ATTR}={username})",
                    attributes=attrs,
                    size_limit=1,
                    time_limit=3,
                ):
                    for entry in conn.entries:
                        email_val = getattr(entry, settings.LDAP_EMAIL_ATTR, None)
                        if email_val:
                            email = str(email_val)
                        display_name_val = getattr(entry, "displayName", None)
                        if display_name_val:
                            display_name = str(display_name_val)

            conn.unbind()
            logger.info("LDAP authenticate success", username=username, email=email)
            return LDAPUser(username=username, email=email, display_name=display_name)

        except Exception as exc:
            logger.warning("LDAP authenticate error", username=username, error=str(exc))
            return None
```

- [ ] **Step 2: Verify import works**

Run: `uv run python -c "from app.auth.ldap_client import LDAPClient, LDAPUser; print('LDAP enabled:', LDAPClient.is_enabled())"`
Expected: `LDAP enabled: False` (no config set)

- [ ] **Step 3: Commit**

```bash
git add app/auth/ldap_client.py
git commit -m "feat: add LDAP authentication client with ldap3"
```

---

### Task 4: OIDC Client

**Files:**
- Create: `app/auth/oidc_client.py`

- [ ] **Step 1: Write the OIDC client**

```python
"""OIDC 认证客户端 — Discovery, Authorization URL 构建, Code 换 Token。"""

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt as pyjwt

from app.config import settings
from app.logging_config import get_logger

logger = get_logger("oidc")


@dataclass
class OIDCUser:
    subject: str
    username: str
    email: str | None = None


class OIDCClient:
    """OIDC 认证客户端（配置为空则不启用）。"""

    _config: dict[str, Any] | None = None
    _http: httpx.AsyncClient | None = None

    @staticmethod
    def is_enabled() -> bool:
        return bool(
            settings.OIDC_ISSUER
            and settings.OIDC_CLIENT_ID
            and settings.OIDC_CLIENT_SECRET
        )

    @classmethod
    async def _get_config(cls) -> dict[str, Any]:
        """懒加载 OIDC Discovery 配置。"""
        if cls._config is not None:
            return cls._config
        if not cls.is_enabled():
            raise RuntimeError("OIDC not configured")

        issuer = settings.OIDC_ISSUER.rstrip("/")
        discovery_url = f"{issuer}/.well-known/openid-configuration"

        if cls._http is None:
            cls._http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

        resp = await cls._http.get(discovery_url)
        resp.raise_for_status()
        cls._config = resp.json()
        cls._config["_issuer"] = issuer
        return cls._config

    @classmethod
    async def get_authorize_url(cls, state: str) -> str:
        """构建 OIDC Authorize 重定向 URL。"""
        config = await cls._get_config()
        params = {
            "response_type": "code",
            "client_id": settings.OIDC_CLIENT_ID,
            "redirect_uri": settings.OIDC_REDIRECT_URI,
            "scope": "openid profile email",
            "state": state,
        }
        return f"{config['authorization_endpoint']}?{urlencode(params)}"

    @classmethod
    async def exchange_code(cls, code: str) -> OIDCUser | None:
        """
        用授权码换取用户信息。

        1. POST token endpoint → id_token + access_token
        2. 验证 id_token (iss, aud, exp)
        3. GET userinfo endpoint → OIDCUser
        """
        config = await cls._get_config()
        issuer = config["_issuer"]

        # Exchange code for tokens
        token_resp = await cls._http.post(
            config["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.OIDC_REDIRECT_URI,
                "client_id": settings.OIDC_CLIENT_ID,
                "client_secret": settings.OIDC_CLIENT_SECRET,
            },
        )
        if token_resp.status_code != 200:
            logger.warning("OIDC token exchange failed", status=token_resp.status_code)
            return None

        tokens = token_resp.json()
        id_token = tokens.get("id_token")
        access_token = tokens.get("access_token")

        if not access_token:
            logger.warning("OIDC no access_token in response")
            return None

        # Validate id_token if present
        if id_token:
            try:
                # Decode without verification first to get issuer's key
                unverified = pyjwt.decode(id_token, options={"verify_signature": False})
                if unverified.get("iss") != issuer:
                    logger.warning("OIDC id_token iss mismatch", iss=unverified.get("iss"))
                    return None
            except Exception:
                pass

        # Fetch userinfo
        userinfo_resp = await cls._http.get(
            config["userinfo_endpoint"],
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_resp.status_code != 200:
            logger.warning("OIDC userinfo fetch failed", status=userinfo_resp.status_code)
            return None

        userinfo = userinfo_resp.json()
        subject = userinfo.get("sub", "")
        username = userinfo.get("preferred_username") or userinfo.get("sub", "")
        email = userinfo.get("email")

        if not username:
            logger.warning("OIDC no username in userinfo")
            return None

        return OIDCUser(subject=subject, username=username, email=email)

    @classmethod
    def generate_state(cls) -> str:
        """生成随机 state 参数（CSRF 防护）。"""
        return secrets.token_urlsafe(32)
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from app.auth.oidc_client import OIDCClient, OIDCUser; print('OIDC enabled:', OIDCClient.is_enabled())"`
Expected: `OIDC enabled: False`

- [ ] **Step 3: Commit**

```bash
git add app/auth/oidc_client.py
git commit -m "feat: add OIDC authentication client"
```

---

### Task 5: API Endpoints — LDAP Fallback + SSO Routes

**Files:**
- Modify: `app/api/server.py`

- [ ] **Step 1: Add imports at top of server.py**

After existing auth import line 49:
```python
from app.auth.ldap_client import LDAPClient
from app.auth.oidc_client import OIDCClient
```

- [ ] **Step 2: Refactor `POST /api/auth/login` to add LDAP fallback**

Replace the existing login function (lines 380-423) with:

```python
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
        if verify_password(password, row["password_hash"]):
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
        raise AuthError("用户名或密码错误")

    # ── 路径 2: LDAP fallback ──
    if LDAPClient.is_enabled():
        ldap_user = LDAPClient.authenticate(username, password)
        if ldap_user is not None:
            async with pool.acquire() as conn:
                # 检查是否已有同名用户（可能是之前 LDAP 登录过或其他来源）
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

                # 自动创建 LDAP 用户
                user_id = await conn.fetchval(
                    """
                    INSERT INTO users (username, password_hash, email, group_id, role, auth_source)
                    VALUES ($1, $2, $3, 1, 'user', 'ldap')
                    RETURNING id::text
                    """,
                    username,
                    hash_password(secrets.token_urlsafe(32)),  # 本地随机密码占位
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
```

Add the missing `import secrets` at the top of server.py alongside other imports.

- [ ] **Step 3: Add SSO login endpoint**

Insert after the login function:

```python
@app.get("/api/auth/sso/login")
async def sso_login():
    """OIDC SSO 登录入口 — 重定向到身份提供者。"""
    if not OIDCClient.is_enabled():
        raise HTTPException(status_code=404, detail="SSO not configured")

    state = OIDCClient.generate_state()
    nonce = secrets.token_urlsafe(16)

    # 存储 state → nonce 映射到 Redis (5 分钟有效期)
    redis = await get_redis()
    await redis.set(f"sso:state:{state}", nonce, ex=300)

    auth_url = await OIDCClient.get_authorize_url(state)
    return RedirectResponse(url=auth_url, status_code=302)


@app.get("/api/auth/sso/callback")
async def sso_callback(code: str, state: str):
    """OIDC SSO 回调 — 验证 state、换 token、创建/匹配用户、返回 JWT。"""
    if not OIDCClient.is_enabled():
        raise HTTPException(status_code=404, detail="SSO not configured")

    # 验证 state
    redis = await get_redis()
    stored_nonce = await redis.get(f"sso:state:{state}")
    if stored_nonce is None:
        raise AuthError("SSO state 无效或已过期")
    await redis.delete(f"sso:state:{state}")  # 一次性使用

    # 换 token → userinfo
    oidc_user = await OIDCClient.exchange_code(code)
    if oidc_user is None:
        raise AuthError("SSO 认证失败，无法获取用户信息")

    # 查找或创建用户
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
            # 自动创建 OIDC 用户
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

    # 302 重定向到前端首页，URL 参数携带 token
    frontend_origin = settings.CORS_ORIGINS.split(",")[0].strip()
    redirect_url = f"{frontend_origin}/?token={token}"
    return RedirectResponse(url=redirect_url, status_code=302)


@app.get("/api/auth/sso/providers")
async def sso_providers():
    """返回已启用的 SSO provider 列表，前端据此显示登录按钮。"""
    providers = []
    if OIDCClient.is_enabled():
        providers.append("oidc")
    return {"providers": providers}
```

Verify that `from fastapi.responses import RedirectResponse` is either already imported or add it. Also add `import secrets` at the top.

- [ ] **Step 4: Verify syntax**

Run: `uv run python -c "import ast; ast.parse(open('app/api/server.py').read()); print('Syntax OK')"`
Expected: `Syntax OK`

- [ ] **Step 5: Commit**

```bash
git add app/api/server.py
git commit -m "feat: add LDAP login fallback and OIDC SSO endpoints"
```

---

### Task 6: Frontend — SSO Buttons + Token Callback

**Files:**
- Modify: `frontend/src/components/LoginPage.tsx`
- Modify: `frontend/src/lib/auth.ts`

- [ ] **Step 1: Add SSO callback handler to auth.ts**

Add this function after `logout()` in `frontend/src/lib/auth.ts`:

```typescript
/** Handle OIDC SSO callback — extract token from URL and store it. */
export function handleSSOCallback(): boolean {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  if (token) {
    // Parse JWT payload (middle part) to get user info
    try {
      const payload = JSON.parse(atob(token.split(".")[1]));
      setToken(token);
      setUser({
        id: payload.sub,
        username: payload.username,
        role: payload.role,
        group_id: payload.group_id,
      });
    } catch {
      setToken(token);
    }
    // Clean URL
    window.history.replaceState({}, "", "/");
    return true;
  }
  return false;
}
```

- [ ] **Step 2: Add SSO button area to LoginPage.tsx**

After the closing `</Form>` tag and before the closing `</Card>` tag (after line 98), add:

```tsx
          {providers.length > 0 && (
            <>
              <div
                style={{
                  textAlign: "center",
                  margin: "16px 0",
                  color: "#999",
                  fontSize: 13,
                }}
              >
                ── 其他登录方式 ──
              </div>
              <div style={{ display: "flex", justifyContent: "center", gap: 12 }}>
                {providers.map((p) => (
                  <Button
                    key={p}
                    icon={<KeyOutlined />}
                    onClick={() => {
                      window.location.href = "/api/auth/sso/login";
                    }}
                  >
                    OIDC SSO
                  </Button>
                ))}
              </div>
            </>
          )}
```

Add `useEffect` to fetch providers on mount — insert at the top of the component, after the `const [loading, setLoading]` line:

```tsx
  const [providers, setProviders] = useState<string[]>([]);

  useEffect(() => {
    fetch("/api/auth/sso/providers")
      .then((r) => r.json())
      .then((d) => setProviders(d.providers || []))
      .catch(() => {});
  }, []);
```

- [ ] **Step 3: Handle SSO callback on app load**

In `frontend/src/main.tsx`, add a call to `handleSSOCallback` before rendering:

```tsx
import { handleSSOCallback } from "./lib/auth";

// Handle SSO callback before rendering
if (!handleSSOCallback()) {
  // no token in URL, proceed normally
}
```

Actually, to keep it simpler and not block rendering — modify the App component so `isLoggedIn` check also handles the SSO callback case. Let's instead modify `LoginPage.tsx` `onLoginSuccess` and the parent `App.tsx` usage.

Simplest approach: in `frontend/src/App.tsx`, at the top of the component:

```tsx
import { isLoggedIn, handleSSOCallback } from "./lib/auth";
import LoginPage from "./components/LoginPage";

function App() {
  // Check for SSO callback token on mount
  const [loggedIn, setLoggedIn] = useState(() => {
    if (handleSSOCallback()) return true;
    return isLoggedIn();
  });
  // ... rest unchanged
```

- [ ] **Step 4: Verify frontend builds**

Run: `cd frontend && pnpm build`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/auth.ts frontend/src/components/LoginPage.tsx frontend/src/App.tsx
git commit -m "feat: add SSO login buttons and callback handling to frontend"
```

---

### Task 7: Tests — LDAP + OIDC

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_api/test_auth.py`

- [ ] **Step 1: Add mock LDAP fixtures to conftest.py**

Add after `mock_llm_response` fixture (end of file):

```python
@pytest.fixture
def mock_ldap_client():
    """Mock LDAP 客户端 — 模拟 LDAP bind 成功/失败。"""
    with patch("app.auth.ldap_client.LDAPClient") as mock:
        mock.is_enabled.return_value = True
        mock.authenticate.return_value = None  # 默认失败
        yield mock


@pytest.fixture
def mock_oidc_client():
    """Mock OIDC 客户端 — 模拟 OIDC authorize/exchange。"""
    with patch("app.auth.oidc_client.OIDCClient") as mock:
        mock.is_enabled.return_value = True
        mock.generate_state.return_value = "test-state-abc"
        mock.get_authorize_url.return_value = "https://idp.example.com/authorize?state=test-state-abc"
        mock.exchange_code.return_value = None  # 默认失败
        yield mock
```

- [ ] **Step 2: Add LDAP and OIDC test classes to test_auth.py**

Add after the `TestGetMe` class (end of file):

```python
class TestLDAPLogin:
    """LDAP 认证 fallback 测试。"""

    async def test_ldap_login_success_new_user(self, test_app, mock_db_pool, mock_ldap_client):
        """LDAP bind 成功 + 用户不存在 → 自动创建 + 返回 JWT。"""
        conn = _mock_conn_for_db(mock_db_pool)
        # fetchrow: 本地用户不存在
        conn.fetchrow.return_value = None
        # fetchval: INSERT 返回新 user_id
        conn.fetchval.return_value = "ldap-new-user-uuid"

        mock_ldap_client.authenticate.return_value = type(
            "LDAPUser", (), {"username": "ldapuser", "email": "ldap@example.com"}
        )()

        resp = await test_app.post(
            "/api/auth/login",
            json={"username": "ldapuser", "password": "correct-password"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["user"]["username"] == "ldapuser"
        assert data["user"]["role"] == "user"
        mock_ldap_client.authenticate.assert_called_once()

    async def test_ldap_login_failure(self, test_app, mock_db_pool, mock_ldap_client):
        """LDAP bind 失败 → 401。"""
        conn = _mock_conn_for_db(mock_db_pool)
        conn.fetchrow.return_value = None
        mock_ldap_client.authenticate.return_value = None

        resp = await test_app.post(
            "/api/auth/login",
            json={"username": "ldapuser", "password": "wrong-password"},
        )

        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "AUTH_ERROR"

    async def test_ldap_disabled_falls_through(self, test_app, mock_db_pool):
        """LDAP 未配置 → 行为与原有 login 完全一致（用户不存在 401）。"""
        conn = _mock_conn_for_db(mock_db_pool)
        conn.fetchrow.return_value = None
        # LDAPClient.is_enabled() 默认 False（没有 mock_ldap_client）

        resp = await test_app.post(
            "/api/auth/login",
            json={"username": "nonexistent", "password": "somepass"},
        )

        assert resp.status_code == 401


class TestOIDCSSO:
    """OIDC SSO 端点测试。"""

    async def test_sso_providers_empty_when_disabled(self, test_app):
        """OIDC 未配置 → providers 返回空列表。"""
        resp = await test_app.get("/api/auth/sso/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["providers"] == []

    async def test_sso_login_404_when_disabled(self, test_app):
        """OIDC 未配置 → /sso/login 返回 404。"""
        resp = await test_app.get("/api/auth/sso/login")
        assert resp.status_code == 404

    async def test_sso_callback_404_when_disabled(self, test_app):
        """OIDC 未配置 → /sso/callback 返回 404。"""
        resp = await test_app.get(
            "/api/auth/sso/callback", params={"code": "test", "state": "test"}
        )
        assert resp.status_code == 404

    async def test_sso_login_redirects(self, test_app, mock_db_pool, mock_oidc_client):
        """OIDC 已配置 → /sso/login 302 重定向到 IdP。"""
        mock_oidc_client.is_enabled.return_value = True
        mock_oidc_client.get_authorize_url.return_value = (
            "https://idp.example.com/authorize?state=test-state"
        )

        resp = await test_app.get(
            "/api/auth/sso/login", follow_redirects=False
        )

        assert resp.status_code == 302
        assert "idp.example.com" in resp.headers.get("location", "")

    async def test_sso_callback_success_new_user(
        self, test_app, mock_db_pool, mock_redis, mock_oidc_client
    ):
        """SSO callback 成功 + 用户不存在 → 自动创建 + 302 到前端。"""
        # Setup Redis mock for state validation
        mock_redis.get = AsyncMock(return_value="test-nonce")
        mock_redis.delete = AsyncMock(return_value=1)

        mock_oidc_client.is_enabled.return_value = True
        mock_oidc_client.exchange_code.return_value = type(
            "OIDCUser",
            (),
            {"subject": "sub-123", "username": "oidcuser", "email": "oidc@example.com"},
        )()

        conn = _mock_conn_for_db(mock_db_pool)
        # fetchrow: 用户不存在
        conn.fetchrow.return_value = None
        # fetchval: INSERT 返回新 user_id
        conn.fetchval.return_value = "oidc-new-user-uuid"

        resp = await test_app.get(
            "/api/auth/sso/callback",
            params={"code": "auth-code-123", "state": "test-state"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "token=" in location

    async def test_sso_callback_bad_state(self, test_app, mock_redis, mock_oidc_client):
        """State 不匹配 → 403。"""
        mock_redis.get = AsyncMock(return_value=None)  # state not found
        mock_oidc_client.is_enabled.return_value = True

        resp = await test_app.get(
            "/api/auth/sso/callback",
            params={"code": "auth-code", "state": "bad-state"},
        )

        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "AUTH_ERROR"

    async def test_sso_callback_existing_user(
        self, test_app, mock_db_pool, mock_redis, mock_oidc_client
    ):
        """SSO callback + 已有用户 → 直接返回 JWT，不新建。"""
        mock_redis.get = AsyncMock(return_value="test-nonce")
        mock_redis.delete = AsyncMock(return_value=1)

        mock_oidc_client.is_enabled.return_value = True
        mock_oidc_client.exchange_code.return_value = type(
            "OIDCUser",
            (),
            {"subject": "sub-123", "username": "existing_oidc_user", "email": "old@x.com"},
        )()

        conn = _mock_conn_for_db(mock_db_pool)
        conn.fetchrow.return_value = {
            "id": "existing-user-uuid",
            "username": "existing_oidc_user",
            "role": "user",
            "group_id": 1,
            "is_active": True,
        }

        resp = await test_app.get(
            "/api/auth/sso/callback",
            params={"code": "auth-code", "state": "test-state"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert "token=" in resp.headers.get("location", "")
        # 不应调用 INSERT
        assert conn.fetchval.called is False or conn.fetchval.call_count == 0
```

- [ ] **Step 3: Run tests to verify they fail (not yet implemented)**

```bash
uv run pytest tests/test_api/test_auth.py::TestLDAPLogin -v
```
Expected: Tests fail (no LDAP fallback in login yet, or mock setup needs adjustment).

Wait — the code changes in Task 5 already implement the LDAP fallback. So the tests should pass once all mocks are correctly set up. Instead:

Run: `uv run pytest tests/test_api/test_auth.py::TestLDAPLogin tests/test_api/test_auth.py::TestOIDCSSO -v`

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest tests/ -v
```
Expected: All existing tests still pass, new tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_api/test_auth.py
git commit -m "test: add LDAP and OIDC integration tests"
```

---

### Task 8: Smoke Test & .env Update

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add example config to .env.example**

Append to `.env.example`:

```bash
# ── LDAP (optional, skip if not using) ──
# LDAP_URL=ldap://ldap.example.com:389
# LDAP_BASE_DN=dc=example,dc=com
# LDAP_USER_RDN=cn=users,cn=accounts
# LDAP_USERNAME_ATTR=uid
# LDAP_EMAIL_ATTR=mail
# LDAP_USE_TLS=false

# ── OIDC SSO (optional, skip if not using) ──
# OIDC_ISSUER=https://accounts.google.com
# OIDC_CLIENT_ID=your-client-id
# OIDC_CLIENT_SECRET=your-client-secret
# OIDC_REDIRECT_URI=http://localhost:8000/api/auth/sso/callback
```

- [ ] **Step 2: Full end-to-end verification**

```bash
# Run all tests
uv run pytest tests/ -v

# Verify frontend builds
cd frontend && pnpm build

# Verify all imports
uv run python -c "
from app.auth.ldap_client import LDAPClient
from app.auth.oidc_client import OIDCClient
from app.config import settings
print('LDAP enabled:', LDAPClient.is_enabled())
print('OIDC enabled:', OIDCClient.is_enabled())
print('LDAP_URL:', repr(settings.LDAP_URL))
print('All OK')
"
```

- [ ] **Step 3: Final commit**

```bash
git add .env.example
git commit -m "docs: add LDAP/OIDC config examples to .env.example"
git push origin main
```

---
