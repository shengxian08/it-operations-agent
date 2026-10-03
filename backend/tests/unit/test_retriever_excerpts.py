from app.rag.retriever import _relevant_excerpt


def test_long_pdf_excerpt_starts_near_question_instead_of_page_header() -> None:
    content = (
        "AI 招聘工具落地实施方案 第3页 "
        + "阶段安排与背景说明。" * 18
        + "3.2 试点岗位选择标准：岗位量大、简历流量高；任职要求标准化；"
        "试点阶段避开高敏感岗位。"
    )

    excerpt = _relevant_excerpt(
        content,
        "AI 招聘工具落地方案中试点岗位选择标准是什么？",
        "AI 招聘工具落地实施方案",
    )

    assert "试点岗位选择标准" in excerpt
    assert "岗位量大" in excerpt
    assert excerpt.index("试点岗位选择标准") < 50
    assert "第3页" not in excerpt


def test_pdf_excerpt_keeps_the_matching_section_without_neighboring_sections() -> None:
    content = (
        "AI 招聘工具落地实施方案 第3页 "
        + "前文背景。" * 30
        + "3.1 工具评估 人工复核环节需要保留。 "
        "3.2 试点岗位选择标准 "
        "• 岗位量大、简历流量高，便于短期积累验证数据； "
        "• 任职要求相对标准化，如客服、销售和初级技术岗； "
        "• 非高敏感岗位，如涉密岗和高管岗，试点阶段风险可控。 "
        "3.3 部署与分层培训计划 培训对象包括一线 HR。"
    )

    excerpt = _relevant_excerpt(content, "岗位选择标准是什么？", "AI 招聘工具落地实施方案")

    assert excerpt.startswith("3.2 试点岗位选择标准")
    assert "岗位量大" in excerpt
    assert "任职要求相对标准化" in excerpt
    assert "非高敏感岗位" in excerpt
    assert "人工复核环节" not in excerpt
    assert "3.3" not in excerpt
    assert "…" not in excerpt
