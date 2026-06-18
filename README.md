# NexusAI · Research Agent

NexusAI 多智能体平台的研究 Agent，通过多个专家子智能体协作完成复杂的信息检索、分析与报告生成任务。

## 项目定位

本项目是 **NexusAI** 智能体生态中的 **Research Agent**（深度研搜），专注于多源信息检索与研究报告生成。NexusAI 是基于 A2A（Agent-to-Agent）协议的多智能体协作平台，Research Agent 是接入其中的第一个专业能力 Agent。

```
NexusAI（A2A 协议平台）
    ├── Research Agent  ← 本项目（深度研搜）
    ├── Coding Agent    ← 后续（代码生成与分析）
    └── ...             ← 更多专业 Agent
```

### Research Agent 核心能力

- **多源信息融合**：互联网搜索（Tavily）、企业知识库（RAG）、业务数据库（SQL）三路并行采集
- **多智能体协作**：主智能体协调 3 个专家子智能体，互补完成信息获取与报告生成
- **自建 RAG 系统**：父子文档分块 + 向量检索（pgvector）+ BM25 关键词检索 + RRF 融合排序
- **实时交互**：WebSocket 实时推送任务进度，SSE 推送会话列表变更
- **企业级基础设施**：RBAC 权限、SSO（LDAP/OIDC）、Prometheus 指标、OpenTelemetry 链路追踪

## 技术栈

| 类别 | 技术 |
|------|------|
| 智能体框架 | LangChain + LangGraph + PostgresSaver |
| 大语言模型 | DeepSeek (deepseek-chat / deepseek-v4-flash) |
| 后端框架 | FastAPI + Uvicorn |
| 数据库 | PostgreSQL 16 + pgvector (向量检索) |
| 缓存/队列 | Redis 7 (缓存 + ARQ 任务队列 + Pub/Sub) |
| 知识库 | ChromaDB + sentence-transformers (BGE-small-zh-v1.5) |
| 前端 | React + TypeScript + Vite + Ant Design |
| 认证 | JWT + RBAC (28 权限点) + LDAP + OIDC SSO |
| 可观测性 | Prometheus 指标 + OpenTelemetry 链路追踪 + structlog 结构化日志 |
| 部署 | Docker + docker-compose + Nginx 反向代理 |

## 快速开始

### 环境要求

- Python 3.12
- Node.js 18+
- Docker Desktop
- API 密钥：DeepSeek API、Tavily API

### 1. 克隆与安装

```bash
git clone https://github.com/dudu-scut/dudu-search.git
cd dudu-search

# 安装后端依赖
uv sync

# 安装前端依赖
cd frontend
pnpm install
cd ..
```

### 2. 环境配置

```bash
cp .env.example .env
```

编辑 `.env`，配置关键变量：

```env
# LLM API
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=your_api_key_here
LLM_DEEPSEEK_MODEL=deepseek-chat

# Tavily 搜索
TAVILY_API_KEY=your_tavily_key_here

# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=deepagents
POSTGRES_PASSWORD=deepagents
POSTGRES_DB=deepagents

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=deepagents
```

### 3. 启动服务

```bash
# 启动数据库（Docker）
cd docker && docker compose up -d postgres redis && cd ..

# 启动后端（端口 8000）
uv run python -m app.api.server

# 启动前端（端口 5173）
cd frontend && pnpm dev
```

访问 `http://localhost:5173` 即可使用。

## 系统架构

```
用户任务
    │
    ▼
┌──────────────────────┐
│    主智能体 (Main)     │ ← 任务理解、步骤规划、子智能体调度、结果汇总
└──────────┬───────────┘
           │
           ├──► 网络搜索助手 ──► Tavily API ──► 互联网信息
           │
           ├──► 知识库助手 ───► ChromaDB ────► 企业内部文档
           │
           └──► 数据库助手 ───► PostgreSQL ──► 业务数据
```

### 生产部署架构

```
                  ┌──────────────────┐
                  │   Nginx (:80)    │
                  │  前端 + 反向代理  │
                  └────────┬─────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        /api/* │      /ws/* │      /    │
              ▼            ▼            ▼
    ┌─────────────┐  WebSocket   静态文件
    │  FastAPI    │  升级         (SPA)
    │  (:8000)    │
    └──────┬──────┘
           │
    ┌──────┼──────┐
    │      │      │
    ▼      ▼      ▼
┌──────┐ ┌──────┐ ┌──────────┐
│Postgres│Redis │ │ARQ Worker│
│(:5432) │(:6379)│ │  (x2)    │
└────────┘ └──────┘ └──────────┘
```

