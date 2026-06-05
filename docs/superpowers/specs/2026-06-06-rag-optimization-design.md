# RAG Optimization Design — DeepAgents 检索增强生成优化

**Date:** 2026-06-06  
**Status:** Design Approved  
**Phases:** 5 (independent, sequential)

---

## Overview

### Current State

The RAG engine in `app/self_rag/engine.py` uses a dual-path retrieval pipeline:
- **Dense**: `BAAI/bge-small-zh-v1.5` (512-dim) → ChromaDB child-chunk cosine similarity (top-4)
- **Sparse**: jieba tokenization → BM25Okapi (top-10)
- **Fusion**: Reciprocal Rank Fusion (k=60) → top-4 parent chunks → LLM answer
- **Chunking**: RecursiveCharacterTextSplitter with parent (1000 chars) / child (200 chars, overlap 50)

### What's Missing

| Capability | Status | Priority |
|---|---|---|
| Cross-Encoder Reranking | ❌ | P0 |
| Query Rewriting / Expansion | ❌ | P1 |
| HyDE (Hypothetical Document Embedding) | ❌ | P1 |
| Metadata Filtering / Self-Querying | ❌ | P1 |
| Iterative Retrieval (Self-RAG style) | ❌ | P2 |
| Knowledge Graph Fusion | ❌ | P3 |
| Incremental Indexing | ❌ | P5 |

### Design Principles

1. **Toggle-able components** — each module can be enabled/disabled independently via config; if one fails, others keep working
2. **Uniform interface** — every new component follows `async def process(query, candidates, context) -> processed_result`
3. **Minimal surgery** — existing dual-path retrieval + RRF logic is NOT rewritten; new modules wrap around it
4. **Phase independence** — each phase delivers a complete, shippable improvement; phases can be deployed independently

---

## Target Architecture

```
User Query
  │
  ▼
┌──────────────────────────────────────────────────────┐
│                 Phase 2: QueryProcessor               │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐   │
│  │KeywordExpand  │→│QueryDecompose│→│   HyDE     │   │
│  └──────────────┘  └──────────────┘  └───────────┘   │
│         │                 │                │          │
│         ▼                 ▼                ▼          │
│    expanded query    sub-queries    hypothetical doc  │
└──────────────────────────────────────────────────────┘
  │
  ▼ (each sub-query independently traverses below)
┌──────────────────────────────────────────────────────┐
│            Existing: Dual-Path Retrieval + RRF        │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐   │
│  │  Dense   │  │   BM25   │  │   RRF Fusion      │   │
│  └──────────┘  └──────────┘  └───────────────────┘   │
│                                   │                   │
│                                   ▼                   │
│                            top-K×N candidates         │
└──────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────┐
│             Phase 1: Cross-Encoder Reranker           │
│         Re-rank candidates → top-M final results       │
└──────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────┐
│              Phase 2: Metadata Filter                  │
│    LLM extracts filter conditions → ChromaDB where     │
└──────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────┐
│            Phase 3: Iterative Retriever               │
│  Retrieve → Judge sufficiency → Rewrite if insufficient│
│                   (max 3 rounds)                      │
└──────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────┐
│           Phase 4: KG Fusion (independent)            │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐   │
│  │EntityExtr│→│GraphStore │→│GraphRetriever      │   │
│  └──────────┘  └──────────┘  └───────────────────┘   │
│                                       │               │
│         vector results + graph results → merge        │
└──────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────┐
│              Phase 5: Incremental Index                │
│   SearchBackend abstraction + BM25Backend refactor     │
│   + future ES/Meilisearch extension point             │
└──────────────────────────────────────────────────────┘
  │
  ▼
        LLM generates final answer
```

---

## Phase 1: Cross-Encoder Reranker

### Model: `BAAI/bge-reranker-v2-m3`

- Multilingual (Chinese + English), same BAAI ecosystem as existing embedding model
- ~1.5GB, runs on CPU; GPU accelerates
- Loaded via HuggingFace (same pattern as `SentenceTransformer`)

### Insertion Point

In `RAGEngine.query()`:
```
RRF Fusion → top-10 candidates
                   │  ← NEW: Reranker.rerank(query, candidates)
                   ▼
              top-4 re-ranked → fetch parent text → return
```

