"""Knowledge Base API 端点测试 — 知识库列表的组隔离。"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_rag_engine():
    """Mock RAG 引擎，返回预设的知识库列表。"""
    mock_engine = MagicMock()
    mock_engine.list_kbs.return_value = [
        {"name": "kb-alpha", "description": "Alpha 知识库", "group_id": 1, "doc_count": 5},
        {"name": "kb-beta", "description": "Beta 知识库", "group_id": 1, "doc_count": 3},
    ]

    with patch("app.api.server.get_rag_engine", return_value=mock_engine):
        yield mock_engine


class TestListKnowledgeBases:
    """GET /api/kb/list 相关测试。"""

    async def test_list_kb_requires_auth(self, test_app):
        """未认证时返回 422。"""
        resp = await test_app.get("/api/kb/list")
        assert resp.status_code == 422

    async def test_list_kb_authenticated_user(self, test_app, mock_rag_engine, auth_headers):
        """普通用户获取本组知识库列表。"""
        resp = await test_app.get("/api/kb/list", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert "knowledge_bases" in data
        assert len(data["knowledge_bases"]) == 2
        assert data["knowledge_bases"][0]["name"] == "kb-alpha"

    async def test_list_kb_authenticated_admin(self, test_app, mock_rag_engine, admin_headers):
        """管理员获取全部知识库列表。"""
        resp = await test_app.get("/api/kb/list", headers=admin_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert "knowledge_bases" in data
        assert len(data["knowledge_bases"]) == 2

    async def test_list_kb_empty(self, test_app, mock_rag_engine, auth_headers):
        """无知识库时返回空列表。"""
        mock_rag_engine.list_kbs.return_value = []

        resp = await test_app.get("/api/kb/list", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["knowledge_bases"] == []
