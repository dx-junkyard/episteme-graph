"""IK-0370: 「学ぶ単位が立っていない章」の列挙から見出しでない断片を除く。

文書構造の ``sections`` に混ざる図の軸目盛・表のセル・論文ヘッダ・参考文献・雑誌フッタを
章の名前として利用者向けの文に載せない。除いた原文は run 内部の記録
（``uncovered_sections_dropped``）に残す。
"""

from __future__ import annotations

import pytest

from core import course_content_builder as ccb

# 砂場コース 900588b4 の course_content_status.uncovered_sections に実際に並んだ断片
# （IK-0370 の basis と uxsim run 20260927T052839Z の観測）。
JUNK_TITLES = [
    "40'",
    "100",
    "S8",
    "Core\nRA\n(deg)",
    "Draft version June 2, 2026\nTypeset using LATEX twocolumn style in AASTeX7.0.1",
    "REFERENCES",
    "RSFR(z)/RSFR,0",
    "Case\nC",
    "MNRAS 000, 1–16 (2026)",
    "arXiv:2605.31198v1 [astro-ph.HE] 1 Jun 2026",
    "ACKNOWLEDGMENTS",
    "7 References",
    "40′ 20′",
    "0.5 GeV",
    "Abbott, B. P., Abbott, R., et al. 2016, PhRvL, 116, 061102",
    # 追補（IK-0374 の周回で観測）: 表の列見出しの一般語
    "Count",
    "Case",
    "Cut",
    "Estimates",
    "Column Density",
    "Column Density (cm^-2)",
    "P (%)",
]

REAL_HEADINGS = [
    "2. METHODS",
    "2.1 Astrophysical modeling",
    "2.1. Astrophysical modeling of DTD",
    "2.2. Hierarchical Bayesian Analysis",
    "Introduction",
    "Results",
    "2 Data",
    "APPENDIX A. DERIVATION",
    "Fermi-LAT observations (2008–2024)",
    "The ΛCDM model",
    "序論",
    "1 はじめに",
    # 列見出しの語を含むが見出しとして実在する形は残す
    "Case study",
    "Column density profiles",
    "3. Count rates and exposure",
    "Model",
]


@pytest.mark.parametrize("title", JUNK_TITLES)
def test_junk_titles_are_rejected(title):
    assert ccb.section_title_rejection_reason(title) is not None
    assert ccb.is_valid_section_title(title) is False


@pytest.mark.parametrize("title", REAL_HEADINGS)
def test_real_headings_are_kept(title):
    assert ccb.section_title_rejection_reason(title) is None


def test_rejection_reasons_are_specific():
    r = ccb.section_title_rejection_reason
    assert r("Core\nRA\n(deg)") == "newline"
    assert r("100") == "numeric"
    assert r("S8") == "too_short"
    assert r("REFERENCES") == "boilerplate"
    assert r("RSFR(z)/RSFR,0") == "formula_like"
    assert r("") == "empty"
    assert r("Count") == "table_header"
    assert r("Column Density") == "table_header"
    assert r("Estimates") == "table_header"


def _artifacts(titles: list[str], covered: set[int] = frozenset()) -> dict:
    sections = [
        {"section_id": f"sec_{i}", "title": t, "order": i} for i, t in enumerate(titles)
    ]
    blocks = [{"block_id": f"lb_{i}", "section_ids": [f"sec_{i}"]} for i in sorted(covered)]
    return {
        "document_structure": {"sections": sections},
        "paper_skeleton": {"logical_blocks": blocks or [{"block_id": "lb_x", "section_ids": []}]},
    }


def test_uncovered_titles_drop_junk_and_record_it():
    titles = ["1. INTRODUCTION"] + JUNK_TITLES + REAL_HEADINGS[:4]
    artifacts_by_doc = {"doc-1": _artifacts(titles, covered={0})}
    dropped: list[str] = []
    uncovered = ccb._uncovered_section_titles(artifacts_by_doc, dropped=dropped)
    assert uncovered == REAL_HEADINGS[:4]
    # 除いた原文は順序保持で記録される（strip のみ。内容は改変しない）
    assert dropped == [t.strip() for t in JUNK_TITLES]
    for junk in JUNK_TITLES:
        assert junk not in uncovered


def test_uncovered_titles_without_dropped_sink_still_filters():
    artifacts_by_doc = {"doc-1": _artifacts(["100", "2. METHODS"])}
    assert ccb._uncovered_section_titles(artifacts_by_doc) == ["2. METHODS"]


def test_dropped_is_deduplicated_across_documents():
    artifacts_by_doc = {
        "doc-1": _artifacts(["REFERENCES", "2. METHODS"]),
        "doc-2": _artifacts(["REFERENCES", "3. RESULTS"]),
    }
    dropped: list[str] = []
    uncovered = ccb._uncovered_section_titles(artifacts_by_doc, dropped=dropped)
    assert uncovered == ["2. METHODS", "3. RESULTS"]
    assert dropped == ["REFERENCES"]


def test_note_does_not_mention_counts():
    assert not any(ch.isdigit() for ch in ccb.UNCOVERED_SECTIONS_NOTE)


def test_equation_section_label_drops_junk_heading():
    assert ccb._section_title({"title": "40'"}) == ""
    assert ccb._section_title({"title": "Core\nRA\n(deg)"}) == ""
    assert ccb._section_title({"title": "  2.1 Astrophysical modeling "}) == (
        "2.1 Astrophysical modeling"
    )
