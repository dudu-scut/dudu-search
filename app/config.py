"""统一配置模块 — 所有环境变量从这里读取，启动时自动校验必填项。"""
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings
from typing import Optional


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

    @property
    def REDIS_URI(self) -> str:
        if self.REDIS_URI_OVERRIDE:
            return self.REDIS_URI_OVERRIDE
        return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

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

    # ── 外部 API ──
    TAVILY_API_KEY: str = ""

    # ── JWT ──
    JWT_SECRET: str = "change-me-in-production"
    JWT_EXPIRE_HOURS: int = 24

    # ── MySQL（教学数据库，工具用） ──
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "deepsearch_demo"
    MYSQL_CHARSET: str = "utf8mb4"
    MYSQL_COLLATION: str = "utf8mb4_unicode_ci"
    MYSQL_SQL_MODE: str = "TRADITIONAL"

    # ── CORS ──
    CORS_ORIGINS: str = "http://localhost:5173"

    # ── 文件上传 ──
    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_UPLOAD_EXTENSIONS: str = ".txt,.csv,.md,.pdf,.docx,.xlsx,.png,.jpg,.jpeg,.gif,.json"

    # ── 任务 ──
    MAX_CONCURRENT_TASKS_PER_USER: int = 3
    TASK_TIMEOUT_SECONDS: int = 300

    # ── 会话清理 ──
    SESSION_RETENTION_DAYS: int = 90

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


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
        print("[Config] 警告: TAVILY_API_KEY 未设置，网络搜索功能将不可用")
    if s.JWT_SECRET == "change-me-in-production":
        print("[Config] 警告: JWT_SECRET 使用默认值，生产环境请务必修改！")
    if missing:
        raise SystemExit(
            "缺少必填配置项，请设置以下环境变量:\n  " + "\n  ".join(missing)
        )


# 模块级快捷访问
settings = get_settings()
