"""Versioned inputs and immutable, hash-addressed evaluation records."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ValidationError(ValueError):
    """An input or artifact cannot be evaluated without ambiguity."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError(f"Cannot read JSON at {path}: {exc}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


@dataclass(frozen=True)
class Document:
    id: str
    text: str

    @property
    def content_hash(self) -> str:
        return text_hash(self.text)


@dataclass(frozen=True)
class Snapshot:
    documents: tuple[Document, ...]
    config_json: str = "{}"
    mapping_json: str = "{}"

    @classmethod
    def from_corpus(cls, raw: Any, config: dict[str, Any] | None = None) -> Snapshot:
        require(isinstance(raw, dict) and raw.get("schema_version") == 1, "Corpus schema_version must be 1")
        rows = raw.get("documents")
        require(isinstance(rows, list) and bool(rows), "Corpus requires at least one document")
        documents = []
        for row in rows:
            require(isinstance(row, dict) and nonempty(row.get("id")) and nonempty(row.get("text")),
                    "Each document requires nonempty id and text")
            documents.append(Document(row["id"], row["text"]))
        require(len({d.id for d in documents}) == len(documents), "Duplicate document/evidence ID")
        require(config is None or isinstance(config, dict), "Retriever config must be an object")
        mapping = raw.get("mapping", {})
        if mapping:
            from .ingest import validate_mapping
            validate_mapping(mapping, {d.id: d.text for d in documents})
        return cls(tuple(sorted(documents, key=lambda d: d.id)), canonical(config or {}).decode("utf-8"),
                   canonical(mapping).decode("utf-8"))

    @property
    def mapping(self) -> dict[str, Any]:
        return json.loads(self.mapping_json)

    @property
    def config(self) -> dict[str, Any]:
        return json.loads(self.config_json)

    @property
    def manifest(self) -> dict[str, str]:
        return {d.id: d.content_hash for d in self.documents}

    @property
    def payload(self) -> dict[str, Any]:
        result = {"schema_version": 1, "kind": "snapshot", "config": self.config,
                "documents": [{"id": d.id, "text": d.text, "content_hash": d.content_hash}
                              for d in self.documents]}
        if self.mapping:
            result["mapping"] = self.mapping
        return result

    @property
    def id(self) -> str:
        return digest(self.payload)


@dataclass(frozen=True)
class GoldEvidence:
    evidence_id: str
    content_hash: str


@dataclass(frozen=True)
class QueryCase:
    id: str
    query: str
    status: str
    reviewed: bool
    gold: tuple[GoldEvidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "query": self.query, "status": self.status, "reviewed": self.reviewed,
                "gold": [{"evidence_id": g.evidence_id, "content_hash": g.content_hash} for g in self.gold]}


@dataclass(frozen=True)
class QueryDataset:
    version: str
    queries: tuple[QueryCase, ...]

    @classmethod
    def load(cls, path: str | Path) -> QueryDataset:
        return cls.from_dict(read_json(path))

    @classmethod
    def from_dict(cls, raw: Any) -> QueryDataset:
        require(isinstance(raw, dict) and raw.get("schema_version") == 1, "Dataset schema_version must be 1")
        require(nonempty(raw.get("version")), "Dataset version is required")
        rows = raw.get("queries")
        require(isinstance(rows, list) and bool(rows), "Dataset requires queries")
        queries = []
        for row in rows:
            require(isinstance(row, dict) and nonempty(row.get("id")) and nonempty(row.get("query")),
                    "Each query requires nonempty id and query")
            status, reviewed, gold_raw = row.get("status"), row.get("reviewed"), row.get("gold")
            require(status in {"active", "needs_review", "unanswerable"}, "Unsupported query status")
            require(type(reviewed) is bool, "Each query requires an explicit reviewed boolean")
            require(isinstance(gold_raw, list), "Query gold must be a list")
            gold = []
            for entry in gold_raw:
                require(isinstance(entry, dict) and nonempty(entry.get("evidence_id"))
                        and is_hash(entry.get("content_hash")), "Gold requires evidence_id and SHA-256 content_hash")
                gold.append(GoldEvidence(entry["evidence_id"], entry["content_hash"]))
            require(len({g.evidence_id for g in gold}) == len(gold), "Duplicate gold evidence ID")
            if status == "active":
                require(reviewed and bool(gold), "Active query needs reviewed, nonempty gold evidence")
            if status == "unanswerable":
                require(reviewed and not gold, "Unanswerable query needs reviewed=true and empty gold")
            queries.append(QueryCase(row["id"], row["query"], status, reviewed,
                                     tuple(sorted(gold, key=lambda g: g.evidence_id))))
        require(len({q.id for q in queries}) == len(queries), "Duplicate query ID")
        return cls(raw["version"], tuple(sorted(queries, key=lambda q: q.id)))

    @property
    def payload(self) -> dict[str, Any]:
        return {"schema_version": 1, "version": self.version, "queries": [q.to_dict() for q in self.queries]}

    @property
    def id(self) -> str:
        return digest(self.payload)


@dataclass(frozen=True)
class Hit:
    evidence_id: str
    score: float
    rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"evidence_id": self.evidence_id, "score": self.score,
                **({"rank": self.rank} if self.rank is not None else {})}


def metrics(hits: tuple[Hit, ...], gold: tuple[GoldEvidence, ...]) -> dict[str, float]:
    relevant = {item.evidence_id for item in gold}
    require(bool(relevant), "Metrics require relevant evidence")
    seen = {hit.evidence_id for hit in hits}
    rr = next((1 / (hit.rank or rank) for rank, hit in enumerate(hits, 1) if hit.evidence_id in relevant), 0.0)
    return {"recall_at_k": len(seen & relevant) / len(relevant), "reciprocal_rank": rr}


