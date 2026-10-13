# RetrievalCI project plan

Regression testing for evolving knowledge bases.

**Status:** Local roadmap implementation completed in v0.2.0, with external PyPI publication and pilot gates still open. See [implementation matrix](IMPLEMENTATION_STATUS.md) and [actual usage instructions](README.md). The design below records the original proposed architecture; current source/CLI commands are documented in README. Deployment and production validation have not started. **Planning date:** 13 October 2026. Public package naming still requires an availability check.

## Goal and target users

RAG developers and knowledge-platform teams updating documents, chunkers, embedding models, or retrieval settings.

A document or indexing change can make a previously answerable question lose its supporting evidence. RetrievalCI compares versioned runs and distinguishes retrieval regressions from missing source material.

## Product hypothesis and validation

Document-aware change attribution and reproducible release gates, with retrieval quality separated from answer quality. Generic LLM evaluation is broader; start with evidence retrieval and explicit gold relevance labels.

Before expanding the implementation, interview three to five target developers. Ask for a recent concrete failure, current workaround, and a repository or sanitized fixture they can share. A successful pilot means someone other than the author can install the tool, reproduce a useful finding, and explain why it belongs in their workflow. These are validation goals, not established demand.

## First release scope

Local versioned document sets, a labeled query suite, one retrieval adapter, corpus/config hashes, top-k evidence metrics, before/after reports, and deterministic CI thresholds. Answer generation is optional and later.

**Out of scope:** No automatic gold labels accepted without review, universal RAG hosting, silent dataset rewrites, or causal claims based solely on a changed score.

## Representative workflow

Developer snapshots corpus and retrieval config → runner executes labeled queries on baseline and candidate indexes → normalizer maps hits to evidence IDs → scorer compares relevance → reporter shows changed sources and affected questions → CI applies reviewed thresholds.

## High level design

```text
Corpus snapshots + Query dataset -> Evaluation runner
Runner -> Baseline retriever / Candidate retriever
Normalized hits + Gold evidence -> Metric engine
Metrics + Document diffs -> Regression report -> CI gate
```

**Implementation stack:** Python, typed retrieval adapters, content-addressed local artifacts, JSON/HTML reports. The implemented local release deliberately uses immutable filesystem manifests/checkpoints instead of SQLite metadata. Optional hosted FastAPI/React services remain outside this release.

**Architecture choice:** Answer-only grading obscures whether retrieval failed. Choose retrieval-first evaluation for deterministic metrics. Full hosted experiment tracking can be added after local reproducibility and CI value are established.

Keep the core as a local library with thin interface adapters. Process boundaries shown above are deliberate; do not split every module into a deployed service. A dashboard is a consumer of the core results, not the owner of domain rules.

## Low level design

| Component | Contract | Responsibility and state |
|---|---|---|
| `SnapshotStore` | `create(corpus, config) -> SnapshotId` | Owns immutable hashes and artifact manifests. |
| `QueryDataset` | `load(version) -> QueryCase[]` | Owns reviewed relevance labels and dataset version. |
| `RetrieverAdapter` | `retrieve(query, k, snapshot) -> Hit[]` | Returns stable source references and scores. |
| `MetricEngine` | `score(hits, gold) -> QueryMetrics` | Calculates explicit retrieval metrics. |
| `Comparator` | `compare(base, candidate) -> Regression[]` | Owns thresholds and comparable-run checks. |
| `EvidenceReporter` | `explain(regression, sourceDiff) -> ReportItem` | Shows changes without inventing causal proof. |

### Data and state ownership

CorpusSnapshot(id, documentHashes, configHash); Document(id, revision, contentHash); QueryCase(id, query, relevantEvidenceIds, status); RetrievalRun(snapshotId, datasetVersion, adapterVersion); Hit(queryId, rank, evidenceId); Comparison(baseRun, candidateRun, thresholds). Dataset labels can be active, needs_review, or unanswerable.

### Invariants

Baseline and candidate comparisons identify dataset and configuration differences. Gold evidence is versioned independently of generated answers. Deleted relevant evidence flags a dataset/corpus issue. Unsupported evidence mapping does not count as a clean retrieval miss. Baselines are immutable.

### Failure and concurrency behavior

Retry transient embedding/retrieval reads with bounded backoff and record attempts. Partial runs fail the completeness gate. Interrupted runs can resume only with matching immutable input hashes. Chunk IDs must map to stable source spans or reviewed evidence units so chunker changes remain comparable.

## SOLID decisions

- **Single responsibility:** SnapshotStore owns input interpretation; EvidenceReporter owns its separate output or integration concern. Domain evaluation does not move into CLI handlers.
- **Open closed:** introduce a new supported adapter behind an existing contract; add new domain behavior explicitly rather than adding hidden project-name switches.
- **Liskov substitution:** adapters must preserve documented result, error, cancellation, and completeness semantics. Run the same contract fixtures against every implementation; unsupported behavior must be explicit.
- **Interface segregation:** separate inspection from mutation and execution from reporting. Consumers receive only the operations they use.
- **Dependency inversion:** domain logic consumes typed contracts; the CLI or composition root wires concrete filesystem, transport, and provider implementations. Pure transformations stay functions instead of gaining unnecessary interfaces.

## LLM system design

No judge needed for initial retrieval metrics. Later evaluate answer grounding and citation support separately, with human-calibrated judgments. Do not let the answer generator supply its own ground truth. Keep development queries separate from held-out evaluation queries.

