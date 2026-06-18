"""FastAPI 依赖注入 — 认证与授权。"""
from typing import Optional, Tuple

import jwt
from fastapi import Depends, Header

from app.auth.jwt import decode_token
from app.exceptions import AuthError, PermissionDeniedError


class UserInfo:
    """从 JWT 解析出的用户信息。

    attributes:
        id:          用户唯一标识（JWT sub）
        username:    用户名
        role:        角色名（"admin" / "manager" / "user" / "viewer" / 自定义）
        group_id:    所属用户组 ID
        permissions: 权限 ID 集合，由 RBAC 依赖工厂填充；未加载时为 None
    """

    def __init__(self, payload: dict):
        self.id: str = payload.get("sub") or ""
        self.username: str = payload.get("username") or ""
        self.role: str = payload.get("role", "user")
        self.group_id: Optional[int] = payload.get("group_id")
        self.permissions: Optional[set] = None  # RBAC 延迟填充
        if not self.id:
            raise AuthError("无效的认证 token: 缺少用户标识")

    @property
    def is_admin(self) -> bool:
        """向后兼容的管理员判断（deprecated，新代码应使用 require_permission）。"""
        return self.role == "admin"

    def has_permission(self, permission: str) -> bool:
        """检查是否拥有指定权限。

        permissions 未加载时退化为 role 判断（仅 admin 通过）。
        admin 角色（permissions 含 "*"）直接通过。
        """
        if self.permissions is None:
            return self.role == "admin"
        return "*" in self.permissions or permission in self.permissions


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
    """要求管理员权限（向后兼容，内部委托给 RBAC）。"""
    if not user.is_admin:
        raise PermissionDeniedError("需要管理员权限")
    # 为 admin 填充权限集合，使下游 has_permission() 一致
    if user.permissions is None:
        try:
            from app.auth.permissions import get_user_permissions
            user.permissions = await get_user_permissions(user.role)
        except Exception:
            user.permissions = {"*"}  # 降级兜底
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


def get_group_filter(user: UserInfo = Depends(get_current_user)) -> Tuple[Optional[int], callable]:
    """返回 (group_id, group_suffix) 用于参数化 SQL 数据隔离过滤。

    管理员: group_id=None, group_suffix 返回 "1=1"（无过滤）
    普通用户: group_id=用户组ID, group_suffix(params) 返回 "group_id = $N"
              其中 N = len(params) + 1，调用者需将 group_id 追加到 params

    用法:
        group_id, group_suffix = get_group_filter(user)
        params = [...]
        if group_id is not None:
            params.append(group_id)
        results = await conn.fetch(
            f"SELECT * FROM sessions WHERE {group_suffix(params)}", *params
        )
    """
    if user.is_admin:
        return None, lambda params: "1=1"
    # 防御性兜底：未分配组的用户默认归入组 1
    gid = user.group_id if user.group_id is not None else 1

    def _suffix(params: list) -> str:
        return f"group_id = ${len(params) + 1}"

    return gid, _suffix
