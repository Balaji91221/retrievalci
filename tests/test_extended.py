from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from retrievalci import (ExecutionPolicy, HTTPRetriever, Hit, LexicalRetriever, QueryDataset,
    RetrievalBatch, SnapshotStore, TransientRetrievalError, ValidationError, compare, evaluate, ingest_directory)
from retrievalci.models import Snapshot, metrics


class ChunkRetriever:
    name, version = "chunk-fixture", "1"
    def retrieve(self, query, k, snapshot):
        chunk = next(iter(snapshot.mapping["chunks"]))
        return RetrievalBatch((Hit(chunk, 1),), snapshot.id, True, "complete-chunk-index")


class DuplicateChunkRetriever(ChunkRetriever):
    def retrieve(self, query, k, snapshot):
        hit = Hit(next(iter(snapshot.mapping["chunks"])), 1)
        return RetrievalBatch((hit, hit), snapshot.id, True, "duplicate-index")


class PartialIndexRetriever:
    name, version = "partial-index", "1"
    def retrieve(self, query, k, snapshot):
        return RetrievalBatch((), snapshot.id, False, "building")


class WrongSnapshotRetriever:
    name, version = "wrong-index", "1"
    def retrieve(self, query, k, snapshot):
        return RetrievalBatch((), "a" * 64, True, "old-index")


class HangingRetriever:
    name, version = "hang", "1"
    def retrieve(self, query, k, snapshot):
        time.sleep(60)
        return []


class CounterRetriever:
    name, version = "counter", "1"
    def __init__(self, path, fail_first=False):
        self.path, self.fail_first = path, fail_first
    @property
    def configuration(self):
        return {"counter_path": self.path, "fail_first": self.fail_first}
    def retrieve(self, query, k, snapshot):
        path = Path(self.path)
        count = int(path.read_text()) + 1 if path.exists() else 1
        path.write_text(str(count))
        if count == 1 and self.fail_first:
            raise TransientRetrievalError("test retry")
        return LexicalRetriever().retrieve(query, k, snapshot)


class LegacyRetriever:
    name, version = "legacy", "1"
    def retrieve(self, query, k, snapshot):
        return []


class ExtendedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / "docs"
        self.docs.mkdir()
        (self.docs / "guide.md").write_text("Refund receipt required.\n\nDelivery tracking uses an order ID.\n", encoding="utf-8")
        self.snapshot = ingest_directory(self.docs, source_ids={"guide.md": "guide"})
        self.dataset = QueryDataset.from_dict({"schema_version": 1, "version": "reviewed-span-v1", "queries": [
            {"id": "a-refund", "query": "refund", "status": "active", "reviewed": True,
             "gold": [{"evidence_id": self.snapshot.documents[0].id, "content_hash": self.snapshot.documents[0].content_hash}]},
            {"id": "b-delivery", "query": "delivery", "status": "active", "reviewed": True,
             "gold": [{"evidence_id": self.snapshot.documents[1].id, "content_hash": self.snapshot.documents[1].content_hash}]}]})

    def test_source_spans_hashes_and_snapshot_roundtrip(self):
        for document in self.snapshot.documents:
            span = self.snapshot.mapping["spans"][document.id]
            source = self.snapshot.mapping["sources"][span["source_id"]]
            self.assertEqual(source["text"][span["start"]:span["end"]], document.text)
        store = SnapshotStore(self.root / "artifacts")
        identity = store.save_snapshot(self.snapshot)
        self.assertEqual(store.load(identity), self.snapshot)

    def test_chunk_layout_changes_preserve_gold_identity(self):
        candidate = ingest_directory(self.docs, source_ids={"guide.md": "guide"}, chunk_size=2)
        self.assertNotEqual(candidate.id, self.snapshot.id)
        self.assertEqual(candidate.manifest, self.snapshot.manifest)
        result = evaluate(ChunkRetriever(), self.dataset, candidate, k=1)
        self.assertTrue(result.complete)
        self.assertEqual(result.payload["summary"]["mean_recall_at_k"], 1)
        self.assertEqual(result.payload["summary"]["mean_reciprocal_rank"], 1)
        # Both evidence units have retrieval rank one, not flattened ranks 1/2.
        self.assertEqual([hit["rank"] for hit in result.payload["results"][0]["hits"]], [1, 1])

    def test_duplicate_chunks_do_not_inflate_recall(self):
        result = evaluate(DuplicateChunkRetriever(), self.dataset, self.snapshot, k=2)
        self.assertFalse(result.complete)
        self.assertIsNone(result.payload["summary"]["mean_recall_at_k"])

    def test_explicit_rename_keeps_evidence_and_reports_path_change(self):
        baseline = evaluate(LexicalRetriever(), self.dataset, self.snapshot, 1)
        (self.docs / "guide.md").rename(self.docs / "manual.md")
        renamed = ingest_directory(self.docs, source_ids={"manual.md": "guide"})
        candidate = evaluate(LexicalRetriever(), self.dataset, renamed, 1)
        report = compare(baseline, candidate)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["source_diffs"][0]["kind"], "renamed")

    def test_modified_span_is_review_issue_with_source_diff(self):
        baseline = evaluate(LexicalRetriever(), self.dataset, self.snapshot, 1)
        (self.docs / "guide.md").write_text("Refund receipt and account required.\n\nDelivery tracking uses an order ID.\n")
        changed = ingest_directory(self.docs, source_ids={"guide.md": "guide"})
        candidate = evaluate(LexicalRetriever(), self.dataset, changed, 1)
        report = compare(baseline, candidate)
        self.assertEqual(report["verdict"], "INCONCLUSIVE")
        self.assertEqual(report["review_issues"][0]["issues"][0]["kind"], "gold_stale")
        self.assertIn("+Refund receipt and account", report["source_diffs"][0]["diff"])

    def test_ingestion_rejects_symlinks_incomplete_rename_maps_and_bad_spans(self):
        with self.assertRaises(ValidationError):
            ingest_directory(self.docs, source_ids={})
        raw = self.snapshot.payload
        raw["mapping"]["spans"][self.snapshot.documents[0].id]["start"] = 9999
        with self.assertRaises(ValidationError):
            Snapshot.from_corpus(raw)
        try:
            (self.docs / "link.txt").symlink_to(self.docs / "guide.md")
        except OSError:
            return
        with self.assertRaisesRegex(ValidationError, "Symlinks"):
            ingest_directory(self.docs)

    def test_partial_and_wrong_indexes_never_pass(self):
        for adapter, kind in [(PartialIndexRetriever(), "partial_index"), (WrongSnapshotRetriever(), "index_snapshot_mismatch")]:
            result = evaluate(adapter, self.dataset, self.snapshot, 1)
            self.assertFalse(result.complete)
            self.assertEqual(result.payload["results"][0]["issues"][0]["kind"], kind)

    def test_legacy_index_assumption_is_explicit_opt_in(self):
        result = evaluate(LegacyRetriever(), self.dataset, self.snapshot, 1)
        self.assertFalse(result.complete)
        result = evaluate(LegacyRetriever(), self.dataset, self.snapshot, 1, require_index_attestation=False)
        self.assertTrue(result.complete)
        self.assertEqual(result.payload["results"][0]["index_attestation"], "legacy-assumed-complete")

    def test_hanging_adapter_is_killed_and_retries_are_bounded(self):
        start = time.perf_counter()
        result = evaluate(HangingRetriever(), self.dataset, self.snapshot, 1,
                          policy=ExecutionPolicy(timeout_s=0.2, max_retries=1, backoff_s=0))
        self.assertLess(time.perf_counter() - start, 5)
        self.assertFalse(result.complete)
        self.assertEqual(len(result.payload["results"][0]["attempts"]), 2)
        self.assertFalse(multiprocessing.active_children())

    def test_transient_error_retries_and_records_attempts(self):
        adapter = CounterRetriever(str(self.root / "count"), fail_first=True)
        result = evaluate(adapter, self.dataset, self.snapshot, 1, policy=ExecutionPolicy(max_retries=1))
        self.assertTrue(result.complete)
        self.assertEqual(len(result.payload["results"][0]["attempts"]), 2)
        self.assertEqual((self.root / "count").read_text(), "3")

    def test_resume_reuses_success_and_checks_all_immutable_inputs(self):
        adapter = CounterRetriever(str(self.root / "count"))
        checkpoint = self.root / "checkpoint"
        partial = evaluate(adapter, self.dataset, self.snapshot, 1, checkpoint_dir=checkpoint, max_queries=1)
        self.assertFalse(partial.complete)
        self.assertEqual(partial.payload["results"][1]["status"], "pending")
        finished = evaluate(adapter, self.dataset, self.snapshot, 1, checkpoint_dir=checkpoint, resume=True)
        self.assertTrue(finished.complete)
        self.assertEqual((self.root / "count").read_text(), "2")
        self.assertEqual(finished.payload["resumed_queries"], ["a-refund"])
        with self.assertRaisesRegex(ValidationError, "inputs differ"):
            evaluate(adapter, self.dataset, self.snapshot, 2, checkpoint_dir=checkpoint, resume=True)
        with self.assertRaisesRegex(ValidationError, "No checkpoint"):
            evaluate(adapter, self.dataset, self.snapshot, 1, checkpoint_dir=self.root / "missing", resume=True)

    def test_corrupt_checkpoint_does_not_silently_rerun(self):
        checkpoint = self.root / "checkpoint"
        evaluate(LexicalRetriever(), self.dataset, self.snapshot, 1, checkpoint_dir=checkpoint, max_queries=1)
        result = next((checkpoint / "results").glob("*.json"))
        result.write_text("{}")
        with self.assertRaisesRegex(ValidationError, "hash mismatch"):
            evaluate(LexicalRetriever(), self.dataset, self.snapshot, 1, checkpoint_dir=checkpoint, resume=True)

    def test_http_provider_contract_retries_and_payload_scope(self):
        captured = []
        snapshot = self.snapshot
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append(payload)
                if len(captured) == 1:
                    self.send_response(503); self.end_headers(); return
                batch = LexicalRetriever().retrieve(payload["query"], payload["k"], snapshot)
                body = json.dumps({"schema_version": 1, "snapshot_id": payload["snapshot_id"],
                    "index_complete": True, "index_id": "test-index-v1", "hits": [hit.to_dict() for hit in batch.hits]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = HTTPRetriever(f"http://127.0.0.1:{server.server_port}/retrieve", "test-index-v1")
            result = evaluate(provider, self.dataset, snapshot, 1)
            self.assertTrue(result.complete)
            self.assertEqual(len(captured), 3)
            self.assertEqual(set(captured[0]), {"schema_version", "query", "k", "snapshot_id"})
            self.assertEqual(len(result.payload["results"][0]["attempts"]), 2)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_provider_rejects_credentials_in_url(self):
        with self.assertRaises(ValidationError):
            HTTPRetriever("https://user:password@example.invalid/retrieve", "v1")

    def test_http_redirects_and_invalid_completeness_are_rejected(self):
        requests = []
        snapshot = self.snapshot
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                requests.append(self.path)
                self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/redirect":
                    self.send_response(307); self.send_header("Location", "/must-not-follow"); self.end_headers(); return
                raw = {"schema_version": 1, "snapshot_id": snapshot.id, "index_complete": True,
                       "index_id": "test", "hits": []}
                if self.path == "/partial":
                    raw["index_complete"] = False
                elif self.path == "/wrong-snapshot":
                    raw["snapshot_id"] = "0" * 64
                else:
                    raw.pop("index_complete")
                body = json.dumps(raw).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with patch.dict(os.environ, {"RETRIEVALCI_TEST_TOKEN": "synthetic-token"}):
                for path in ["/redirect", "/partial", "/wrong-snapshot", "/malformed"]:
                    adapter = HTTPRetriever(f"http://127.0.0.1:{server.server_port}{path}", "test", token_env="RETRIEVALCI_TEST_TOKEN")
                    result = evaluate(adapter, self.dataset, snapshot, 1)
                    self.assertFalse(result.complete)
                    self.assertNotIn("synthetic-token", result.record_json)
            self.assertNotIn("/must-not-follow", requests)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_cli_directory_ingestion_and_checkpoint_resume(self):
        manifest = self.root / "source-ids.json"
        manifest.write_text(json.dumps({"guide.md": "guide"}))
        dataset_path = self.root / "queries.json"
        dataset_path.write_text(json.dumps(self.dataset.payload))
        store_path = str(self.root / "artifacts")
        def command(*args):
            return subprocess.run([sys.executable, "-m", "retrievalci", *args, "--store", store_path],
                                  text=True, capture_output=True, timeout=15)
        snapshot = command("snapshot", str(self.docs), "--source-ids", str(manifest), "--chunk-size", "2")
        self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
        snapshot_id = json.loads(snapshot.stdout)["snapshot_id"]
        common = ["run", "--dataset", str(dataset_path), "--snapshot", snapshot_id, "--k", "1",
                  "--checkpoint", str(self.root / "checkpoints")]
        partial = command(*common, "--max-queries", "1")
        self.assertEqual(partial.returncode, 2, partial.stderr)
        resumed = command(*common, "--resume")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)["resumed_queries"], ["a-refund"])

    def test_heldout_fixture_has_distinct_reviewed_queries(self):
        fixture = Path(__file__).resolve().parents[1] / "examples" / "heldout"
        snapshot = ingest_directory(fixture / "sources", source_ids=json.loads((fixture / "source-ids.json").read_text()))
        dataset = QueryDataset.load(fixture / "queries.json")
        result = evaluate(LexicalRetriever(), dataset, snapshot, k=1)
        self.assertTrue(result.complete)
        self.assertEqual(result.payload["summary"]["mean_recall_at_k"], 1)


if __name__ == "__main__":
    unittest.main()
