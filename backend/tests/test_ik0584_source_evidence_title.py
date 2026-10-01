"""IK-0584: 出典（source）カードの見出しに引用（summary）を流用しない。

見出しは掲載節の見出しだけ。見出しが無い・引用と同じ文なら ``title`` を持たせず、
カードは引用だけを描く。freeze 済みの旧教材（label なしの source リンク）にも読み時で効く。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import course_content_builder as ccb  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_QUOTE = "The residual vanishes when the TOV equation holds."


def _source_item(link: dict) -> dict:
    items = ccb.build_topic_evidence_items({"evidence_links": [link]})
    return next(i for i in items if i["kind"] == "source")


def test_old_frozen_source_link_has_no_duplicated_title():
    item = _source_item({"kind": "source", "target_id": "ev_0001", "summary": _QUOTE,
                         "support_role": "source_quote"})
    assert "title" not in item
    assert item["summary"] == _QUOTE


def test_section_heading_becomes_title_and_excerpt_stays_summary():
    item = _source_item({"kind": "source", "target_id": "ev_0001", "summary": _QUOTE,
                         "support_role": "source_quote", "section_label": "3 Residual constraint"})
    assert item["title"] == "3 Residual constraint"
    assert item["summary"] == _QUOTE
    assert item["title"] != item["summary"]


def test_heading_identical_to_excerpt_is_dropped():
    item = _source_item({"kind": "source", "target_id": "ev_0001", "summary": _QUOTE,
                         "label": "  The residual vanishes when the  TOV equation holds. "})
    assert "title" not in item


def test_raw_internal_id_is_never_the_title():
    item = _source_item({"kind": "source", "target_id": "ev_0042", "summary": ""})
    assert item.get("title", "") != "ev_0042"


def test_latex_source_keeps_formula_title():
    item = _source_item({"kind": "source", "target_id": "ev_0002", "summary": "",
                         "latex": r"\frac{a}{b}", "support_role": "equation_quote"})
    assert item["title"] == "数式引用"


def test_freeze_carries_section_label_onto_source_links():
    evidence = {"ev_1": {"evidence_id": "ev_1", "evidence_text": _QUOTE,
                         "evidence_role": "source_quote", "section_label": "Methods"}}
    links = ccb._topic_evidence_links(
        [{"component_id": "comp_001", "linked_evidence_ids": ["ev_1"]}],
        [], {}, evidence, "linked",
    )
    source = next(link for link in links if link["kind"] == "source")
    assert source["section_label"] == "Methods"
    item = _source_item(source)
    assert item["title"] == "Methods" and item["summary"] == _QUOTE


def test_learner_card_omits_heading_when_title_missing():
    src = (_ROOT / "frontend/public/js/app.js").read_text(encoding="utf-8")
    assert "evidenceItem.title || evidenceItem.id" not in src
    assert re.search(r"cardTitle \? '<strong>'", src)
