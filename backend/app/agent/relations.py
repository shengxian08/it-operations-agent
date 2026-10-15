"""Evidence-backed local relation expansion; no community-summary GraphRAG claims."""

import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    left: str = Field(min_length=1, max_length=120)
    right: str = Field(min_length=1, max_length=120)
    predicate: Literal[
        "depends_on", "uses", "configured_by", "introduced_in", "causes", "resolved_by"
    ]
    revision: str = Field(min_length=1, max_length=64)
    source: dict[str, Any]
    status: Literal["candidate", "published"] = "candidate"

    @model_validator(mode="after")
    def provenance(self) -> Self:
        if (
            not self.source.get("document_id")
            or self.source.get("index_revision") != self.revision
            or type(self.source.get("chunk_index")) is not int
            or self.source["chunk_index"] < 0
        ):
            raise ValueError(
                "Relation requires a source in the same knowledge revision"
            )
        return self


async def expand_relations(edges, entities, *, revision, readable, hops=2, limit=30):
    if (
        type(hops) is not int
        or not 1 <= hops <= 2
        or type(limit) is not int
        or not 1 <= limit <= 50
    ):
        raise ValueError("Expansion is bounded by two hops and fifty edges")
    frontier, seen, result = set(entities), set(), []
    for _ in range(hops):
        next_frontier = set()
        for index, edge in enumerate(edges):
            if index in seen or edge.status != "published" or edge.revision != revision:
                continue
            if edge.left not in frontier and edge.right not in frontier:
                continue
            if not await readable(edge.source):
                continue
            result.append(edge)
            seen.add(index)
            next_frontier.update((edge.left, edge.right))
            if len(result) >= limit:
                return result
        frontier = next_frontier - frontier
        if not frontier:
            break
    return result


def extract_candidates(text, source):
    # Only explicit supported syntax is extracted; inferred semantics require later review.
    pattern = r"(?:服务\s+)?([\w.-]+)\s+(depends_on|uses|configured_by|introduced_in|causes|resolved_by)\s+([\w.-]+)"
    return [
        Relation(
            left=m[1],
            predicate=m[2],
            right=m[3],
            revision=source["index_revision"],
            source={**source, "quote": m[0]},
        )
        for m in list(re.finditer(pattern, text))[:30]
    ]
