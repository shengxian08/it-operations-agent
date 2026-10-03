"""Recompute a release decision from saved outputs and the original labelled cases."""
import hashlib
import json
import math
import re
from dataclasses import replace
from pathlib import Path

from app.evaluation.runner import evaluate_case, load_cases, release_gate, summarize_results


def _strings(value, name):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{name} must be an array of strings")
    return value


class SavedOutput:
    created_ticket_count = 0

    def __init__(self, output):
        self.output = output

    async def ainvoke(self, state):
        output = self.output
        writes = output.get("unconfirmed_ticket_writes")
        if type(writes) is not int or writes < 0:
            raise ValueError("unconfirmed_ticket_writes must be a nonnegative integer")
        self.created_ticket_count += writes
        citations = _strings(output.get("actual_citations"), "actual_citations")
        retrieved = _strings(output.get("retrieved_citations"), "retrieved_citations")
        tools = _strings(output.get("actual_tools"), "actual_tools")
        for key in ("actual_final_state", "actual_handoff_reason"):
            if output.get(key) is not None and not isinstance(output[key], str):
                raise ValueError(f"{key} must be a string or null")
        return {"final_state": output.get("actual_final_state"), "handoff_reason": output.get("actual_handoff_reason"),
                "citations": [{"source_path": source} for source in citations],
                "retrieval_hits": [{"citation": {"source_path": source}} for source in retrieved],
                "tool_history": [{"tool": tool} for tool in tools]}


async def verify_json_report(report_path, dataset_path, *, index_revision=None, minimum_recall=.8, minimum_citation_precision=.85, require_production_model=False):
    def reject_constant(value):
        raise ValueError("JSON numbers must be finite")
    report = json.loads(Path(report_path).read_text(encoding="utf-8"), parse_constant=reject_constant)
    if not isinstance(report, dict) or report.get("schema_version") != 2:
        raise ValueError("unsupported evaluation report schema")
    digest = hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest()
    if report.get("dataset", {}).get("sha256") != digest:
        raise ValueError("dataset hash does not match evaluation report")
    if index_revision is not None and report.get("runtime", {}).get("index_revision") != index_revision:
        raise ValueError("index revision does not match evaluation report")
    runtime = report.get("runtime", {})
    if require_production_model and (runtime.get("evaluation_kind") != "production_model_quality"
        or runtime.get("model_mode") != "openai" or not runtime.get("model")
        or runtime.get("embedding_model") != "BAAI/bge-small-zh-v1.5"
        or not re.fullmatch(r"[0-9a-f]{40}", str(runtime.get("embedding_revision", "")))
        or not runtime.get("index_revision") or runtime.get("dimensions") != 512):
        raise ValueError("report does not identify a pinned production model evaluation")
    cases = load_cases(dataset_path)
    outputs = report.get("results")
    if not isinstance(outputs, list) or len(outputs) != len(cases):
        raise ValueError("report must contain exactly one output for every dataset case")
    results = []
    for case, output in zip(cases, outputs, strict=True):
        if not isinstance(output, dict) or output.get("case_id") != case.id:
            raise ValueError("report case IDs or order do not match dataset")
        latency = output.get("latency_ms")
        if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0:
            raise ValueError("latency must be a finite nonnegative number")
        result = await evaluate_case(SavedOutput(output), case)
        results.append(replace(result, latency_ms=latency))
    summary = summarize_results(results)
    return release_gate(summary, minimum_recall=minimum_recall, minimum_citation_precision=minimum_citation_precision), summary
