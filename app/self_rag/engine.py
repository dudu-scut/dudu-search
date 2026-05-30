"""
自建 RAG 引擎

提供知识库管理、文档摄入、语义检索和 LLM 问答能力。
用 ChromaDB + sentence-transformers 替换 RAGFlow，无需外部服务。

文档拆分与检索策略：
- 拆分：LangChain RecursiveCharacterTextSplitter（中文优化分隔符：段落 → 句子 → 短句）
- 检索：父子文档 + 双路融合 — 小粒度子块做向量检索 + BM25 关键词检索，RRF 融合后回填父块喂 LLM
"""

import json
import os
import uuid
from pathlib import Path
from typing import Optional

import chromadb
import jieba
from chromadb.config import Settings as ChromaSettings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import OpenAI
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

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

# 中文优化的递归分隔符：优先按段落切，其次按句子，最后按字符
CN_SEPARATORS = ["\n\n", "\n", "。", ".", "；", ";", "！", "!", "？", "?", "，", ",", " ", ""]


class RAGEngine:
    """自建 RAG 引擎单例，封装知识库的增删查改和文档摄入、问答全流程。"""

    def __init__(self):
        os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
        os.makedirs(DOC_STORE_DIR, exist_ok=True)

        self._embedding_model = SentenceTransformer(EMBEDDING_MODEL)
        self._chroma_client = chromadb.PersistentClient(
            path=CHROMA_PERSIST_DIR,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._llm_client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

        # BM25 索引缓存：按知识库名存储，延迟构建
        self._bm25_indices: dict[str, BM25Okapi] = {}
        self._bm25_doc_ids: dict[str, list[str]] = {}
        self._bm25_metadatas: dict[str, list[dict]] = {}

    # ---- embedding ----

    def _embed(self, texts: list[str]) -> list[list[float]]:
        embeddings = self._embedding_model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        return embeddings.tolist()

    # ---- text splitting ----

    def _get_text_splitter(self, chunk_size: int) -> RecursiveCharacterTextSplitter:
        """创建中文优化的递归文本拆分器。

        按 CN_SEPARATORS 优先级依次尝试：先按段落 (\n\n) 切，
        段落超长则按句子 (。.)，句子超长则按短句 (；；；)，最终按字符。
        keep_separator=True 保证分隔符保留在 chunk 中，不丢失语义边界。
        """
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=CHUNK_OVERLAP,
            separators=CN_SEPARATORS,
            keep_separator=True,
        )

    # ---- document parsing ----

    @staticmethod
    def _parse_file(file_path: str) -> str:
        ext = Path(file_path).suffix.lower()
        if ext in (".md", ".txt"):
            return Path(file_path).read_text(encoding="utf-8")
        elif ext == ".pdf":
            try:
                import pypdf
            except ImportError:
                raise ImportError("需要安装 pypdf 来解析 PDF 文件")
            reader = pypdf.PdfReader(file_path)
            return "\n".join(
                page.extract_text() or "" for page in reader.pages
            )
        elif ext == ".docx":
            try:
                import docx
            except ImportError:
                raise ImportError("需要安装 python-docx 来解析 Word 文件")
            doc = docx.Document(file_path)
            return "\n".join(para.text for para in doc.paragraphs)
        else:
            raise ValueError(f"不支持的文件格式: {ext}")

    # ---- Chinese tokenization (jieba) ----

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """jieba 中文分词，用于 BM25 索引和检索。"""
        return list(jieba.cut(text))

    # ---- BM25 index management ----

    def _invalidate_bm25(self, kb_name: str) -> None:
        """清除指定 KB 的 BM25 缓存，迫使下次查询时重建。"""
        self._bm25_indices.pop(kb_name, None)
        self._bm25_doc_ids.pop(kb_name, None)
        self._bm25_metadatas.pop(kb_name, None)

    def _rebuild_bm25(self, kb_name: str) -> None:
        """从 ChromaDB 子块 collection 重建 BM25 索引。

        获取所有子块的文本、ID 和元数据，分词后构建 BM25Okapi。
        BM25 操作的是子块粒度（与稠密检索一致），融合时按 parent_id 聚合。
        """
        collection = self.get_kb(kb_name)
        if collection is None:
            return

        results = collection.get()
        if not results or not results.get("documents"):
            self._bm25_indices[kb_name] = None
            return

        docs = results["documents"]
        ids = results["ids"]
        metadatas = results.get("metadatas") or [{}] * len(docs)

        tokenized = [self._tokenize(doc) for doc in docs]
        self._bm25_indices[kb_name] = BM25Okapi(tokenized)
        self._bm25_doc_ids[kb_name] = list(ids)
        self._bm25_metadatas[kb_name] = list(metadatas)

    def _bm25_search(self, kb_name: str, query: str, top_k: int) -> list[tuple]:
        """BM25 关键词检索。

        :return: [(parent_id, bm25_score), ...] 按分数降序排列
        """
        if kb_name not in self._bm25_indices:
            self._rebuild_bm25(kb_name)

        index = self._bm25_indices.get(kb_name)
        if index is None:
            return []

        tokenized_query = self._tokenize(query)
        scores = index.get_scores(tokenized_query)

        doc_ids = self._bm25_doc_ids[kb_name]
        metadatas = self._bm25_metadatas[kb_name]

        # 按分数排序取 top_k，并映射到 parent_id
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results = []
        seen_parents = set()
        for idx, score in ranked:
            if score <= 0:
                continue
            meta = metadatas[idx] if idx < len(metadatas) else {}
            parent_id = meta.get("parent_id") if meta else None
            if parent_id and parent_id not in seen_parents:
                results.append((parent_id, float(score)))
                seen_parents.add(parent_id)
            if len(results) >= top_k:
                break

        return results

    # ---- RRF fusion ----

    @staticmethod
    def _rrf_fusion(
        dense_ranks: dict[str, int],
        bm25_ranks: dict[str, int],
        k: int = 60,
    ) -> list[str]:
        """Reciprocal Rank Fusion。

        对两路检索的排名做融合排序，公式：score(d) = Σ 1/(k + rank_i(d))
        rank 从 0 开始计，未出现的 doc 不参与该路计算。

        :param dense_ranks: {parent_id: rank} 稠密路排名
        :param bm25_ranks: {parent_id: rank} BM25 路排名
        :param k: RRF 平滑参数，默认 60
        :return: 按 RRF 分数降序排列的 parent_id 列表
        """
        rrf_scores: dict[str, float] = {}

        for parent_id, rank in dense_ranks.items():
            rrf_scores[parent_id] = rrf_scores.get(parent_id, 0.0) + 1.0 / (k + rank)

        for parent_id, rank in bm25_ranks.items():
            rrf_scores[parent_id] = rrf_scores.get(parent_id, 0.0) + 1.0 / (k + rank)

        sorted_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)
        return sorted_ids

    # ---- knowledge base management ----

    def create_kb(self, name: str, description: str = "", group_id: int | None = None) -> str:
        """创建知识库，同时创建子块 collection 和父块 collection。

        :param group_id: 用户组 ID，用于多租户隔离（可选）
        """
        safe_name = self._safe_collection_name(name)
        existing = [c.name for c in self._chroma_client.list_collections()]

        if safe_name in existing:
            raise ValueError(f"知识库 '{name}' 已存在")

        metadata = {"description": description, "display_name": name, "role": "children"}
        if group_id is not None:
            metadata["group_id"] = str(group_id)

        self._chroma_client.create_collection(
            name=safe_name,
            metadata=metadata,
        )
        parent_metadata = {"description": f"{description} (父块)", "display_name": name, "role": "parents"}
        if group_id is not None:
            parent_metadata["group_id"] = str(group_id)

        parent_name = self._parent_collection_name(name)
        self._chroma_client.create_collection(
            name=parent_name,
            metadata=parent_metadata,
        )
        return safe_name

    def list_kbs(self, group_id: int | None = None) -> list[dict]:
        """列出所有知识库的名称和描述（仅返回子块 collection，父块内部过滤）。

        :param group_id: 用户组 ID，非 None 时仅返回该组的知识库；None 时返回全部（管理员）
        """
        result = []
        for c in self._chroma_client.list_collections():
            if c.name.startswith("_") or c.name.endswith("_parents"):
                continue
            meta = self._get_collection_metadata(c.name)

            # 组过滤：仅当 group_id 指定时检查
            kb_group_id = meta.get("group_id")
            if group_id is not None:
                # 没有 group_id 元数据的旧知识库：仅管理员可见（group_id=None 时不进此分支）
                if kb_group_id is None:
                    continue
                if int(kb_group_id) != group_id:
                    continue

            result.append({
                "name": meta.get("display_name", c.name),
                "description": meta.get("description", ""),
                "kb_id": c.name,
            })
        return result

    def delete_kb(self, kb_name: str, group_id: int | None = None) -> None:
        """按名称删除知识库及其父块 collection。

        :param group_id: 用户组 ID，非 None 时校验所有权；None 时跳过校验（管理员）
        :raises PermissionError: 组 ID 不匹配
        :raises ValueError: 知识库不存在
        """
        safe_name = self._safe_collection_name(kb_name)
        collection = self.get_kb(kb_name)
        if collection is None:
            raise ValueError(f"知识库 '{kb_name}' 不存在")

        # 组所有权校验
        if group_id is not None:
            meta = self._get_collection_metadata(safe_name)
            kb_group_id = meta.get("group_id")
            if kb_group_id is not None and int(kb_group_id) != group_id:
                raise ValueError(f"无权删除知识库 '{kb_name}'（组不匹配）")

        self._chroma_client.delete_collection(name=safe_name)
        parent_name = self._parent_collection_name(kb_name)
        try:
            self._chroma_client.delete_collection(name=parent_name)
        except Exception:
            pass
        self._invalidate_bm25(kb_name)

    def get_kb(self, kb_name: str):
        """获取子块 ChromaDB collection 对象，不存在时返回 None。"""
        safe_name = self._safe_collection_name(kb_name)
        for c in self._chroma_client.list_collections():
            if c.name == safe_name:
                return c
        return None

    def check_kb_access(self, kb_name: str, group_id: int) -> bool:
        """校验指定 group_id 是否有权访问该知识库。

        :return: True 表示有权访问或 KB 无 group_id 元数据（旧数据兼容）
        """
        safe_name = self._safe_collection_name(kb_name)
        meta = self._get_collection_metadata(safe_name)
        kb_group_id = meta.get("group_id")
        if kb_group_id is None:
            # 旧知识库无 group_id，兼容放行
            return True
        return int(kb_group_id) == group_id

    def _parent_collection_name(self, kb_name: str) -> str:
        return f"{self._safe_collection_name(kb_name)}_parents"

    def _get_parent_collection(self, kb_name: str):
        """获取父块 collection，不存在时返回 None。"""
        parent_name = self._parent_collection_name(kb_name)
        for c in self._chroma_client.list_collections():
            if c.name == parent_name:
                return c
        return None

    # ---- document ingestion (parent-child strategy) ----

    def ingest_file(self, kb_name: str, file_path: str) -> int:
        """向指定知识库摄入文件。

        采用父子文档策略：
        1. 解析文件全文
        2. 用大粒度拆分器切出父块（~1000字，完整段落/章节）
        3. 每个父块再用小粒度拆分器切出子块（~200字，精确检索单元）
        4. 子块入主 collection（有 embedding，参与稠密检索 + BM25 检索）
        5. 父块入 _parents collection（无 embedding，仅做上下文回填）
        """
        collection = self.get_kb(kb_name)
        if collection is None:
            raise ValueError(f"知识库 '{kb_name}' 不存在，请先创建")

        text = self._parse_file(file_path)
        parent_splitter = self._get_text_splitter(PARENT_CHUNK_SIZE)
        child_splitter = self._get_text_splitter(CHILD_CHUNK_SIZE)

        parents = parent_splitter.split_text(text)
        if not parents:
            return 0

        parent_collection = self._get_parent_collection(kb_name)
        if parent_collection is None:
            raise ValueError(f"知识库 '{kb_name}' 的父块 collection 不存在，请重新创建知识库")

        file_name = Path(file_path).name
        total_children = 0

        for parent_idx, parent_text in enumerate(parents):
            parent_id = f"{kb_name}_p_{uuid.uuid4().hex[:12]}"

            children = child_splitter.split_text(parent_text)
            if not children:
                continue

            child_embeddings = self._embed(children)
            child_ids = [f"{kb_name}_{uuid.uuid4().hex[:12]}" for _ in children]
            child_metadatas = [
                {
                    "source": file_name,
                    "chunk_index": i,
                    "kb_name": kb_name,
                    "parent_id": parent_id,
                }
                for i in range(len(children))
            ]

            collection.add(
                ids=child_ids,
                embeddings=child_embeddings,
                documents=children,
                metadatas=child_metadatas,
            )
            total_children += len(children)

            parent_collection.add(
                ids=[parent_id],
                documents=[parent_text],
                metadatas=[{
                    "source": file_name,
                    "parent_index": parent_idx,
                    "kb_name": kb_name,
                    "child_count": len(children),
                }],
            )

        self._save_doc_meta(kb_name, file_name, total_children)
        # 摄入后失效 BM25 缓存，下次查询时自动重建
        self._invalidate_bm25(kb_name)
        return total_children

    def ingest_directory(self, kb_name: str, dir_path: str) -> dict:
        """摄入目录下所有支持的文件，返回每个文件的 chunk 数。"""
        supported = {".md", ".txt", ".pdf", ".docx"}
        results = {}
        for f in Path(dir_path).iterdir():
            if f.is_file() and f.suffix.lower() in supported:
                try:
                    n = self.ingest_file(kb_name, str(f))
                    results[f.name] = n
                except Exception as e:
                    results[f.name] = f"失败: {e}"
        return results

    # ---- query (dual-path retrieval: dense + BM25 → RRF) ----

    def query(self, kb_name: str, question: str) -> str:
        """在指定知识库中检索问答。

        双路检索 + RRF 融合：
        1. 稠密路：question embedding → ChromaDB.query(top_k) → dense parent ranks
        2. BM25 路：jieba 分词 → BM25.search(top_k) → bm25 parent ranks
        3. RRF 融合排序 → 取 top_k 个 parent_ids
        4. 回填父块文本 → LLM 生成答案
        """
        collection = self.get_kb(kb_name)
        if collection is None:
            return f"知识库 '{kb_name}' 不存在"

        # --- 稠密路：向量检索 ---
        q_embedding = self._embed([question])[0]
        dense_results = collection.query(query_embeddings=[q_embedding], n_results=TOP_K)

        dense_docs = dense_results.get("documents", [[]])[0]
        dense_metadatas = dense_results.get("metadatas", [[]])[0]

        if not dense_docs and not BM25_ENABLED:
            return "未在知识库中找到相关内容。"

        # 构建稠密路 parent rank（rank 从 0 开始）
        dense_ranks: dict[str, int] = {}
        for rank, meta in enumerate(dense_metadatas):
            if meta and meta.get("parent_id"):
                pid = meta["parent_id"]
                if pid not in dense_ranks:
                    dense_ranks[pid] = rank

        # --- BM25 路：关键词检索 ---
        bm25_ranks: dict[str, int] = {}
        if BM25_ENABLED:
            bm25_results = self._bm25_search(kb_name, question, BM25_TOP_K)
            for rank, (parent_id, _score) in enumerate(bm25_results):
                if parent_id not in bm25_ranks:
                    bm25_ranks[parent_id] = rank

        # --- RRF 融合 ---
        if bm25_ranks:
            merged_parent_ids = self._rrf_fusion(dense_ranks, bm25_ranks, RRF_K)
        else:
            # BM25 未启用或无结果时，仅用稠密路排序
            merged_parent_ids = sorted(dense_ranks, key=dense_ranks.get)

        # 取融合后的 top_k 个父块
        top_parent_ids = merged_parent_ids[:HYBRID_TOP_K]

        # --- 回填父块 ---
        parent_collection = self._get_parent_collection(kb_name)
        parent_texts = []
        sources = []

        if parent_collection and top_parent_ids:
            parent_results = parent_collection.get(ids=top_parent_ids)
            parent_docs = parent_results.get("documents", [])
            parent_metadatas = parent_results.get("metadatas", [])
            if parent_docs:
                parent_texts = list(parent_docs)
            for m in parent_metadatas:
                if m and m.get("source") and m["source"] not in sources:
                    sources.append(m["source"])

        # 兼容没有父块的旧数据：回退使用稠密路检索到的子块文本
        if not parent_texts:
            parent_texts = dense_docs
            for m in dense_metadatas:
                if m and m.get("source") and m["source"] not in sources:
                    sources.append(m["source"])

        context = "\n\n---\n\n".join(
            f"[片段 {i + 1}] {doc}" for i, doc in enumerate(parent_texts)
        )

        answer = self._generate_answer(question, context, sources)
        return answer

    def _generate_answer(self, question: str, context: str, sources: list[str]) -> str:
        system_prompt = (
            "你是一个专业的知识库问答助手。请根据提供的文档片段回答用户问题。\n"
            "要求：\n"
            "- 基于文档内容作答，不要编造信息\n"
            "- 如果文档内容不足以回答，请明确说明\n"
            "- 回答要准确、简洁、有条理"
        )

        user_prompt = (
            f"【用户问题】\n{question}\n\n"
            f"【参考文档片段】\n{context}\n\n"
            f"【参考来源】\n" + "\n".join(f"- {s}" for s in sources)
        )

        try:
            response = self._llm_client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"LLM 调用失败: {e}"

    # ---- helpers ----

    @staticmethod
    def _safe_collection_name(name: str) -> str:
        return name.strip().replace(" ", "_").lower()

    def _get_collection_metadata(self, safe_name: str) -> dict:
        try:
            c = self._chroma_client.get_collection(name=safe_name)
            return c.metadata or {}
        except Exception:
            return {}

    def _save_doc_meta(self, kb_name: str, file_name: str, chunk_count: int) -> None:
        meta_dir = Path(DOC_STORE_DIR) / kb_name
        meta_dir.mkdir(parents=True, exist_ok=True)
        meta_file = meta_dir / "doc_meta.json"
        records = []
        if meta_file.exists():
            try:
                records = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError):
                records = []
        records.append({"file": file_name, "chunks": chunk_count})
        meta_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


_engine: Optional[RAGEngine] = None


def get_rag_engine() -> RAGEngine:
    """获取全局 RAG 引擎单例，首次访问时加载模型。"""
    global _engine
    if _engine is None:
        _engine = RAGEngine()
    return _engine
