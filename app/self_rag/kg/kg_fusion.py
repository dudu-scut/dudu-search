"""Merge vector retrieval results with knowledge graph context for LLM answer."""

import logging

from app.self_rag.config import KG_ENABLED
from app.self_rag.kg.entity_extractor import EntityExtractor
from app.self_rag.kg.graph_retriever import GraphRetriever
from app.self_rag.kg.graph_store import GraphStore

logger = logging.getLogger(__name__)


class KGFusion:
    """Fuses vector retrieval results with knowledge graph context.

    Usage::

        fusion = KGFusion(graph_store)
        kg_context = await fusion.get_kg_context(query)
        # Append kg_context to the LLM prompt alongside vector-retrieved docs
    """

    def __init__(self, store: GraphStore) -> None:
        self._store: GraphStore = store
        self._extractor: EntityExtractor = EntityExtractor()
        self._retriever: GraphRetriever = GraphRetriever(store, self._extractor)

    async def get_kg_context(self, query: str) -> str:
        """Retrieve and format KG context for a query.

        Returns:
            Formatted string for LLM prompt, or empty string if KG disabled/empty.
        """
        if not KG_ENABLED:
            return ""
        if self._store.node_count() == 0:
            return ""

        try:
            neighbors = await self._retriever.retrieve(query)
            return self._retriever.format_context(neighbors)
        except Exception:
            logger.warning("KG context retrieval failed", exc_info=True)
            return ""
