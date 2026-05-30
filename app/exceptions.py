"""DeepAgents 统一异常层次结构。

所有业务异常继承自 DeepAgentsError，携带结构化信息供 API 层统一处理。
"""

from typing import Any, Optional


class DeepAgentsError(Exception):
    """所有 DeepAgents 异常的基类。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
        http_status: int = 500,
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.http_status = http_status
        super().__init__(message)


# ── LLM 相关异常 ──

class LLMError(DeepAgentsError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(code="LLM_ERROR", message=message, details=details, http_status=502)


class LLMTimeoutError(LLMError):
    def __init__(self, message: str = "LLM 调用超时", details: Optional[dict] = None):
        super().__init__(message, details)
        self.code = "LLM_TIMEOUT"


class LLMAuthError(LLMError):
    def __init__(self, message: str = "LLM API 认证失败", details: Optional[dict] = None):
        super().__init__(message, details)
        self.code = "LLM_AUTH_ERROR"
        self.http_status = 500


# ── 存储相关异常 ──

class StorageError(DeepAgentsError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(code="STORAGE_ERROR", message=message, details=details, http_status=500)


class DatabaseConnectionError(StorageError):
    def __init__(self, message: str = "数据库连接失败", details: Optional[dict] = None):
        super().__init__(message, details)
        self.code = "DB_CONNECTION_ERROR"


# ── 工具相关异常 ──

class ToolError(DeepAgentsError):
    def __init__(self, tool_name: str, message: str, details: Optional[dict] = None):
        super().__init__(code="TOOL_ERROR", message=f"[{tool_name}] {message}", details=details, http_status=502)


class SQLExecutionError(ToolError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__("sql_executor", message, details)
        self.code = "SQL_EXECUTION_ERROR"
        self.http_status = 400


# ── 认证相关异常 ──

class AuthError(DeepAgentsError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(code="AUTH_ERROR", message=message, details=details, http_status=401)


class PermissionDeniedError(AuthError):
    def __init__(self, message: str = "权限不足", details: Optional[dict] = None):
        super().__init__(message, details)
        self.code = "PERMISSION_DENIED"
        self.http_status = 403


# ── 配置相关异常 ──

class ConfigError(DeepAgentsError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(code="CONFIG_ERROR", message=message, details=details, http_status=500)


# ── 验证相关异常 ──

class ValidationError(DeepAgentsError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(code="VALIDATION_ERROR", message=message, details=details, http_status=422)
