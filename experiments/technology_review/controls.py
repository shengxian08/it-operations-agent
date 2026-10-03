"""Small measurement helpers, deliberately independent of production changes."""
import math
import re


def document_metrics(ranking, relevant, *, k=5):
    if not relevant:
        return None  # No-answer refusal requires a separate decision/model test.
    unique = list(dict.fromkeys(ranking))[:k]
    gold = set(relevant)
    flags = [key in gold for key in unique]
    dcg = sum(1 / math.log2(i + 2) for i, hit in enumerate(flags) if hit)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(gold))))
    return {"recall": sum(flags) / len(gold), "precision": sum(flags) / k,
            "mrr": next((1 / (i + 1) for i, hit in enumerate(flags) if hit), 0),
            "ndcg": dcg / ideal}


def rrf(vector_scores, lexical_scores, *, k=60):
    if k <= 0:
        raise ValueError("RRF k must be positive")
    scores = {}
    for branch in (vector_scores, lexical_scores):
        # Equal scores use stable IDs to make synthetic repeats reproducible.
        ordered = sorted(branch, key=lambda key: (-branch[key], key))
        for position, key in enumerate(ordered, 1):
            scores[key] = scores.get(key, 0) + 1 / (k + position)
    return scores


def contextual_text(title, source, start, raw):
    if source[start:start + len(raw)] != raw:
        raise ValueError("raw chunk is not the stated source span")
    levels = {}
    # The experiment corpus is plain Markdown without headings inside fences.
    # A production parser must distinguish fenced code before extracting headings.
    for match in re.finditer(r"(?m)^(#{1,6})[ \t]+(.+?)\s*$", source[:start]):
        level = len(match.group(1))
        levels = {n: text for n, text in levels.items() if n < level}
        levels[level] = match.group(2).strip()
    path = " > ".join(levels[n] for n in sorted(levels))
    return f"{title}\n章节：{path}\n{raw}" if path else f"{title}\n{raw}"


def validate_corpus(corpus):
    if corpus.get("synthetic") is not True:
        raise ValueError("this controlled corpus must be labelled synthetic")
    documents = {doc["id"]: doc for doc in corpus["documents"]}
    if len(documents) != len(corpus["documents"]):
        raise ValueError("duplicate document IDs")
    roles = {"employee": {"employee"}, "support": {"employee", "support"},
             "admin": {"employee", "support", "admin"}}
    query_ids = set()
    for query in corpus["queries"]:
        if query["id"] in query_ids:
            raise ValueError("duplicate query IDs")
        query_ids.add(query["id"])
        if query["split"] not in {"dev", "holdout"}:
            raise ValueError("invalid split")
        for relevant in query["relevant"]:
            if relevant not in documents:
                raise ValueError("gold references missing document")
            if documents[relevant]["access"] not in roles[query["role"]]:
                raise ValueError("gold violates permission")
    if not any(query["split"] == "holdout" for query in corpus["queries"]):
        raise ValueError("missing holdout queries")
