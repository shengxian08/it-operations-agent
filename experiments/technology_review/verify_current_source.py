"""Refuse to retain pilot results if current source changes experimental inputs."""
import argparse
import hashlib
import json
from pathlib import Path


def verify(pilot_path, current_path, output):
    pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
    current = json.loads(current_path.read_text(encoding="utf-8"))
    checks = {"corpus_hash": pilot["corpus_sha256"] == current["corpus_sha256"],
              "model_identity": pilot["model"] == current["model"],
              "raw_chunk_records": pilot["chunks"] == current["chunks"],
              "baseline_and_context_inputs": pilot["inputs"] == current["inputs"],
              "loaded_current_source": current["environment"]["app_source"] == "/workspace/backend/app/rag/ingest.py"}
    drifts = {}
    for arm in pilot["vectors"]:
        left, right = pilot["vectors"][arm], current["vectors"][arm]
        if len(left) != len(right) or any(len(a) != len(b) for a, b in zip(left, right, strict=True)):
            raise ValueError("current-source vector shape differs")
        drifts[arm] = max(abs(a - b) for row_a, row_b in zip(left, right, strict=True)
                         for a, b in zip(row_a, row_b, strict=True))
    checks["all_vectors_exactly_equal"] = all(value == 0 for value in drifts.values())
    result = {"pilot_sha256": hashlib.sha256(pilot_path.read_bytes()).hexdigest(),
              "current_sha256": hashlib.sha256(current_path.read_bytes()).hexdigest(),
              "current_app_source": current["environment"]["app_source"],
              "current_app_source_sha256": current["environment"]["app_source_sha256"],
              "checks": checks, "max_abs_vector_drift": drifts,
              "pilot_retrieval_inputs_verified_against_current_source": all(checks.values())}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if not all(checks.values()):
        raise ValueError("pilot is not an exact current-source reproduction; withdraw and rerun retrieval")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.pilot, args.current, args.output)
