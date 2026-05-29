# DeepAgents — 多智能体深度研究系统

## 项目概述

基于 DeepAgents 框架的多智能体协作系统，主智能体负责理解用户任务、规划步骤、调度子智能体并汇总结果。三个专家子智能体分别负责网络搜索、数据库查询和知识库检索，互补完成信息获取。最终由主智能体生成 Markdown/PDF 交付文档。

## 技术栈

- **框架**: DeepAgents 0.5.7 + LangChain 1.2 + LangGraph 1.1
- **LLM**: DeepSeek (`deepseek-chat`) 通过 OpenAI 兼容 API，配置在 `app/agent/llm.py`
- **Web**: FastAPI + WebSocket，`app/api/server.py`
- **包管理**: uv（`pyproject.toml` + `uv.lock`），Python >=3.12,<3.13
- **自建 RAG**: ChromaDB（嵌入式持久化）+ sentence-transformers（`BAAI/bge-small-zh-v1.5`）+ jieba 分词 + BM25 稀疏检索，位于 `app/self_rag/`
- **数据库**: MySQL，通过 `mysql-connector-python` 直连
- **网络搜索**: Tavily API
- **文档生成**: Markdown 直写 + Markdown→PDF（reportlab）

## 项目结构

```
deepsearch-agents/
├── app/
│   ├── agent/
│   │   ├── main_agent.py          # 主智能体组装 + run_deep_agent() 异步入口
│   │   ├── llm.py                 # LLM 初始化（DeepSeek + OpenAI 兼容协议）
│   │   ├── prompts.py             # YAML 提示词加载
│   │   └── subagents/
│   │       ├── network_search_agent.py   # Tavily 网络搜索子智能体
│   │       ├── database_query_agent.py   # MySQL 数据库查询子智能体
│   │       └── knowledge_base_agent.py   # 自建 RAG 知识库子智能体
│   ├── prompt/
│   │   └── prompts.yml            # 主智能体 + 子智能体提示词集中配置
│   ├── api/
│   │   ├── server.py              # FastAPI 入口（REST + WebSocket + KB管理API）
│   │   ├── monitor.py             # ToolMonitor：工具调用→WebSocket 事件推送
│   │   └── context.py             # ContextVar：协程级 session_dir/thread_id
│   ├── tools/
│   │   ├── tavily_tool.py         # internet_search 工具
│   │   ├── db_tools.py            # list_sql_tables / get_table_data / execute_sql_query
│   │   ├── self_rag_tools.py      # list_knowledge_bases / query_knowledge_base
│   │   ├── markdown_tools.py      # generate_markdown 工具
│   │   ├── pdf_tools.py           # convert_md_to_pdf 工具
│   │   └── upload_file_read_tool.py
│   ├── self_rag/
│   │   ├── engine.py              # RAGEngine：ChromaDB + embedding + BM25 + LLM QA
│   │   └── config.py              # 自建 RAG 配置（拆分、检索、BM25 参数等）
│   └── utils/
│       ├── path_utils.py          # resolve_path：虚拟路径→本地会话目录
│       └── word_converter.py      # Markdown→PDF 底层转换（reportlab）
├── frontend/                      # 前端（Vite）
├── docker/                        # MySQL Docker 部署
├── pyproject.toml
└── .env                           # 环境变量（不提交）
```

## 架构要点

### 智能体协作流

```
用户任务 → main_agent (create_deep_agent)
  ├─ 信息获取阶段: 并行/串行调用三个子智能体
  │   ├─ 网络搜索助手 (tavily) → internet_search
  │   ├─ 数据库查询助手 (db) → list_sql_tables → get_table_data → execute_sql_query
  │   └─ 知识库助手 (rag) → list_knowledge_bases → query_knowledge_base
  └─ 文件生成阶段: 主智能体直接调用
      ├─ generate_markdown
      └─ convert_md_to_pdf
```

### 关键设计决策

- **子智能体只获取信息，不生成文件**：文件生成工具只挂载在主智能体上，子智能体拿到原始数据后返回给主智能体汇总，再由主智能体调用 `generate_markdown` / `convert_md_to_pdf`
- **会话隔离**：每次任务创建 `output/session_{thread_id}/` 工作目录，上传文件先存到 `updated/session_{thread_id}/` 再复制到工作目录
- **ContextVar 传递上下文**：`session_dir` 和 `thread_id` 通过 `contextvars.ContextVar` 在协程链路中隐式传递，工具层无需显式传参
- **monitor 埋点**：所有工具调用和子智能体调用都通过 `monitor.report_tool()` / `monitor.report_assistant()` 上报，由 `ToolMonitor._emit()` 通过 WebSocket 推送给前端，同时保留控制台输出作为保底

### 自建 RAG 链路

#### 文档拆分（父子文档策略）

