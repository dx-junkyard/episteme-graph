"""図の単位・根拠の無い章の単位の結び付け（IK-0456）。

サンドボックスのコース（t18「主結果：µ(a)と音速への制約」）は、学ぶ単位が
section_block 1つ + figure 2つなのに ``evidence_links = []`` だった。

- figure の単位は component を束ねず、``linked_claim_ids`` は DB UUID で artifact の
  claim 名前空間と突合できない。``linked_figure_ids``（``FigureRecord.figure_id``）は
  一度も読まれていなかった → 図そのものと、``FigureRecord.linked_claim_ids`` で
  その図に結びついた主張を引く。
- section_block の単位の章（本文段落 2 つ）には主張も evidence も無い（解析側の事実）
  → 章の本文チャンクは結びつけたまま、構造化された根拠が無いことを事実文で残す。
"""

from __future__ import annotations

import core.course_content_builder as ccb
from core import label_vocab
from core.course_data import UNIT_SOURCE_TEACHER_SELECTED

DOC = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def _artifacts():
    return {
        "claim_object_builder": {"claims": [
            {"claim_id": "c_fig6", "text": "We find a deviation from GR.", "section_id": "sec_58",
             "source_evidence_ids": ["ev_1"], "subclaim_ids": ["c_fig6_sub"]},
            {"claim_id": "c_fig6_sub", "text": "Deviation at late times.", "section_id": "sec_58",
             "source_evidence_ids": ["ev_1"], "parent_claim_id": "c_fig6"},
            {"claim_id": "c_other", "text": "Unrelated.", "section_id": "sec_58",
             "source_evidence_ids": ["ev_2"]},
        ]},
        "evidence_registry": {"records": [
            {"evidence_id": "ev_1", "evidence_text": "…", "source": {"block_id": "b_398"}},
            {"evidence_id": "ev_2", "evidence_text": "…", "source": {"block_id": "b_399"}},
        ]},
        "figure_table_semantics": {"figures": [
            {"figure_id": "fig_6", "caption": "Constraints on mu(a)",
             "linked_claim_ids": ["c_fig6", "c_fig6_sub"]},
            {"figure_id": "fig_5", "caption": "1D marginalized constraints", "linked_claim_ids": []},
        ]},
    }


def _bundle(documents=(DOC,)):
    return ccb._collect_structured_content({doc: _artifacts() for doc in documents})


def _row(key, kind, *, figures=(), blocks=(), sections=(), document_id=DOC):
    return {
        "unit_id": "uuid-" + key, "document_id": document_id, "stable_key": key,
        "unit_kind": kind, "label": key, "summary": "",
        "linked_claim_ids": ["550e8400-e29b-41d4-a716-446655440000"],  # DB UUID
        "linked_equation_ids": [], "linked_component_ids": [],
        "linked_figure_ids": list(figures), "agent_payload": {"figure_type": "unknown"},
        "order_index": 0, "section_ids": list(sections), "source_block_ids": list(blocks),
    }


def _units():
    rows = [
        _row("k_sec", "section_block", blocks=["b_384", "b_385"], sections=["sec_47"]),
        _row("k_fig6", "figure", figures=["fig_6"], blocks=["b_398"], sections=["sec_58"]),
        _row("k_fig5", "figure", figures=["fig_5"], blocks=["b_381"], sections=["sec_46"]),
    ]
    return {r["stable_key"]: r for r in rows}


def _topic(units):
    return {"id": "t18", "title": "主結果", "units": [
        {"kind": r["unit_kind"], "stable_key": k, "unit_id": r["unit_id"], "label": k,
         "source": UNIT_SOURCE_TEACHER_SELECTED}
        for k, r in units.items()
    ]}


def _chunks():
    return {"m1": [
        {"id": "chunk-results", "document_id": DOC, "text": "IV. RESULTS", "block_ids": ["b_384", "b_385"], "formulas": []},
        {"id": "chunk-fig6", "document_id": DOC, "text": "FIG. 6", "block_ids": ["b_398"], "formulas": []},
    ]}


