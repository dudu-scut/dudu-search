# DeepAgents — 多智能体深度研究系统

## 项目概述

基于 DeepAgents 框架的多智能体协作系统，主智能体负责理解用户任务、规划步骤、调度子智能体并汇总结果。三个专家子智能体分别负责网络搜索、数据库查询和知识库检索，互补完成信息获取。最终由主智能体生成 Markdown/PDF 交付文档。

已完成全链路生产优化（6 阶段 16 计划）：配置管理、异常体系、JWT 认证、用户组隔离、安全加固、结构化日志、ARQ 任务队列、限流、Prometheus 指标、会话持久化、测试套件、前端增强、容器化部署。

## 技术栈

| 类别 | 技术 |
|------|------|
| 智能体框架 | DeepAgents 0.5.7 + LangChain 1.2 + LangGraph 1.1 |
| LLM | DeepSeek (deepseek-chat)，OpenAI 兼容 API (`app/agent/llm.py`) |
| Web | FastAPI + WebSocket (`app/api/server.py`) |
| 包管理 | uv (`pyproject.toml` + `uv.lock`)，Python >=3.12,<3.13 |
| 数据库 | PostgreSQL 16 + pgvector (asyncpg 直连) |
| 缓存/队列 | Redis 7 (缓存 + ARQ 任务队列) |
| RAG | ChromaDB + sentence-transformers (BAAI/bge-small-zh-v1.5) + jieba BM25 |
| 认证 | JWT (Bearer Token, PyJWT) + bcrypt 密码哈希 |
| 日志 | structlog (JSON/console 双模式, trace_id 注入, 敏感字段脱敏) |
| 指标 | prometheus-client (Counter/Histogram/Gauge 全链路埋点) |
| 前端 | React + TypeScript + Vite + Ant Design |
| 部署 | Docker 多阶段构建 + docker-compose + Nginx 反向代理 |
| 测试 | pytest + pytest-asyncio + pytest-cov + httpx |

## 项目结构

