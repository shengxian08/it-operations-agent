"""Pinned public Qwen assets, then offline reranking of the SAME candidate pool.

Download and inference are separate commands/containers. Only synthetic inputs
are supported. There is no API call, settings import or production index write.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import resource
import statistics
import time

from experiments.technology_review.controls import document_metrics

REPO = "Qwen/Qwen3-Reranker-0.6B"
REVISION = "e61197ed45024b0ed8a2d74b80b4d909f1255473"


def download(output):
    from huggingface_hub import snapshot_download
    start = time.perf_counter()
    path = Path(snapshot_download(REPO, revision=REVISION, token=False,
        allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "README.md", "LICENSE*", "*.jinja"]))
    files = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            files.append({"path": str(item.relative_to(path)), "bytes": item.stat().st_size,
                          "sha256": hashlib.sha256(item.read_bytes()).hexdigest()})
    record = {"repo": REPO, "revision": REVISION, "snapshot": str(path), "files": files,
              "download_seconds": time.perf_counter() - start, "document_uploaded": False,
              "used_hf_token": False}
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"repo": REPO, "revision": REVISION, "files": len(files),
                      "bytes": sum(item["bytes"] for item in files), "seconds": record["download_seconds"]}))


def infer(encoded_path, retrieval_path, output):
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise ValueError("candidate inference requires offline mode")
    encoded_bytes, retrieval_bytes = encoded_path.read_bytes(), retrieval_path.read_bytes()
    encoded, retrieval = json.loads(encoded_bytes), json.loads(retrieval_bytes)
    if not encoded["synthetic"] or not retrieval["synthetic"]:
        raise ValueError("only the approved synthetic comparison corpus is allowed")
    if retrieval["encoded_sha256"] != hashlib.sha256(encoded_bytes).hexdigest():
        raise ValueError("candidate pool and encoded inputs differ")
    import torch
    from sentence_transformers import CrossEncoder
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    start = time.perf_counter()
    model = CrossEncoder(REPO, revision=REVISION, local_files_only=True, device="cpu", max_length=512,
                         model_kwargs={"dtype": torch.float32})
    cold_load = time.perf_counter() - start
    by_id = {chunk["id"]: chunk for chunk in encoded["chunks"]}
    texts = dict(zip(by_id, encoded["inputs"]["baseline"], strict=True))
    rows = [row for row in retrieval["queries"] if row["arm"] == "baseline_weighted"]
    pairs, slices = [], []
    for row in rows:
        ids = sorted(row["combined_scores"])
        slices.append((len(pairs), len(pairs) + len(ids), ids))
        pairs.extend((row["text"], texts[ident]) for ident in ids)
    start = time.perf_counter()
    values = model.predict(pairs, batch_size=8, show_progress_bar=True)
    inference_seconds = time.perf_counter() - start
    scores = values.tolist()
    if len(scores) != len(pairs) or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in scores):
        raise ValueError("candidate must produce one finite scalar for each pair")
    results = []
    for row, (start, end, ids) in zip(rows, slices, strict=True):
        raw = dict(zip(ids, scores[start:end], strict=True))
        # Qwen ST returns the yes/no logit difference. Sigmoid is monotonic and
        # is recorded only; no old lexical evidence threshold is reused.
        normalized = {key: (1 / (1 + math.exp(-value)) if value >= 0 else math.exp(value) / (1 + math.exp(value))) for key, value in raw.items()}
        ranked = sorted(ids, key=lambda key: (-raw[key], -row["combined_scores"][key], key))
        selected, counts = [], {}
        for key in ranked:
            doc = by_id[key]["synthetic_document_id"]
            if counts.get(doc, 0) >= 2:
                continue
            counts[doc] = counts.get(doc, 0) + 1
            selected.append(doc)
            if len(selected) == 5:
                break
        results.append({"query_id": row["query_id"], "split": row["split"], "category": row["category"],
                        "gold": row["gold"], "candidate_ids": ids, "raw_scores": raw,
                        "sigmoid_scores": normalized, "final": selected,
                        "metrics_final": document_metrics(selected, row["gold"]),
                        "baseline_final": row["final"], "baseline_metrics": row["metrics_final"]})
    # Three repeats on one fixed 40+ candidate query measure latency/ranking
    # determinism; they do not substitute repeated full quality evaluation.
    warm_seconds, max_drift = [], 0.0
    start, end, _ = slices[8]  # first fixed holdout query, not chosen from results
    for _ in range(3):
        begin = time.perf_counter()
        repeated = model.predict(pairs[start:end], batch_size=8, show_progress_bar=False).tolist()
        warm_seconds.append(time.perf_counter() - begin)
        max_drift = max(max_drift, max(abs(a - b) for a, b in zip(scores[start:end], repeated, strict=True)))
    summary = {}
    for split in ("dev", "holdout"):
        answerable = [row for row in results if row["split"] == split and row["gold"]]
        summary[split] = {"answerable_count": len(answerable),
                          **{key: statistics.mean(row["metrics_final"][key] for row in answerable)
                             for key in ("recall", "precision", "mrr", "ndcg")}}
    result = {"schema": 1, "synthetic": True, "real_candidate_model": True,
              "repo": REPO, "revision": REVISION, "device": "cpu", "dtype": "float32",
              "max_length": 512, "batch_size": 8, "score_kind": "ST LogitScore yes-minus-no; sigmoid also recorded",
              "same_candidate_pool_as": "baseline_weighted", "answer_or_refusal_evaluated": False,
              "encoded_sha256": hashlib.sha256(encoded_bytes).hexdigest(),
              "retrieval_sha256": hashlib.sha256(retrieval_bytes).hexdigest(),
              "versions": {name: importlib.metadata.version(name) for name in ["sentence-transformers", "transformers", "torch"]},
              "summary": summary, "queries": results,
              "performance": {"cold_load_seconds": cold_load, "all_pairs_count": len(pairs),
                              "all_pairs_seconds": inference_seconds, "warm_query_candidate_count": end - start,
                              "warm_query_repeats_seconds": warm_seconds, "warm_query_median_seconds": statistics.median(warm_seconds),
                              "repeat_max_abs_score_drift": max_drift,
                              "peak_process_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": summary, "performance": result["performance"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["download", "infer"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--encoded", type=Path)
    parser.add_argument("--retrieval", type=Path)
    args = parser.parse_args()
    if args.mode == "download":
        download(args.output)
    else:
        infer(args.encoded, args.retrieval, args.output)
