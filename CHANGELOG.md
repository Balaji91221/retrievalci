# Changelog

## 0.2.0 — local development release, 13 October 2026

- Prepared public `retrievalci` distributions with SPDX license metadata, a PyPI description and complete authored source-distribution fixtures/docs.

- Added UTF-8 directory ingestion, explicit stable source IDs, reviewed paragraph
  evidence, source spans, and chunk-to-evidence mapping.
- Added subprocess deadlines, bounded transient retries, attempt traces, partial
  index checks, and immutable resumable checkpoints.
- Added opt-in JSON HTTP adapter and controlled local provider fixture.
- Added source diffs, rename detection, query filtering, and expandable evidence in
  offline reports.
- Added separate held-out synthetic fixtures and packaging CI configuration.
- Adapters now declare index completeness by default. Legacy list adapters require
  explicit `require_index_attestation=False`; that assumption is recorded.
- Existing schema-v1 corpus snapshots and evaluation records remain readable.

## 0.1.0 — initial local slice

Content-addressed JSON corpus snapshots, reviewed gold labels, lexical retrieval,
recall/MRR metrics, comparison gates, and static reports. Never publicly published.