```
deepsearch-agents/
├── app/
│   ├── agent/
│   │   ├── main_agent.py          # 主智能体: create_deep_agent() + run_deep_agent()
│   │   ├── llm.py                 # LLM 初始化 (get_llm)
│   │   ├── prompts.py             # YAML 提示词加载 (load_yaml)
│   │   └── subagents/
│   │       ├── network_search_agent.py
│   │       ├── database_query_agent.py
│   │       └── knowledge_base_agent.py
│   ├── api/
│   │   ├── server.py              # FastAPI 入口: REST + WebSocket + 中间件栈
│   │   ├── monitor.py             # ToolMonitor: 工具调用→WebSocket 推送 + Redis 缓存
│   │   └── context.py             # ContextVar: trace_id/user_id/group_id/session_dir/thread_id
│   ├── auth/
│   │   ├── jwt.py                 # JWT 生成/验证 (create_access_token, hash_password, verify_password)
│   │   └── dependencies.py        # FastAPI 依赖: get_current_user, require_admin
│   ├── storage/
│   │   ├── db.py                  # asyncpg 连接池 + Schema: sessions/messages/agent_events/long_term_memories
│   │   ├── redis_client.py        # Redis 客户端: 缓存/事件/限流/取消信号
│   │   └── memory_service.py      # 记忆服务: 语义检索/上下文构建/会话巩固
│   ├── tasks/
│   │   └── cleanup.py             # ARQ cron: 过期会话清理 (每天凌晨 3 点)
│   ├── tools/
│   │   ├── tavily_tool.py         # internet_search (Tavily API)
│   │   ├── db_tools.py            # list_sql_tables/get_table_data/execute_sql_query (SQL 安全校验)
│   │   ├── self_rag_tools.py      # list_knowledge_bases/query_knowledge_base (ChromaDB RAG)
│   │   ├── markdown_tools.py      # generate_markdown (文件输出)
│   │   ├── pdf_tools.py           # convert_md_to_pdf (reportlab)
│   │   └── upload_file_read_tool.py
│   ├── self_rag/
│   │   ├── engine.py              # RAGEngine 单例: ChromaDB + embedding + BM25 + LLM QA
│   │   └── config.py              # RAG 配置: 拆分/检索/BM25/RRF 参数
│   ├── prompt/
│   │   └── prompts.yml            # 主智能体 + 子智能体提示词集中配置
│   ├── config.py                  # Pydantic BaseSettings 统一配置 (所有环境变量)
│   ├── exceptions.py              # 异常层次: AppError → LLMError/DBError/AuthError/ValidationError
│   ├── logging_config.py          # structlog 配置: setup_logging/get_logger + 脱敏处理器
│   ├── metrics.py                 # Prometheus: TASK/TOOL/LLM/SQL/HTTP 指标 + ACTIVE_TASKS
│   └── worker.py                  # ARQ Worker: run_agent_task + WorkerSettings (cron/concurrency)
├── tests/
│   ├── conftest.py                # Fixtures: mock DB pool, mock Redis, test FastAPI app, auth headers
│   ├── test_tools/                # 工具层测试 (34 tests)
│   │   ├── test_db_tools.py       # SQL 安全校验 (20 tests)
│   │   ├── test_tavily_tool.py    # 搜索工具 (7 tests)
│   │   └── test_markdown_tools.py # Markdown 工具 (7 tests)
│   ├── test_api/                  # API 层测试 (31 tests)
│   │   ├── test_auth.py           # 认证: 注册/登录/me (13 tests)
│   │   ├── test_sessions.py       # 会话 CRUD (7 tests)
│   │   ├── test_kb.py             # 知识库隔离 (4 tests)
│   │   ├── test_upload.py         # 文件上传安全 (5 tests)
│   │   └── test_ws.py             # WebSocket 连接 (2 tests)
│   ├── test_agent/                # Agent 集成测试 (19 tests)
│   │   └── test_main_agent.py     # 重试逻辑 + Agent 构建
│   └── eval/                      # 评测基准
│       ├── benchmark.json         # 6 条评测用例 (搜索/数据库/报告)
│       ├── metrics.py             # EvalResult/EvalReport + compare_reports
│       └── run_eval.py            # CLI 评测执行器
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── ChatComposer.tsx   # 输入框 + 发送/取消按钮 (isRunning 切换图标)
│   │   │   ├── EventStream.tsx    # 实时事件流 + 类型筛选 (Tag.CheckableTag)
│   │   │   ├── SessionList.tsx    # 历史会话列表 (30s 轮询)
│   │   │   ├── TaskHistory.tsx    # 历史任务表格 (搜索/筛选/30s 轮询)
│   │   │   ├── FilePreview.tsx    # 文件预览弹窗 (图片/Markdown/PDF)
│   │   │   ├── LoginPage.tsx      # 登录/注册 (表单验证, token 存储)
│   │   │   └── MemoryPanel.tsx
│   │   ├── hooks/
│   │   │   └── useDeepAgentSession.ts  # WebSocket + 状态管理 (15s/30s 轮询)
│   │   ├── lib/
│   │   │   ├── api.ts             # REST API 函数 (startTask/cancelTask/listSessions/...)
│   │   │   └── auth.ts            # 前端认证 (getUser/logout/authFetch)
│   │   └── types.ts
│   ├── Dockerfile                 # 前端多阶段构建 (pnpm build + nginx)
│   └── nginx.conf                 # SPA 配置: API 代理/WebSocket/Gzip/缓存
├── docker/
│   └── docker-compose.yaml        # 开发环境 (PostgreSQL + Redis)
├── Dockerfile                     # 后端多阶段构建 (uv sync + python:3.12-slim)
├── docker-compose.prod.yaml       # 生产环境全栈: postgres/redis/app/worker(x2)/frontend
├── nginx.conf                     # 生产 Nginx: /api /ws /live /ready /metrics 代理
├── .env.prod                      # 生产环境变量模板
├── pyproject.toml
└── CLAUDE.md
```

