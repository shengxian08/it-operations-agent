"""Produce a small reviewable evidence index from actual experiment artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(root, output):
    artifacts = root / "artifacts/technology-review"
    corpus = json.loads((artifacts / "corpus.json").read_text(encoding="utf-8"))
    encoded = json.loads((artifacts / "encoded.json").read_text(encoding="utf-8"))
    retrieval = json.loads((artifacts / "retrieval.json").read_text(encoding="utf-8"))
    parsing = json.loads((artifacts / "parsing.json").read_text(encoding="utf-8"))
    token = json.loads((artifacts / "token-audit.json").read_text(encoding="utf-8"))
    if encoded["corpus_sha256"] != sha(artifacts / "corpus.json") or retrieval["encoded_sha256"] != sha(artifacts / "encoded.json"):
        raise ValueError("retrieval evidence chain hashes differ")
    if token["encoded_sha256"] != sha(artifacts / "encoded.json"):
        raise ValueError("token audit does not match exact vectors")
    source_check = json.loads((artifacts / "current-source-verification.json").read_text(encoding="utf-8"))
    current = json.loads((artifacts / "current-code-encoded.json").read_text(encoding="utf-8"))
    if not source_check["pilot_retrieval_inputs_verified_against_current_source"] or source_check["current_sha256"] != sha(artifacts / "current-code-encoded.json"):
        raise ValueError("current workspace source reproduction is unverified")
    if current["environment"]["app_source_sha256"] != sha(root / "backend/app/rag/ingest.py"):
        raise ValueError("verified current source changed since encoding")
    checks = {}
    for name in ("controls.xml", "business-and-provenance.xml"):
        suites = ET.parse(artifacts / name).getroot()
        entries = list(suites) if suites.tag == "testsuites" else [suites]
        checks[name] = {key: sum(int(suite.attrib.get(key, 0)) for suite in entries)
                        for key in ("tests", "failures", "errors", "skipped")}
        if checks[name]["failures"] or checks[name]["errors"] or checks[name]["skipped"]:
            raise ValueError(f"not all isolated checks executed/passed: {name}")
    previous = json.loads((root / "artifacts/audit-implementation-evidence.json").read_text(encoding="utf-8"))
    compared = previous["files_changed_in_this_scope"]
    mismatches = [record["path"] for record in compared
                  if not (root / record["path"]).is_file() or sha(root / record["path"]) != record["sha256"]]
    result = {
        "date": "2026-10-02", "source_conversation": "https://chatgpt.com/share/6abf55eb-56f8-83e9-ad6c-4a295bdb43ec",
        "scope": "technology selection review and isolated experiments",
        "synthetic_corpus": {"documents": len(corpus["documents"]), "chunks": len(encoded["chunks"]),
                             "queries": len(corpus["queries"]), "sha256": sha(artifacts / "corpus.json"),
                             "holdout_used_for_tuning": False},
        "real_embedding": encoded["model"], "embedding_environment": encoded["environment"],
        "embedding_performance": encoded["performance"], "retrieval_summary": retrieval["summary"],
        "current_source_reproduction": source_check,
        "current_source_embedding_performance": current["performance"],
        "permission_leaks": retrieval["permission_leaks"], "index_manifest": retrieval["index_manifest"],
        "tokens": {arm: {key: row[key] for key in ("max", "over_512_count")}
                   for arm, row in token["inputs"].items()},
        "pdf_boundaries": [{"id": row["id"], "expected": row["expected"], "actual": row["actual"],
                            "seconds": row["seconds"], "checks": row.get("checks")}
                           for row in parsing["pdf_cases"]],
        "test_checks": checks,
        "prior_scope_unchanged": {"compared_files": len(compared), "mismatches": mismatches},
        "real_customer_pdf_quality_verified": False, "real_answer_citation_or_refusal_quality_verified": False,
        "target_linux_capacity_verified": False, "production_ready": False,
        "candidate_preflight_at_baseline_time": encoded["candidate_preflight"],
        "raw_evidence": [],
    }
    qwen = artifacts / "qwen-reranker.json"
    if qwen.exists():
        data = json.loads(qwen.read_text(encoding="utf-8"))
        if data["encoded_sha256"] != sha(artifacts / "encoded.json") or data["retrieval_sha256"] != sha(artifacts / "retrieval.json"):
            raise ValueError("Qwen candidate evidence hashes differ")
        result["qwen_reranker"] = {key: data[key] for key in
                                  ("repo", "revision", "device", "dtype", "max_length", "batch_size", "summary", "performance")}
    docling = artifacts / "docling/comparison.json"
    if docling.exists():
        result["docling"] = json.loads(docling.read_text(encoding="utf-8"))
    for path in sorted(artifacts.rglob("*")):
        if path.is_file() and (path.suffix in {".json", ".xml"} or path.name.endswith("exit.txt")):
            if any(part in {"candidate-model-cache", "hf-cache", "models"} for part in path.relative_to(artifacts).parts):
                continue
            result["raw_evidence"].append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)})
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "raw_evidence_count": len(result["raw_evidence"]),
                      "test_checks": checks, "prior_scope_mismatches": mismatches}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.root.resolve(), args.output)
