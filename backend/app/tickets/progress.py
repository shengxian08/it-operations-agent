"""Bounded display text for deterministic ticket progress."""
import unicodedata


def clean_summary(content: str) -> str:
    cleaned = " ".join("".join(" " if ch.isspace() else ch for ch in content
                               if ch.isspace() or not unicodedata.category(ch).startswith("C")).split())
    return cleaned if len(cleaned) <= 600 else cleaned[:600] + "…（后续内容请查看工单详情）"

