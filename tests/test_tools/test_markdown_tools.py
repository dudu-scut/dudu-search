"""Markdown 工具测试。

generate_markdown 是 @tool 装饰的 LangChain 工具，通过 ContextVar 获取
会话目录，调用 resolve_path 解析路径，最终将内容写入文件。
"""

import os
import tempfile
from unittest.mock import patch

import pytest


class TestGenerateMarkdown:
    """测试 generate_markdown 工具。"""

    def test_generate_creates_file(self):
        """应该成功创建 Markdown 文件。"""
        from app.api.context import set_session_context, reset_session_context
        from app.tools.markdown_tools import generate_markdown

        with tempfile.TemporaryDirectory() as tmpdir:
            # 设置会话目录 ContextVar
            token = set_session_context(tmpdir)

            try:
                result = generate_markdown.invoke({
                    "content": "# Test\n\nHello World",
                    "filename": "test_output.md",
                    "path": "",
                })

                file_path = os.path.join(tmpdir, "test_output.md")
                assert os.path.exists(file_path), f"文件应存在：{file_path}"
                assert "test_output.md" in result

                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    assert "# Test" in content
                    assert "Hello World" in content
            finally:
                reset_session_context(token)

    def test_generate_auto_appends_md_extension(self):
        """不包含 .md 后缀的文件名应自动补全。"""
        from app.api.context import set_session_context, reset_session_context
        from app.tools.markdown_tools import generate_markdown

        with tempfile.TemporaryDirectory() as tmpdir:
            token = set_session_context(tmpdir)

            try:
                result = generate_markdown.invoke({
                    "content": "# No extension",
                    "filename": "no_ext",
                    "path": "",
                })

                # 文件名应被自动补充 .md
                assert "no_ext.md" in result
                file_path = os.path.join(tmpdir, "no_ext.md")
                assert os.path.exists(file_path)
            finally:
                reset_session_context(token)

    def test_generate_empty_content(self):
        """空内容也应该创建文件。"""
        from app.api.context import set_session_context, reset_session_context
        from app.tools.markdown_tools import generate_markdown

        with tempfile.TemporaryDirectory() as tmpdir:
            token = set_session_context(tmpdir)

            try:
                result = generate_markdown.invoke({
                    "content": "",
                    "filename": "empty.md",
                    "path": "",
                })

                file_path = os.path.join(tmpdir, "empty.md")
                assert os.path.exists(file_path)

                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    assert content == ""
            finally:
                reset_session_context(token)

    def test_generate_in_subdirectory(self):
        """指定子目录时应自动创建目录并写入文件。"""
        from app.api.context import set_session_context, reset_session_context
        from app.tools.markdown_tools import generate_markdown

        with tempfile.TemporaryDirectory() as tmpdir:
            token = set_session_context(tmpdir)

            try:
                result = generate_markdown.invoke({
                    "content": "# Subdir test",
                    "filename": "report.md",
                    "path": "sub_dir",
                })

                file_path = os.path.join(tmpdir, "sub_dir", "report.md")
                assert os.path.exists(file_path), f"子目录文件应存在：{file_path}"
                assert "report.md" in result
            finally:
                reset_session_context(token)

    def test_generate_chinese_filename(self):
        """中文文件名应正常处理。"""
        from app.api.context import set_session_context, reset_session_context
        from app.tools.markdown_tools import generate_markdown

        with tempfile.TemporaryDirectory() as tmpdir:
            token = set_session_context(tmpdir)

            try:
                result = generate_markdown.invoke({
                    "content": "# 中文测试",
                    "filename": "测试报告.md",
                    "path": "",
                })

                file_path = os.path.join(tmpdir, "测试报告.md")
                assert os.path.exists(file_path), f"中文文件应存在：{file_path}"
            finally:
                reset_session_context(token)

    def test_generate_large_content(self):
        """较大内容应正常写入。"""
        from app.api.context import set_session_context, reset_session_context
        from app.tools.markdown_tools import generate_markdown

        large_content = "# Large File\n\n" + ("Line content\n" * 1000)

        with tempfile.TemporaryDirectory() as tmpdir:
            token = set_session_context(tmpdir)

            try:
                result = generate_markdown.invoke({
                    "content": large_content,
                    "filename": "large.md",
                    "path": "",
                })

                file_path = os.path.join(tmpdir, "large.md")
                assert os.path.exists(file_path)

                with open(file_path, "r", encoding="utf-8") as f:
                    written = f.read()
                    assert written == large_content
            finally:
                reset_session_context(token)

    def test_filename_without_session_context(self):
        """未设置会话目录时，应回退到当前工作目录。"""
        from app.tools.markdown_tools import generate_markdown

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("os.getcwd", return_value=tmpdir):
                result = generate_markdown.invoke({
                    "content": "# CWD fallback",
                    "filename": "fallback.md",
                    "path": "",
                })

                file_path = os.path.join(tmpdir, "fallback.md")
                assert os.path.exists(file_path), (
                    f"回退文件应存在：{file_path}"
                )
