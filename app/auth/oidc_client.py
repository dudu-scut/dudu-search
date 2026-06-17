"""OIDC 认证客户端 — Discovery, Authorization URL 构建, Code 换 Token。"""

import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

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
        if not issuer.startswith("https://") and "localhost" not in issuer and "127.0.0.1" not in issuer:
            raise ValueError(f"OIDC issuer URL 必须使用 HTTPS，当前值: {issuer}")
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

        1. POST token endpoint → access_token
        2. GET userinfo endpoint (Bearer) → OIDCUser
        3. 失败返回 None
        """
        config = await cls._get_config()

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
        access_token = tokens.get("access_token")

        if not access_token:
            logger.warning("OIDC no access_token in response")
            return None

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
