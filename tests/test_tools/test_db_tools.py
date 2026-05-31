"""数据库工具测试 — SQL 安全检查。

validate_sql_query 返回 (safe_query, error_message) 元组：
- 通过时 error_message 为空字符串
- 拒绝时 safe_query 为空字符串，error_message 包含错误原因
"""

import pytest

from app.tools.db_tools import validate_sql_query


class TestValidateSqlQuery:
    """测试 SQL 查询验证函数 validate_sql_query。"""

    # ── 允许的查询类型 ──

    def test_select_allowed(self):
        """SELECT 查询应通过校验。"""
        safe_query, error = validate_sql_query("SELECT * FROM users")
        assert error == ""
        assert "SELECT" in safe_query.upper()

    def test_show_allowed(self):
        """SHOW 查询应通过校验。"""
        safe_query, error = validate_sql_query("SHOW TABLES")
        assert error == ""
        assert "SHOW" in safe_query.upper()

    def test_describe_allowed(self):
        """DESCRIBE 查询应通过校验。"""
        safe_query, error = validate_sql_query("DESCRIBE users")
        assert error == ""
        assert "DESCRIBE" in safe_query.upper()

    def test_explain_allowed(self):
        """EXPLAIN 查询应通过校验。"""
        safe_query, error = validate_sql_query("EXPLAIN SELECT * FROM users")
        assert error == ""
        assert "EXPLAIN" in safe_query.upper()

    def test_with_allowed(self):
        """WITH (CTE) 查询应通过校验。"""
        safe_query, error = validate_sql_query("WITH cte AS (SELECT 1) SELECT * FROM cte")
        assert error == ""
        assert "WITH" in safe_query.upper()

    # ── 拒绝的查询类型 ──

    def test_drop_rejected(self):
        """DROP TABLE 应被拒绝。"""
        safe_query, error = validate_sql_query("DROP TABLE users")
        assert error != ""
        assert "仅允许" in error

    def test_insert_rejected(self):
        """INSERT 应被拒绝。"""
        safe_query, error = validate_sql_query("INSERT INTO users VALUES (1)")
        assert error != ""
        assert "仅允许" in error

    def test_update_rejected(self):
        """UPDATE 应被拒绝。"""
        safe_query, error = validate_sql_query("UPDATE users SET name='x'")
        assert error != ""
        assert "仅允许" in error

    def test_delete_rejected(self):
        """DELETE 应被拒绝。"""
        safe_query, error = validate_sql_query("DELETE FROM users")
        assert error != ""
        assert "仅允许" in error

    def test_alter_rejected(self):
        """ALTER TABLE 应被拒绝。"""
        safe_query, error = validate_sql_query("ALTER TABLE users ADD COLUMN x INT")
        assert error != ""
        assert "仅允许" in error

    # ── 注入与绕过攻击 ──

    def test_comment_bypass_blocked(self):
        """注释绕过攻击应被拦截（-- SELECT 后接 DROP）。"""
        safe_query, error = validate_sql_query("-- SELECT\nDROP TABLE users")
        # SQL 注释行导致 SAFE_SQL_PATTERN 不匹配
        assert error != ""
        assert "仅允许" in error

    def test_dangerous_keyword_in_subquery(self):
        """子查询中包含危险关键字（DROP）应被拦截。"""
        safe_query, error = validate_sql_query("SELECT * FROM (DROP TABLE users)")
        # DROP 在 DANGEROUS_KEYWORDS 列表中，会触发拦截
        assert error != ""
        assert "禁止" in error

    def test_dangerous_keyword_in_comment(self):
        """注释中的危险关键字也应被拦截（保守策略）。"""
        safe_query, error = validate_sql_query("SELECT * FROM users -- DROP TABLE users")
        # 正则匹配整个字符串，注释中的 DROP 仍命中
        assert error != ""
        assert "禁止" in error

    # ── LIMIT 自动追加 ──

    def test_limit_auto_append(self):
        """无 LIMIT 的 SELECT 应自动追加 LIMIT 1000。"""
        safe_query, error = validate_sql_query("SELECT * FROM huge_table")
        assert error == ""
        assert "LIMIT 1000" in safe_query

    def test_with_limit_passes(self):
        """已含 LIMIT 的 SELECT 不应重复追加。"""
        safe_query, error = validate_sql_query("SELECT * FROM users LIMIT 10")
        assert error == ""
        assert safe_query == "SELECT * FROM users LIMIT 10"

    def test_limit_appended_to_non_select_query(self):
        """非 SELECT 语句（如 SHOW）即使无 LIMIT 也会自动追加。"""
        safe_query, error = validate_sql_query("SHOW TABLES")
        assert error == ""
        assert "LIMIT 1000" in safe_query

    # ── 边界情况 ──

    def test_empty_query(self):
        """空查询应被拒绝。"""
        safe_query, error = validate_sql_query("")
        assert error != ""
        assert "仅允许" in error

    def test_whitespace_only(self):
        """纯空白查询应被拒绝。"""
        safe_query, error = validate_sql_query("   \n\t  ")
        assert error != ""
        assert "仅允许" in error

    def test_select_with_semicolon_trimmed(self):
        """SELECT 末尾的分号应在追加 LIMIT 前被去除。"""
        safe_query, error = validate_sql_query("SELECT * FROM users;")
        assert error == ""
        assert "LIMIT 1000" in safe_query
        assert not safe_query.endswith("; LIMIT 1000")

    def test_case_insensitive_prefix(self):
        """select / show 等小写前缀也应通过校验。"""
        safe_query, error = validate_sql_query("select * from users")
        assert error == ""

        safe_query2, error2 = validate_sql_query("show tables")
        assert error2 == ""

        safe_query3, error3 = validate_sql_query("describe users")
        assert error3 == ""
