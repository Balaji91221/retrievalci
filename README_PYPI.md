# RetrievalCI

Catch retrieval regressions before they reach your answers.

RetrievalCI snapshots a corpus, evaluates reviewed queries and compares baseline/candidate retrieval runs. It separates ranking regressions from deleted or changed gold evidence, then produces JSON and offline HTML reports.

[Source](https://github.com/Balaji91221/retrievalci) · [Usage guide](https://github.com/Balaji91221/retrievalci/blob/main/docs/usage.md) · [Architecture](https://github.com/Balaji91221/retrievalci/blob/main/docs/architecture.md) · [Issues](https://github.com/Balaji91221/retrievalci/issues)

## Install

Requires Python 3.10+. The runtime uses the standard library; the bundled lexical retriever needs no model account or database.

```bash
python -m pip install retrievalci
retrievalci --help
```

Create a snapshot of your own UTF-8 Markdown/text corpus:

```bash
retrievalci snapshot /path/to/documents --store .retrievalci
```

The command prints a snapshot ID and evidence manifest. Reviewed query labels and compatible baseline/candidate runs are required for a regression comparison.

## A small SDK example

Save this as a Python script and run it normally. The entry-point guard is required because retrieval attempts use spawned worker processes.

```python
import hashlib
from retrievalci import LexicalRetriever, QueryDataset, Snapshot, compare, evaluate


def main():
    text = "Refunds are available within 30 days."
    snapshot = Snapshot.from_corpus({
        "schema_version": 1,
        "documents": [{"id": "refund-policy", "text": text}],
    })
    dataset = QueryDataset.from_dict({
        "schema_version": 1,
        "version": "example-v1",
        "queries": [{
            "id": "refund-question", "query": "refunds",
            "status": "active", "reviewed": True,
            "gold": [{"evidence_id": "refund-policy",
                      "content_hash": hashlib.sha256(text.encode()).hexdigest()}],
        }],
    })
    baseline = evaluate(LexicalRetriever(), dataset, snapshot, k=1)
    print(compare(baseline, baseline)["verdict"])  # PASS: a wiring smoke check.


if __name__ == "__main__":
    main()
```

The self-comparison above checks integration. The [four-case repository demo](https://github.com/Balaji91221/retrievalci/blob/main/examples/demo.py) exercises an actual seeded ranking regression and separate stale/deleted evidence outcomes. Fixtures, tests and docs are included in the source distribution; the wheel installs the library and CLI.

## Integrate and gate changes

- Implement `RetrieverAdapter.retrieve(query, k, snapshot)` for your retriever, returning a `RetrievalBatch` with ordered hits and explicit index completeness.
- Use `HTTPRetriever` or `--provider-url` to connect a service that implements the documented JSON contract.
- Save immutable snapshots/runs with `SnapshotStore`; compare per-query recall@k and reciprocal rank.
- Use bounded retries and checkpoint resume for incomplete evaluations.

```bash
retrievalci compare --baseline BASELINE_RUN_ID --candidate CANDIDATE_RUN_ID \
  --store .retrievalci --json comparison.json --html comparison.html
```

Use actual stored run IDs for the same reviewed dataset, k and evidence identity. The compare command returns exit 0 for PASS, 1 for FAIL and 2 for INCONCLUSIVE. An incomplete run cannot pass. RetrievalCI does not provide an MCP server.

![RetrievalCI local ranking-regression report](https://raw.githubusercontent.com/Balaji91221/retrievalci/main/docs/assets/demo.png)

This offline report comes from authored fixture data, not a production benchmark.

## Boundaries

The tool evaluates retrieval evidence, not generated-answer quality. Labels need human review. Provider index completeness is declared, not independently proven. Adapters are trusted Python code; process deadlines contain hangs but do not sandbox malicious code. Managed embeddings, PDF/OCR and hosted dashboards are outside this release.

See [full usage and provider contract](https://github.com/Balaji91221/retrievalci/blob/main/docs/usage.md), [recorded verification](https://github.com/Balaji91221/retrievalci/blob/main/IMPLEMENTATION_STATUS.md), [security reporting](https://github.com/Balaji91221/retrievalci/blob/main/SECURITY.md), and [release notes](https://github.com/Balaji91221/retrievalci/blob/main/CHANGELOG.md).

Version 0.2.0 is a development release. Original code is [MIT licensed](https://github.com/Balaji91221/retrievalci/blob/main/LICENSE). Independent pilots and production validation are separate from package availability.
