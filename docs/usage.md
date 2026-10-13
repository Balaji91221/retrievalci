# RetrievalCI usage guide

[Back to README](../README.md) · [Architecture](architecture.md)

Run commands from the repository root unless a command says otherwise.

Evidence-aware regression checks for retrieval systems. Snapshot a corpus, run
reviewed queries against baseline/candidate retrievers, and distinguish ranking
regressions from missing or stale source evidence.

Version 0.2.0 is a development release with public source on GitHub. Install with
`python -m pip install retrievalci`. It is not production validated or a hosted
service. Distribution name: `retrievalci`;
Python import and CLI: `retrievalci`. Runtime dependencies: Python >=3.10 standard
library only. Local lexical retrieval requires no model account or network.

## Install and try

```bash
git clone https://github.com/Balaji91221/retrievalci.git
cd retrievalci
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
retrievalci --help
python examples/demo.py
python -m unittest discover -s tests -v
```

On Windows activate `.venv\Scripts\activate`. For offline installation with
setuptools >=61 and wheel already available, use
`python -m pip install --no-build-isolation --no-deps .`. Source-only execution is
also supported with `PYTHONPATH=src python3 -m retrievalci --help`.

The demo writes offline JSON/HTML reports to `demo-output/` and verifies:

| Fixture | Expected gate | Explanation |
|---|---|---|
| Identical baseline | PASS | No metric drop |
| Added ranking distractor | FAIL | Existing relevant evidence was displaced |
| Deleted gold source | INCONCLUSIVE | Corpus/labels need review |
| Changed gold content | INCONCLUSIVE | The reviewed hash is stale |

The HTML report provides query search, a regressions filter, expandable hit/gold
records, source diffs, rename details, and full machine-readable evidence. It uses
no remote JavaScript or network resources.

## Snapshot a JSON corpus or raw directory

```bash
retrievalci snapshot examples/fixtures/baseline.json --store .retrievalci
retrievalci snapshot examples/heldout/sources \
  --source-ids examples/heldout/source-ids.json --chunk-size 2 --store .retrievalci
```

Snapshot IDs are immutable full SHA-256 digests, not mutable names. JSON corpus
input has `schema_version: 1` and `documents: [{"id": "stable-id", "text": "..."}]`.
Optional `--config config.json` hashes retriever settings into the snapshot.

Directory ingestion supports UTF-8 `.md` and `.txt` files, rejects symlinks, and
limits each file to 2 MB by default in the SDK. Each source becomes reviewed
paragraph evidence plus exact Unicode-character offsets. `--chunk-size` groups
paragraphs into retrieval chunks; **chunk boundaries do not change gold IDs**.
`source-ids.json` is a complete relative-path-to-stable-source-ID map:

```json
{"access.md": "heldout/access", "workspace.txt": "heldout/workspace"}
```

A file rename remains comparable when its stable ID is retained in that manifest.
Without the manifest, the relative path is the source ID; renaming it becomes
removed/new evidence. Paragraph IDs are `source-id#paragraph:N`. Inserting,
reordering, or changing paragraphs can invalidate their reviewed labels; hashes
force re-review rather than guessing identity. This is not semantic alignment of
arbitrary rewritten documents. Binary/PDF/OCR ingestion is outside this release.

Snapshots preserve full source text, hashes, spans, chunks, and config. Artifact
writes are atomic and never replace an existing content-addressed file. Hashes
are checked when loading. Retain old snapshots when changing ingestion settings.

## Review queries and evaluate

```bash
retrievalci run --dataset examples/fixtures/queries.json \
  --snapshot SNAPSHOT_ID --k 1 --store .retrievalci
```

Use the actual snapshot ID returned by the matching `snapshot` command. Query
JSON has `schema_version: 1`, a nonempty `version`, and `queries`:

```json
{
  "id": "refund-question",
  "query": "refund",
  "status": "active",
  "reviewed": true,
  "gold": [
    {"evidence_id": "policy/refunds", "content_hash": "REPLACE_WITH_PRINTED_DOCUMENT_SHA256"}
  ]
}
```

