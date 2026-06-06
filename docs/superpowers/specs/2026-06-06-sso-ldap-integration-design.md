# SSO/LDAP 集成 — 设计文档

> 日期: 2026-06-06 | 状态: 待实现

## 1. 背景与目标

**当前状态**: DeepAgents 已实现完整的用户名密码 + JWT 认证体系（注册/登录/鉴权/组隔离）。

**目标**: 在现有体系基础上，以最小变更增加 SSO (OIDC) 和 LDAP 认证能力。保留现有用户名密码登录，LDAP/OIDC 作为并行可选路径接入。

**场景**: 个人 / 小团队自用，配置驱动，不配置的功能不启用。

## 2. 架构概览

```
          前端 LoginPage
        /       |        \
   用户名密码  LDAP       OIDC SSO
      |          |          |
      v          v          v
  bcrypt验证  LDAP bind   OIDC flow
      |          |          |
      +-----+----+----------+
            |
            v
      返回 JWT → localStorage → authFetch
```

- **用户名密码**: 不改动，现有 `POST /api/auth/login` 逻辑
- **LDAP**: 登录时后端自动 fallback — 本地用户表找不到 → 尝试 LDAP bind → 成功则自动创建本地用户
- **OIDC**: 独立入口 `GET /api/auth/sso/login` → 重定向 IdP → callback → 创建/匹配本地用户 → 返回 JWT

## 3. 新增配置项

```python
# ── LDAP（空值 = 未启用）──
LDAP_URL: str = ""               # ldap://ldap.example.com:389
LDAP_BASE_DN: str = ""           # dc=example,dc=com
LDAP_USER_RDN: str = ""          # cn=users,cn=accounts
LDAP_USERNAME_ATTR: str = "uid"
LDAP_EMAIL_ATTR: str = "mail"
LDAP_USE_TLS: bool = False

# ── OIDC（空值 = 未启用）──
OIDC_ISSUER: str = ""            # https://accounts.google.com
OIDC_CLIENT_ID: str = ""
OIDC_CLIENT_SECRET: str = ""
OIDC_REDIRECT_URI: str = "http://localhost:8000/api/auth/sso/callback"
```

所有配置通过 Pydantic `BaseSettings` 从 `.env` 读取，不配则对应功能不启动。

## 4. 数据库变更

```sql
-- 1 个 ALTER，幂等
ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_source VARCHAR(10) NOT NULL DEFAULT 'local';
```

`auth_source` 取值:

| 值 | 含义 |
|----|------|
| `local` | 用户名密码注册（默认，兼容现有数据） |
| `ldap` | LDAP 首次绑定时自动创建 |
| `oidc` | OIDC 首次 SSO 时自动创建 |

无需新表。

## 5. 后端设计

### 5.1 新增文件

```
app/auth/
├── ldap_client.py    # NEW: LDAP 连接 + bind + 用户搜索
└── oidc_client.py    # NEW: OIDC Discovery → code→token → userinfo
```

### 5.2 LDAP 客户端 (`app/auth/ldap_client.py`)

```python
@dataclass
class LDAPUser:
    username: str
    email: str | None
    display_name: str | None

class LDAPClient:
    def authenticate(username: str, password: str) -> LDAPUser | None:
        """
        1. 连接 LDAP Server (settings.LDAP_URL)
        2. 拼接用户 DN: {attr}={username},{user_rdn},{base_dn}
        3. bind(user_dn, password)
        4. bind 成功 → 搜索用户属性 → 返回 LDAPUser
        5. bind 失败 → 返回 None
        """
```

要点:
- 连接超时 5s，搜索超时 3s
- 支持 `STARTTLS`（`LDAP_USE_TLS=true`）
- 依赖 `ldap3>=2.9`（纯 Python，无需系统 C 库，跨平台无忧）
- 配置为空时直接返回 None，不抛异常

### 5.3 OIDC 客户端 (`app/auth/oidc_client.py`)

```python
@dataclass
class OIDCUser:
    subject: str         # sub claim
    username: str        # preferred_username 或 sub
    email: str | None

class OIDCClient:
    def get_authorize_url(state: str) -> str:
        """根据 issuer 的 discovery document 构建 authorize URL"""

    def exchange_code(code: str) -> OIDCUser | None:
        """
        1. POST token endpoint → id_token + access_token
        2. 验证 id_token (iss, aud, exp, nonce)
        3. GET userinfo endpoint → OIDCUser
        4. 返回用户信息
        """
```

要点:
- 通过 `/.well-known/openid-configuration` 自动发现端点
- state 校验防 CSRF（state 存 Redis，5 分钟过期）
- nonce 校验防重放
- 不信任未知 issuer，验证 aud 匹配 client_id
- 无需额外库：`httpx` + `jwt` 解析（已有依赖）

