"""统一配置模块 — 所有环境变量从这里读取，启动时自动校验必填项。"""

import sys
from pathlib import Path
from pydantic import AliasChoices, ConfigDict, Field
from pydantic_settings import BaseSettings
from typing import Optional

# 计算 .env 文件的绝对路径，确保无论从哪个目录启动都能找到配置文件
_ENV_FILE = str(Path(__file__).resolve().parent.parent / ".env")

# Windows 下 psycopg async 需要 SelectorEventLoop（ProactorEventLoop 不兼容）
# 必须在任何 asyncio.run() 之前设置，确保 Worker 和 Server 都使用正确的事件循环
if sys.platform == "win32":
    import asyncio as _asyncio
    _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())


class Settings(BaseSettings):
    """应用配置，所有值从环境变量或 .env 文件读取。"""

    # ── 应用基础 ──
    APP_NAME: str = "DeepAgents"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ── 数据库 ──
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "deepagents"
    POSTGRES_PASSWORD: str = "deepagents"
    POSTGRES_DB: str = Field("deepagents", validation_alias="POSTGRES_DATABASE")

    # Direct URI overrides (optional — computed from individual fields if not set)
    POSTGRES_URI_OVERRIDE: Optional[str] = Field(None, validation_alias="POSTGRES_URI")
    REDIS_URI_OVERRIDE: Optional[str] = Field(None, validation_alias="REDIS_URI")

    @property
    def POSTGRES_URI(self) -> str:
        if self.POSTGRES_URI_OVERRIDE:
            # Ensure old PG URI uses plain postgresql:// for asyncpg
            return self.POSTGRES_URI_OVERRIDE.replace("postgresql+asyncpg://", "postgresql://")
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def POSTGRES_SYNC_URI(self) -> str:
        """同步 URI，给 langgraph PostgresSaver 使用。"""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # ── 数据库连接池 ──
    DB_POOL_MIN_SIZE: int = 3
    DB_POOL_MAX_SIZE: int = 15
    DB_COMMAND_TIMEOUT: int = 30

    # ── Redis ──
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = "deepagents"
    REDIS_DB: int = 0

    # ── Redis 高可用（Sentinel）──
    # 当 REDIS_SENTINEL_HOSTS 非空时，自动切换到 Sentinel 模式
    REDIS_SENTINEL_HOSTS: str = ""  # 逗号分隔，如 "host1:26379,host2:26379"
    REDIS_SENTINEL_MASTER: str = "mymaster"  # Sentinel 监控的 master 名称
    REDIS_SENTINEL_PASSWORD: str = ""  # Sentinel 认证密码（可选）

    # ── Redis 连接池 ──
    REDIS_MAX_CONNECTIONS: int = 20  # 单进程最大 Redis 连接数

    @property
    def REDIS_URI(self) -> str:
        if self.REDIS_URI_OVERRIDE:
            return self.REDIS_URI_OVERRIDE
        return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def REDIS_SENTINEL_HOST_LIST(self) -> list[tuple[str, int]]:
        """解析 Sentinel 主机列表。"""
        if not self.REDIS_SENTINEL_HOSTS:
            return []
        result = []
        for host_port in self.REDIS_SENTINEL_HOSTS.split(","):
            host_port = host_port.strip()
            if ":" in host_port:
                host, port = host_port.rsplit(":", 1)
                result.append((host, int(port)))
            else:
                result.append((host_port, 26379))
        return result

    # ── LLM ──
    LLM_API_KEY: str = Field("", validation_alias="OPENAI_API_KEY")
    LLM_BASE_URL: str = Field("https://api.deepseek.com/v1", validation_alias="OPENAI_BASE_URL")
    LLM_MODEL: str = Field("deepseek-chat", validation_alias="LLM_DEEPSEEK_MODEL")

    # ── Embedding ──
    EMBEDDING_MODEL: str = Field(
        "BAAI/bge-small-zh-v1.5",
        validation_alias=AliasChoices("EMBEDDING_MODEL", "SELF_RAG_EMBEDDING_MODEL")
    )
    EMBEDDING_DIM: int = Field(512, validation_alias=AliasChoices("EMBEDDING_DIM", "MEMORY_EMBEDDING_DIM"))
    # HuggingFace 镜像站（国内环境使用 https://hf-mirror.com 加速模型下载）
    HF_ENDPOINT: str = "https://huggingface.co"

    # ── 外部 API ──
    TAVILY_API_KEY: str = ""

    # ── JWT ──
    JWT_SECRET: str = "change-me-in-production"
    JWT_EXPIRE_HOURS: int = 24

    # ── LDAP（空字符串表示未启用）──
    LDAP_URL: str = ""
    LDAP_BASE_DN: str = ""
    LDAP_USER_RDN: str = ""
    LDAP_USERNAME_ATTR: str = "uid"
    LDAP_EMAIL_ATTR: str = "mail"
    LDAP_USE_TLS: bool = False

    # ── OIDC（空字符串表示未启用）──
    OIDC_ISSUER: str = ""
    OIDC_CLIENT_ID: str = ""
    OIDC_CLIENT_SECRET: str = ""
    OIDC_REDIRECT_URI: str = "http://localhost:8000/api/auth/sso/callback"

    # ── MySQL（教学数据库，工具用） ──
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "deepsearch_demo"
    MYSQL_CHARSET: str = "utf8mb4"
    MYSQL_COLLATION: str = "utf8mb4_unicode_ci"
    MYSQL_SQL_MODE: str = "TRADITIONAL"

    # ── OpenTelemetry 链路追踪 ──
    OTEL_ENABLED: bool = False  # 是否启用分布式链路追踪
    OTEL_SERVICE_NAME: str = "deepagents"  # 服务名称，在 Jaeger/Grafana 中显示
    OTEL_EXPORTER_ENDPOINT: str = "http://localhost:4317"  # OTLP gRPC 端点（Jaeger 默认 4317）
    OTEL_EXPORTER_INSECURE: bool = True  # gRPC 是否使用非安全连接（开发环境 True，生产 False）

    # ── CORS ──
    CORS_ORIGINS: str = "http://localhost:5173"

    # ── 文件上传 ──
    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_UPLOAD_EXTENSIONS: str = ".txt,.csv,.md,.pdf,.docx,.xlsx,.png,.jpg,.jpeg,.gif,.json"

    # ── 日志 ──
    LOG_FORMAT: str = "console"  # "console" (开发模式，彩色) 或 "json" (生产模式)

    # ── 任务 ──
    MAX_CONCURRENT_TASKS_PER_USER: int = 3
    TASK_TIMEOUT_SECONDS: int = 300

    # ── Worker 分布式部署 ──
    WORKER_MAX_JOBS: int = 10  # 单 Worker 进程最大并发任务数
    WORKER_HEALTH_TTL: int = 30  # Worker 心跳键 TTL（秒），超时视为离线

    # ── 会话清理 ──
    SESSION_RETENTION_DAYS: int = 90

    model_config = ConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """获取全局配置单例。首次调用时自动校验必填项。"""
    global _settings
    if _settings is None:
        _settings = Settings()
        _validate_required(_settings)
    return _settings


