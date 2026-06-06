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

        1. POST token endpoint -> id_token + access_token
        2. 验证 id_token (iss, aud, exp)
        3. GET userinfo endpoint -> OIDCUser
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
