# Held-out synthetic fixture

This corpus and four query labels are separate from the development refund/delivery
fixtures. They exercise source-span ingestion and the final pipeline without
fitting a model or prompt. The labels were deliberately authored with the fixture;
this is not independent human review, a representative benchmark, or evidence of
real-world demand. Do not use these questions to tune a future retriever and still
call them held out. Record any future transition into a development set.

Use `sources/`, `source-ids.json`, and `queries.json` together. Files under this
folder outside `sources/` must not be ingested as source material.
