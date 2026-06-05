"""Unit tests for Knowledge Graph module."""

import tempfile
from unittest.mock import MagicMock, patch

import pytest


class TestGraphStore:
    """Tests for the networkx-backed GraphStore."""

    def test_add_entities_creates_nodes(self):
        from app.self_rag.kg.graph_store import GraphStore
        store = GraphStore("test_kb", tempfile.mkdtemp())
        store.add_entities([
            {"name": "阿里巴巴", "type": "公司", "attributes": {"行业": "电商"}},
            {"name": "淘宝", "type": "产品", "attributes": {}},
        ])
        assert store.node_count() == 2
        assert "阿里巴巴" in store.graph
        assert store.graph.nodes["阿里巴巴"]["type"] == "公司"

    def test_add_relations_creates_edges(self):
        from app.self_rag.kg.graph_store import GraphStore
        store = GraphStore("test_kb", tempfile.mkdtemp())
        store.add_entities([
            {"name": "阿里巴巴", "type": "公司"},
            {"name": "淘宝", "type": "产品"},
        ])
        store.add_relations([
            {"subject": "阿里巴巴", "predicate": "拥有", "object": "淘宝"},
        ])
        assert store.edge_count() == 1
        assert store.graph.has_edge("阿里巴巴", "淘宝")

    def test_get_neighbors_one_hop(self):
        from app.self_rag.kg.graph_store import GraphStore
        store = GraphStore("test_kb", tempfile.mkdtemp())
        store.add_entities([
            {"name": "A", "type": "公司"},
            {"name": "B", "type": "公司"},
        ])
        store.add_relations([
            {"subject": "A", "predicate": "收购", "object": "B"},
        ])
        neighbors = store.get_neighbors("A", hops=1)
        assert len(neighbors) == 1
        assert neighbors[0]["entity"] == "B"
        assert neighbors[0]["relation"] == "收购"

    def test_search_entity_fuzzy(self):
        from app.self_rag.kg.graph_store import GraphStore
        store = GraphStore("test_kb", tempfile.mkdtemp())
        store.add_entities([{"name": "阿里巴巴集团", "type": "公司"}])
        results = store.search_entity("阿里巴巴", fuzzy=True)
        assert "阿里巴巴集团" in results

    def test_save_and_load_roundtrip(self):
        from app.self_rag.kg.graph_store import GraphStore
        tmpdir = tempfile.mkdtemp()
        store = GraphStore("test_kb", tmpdir)
        store.add_entities([{"name": "TestEntity", "type": "概念"}])
        store.save()

        store2 = GraphStore("test_kb", tmpdir)
        assert store2.load() is True
        assert store2.node_count() == 1
        assert "TestEntity" in store2.graph

    def test_clear_removes_all(self):
        from app.self_rag.kg.graph_store import GraphStore
        store = GraphStore("test_kb", tempfile.mkdtemp())
        store.add_entities([{"name": "X"}, {"name": "Y"}])
        assert store.node_count() == 2
        store.clear()
        assert store.node_count() == 0
        assert store.edge_count() == 0

    def test_add_relations_auto_creates_missing_nodes(self):
        from app.self_rag.kg.graph_store import GraphStore
        store = GraphStore("test_kb", tempfile.mkdtemp())
        store.add_relations([
            {"subject": "新实体A", "predicate": "关联", "object": "新实体B"},
        ])
        assert store.node_count() == 2
        assert store.edge_count() == 1


class TestEntityExtractor:
    """Tests for LLM entity extraction."""

    @pytest.mark.asyncio
    async def test_extract_from_query_returns_entities(self):
        from app.self_rag.kg.entity_extractor import EntityExtractor

        with patch("app.self_rag.kg.entity_extractor.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.choices = [
                MagicMock(
                    message=MagicMock(
                        content='{"entities":["阿里巴巴","京东"]}'
                    )
                )
            ]
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            extractor = EntityExtractor()
            entities = await extractor.extract_from_query("对比阿里巴巴和京东")
            assert "阿里巴巴" in entities
            assert "京东" in entities

    @pytest.mark.asyncio
    async def test_extract_from_query_failure_returns_empty(self):
        from app.self_rag.kg.entity_extractor import EntityExtractor

        with patch("app.self_rag.kg.entity_extractor.OpenAI") as mock_openai:
            mock_openai.side_effect = Exception("Connection refused")
            extractor = EntityExtractor()
            entities = await extractor.extract_from_query("test")
            assert entities == []
