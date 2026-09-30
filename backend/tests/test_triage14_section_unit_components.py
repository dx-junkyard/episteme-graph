"""章立ての単位のトピックに、単位の主張を参照する部品（component）を ⚓ として出す（TRIAGE14）。

章立て・図の単位は component を束ねないため、教材に部品チップが 1 つも出ず、
学習者が部品の文脈（``/components/{id}/context``）に入れなかった。結びつきは
A層の ``linked_claim_ids`` だけで引き（推定しない・同じ論文の中だけ）、散文は変えない。
"""

from __future__ import annotations

import core.course_content_builder as ccb
from core.course_content_builder import build_topic_evidence_items

from tests.test_ik0388_section_block_evidence import (
    DOC, OTHER, _artifacts, _chunks, _figures_index, _section_unit, _topic,
)


def _with_components(doc_components):
    arts = _artifacts()
    arts["component_assembly"] = {"components": doc_components}
    return arts


def _enrich(artifacts_by_doc):
    bundle = ccb._collect_structured_content(artifacts_by_doc)
    units = {"k1:sec2": _section_unit()}
    return ccb._enrich_topics([_topic("k1:sec2")], bundle, _chunks(), _figures_index(), units)[0]


COMP_HIT = {"component_id": "comp_7", "name": "Flux ratio", "label": "Flux ratio",
            "summary": "s", "linked_claim_ids": ["c_data"]}
COMP_MISS = {"component_id": "comp_8", "name": "Other", "label": "Other",
             "summary": "s", "linked_claim_ids": ["c_result"]}


def test_component_linked_to_unit_claim_becomes_chip():
    topic = _enrich({DOC: _with_components([COMP_MISS, COMP_HIT])})
    assert "comp_7" in topic["linked_component_ids"]
    assert "comp_8" not in topic["linked_component_ids"]
    items = build_topic_evidence_items(topic)
    comps = [i for i in items if i["kind"] == "component"]
    assert [c["id"] for c in comps] == ["comp_7"]
    blocks = [b for b in topic["content_blocks"] if b.get("type") == "components"]
    assert blocks and blocks[0]["items"][0]["component_id"] == "comp_7"


def test_prose_is_not_rewritten_by_linked_components():
    base = _enrich({DOC: _artifacts()})
    topic = _enrich({DOC: _with_components([COMP_HIT])})
    assert topic["summary"] == base["summary"]
    assert topic["content"] == base["content"]


def test_other_document_component_is_not_used():
    other = _with_components([COMP_HIT])
    topic = _enrich({DOC: _artifacts(), OTHER: other})
    assert "comp_7" not in topic["linked_component_ids"]


def test_no_linked_component_means_no_chip():
    topic = _enrich({DOC: _with_components([COMP_MISS])})
    assert not [i for i in build_topic_evidence_items(topic) if i["kind"] == "component"]


# --- 要素文脈の学習者射影（TRIAGE14 項目 2 / 3） ---------------------------------

from core import element_context, learner_context_common  # noqa: E402
from core.text_excerpt import normalize_source_line_breaks  # noqa: E402


def test_derivation_item_hides_english_generated_label_and_count():
    item = learner_context_common.project_item({
        "element_type": "derivation", "element_id": "derivation_claim_0014",
        "label": "Logical progression through 41 claims in section…",
        "sublabel": "操作: 比較・検証 → 結論を導出 → 比較・検証 → 中間主張を導出 → 式を適用 → 仮定を提示",
        "relation_label": "の導出に属する", "relation_status": "source_backed",
    })
    assert item["label"] == "導出の流れ"
    assert "41" not in item["label"] + item["sublabel"]
    assert item["sublabel"] == "操作: 比較・検証 → 結論を導出 → 中間主張を導出 → 式を適用"
    assert item["id"] is None and item["navigable"] is False


def test_non_navigable_internal_ids_are_not_exposed():
    for eid, etype in (("ev_0280", "evidence"), ("fig_6", "figure")):
        item = learner_context_common.project_item({
            "element_type": etype, "element_id": eid, "label": "x",
            "relation_label": "r", "relation_status": "source_backed",
        })
        assert item["id"] is None


def test_claim_focus_does_not_repeat_the_same_text_three_times():
    text = "the k-essence-like parameterization instead gives µ0 ≈1.04."
    focus = element_context._project_focus(
        {"label": text, "headline": text, "intrinsic_summary": text,
         "intrinsic": {"kind_key": "result", "summary": text, "summary_is_source_language": True}},
        element_context.ELEMENT_TYPE_CLAIM, "u",
    )
    assert focus["intrinsic_summary"] == ""
    assert "summary" not in focus.get("intrinsic", {})


def test_line_break_normalization():
    assert normalize_source_line_breaks("Interest-\ningly, FIG. 10.\nThe\nmap\n\nNext") == "Interestingly, FIG. 10. The map\n\nNext"
    assert normalize_source_line_breaks("k-essence-like") == "k-essence-like"
