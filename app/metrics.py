"""Prometheus 指标定义。"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest, REGISTRY

# ── 任务指标 ──

TASK_TOTAL = Counter(
    "deepagents_task_total",
    "任务总数",
    ["status"],  # started, completed, failed, cancelled
)

TASK_DURATION = Histogram(
    "deepagents_task_duration_seconds",
    "任务执行耗时（秒）",
    buckets=[10, 30, 60, 120, 300, 600],
)

# ── 工具调用指标 ──

TOOL_CALL_TOTAL = Counter(
    "deepagents_tool_call_total",
    "工具调用次数",
    ["tool_name"],
)

TOOL_CALL_DURATION = Histogram(
    "deepagents_tool_call_duration_seconds",
    "工具调用耗时（秒）",
    ["tool_name"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
)

# ── LLM 调用指标 ──

LLM_CALL_TOTAL = Counter(
    "deepagents_llm_call_total",
    "LLM 调用次数",
    ["status"],  # success, error
)

LLM_CALL_DURATION = Histogram(
    "deepagents_llm_call_duration_seconds",
    "LLM 调用耗时（秒）",
    buckets=[0.5, 1, 2, 5, 10, 30, 60],
)

# ── SQL 查询指标 ──

SQL_QUERY_TOTAL = Counter(
    "deepagents_sql_query_total",
    "SQL 查询次数",
    ["table"],
)

SQL_QUERY_DURATION = Histogram(
    "deepagents_sql_query_duration_seconds",
    "SQL 查询耗时（秒）",
    ["table"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 5, 10],
)

# ── HTTP 请求指标 ──

HTTP_REQUEST_TOTAL = Counter(
    "deepagents_http_request_total",
    "HTTP 请求总数",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "deepagents_http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 2, 5],
)

# ── 活跃任务 ──

ACTIVE_TASKS = Gauge(
    "deepagents_active_tasks",
    "当前活跃任务数",
)


def get_metrics() -> bytes:
    """生成 Prometheus 格式的指标数据。"""
    return generate_latest(REGISTRY)
