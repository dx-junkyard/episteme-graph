"""Tests for the ``contextual_explanation`` pipeline stage (Phase 2 §5.1
integration): ``docs/features/hierarchical_context_explanation_design.md``.

Scope of this file:
- ``core/document_pipeline/contextual_explanation_inputs.py`` — building
  ``ElementExplanationInput`` objects per element type, thesis-ref
  resolution, figure no-concept-link skipping, priority ordering + cap,
  and the (fail-soft) L-layer generic-explanation excerpt lookup.
- ``core/document_pipeline/orchestrator.py``'s
  ``_build_contextual_explanation`` / ``_stage_contextual_explanation`` —
  daily cost-gating, candidate-only persistence, non-fatal failure, artifact
  resume, and stage placement (between ``narrative_annotator`` and
  ``course_mapping``).
- The document-deletion cascade (``_purge_document`` /
  ``delete_material``) issuing an explicit ``DELETE FROM
  element_explanations`` (orphan-gap prevention, P4).

The agent itself (``src/episteme_graph/agents/contextual_explanation/``) and
the ``element_explanations`` store are covered by their own test files
(``src/tests/agents/contextual_explanation/`` and
``test_element_explanations*.py``) and are not re-tested here.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.document_pipeline import contextual_explanation_inputs as cei  # noqa: E402
from core.document_pipeline import orchestrator as orch  # noqa: E402


# ---------------------------------------------------------------------------
# Lightweight fakes (types.SimpleNamespace, matching test_document_pipeline.py's
# established convention for stand-in agent-result objects).
# ---------------------------------------------------------------------------


def _component(component_id, label="Label", summary="Summary", **kw):
    defaults = dict(
        component_id=component_id, label=label, summary=summary,
        supports_thesis_node_ids=[], role_in_thesis="", supports_claim_ids=[],
        dependencies=[], linked_equation_ids=[],
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _claim(claim_id, text="Claim text", **kw):
    defaults = dict(
        claim_id=claim_id, text=text, section_title="", subclaim_ids=[], equation_ids=[],
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _equation_semantics(**kw):
    defaults = dict(
        summary="", linked_claim_ids=[], defined_symbols=[], input_equation_ids=[],
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _equation(equation_id, semantics=None, **kw):
    defaults = dict(
        equation_id=equation_id,
        semantics=semantics or _equation_semantics(),
        source_extraction=types.SimpleNamespace(latex=None, plain_text=None, raw_text=""),
        reconstruction=types.SimpleNamespace(status="none", latex=None, plain_text=None),
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _symbol(symbol="x", meaning=""):
    return types.SimpleNamespace(symbol=symbol, meaning=meaning)


def _result(items, attr):
    return types.SimpleNamespace(**{attr: items})


def _fig(figure_id="fig_1", caption="Setup diagram", linked_claim_ids=None, caption_block_id="cb1"):
    """A figure_table_semantics FigureRecord, represented as a plain dict —
    exactly the shape ``ctx.fig_tbl`` has on a resumed run (see
    ``orchestrator._from_agent_dict``'s comment: figure_table_semantics lacks
    a ``from_dict`` so resumed runs hand this stage a raw dict). Using dicts
    here exercises the trickier of the two shapes ``_as_dict`` must accept."""
    return {
        "figure_id": figure_id,
        "caption": caption,
        "linked_claim_ids": list(linked_claim_ids or []),
        "source_location": {"caption_block_id": caption_block_id},
    }


def _derivation_step(step_id="step_1", output_equation_ids=None, **kw):
    defaults = dict(
        step_id=step_id, input_equation_ids=[],
        output_equation_ids=list(output_equation_ids or []),
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _derivation_chain(derivation_id="der_1", steps=None, output_equation_ids=None, **kw):
    defaults = dict(
        derivation_id=derivation_id, steps=list(steps or []),
        input_equation_ids=[], intermediate_equation_ids=[],
        output_equation_ids=list(output_equation_ids or []),
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _no_db_lookups(monkeypatch, *, figure_maps=None, identity_links=None,
                   course_equation_ids=None):
    """Ensure figure/identity-link/course lookups never attempt a real DB connection."""
    by_key, by_caption_block = figure_maps or ({}, {})
    monkeypatch.setattr(cei, "build_figure_db_maps", lambda document_id: (by_key, by_caption_block))
    monkeypatch.setattr(cei, "build_identity_link_map", lambda document_id: (identity_links or {}))
    monkeypatch.setattr(
        cei, "build_course_snapshot_equation_ids",
        lambda material_id: list(course_equation_ids or []),
    )


def _build(monkeypatch, *, components=None, claims=None, equations=None, figures=None,
           apparatus_records=None, thesis=None, max_elements=40, figure_maps=None,
           identity_links=None, derivations=None, material_id=None,
           course_equation_ids=None):
    _no_db_lookups(
        monkeypatch, figure_maps=figure_maps, identity_links=identity_links,
        course_equation_ids=course_equation_ids,
    )
    return cei.build_contextual_explanation_inputs(
        document_id="doc-1",
        cartridge_id=None,
        component_result=_result(components or [], "components"),
        claim_objects=_result(claims or [], "claims"),
        equations=_result(equations or [], "equations"),
        fig_tbl={"figures": figures or []},
        apparatus_result=_result(apparatus_records or [], "apparatus_records"),
        thesis=thesis,
        max_elements=max_elements,
        material_id=material_id,
        derivations=_result(derivations, "chains") if derivations is not None else None,
    )


# ---------------------------------------------------------------------------
# component element construction
# ---------------------------------------------------------------------------


class TestComponentElement:
    def test_local_text_is_label_and_summary(self, monkeypatch):
        comp = _component("comp_1", label="Ohm's law", summary="V=IR")
        elements, meta = _build(monkeypatch, components=[comp])
        assert len(elements) == 1
        el = elements[0]
        assert el.element_type == "theory_component"
        assert el.element_id == "comp_1"
        assert "Ohm's law" in el.local_text and "V=IR" in el.local_text
        assert meta["counts_by_kind"]["component"] == 1

    def test_upper_context_resolves_thesis_ref_and_supports_claim(self, monkeypatch):
        claim = _claim("claim_x", text="X is true")
        comp = _component(
            "comp_1",
            supports_thesis_node_ids=["central_thesis"],
            supports_claim_ids=["claim_x"],
        )
        thesis = types.SimpleNamespace(
            central_thesis={"text": "The central result", "claim_ids": []},
            support_structure={},
        )
        elements, _ = _build(monkeypatch, components=[comp], claims=[claim], thesis=thesis)
        upper_texts = [u["text"] for u in elements[0].upper_context]
        assert "The central result" in upper_texts
        assert "X is true" in upper_texts

    def test_lower_context_resolves_dependency_and_equation(self, monkeypatch):
        member = _component("comp_member", label="Member", summary="does X")
        eq = _equation("eq_1", semantics=_equation_semantics(summary="derives Y"))
        comp = _component(
            "comp_1",
            dependencies=[{"dependency_type": "depends_on", "component_refs": ["comp_member"]}],
            linked_equation_ids=["eq_1"],
        )
        elements, _ = _build(monkeypatch, components=[comp, member], equations=[eq])
        comp_element = next(e for e in elements if e.element_id == "comp_1")
        lower_texts = [entry["text"] for entry in comp_element.lower_context]
        assert any("Member" in t for t in lower_texts)
        assert any("derives Y" in t for t in lower_texts)


# ---------------------------------------------------------------------------
# claim element construction
# ---------------------------------------------------------------------------


class TestClaimElement:
    def test_thesis_linked_claims_are_ordered_first(self, monkeypatch):
        thesis = types.SimpleNamespace(
            central_thesis={"text": "Central result", "claim_ids": ["claim_thesis"]},
            support_structure={},
        )
        claim_thesis = _claim("claim_thesis", text="Thesis claim")
        claim_other = _claim("claim_other", text="Other claim")
        # Listed in "wrong" input order to prove the re-sort actually happens.
        elements, _ = _build(monkeypatch, claims=[claim_other, claim_thesis], thesis=thesis)
        claim_elements = [e for e in elements if e.element_type == "theory_claim"]
        assert [e.element_id for e in claim_elements] == ["claim_thesis", "claim_other"]
        assert any(u["text"] == "Central result" for u in claim_elements[0].upper_context)
        assert claim_elements[1].upper_context == []

    def test_section_title_and_subclaims_and_equations(self, monkeypatch):
        sub = _claim("claim_sub", text="Sub proposition")
        eq = _equation("eq_1", semantics=_equation_semantics(summary="derives Z"))
        claim = _claim(
            "claim_x", text="Main proposition", section_title="Methods",
            subclaim_ids=["claim_sub"], equation_ids=["eq_1"],
        )
        elements, _ = _build(monkeypatch, claims=[claim, sub], equations=[eq])
        main = next(e for e in elements if e.element_id == "claim_x")
        assert {"relation": "located_in", "text": "Methods", "kind": "section"} in main.upper_context
        lower_texts = [entry["text"] for entry in main.lower_context]
        assert any("Sub proposition" in t for t in lower_texts)
        assert any("derives Z" in t for t in lower_texts)


# ---------------------------------------------------------------------------
# equation element construction
# ---------------------------------------------------------------------------


class TestEquationElement:
    def test_upper_from_linked_claim_and_lower_from_symbols(self, monkeypatch):
        claim = _claim("claim_x", text="claim text")
        eq = _equation(
            "eq_1",
            semantics=_equation_semantics(
                summary="defines force",
                linked_claim_ids=["claim_x"],
                defined_symbols=[_symbol("F", "force")],
            ),
        )
        elements, _ = _build(monkeypatch, claims=[claim], equations=[eq])
        eq_el = next(e for e in elements if e.element_type == "equation")
        assert "defines force" in eq_el.local_text
        assert any(u["text"] == "claim text" for u in eq_el.upper_context)
        assert any("force" in entry["text"] for entry in eq_el.lower_context)

    def test_symbol_without_meaning_is_not_surfaced(self, monkeypatch):
        eq = _equation(
            "eq_1",
            semantics=_equation_semantics(defined_symbols=[_symbol("x", "")]),
        )
        elements, _ = _build(monkeypatch, equations=[eq])
        assert elements[0].lower_context == []


# ---------------------------------------------------------------------------
# figure element construction (no_concept_link skip, P4)
# ---------------------------------------------------------------------------


class TestFigureElement:
    def test_figure_without_linked_claims_is_skipped(self, monkeypatch):
        fig = _fig(linked_claim_ids=[])
        elements, meta = _build(monkeypatch, figures=[fig])
        assert elements == []
        assert meta["skipped"] == [
            {"element_type": "figure", "element_id": "fig_1", "reason": "no_concept_link"},
        ]
        assert meta["counts_by_kind"]["figure"] == 0

    def test_figure_with_linked_claim_resolves_db_id_and_context(self, monkeypatch):
        claim = _claim("claim_x", text="claim text")
        row = {
            "id": "figrow-uuid-1",
            "figure_key": "fig_1",
            "caption_block_id": "cb1",
            "inner_labels": [{"text": "Mirror M1"}],
        }
        apparatus_record = types.SimpleNamespace(
            figure_id="figrow-uuid-1",
            parts=[types.SimpleNamespace(name="Laser", role="emits light")],
            iterative_analysis=None,
        )
        fig = _fig(linked_claim_ids=["claim_x"])
        elements, meta = _build(
            monkeypatch,
            claims=[claim],
            figures=[fig],
            apparatus_records=[apparatus_record],
            figure_maps=({"fig_1": row}, {"cb1": row}),
        )
        # elements includes both the figure and the claim (every claim is also
        # its own top-level element) — isolate the figure.
        assert len(elements) == 2
        el = next(e for e in elements if e.element_type == "figure")
        # The DB row id is used, not the caption-derived figure_id.
        assert el.element_id == "figrow-uuid-1"
        assert el.local_text == "Setup diagram"
        assert any(u["text"] == "claim text" for u in el.upper_context)
        lower_texts = [entry["text"] for entry in el.lower_context]
        assert any("Laser" in t for t in lower_texts)
        assert any("Mirror M1" in t for t in lower_texts)
        assert meta["skipped"] == []

    def test_figure_without_matching_db_row_falls_back_to_caption_id(self, monkeypatch):
        claim = _claim("claim_x", text="claim text")
        fig = _fig(figure_id="fig_orphan", linked_claim_ids=["claim_x"], caption_block_id="cb_unmatched")
        elements, _ = _build(monkeypatch, claims=[claim], figures=[fig])
        assert elements[0].element_id == "fig_orphan"

    def test_chapter_labeled_figure_id_resolves_via_normalized_figure_key(self, monkeypatch):
        """バグB回帰: figure_table_semantics の FigureRecord.figure_id は句読点保持
        (``fig_3.3``) だが document_figures.figure_key はアンダースコア正規化済み
        (``fig_3_3``)。caption_block_id はわざと不一致にして、figure_key の
        normalize_figure_join_key 経由の一致だけで DB 行（UUID）に解決されることを
        確認する（``_resolve_figure_db_row``）。"""
        claim = _claim("claim_x", text="claim text")
        row = {
            "id": "figrow-uuid-3-3",
            "figure_key": "fig_3_3",
            "caption_block_id": "cb-real",
            "inner_labels": [],
        }
        fig = _fig(figure_id="fig_3.3", linked_claim_ids=["claim_x"], caption_block_id="cb-nomatch")
        elements, meta = _build(
            monkeypatch,
            claims=[claim],
            figures=[fig],
            figure_maps=({"fig_3_3": row}, {"cb-real": row}),
        )
        el = next(e for e in elements if e.element_type == "figure")
        assert el.element_id == "figrow-uuid-3-3"
        assert meta["skipped"] == []


class TestBuildFigureDbMaps:
    """``build_figure_db_maps`` 自体が figure_key を正規化して索引すること
    （``_resolve_figure_db_row`` 側だけでなく登録側も両辺で正規化規則を揃える必要がある）。"""

    def test_by_key_index_is_normalized(self, monkeypatch):
        rows = [
            {"id": "figrow-uuid-3-3", "figure_key": "fig_3_3", "caption_block_id": "cb-real"},
        ]
        monkeypatch.setattr(
            "core.document_pipeline.figure_images.load_document_figures",
            lambda document_id: rows,
        )
        by_key, by_caption_block = cei.build_figure_db_maps("doc-1")
        assert by_key["fig_3_3"]["id"] == "figrow-uuid-3-3"
        assert by_caption_block["cb-real"]["id"] == "figrow-uuid-3-3"


# ---------------------------------------------------------------------------
# priority ordering + cap (design §5.1 "選抜と上限")
# ---------------------------------------------------------------------------


class TestPriorityAndTruncation:
    def test_combined_order_is_component_figure_claim_equation(self, monkeypatch):
        comp = _component("comp_1")
        claim = _claim("claim_1")
        eq = _equation("eq_1")
        row = {"id": "fig-db-1", "figure_key": "fig_1", "caption_block_id": "cb1", "inner_labels": []}
        fig = _fig(linked_claim_ids=["claim_1"])
        elements, meta = _build(
            monkeypatch,
            components=[comp], claims=[claim], equations=[eq], figures=[fig],
            figure_maps=({"fig_1": row}, {"cb1": row}),
        )
        assert [e.element_type for e in elements] == [
            "theory_component", "figure", "theory_claim", "equation",
        ]
        assert meta["truncated"] is False

    def test_max_elements_truncates_and_reports_count(self, monkeypatch):
        comps = [_component(f"comp_{i}") for i in range(5)]
        elements, meta = _build(monkeypatch, components=comps, max_elements=3)
        assert len(elements) == 3
        assert meta["truncated"] is True
        assert meta["truncated_count"] == 2
        assert meta["considered"] == 5
        assert meta["selected"] == 3

    def test_zero_max_elements_selects_none(self, monkeypatch):
        comps = [_component("comp_1")]
        elements, meta = _build(monkeypatch, components=comps, max_elements=0)
        assert elements == []
        assert meta["truncated"] is True
        assert meta["truncated_count"] == 1


# ---------------------------------------------------------------------------
# required equations (指示書 §5.2 / element_context_presentation_redesign.md
# §8 Phase 3): equations the teaching material presents are reserved outside
# ``max_elements`` instead of falling off the end of a flat concatenation.
# ---------------------------------------------------------------------------


class TestRequiredEquations:
    def test_required_equation_survives_a_cap_filled_by_other_kinds(self, monkeypatch):
        """component / figure / claim だけで上限が埋まっても required は選ばれる。"""
        comps = [_component(f"comp_{i}") for i in range(3)]
        # comp_0 references eq_req -> the course builder's material rule shows it.
        comps[0].linked_equation_ids = ["eq_req"]
        eq_required = _equation("eq_req", semantics=_equation_semantics(summary="required eq"))
        eq_optional = _equation("eq_opt", semantics=_equation_semantics(summary="optional eq"))
        elements, meta = _build(
            monkeypatch, components=comps, equations=[eq_required, eq_optional], max_elements=3,
        )
        ids = [e.element_id for e in elements]
        # The cap still admits exactly 3 non-required elements (the components),
        # and the required equation rides on top of it.
        assert "eq_req" in ids
        assert "eq_opt" not in ids
        assert len(elements) == 4
        assert elements[0].element_id == "eq_req"  # required first
        assert meta["required_equations_considered"] == 1
        assert meta["required_equations_selected"] == 1
        assert meta["required_equations_unresolved"] == 0
        assert meta["truncated"] is True
        assert meta["truncated_count"] == 1  # the optional equation

    def test_required_equation_is_not_duplicated(self, monkeypatch):
        """複数ソースが同じ式を指しても入力は1回だけ（optional 枠にも残らない）。"""
        comp = _component("comp_1", linked_equation_ids=["eq_1"])
        comp.evidence_refs = {"equation_ids": ["eq_1"]}
        claim = _claim("claim_1", equation_ids=["eq_1"])
        thesis = types.SimpleNamespace(
            central_thesis={"text": "T", "claim_ids": [], "equation_ids": ["eq_1"]},
            support_structure={"evidence": [{"text": "S", "claim_ids": [], "equation_ids": ["eq_1"]}]},
        )
        eq = _equation("eq_1")
        elements, meta = _build(
            monkeypatch, components=[comp], claims=[claim], equations=[eq], thesis=thesis,
            derivations=[_derivation_chain(output_equation_ids=["eq_1"])],
            course_equation_ids=["eq_1"], material_id="mat-1",
        )
        eq_ids = [e.element_id for e in elements if e.element_type == "equation"]
        assert eq_ids == ["eq_1"]
        assert meta["required_equations_considered"] == 1
        assert meta["required_equations_selected"] == 1
        assert meta["counts_by_kind"]["equation"] == 1

    def test_unresolved_required_id_is_skipped_not_fabricated(self, monkeypatch):
        """artifact に実体の無い required ID は入力化せず、明示 skip として記録する。"""
        comp = _component("comp_1", linked_equation_ids=["eq_ghost", "eq_real"])
        eq = _equation("eq_real")
        elements, meta = _build(monkeypatch, components=[comp], equations=[eq])
        eq_ids = [e.element_id for e in elements if e.element_type == "equation"]
        assert eq_ids == ["eq_real"]
        assert {
            "element_type": "equation", "element_id": "eq_ghost",
            "reason": "equation_not_resolved",
        } in meta["skipped"]
        assert meta["required_equations_considered"] == 2
        assert meta["required_equations_selected"] == 1
        assert meta["required_equations_unresolved"] == 1

    def test_optional_equations_still_obey_max_elements(self, monkeypatch):
        """required でない式は従来どおり上限の中で競合する。"""
        eqs = [_equation(f"eq_{i}") for i in range(5)]
        elements, meta = _build(monkeypatch, equations=eqs, max_elements=2)
        assert [e.element_id for e in elements] == ["eq_0", "eq_1"]
        assert meta["required_equations_considered"] == 0
        assert meta["required_equations_selected"] == 0
        assert meta["truncated"] is True
        assert meta["truncated_count"] == 3

    def test_zero_max_elements_disables_the_reservation_too(self, monkeypatch):
        """``max_elements=0`` は運用の停止スイッチ。required も含めて何も選ばない。"""
        comp = _component("comp_1", linked_equation_ids=["eq_1"])
        elements, meta = _build(
            monkeypatch, components=[comp], equations=[_equation("eq_1")], max_elements=0,
        )
        assert elements == []
        assert meta["selected"] == 0
        assert meta["required_equations_considered"] == 1
        assert meta["required_equations_selected"] == 0
        assert meta["truncated"] is True

    def test_claim_and_thesis_and_derivation_result_equations_are_required(self, monkeypatch):
        claim = _claim("claim_1", equation_ids=["eq_claim"])
        thesis = types.SimpleNamespace(
            central_thesis={"text": "T", "claim_ids": [], "equation_ids": ["eq_thesis"]},
            support_structure={},
        )
        chain = _derivation_chain(
            steps=[
                _derivation_step("step_1", output_equation_ids=["eq_mid"]),
                _derivation_step("step_2", output_equation_ids=["eq_result"]),
            ],
        )
        eqs = [_equation(e) for e in ("eq_claim", "eq_thesis", "eq_mid", "eq_result", "eq_other")]
        elements, meta = _build(
            monkeypatch, claims=[claim], thesis=thesis, equations=eqs, derivations=[chain],
            max_elements=1,
        )
        selected_eq_ids = [e.element_id for e in elements if e.element_type == "equation"]
        # Terminal derivation output only: eq_mid stays optional (and is cut by
        # the cap of 1, which the claim element consumes).
        assert set(selected_eq_ids) == {"eq_claim", "eq_thesis", "eq_result"}
        assert meta["required_equations_considered"] == 3
        assert meta["required_equations_selected"] == 3

    def test_course_snapshot_embed_makes_an_otherwise_unlinked_equation_required(self, monkeypatch):
        """教員が本文に手書きした ``![[equation:id]]`` は他の紐づきが無くても対象。"""
        comps = [_component(f"comp_{i}") for i in range(3)]
        eq = _equation("eq_F2")
        elements, meta = _build(
            monkeypatch, components=comps, equations=[eq], max_elements=3,
            material_id="mat-1",
            # A snapshot embed written as ``eq_eq_F2`` normalizes onto ``eq_F2``;
            # ``eq_from_other_doc`` belongs to a sibling document in the same
            # course and is silently ignored (not reported as unresolved).
            course_equation_ids=["eq_eq_F2", "eq_from_other_doc"],
        )
        assert "eq_F2" in [e.element_id for e in elements]
        assert meta["required_equations_considered"] == 1
        assert meta["required_equations_selected"] == 1
        assert meta["required_equations_unresolved"] == 0
        assert meta["skipped"] == []

    def test_course_snapshot_source_is_not_consulted_without_material_id(self, monkeypatch):
        """material_id が無い呼び出しはコース逆引きを行わない（DB を触らない）。"""
        called: list[str] = []

        def _spy(material_id):
            called.append(material_id)
            return []

        monkeypatch.setattr(cei, "build_figure_db_maps", lambda document_id: ({}, {}))
        monkeypatch.setattr(cei, "build_identity_link_map", lambda document_id: {})
        monkeypatch.setattr(cei, "build_course_snapshot_equation_ids", _spy)
        cei.build_contextual_explanation_inputs(
            document_id="doc-1", cartridge_id=None,
            component_result=_result([], "components"),
            claim_objects=_result([], "claims"),
            equations=_result([], "equations"),
            fig_tbl={"figures": []}, apparatus_result=None, thesis=None,
            max_elements=40,
        )
        assert called == [""]


class TestCourseSnapshotEquationIds:
    """``build_course_snapshot_equation_ids``: bounded, read-only, fail-soft."""

    def _patch_session(self, monkeypatch, rows=None, raises=None):
        captured = {}

        class _FakeResult:
            def fetchall(self):
                return rows or []

        class _FakeSession:
            def execute(self, statement, params=None):
                captured["sql"] = str(statement)
                captured["params"] = params
                if raises:
                    raise raises
                return _FakeResult()

            def close(self):
                captured["closed"] = True

        monkeypatch.setattr("core.postgres.get_session", lambda: _FakeSession())
        return captured

    def _course(self, material_id="mat-1", text="See ![[equation:eq_7]] here."):
        return ({
            "sources": [{"material_id": material_id}],
            "topics": [{"id": "t1", "student_material": {"source_text": text}}],
        },)

    def test_extracts_embedded_equation_ids(self, monkeypatch):
        captured = self._patch_session(monkeypatch, rows=[self._course()])
        assert cei.build_course_snapshot_equation_ids("mat-1") == ["eq_7"]
        assert captured["params"]["material_id"] == "mat-1"
        assert captured["params"]["limit"] == cei.MAX_COURSE_SNAPSHOTS
        assert captured["closed"] is True
        # Read-only: no writes to course state from the analysis pipeline.
        sql = captured["sql"].upper()
        assert "SELECT" in sql
        for verb in ("INSERT", "UPDATE", "DELETE"):
            assert verb not in sql

    def test_legacy_double_eq_prefix_is_normalized_by_the_course_builder_rule(self, monkeypatch):
        self._patch_session(monkeypatch, rows=[self._course(text="![[equation:eq_eq_F2]]")])
        assert cei.build_course_snapshot_equation_ids("mat-1") == ["eq_F2"]

    def test_scans_nested_chapter_topics_and_spoken_script(self, monkeypatch):
        self._patch_session(monkeypatch, rows=[({
            "sources": [{"material_id": "mat-1"}],
            "chapters": [{"topics": [{"id": "c1", "content": "![[equation:eq_a]]"}]}],
            "topics": [{"id": "t1", "spoken_script": "[[equation:eq_b]]"}],
        },)])
        assert sorted(cei.build_course_snapshot_equation_ids("mat-1")) == ["eq_a", "eq_b"]

    def test_blank_material_id_never_queries(self, monkeypatch):
        def _boom():
            raise AssertionError("must not open a session")

        monkeypatch.setattr("core.postgres.get_session", _boom)
        assert cei.build_course_snapshot_equation_ids("") == []

    def test_db_failure_is_fail_soft(self, monkeypatch):
        self._patch_session(monkeypatch, raises=RuntimeError("no db in this test"))
        assert cei.build_course_snapshot_equation_ids("mat-1") == []


# ---------------------------------------------------------------------------
# L-layer generic-explanation excerpt (E3: only ever supplied when a
# CONFIRMED identity link exists; fail-soft otherwise).
# ---------------------------------------------------------------------------


class TestLibraryExcerpt:
    def test_confirmed_link_supplies_excerpt(self, monkeypatch):
        monkeypatch.setattr(cei, "build_figure_db_maps", lambda document_id: ({}, {}))
        monkeypatch.setattr(
            cei, "build_identity_link_map",
            lambda document_id: {("theory_component", "comp_1"): "entry-1"},
        )
        monkeypatch.setattr(
            "core.library.store.get_entry",
            lambda entry_id: {"latest_version_no": 2, "status": "active"},
        )
        monkeypatch.setattr(
            "core.library.store.get_version",
            lambda entry_id, version_no: {
                "content": {"name": "Michelson interferometer", "summary": "s", "body": {"a": 1}},
            },
        )
        comp = _component("comp_1")
        elements, _ = cei.build_contextual_explanation_inputs(
            document_id="doc-1", cartridge_id=None,
            component_result=_result([comp], "components"),
            claim_objects=_result([], "claims"),
            equations=_result([], "equations"),
            fig_tbl={"figures": []},
            apparatus_result=None,
            thesis=None,
            max_elements=40,
        )
        excerpt = elements[0].library_excerpt
        assert excerpt is not None
        assert excerpt["entry_id"] == "entry-1"
        assert excerpt["version_no"] == 2
        assert excerpt["name"] == "Michelson interferometer"

    def test_retired_entry_yields_no_excerpt(self, monkeypatch):
        monkeypatch.setattr(cei, "build_figure_db_maps", lambda document_id: ({}, {}))
        monkeypatch.setattr(
            cei, "build_identity_link_map",
            lambda document_id: {("theory_component", "comp_1"): "entry-1"},
        )
        monkeypatch.setattr(
            "core.library.store.get_entry",
            lambda entry_id: {"latest_version_no": 2, "status": "retired"},
        )
        comp = _component("comp_1")
        elements, _ = cei.build_contextual_explanation_inputs(
            document_id="doc-1", cartridge_id=None,
            component_result=_result([comp], "components"),
            claim_objects=_result([], "claims"),
            equations=_result([], "equations"),
            fig_tbl={"figures": []},
            apparatus_result=None,
            thesis=None,
            max_elements=40,
        )
        assert elements[0].library_excerpt is None

    def test_no_confirmed_link_means_no_excerpt(self, monkeypatch):
        comp = _component("comp_1")
        elements, _ = _build(monkeypatch, components=[comp])
        assert elements[0].library_excerpt is None

    def test_identity_link_lookup_failure_is_fail_soft(self, monkeypatch):
        def _raise(document_id):
            raise RuntimeError("no db in this test")

        monkeypatch.setattr(cei, "build_figure_db_maps", lambda document_id: ({}, {}))
        # build_identity_link_map itself already wraps DB access in try/except
        # (fail-soft, E3's safe default); simulate that by patching the
        # underlying DB call it would use.
        monkeypatch.setattr(
            "core.deliberation.identity_links.confirmed_links_for_document", _raise,
        )
        comp = _component("comp_1")
        elements, _ = cei.build_contextual_explanation_inputs(
            document_id="doc-1", cartridge_id=None,
            component_result=_result([comp], "components"),
            claim_objects=_result([], "claims"),
            equations=_result([], "equations"),
            fig_tbl={"figures": []},
            apparatus_result=None,
            thesis=None,
            max_elements=40,
        )
        assert elements[0].library_excerpt is None


# ---------------------------------------------------------------------------
# core rule: no FastAPI import in the new input-builder module.
# ---------------------------------------------------------------------------


class TestInputBuilderCoreRules:
    def test_does_not_import_fastapi(self):
        src = (BACKEND / "core" / "document_pipeline" / "contextual_explanation_inputs.py").read_text(
            encoding="utf-8"
        )
        assert "import fastapi" not in src
        assert "from fastapi" not in src


# ===========================================================================
# Orchestrator wiring: _build_contextual_explanation / _stage_contextual_
# explanation (cost gate, candidate-only persistence, non-fatal failure,
# artifact resume, stage placement).
# ===========================================================================


def _fake_agent_result(elements_out, llm_call_count=1, truncated=False):
    return types.SimpleNamespace(elements=elements_out, llm_call_count=llm_call_count, truncated=truncated)


def _element_result(element_type, element_id, contextual=None, generic=None, skipped_reason=None):
    return types.SimpleNamespace(
        element_type=element_type, element_id=element_id,
        contextual_explanation=contextual, generic_explanation=generic,
        evidence_quote="quote", reason="reason", confidence=0.8,
        skipped_reason=skipped_reason,
    )


class TestBuildContextualExplanation:
    def _patch_common(self, monkeypatch, *, elements, meta, agent_result, insert_spy):
        monkeypatch.setattr(
            "core.document_pipeline.contextual_explanation_inputs.build_contextual_explanation_inputs",
            lambda **kwargs: (elements, meta),
        )

        class _FakeAgent:
            def __init__(self, llm_model=None, **kw):
                self.llm_model = llm_model

            def run(self, elements, cartridge_id=None):
                return agent_result

        monkeypatch.setattr(
            "episteme_graph.agents.contextual_explanation.agent.ContextualExplanationAgent",
            _FakeAgent,
        )
        monkeypatch.setattr(
            "core.postgres.get_session",
            lambda: types.SimpleNamespace(commit=lambda: None, rollback=lambda: None, close=lambda: None),
        )
        monkeypatch.setattr("core.element_explanations.insert_candidates", insert_spy)
        # Isolate the module-level daily CostGate so tests don't interfere.
        monkeypatch.setattr(orch, "_ctxexpl_cost_gate", orch.CostGate())

    def _no_elements_meta(self):
        return {
            "considered": 0, "selected": 0, "truncated": False, "truncated_count": 0,
            "counts_by_kind": {}, "skipped": [],
        }

    def test_no_elements_short_circuits_without_llm_call(self, monkeypatch):
        insert_spy = MagicMock(return_value=[])
        self._patch_common(
            monkeypatch, elements=[], meta=self._no_elements_meta(),
            agent_result=_fake_agent_result([]), insert_spy=insert_spy,
        )
        payload = orch._build_contextual_explanation(
            document_id="doc-1", cartridge_id=None, component_result=None,
            claim_objects=None, equations=None, fig_tbl=None,
            apparatus_result=None, thesis=None,
        )
        assert payload["llm_calls"] == 0
        assert payload["saved_candidates"] == 0
        insert_spy.assert_not_called()

    def test_saves_candidates_and_records_agent_skips(self, monkeypatch):
        elements = [types.SimpleNamespace(element_id="comp_1", element_type="theory_component")]
        meta = {
            "considered": 2, "selected": 2, "truncated": False, "truncated_count": 0,
            "counts_by_kind": {"component": 2}, "skipped": [],
        }
        results = [
            _element_result("theory_component", "comp_1", contextual="This paper uses X to support Y."),
            _element_result("equation", "eq_1", skipped_reason="repair_failed"),
        ]
        insert_spy = MagicMock(return_value=[{"id": "expl-1"}])
        self._patch_common(
            monkeypatch, elements=elements, meta=meta,
            agent_result=_fake_agent_result(results, llm_call_count=2), insert_spy=insert_spy,
        )
        payload = orch._build_contextual_explanation(
            document_id="doc-1", cartridge_id=None, component_result=None,
            claim_objects=None, equations=None, fig_tbl=None,
            apparatus_result=None, thesis=None,
        )
        assert payload["llm_calls"] == 2
        assert payload["saved_candidates"] == 1
        assert payload["agent_skipped"] == [
            {"element_type": "equation", "element_id": "eq_1", "reason": "repair_failed"},
        ]
        insert_spy.assert_called_once()
        _, called_document_id, called_items = insert_spy.call_args[0]
        assert called_document_id == "doc-1"
        assert len(called_items) == 1
        assert called_items[0]["element_id"] == "comp_1"
        assert called_items[0]["kind"] == "contextual"
        # E2 candidate-only: the pipeline never even offers a status field —
        # the store is the sole authority that forces status='candidate'.
        assert "status" not in called_items[0]

    def test_both_contextual_and_generic_produce_two_items(self, monkeypatch):
        elements = [types.SimpleNamespace(element_id="comp_1", element_type="theory_component")]
        meta = {
            "considered": 1, "selected": 1, "truncated": False, "truncated_count": 0,
            "counts_by_kind": {"component": 1}, "skipped": [],
        }
        results = [
            _element_result(
                "theory_component", "comp_1",
                contextual="Contextual text.", generic="Generic text.",
            ),
        ]
        insert_spy = MagicMock(return_value=[{"id": "e1"}, {"id": "e2"}])
        self._patch_common(
            monkeypatch, elements=elements, meta=meta,
            agent_result=_fake_agent_result(results, llm_call_count=1), insert_spy=insert_spy,
        )
        payload = orch._build_contextual_explanation(
            document_id="doc-1", cartridge_id=None, component_result=None,
            claim_objects=None, equations=None, fig_tbl=None,
            apparatus_result=None, thesis=None,
        )
        _, _, called_items = insert_spy.call_args[0]
        kinds = sorted(item["kind"] for item in called_items)
        assert kinds == ["contextual", "generic"]
        assert payload["saved_candidates"] == 2

    def test_daily_limit_exhausted_skips_agent_entirely(self, monkeypatch):
        elements = [types.SimpleNamespace(element_id="comp_1", element_type="theory_component")]
        meta = {
            "considered": 1, "selected": 1, "truncated": False, "truncated_count": 0,
            "counts_by_kind": {"component": 1}, "skipped": [],
        }
        run_spy = MagicMock()

        class _FakeAgent:
            def __init__(self, llm_model=None, **kw):
                pass

            def run(self, elements, cartridge_id=None):
                run_spy()
                return _fake_agent_result([])

        insert_spy = MagicMock()
        monkeypatch.setattr(
            "core.document_pipeline.contextual_explanation_inputs.build_contextual_explanation_inputs",
            lambda **kwargs: (elements, meta),
        )
        monkeypatch.setattr(
            "episteme_graph.agents.contextual_explanation.agent.ContextualExplanationAgent",
            _FakeAgent,
        )
        monkeypatch.setattr("core.element_explanations.insert_candidates", insert_spy)
        monkeypatch.setenv("CTXEXPL_MAX_CALLS_PER_DAY", "1")
        gate = orch.CostGate()
        monkeypatch.setattr(orch, "_ctxexpl_cost_gate", gate)
        # Exhaust today's budget before the stage even gets a chance to run.
        assert gate.check_and_count(daily_limit=1, daily_key=orch.today_str())

        payload = orch._build_contextual_explanation(
            document_id="doc-1", cartridge_id=None, component_result=None,
            claim_objects=None, equations=None, fig_tbl=None,
            apparatus_result=None, thesis=None,
        )
        assert payload.get("skipped_by_limit") is True
        run_spy.assert_not_called()
        insert_spy.assert_not_called()

    def test_required_equation_counts_survive_the_daily_limit_skip(self, monkeypatch):
        """CostGate 到達時は LLM を呼ばず、required 対象数を artifact に残す（§5.6）。"""
        elements = [types.SimpleNamespace(element_id="eq_1", element_type="equation")]
        meta = {
            "considered": 1, "selected": 1, "truncated": False, "truncated_count": 0,
            "counts_by_kind": {"equation": 1},
            "skipped": [{"element_type": "equation", "element_id": "eq_x",
                         "reason": "equation_not_resolved"}],
            "required_equations_considered": 2,
            "required_equations_selected": 1,
            "required_equations_unresolved": 1,
        }
        run_spy = MagicMock()

        class _FakeAgent:
            def __init__(self, llm_model=None, **kw):
                pass

            def run(self, elements, cartridge_id=None):
                run_spy()
                return _fake_agent_result([])

        monkeypatch.setattr(
            "core.document_pipeline.contextual_explanation_inputs.build_contextual_explanation_inputs",
            lambda **kwargs: (elements, meta),
        )
        monkeypatch.setattr(
            "episteme_graph.agents.contextual_explanation.agent.ContextualExplanationAgent",
            _FakeAgent,
        )
        monkeypatch.setattr("core.element_explanations.insert_candidates", MagicMock())
        monkeypatch.setenv("CTXEXPL_MAX_CALLS_PER_DAY", "1")
        gate = orch.CostGate()
        monkeypatch.setattr(orch, "_ctxexpl_cost_gate", gate)
        assert gate.check_and_count(daily_limit=1, daily_key=orch.today_str())

        payload = orch._build_contextual_explanation(
            document_id="doc-1", cartridge_id=None, component_result=None,
            claim_objects=None, equations=None, fig_tbl=None,
            apparatus_result=None, thesis=None,
        )
        assert payload.get("skipped_by_limit") is True
        run_spy.assert_not_called()
        assert payload["required_equations_considered"] == 2
        assert payload["required_equations_selected"] == 1
        assert payload["required_equations_unresolved"] == 1
        assert payload["skipped"] == meta["skipped"]

    def test_required_equation_counts_are_reported_in_the_payload(self, monkeypatch):
        elements = [types.SimpleNamespace(element_id="eq_1", element_type="equation")]
        meta = {
            "considered": 1, "selected": 1, "truncated": False, "truncated_count": 0,
            "counts_by_kind": {"equation": 1}, "skipped": [],
            "required_equations_considered": 3,
            "required_equations_selected": 3,
            "required_equations_unresolved": 0,
        }
        self._patch_common(
            monkeypatch, elements=elements, meta=meta,
            agent_result=_fake_agent_result([], llm_call_count=1),
            insert_spy=MagicMock(return_value=[]),
        )
        payload = orch._build_contextual_explanation(
            document_id="doc-1", cartridge_id=None, component_result=None,
            claim_objects=None, equations=None, fig_tbl=None,
            apparatus_result=None, thesis=None,
        )
        assert payload["required_equations_considered"] == 3
        assert payload["required_equations_selected"] == 3
        assert payload["required_equations_unresolved"] == 0

    def test_material_id_and_derivations_reach_the_input_builder(self, monkeypatch):
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return [], {
                "considered": 0, "selected": 0, "truncated": False, "truncated_count": 0,
                "counts_by_kind": {}, "skipped": [],
            }

        monkeypatch.setattr(
            "core.document_pipeline.contextual_explanation_inputs.build_contextual_explanation_inputs",
            _capture,
        )
        monkeypatch.setattr(orch, "_ctxexpl_cost_gate", orch.CostGate())
        orch._build_contextual_explanation(
            document_id="doc-1", cartridge_id=None, component_result=None,
            claim_objects=None, equations=None, fig_tbl=None,
            apparatus_result=None, thesis=None,
            material_id="mat-1", derivations="derivations-sentinel",
        )
        assert captured["material_id"] == "mat-1"
        assert captured["derivations"] == "derivations-sentinel"

    def test_model_env_var_is_forwarded_to_agent(self, monkeypatch):
        elements = [types.SimpleNamespace(element_id="comp_1", element_type="theory_component")]
        meta = {
            "considered": 1, "selected": 1, "truncated": False, "truncated_count": 0,
            "counts_by_kind": {"component": 1}, "skipped": [],
        }
        captured = {}

        class _FakeAgent:
            def __init__(self, llm_model=None, **kw):
                captured["llm_model"] = llm_model

            def run(self, elements, cartridge_id=None):
                return _fake_agent_result([])

        monkeypatch.setattr(
            "core.document_pipeline.contextual_explanation_inputs.build_contextual_explanation_inputs",
            lambda **kwargs: (elements, meta),
        )
        monkeypatch.setattr(
            "episteme_graph.agents.contextual_explanation.agent.ContextualExplanationAgent",
            _FakeAgent,
        )
        monkeypatch.setattr("core.element_explanations.insert_candidates", MagicMock(return_value=[]))
        monkeypatch.setattr(orch, "_ctxexpl_cost_gate", orch.CostGate())
        monkeypatch.setenv("CTXEXPL_LLM_MODEL", "gpt-test-model")

        orch._build_contextual_explanation(
            document_id="doc-1", cartridge_id=None, component_result=None,
            claim_objects=None, equations=None, fig_tbl=None,
            apparatus_result=None, thesis=None,
        )
        assert captured["llm_model"] == "gpt-test-model"


# ---------------------------------------------------------------------------
# _stage_contextual_explanation: non-fatal failure + artifact resume.
# ---------------------------------------------------------------------------


def _make_ctx(**overrides):
    result = orch.DocumentPipelineResult(
        document_id="doc-1", material_id="mat-1", cartridge_id=None, run_id="run-1",
    )
    ctx = orch.PipelineContext(
        pdf_bytes=b"", document_id="doc-1", material_id="mat-1", filename=None,
        source_kind="pdf", cartridge_id=None, course_id=None, run_id="run-1",
        agent_classes={}, effective_options={}, result=result,
    )
    reports: list[tuple] = []
    saved: dict = {}
    ctx.report_start = lambda stage, **kw: reports.append(("start", stage, kw))
    ctx.report_done = lambda stage, payload=None, **kw: reports.append(("done", stage, payload))
    ctx.save_artifact = lambda stage, value: saved.__setitem__(stage, value)
    ctx.artifact = lambda stage: saved.get(stage)
    ctx.should_use_artifact = lambda stage: False
    ctx.finish_target_stage = lambda stage, payload=None: False
    ctx._reports = reports  # test-only bookkeeping
    ctx._saved = saved
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


class TestStageWiring:
    def test_stage_is_non_fatal_on_build_failure(self, monkeypatch):
        ctx = _make_ctx()
        monkeypatch.setattr(
            orch, "_build_contextual_explanation", MagicMock(side_effect=RuntimeError("boom")),
        )
        result = orch._stage_contextual_explanation(ctx)
        assert result is False
        assert "contextual_explanation" in ctx._saved
        assert ctx._saved["contextual_explanation"]["error"] == "boom"
        assert ctx._saved["contextual_explanation"]["status"] == "completed"

    def test_stage_forwards_material_id_and_derivations(self, monkeypatch):
        """required equation の導出ソース2つ（コース snapshot 逆引きキー / 導出結果）
        がステージから渡ること。"""
        build_spy = MagicMock(return_value={"status": "completed"})
        monkeypatch.setattr(orch, "_build_contextual_explanation", build_spy)
        ctx = _make_ctx(derivations="derivations-sentinel")

        orch._stage_contextual_explanation(ctx)

        kwargs = build_spy.call_args.kwargs
        assert kwargs["material_id"] == "mat-1"
        assert kwargs["derivations"] == "derivations-sentinel"

    def test_stage_resumes_from_existing_artifact_without_rebuilding(self, monkeypatch):
        build_spy = MagicMock()
        monkeypatch.setattr(orch, "_build_contextual_explanation", build_spy)
        ctx = _make_ctx(should_use_artifact=lambda stage: True)
        ctx._saved["contextual_explanation"] = {"status": "completed", "llm_calls": 3}

        result = orch._stage_contextual_explanation(ctx)

        assert result is False
        build_spy.assert_not_called()
        assert ("done", "contextual_explanation", {"status": "completed", "llm_calls": 3}) in ctx._reports


class TestStagePlacement:
    def test_registered_between_narrative_annotator_and_course_mapping(self):
        stages = orch.PIPELINE_STAGES
        assert "contextual_explanation" in stages
        na = stages.index("narrative_annotator")
        ce = stages.index("contextual_explanation")
        cm = stages.index("course_mapping")
        assert na < ce < cm

    def test_pipeline_steps_registers_a_step_for_the_stage(self):
        names = [step.name for step in orch._PIPELINE_STEPS if step.name is not None]
        assert "contextual_explanation" in names

    def test_feature_registered_in_llm_usage_known_features(self):
        from core.llm_usage.schema import KNOWN_FEATURES

        assert "pipeline:contextual_explanation" in KNOWN_FEATURES


# ---------------------------------------------------------------------------
# Document-deletion cascade: element_explanations must not become orphaned
# (P4 / design §5.2 "行削除 API は作らない... document 削除経路の明示 DELETE に同乗").
# ---------------------------------------------------------------------------


class TestDeletionCascade:
    def test_purge_document_deletes_element_explanations(self):
        src = (BACKEND / "core" / "versioning" / "deletion.py").read_text(encoding="utf-8")
        assert "DELETE FROM element_explanations" in src

    def test_delete_material_route_deletes_element_explanations(self):
        src = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
        assert "DELETE FROM element_explanations" in src
