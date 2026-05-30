"""
Redis 客户端管理。

提供异步 Redis 客户端的创建、关闭，以及热状态操作的辅助方法。
"""
import os
from typing import Optional

import redis.asyncio as aioredis
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

REDIS_URI = os.getenv("REDIS_URI", "redis://:deepagents@localhost:6379/0")

_client: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    """获取或创建异步 Redis 客户端（懒初始化）。"""
    global _client
    if _client is not None:
        return _client
    _client = aioredis.from_url(REDIS_URI, decode_responses=True)
    return _client


async def close_redis() -> None:
    """关闭 Redis 连接。"""
    global _client
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
    key = f"ws_events:{thread_id}"
    pipe = r.pipeline()
    pipe.lpush(key, event_json)
    pipe.ltrim(key, 0, 499)  # 每个线程最多保留 500 条事件
    pipe.expire(key, ttl)
    await pipe.execute()


async def get_cached_ws_events(thread_id: str, count: int = 50) -> list[str]:
    """获取缓存的 WebSocket 事件。"""
    r = await get_redis()
    key = f"ws_events:{thread_id}"
    return await r.lrange(key, 0, count - 1)
