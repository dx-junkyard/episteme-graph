"""第 15 周 教員側の構造表示の是正（内部 ID・重複・見出し）。"""

from __future__ import annotations

from core.deliberation.graph_dialogue import build_graph_grounding, graph_grounding_to_text
from core.graph_paper_layer.builder import _reattach_orphan_parents, is_heading_like_title
from core.theory_modules import related as tm_related
from core.theory_modules.schema import (
    UNIDENTIFIED_ELEMENT_TEXT,
    equation_display_name,
    mask_internal_ids_readable,
)


class TestMaskInternalIds:
    def test_truncated_equation_id_is_replaced_whole(self):
        out = mask_internal_ids_readable("Apply constraint eq_eqcand_inline_blk_3df32664_")
        assert "eq_" not in out and out.endswith("番号なしの式")

    def test_other_ids_become_fact_text(self):
        out = mask_internal_ids_readable("claim_span_002_sub02 → claim_s")
        assert "claim_" not in out
        assert UNIDENTIFIED_ELEMENT_TEXT in out

    def test_unnumbered_equation_gets_lhs(self):
        assert equation_display_name({"latex": "c_s^2 = \\partial P/\\partial\\rho"}) == "番号なしの式（左辺 c_s^2）"
        assert equation_display_name({"label": "(12)", "latex": "a=b"}) == "式 (12)"
        assert equation_display_name({}) == "番号なしの式"

    def test_mask_uses_records(self):
        records = {"eq_blk_1": {"latex": "M = 4\\pi r^2"}}
        assert mask_internal_ids_readable("Define eq_blk_1", records) == "Define 番号なしの式（左辺 M）"


class TestGraphDialogueGrounding:
    def _graph(self):
        nodes = [
            {"component_id": "theory_op_0001", "label": "Theory basis", "graph_layer": "main",
             "description": "same claim", "display_order": 1},
            {"component_id": "theory_op_0002", "label": "Consistency relation", "graph_layer": "main",
             "description": "same claim", "display_order": 2},
        ]
        edges = [
            {"source_component_id": "theory_op_0001", "target_component_id": "theory_op_0002", "edge_type": "derives"},
            {"source_component_id": "theory_op_0002", "target_component_id": "theory_op_0001", "edge_type": "derives"},
        ]
        return {"nodes": nodes, "edges": edges}

    def test_shared_claim_and_bidirectional_edge_are_stated_as_facts(self):
        text = graph_grounding_to_text(build_graph_grounding(self._graph()))
        assert "同じ主張文を根拠に持ちます" in text
        assert "向きが確定していない関係" in text
        assert text.count("↔") == 1
        assert "theory_op_" not in text


class TestPaperLayerHeadings:
    def test_table_header_is_not_a_heading(self):
        assert not is_heading_like_title("Case\nCut\nNo. of\nEvents")
        assert is_heading_like_title("2. METHODS")

    def test_orphan_parent_is_reattached(self):
        sections = [
            {"section_id": "s1", "level": 1, "parent_section_id": None, "folded_section_ids": ["sec_9"]},
            {"section_id": "s2", "level": 2, "parent_section_id": "sec_9"},
            {"section_id": "s3", "level": 2, "parent_section_id": "missing"},
        ]
        _reattach_orphan_parents(sections, [])
        assert sections[1]["parent_section_id"] == "s1"
        assert sections[2]["parent_section_id"] == "s1"


class TestRelatedOneLinePerOuter:
    def test_no_partners_means_single_fact_and_no_rows(self):
        own = [
            {"id": f"m{i}", "agent_module_key": "", "rule_version": "m2", "level": "outer",
             "structure_fingerprint": "fp", "identity_eligible": True}
            for i in range(17)
        ]
        body = tm_related.build_related_payload(
            "doc", own_rows=own, matches=[], decisions={}, rule_version="m2",
            can_view=lambda d: True, titles={}, candidate_key_for=lambda fp: fp,
        )
        assert body["modules"] == []
        assert body["facts"] == [tm_related.FACT_RELATED_NONE]


class TestSeminarBriefReadable:
    def _labels(self):
        from core.doubt.seminar_brief import _ReadableLabels

        labels = _ReadableLabels.__new__(_ReadableLabels)
        labels.equation_records = {"eq_7": {"label": "(7)"}}
        labels.node_labels = {"eq_op_0062": "Define eq_7", "eq_op_0099": "eq_op_0099"}
        return labels

    def test_node_id_statement_uses_node_label(self):
        assert self._labels().text("eq_op_0062") == "Define 式 (7)"

    def test_unresolvable_id_is_fact_text(self):
        out = self._labels().text("eq_op_0099")
        assert "eq_" not in out

    def test_fact_line_ids_are_masked(self):
        out = self._labels().text("『Apply constraint eq_eqcand_inline_blk_3df32664_、claim_span_002_sub02 → claim_s』")
        assert "eq_eqcand" not in out and "claim_span" not in out


def test_get_material_accepts_document_uuid():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "api/routes/admin.py").read_text(encoding="utf-8")
    start = text.index("def get_material(")
    src = text[start:text.index("\ndef ", start + 10)]
    assert "d.id::text = :material_id" in src
    assert "Material not found" not in src
