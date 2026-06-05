# RAG Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 5-phase RAG optimization: Cross-Encoder Reranker, QueryProcessor (expansion/decomposition/HyDE/metadata), Iterative Retriever, Knowledge Graph Fusion, and SearchBackend abstraction.

**Architecture:** Each phase adds new composable modules around the existing `RAGEngine.query()` pipeline. New modules are toggle-able via config flags and have independent try-catch fallback. No existing dual-path retrieval + RRF logic is rewritten.

**Tech Stack:** Python 3.12, sentence-transformers, ChromaDB, jieba, rank-bm25, networkx (new dep), DeepSeek LLM, pytest + pytest-asyncio

---

## Phase Dependency Order

```
Phase 1 (Reranker) ──────────┐
                              ├── Phase 3 (Iterative) ──→ Done
Phase 2 (QueryProcessor) ────┘

Phase 4 (KG Fusion) ─────────→ Done (independent)

Phase 5 (SearchBackend) ─────→ Done (independent)
```

## Quality Gate Checklist (Every Phase)

Before marking any phase complete, all of these MUST pass:
1. `uv run pytest tests/ -v` — all existing tests still pass
2. New phase-specific tests pass
3. Code review via `/code-review` — no unresolved findings
4. Manual verification: `uv run python -m app.api.server` starts without error
5. Feature toggle OFF → behavior identical to pre-change

---

## Phase 1: Cross-Encoder Reranker

### Task 1.1: Add reranker config to `app/self_rag/config.py`

**Files:**
- Modify: `app/self_rag/config.py`

- [ ] **Step 1: Add reranker configuration block**

Add the following block at the end of `app/self_rag/config.py` (after line 51, the `LLM_MODEL` line):

```python
# ── Phase 1: Cross-Encoder Reranker ──
RERANK_ENABLED = os.getenv("SELF_RAG_RERANK_ENABLED", "true").lower() != "false"
RERANK_MODEL = os.getenv("SELF_RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
RERANK_DEVICE = os.getenv("SELF_RAG_RERANK_DEVICE", "cpu")
RERANK_TOP_K_INPUT = int(os.getenv("SELF_RAG_RERANK_TOP_K_INPUT", "10"))
RERANK_TOP_K_OUTPUT = int(os.getenv("SELF_RAG_RERANK_TOP_K_OUTPUT", "4"))
```

- [ ] **Step 2: Verify config imports work**

Run: `uv run python -c "from app.self_rag.config import RERANK_ENABLED, RERANK_MODEL; print(RERANK_ENABLED, RERANK_MODEL)"`
Expected: `True BAAI/bge-reranker-v2-m3`

- [ ] **Step 3: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/config.py && git commit -m "feat(rag): add reranker configuration block

Phase 1 prep — config toggles for Cross-Encoder reranker

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 1.2: Create `app/self_rag/reranker.py`

**Files:**
- Create: `app/self_rag/reranker.py`

- [ ] **Step 1: Create the reranker module**

Write `app/self_rag/reranker.py`:

```python
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

    Attributes:
        MODEL_NAME: HuggingFace model identifier for the cross-encoder.
        DEVICE: Torch device string (``"cpu"`` or ``"cuda"``).
        TOP_K_INPUT: Maximum number of candidates accepted for re-ranking.
        TOP_K_OUTPUT: Number of candidates returned after re-ranking.
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
        """Lazy-load the cross-encoder model.

        Returns:
            The loaded model, or ``None`` if loading failed.
        """
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
        """Re-rank candidate documents using the cross-encoder.

        Args:
            query: The user's original question.
            candidates: List of dicts with keys ``"id"``, ``"text"``, ``"score"``.

        Returns:
            Candidates re-ordered by ``rerank_score`` (descending), limited to
            ``TOP_K_OUTPUT``. Falls back to original order on any error.
        """
        if not candidates:
            return candidates

        model = self._load_model()
        if model is None:
            return candidates[: self.TOP_K_OUTPUT]

        # Limit input size
        limited = candidates[: self.TOP_K_INPUT]

        try:
            # Build (query, doc) pairs and predict
            pairs = [(query, c["text"]) for c in limited]
            scores = await asyncio.wait_for(
                asyncio.to_thread(model.predict, pairs),
                timeout=5.0,
            )

            # Annotate candidates with rerank scores
            for i, c in enumerate(limited):
                c["rerank_score"] = float(scores[i]) if i < len(scores) else 0.0

            # Sort by rerank score descending
            limited.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)
            return limited[: self.TOP_K_OUTPUT]

        except asyncio.TimeoutError:
            logger.warning("Reranker timed out — falling back to original order")
        except Exception:
            logger.warning("Reranker inference failed — falling back", exc_info=True)

        return candidates[: self.TOP_K_OUTPUT]
```

- [ ] **Step 2: Verify the module imports without loading model**

Run: `uv run python -c "from app.self_rag.reranker import Reranker; r = Reranker(); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/reranker.py && git commit -m "feat(rag): add Cross-Encoder Reranker module

Lazy-loads BAAI/bge-reranker-v2-m3, 5s timeout fallback,
graceful degradation on model load/inference failure

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 1.3: Write unit tests for Reranker

**Files:**
- Create: `tests/test_self_rag/__init__.py`
- Create: `tests/test_self_rag/test_reranker.py`

- [ ] **Step 1: Create test package init**

Write `tests/test_self_rag/__init__.py` (empty file):

```python
# Self-RAG test package
```

- [ ] **Step 2: Write reranker tests**

Write `tests/test_self_rag/test_reranker.py`:

```python
"""Unit tests for Cross-Encoder Reranker."""

from unittest.mock import MagicMock, patch

import pytest


class TestRerankerInit:
    """Tests for Reranker initialization."""

    def test_default_config(self):
        from app.self_rag.reranker import Reranker
        r = Reranker(
            model_name="test-model",
            device="cpu",
            top_k_input=10,
            top_k_output=4,
        )
        assert r.MODEL_NAME == "test-model"
        assert r.DEVICE == "cpu"
        assert r.TOP_K_INPUT == 10
        assert r.TOP_K_OUTPUT == 4
        assert r._model is None
        assert r._load_failed is False


