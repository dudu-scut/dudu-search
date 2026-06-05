"""Unit tests for IterativeRetriever."""

from unittest.mock import MagicMock, patch

import pytest


class TestIterativeRetriever:
    """Tests for the iterative retrieval loop."""

    @pytest.mark.asyncio
    async def test_sufficient_on_first_round_stops_early(self):
        from app.self_rag.iterative_retriever import IterativeRetriever

        async def do_retrieve(q):
            return ["id1", "id2"]

        def do_get_texts(ids):
            return ["text for " + i for i in ids]

        with patch("app.self_rag.iterative_retriever.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.choices = [
                MagicMock(
                    message=MagicMock(
                        content='{"relevance":4,"completeness":4,"informativeness":4,"sufficient":true,"reason":"OK","rewrite_suggestion":""}'
                    )
                )
            ]
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            ir = IterativeRetriever(max_rounds=3)
            result = await ir.retrieve_with_judgment(
                "test query", do_retrieve, do_get_texts
            )
            assert result.rounds == 1
            assert result.sufficient is True
            assert result.parent_ids == ["id1", "id2"]

    @pytest.mark.asyncio
    async def test_insufficient_triggers_rewrite_and_retry(self):
        from app.self_rag.iterative_retriever import IterativeRetriever

        call_count = [0]

        async def do_retrieve(q):
            call_count[0] += 1
            return [f"id_{call_count[0]}"]

        def do_get_texts(ids):
            return ["text for " + i for i in ids]

        with patch("app.self_rag.iterative_retriever.OpenAI") as mock_openai:
            mock_client = MagicMock()

            # judge round 1: insufficient
            judge1 = MagicMock()
            judge1.choices = [
                MagicMock(
                    message=MagicMock(
                        content='{"relevance":2,"completeness":1,"informativeness":2,"sufficient":false,"reason":"不完整","rewrite_suggestion":"换个角度"}'
                    )
                )
            ]
            # rewrite response
            rewrite_resp = MagicMock()
            rewrite_resp.choices = [
                MagicMock(message=MagicMock(content="改写后的查询"))
            ]
            # judge round 2: sufficient
            judge2 = MagicMock()
            judge2.choices = [
                MagicMock(
                    message=MagicMock(
                        content='{"relevance":4,"completeness":4,"informativeness":4,"sufficient":true,"reason":"OK","rewrite_suggestion":""}'
                    )
                )
            ]

            mock_client.chat.completions.create.side_effect = [
                judge1,
                rewrite_resp,
                judge2,
            ]
            mock_openai.return_value = mock_client

            ir = IterativeRetriever(max_rounds=3)
            result = await ir.retrieve_with_judgment(
                "test query", do_retrieve, do_get_texts
            )

            assert call_count[0] >= 2
            assert result.sufficient is True
            assert len(result.retrieval_log) == 2

    @pytest.mark.asyncio
    async def test_max_rounds_exceeded_returns_what_we_have(self):
        from app.self_rag.iterative_retriever import IterativeRetriever

        async def do_retrieve(q):
            return ["id_x"]

        def do_get_texts(ids):
            return ["text x"]

        with patch("app.self_rag.iterative_retriever.OpenAI") as mock_openai:
            mock_client = MagicMock()

            def make_response(content):
                r = MagicMock()
                r.choices = [MagicMock(message=MagicMock(content=content))]
                return r

            # Round 1: judge insufficient → rewrite → Round 2: judge insufficient
            # (max_rounds=2, so stops after round 2 without trying rewrite again)
            mock_client.chat.completions.create.side_effect = [
                make_response('{"relevance":2,"completeness":2,"informativeness":2,"sufficient":false,"reason":"不够","rewrite_suggestion":"再试"}'),
                make_response("改写后的查询"),
                make_response('{"relevance":2,"completeness":2,"informativeness":2,"sufficient":false,"reason":"还是不够","rewrite_suggestion":""}'),
            ]
            mock_openai.return_value = mock_client

            ir = IterativeRetriever(max_rounds=2)
            result = await ir.retrieve_with_judgment(
                "test", do_retrieve, do_get_texts
            )

            assert result.rounds == 2
            assert result.sufficient is False
            # id_x is seen in round 1, round 2 returns it again but it's already seen
            # So only 1 unique parent_id, which is the expected behavior
            assert len(result.parent_ids) >= 1

    @pytest.mark.asyncio
    async def test_judge_llm_failure_falls_back_to_sufficient(self):
        from app.self_rag.iterative_retriever import IterativeRetriever

        async def do_retrieve(q):
            return ["id_fallback"]

        def do_get_texts(ids):
            return ["fallback text"]

        with patch("app.self_rag.iterative_retriever.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = Exception("API down")
            mock_openai.return_value = mock_client

            ir = IterativeRetriever(max_rounds=3)
            result = await ir.retrieve_with_judgment(
                "test", do_retrieve, do_get_texts
            )

            assert result.sufficient is True  # fallback treats as sufficient
            assert result.rounds == 1
