"""In-memory knowledge graph store backed by networkx + JSON persistence."""

import json
import logging
import os
from pathlib import Path

import networkx as nx

logger = logging.getLogger(__name__)


class GraphStore:
    """Directed graph store with JSON disk persistence.

    Nodes represent entities (name + type + attributes).
    Edges represent relations (subject → object with predicate label).
    """

    def __init__(self, kb_name: str, data_dir: str) -> None:
        self._kb_name: str = kb_name
        self._data_dir: str = data_dir
        self._graph: nx.DiGraph = nx.DiGraph()
        os.makedirs(data_dir, exist_ok=True)

    @property
    def graph(self) -> nx.DiGraph:
        return self._graph

    @property
    def _file_path(self) -> str:
        return str(Path(self._data_dir) / f"{self._kb_name}.json")

    def add_entities(self, entities: list[dict]) -> None:
        """Add or update entity nodes."""
        for e in entities:
            name = e.get("name", "").strip()
            if not name:
                continue
            self._graph.add_node(
                name,
                type=e.get("type", ""),
                attributes=e.get("attributes", {}),
            )

    def add_relations(self, relations: list[dict]) -> None:
        """Add relation edges between entities."""
        for r in relations:
            subj = r.get("subject", "").strip()
            obj = r.get("object", "").strip()
            pred = r.get("predicate", "")
            if not subj or not obj:
                continue
            if subj not in self._graph:
                self._graph.add_node(subj, type="", attributes={})
            if obj not in self._graph:
                self._graph.add_node(obj, type="", attributes={})
            self._graph.add_edge(subj, obj, predicate=pred, **{
                k: v for k, v in r.items()
                if k not in ("subject", "predicate", "object")
            })

    def get_neighbors(self, entity: str, hops: int = 1) -> list[dict]:
        """Get k-hop neighbors of an entity.

        Returns:
            List of dicts with entity info and relations.
        """
        if entity not in self._graph:
            return []

        results: list[dict] = []
        visited: set[str] = {entity}
        frontier: set[str] = {entity}

        for _ in range(hops):
            next_frontier: set[str] = set()
            for node in frontier:
                for _, neighbor in self._graph.out_edges(node):
                    if neighbor not in visited:
                        edge_data = self._graph.edges[node, neighbor]
                        node_data = self._graph.nodes[neighbor]
                        results.append({
                            "entity": neighbor,
                            "type": node_data.get("type", ""),
                            "relation": edge_data.get("predicate", ""),
                            "source_entity": node,
                        })
                        visited.add(neighbor)
                        next_frontier.add(neighbor)
                for predecessor, _ in self._graph.in_edges(node):
                    if predecessor not in visited:
                        edge_data = self._graph.edges[predecessor, node]
                        node_data = self._graph.nodes[predecessor]
                        results.append({
                            "entity": predecessor,
                            "type": node_data.get("type", ""),
                            "relation": edge_data.get("predicate", ""),
                            "source_entity": node,
                        })
                        visited.add(predecessor)
                        next_frontier.add(predecessor)
            frontier = next_frontier

        return results

    def search_entity(self, name: str, fuzzy: bool = True) -> list[str]:
        """Search for entities by name.

        Args:
            name: Search term.
            fuzzy: If True, do substring matching. Otherwise exact match.

        Returns:
            List of matching entity names.
        """
        if fuzzy:
            return [n for n in self._graph.nodes if name.lower() in n.lower()]
        return [name] if name in self._graph else []

    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def save(self) -> None:
        """Persist graph to JSON."""
        data = nx.node_link_data(self._graph)
        with open(self._file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Graph saved", kb=self._kb_name, nodes=self.node_count())

    def load(self) -> bool:
        """Load graph from JSON. Returns False if file missing/corrupt."""
        if not os.path.exists(self._file_path):
            return False
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._graph = nx.node_link_graph(data)
            logger.info("Graph loaded", kb=self._kb_name, nodes=self.node_count())
            return True
        except Exception:
            logger.warning("Graph load failed — starting empty", exc_info=True)
            self._graph = nx.DiGraph()
            return False

    def clear(self) -> None:
        """Clear all nodes and edges."""
        self._graph.clear()