def _figures_index():
    item = {"figure_id": "f-uuid-6", "document_id": DOC, "figure_key": "fig_6", "caption": "Constraints on mu(a)"}
    # fig_5 は抽出されていない（document_figures に行が無い）。
    return {"f-uuid-6": item, f"{DOC}::fig_6": item}


def _enrich(units, bundle=None):
    return ccb._enrich_topics([_topic(units)], bundle or _bundle(), _chunks(), _figures_index(), units)[0]


class TestFigureUnitRefs:
    def test_figure_unit_binds_figure_and_linked_claims(self):
        refs = ccb._figure_unit_refs([_units()["k_fig6"]], ccb._BundleScope(_bundle()))
        claims, figures, blocks = refs
        # atomic 子が同じく当たった親は子で代表させる。図に結びつかない主張は引かない。
        assert claims == [(DOC, "c_fig6_sub")]
        assert figures == [(DOC, "fig_6")]
        assert blocks == [(DOC, "b_398")]

    def test_figure_without_linked_claims_binds_only_the_figure_ref(self):
        claims, figures, _blocks = ccb._figure_unit_refs([_units()["k_fig5"]], ccb._BundleScope(_bundle()))
        assert claims == []
        assert figures == [(DOC, "fig_5")]

    def test_other_document_is_never_used(self):
        refs = ccb._figure_unit_refs([_units()["k_fig6"]], ccb._BundleScope(_bundle((DOC, OTHER))))
        for refs_list in refs:
            assert all(doc == DOC for doc, _ in refs_list)

    def test_unit_without_document_binds_nothing(self):
        row = _row("k", "figure", figures=["fig_6"], document_id="")
        assert ccb._figure_unit_refs([row], ccb._BundleScope(_bundle())) == ([], [], [])

    def test_non_figure_units_are_ignored(self):
        assert ccb._figure_unit_refs([_units()["k_sec"]], ccb._BundleScope(_bundle())) == ([], [], [])


class TestTopicWithFigureAndBareSectionUnits:
    def test_evidence_links_carry_figure_and_claim(self):
        topic = _enrich(_units())
        kinds = {(link["kind"], link["target_id"]) for link in topic["evidence_links"]}
        assert ("figure", "f-uuid-6") in kinds
        assert ("claim", "c_fig6_sub") in kinds
        # 抽出されていない図（fig_5）は捏造しない。
        assert not any(link["kind"] == "figure" and "5" in str(link["target_id"]) for link in topic["evidence_links"])
        assert "c_fig6_sub" in topic["linked_claim_ids"]
        assert set(topic["material_chunk_ids"]) == {"chunk-results", "chunk-fig6"}

    def test_bare_section_unit_leaves_a_fact_note(self):
        topic = _enrich(_units())
        assert topic["grounding_note"] == label_vocab.UNITS_WITHOUT_KNOWLEDGE_NOTE
        assert topic["coverage"] == {"status": "weak", "message": label_vocab.UNITS_WITHOUT_KNOWLEDGE_NOTE}

    def test_only_bare_section_unit_is_missing(self):
        units = {"k_sec": _units()["k_sec"]}
        topic = _enrich(units)
        assert topic["evidence_links"] == []
        assert topic["material_chunk_ids"] == ["chunk-results"]
        assert topic["coverage"]["status"] == "missing"
        assert topic["coverage"]["message"] == label_vocab.UNITS_WITHOUT_KNOWLEDGE_NOTE

    def test_no_note_when_every_unit_binds_knowledge(self):
        units = {"k_fig6": _units()["k_fig6"]}
        topic = _enrich(units)
        assert topic.get("grounding_note", "") != label_vocab.UNITS_WITHOUT_KNOWLEDGE_NOTE

    def test_note_is_regenerated_not_carried_over(self):
        assert label_vocab.UNITS_WITHOUT_KNOWLEDGE_NOTE in ccb.GENERATED_DRAFT_NOTES

    def test_note_has_no_numbers(self):
        import re
        assert not re.search(r"\d", label_vocab.UNITS_WITHOUT_KNOWLEDGE_NOTE)
