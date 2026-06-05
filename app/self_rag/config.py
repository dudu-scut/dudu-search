"""
自建 RAG 配置模块

统一管理嵌入模型、ChromaDB 存储路径和 LLM 相关配置。
"""

import os
from pathlib import Path

from app.config import settings

# 统一嵌入模型配置：优先读 EMBEDDING_MODEL，兼容旧 SELF_RAG_EMBEDDING_MODEL
EMBEDDING_MODEL = settings.EMBEDDING_MODEL

# ChromaDB 持久化目录，默认在 app 同级
_base = Path(__file__).resolve().parents[1]
CHROMA_PERSIST_DIR = os.getenv(
    "SELF_RAG_CHROMA_DIR",
    str(_base / "self_rag_data" / "chroma"),
)

# 文档存储目录
DOC_STORE_DIR = os.getenv(
    "SELF_RAG_DOC_DIR",
    str(_base / "self_rag_data" / "documents"),
)

# 检索参数
TOP_K = int(os.getenv("SELF_RAG_TOP_K", "4"))

# 父子文档拆分配置
# 父块：大粒度段落/章节，用于给 LLM 提供完整上下文
PARENT_CHUNK_SIZE = int(os.getenv("SELF_RAG_PARENT_CHUNK_SIZE", "1000"))
# 子块：小粒度句子/短段落，用于高精度语义检索
CHILD_CHUNK_SIZE = int(os.getenv("SELF_RAG_CHILD_CHUNK_SIZE", "200"))
# 相邻块重叠字符数
CHUNK_OVERLAP = int(os.getenv("SELF_RAG_CHUNK_OVERLAP", "50"))

# BM25 混合检索配置
BM25_ENABLED = os.getenv("SELF_RAG_BM25_ENABLED", "true").lower() != "false"
BM25_TOP_K = int(os.getenv("SELF_RAG_BM25_TOP_K", "10"))
RRF_K = int(os.getenv("SELF_RAG_RRF_K", "60"))
HYBRID_TOP_K = int(os.getenv("SELF_RAG_HYBRID_TOP_K", "4"))
# LLM 关键词提取预留开关（默认关闭）
KEYWORD_EXTRACTION = os.getenv("SELF_RAG_KEYWORD_EXTRACTION", "false").lower() == "true"

# LLM 配置从统一配置模块读取
LLM_BASE_URL = settings.LLM_BASE_URL
LLM_API_KEY = settings.LLM_API_KEY
LLM_MODEL = settings.LLM_MODEL
