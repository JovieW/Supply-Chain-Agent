from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Sequence

from .config import RAGSettings
from .documents import KnowledgeChunk, discover_documents, load_and_chunk
from .embeddings import Embedder


INDEX_SCHEMA_VERSION = "4"


@dataclass(frozen=True)
class IndexStats:
    documents: int
    chunks: int
    added_or_updated: int
    deleted: int


class ChromaKnowledgeBase:
    """Persistent Chroma collection with incremental document synchronization."""

    def __init__(
        self,
        settings: RAGSettings,
        embedder: Embedder,
        allow_model_mismatch: bool = False,
    ) -> None:
        try:
            import chromadb
        except ImportError as exc:
            raise RuntimeError(
                "Vector knowledge base requires chromadb. Run: pip install -r requirements.txt"
            ) from exc
        self.settings = settings
        self.embedder = embedder
        self._lock = RLock()  # 创建线程锁，防止多个线程同时修改 Chroma。
        settings.persist_dir.mkdir(parents=True, exist_ok=True)  #创建存储目录
        self._client = chromadb.PersistentClient(path=str(settings.persist_dir))  # 创建持久化数据库连接。
        self._collection = self._client.get_or_create_collection(
            name=settings.collection_name,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": embedder.identity,
                "schema_version": INDEX_SCHEMA_VERSION,
            },
        )
        model = self._collection.metadata.get("embedding_model")
        schema_version = str(self._collection.metadata.get("schema_version", ""))
        if model != embedder.identity and not allow_model_mismatch:
            raise RuntimeError(
                f"Index uses {model!r}, but configuration uses {embedder.identity!r}. "
                "Rebuild it with: python -m src.rag.ingest --rebuild"
            )
        if schema_version != INDEX_SCHEMA_VERSION and not allow_model_mismatch:
            raise RuntimeError(
                f"Index schema is {schema_version!r}, expected {INDEX_SCHEMA_VERSION!r}. "
                "Rebuild it with: python -m src.rag.ingest --rebuild"
            )
        if self.count and not allow_model_mismatch:
            sample = self._collection.peek(limit=1)
            metadata_rows = sample.get("metadatas") or []
            required = {
                "document_id", "version", "status", "effective_from", "effective_to",
                "owner", "product_scope", "supplier_scope", "region_scope",
            }
            if not metadata_rows or required - set(metadata_rows[0] or {}):
                raise RuntimeError(
                    "Index chunks do not contain governance metadata. Rebuild with: "
                    "python -m src.rag.ingest --rebuild"
                )

    @property
    def count(self) -> int:
        return self._collection.count()

    def sync(self, rebuild: bool = False) -> IndexStats:
        with self._lock:
            return self._sync_unlocked(rebuild)

    # 实现知识库索引的增量同步
    def _sync_unlocked(self, rebuild: bool = False) -> IndexStats:
        if rebuild:  # 全量重建
            self._client.delete_collection(self.settings.collection_name)
            self._collection = self._client.create_collection(
                name=self.settings.collection_name,
                metadata={
                    "hnsw:space": "cosine",
                    "embedding_model": self.embedder.identity,
                    "schema_version": INDEX_SCHEMA_VERSION,
                },
            )

        paths = discover_documents(self.settings.knowledge_dir)  # 扫描当前知识文件
        current_sources = {
            path.relative_to(self.settings.knowledge_dir).as_posix() for path in paths
        }
        existing = self._collection.get(include=["metadatas"])  # 读取已有索引
        existing_by_source: dict[str, list[tuple[str, str]]] = {}  # 组织已有chunk
        for chunk_id, metadata in zip(existing["ids"], existing.get("metadatas") or []):
            if metadata:
                existing_by_source.setdefault(str(metadata["source"]), []).append(
                    (chunk_id, str(metadata.get("file_hash", "")))
                )

        # 删除源文件已经不存在的chunk
        stale_ids = [
            chunk_id
            for source, entries in existing_by_source.items()
            if source not in current_sources
            for chunk_id, _ in entries
        ]
        if stale_ids:  # 从chroma中删除
            self._collection.delete(ids=stale_ids)

        # 识别未变化、新增和修改文件：根据文件哈希判断是否变化
        changed_chunks: list[KnowledgeChunk] = []
        replaced_ids: list[str] = []
        for path in paths:
            chunks = load_and_chunk(  # 切块
                path,
                self.settings.knowledge_dir,
                self.settings.chunk_size,
                self.settings.chunk_overlap,
            )
            source = path.relative_to(self.settings.knowledge_dir).as_posix()
            indexed = existing_by_source.get(source, [])
            file_hash = chunks[0].file_hash if chunks else ""
            if indexed and all(old_hash == file_hash for _, old_hash in indexed):
                continue
            replaced_ids.extend(chunk_id for chunk_id, _ in indexed)
            changed_chunks.extend(chunks)

        if replaced_ids:
            self._collection.delete(ids=replaced_ids)
        self._upsert_batches(changed_chunks)
        return IndexStats(
            documents=len(paths),
            chunks=self.count,
            added_or_updated=len(changed_chunks),
            deleted=len(stale_ids) + len(replaced_ids),
        )

    def search(
        self, query: str, top_k: int, candidates: int | None = None,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if self.count == 0 or top_k <= 0:
                return []
            n_results = min(candidates or top_k, self.count)
            query_args = {
                "query_embeddings": [self.embedder.embed_query(query)],  # 生成query向量
                "n_results": n_results,
                "include": ["documents", "metadatas", "distances"],
            }
            if where:
                query_args["where"] = where
            result = self._collection.query(**query_args)
        rows = []
        for chunk_id, content, metadata, distance in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ):
            rows.append(
                {
                    "id": chunk_id,
                    "content": content,
                    "score": round(max(0.0, 1.0 - float(distance)), 4),  # 余弦相似性
                    **metadata,
                }
            )
        return rows

    def _upsert_batches(
        self, chunks: Sequence[KnowledgeChunk], batch_size: int = 64
    ) -> None:
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            documents = [chunk.content for chunk in batch]
            # Retrieval must understand what a short section is about.  Chroma
            # keeps the raw body for citations, while embeddings also receive
            # the document title, section heading, and governed type.
            embedding_documents = [
                (
                    f"文档标题：{chunk.title}\n"
                    f"章节：{chunk.section}\n"
                    f"文档类型：{chunk.document_type}\n"
                    f"正文：{chunk.content}"
                )
                for chunk in batch
            ]
            self._collection.upsert(
                ids=[chunk.id for chunk in batch],
                documents=documents,
                metadatas=[chunk.metadata() for chunk in batch],
                embeddings=self.embedder.embed_documents(embedding_documents),
            )
