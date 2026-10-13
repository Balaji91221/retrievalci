"""Thin CLI composition around the public SDK. Exit codes: 0 pass, 1 regression, 2 incomplete/error."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import LexicalRetriever, compare, evaluate
from .models import QueryDataset, ValidationError, read_json
from .report import render_html
from .store import SnapshotStore
from .ingest import ingest_directory
from .execution import ExecutionPolicy
from .providers import HTTPRetriever


def write_report(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retrievalci", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot", help="Create an immutable corpus snapshot")
    snapshot.add_argument("corpus", help="JSON corpus with schema_version=1 and documents")
    snapshot.add_argument("--config", help="Retriever JSON config; default {}")
    snapshot.add_argument("--source-ids", help="Complete relative-path to stable-source-ID JSON manifest for directory input")
    snapshot.add_argument("--chunk-size", type=int, default=1, help="Paragraph evidence units per chunk for directory input")
    run = sub.add_parser("run", help="Evaluate reviewed queries against a snapshot")
    run.add_argument("--dataset", required=True)
    run.add_argument("--snapshot", required=True, help="Snapshot SHA-256 ID")
    run.add_argument("--k", type=int, default=3)
    run.add_argument("--timeout", type=float, default=5, help="Hard per-attempt subprocess deadline in seconds")
    run.add_argument("--retries", type=int, default=1, help="Additional attempts after transient errors")
    run.add_argument("--backoff", type=float, default=0.05)
    run.add_argument("--checkpoint", help="Dedicated immutable checkpoint directory")
    run.add_argument("--resume", action="store_true")
    run.add_argument("--max-queries", type=int, help="Optional query budget; remaining queries are incomplete")
    run.add_argument("--provider-url", help="Opt-in JSON HTTP retrieval endpoint instead of local lexical retrieval")
    run.add_argument("--provider-version", help="Provider index/implementation version")
    run.add_argument("--token-env", help="Environment variable containing provider bearer token")
    comparison = sub.add_parser("compare", help="Compare two saved evaluation runs")
    comparison.add_argument("--baseline", required=True)
    comparison.add_argument("--candidate", required=True)
    comparison.add_argument("--max-recall-drop", type=float, default=0)
    comparison.add_argument("--max-rr-drop", type=float, default=0)
    comparison.add_argument("--json", dest="json_path")
    comparison.add_argument("--html", dest="html_path")
    for command in (snapshot, run, comparison):
        command.add_argument("--store", default=".retrievalci")
    args = parser.parse_args(argv)
    store = SnapshotStore(args.store)
    try:
        if args.command == "snapshot":
            config = read_json(args.config) if args.config else None
            if Path(args.corpus).is_dir():
                value = ingest_directory(args.corpus, config, source_ids=read_json(args.source_ids) if args.source_ids else None,
                                         chunk_size=args.chunk_size)
                store.save_snapshot(value)
            else:
                if args.source_ids:
                    raise ValidationError("--source-ids applies only to directory ingestion")
                value = store.create(read_json(args.corpus), config)
            output = {"snapshot_id": value.id, "documents": value.manifest}
            if value.mapping:
                output["mapping"] = {"sources": {key: {"path": source["path"], "content_hash": source["content_hash"]}
                    for key, source in value.mapping["sources"].items()}, "spans": value.mapping["spans"],
                    "chunks": value.mapping["chunks"]}
            code = 0
        elif args.command == "run":
            adapter = LexicalRetriever()
            if args.provider_url:
                if not args.provider_version:
                    raise ValidationError("--provider-version is required with --provider-url")
                adapter = HTTPRetriever(args.provider_url, args.provider_version, args.timeout, args.token_env)
            elif args.provider_version or args.token_env:
                raise ValidationError("Provider flags require --provider-url")
            value = evaluate(adapter, QueryDataset.load(args.dataset), store.load(args.snapshot), args.k,
                             policy=ExecutionPolicy(args.timeout, args.retries, args.backoff),
                             checkpoint_dir=args.checkpoint, resume=args.resume, max_queries=args.max_queries)
            run_id = store.save_run(value)
            output = {"run_id": run_id, **value.payload}
            code = 0 if value.complete else 2
        else:
            output = compare(store.load_run(args.baseline), store.load_run(args.candidate),
                             args.max_recall_drop, args.max_rr_drop)
            if args.json_path:
                write_report(args.json_path, json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
            if args.html_path:
                write_report(args.html_path, render_html(output))
            code = {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[output["verdict"]]
        print(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False))
        return code
    except (ValidationError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
