"""第 25 周 第 9 波 面 A（share）の是正 — IK-1076 / IK-1077 / IK-0681 / IK-1063・IK-1050 の残り。

DB も LLM も使わない（純関数・monkeypatch・ソースの契約）。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

ADMIN_JS = (ROOT / "frontend/public/js/admin.js").read_text(encoding="utf-8")
DELIB_JS = (ROOT / "frontend/public/js/deliberation.js").read_text(encoding="utf-8")
STUDIO_JS = (ROOT / "frontend/public/js/admin-lecture-studio.js").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "frontend/public/admin.html").read_text(encoding="utf-8")


# ===========================================================================
# IK-1076 — 一覧と詳細の判断可否は 1 つの述語（置き換わった部品の記録・見送った概念は判断しない）
# ===========================================================================


class TestDecidablePredicate:
    def test_instance_is_live(self):
        from core.deliberation import identity_links as il

        assert il.instance_is_live("theory_component", "a", {"a"}, set()) is True
        assert il.instance_is_live("theory_component", "b", {"a"}, set()) is False
        assert il.instance_is_live("theory_module", "m", set(), {"m"}) is True
        assert il.instance_is_live("theory_module", "x", set(), {"m"}) is False
        assert il.instance_is_live("symbol", "s", set(), set()) is True

    def test_library_list_uses_the_same_predicate(self):
        from routes import library as lib

        src = inspect.getsource(lib._link_resolves_to_live_component)
        assert "instance_is_live" in src
        assert lib._link_resolves_to_live_component(
            {"instance_element_type": "theory_component", "instance_element_id": "b"}, {"a"}, set()
        ) is False

    def test_undecidable_note(self):
        from core.deliberation import identity_links as il

        assert il.link_undecidable_note({"instance_stranded_note": "x"}) == il.LINK_UNDECIDABLE_STRANDED_NOTE
        assert il.link_undecidable_note({}, entry_review_status="dismissed") == il.LINK_UNDECIDABLE_ENTRY_DISMISSED_NOTE
        assert il.link_undecidable_note({}, entry_review_status="confirmed") == ""
        assert il.link_undecidable_note({}) == ""

    def test_detail_response_marks_stranded_row_undecidable(self, monkeypatch):
        from routes import deliberation as d
        from core.knowledge_objects.remap import STRANDED_RECORD_NOTE

        monkeypatch.setattr(
            d.identity_links, "describe_instance",
            lambda link: {"instance_label": "", "instance_stranded_note": STRANDED_RECORD_NOTE},
        )
        monkeypatch.setattr(d.identity_links, "describe_actors", lambda link: {})
        monkeypatch.setattr(d.library_store, "get_entry", lambda _id: {"name": "P", "review_status": "candidate"})
        out = d._identity_link_response({
            "id": "l1", "status": "candidate", "shared_part_id": "e1",
            "instance_element_type": "theory_component", "instance_element_id": "c1",
        })
        assert out["decidable"] is False
        assert out["undecidable_note"] == d.identity_links.LINK_UNDECIDABLE_STRANDED_NOTE
        assert "confirm_question" not in out
        assert "confirm_note" not in out

    def test_detail_response_live_row_keeps_shape(self, monkeypatch):
        from routes import deliberation as d

        monkeypatch.setattr(d.identity_links, "describe_instance", lambda link: {"instance_label": "Part"})
        monkeypatch.setattr(d.identity_links, "describe_actors", lambda link: {})
        monkeypatch.setattr(d.library_store, "get_entry", lambda _id: {"name": "P", "review_status": "candidate"})
        out = d._identity_link_response({"id": "l1", "status": "candidate", "shared_part_id": "e1"})
        assert "decidable" not in out
        assert out["confirm_question"]

    def test_dismissed_concept_rows_are_undecidable(self, monkeypatch):
        from routes import deliberation as d

        monkeypatch.setattr(d.identity_links, "describe_instance", lambda link: {"instance_label": "Part"})
        monkeypatch.setattr(d.identity_links, "describe_actors", lambda link: {})
        monkeypatch.setattr(d.library_store, "get_entry", lambda _id: {"name": "P", "review_status": "dismissed"})
        out = d._identity_link_response({"id": "l1", "status": "candidate", "shared_part_id": "e1"})
        assert out["decidable"] is False
        assert out["undecidable_note"] == d.identity_links.LINK_UNDECIDABLE_ENTRY_DISMISSED_NOTE

    def test_pair_questions_never_name_an_unreadable_part(self):
        from routes import deliberation as d

        rows = [
            {"shared_part_id": "e", "status": "candidate", "instance_element_type": "theory_component",
             "instance_label": "", "decidable": False, "confirm_question": "x（名前を読めない要素）"},
            {"shared_part_id": "e", "status": "candidate", "instance_element_type": "theory_component",
             "instance_label": "Physical consistency result", "shared_part_name": "P"},
        ]
        d._attach_pair_confirm_questions(rows)
        assert "confirm_question" not in rows[0]
        assert "名前を読めない" not in rows[1].get("confirm_question", "")

    def test_confirm_and_restore_refuse_undecidable(self):
        from routes import deliberation as d

        assert "_ensure_identity_link_decidable" in inspect.getsource(d._decide_identity_link)
        assert "_ensure_identity_link_decidable" in inspect.getsource(d.restore_identity_link)
        assert "409" in inspect.getsource(d._ensure_identity_link_decidable)

    def test_frontends_hide_buttons_on_undecidable_rows(self):
        assert ADMIN_JS.count('link.status === "candidate" && link.decidable !== false') == 2
        assert "data-bound-part-undecidable" in ADMIN_JS
        assert "data-identity-undecidable" in ADMIN_JS
        assert '(link.status === "rejected" || link.status === "revoked") && link.decidable !== false' in ADMIN_JS
        assert 'link.status === "candidate" && decidable' in DELIB_JS
        assert '(link.status === "rejected" || link.status === "revoked") && decidable' in DELIB_JS


# ===========================================================================
# IK-1077 — 引用できない説明にボタンを出さず、断りは日本語の事実文
# ===========================================================================


class TestCiteAffordance:
    def test_can_cite_matches_the_403_condition(self):
        from routes import theory_components as tc

        teacher = {"id": "u1", "role": "TEACHER"}
        assert tc._explanation_can_cite("", True, teacher) is True
        assert tc._explanation_can_cite("", False, teacher) is False
        assert tc._explanation_can_cite("u1", False, teacher) is True
        assert tc._explanation_can_cite("u2", False, {"id": "u1", "role": "SYSTEM_ADMIN"}) is True
        assert tc._explanation_can_cite("u2", False, None) is False

    def test_cite_route_uses_the_same_predicate_and_japanese_detail(self):
        from routes import theory_components as tc

        src = inspect.getsource(tc.cite_explanation)
        assert "_explanation_can_cite" in src
        assert "citation_blocked_note" in src
        assert "This explanation is not shared" not in inspect.getsource(tc)

    def test_notes(self):
        from core import explanation_governance as xgov

        assert "著者だけ" in xgov.citation_blocked_note(is_standard=False)
        assert "編集できる教員" in xgov.citation_blocked_note(is_standard=True)

    def test_explanation_out_carries_can_cite(self):
        from routes import theory_components as tc

        fields = tc.ExplanationOut.model_fields
        assert "can_cite" in fields and "cite_note" in fields

    def test_card_hides_cite_button(self):
        assert "exp.can_cite === false" in STUDIO_JS
        assert "data-exp-cite-note" in STUDIO_JS


# ===========================================================================
# IK-0681 — TeX の label キーは判定を切り詰めの前に（式の中に現れる経路・要素文脈）
# ===========================================================================


class TestTexLabelKeyBeforeTruncation:
    def test_where_text_drops_tex_keys(self):
        from core.symbol_lookup import _where_text

        assert _where_text(["eq:tovnet", "eq:tov_residual"], False) == "番号の無い式"
        assert _where_text(["eq:tovnet", "3"], False) == "式 (3)・番号の無い式"
        assert "eq:" not in _where_text(["eq:tov_resid…"], False)

    def test_formula_fallback_judges_before_truncation(self):
        from core import symbol_lookup as sl

        src = inspect.getsource(sl)
        assert 'label = _printed_equation_label(formula.get("label"))' in src
        assert 'label = _clean(formula.get("label"), limit=12)' not in src

    def test_equation_focus_label(self):
        from core.learner_context_common import equation_focus_label

        assert equation_focus_label("eq:tovnet", "eq_tex_b14") == ""
        assert equation_focus_label("12", "eq_12") == "12"

    def test_mini_ref_label(self):
        from core.element_context import _project_mini_ref

        out = _project_mini_ref({"element_type": "equation", "element_id": "eq_tex_b3", "label": "eq:tovnet"})
        assert "eq:" not in out["label"]


# ===========================================================================
# IK-1063 / IK-1050 の残り — 旧い行の記録なしを言う・足し直しの理由を捨てない
# ===========================================================================


class TestRecordsNotSilent:
    def test_confirmed_relation_without_reason_says_so(self):
        from core.library import schema as ls

        assert ls.relation_decision_reason_line("confirmed", "") == ls.RELATION_CONFIRMED_REASON_UNRECORDED
        assert "記録なし" in ls.RELATION_CONFIRMED_REASON_UNRECORDED

    def test_entry_without_decider_says_so(self):
        from core.library import schema as ls
        from routes import library as lib

        assert ls.entry_decision_unrecorded_line("confirmed") == ls.ENTRY_CONFIRMED_UNRECORDED_LINE
        assert ls.entry_decision_unrecorded_line("dismissed") == ls.ENTRY_DISMISSED_UNRECORDED_LINE
        assert ls.entry_decision_unrecorded_line("candidate") == ""
        assert "entry_decision_unrecorded_line" in inspect.getsource(lib)

    def test_reason_add_audit_action_declared(self):
        from core.library import schema as ls

        assert ls.AUDIT_ACTION_RELATION_REASON_ADD in ls.REGISTRY_AUDIT_ACTIONS

    def test_readd_reason_fills_only_empty_own(self, monkeypatch):
        from routes import library as lib

        filled = {"id": "r1", "reason": "上位の定義"}
        monkeypatch.setattr(lib.library_registry, "add_relation_reason", lambda *a, **k: filled)
        rel, fact = lib._relation_readd_reason_fact({"id": "r1", "reason": ""}, "上位の定義", "u1")
        assert rel is filled and fact == lib.RELATION_REASON_ADDED_FACT

        monkeypatch.setattr(lib.library_registry, "add_relation_reason", lambda *a, **k: None)
        rel, fact = lib._relation_readd_reason_fact({"id": "r1", "reason": ""}, "別の人の理由", "u2")
        assert "別の人の理由" in fact and "保存していません" in fact

        rel, fact = lib._relation_readd_reason_fact({"id": "r1", "reason": "既存"}, "新しい理由", "u1")
        assert "書き換えない" in fact and "新しい理由" in fact

        rel, fact = lib._relation_readd_reason_fact({"id": "r1", "reason": "既存"}, "", "u1")
        assert fact == ""

    def test_registry_fill_is_guarded_in_sql(self):
        from core.library import registry as reg

        src = inspect.getsource(reg.add_relation_reason)
        assert "COALESCE(reason, '') = ''" in src
        assert "created_by = CAST(:actor AS uuid)" in src
        assert "DELETE" not in src


def test_cache_busters_bumped():
    assert "share9" in ADMIN_HTML.split("admin.js?v=")[1].split('"')[0]
    assert "share9" in ADMIN_HTML.split("admin-lecture-studio.js?v=")[1].split('"')[0]
    assert "share9" in ADMIN_HTML.split("deliberation.js?v=")[1].split('"')[0]