### New File: `app/self_rag/reranker.py`

```python
class Reranker:
    MODEL_NAME: str
    DEVICE: str
    TOP_K: int
    _model: CrossEncoder  # lazy-loaded

    async def rerank(self, query: str, candidates: list[dict]) -> list[dict]:
        """
        candidates: [{"id": str, "text": str, "score": float}, ...]
        returns: re-ranked candidates with "rerank_score" added
        """
```

### Config Additions (`app/self_rag/config.py`)

```python
RERANK_ENABLED: bool = True
RERANK_MODEL: str = "BAAI/bge-reranker-v2-m3"
RERANK_DEVICE: str = "cpu"
RERANK_TOP_K_INPUT: int = 10
RERANK_TOP_K_OUTPUT: int = 4
```

### Error Handling

| Failure | Behavior |
|---|---|
| Model load fails | Log warning, auto-fallback to no-reranker mode |
| Inference exception | Return original RRF-ranked results |
| Timeout (>5s) | Fallback to original results |

### Files Changed

| File | Action |
|---|---|
| `app/self_rag/reranker.py` | **New** |
| `app/self_rag/config.py` | Add reranker config block |
| `app/self_rag/engine.py` | Insert ~5 lines in `query()` after RRF |

### Tests

1. **Unit**: Mock CrossEncoder, verify sort order, top_k truncation
2. **Integration**: Real `bge-reranker-base` (lighter variant) on `demo_kb`, verify end-to-end
3. **Regression**: Disabled reranker → results identical to pre-change behavior

---

## Phase 2: Query-Side Enhancement (QueryProcessor)

### New File: `app/self_rag/query_processor.py`

Four sub-modules sharing one LLM infrastructure:

#### ① KeywordExpander

- **Strategy**: LLM extracts keywords + synonyms from query
- **Input**: `"电商行业的发展趋势是什么？"`
- **Output**: `"电商行业 电子商务 发展趋势 演变趋势 未来方向"`
- **Usage**: Expanded keywords appended to original query before embedding + BM25
- **Cost**: 1 cheap LLM call, ~300ms

#### ② QueryDecomposer

- **Strategy**: LLM judges complexity → decomposes complex queries into 2-3 sub-queries
- **Complexity signals**: multi-aspect, comparison, causal, multi-hop
- **Each sub-query**: independently runs full retrieval pipeline; results merged + deduplicated
- **Default**: OFF (extra LLM calls per sub-query)

#### ③ HyDEGenerator

- **Strategy**: LLM generates hypothetical answer → embed with bge-small-zh-v1.5 → use as additional retrieval vector
- **Fusion**: HyDE vector results enter RRF alongside original query results
- **Default**: OFF (1 extra LLM call + 1 extra embedding)

#### ④ MetadataFilter

- **Strategy**: LLM extracts structured filter conditions from query → ChromaDB `where` clause
- **Supported dimensions**: time range, doc type, entity name, category
- **Example**: `"2024年电商报告"` → `{"year": "2024", "doc_type": "report"}`
- **Merged with KeywordExpander**: Single LLM call for both (cost optimization)

### Config Additions

```python
QUERY_EXPANSION_ENABLED: bool = True
QUERY_DECOMPOSITION_ENABLED: bool = False
HYDE_ENABLED: bool = False
METADATA_FILTER_ENABLED: bool = True
QUERY_PROCESSOR_MODEL: str = "deepseek-chat"
```

### Error Handling

- Each sub-module has independent try-catch; failure in one doesn't affect others
- LLM timeout (5s) → fallback, skip that sub-module
- HyDE generates empty result → degrade to pure original query retrieval
- All modules off → behavior identical to pre-change

### Files Changed

| File | Action |
|---|---|
| `app/self_rag/query_processor.py` | **New** |
| `app/self_rag/config.py` | Add query processor config block |
| `app/self_rag/engine.py` | Prepend QueryProcessor before existing retrieval |

### Tests

1. **Unit**: Each sub-module with mock LLM (fixed returns)
2. **Integration**: Real LLM calls against `demo_kb`, compare retrieval hits with/without processor
3. **Regression**: All switches OFF → results identical to pre-change

---

## Phase 3: Iterative Retrieval (Self-RAG Style)

### Flow

