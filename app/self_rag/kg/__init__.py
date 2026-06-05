"""Knowledge Graph module — lightweight GraphRAG with LLM entity extraction."""

from app.self_rag.kg.entity_extractor import EntityExtractor
from app.self_rag.kg.graph_builder import GraphBuilder
from app.self_rag.kg.graph_retriever import GraphRetriever
from app.self_rag.kg.graph_store import GraphStore
from app.self_rag.kg.kg_fusion import KGFusion

__all__ = [
    "EntityExtractor",
    "GraphBuilder",
    "GraphRetriever",
    "GraphStore",
    "KGFusion",
]