## 架构要点

### 中间件栈 (server.py lifespan)

```
请求 → CORS → QPS 限流 (Redis 滑动窗口) → HTTP 指标 → trace_id → Auth (JWT) → 路由处理
```

### 任务生命周期

```
POST /api/task → ARQ enqueue (Redis) → Worker: run_agent_task()
  ├─ Redis: 设置 cancel:{thread_id} 标志 + task_job:{thread_id} 映射
  ├─ DB: status queued→running→completed/failed/cancelled
  └─ Prometheus: TASK_TOTAL{status} + ACTIVE_TASKS + TASK_DURATION

POST /api/task/{thread_id}/cancel
  ├─ Redis: SET cancel:{thread_id}=1
  ├─ ARQ: job.abort() (队列中未执行)
  └─ Worker: 每 0.5s 轮询 cancel:{thread_id}，asyncio.wait FIRST_COMPLETED 模式
```

### 取消机制 (三级)

1. **Redis 取消信号**: `cancel:{thread_id}` key，API 写入，Worker 轮询 (0.5s)
2. **ARQ job.abort()**: 任务还在队列时直接中止
3. **Worker 竞速**: `asyncio.wait([agent_task, cancel_event.wait()], FIRST_COMPLETED)` → 取消信号先到则 cancel agent_task

### 数据库 Schema

`app/storage/db.py` 初始化 4 表 + FK 约束:
- **sessions**: thread_id (PK), user_id, group_id, title, status (queued/running/completed/failed/cancelled), metadata
- **messages**: thread_id (FK→sessions CASCADE), role, content, tool_calls, token_count
- **agent_events**: thread_id (FK→sessions CASCADE), event_type, message, payload
- **long_term_memories**: memory_type (fact/preference/episodic/semantic), content, embedding vector(512), importance

### 配置模块 (app/config.py)

Pydantic `BaseSettings`，自动从 `.env` 读取，所有配置集中管理:

```python
class Settings(BaseSettings):
    # LLM
    LLM_API_KEY: str
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    LLM_MODEL: str = "deepseek-chat"
    # Database
    POSTGRES_HOST/PORT/USER/PASSWORD/DB
    DB_POOL_MIN_SIZE/MAX_SIZE/COMMAND_TIMEOUT
    # Redis
    REDIS_HOST/PORT/PASSWORD/DB
    # Auth
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440
    # Limits
    USER_MAX_CONCURRENT_TASKS: int = 3
    GLOBAL_QPS_LIMIT: int = 50
    TASK_TIMEOUT_SECONDS: int = 300
    SESSION_RETENTION_DAYS: int = 30
    # Logging
    LOG_FORMAT: Literal["console", "json"] = "console"
    # ... more settings
```

### 指标模块 (app/metrics.py)

```python
TASK_TOTAL = Counter("task_total", "...", ["status"])        # started/completed/failed/cancelled
TASK_DURATION = Histogram("task_duration_seconds", "...")
TOOL_CALL_TOTAL = Counter("tool_call_total", "...", ["tool_name"])
TOOL_CALL_DURATION = Histogram("tool_call_duration_seconds", "...", ["tool_name"])
LLM_CALL_TOTAL/DURATION, SQL_QUERY_TOTAL/DURATION, HTTP_REQUEST_TOTAL/DURATION
ACTIVE_TASKS = Gauge("active_tasks", "...")
```

端点: `GET /metrics` (admin 认证保护)

### 自建 RAG 链路

父子文档策略: 200 字子块检索 + 1000 字父块作答，双路融合 (dense + BM25 → RRF k=60 → top 4)

## 常用命令