class TestRerankerFallback:
    """Tests for reranker fallback behavior."""

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty(self):
        from app.self_rag.reranker import Reranker
        r = Reranker(model_name="test-model")
        result = await r.rerank("query", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_model_load_failed_returns_unordered_candidates(self):
        from app.self_rag.reranker import Reranker
        r = Reranker(model_name="test-model", top_k_output=2)
        r._load_failed = True
        candidates = [
            {"id": "a", "text": "text a", "score": 0.5},
            {"id": "b", "text": "text b", "score": 0.9},
            {"id": "c", "text": "text c", "score": 0.3},
        ]
        result = await r.rerank("query", candidates)
        assert len(result) == 2
        # Returns first N candidates unchanged when model not available
        assert result[0]["id"] == "a"

    @pytest.mark.asyncio
    async def test_model_predict_called_and_sorts_by_score(self):
        from app.self_rag.reranker import Reranker

        mock_model = MagicMock()
        mock_model.predict.return_value = [0.1, 0.9, 0.5]

        r = Reranker(model_name="test-model", top_k_output=3)
        r._model = mock_model

        candidates = [
            {"id": "a", "text": "text a", "score": 0.5},
            {"id": "b", "text": "text b", "score": 0.9},
            {"id": "c", "text": "text c", "score": 0.3},
        ]
        result = await r.rerank("test query", candidates)

        assert len(result) == 3
        assert result[0]["id"] == "b"  # highest rerank score (0.9)
        assert result[1]["id"] == "c"  # middle (0.5)
        assert result[2]["id"] == "a"  # lowest (0.1)
        assert all("rerank_score" in c for c in result)

    @pytest.mark.asyncio
    async def test_truncates_to_top_k_output(self):
        from app.self_rag.reranker import Reranker

        mock_model = MagicMock()
        mock_model.predict.return_value = [0.1, 0.2, 0.3, 0.4, 0.5]
        r = Reranker(model_name="test-model", top_k_output=2)
        r._model = mock_model
        candidates = [
            {"id": f"c{i}", "text": f"text {i}", "score": 0.1 * i}
            for i in range(5)
        ]
        result = await r.rerank("q", candidates)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_timeout_falls_back_to_original_order(self):
        from app.self_rag.reranker import Reranker
        import asyncio

        mock_model = MagicMock()

        async def slow_predict(*args, **kwargs):
            await asyncio.sleep(10)
            return [0.5, 0.5]

        mock_model.predict = slow_predict

        r = Reranker(model_name="test-model", top_k_output=2)
        r._model = mock_model
        candidates = [
            {"id": "a", "text": "text a", "score": 0.5},
            {"id": "b", "text": "text b", "score": 0.9},
        ]
        result = await r.rerank("q", candidates)
        assert len(result) == 2
        assert result[0]["id"] == "a"  # original order preserved
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_self_rag/test_reranker.py -v`
Expected: All 6 tests PASS

- [ ] **Step 4: Commit**

```bash
cd deepsearch-agents && git add tests/test_self_rag/ && git commit -m "test(rag): add Reranker unit tests

6 tests covering init, empty candidates, load failure fallback,
sort-by-score, truncation, and timeout fallback

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 1.4: Integrate Reranker into `RAGEngine.query()`

**Files:**
- Modify: `app/self_rag/engine.py`

- [ ] **Step 1: Add import for Reranker and config**

In `app/self_rag/engine.py`, add to the existing import block from `app.self_rag.config` (line 27-41 area). Add `RERANK_ENABLED, RERANK_TOP_K_OUTPUT` to the existing import:

Edit the import block — change:
```python
from app.self_rag.config import (
    BM25_ENABLED,
    BM25_TOP_K,
    CHILD_CHUNK_SIZE,
    CHROMA_PERSIST_DIR,
    CHUNK_OVERLAP,
    DOC_STORE_DIR,
    EMBEDDING_MODEL,
    HYBRID_TOP_K,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    PARENT_CHUNK_SIZE,
    RRF_K,
    TOP_K,
)
```

To:
```python
from app.self_rag.config import (
    BM25_ENABLED,
    BM25_TOP_K,
    CHILD_CHUNK_SIZE,
    CHROMA_PERSIST_DIR,
    CHUNK_OVERLAP,
    DOC_STORE_DIR,
    EMBEDDING_MODEL,
    HYBRID_TOP_K,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    PARENT_CHUNK_SIZE,
    RERANK_ENABLED,
    RERANK_TOP_K_OUTPUT,
    RRF_K,
    TOP_K,
)
```

- [ ] **Step 2: Add reranker field to `RAGEngine.__init__`**

In `RAGEngine.__init__` (around line 59, after `self._bm25_metadatas`), add:

```python
        # Reranker: lazy-init on first query
        self._reranker = None
```

- [ ] **Step 3: Add `_get_reranker` helper method**

Add this method to `RAGEngine` class (after `_tokenize`, before `_invalidate_bm25`):

```python
    def _get_reranker(self):
        """Lazy-init the cross-encoder reranker."""
        if not RERANK_ENABLED:
            return None
        if self._reranker is None:
            from app.self_rag.reranker import Reranker
            self._reranker = Reranker()
        return self._reranker
```

- [ ] **Step 4: Insert reranker call into `query()` method**

In the `query()` method, locate the section after RRF fusion where `top_parent_ids` is computed (around lines 472-473):

```python
        # 取融合后的 top_k 个父块
        top_parent_ids = merged_parent_ids[:HYBRID_TOP_K]
```

Replace with:

```python
        # 取融合后的 top_k 个父块（扩取更多候选供 reranker 精排）
        top_parent_ids = merged_parent_ids[:HYBRID_TOP_K]

        # ── Phase 1: Cross-Encoder Reranker ──
        reranker = self._get_reranker()
        if reranker is not None and len(top_parent_ids) > 1:
            # 先回填父块文本构建候选列表
            parent_collection = self._get_parent_collection(kb_name)
            if parent_collection:
                parent_results = parent_collection.get(ids=top_parent_ids)
                parent_docs = parent_results.get("documents", [])
                if parent_docs:
                    candidates = [
                        {"id": pid, "text": doc, "score": 0.0}
                        for pid, doc in zip(top_parent_ids, parent_docs)
                    ]
                    import asyncio
                    try:
                        loop = asyncio.get_event_loop()
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                    reranked = loop.run_until_complete(
                        reranker.rerank(question, candidates)
                    )
                    top_parent_ids = [c["id"] for c in reranked[:RERANK_TOP_K_OUTPUT]]
```

- [ ] **Step 5: Verify no syntax errors**

Run: `uv run python -c "from app.self_rag.engine import RAGEngine; print('OK')"`
Expected: `OK` (model not loaded yet)

- [ ] **Step 6: Run all existing tests**

Run: `uv run pytest tests/ -v`
Expected: All existing tests still PASS

- [ ] **Step 7: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/engine.py && git commit -m "feat(rag): integrate Cross-Encoder Reranker into query pipeline

Inserts reranker after RRF fusion with lazy init and feature toggle.
When RERANK_ENABLED=false, behavior is identical to pre-change.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 1.5: Phase 1 Code Review & Gate Check

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL tests PASS

- [ ] **Step 2: Run code review**

Invoke: `/code-review` on the Phase 1 diff. Fix any findings.

- [ ] **Step 3: Verify toggle-off regression**

Run: `SELF_RAG_RERANK_ENABLED=false uv run pytest tests/ -v`
Expected: All tests still PASS (reranker skipped, original behavior preserved)

- [ ] **Step 4: Verify app starts**

Run: `timeout 5 uv run python -m app.api.server 2>&1 || true`
Expected: No import errors, app initializes cleanly

- [ ] **Step 5: Mark Phase 1 complete**

Phase 1 done. Proceed to Phase 2.

---

## Phase 2: Query-Side Enhancement (QueryProcessor)

### Task 2.1: Add QueryProcessor config to `app/self_rag/config.py`

**Files:**
- Modify: `app/self_rag/config.py`

- [ ] **Step 1: Add query processor configuration block**

Add at the end of `app/self_rag/config.py`:

```python
# ── Phase 2: Query Processor ──
QUERY_EXPANSION_ENABLED = os.getenv("SELF_RAG_QUERY_EXPANSION", "true").lower() != "false"
QUERY_DECOMPOSITION_ENABLED = os.getenv("SELF_RAG_QUERY_DECOMPOSITION", "false").lower() == "true"
HYDE_ENABLED = os.getenv("SELF_RAG_HYDE_ENABLED", "false").lower() == "true"
METADATA_FILTER_ENABLED = os.getenv("SELF_RAG_METADATA_FILTER", "true").lower() != "false"
QUERY_PROCESSOR_MODEL = os.getenv("SELF_RAG_QUERY_PROCESSOR_MODEL", LLM_MODEL)
QUERY_PROCESSOR_TIMEOUT = int(os.getenv("SELF_RAG_QUERY_PROCESSOR_TIMEOUT", "5"))
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from app.self_rag.config import QUERY_EXPANSION_ENABLED, HYDE_ENABLED; print(QUERY_EXPANSION_ENABLED, HYDE_ENABLED)"`
Expected: `True False`

- [ ] **Step 3: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/config.py && git commit -m "feat(rag): add QueryProcessor configuration block

Phase 2 prep — toggles for keyword expansion, decomposition,
HyDE generation, and metadata filtering

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2.2: Create `app/self_rag/query_processor.py`

**Files:**
- Create: `app/self_rag/query_processor.py`

- [ ] **Step 1: Create the QueryProcessor module**

Write `app/self_rag/query_processor.py`:

```python
"""
Query Processor for RAG — keyword expansion, decomposition, HyDE, metadata filtering.

All sub-modules share one LLM client. Each sub-module has independent error
handling — failure in one does not affect others or the base retrieval.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

from app.self_rag.config import (
    HYDE_ENABLED,
    LLM_API_KEY,
    LLM_BASE_URL,
    METADATA_FILTER_ENABLED,
    QUERY_DECOMPOSITION_ENABLED,
    QUERY_EXPANSION_ENABLED,
    QUERY_PROCESSOR_MODEL,
    QUERY_PROCESSOR_TIMEOUT,
)

logger = logging.getLogger(__name__)


@dataclass
class ProcessedQuery:
    """Result of query processing.

    Attributes:
        original: The original user query.
        expanded: Original query with expanded keywords appended (for embedding/BM25).
        sub_queries: Decomposed sub-queries (empty list if decomposition disabled/failed).
        hyde_text: Hypothetical answer text (empty string if HyDE disabled/failed).
        metadata_filter: ChromaDB ``where`` clause dict (``None`` if no filters extracted).
    """

    original: str
    expanded: str = ""
    sub_queries: list[str] = field(default_factory=list)
    hyde_text: str = ""
    metadata_filter: Optional[dict] = None


class QueryProcessor:
    """Processes user queries before retrieval.

    Four sub-modules, each independently toggle-able:

    * **KeywordExpander** — LLM extracts keywords/synonyms, appends to query.
    * **QueryDecomposer** — LLM judges complexity, splits into sub-queries.
    * **HyDEGenerator** — LLM generates hypothetical answer for embedding.
    * **MetadataFilter** — LLM extracts structured ChromaDB filter from query.

    Keyword expansion and metadata filtering are merged into one LLM call
    for efficiency when both are enabled.
    """

    def __init__(
        self,
        model: str = QUERY_PROCESSOR_MODEL,
        timeout: int = QUERY_PROCESSOR_TIMEOUT,
    ) -> None:
        self._model: str = model
        self._timeout: int = timeout
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
        return self._client

    async def process(self, query: str) -> ProcessedQuery:
        """Run enabled sub-modules and return a :class:`ProcessedQuery`.

        Each sub-module runs with independent error handling.
        """
        result = ProcessedQuery(original=query, expanded=query)

        # Run expansion + metadata filter in parallel (they can share one LLM call)
        expand_task = None
        metadata_task = None
        hyde_task = None
        decompose_task = None

        if QUERY_EXPANSION_ENABLED or METADATA_FILTER_ENABLED:
            expand_task = asyncio.create_task(
                self._expand_and_filter(query)
            )

        if HYDE_ENABLED:
            hyde_task = asyncio.create_task(self._generate_hyde(query))

        if QUERY_DECOMPOSITION_ENABLED:
            decompose_task = asyncio.create_task(self._decompose(query))

        # Gather results
        if expand_task is not None:
            try:
                expanded, meta_filter = await expand_task
                result.expanded = expanded
                result.metadata_filter = meta_filter
            except Exception:
                logger.warning("Keyword expansion / metadata filter failed", exc_info=True)

        if hyde_task is not None:
            try:
                result.hyde_text = await hyde_task
            except Exception:
                logger.warning("HyDE generation failed", exc_info=True)

        if decompose_task is not None:
            try:
                result.sub_queries = await decompose_task
            except Exception:
                logger.warning("Query decomposition failed", exc_info=True)

        return result

    async def _expand_and_filter(self, query: str) -> tuple[str, Optional[dict]]:
        """Combined LLM call for keyword expansion + metadata filter extraction.

        Returns:
            (expanded_query, metadata_filter_dict_or_None)
        """
        prompt = (
            "你是一个查询分析助手。对用户问题做两件事：\n"
            "1. 提取关键词和同义词（逗号分隔，5-10个关键词）\n"
            "2. 如果问题中包含时间、文档类型、实体名称等过滤条件，提取出来\n\n"
            "请严格按以下JSON格式回复：\n"
            '{"keywords": "关键词1, 关键词2, ...", "filters": {"year": "2024", "doc_type": "report"}}'
            "\n\n"
            f"用户问题：{query}"
        )

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.client.chat.completions.create(
                        model=self._model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=200,
                    )
                ),
                timeout=self._timeout,
            )
            content = response.choices[0].message.content or "{}"
            # Extract JSON from possible markdown code block
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            data = json.loads(content.strip())
        except Exception:
            logger.warning("Expand+filter LLM call failed", exc_info=True)
            return query, None

        keywords = data.get("keywords", "")
        filters = data.get("filters") if METADATA_FILTER_ENABLED else None

        # Filter out empty dict
        if filters is not None and not filters:
            filters = None

        expanded = f"{query} {keywords}" if keywords else query
        return expanded.strip(), filters

    async def _generate_hyde(self, query: str) -> str:
        """Generate a hypothetical answer document for HyDE retrieval."""
        prompt = (
            "你是一个知识库助手。请根据以下问题，写一段假设性的答案（200-400字），"
            "用文档报告的风格来写，就像这个答案来自知识库中的一篇文档。\n\n"
            f"问题：{query}"
        )

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.client.chat.completions.create(
                        model=self._model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3,
                        max_tokens=500,
                    )
                ),
                timeout=self._timeout,
            )
            return response.choices[0].message.content or ""
        except Exception:
            logger.warning("HyDE generation LLM call failed", exc_info=True)
            return ""

    async def _decompose(self, query: str) -> list[str]:
        """Decompose a complex query into 2-3 sub-queries, or return empty list
        if the query is simple.
        """
        prompt = (
            "判断以下问题是否需要拆分成多个子问题来回答。如果需要拆分，返回2-3个子问题（每行一个）。"
            "如果问题很简单不需要拆分，返回空。\n\n"
            "需要拆分的情况：包含多个子问题、对比类问题、因果类问题、需要多角度回答的问题。\n\n"
            f"问题：{query}\n\n"
            "请按以下格式回复（不需要拆分的返回空行）：\n"
            "子问题1\n子问题2\n子问题3"
        )

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.client.chat.completions.create(
                        model=self._model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=200,
                    )
                ),
                timeout=self._timeout,
            )
            content = response.choices[0].message.content or ""
            lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
            # Filter out empty/trivial lines
            return [l for l in lines if len(l) > 2 and l != query]
        except Exception:
            logger.warning("Query decomposition LLM call failed", exc_info=True)
            return []
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from app.self_rag.query_processor import QueryProcessor, ProcessedQuery; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/query_processor.py && git commit -m "feat(rag): add QueryProcessor module with 4 sub-modules

Keyword expansion, query decomposition, HyDE generation,
and metadata filtering — each independently toggle-able with
error isolation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2.3: Write unit tests for QueryProcessor

**Files:**
- Create: `tests/test_self_rag/test_query_processor.py`

- [ ] **Step 1: Write QueryProcessor tests**

Write `tests/test_self_rag/test_query_processor.py`:

```python
"""Unit tests for QueryProcessor — keyword expansion, decomposition, HyDE, metadata filter."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_openai():
    """Mock OpenAI client for QueryProcessor LLM calls."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"keywords": "电商, 电子商务, 趋势", "filters": {"doc_type": "report"}}'
            )
        )
    ]
    mock_client.chat.completions.create.return_value = mock_response

    with patch("app.self_rag.query_processor.OpenAI", return_value=mock_client):
        yield mock_client