```
Query (post-Phase-2) → Retrieve (dual + RRF + Reranker)
  │
  ▼
Judgment Node: LLM checks retrieval quality
  Input: query + retrieved top-K snippets
  Output: {sufficient: bool, reason: str, rewrite_suggestion: str}
  │
  ├── sufficient=true → proceed to answer generation
  │
  └── sufficient=false (round < 3)
        │
        ▼
      LLM rewrites query based on rewrite_suggestion
        │
        ▼
      Retrieve again → append to existing candidates → back to judge
```

### New File: `app/self_rag/iterative_retriever.py`

```python
class IterativeRetriever:
    MAX_ROUNDS: int = 3
    SUFFICIENCY_MIN_SCORE: int = 3  # 1-5 scale

    async def retrieve(
        self, query: str, engine: RAGEngine,
        query_processor: QueryProcessor | None,
    ) -> RetrievalResult:
        """
        Returns final results + retrieval log (rounds, scores, rewrites)
        """
```

### Judgment Dimensions (1-5 each)

| Dimension | What it checks |
|---|---|
| Relevance | Do the snippets relate to the question? |
| Completeness | Do they cover all aspects of the question? |
| Informativeness | Do they contain sufficient detail? |

Any dimension < 3 → `sufficient = false`

### Rewrite Strategy by Round

| Round | Strategy |
|---|---|
| 1st failure | Re-express query differently (synonym, angle shift) |
| 2nd failure | Identify missing dimensions from results, target those |
| 3rd | Force-end, return results with "info may be incomplete" warning |

### Config Additions

```python
ITERATIVE_RETRIEVAL_ENABLED: bool = True
ITERATIVE_MAX_ROUNDS: int = 3
ITERATIVE_SUFFICIENCY_MIN_SCORE: int = 3
```

### Error Handling

- Judge LLM fails → treat as sufficient, return existing results
- Rewrite LLM fails → end loop, return existing results
- All rounds exhausted → return results + warning annotation
- Worst case: degrades to single-pass retrieval

### LangGraph Integration (Optional)

Iterative retrieval is a natural fit for LangGraph (judgment as conditional edge). If integration is clean, implement as a subgraph; otherwise use simple `while` loop with the same public interface.

### Files Changed

| File | Action |
|---|---|
| `app/self_rag/iterative_retriever.py` | **New** |
| `app/self_rag/config.py` | Add iterative retrieval config |
| `app/self_rag/engine.py` | Wrap existing query logic in iterative loop |

### Tests

1. **Unit**: Mock judge returning sufficient/insufficient, verify loop control
2. **Integration**: Construct deliberately unanswerable query, verify graceful degradation
3. **Comparison**: Same query with/without iterative mode, compare result quality

---

## Phase 4: Knowledge Graph Fusion

### Architecture — Lightweight GraphRAG

Zero external dependencies. LLM-driven entity extraction + in-memory `networkx` graph + JSON persistence.

```
app/self_rag/kg/
├── __init__.py
├── entity_extractor.py    # LLM entity + relation extraction
├── graph_store.py          # networkx.DiGraph + JSON persistence
├── graph_builder.py        # Orchestrates doc → graph construction
├── graph_retriever.py      # Entity linking + k-hop traversal
└── kg_fusion.py            # Merge vector results + graph results
```

### Module Details

#### EntityExtractor (`entity_extractor.py`)

- **Input**: Document text (parent chunk, ~1000 chars)
- **Output**: Structured JSON with entities + relation triples
- **Trigger**: Async background task on document ingestion
- **Prompt**: Strict JSON output format
  ```json
  {
    "entities": [
      {"name": "阿里巴巴", "type": "公司", "attributes": {"行业": "电商"}}
    ],
    "relations": [
      {"subject": "阿里巴巴", "predicate": "收购", "object": "饿了么", "year": "2018"}
    ]
  }
  ```
- **Caching**: Results written alongside `doc_meta.json`; re-extraction skipped if unchanged

#### GraphStore (`graph_store.py`)

- **Backend**: `networkx.DiGraph` (in-memory) + JSON disk persistence
- **Path**: `app/self_rag_data/graph/{kb_name}.json`
- **Operations**:
  - `add_entities(entities)` — upsert nodes
  - `add_relations(relations)` — add edges
  - `get_neighbors(entity, hops=1|2)` — k-hop subgraph
  - `search_entity(name, fuzzy=True)` — entity lookup with fuzzy matching
  - `save(path)` / `load(path)` — persistence

