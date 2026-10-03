import hashlib
import inspect
import json
import math
import re
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, Protocol, cast


FinalState = Literal[
    "answered",
    "ticket_status",
    "awaiting_confirmation",
    "handoff",
]
FINAL_STATES = {"answered", "ticket_status", "awaiting_confirmation", "handoff"}
WRITE_TOOLS = {"create_ticket", "create_confirmed", "ticket_create"}


class EvaluationRuntime(Protocol):
    async def ainvoke(self, state: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    id: str
    query: str
    expected_final_state: FinalState
    expected_citations: tuple[str, ...]
    expected_tools: tuple[str, ...]
    expected_handoff_reason: str | None
    relevant_sources: tuple[str, ...] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvaluationCase":
        required = {
            "id",
            "query",
            "expected_final_state",
            "expected_citations",
            "expected_tools",
        }
        allowed = required | {"expected_handoff_reason", "relevant_sources"}
        missing = required - value.keys()
        extra = value.keys() - allowed
        if missing:
            raise ValueError(f"missing case fields: {', '.join(sorted(missing))}")
        if extra:
            raise ValueError(f"unknown case fields: {', '.join(sorted(extra))}")

        case_id = _nonempty_string(value["id"], "id")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", case_id):
            raise ValueError("id must be a safe identifier of at most 100 characters")
        query = _nonempty_string(value["query"], "query")
        if len(query) > 10_000:
            raise ValueError("query must not exceed 10000 characters")
        final_state = _nonempty_string(
            value["expected_final_state"], "expected_final_state"
        )
        if final_state not in FINAL_STATES:
            raise ValueError(f"invalid expected_final_state: {final_state}")

        citations = _string_tuple(value["expected_citations"], "expected_citations")
        for citation in citations:
            if Path(citation).name != citation or Path(citation).suffix.lower() not in {
                ".md",
                ".pdf",
            }:
                raise ValueError(
                    "expected_citations must contain knowledge file names only"
                )
        tools = _string_tuple(value["expected_tools"], "expected_tools")
        relevant_sources = None
        if value.get("relevant_sources") is not None:
            relevant_sources = _string_tuple(value["relevant_sources"], "relevant_sources")
            for source in relevant_sources:
                if "/" in source or "\\" in source or Path(source).suffix.lower() not in {".md", ".pdf"}:
                    raise ValueError("relevant_sources must contain knowledge file names only")
            if not set(citations).issubset(relevant_sources):
                raise ValueError("relevance labels must include every required source")
        reason_value = value.get("expected_handoff_reason")
        reason = (
            None
            if reason_value is None
            else _nonempty_string(reason_value, "expected_handoff_reason")
        )
        if final_state == "handoff" and reason is None:
            raise ValueError("handoff cases require expected_handoff_reason")
        if final_state != "handoff" and reason is not None:
            raise ValueError("non-handoff cases cannot expect a handoff reason")

        return cls(
            id=case_id,
            query=query,
            expected_final_state=cast(FinalState, final_state),
            expected_citations=citations,
            expected_tools=tools,
            expected_handoff_reason=reason,
            relevant_sources=relevant_sources,
        )


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    case_id: str
    passed: bool
    latency_ms: float
    recall_at_5: float | None
    citation_precision: float | None
    required_source_coverage: float | None
    has_relevance_labels: bool
    relevant_citation_count: int
    expected_citation_count: int
    retrieved_match_count: int
    actual_citation_count: int
    citation_match_count: int
    final_state_passed: bool
    citations_passed: bool
    tools_passed: bool
    handoff_reason_passed: bool
    safety_passed: bool
    unconfirmed_ticket_writes: int
    expected_final_state: str
    actual_final_state: str | None
    expected_citations: tuple[str, ...]
    actual_citations: tuple[str, ...]
    retrieved_citations: tuple[str, ...]
    expected_tools: tuple[str, ...]
    actual_tools: tuple[str, ...]
    expected_handoff_reason: str | None
    actual_handoff_reason: str | None

    @property
    def created_ticket_count(self) -> int:
        """Compatibility name used by the original Task 10 acceptance example."""
        return self.unconfirmed_ticket_writes


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    total_cases: int
    passed_cases: int
    failed_cases: int
    recall_at_5: float
    citation_precision: float | None
    required_source_coverage: float
    precision_labeled_citation_count: int
    relevant_citation_count: int
    knowledge_case_count: int
    relevance_labeled_case_count: int
    critical_behavior_pass_rate: float
    final_state_pass_rate: float
    tool_behavior_pass_rate: float
    handoff_reason_pass_rate: float
    unconfirmed_ticket_writes: int
    p50_latency_ms: float
    p95_latency_ms: float
    failed_case_ids: tuple[str, ...]

    @property
    def pass_rate(self) -> float:
        return self.passed_cases / self.total_cases if self.total_cases else 0.0

    @property
    def relevance_label_coverage(self) -> float:
        return self.relevance_labeled_case_count / self.knowledge_case_count if self.knowledge_case_count else 0.0


@dataclass(frozen=True, slots=True)
class ReleaseGate:
    passed: bool
    reasons: tuple[str, ...]
    minimum_recall: float = 0.8
    minimum_citation_precision: float = 0.85


def release_gate(summary: EvaluationSummary, *, minimum_recall=0.8, minimum_citation_precision=0.85) -> ReleaseGate:
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in (minimum_recall, minimum_citation_precision)):
        raise ValueError("release thresholds must be finite fractions between zero and one")
    minimum_recall = max(0.8, minimum_recall)
    minimum_citation_precision = max(0.85, minimum_citation_precision)
    failures = []
    if summary.recall_at_5 < minimum_recall:
        failures.append("recall_below_threshold")
    if summary.citation_precision is None:
        failures.append("unmeasured_citation_precision")
    elif summary.citation_precision < minimum_citation_precision:
        failures.append("citation_precision_below_threshold")
    if summary.relevance_label_coverage < 1.0:
        failures.append("incomplete_relevance_labels")
    for rate, code in ((summary.final_state_pass_rate, "final_state_failure"),
                       (summary.tool_behavior_pass_rate, "tool_behavior_failure"),
                       (summary.handoff_reason_pass_rate, "handoff_reason_failure"),
                       (summary.critical_behavior_pass_rate, "critical_behavior_failure")):
        if rate != 1.0:
            failures.append(code)
    if summary.unconfirmed_ticket_writes:
        failures.append("unconfirmed_ticket_write")
    return ReleaseGate(not failures, tuple(failures), minimum_recall, minimum_citation_precision)


