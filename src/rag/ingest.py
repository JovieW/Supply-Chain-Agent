from __future__ import annotations

import argparse

from .config import RAGSettings
from .embeddings import create_embedder
from .vector_store import ChromaKnowledgeBase


def build_index(rebuild: bool = False):  # 创建知识库 并同步文档
    settings = RAGSettings.from_env()
    store = ChromaKnowledgeBase(  # 创建 Chroma 知识库对象
        settings,
        create_embedder(settings),  # 创建 embedding 模型
        allow_model_mismatch=rebuild,  # 删除旧索引重新建
    )
    return store.sync(rebuild=rebuild)  # 同步知识库


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the enterprise vector knowledge base")  # 创建参数解析器
    parser.add_argument("--rebuild", action="store_true", help="drop and recreate the index")
    args = parser.parse_args()
    stats = build_index(rebuild=args.rebuild)  # 构建索引
    print(
        f"Indexed {stats.documents} documents / {stats.chunks} chunks "
        f"(updated {stats.added_or_updated}, deleted {stats.deleted})."
    )


if __name__ == "__main__":
    main()
