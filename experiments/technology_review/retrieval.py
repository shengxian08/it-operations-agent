"""Single-variable synthetic retrieval comparisons in new test collections.

The baseline reproduces the production scoring policy, not the PG snapshot/API.
The application-layer RRF does not use Qdrant native sparse/fusion APIs.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import statistics
import time
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models
from rank_bm25 import BM25Okapi

from app.production.knowledge import _min_max
from app.rag.ingest import tokenize
from app.rag.retriever import ACCESS_HIERARCHY, LexicalReranker
from experiments.technology_review.controls import document_metrics, rrf, validate_corpus


def select_documents(ids, by_id, *, top_k=5):
    result, counts = [], {}
    for ident in ids:
        doc_id = by_id[ident]["synthetic_document_id"]
        if counts.get(doc_id, 0) >= 2:
            continue
        counts[doc_id] = counts.get(doc_id, 0) + 1
        result.append(doc_id)
        if len(result) == top_k:
            break
    return result


async def run(corpus_path, encoded_path, output, run_id):
    if not run_id.isalnum() or len(run_id) > 24:
        raise ValueError("run-id must be a short alphanumeric test identifier")
    corpus_bytes, encoded_bytes = corpus_path.read_bytes(), encoded_path.read_bytes()
    corpus, encoded = json.loads(corpus_bytes), json.loads(encoded_bytes)
    validate_corpus(corpus)
    if encoded["corpus_sha256"] != hashlib.sha256(corpus_bytes).hexdigest():
        raise ValueError("encoded vectors do not belong to this exact corpus")
    if not encoded["real_embeddings"] or encoded["model"]["dimensions"] != 512:
        raise ValueError("requires the real pinned 512-dimensional BGE run")
    # Explicit isolated port only; never use settings or a production URL.
    client = QdrantClient(url="http://127.0.0.1:17333", timeout=15)
    if client.info().version != "1.15.4":
        raise ValueError("unexpected test Qdrant version")
    chunks = encoded["chunks"]
    by_id = {chunk["id"]: chunk for chunk in chunks}
    key_by_point = {str(uuid5(NAMESPACE_URL, f"tech-review:{chunk['id']}")): chunk["id"] for chunk in chunks}
    collections, index_manifest = {}, {}
    try:
        for arm in ("baseline", "heading_context"):
            collection = f"technology_review_{run_id}_{arm}"
            if client.collection_exists(collection):
                raise ValueError("experiment collection already exists; choose a fresh run-id")
            client.create_collection(collection, vectors_config=models.VectorParams(size=512, distance=models.Distance.COSINE))
            collections[arm] = collection
            points = [models.PointStruct(id=str(uuid5(NAMESPACE_URL, f"tech-review:{chunk['id']}")),
                        vector=vector, payload={"chunk_id": chunk["id"], "access_level": chunk["access_level"],
                        "source_path": chunk["source_path"], "source_version": chunk["version"],
                        "raw_sha256": hashlib.sha256(chunk["content"].encode()).hexdigest(),
                        "model": encoded["model"]["name"], "model_revision": encoded["model"]["revision"]})
                      for chunk, vector in zip(chunks, encoded["vectors"][arm], strict=True)]
            client.upsert(collection, points=points, wait=True)
            if client.count(collection, exact=True).count != len(chunks):
                raise ValueError("test index count mismatch")
            index_manifest[arm] = {"collection": collection, "point_count": len(chunks),
                                   "model": encoded["model"],
                                   "inputs_sha256": hashlib.sha256(json.dumps(encoded["inputs"][arm], ensure_ascii=False).encode()).hexdigest()}
        indexes = {}
        for arm in collections:
            for role, levels in ACCESS_HIERARCHY.items():
                selected = [(chunk, text) for chunk, text in zip(chunks, encoded["inputs"][arm], strict=True)
                            if chunk["access_level"] in levels]
                indexes[arm, role] = ([chunk for chunk, _ in selected], BM25Okapi([tokenize(text) or ["_empty_"] for _, text in selected]))
        all_rows, leakage = [], []
        parameters = corpus["fixed_parameters"]
        for arm, fusion in (("baseline", "weighted"), ("baseline", "rrf"), ("heading_context", "weighted")):
            texts = dict(zip(by_id, encoded["inputs"][arm], strict=True))
            for query, query_vector in zip(corpus["queries"], encoded["vectors"]["queries"], strict=True):
                repetitions, timings, first_detail = [], [], None
                for _ in range(parameters["warm_repeats"]):
                    start = time.perf_counter()
                    result = client.query_points(collection_name=collections[arm], query=query_vector,
                        query_filter=models.Filter(must=[models.FieldCondition(key="access_level",
                            match=models.MatchAny(any=list(ACCESS_HIERARCHY[query["role"]])))]),
                        limit=parameters["branch_limit"], with_payload=False)
                    vector_scores = {key_by_point[str(point.id)]: float(point.score) for point in result.points}
                    allowed_chunks, bm25 = indexes[arm, query["role"]]
                    lexical_scores = dict(sorted([(chunk["id"], float(score)) for chunk, score in
                        zip(allowed_chunks, bm25.get_scores(tokenize(query["text"])), strict=True)],
                        key=lambda pair: (-pair[1], pair[0]))[:parameters["branch_limit"]])
                    if fusion == "weighted":
                        vectors, lexical = _min_max(vector_scores), _min_max(lexical_scores)
                        combined = {key: 0.65 * vectors.get(key, 0) + 0.35 * lexical.get(key, 0)
                                    for key in set(vectors) | set(lexical)}
                    else:
                        combined = rrf(vector_scores, lexical_scores, k=parameters["rrf_k"])
                    ids = sorted(combined, key=lambda key: (-combined[key], key))
                    rerank_scores = await LexicalReranker().score(query["text"], [texts[key] for key in ids])
                    reranked = sorted(zip(ids, rerank_scores, strict=True),
                                      key=lambda pair: (-pair[1], -combined[pair[0]], pair[0]))
                    ranking = select_documents([key for key, _ in reranked], by_id)
                    pre_ranking = select_documents(ids, by_id)
                    timings.append(time.perf_counter() - start)
                    repetitions.append(ranking)
                    for key in combined:
                        if by_id[key]["access_level"] not in ACCESS_HIERARCHY[query["role"]]:
                            leakage.append({"query": query["id"], "chunk": key})
                    if first_detail is None:
                        first_detail = {"vector_scores": vector_scores, "bm25_scores": lexical_scores,
                                        "combined_scores": combined, "reranker_scores": dict(reranked),
                                        "candidate_count": len(combined), "pre_rerank": pre_ranking, "final": ranking}
                if any(ranking != repetitions[0] for ranking in repetitions):
                    raise ValueError("repeated retrieval ranking changed")
                row = {"arm": f"{arm}_{fusion}", "query_id": query["id"], "split": query["split"],
                       "category": query["category"], "text": query["text"], "role": query["role"],
                       "gold": query["relevant"], **first_detail,
                       "metrics_pre_rerank": document_metrics(first_detail["pre_rerank"], query["relevant"]),
                       "metrics_final": document_metrics(first_detail["final"], query["relevant"]),
                       "retrieval_seconds": timings,
                       "no_answer_decision_evaluated": False}
                all_rows.append(row)
        summary = {}
        for arm in sorted({row["arm"] for row in all_rows}):
            for split in ("dev", "holdout"):
                rows = [row for row in all_rows if row["arm"] == arm and row["split"] == split]
                answerable = [row for row in rows if row["gold"]]
                times = [duration for row in rows for duration in row["retrieval_seconds"]]
                aggregate = {stage: {metric: statistics.mean(row[stage][metric] for row in answerable)
                            for metric in ("recall", "precision", "mrr", "ndcg")}
                            for stage in ("metrics_pre_rerank", "metrics_final")}
                summary[f"{arm}:{split}"] = {"query_count": len(rows), "answerable_query_count": len(answerable),
                    "no_answer_count": len(rows) - len(answerable), **aggregate,
                    "retrieval_median_seconds": statistics.median(times),
                    "retrieval_p95_seconds": sorted(times)[int(0.95 * (len(times) - 1))],
                    "retrieval_range_seconds": [min(times), max(times)]}
        result = {"schema": 1, "synthetic": True, "real_embeddings": True,
                  "production_snapshot_api_evaluated": False, "answer_model_evaluated": False,
                  "tie_policy": "stable chunk ID for reproducibility; production ties have no explicit final ID sort",
                  "corpus_sha256": encoded["corpus_sha256"], "encoded_sha256": hashlib.sha256(encoded_bytes).hexdigest(),
                  "parameters": parameters, "index_manifest": index_manifest,
                  "permission_leaks": leakage, "summary": summary, "queries": all_rows,
                  "collections_retained_for_review": list(collections.values())}
        if leakage:
            raise ValueError("permission leak in experimental candidate pool")
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"summary": summary, "permission_leaks": leakage, "index_manifest": index_manifest}, ensure_ascii=False))
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--encoded", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.corpus, args.encoded, args.output, args.run_id))
