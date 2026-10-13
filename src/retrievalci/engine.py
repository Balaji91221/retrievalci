"""Retrieval adapters, evaluation orchestration, and comparison gates."""
from __future__ import annotations

import math
import re
import time
import difflib
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol, Sequence

from .models import (EvaluationRun, Hit, QueryDataset, Snapshot, digest,
                     metrics, parse_hits, require, summarize)
from .execution import ExecutionPolicy, ResumeJournal, execute
from .providers import RetrievalBatch


class RetrieverAdapter(Protocol):
    """Return ordered, unique, mapped evidence IDs; raise on incomplete retrieval.

    Adapters run in killable subprocesses. Return RetrievalBatch with explicit
    snapshot alignment/index completeness. Legacy list results remain supported
    but are labeled legacy-assumed-complete in the trace.
    """
    name: str
    version: str

    def retrieve(self, query: str, k: int, snapshot: Snapshot) -> RetrievalBatch | Sequence[Hit]: ...


class LexicalRetriever:
    """Term frequency / sqrt(document length), with deterministic evidence-ID ties.

    Deliberately small local baseline, not an embedding or production search engine.
    """
    name = "lexical"
    version = "2"

    def retrieve(self, query: str, k: int, snapshot: Snapshot) -> RetrievalBatch:
        config = snapshot.config
        require(not (set(config) - {"stop_words", "chunk_mode"}), "Lexical adapter supports stop_words/chunk_mode config")
        require(type(config.get("chunk_mode", False)) is bool, "chunk_mode must be boolean")
        stop_words = config.get("stop_words", [])
        require(isinstance(stop_words, list) and all(isinstance(s, str) for s in stop_words),
                "stop_words must be a list of strings")
        stop = {word.casefold() for word in stop_words}

        def tokenize(text: str) -> list[str]:
            return [word for word in re.findall(r"\w+", text.casefold()) if word not in stop]

        terms = set(tokenize(query))
        hits = []
        units = {doc.id: doc.text for doc in snapshot.documents}
        if config.get("chunk_mode"):
            require(bool(snapshot.mapping), "chunk_mode requires mapped source evidence")
            units = {chunk: "\n".join(units[eid] for eid in ids) for chunk, ids in snapshot.mapping["chunks"].items()}
        for unit_id, text in units.items():
            tokens = tokenize(text)
            frequency = Counter(tokens)
            score = sum(frequency[t] for t in terms) / math.sqrt(len(tokens)) if tokens else 0.0
            if score > 0:
                hits.append(Hit(unit_id, score))
        ranked = sorted(hits, key=lambda hit: (-hit.score, hit.evidence_id))[:k]
        return RetrievalBatch(tuple(ranked), snapshot.id, True, snapshot.id)


def normalize_batch(batch: Any, snapshot: Snapshot, k: int) -> tuple[tuple[Hit, ...], dict[str, Any]]:
    if isinstance(batch, RetrievalBatch):
        require(type(batch.index_complete) is bool and batch.index_complete, "partial_index: provider index is incomplete")
        require(batch.snapshot_id == snapshot.id, "index_snapshot_mismatch: provider index does not match snapshot")
        require(isinstance(batch.index_id, str) and bool(batch.index_id), "Provider index_id is required")
        rows, metadata = batch.hits, {"index_complete": True, "index_id": batch.index_id,
                                     "index_attestation": "provider-declared"}
    else:
        rows, metadata = batch, {"index_complete": True, "index_id": snapshot.id,
                                 "index_attestation": "legacy-assumed-complete"}
    chunks = snapshot.mapping.get("chunks", {})
    raw = parse_hits(rows, {**snapshot.manifest, **{chunk: "mapped" for chunk in chunks}}, k)
    result, seen = [], set()
    for rank, hit in enumerate(raw, 1):
        for evidence_id in chunks.get(hit.evidence_id, [hit.evidence_id]):
            if evidence_id not in seen:
                seen.add(evidence_id)
                result.append(Hit(evidence_id, hit.score, rank))
    return tuple(result), {**metadata, "retrieval_units": [hit.to_dict() for hit in raw]}


