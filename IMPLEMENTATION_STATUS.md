# Implementation status

Version 0.2.0, local development release, 13 October 2026. The in-scope local
retrieval pipeline is implemented. GitHub source publication is separate from a
PyPI package release. No PyPI publication, independent pilot, production
validation, or hosted platform is claimed.

## Implementation matrix

| Plan capability | Implemented result | Verification |
|---|---|---|
| Versioned input/evidence identity | Hash-addressed snapshots; UTF-8 directory + JSON ingestion; explicit stable source IDs; Unicode source spans | Hash corruption, rename, changed-span, and source-offset tests |
| Chunk changes | Reviewed paragraph evidence independent of chunk grouping; raw chunk hits normalize/deduplicate at original ranks | Chunk-size and duplicate-chunk tests |
| Retrieval adapters | Deterministic lexical adapter plus explicit HTTP JSON provider | Controlled loopback HTTP contract/retry tests |
| Completeness | Snapshot/index attestation, partial-index errors, coverage, no scored-query false passes | Partial index, wrong snapshot, malformed/unmapped hits, exclusions |
| Bounded failure handling | Spawned process deadline; bounded transient retries/backoff; attempt records | Hanging worker killed; retry count and cleanup checked |
| Resume | Immutable input signature and per-query checkpoint records; successful results reused | Partial-resume counter test, input mismatch, corrupted checkpoint rejection |
| Deterministic metrics | Recall@k, reciprocal rank, macro means, per-query thresholds | Hand-computed multiple-gold fixtures |
| Comparison/explanations | Strict compatible-input gate; stale/deleted gold separated; source diffs/rename details | Seeded rank regression and stale-label tests |
| Local report UI | Offline query/regression filtering, expandable evidence, source diffs | Escaping tests and generated demo reports |
| Packaging/CI/docs | SDK/CLI, wheel metadata, CI matrix, contribution/security/changelog docs | Source and clean-install tests; CI configured, not claimed executed |
| Held-out data | Separate synthetic corpus/query suite | Separate suite evaluated; no real-user validation claimed |

## Verification

**37 tests passed** from the source checkout and again from a v0.2.0 wheel
installed with `--no-index --no-deps` into a fresh virtual environment. Both source
and installed demos produced PASS, FAIL, INCONCLUSIVE, INCONCLUSIVE as expected.
The report's embedded JavaScript also passed `node --check` after extraction.
These checks ran locally on macOS/Python 3.10.10. The full suite
requires a loopback socket for the controlled HTTP provider. A restricted sandbox
that prohibits socket binding must allow this local test, rather than silently
skip it. The configured cross-platform GitHub CI matrix has not run in this chat.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 examples/demo.py
python3 -m pip wheel . --no-build-isolation --no-deps --wheel-dir /tmp/retrievalci-v2-wheel
```

## Explicit boundaries

- Source evidence is reviewed paragraph-sized text, not automatic semantic matching
  across rewritten documents. Paragraph insertion/reordering can require re-review.
- No PDF/OCR, embeddings, automatic relevance labels, or optional answer grading.
- Provider index completeness is an explicit attestation, not externally proven.
- Adapters are trusted Python code. Deadlines contain hangs, not malicious code;
  Windows does not provide descendant-process-tree containment.
- Hashes detect changes, not malicious replacement of both artifacts and IDs.
- Content-addressed filesystem artifacts deliberately replace the initial plan's
  SQLite metadata. No hosted dashboard/database service is required.
- Generic HTTP integration is implemented/tested against a controlled service;
  no third-party provider account or real production index was accessed.

## Owner/external release work

Original code is MIT licensed. Select an available PyPI package name; verify the
repository's private security-reporting route; run the configured CI matrix; review
release artifacts; publish to PyPI only with owner authorization; interview users
and conduct an independent pilot with representative data. GitHub source publication
does not complete these gates. Synthetic tests do not replace these activities.
