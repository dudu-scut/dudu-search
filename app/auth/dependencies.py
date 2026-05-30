"""FastAPI 依赖注入 — 认证与授权。"""
from typing import Optional

import jwt
from fastapi import Depends, Header

from app.auth.jwt import decode_token
from app.exceptions import AuthError, PermissionDeniedError


class UserInfo:
    """从 JWT 解析出的用户信息。"""

    def __init__(self, payload: dict):
        self.id: str = payload["sub"]
        self.username: str = payload["username"]
        self.role: str = payload.get("role", "user")
        self.group_id: Optional[int] = payload.get("group_id")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


async def get_current_user(
    authorization: str = Header(..., description="Bearer <token>"),
) -> UserInfo:
    if not authorization:
        raise AuthError("缺少认证信息，请先登录")

    token = authorization
    if authorization.startswith("Bearer "):
        token = authorization[7:]

    if not token:
        raise AuthError("认证 token 为空")

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise AuthError("无效的 token 类型")
        return UserInfo(payload)
    except jwt.ExpiredSignatureError:
        raise AuthError("登录已过期，请重新登录")
    except jwt.InvalidTokenError as e:
        raise AuthError(f"无效的认证 token: {e}")


async def require_admin(
    user: UserInfo = Depends(get_current_user),
) -> UserInfo:
    if not user.is_admin:
        raise PermissionDeniedError("需要管理员权限")
    return user


async def get_optional_user(
    authorization: Optional[str] = Header(None),
) -> Optional[UserInfo]:
    if not authorization:
        return None
    try:
        return await get_current_user(authorization)
    except AuthError:
        return None