class TestQueryProcessorExpandAndFilter:
    """Tests for keyword expansion + metadata filtering."""

    @pytest.mark.asyncio
    async def test_expand_appends_keywords(self, mock_openai):
        from app.self_rag.query_processor import QueryProcessor
        from app.self_rag.config import QUERY_EXPANSION_ENABLED
        import app.self_rag.query_processor as qp_mod

        # Force enable expansion for this test
        with patch.object(qp_mod, "QUERY_EXPANSION_ENABLED", True), \
             patch.object(qp_mod, "METADATA_FILTER_ENABLED", True):
            qp = QueryProcessor(timeout=10)
            result = await qp.process("电商趋势")
            assert "电商" in result.expanded
            assert result.metadata_filter == {"doc_type": "report"}

    @pytest.mark.asyncio
    async def test_disable_expansion_returns_original(self, mock_openai):
        from app.self_rag.query_processor import QueryProcessor
        import app.self_rag.query_processor as qp_mod

        with patch.object(qp_mod, "QUERY_EXPANSION_ENABLED", False), \
             patch.object(qp_mod, "METADATA_FILTER_ENABLED", False), \
             patch.object(qp_mod, "HYDE_ENABLED", False), \
             patch.object(qp_mod, "QUERY_DECOMPOSITION_ENABLED", False):
            qp = QueryProcessor(timeout=10)
            result = await qp.process("电商趋势")
            assert result.expanded == "电商趋势"
            assert result.hyde_text == ""
            assert result.sub_queries == []
            assert result.metadata_filter is None

    @pytest.mark.asyncio
    async def test_llm_call_failure_graceful_degradation(self):
        from app.self_rag.query_processor import QueryProcessor
        import app.self_rag.query_processor as qp_mod

        with patch.object(qp_mod, "QUERY_EXPANSION_ENABLED", True), \
             patch.object(qp_mod, "METADATA_FILTER_ENABLED", True), \
             patch("app.self_rag.query_processor.OpenAI") as mock_openai_cls:
            mock_openai_cls.side_effect = Exception("Connection refused")
            qp = QueryProcessor(timeout=10)
            result = await qp.process("test query")
            # Falls back to original query
            assert result.expanded == "test query"
            assert result.metadata_filter is None