## 核心特性

### 智能体与 RAG

- **多智能体协作**：主智能体协调 3 个专家子智能体（网络搜索、知识库检索、数据库查询），互补完成复杂研究任务
- **自建 RAG 系统**：父子文档分块策略，向量检索（pgvector cosine）+ BM25 关键词检索，RRF 融合排序
- **记忆系统**：基于 pgvector 的语义记忆，会话结束自动 LLM 摘要 + 事实提取，跨会话复用
- **智能文件生成**：自动生成 Markdown 和 PDF 报告

### 企业级基础设施

- **RBAC 权限系统**：三级模型（角色 → 权限 → 资源:操作），4 个内置角色 + 28 个权限点 + Redis 缓存 + 前端管理 UI
- **SSO 集成**：LDAP + OIDC（Google/GitHub 等），JWT Bearer Token + bcrypt
- **用户组数据隔离**：会话、知识库、数据库内容按 group_id 隔离
- **可观测性**：Prometheus 10 个指标全链路埋点 + OpenTelemetry 分布式追踪 + structlog 结构化日志
- **多 Worker 分布式部署**：ARQ Worker 心跳上报、Redis Sentinel 支持、水平扩展

### 实时交互

- **WebSocket 实时推送**：任务进度、工具调用、AI 回复流式输出
- **SSE 事件推送**：会话列表变更、文件列表更新，替代前端轮询
- **会话分享**：生成只读分享链接，支持过期时间和访问计数
- **断线重连**：Redis 缓存最近 500 条事件，WebSocket 重连自动回放

### 会话与存储

- **PostgreSQL 持久化**：sessions / messages / agent_events / long_term_memories / roles / permissions 多表
- **会话历史管理**：历史会话查看、继续问答、状态机管理（LIVE ↔ HISTORICAL）
- **文件存储抽象**：StorageBackend ABC，支持本地存储和 MinIO/S3 对象存储
- **提示词模板**：per-group / per-user 定制系统提示词，三层优先级覆盖

## 项目结构

```
deepsearch-agents/
├── app/
│   ├── agent/                  # 智能体核心
│   │   ├── main_agent.py       # 主智能体（LangGraph + PostgresSaver）
│   │   ├── llm.py              # LLM 配置（重试策略）
│   │   ├── prompts.py          # YAML 提示词加载
│   │   └── subagents/          # 专家子智能体
│   ├── api/                    # Web API
│   │   ├── server.py           # FastAPI 入口（REST + WebSocket + SSE）
│   │   ├── monitor.py          # 实时事件监控
│   │   └── context.py          # ContextVar 会话上下文
│   ├── auth/                   # 认证与权限
│   │   ├── jwt.py              # JWT 生成/验证 + 密码哈希
│   │   ├── dependencies.py     # FastAPI 认证依赖注入
│   │   ├── permissions.py      # RBAC 权限检查 + Redis 缓存
│   │   ├── ldap_client.py      # LDAP SSO
│   │   └── oidc_client.py      # OIDC SSO
│   ├── storage/                # 存储持久化
│   │   ├── db.py               # PostgreSQL 连接池 + Schema + RBAC 种子数据
│   │   ├── redis_client.py     # Redis 客户端 + Pub/Sub + 事件缓存
│   │   ├── memory_service.py   # 记忆服务（检索/存储/巩固）
│   │   └── storage.py          # 存储抽象层（StorageBackend ABC）
│   ├── tools/                  # 工具函数
│   │   ├── tavily_tool.py      # 网络搜索
│   │   ├── db_tools.py         # 数据库查询（SQL 安全校验 + aiomysql 异步）
│   │   ├── self_rag_tools.py   # 知识库检索
│   │   ├── markdown_tools.py   # Markdown 报告生成
│   │   └── pdf_tools.py        # PDF 转换
│   ├── self_rag/               # 自建 RAG 系统
│   │   ├── engine.py           # RAG 引擎（ChromaDB + BM25）
│   │   └── config.py           # 检索参数配置
│   ├── tracing/                # OpenTelemetry 链路追踪
│   ├── config.py               # Pydantic 统一配置
│   ├── exceptions.py           # 异常层次体系
│   ├── metrics.py              # Prometheus 指标
│   └── worker.py               # ARQ 异步任务 Worker
├── frontend/                   # React 前端
│   ├── src/
│   │   ├── components/
│   │   │   ├── ChatComposer.tsx          # 任务输入 + 文件上传
│   │   │   ├── ConversationThread.tsx    # 对话线程（多轮渲染）
│   │   │   ├── SessionList.tsx           # 历史会话侧边栏
│   │   │   ├── UserManager.tsx           # RBAC 用户/角色管理 Drawer
│   │   │   ├── KnowledgeBaseManager.tsx  # 知识库管理 Drawer
│   │   │   ├── PromptTemplateManager.tsx # 提示词模板管理 Drawer
│   │   │   ├── LoginPage.tsx             # 登录/注册/SSO
│   │   │   ├── FileDock.tsx              # 输出文件面板
│   │   │   └── MemoryPanel.tsx           # 记忆面板
│   │   ├── hooks/
│   │   │   ├── useDeepAgentSession.ts    # 会话状态管理 + WebSocket
│   │   │   ├── useSSE.ts                 # SSE 事件订阅
│   │   │   └── ThemeContext.tsx           # 暗色/亮色主题
│   │   └── lib/
│   │       ├── api.ts                    # API 函数
│   │       └── auth.ts                   # 前端认证工具
│   └── Dockerfile
├── docker/
│   └── docker-compose.yaml     # 开发环境 PostgreSQL + Redis
├── Dockerfile                  # 后端 Docker 镜像
├── docker-compose.prod.yaml    # 生产环境全栈编排
└── tests/                      # 84 个测试用例
```