### 5.4 API 端点变更

#### 改造 `POST /api/auth/login`

```python
@app.post("/api/auth/login")
@limiter.limit("5/minute")
async def login(request: Request):
    # 1. 查本地用户表 (auth_source = 'local')
    #    → 找到 → bcrypt 验证 → 返回 JWT
    # 2. 未找到 + LDAP 已配置 → 调 ldap_client.authenticate()
    #    → 成功 → 自动创建用户 (auth_source='ldap') → 返回 JWT
    #    → 失败 → 401
    # 3. 未找到 + LDAP 未配置 → 401
```

#### 新增 `GET /api/auth/sso/login`

```python
@app.get("/api/auth/sso/login")
async def sso_login(request: Request):
    # 1. 校验 OIDC 已配置，否则 404
    # 2. 生成 state + nonce，存入 Redis (TTL 5min)
    # 3. 构造 OIDC authorize URL → 302 重定向
```

#### 新增 `GET /api/auth/sso/callback`

```python
@app.get("/api/auth/sso/callback")
async def sso_callback(code: str, state: str):
    # 1. 从 Redis 取 state 并验证 → 不匹配则 403
    # 2. 调 oidc_client.exchange_code(code)
    # 3. 查 users 表 (username = oidc_user.username, auth_source = 'oidc')
    #    → 不存在 → 自动创建
    #    → 存在 → 更新 email
    # 4. 生成 JWT → 302 重定向到前端首页 URL（携带 JWT 参数）
```

#### 新增 `GET /api/auth/sso/providers`

```python
@app.get("/api/auth/sso/providers")
async def sso_providers():
    # 返回启用的 SSO provider 列表，前端据此显示对应按钮
    # 例: { "providers": ["oidc"] } 或 { "providers": [] }
```

### 5.5 安全考虑

- LDAP 连接不缓存密码，每次 bind 独立连接
- OIDC state/nonce 防 CSRF（Redis 5min TTL）
- 自动创建用户默认 `is_active=True`, `role=user`, `group_id=1`（默认组）
- 限流：login 保持 5/min，SSO 入口加 3/min

## 6. 前端设计

### 6.1 LoginPage 改动

- 现有用户名密码登录/注册 Tab **完全不变**
- 登录框底部新增分割线 + 按钮区域:

```
        ── 其他登录方式 ──
    [ 🐙 GitHub ]  [ 🔑 OIDC SSO ]
```

- 按钮列表由 `GET /api/auth/sso/providers` 接口动态决定
- LDAP 用户无感知 — 复用现有用户名密码输入框，后端自动 fallback

### 6.2 OIDC 回调处理

```
GET /?token=jwt_value  # SSO 成功回调
  → 前端检测 URL 参数 token
  → setToken(token) + setUser(解析)
  → 跳转首页
```

### 6.3 auth.ts 改动

- 不做结构性改动，`setToken`/`setUser`/`authFetch` 保持不变
- 登录页面首次加载时调用 `GET /api/auth/sso/providers` 获取按钮列表

## 7. 测试计划

| 测试点 | 内容 |
|--------|------|
| LDAP mock bind 成功 | 新用户自动创建 + 返回 JWT + auth_source=ldap |
| LDAP mock bind 失败 | 返回 401，不创建用户 |
| LDAP 未配置 | login 行为与改前完全一致 |
| OIDC mock 流程 | state → code → exchange → 创建用户 → 返回 JWT |
| OIDC state 不匹配 | 403，校验拒绝 |
| OIDC 已存在用户 | 匹配现有用户，不重复创建 |
| OIDC 未配置 | /sso/login 返回 404, /sso/providers 返回空 |
| auth_source 字段 | 现有用户默认 local，向后兼容 |

## 8. 依赖新增

```toml
# pyproject.toml
"ldap3>=2.9",
```

## 9. 改动清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/config.py` | 改 | 新增 LDAP/OIDC 配置项 |
| `app/auth/ldap_client.py` | 新增 | LDAP 客户端 |
| `app/auth/oidc_client.py` | 新增 | OIDC 客户端 |
| `app/auth/jwt.py` | 不改 | - |
| `app/auth/dependencies.py` | 不改 | - |
| `app/api/server.py` | 改 | 改造 login + 新增 3 个 SSO 端点 |
| `app/storage/db.py` | 改 | ALTER TABLE 迁移 |
| `frontend/src/components/LoginPage.tsx` | 改 | 新增 SSO 按钮 + provider 动态加载 |
| `frontend/src/lib/auth.ts` | 改 | SSO 回调 token 解析 |
| `pyproject.toml` | 改 | 新增 python-ldap |
| `tests/test_api/test_auth.py` | 改 | 新增 LDAP/OIDC 测试用例 |
| `tests/conftest.py` | 改 | 新增 LDAP mock fixture |
