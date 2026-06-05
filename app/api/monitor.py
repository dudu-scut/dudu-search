"""
Agent 执行过程监控模块

负责把工具调用、子智能体调用、任务结果和会话目录等事件统一包装后推送给前端
在 Web 服务中优先通过 WebSocket 定向推送；在脚本调试场景中保留控制台输出
"""

import asyncio
import builtins
import datetime
from typing import Any, Optional

from fastapi import WebSocket

from app.api.context import get_thread_context
from app.logging_config import get_logger

logger = get_logger("monitor")


class ToolMonitor:
    """
    工具和助手调用的统一监控入口

    业务工具只需要导入全局 monitor，并调用 report_tool/report_assistant 等方法
    具体是通过 WebSocket 推送，还是输出到脚本运行时，由本类内部统一处理
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolMonitor, cls).__new__(cls)
            cls._instance.websocket_manager = None
        return cls._instance

    def set_websocket_manager(self, manager: "ConnectionManager") -> None:
        """绑定 FastAPI WebSocket 连接管理器"""
        self.websocket_manager = manager

    def _emit(
        self,
        event_type: str,
        message: str,
        data: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        构造统一监控事件，并尝试推送到当前 thread_id 对应的前端连接

        :param event_type: 事件类型，例如 tool_start、assistant_call
        :param message: 面向前端展示的事件说明
        :param data: 附加结构化数据
        """
        payload = {
            "type": "monitor_event",
            "event": event_type,
            "message": message,
            "data": data or {},
            "timestamp": datetime.datetime.now().isoformat(),
        }

        if self.websocket_manager:
            try:
                thread_id = get_thread_context()
                manager_loop = self.websocket_manager.loop

                if manager_loop and thread_id:
                    self._send_to_websocket(payload, thread_id, manager_loop)
            except Exception as e:
                logger.warning("WebSocket send 失败", exc_info=True)

        # DeepAgents 脚本调试时，如果运行时暴露了 stream_writer，也同步写入流式输出
        if hasattr(builtins, "runtime") and hasattr(builtins.runtime, "stream_writer"):
            try:
                builtins.runtime.stream_writer(payload)
            except Exception:
                pass

        # 写入 Redis 缓存（fire-and-forget，便于断线重连恢复）
        # 同时发布到 Redis Pub/Sub 频道，让 Server 进程转发到 WebSocket
        thread_id = get_thread_context()
        if thread_id:
            try:
                from app.storage.redis_client import cache_event, publish_ws_event
                import json as _json
                import asyncio as _asyncio

                async def _cache_and_publish():
                    try:
                        await cache_event(thread_id, payload)
                        pub_msg = _json.dumps(
                            {"type": "monitor_event", "event": event_type, "message": message,
                             "data": data or {}, "timestamp": payload["timestamp"]},
                            ensure_ascii=False, default=str,
                        )
                        await publish_ws_event(thread_id, pub_msg)
                        logger.debug("事件已缓存并发布", thread_id=thread_id, event_type=event_type)
                    except Exception as e:
                        logger.warning("事件缓存/发布失败", thread_id=thread_id, error=str(e))

                _asyncio.ensure_future(_cache_and_publish())
            except Exception:
                pass

        # 持久化事件到 PostgreSQL（fire-and-forget，不阻塞主流程）
        try:
            import asyncio as _asyncio
            _asyncio.ensure_future(_persist_monitor_event(
                thread_id=thread_id,
                event_type=event_type,
                message=message,
                payload=data or {},
            ))
        except Exception:
            pass

        # 控制台保底输出，便于无前端场景下观察执行过程
        logger.info(message, event_type=event_type)

    def _send_to_websocket(
        self,
        payload: dict[str, Any],
        thread_id: str,
        manager_loop: asyncio.AbstractEventLoop,
    ) -> None:
        """
        将监控事件投递到 WebSocket 所在事件循环

        FastAPI 的 WebSocket 必须在创建它的事件循环中发送消息
        如果当前代码已经在同一个循环里，直接 create_task；否则使用线程安全投递
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        coroutine = self.websocket_manager.send_to_thread(payload, thread_id)
        if current_loop and current_loop == manager_loop:
            current_loop.create_task(coroutine)
        else:
            asyncio.run_coroutine_threadsafe(coroutine, manager_loop)

    async def _emit_error(self, code: str, message: str) -> None:
        """发送错误事件到前端（通过 Redis Pub/Sub 跨进程广播）。"""
        payload = {
            "type": "monitor_event",
            "event": "error",
            "message": message,
            "data": {"code": code},
            "timestamp": datetime.datetime.now().isoformat(),
        }
        # 1) 直接 WebSocket 推送（仅在 Server 进程中有效）
        if self.websocket_manager:
            try:
                thread_id = get_thread_context()
                manager_loop = self.websocket_manager.loop
                if manager_loop and thread_id:
                    self._send_to_websocket(payload, thread_id, manager_loop)
            except Exception:
                pass
        # 2) Redis Pub/Sub 跨进程桥接（Worker → Server → 前端）
        try:
            from app.storage.redis_client import publish_ws_event
            import json as _json

            pub_msg = _json.dumps(payload, ensure_ascii=False, default=str)
            thread_id = get_thread_context()
            if thread_id:
                await publish_ws_event(thread_id, pub_msg)
        except Exception:
            pass

    def report_tool(
        self,
        tool_name: str,
        args: Optional[dict[str, Any]] = None,
    ) -> None:
        """报告开始执行某个工具"""
        self._emit(
            "tool_start",
            f"开始执行工具: {tool_name}",
            {"tool_name": tool_name, "args": args},
        )

    def report_assistant(
        self,
        assistant_name: str,
        args: Optional[dict[str, Any]] = None,
    ) -> None:
        """报告正在调用某个子智能体"""
        self._emit(
            "assistant_call",
            f"正在调用助手: {assistant_name}",
            {"assistant_name": assistant_name, "args": args},
        )

    def report_task_result(self, result: str) -> None:
        """报告任务最终结果"""
        self._emit("task_result", "任务执行完成", {"result": result})

    def report_task_cancelled(self) -> None:
        """报告任务已被用户取消"""
        self._emit("task_cancelled", "任务已取消")

    def report_session_dir(self, path: str) -> None:
        """报告当前任务工作目录"""
        self._emit("session_created", f"工作目录已创建: {path}", {"path": path})


monitor = ToolMonitor()


class ConnectionManager:
    """
    WebSocket 连接管理器

    active_connections 使用 thread_id 作为 key，保证监控事件只推送给对应任务的前端连接
    """

    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        # WebSocket 发送必须回到创建连接的事件循环，因此启动时需要显式绑定 loop
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定 FastAPI 主事件循环，并同步注册到 monitor"""
        self.loop = loop
        monitor.set_websocket_manager(self)
        logger.info("ConnectionManager 已绑定到事件循环", loop_id=id(self.loop))

    async def register(self, websocket: WebSocket, thread_id: str) -> None:
        """注册已接受的 WebSocket 连接（不重复 accept）。"""
        self.active_connections[thread_id] = websocket
        logger.info("客户端已注册", thread_id=thread_id)

    async def connect(self, websocket: WebSocket, thread_id: str) -> None:
        """接受 WebSocket 连接，并按 thread_id 保存"""
        await websocket.accept()
        self.active_connections[thread_id] = websocket
        logger.info("客户端已连接", thread_id=thread_id)

    def disconnect(self, websocket: WebSocket, thread_id: str) -> None:
        """移除已经断开的 WebSocket 连接"""
        if self.active_connections.get(thread_id) is websocket:
            del self.active_connections[thread_id]
            logger.info("客户端已断开", thread_id=thread_id)
        else:
            logger.info("过期 WebSocket 断开，保留当前连接", thread_id=thread_id)

    async def send_personal_message(self, message: str, websocket: WebSocket) -> None:
        """向指定 WebSocket 发送纯文本消息"""
        await websocket.send_text(message)

    async def send_to_thread(self, message: dict[str, Any], thread_id: str) -> None:
        """向指定 thread_id 对应的前端连接发送 JSON 消息"""
        if thread_id in self.active_connections:
            websocket = self.active_connections[thread_id]
            await websocket.send_json(message)

    async def disconnect_all(self) -> None:
        """关闭所有活跃的 WebSocket 连接"""
        logger.info("正在关闭 WebSocket 连接", count=len(self.active_connections))
        for thread_id, websocket in list(self.active_connections.items()):
            try:
                await websocket.close()
                logger.info("已关闭连接", thread_id=thread_id)
            except Exception as e:
                logger.warning("关闭连接失败", thread_id=thread_id, exc_info=True)
        self.active_connections.clear()
        logger.info("所有连接已清理")


manager = ConnectionManager()


async def _persist_monitor_event(
    thread_id: str | None,
    event_type: str,
    message: str,
    payload: dict,
) -> None:
    """将监控事件异步写入 PostgreSQL。"""
    if not thread_id:
        return
    try:
        import json
        from app.storage.db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO agent_events (thread_id, event_type, message, payload) "
                "VALUES ($1, $2, $3, $4)",
                thread_id, event_type, message,
                json.dumps(payload, ensure_ascii=False),
            )
    except Exception as e:
        logger.warning("事件持久化失败", event_type=event_type, exc_info=True)
        # 通过 WebSocket 通知前端（如果连接存在）
        try:
            await monitor._emit_error(
                code="EVENT_PERSIST_ERROR",
                message="事件记录失败，执行轨迹可能不完整",
            )
        except Exception:
            pass
