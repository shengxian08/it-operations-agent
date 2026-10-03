# Offline Evaluation Report

This historical local baseline is preserved below. Its expected citations are required
supporting sources, rather than exhaustive relevance labels. The original metric named
“citation precision” was required-source coverage; it did not measure the fraction of
emitted citations that were relevant. Non-knowledge paths are excluded from retrieval.

**Production release quality is not verified.** The existing 84-case dataset has no
exhaustive `relevant_sources` labels, so true citation precision is **not measured**
and the production release gate fails. The values below are the September 25 baseline,
not a new run against the production model or a pinned production knowledge revision.

## Baseline

- Generated at: `2026-09-25T04:06:52.682262+00:00`
- Command: `python scripts/run_evaluation.py --input /app/data/eval/cases.jsonl --report /app/docs/evaluation-report.md`
- Dataset: `/app/data/eval/cases.jsonl`
- Data version (SHA-256): `8ead65f68a12`

## Metrics

| Metric | Value |
| --- | ---: |
| Cases | 84 |
| Overall pass rate | 0.964 |
| Recall@5 | 0.940 |
| Required source coverage (historically mislabeled precision) | 0.940 |
| True citation precision | Not measured |
| Final state pass rate | 1.000 |
| Tool behavior pass rate | 1.000 |
| Handoff reason pass rate | 1.000 |
| Unconfirmed ticket writes | 0 |
| P50 latency | 53.04 ms |
| P95 latency | 57.19 ms |

## Failures

Failed case IDs: `knowledge-014`, `knowledge-026`, `knowledge-036`

| Case | Failed checks |
| --- | --- |
| `knowledge-014` | citations |
| `knowledge-026` | citations |
| `knowledge-036` | citations |

## Production evaluation and release verification

Each answered case must provide `relevant_sources` as an exhaustive, reviewed list of
relevant uploaded Markdown/PDF **file basenames**. `expected_citations` identifies the
sources required to answer the case; it is not automatically an exhaustive relevance
label. Additional relevant citations are valid, and unrelated emitted sources lower
precision. Metrics count unique cited source basenames per case. Do not copy required
sources into relevance labels without reviewing all candidate sources.

Run in the worker image with the same production configuration and pinned BGE model
revision used by publication. Provide an enabled evaluation user and a conversation
owned by that user. The runtime pins the selected knowledge revision once, uses the
shared production retrieval/embedding factories, and enables structured citation
validation. The semantic dimension is read from configuration (BGE-small-zh-v1.5: 512).

```bash
python scripts/run_evaluation.py --input /evaluation/annotated.jsonl \
  --report /reports/quality.md --json-report /reports/quality.json \
  --index-revision <published-revision-id> \
  --user-id <evaluation-user-id> --conversation-id <owned-conversation-id>
python scripts/evaluate_release.py --report /reports/quality.json \
  --dataset /evaluation/annotated.jsonl --index-revision <published-revision-id> \
  --require-production-model
```

The JSON artifact records the full dataset SHA-256, runtime/model identity, pipeline
configuration, pinned revision, per-case actual outputs, metrics, and gate blockers.
The second command validates the dataset hash/revision and recomputes assertions from
saved actual outputs against the labelled dataset; it ignores stored pass flags and
summary values. Exit codes are 0 for a passing gate, 1 for a failed gate, and 2 for
invalid inputs. Recall@5 must be at least 0.80 and true citation precision at least
0.85. Every state, tool, handoff-reason and critical behavior assertion must pass;
unconfirmed ticket writes must be zero. Missing relevance labels block release.

The evaluation ticket adapter is read-only: it tests graph tool selection and ticket
lookup, and returns an unusable synthetic confirmation token. Real confirmation,
idempotency, transaction rollback and permission behavior are covered by the separate
PostgreSQL business integration tests. This graph harness does not establish those
transaction properties or concurrency capacity. A formal model evaluation has not
been run in this implementation session.

For the existing local demo only, `--demo` selects the legacy collection and mock
provider in development/test. Its process exit code measures logic regression using
required-source coverage. Its JSON still records `evaluation_kind=demo_regression`,
unmeasured precision and a failed production release gate. Staging/production reject
demo mode, and `--require-production-model` rejects this evidence.

Production imports now prepare immutable upload files and database parse jobs:

```bash
python scripts/ingest_knowledge.py --directory /corpus --actor-id <enabled-admin-id> --dry-run
python scripts/ingest_knowledge.py --directory /corpus --actor-id <enabled-admin-id>
```

Repeat imports reuse pending same-content jobs or skip unchanged active articles.
Markdown frontmatter access levels are preserved unless explicitly overridden with
`--access-level`; missing metadata and PDFs default to employee access.
Worker parsing is followed by administrator preview and queued publication through the
admin UI. The CLI does not load semantic weights or switch the active index pointer.
Directory synchronization/deletion is prohibited for production knowledge. Development
demo imports require `--demo` and append/update by default. Optional demo deletion
requires a nonempty mounted corpus, `--sync-delete --dry-run --plan-file <json>`, then
the exact reviewed file and `--confirm-plan <sha256>`; changed corpus/index inventories
invalidate the plan. Only reviewed document IDs are eligible for deletion.
