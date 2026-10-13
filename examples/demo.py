"""Offline example: expected regression and review gates are successful demo outcomes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from retrievalci import LexicalRetriever, QueryDataset, SnapshotStore, compare, evaluate
from retrievalci.models import read_json
from retrievalci.report import render_html


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="demo-output")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    fixtures = Path(__file__).parent / "fixtures"
    store = SnapshotStore(output / "artifacts")
    dataset = QueryDataset.load(fixtures / "queries.json")
    runs = {}
    for name in ["baseline", "ranking-regression", "deleted-gold", "stale-gold"]:
        snapshot = store.create(read_json(fixtures / f"{name}.json"))
        runs[name] = evaluate(LexicalRetriever(), dataset, snapshot, k=1)
        store.save_run(runs[name])
    expected = {"baseline": "PASS", "ranking-regression": "FAIL",
                "deleted-gold": "INCONCLUSIVE", "stale-gold": "INCONCLUSIVE"}
    for name, run in runs.items():
        report = compare(runs["baseline"], run)
        (output / f"{name}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (output / f"{name}.html").write_text(render_html(report), encoding="utf-8")
        if report["verdict"] != expected[name]:
            raise SystemExit(f"Demo failed: {name}: {report['verdict']} != {expected[name]}")
        print(f"{name}: {report['verdict']} (expected)")
    print(f"Offline reports: {output.resolve()}")


if __name__ == "__main__":
    main()
