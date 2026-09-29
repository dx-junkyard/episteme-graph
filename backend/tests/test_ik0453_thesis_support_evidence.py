"""中心命題・支持構造の単位（thesis_support）の根拠と、短い inline 式の本文化のテスト。

IK-0453: ``topic.units`` が thesis_support の単位（主結果・確かめられていない点）だけの
トピックに根拠が1つも付かなかった。thesis_support の単位は component を束ねず、
``linked_claim_ids`` は DB UUID で artifact の claim 名前空間と突合できないため。
単位に対応する thesis ノード（stable_key / 本文で引く）の claim 参照
（``claim:{block}:{span}``）と出典 block から、その論文の中でだけ主張・式・図・出典
チャンクを引くことを固定する。parent_component の単位が従来どおり束ねることも固定する。

IK-0454: latex の無い inline 式候補（``J=3–2`` / ``w0``）の ``[[FORMULA_N]]`` を事実文で
潰さず短い原文を本文に書く・付録に抽出段の式 ID を出さない。
"""

from __future__ import annotations

import core.course_content_builder as ccb
from core import label_vocab
from core.course_data import UNIT_SOURCE_TEACHER_SELECTED
from core.knowledge_objects import learning_units as lu

DOC = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"

UNTESTED_TEXT = "The magnetic field geometry along the line of sight is not tested."
RESULT_TEXT = "The filament is magnetically supercritical."


def _thesis():
    return {
        "central_thesis": {
            "text": RESULT_TEXT,
            "claim_ids": ["claim:b_40:span_001"],
            "equation_ids": ["eq_9"],
            "evidence_block_ids": [],
        },
        "support_structure": {
            "uncertainty_sources": [
                {"text": UNTESTED_TEXT, "claim_ids": ["claim:b_50:span_002"], "equation_ids": []},
            ],
            "assumptions": [],
        },
    }


def _artifacts():
    return {
        "thesis_reconstruction": _thesis(),
        "claim_object_builder": {
            "claims": [
                {"claim_id": "c_setup", "text": "Setup", "source_evidence_ids": ["ev_1"],
                 "source_span_ids": ["span_001"], "section_id": "sec_2"},
                {"claim_id": "c_result", "text": "The mass-to-flux ratio is supercritical.",
                 "source_evidence_ids": ["ev_3"], "source_span_ids": ["span_001"],
                 "section_id": "sec_4", "equation_ids": ["eq_9"]},
                {"claim_id": "c_untested_parent", "text": "Parent",
                 "source_evidence_ids": ["ev_5"], "source_span_ids": ["span_002"],
                 "section_id": "sec_5", "subclaim_ids": ["c_untested_sub"]},
                {"claim_id": "c_untested_sub", "text": "Line-of-sight geometry is untested.",
                 "source_evidence_ids": ["ev_5"], "source_span_ids": ["span_002"],
                 "section_id": "sec_5", "parent_claim_id": "c_untested_parent"},
                {"claim_id": "c_other_in_sec5", "text": "Unrelated sentence in the same section.",
                 "source_evidence_ids": ["ev_6"], "source_span_ids": ["span_009"], "section_id": "sec_5"},
            ]
        },
        "evidence_registry": {
            "records": [
                {"evidence_id": "ev_1", "evidence_text": "…", "source": {"block_id": "b_10"}},
                {"evidence_id": "ev_3", "evidence_text": "…", "source": {"block_id": "b_40"}},
                {"evidence_id": "ev_5", "evidence_text": "…", "source": {"block_id": "b_50"}},
                {"evidence_id": "ev_6", "evidence_text": "…", "source": {"block_id": "b_51"}},
            ]
        },
        "equation_semantics": {
            "equations": [
                {"equation_id": "eq_9", "latex": r"\lambda = M/\Phi",
                 "source_location": {"block_id": "b_41", "section_id": "sec_4"}},
            ]
        },
        "figure_table_semantics": {
            "figures": [
                {"figure_id": "fig_2", "caption": "Mass-to-flux map", "linked_claim_ids": ["c_result"]},
            ]
        },
        "component_assembly": {"components": [
            {"component_id": "comp_child", "label": "Transform representation: flux",
             "summary": "child", "linked_claim_ids": ["c_setup"]},
        ]},
    }


def _bundle(documents=(DOC,)):
    return ccb._collect_structured_content({doc: _artifacts() for doc in documents})


def _node(ref):
    return next(n for n in lu.thesis_support_nodes(_thesis()) if n["thesis_ref"] == ref)


def _thesis_unit(ref="central_thesis", *, document_id=DOC, stable_key=None, summary=None):
    node = _node(ref)
    key = stable_key or lu.thesis_support_stable_key(document_id or DOC, node)
    return {
        "unit_id": "uuid-" + key,
        "document_id": document_id,
        "stable_key": key,
        "unit_kind": "thesis_support",
        "label": "主結果",
        "summary": node["text"] if summary is None else summary,
        "linked_claim_ids": ["550e8400-e29b-41d4-a716-446655440000"],  # DB UUID
        "linked_equation_ids": list(node["equation_ids"]),
        "linked_component_ids": [],
        "linked_figure_ids": [],
        "agent_payload": {"thesis_kind": node["kind"]},
        "order_index": 0,
        "section_ids": [],
        "source_block_ids": list(node["evidence_block_ids"]),
    }


