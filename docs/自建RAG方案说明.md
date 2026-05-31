# 自建 RAG 方案说明

## 架构概览

DeepAgents 自建 RAG 系统基于 ChromaDB 向量检索 + BM25 关键词检索 + RRF 融合 + LLM 生成答案，专为私域场景设计，无需外部向量数据库服务。

## 技术栈

| 组件 | 技术选型 | 说明 |
|------|---------|------|
| 向量数据库 | ChromaDB（本地持久化） | 嵌入式运行，无需独立服务 |
| Embedding 模型 | `BAAI/bge-small-zh-v1.5` | 512 维，中文优化，约 100MB |
| 关键词检索 | BM25（jieba 分词） | 稀疏检索，捕获精确关键词命中 |
| 文档拆分 | RecursiveCharacterTextSplitter | 父子文档策略（详见下文） |
| 融合算法 | RRF (Reciprocal Rank Fusion) | k=60 平滑参数 |
| 答案生成 | DeepSeek LLM | 基于召回上下文生成自然语言答案 |

## 数据流

```
文档摄入:
  文件 (PDF/DOCX/MD/TXT)
    → 解析提取全文
    → 父块拆分 (parent_splitter, 1000 字)
    → 子块拆分 (child_splitter, 200 字, overlap=50)
    → 子块 Embedding (bge-small-zh-v1.5)
    → 子块存入 ChromaDB 主 collection
    → 父块存入 ChromaDB _parents collection

查询检索:
  用户问题
    → 向量化 (embedding)
    → 稠密检索: ChromaDB.query(top_k=4)          → 稠密排名
    → 稀疏检索: jieba 分词 → BM25.search(top_k=10) → BM25 排名
    → RRF 融合 (k=60) → 去重 → Top-4 父块 ID
    → 从 _parents collection 获取完整父块文本
    → LLM 基于父块上下文生成答案
```

### 父子文档策略

```
┌─────────────────────────────────────┐
│  父块 (1000 字)                      │
│  完整段落/章节，提供完整上下文        │
│  ┌───────────────────────────────┐  │
│  │ 子块 1 (200 字)               │  │
│  │ 参与向量检索，精确匹配         │  │
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │ 子块 2 (200 字, overlap 50)   │  │
│  └───────────────────────────────┘  │
│  ...                                │
└─────────────────────────────────────┘

子块检索 → 找到匹配的父块 ID → 返回完整父块文本 → LLM 作答
```

- **子块检索**：200 字小粒度，嵌入匹配更精准
- **父块作答**：1000 字大粒度，提供完整段落上下文给 LLM
- **Overlap**：相邻子块重叠 50 字符，避免边界切断关键信息

## 关键设计决策

### 1. 双路融合（稠密 + 稀疏）

- **稠密检索（向量）**：语义相似度，召回概念相关的文档
- **稀疏检索（BM25）**：关键词匹配，召回精确命中术语的文档
- **RRF 融合**：按 parent_id 聚合两路排名，避免同一父块的多个子块同时命中造成偏置

### 2. BM25 懒加载缓存

首次查询时从 ChromaDB 子块重建 BM25 索引并缓存在内存中。文档摄入或删除时自动失效重建。

### 3. 存储结构

每个知识库对应两个 ChromaDB collection：

- `{kb_name}` — 子块（有 embedding，参与稠密检索）
- `{kb_name}_parents` — 父块（无 embedding，仅做 ID 查找回填）

### 4. RAGEngine 单例

通过 `get_rag_engine()` 获取全局单例，首次访问时加载 embedding 模型和 ChromaDB 客户端，后续请求复用。

## 配置参数

所有参数定义在 `app/self_rag/config.py`，可通过环境变量覆盖：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | Embedding 模型名称 |
| `EMBEDDING_DIM` | `512` | 向量维度 |
| `SELF_RAG_CHROMA_DIR` | `self_rag_data/chroma/` | ChromaDB 持久化目录 |
| `SELF_RAG_DOC_DIR` | `self_rag_data/documents/` | 文档存储目录 |
| `SELF_RAG_TOP_K` | `4` | 稠密检索返回数 |
| `SELF_RAG_PARENT_CHUNK_SIZE` | `1000` | 父块大小（字符数） |
| `SELF_RAG_CHILD_CHUNK_SIZE` | `200` | 子块大小（字符数） |
| `SELF_RAG_CHUNK_OVERLAP` | `50` | 相邻块重叠字符数 |
| `SELF_RAG_BM25_ENABLED` | `true` | 启用 BM25 混合检索 |
| `SELF_RAG_BM25_TOP_K` | `10` | BM25 路召回数 |
| `SELF_RAG_RRF_K` | `60` | RRF 融合平滑参数 |
| `SELF_RAG_HYBRID_TOP_K` | `4` | 融合后返回的最终父块数 |
| `SELF_RAG_KEYWORD_EXTRACTION` | `false` | LLM 关键词提取（实验性） |

## 部署注意事项

### 容器化部署

在生产 Docker 环境中，ChromaDB 数据默认存储在容器内的 `self_rag_data/` 目录。如需持久化知识库数据，应在 `docker-compose.prod.yaml` 中为 `app` 和 `worker` 服务挂载 volume：

```yaml
app:
  volumes:
    - rag_data:/app/self_rag_data

worker:
  volumes:
    - rag_data:/app/self_rag_data

volumes:
  rag_data:
```

### 首次启动

首次启动时 embedding 模型（约 100MB）会自动从 HuggingFace 下载。在无网络环境部署时，可预先下载模型并挂载到容器中，通过 `TRANSFORMERS_CACHE` 环境变量指定缓存目录。