Case = EvaluationCase
Result = EvaluationResult
Summary = EvaluationSummary


def load_cases(
    path: str | Path,
    *,
    knowledge_dir: str | Path | None = None,
    minimum_cases: int = 80,
    maximum_cases: int = 120,
    minimum_handoff_cases: int = 15,
    minimum_ticket_cases: int = 15,
) -> list[EvaluationCase]:
    source = Path(path)
    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"unable to read evaluation cases: {source}") from error

    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            raise ValueError(f"blank JSONL record at line {line_number}")
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON at line {line_number}: {error.msg}") from error
        if not isinstance(value, dict):
            raise ValueError(f"evaluation case at line {line_number} must be an object")
        if "expected_handoff_reason" not in value:
            raise ValueError(
                f"invalid case at line {line_number}: missing case fields: "
                "expected_handoff_reason"
            )
        try:
            case = EvaluationCase.from_mapping(value)
        except ValueError as error:
            raise ValueError(f"invalid case at line {line_number}: {error}") from error
        if case.id in seen_ids:
            raise ValueError(f"duplicate evaluation case id: {case.id}")
        seen_ids.add(case.id)
        cases.append(case)

    if not minimum_cases <= len(cases) <= maximum_cases:
        raise ValueError(
            f"evaluation dataset must contain {minimum_cases}-{maximum_cases} cases; "
            f"found {len(cases)}"
        )
    handoff_count = sum(case.expected_final_state == "handoff" for case in cases)
    if handoff_count < minimum_handoff_cases:
        raise ValueError(
            "evaluation dataset must contain at least "
            f"{minimum_handoff_cases} risk/no-answer cases"
        )
    ticket_tools = {"get_ticket_status", "issue_confirmation"}
    ticket_count = sum(bool(ticket_tools & set(case.expected_tools)) for case in cases)
    if ticket_count < minimum_ticket_cases:
        raise ValueError(
            "evaluation dataset must contain at least "
            f"{minimum_ticket_cases} ticket lookup/create cases"
        )

    if knowledge_dir is not None:
        available_sources = {
            item.name for item in Path(knowledge_dir).iterdir() if item.is_file()
        }
        missing_sources = sorted(
            {
                citation
                for case in cases
                for citation in (*case.expected_citations, *(case.relevant_sources or ()))
                if citation not in available_sources
            }
        )
        if missing_sources:
            raise ValueError(
                "evaluation citations do not exist in the knowledge directory: "
                + ", ".join(missing_sources)
            )
    return cases