When optional model calls are introduced, version prompts and output schemas, validate every response, bound tokens/time/cost, and store source references. Treat retrieved files and tool output as data. Keep model output out of authorization and deterministic pass/fail decisions. Document which content leaves the machine.

## Installation and integration design

The following command names record the original proposed interfaces; actual local commands are in README. Original code is MIT licensed. Reserve a public package name and publish only after the clean-install gate passes.

```text
retrievalci snapshot ./docs --name candidate
retrievalci run --dataset queries.json --snapshot candidate
retrievalci compare --baseline baseline-run --candidate candidate-run
```

**Public SDK contract:**

```text
evaluate(retriever, dataset, snapshot, config) -> EvaluationRun
```

Provide one end-to-end example with pinned supported dependencies. Keep the CLI a wrapper over the public SDK. Document configuration discovery, schema version, error categories, supported platforms, and exit codes. For check-oriented commands, distinguish success, failed checks, and incomplete/infrastructure failure. MCP integrations should expose narrow tools and validate configured roots; a plugin packages instructions and connection configuration rather than duplicating the engine.

## Repository structure

```text
src/
  core/             # Domain types, state, and evaluation
  adapters/         # External protocols, analyzers, runners, or storage
  cli/              # User commands and composition
tests/
  unit/
  integration/
  fixtures/
examples/
docs/
  decisions/
  api/
```

Add a report UI or server directory only when that milestone begins. Keep runtime artifacts and any credential files out of version control. Do not create a shared framework spanning these seven independent projects.

## Implementation roadmap

| Phase | Working deliverable | Verification gate |
|---|---|---|
| 1. Define evidence identity | Implemented: JSON/directory ingestion, stable source IDs, reviewed paragraph spans and chunk maps. | Chunk grouping and explicit file renames remain comparable; edits force review. |
| 2. Implement retrieval adapter | Implemented: lexical/HTTP adapters, subprocess deadlines, attempts, checkpoints, and index completeness. | Controlled HTTP, timeout, retry, resume, and partial-index tests. |
| 3. Add metrics | Implemented: recall/MRR at raw retrieval-unit ranks, with deduplicated evidence. | Hand-calculated metrics and chunk normalization tests. |
| 4. Build comparison gate | Implemented: compatible-input/per-query gates and separate stale/deleted-gold findings. | Incomplete/partial runs never pass; seeded regressions are detected. |
| 5. Add explanations | Implemented: source diffs, rename details, expandable hit/gold evidence and offline query filtering. | Escaping/source-diff tests; no unsupported causal claims. |
| 6. Package and pilot | Local package, clean-install validation, CI configuration and release docs implemented. PyPI publication/pilot are external remaining gates. | No PyPI release or independent pilot is claimed. |

Proceed in order. Each phase should finish with a runnable example and its verification evidence. Size the next phase after measuring the current one; no fixed delivery date is assumed.

## Testing and evaluation

Fixtures for relevant-document deletion, chunk-boundary changes, ranking regression, duplicate chunks, renamed documents, stale labels, partial index builds, adapter timeouts, and identical baseline/candidate runs.

Report evidence recall@k, reciprocal rank where applicable, query coverage, and latency. Define treatment of multiple relevant units explicitly. Release gate: identical runs show no regression; seeded retrieval regressions are detected; stale labels are flagged separately.

Use unit tests for pure rules and integration tests at external boundaries. Maintain a held-out set for model-dependent behavior. Record run counts, versions, and incomplete cases. Do not call a system production-ready based only on seeded fixtures.

## Operations and release

- Start local-first. Log structured event IDs, durations, completeness, and errors without secrets; make detailed source capture explicit.
- Version configuration and stored artifact schemas. Validate compatibility before loading older artifacts; migrate a copy and retain the original.
- Pin supported integration versions in examples and test the documented minimum/current range before releases.
- Build distributable artifacts in CI and test installation in a clean environment. Publish release notes, a changelog, contribution instructions, and a security-reporting route.
- Roll back by pinning the previous package version. Keep baseline artifacts readable; do not overwrite them during a failed upgrade.
- A future hosted dashboard requires authentication, tenant isolation, retention/deletion controls, job quotas, and tested backups. Those are a separate release, not implicit MVP features.

## Learning outcomes

Build the deterministic core to practice data modeling and LLD. Trace the process and storage boundaries to practice HLD. Package the CLI/SDK to learn API stability and developer experience. Add model evaluation only where the product needs it. Use the optional report UI to learn full-stack integration around meaningful domain results.

## Open decisions and risks

Gold relevance maintenance is ongoing work. Changes to task intent may invalidate old labels. Numerical thresholds need user-specific calibration, not universal defaults. Provider model/version drift can reduce reproducibility.

The implemented local platform/dependencies and MIT license are documented. Remaining external release inputs are public naming checks, the configured compatibility matrix, and pilot users; these are not reasons to expand the local implementation scope.

## Delivery checklist

- [ ] Product hypothesis tested with target developers
- [x] First vertical slice implemented
- [x] Domain and controlled local integration verification gates passed
- [ ] Model-dependent behavior evaluated, if present
- [x] Clean-install example verified
- [x] Local documentation and package artifacts reviewed
- [ ] PyPI package release published
- [ ] Independent pilot completed
