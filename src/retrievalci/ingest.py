"""UTF-8 directory ingestion with reviewed evidence independent of chunk layout."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .models import Snapshot, digest, nonempty, require, text_hash


def validate_mapping(mapping: Any, documents: dict[str, str]) -> None:
    require(isinstance(mapping, dict) and mapping.get("version") == 1, "Unsupported source mapping")
    sources, spans, chunks = mapping.get("sources"), mapping.get("spans"), mapping.get("chunks")
    require(isinstance(sources, dict) and isinstance(spans, dict) and isinstance(chunks, dict),
            "Mapping requires sources, spans, and chunks")
    require(set(spans) == set(documents), "Every evidence unit needs exactly one source span")
    for source_id, source in sources.items():
        require(nonempty(source_id) and isinstance(source, dict) and nonempty(source.get("path"))
                and isinstance(source.get("text"), str), "Invalid source record")
        require(source.get("content_hash") == text_hash(source["text"]), "Source hash mismatch")
    for evidence_id, span in spans.items():
        require(isinstance(span, dict) and span.get("source_id") in sources, "Unmapped source span")
        start, end = span.get("start"), span.get("end")
        text = sources[span["source_id"]]["text"]
        require(type(start) is int and type(end) is int and 0 <= start < end <= len(text), "Invalid source offsets")
        require(text[start:end] == documents[evidence_id], "Evidence text does not match its source span")
    covered: set[str] = set()
    for chunk_id, ids in chunks.items():
        require(nonempty(chunk_id) and chunk_id not in documents and isinstance(ids, list) and bool(ids),
                "Invalid or colliding chunk ID")
        require(all(isinstance(i, str) and i in documents for i in ids) and len(set(ids)) == len(ids),
                "Chunk contains duplicate or unknown evidence units")
        covered.update(ids)
    require(covered == set(documents), "Chunk mapping does not cover all evidence")


def ingest_directory(root: str | Path, config: dict[str, Any] | None = None, *,
                     source_ids: dict[str, str] | None = None, chunk_size: int = 1,
                     max_file_bytes: int = 2_000_000) -> Snapshot:
    """Import .txt/.md files. IDs survive chunk changes and explicit file renames.

    Paragraph ordinal identifies reviewed evidence. Edits/insertion can invalidate
    labels (content hashes enforce re-review). Spans use Unicode character offsets.
    Symlinks are rejected; source_ids is a complete path->stable-ID rename manifest.
    """
    root = Path(root).resolve()
    require(root.is_dir(), "Corpus directory does not exist")
    require(type(chunk_size) is int and chunk_size > 0, "chunk_size must be positive")
    require(type(max_file_bytes) is int and max_file_bytes > 0, "max_file_bytes must be positive")
    files = []
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), f"Symlinks are not ingested: {path.relative_to(root)}")
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}:
            files.append(path)
    require(bool(files), "No .md/.txt files found")
    paths = {path.relative_to(root).as_posix() for path in files}
    if source_ids is not None:
        require(isinstance(source_ids, dict) and set(source_ids) == paths,
                "source_ids must map every ingested relative path exactly once")
        require(all(nonempty(v) for v in source_ids.values()) and len(set(source_ids.values())) == len(paths),
                "Stable source IDs must be nonempty and unique")
    documents, sources, spans, chunks = [], {}, {}, {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        source_id = source_ids[relative] if source_ids is not None else relative
        require(path.stat().st_size <= max_file_bytes, f"File exceeds ingestion limit: {relative}")
        text = path.read_bytes().decode("utf-8")
        require(nonempty(text), f"Empty source: {relative}")
        sources[source_id] = {"path": relative, "text": text, "content_hash": text_hash(text)}
        # Blank-line-delimited evidence stays independent of retriever chunk size.
        bounds = [0]
        bounds.extend(match.end() for match in re.finditer(r"\n[ \t\r]*\n", text))
        ids = []
        for index, (start, end) in enumerate(zip(bounds, bounds[1:] + [len(text)]), 1):
            raw = text[start:end]
            left = len(raw) - len(raw.lstrip())
            end = start + len(raw.rstrip())
            start += left
            if start >= end:
                continue
            evidence_id = f"{source_id}#paragraph:{index}"
            documents.append({"id": evidence_id, "text": text[start:end]})
            spans[evidence_id] = {"source_id": source_id, "start": start, "end": end}
            ids.append(evidence_id)
        for offset in range(0, len(ids), chunk_size):
            group = ids[offset:offset + chunk_size]
            chunk_id = "chunk:" + digest({"ids": group, "source_hash": sources[source_id]["content_hash"]})
            chunks[chunk_id] = group
    mapping = {"version": 1, "sources": sources, "spans": spans, "chunks": chunks}
    return Snapshot.from_corpus({"schema_version": 1, "documents": documents, "mapping": mapping}, config)