#### GraphRetriever (`graph_retriever.py`)

- **Entity linking**: Extract entities from query (reuses EntityExtractor prompt, entities-only mode)
- **Subgraph retrieval**: 1-hop neighbors by default; expand to 2-hop if results < threshold
- **Formatting**: `"[阿里巴巴] 是 [电商公司]，与 [饿了么] 存在 [收购] 关系"`
- **Runs in parallel** with vector retrieval — no added serial latency

#### KGFusion (`kg_fusion.py`)

- Vector results + graph results fed to LLM together
- Prompt sections: `## 文档上下文\n...\n## 知识关联\n...`
- LLM autonomously synthesizes both information sources

### Config Additions

```python
KG_ENABLED: bool = False  # Default OFF, enable when doc count grows
KG_EXTRACT_MODEL: str = "deepseek-chat"
KG_MAX_ENTITIES_PER_CHUNK: int = 20
KG_RETRIEVAL_HOPS: int = 1
KG_FUSION_TOP_K: int = 5
```

### Build Strategy

**Recommendation A**: Auto-trigger on document ingestion, async background task, doesn't block ingestion API response.

### Error Handling

- Entity extraction fails for a chunk → skip that chunk, continue with others
- Graph JSON corrupted → rebuild from scratch on next ingestion
- Entity linking finds no entities → graph retrieval returns empty, vector-only fallback
- GraphStore load fails → initialize empty graph, log warning

### Files Changed

| File | Action |
|---|---|
| `app/self_rag/kg/__init__.py` | **New** |
| `app/self_rag/kg/entity_extractor.py` | **New** |
| `app/self_rag/kg/graph_store.py` | **New** |
| `app/self_rag/kg/graph_builder.py` | **New** |
| `app/self_rag/kg/graph_retriever.py` | **New** |
| `app/self_rag/kg/kg_fusion.py` | **New** |
| `app/self_rag/config.py` | Add KG config block |
| `app/self_rag/engine.py` | Add KG fusion integration in `query()` |

### Tests

1. **Unit**: EntityExtractor (mock LLM), GraphStore CRUD operations
2. **Integration**: Real LLM extraction on `demo_kb`, build graph, retrieve
3. **Fusion**: Same query "vector-only" vs "vector+graph" quality comparison

---

## Phase 5: Incremental Indexing (SearchBackend Abstraction)

### Problem

`BM25Okapi` (rank_bm25) does not support native incremental updates. Each `add`/`remove` requires rebuilding corpus statistics. For small document sets (<5000 docs), this rebuild takes <100ms and is not a real bottleneck.

### Solution: Abstraction + Future-Proofing

Rather than force incremental semantics onto BM25Okapi, abstract the search backend and keep the existing behavior for now.

### New File: `app/self_rag/search_backend.py`

```python
class SearchBackend(ABC):
    """Abstract interface for sparse retrieval backends"""

    @abstractmethod
    def add(self, doc_id: str, text: str) -> None: ...

    @abstractmethod
    def remove(self, doc_id: str) -> None: ...

    @abstractmethod
    def search(self, query: str, top_k: int) -> list[tuple[str, float]]: ...

    @abstractmethod
    def clear(self) -> None: ...


class BM25Backend(SearchBackend):
    """Current BM25Okapi implementation, wrapped in the interface"""

    def __init__(self):
        self._model: BM25Okapi | None = None
        self._doc_store: dict[str, list[str]] = {}

    def add(self, doc_id: str, text: str) -> None:
        """Append to doc_store, rebuild BM25 model (fast for <5000 docs)"""
        ...

    def remove(self, doc_id: str) -> None:
        """Remove from doc_store, rebuild BM25 model"""
        ...

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """Same behavior as current _bm25_search()"""
        ...


class ElasticsearchBackend(SearchBackend):
    """Future: native incremental indexing for large-scale KBs"""
    ...  # Not implemented in Phase 5, extension point only
```

### Config Additions

