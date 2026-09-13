"""学ぶ単位の導出（learning_units_design.md §5 / LU3・P2-1）。

固定する契約:

①5 種別が設計書 §5 の表どおりに出る（出所 ID・label・teaches・links）
②素材が ``None`` の種別はスキップする（「単位ゼロ」と解釈しない）
③入力（agent の結果）を mutate しない
④決定論（同じ入力 → 同じ並び・同じキー）
⑤``fig_3.3`` / ``eq_op_*`` のような内部 ID を label にしない（LU5 / PL7 と同じ規律）
⑥``prior_work`` / ``meta`` の論理ブロックも落とさない（情報を落とさない）

DB にも LLM にも接続しない。
"""

from __future__ import annotations

import copy
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.knowledge_objects import learning_units as lu  # noqa: E402
from core.knowledge_objects import stable_key as ko_keys  # noqa: E402
from core.schema import LEARNING_UNIT_KINDS  # noqa: E402

DOC = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# fixtures（agent の結果を模した最小構造）
# ---------------------------------------------------------------------------


@dataclass
class _Block:
    block_id: str
    block_type: str
    label: str
    section_ids: list[str] = field(default_factory=list)
    evidence_block_ids: list[str] = field(default_factory=list)
    summary: str = ""
    reason: str = ""
    confidence: float = 0.9


def _skeleton():
    return types.SimpleNamespace(logical_blocks=[
        _Block("lb_1", "assumptions", "Slow-roll assumption",
               section_ids=["sec2"], evidence_block_ids=["b1", "b2"],
               summary="The field rolls slowly."),
        _Block("lb_2", "prior_work", "", section_ids=["sec1"],
               evidence_block_ids=["b0"], summary="Earlier treatments."),
    ])


def _thesis():
    return types.SimpleNamespace(
        central_thesis={
            "text": "The corrected estimator removes the leading bias.",
            "claim_ids": ["span_001"],
            "equation_ids": ["eq_7"],
            "evidence_block_ids": ["b1"],
            "reason": "should not be persisted",
        },
        support_structure={
            "derivation": [
                {"text": "Linearising gives the transfer relation.",
                 "claim_ids": ["span_002"], "equation_ids": [], "reason": "r"},
            ],
            "result": [],
        },
    )


def _component_result():
    child_a = types.SimpleNamespace(
        component_id="cmp_a", label="Linearize: Bias correction", summary="",
        concepts=["transfer function"],
    )
    child_b = types.SimpleNamespace(
        component_id="cmp_b", label="Solve: Bias correction", summary="",
        concepts=["bias"],
    )
    unchanged = types.SimpleNamespace(
        component_id="cmp_solo", label="Noise model", summary="Stays as it is.",
        concepts=["noise"],
    )
    return types.SimpleNamespace(
        components=[child_a, child_b, unchanged],
        refinement_report={"split_actions": [
            {"parent_component_id": "cmp_parent", "parent_label": "Bias correction",
             "child_component_ids": ["cmp_a", "cmp_b"]},
            {"parent_component_id": "cmp_solo", "parent_label": "Noise model",
             "child_component_ids": ["cmp_solo"]},
        ]},
        component_refinement={"component_refinement_records": [
            {"original_component_id": "cmp_parent", "refinement_status": "split",
             "split_into": ["cmp_a", "cmp_b"],
             "provenance": {"source_claim_ids": ["b1:span_001"],
                            "source_equation_ids": ["eq_7"],
                            "source_evidence_ids": ["ev_9"]}},
            {"original_component_id": "cmp_solo", "refinement_status": "unchanged",
             "split_into": ["cmp_solo"],
             "provenance": {"source_claim_ids": [], "source_equation_ids": [],
                            "source_evidence_ids": []}},
        ]},
    )


def _dsl():
    return types.SimpleNamespace(nodes=[
        types.SimpleNamespace(
            node_id="dsl_1", node_type="Observable", node_value="power spectrum",
            source_kind="claim", source_refs={"claim_ids": ["b1:span_001"],
                                              "equation_ids": ["eq_7"]},
            reason="", confidence=0.7, is_thesis_anchor=True,
        ),
    ])


