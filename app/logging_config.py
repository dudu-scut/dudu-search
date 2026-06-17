"""结构化日志配置 — 基于 structlog + stdlib logging。

用法:
    from app.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("用户登录", username="test", trace_id="abc123")
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog


def _add_trace_id(logger: Any, method_name: str, event_dict: dict) -> dict:
    """自动注入 trace_id 到每条日志。"""
    from app.api.context import get_trace_id
    trace_id = get_trace_id()
    if trace_id:
        event_dict["trace_id"] = trace_id
    return event_dict


def _add_user_id(logger: Any, method_name: str, event_dict: dict) -> dict:
    """自动注入 user_id 到每条日志。"""
    from app.api.context import get_current_user_id
    user_id = get_current_user_id()
    if user_id:
        event_dict["user_id"] = user_id
    return event_dict


def _mask_sensitive(logger: Any, method_name: str, event_dict: dict) -> dict:
    """脱敏处理器 — 自动屏蔽敏感字段（模糊匹配）。"""
    SENSITIVE_PATTERNS = {
        "password", "api_key", "token", "secret", "authorization",
    }
    for key in list(event_dict.keys()):
        key_lower = key.lower()
        if any(pattern in key_lower for pattern in SENSITIVE_PATTERNS):
            event_dict[key] = "****"
    return event_dict


def setup_logging(log_format: str = "console") -> None:
    """初始化日志系统（应用启动时调用一次）。

    Args:
        log_format: "console" (开发模式，彩色) 或 "json" (生产模式)
    """
    # 1. 配置 stdlib logging 基础
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )

    # 2. 抑制 noisy 的第三方库日志
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # 3. 共享的 processors
    shared_processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_trace_id,
        _add_user_id,
        _mask_sensitive,
        structlog.stdlib.ExtraAdder(),
    ]

    if log_format == "json":
        # 生产模式：JSON 输出，适合 Filebeat/Logstash 采集
        structlog.configure(
            processors=shared_processors + [
                structlog.processors.dict_tracebacks,
                structlog.processors.JSONRenderer(),
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
    else:
        # 开发模式：彩色 console 输出
        structlog.configure(
            processors=shared_processors + [
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )

    # 确保 structlog 日志转发到 stdlib
    structlog.get_logger().info("日志系统初始化完成", format=log_format)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取结构化 logger 实例。"""
    return structlog.get_logger(name or __name__)
