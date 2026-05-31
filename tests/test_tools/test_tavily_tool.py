"""网络搜索工具测试。

internet_search 是 @tool 装饰的 LangChain 工具，底层调用 TavilyClient。
测试通过 mock tavily_client 模块级实例来隔离外部 API 调用。
"""

import pytest
from unittest.mock import MagicMock, patch


class TestInternetSearch:
    """测试 internet_search 工具。"""

    @patch("app.tools.tavily_tool.tavily_client")
    def test_search_returns_results(self, mock_client):
        """正常情况：返回搜索结果字典。"""
        mock_client.search.return_value = {
            "results": [
                {
                    "title": "Test Result",
                    "url": "https://example.com",
                    "content": "Test content",
                },
            ]
        }

        from app.tools.tavily_tool import internet_search

        result = internet_search.invoke({"query": "test query"})

        # Tavily 返回的原始字典作为结果回传
        assert isinstance(result, dict)
        assert "results" in result
        assert len(result["results"]) > 0
        assert result["results"][0]["title"] == "Test Result"
        assert result["results"][0]["url"] == "https://example.com"

    @patch("app.tools.tavily_tool.tavily_client")
    def test_search_empty_results(self, mock_client):
        """空结果应返回空列表。"""
        mock_client.search.return_value = {"results": []}

        from app.tools.tavily_tool import internet_search

        result = internet_search.invoke({"query": "no results query"})
        assert isinstance(result, dict)
        assert "results" in result
        assert result["results"] == []

    @patch("app.tools.tavily_tool.tavily_client")
    def test_search_handles_api_error(self, mock_client):
        """API 错误时应抛出异常（工具不吞异常）。"""
        mock_client.search.side_effect = Exception("API rate limit exceeded")

        from app.tools.tavily_tool import internet_search

        # internet_search 不捕获 Tavily 异常，调用方需自行处理
        with pytest.raises(Exception, match="API rate limit exceeded"):
            internet_search.invoke({"query": "error query"})

    @patch("app.tools.tavily_tool.tavily_client")
    def test_search_passes_topic(self, mock_client):
        """topic 参数应正确传递给 TavilyClient。"""
        mock_client.search.return_value = {"results": []}

        from app.tools.tavily_tool import internet_search

        internet_search.invoke({"query": "latest news", "topic": "news"})
        mock_client.search.assert_called_once()
        call_kwargs = mock_client.search.call_args.kwargs
        assert call_kwargs["topic"] == "news"
        assert call_kwargs["query"] == "latest news"

    @patch("app.tools.tavily_tool.tavily_client")
    def test_search_passes_max_results(self, mock_client):
        """max_results 参数应正确传递给 TavilyClient。"""
        mock_client.search.return_value = {"results": []}

        from app.tools.tavily_tool import internet_search

        internet_search.invoke({"query": "test", "max_results": 3})
        mock_client.search.assert_called_once()
        call_kwargs = mock_client.search.call_args.kwargs
        assert call_kwargs["max_results"] == 3

    @patch("app.tools.tavily_tool.tavily_client")
    def test_search_default_max_results(self, mock_client):
        """未指定 max_results 时应使用默认值 5。"""
        mock_client.search.return_value = {"results": []}

        from app.tools.tavily_tool import internet_search

        internet_search.invoke({"query": "test"})
        call_kwargs = mock_client.search.call_args.kwargs
        assert call_kwargs["max_results"] == 5

    @patch("app.tools.tavily_tool.tavily_client")
    def test_search_include_raw_content(self, mock_client):
        """include_raw_content 参数应正确传递。"""
        mock_client.search.return_value = {"results": []}

        from app.tools.tavily_tool import internet_search

        internet_search.invoke(
            {"query": "test", "include_raw_content": True}
        )
        call_kwargs = mock_client.search.call_args.kwargs
        assert call_kwargs["include_raw_content"] is True