def _figures():
    return types.SimpleNamespace(figures=[
        types.SimpleNamespace(
            figure_id="fig_3.3", document_id=DOC, figure_type="data_plot",
            source_location=types.SimpleNamespace(
                page=4, caption_block_id="b5", section_id="sec3"),
            caption="Residual bias versus redshift for the corrected estimator.",
            linked_claim_ids=["b1:span_001"],
        ),
    ])


def _evidence():
    return types.SimpleNamespace(records=[
        types.SimpleNamespace(
            evidence_id="ev_9",
            source=types.SimpleNamespace(block_id="b3", section_id="sec2", page=2),
            evidence_text="quote",
        ),
    ])


CLAIM_ID_MAP = {
    "b1:span_001": "claim-uuid-1",
    "span_001": "claim-uuid-1",
    "b2:span_002": "claim-uuid-2",
    "span_002": "claim-uuid-2",
}
COMPONENT_ID_MAP = {"cmp_a": "cmp-uuid-a", "cmp_b": "cmp-uuid-b", "cmp_solo": "cmp-uuid-solo"}


def _build(**overrides):
    params = {
        "skeleton": _skeleton(),
        "thesis": _thesis(),
        "component_result": _component_result(),
        "dsl": _dsl(),
        "figures": _figures(),
        "claim_id_map": dict(CLAIM_ID_MAP),
        "component_id_map": dict(COMPONENT_ID_MAP),
        "evidence_registry": _evidence(),
    }
    params.update(overrides)
    return lu.build_learning_unit_items(DOC, **params)


def _by_kind(items, kind):
    return [i for i in items if i["values"]["unit_kind"] == kind]


def _one(items, agent_id):
    matches = [i for i in items if i["agent_id"] == agent_id]
    assert matches, f"unit {agent_id} not built"
    return matches[0]


# ---------------------------------------------------------------------------
# ① 種別ごとの形
# ---------------------------------------------------------------------------


class TestSectionBlockUnits:
    def test_block_becomes_a_unit_with_its_source_blocks(self):
        unit = _one(_build(), "lb_1")
        values = unit["values"]
        assert values["unit_kind"] == "section_block"
        assert values["label"] == "Slow-roll assumption"
        assert values["summary"] == "The field rolls slowly."
        assert values["section_ids"] == ["sec2"]
        assert values["source_block_ids"] == ["b1", "b2"]

    def test_claims_are_resolved_from_the_evidence_blocks(self):
        values = _one(_build(), "lb_1")["values"]
        assert values["linked_claim_ids"] == ["claim-uuid-1", "claim-uuid-2"]
        assert values["teaches"] == [
            {"kind": "claim", "ref": "claim-uuid-1", "label": ""},
            {"kind": "claim", "ref": "claim-uuid-2", "label": ""},
        ]

    def test_prior_work_and_meta_blocks_are_kept(self):
        """§5: ``prior_work`` / ``meta`` も落とさない（情報を落とさない）。"""
        unit = _one(_build(), "lb_2")
        assert unit["values"]["unit_kind"] == "section_block"

    def test_empty_label_falls_back_without_inventing_text(self):
        values = _one(_build(), "lb_2")["values"]
        assert values["label"] == "Earlier treatments."

    def test_claims_without_a_matching_block_are_not_attached(self):
        items = _build(claim_id_map={"b9:span_9": "claim-uuid-9"})
        assert _one(items, "lb_1")["values"]["linked_claim_ids"] == []


class TestThesisSupportUnits:
    def test_central_thesis_and_support_entries_use_the_persistence_vocabulary(self):
        ids = [i["agent_id"] for i in _by_kind(_build(), "thesis_support")]
        assert ids == ["central_thesis", "support:derivation:0"]

    def test_claims_are_db_uuids_and_equations_stay_agent_ids(self):
        values = _one(_build(), "central_thesis")["values"]
        assert values["linked_claim_ids"] == ["claim-uuid-1"]
        assert values["linked_equation_ids"] == ["eq_7"]
        assert {"kind": "equation", "ref": "eq_7", "label": ""} in values["teaches"]

    def test_reason_is_not_carried_over(self):
        unit = _one(_build(), "central_thesis")
        assert "should not be persisted" not in str(unit)

    def test_empty_thesis_produces_no_unit(self):
        empty = types.SimpleNamespace(
            central_thesis={"text": "", "claim_ids": [], "equation_ids": []},
            support_structure={"derivation": []},
        )
        assert _by_kind(_build(thesis=empty), "thesis_support") == []


