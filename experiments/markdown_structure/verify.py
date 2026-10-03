"""Run current source APIs against immutable synthetic and repository fixtures.

The Docker invocation has no network, a read-only approved public model cache,
and no settings/secrets/production mounts. The model loader is explicitly local;
the application embedder constructor, tokenizer guard and encode methods run.
"""
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import time
from types import SimpleNamespace

import app.rag.ingest as ingest_module
from app.agent.graph import _answer_prompt
from app.rag.ingest import (
    EmbeddingInputTooLong,
    SentenceTransformerEmbedder,
    chunk_markdown,
    chunk_search_text,
    parse_frontmatter,
    search_text,
)
from app.rag.markdown import PARSER_VERSION, parse_markdown
from app.rag.retriever import _to_hit
from experiments.technology_review.controls import validate_corpus

MODEL = "BAAI/bge-small-zh-v1.5"
REVISION = "7999e1d3359715c523056ef9478215996d62a620"


def sha(data):
    return hashlib.sha256(data).hexdigest()


async def run(args):
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise ValueError("requires offline cached model")
    if not Path(ingest_module.__file__).resolve().is_relative_to(Path("/workspace/backend")):
        raise ValueError("must import current source, not the image's historical app")
    corpus_bytes = args.corpus.read_bytes()
    corpus = json.loads(corpus_bytes)
    validate_corpus(corpus)
    previous_bytes = args.previous.read_bytes()
    previous = json.loads(previous_bytes)
    if sha(corpus_bytes) != previous["corpus_sha256"]:
        raise ValueError("fixed corpus changed")
    import torch
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    import sentence_transformers
    snapshot = Path(os.environ["HF_HOME"]) / "hub/models--BAAI--bge-small-zh-v1.5/snapshots" / REVISION
    real_loader = sentence_transformers.SentenceTransformer

    def local_loader(name, *, revision):
        if name != MODEL or revision != REVISION:
            raise ValueError("unexpected model identity")
        return real_loader(str(snapshot), token=False, local_files_only=True, device="cpu")

    started = time.perf_counter()
    sentence_transformers.SentenceTransformer = local_loader
    try:
        embedder = SentenceTransformerEmbedder(MODEL, revision=REVISION)
    finally:
        sentence_transformers.SentenceTransformer = real_loader
    load_seconds = time.perf_counter() - started
    if embedder.dimensions != 512:
        raise ValueError("unexpected vector dimensions")

    chunks, baseline, contextual = [], [], []
    for document in sorted(corpus["documents"], key=lambda item: item["source_path"]):
        source = document["markdown"]
        for chunk in chunk_markdown(source, source_path=document["source_path"],
                source_title=document["title"], version=document["version"], access_level=document["access"]):
            if source[chunk.char_start:chunk.char_end] != chunk.content:
                raise ValueError("current source span mismatch")
            record = asdict(chunk)
            record.update(synthetic_document_id=document["id"], id=f"{document['id']}:{chunk.chunk_index}")
            chunks.append(record)
            baseline.append(search_text(chunk.source_title, chunk.content))
            contextual.append(chunk_search_text(chunk))
    if len(chunks) != len(previous["chunks"]):
        raise ValueError("unexpected corpus boundary change")
    # The immutable short corpus must preserve every old raw chunk and source span.
    for old, current in zip(previous["chunks"], chunks, strict=True):
        for key, value in old.items():
            if current[key] != value:
                raise ValueError(f"old raw identity changed: {key}")
    if baseline != previous["inputs"]["baseline"] or contextual != previous["inputs"]["heading_context"]:
        raise ValueError("production search representation differs from the approved prototype")
    counts = await embedder.token_counts(contextual)
    vectors = []
    started = time.perf_counter()
    for offset in range(0, len(contextual), 16):
        vectors.extend(await embedder.encode(contextual[offset:offset + 16]))
    encode_seconds = time.perf_counter() - started
    norms = [sum(v * v for v in row) ** 0.5 for row in vectors]
    if any(len(row) != 512 for row in vectors) or any(abs(norm - 1) > 0.001 for norm in norms):
        raise ValueError("unexpected vector shape or normalization")
    drift = max(abs(a - b) for old, new in zip(previous["vectors"]["heading_context"], vectors, strict=True)
                for a, b in zip(old, new, strict=True))

    seed_records = []
    for path in sorted(args.seeds.glob("*.md")):
        raw_bytes = path.read_bytes()
        source = raw_bytes.decode("utf-8")
        record = {"source_path": path.name, "source_sha256": sha(raw_bytes), "repository_fixture": True,
                  "enterprise_private_sample": False, "source": source}
        try:
            metadata = parse_frontmatter(source)
            seed_chunks = chunk_markdown(source, source_path=path.name,
                source_title=parse_markdown(source).title or path.stem,
                version=metadata.get("version", "repository-audit"),
                access_level=metadata.get("access_level", "employee"))
            inputs = [chunk_search_text(chunk) for chunk in seed_chunks]
            lengths = [len(ids) for ids in embedder._model.tokenizer(inputs, truncation=False,
                       add_special_tokens=True, verbose=False)["input_ids"]]
            record.update(chunks=[asdict(chunk) for chunk in seed_chunks], inputs=inputs, token_counts=lengths)
            if any(source[c.char_start:c.char_end] != c.content for c in seed_chunks):
                raise ValueError("seed span mismatch")
            await embedder.token_counts(inputs)
            record["status"] = "verified_source_spans_and_real_token_budget"
        except (ValueError, EmbeddingInputTooLong) as error:
            record.update(status="explicit_manual_review_required", error=str(error))
        seed_records.append(record)
    if len(seed_records) != 33:
        raise ValueError("expected all 33 repository Markdown fixtures")

    over = search_text("预算反例", "网" * 600, ["真实章节"])
    over_count = len(embedder._model.tokenizer(over, truncation=False,
                    add_special_tokens=True, verbose=False)["input_ids"])
    encode_called = False
    original_encode = embedder._model.encode

    def forbidden_encode(*args, **kwargs):
        nonlocal encode_called
        encode_called = True
        raise AssertionError("over-budget input reached model.encode")

    embedder._model.encode = forbidden_encode
    try:
        await embedder.encode([over])
        raise AssertionError("real tokenizer did not reject over-budget input")
    except EmbeddingInputTooLong as error:
        rejection = {"input": over, "actual_tokens": over_count, "error": str(error),
                     "model_encode_called": encode_called, "synthetic": True}
    finally:
        embedder._model.encode = original_encode
    if encode_called or over_count <= 512:
        raise AssertionError("invalid negative token experiment")

    source = "# 指南\n\n## 配置\n\n```powershell\n    Get-Service\n\n" + "    retry = 2\n" * 23 + "```\n"
    [chunk] = chunk_markdown(source, source_path="prompt-proof.md", source_title="指南")
    hit = _to_hit(SimpleNamespace(**asdict(chunk), revision_id="synthetic-prompt-proof"), "Get-Service", 1, 1, 1, 1)
    prompt = _answer_prompt("如何检查服务？", [hit])
    context = json.loads(prompt.split("<knowledge_evidence>\n", 1)[1].split("\n</knowledge_evidence>", 1)[0])
    if context[0]["excerpt"] != chunk.content or len(chunk.content) <= 240:
        raise ValueError("actual prompt lost complete raw code")
    query_inputs = [embedder.query_instruction + query["text"] for query in corpus["queries"]]
    query_counts = await embedder.token_counts(query_inputs)
    query_vectors = await embedder.encode(query_inputs)
    result = {"schema": 2, "synthetic": True, "real_embeddings": True, "answer_model_executed": False,
        "corpus_sha256": sha(corpus_bytes), "previous_encoded_sha256": sha(previous_bytes),
        "model": previous["model"], "chunks": chunks, "inputs": {"baseline": baseline, "heading_context": contextual},
        "vectors": {"baseline": previous["vectors"]["baseline"], "heading_context": vectors, "queries": query_vectors},
        "baseline_vectors_origin": "immutable previous real BGE experiment; deliberately reused control arm",
        "parser_version": PARSER_VERSION, "context_token_counts": counts, "query_token_counts": query_counts,
        "previous_context_max_abs_drift": drift,
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
            "network": "docker --network none", "cache": "read-only pinned local snapshot; token=False",
            "loader": "application constructor; local-only SentenceTransformer loader substituted for cache isolation",
            "app_source_sha256": sha(Path(ingest_module.__file__).read_bytes()),
            "parser_source_sha256": sha(Path('/workspace/backend/app/rag/markdown.py').read_bytes()),
            "versions": {name: importlib.metadata.version(name) for name in
                         ["markdown-it-py", "sentence-transformers", "transformers", "torch"]}},
        "performance": {"load_seconds": load_seconds, "encode_seconds": encode_seconds,
            "peak_process_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024},
        "seed_audit": seed_records, "over_budget_rejection": rejection,
        "actual_prompt_proof": {"synthetic": True, "answer_model_executed": False,
            "source": source, "chunk": asdict(chunk), "prompt": prompt, "context": context}}
    args.output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"chunks": len(chunks), "maximum_context_tokens": max(counts), "context_vector_drift": drift,
        "seed_documents": len(seed_records), "seed_manual_review": [r["source_path"] for r in seed_records
            if r["status"] != "verified_source_spans_and_real_token_budget"], "negative": rejection,
        "performance": result["performance"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("corpus", "previous", "seeds", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
