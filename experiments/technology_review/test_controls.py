"""Checks that prevent misleading retrieval and provenance measurements."""
from pathlib import Path

import pytest

from experiments.technology_review.controls import (
    contextual_text, document_metrics, rrf, validate_corpus,
)
from experiments.technology_review.parsing import atomic_markdown_spans


def test_metrics_count_documents_once_and_do_not_call_no_answer_recall_zero():
    result = document_metrics(["x", "b", "b", "a"], ["a", "b"], k=3)
    assert result["recall"] == 1
    assert result["mrr"] == 0.5
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["ndcg"] == pytest.approx((1 / 1.584962500721156 + 0.5) / (1 + 1 / 1.584962500721156))
    assert document_metrics(["x"], [], k=5) is None


def test_rrf_ignores_score_scale_and_does_not_double_count_same_list():
    assert rrf({"a": 0.8, "b": 0.2}, {"b": 40, "c": 5}, k=60) == rrf(
        {"a": 8, "b": 2}, {"b": 0.04, "c": 0.005}, k=60
    )
    with pytest.raises(ValueError):
        rrf({}, {}, k=0)


def test_heading_context_uses_only_prior_source_headings_and_preserves_raw_span():
    text = "# 总指南\n\n## E-901\n\n先检查证书。\n\n## E-902\n\n联系支持。"
    start = text.index("先检查证书。")
    assert contextual_text("指南", text, start, "先检查证书。") == "指南\n章节：总指南 > E-901\n先检查证书。"
    with pytest.raises(ValueError):
        contextual_text("指南", text, start, "猜测的修复步骤")


def test_validation_rejects_gold_outside_role_and_missing_holdout(tmp_path: Path):
    corpus = {"synthetic": True, "documents": [{"id": "private", "access": "admin"}],
              "queries": [{"id": "q", "split": "holdout", "role": "employee", "relevant": ["private"]}]}
    with pytest.raises(ValueError, match="permission"):
        validate_corpus(corpus)
    corpus["queries"][0]["relevant"] = []
    validate_corpus(corpus)
    corpus["queries"][0]["split"] = "dev"
    with pytest.raises(ValueError, match="holdout"):
        validate_corpus(corpus)


def test_code_block_with_blank_line_remains_one_raw_span_and_oversize_is_rejected():
    source = "# 诊断\n\n```powershell\nGet-Item -LiteralPath example\n\n# 保留输出\n```\n"
    spans = atomic_markdown_spans(source)
    assert len(spans) == 1
    assert spans[0]["type"] == "code"
    assert source[spans[0]["start"]:spans[0]["end"]] == spans[0]["raw"]
    with pytest.raises(ValueError, match="oversize code fence"):
        atomic_markdown_spans("```powershell\n" + "x" * 801 + "\n```\n")


def test_unclosed_code_block_cannot_silently_enter_paragraph_index():
    with pytest.raises(ValueError, match="unclosed"):
        atomic_markdown_spans("# 指南\n\n```powershell\nGet-Item\n")
