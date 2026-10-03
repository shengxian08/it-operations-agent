import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def script(name):
    path = Path(__file__).resolve().parents[3] / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def dataset(path, *, labels=True):
    cases = []
    for index in range(84):
        state = "handoff" if index < 15 else "ticket_status" if index < 30 else "answered"
        case = {"id": f"case-{index}", "query": f"case-{index}", "expected_final_state": state,
                "expected_citations": ["vpn.md"] if state == "answered" else [],
                "expected_tools": ["retrieve_evidence"] if state == "answered" else ["get_ticket_status"] if state == "ticket_status" else [],
                "expected_handoff_reason": "needs_support" if state == "handoff" else None}
        if labels and state == "answered":
            case["relevant_sources"] = ["vpn.md"]
        cases.append(case)
    path.write_text("\n".join(json.dumps(case) for case in cases) + "\n", encoding="utf-8")
    return cases


class Runtime:
    created_ticket_count = 0
    metadata = {"index_revision": "revision-pinned"}
    def __init__(self, cases, wrong_tool=False):
        self.cases = {case["query"]: case for case in cases}
        self.closed = False
        self.wrong_tool = wrong_tool
    async def ainvoke(self, state):
        case = self.cases[state["message"]]
        sources = [{"source_path": source} for source in case["expected_citations"]]
        tools = case["expected_tools"]
        if self.wrong_tool and case["id"] == "case-15":
            tools = []
        return {"final_state": case["expected_final_state"], "handoff_reason": case["expected_handoff_reason"],
                "citations": sources, "retrieval_hits": [{"citation": source} for source in sources],
                "tool_history": [{"tool": name} for name in tools]}
    async def close(self):
        self.closed = True


@pytest.mark.parametrize("labels,wrong_tool,expected", [(True, False, 0), (False, False, 1), (True, True, 1)])
def test_evaluation_cli_outputs_json_and_enforces_release(tmp_path, labels, wrong_tool, expected):
    cli = script("run_evaluation")
    source = tmp_path / "cases.jsonl"
    runtime = Runtime(dataset(source, labels=labels), wrong_tool)
    report = tmp_path / "report.md"
    assert cli.main(["--input", str(source), "--report", str(report)], runtime_factory=lambda: runtime) == expected
    value = json.loads(report.with_suffix(".json").read_text())
    assert value["runtime"]["index_revision"] == "revision-pinned"
    assert value["release_gate"]["passed"] is (expected == 0)
    assert runtime.closed
    if not labels:
        assert value["summary"]["citation_precision"] is None
        assert "unmeasured_citation_precision" in value["release_gate"]["reasons"]


def test_explicit_demo_regression_does_not_claim_release_quality(tmp_path):
    cli = script("run_evaluation")
    source = tmp_path / "cases.jsonl"
    runtime = Runtime(dataset(source, labels=False))
    runtime.metadata = {"evaluation_kind": "demo_regression", "model_mode": "mock"}
    report = tmp_path / "demo.md"
    assert cli.main(["--demo", "--input", str(source), "--report", str(report)], runtime_factory=lambda: runtime) == 0
    value = json.loads(report.with_suffix(".json").read_text())
    assert value["release_gate"]["passed"] is False
    assert value["runtime"]["evaluation_kind"] == "demo_regression"


@pytest.mark.asyncio
async def test_demo_factory_wires_shared_embedder_without_loading_infrastructure():
    cli = script("run_evaluation")
    settings = SimpleNamespace(environment="test", demo_enabled=True, database_url="postgresql+asyncpg://test:test@127.0.0.1:1/test",
        qdrant_url="http://127.0.0.1:1", qdrant_collection="unit-demo", embedding_mode="deterministic",
        embedding_model="hash", embedding_revision="", embedding_hash_dim=32)
    runtime = await cli.build_demo_runtime(settings)
    assert runtime.metadata["evaluation_kind"] == "demo_regression"
    assert runtime.metadata["dimensions"] == 32
    await runtime.close()
    settings.environment = "production"
    with pytest.raises(ValueError, match="forbidden"):
        await cli.build_demo_runtime(settings)


@pytest.mark.asyncio
async def test_release_verifier_recomputes_instead_of_trusting_summary(tmp_path):
    from app.evaluation.release import verify_json_report
    from app.evaluation.runner import evaluate_cases, load_cases, write_json_report
    source = tmp_path / "cases.jsonl"
    cases = dataset(source)
    runtime = Runtime(cases, wrong_tool=True)
    results, summary = await evaluate_cases(runtime, load_cases(source))
    output = tmp_path / "report.json"
    write_json_report(output, summary, results, dataset_path=source, command="test", metadata=runtime.metadata)
    value = json.loads(output.read_text())
    value["release_gate"]["passed"] = True
    value["summary"]["tool_behavior_pass_rate"] = 1.0
    value["results"][15]["tools_passed"] = True
    output.write_text(json.dumps(value))
    gate, checked = await verify_json_report(output, source, index_revision="revision-pinned")
    assert not gate.passed and "tool_behavior_failure" in gate.reasons
    assert checked.tool_behavior_pass_rate < 1
    with pytest.raises(ValueError, match="production model"):
        await verify_json_report(output, source, require_production_model=True)
    with pytest.raises(ValueError, match="revision"):
        await verify_json_report(output, source, index_revision="other")
    value["results"][0]["latency_ms"] = float("nan")
    output.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="finite|JSON"):
        await verify_json_report(output, source)
