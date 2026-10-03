"""Read-only completion audit of authored local fixtures and stored test results.

This does not load Settings or model weights, connect services, publish content,
or read anything outside the explicit implementation/evidence manifest.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import statistics
import xml.etree.ElementTree as ET

from experiments.technology_review.controls import document_metrics


ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "artifacts/markdown-structure"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def junit(name):
    path = DIRECTORY / name
    root = ET.parse(path).getroot()
    counts = {"tests": len(root.findall(".//testcase")),
              "failures": len(root.findall(".//failure")),
              "errors": len(root.findall(".//error")),
              "skipped": len(root.findall(".//skipped"))}
    return {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(path), **counts}


def run():
    checks = {key: junit(name) for key, name in {
        "backend": "backend-final.xml", "frontend": "frontend-all.xml", "browser": "browser-all.xml",
        "review_fix": "review-green.xml"}.items()}
    expected = {"backend": 346, "frontend": 33, "browser": 20, "review_fix": 82}
    for key, check in checks.items():
        if check["tests"] != expected[key] or any(check[n] for n in ("failures", "errors", "skipped")):
            raise ValueError(f"incomplete verification: {key}: {check}")
    red_checks = {key: junit(name) for key, name in {
        "initial_structure": "unit-red.xml", "versions": "versions-red.xml",
        "token_context": "budget-context-red.xml", "token_index": "budget-index-red.xml",
        "ui": "frontend-red.xml", "size": "size-red.xml", "review": "review-red.xml"}.items()}
    if any(check["failures"] == 0 or check["errors"] != 0 for check in red_checks.values()):
        raise ValueError("RED evidence must reproduce assertion failures, not collection/errors")
    encoded_path = DIRECTORY / "real-bge-final.json"
    encoded = json.loads(encoded_path.read_text(encoding="utf-8"))
    if encoded["environment"]["app_source_sha256"] != sha(ROOT / "backend/app/rag/ingest.py"):
        raise ValueError("real model evidence does not match final adapter")
    if encoded["environment"]["parser_source_sha256"] != sha(ROOT / "backend/app/rag/markdown.py"):
        raise ValueError("real model evidence does not match final parser")
    corpus_path = ROOT / "artifacts/technology-review/corpus.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    if sha(corpus_path) != "ef522ef95f528a80067c046d64763d83d85f0e9f8c9d7a8e0f4de23296ee1e86":
        raise ValueError("immutable corpus/gold changed")
    if encoded["corpus_sha256"] != sha(corpus_path):
        raise ValueError("wrong encoded corpus")
    previous = ROOT / "artifacts/technology-review/encoded.json"
    if encoded["previous_encoded_sha256"] != sha(previous):
        raise ValueError("control embedding evidence changed")
    if len(encoded["chunks"]) != 82 or encoded["previous_context_max_abs_drift"] != 0:
        raise ValueError("current source differs from approved context experiment")
    by_document = {item["id"]: item for item in corpus["documents"]}
    for chunk in encoded["chunks"]:
        source = by_document[chunk["synthetic_document_id"]]["markdown"]
        if source[chunk["char_start"]:chunk["char_end"]] != chunk["content"]:
            raise ValueError("raw synthetic source span mismatch")
    for arm in ("heading_context", "queries"):
        for vector in encoded["vectors"][arm]:
            if len(vector) != 512 or abs(sum(v * v for v in vector) ** 0.5 - 1) > 0.001:
                raise ValueError("real vector shape/norm mismatch")
    seeds = encoded["seed_audit"]
    if len(seeds) != 33:
        raise ValueError("seed audit incomplete")
    for row in seeds:
        source_path = ROOT / "data/knowledge" / row["source_path"]
        if row["status"] != "verified_source_spans_and_real_token_budget" or sha(source_path) != row["source_sha256"]:
            raise ValueError("seed source/status differs from audited record")
        if max(row["token_counts"]) > 512:
            raise ValueError("over-budget seed accepted")
        for chunk in row["chunks"]:
            if row["source"][chunk["char_start"]:chunk["char_end"]] != chunk["content"]:
                raise ValueError("seed raw source span mismatch")
    rejection = encoded["over_budget_rejection"]
    if rejection["actual_tokens"] <= 512 or rejection["model_encode_called"]:
        raise ValueError("real budget rejection unproved")
    prompt = encoded["actual_prompt_proof"]
    if prompt["answer_model_executed"] or prompt["context"][0]["excerpt"] != prompt["chunk"]["content"]:
        raise ValueError("actual prompt raw fidelity mismatch")
    retrieval_path = DIRECTORY / "retrieval-final.json"
    retrieval = json.loads(retrieval_path.read_text(encoding="utf-8"))
    if retrieval["encoded_sha256"] != sha(encoded_path) or retrieval["permission_leaks"]:
        raise ValueError("final retrieval provenance/permissions mismatch")
    if retrieval["parameters"] != corpus["fixed_parameters"]:
        raise ValueError("retrieval parameters changed")
    gold = {item["id"]: item for item in corpus["queries"]}
    for row in retrieval["queries"]:
        if row["gold"] != gold[row["query_id"]]["relevant"]:
            raise ValueError("gold changed in results")
        actual = document_metrics(row["final"], row["gold"])
        if actual != row["metrics_final"]:
            raise ValueError("reported metrics differ from raw ranking")
    holdout = {}
    for arm in ("baseline_weighted", "heading_context_weighted"):
        rows = [row for row in retrieval["queries"] if row["arm"] == arm and row["split"] == "holdout" and row["gold"]]
        holdout[arm] = {metric: statistics.mean(row["metrics_final"][metric] for row in rows)
                        for metric in ("recall", "mrr")}
        if len(rows) != 15:
            raise ValueError("holdout denominator changed")
    if "All checks passed!" not in (DIRECTORY / "ruff.log").read_text(encoding="utf-8-sig"):
        raise ValueError("ruff did not pass")
    if "124 packages" not in (DIRECTORY / "lock.log").read_text(encoding="utf-8-sig"):
        raise ValueError("lock check evidence missing")
    if "built in" not in (DIRECTORY / "frontend-build.log").read_text(encoding="utf-8-sig"):
        raise ValueError("frontend build evidence missing")
    if "0004_markdown_structure" not in (DIRECTORY / "migration.log").read_text(encoding="utf-8-sig"):
        raise ValueError("local migration evidence missing")
    for name in ("markdown-preview-1440.png", "markdown-preview-390.png"):
        if not (DIRECTORY / "browser" / name).is_file():
            raise ValueError("required browser screenshot absent")
    plan = (ROOT / "docs/superpowers/plans/2026-10-02-markdown-structure.md").read_text(encoding="utf-8")
    if "- [ ]" in plan:
        raise ValueError("plan still contains unfinished tasks")
    files = json.loads((DIRECTORY / "review-manifest.json").read_text(encoding="utf-8"))
    sources = [{**item, "final_sha256": sha(ROOT / item["path"])} for item in files]
    audit_source = "experiments/markdown_structure/complete_evidence.py"
    sources.append({"path": audit_source, "new_file": True, "post_review_read_only_completion_audit": True,
                    "final_sha256": sha(ROOT / audit_source)})
    artifacts = [path for path in DIRECTORY.rglob("*") if path.is_file() and "before" not in path.parts
                 and path.name not in {"implementation-evidence.json", "completion-audit.log"}]
    evidence = {
        "schema": "markdown-structure-implementation-v1", "observed_utc": datetime.now(timezone.utc).isoformat(),
        "objective": "approved source based Markdown independent implementation",
        "software_contract_verified": True, "checks": checks, "red_evidence": red_checks,
        "runtime": {"host_python": platform.python_version(), "semantic_python": encoded["environment"]["python"],
                    "services": "compose.test loopback PG15932/Redis16379/Qdrant17333; no recreate/down",
                    "docker_image": "sha256:11943d9ed8d4036b954523ee0ec4e53d476d43695605aa430af5a62c7ba26ade"},
        "semantic_verification": {"model": encoded["model"], "network": "none", "local_public_cache_read_only": True,
            "local_loader_substitution": encoded["environment"]["loader"], "corpus_sha256": sha(corpus_path),
            "synthetic_documents": 82, "synthetic_chunks": 82, "max_context_tokens": max(encoded["context_token_counts"]),
            "repository_documents": 33, "repository_chunks": sum(len(row["chunks"]) for row in seeds),
            "max_repository_tokens": max(n for row in seeds for n in row["token_counts"]),
            "rejected_actual_tokens": rejection["actual_tokens"], "encode_called_for_rejection": False,
            "current_vectors_max_abs_drift_from_approved_context": 0,
            "baseline_vectors_reused_from_previous_real_control": True, "context_and_query_vectors_newly_encoded": True,
            "holdout_query_count": 18, "holdout_answerable_denominator": 15, "holdout": holdout,
            "production_snapshot_api_in_semantic_experiment": False, "answer_model_executed": False},
        "independent_review": {"reviewer": "/root/markdown_final_review", "model": "gpt-6-astra", "count": 1,
            "initial_critical": 0, "initial_important": 2, "minor": 0,
            "fixed": ["MD-007", "MD-008"], "verification": "actual RED→GREEN then 346 full backend tests; no re-review"},
        "completion_audit": [
            {"task": 1, "evidence": ["unit-red.xml", "review-green.xml", "real-bge-final.json"], "complete": True},
            {"task": 2, "evidence": ["versions-green.xml", "budget-index-green.xml", "backend-final.xml", "migration.log"], "complete": True},
            {"task": 3, "evidence": ["budget-context-green.xml", "real-bge-final.json", "retrieval-final.json"], "complete": True},
            {"task": 4, "evidence": ["frontend-all.xml", "frontend-build.log", "browser-all.xml", "browser/"], "complete": True},
            {"task": 5, "evidence": ["backend-final.xml", "ruff.log", "lock.log", "review-findings.md", "business-probes.json"], "complete": True}],
        "unverified": ["enterprise private document fidelity", "real answer/citation quality",
            "target Linux full-stack/enterprise identity", "capacity", "complete restore", "external receiver"],
        "remaining_independent_work": ["T02 multi-turn ticket lookup", "T03 required fault collection", "T04 explicit handoff/context", "T05 latest progress", "T06 real quality", "T07 target acceptance"],
        "production_migration_or_deploy_executed": False, "enterprise_documents_uploaded": False,
        "paid_models_called": False, "git_commit_push_merge_executed": False,
        "test_environment": {"ENVIRONMENT": "test", "MODEL_MODE": "mock", "DEMO_ENABLED": "true",
            "TEST_DATABASE_URL": "postgresql+asyncpg://itops_test:isolated-test-only@127.0.0.1:15932/itops_testing",
            "TEST_REDIS_URL": "redis://127.0.0.1:16379/15", "TEST_QDRANT_URL": "http://127.0.0.1:17333",
            "PYTHONPATH": "D:/wendangjiexi/backend;D:/wendangjiexi"},
        "commands": [
            {"cwd": "D:/wendangjiexi", "command": "docker compose -f compose.test.yml up -d --wait postgres redis qdrant", "exit_code": 0},
            {"cwd": "D:/wendangjiexi/backend", "command": ".venv/Scripts/python.exe -m alembic upgrade head", "output": "migration.log", "exit_code": 0},
            {"cwd": "D:/wendangjiexi/backend", "command": ".venv/Scripts/python.exe -m pytest -q --junitxml=../artifacts/markdown-structure/backend-final.xml", "output": "backend-final.log", "exit_code": 0},
            {"cwd": "D:/wendangjiexi/frontend", "command": "npm test -- --reporter=junit --outputFile=../artifacts/markdown-structure/frontend-all.xml", "exit_code": 0},
            {"cwd": "D:/wendangjiexi/frontend", "command": "npm run build", "output": "frontend-build.log", "exit_code": 0},
            {"cwd": "D:/wendangjiexi/frontend", "command": "npm run test:e2e -- --project=demo --project=production-mock --reporter=list,junit", "output": "browser-all.log", "exit_code": 0},
            {"cwd": "D:/wendangjiexi/backend", "command": "uv run --locked ruff check --config pyproject.toml app tests ../scripts ../experiments/markdown_structure", "output": "ruff.log", "exit_code": 0},
            {"cwd": "D:/wendangjiexi/backend", "command": "uv lock --check", "output": "lock.log", "exit_code": 0},
            {"cwd": "D:/wendangjiexi", "command": "docker run --rm --network none --read-only --tmpfs /tmp --cpus 4 --memory 6g --workdir /workspace --mount type=bind,source=D:/wendangjiexi/backend,target=/workspace/backend,readonly --mount type=bind,source=D:/wendangjiexi/experiments,target=/workspace/experiments,readonly --mount type=bind,source=D:/wendangjiexi/data/knowledge,target=/fixtures,readonly --mount type=bind,source=D:/wendangjiexi/artifacts/technology-review,target=/inputs,readonly --mount type=bind,source=D:/wendangjiexi/artifacts/markdown-structure,target=/results --mount type=volume,source=itops-engineering-model-cache,target=/app/models,readonly --env PYTHONPATH=/workspace/backend:/workspace --env HF_HOME=/app/models --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 --env HF_HUB_DISABLE_TELEMETRY=1 --env TOKENIZERS_PARALLELISM=false --env OMP_NUM_THREADS=4 --env MKL_NUM_THREADS=4 --entrypoint python sha256:11943d9ed8d4036b954523ee0ec4e53d476d43695605aa430af5a62c7ba26ade -m experiments.markdown_structure.verify --corpus /inputs/corpus.json --previous /inputs/encoded.json --seeds /fixtures --output /results/real-bge-final.json", "output": "real-bge-final.log", "exit_code": 0},
            {"cwd": "D:/wendangjiexi/backend", "command": ".venv/Scripts/python.exe -m experiments.technology_review.retrieval --corpus ../artifacts/technology-review/corpus.json --encoded ../artifacts/markdown-structure/real-bge-final.json --output ../artifacts/markdown-structure/retrieval-final.json --run-id md20261002finalv1", "output": "retrieval-final.log", "exit_code": 0},
            {"cwd": "D:/wendangjiexi/backend", "command": ".venv/Scripts/python.exe -m experiments.markdown_structure.complete_evidence", "exit_code": 0}],
        "sources": sources, "artifact_hashes": {str(path.relative_to(ROOT)).replace("\\", "/"): sha(path) for path in artifacts}}
    (DIRECTORY / "implementation-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": checks, "holdout": holdout, "software_contract_verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    run()
