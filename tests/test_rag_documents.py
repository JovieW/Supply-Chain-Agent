from __future__ import annotations

import tempfile
import unittest
import importlib.util
import math
from pathlib import Path

from src.rag.documents import discover_documents, load_and_chunk
from src.rag.retriever import _build_scenario_context, format_retrieved_context


class FakeEmbedder:
    identity = "test:keyword-v1"

    def _embed(self, text: str) -> list[float]:
        vector = [
            float(text.count("库存") + text.count("补货")),
            float(text.count("合同") + text.count("交付")),
            float(text.count("延误") + text.count("运输")),
        ]
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts):
        return [self._embed(text) for text in texts]

    def embed_query(self, text):
        return self._embed(text)


class DocumentIngestionTests(unittest.TestCase):
    def test_markdown_metadata_and_stable_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_dir = root / "contracts"
            contract_dir.mkdir()
            path = contract_dir / "supplier_contract.md"
            path.write_text(
                "# 供应合同\n\n## 交付条款\n\n供应商必须在七天内交付。",
                encoding="utf-8",
            )

            first = load_and_chunk(path, root, chunk_size=100, chunk_overlap=10)
            second = load_and_chunk(path, root, chunk_size=100, chunk_overlap=10)

            self.assertEqual(first[0].id, second[0].id)
            self.assertEqual(first[0].title, "供应合同")
            self.assertEqual(first[0].section, "交付条款")
            self.assertEqual(first[0].document_type, "contract")
            self.assertEqual(first[0].source, "contracts/supplier_contract.md")

    def test_recursive_discovery_only_returns_supported_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sop").mkdir()
            (root / "sop" / "policy.txt").write_text("policy", encoding="utf-8")
            (root / "ignored.csv").write_text("a,b", encoding="utf-8")
            self.assertEqual([path.name for path in discover_documents(root)], ["policy.txt"])


class RetrieverCompatibilityTests(unittest.TestCase):
    def test_prompt_context_keeps_citations(self) -> None:
        retrieved = {
            "by_agent": {
                "risk": [{
                    "id": "abc",
                    "display_id": "供应合同#交付条款-片段1",
                    "document_type": "contract",
                    "title": "供应合同",
                    "section": "交付条款",
                    "content": "供应商应在七天内交付。",
                    "score": 0.91,
                    "vector_score": 0.87,
                }]
            }
        }
        context = format_retrieved_context(retrieved, "risk")
        self.assertIn("供应合同#交付条款-片段1", context)
        self.assertIn("向量相似度=0.870", context)

    def test_snapshot_becomes_semantic_query_context(self) -> None:
        context = _build_scenario_context({
            "product": {"name": "芯片", "sku": "CHIP-01"},
            "inventory": {"quantity": 20, "reorder_point": 100},
            "suppliers": [],
            "purchase_orders": [],
            "shipments": [],
        })
        self.assertIn("芯片", context)
        self.assertIn("库存 20", context)
        self.assertIn("补货点 100", context)


@unittest.skipUnless(importlib.util.find_spec("chromadb"), "chromadb not installed")
class VectorStoreIntegrationTests(unittest.TestCase):
    def test_persistent_incremental_index_and_vector_search(self) -> None:
        from src.rag.config import RAGSettings
        from src.rag.vector_store import ChromaKnowledgeBase

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            knowledge = root / "knowledge"
            knowledge.mkdir()
            (knowledge / "inventory_policy.md").write_text(
                "# 库存 SOP\n\n库存低于补货点时必须补货。", encoding="utf-8"
            )
            (knowledge / "supplier_contract.md").write_text(
                "# 供应合同\n\n合同要求供应商七天内交付。", encoding="utf-8"
            )
            settings = RAGSettings(
                knowledge_dir=knowledge,
                persist_dir=root / "vectors",
                collection_name="integration_test",
                embedding_provider="test",
                embedding_model="test",
                chunk_size=100,
                chunk_overlap=10,
                candidate_multiplier=2,
            )
            store = ChromaKnowledgeBase(settings, FakeEmbedder())
            first = store.sync()
            second = store.sync()
            results = store.search("供应合同的交付要求", top_k=1)

            self.assertEqual(first.documents, 2)
            self.assertEqual(first.chunks, 2)
            self.assertEqual(second.added_or_updated, 0)
            self.assertEqual(results[0]["document_type"], "contract")


if __name__ == "__main__":
    unittest.main()
