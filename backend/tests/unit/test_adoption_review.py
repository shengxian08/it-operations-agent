import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def module() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts/agent_evaluation_review.py"
    spec = importlib.util.spec_from_file_location("adoption_review", path)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def evidence() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    freeze = {
        "project": "it",
        "acceptance_count": 120,
        "repeats": 3,
        "dataset_sha256": "frozen",
        "strategies": [{"id": s} for s in "ABCD"],
        "adoption": {
            "citation_support_rate": 0.95,
            "min_success_improvement_percentage_points": 5,
            "max_cost_ratio": 1.5,
            "max_p95_ratio": 1.5,
        },
    }
    records, reviews = [], []
    for strategy in "ABCD":
        for case in range(120):
            for repeat in range(1, 4):
                key = {"strategy": strategy, "case_id": str(case), "repeat": repeat}
                records.append(
                    {
                        **key,
                        "tier": "real-model",
                        "category": "investigation",
                        "metadata": {"dataset_sha256": "frozen", "code_sha256": "code"},
                        "model_id": "TEST_FIXTURE_ONLY",
                        "elapsed_ms": 100,
                        "critical_violations": 0,
                        "control_passed": True,
                        "usage": {"cost": 1, "cost_unit": "test-unit"},
                    }
                )
                reviews.append(
                    {
                        **key,
                        "dataset_sha256": "frozen",
                        "code_sha256": "code",
                        "model_id": "TEST_FIXTURE_ONLY",
                        "reviewer": "test-human-fixture",
                        "review_kind": "independent-human",
                        "task_success": case < (80 if strategy == "A" else 100),
                        "critical_violations": 0,
                        "supported_citations": 10,
                        "total_citations": 10,
                    }
                )
    return freeze, records, reviews


def test_adoption_requires_success_gain_and_same_cost_latency_limits() -> None:
    freeze, records, reviews = evidence()
    result = module().score(records, reviews, freeze)
    assert result["real_model_quality"] == "reviewed"
    comparison = next(
        c for c in result["comparisons"] if c["candidate"] == "C" and c["baseline"] == "A"
    )
    assert comparison["eligible"] and comparison["improvement_percentage_points"] > 5
    for record in records:
        if record["strategy"] == "C":
            record["usage"]["cost"] = 2
    comparison = next(
        c
        for c in module().score(records, reviews, freeze)["comparisons"]
        if c["candidate"] == "C" and c["baseline"] == "A"
    )
    assert not comparison["eligible"] and comparison["cost_ratio"] == 2


def test_any_critical_violation_blocks_adoption() -> None:
    freeze, records, reviews = evidence()
    reviews[0]["critical_violations"] = 1
    result = module().score(records, reviews, freeze)
    assert not any(c["eligible"] for c in result["comparisons"])


def test_missing_repeat_and_duplicate_repeat_cannot_inflate_stability() -> None:
    freeze, records, reviews = evidence()
    with pytest.raises(ValueError, match="complete|duplicate"):
        module().score(records[:-1], reviews[:-1], freeze)
    with pytest.raises(ValueError, match="duplicate"):
        module().score(records + [records[-1]], reviews, freeze)


def test_reviews_are_bound_to_model_code_and_frozen_data() -> None:
    freeze, records, reviews = evidence()
    reviews[0]["code_sha256"] = "different"
    with pytest.raises(ValueError, match="provenance"):
        module().score(records, reviews, freeze)


def test_scripted_runs_never_become_real_model_quality() -> None:
    freeze, records, reviews = evidence()
    for record in records:
        record["tier"] = "scripted"
    result = module().score(records, reviews, freeze)
    assert result["real_model_quality"] == "unverified" and result["comparisons"] == []