async def evaluate_case(
    runtime: EvaluationRuntime,
    case: EvaluationCase | Mapping[str, Any],
) -> EvaluationResult:
    resolved_case = (
        case if isinstance(case, EvaluationCase) else EvaluationCase.from_mapping(case)
    )
    ticket_count_before = await _read_ticket_count(runtime)
    started_at = perf_counter()
    state = await runtime.ainvoke(
        {
            "user_id": getattr(runtime, "user_id", "u-001"),
            "conversation_id": getattr(runtime, "conversation_id", "c-001"),
            "message": resolved_case.query,
            "step_count": 0,
            "trace_id": f"evaluation-{resolved_case.id}",
        }
    )
    latency_ms = (perf_counter() - started_at) * 1000
    if not isinstance(state, Mapping):
        raise TypeError("evaluation runtime must return a mapping state")
    ticket_count_after = await _read_ticket_count(runtime)

    retrieved = _source_paths(
        state.get("retrieval_hits"),
        nested_citation=True,
        limit=5,
    )
    actual_citations = _source_paths(state.get("citations"), nested_citation=False)
    actual_tools = _tool_names(state.get("tool_history"))
    expected_sources = set(resolved_case.expected_citations)
    retrieved_matches = len(expected_sources & set(retrieved))
    citation_matches = len(expected_sources & set(actual_citations))
    expected_count = len(expected_sources)
    recall = retrieved_matches / expected_count if expected_count else None
    coverage = citation_matches / expected_count if expected_count else None
    relevance_labels = resolved_case.relevant_sources
    relevant_count = len(set(actual_citations) & set(relevance_labels or ()))
    precision = (relevant_count / len(actual_citations)
                 if relevance_labels is not None and actual_citations else None)

    actual_final_state = _optional_string(state.get("final_state"))
    actual_handoff_reason = _optional_string(state.get("handoff_reason"))
    final_state_passed = actual_final_state == resolved_case.expected_final_state
    # Empty citation expectations are neutral. Ticket and handoff paths do not retrieve,
    # so treating their intentional empty list as a retrieval failure is misleading.
    citations_passed = (not actual_citations if not expected_count and resolved_case.expected_final_state != "answered"
        else not expected_count or (recall == 1.0 and expected_sources.issubset(actual_citations)))
    if precision is not None and precision < 0.85:
        citations_passed = False
    tools_passed = actual_tools == resolved_case.expected_tools
    handoff_reason_passed = (
        actual_handoff_reason == resolved_case.expected_handoff_reason
    )

    counter_delta = max(0, ticket_count_after - ticket_count_before)
    observed_write_tools = sum(tool in WRITE_TOOLS for tool in actual_tools)
    unconfirmed_writes = max(counter_delta, observed_write_tools)
    safety_passed = unconfirmed_writes == 0
    passed = all(
        (
            final_state_passed,
            citations_passed,
            tools_passed,
            handoff_reason_passed,
            safety_passed,
        )
    )
    return EvaluationResult(
        case_id=resolved_case.id,
        passed=passed,
        latency_ms=latency_ms,
        recall_at_5=recall,
        citation_precision=precision,
        required_source_coverage=coverage,
        has_relevance_labels=relevance_labels is not None,
        relevant_citation_count=relevant_count,
        expected_citation_count=expected_count,
        retrieved_match_count=retrieved_matches,
        actual_citation_count=len(actual_citations),
        citation_match_count=citation_matches,
        final_state_passed=final_state_passed,
        citations_passed=citations_passed,
        tools_passed=tools_passed,
        handoff_reason_passed=handoff_reason_passed,
        safety_passed=safety_passed,
        unconfirmed_ticket_writes=unconfirmed_writes,
        expected_final_state=resolved_case.expected_final_state,
        actual_final_state=actual_final_state,
        expected_citations=resolved_case.expected_citations,
        actual_citations=actual_citations,
        retrieved_citations=retrieved,
        expected_tools=resolved_case.expected_tools,
        actual_tools=actual_tools,
        expected_handoff_reason=resolved_case.expected_handoff_reason,
        actual_handoff_reason=actual_handoff_reason,
    )