```bash
# 安装依赖
uv sync

# 启动数据库 (开发环境)
cd docker && docker compose up -d postgres redis

# 启动后端 API (端口 8000)
uv run python -m app.api.server

# 启动 ARQ Worker (异步任务执行)
uv run arq app.worker.WorkerSettings

# 启动前端 (开发模式, 端口 5173)
cd frontend && pnpm dev

# 运行全部测试
uv run pytest tests/ -v

# 运行特定测试 + 覆盖率
uv run pytest tests/test_tools/ -v --cov=app.tools --cov-report=term-missing

# 运行评测基准
uv run python -m tests.eval.run_eval

# 前端构建检查
cd frontend && pnpm build

# 生产部署
docker compose -f docker-compose.prod.yaml up -d
```

## 环境变量

复制 `.env.example` 为 `.env`，关键变量:

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | LLM API 密钥 (必填) | - |
| `LLM_BASE_URL` | LLM API 地址 | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 模型名称 | `deepseek-chat` |
| `TAVILY_API_KEY` | Tavily 搜索 API 密钥 (建议) | - |
| `POSTGRES_HOST/PORT/USER/PASSWORD/DB` | PostgreSQL 连接 | localhost/5432/deepagents/deepagents/deepagents |
| `POSTGRES_URI` | PostgreSQL 连接 URI (优先级高于单个变量) | - |
| `DB_POOL_MIN_SIZE/MAX_SIZE` | 连接池配置 | 2/10 |
| `DB_COMMAND_TIMEOUT` | SQL 命令超时 (秒) | 30 |
| `REDIS_HOST/PORT/PASSWORD/DB` | Redis 连接 | localhost/6379/deepagents/0 |
| `JWT_SECRET` | JWT 签名密钥 (生产务必修改) | - |
| `JWT_ALGORITHM` | JWT 算法 | HS256 |
| `JWT_EXPIRE_MINUTES` | Token 过期时间 (分钟) | 1440 |
| `USER_MAX_CONCURRENT_TASKS` | 每用户最大并发任务 | 3 |
| `GLOBAL_QPS_LIMIT` | 全局每秒请求上限 | 50 |
| `TASK_TIMEOUT_SECONDS` | 单任务超时 (秒) | 300 |
| `SESSION_RETENTION_DAYS` | 会话保留天数 | 30 |
| `LOG_FORMAT` | 日志格式: console/json | console |
| `CORS_ORIGINS` | 允许跨域来源 (逗号分隔) | `*` (开发) |
| `NGINX_PORT` | 生产部署对外端口 | 80 |
| `EMBEDDING_MODEL` | 嵌入模型 | `BAAI/bge-small-zh-v1.5` |
| `SELF_RAG_TOP_K` | 稠密检索返回数 | 4 |
| `SELF_RAG_BM25_ENABLED` | 启用 BM25 | true |

## 关键设计决策

- **子智能体只获取信息，不生成文件**: 文件生成工具只挂载主智能体
- **ContextVar 传递上下文**: trace_id/user_id/group_id/session_dir/thread_id 通过 ContextVar 隐式传递
- **monitor 多通道推送**: `_emit()` → WebSocket + Redis 缓存 + 控制台
- **mock 优先测试**: 所有外部依赖通过 Mock 隔离，不连真实服务
- **`.env` 不入库**: API 密钥等敏感信息通过 Pydantic BaseSettings 从环境变量读取

## 注意事项

- 使用 `uv` 管理依赖，不要用 `pip install`，用 `uv sync` 或 `uv add`
- 安装 ARQ 后 redis 会被降级到 5.3.1 (ARQ 要求 redis<6.0)，不影响功能
- `.env` 中的 API 密钥不要提交到 git
- Windows 环境下路径统一使用 `/` 分隔符
- 前端 `frontend/node_modules` 体积较大，搜索时注意排除
- 旧 RAGFlow 代码保留在 `app/ragflow/` 但已不被引用，仅作参考