@dataclass(frozen=True)
class EvaluationRun:
    """A canonical JSON record. Return fresh copies to prevent mutation after hashing."""
    record_json: str

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.record_json)

    @property
    def id(self) -> str:
        return digest(self.payload)

    @property
    def complete(self) -> bool:
        return self.payload["summary"]["complete"]

    @classmethod
    def from_dict(cls, data: Any) -> EvaluationRun:
        require(isinstance(data, dict) and data.get("schema_version") == 1 and data.get("kind") == "run",
                "Unsupported run schema")
        require(data.get("evidence_identity") in {"document-id/v1", "source-span/v1"}, "Unsupported evidence identity mapping")
        require(is_hash(data.get("snapshot_id")), "Invalid snapshot ID")
        dataset = QueryDataset.from_dict(data.get("dataset"))
        require(data.get("dataset_id") == dataset.id, "Dataset hash mismatch")
        require(type(data.get("k")) is int and data["k"] > 0, "k must be positive")
        require(isinstance(data.get("config"), dict), "Run config missing")
        require(data.get("config_hash") == digest(data["config"]), "Config hash mismatch")
        manifest = data.get("manifest")
        require(isinstance(manifest, dict) and bool(manifest) and all(nonempty(k) and is_hash(v)
                for k, v in manifest.items()), "Invalid corpus manifest")
        if data.get("evidence_identity") == "source-span/v1":
            sources, spans = data.get("sources"), data.get("spans")
            require(isinstance(sources, dict) and isinstance(spans, dict) and set(spans) == set(manifest),
                    "Run requires complete source-span mapping")
            for source_id, source in sources.items():
                require(nonempty(source_id) and isinstance(source, dict) and nonempty(source.get("path"))
                        and isinstance(source.get("text"), str), "Invalid run source")
                require(source.get("content_hash") == text_hash(source["text"]), "Run source hash mismatch")
            for evidence_id, span in spans.items():
                require(isinstance(span, dict) and span.get("source_id") in sources, "Invalid run span source")
                text = sources[span["source_id"]]["text"]
                start, end = span.get("start"), span.get("end")
                require(type(start) is int and type(end) is int and 0 <= start < end <= len(text), "Invalid run span offsets")
                require(text_hash(text[start:end]) == manifest[evidence_id], "Run span content hash mismatch")
        adapter = data.get("adapter")
        require(isinstance(adapter, dict) and nonempty(adapter.get("name")) and nonempty(adapter.get("version")),
                "Adapter name and version required")
        results = data.get("results")
        require(isinstance(results, list) and len(results) == len(dataset.queries), "Incomplete result list")
        require([r.get("query_id") for r in results if isinstance(r, dict)] == [q.id for q in dataset.queries],
                "Result/query identity mismatch")
        for result, query in zip(results, dataset.queries):
            status = result.get("status")
            require(status in {"ok", "review_required", "error", "excluded", "pending"}, "Unknown result status")
            require(isinstance(result.get("issues"), list), "Result issues must be a list")
            require(isinstance(result.get("hits"), list), "Result hits must be a list")
            if status == "ok":
                require(query.status == "active", "Only active queries can be scored")
                require(all(manifest.get(g.evidence_id) == g.content_hash for g in query.gold),
                        "Scored query has missing/stale gold")
                mapped = data.get("evidence_identity") == "source-span/v1"
                hits = parse_hits(result["hits"], manifest, len(manifest) if mapped else data["k"])
                require(all(hit.rank is None or hit.rank <= data["k"] for hit in hits), "Hit rank exceeds k")
                if "index_complete" in result:
                    require(result["index_complete"] is True, "Scored query must have complete index")
                require(result.get("metrics") == metrics(hits, query.gold), "Stored metric mismatch")
                require(not result["issues"], "Successful query cannot have unresolved issues")
            else:
                require(result.get("metrics") is None and bool(result["issues"]), "Unscored result needs issues")
                if status == "excluded":
                    require(query.status == "unanswerable", "Only reviewed unanswerable queries may be excluded")
        require(data.get("summary") == summarize(results), "Stored summary mismatch")
        return cls(canonical(data).decode("utf-8"))


def parse_hits(rows: Any, manifest: dict[str, str], k: int) -> tuple[Hit, ...]:
    require(isinstance(rows, (list, tuple)) and len(rows) <= k, "Adapter returned more than k hits or invalid list")
    hits = []
    for row in rows:
        if isinstance(row, Hit):
            hit = row
        else:
            require(isinstance(row, dict), "Invalid hit")
            hit = Hit(row.get("evidence_id"), row.get("score"), row.get("rank"))
        require(isinstance(hit.evidence_id, str) and hit.evidence_id in manifest, "Unmapped evidence ID from adapter")
        require(type(hit.score) in {float, int} and math.isfinite(hit.score), "Adapter score must be finite")
        require(hit.rank is None or (type(hit.rank) is int and hit.rank > 0), "Invalid hit rank")
        hits.append(hit)
    require(len({hit.evidence_id for hit in hits}) == len(hits), "Duplicate evidence IDs from adapter")
    return tuple(hits)


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r["metrics"] for r in results if r["status"] == "ok"]
    eligible = [r for r in results if r["status"] != "excluded"]
    return {
        "complete": bool(scored) and len(scored) == len(eligible),
        "query_count": len(results), "scored_count": len(scored),
        "excluded_count": len(results) - len(eligible),
        "coverage": len(scored) / len(eligible) if eligible else 0.0,
        "mean_recall_at_k": sum(m["recall_at_k"] for m in scored) / len(scored) if scored else None,
        "mean_reciprocal_rank": sum(m["reciprocal_rank"] for m in scored) / len(scored) if scored else None,
    }
