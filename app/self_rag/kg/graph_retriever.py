"""Knowledge graph retrieval — entity linking + k-hop subgraph traversal."""

import logging

from app.self_rag.config import KG_FUSION_TOP_K, KG_RETRIEVAL_HOPS
from app.self_rag.kg.entity_extractor import EntityExtractor
from app.self_rag.kg.graph_store import GraphStore

logger = logging.getLogger(__name__)


class GraphRetriever:
    """Retrieves knowledge graph context for a query via entity linking."""

    def __init__(
        self,
        store: GraphStore,
        extractor: EntityExtractor,
        hops: int | None = None,
        top_k: int | None = None,
    ) -> None:
        self._store: GraphStore = store
        self._extractor: EntityExtractor = extractor
        self._hops: int = hops or KG_RETRIEVAL_HOPS
        self._top_k: int = top_k or KG_FUSION_TOP_K

    async def retrieve(self, query: str) -> list[dict]:
        """Retrieve graph context for a query.

        Returns:
            List of neighbor dicts, formatted for LLM context.
        """
        entity_names = await self._extractor.extract_from_query(query)
        if not entity_names:
            return []

        all_neighbors: list[dict] = []
        seen: set[str] = set()

        for name in entity_names:
            matches = self._store.search_entity(name, fuzzy=True)
            for match in matches[:3]:
                neighbors = self._store.get_neighbors(match, hops=self._hops)
                for n in neighbors:
                    key = f"{n['entity']}|{n.get('relation', '')}"
                    if key not in seen:
                        seen.add(key)
                        all_neighbors.append(n)

        return all_neighbors[: self._top_k]

    def format_context(self, neighbors: list[dict]) -> str:
        """Format graph neighbors as human-readable context.

        Returns:
            Multi-line string suitable for LLM prompt.
        """
        if not neighbors:
            return ""

        lines = ["## 知识关联"]
        for i, n in enumerate(neighbors):
            entity_type = f"({n.get('type', '')})" if n.get("type") else ""
            line = (
                f"[{n.get('source_entity', '')}] "
                f"—{n.get('relation', '关联')}→ "
                f"[{n.get('entity', '')}]{entity_type}"
            )
            lines.append(f"{i + 1}. {line}")
        return "\n".join(lines)
