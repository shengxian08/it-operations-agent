"""Score frozen real-model runs using separately supplied independent reviews.

This does not invoke a model, invent labels, or change the application's defaults.
"""

import argparse
import json
import math
from pathlib import Path
from statistics import mean


def index(values):
    out = {}
    for value in values:
        key = (value["strategy"], value["case_id"], value["repeat"])
        if key in out:
            raise ValueError("duplicate run/review identity")
        out[key] = value
    return out


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Review counters must be nonnegative integers")
    return value


def p95(values):
    return sorted(values)[math.ceil(len(values) * 0.95) - 1] if values else None


def ratio(candidate, baseline):
    if candidate is None or baseline is None or baseline <= 0:
        return None
    return candidate / baseline


def score(records, reviews, freeze, *, case_ids=None):
    unverified = {
        "schema_version": 1,
        "real_model_quality": "unverified",
        "comparisons": [],
        "default_changed": False,
        "reason": "Complete frozen real-model runs and independent human reviews are required",
    }
    if not records or {r["tier"] for r in records} != {"real-model"} or not reviews:
        return unverified
    runs, reviewed = index(records), index(reviews)
    strategies = {s["id"] for s in freeze["strategies"]}
    cases = {r["case_id"] for r in records}
    expected = {
        (s, c, n) for s in strategies for c in cases for n in range(1, freeze["repeats"] + 1)
    }
    if (
        len(cases) != freeze["acceptance_count"]
        or set(runs) != expected
        or set(reviewed) != expected
        or (case_ids is not None and cases != set(case_ids))
    ):
        raise ValueError("A complete frozen task/strategy/three-repeat matrix is required")
    if (
        len({r["model_id"] for r in records}) != 1
        or len({r["metadata"]["code_sha256"] for r in records}) != 1
    ):
        raise ValueError("Strategies must share model and code provenance")
    units = set()
    for key, run in runs.items():
        review = reviewed[key]
        if (
            review.get("review_kind") != "independent-human"
            or not review.get("reviewer")
            or str(review["reviewer"]).lower().startswith(("model", "agent", "self"))
        ):
            raise ValueError(
                "An independent human reviewer is required for semantic task judgements"
            )
        if (
            review.get("dataset_sha256") != freeze["dataset_sha256"]
            or run["metadata"]["dataset_sha256"] != freeze["dataset_sha256"]
            or review.get("code_sha256") != run["metadata"]["code_sha256"]
            or review.get("model_id") != run["model_id"]
        ):
            raise ValueError("Review provenance does not match frozen data, code, or model")
        if type(review.get("task_success")) is not bool or type(run["control_passed"]) is not bool:
            raise ValueError("Task and control outcomes must be booleans")
        supported = integer(review["supported_citations"])
        total = integer(review["total_citations"])
        if supported > total:
            raise ValueError("Supported citations cannot exceed reviewed citations")
        integer(review["critical_violations"])
        integer(run["critical_violations"])
        cost = run["usage"].get("cost")
        elapsed = run.get("elapsed_ms")
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in (cost, elapsed)
        ):
            raise ValueError("Measured cost and elapsed time are required for every real-model run")
        unit = run["usage"].get("cost_unit")
        if not isinstance(unit, str) or not unit:
            raise ValueError("Measured costs need a common explicit unit")
        units.add(unit)
    if len(units) != 1:
        raise ValueError("Costs with different units cannot be compared")

    # The subject being measured is a task that succeeds in all three attempts.
    def metrics(strategy, complex_only=False):
        groups = {}
        for key, run in runs.items():
            if key[0] != strategy:
                continue
            if complex_only and run.get("category") not in {
                "relations",
                "investigation",
                "merchant_ops",
            }:
                continue
            groups.setdefault(key[1], []).append((run, reviewed[key]))
        successful = [
            group
            for group in groups.values()
            if all(
                r["control_passed"]
                and review["task_success"]
                and not r["critical_violations"]
                and not review["critical_violations"]
                for r, review in group
            )
        ]
        successes = [(r, review) for group in successful for r, review in group]
        citations = [(r, review) for group in groups.values() for r, review in group]
        total = sum(review["total_citations"] for _, review in citations)
        return {
            "tasks": len(groups),
            "stable_success_tasks": len(successful),
            "success_rate": len(successful) / len(groups) if groups else None,
            "successful_task_cost": mean(r["usage"]["cost"] for r, _ in successes)
            if successes
            else None,
            "p95_success_elapsed_ms": p95([r["elapsed_ms"] for r, _ in successes]),
            "citation_support_rate": sum(review["supported_citations"] for _, review in citations)
            / total
            if total
            else None,
        }

    total_violations = sum(
        r["critical_violations"] + reviewed[k]["critical_violations"] for k, r in runs.items()
    )
    limits = freeze["adoption"]
    summaries = {s: {"all": metrics(s), "complex": metrics(s, True)} for s in sorted(strategies)}
    pairs = (
        [("B", "A"), ("C", "A"), ("D", "B"), ("D", "C")]
        if freeze["project"] == "it"
        else [("B", "A"), ("C", "B"), ("D", "C")]
    )
    comparisons = []
    for candidate, baseline in pairs:
        if candidate not in summaries or baseline not in summaries:
            continue
        c, b = summaries[candidate]["complex"], summaries[baseline]["complex"]
        improvement = (
            (c["success_rate"] - b["success_rate"]) * 100
            if c["success_rate"] is not None and b["success_rate"] is not None
            else None
        )
        cost_ratio = ratio(c["successful_task_cost"], b["successful_task_cost"])
        latency_ratio = ratio(c["p95_success_elapsed_ms"], b["p95_success_elapsed_ms"])
        support = summaries[candidate]["all"]["citation_support_rate"]
        citation_gate = limits.get("citation_support_rate")
        gates = {
            "zero_critical_violations": total_violations == 0,
            "success_improved": improvement is not None
            and improvement >= limits["min_success_improvement_percentage_points"],
            "cost": cost_ratio is not None and cost_ratio <= limits["max_cost_ratio"],
            "latency": latency_ratio is not None and latency_ratio <= limits["max_p95_ratio"],
            "citations": citation_gate is None
            or (support is not None and support >= citation_gate),
        }
        comparisons.append(
            {
                "candidate": candidate,
                "baseline": baseline,
                "complex_tasks": c["tasks"],
                "improvement_percentage_points": improvement,
                "cost_ratio": cost_ratio,
                "p95_ratio": latency_ratio,
                "gates": gates,
                "eligible": all(gates.values()),
            }
        )
    return {
        "schema_version": 1,
        "real_model_quality": "reviewed",
        "critical_violations": total_violations,
        "cost_unit": next(iter(units)),
        "strategy_summaries": summaries,
        "comparisons": comparisons,
        "default_changed": False,
        "reason": "A report records eligibility; routing changes require a separately reviewed configuration",
    }


def main():
    from agent_evaluation import load

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze, cases = load()

    def read(path):
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    report = score(
        read(args.records), read(args.reviews), freeze, case_ids=[c["id"] for c in cases]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"quality": report["real_model_quality"], "report": str(args.output)}))


if __name__ == "__main__":
    main()
