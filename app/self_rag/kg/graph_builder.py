"""Orchestrates building a knowledge graph from document chunks."""

import logging

from app.self_rag.kg.entity_extractor import EntityExtractor
from app.self_rag.kg.graph_store import GraphStore

logger = logging.getLogger(__name__)


class GraphBuilder:
    """Builds a knowledge graph by extracting entities/relations from chunks."""

    def __init__(self, store: GraphStore, extractor: EntityExtractor) -> None:
        self._store: GraphStore = store
        self._extractor: EntityExtractor = extractor

    async def build_from_chunks(
        self,
        chunks: list[str],
        chunk_ids: list[str],
        cache: dict | None = None,
    ) -> int:
        """Extract entities and relations from chunks and add to graph.

        Args:
            chunks: Document chunk texts.
            chunk_ids: Unique IDs for each chunk (used for caching).
            cache: Optional dict of ``{chunk_id: extraction_result}`` to
                skip already-extracted chunks.

        Returns:
            Total number of entities added.
        """
        cache = cache or {}
        total_entities = 0

        for chunk_text, chunk_id in zip(chunks, chunk_ids):
            if chunk_id in cache:
                data = cache[chunk_id]
            else:
                data = await self._extractor.extract_from_chunk(chunk_text)
                cache[chunk_id] = data

            entities = data.get("entities", [])
            relations = data.get("relations", [])

            if entities:
                self._store.add_entities(entities)
                total_entities += len(entities)
            if relations:
                self._store.add_relations(relations)

        self._store.save()
        logger.info(
            "Graph build complete",
            nodes=self._store.node_count(),
            edges=self._store.edge_count(),
        )
        return total_entities
