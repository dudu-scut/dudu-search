# DeepAgents — 多智能体深度研究系统

基于 DeepAgents 框架构建的智能协作研究系统，通过多智能体分工协作完成复杂的信息检索、分析与报告生成任务。

## 项目定位

**DeepSearch** 是一款面向私域场景的深度研究搜索系统，专注于帮助企业和组织整合内部知识资产，实现智能化的信息检索与分析服务。

### 核心应用场景

| 场景类型         | 具体应用                   | 价值体现              |
| ------------ | ---------------------- | ----------------- |
| **企业内部制度查询** | 员工手册、政策法规、流程规范的智能问答与检索 | 快速获取准确信息，减少人工咨询成本 |
| **法律条款查询**   | 法律法规库、合同条款、合规要求的智能检索   | 精准匹配相关条款，辅助决策分析   |
| **产品售后支持**   | 技术文档、知识库、产品手册的智能问答     | 提升客服效率，标准化问题解答    |
| **行业研究报告**   | 内部调研资料、行业分析、市场研究的整合分析  | 快速生成综合报告，提升研究效率   |
| **学术资料整理**   | 论文检索、文献综述、研究资料的整理与归纳   | 结构化输出，节省整理时间      |

### 私域特性

- **数据安全**：所有数据存储在本地或私有云，信息不外泄
- **知识可控**：企业自有知识库资产，完全自主管理
- **场景定制**：可根据不同行业和业务需求灵活配置
- **精准检索**：结合向量检索与关键词匹配，确保结果准确

## 核心特性

- **私域知识整合**：支持企业内部文档库、数据库、在线资源的多源信息整合，构建统一知识门户
- **智能问答检索**：基于 RAG 技术的语义检索，快速定位相关信息，支持自然语言查询
- **多智能体协作**：主智能体协调三个专家子智能体，互补完成信息获取与报告生成
- **智能文件生成**：自动生成结构化的 Markdown 和 PDF 报告，支持自定义模板
- **实时交互体验**：WebSocket 实时推送任务进度和结果，支持流式输出
- **会话隔离管理**：每个任务独立工作目录，数据安全隔离，保护隐私信息
- **灵活的部署方式**：支持本地部署、Docker 容器化、私有云部署，满足不同安全要求

## 系统架构

```
用户任务
    │
    ▼
┌─────────────────────┐
│     主智能体         │ ← 任务理解、步骤规划、子智能体调度、结果汇总
│   (Main Agent)      │
└─────────┬───────────┘
          │
          ├──► 网络搜索助手 ──► Tavily API ──► 互联网信息
          │
          ├──► 知识库助手 ───► ChromaDB ────► 企业内部文档
          │
          └──► 数据库助手 ───► MySQL ──────► 业务数据
```

## 技术栈

| 类别    | 技术                                               |
| ----- | ------------------------------------------------ |
| 智能体框架 | DeepAgents 0.5.7 + LangChain 1.2 + LangGraph 1.1 |
| 大语言模型 | DeepSeek (deepseek-chat)                         |
| 后端框架  | FastAPI + Uvicorn                                |
| 数据库   | MySQL 8.4                                        |
| 知识库   | ChromaDB + sentence-transformers                 |
| 前端    | React + TypeScript + Vite                        |

## 快速开始

### 环境要求

- Python 3.12
- Node.js 18+
- MySQL 8.4 (或 Docker)
- API 密钥：DeepSeek API、Tavily API

### 1. 克隆与安装

```bash
cd deepsearch-agents

# 安装后端依赖
uv sync

# 安装前端依赖
cd frontend
pnpm install
cd ..
```

### 2. 环境配置

复制环境变量配置文件：

```bash
cp .env.example .env
```

编辑 `.env` 文件，配置以下关键变量：

```env
# DeepSeek API 配置
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=your_api_key_here
LLM_DEEPSEEK_MODEL=deepseek-chat

# Tavily 搜索 API
TAVILY_API_KEY=your_tavily_key_here

# MySQL 数据库配置
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_mysql_password
MYSQL_DATABASE=deepsearch_db
```

### 3. 启动 MySQL 数据库

使用 Docker 启动 MySQL：

```bash
cd docker
docker-compose up -d
```

### 4. 启动服务

**后端服务**（端口 8000）：

```bash
uv run python -m app.api.server
```

**前端服务**（开发模式）：

```bash
cd frontend
pnpm dev
```

访问 `http://localhost:5173` 即可使用系统。

## 私域部署方案

### 典型部署架构

#### 1. 企业内部私有部署

适用于对数据安全性要求高的金融机构、政府部门、大型企业。

```
企业内部网络
    │
    ├── DeepSearch 后端服务（内网服务器）
    ├── MySQL 数据库（本地部署）
    ├── ChromaDB 知识库（内网存储）
    └── 前端 Web 服务（内网访问）
```

