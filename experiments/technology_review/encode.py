"""Encode synthetic experiment inputs with the cached production BGE model.

Run inside the existing worker image with network disabled and cache read-only.
No API client, settings, database or knowledge publishing is invoked.
"""
import argparse
import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import time
from dataclasses import asdict

import app.rag.ingest as ingest_module
from app.rag.ingest import SentenceTransformerEmbedder, chunk_markdown, search_text
from experiments.technology_review.controls import contextual_text, validate_corpus

MODEL = "BAAI/bge-small-zh-v1.5"
REVISION = "7999e1d3359715c523056ef9478215996d62a620"


async def run(corpus_path, output):
    data = corpus_path.read_bytes()
    corpus = json.loads(data)
    validate_corpus(corpus)
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise ValueError("experiment requires HF_HUB_OFFLINE=1")
    start = time.perf_counter()
    import torch
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    import_seconds = time.perf_counter() - start
    start = time.perf_counter()
    embedder = SentenceTransformerEmbedder(MODEL, revision=REVISION)
    load_seconds = time.perf_counter() - start
    if embedder.dimensions != 512:
        raise ValueError("baseline model must report 512 dimensions")
    chunks, baseline, contextual = [], [], []
    for document in sorted(corpus["documents"], key=lambda doc: doc["source_path"]):
        for chunk in chunk_markdown(document["markdown"], source_path=document["source_path"],
                                    version=document["version"], access_level=document["access"],
                                    source_title=document["title"]):
            record = asdict(chunk)
            if document["markdown"][chunk.char_start:chunk.char_end] != chunk.content:
                raise ValueError("baseline span differs from raw source")
            record["synthetic_document_id"] = document["id"]
            record["id"] = f"{document['id']}:{chunk.chunk_index}"
            chunks.append(record)
            baseline.append(search_text(chunk.source_title, chunk.content))
            contextual.append(contextual_text(chunk.source_title, document["markdown"],
                                              chunk.char_start, chunk.content))
    async def batches(inputs):
        result = []
        for offset in range(0, len(inputs), corpus["fixed_parameters"]["embedding_batch"]):
            result.extend(await embedder.encode(inputs[offset:offset + 16]))
        return result
    start = time.perf_counter()
    base_vectors = await batches(baseline)
    baseline_seconds = time.perf_counter() - start
    start = time.perf_counter()
    context_vectors = await batches(contextual)
    context_seconds = time.perf_counter() - start
    queries = [embedder.query_instruction + query["text"] for query in corpus["queries"]]
    start = time.perf_counter()
    query_vectors = await batches(queries)
    first_query_batch_seconds = time.perf_counter() - start
    repeat_seconds, max_drift, single_seconds = [], 0.0, []
    for _ in range(corpus["fixed_parameters"]["warm_repeats"]):
        start = time.perf_counter()
        repeated = await batches(queries)
        repeat_seconds.append(time.perf_counter() - start)
        max_drift = max(max_drift, max(abs(a - b) for original, repeated_row in
                        zip(query_vectors, repeated, strict=True) for a, b in
                        zip(original, repeated_row, strict=True)))
        start = time.perf_counter()
        await embedder.encode_query(corpus["queries"][0]["text"])
        single_seconds.append(time.perf_counter() - start)
    all_vectors = base_vectors + context_vectors + query_vectors
    norms = [sum(value * value for value in row) ** 0.5 for row in all_vectors]
    if any(len(row) != 512 for row in all_vectors) or any(abs(norm - 1) > 0.001 for norm in norms):
        raise ValueError("unexpected shape or unnormalized vectors")
    cache = Path(os.environ["HF_HOME"]) / "hub"
    candidates = {
        "docling": {"module": "docling", "model_repo": None},
        "mineru": {"module": "mineru", "model_repo": None},
        "bge-reranker-v2-m3": {"module": "sentence_transformers", "model_repo": "BAAI/bge-reranker-v2-m3"},
        "qwen3-reranker-0.6b": {"module": "sentence_transformers", "model_repo": "Qwen/Qwen3-Reranker-0.6B"},
        "qwen3-embedding-0.6b": {"module": "sentence_transformers", "model_repo": "Qwen/Qwen3-Embedding-0.6B"},
    }
    preflight = {}
    for key, spec in candidates.items():
        module_available = importlib.util.find_spec(spec["module"]) is not None
        snapshots = (list((cache / f"models--{spec['model_repo'].replace('/', '--')}" / "snapshots").glob("*"))
                     if spec["model_repo"] else [])
        preflight[key] = {"module_available": module_available,
                          "cached_model_revisions": [path.name for path in snapshots],
                          "executed": False,
                          "reason": "parser package and verified artifacts absent" if not module_available
                                    else "candidate pinned model weights absent from read-only offline cache"}
    result = {
        "schema": 1, "synthetic": True, "real_embeddings": True, "answer_model_executed": False,
        "corpus_sha256": hashlib.sha256(data).hexdigest(),
        "model": {"name": MODEL, "revision": REVISION, "dimensions": 512,
                  "normalized": True, "query_instruction": embedder.query_instruction, "device": "cpu"},
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "torch_threads": torch.get_num_threads(), "network": "disabled by docker run",
                        "app_source": str(Path(ingest_module.__file__).resolve()),
                        "app_source_sha256": hashlib.sha256(Path(ingest_module.__file__).read_bytes()).hexdigest(),
                        "versions": {name: importlib.metadata.version(name) for name in
                                     ["sentence-transformers", "transformers", "torch", "qdrant-client"]}},
        "chunks": chunks, "inputs": {"baseline": baseline, "heading_context": contextual},
        "vectors": {"baseline": base_vectors, "heading_context": context_vectors, "queries": query_vectors},
        "performance": {"dependency_import_seconds": import_seconds, "cold_model_load_seconds": load_seconds,
                        "baseline_document_batch_seconds": baseline_seconds,
                        "context_document_batch_seconds": context_seconds,
                        "first_query_batch_seconds": first_query_batch_seconds,
                        "warm_query_batches_seconds": repeat_seconds,
                        "warm_query_batch_median_seconds": statistics.median(repeat_seconds),
                        "warm_single_query_seconds": single_seconds,
                        "warm_single_query_median_seconds": statistics.median(single_seconds),
                        "repeated_query_max_abs_drift": max_drift,
                        "peak_process_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024},
        "candidate_preflight": preflight,
    }
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ["model", "environment", "performance", "candidate_preflight"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--required-app-root", type=Path)
    arguments = parser.parse_args()
    if arguments.required_app_root and not Path(ingest_module.__file__).resolve().is_relative_to(arguments.required_app_root.resolve()):
        raise ValueError("app was imported from the image instead of the required current source mount")
    asyncio.run(run(arguments.corpus, arguments.output))
