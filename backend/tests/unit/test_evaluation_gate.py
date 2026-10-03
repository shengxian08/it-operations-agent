import json
from pathlib import Path

import pytest

from app.evaluation.runner import EvaluationCase, evaluate_case, summarize_results


class Runtime:
    created_ticket_count = 0
    def __init__(self, citations, final_state="answered", tools=("retrieve_evidence",)):
        self.citations, self.final_state, self.tools = citations, final_state, tools
    async def ainvoke(self, state):
        return {"retrieval_hits": [{"citation": {"source_path": "vpn.md"}}],
                "citations": [{"source_path": path} for path in self.citations],
                "final_state": self.final_state, "tool_history": [{"tool": tool} for tool in self.tools]}


def case(**overrides):
    value = {"id": "gate-case", "query": "VPN失败", "expected_final_state": "answered",
             "expected_citations": ["vpn.md"], "expected_tools": ["retrieve_evidence"], "expected_handoff_reason": None}
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_required_source_coverage_is_not_precision_without_relevance_labels():
    result = await evaluate_case(Runtime(["vpn.md", "unrelated.md"]), case())
    assert result.required_source_coverage == 1.0
    assert result.citation_precision is None
    summary = summarize_results([result])
    assert summary.required_source_coverage == 1.0 and summary.citation_precision is None


@pytest.mark.asyncio
async def test_precision_counts_irrelevant_and_additional_relevant_sources():
    labels = ["vpn.md", "certificate.md"]
    inaccurate = await evaluate_case(Runtime(["vpn.md", "unrelated.md"]), case(relevant_sources=labels))
    accurate = await evaluate_case(Runtime(["vpn.md", "certificate.md"]), case(relevant_sources=labels))
    assert inaccurate.citation_precision == 0.5
    assert accurate.citation_precision == 1.0
    assert inaccurate.required_source_coverage == accurate.required_source_coverage == 1.0


@pytest.mark.asyncio
async def test_release_gate_requires_measured_labels_and_all_critical_behaviors():
    from app.evaluation.runner import release_gate
    unlabelled = await evaluate_case(Runtime(["vpn.md"]), case())
    assert not release_gate(summarize_results([unlabelled])).passed
    wrong_state = await evaluate_case(Runtime(["vpn.md"], final_state="handoff"), case(relevant_sources=["vpn.md"]))
    gate = release_gate(summarize_results([wrong_state]))
    assert not gate.passed and "final_state_failure" in gate.reasons
    wrong_tool = await evaluate_case(Runtime(["vpn.md"], tools=("create_ticket",)), case(relevant_sources=["vpn.md"]))
    assert not release_gate(summarize_results([wrong_tool])).passed
    valid = await evaluate_case(Runtime(["vpn.md"]), case(relevant_sources=["vpn.md"]))
    assert release_gate(summarize_results([valid])).passed


@pytest.mark.asyncio
async def test_partial_relevance_labelling_cannot_hide_unlabelled_bad_citations():
    from app.evaluation.runner import release_gate
    labelled = await evaluate_case(Runtime(["vpn.md"]), case(id="labelled", relevant_sources=["vpn.md"]))
    unknown = await evaluate_case(Runtime(["vpn.md", "bad.md"]), case(id="unknown"))
    summary = summarize_results([labelled, unknown])
    assert summary.citation_precision == 1.0
    assert not release_gate(summary).passed
    assert "incomplete_relevance_labels" in release_gate(summary).reasons


@pytest.mark.asyncio
async def test_json_report_preserves_unmeasured_metrics_and_fails_gate(tmp_path):
    from app.evaluation.runner import render_json_report
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(json.dumps(case()) + "\n", encoding="utf-8")
    result = await evaluate_case(Runtime(["vpn.md"]), case())
    report = render_json_report(summarize_results([result]), [result], dataset_path=dataset, command="test")
    assert report["summary"]["citation_precision"] is None
    assert report["release_gate"]["passed"] is False
    assert len(report["dataset"]["sha256"]) == 64
    json.dumps(report, allow_nan=False)


def test_relevance_labels_must_include_required_sources():
    with pytest.raises(ValueError, match="required"):
        EvaluationCase.from_mapping(case(relevant_sources=["unrelated.md"]))
