"""
MySQL 数据库查询工具模块

封装数据库查询助手使用的三个 LangChain 工具：
list_sql_tables 用于发现真实表名，get_table_data 用于预览字段和样例数据，
execute_sql_query 用于在确认结构后执行自定义查询。
"""

import re

from langchain_core.tools import tool
from mysql.connector import Error, connect

from app.api.monitor import monitor
from app.config import settings
from app.metrics import SQL_QUERY_DURATION, SQL_QUERY_TOTAL


# 集中读取数据库配置，后续三个工具都复用这份连接参数
def get_db_config():
    """
    从环境变量读取 MySQL 连接配置

    所有数据库工具都通过此函数拿到同一份连接参数，避免每个工具重复读取环境变量
    :return: mysql.connector.connect 可直接使用的连接参数
    """
    config = {
        "host": settings.MYSQL_HOST,
        "port": settings.MYSQL_PORT,
        "user": settings.MYSQL_USER,
        "password": settings.MYSQL_PASSWORD,
        "database": settings.MYSQL_DATABASE,
        "charset": settings.MYSQL_CHARSET,
        "collation": settings.MYSQL_COLLATION,
        "sql_mode": settings.MYSQL_SQL_MODE,
        "autocommit": True,
    }

    # 去掉未配置的可选项，避免把 None 传给 mysql.connector 造成连接参数异常
    config = {k: v for k, v in config.items() if v is not None}

    # user/password/database 是本教程工具能正常查询业务库的最小必要配置
    required_keys = ["user", "password", "database"]
    missing_keys = [k for k in required_keys if k not in config]
    if missing_keys:
        raise ValueError(f"缺失数据库核心配置：{', '.join(missing_keys)}")

    return config


@tool
def list_sql_tables() -> str:
    """
    查询当前数据库中所有可用表

    作用：让模型先识别真实可用的表名，方便后续预览表结构和编写自定义 SQL。
    :return: 有表：可用的表有：表1,表2,表3...
             没有表：没有可用的表
             出现异常：查询出现异常：异常信息
    """

    # 埋点：工具一被调用，前端可以展示当前正在查询数据库表名
    monitor.report_tool(tool_name="数据库表名查询工具：list_sql_tables", args={})

    # 加载数据库连接信息
    config = get_db_config()

    # MySQL 查询的固定步骤：
    # 1. 创建连接
    # 2. 创建 cursor
    # 3. 执行 SQL
    # 4. 获取返回结果
    # 5. 释放连接和 cursor 资源
    # 这里捕获异常并返回中文提示，避免工具报错直接中断 Agent 执行链路
    try:
        # 使用 with 管理连接和游标，查询结束后自动释放数据库资源
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                sql = "SHOW TABLES"
                cursor.execute(sql)

                # SHOW TABLES 返回形如：[("drugs",), ("inventory",), ("sales_records",)]
                tables = cursor.fetchall()
                if not tables:
                    return "没有可用的表"

                # 取每个元组的第一个元素，拼成模型容易阅读的表名列表
                table_names = [table[0] for table in tables]
                return f"可用的表有：{', '.join(table_names)}"
    except Error as e:
        return f"查询出现异常：{str(e)}"


@tool
def get_table_data(table_name) -> str:
    """
    查询指定表的前 100 行数据

    当前工具调用之前，应先调用 list_sql_tables 完成表名校验。
    此工具的作用：
    1. 完成单表样例数据查询
    2. 为多表查询提供表结构信息和数据格式参考
    :param table_name: 表名
    :return: CSV 格式数据
             1. 第一行是列信息，列之间使用英文逗号分隔
             2. 第二行开始是表数据，值之间也使用英文逗号分隔
             3. 行和行之间使用 \n 分隔
             4. 至多查询 100 条表数据
             例如：
                id,name,age\n -> 列头
                1,张三,18\n
                1,张三,18\n
                1,张三,18\n -> 至多查询 100 条
    """
    # 埋点：工具二被调用，前端可以展示当前正在预览哪张表
    monitor.report_tool(
        tool_name="数据库表数据查询工具：get_table_data",
        args={"table_name": table_name},
    )

    # 获取数据库参数
    config = get_db_config()

    # 查询流程同样是：连接 -> cursor -> 执行 SQL -> 获取列信息和数据 -> 自动释放资源
    try:
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                # 白名单校验：只允许字母、数字、下划线，防止 SQL 注入
                if not re.fullmatch(r'\w+', table_name):
                    return f"非法的表名：{table_name}"
                sql = f"SELECT * FROM {table_name} LIMIT 100"
                cursor.execute(sql)

                # cursor.description 保存查询结果的列元信息
                # 例如：[("id", ...), ("name", ...), ("age", ...)]
                # 如果 SQL 没有结果集，description 可能为 None
                description = cursor.description
                if not description:
                    return f"数据表 {table_name} 暂无数据。"

                # 只取每个列信息元组的第一个元素，也就是列名
                # 例如：["id", "name", "age"]
                columns = [desc[0] for desc in description]

                # fetchall 返回表数据，形如：[(1, "张三", 18), (2, "李四", 20)]
                rows = cursor.fetchall()

                # 把每一行数据从元组转成 CSV 行文本
                # NULL 值转换为空字符串，避免 "None" 字符串干扰 LLM 分析
                results = [",".join("" if v is None else str(v) for v in row) for row in rows]

                # columns 组成 CSV 头部，rows 组成 CSV 数据体
                # 最终返回：
                # id,name,age
                # 1,张三,18
                header_str = ",".join(columns)
                data_str = "\n".join(results)
                return f"{header_str}\n{data_str}"
    except Error as e:
        return f"查询出现异常：{str(e)}"