The placeholder is deliberately invalid. Review relevance and copy the exact
hash shown by `snapshot` before marking a label reviewed. The tool checks the
review declaration, not whether a human genuinely performed the review.

- `active`: reviewed, nonempty, unique gold IDs/hashes are required.
- `needs_review`: cannot pass the completeness gate.
- `unanswerable`: reviewed=true and no gold. Explicitly excluded from retrieval
  metrics. No-answer/answer-generation quality is not evaluated.

Gold source deletion and hash changes are separate review issues, not numerical
retrieval misses. Entirely excluded or partially completed suites cannot pass.

## Comparison and CI gate

```bash
retrievalci compare --baseline BASELINE_RUN_ID --candidate CANDIDATE_RUN_ID \
  --store .retrievalci --json comparison.json --html comparison.html
```

Exit codes: `0` success, `1` metric regression, `2` incomplete/incompatible/review
required/infrastructure error. A complete `run` can have zero recall; the `compare`
command is the regression gate. Partial runs are saved for diagnosis with exit 2.

Runs must use the same dataset hash, k, and evidence identity. Adapter versions and
retrieval configurations may change; the comparison identifies those experiment
changes. `--max-recall-drop 0.1 --max-rr-drop 0.1` allows per-query drops up to 0.1.
Defaults are zero. Improvements elsewhere cannot hide a query's regression.

**Metric semantics:** recall@k is the fraction of reviewed gold units covered by
the first k retrieved units. Mapped chunks expand into deduplicated evidence while
preserving their retrieval rank. Two relevant paragraphs in the first chunk both
have rank 1. Reciprocal rank is 1 / the first relevant retrieval-unit rank, or 0.
The k budget counts raw chunks/documents, not expanded paragraphs. Macro means
cover scored queries only; coverage counts scored/non-excluded queries. Partial
means are diagnostic and never constitute a passing gate.

After evidence re-review, version the dataset and evaluate both compatible sides
against that label set, or explicitly establish a new baseline. The tool never
rewrites labels or silently compares different query suites. Source/score changes
are observations, not proof of causation.

## Bounded execution and resume

Each adapter attempt runs in a fresh spawned process. Hard deadlines include
retrieval work; transient connection/timeouts retry with capped exponential
backoff. All attempts record outcomes and latency. Input/configuration/contract
errors are not retried. CLI defaults are five seconds and one additional retry.

```bash
retrievalci run --dataset queries.json --snapshot SNAPSHOT_ID --k 3 \
  --timeout 5 --retries 2 --backoff 0.1 --checkpoint .checkpoints/experiment-a

retrievalci run --dataset queries.json --snapshot SNAPSHOT_ID --k 3 \
  --timeout 5 --retries 2 --backoff 0.1 --checkpoint .checkpoints/experiment-a --resume
```

A checkpoint directory belongs to one exact snapshot/dataset/adapter/config/k/
execution-policy combination. Successful query results are immutable and reused;
failed or interrupted queries rerun. Changed inputs and corrupted checkpoints are
errors. `--max-queries 1` intentionally creates a partial run to exercise recovery.
The first call without `--resume` refuses existing checkpoints. Resuming a missing
checkpoint is also an error. An interrupted in-flight query reruns from its start.

Subprocesses contain hangs, not malicious adapters. POSIX workers own a process
group, which is terminated on cleanup. Windows cleanup terminates the worker;
untrusted descendant-process containment is not provided.

## SDK

Scripts must guard their entry point because adapter execution uses `spawn`.

```python
from retrievalci import (
    ExecutionPolicy, LexicalRetriever, QueryDataset, SnapshotStore,
    compare, evaluate, ingest_directory,
)


def main():
    snapshot = ingest_directory("examples/heldout/sources", source_ids={
        "access.md": "heldout/access", "workspace.txt": "heldout/workspace",
    })
    store = SnapshotStore(".retrievalci")
    store.save_snapshot(snapshot)
    dataset = QueryDataset.load("examples/heldout/queries.json")
    baseline = evaluate(LexicalRetriever(), dataset, snapshot, k=1,
                        policy=ExecutionPolicy(timeout_s=5, max_retries=1))
    run_id = store.save_run(baseline)
    report = compare(store.load_run(run_id), baseline)
    assert report["verdict"] == "PASS"


if __name__ == "__main__":
    main()
```

