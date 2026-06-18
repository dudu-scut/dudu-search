"""RBAC 权限检查模块。

三级模型：角色(Role) -> 权限(Permission) -> 资源:操作(resource:action)

- 权限查询带 Redis 缓存（5 分钟 TTL）
- 提供 FastAPI 依赖注入工厂 require_permission() / require_any_permission()
- 向后兼容：admin 角色自动拥有所有权限（通配符 "*"）
"""

import json
from typing import Optional, Callable

from fastapi import Depends

from app.auth.dependencies import get_current_user, UserInfo
from app.exceptions import PermissionDeniedError
from app.logging_config import get_logger

logger = get_logger("permissions")

# Redis 缓存键前缀和 TTL
_RBAC_CACHE_PREFIX = "rbac:perms"
_RBAC_CACHE_TTL = 300  # 5 分钟

# admin 角色拥有全部权限的特殊标记
_ADMIN_WILDCARD = "*"


async def get_user_permissions(role: str) -> set[str]:
    """查询指定角色的全部权限 ID 集合。

    优先从 Redis 缓存读取，缓存未命中则查询数据库并回填缓存。
    admin 角色直接返回 {"*"} 通配符，无需查库。

    :param role: 用户角色名（对应 roles.name）
    :return: 权限 ID 集合，如 {"task:create", "task:read", ...}
    """
    # admin 超级权限快捷路径
    if role == "admin":
        return {_ADMIN_WILDCARD}

    from app.storage.redis_client import get_redis
    r = await get_redis()
    cache_key = f"{_RBAC_CACHE_PREFIX}:{role}"

    # 尝试从缓存读取
    cached = await r.get(cache_key)
    if cached:
        try:
            return set(json.loads(cached))
        except (json.JSONDecodeError, TypeError):
            pass

    # 缓存未命中 → 查数据库
    from app.storage.db import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT rp.permission_id FROM role_permissions rp "
            "WHERE rp.role_name = $1",
            role,
        )

    perms = {row["permission_id"] for row in rows}

    # 回填缓存
    await r.set(cache_key, json.dumps(list(perms)), ex=_RBAC_CACHE_TTL)

    return perms


async def invalidate_permission_cache(role: Optional[str] = None) -> None:
    """清除权限缓存。role=None 时清除所有 RBAC 缓存。"""
    from app.storage.redis_client import get_redis
    r = await get_redis()

    if role:
        await r.delete(f"{_RBAC_CACHE_PREFIX}:{role}")
        logger.info("已清除角色权限缓存", role=role)
    else:
        async for key in r.scan_iter(match=f"{_RBAC_CACHE_PREFIX}:*", count=50):
            await r.delete(key)
        logger.info("已清除全部权限缓存")


def can(user: UserInfo, permission: str) -> bool:
    """同步权限检查（需提前在 UserInfo 上加载 permissions 集合）。

    admin 角色（permissions 含 "*"）直接通过。
    """
    perms = getattr(user, "permissions", None)
    if perms is None:
        # 未预加载时退化为 role 判断
        return user.role == "admin"
    return _ADMIN_WILDCARD in perms or permission in perms


def require_permission(*required: str) -> Callable:
    """FastAPI 依赖工厂 — 校验用户拥有全部所需权限。

    用法:
        @app.get("/api/admin/metrics")
        async def metrics(user: UserInfo = Depends(require_permission("metric:read"))):
            ...
    """
    async def _dependency(
        user: UserInfo = Depends(get_current_user),
    ) -> UserInfo:
        # admin 快捷路径
        if user.role == "admin":
            user.permissions = {_ADMIN_WILDCARD}
            return user

        perms = await get_user_permissions(user.role)
        user.permissions = perms

        missing = [p for p in required if p not in perms]
        if missing:
            raise PermissionDeniedError(
                f"权限不足，缺少: {', '.join(missing)}"
            )
        return user

    return _dependency


def require_any_permission(*required: str) -> Callable:
    """FastAPI 依赖工厂 — 校验用户拥有任一所需权限。

    用法:
        @app.get("/api/prompt-templates")
        async def list_templates(
            user: UserInfo = Depends(require_any_permission("prompt:read", "prompt:create"))
        ):
            ...
    """
    async def _dependency(
        user: UserInfo = Depends(get_current_user),
    ) -> UserInfo:
        if user.role == "admin":
            user.permissions = {_ADMIN_WILDCARD}
            return user

        perms = await get_user_permissions(user.role)
        user.permissions = perms

        if not any(p in perms for p in required):
            raise PermissionDeniedError(
                f"权限不足，需要任一: {', '.join(required)}"
            )
        return user

    return _dependency