def _validate_required(s: Settings) -> None:
    """启动时校验必填配置。"""
    missing = []
    if not s.LLM_API_KEY:
        missing.append("LLM_API_KEY (大模型API密钥，必填)")
    if not s.TAVILY_API_KEY:
        print("[Config] 警告: TAVILY_API_KEY 未设置，网络搜索功能将不可用", file=sys.stderr)
    if s.JWT_SECRET == "change-me-in-production":
        if not s.DEBUG:
            raise SystemExit(
                "[Config] 致命错误: JWT_SECRET 使用默认值，非 DEBUG 模式下必须修改！\n"
                "  请在 .env 中设置 JWT_SECRET=<随机强密码> 或启用 DEBUG=True"
            )
        print("[Config] 警告: JWT_SECRET 使用默认值，生产环境请务必修改！", file=sys.stderr)
    if missing:
        raise SystemExit(
            "缺少必填配置项，请设置以下环境变量:\n  " + "\n  ".join(missing)
        )


# 模块级快捷访问
settings = get_settings()

# 尽早设置 HF_ENDPOINT，确保 sentence-transformers 加载模型时使用镜像站
# 这必须在任何 SentenceTransformer 实例化之前执行
import os as _os
_os.environ["HF_ENDPOINT"] = settings.HF_ENDPOINT
