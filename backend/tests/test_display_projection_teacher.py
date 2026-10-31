"""教員向けの名指しの面の表示投影（DP7）と、射影では直せない作り込みの漏れの是正。

正本: ``docs/features/display_projection_design.md`` §2 DP7 / §6（2026-10-01 教員向け）。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import APIRouter, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import display_projection as dp  # noqa: E402


class TestTokenReplacer:
    def test_replacer_is_used_and_falls_back_to_default(self):
        text = "Equation (eq_tex_b14) follows from synth_claim_0003."
        out = dp.mask_internal_ids(text, token_replacer=lambda t: "式 (3)" if t == "eq_tex_b14" else None)
        assert out == f"Equation (式 (3)) follows from {dp.UNIDENTIFIED_ELEMENT_TEXT}."

    def test_replacer_exception_falls_back(self):
        def _boom(_t):
            raise RuntimeError("x")

        assert dp.mask_internal_ids("eq_op_7", token_replacer=_boom) == dp.UNIDENTIFIED_ELEMENT_TEXT

    def test_teacher_projection_keeps_labels_and_addresses(self):
        payload = {
            "module_key": "m2:abc",
            "node_ids": ["theory_op_0001"],
            "confidence_label": "高い",
            "confidence": 0.8,
            "structure_fingerprint": "m2|outer|ops=a",
            "label": "Define eq_tex_b14",
        }
        out = dp.project_for_teacher(payload, token_replacer=lambda t: "式 (3)")
        assert out == {
            "module_key": "m2:abc",
            "node_ids": ["theory_op_0001"],
            "confidence_label": "高い",
            "label": "Define 式 (3)",
        }


class TestTeacherDisplayRoute:
    def test_route_projects_the_return_value(self):
        from display_route import TeacherDisplayRoute

        router = APIRouter(route_class=TeacherDisplayRoute)

        @router.get("/x")
        def _x() -> dict:
            return {"statement": "Infer intermediate claim synth_claim_0001", "score": 3, "target_id": "eq_op_1"}

        app = FastAPI()
        app.include_router(router)
        body = TestClient(app).get("/x").json()
        assert body == {
            "statement": f"Infer intermediate claim {dp.UNIDENTIFIED_ELEMENT_TEXT}",
            "target_id": "eq_op_1",
        }

    def test_projection_failure_returns_original(self, monkeypatch):
        import display_route

        def _boom(_v):
            raise RuntimeError("x")

        monkeypatch.setattr(display_route, "project_for_teacher", _boom)
        assert display_route.project_teacher_payload({"a": 1}) == {"a": 1}


class TestPlainMath:
    def test_escaped_braces_leave_no_backslash(self):
        from core.theory_modules.builder import plain_math

        assert plain_math(r"\{\Theta^2_{\rm GW}\}") == "Θ^2_GW"
        assert plain_math(r"D, M_{\mathrm{PISN}}, N") == "D, M_PISN, N"
        assert "\\" not in plain_math(r"a\_b \% c \ d")


class TestComponentGraphLabels:
    def test_visual_label_drops_cut_flow_with_ids(self):
        import routes.theory_components as tc

        out = tc._readable_visual_label("Infer intermediate claim\nsynth_claim_0013 → clai", {})
        assert out == "Infer intermediate claim"

    def test_visual_label_keeps_readable_equation_flow(self):
        import routes.theory_components as tc

        records = {"eq_tex_b2": {"label": "(2)"}, "eq_tex_b3": {"label": "(3)"}}
        out = tc._readable_visual_label("Substitute\neq_tex_b2 → eq_tex_b3", records)
        assert out == "Substitute\n式 (2) → 式 (3)"

    def test_main_display_label_has_no_raw_tex(self):
        import routes.theory_components as tc

        graph = {"nodes": [{"component_id": "theory_op_3", "label": "Elimination", "graph_layer": "main",
                            "theory_object": r"D, M_{\mathrm{PISN}}, N"}]}
        tc._attach_main_stage_display_labels(graph, [])
        label = graph["nodes"][0]["display_label"]
        assert "\\" not in label and "{" not in label
        assert label.endswith("D, M_PISN, N")

    def test_teacher_token_replacer_uses_printed_numbers(self):
        import routes.theory_components as tc

        replace = tc._teacher_token_replacer({"eq_tex_b14": {"label": "(14)"}})
        assert replace("eq_tex_b14") == "式 (14)"
        assert replace("tex_b14") == "式 (14)"
        assert replace("eq_blk_004_0084") == "番号なしの式"
        assert replace("synth_claim_0001") is None

    def test_named_tex_label_equation_ids_are_masked(self):
        import routes.theory_components as tc

        records = {"eq_delta_b": {"label": "(5)"}}
        out = tc._mask_named_equation_ids(
            {"label": "Define eq_delta_b", "equation_id": "eq_delta_b", "chain": ["eq_delta_b"]}, records
        )
        assert out == {"label": "Define 式 (5)", "equation_id": "eq_delta_b", "chain": ["eq_delta_b"]}


class TestSeminarBriefNodeLabels:
    def _labels(self):
        from core.doubt.seminar_brief import _ReadableLabels

        labels = _ReadableLabels.__new__(_ReadableLabels)
        labels.equation_records = {}
        labels.node_labels = {}
        labels.claim_texts = {
            "synth_claim_0013": "Halo assembly bias is the dependence of halo clustering on properties beyond halo mass.",
        }
        return labels

    def test_operation_phrase_plus_cut_id_becomes_operation_and_claim(self):
        labels = self._labels()
        out = labels._readable_node_label(
            "Infer intermediate claim\nsynth_claim_0013 → clai",
            operation="infer_intermediate_claim",
            claim_ids=["synth_claim_0013"],
        )
        assert out.startswith("中間主張を導出: Halo assembly bias")
        assert "clai" not in out.replace("claim", "") and dp.UNIDENTIFIED_ELEMENT_TEXT not in out
        assert "synth_claim" not in out

    def test_without_claim_text_falls_back_to_japanese_operation(self):
        out = self._labels()._readable_node_label(
            "Flag limitation\nclaim_span_002 → claim_s", operation="flag_limitation", claim_ids=["missing"]
        )
        assert out == "制約を確認"

    def test_readable_head_is_kept(self):
        out = self._labels()._readable_node_label(
            "Linear bias relation", operation="derive_bias", claim_ids=["synth_claim_0013"]
        )
        assert out == "Linear bias relation"


class TestTargetLabelExcerpt:
    def test_claim_label_is_not_cut_mid_word(self):
        from core.doubt.open_assumptions import target_label

        long_text = ("word " * 60).strip() + " claimtail"

        class _Row:
            def fetchone(self):
                return (long_text,)

        class _Session:
            def execute(self, *a, **k):
                return _Row()

        out = target_label(_Session(), "claim", "x", document_id=None)
        assert len(out) <= 161
        assert out.endswith("…") and not out.rstrip("…").endswith("wor")