class TestQueryProcessorDecomposition:
    """Tests for query decomposition."""

    @pytest.mark.asyncio
    async def test_decompose_returns_sub_queries(self):
        from app.self_rag.query_processor import QueryProcessor
        import app.self_rag.query_processor as qp_mod

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="淘宝商业模式\n京东商业模式\n两者差异"))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        with patch.object(qp_mod, "QUERY_EXPANSION_ENABLED", False), \
             patch.object(qp_mod, "METADATA_FILTER_ENABLED", False), \
             patch.object(qp_mod, "HYDE_ENABLED", False), \
             patch.object(qp_mod, "QUERY_DECOMPOSITION_ENABLED", True), \
             patch("app.self_rag.query_processor.OpenAI", return_value=mock_client):
            qp = QueryProcessor(timeout=10)
            result = await qp.process("对比淘宝和京东")
            assert len(result.sub_queries) == 3
```


- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_self_rag/test_query_processor.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
cd deepsearch-agents && git add tests/test_self_rag/test_query_processor.py && git commit -m "test(rag): add QueryProcessor unit tests

Tests for expansion, graceful degradation on LLM failure,
and query decomposition

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2.4: Integrate QueryProcessor into `RAGEngine`

**Files:**
- Modify: `app/self_rag/engine.py`

- [ ] **Step 1: Add QueryProcessor import and field**

In `app/self_rag/engine.py`, add the import near the top (after ChromaDB imports):

```python
from app.self_rag.query_processor import QueryProcessor, ProcessedQuery
```

In `RAGEngine.__init__`, add after the reranker line:

```python
        # QueryProcessor: lazy-init on first query
        self._query_processor = None
```

- [ ] **Step 2: Add `_get_query_processor` helper**

```python
    def _get_query_processor(self) -> Optional[QueryProcessor]:
        """Lazy-init the query processor. Returns None if all features disabled."""
        from app.self_rag.config import (
            QUERY_EXPANSION_ENABLED,
            QUERY_DECOMPOSITION_ENABLED,
            HYDE_ENABLED,
            METADATA_FILTER_ENABLED,
        )
        if not any([
            QUERY_EXPANSION_ENABLED,
            QUERY_DECOMPOSITION_ENABLED,
            HYDE_ENABLED,
            METADATA_FILTER_ENABLED,
        ]):
            return None
        if self._query_processor is None:
            self._query_processor = QueryProcessor()
        return self._query_processor
```

- [ ] **Step 3: Add HyDE embedding retrieval support method**

Add to `RAGEngine`:

```python
    def _hyde_retrieve(self, kb_name: str, hyde_text: str) -> dict[str, int]:
        """Use HyDE-generated text for dense retrieval, return parent ranks."""
        collection = self.get_kb(kb_name)
        if collection is None or not hyde_text:
            return {}

        hyde_embedding = self._embed([hyde_text])[0]
        results = collection.query(query_embeddings=[hyde_embedding], n_results=TOP_K)
        metadatas = results.get("metadatas", [[]])[0]

        ranks: dict[str, int] = {}
        for rank, meta in enumerate(metadatas):
            if meta and meta.get("parent_id"):
                pid = meta["parent_id"]
                if pid not in ranks:
                    ranks[pid] = rank
        return ranks
```

- [ ] **Step 4: Modify `query()` to use QueryProcessor and HyDE**

In the `query()` method, insert after the `collection is None` check and before the dense retrieval section. Replace the existing dense/BM25/RRF block with an async-aware version.

The key change: wrap the top of `query()` to process the query first, then handle sub-queries + HyDE.

Add this block right after the `collection is None` check (after line 437):

```python
        # ── Phase 2: Query Processing ──
        import asyncio as _asyncio
        qp = self._get_query_processor()
        processed: Optional[ProcessedQuery] = None
        if qp is not None:
            try:
                loop = _asyncio.get_event_loop()
            except RuntimeError:
                loop = _asyncio.new_event_loop()
                _asyncio.set_event_loop(loop)
            processed = loop.run_until_complete(qp.process(question))

        # Determine the effective query for dense retrieval
        effective_question = processed.expanded if processed else question

        # If query was decomposed, run retrieval for each sub-query and merge
        all_dense_ranks: dict[str, int] = {}
        all_bm25_ranks: dict[str, int] = {}

        queries_to_run = [effective_question]
        if processed and processed.sub_queries:
            queries_to_run.extend(processed.sub_queries)

        for q in queries_to_run:
            # --- 稠密路 ---
            q_embedding = self._embed([q])[0]
            dense_results = collection.query(query_embeddings=[q_embedding], n_results=TOP_K)
            dense_metadatas = dense_results.get("metadatas", [[]])[0]

            for rank, meta in enumerate(dense_metadatas):
                if meta and meta.get("parent_id"):
                    pid = meta["parent_id"]
                    if pid not in all_dense_ranks:
                        all_dense_ranks[pid] = rank

            # --- BM25 路 ---
            if BM25_ENABLED:
                bm25_results = self._bm25_search(kb_name, q, BM25_TOP_K)
                for rank, (parent_id, _score) in enumerate(bm25_results):
                    if parent_id not in all_bm25_ranks:
                        all_bm25_ranks[parent_id] = rank

            # --- HyDE: use hypothetical answer for extra dense retrieval ---
            if processed and processed.hyde_text:
                hyde_ranks = self._hyde_retrieve(kb_name, processed.hyde_text)
                for pid, rank in hyde_ranks.items():
                    if pid not in all_dense_ranks:
                        all_dense_ranks[pid] = rank

        # --- RRF 融合 ---
        if all_bm25_ranks:
            merged_parent_ids = self._rrf_fusion(all_dense_ranks, all_bm25_ranks, RRF_K)
        else:
            merged_parent_ids = sorted(all_dense_ranks, key=all_dense_ranks.get)
```

Then remove the old dense retrieval + BM25 + RRF blocks (lines 439-473 in the original), since they are replaced by the block above.

**Important**: Keep the original variable names for the fallback path — if `qp is None`, `effective_question` is `question` and `queries_to_run` is `[question]`, so behavior is identical.

- [ ] **Step 5: Keep the fallback when no dense results**

After the RRF fusion block, add:

```python
        if not merged_parent_ids:
            return "未在知识库中找到相关内容。"