def _topic(units: dict, kind="thesis_support"):
    return {
        "id": "t3",
        "title": "主結果",
        "units": [
            {"kind": kind, "stable_key": k, "unit_id": row["unit_id"], "label": "主結果",
             "source": UNIT_SOURCE_TEACHER_SELECTED}
            for k, row in units.items()
        ],
    }


def _chunks():
    return {
        "m1": [
            {"id": "chunk-a", "document_id": DOC, "text": "Sec 2", "block_ids": ["b_10"], "formulas": []},
            {"id": "chunk-b", "document_id": DOC, "text": "Sec 4", "block_ids": ["b_40", "b_41"], "formulas": []},
            {"id": "chunk-c", "document_id": DOC, "text": "Sec 5", "block_ids": ["b_50", "b_51"], "formulas": []},
        ]
    }


def _figures_index():
    item = {"figure_id": "f-uuid-2", "document_id": DOC, "figure_key": "fig_2", "caption": "Mass-to-flux map"}
    return {"f-uuid-2": item, f"{DOC}::fig_2": item}


def _enrich(units, *, bundle=None, kind="thesis_support"):
    return ccb._enrich_topics(
        [_topic(units, kind)], bundle or _bundle(), _chunks(), _figures_index(), units
    )[0]


# ---------------------------------------------------------------------------
# IK-0453
# ---------------------------------------------------------------------------


class TestThesisSupportUnitsCarryEvidence:
    def test_central_thesis_binds_claims_equations_figures_and_chunks(self):
        row = _thesis_unit()
        topic = _enrich({row["stable_key"]: row})
        # claim:b_40:span_001 は出典 block b_40 × span_001 の c_result だけ（b_10 の c_setup は違う）。
        assert topic["linked_claim_ids"] == ["c_result"]
        assert topic["linked_equation_ids"] == ["eq_9"]
        kinds = {(link["kind"], link["target_id"]) for link in topic["evidence_links"]}
        assert ("claim", "c_result") in kinds
        assert ("equation", "eq_9") in kinds
        assert ("figure", "f-uuid-2") in kinds
        assert topic["material_chunk_ids"] == ["chunk-b"]

    def test_untested_point_binds_the_atomic_child_not_the_section(self):
        row = _thesis_unit("support:uncertainty_sources:0")
        topic = _enrich({row["stable_key"]: row})
        assert topic["linked_claim_ids"] == ["c_untested_sub"]
        assert "c_other_in_sec5" not in topic["linked_claim_ids"]  # 同じ章でも参照していない
        assert topic["material_chunk_ids"] == ["chunk-c"]

    def test_falls_back_to_summary_text_when_stable_key_differs(self):
        row = _thesis_unit(stable_key="k1:collided#2")
        refs = ccb._thesis_support_unit_refs([row], ccb._BundleScope(_bundle()))
        # "#2" を外しても一致しないので本文で引く。
        assert refs[0] == [(DOC, "c_result")]

    def test_unmatched_unit_binds_nothing(self):
        row = _thesis_unit(stable_key="k1:unknown", summary="Something else entirely.")
        assert ccb._thesis_support_unit_refs([row], ccb._BundleScope(_bundle())) == ([], [], [])

    def test_other_document_is_never_used(self):
        row = _thesis_unit()
        refs = ccb._thesis_support_unit_refs([row], ccb._BundleScope(_bundle((DOC, OTHER))))
        for refs_list in refs:
            assert all(doc == DOC for doc, _ in refs_list)

    def test_unit_without_document_binds_nothing(self):
        row = _thesis_unit(document_id="")
        assert ccb._thesis_support_unit_refs([row], ccb._BundleScope(_bundle())) == ([], [], [])

    def test_source_block_ids_bind_claims_on_those_blocks(self):
        row = _thesis_unit(stable_key="k1:none", summary="no match")
        row["source_block_ids"] = ["b_10"]
        claims, _eqs, blocks = ccb._thesis_support_unit_refs([row], ccb._BundleScope(_bundle()))
        assert claims == [(DOC, "c_setup")]
        assert blocks == [(DOC, "b_10")]

    def test_claim_limit(self):
        artifacts = _artifacts()
        artifacts["claim_object_builder"]["claims"] = [
            {"claim_id": f"c_{i}", "text": "x", "source_evidence_ids": ["ev_3"], "source_span_ids": ["span_001"]}
            for i in range(20)
        ]
        bundle = ccb._collect_structured_content({DOC: artifacts})
        claims, _e, _b = ccb._thesis_support_unit_refs([_thesis_unit()], ccb._BundleScope(bundle))
        assert len(claims) == ccb._SECTION_UNIT_CLAIM_LIMIT

    def test_section_block_rule_is_not_applied_to_thesis_units(self):
        """section_block の抽出（章全体）は thesis_support には広げない。"""
        row = _thesis_unit()
        assert ccb._section_unit_refs([row], ccb._BundleScope(_bundle())) == ([], [], [])

    def test_stable_key_helper_matches_learning_unit_derivation(self):
        """コース側の突合キーは learning_units の導出と同じ（衝突接尾辞なし）。"""
        items = lu.build_learning_unit_items(DOC, thesis=_thesis(), claim_id_map={})
        keys = {i["stable_key"] for i in items if i["values"]["unit_kind"] == "thesis_support"}
        expected = {lu.thesis_support_stable_key(DOC, n) for n in lu.thesis_support_nodes(_thesis())}
        assert keys == expected