def evaluate(retriever: RetrieverAdapter, dataset: QueryDataset, snapshot: Snapshot,
             k: int = 3, *, policy: ExecutionPolicy | None = None,
             checkpoint_dir: str | Path | None = None, resume: bool = False,
             max_queries: int | None = None, require_index_attestation: bool = True) -> EvaluationRun:
    """Evaluate every query; errors and stale gold make the run incomplete.

    Mapped chunks expand to source evidence with the original retrieval rank.
    Repeated chunks/evidence cannot inflate recall. No automatic gold rewriting.
    """
    require(type(k) is int and k > 0, "k must be a positive integer")
    dataset = QueryDataset.from_dict(dataset.payload)
    snapshot = Snapshot.from_corpus(snapshot.payload, snapshot.config)
    policy = policy or ExecutionPolicy()
    policy.validate()
    require(max_queries is None or (type(max_queries) is int and max_queries >= 0), "max_queries must be nonnegative")
    require(not resume or checkpoint_dir is not None, "resume requires checkpoint_dir")
    adapter = {"name": retriever.name, "version": retriever.version,
               "configuration": getattr(retriever, "configuration", {})}
    inputs = {"snapshot_id": snapshot.id, "dataset_id": dataset.id, "k": k,
              "adapter": adapter, "policy": asdict(policy), "require_index_attestation": require_index_attestation}
    journal = ResumeJournal(checkpoint_dir, inputs, resume) if checkpoint_dir else None
    cached = journal.completed() if journal and resume else {}
    manifest = snapshot.manifest
    results = []
    processed = 0
    for case in dataset.queries:
        result: dict[str, Any] = {"query_id": case.id, "status": "ok", "hits": [],
                                 "issues": [], "metrics": None, "latency_ms": 0.0, "attempts": []}
        if case.id in cached:
            results.append(cached[case.id])
            continue
        if max_queries is not None and processed >= max_queries:
            result.update(status="pending", issues=[{"kind": "interrupted", "message": "Query budget reached; resume checkpoint"}])
            results.append(result)
            continue
        processed += 1
        if case.status == "unanswerable":
            result.update(status="excluded", issues=[{"kind": "unanswerable_excluded",
                "message": "Reviewed unanswerable queries are outside retrieval-recall metrics."}])
        elif case.status == "needs_review":
            result.update(status="review_required", issues=[{"kind": "labels_need_review"}])
        else:
            for gold in case.gold:
                actual = manifest.get(gold.evidence_id)
                if actual is None:
                    result["issues"].append({"kind": "gold_deleted", "evidence_id": gold.evidence_id})
                elif actual != gold.content_hash:
                    result["issues"].append({"kind": "gold_stale", "evidence_id": gold.evidence_id,
                                             "expected_hash": gold.content_hash, "actual_hash": actual})
            if result["issues"]:
                result["status"] = "review_required"
            else:
                start = time.perf_counter()
                try:
                    batch, attempts = execute(retriever, case.query, k, snapshot, policy)
                    result["attempts"] = attempts
                    if attempts[-1]["outcome"] != "success":
                        result.update(status="error", issues=[{"kind": "retrieval_error", **attempts[-1]}])
                        hits = None
                    else:
                        hits, metadata = normalize_batch(batch, snapshot, k)
                        require(not require_index_attestation or metadata["index_attestation"] == "provider-declared",
                                "Index completeness declaration required; legacy list adapters are disallowed")
                        result.update(metadata)
                    if hits is not None:
                        result["hits"] = [hit.to_dict() for hit in hits]
                        result["metrics"] = metrics(hits, case.gold)
                except Exception as exc:
                    kind = str(exc).split(":", 1)[0]
                    kind = kind if kind in {"partial_index", "index_snapshot_mismatch"} else "retrieval_error"
                    result.update(status="error", issues=[{"kind": kind,
                                  "error_type": type(exc).__name__, "message": str(exc)}])
                finally:
                    result["latency_ms"] = round((time.perf_counter() - start) * 1000, 3)
        results.append(result)
        if journal:
            journal.save(result)
    return EvaluationRun.from_dict({
        "schema_version": 1, "kind": "run", "evidence_identity": "source-span/v1" if snapshot.mapping else "document-id/v1",
        "snapshot_id": snapshot.id, "manifest": manifest,
        "dataset": dataset.payload, "dataset_id": dataset.id,
        "config": snapshot.config, "config_hash": digest(snapshot.config),
        "adapter": adapter, "k": k, "execution_policy": asdict(policy),
        "sources": snapshot.mapping.get("sources", {}), "spans": snapshot.mapping.get("spans", {}),
        "input_hash": digest(inputs), "resumed_queries": sorted(cached),
        "results": results, "summary": summarize(results),
    })


