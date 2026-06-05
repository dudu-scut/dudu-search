"""
Cross-Encoder Reranker for RAG retrieval results.

Uses BAAI/bge-reranker-v2-m3 to re-rank candidate documents after RRF fusion.
Supports lazy loading, timeout fallback, and graceful degradation.
"""

import asyncio
import logging
from typing import Optional

from app.self_rag.config import (
    RERANK_DEVICE,
    RERANK_MODEL,
    RERANK_TOP_K_INPUT,
    RERANK_TOP_K_OUTPUT,
)

logger = logging.getLogger(__name__)


class Reranker:
    """Cross-Encoder re-ranker for post-retrieval refinement.

    Wraps a HuggingFace cross-encoder model. On model load failure or
    inference timeout, falls back to returning candidates unchanged.
    """

    def __init__(
        self,
        model_name: str = RERANK_MODEL,
        device: str = RERANK_DEVICE,
        top_k_input: int = RERANK_TOP_K_INPUT,
        top_k_output: int = RERANK_TOP_K_OUTPUT,
    ) -> None:
        self.MODEL_NAME: str = model_name
        self.DEVICE: str = device
        self.TOP_K_INPUT: int = top_k_input
        self.TOP_K_OUTPUT: int = top_k_output
        self._model: Optional[object] = None
        self._load_failed: bool = False

    def _load_model(self) -> Optional[object]:
        """Lazy-load the cross-encoder model."""
        if self._load_failed:
            return None
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                self.MODEL_NAME,
                device=self.DEVICE,
            )
            logger.info(
                "Reranker model loaded",
                model=self.MODEL_NAME,
                device=self.DEVICE,
            )
            return self._model
        except Exception:
            self._load_failed = True
            logger.warning(
                "Reranker model load failed — retrieval will skip re-ranking",
                exc_info=True,
            )
            return None

    async def rerank(
        self,
        query: str,
        candidates: list[dict],
    ) -> list[dict]:
        """Re-rank candidate documents using the cross-encoder."""
        if not candidates:
            return candidates

        model = self._load_model()
        if model is None:
            return candidates[: self.TOP_K_OUTPUT]

        limited = candidates[: self.TOP_K_INPUT]

        try:
            pairs = [(query, c["text"]) for c in limited]
            scores = await asyncio.wait_for(
                asyncio.to_thread(model.predict, pairs),
                timeout=5.0,
            )

            for i, c in enumerate(limited):
                c["rerank_score"] = float(scores[i]) if i < len(scores) else 0.0

            limited.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)
            return limited[: self.TOP_K_OUTPUT]

        except asyncio.TimeoutError:
            logger.warning("Reranker timed out — falling back to original order")
        except Exception:
            logger.warning("Reranker inference failed — falling back", exc_info=True)

        return candidates[: self.TOP_K_OUTPUT]
