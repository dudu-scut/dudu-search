"""FastAPI 依赖注入 — 认证与授权。"""
from typing import Optional, Tuple

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


def get_group_filter(user: UserInfo = Depends(get_current_user)) -> Tuple[Optional[int], str]:
    """返回 (group_id, SQL WHERE clause) 用于数据隔离过滤。

    管理员可以看到所有组的数据。普通用户只能看到自己所在组的数据。
    如果 group_id 为 None（非管理员但未分配组），默认使用 group_id=1 防止数据泄漏。

    用法:
        group_id, filter_clause = get_group_filter(user)
        # filter_clause 是完整的 WHERE 子句片段，例如 "group_id = $1" 或 "1=1"
        results = await conn.fetch(
            f"SELECT * FROM sessions WHERE {filter_clause}", group_id
        )
    """
    if user.is_admin:
        return None, "1=1"
    # 防御性兜底：未分配组的用户默认归入组 1，避免因 group_id=None 导致
    # server.py 中 `if group_id is not None` 分支跳过，从而绕过组隔离
    gid = user.group_id if user.group_id is not None else 1
    return gid, "group_id = $1"