特点：

- 完全离线运行，数据不出内网
- 可对接现有 LDAP/SSO 认证系统
- 支持高并发内部访问
- 可根据企业需求定制功能

#### 2. SaaS 私有化部署

适用于需要云端管理但要求数据隔离的中小企业。

```
云端私有实例
    │
    ├── 独立容器集群（数据隔离）
    ├── 专属数据库实例
    ├── 独立知识库存储
    └── 企业专属域名访问
```

特点：

- 无需维护硬件设施
- 享受云端便利性的同时保证数据隔离
- 弹性扩展，按需付费
- 专业技术支持

#### 3. 混合部署模式

适用于需要在多个地点协作的组织。

```
总部数据中心（核心知识库）
    │
    ├── 分支机构（本地缓存加速）
    ├── 移动办公（VPN 安全接入）
    └── 合作伙伴（受限访问接口）
```

特点：

- 核心数据集中在总部
- 常用数据分发到分支加速访问
- 灵活的多方协作机制
- 精细的权限控制

### 知识库建设建议

1. **文档整理**：优先整理结构化的政策文档、操作手册
2. **历史数据**：导入历史查询记录，形成知识积累
3. **持续更新**：建立知识库更新机制，保持内容时效性
4. **权限分级**：根据文档敏感度设置不同的访问权限

## 使用示例

### 通过前端界面使用

1. 在任务输入框中描述您的研究需求
2. 系统自动调度三个专家助手收集信息
3. 实时查看信息收集进度
4. 主智能体汇总结果生成报告
5. 下载 Markdown 或 PDF 格式的报告

### 私域场景示例任务

#### 企业内部制度查询

```
请帮我查询公司关于年假制度的最新规定，并整理成一份简明的员工指南。
```

```
查询公司信息安全政策中关于数据备份的具体要求。
```

#### 法律合规查询

```
检索合同法中关于违约金条款的相关规定，并结合我们现有合同模板给出修改建议。
```

```
查询 GDPR 或个人信息保护法中关于用户数据收集的合规要求。
```

#### 产品技术支持

```
请从产品知识库中查找XXX产品的常见故障排查指南，并整理成技术支持手册。
```

```
查询产品手册中关于设备维护周期的建议，生成一份维护计划建议书。
```

#### 综合研究报告

```
结合行业分析报告库和最新市场数据，生成一份2026年行业发展趋势分析报告。
```

```
请先读取我上传的会议记录，再结合公司知识库整理出一份行动项清单。
```

## 项目结构

```
deepsearch-agents/
├── app/
│   ├── agent/                 # 智能体核心模块
│   │   ├── main_agent.py      # 主智能体实现
│   │   ├── llm.py            # LLM 配置
│   │   └── subagents/        # 专家子智能体
│   │       ├── network_search_agent.py
│   │       ├── database_query_agent.py
│   │       └── knowledge_base_agent.py
│   ├── api/                   # Web API
│   │   ├── server.py         # FastAPI 服务入口
│   │   ├── monitor.py       # 实时事件监控
│   │   └── context.py       # 会话上下文管理
│   ├── tools/                # 工具函数
│   │   ├── tavily_tool.py   # 网络搜索工具
│   │   ├── db_tools.py      # 数据库工具
│   │   ├── self_rag_tools.py # 知识库工具
│   │   ├── markdown_tools.py # Markdown 生成
│   │   └── pdf_tools.py     # PDF 转换
│   ├── self_rag/             # 自建 RAG 系统
│   │   ├── engine.py        # RAG 引擎
│   │   └── config.py        # 配置管理
│   └── prompt/               # 提示词配置
│       └── prompts.yml       # 集中式提示词管理
├── frontend/                  # React 前端
├── docker/                    # Docker 部署配置
│   └── mysql/
│       └── mysql.sql         # 数据库初始化脚本
└── examples/                  # 示例代码
    └── skills/               # 技能扩展示例
```

## 核心模块说明

### 主智能体 (Main Agent)

主智能体是系统的核心协调者，负责：

- 理解用户任务意图
- 规划任务执行步骤
- 调度合适的子智能体
- 汇总子智能体返回的信息
- 生成最终交付文档

### 专家子智能体

| 子智能体   | 职责      | 工具           |
| ------ | ------- | ------------ |
| 网络搜索助手 | 互联网信息检索 | Tavily API   |
| 知识库助手  | 企业文档检索  | ChromaDB RAG |
| 数据库助手  | 业务数据查询  | MySQL        |

### 自建 RAG 系统

采用父子文档分块策略，结合向量检索与 BM25 关键词检索：