class TestParentComponentUnitsStillBind:
    def test_parent_component_binds_children(self):
        row = {
            "unit_id": "uuid-p", "document_id": DOC, "stable_key": "k1:parent",
            "unit_kind": "parent_component", "label": "磁束の表現", "summary": "",
            "linked_claim_ids": [], "linked_equation_ids": [], "linked_component_ids": [],
            "linked_figure_ids": [], "agent_payload": {"linked_component_agent_ids": ["comp_child"]},
            "order_index": 0, "section_ids": [], "source_block_ids": [],
        }
        topic = _enrich({"k1:parent": row}, kind="parent_component")
        assert ("claim", "c_setup") in {(l["kind"], l["target_id"]) for l in topic["evidence_links"]}


# ---------------------------------------------------------------------------
# IK-0454
# ---------------------------------------------------------------------------


def _inline_topic(items, text="13CO [[FORMULA_0]] 分子線"):
    return {
        "content_blocks": [{"type": "equations", "items": items}],
        "student_material": {"source_text": text},
    }


class TestShortInlineFormulasAreWrittenIntoText:
    def test_raw_text_only_formula_is_written_as_text(self):
        topic = _inline_topic([{"equation_id": "eq_eqcand_inline_blk_1_2_ab", "raw_text": "J=3–2"}])
        assert ccb._sanitize_topic_formula_placeholders(topic) is False
        text = topic["student_material"]["source_text"]
        assert text == "13CO J=3–2 分子線"
        assert label_vocab.UNRESOLVED_FORMULA_PLACEHOLDER_TEXT not in text
        assert "grounding_note" not in topic

    def test_tex_like_text_is_wrapped_in_dollars(self):
        topic = _inline_topic([{"equation_id": "eq_eqcand_inline_x", "plain_text": r"\alpha_K"}], "値 [[FORMULA_0]]")
        ccb._sanitize_topic_formula_placeholders(topic)
        assert topic["student_material"]["source_text"] == r"値 $\alpha_K$"

    def test_long_or_multiline_text_keeps_the_fact_placeholder(self):
        for body in ("x" * 41, "a = b\nc = d"):
            topic = _inline_topic([{"equation_id": "eq_1", "raw_text": body}])
            assert ccb._sanitize_topic_formula_placeholders(topic) is True
            assert label_vocab.UNRESOLVED_FORMULA_PLACEHOLDER_TEXT in topic["student_material"]["source_text"]

    def test_plain_text_only_formula_is_not_counted_as_drawable(self):
        """学習画面は [[FORMULA_N]] を latex で描く（無ければ ID を出す）ので、plain_text だけでは残さない。"""
        topic = _inline_topic([{"equation_id": "eq_eqcand_inline_y", "plain_text": "w0"}], "[[FORMULA_0]] と [[FORMULA_1]]")
        ccb._sanitize_topic_formula_placeholders(topic)
        text = topic["student_material"]["source_text"]
        assert text.startswith("w0 と ")
        assert "[[FORMULA_" not in text

    def test_latex_formula_is_kept(self):
        topic = _inline_topic([{"equation_id": "eq_1", "latex": "a=b", "raw_text": "a=b"}])
        ccb._sanitize_topic_formula_placeholders(topic)
        assert "[[FORMULA_0]]" in topic["student_material"]["source_text"]

    def test_internal_id_body_is_not_written(self):
        assert ccb.inline_formula_text({"raw_text": "eq_eqcand_inline_blk_1"}) == ""


class TestAppendixNeverShowsInternalEquationIds:
    def _material(self, items, linked=()):
        topic = {"content_blocks": [{"type": "equations", "items": items}],
                 "linked_equation_ids": list(linked)}
        result = {"student_material": {"source_text": "本文"}}
        ccb._ensure_required_equations_in_material(result, topic)
        return result["student_material"]["source_text"]

    def test_inline_candidate_is_labelled_by_its_text(self):
        text = self._material([{"equation_id": "eq_eqcand_inline_blk_9_3_ff", "raw_text": "w0"}])
        lines = [line for line in text.splitlines() if line.startswith("- ")]
        assert lines and lines[0].startswith("- w0:")
        assert "eqcand" not in "\n".join(lines)

    def test_internal_id_without_body_is_dropped(self):
        text = self._material([], linked=["eq_eqcand_inline_blk_9_3_ff"])
        assert "eqcand" not in text

    def test_printed_equation_ids_are_still_shown(self):
        text = self._material([{"equation_id": "eq_3_14", "latex": "a=b"}])
        assert "- eq_3_14:" in text and "![[equation:eq_3_14]]" in text
