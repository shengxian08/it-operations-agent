"""Verify pinned CPU embeddings offline; this is not a real-model quality evaluation."""
import asyncio
import json
import math
from app.core.config import get_settings
from app.production.knowledge import build_embedder


async def main():
    settings = get_settings()
    if settings.embedding_mode != "sentence-transformer":
        raise ValueError("Select pinned semantic embeddings before this probe")
    embedder = await asyncio.to_thread(build_embedder, settings)
    documents = await embedder.encode(["VPN无法连接，请检查网络和VPN客户端。", "打印机没有纸张，请添加打印纸。"])
    query = await embedder.encode_query("公司的VPN连接失败应该检查什么？")
    scores = [sum(a*b for a,b in zip(query,document,strict=True)) for document in documents]
    norms = [math.sqrt(sum(value*value for value in vector)) for vector in [query,*documents]]
    assert embedder.dimensions == settings.embedding_dimensions
    assert all(abs(norm-1) < .001 for norm in norms)
    assert scores[0] > scores[1]
    print(json.dumps({"embedding_model":settings.embedding_model,"revision":settings.embedding_revision,
        "dimensions":embedder.dimensions,"normalized":True,"expected_document_ranked_first":True}))


if __name__ == "__main__":
    asyncio.run(main())
