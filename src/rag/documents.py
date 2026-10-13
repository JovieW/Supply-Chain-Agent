from __future__ import annotations

import hashlib
import re
from datetime import date
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf", ".docx"}


@dataclass(frozen=True)
class KnowledgeChunk:
    id: str
    content: str
    source: str
    title: str
    section: str
    document_type: str
    chunk_index: int
    file_hash: str
    document_id: str
    version: str
    status: str
    effective_from: str
    effective_to: str
    owner: str
    product_scope: str
    supplier_scope: str
    region_scope: str
    page: int | None = None

    def metadata(self) -> dict[str, str | int]:
        data: dict[str, str | int] = {
            "source": self.source,
            "title": self.title,
            "section": self.section,
            "document_type": self.document_type,
            "chunk_index": self.chunk_index,
            "file_hash": self.file_hash,
            "document_id": self.document_id,
            "version": self.version,
            "status": self.status,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "owner": self.owner,
            "product_scope": self.product_scope,
            "supplier_scope": self.supplier_scope,
            "region_scope": self.region_scope,
        }
        if self.page is not None:
            data["page"] = self.page
        return data


def discover_documents(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )

# 切块：固定最大字符长度700 + 重叠窗口100字符 + 语义边界优化
def load_and_chunk(
    path: Path,
    root: Path,
    chunk_size: int = 700,
    chunk_overlap: int = 100,
) -> list[KnowledgeChunk]:
    if chunk_size <= 0 or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_size must be positive and overlap smaller than it")
    raw = path.read_bytes()
    file_hash = hashlib.sha256(raw).hexdigest()
    relative_source = path.relative_to(root).as_posix()
    document_type = _infer_document_type(path, root)
    governance = _document_governance(path, relative_source)
    chunks: list[KnowledgeChunk] = []
    for title, section, text, page in _load_sections(path):  # 先把文档按标题或页拆成section
        for content in _split_text(text, chunk_size, chunk_overlap):  # 切块
            index = len(chunks)
            stable_key = f"{relative_source}:{file_hash}:{index}"
            chunks.append(
                KnowledgeChunk(  # 一块只是文本及其附属信息的数据结构
                    id=hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:32],
                    content=content,
                    source=relative_source,
                    title=title or path.stem,
                    section=section or title or path.stem,
                    document_type=document_type,
                    chunk_index=index,
                    file_hash=file_hash,
                    **governance,
                    page=page,
                )
            )
    return chunks


def _load_sections(path: Path) -> list[tuple[str, str, str, int | None]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF ingestion requires pypdf") from exc
        reader = PdfReader(str(path))
        return [
            (path.stem, f"第 {page_no} 页", page.extract_text() or "", page_no)
            for page_no, page in enumerate(reader.pages, start=1)
        ]
    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError as exc:
            raise RuntimeError("DOCX ingestion requires python-docx") from exc
        doc = Document(str(path))
        lines = []
        for paragraph in doc.paragraphs:
            style_name = getattr(paragraph.style, "name", "") or ""
            heading = re.search(r"Heading\s+(\d+)", style_name, re.IGNORECASE)
            if heading:
                lines.append(f"{'#' * int(heading.group(1))} {paragraph.text}")
            else:
                lines.append(paragraph.text)
        return _parse_headings("\n".join(lines), path.stem)
    _, body = _parse_front_matter(path.read_text(encoding="utf-8"))
    return _parse_headings(body, path.stem)


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Parse a deliberately small YAML-like header without another dependency."""
    if not text.startswith("---\n"):
        return {}, text
    closing = text.find("\n---\n", 4)
    if closing < 0:
        raise ValueError("Knowledge document front matter is not closed with ---")
    metadata: dict[str, str] = {}
    for line in text[4:closing].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Invalid knowledge metadata line: {line}")
        key, value = line.split(":", 1)
        metadata[key.strip().lower()] = value.strip().strip('"\'')
    return metadata, text[closing + 5:]


def _scope(value: str | None) -> str:
    values = [item.strip().lower() for item in str(value or "all").split(",") if item.strip()]
    return ",".join(dict.fromkeys(values or ["all"]))


def _document_governance(path: Path, relative_source: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if path.suffix.lower() in {".md", ".txt"}:
        metadata, _ = _parse_front_matter(path.read_text(encoding="utf-8"))
    effective_from = metadata.get("effective_from", "2000-01-01")
    effective_to = metadata.get("effective_to", "2099-12-31")
    for field_name, value in (("effective_from", effective_from), ("effective_to", effective_to)):
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{relative_source}: invalid {field_name}={value}") from exc
    status = metadata.get("status", "active").lower()
    if status not in {"active", "draft", "retired"}:
        raise ValueError(f"{relative_source}: unsupported document status {status}")
    return {
        "document_id": metadata.get("document_id", relative_source),
        "version": metadata.get("version", "1.0"),
        "status": status,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "owner": metadata.get("owner", "unassigned"),
        "product_scope": _scope(metadata.get("product_ids")),
        "supplier_scope": _scope(metadata.get("supplier_ids")),
        "region_scope": _scope(metadata.get("regions")),
    }


def _parse_headings(text: str, fallback_title: str) -> list[tuple[str, str, str, None]]:
    sections: list[tuple[str, str, str, None]] = []
    document_title = fallback_title
    current_heading = fallback_title
    title_found = False
    lines: list[str] = []

    def flush() -> None:
        content = "\n".join(lines).strip()
        if content:
            sections.append((document_title, current_heading, content, None))

    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+)$", line.strip())
        if match:
            flush()
            lines.clear()
            current_heading = match.group(1).strip()
            if not title_found:
                document_title = current_heading
                title_found = True
            continue
        lines.append(line)
    flush()
    return sections


def _split_text(text: str, chunk_size: int, overlap: int) -> Iterable[str]:
    cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()  # 文本清洗
    if not cleaned:
        return
    start = 0
    while start < len(cleaned):
        end = min(start + chunk_size, len(cleaned))
        if end < len(cleaned):  # 这个chunk是文本中间部分
            boundary = max(  # 在chunk中的后半段进行语义优化边界，在后半段是因为防止块太小
                cleaned.rfind(mark, start + chunk_size // 2, end)
                for mark in ("\n", "。", "；", ". ")
            )
            if boundary > start:
                end = boundary + 1
        chunk = cleaned[start:end].strip()
        if chunk:
            yield chunk
        if end >= len(cleaned):
            break
        start = max(end - overlap, start + 1)


def _infer_document_type(path: Path, root: Path) -> str:
    value = " ".join((*path.relative_to(root).parts, path.stem)).lower()
    rules = {
        "contract": ("contract", "sla", "合同", "协议"),
        "historical_event": ("history", "historical", "incident", "event", "历史", "事件", "案例"),
        # ``policies`` is not a substring match for the singular ``policy``.
        # Keep both forms so directory-based classification works for the
        # enterprise corpus as well as for individual policy filenames.
        "sop": (
            "sop", "policy", "policies", "playbook", "procedure",
            "流程", "策略", "规范",
        ),
    }
    for document_type, keywords in rules.items():
        if any(keyword in value for keyword in keywords):
            return document_type
    return "general"
