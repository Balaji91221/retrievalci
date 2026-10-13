from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from retrievalci import (Hit, LexicalRetriever, QueryDataset, Snapshot, SnapshotStore,
                         ValidationError, compare, evaluate, RetrievalBatch)
from retrievalci.models import GoldEvidence, EvaluationRun, metrics, read_json
from retrievalci.report import render_html

FIXTURES = Path(__file__).resolve().parents[1] / "examples" / "fixtures"


class FixedRetriever:
    name = "fixed-test-adapter"
    version = "1"

    def __init__(self, hits):
        self.hits = hits

    def retrieve(self, query, k, snapshot):
        if isinstance(self.hits, list):
            return RetrievalBatch(tuple(self.hits), snapshot.id, True, "fixed-test-index")
        return self.hits


class PartialRetriever(FixedRetriever):
    def retrieve(self, query, k, snapshot):
        if "refund" in query:
            raise RuntimeError("one query failed")
        return RetrievalBatch((Hit("help/delivery", 1),), snapshot.id, True, "partial-test-index")


class BrokenRetriever(FixedRetriever):
    def retrieve(self, query, k, snapshot):
        raise TimeoutError("upstream timed out")


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SnapshotStore(self.temp.name)
        self.dataset = QueryDataset.load(FIXTURES / "queries.json")
        self.snapshot = self.store.create(read_json(FIXTURES / "baseline.json"))
        self.baseline = evaluate(LexicalRetriever(), self.dataset, self.snapshot, k=1)

    def candidate(self, fixture):
        snapshot = self.store.create(read_json(FIXTURES / (fixture + ".json")))
        return evaluate(LexicalRetriever(), self.dataset, snapshot, k=1)

    def test_hand_calculated_metrics(self):
        gold = tuple(GoldEvidence(i, "a" * 64) for i in ["a", "b", "c"])
        result = metrics((Hit("x", 5), Hit("b", 4), Hit("a", 3)), gold)
        self.assertAlmostEqual(result["recall_at_k"], 2 / 3)
        self.assertEqual(result["reciprocal_rank"], 0.5)
        self.assertEqual(metrics((), gold), {"recall_at_k": 0.0, "reciprocal_rank": 0.0})

    def test_snapshot_hash_stable_across_input_order(self):
        corpus = read_json(FIXTURES / "baseline.json")
        corpus["documents"].reverse()
        self.assertEqual(self.store.create(corpus).id, self.snapshot.id)
        self.assertEqual(self.store.load(self.snapshot.id), self.snapshot)
        self.assertNotEqual(self.store.create(corpus, {"stop_words": ["refund"]}).id, self.snapshot.id)

    def test_modified_artifact_is_rejected(self):
        target = Path(self.temp.name) / "snapshots" / (self.snapshot.id + ".json")
        content = json.loads(target.read_text())
        content["documents"][0]["text"] += " tampered"
        target.write_text(json.dumps(content))
        with self.assertRaisesRegex(ValidationError, "hash mismatch"):
            self.store.load(self.snapshot.id)
        with self.assertRaisesRegex(ValidationError, "modified"):
            self.store.create(read_json(FIXTURES / "baseline.json"))

    def test_artifact_id_cannot_escape_store(self):
        with self.assertRaises(ValidationError):
            self.store.load("../../secret")

    def test_gold_must_be_reviewed_and_unique(self):
        raw = read_json(FIXTURES / "queries.json")
        raw["queries"][0]["reviewed"] = False
        with self.assertRaisesRegex(ValidationError, "reviewed"):
            QueryDataset.from_dict(raw)
        raw = read_json(FIXTURES / "queries.json")
        raw["queries"][0]["gold"].append(raw["queries"][0]["gold"][0])
        with self.assertRaisesRegex(ValidationError, "Duplicate gold"):
            QueryDataset.from_dict(raw)

    def test_identical_runs_pass_without_score_noise(self):
        repeated = evaluate(LexicalRetriever(), self.dataset, self.snapshot, 1)
        report = compare(self.baseline, repeated)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["regressions"], [])
        self.assertEqual(repeated.payload["summary"]["mean_recall_at_k"], 1.0)

    def test_seeded_ranking_regression_fails_and_reports_evidence(self):
        report = compare(self.baseline, self.candidate("ranking-regression"))
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(len(report["regressions"]), 1)
        self.assertEqual(report["regressions"][0]["query_id"], "refund-question")
        self.assertEqual(report["regressions"][0]["candidate_hits"][0]["evidence_id"], "noise/refund")
        self.assertEqual(report["source_changes"]["added"], ["noise/refund"])

    def test_thresholds_are_per_query_and_validated(self):
        candidate = self.candidate("ranking-regression")
        self.assertEqual(compare(self.baseline, candidate, 1, 1)["verdict"], "PASS")
        for threshold in [-1, float("nan"), float("inf"), 2, True]:
            with self.subTest(threshold=threshold), self.assertRaises(ValidationError):
                compare(self.baseline, candidate, threshold)

    def test_deleted_and_stale_gold_are_not_retrieval_misses(self):
        for fixture, expected in [("deleted-gold", "gold_deleted"), ("stale-gold", "gold_stale")]:
            with self.subTest(fixture=fixture):
                candidate = self.candidate(fixture)
                report = compare(self.baseline, candidate)
                self.assertFalse(candidate.complete)
                self.assertEqual(report["verdict"], "INCONCLUSIVE")
                self.assertEqual(report["regressions"], [])
                self.assertEqual(report["review_issues"][0]["issues"][0]["kind"], expected)
                self.assertEqual(candidate.payload["summary"]["coverage"], 0.5)

    def test_incompatible_dataset_or_k_never_pass(self):
        raw = self.dataset.payload
        raw["version"] = "new-review-version"
        changed = evaluate(LexicalRetriever(), QueryDataset.from_dict(raw), self.snapshot, 1)
        self.assertEqual(compare(self.baseline, changed)["verdict"], "INCONCLUSIVE")
        changed = evaluate(LexicalRetriever(), self.dataset, self.snapshot, 2)
        self.assertEqual(compare(self.baseline, changed)["verdict"], "INCONCLUSIVE")

    def test_retrieval_configuration_changes_are_explicit(self):
        snapshot = self.store.create(read_json(FIXTURES / "baseline.json"), {"stop_words": ["refund"]})
        candidate = evaluate(LexicalRetriever(), self.dataset, snapshot, 1)
        report = compare(self.baseline, candidate)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(report["experiment_changes"], ["config_hash"])

    def test_adapter_contract_errors_prevent_pass(self):
        bad_hits = [[Hit("missing", 1)], [Hit("policy/refunds", float("nan"))],
                    [Hit("policy/refunds", 2), Hit("policy/refunds", 1)], "not a hit list"]
        for hits in bad_hits:
            with self.subTest(hits=hits):
                candidate = evaluate(FixedRetriever(hits), self.dataset, self.snapshot, 3)
                self.assertFalse(candidate.complete)
                self.assertTrue(all(r["status"] == "error" for r in candidate.payload["results"]))
        candidate = evaluate(BrokenRetriever([]), self.dataset, self.snapshot, 1)
        self.assertEqual(compare(self.baseline, candidate)["verdict"], "INCONCLUSIVE")

    def test_empty_hits_are_valid_misses(self):
        candidate = evaluate(FixedRetriever([]), self.dataset, self.snapshot, 1)
        self.assertTrue(candidate.complete)
        self.assertEqual(candidate.payload["summary"]["mean_recall_at_k"], 0)
        self.assertEqual(compare(self.baseline, candidate)["verdict"], "FAIL")

    def test_review_status_and_no_scored_queries_prevent_pass(self):
        raw = self.dataset.payload
        raw["queries"][0]["status"] = "needs_review"
        run = evaluate(LexicalRetriever(), QueryDataset.from_dict(raw), self.snapshot, 1)
        self.assertFalse(run.complete)
        for query in raw["queries"]:
            query.update(status="unanswerable", reviewed=True, gold=[])
        run = evaluate(LexicalRetriever(), QueryDataset.from_dict(raw), self.snapshot, 1)
        self.assertFalse(run.complete)
        self.assertEqual(run.payload["summary"]["excluded_count"], 2)
        self.assertIsNone(run.payload["summary"]["mean_recall_at_k"])

    def test_partial_adapter_failure_is_visible(self):
        run = evaluate(PartialRetriever([]), self.dataset, self.snapshot, 1)
        self.assertFalse(run.complete)
        self.assertEqual(run.payload["summary"]["coverage"], 0.5)

    def test_saved_runs_roundtrip_and_inconsistent_metrics_are_rejected(self):
        identity = self.store.save_run(self.baseline)
        self.assertEqual(self.store.load_run(identity).id, identity)
        raw = self.baseline.payload
        raw["results"][0]["metrics"]["recall_at_k"] = 0
        with self.assertRaisesRegex(ValidationError, "metric mismatch"):
            EvaluationRun.from_dict(raw)
        raw = self.baseline.payload
        raw["results"].pop()
        with self.assertRaisesRegex(ValidationError, "Incomplete"):
            EvaluationRun.from_dict(raw)

    def test_comparison_validates_sdk_run_objects(self):
        raw = self.baseline.payload
        raw["summary"]["scored_count"] = 99
        with self.assertRaisesRegex(ValidationError, "summary mismatch"):
            compare(self.baseline, EvaluationRun(json.dumps(raw)))

    def test_unsupported_mapping_and_invalid_corpus_are_rejected(self):
        raw = self.baseline.payload
        raw["evidence_identity"] = "unmapped-chunks"
        with self.assertRaisesRegex(ValidationError, "identity mapping"):
            EvaluationRun.from_dict(raw)
        corpus = read_json(FIXTURES / "baseline.json")
        corpus["documents"].append(corpus["documents"][0])
        with self.assertRaisesRegex(ValidationError, "Duplicate document"):
            self.store.create(corpus)

    def test_html_escapes_evidence_text(self):
        report = compare(self.baseline, self.baseline)
        report["query_deltas"][0]["query_id"] = "<script>alert(1)</script>"
        html = render_html(report)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def command(self, *arguments):
        return subprocess.run([sys.executable, "-m", "retrievalci", *arguments, "--store", self.temp.name],
                              text=True, capture_output=True, timeout=15)

    def test_cli_lifecycle_and_exit_codes(self):
        snapshot = self.command("snapshot", str(FIXTURES / "baseline.json"))
        self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
        snapshot_id = json.loads(snapshot.stdout)["snapshot_id"]
        run = self.command("run", "--dataset", str(FIXTURES / "queries.json"), "--snapshot", snapshot_id, "--k", "1")
        self.assertEqual(run.returncode, 0, run.stderr)
        run_id = json.loads(run.stdout)["run_id"]
        report_path = str(Path(self.temp.name) / "report.html")
        same = self.command("compare", "--baseline", run_id, "--candidate", run_id, "--html", report_path)
        self.assertEqual(same.returncode, 0, same.stderr)
        self.assertTrue(Path(report_path).is_file())
        candidate_id = self.store.save_run(self.candidate("ranking-regression"))
        failed = self.command("compare", "--baseline", run_id, "--candidate", candidate_id)
        self.assertEqual(failed.returncode, 1, failed.stderr)
        missing = self.store.create(read_json(FIXTURES / "deleted-gold.json"))
        incomplete = self.command("run", "--dataset", str(FIXTURES / "queries.json"), "--snapshot", missing.id, "--k", "1")
        self.assertEqual(incomplete.returncode, 2, incomplete.stderr)
        self.assertIn("run_id", json.loads(incomplete.stdout))
        missing_id = json.loads(incomplete.stdout)["run_id"]
        incomplete_compare = self.command("compare", "--baseline", run_id, "--candidate", missing_id)
        self.assertEqual(incomplete_compare.returncode, 2, incomplete_compare.stderr)
        malformed = self.command("run", "--dataset", "missing.json", "--snapshot", snapshot_id)
        self.assertEqual(malformed.returncode, 2)


if __name__ == "__main__":
    unittest.main()
