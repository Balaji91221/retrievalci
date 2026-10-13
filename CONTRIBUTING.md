# Contributing

Run `python -m pip install .`, `python -m unittest discover -s tests -v`, and
`python examples/demo.py` from a clean virtual environment. Source-only testing
can set `PYTHONPATH=src`. HTTP integration tests bind a loopback port; enable local
network access when your development sandbox blocks socket binding.

Keep retrieval scoring independent of answer generation and model judges. A new
adapter must return explicit index completeness and snapshot identity, enforce
bounded I/O, map hits to stable evidence/chunk IDs, and pass failure/timeout tests.
Custom adapters must be top-level pickleable classes because execution uses
spawned processes. Library entry points that call evaluate need a normal
`if __name__ == '__main__':` guard in scripts.

Add focused regression tests and a small reproducible fixture for behavior
changes. Never update a gold hash to make a failing gate pass without reviewing
whether the evidence is still relevant. Keep development and held-out queries
separate. New metric definitions must state their denominator and rank unit.

Maintain old artifact readers. Additive v1 fields are optional for legacy runs;
breaking schema changes must use a new version and copy-based migration. Do not
overwrite baselines. Document changed provider wire contracts and package APIs in
CHANGELOG.md. Avoid production data, credentials, and source documents without
permission in fixtures or issue reports.

The CI matrix is configured for Python 3.10, 3.12, and 3.13 on Linux, macOS, and
Windows. Configuration is not a claim all jobs have run; local verification is
recorded in IMPLEMENTATION_STATUS.md. Original code is MIT licensed. GitHub source
publication does not imply a PyPI release. Package publication remains an owner
action; this checkout does not automatically publish registry artifacts.