class TestParentComponentUnits:
    def test_one_unit_per_llm_original(self):
        ids = [i["agent_id"] for i in _by_kind(_build(), "parent_component")]
        assert ids == ["cmp_parent", "cmp_solo"]

    def test_split_parent_uses_the_parent_label_and_links_the_children(self):
        values = _one(_build(), "cmp_parent")["values"]
        assert values["label"] == "Bias correction"
        assert values["linked_component_ids"] == ["cmp-uuid-a", "cmp-uuid-b"]

    def test_child_agent_ids_are_kept_for_the_course_side(self):
        """§4.1: ``agent_payload.linked_component_agent_ids`` はコース側の突合に必須。"""
        payload = _one(_build(), "cmp_parent")["values"]["agent_payload"]
        assert payload["linked_component_agent_ids"] == ["cmp_a", "cmp_b"]
        assert payload["refinement_status"] == "split"

    def test_unchanged_original_keeps_its_own_label_and_summary(self):
        values = _one(_build(), "cmp_solo")["values"]
        assert values["label"] == "Noise model"
        assert values["summary"] == "Stays as it is."

    def test_split_parent_summary_is_not_invented_from_children(self):
        assert _one(_build(), "cmp_parent")["values"]["summary"] == ""

    def test_source_blocks_come_from_claims_and_evidence(self):
        values = _one(_build(), "cmp_parent")["values"]
        assert values["source_block_ids"] == ["b1", "b3"]

    def test_concepts_are_normalised_through_concept_name_list(self):
        teaches = _one(_build(), "cmp_parent")["values"]["teaches"]
        concepts = [t["ref"] for t in teaches if t["kind"] == "concept"]
        assert concepts == ["transfer function", "bias"]

    def test_unresolved_children_are_dropped_not_guessed(self):
        values = _one(_build(component_id_map={}), "cmp_parent")["values"]
        assert values["linked_component_ids"] == []
        assert values["agent_payload"]["linked_component_agent_ids"] == ["cmp_a", "cmp_b"]


class TestDslNodeUnits:
    def test_node_becomes_a_concept_unit(self):
        values = _one(_build(), "dsl_1")["values"]
        assert values["label"] == "power spectrum"
        assert values["summary"] == "Observable"
        assert values["teaches"] == [
            {"kind": "concept", "ref": "power spectrum", "label": "power spectrum"},
        ]
        assert values["agent_payload"]["is_thesis_anchor"] is True


class TestFigureUnits:
    def test_caption_is_the_label_not_the_internal_figure_id(self):
        values = _one(_build(), "fig_3.3")["values"]
        assert values["label"].startswith("Residual bias versus redshift")
        assert "fig_3.3" not in values["label"]
        assert values["linked_figure_ids"] == ["fig_3.3"]
        assert values["linked_claim_ids"] == ["claim-uuid-1"]

    def test_caption_block_and_section_are_kept(self):
        values = _one(_build(), "fig_3.3")["values"]
        assert values["source_block_ids"] == ["b5"]
        assert values["section_ids"] == ["sec3"]


# ---------------------------------------------------------------------------
# ② 素材が無い種別はスキップ
# ---------------------------------------------------------------------------


