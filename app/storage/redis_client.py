"""
Redis 客户端管理。

提供异步 Redis 客户端的创建、关闭，以及热状态操作的辅助方法。
"""
import asyncio
import json
from typing import Optional

import redis.asyncio as aioredis

from app.config import settings

REDIS_URI = settings.REDIS_URI

_client: Optional[aioredis.Redis] = None
_lock: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    """懒初始化 Lock，避免模块加载时在事件循环外创建。"""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def get_redis() -> aioredis.Redis:
    """获取或创建异步 Redis 客户端（懒初始化 + 双重检查锁）。"""
    global _client
    if _client is not None:
        return _client
    async with _get_lock():
        if _client is not None:
            return _client
        _client = aioredis.from_url(REDIS_URI, decode_responses=True)
        return _client


# 别名：server.py / worker.py 中多处使用了 get_redis_client 这个名字
get_redis_client = get_redis


async def close_redis() -> None:
    """关闭 Redis 连接（加锁防止与 get_redis 竞态）。"""
    global _client
    async with _get_lock():
        if _client is not None:
            await _client.close()
            _client = None


# ---- 热状态辅助操作 ----

async def register_active_task(thread_id: str, task_id: str) -> None:
    """在 Redis 中注册活跃任务。"""
    r = await get_redis()
    await r.hset("active_tasks", thread_id, task_id)


async def unregister_active_task(thread_id: str) -> None:
    """从 Redis 中移除活跃任务。"""
    r = await get_redis()
    await r.hdel("active_tasks", thread_id)


async def is_task_active(thread_id: str) -> bool:
    """检查指定 thread_id 是否有活跃任务。"""
    r = await get_redis()
    return await r.hexists("active_tasks", thread_id)


async def get_active_task_ids() -> list[str]:
    """获取所有活跃任务 ID。"""
    r = await get_redis()
    return list((await r.hgetall("active_tasks")).keys())


async def cache_ws_event(thread_id: str, event_json: str, ttl: int = 604800) -> None:
    """缓存 WebSocket 事件到 Redis List（7天 TTL）。"""
    r = await get_redis()
    key = f"ws:events:{thread_id}"  # 与 cache_event 使用相同前缀
    pipe = r.pipeline()
    pipe.lpush(key, event_json)
    pipe.ltrim(key, 0, 499)  # 每个线程最多保留 500 条事件
    pipe.expire(key, ttl)
    await pipe.execute()


async def cache_event(thread_id: str, event: dict) -> None:
    """将事件字典写入 Redis 列表（最新在前）。保留最近 500 条，7 天过期。"""
    import json
    r = await get_redis()
    key = f"ws:events:{thread_id}"
    pipe = r.pipeline()
    pipe.lpush(key, json.dumps(event, ensure_ascii=False, default=str))
    pipe.ltrim(key, 0, 499)  # 保留最近 500 条
    pipe.expire(key, 7 * 24 * 3600)  # 7 天 TTL
    await pipe.execute()


async def get_cached_ws_events(thread_id: str, count: int = 50) -> list[str]:
    """获取缓存的 WebSocket 事件。"""
    r = await get_redis()
    key = f"ws:events:{thread_id}"  # 与 cache_event 使用相同前缀
    return await r.lrange(key, 0, count - 1)


# ── Redis Pub/Sub：跨进程 WebSocket 事件桥接 ──
# Worker 进程通过 publish 把事件推到 Redis 频道，
# Server 进程通过 subscribe 监听频道并转发到 WebSocket。

_WS_CHANNEL_PREFIX = "ws:channel"


def _ws_channel(thread_id: str) -> str:
    return f"{_WS_CHANNEL_PREFIX}:{thread_id}"


async def publish_ws_event(thread_id: str, event_json: str) -> None:
    """向 Redis Pub/Sub 频道发布 WebSocket 事件（Worker 进程调用）。"""
    r = await get_redis()
    await r.publish(_ws_channel(thread_id), event_json)


async def _create_pubsub_connection() -> aioredis.Redis:
    """为 Pub/Sub 创建独立 Redis 连接，避免与共享 _client 互相干扰。

    Pub/Sub 模式下多个订阅者共享同一连接会导致消息路由混乱，
    每个订阅者应使用独立连接。
    """
    return aioredis.from_url(REDIS_URI, decode_responses=True)


async def subscribe_ws_events(thread_id: str):
    """订阅指定 thread_id 的 WebSocket 事件频道，返回异步生成器。

    Server 进程的 WebSocket 端点使用此生成器获取 Worker 推送的实时事件。
    每次调用创建独立 Redis 连接，退出时自动清理。

    用法:
        async for event_json in subscribe_ws_events(thread_id):
            ...
    """
    conn = await _create_pubsub_connection()
    try:
        async with conn.pubsub() as pubsub:
            channel = _ws_channel(thread_id)
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield message["data"]
    finally:
        await conn.close()


# ── Redis Pub/Sub：SSE 通知频道 ──
# 用于替代前端 HTTP 轮询，当会话列表或文件列表发生变更时，
# 后端主动通过 Redis 频道推送通知，SSE 端点转发给前端。

_SSE_SESSION_CHANNEL = "sse:sessions"
_SSE_FILE_PREFIX = "sse:files"


async def publish_session_event(group_id: Optional[int], event: dict) -> None:
    """向 SSE 会话频道发布会话变更通知（任务创建/完成/取消等）。"""
    r = await get_redis()
    await r.publish(_SSE_SESSION_CHANNEL, json.dumps(event, default=str))


async def publish_file_event(thread_id: str) -> None:
    """向 SSE 文件频道发布文件变更通知（文件上传/生成/删除）。"""
    r = await get_redis()
    await r.publish(f"{_SSE_FILE_PREFIX}:{thread_id}", json.dumps({
        "event": "files_updated",
        "thread_id": thread_id,
    }))


async def subscribe_sse_sessions():
    """订阅全局 SSE 会话变更频道，返回异步生成器。"""
    conn = await _create_pubsub_connection()
    try:
        async with conn.pubsub() as pubsub:
            await pubsub.subscribe(_SSE_SESSION_CHANNEL)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield message["data"]
    finally:
        await conn.close()


async def subscribe_sse_files(thread_id: str):
    """订阅指定 thread_id 的 SSE 文件变更频道，返回异步生成器。"""
    conn = await _create_pubsub_connection()
    try:
        async with conn.pubsub() as pubsub:
            channel = f"{_SSE_FILE_PREFIX}:{thread_id}"
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield message["data"]
    finally:
        await conn.close()