def compare(baseline: EvaluationRun, candidate: EvaluationRun,
            max_recall_drop: float = 0.0, max_rr_drop: float = 0.0) -> dict[str, Any]:
    """Fail on per-query metric drops; incomparable/incomplete inputs never pass.

    Dataset identity, k, and evidence identity must match. Adapter/configuration
    changes are valid experiment dimensions and are explicitly recorded.
    """
    for threshold in (max_recall_drop, max_rr_drop):
        require(type(threshold) in {int, float} and math.isfinite(threshold) and 0 <= threshold <= 1,
                "Drop thresholds must be finite numbers between 0 and 1")
    base = EvaluationRun.from_dict(baseline.payload).payload
    cand = EvaluationRun.from_dict(candidate.payload).payload
    compatibility_issues = [f"{key} differs" for key in ("dataset_id", "k", "evidence_identity")
                            if base.get(key) != cand.get(key)]
    for label, run in (("baseline", base), ("candidate", cand)):
        if not run["summary"]["complete"]:
            compatibility_issues.append(f"{label} is incomplete or requires evidence review")
    bdocs, cdocs = base["manifest"], cand["manifest"]
    changes = {"added": sorted(set(cdocs) - set(bdocs)), "deleted": sorted(set(bdocs) - set(cdocs)),
               "modified": sorted(i for i in set(bdocs) & set(cdocs) if bdocs[i] != cdocs[i])}
    experiment_changes = [key for key in ("adapter", "config_hash") if base[key] != cand[key]]
    source_diffs = []
    bsources, csources = base.get("sources", {}), cand.get("sources", {})
    for source_id in sorted(set(bsources) | set(csources)):
        before, after = bsources.get(source_id), csources.get(source_id)
        if before == after:
            continue
        kind = "added" if before is None else "deleted" if after is None else "renamed" if before["content_hash"] == after["content_hash"] else "modified"
        source_diffs.append({"source_id": source_id, "kind": kind,
            "before_path": before["path"] if before else None, "after_path": after["path"] if after else None,
            "diff": "".join(difflib.unified_diff((before["text"] if before else "").splitlines(keepends=True),
                (after["text"] if after else "").splitlines(keepends=True),
                fromfile=before["path"] if before else "/dev/null", tofile=after["path"] if after else "/dev/null"))})
    regressions, deltas, review_issues = [], [], []
    for label, run in (("baseline", base), ("candidate", cand)):
        for result in run["results"]:
            if result["status"] in {"error", "review_required", "pending"}:
                review_issues.append({"run": label, "query_id": result["query_id"],
                                      "status": result["status"], "issues": result["issues"]})
    if base["dataset_id"] == cand["dataset_id"] and base["k"] == cand["k"]:
        for previous, current in zip(base["results"], cand["results"]):
            if previous["status"] != "ok" or current["status"] != "ok":
                continue
            recall_drop = previous["metrics"]["recall_at_k"] - current["metrics"]["recall_at_k"]
            rr_drop = previous["metrics"]["reciprocal_rank"] - current["metrics"]["reciprocal_rank"]
            entry = {"query_id": previous["query_id"], "recall_drop": recall_drop,
                     "rr_drop": rr_drop, "baseline": previous["metrics"], "candidate": current["metrics"],
                     "baseline_hits": previous["hits"], "candidate_hits": current["hits"]}
            query = next(q for q in base["dataset"]["queries"] if q["id"] == previous["query_id"])
            gold_ids = [gold["evidence_id"] for gold in query["gold"]]
            entry["gold_evidence"] = [{"evidence_id": evidence_id,
                "source_span": cand.get("spans", {}).get(evidence_id, base.get("spans", {}).get(evidence_id))}
                for evidence_id in gold_ids]
            deltas.append(entry)
            if recall_drop > max_recall_drop + 1e-12 or rr_drop > max_rr_drop + 1e-12:
                regressions.append(entry)
    verdict = "INCONCLUSIVE" if compatibility_issues else "FAIL" if regressions else "PASS"
    return {"schema_version": 1, "kind": "comparison", "verdict": verdict,
            "baseline_id": baseline.id, "candidate_id": candidate.id,
            "thresholds": {"max_recall_drop": max_recall_drop, "max_rr_drop": max_rr_drop},
            "compatibility_issues": compatibility_issues, "experiment_changes": experiment_changes,
            "source_changes": changes, "review_issues": review_issues, "regressions": regressions,
            "source_diffs": source_diffs,
            "query_deltas": deltas, "baseline_summary": base["summary"], "candidate_summary": cand["summary"],
            "interpretation": "Source changes and score changes are observations, not proof of causation."}
