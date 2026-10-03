"""Compare actual local parser outputs; no fixture-name rejection is a model gate."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


def compare(baseline_path, candidate_directory, output):
    baseline_bytes = baseline_path.read_bytes()
    candidate_path = candidate_directory / "results.json"
    candidate_bytes = candidate_path.read_bytes()
    baseline, candidate = json.loads(baseline_bytes), json.loads(candidate_bytes)
    by_name = {row["id"]: row for row in baseline["pdf_cases"]}
    cases = []
    for sample in candidate["samples"]:
        old = by_name[sample["name"]]
        if sample["input_sha256"] != old["source_sha256"]:
            raise ValueError(f"not the same PDF bytes: {sample['name']}")
        raw_evidence = json.loads((candidate_directory / sample["name"] / "evidence-1.json").read_text(encoding="utf-8"))
        warm = [row["seconds"] for row in sample["runs"] if row["phase"] == "warm"]
        cases.append({"name": sample["name"], "input_sha256": sample["input_sha256"],
                      "fixture_contract_expected": sample["baseline_contract_expected"],
                      "baseline_status": old["actual"], "baseline_api_seconds": old["seconds"],
                      "baseline_api_median_seconds": statistics.median(old["seconds"]),
                      "candidate_statuses": [row["status"] for row in sample["runs"]],
                      "candidate_setup_seconds": sample["runs"][0]["seconds"],
                      "candidate_warm_seconds": warm, "candidate_warm_median_seconds": statistics.median(warm),
                      "candidate_fixture_assessment": raw_evidence,
                      "assessment_is_not_automatic_production_rejection": True})
    result = {"schema": 1, "synthetic": True, "real_customer_pdf": False,
              "same_input_hashes": True, "same_cpu_memory_limit": "2 CPU / 4 GiB, local Linux containers",
              "baseline_result_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
              "candidate_result_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
              "baseline_parser": baseline["parser"], "baseline_environment": baseline["environment"],
              "candidate_versions": candidate["versions"], "candidate_pipeline_class": candidate["pipeline_class"],
              "candidate_pipeline_options": candidate["pipeline_options"],
              "candidate_final_rss": candidate["final_rss"], "candidate_final_cgroup": candidate["final_cgroup"],
              "candidate_converter_automatic_rejections": sum(sample["runs"][0]["status"] != "success" for sample in candidate["samples"]),
              "manual_compatibility_assessment_not_runtime_gate": True, "cases": cases}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"same_input_hashes": True, "cases": [{"name": row["name"], "baseline": row["baseline_status"],
                      "docling": row["candidate_statuses"][0], "warm_median_seconds": row["candidate_warm_median_seconds"]} for row in cases]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    compare(args.baseline, args.candidate_directory, args.output)
