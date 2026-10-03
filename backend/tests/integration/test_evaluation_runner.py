import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.runner import (
    EvaluationCase,
    evaluate_case,
    load_cases,
    render_markdown_report,
    summarize_results,
)
from app.rag.retriever import Citation, RetrievalHit


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def case(**overrides: Any) -> dict[str, Any]:
    value = {
        "id": "knowledge-001",
        "query": "VPN 无法连接",
        "expected_final_state": "answered",
        "expected_citations": ["vpn-connection.md"],
        "relevant_sources": ["vpn-connection.md"],
        "expected_tools": ["retrieve_evidence"],
        "expected_handoff_reason": None,
    }
    value.update(overrides)
    return value


def vpn_hit() -> RetrievalHit:
    return RetrievalHit(
        citation=Citation(
            document_id="doc-vpn",
            source_title="VPN 连接故障处理",
            source_path="vpn-connection.md",
            chunk_index=0,
            excerpt="关闭并重新打开 VPN 客户端。",
        ),
        score=0.9,
        vector_score=0.8,
        bm25_score=2.0,
        combined_score=0.85,
    )


@dataclass
class FakeRuntime:
    state: dict[str, Any]
    created_ticket_count: int = 0
    write_on_invoke: bool = False
    invocations: list[dict[str, Any]] = field(default_factory=list)

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        self.invocations.append(state)
        if self.write_on_invoke:
            self.created_ticket_count += 1
        return self.state


@pytest.mark.asyncio
async def test_evaluate_case_scores_retrieval_citations_state_and_tools() -> None:
    hit = vpn_hit()
    runtime = FakeRuntime(
        {
            "retrieval_hits": [hit],
            "citations": [hit.citation],
            "tool_history": [{"tool": "retrieve_evidence", "result": "found"}],
            "final_state": "answered",
        }
    )

    result = await evaluate_case(runtime, case())

    assert result.passed is True
    assert result.recall_at_5 == 1.0
    assert result.citation_precision == 1.0
    assert result.actual_tools == ("retrieve_evidence",)
    assert runtime.invocations[0]["trace_id"] == "evaluation-knowledge-001"


@pytest.mark.asyncio
async def test_additional_relevant_citations_do_not_penalize_required_source() -> None:
    hit = vpn_hit()
    additional = Citation(
        document_id="doc-vpn-certificate",
        source_title="VPN 证书处理",
        source_path="vpn-certificate.md",
        chunk_index=0,
        excerpt="检查证书有效期。",
    )
    result = await evaluate_case(
        FakeRuntime(
            {
                "retrieval_hits": [hit],
                "citations": [hit.citation, additional],
                "tool_history": [{"tool": "retrieve_evidence"}],
                "final_state": "answered",
            }
        ),
        case(relevant_sources=["vpn-connection.md", "vpn-certificate.md"]),
    )

    assert result.citation_precision == 1.0
    assert result.citations_passed is True


@pytest.mark.asyncio
async def test_ticket_create_uses_actual_tool_and_empty_citations_are_neutral() -> None:
    runtime = FakeRuntime(
        {
            "retrieval_hits": [],
            "citations": [],
            "tool_history": [{"tool": "issue_confirmation", "result": "issued"}],
            "final_state": "awaiting_confirmation",
        }
    )

    result = await evaluate_case(
        runtime,
        case(
            id="ticket-create-001",
            query="VPN 失败，请建工单",
            expected_final_state="awaiting_confirmation",
            expected_citations=[],
            expected_tools=["issue_confirmation"],
        ),
    )

    assert result.passed is True
    assert result.recall_at_5 is None
    assert result.citation_precision is None
    assert result.created_ticket_count == 0


@pytest.mark.asyncio
async def test_ticket_lookup_uses_the_ticket_status_final_state() -> None:
    runtime = FakeRuntime(
        {
            "citations": [],
            "tool_history": [{"tool": "get_ticket_status", "result": "found"}],
            "final_state": "ticket_status",
        }
    )

    result = await evaluate_case(
        runtime,
        case(
            id="ticket-lookup-001",
            query="查询工单 IT-2026-0001",
            expected_final_state="ticket_status",
            expected_citations=[],
            expected_tools=["get_ticket_status"],
        ),
    )

    assert result.passed is True


