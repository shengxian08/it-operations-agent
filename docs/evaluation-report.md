# Offline Evaluation Report

Generated from the fixed local evaluation set. Expected citations are required
supporting sources rather than an exhaustive relevance list. Citation correctness
measures whether answers that emit citations include those sources; retrieval recall
captures missing evidence. Non-knowledge paths are excluded.

## Baseline

- Generated at: `2026-08-12T07:54:23.343401+00:00`
- Command: `python scripts/run_evaluation.py --input /app/data/eval/cases.jsonl --report /app/docs/evaluation-report.md`
- Dataset: `/app/data/eval/cases.jsonl`
- Data version (SHA-256): `731da1e2acf7`

## Metrics

| Metric | Value |
| --- | ---: |
| Cases | 84 |
| Overall pass rate | 0.952 |
| Recall@5 | 0.920 |
| Citation precision | 0.939 |
| Final state pass rate | 0.988 |
| Tool behavior pass rate | 1.000 |
| Handoff reason pass rate | 0.988 |
| Unconfirmed ticket writes | 0 |
| P50 latency | 53.02 ms |
| P95 latency | 57.88 ms |

## Failures

Failed case IDs: `knowledge-001`, `knowledge-014`, `knowledge-026`, `knowledge-037`

| Case | Failed checks |
| --- | --- |
| `knowledge-001` | citations |
| `knowledge-014` | citations |
| `knowledge-026` | citations |
| `knowledge-037` | final_state, citations, handoff_reason |
