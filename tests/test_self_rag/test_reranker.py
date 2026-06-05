"""Unit tests for Cross-Encoder Reranker."""

from unittest.mock import MagicMock, patch

import pytest


class TestRerankerInit:
    """Tests for Reranker initialization."""

    def test_default_config(self):
        from app.self_rag.reranker import Reranker

        r = Reranker(
            model_name="test-model",
            device="cpu",
            top_k_input=10,
            top_k_output=4,
        )
        assert r.MODEL_NAME == "test-model"
        assert r.DEVICE == "cpu"
        assert r.TOP_K_INPUT == 10
        assert r.TOP_K_OUTPUT == 4
        assert r._model is None
        assert r._load_failed is False


class TestRerankerFallback:
    """Tests for reranker fallback behavior."""

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty(self):
        from app.self_rag.reranker import Reranker

        r = Reranker(model_name="test-model")
        result = await r.rerank("query", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_model_load_failed_returns_unordered_candidates(self):
        from app.self_rag.reranker import Reranker

        r = Reranker(model_name="test-model", top_k_output=2)
        r._load_failed = True
        candidates = [
            {"id": "a", "text": "text a", "score": 0.5},
            {"id": "b", "text": "text b", "score": 0.9},
            {"id": "c", "text": "text c", "score": 0.3},
        ]
        result = await r.rerank("query", candidates)
        assert len(result) == 2
        assert result[0]["id"] == "a"

    @pytest.mark.asyncio
    async def test_model_predict_called_and_sorts_by_score(self):
        from app.self_rag.reranker import Reranker

        mock_model = MagicMock()
        mock_model.predict.return_value = [0.1, 0.9, 0.5]

        r = Reranker(model_name="test-model", top_k_output=3)
        r._model = mock_model

        candidates = [
            {"id": "a", "text": "text a", "score": 0.5},
            {"id": "b", "text": "text b", "score": 0.9},
            {"id": "c", "text": "text c", "score": 0.3},
        ]
        result = await r.rerank("test query", candidates)

        assert len(result) == 3
        assert result[0]["id"] == "b"
        assert result[1]["id"] == "c"
        assert result[2]["id"] == "a"
        assert all("rerank_score" in c for c in result)

    @pytest.mark.asyncio
    async def test_truncates_to_top_k_output(self):
        from app.self_rag.reranker import Reranker

        mock_model = MagicMock()
        mock_model.predict.return_value = [0.1, 0.2, 0.3, 0.4, 0.5]
        r = Reranker(model_name="test-model", top_k_output=2)
        r._model = mock_model
        candidates = [
            {"id": f"c{i}", "text": f"text {i}", "score": 0.1 * i}
            for i in range(5)
        ]
        result = await r.rerank("q", candidates)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_timeout_falls_back_to_original_order(self):
        from app.self_rag.reranker import Reranker
        import asyncio as _asyncio

        mock_model = MagicMock()

        async def slow_predict(*args, **kwargs):
            await _asyncio.sleep(10)
            return [0.5, 0.5]

        mock_model.predict = slow_predict

        r = Reranker(model_name="test-model", top_k_output=2)
        r._model = mock_model
        candidates = [
            {"id": "a", "text": "text a", "score": 0.5},
            {"id": "b", "text": "text b", "score": 0.9},
        ]
        result = await r.rerank("q", candidates)
        assert len(result) == 2
        assert result[0]["id"] == "a"