```
文件 → _parse_file(PDF/DOCX/MD/TXT) → 全文
  → parent_splitter(1000字, RecursiveCharacterTextSplitter)
     分隔符优先级: \n\n(段落) → \n(换行) → 。.(句子) → ；;(短句) → ，,(逗号) → 字符
  → 父块[] → child_splitter(200字) → 子块[]
  → 子块 embed(bge-small-zh-v1.5) → ChromaDB 主 collection（参与检索）
  → 父块直接存储 → ChromaDB _parents collection（仅做上下文回填）
```

#### 检索（双路融合）

```
用户问题
  ├─→ embed → ChromaDB.query(top_k=4)      → dense_ranks  {parent_id: rank}
  ├─→ jieba → BM25.search(top_k=10)        → bm25_ranks   {parent_id: rank}
  └─→ RRF 融合 (k=60) → top 4 parent_ids
      → ChromaDB._parents.get(ids) → 父块文本
      → _generate_answer(DeepSeek LLM) → 答案
```

关键设计：
- **子块检索、父块作答**：200字子块匹配更精准，1000字父块提供完整段落上下文
- **稠密 + 稀疏互补**：向量相似度捕获语义相关，BM25 捕获精确关键词命中
- **RRF 去重融合**：按 parent_id 聚合两路排名，避免同一父块的多个子块同时命中造成偏置
- **BM25 懒加载缓存**：首次查询时从 ChromaDB 子块重建 BM25 索引并缓存在内存，摄入/删除时失效

#### 存储结构

每个知识库对应两个 ChromaDB collection：
- `{kb_name}` — 子块（有 embedding，参与稠密检索）
- `{kb_name}_parents` — 父块（无 embedding，仅做 ID 查找回填）

RAGEngine 是单例（`get_rag_engine()`），首次访问时加载 embedding 模型（~100MB）和 ChromaDB 客户端。

### 数据库查询安全

`db_tools.py` 中 `execute_sql_query` 只允许 `SELECT/SHOW/DESCRIBE/EXPLAIN` 开头语句，`get_table_data` 对表名做 `\w+` 正则白名单校验，返回行数上限 1000。

## 常用命令

```bash
# 安装依赖
uv sync

# 启动后端 (端口 8000)
uv run python -m app.api.server

# 运行主智能体（脚本调试模式）
uv run python -m app.agent.main_agent

# 单独测试工具
uv run python -m app.tools.db_tools
uv run python -m app.tools.markdown_tools
uv run python -m app.tools.pdf_tools
uv run python -m app.tools.tavily_tool
```

## 环境变量

复制 `.env.example` 为 `.env`，关键变量：

| 变量 | 说明 |
|------|------|
| `OPENAI_BASE_URL` | LLM API 地址（默认 DeepSeek） |
| `OPENAI_API_KEY` | LLM API 密钥 |
| `LLM_DEEPSEEK_MODEL` | 模型名（默认 `deepseek-chat`） |
| `TAVILY_API_KEY` | Tavily 搜索 API 密钥 |
| `MYSQL_HOST/PORT/USER/PASSWORD/DATABASE` | MySQL 连接配置 |
| `SELF_RAG_EMBEDDING_MODEL` | 嵌入模型（默认 `BAAI/bge-small-zh-v1.5`） |
| `SELF_RAG_TOP_K` | 稠密检索返回片段数（默认 4） |
| `SELF_RAG_PARENT_CHUNK_SIZE` | 父块大小，字符数（默认 1000） |
| `SELF_RAG_CHILD_CHUNK_SIZE` | 子块大小，字符数（默认 200） |
| `SELF_RAG_CHUNK_OVERLAP` | 相邻块重叠字符数（默认 50） |
| `SELF_RAG_BM25_ENABLED` | 是否启用 BM25 混合检索（默认 true） |
| `SELF_RAG_BM25_TOP_K` | BM25 路召回数（默认 10） |
| `SELF_RAG_RRF_K` | RRF 融合平滑参数（默认 60） |
| `SELF_RAG_HYBRID_TOP_K` | 融合后最终返回父块数（默认 4） |

## 提示词管理

所有智能体提示词集中在 `app/prompt/prompts.yml`，通过 `app/agent/prompts.py` 的 `load_yaml()` 加载。修改智能体行为只需编辑 YAML，无需改动 Python 代码。子智能体的 `description` 字段决定主智能体的路由判断，`system_prompt` 约束子智能体的执行方式。

## 注意事项

- 项目使用 `uv` 管理依赖，不要用 `pip install`，用 `uv sync` 或 `uv add`
- `.env` 中的 API 密钥不要提交到 git
- 旧 RAGFlow 代码保留在 `app/ragflow/` 和 `app/tools/ragflow_tools.py`，但已不被活跃代码引用，仅作参考
- Windows 环境下路径统一使用 `/` 分隔符（代码中有 `replace("\\", "/")` 处理）
- 前端 `frontend/node_modules` 体积较大，搜索时注意排除