@pytest.mark.asyncio
async def test_direct_case_mapping_defaults_non_handoff_reason_to_none() -> None:
    runtime = FakeRuntime(
        {
            "citations": [],
            "tool_history": [{"tool": "issue_confirmation"}],
            "final_state": "awaiting_confirmation",
        }
    )
    direct_case = case(
        id="ticket-create-plan-example",
        expected_final_state="awaiting_confirmation",
        expected_citations=[],
        expected_tools=["issue_confirmation"],
    )
    direct_case.pop("expected_handoff_reason")

    result = await evaluate_case(runtime, direct_case)

    assert result.passed is True


@pytest.mark.asyncio
async def test_unconfirmed_ticket_write_fails_the_safety_assertion() -> None:
    runtime = FakeRuntime(
        {
            "citations": [],
            "tool_history": [{"tool": "issue_confirmation"}],
            "final_state": "awaiting_confirmation",
        },
        write_on_invoke=True,
    )

    result = await evaluate_case(
        runtime,
        case(
            id="ticket-create-unsafe",
            expected_final_state="awaiting_confirmation",
            expected_citations=[],
            expected_tools=["issue_confirmation"],
        ),
    )

    assert result.passed is False
    assert result.safety_passed is False
    assert result.unconfirmed_ticket_writes == 1


def test_fixed_dataset_is_valid_and_references_real_knowledge() -> None:
    cases = load_cases(
        PROJECT_ROOT / "data" / "eval" / "cases.jsonl",
        knowledge_dir=PROJECT_ROOT / "data" / "knowledge",
    )

    assert 80 <= len(cases) <= 120
    assert sum(item.expected_final_state == "handoff" for item in cases) >= 15
    assert (
        sum(
            bool({"get_ticket_status", "issue_confirmation"} & set(item.expected_tools))
            for item in cases
        )
        >= 15
    )
    create_cases = [item for item in cases if "issue_confirmation" in item.expected_tools]
    assert create_cases
    assert all(not item.expected_citations for item in create_cases)


def test_loader_rejects_duplicate_ids_and_bad_jsonl(tmp_path: Path) -> None:
    duplicate = json.dumps(case(), ensure_ascii=False)
    path = tmp_path / "cases.jsonl"
    path.write_text(f"{duplicate}\n{duplicate}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate evaluation case id"):
        load_cases(
            path,
            minimum_cases=1,
            minimum_handoff_cases=0,
            minimum_ticket_cases=0,
        )


@pytest.mark.asyncio
async def test_summary_has_micro_metrics_percentiles_and_report() -> None:
    hit = vpn_hit()
    passing = await evaluate_case(
        FakeRuntime(
            {
                "retrieval_hits": [hit],
                "citations": [hit.citation],
                "tool_history": [{"tool": "retrieve_evidence"}],
                "final_state": "answered",
            }
        ),
        case(id="summary-001"),
    )
    missing = await evaluate_case(
        FakeRuntime(
            {
                "retrieval_hits": [],
                "citations": [],
                "tool_history": [{"tool": "retrieve_evidence"}],
                "final_state": "handoff",
                "handoff_reason": "insufficient_evidence",
            }
        ),
        case(id="summary-002"),
    )

    summary = summarize_results([passing, missing])
    report = render_markdown_report(
        summary,
        [passing, missing],
        dataset_path=PROJECT_ROOT / "data" / "eval" / "cases.jsonl",
        command="python scripts/run_evaluation.py",
        generated_at=datetime(2026, 8, 12, tzinfo=UTC),
    )

    assert summary.recall_at_5 == 0.5
    assert summary.citation_precision == 1.0
    assert summary.required_source_coverage == 0.5
    assert summary.p50_latency_ms <= summary.p95_latency_ms
    assert summary.failed_case_ids == ("summary-002",)
    assert "Recall@5" in report
    assert "Citation precision" in report
    assert "`summary-002`" in report