```

Remove the old `if not dense_docs and not BM25_ENABLED` check (original line 446-447) since it's now covered.

- [ ] **Step 6: Verify no syntax errors**

Run: `uv run python -c "from app.self_rag.engine import RAGEngine; print('OK')"`
Expected: `OK`

- [ ] **Step 7: Run all existing tests**

Run: `uv run pytest tests/ -v`
Expected: ALL tests PASS

- [ ] **Step 8: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/engine.py && git commit -m "feat(rag): integrate QueryProcessor into retrieval pipeline

Query expansion, decomposition, HyDE, and metadata filtering
inserted before dense+BM25 retrieval. All features toggle-able.
When all disabled, behavior is identical to pre-change.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2.5: Phase 2 Code Review & Gate Check

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL tests PASS

- [ ] **Step 2: Run code review**

Invoke: `/code-review` on the Phase 2 diff. Fix any findings.

- [ ] **Step 3: Verify toggle-off regression**

Run:
```bash
SELF_RAG_QUERY_EXPANSION=false SELF_RAG_METADATA_FILTER=false uv run pytest tests/ -v
```
Expected: All tests still PASS

- [ ] **Step 4: Verify app starts**

Run: `timeout 5 uv run python -m app.api.server 2>&1 || true`
Expected: No import errors

- [ ] **Step 5: Mark Phase 2 complete**

Phase 2 done. Proceed to Phase 3.

---

## Phase 3: Iterative Retrieval (Self-RAG Style)

### Task 3.1: Add iterative retrieval config to `app/self_rag/config.py`

**Files:**
- Modify: `app/self_rag/config.py`

- [ ] **Step 1: Add config block**

```python
# ── Phase 3: Iterative Retrieval ──
ITERATIVE_RETRIEVAL_ENABLED = os.getenv("SELF_RAG_ITERATIVE", "true").lower() != "false"
ITERATIVE_MAX_ROUNDS = int(os.getenv("SELF_RAG_ITERATIVE_MAX_ROUNDS", "3"))
ITERATIVE_SUFFICIENCY_MIN_SCORE = int(os.getenv("SELF_RAG_ITERATIVE_MIN_SCORE", "3"))
```

- [ ] **Step 2: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/config.py && git commit -m "feat(rag): add iterative retrieval config block

Phase 3 prep — toggles for Self-RAG style iterative retrieval

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3.2: Create `app/self_rag/iterative_retriever.py`

**Files:**
- Create: `app/self_rag/iterative_retriever.py`

- [ ] **Step 1: Write the IterativeRetriever module**

Write `app/self_rag/iterative_retriever.py`:

```python
"""
Iterative Retriever — Self-RAG style retrieval with sufficiency judgment.

After initial retrieval, an LLM judge evaluates whether results are sufficient.
If not, the query is rewritten and retrieval is retried (up to MAX_ROUNDS).
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

from app.self_rag.config import (
    ITERATIVE_MAX_ROUNDS,
    ITERATIVE_SUFFICIENCY_MIN_SCORE,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
)

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """Result from iterative retrieval.

    Attributes:
        parent_ids: Final list of parent document IDs.
        rounds: Number of retrieval rounds executed.
        sufficient: Whether the final results were judged sufficient.
        retrieval_log: List of per-round diagnostics.
    """

    parent_ids: list[str] = field(default_factory=list)
    rounds: int = 0
    sufficient: bool = True
    retrieval_log: list[dict] = field(default_factory=list)


class IterativeRetriever:
    """Wraps retrieval with sufficiency judgment and automatic query rewriting.

    Flow::

        Retrieve → Judge → [sufficient] → Return
                        → [insufficient] → Rewrite → Retrieve → ...

    Hard cap at ``MAX_ROUNDS``. On any LLM failure in the judge/rewrite
    steps, treats results as sufficient to avoid infinite loops.
    """

    JUDGE_PROMPT = (
        "你是一个检索质量评判专家。请根据以下信息判断检索结果是否充分回答了用户问题。\n\n"
        "评判维度（每项1-5分）：\n"
        "1. 相关性：检索到的片段与问题相关吗？\n"
        "2. 完整性：检索结果覆盖了问题的所有方面吗？\n"
        "3. 信息量：检索到的内容包含足够的细节吗？\n\n"
        "请严格按以下JSON格式回复：\n"
        '{{"relevance": 4, "completeness": 3, "informativeness": 4, '
        '"sufficient": true, "reason": "理由", "rewrite_suggestion": ""}}\n\n"
        "如果任一维度低于{min_score}分，sufficient应为false，并提供rewrite_suggestion。"
    )

    REWRITE_PROMPT = (
        "原始查询没有检索到足够的信息，请改写查询以获得更好的检索结果。\n\n"
        "原始查询：{original_query}\n"
        "检索到的不完整信息：{retrieved_snippets}\n"
        "改写原因：{reason}\n"
        "改写建议方向：{suggestion}\n\n"
        "请输出改写后的查询（只输出查询文本，不要加其他内容）："
    )

    def __init__(self, max_rounds: int = ITERATIVE_MAX_ROUNDS) -> None:
        self._max_rounds: int = max_rounds
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
        return self._client

    async def retrieve_with_judgment(
        self,
        query: str,
        do_retrieve,  # async callable: (str) -> list[str]
        do_get_texts,  # callable: (list[str]) -> list[str]
    ) -> RetrievalResult:
        """Run iterative retrieval with sufficiency judgment.

        Args:
            query: Original user query.
            do_retrieve: Async callable that takes a query string and returns
                a list of parent IDs.
            do_get_texts: Sync callable that takes a list of parent IDs and
                returns their full text.
        """
        result = RetrievalResult()

        current_query = query
        all_ids: list[str] = []
        all_snippets: list[str] = []
        seen_ids: set[str] = set()

        for round_num in range(1, self._max_rounds + 1):
            result.rounds = round_num

            # Retrieve
            ids = await do_retrieve(current_query)
            new_ids = [i for i in ids if i not in seen_ids]
            if new_ids:
                texts = do_get_texts(new_ids)
            else:
                texts = []

            all_ids.extend(new_ids)
            all_snippets.extend(texts)
            for i in new_ids:
                seen_ids.add(i)

            # Judge sufficiency
            try:
                judgment = await self._judge(query, all_snippets[:10])
            except Exception:
                logger.warning("Judge LLM call failed — treating as sufficient", exc_info=True)
                result.sufficient = True
                break

            result.retrieval_log.append({
                "round": round_num,
                "query": current_query,
                "new_ids": new_ids,
                "judgment": judgment,
            })

            if judgment.get("sufficient", True):
                result.sufficient = True
                break

            # Not sufficient — rewrite query
            if round_num < self._max_rounds:
                try:
                    current_query = await self._rewrite(
                        original_query=query,
                        retrieved_snippets="\n".join(all_snippets[:5]),
                        reason=judgment.get("reason", ""),
                        suggestion=judgment.get("rewrite_suggestion", ""),
                    )
                except Exception:
                    logger.warning("Rewrite LLM call failed", exc_info=True)
                    result.sufficient = False
                    break
            else:
                result.sufficient = False
                logger.info(
                    "Max rounds reached without sufficient results",
                    rounds=round_num,
                )

        result.parent_ids = all_ids
        return result

    async def _judge(self, query: str, snippets: list[str]) -> dict:
        """LLM judge evaluates retrieval sufficiency."""
        snippets_text = "\n---\n".join(
            f"[{i+1}] {s}" for i, s in enumerate(snippets)
        )
        prompt = self.JUDGE_PROMPT.format(min_score=ITERATIVE_SUFFICIENCY_MIN_SCORE)
        prompt += f"\n\n用户问题：{query}\n检索结果：\n{snippets_text}"

        response = await asyncio.to_thread(
            lambda: self.client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=300,
            )
        )
        content = response.choices[0].message.content or "{}"
        # Extract JSON from possible markdown wrapping
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            return {"sufficient": True, "reason": "JSON parse failed"}

    async def _rewrite(
        self,
        original_query: str,
        retrieved_snippets: str,
        reason: str,
        suggestion: str,
    ) -> str:
        """LLM rewrites query for better retrieval."""
        prompt = self.REWRITE_PROMPT.format(
            original_query=original_query,
            retrieved_snippets=retrieved_snippets,
            reason=reason,
            suggestion=suggestion,
        )

        response = await asyncio.to_thread(
            lambda: self.client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
            )
        )
        return (response.choices[0].message.content or original_query).strip()
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from app.self_rag.iterative_retriever import IterativeRetriever; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/iterative_retriever.py && git commit -m "feat(rag): add IterativeRetriever with sufficiency judgment

Self-RAG style: retrieve → judge → rewrite → retry (max 3 rounds).
LLM judge evaluates relevance/completeness/informativeness.
Hard cap prevents infinite loops. LLM failures → graceful degradation.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3.3: Write unit tests for IterativeRetriever

**Files:**
- Create: `tests/test_self_rag/test_iterative_retriever.py`

- [ ] **Step 1: Write iterative retriever tests**

Write `tests/test_self_rag/test_iterative_retriever.py`:

```python
"""Unit tests for IterativeRetriever."""

from unittest.mock import AsyncMock, MagicMock, patch

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

            # First call: judge says insufficient
            # Second call: judge says sufficient
            judge_responses = [
                MagicMock(
                    message=MagicMock(
                        content='{"relevance":2,"completeness":1,"informativeness":2,"sufficient":false,"reason":"不完整","rewrite_suggestion":"换个角度"}'
                    )
                ),
                MagicMock(
                    message=MagicMock(
                        content='{"relevance":4,"completeness":4,"informativeness":4,"sufficient":true,"reason":"OK","rewrite_suggestion":""}'
                    )
                ),
            ]
            rewrite_response = MagicMock(
                message=MagicMock(content="改写后的查询")
            )

            mock_client.chat.completions.create.side_effect = [
                MagicMock(choices=judge_responses[0:1]),   # judge round 1
                MagicMock(choices=[rewrite_response]),      # rewrite
                MagicMock(choices=judge_responses[1:2]),   # judge round 2
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
            # Always insufficient
            mock_response = MagicMock()
            mock_response.choices = [
                MagicMock(
                    message=MagicMock(
                        content='{"relevance":2,"completeness":2,"informativeness":2,"sufficient":false,"reason":"不够","rewrite_suggestion":"再试"}'
                    )
                )
            ]
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            ir = IterativeRetriever(max_rounds=2)
            result = await ir.retrieve_with_judgment(
                "test", do_retrieve, do_get_texts
            )

            assert result.rounds == 2
            assert result.sufficient is False
            assert len(result.parent_ids) == 2  # one per round, no dedup
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_self_rag/test_iterative_retriever.py -v`
Expected: All 3 tests PASS

- [ ] **Step 3: Commit**

```bash
cd deepsearch-agents && git add tests/test_self_rag/test_iterative_retriever.py && git commit -m "test(rag): add IterativeRetriever unit tests

Tests for first-round sufficient, insufficient→retry, and max-rounds exceeded

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3.4: Phase 3 Code Review & Gate Check

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL tests PASS

- [ ] **Step 2: Run code review**

Invoke: `/code-review` on the Phase 3 diff. Fix any findings.

- [ ] **Step 3: Verify app starts**

Run: `timeout 5 uv run python -m app.api.server 2>&1 || true`
Expected: No import errors

- [ ] **Step 4: Mark Phase 3 complete**

Phase 3 done. Proceed to Phase 4.

---

## Phase 4: Knowledge Graph Fusion

### Task 4.1: Install networkx dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add networkx to dependencies**

Run:
```bash
cd deepsearch-agents && uv add networkx
```

- [ ] **Step 2: Verify install**

Run: `uv run python -c "import networkx; print(networkx.__version__)"`
Expected: Version printed (e.g., `3.4`)

- [ ] **Step 3: Commit**

```bash
cd deepsearch-agents && git add pyproject.toml uv.lock && git commit -m "chore: add networkx dependency for KG module

Phase 4 prep — in-memory graph storage for knowledge graph fusion

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4.2: Add KG config to `app/self_rag/config.py`

**Files:**
- Modify: `app/self_rag/config.py`

- [ ] **Step 1: Add KG config block**

```python
# ── Phase 4: Knowledge Graph ──
import os as _kg_os
KG_ENABLED = _kg_os.getenv("SELF_RAG_KG_ENABLED", "false").lower() == "true"
KG_EXTRACT_MODEL = _kg_os.getenv("SELF_RAG_KG_MODEL", LLM_MODEL)
KG_MAX_ENTITIES_PER_CHUNK = int(_kg_os.getenv("SELF_RAG_KG_MAX_ENTITIES", "20"))
KG_RETRIEVAL_HOPS = int(_kg_os.getenv("SELF_RAG_KG_HOPS", "1"))
KG_FUSION_TOP_K = int(_kg_os.getenv("SELF_RAG_KG_FUSION_TOP_K", "5"))
KG_DATA_DIR = _kg_os.getenv(
    "SELF_RAG_KG_DATA_DIR",
    str(Path(__file__).resolve().parents[1] / "self_rag_data" / "graph"),
)
```

Note: Replace the `import os` usage with a direct path reference — the existing `os` import at the top of `config.py` already covers this. Just use the existing `os` and `Path`:

```python
# ── Phase 4: Knowledge Graph ──
KG_ENABLED = os.getenv("SELF_RAG_KG_ENABLED", "false").lower() == "true"
KG_EXTRACT_MODEL = os.getenv("SELF_RAG_KG_MODEL", LLM_MODEL)
KG_MAX_ENTITIES_PER_CHUNK = int(os.getenv("SELF_RAG_KG_MAX_ENTITIES", "20"))
KG_RETRIEVAL_HOPS = int(os.getenv("SELF_RAG_KG_HOPS", "1"))
KG_FUSION_TOP_K = int(os.getenv("SELF_RAG_KG_FUSION_TOP_K", "5"))
KG_DATA_DIR = os.getenv(
    "SELF_RAG_KG_DATA_DIR",
    str(Path(__file__).resolve().parents[1] / "self_rag_data" / "graph"),
)
```

- [ ] **Step 2: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/config.py && git commit -m "feat(rag): add Knowledge Graph configuration block

Phase 4 prep — toggles for KG entity extraction,
graph retrieval hops, and fusion parameters

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4.3: Create KG module files

**Files:**
- Create: `app/self_rag/kg/__init__.py`
- Create: `app/self_rag/kg/entity_extractor.py`
- Create: `app/self_rag/kg/graph_store.py`
- Create: `app/self_rag/kg/graph_builder.py`
- Create: `app/self_rag/kg/graph_retriever.py`
- Create: `app/self_rag/kg/kg_fusion.py`

- [ ] **Step 1: Create `app/self_rag/kg/__init__.py`**

```python
"""Knowledge Graph module — lightweight GraphRAG with LLM entity extraction."""
```

- [ ] **Step 2: Create `app/self_rag/kg/entity_extractor.py`**

```python
"""LLM-driven entity and relation extraction from document chunks."""

import asyncio
import json
import logging
from typing import Optional

from openai import OpenAI

from app.self_rag.config import (
    KG_EXTRACT_MODEL,
    KG_MAX_ENTITIES_PER_CHUNK,
    LLM_API_KEY,
    LLM_BASE_URL,
)

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = (
    "你是一个知识图谱构建专家。从以下文档片段中抽取实体和关系。\n\n"
    "要求：\n"
    "- 实体类型包括但不限于：人物、公司、产品、技术、概念、事件、地点、时间\n"
    "- 关系包括但不限于：属于、创建、收购、合作、竞争、影响、包含、位于\n"
    "- 最多抽取{max_entities}个实体\n\n"
    "请严格按以下JSON格式输出（不要加任何其他文字）：\n"
    '{{"entities":[{{"name":"实体名","type":"类型","attributes":{{"属性":"值"}}}}],'
    '"relations":[{{"subject":"主体","predicate":"关系","object":"客体"}}]}}\n\n'
    "文档片段：\n{text}"
)

ENTITIES_ONLY_PROMPT = (
    "从以下问题中提取提到的实体名称（人名、公司名、产品名、概念等）。\n"
    "请严格按以下JSON格式输出：\n"
    '{{"entities":["实体1","实体2"]}}\n\n'
    "问题：{query}"
)


class EntityExtractor:
    """Extracts entities and relations from text using LLM."""

    def __init__(self, model: str = KG_EXTRACT_MODEL) -> None:
        self._model: str = model
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
        return self._client

    async def extract_from_chunk(self, text: str) -> dict:
        """Extract entities and relations from a document chunk.

        Returns:
            Dict with ``"entities"`` and ``"relations"`` keys, or empty dict on failure.
        """
        prompt = EXTRACT_PROMPT.format(
            max_entities=KG_MAX_ENTITIES_PER_CHUNK,
            text=text[:3000],  # Truncate very long chunks
        )
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.client.chat.completions.create(
                        model=self._model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=1000,
                    )
                ),
                timeout=10.0,
            )
            content = response.choices[0].message.content or "{}"
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            return json.loads(content.strip())
        except Exception:
            logger.warning("Entity extraction failed for chunk", exc_info=True)
            return {"entities": [], "relations": []}

    async def extract_from_query(self, query: str) -> list[str]:
        """Extract entity names from a query for entity linking.

        Returns:
            List of entity name strings.
        """
        prompt = ENTITIES_ONLY_PROMPT.format(query=query)
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.client.chat.completions.create(
                        model=self._model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.0,
                        max_tokens=200,
                    )
                ),
                timeout=5.0,
            )
            content = response.choices[0].message.content or "{}"
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            data = json.loads(content.strip())
            return data.get("entities", [])
        except Exception:
            logger.warning("Query entity extraction failed", exc_info=True)
            return []
```

- [ ] **Step 3: Create `app/self_rag/kg/graph_store.py`**

```python
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
            # Ensure nodes exist
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
```

- [ ] **Step 4: Create `app/self_rag/kg/graph_builder.py`**

```python
"""Orchestrates building a knowledge graph from document chunks."""

import asyncio
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
```

- [ ] **Step 5: Create `app/self_rag/kg/graph_retriever.py`**

```python
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
        hops: int = KG_RETRIEVAL_HOPS,
        top_k: int = KG_FUSION_TOP_K,
    ) -> None:
        self._store: GraphStore = store
        self._extractor: EntityExtractor = extractor
        self._hops: int = hops
        self._top_k: int = top_k

    async def retrieve(self, query: str) -> list[dict]:
        """Retrieve graph context for a query.

        Returns:
            List of neighbor dicts, formatted for LLM context.
        """
        # Step 1: Entity linking — extract entities from query
        entity_names = await self._extractor.extract_from_query(query)
        if not entity_names:
            return []

        # Step 2: Find matching entities in graph
        all_neighbors: list[dict] = []
        seen: set[str] = set()

        for name in entity_names:
            matches = self._store.search_entity(name, fuzzy=True)
            for match in matches[:3]:  # Limit per entity
                neighbors = self._store.get_neighbors(match, hops=self._hops)
                for n in neighbors:
                    key = f"{n['entity']}|{n.get('relation','')}"
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
```

- [ ] **Step 6: Create `app/self_rag/kg/kg_fusion.py`**

```python
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
```

- [ ] **Step 7: Verify all imports**

Run: `uv run python -c "from app.self_rag.kg import GraphStore, EntityExtractor, GraphBuilder, GraphRetriever, KGFusion; print('OK')"`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/kg/ && git commit -m "feat(rag): add Knowledge Graph module (lightweight GraphRAG)

6 files: entity extraction, networkx graph store, graph builder,
graph retriever with entity linking + k-hop traversal, and KG fusion.
Zero external dependencies beyond networkx.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4.4: Write KG unit tests

**Files:**
- Create: `tests/test_self_rag/test_kg.py`

- [ ] **Step 1: Write KG tests**

Write `tests/test_self_rag/test_kg.py`:

```python
"""Unit tests for Knowledge Graph module."""

import json
import os
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
```

- [ ] **Step 2: Run KG tests**

Run: `uv run pytest tests/test_self_rag/test_kg.py -v`
Expected: 6 tests PASS

- [ ] **Step 3: Commit**

```bash
cd deepsearch-agents && git add tests/test_self_rag/test_kg.py && git commit -m "test(rag): add Knowledge Graph unit tests

Tests for GraphStore CRUD, neighbor traversal, fuzzy search,
save/load roundtrip, and entity extraction

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4.5: Phase 4 Code Review & Gate Check

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL tests PASS

- [ ] **Step 2: Run code review**

Invoke: `/code-review` on the Phase 4 diff. Fix any findings.

- [ ] **Step 3: Verify app starts**

Run: `timeout 5 uv run python -m app.api.server 2>&1 || true`
Expected: No import errors

- [ ] **Step 4: Mark Phase 4 complete**

Phase 4 done. Proceed to Phase 5.

---

## Phase 5: Incremental Indexing (SearchBackend Abstraction)

### Task 5.1: Add SearchBackend config to `app/self_rag/config.py`

**Files:**
- Modify: `app/self_rag/config.py`

- [ ] **Step 1: Add config block**

```python
# ── Phase 5: Search Backend ──
BM25_INCREMENTAL_ENABLED = os.getenv("SELF_RAG_BM25_INCREMENTAL", "false").lower() == "true"
BM25_FULL_REBUILD_THRESHOLD = int(os.getenv("SELF_RAG_BM25_REBUILD_THRESHOLD", "5000"))
EXTERNAL_SEARCH_BACKEND = os.getenv("SELF_RAG_SEARCH_BACKEND", "bm25")
```

- [ ] **Step 2: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/config.py && git commit -m "feat(rag): add SearchBackend configuration block

Phase 5 prep — backend type, rebuild threshold

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5.2: Create `app/self_rag/search_backend.py`

**Files:**
- Create: `app/self_rag/search_backend.py`

- [ ] **Step 1: Write the SearchBackend module**

Write `app/self_rag/search_backend.py`:

```python
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

    def add(self, doc_id: str, text: str, metadata: dict | None = None) -> None:
        tokens = self._tokenizer(text)
        self._doc_store[doc_id] = (tokens, metadata or {})
        self._rebuild_if_needed()

    def remove(self, doc_id: str) -> None:
        self._doc_store.pop(doc_id, None)
        self._rebuild_if_needed()

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        if self._model is None:
            return []

        tokenized_query = self._tokenizer(query)
        scores = self._model.get_scores(tokenized_query)

        # Pair scores with doc_ids
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
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from app.self_rag.search_backend import BM25Backend; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/search_backend.py && git commit -m "feat(rag): add SearchBackend abstraction with BM25 implementation

BM25Backend wraps existing BM25Okapi logic behind a common interface.
Future-proof extension point for Elasticsearch/Meilisearch backends.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5.3: Refactor `RAGEngine` to use `BM25Backend`

**Files:**
- Modify: `app/self_rag/engine.py`

- [ ] **Step 1: Add import and replace BM25 internals with backend**

In `app/self_rag/engine.py` imports, add:

```python
from app.self_rag.search_backend import BM25Backend
```

In `RAGEngine.__init__`, replace the BM25 cache dicts (lines 62-64):

Remove:
```python
        # BM25 索引缓存：按知识库名存储，延迟构建
        self._bm25_indices: dict[str, BM25Okapi] = {}
        self._bm25_doc_ids: dict[str, list[str]] = {}
        self._bm25_metadatas: dict[str, list[dict]] = {}
```

Replace with:
```python
        # BM25 索引缓存：按知识库名存储 BM25Backend 实例，延迟构建
        self._bm25_backends: dict[str, BM25Backend] = {}
```

- [ ] **Step 2: Replace `_invalidate_bm25`, `_rebuild_bm25`, `_bm25_search`**

Replace the three BM25 methods (lines 125-188) with:

```python
    # ---- BM25 index management (via SearchBackend) ----

    def _invalidate_bm25(self, kb_name: str) -> None:
        """清除指定 KB 的 BM25 缓存，迫使下次查询时重建。"""
        self._bm25_backends.pop(kb_name, None)

    def _get_bm25_backend(self, kb_name: str) -> BM25Backend:
        """Get or build the BM25Backend for a knowledge base."""
        if kb_name in self._bm25_backends:
            return self._bm25_backends[kb_name]

        backend = BM25Backend(tokenizer=self._tokenize)
        collection = self.get_kb(kb_name)
        if collection is not None:
            results = collection.get()
            if results and results.get("documents"):
                docs = results["documents"]
                ids = results["ids"]
                metadatas = results.get("metadatas") or [{} for _ in range(len(docs))]
                for doc_id, doc_text, meta in zip(ids, docs, metadatas):
                    backend.add(doc_id, doc_text, meta)

        self._bm25_backends[kb_name] = backend
        return backend

    def _bm25_search(self, kb_name: str, query: str, top_k: int) -> list[tuple]:
        """BM25 关键词检索。

        :return: [(parent_id, bm25_score), ...] 按分数降序排列
        """
        backend = self._get_bm25_backend(kb_name)
        if backend.size() == 0:
            return []

        scored_docs = backend.search(query, top_k)

        # Map child doc_ids to parent_ids (dedup by parent_id)
        results = []
        seen_parents = set()
        for doc_id, score in scored_docs:
            meta = backend.pop_doc_metadata(doc_id)
            parent_id = meta.get("parent_id") if meta else None
            if parent_id and parent_id not in seen_parents:
                results.append((parent_id, float(score)))
                seen_parents.add(parent_id)
            if len(results) >= top_k:
                break

        return results
```

- [ ] **Step 3: Remove `rank_bm25` import since BM25Backend handles it**

Remove `from rank_bm25 import BM25Okapi` from the imports in `engine.py` (line 23), since BM25Okapi is now only used inside `search_backend.py`.

- [ ] **Step 4: Verify no syntax errors**

Run: `uv run python -c "from app.self_rag.engine import RAGEngine; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Run all existing tests**

Run: `uv run pytest tests/ -v`
Expected: ALL tests PASS

- [ ] **Step 6: Commit**

```bash
cd deepsearch-agents && git add app/self_rag/engine.py && git commit -m "refactor(rag): replace inline BM25 with SearchBackend abstraction

BM25Backend now handles BM25 index management via the SearchBackend
interface. RAGEngine delegates to backend instead of managing
BM25Okapi instances directly. Behavior is identical.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5.4: Add KB stats API endpoint

**Files:**
- Modify: `app/api/server.py`

- [ ] **Step 1: Add stats endpoint**

After the existing `@app.delete("/api/kb/{kb_name}")` block (around line 1193 in server.py), add:

```python
@app.get("/api/kb/{kb_name}/stats")
async def get_kb_stats(
    kb_name: str,
    user: UserInfo = Depends(get_current_user),
):
    """获取知识库统计信息。

    返回文档数、chunk数、BM25索引大小和知识图谱实体数。
    """
    engine = get_rag_engine()
    collection = engine.get_kb(kb_name)
    if collection is None:
        raise HTTPException(status_code=404, detail=f"知识库 '{kb_name}' 不存在")

    # 组权限校验
    if not engine.check_kb_access(kb_name, user.group_id):
        raise HTTPException(status_code=403, detail="无权访问该知识库")

    results = collection.get()
    chunk_count = len(results.get("ids", [])) if results else 0

    # BM25 backend size
    backend = engine._get_bm25_backend(kb_name)
    bm25_size = backend.size()

    # KG entity count (if KG enabled)
    graph_entities = 0
    try:
        from app.self_rag.config import KG_ENABLED, KG_DATA_DIR
        if KG_ENABLED:
            from app.self_rag.kg.graph_store import GraphStore
            gs = GraphStore(kb_name, KG_DATA_DIR)
            gs.load()
            graph_entities = gs.node_count()
    except Exception:
        pass

    # Document count from doc_meta.json
    import json
    from pathlib import Path
    from app.self_rag.config import DOC_STORE_DIR
    doc_count = 0
    meta_file = Path(DOC_STORE_DIR) / kb_name / "doc_meta.json"
    if meta_file.exists():
        try:
            records = json.loads(meta_file.read_text(encoding="utf-8"))
            doc_count = len(records)
        except Exception:
            pass

    return {
        "kb_name": kb_name,
        "doc_count": doc_count,
        "chunk_count": chunk_count,
        "bm25_size": bm25_size,
        "graph_entities": graph_entities,
    }
```

- [ ] **Step 2: Verify app starts**

Run: `timeout 5 uv run python -m app.api.server 2>&1 || true`
Expected: No import errors, "Uvicorn running" message

- [ ] **Step 3: Write API test**

Add to `tests/test_api/test_kb.py`:

```python
    async def test_get_kb_stats_authenticated(self, test_app, mock_rag_engine, auth_headers):
        """获取知识库统计信息。"""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": ["a", "b", "c"], "documents": ["d1", "d2", "d3"]}
        mock_rag_engine.get_kb.return_value = mock_collection
        mock_rag_engine.check_kb_access.return_value = True

        # Mock BM25 backend
        mock_backend = MagicMock()
        mock_backend.size.return_value = 3
        mock_rag_engine._get_bm25_backend.return_value = mock_backend

        resp = await test_app.get("/api/kb/test-kb/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["kb_name"] == "test-kb"
        assert data["chunk_count"] == 3
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: ALL tests PASS

- [ ] **Step 5: Commit**

```bash
cd deepsearch-agents && git add app/api/server.py tests/test_api/test_kb.py && git commit -m "feat(rag): add KB stats API endpoint

GET /api/kb/{name}/stats returns doc_count, chunk_count,
bm25_size, and graph_entities

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5.5: Phase 5 Code Review & Final Gate Check

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL tests PASS

- [ ] **Step 2: Run code review**

Invoke: `/code-review` on the Phase 5 diff. Fix any findings.

- [ ] **Step 3: Run full regression — all features OFF**

```bash
SELF_RAG_RERANK_ENABLED=false \
SELF_RAG_QUERY_EXPANSION=false \
SELF_RAG_METADATA_FILTER=false \
SELF_RAG_ITERATIVE=false \
SELF_RAG_KG_ENABLED=false \
uv run pytest tests/ -v
```
Expected: ALL tests PASS

- [ ] **Step 4: Verify app starts cleanly**

Run: `timeout 5 uv run python -m app.api.server 2>&1 || true`
Expected: No errors, app initializes

- [ ] **Step 5: Final commit and push**

```bash
cd deepsearch-agents && git push origin main
```

---

## Summary

| Phase | New Files | Modified Files | Test Files |
|---|---|---|---|
| Phase 1 (Reranker) | `reranker.py` | `config.py`, `engine.py` | `test_reranker.py` |
| Phase 2 (QueryProcessor) | `query_processor.py` | `config.py`, `engine.py` | `test_query_processor.py` |
| Phase 3 (Iterative) | `iterative_retriever.py` | `config.py` | `test_iterative_retriever.py` |
| Phase 4 (KG) | `kg/` (6 files) | `config.py`, `pyproject.toml` | `test_kg.py` |
| Phase 5 (SearchBackend) | `search_backend.py` | `config.py`, `engine.py`, `server.py`, `test_kb.py` | (integrated) |

**Total: 12 new source files, 4 modified files, 4 new test files**

### Rollback Strategy

Every feature has a config toggle (env var). To disable any phase:
```bash
# Phase 1
SELF_RAG_RERANK_ENABLED=false
# Phase 2
SELF_RAG_QUERY_EXPANSION=false SELF_RAG_METADATA_FILTER=false
# Phase 3
SELF_RAG_ITERATIVE=false
# Phase 4
SELF_RAG_KG_ENABLED=false
# Phase 5 (no behavioral change — pure refactor)
```
