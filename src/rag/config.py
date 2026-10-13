from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RAGSettings:
    """Runtime settings for the enterprise knowledge base."""

    knowledge_dir: Path
    persist_dir: Path
    collection_name: str
    embedding_provider: str
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    candidate_multiplier: int
    min_score: float = 0.25

    @classmethod
    def from_env(cls) -> "RAGSettings":
        provider = os.getenv("RAG_EMBEDDING_PROVIDER", "local").lower().strip()
        default_model = "text-embedding-v4" if provider == "dashscope" else "BAAI/bge-small-zh-v1.5"
        return cls(
            knowledge_dir=Path(os.getenv("RAG_KNOWLEDGE_DIR", PROJECT_ROOT / "data" / "knowledge")).resolve(),
            persist_dir=Path(os.getenv("RAG_PERSIST_DIR", PROJECT_ROOT / "data" / "vector_store")).resolve(),
            collection_name=os.getenv("RAG_COLLECTION_NAME", "enterprise_knowledge"),
            embedding_provider=provider,
            embedding_model=os.getenv("RAG_EMBEDDING_MODEL", default_model),
            chunk_size=int(os.getenv("RAG_CHUNK_SIZE", "700")),
            chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", "100")),
            candidate_multiplier=int(os.getenv("RAG_CANDIDATE_MULTIPLIER", "4")),
            min_score=float(os.getenv("RAG_MIN_SCORE", "0.25")),
        )
