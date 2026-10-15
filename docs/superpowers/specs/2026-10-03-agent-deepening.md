# Approved Agent deepening specification

User approved implementation on 2026-10-03. Preserve each project's existing core and all approved pending changes.

IT: LangGraph outer workflow, maximum two additional Agentic RAG retrieval rounds, six readonly authorized MCP tools, provenance/ACL/revision-aware PostgreSQL relation retrieval (maximum two hops), controller + two independently looping specialists, durable task state, optional support/investigate mode.

Commerce: bounded Runtime/Registry/Executor/domain services, role-constrained shopping/merchant_ops/aftersales, current facts and integer minor-unit quotes, immutable separately confirmed proposals, merchant content/check specialists, versioned merchant-published keyword policies and aftersales proposals; MCP queries/proposals only; independent kind deployment and recovery drills.

Both: trusted identity never supplied by the model, short database transactions outside model calls, stable action identity/idempotency/version rechecks, global time/token/tool budget and cancellation, unknown is not absent, untrusted sources cannot grant authority, preserve legacy behavior. Public/synthetic data only. No paid model calls or production changes. Task-level recovery v1; node checkpoints deferred.

Evaluation: separate frozen acceptance (>=120 independent tasks each, commerce >=40 per mode) and development data, 3 runs, reproducible version metadata; IT 2x2 single/multi x hybrid/relation; equal tools/data/authority/budget. Zero critical violations; IT eligible citations >=95%; complex policy success +5pp with cost and P95 <=1.5x or remain experimental. Scripted controls, real infrastructure and real model ability reported separately; real model quality unverified without authorized model. Existing tests and v1 cases preserved.

Final deliverables: runnable HTTP/UI/MCP entries, architecture/contracts, frozen datasets, comparisons, faults, version list and demos. Production HA/money/private document/model quality are separate acceptances.
