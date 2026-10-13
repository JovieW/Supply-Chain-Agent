from __future__ import annotations

import os
from typing import Protocol, Sequence

from .config import RAGSettings


class Embedder(Protocol):
    @property
    def identity(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class LocalBGEEmbedder:
    """Local multilingual embeddings; enterprise data never leaves the machine."""

    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Local embeddings require sentence-transformers. Run: pip install -r requirements.txt"
            ) from exc
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    @property
    def identity(self) -> str:
        return f"sentence-transformers:{self.model_name}"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # 生成向量
        vectors = self._model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        query = f"为这个句子生成表示以用于检索相关文章：{text}"
        return self.embed_documents([query])[0]


class DashScopeEmbedder:
    """DashScope's OpenAI-compatible embedding endpoint."""

    def __init__(self, model_name: str) -> None:
        from langchain_openai import OpenAIEmbeddings

        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("RAG_EMBEDDING_PROVIDER=dashscope requires DASHSCOPE_API_KEY")
        self.model_name = model_name
        self._client = OpenAIEmbeddings(
            model=model_name,
            api_key=api_key,
            base_url=os.getenv(
                "DASHSCOPE_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
        )

    @property
    def identity(self) -> str:
        return f"dashscope:{self.model_name}"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._client.embed_documents(list(texts))

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text)


def create_embedder(settings: RAGSettings) -> Embedder:
    if settings.embedding_provider == "local":
        return LocalBGEEmbedder(settings.embedding_model)
    if settings.embedding_provider == "dashscope":
        return DashScopeEmbedder(settings.embedding_model)
    raise ValueError(
        "Unsupported RAG_EMBEDDING_PROVIDER. Expected 'local' or 'dashscope', "
        f"got {settings.embedding_provider!r}."
    )