```python
BM25_INCREMENTAL_ENABLED: bool = False
BM25_FULL_REBUILD_THRESHOLD: int = 5000
EXTERNAL_SEARCH_BACKEND: str = "bm25"  # "bm25" | "elasticsearch" | "meilisearch"
```

### New API Endpoint

```
GET /api/kb/{kb_name}/stats
→ {doc_count: int, chunk_count: int, bm25_size: int, graph_entities: int}
```

### Files Changed

| File | Action |
|---|---|
| `app/self_rag/search_backend.py` | **New** — BM25Backend + SearchBackend ABC |
| `app/self_rag/config.py` | Add search backend config |
| `app/self_rag/engine.py` | Replace inline `_bm25_*` methods with backend delegation |
| `app/api/server.py` | Add `/api/kb/{name}/stats` endpoint |

### Tests

1. **Unit**: BM25Backend add/remove/search, verify results match pre-refactor behavior
2. **Regression**: All existing KB tests pass with no behavioral change
3. **Performance**: Benchmark BM25 rebuild time at 100/1000/5000/10000 docs

---

## Phase Dependency Graph

```
Phase 1 (Reranker) ─────────────────────┐
                                         ├── Phase 3 (Iterative) ──→ Done
Phase 2 (QueryProcessor) ───────────────┘
                                         
Phase 4 (KG Fusion) ────────────────────→ Done (independent)

Phase 5 (SearchBackend) ────────────────→ Done (independent)
```

### Recommended Execution Order

1. **Phase 1** first — simplest, highest ROI, builds confidence
2. **Phase 2** second — independent of Phase 1, larger surface area
3. **Phase 3** third — depends on both Phase 1 + 2 for reranker and query rewriting
4. **Phase 4** fourth — largest independent module, can even be parallelized with Phase 5
5. **Phase 5** last — pure refactoring, lowest risk

---

## Quality Gates (Per Phase)

Every phase MUST pass before proceeding to the next:

1. ✅ All new code has type annotations
2. ✅ Unit tests pass (pytest)
3. ✅ Integration tests pass (real models, demo_kb)
4. ✅ Regression: disa bling all new features → identical output to pre-change
5. ✅ Code review: separate CR agent reviews the diff
6. ✅ Manual verification: run the app, ask a query, verify results

---

## Risk Matrix

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Reranker model download fails (China network) | Medium | Low | HF mirror + fallback mode |
| QueryProcessor LLM calls increase latency too much | Medium | Medium | Toggle off expensive modules; async where possible |
| Iterative retrieval infinite loop | Low | High | Hard cap at 3 rounds + timeout |
| KG entity extraction produces garbage JSON | Medium | Medium | Strict JSON schema validation + retry + skip-on-fail |
| BM25Backend refactor breaks existing retrieval | Low | High | Extensive regression tests before merge |

---

## Config Summary: All New Toggles

```python
# Phase 1 — Reranker
RERANK_ENABLED: bool = True
RERANK_MODEL: str = "BAAI/bge-reranker-v2-m3"
RERANK_DEVICE: str = "cpu"
RERANK_TOP_K_INPUT: int = 10
RERANK_TOP_K_OUTPUT: int = 4

# Phase 2 — QueryProcessor
QUERY_EXPANSION_ENABLED: bool = True
QUERY_DECOMPOSITION_ENABLED: bool = False
HYDE_ENABLED: bool = False
METADATA_FILTER_ENABLED: bool = True
QUERY_PROCESSOR_MODEL: str = "deepseek-chat"

# Phase 3 — Iterative Retrieval
ITERATIVE_RETRIEVAL_ENABLED: bool = True
ITERATIVE_MAX_ROUNDS: int = 3
ITERATIVE_SUFFICIENCY_MIN_SCORE: int = 3

# Phase 4 — Knowledge Graph
KG_ENABLED: bool = False
KG_EXTRACT_MODEL: str = "deepseek-chat"
KG_MAX_ENTITIES_PER_CHUNK: int = 20
KG_RETRIEVAL_HOPS: int = 1
KG_FUSION_TOP_K: int = 5

# Phase 5 — Search Backend
BM25_INCREMENTAL_ENABLED: bool = False
BM25_FULL_REBUILD_THRESHOLD: int = 5000
EXTERNAL_SEARCH_BACKEND: str = "bm25"
```
