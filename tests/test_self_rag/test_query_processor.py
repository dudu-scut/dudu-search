"""Unit tests for QueryProcessor — keyword expansion, decomposition, HyDE, metadata filter."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_openai():
    """Mock OpenAI client for QueryProcessor LLM calls."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"keywords": "电商, 电子商务, 趋势", "filters": {"doc_type": "report"}}'
            )
        )
    ]
    mock_client.chat.completions.create.return_value = mock_response

    with patch("app.self_rag.query_processor.OpenAI", return_value=mock_client):
        yield mock_client


class TestQueryProcessorExpandAndFilter:
    """Tests for keyword expansion + metadata filtering."""

    @pytest.mark.asyncio
    async def test_expand_appends_keywords(self, mock_openai):
        from app.self_rag.query_processor import QueryProcessor
        import app.self_rag.query_processor as qp_mod

        with patch.object(qp_mod, "QUERY_EXPANSION_ENABLED", True), \
             patch.object(qp_mod, "METADATA_FILTER_ENABLED", True):
            qp = QueryProcessor(timeout=10)
            result = await qp.process("电商趋势")
            assert "电商" in result.expanded
            assert result.metadata_filter == {"doc_type": "report"}

    @pytest.mark.asyncio
    async def test_disable_expansion_returns_original(self, mock_openai):
        from app.self_rag.query_processor import QueryProcessor
        import app.self_rag.query_processor as qp_mod

        with patch.object(qp_mod, "QUERY_EXPANSION_ENABLED", False), \
             patch.object(qp_mod, "METADATA_FILTER_ENABLED", False), \
             patch.object(qp_mod, "HYDE_ENABLED", False), \
             patch.object(qp_mod, "QUERY_DECOMPOSITION_ENABLED", False):
            qp = QueryProcessor(timeout=10)
            result = await qp.process("电商趋势")
            assert result.expanded == "电商趋势"
            assert result.hyde_text == ""
            assert result.sub_queries == []
            assert result.metadata_filter is None

    @pytest.mark.asyncio
    async def test_llm_call_failure_graceful_degradation(self):
        from app.self_rag.query_processor import QueryProcessor
        import app.self_rag.query_processor as qp_mod

        with patch.object(qp_mod, "QUERY_EXPANSION_ENABLED", True), \
             patch.object(qp_mod, "METADATA_FILTER_ENABLED", True), \
             patch("app.self_rag.query_processor.OpenAI") as mock_openai_cls:
            mock_openai_cls.side_effect = Exception("Connection refused")
            qp = QueryProcessor(timeout=10)
            result = await qp.process("test query")
            assert result.expanded == "test query"
            assert result.metadata_filter is None


class TestQueryProcessorDecomposition:
    """Tests for query decomposition."""

    @pytest.mark.asyncio
    async def test_decompose_returns_sub_queries(self):
        from app.self_rag.query_processor import QueryProcessor
        import app.self_rag.query_processor as qp_mod

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="淘宝商业模式\n京东商业模式\n两者差异"))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        with patch.object(qp_mod, "QUERY_EXPANSION_ENABLED", False), \
             patch.object(qp_mod, "METADATA_FILTER_ENABLED", False), \
             patch.object(qp_mod, "HYDE_ENABLED", False), \
             patch.object(qp_mod, "QUERY_DECOMPOSITION_ENABLED", True), \
             patch("app.self_rag.query_processor.OpenAI", return_value=mock_client):
            qp = QueryProcessor(timeout=10)
            result = await qp.process("对比淘宝和京东")
            assert len(result.sub_queries) == 3


class TestQueryProcessorHyDE:
    """Tests for HyDE generation."""

    @pytest.mark.asyncio
    async def test_hyde_returns_text(self):
        from app.self_rag.query_processor import QueryProcessor
        import app.self_rag.query_processor as qp_mod

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="假设性答案文档内容"))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        with patch.object(qp_mod, "QUERY_EXPANSION_ENABLED", False), \
             patch.object(qp_mod, "METADATA_FILTER_ENABLED", False), \
             patch.object(qp_mod, "HYDE_ENABLED", True), \
             patch.object(qp_mod, "QUERY_DECOMPOSITION_ENABLED", False), \
             patch("app.self_rag.query_processor.OpenAI", return_value=mock_client):
            qp = QueryProcessor(timeout=10)
            result = await qp.process("什么是深度学习")
            assert result.hyde_text == "假设性答案文档内容"
            assert result.original == "什么是深度学习"