```
用户问题
    ├─► 向量检索 (ChromaDB) ──► 语义相关结果
    └─► BM25 检索 ──────────► 关键词命中结果
            │
            ▼
        RRF 融合
            │
            ▼
    选取 Top 结果，召回父文档作为上下文
```

### 工具函数

所有工具函数通过统一接口注册到智能体：

- `internet_search` - 网络搜索
- `list_sql_tables` / `get_table_data` / `execute_sql_query` - 数据库操作
- `list_knowledge_bases` / `query_knowledge_base` - 知识库检索
- `generate_markdown` - Markdown 生成
- `convert_md_to_pdf` - PDF 转换

## 提示词管理

系统采用集中式提示词管理，所有智能体的提示词定义在 `app/prompt/prompts.yml`：

```yaml
main_agent:
  system_prompt: |
    当前日期: {current_date}。
    你是研究团队负责人，负责协调三个专家助手完成复杂任务

sub_agents:
  network_search:
    description: 网络搜索助手，负责互联网信息检索
    system_prompt: ...
```

修改智能体行为只需编辑 YAML 文件，无需改动代码。

## 数据库配置

### 表结构

系统使用 MySQL 存储业务数据。首次启动时自动执行 `docker/mysql/mysql.sql` 初始化表结构。

### 安全措施

- 只允许 SELECT/SHOW/DESCRIBE/EXPLAIN 查询语句
- 表名使用正则白名单校验
- 返回行数上限 1000 行

## 开发指南

### 常用开发命令

```bash
# 运行后端服务
uv run python -m app.api.server

# 运行主智能体（脚本调试）
uv run python -m app.agent.main_agent

# 测试工具函数
uv run python -m app.tools.db_tools
uv run python -m app.tools.markdown_tools
uv run python -m app.tools.pdf_tools
uv run python -m app.tools.tavily_tool
```

### 添加新工具

1. 在 `app/tools/` 目录创建工具文件
2. 使用 `@tool` 装饰器定义工具函数
3. 在 `app/agent/main_agent.py` 中注册工具
4. 在 `app/prompt/prompts.yml` 中添加工具描述

### 扩展子智能体

1. 在 `app/agent/subagents/` 创建新子智能体
2. 定义工具集和系统提示词
3. 在 `app/agent/main_agent.py` 中注册子智能体
4. 更新 `app/prompt/prompts.yml` 配置

## 环境变量参考

| 变量                         | 说明            | 默认值                        |
| -------------------------- | ------------- | -------------------------- |
| `OPENAI_BASE_URL`          | LLM API 地址    | <https://api.deepseek.com> |
| `OPENAI_API_KEY`           | LLM API 密钥    | -                          |
| `LLM_DEEPSEEK_MODEL`       | 模型名称          | deepseek-chat              |
| `TAVILY_API_KEY`           | Tavily API 密钥 | -                          |
| `MYSQL_HOST`               | MySQL 主机      | localhost                  |
| `MYSQL_PORT`               | MySQL 端口      | 3306                       |
| `MYSQL_USER`               | MySQL 用户      | root                       |
| `MYSQL_PASSWORD`           | MySQL 密码      | -                          |
| `MYSQL_DATABASE`           | 数据库名          | deepsearch\_db             |
| `SELF_RAG_EMBEDDING_MODEL` | 嵌入模型          | BAAI/bge-small-zh-v1.5     |
| `SELF_RAG_TOP_K`           | 检索返回数         | 4                          |
| `SELF_RAG_BM25_ENABLED`    | 启用 BM25       | true                       |

## 注意事项

- 使用 `uv` 管理 Python 依赖，不要直接使用 `pip`
- API 密钥等敏感信息不要提交到版本控制
- Windows 环境下路径使用 `/` 分隔符
- 前端依赖安装在 `frontend/node_modules`，请勿提交到仓库

## 许可证

本项目仅供学习和研究使用。

## 安全与隐私

### 数据安全措施

- **传输加密**：所有 API 通信使用 HTTPS/TLS 加密
- **存储加密**：敏感数据支持静态加密存储
- **访问控制**：基于角色的权限控制系统（RBAC）
- **审计日志**：完整的操作日志记录，支持审计追踪
- **会话隔离**：每个用户/任务的会话完全隔离，防止数据泄露

### 隐私保护

- **数据本地化**：所有数据存储在用户指定位置
- **无数据回传**：系统不会将用户数据用于模型训练或其他用途
- **最小权限**：默认配置遵循最小权限原则
- **合规支持**：符合 GDPR、个人信息保护法等法规要求

### 企业级安全特性

- 支持 SSO/LDAP 集成
- 提供 API Key 和 Token 认证
- 支持 IP 白名单访问控制
- 提供数据备份与恢复机制
- 支持安全漏洞扫描和修复