async def evaluate_cases(
    runtime: EvaluationRuntime,
    cases: Sequence[EvaluationCase],
) -> tuple[list[EvaluationResult], EvaluationSummary]:
    results = [await evaluate_case(runtime, case) for case in cases]
    return results, summarize_results(results)


def summarize_results(results: Sequence[EvaluationResult]) -> EvaluationSummary:
    if not results:
        raise ValueError("at least one evaluation result is required")
    total = len(results)
    expected_citations = sum(item.expected_citation_count for item in results)
    labelled = [item for item in results if item.has_relevance_labels]
    emitted_labelled_citations = sum(item.actual_citation_count for item in labelled)
    knowledge_cases = [item for item in results if item.expected_final_state == "answered"]
    critical_cases = [item for item in results if item.expected_final_state != "answered"]
    return EvaluationSummary(
        total_cases=total,
        passed_cases=sum(item.passed for item in results),
        failed_cases=sum(not item.passed for item in results),
        recall_at_5=(
            sum(item.retrieved_match_count for item in results) / expected_citations
            if expected_citations
            else 0.0
        ),
        citation_precision=(
            sum(item.relevant_citation_count for item in labelled) / emitted_labelled_citations
            if emitted_labelled_citations else None
        ),
        required_source_coverage=sum(item.citation_match_count for item in results) / expected_citations if expected_citations else 0.0,
        precision_labeled_citation_count=emitted_labelled_citations,
        relevant_citation_count=sum(item.relevant_citation_count for item in labelled),
        knowledge_case_count=len(knowledge_cases),
        relevance_labeled_case_count=sum(item.has_relevance_labels for item in knowledge_cases),
        critical_behavior_pass_rate=sum(item.passed for item in critical_cases) / len(critical_cases) if critical_cases else 1.0,
        final_state_pass_rate=sum(item.final_state_passed for item in results) / total,
        tool_behavior_pass_rate=sum(item.tools_passed for item in results) / total,
        handoff_reason_pass_rate=(
            sum(item.handoff_reason_passed for item in results) / total
        ),
        unconfirmed_ticket_writes=sum(
            item.unconfirmed_ticket_writes for item in results
        ),
        p50_latency_ms=_percentile([item.latency_ms for item in results], 0.50),
        p95_latency_ms=_percentile([item.latency_ms for item in results], 0.95),
        failed_case_ids=tuple(item.case_id for item in results if not item.passed),
    )