Adapters are top-level pickleable classes with `name`, `version`, optional
JSON-serializable `configuration`, and
`retrieve(query, k, snapshot) -> RetrievalBatch`. A batch declares:

```python
RetrievalBatch(
    hits=(Hit("known-evidence-or-chunk-id", 0.9),),
    snapshot_id=snapshot.id,
    index_complete=True,
    index_id="reviewed-provider-index-version",
)
```

The framework rejects incomplete indexes, wrong snapshots, unknown IDs, duplicate
raw IDs, excessive hits, and non-finite scores. Overlapping chunks are deduplicated
by evidence ID at the earliest rank. Index completeness is provider-declared,
not a proof of remote index correctness. Providers must check their actual index.
Legacy list-returning adapters require explicit SDK opt-in
`require_index_attestation=False`; the assumption appears in the trace. CLI runs
always require explicit attestation.

The lexical adapter uses Unicode words, casefolding, term frequency divided by
sqrt(document length), and stable ID tie breaking. Config keys are `stop_words`
(list of strings) and `chunk_mode` (boolean). `chunk_mode=true` retrieves mapped
chunks instead of individual evidence paragraphs. It is a deterministic baseline,
not an embedding model.

## HTTP provider integration

Run the controlled provider fixture in a separate terminal:

```bash
python examples/http_provider.py --snapshot SNAPSHOT_ID --store .retrievalci
```

Then evaluate through the actual HTTP adapter:

```bash
retrievalci run --dataset queries.json --snapshot SNAPSHOT_ID --k 3 \
  --provider-url http://127.0.0.1:8765/retrieve --provider-version fixture-v1
```

A provider receives JSON `{schema_version, query, k, snapshot_id}` by POST. It
returns `{schema_version: 1, snapshot_id, index_complete, index_id, hits}`, where
hits contain `evidence_id` and finite `score`. No corpus text is sent automatically.
Use `--token-env MY_PROVIDER_TOKEN` for an explicitly configured bearer token.

Non-loopback endpoints must use HTTPS. TLS verification is enabled, redirects
are rejected, proxy environment settings ignored, response bodies capped at 1 MB,
and credentials/query parameters prohibited in URLs. 408/429/500/502/503/504 and
connection/timeouts are transient. Other HTTP/schema errors fail immediately.
Do not put secrets into adapter configuration, source IDs, or error messages.

## Architecture, artifacts, and release status

```text
Directory/JSON -> ingestion + source mapping -> immutable snapshot
Reviewed dataset + snapshot -> bounded adapter worker -> normalized evidence
                                 |                         |
                         immutable checkpoints       metrics/run record
                                                           |
Baseline + candidate -> compatibility/completeness gate -> offline report
```

Domain models/metrics remain separate from ingestion, providers, subprocess
execution, checkpoint/artifact storage, reporting, and CLI composition. The
initial plan's optional SQLite metadata is replaced by simpler content-addressed
files; no database service is needed. Existing v1 artifacts remain readable.
Roll back by pinning the old package and keeping its baseline artifacts; do not
expect an old reader to understand newly generated span-mapped records.

`examples/heldout/` is a separate synthetic query suite; no model or prompt was
fit to it. It is not a real-user benchmark or independent human review. The
configured CI matrix tests wheel installation, the full suite, CLI, and demos on
Python 3.10/3.12/3.13 across Linux/macOS/Windows. See IMPLEMENTATION_STATUS.md for
what was actually executed locally.

See CONTRIBUTING.md, SECURITY.md, CHANGELOG.md, and PROJECT_PLAN.md. The original code is MIT licensed.
Independent pilots and production validation remain separate work.
Use [SECURITY.md](../SECURITY.md) for private reporting. Hosted dashboards, multi-tenancy, PDF/OCR,
embedding providers, and optional answer evaluation are outside this local release.