class TestMissingMaterialIsSkipped:
    def test_no_material_at_all_yields_no_units(self):
        assert lu.build_learning_unit_items(DOC) == []

    def test_only_the_missing_kind_is_skipped(self):
        items = _build(dsl=None, figures=None)
        kinds = {i["values"]["unit_kind"] for i in items}
        assert kinds == {"section_block", "thesis_support", "parent_component"}

    def test_available_kinds_reports_both_sides(self):
        present, skipped = lu.available_kinds(skeleton=_skeleton(), figures=_figures())
        assert present == ["section_block", "figure"]
        assert skipped == ["thesis_support", "parent_component", "dsl_node"]

    def test_component_result_without_refinement_records_yields_no_unit(self):
        bare = types.SimpleNamespace(components=[], refinement_report={},
                                     component_refinement={})
        assert _by_kind(_build(component_result=bare), "parent_component") == []


# ---------------------------------------------------------------------------
# ③④ 入力非改変・決定論
# ---------------------------------------------------------------------------


class TestPurity:
    def test_inputs_are_not_mutated(self):
        skeleton, thesis = _skeleton(), _thesis()
        component_result, dsl, figures = _component_result(), _dsl(), _figures()
        claim_id_map = dict(CLAIM_ID_MAP)
        before = copy.deepcopy([
            [vars(b) for b in skeleton.logical_blocks],
            thesis.central_thesis, thesis.support_structure,
            component_result.component_refinement, component_result.refinement_report,
            claim_id_map,
        ])
        lu.build_learning_unit_items(
            DOC, skeleton=skeleton, thesis=thesis, component_result=component_result,
            dsl=dsl, figures=figures, claim_id_map=claim_id_map,
            component_id_map=dict(COMPONENT_ID_MAP), evidence_registry=_evidence(),
        )
        after = copy.deepcopy([
            [vars(b) for b in skeleton.logical_blocks],
            thesis.central_thesis, thesis.support_structure,
            component_result.component_refinement, component_result.refinement_report,
            claim_id_map,
        ])
        assert before == after

    def test_repeated_builds_are_identical(self):
        assert _build() == _build()

    def test_units_are_ordered_by_kind_then_document_order(self):
        kinds = [i["values"]["unit_kind"] for i in _build()]
        expected_order = [k for k in LEARNING_UNIT_KINDS if k in set(kinds)]
        seen: list[str] = []
        for kind in kinds:
            if kind not in seen:
                seen.append(kind)
        assert seen == expected_order

    def test_stable_key_matches_the_declared_material(self):
        unit = _one(_build(), "lb_1")
        assert unit["stable_key"] == ko_keys.learning_unit_stable_key(
            DOC, "section_block", "assumptions Slow-roll assumption", ["b1", "b2"]
        )

    def test_order_index_is_not_part_of_the_key(self):
        reordered = types.SimpleNamespace(
            logical_blocks=list(reversed(_skeleton().logical_blocks))
        )
        original = {i["agent_id"]: i["stable_key"] for i in _by_kind(_build(), "section_block")}
        shuffled = {
            i["agent_id"]: i["stable_key"]
            for i in _by_kind(_build(skeleton=reordered), "section_block")
        }
        assert original == shuffled

    def test_same_run_collisions_get_a_deterministic_suffix(self):
        twins = types.SimpleNamespace(logical_blocks=[
            _Block("lb_b", "assumptions", "Same", evidence_block_ids=["b1"]),
            _Block("lb_a", "assumptions", "Same", evidence_block_ids=["b1"]),
        ])
        keys = {
            i["agent_id"]: i["stable_key"]
            for i in _by_kind(_build(skeleton=twins), "section_block")
        }
        assert keys["lb_b"].endswith("#2")
        assert not keys["lb_a"].endswith("#2")


# ---------------------------------------------------------------------------
# ⑤ 内部 ID を label にしない
# ---------------------------------------------------------------------------


class TestInternalIdLabels:
    def test_internal_id_shapes_are_recognised(self):
        for value in ("fig_3.3", "eq_op_12", "theory_op_3", "ev_001", "block_12"):
            assert lu.is_internal_id_label(value), value

    def test_human_labels_are_not_treated_as_internal_ids(self):
        for value in ("Figure 3.3", "Bias correction", "power spectrum", "Table 2"):
            assert not lu.is_internal_id_label(value), value

    def test_no_unit_label_is_an_internal_id(self):
        for unit in _build():
            assert not lu.is_internal_id_label(unit["values"]["label"])