def render_markdown_report(
    summary: EvaluationSummary,
    results: Sequence[EvaluationResult],
    *,
    dataset_path: str | Path,
    command: str,
    generated_at: datetime | None = None,
    gate: ReleaseGate | None = None,
) -> str:
    source = Path(dataset_path)
    generated = generated_at or datetime.now(UTC)
    data_version = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    failed_ids = ", ".join(f"`{item}`" for item in summary.failed_case_ids) or "None"
    details = []
    for result in results:
        if result.passed:
            continue
        failures = []
        if not result.final_state_passed:
            failures.append("final_state")
        if not result.citations_passed:
            failures.append("citations")
        if not result.tools_passed:
            failures.append("tools")
        if not result.handoff_reason_passed:
            failures.append("handoff_reason")
        if not result.safety_passed:
            failures.append("unconfirmed_write")
        details.append(f"| `{result.case_id}` | {', '.join(failures)} |")
    detail_rows = "\n".join(details) or "| None | None |"
    precision_text = f"{summary.citation_precision:.3f}" if summary.citation_precision is not None else "Not measured (no exhaustive relevance labels)"
    gate = gate or release_gate(summary)
    return f"""# Offline Evaluation Report

Expected citations measure required-source coverage. True citation precision counts
emitted sources against explicit exhaustive `relevant_sources` labels; unlabelled
cases do not produce a precision measurement. Non-knowledge paths do not contribute
to retrieval metrics. Every critical state/tool/safety assertion must pass release.

## Baseline

- Generated at: `{generated.astimezone(UTC).isoformat()}`
- Command: `{command}`
- Dataset: `{source.as_posix()}`
- Data version (SHA-256): `{data_version}`

## Metrics

| Metric | Value |
| --- | ---: |
| Cases | {summary.total_cases} |
| Overall pass rate | {summary.pass_rate:.3f} |
| Recall@5 | {summary.recall_at_5:.3f} |
| Required source coverage | {summary.required_source_coverage:.3f} |
| Citation precision | {precision_text} |
| Relevance label coverage | {summary.relevance_label_coverage:.3f} |
| Final state pass rate | {summary.final_state_pass_rate:.3f} |
| Tool behavior pass rate | {summary.tool_behavior_pass_rate:.3f} |
| Handoff reason pass rate | {summary.handoff_reason_pass_rate:.3f} |
| Unconfirmed ticket writes | {summary.unconfirmed_ticket_writes} |
| Critical behavior pass rate | {summary.critical_behavior_pass_rate:.3f} |
| P50 latency | {summary.p50_latency_ms:.2f} ms |
| P95 latency | {summary.p95_latency_ms:.2f} ms |

## Failures

Release gate: **{'PASS' if gate.passed else 'FAIL'}**. Reasons: {', '.join(gate.reasons) or 'None'}.

Failed case IDs: {failed_ids}

| Case | Failed checks |
| --- | --- |
{detail_rows}
"""


def render_json_report(summary, results, *, dataset_path, command, generated_at=None, metadata=None, gate=None):
    source = Path(dataset_path)
    generated = generated_at or datetime.now(UTC)
    return {"schema_version": 2, "generated_at": generated.astimezone(UTC).isoformat(), "command": command,
            "dataset": {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
            "runtime": metadata or {}, "summary": {**asdict(summary), "pass_rate": summary.pass_rate,
                "relevance_label_coverage": summary.relevance_label_coverage},
            "release_gate": asdict(gate or release_gate(summary)), "results": [asdict(item) for item in results]}


def write_json_report(path, summary, results, **kwargs):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(render_json_report(summary, results, **kwargs), ensure_ascii=False,
        indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_markdown_report(
    path: str | Path,
    summary: EvaluationSummary,
    results: Sequence[EvaluationResult],
    *,
    dataset_path: str | Path,
    command: str,
    generated_at: datetime | None = None,
    gate: ReleaseGate | None = None,
) -> None:
    Path(path).write_text(
        render_markdown_report(
            summary,
            results,
            dataset_path=dataset_path,
            command=command,
            generated_at=generated_at,
            gate=gate,
        ),
        encoding="utf-8",
    )


def _nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    items = tuple(_nonempty_string(item, field_name) for item in value)
    if len(items) != len(set(items)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return items


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _source_paths(
    value: Any,
    *,
    nested_citation: bool,
    limit: int | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    paths: list[str] = []
    for index, item in enumerate(value):
        if limit is not None and index >= limit:
            break
        citation = _field(item, "citation") if nested_citation else item
        source_path = _field(citation, "source_path")
        if isinstance(source_path, str) and source_path:
            paths.append(Path(source_path).name)
    return tuple(dict.fromkeys(paths))


def _tool_names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    tools = []
    for entry in value:
        tool = _field(entry, "tool")
        if isinstance(tool, str) and tool:
            tools.append(tool)
    return tuple(tools)


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


async def _read_ticket_count(runtime: Any) -> int:
    value: Any = None
    for name in ("get_created_ticket_count", "get_ticket_count", "ticket_count"):
        candidate = getattr(runtime, name, None)
        if callable(candidate):
            value = candidate()
            break
        if candidate is not None:
            value = candidate
            break
    else:
        value = getattr(runtime, "created_ticket_count", None)
        if value is None:
            service = getattr(runtime, "ticket_service", None)
            value = getattr(service, "created_ticket_count", 0)
    if inspect.isawaitable(value):
        value = await cast(Awaitable[Any], value)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TypeError("runtime ticket count must be a non-negative integer")
    return value


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between zero and one")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight
