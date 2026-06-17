"""
Search Backend abstraction for sparse retrieval.

Provides a common interface so the RAG engine can swap between
BM25Okapi (current), Elasticsearch, or Meilisearch without changing
the query pipeline.
"""

import logging
from abc import ABC, abstractmethod

from rank_bm25 import BM25Okapi

from app.self_rag.config import BM25_FULL_REBUILD_THRESHOLD

logger = logging.getLogger(__name__)


class SearchBackend(ABC):
    """Abstract interface for sparse (keyword) retrieval backends."""

    @abstractmethod
    def add(self, doc_id: str, text: str, metadata: dict | None = None) -> None:
        """Index a single document."""
        ...

    @abstractmethod
    def remove(self, doc_id: str) -> None:
        """Remove a document from the index."""
        ...

    @abstractmethod
    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """Search the index.

        Returns:
            List of ``(doc_id, score)`` tuples sorted by relevance descending.
        """
        ...

    @abstractmethod
    def clear(self) -> None:
        """Remove all documents from the index."""
        ...

    @abstractmethod
    def size(self) -> int:
        """Return the number of indexed documents."""
        ...


class BM25Backend(SearchBackend):
    """BM25Okapi-based sparse retrieval backend.

    Wraps the existing BM25 logic. For document counts below
    ``BM25_FULL_REBUILD_THRESHOLD``, rebuilding the entire corpus on each
    add/remove is fast (<100ms). For larger corpora, consider switching to
    an external backend.
    """

    def __init__(self, tokenizer) -> None:
        """Args:
            tokenizer: Callable that takes a string and returns a list of tokens.
        """
        self._tokenizer = tokenizer
        self._model: BM25Okapi | None = None
        self._doc_store: dict[str, tuple[list[str], dict]] = {}
        # doc_id → (tokenized_text, metadata)
        self._dirty: bool = False  # 延迟重建标志：add/remove 后标记，search 时才重建

    def add(self, doc_id: str, text: str, metadata: dict | None = None) -> None:
        tokens = self._tokenizer(text)
        self._doc_store[doc_id] = (tokens, metadata or {})
        self._dirty = True

    def remove(self, doc_id: str) -> None:
        self._doc_store.pop(doc_id, None)
        self._dirty = True

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        # 延迟重建：只在 search 时才检查并重建 BM25 索引
        if self._dirty:
            self._rebuild_if_needed()
            self._dirty = False

        if self._model is None:
            return []

        tokenized_query = self._tokenizer(query)
        scores = self._model.get_scores(tokenized_query)

        doc_ids = list(self._doc_store.keys())
        ranked = sorted(
            enumerate(scores),
            key=lambda x: x[1],
            reverse=True,
        )

        results = []
        for idx, score in ranked:
            if score <= 0:
                continue
            if idx < len(doc_ids):
                results.append((doc_ids[idx], float(score)))
            if len(results) >= top_k:
                break
        return results

    def clear(self) -> None:
        self._doc_store.clear()
        self._model = None
        self._dirty = False

    def size(self) -> int:
        return len(self._doc_store)

    def _rebuild_if_needed(self) -> None:
        """Rebuild the BM25 model from current doc_store.

        This is O(n) in corpus size but fast for n < BM25_FULL_REBUILD_THRESHOLD.
        """
        if not self._doc_store:
            self._model = None
            return

        size = len(self._doc_store)
        if size > BM25_FULL_REBUILD_THRESHOLD:
            logger.warning(
                "BM25 corpus exceeds threshold — consider switching to "
                "an external search backend for better incremental performance",
                corpus_size=size,
                threshold=BM25_FULL_REBUILD_THRESHOLD,
            )

        tokenized = [tokens for tokens, _meta in self._doc_store.values()]
        self._model = BM25Okapi(tokenized)

    def pop_doc_metadata(self, doc_id: str) -> dict | None:
        """Get and return metadata for a doc_id (used during BM25→parent_id mapping)."""
        entry = self._doc_store.get(doc_id)
        if entry:
            return entry[1]
        return None

    def doc_ids(self) -> list[str]:
        """Return all indexed document IDs in insertion order."""
        return list(self._doc_store.keys())