## API 端点概览

### 认证

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录 |
| GET | `/api/auth/me` | 当前用户信息 |

### 任务与会话

| 方法 | 端点 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/task` | `task:create` | 提交研究任务 |
| POST | `/api/task/{id}/cancel` | `task:cancel` | 取消任务（含所有权校验） |
| GET | `/api/sessions` | `session:read` | 会话列表（组隔离） |
| DELETE | `/api/sessions/{id}` | `session:delete` | 删除会话 |
| POST | `/api/sessions/{id}/share` | `share:create` | 创建分享链接 |

### 知识库与记忆

| 方法 | 端点 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/kb/create` | `kb:create` | 创建知识库 |
| GET | `/api/kb/list` | `kb:read` | 知识库列表（组隔离） |
| POST | `/api/kb/ingest` | `kb:ingest` | 摄入文档 |
| GET | `/api/memories` | `memory:read` | 记忆列表 |
| DELETE | `/api/memories/{id}` | `memory:delete` | 删除记忆 |

### RBAC 管理（仅管理员）

| 方法 | 端点 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/admin/users` | `user:read` | 用户列表 |
| PUT | `/api/admin/users/{id}/role` | `user:update` | 修改用户角色 |
| GET | `/api/admin/roles` | `role:read` | 角色列表 + 权限集 |
| POST | `/api/admin/roles` | `role:manage` | 创建自定义角色 |
| PUT | `/api/admin/roles/{name}` | `role:manage` | 修改角色权限 |

## 生产部署

```bash
# 1. 配置生产环境变量
cp .env.prod .env.prod.local
# 编辑 .env.prod.local，修改 JWT_SECRET、API 密钥、数据库密码

# 2. 启动全部服务
docker compose -f docker-compose.prod.yaml --env-file .env.prod.local up -d

# 3. 验证
curl http://localhost/ready

# 4. 水平扩展 Worker
docker compose -f docker-compose.prod.yaml up -d --scale worker=4
```

| 服务 | 端口 | 说明 |
|------|------|------|
| Nginx | 80 | 前端 SPA + 反向代理 |
| FastAPI | 8000 (内网) | 后端 API + WebSocket + SSE |
| ARQ Worker | - | 异步任务执行（可水平扩展） |
| PostgreSQL | 5432 (内网) | 持久化 + pgvector 向量检索 |
| Redis | 6379 (内网) | 缓存 + 任务队列 + Pub/Sub |

## 测试

```bash
uv run pytest tests/ -v                        # 全部 84 个测试
uv run pytest tests/ -v --cov=app --cov-report=term-missing  # 带覆盖率
uv run python -m tests.eval.run_eval           # 评测基准
```

## 许可证

本项目仅供学习和研究使用。