# ── SQL 安全校验 ──

# 严格的正则前缀检查（防注释/空白绕过）
SAFE_SQL_PATTERN = re.compile(
    r'^\s*(SELECT|SHOW|DESCRIBE|EXPLAIN|WITH)\b',
    re.IGNORECASE,
)

MAX_SQL_ROWS = 1000
MAX_SQL_TIMEOUT_SECONDS = 30

# 危险关键字（写操作、DDL、权限变更）
DANGEROUS_KEYWORDS = [
    r'\bINSERT\b', r'\bUPDATE\b', r'\bDELETE\b', r'\bDROP\b',
    r'\bALTER\b', r'\bCREATE\b', r'\bTRUNCATE\b', r'\bGRANT\b',
    r'\bREVOKE\b', r'\bEXEC\b', r'\bEXECUTE\b', r'\bREPLACE\b',
    r'\bLOAD\b', r'\bIMPORT\b', r'\bRENAME\b',
]


def validate_sql_query(query: str) -> tuple:
    """校验 SQL 查询安全性。

    规则：
    1. 必须以 SELECT/SHOW/DESCRIBE/EXPLAIN/WITH 开头
    2. 不能包含危险关键字（写操作、DDL 等）
    3. 自动追加 LIMIT（如果没有）

    :return: (safe_query, error_message) — error_message 为空表示通过
    """
    query_stripped = query.strip()

    # 1. 前缀检查
    if not SAFE_SQL_PATTERN.match(query_stripped):
        return "", "仅允许 SELECT/SHOW/DESCRIBE/EXPLAIN/WITH 查询"

    # 2. 危险关键字检查
    for pattern in DANGEROUS_KEYWORDS:
        if re.search(pattern, query_stripped, re.IGNORECASE):
            keyword = pattern.replace(r'\b', '')
            return "", f"查询包含禁止的关键字: {keyword}"

    # 3. 自动追加 LIMIT（如果没有）
    if not re.search(r'\bLIMIT\b', query_stripped, re.IGNORECASE):
        query_stripped = f"{query_stripped.rstrip(';')} LIMIT {MAX_SQL_ROWS}"

    return query_stripped, ""


@tool
def execute_sql_query(query) -> str:
    """
    执行自定义 SQL 查询

    切记：执行之前，需要通过 list_sql_tables 明确真实表名，
    再通过 get_table_data 明确表结构和数据格式。
    适合多表关联、筛选、聚合、排序等复杂查询。
    :param query: 要执行的自定义 SQL 语句
    :return: CSV 格式数据
             1. 第一行是列信息，列之间使用英文逗号分隔
             2. 第二行开始是表数据，值之间也使用英文逗号分隔
             3. 行和行之间使用 \\n 分隔
             例如：
                id,name,age\\n -> 列头
                1,张三,18\\n
                1,张三,18\\n
    """
    # 埋点：记录模型最终生成的 SQL，便于教学时观察是否真的落到了正确表字段上
    monitor.report_tool(
        tool_name="数据库表数据查询工具：execute_sql_query", args={"query": query}
    )

    # 安全校验
    safe_query, error = validate_sql_query(query)
    if error:
        return f"SQL 安全校验失败: {error}"

    # 获取数据库参数
    config = get_db_config()

    SQL_QUERY_TOTAL.labels(table="custom").inc()
    try:
        with SQL_QUERY_DURATION.labels(table="custom").time():
            with connect(**config) as conn:
                with conn.cursor() as cursor:
                    # 执行校验后的安全查询
                    cursor.execute(safe_query)

                    description = cursor.description
                    if not description:
                        return f"执行自定义 SQL 语句没有查询结果，SQL 为：{query}"

                    columns = [desc[0] for desc in description]

                    # 限制返回行数
                    rows = cursor.fetchmany(MAX_SQL_ROWS)

                    if len(rows) == MAX_SQL_ROWS:
                        # 检查是否还有更多数据
                        has_more = cursor.fetchone() is not None
                        truncation_note = (
                            "\n\n> ⚠️ 结果已截断，仅显示前 1000 行。请添加更精确的 WHERE 条件缩小范围。"
                            if has_more
                            else ""
                        )
                    else:
                        truncation_note = ""

                    results = [",".join("" if v is None else str(v) for v in row) for row in rows]

                    header_str = ",".join(columns)
                    data_str = "\n".join(results)
                    return f"{header_str}\n{data_str}{truncation_note}"
    except Error as e:
        return f"查询出现异常：{str(e)}"


if __name__ == "__main__":
    # 本地调试入口：直接运行本文件可验证 .env 中的 MySQL 连接配置是否可用
    print(
        execute_sql_query.invoke(
            {
                "query": "SELECT * FROM `drugs` dgs join sales_records srd on dgs.drug_id = srd.drug_id"
            }
        )
    )