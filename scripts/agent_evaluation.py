"""Frozen three-repeat evaluation jobs and tier-separated evidence reports.

External adapters run the same jobs against a provisioned application/model.
Scripted controls never count as measured task ability or strategy improvement.
"""

import argparse
import asyncio
import hashlib
import importlib
import importlib.metadata
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "src")]
DATA = ROOT / "eval/agent-deepening"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load():
    freeze = json.loads((DATA / "freeze.json").read_text(encoding="utf-8"))
    raw = (DATA / "acceptance.jsonl").read_bytes()
    if digest(raw) != freeze["dataset_sha256"]:
        raise ValueError("Frozen acceptance set changed; publish a new dataset version")
    cases = [json.loads(line) for line in raw.decode().splitlines()]
    dev = [
        json.loads(line)
        for line in (DATA / "development.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    if (
        len(cases) < 120
        or len({c["id"] for c in cases}) != len(cases)
        or len({c["prompt"] for c in cases}) != len(cases)
    ):
        raise ValueError("Acceptance tasks must be unique and independent")
    if {c["id"] for c in cases} & {c["id"] for c in dev} or {c["prompt"] for c in cases} & {
        c["prompt"] for c in dev
    }:
        raise ValueError("Development and acceptance sets overlap")
    if freeze["project"] == "commerce" and any(
        sum(c["task_mode"] == m for c in cases) < 40
        for m in ("shopping", "merchant_ops", "aftersales")
    ):
        raise ValueError("Every commerce mode needs 40 tasks")
    return freeze, cases


def metadata(freeze):
    source_paths = []
    for base in ("backend/app", "src", "frontend/src", "scripts"):
        directory = ROOT / base
        if directory.exists():
            source_paths.extend(
                p
                for p in directory.rglob("*")
                if p.suffix in {".py", ".ts", ".tsx"} and "__pycache__" not in p.parts
            )
    code = hashlib.sha256()
    for path in sorted(source_paths):
        code.update(path.relative_to(ROOT).as_posix().encode())
        code.update(path.read_bytes())

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()

    return {
        "code_commit": git("rev-parse", "HEAD"),
        "working_tree_dirty": bool(git("status", "--porcelain")),
        "code_sha256": code.hexdigest(),
        "dataset_sha256": freeze["dataset_sha256"],
        "tool_contract_version": freeze["tool_contract_version"],
        "prompt_version": freeze["prompt_version"],
        "data_version": freeze["data_version"],
        "mcp_sdk": importlib.metadata.version("mcp"),
        "python": sys.version.split()[0],
        "model": "adapter must record exact provider/model/revision",
        "knowledge_version": "adapter must record per-observation revisions",
    }


def jobs(freeze, cases, meta):
    for strategy in freeze["strategies"]:
        for case in cases:
            for repeat in range(1, freeze["repeats"] + 1):
                yield {
                    "case": case,
                    "strategy": strategy,
                    "repeat": repeat,
                    "metadata": meta,
                    "budget": {
                        k: freeze[k]
                        for k in (
                            "max_model_calls",
                            "max_tool_calls",
                            "max_reserved_tokens",
                            "timeout_seconds",
                        )
                    },
                }


def summarize(records, freeze):
    summaries = []
    for strategy in freeze["strategies"]:
        selected = [r for r in records if r["strategy"] == strategy["id"]]
        tasks = {}
        for r in selected:
            tasks.setdefault(r["case_id"], []).append(r)
        times = sorted(r["elapsed_ms"] for r in selected)
        controls = sum(all(run["control_passed"] for run in task) for task in tasks.values())
        summaries.append(
            {
                "strategy": strategy["id"],
                "tasks": len(tasks),
                "runs": len(selected),
                "stable_control_tasks": controls,
                "critical_violations": sum(r["critical_violations"] for r in selected),
                "p95_control_elapsed_ms": times[max(0, int(len(times) * 0.95) - 1)]
                if times
                else None,
                "model_task_success_rate": None,
                "citation_support_rate": None,
                "successful_task_cost": None,
            }
        )
    return {
        "schema_version": 2,
        "evidence_tier": records[0]["tier"] if records else "unverified",
        "strategy_summaries": summaries,
        "real_model_quality": "unverified",
        "adoption": {
            "default": "single",
            "complex_strategy": "experimental",
            "reason": "No independent real-model task and citation review; the quality/cost/latency gates remain unverified",
        },
        "label_status": freeze["source_note"],
        "metadata": records[0]["metadata"] if records else None,
    }


async def run(args, freeze, cases, meta):
    if args.adapter:
        module, name = args.adapter.split(":", 1)
        adapter = getattr(importlib.import_module(module), name)
    else:
        if args.tier != "scripted":
            raise ValueError(
                "A provisioned real-services/real-model adapter is required for this tier"
            )
        module = (
            "app.agent.evaluation_control"
            if freeze["project"] == "it"
            else "ecommerce_agent.evaluation.agent_control"
        )
        adapter = importlib.import_module(module).run_case
    records = []
    for job in jobs(freeze, cases, meta):
        start = time.perf_counter()
        result = await adapter(job)
        if (
            result.get("tier") != args.tier
            or not isinstance(result.get("checks"), dict)
            or not result["checks"]
        ):
            raise ValueError("Adapter must return explicit matching-tier checks and provenance")
        if args.tier == "real-model" and not result.get("model_id"):
            raise ValueError("Real model evidence must identify the actual model")
        if args.tier == "real-model" and (
            not isinstance(result.get("answer"), str)
            or not result["answer"]
            or not isinstance(result.get("task_result"), dict)
        ):
            raise ValueError(
                "Real-model adapters must retain the answer and structured task result for independent review"
            )
        checks = result["checks"]
        records.append(
            {
                "case_id": job["case"]["id"],
                "category": job["case"]["category"],
                "repeat": job["repeat"],
                "strategy": job["strategy"]["id"],
                "tier": args.tier,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
                "control_passed": all(value is True for value in checks.values()),
                "critical_violations": sum(
                    value is not True
                    for name, value in checks.items()
                    if name.startswith("critical_")
                ),
                "checks": checks,
                "observations": result.get("observations", []),
                "answer": result.get("answer"),
                "task_result": result.get("task_result"),
                "usage": result.get("usage", {}),
                "model_id": result.get("model_id", "scripted-control"),
                "metadata": meta,
            }
        )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "runs.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
        newline="\n",
    )
    report = summarize(records, freeze)
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "runs": len(records),
                "control_passed": sum(r["control_passed"] for r in records),
                "model_quality": "unverified",
                "report": str(args.output / "report.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if all(r["control_passed"] for r in records) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-jobs", type=Path)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/agent-deepening/evaluation"
    )
    parser.add_argument(
        "--tier", choices=("scripted", "real-services", "real-model"), default="scripted"
    )
    parser.add_argument(
        "--adapter",
        help="Trusted provisioned adapter module:function(job); no implicit model setup",
    )
    args = parser.parse_args()
    freeze, cases = load()
    meta = metadata(freeze)
    if args.export_jobs:
        args.export_jobs.parent.mkdir(parents=True, exist_ok=True)
        args.export_jobs.write_text(
            "".join(
                json.dumps(job, ensure_ascii=False) + "\n" for job in jobs(freeze, cases, meta)
            ),
            encoding="utf-8",
            newline="\n",
        )
        print(
            json.dumps({"tasks": len(cases), "repeats": 3, "strategies": len(freeze["strategies"])})
        )
        return 0
    return asyncio.run(run(args, freeze, cases, meta))


if __name__ == "__main__":
    raise SystemExit(main())
