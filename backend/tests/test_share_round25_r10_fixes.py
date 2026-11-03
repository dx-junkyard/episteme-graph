"""第 25 周 第 10 波 面 A（share）の是正 — IK-1126 / IK-1123 / IK-1124 / IK-1125 / IK-1062 の残り、
共有画面の版の見出し（IK-0956 の面）と関係の確定の理由の書き足し（頭脳 NEW）。

DB も LLM も使わない（純関数・monkeypatch・ソースの契約）。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

ADMIN_JS = (ROOT / "frontend/public/js/admin.js").read_text(encoding="utf-8")
DELIB_JS = (ROOT / "frontend/public/js/deliberation.js").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "frontend/public/admin.html").read_text(encoding="utf-8")
MANUAL_19 = (ROOT / "docs/manual/teacher/19-admin-knowledge-library.md").read_text(encoding="utf-8")


# ===========================================================================
# IK-1126 — 「届きます」はトピックの ⚓（学生の画面の道）で言う
# ===========================================================================


def _course(topics):
    return {"topics": topics, "sources": []}


class TestCitationAnchorGranularity:
    def test_no_anchor_in_any_topic(self):
        from core.course_content_builder import course_has_component_anchor

        course = _course([{"id": "t0", "title": "暗黒エネルギー", "student_material": {"body": "本文"}}])
        assert course_has_component_anchor(course, component_keys={"uuid-1", "comp_003"}, document_id="d1") is False

    def test_anchor_by_linked_component_or_evidence_link(self):
        from core.course_content_builder import course_has_component_anchor

        course = _course([{"id": "t1", "linked_component_ids": ["comp_003"]}])
        assert course_has_component_anchor(course, component_keys={"uuid-1", "comp_003"}, document_id="d1") is True
        course = _course([{"id": "t1", "evidence_links": [{"kind": "component", "target_id": "uuid-1"}]}])
        assert course_has_component_anchor(course, component_keys={"uuid-1"}, document_id="d1") is True

    def test_agent_id_in_another_document_does_not_count(self):
        from core.course_content_builder import course_has_component_anchor

        course = _course([{
            "id": "t1",
            "linked_component_ids": ["comp_003"],
            "content_blocks": [{"type": "components", "items": [{"component_id": "comp_003", "document_id": "d-other"}]}],
        }])
        assert course_has_component_anchor(course, component_keys={"comp_003"}, document_id="d1") is False

    def test_chapter_nested_topics_are_walked(self):
        from core.course_content_builder import course_has_component_anchor

        course = {"chapters": [{"topics": [{"id": "t9", "linked_component_ids": ["comp_003"]}]}]}
        assert course_has_component_anchor(course, component_keys={"comp_003"}, document_id="d1") is True

    def test_fact_says_no_anchor_even_when_paper_is_in_sources(self):
        from core import explanation_governance as xgov

        fact = xgov.citation_delivery_fact(
            review_status="teacher_approved", delivered=True, course_has_part=True, part_anchored=False,
        )
        assert fact == xgov.CITATION_NO_ANCHOR_IN_COURSE_FACT
        assert "⚓" in fact and "引用の記録は残ります" in fact
        fact = xgov.citation_delivery_fact(
            review_status="teacher_approved", delivered=True, course_has_part=True, part_anchored=None,
        )
        assert fact == xgov.CITATION_ANCHOR_UNCHECKED_FACT
        # 従来の呼び出し（anchored を渡さない）は不変。
        assert xgov.citation_delivery_fact(
            review_status="teacher_approved", delivered=True,
        ) == xgov.CITATION_DELIVERED_FACT

    def test_course_scope_fact(self):
        from core import explanation_governance as xgov

        assert xgov.citation_course_scope_fact("public") == ""
        assert xgov.citation_course_scope_fact(None) == ""
        assert xgov.citation_course_scope_fact("private") == "引用先のコースの公開範囲は「非公開（自分のみ）」です。"
        assert "グループ限定" in xgov.citation_course_scope_fact("group")

    def test_route_uses_anchor_helper(self):
        from routes import theory_components as tc

        src = inspect.getsource(tc._citing_course_has_component_document)
        assert "course_has_component_anchor(" in src
        assert "legacy_ids" in src
        cite = inspect.getsource(tc.cite_explanation) + inspect.getsource(tc._citation_response)  # 第 11 波（IK-1154）で応答を分けた
        assert 'part_anchored=_course_reach["anchored"]' in cite
        assert "citation_course_scope_fact(" in cite


# ===========================================================================
# IK-1123 — 札と判断の行の印が重ならない
# ===========================================================================


class TestDecisionMarkNotDoubled:
    def test_decision_line_without_mark(self):
        from core.deliberation import identity_links as il

        assert il.decision_line("rejected", "西川", "2026-10-31") == "〔見送り〕 見送り: 西川・2026-10-31"
        assert il.decision_line("rejected", "西川", "2026-10-31", with_mark=False) == "見送り: 西川・2026-10-31"

    def test_describe_actors_returns_mark_free_line(self, monkeypatch):
        from core import atlas_review_display as ard
        from core.deliberation import identity_links as il

        monkeypatch.setattr(ard, "fetch_actor_labels", lambda session, ids: {"u1": "西川"})

        class _S:
            def close(self):
                pass

        monkeypatch.setattr(il, "get_session", lambda: _S())
        out = il.describe_actors({"status": "rejected", "decided_by": "u1", "decided_at": "2026-10-31T01:00"})
        assert out["decision_actor_line"] == "見送り: 西川・2026-10-31"
        assert out["status_label"] == "見送り"

    def test_detail_row_uses_mark_free_line(self):
        i = ADMIN_JS.index("function _libraryBoundLinksHtml")
        body = ADMIN_JS[i:i + 4000]
        assert "link.decision_actor_line || link.decision_line" in body


# ===========================================================================
# IK-1062 の残り — 戻した行に前の判断の誰・いつ・なぜ
# ===========================================================================


class TestPriorDecisionLine:
    def test_rejected_without_reason(self):
        from core.deliberation import identity_links as il

        line = il.prior_decision_line(
            "rejected", decided_by_label="岡村", decided_at="2026-10-27T03:00", reason="",
        )
        assert line.startswith("前の判断の記録: 見送り: 岡村・2026-10-27")
        assert "見送りの理由: 記録なし" in line
        assert "〔" not in line

    def test_revoked_with_reason(self):
        from core.deliberation import identity_links as il

        line = il.prior_decision_line(
            "revoked", decided_by_label="岡村", decided_at="2026-10-26",
            revoked_by_label="西川", revoked_at="2026-10-27", reason="版違い",
        )
        assert "取り消し: 西川・2026-10-27（元の確定: 岡村・2026-10-26）" in line
        assert "取り消しの理由: 版違い" in line

    def test_unknown_origin_is_empty(self):
        from core.deliberation import identity_links as il

        assert il.prior_decision_line("", decided_by_label="", decided_at=None) == ""

    def test_rendered_on_all_three_surfaces(self):
        from routes import library as lib

        assert "prior_decision_line" in inspect.getsource(lib._attach_identity_pair_facts)
        assert "data-identity-prior-decision" in ADMIN_JS
        assert "data-identity-prior-decision" in DELIB_JS


# ===========================================================================
# IK-1124 — 凍結の確認文に学生への効果
# ===========================================================================


def test_freeze_confirm_mirrors_learner_effect_note():
    from core.label_vocab import LIBRARY_FREEZE_LEARNER_EFFECT_NOTE

    i = ADMIN_JS.index("function freezeLibraryEntry")
    assert LIBRARY_FREEZE_LEARNER_EFFECT_NOTE in ADMIN_JS[i:i + 1500]
    assert "学生の画面" in LIBRARY_FREEZE_LEARNER_EFFECT_NOTE
    assert LIBRARY_FREEZE_LEARNER_EFFECT_NOTE.replace("学生の画面", "") in MANUAL_19.replace("\n", "").replace("学生の画面", "")


# ===========================================================================
# IK-1125 — ドリルダウンのボタンの平文化
# ===========================================================================


class TestDrilldownPlainText:
    def test_spacing_and_modifiers(self):
        from core.learning_support_agent import _strip_math_delimiters as f

        assert f(r"入力 $\mathbf{F}_{\!\mathrm{EOS}}$ の選び方") == "入力 F_EOS の選び方"
        assert f(r"$\hat\varepsilon_c$ と $\mathcal{N}_\mathrm{TOV}$") == "ε_c と N_TOV"
        assert f(r"$a\,b$") == "a b"

    def test_existing_behaviour_kept(self):
        from core.learning_support_agent import _strip_math_delimiters as f

        assert f(r"\alpha_K>0 implies \mu\ge1") == "α_K>0 implies μ≥1"


# ===========================================================================
# IK-0956 の面 — 共有画面の見出しに版（PDF / TeX）
# ===========================================================================


class TestShareHeadingFormat:
    def test_material_format_label(self):
        from routes import admin as adm

        assert adm._material_format_label("arXiv-2605.31198v1.tar.gz") == "TeX 版"
        assert adm._material_format_label("paper.pdf") == "PDF 版"
        assert adm._material_format_label("") == ""

    def test_schema_and_modal(self):
        from schemas import MaterialOut

        assert "format_label" in MaterialOut.model_fields
        assert 'data-format-label="' in ADMIN_JS
        assert "openDocumentShareModal(documentId, title, materialId, visibility, groupId, formatLabel)" in ADMIN_JS
        assert "data-doc-share-heading" in ADMIN_JS


# ===========================================================================
# 頭脳 NEW — 理由の無い確定に、確定した本人が後から理由を書き足す
# ===========================================================================


class TestRelationDecisionReason:
    def test_sql_guard(self):
        from core.library import registry as reg

        src = inspect.getsource(reg.add_relation_decision_reason)
        assert "status = 'confirmed'" in src
        assert "COALESCE(review_note, '') = ''" in src
        assert "decided_by = CAST(:actor AS uuid)" in src
        assert "DELETE" not in src
        assert "AUDIT_ACTION_RELATION_DECISION_REASON_ADD" in src

    def test_audit_action_declared(self):
        from core.library import schema as ls

        assert ls.AUDIT_ACTION_RELATION_DECISION_REASON_ADD in ls.REGISTRY_AUDIT_ACTIONS

    def test_flag_only_for_the_decider(self, monkeypatch):
        from core import atlas_review_display as ard
        from routes import library as lib

        monkeypatch.setattr(ard, "fetch_actor_labels", lambda session, ids: {})

        class _S:
            def close(self):
                pass

        monkeypatch.setattr(lib, "get_session", lambda: _S())
        base = {"status": "confirmed", "review_note": "", "decided_by": "u1", "kind": "related",
                "subject_entry_id": "e1", "object_entry_id": "e2"}
        rels = [dict(base), dict(base, decided_by="u2"), dict(base, review_note="書いた"),
                dict(base, status="candidate")]
        lib._describe_relations(rels, "e1", viewer_user_id="u1")
        assert [bool(r.get("can_add_decision_reason")) for r in rels] == [True, False, False, False]

    def test_route_and_ui(self):
        from routes import library as lib

        assert hasattr(lib, "add_relation_decision_reason")
        src = inspect.getsource(lib.add_relation_decision_reason)
        assert "status_code=409" in src and "status_code=422" in src
        assert 'data-ui-anchor="knowledge-library.relation-decision-reason"' in ADMIN_JS
        assert "/decision-reason" in ADMIN_JS
        assert "{#relation-decision-reason}" in MANUAL_19


def test_cache_busters_bumped():
    assert "share10" in ADMIN_HTML.split("admin.js?v=")[1].split('"')[0]
    assert "share10" in ADMIN_HTML.split("deliberation.js?v=")[1].split('"')[0]
