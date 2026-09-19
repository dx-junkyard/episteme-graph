"""学ぶ単位の章（``section_ids``）の決定論伝播と、候補の章順（P2 / 実データ由来の是正）。

実測（2026-09-19・2609.* 10 本）では ``section_ids`` が入っていたのは ``section_block``
だけで、thesis_support / parent_component / dsl_node は全て空だった。素材側に章が
書かれていないわけではなく、``EvidenceRecord.source`` が ``block_id`` と ``section_id``
を両方持ち、``PaperSkeletonResult.logical_blocks`` も両方を並べている — 読んでいなかった
だけである。章が付くと、コースビルダーの候補表を論文の並び（章順）で出せる。

``stable_key`` の材料は変えない（章が付いても同じ単位を指し続ける）。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core import course_units  # noqa: E402
from core.knowledge_objects import learning_units as lu  # noqa: E402

DOC = "11111111-1111-1111-1111-111111111111"
DOC_B = "22222222-2222-2222-2222-222222222222"


def _evidence():
    return types.SimpleNamespace(records=[
        types.SimpleNamespace(
            evidence_id="ev_1",
            source=types.SimpleNamespace(block_id="b1", section_id="sec_intro"),
        ),
        types.SimpleNamespace(
            evidence_id="ev_2",
            source=types.SimpleNamespace(block_id="b2", section_id="sec_method"),
        ),
    ])


def _skeleton():
    return types.SimpleNamespace(logical_blocks=[
        types.SimpleNamespace(
            block_id="lb_1", block_type="assumptions", label="Assumption",
            section_ids=["sec_intro"], evidence_block_ids=["b1"], summary="…",
        ),
    ])


def _thesis():
    return types.SimpleNamespace(
        central_thesis={
            "text": "The corrected estimator removes the leading bias.",
            "claim_ids": ["b2:span_001"],
            "equation_ids": [],
            "evidence_block_ids": ["b2"],
        },
        support_structure={
            # 出典 block を持たない entry — claim の出典 block から章を引く。
            "derivation": [
                {"text": "Linearising gives the relation.",
                 "claim_ids": ["b1:span_002"], "equation_ids": []},
            ],
        },
    )


def _dsl():
    return types.SimpleNamespace(nodes=[
        types.SimpleNamespace(
            node_id="dsl_1", node_type="Observable", node_value="power spectrum",
            source_refs={"claim_ids": ["b2:span_001"], "equation_ids": []},
        ),
    ])


def _component_result():
    return types.SimpleNamespace(
        components=[types.SimpleNamespace(component_id="cmp_a", label="Bias correction", summary="")],
        refinement_report={"split_actions": []},
        component_refinement={"component_refinement_records": [
            {"original_component_id": "cmp_a", "refinement_status": "unchanged",
             "split_into": ["cmp_a"],
             "provenance": {"source_claim_ids": [], "source_equation_ids": [],
                            "source_evidence_ids": ["ev_2"]}},
        ]},
    )


CLAIM_ID_MAP = {"b1:span_002": "claim-uuid-2", "b2:span_001": "claim-uuid-1"}


def _build(**overrides):
    params = {
        "skeleton": _skeleton(),
        "thesis": _thesis(),
        "component_result": _component_result(),
        "dsl": _dsl(),
        "figures": None,
        "claim_id_map": dict(CLAIM_ID_MAP),
        "component_id_map": {"cmp_a": "cmp-uuid-a"},
        "evidence_registry": _evidence(),
    }
    params.update(overrides)
    return lu.build_learning_unit_items(DOC, **params)


def _by_kind(items, kind):
    return [i for i in items if i["values"]["unit_kind"] == kind]


class TestBlockSectionResolution:
    def test_evidence_records_supply_block_to_section(self):
        index = lu._block_sections(None, _evidence())
        assert index == {"b1": "sec_intro", "b2": "sec_method"}

    def test_skeleton_blocks_supply_it_too(self):
        index = lu._block_sections(_skeleton(), None)
        assert index == {"b1": "sec_intro"}

    def test_a_logical_block_spanning_several_sections_is_ambiguous(self):
        skeleton = types.SimpleNamespace(logical_blocks=[
            types.SimpleNamespace(block_id="lb", block_type="t", label="l",
                                  section_ids=["sec_a", "sec_b"],
                                  evidence_block_ids=["b9"], summary=""),
        ])
        assert lu._block_sections(skeleton, None) == {}

    def test_no_material_gives_an_empty_index(self):
        assert lu._block_sections(None, None) == {}


class TestSectionPropagation:
    def test_thesis_support_uses_its_own_evidence_blocks(self):
        [central, support] = _by_kind(_build(), lu.KIND_THESIS_SUPPORT)
        assert central["values"]["section_ids"] == ["sec_method"]
        # 出典 block が無い entry は claim の出典 block から引く。
        assert support["values"]["section_ids"] == ["sec_intro"]

    def test_parent_component_uses_its_source_blocks(self):
        [unit] = _by_kind(_build(), lu.KIND_PARENT_COMPONENT)
        assert unit["values"]["section_ids"] == ["sec_method"]

    def test_dsl_node_uses_its_claims(self):
        [unit] = _by_kind(_build(), lu.KIND_DSL_NODE)
        assert unit["values"]["section_ids"] == ["sec_method"]

    def test_nothing_is_invented_without_a_resolution(self):
        items = _build(evidence_registry=None, skeleton=None)
        for item in items:
            assert item["values"]["section_ids"] == []

    def test_stable_keys_do_not_change_when_sections_are_added(self, monkeypatch):
        """章は stable_key の材料ではない（章が付いても同じ単位を指し続ける）。"""
        with_sections = {i["agent_id"]: i["stable_key"] for i in _build()}
        assert any(i["values"]["section_ids"] for i in _build())
        monkeypatch.setattr(lu, "_block_sections", lambda skeleton, evidence: {})
        without = _build()
        assert all(not i["values"]["section_ids"] for i in without
                   if i["values"]["unit_kind"] != lu.KIND_SECTION_BLOCK)
        for item in without:
            assert with_sections[item["agent_id"]] == item["stable_key"]


# ---------------------------------------------------------------------------
# 候補表の章順（core/course_units.py）
# ---------------------------------------------------------------------------


class _Session:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _stmt, _params=None):
        return self

    def fetchall(self):
        return self._rows


def _row(unit_id, kind, label, order_index=0, section_ids=None, document_id=DOC):
    return (
        unit_id, document_id, f"k1:{unit_id}", kind, label, "", order_index,
        list(section_ids or []),
    )


class TestCandidateOrderFollowsSections:
    def test_units_of_the_same_section_stay_together(self):
        rows = [
            _row("sec-2", "section_block", "2 Method", order_index=1, section_ids=["sec_b"]),
            _row("sec-1", "section_block", "1 Intro", order_index=0, section_ids=["sec_a"]),
            _row("th-b", "thesis_support", "method claim", section_ids=["sec_b"]),
            _row("th-a", "thesis_support", "intro claim", section_ids=["sec_a"]),
        ]
        candidates = course_units.list_unit_candidates(_Session(rows), [DOC])
        assert [c.unit_id for c in candidates] == ["sec-1", "th-a", "sec-2", "th-b"]

    def test_units_without_a_section_go_last(self):
        rows = [
            _row("th-none", "thesis_support", "no section"),
            _row("sec-1", "section_block", "1 Intro", section_ids=["sec_a"]),
            _row("th-a", "thesis_support", "intro claim", section_ids=["sec_a"]),
        ]
        candidates = course_units.list_unit_candidates(_Session(rows), [DOC])
        assert [c.unit_id for c in candidates] == ["sec-1", "th-a", "th-none"]

    def test_sections_of_different_documents_do_not_mix(self):
        rows = [
            _row("b-sec", "section_block", "B 1", section_ids=["sec_a"], document_id=DOC_B),
            _row("a-sec", "section_block", "A 1", section_ids=["sec_a"], document_id=DOC),
            _row("b-th", "thesis_support", "B claim", section_ids=["sec_a"], document_id=DOC_B),
            _row("a-th", "thesis_support", "A claim", section_ids=["sec_a"], document_id=DOC),
        ]
        candidates = course_units.list_unit_candidates(_Session(rows), [DOC, DOC_B])
        assert [c.unit_id for c in candidates] == ["a-sec", "a-th", "b-sec", "b-th"]

    def test_order_is_independent_of_the_row_order(self):
        rows = [
            _row("sec-1", "section_block", "1 Intro", order_index=0, section_ids=["sec_a"]),
            _row("sec-2", "section_block", "2 Method", order_index=1, section_ids=["sec_b"]),
            _row("th-b", "thesis_support", "method claim", section_ids=["sec_b"]),
        ]
        forward = course_units.list_unit_candidates(_Session(list(rows)), [DOC])
        backward = course_units.list_unit_candidates(_Session(list(reversed(rows))), [DOC])
        assert [c.unit_id for c in forward] == [c.unit_id for c in backward]

    def test_without_section_blocks_the_kind_order_is_unchanged(self):
        rows = [
            _row("th-1", "thesis_support", "claim"),
            _row("fig-1", "figure", "figure"),
        ]
        candidates = course_units.list_unit_candidates(_Session(rows), [DOC])
        assert [c.unit_kind for c in candidates] == ["thesis_support", "figure"]
