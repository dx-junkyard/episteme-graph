"""第 25 周 第 8 波 面 A（share）の是正 — IK-1061 / IK-1062 / IK-1063 / IK-1064・IK-1050 の残り・IK-0980。

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
APP_JS = (ROOT / "frontend/public/js/app.js").read_text(encoding="utf-8")
DELIB_JS = (ROOT / "frontend/public/js/deliberation.js").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "frontend/public/admin.html").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "frontend/public/index.html").read_text(encoding="utf-8")
DELIB_MANUAL = (ROOT / "docs/manual/teacher/24-admin-deliberation.md").read_text(encoding="utf-8")
LIBRARY_MANUAL = (ROOT / "docs/manual/teacher/19-admin-knowledge-library.md").read_text(encoding="utf-8")
MIGRATION_093 = (BACKEND / "db/093_identity_link_revoke.sql").read_text(encoding="utf-8")


# ===========================================================================
# IK-1061 — 引用の「届きます」は引用先のコースの教材が部品の論文を含むときだけ
# ===========================================================================


class TestCitationDeliveryNeedsCoursePart:
    def test_fact_says_not_in_course(self):
        from core import explanation_governance as xgov

        assert xgov.citation_delivery_fact(
            review_status="teacher_approved", delivered=True, course_has_part=False
        ) == xgov.CITATION_NOT_IN_COURSE_FACT
        # 査読待ちでも、教材に無い事実を先に言う（届く道が無い）。
        assert xgov.citation_delivery_fact(
            review_status="draft", delivered=False, course_has_part=False
        ) == xgov.CITATION_NOT_IN_COURSE_FACT

    def test_unknown_course_never_claims_delivery(self):
        from core import explanation_governance as xgov

        fact = xgov.citation_delivery_fact(
            review_status="teacher_approved", delivered=True, course_has_part=None
        )
        assert fact == xgov.CITATION_COURSE_UNCHECKED_FACT
        assert fact != xgov.CITATION_DELIVERED_FACT

    def test_delivered_only_when_part_in_course(self):
        from core import explanation_governance as xgov

        assert xgov.citation_delivery_fact(
            review_status="teacher_approved", delivered=True, course_has_part=True
        ) == xgov.CITATION_DELIVERED_FACT

    def test_part_check_uses_course_sources(self, monkeypatch):
        from routes import theory_components as tc

        class _Row:
            def __getitem__(self, i):
                return "doc-tex"

        class _Session:
            def execute(self, *a, **kw):
                class _R:
                    def fetchone(self_inner):
                        return _Row()
                return _R()

            def close(self):
                pass

        monkeypatch.setattr(tc, "get_editable_course_data", lambda uid, cid: {"sources": []})
        monkeypatch.setattr(tc, "_pg_session", lambda: _Session())
        import services

        monkeypatch.setattr(services, "list_course_source_document_ids", lambda data: {"doc-pdf"})
        user = {"id": "u1", "role": "teacher"}
        assert tc._citing_course_has_component_document("c1", "comp-1", user)["has_part"] is False
        monkeypatch.setattr(services, "list_course_source_document_ids", lambda data: {"doc-tex"})
        assert tc._citing_course_has_component_document("c1", "comp-1", user)["has_part"] is True
        monkeypatch.setattr(tc, "get_editable_course_data", lambda uid, cid: None)
        assert tc._citing_course_has_component_document("c1", "comp-1", user)["has_part"] is None

    def test_cite_route_passes_the_course_check(self):
        from routes import theory_components as tc

        src = inspect.getsource(tc.cite_explanation) + inspect.getsource(tc._citation_response)  # 第 11 波（IK-1154）で応答を分けた
        # 第 10 波（IK-1126）: 判定は dict（has_part / anchored / visibility）で渡す。
        assert "_citing_course_has_component_document(" in src
        assert "course_has_part=_course_reach[\"has_part\"]" in src


# ===========================================================================
# IK-1062 — 見送り・取り消した同一性リンクを候補に戻す
# ===========================================================================


class TestIdentityLinkRestore:
    def test_migration_adds_restore_columns(self):
        for col in ("restored_by", "restored_at", "restore_reason", "restored_from"):
            assert f"ADD COLUMN IF NOT EXISTS {col}" in MIGRATION_093
        assert "DELETE" not in MIGRATION_093.upper().replace("DELETE 文", "")

    def test_restore_requires_actor_and_reason(self):
        from core.deliberation import identity_links as il

        with pytest.raises(ValueError):
            il.restore("x", restored_by="", reason="r", audit_entity_type="deliberation", record_audit=print)
        with pytest.raises(ValueError):
            il.restore("x", restored_by="u", reason=" ", audit_entity_type="deliberation", record_audit=print)

    def test_restore_only_from_negative_states(self, monkeypatch):
        from core.deliberation import identity_links as il

        for status in ("candidate", "confirmed"):
            monkeypatch.setattr(il, "get_by_id", lambda _id, s=status: {"id": "x", "status": s})
            assert il.restore(
                "x", restored_by="u", reason="r", audit_entity_type="deliberation", record_audit=print
            ) is None

    def test_restore_goes_through_candidate_flow_and_audits(self, monkeypatch):
        from core.deliberation import identity_links as il

        monkeypatch.setattr(il, "get_by_id", lambda _id: {
            "id": "x", "status": "revoked", "decided_by": "a", "revoked_by": "b", "shared_part_id": "p",
        })
        executed: list = []

        class _Res:
            def fetchone(self):
                return ("x", "theory_component", "c1", "d1", "p", "candidate", {}, [], "", None,
                        None, "a", None, None, None, None, "b", None, "r0", "u", None, "why", "revoked")

        class _Session:
            def execute(self, sql, params):
                executed.append((str(sql), params))
                return _Res()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(il, "get_session", lambda: _Session())
        audits: list = []
        out = il.restore("x", restored_by="u", reason="誤りだった", audit_entity_type="deliberation",
                         record_audit=lambda *a: audits.append(a))
        assert out["status"] == "candidate" and out["restored_from"] == "revoked"
        sql, params = executed[0]
        assert "restored_by" in sql and "AND status = :old_status" in sql
        assert params["old_status"] == "revoked" and params["new_status"] == "candidate"
        # 監査は 1 行・action は identity_link.restored・前の判断の人を残す。
        assert len(audits) == 1
        assert audits[0][2:4] == ("revoked", "candidate")
        assert audits[0][5]["action"] == il.RESTORE_AUDIT_ACTION
        assert audits[0][5]["decided_by"] == "a"
        assert "candidate_flow" in inspect.getsource(il)

    def test_restore_line_and_actors(self):
        from core.deliberation import identity_links as il

        line = il.restore_line("佐藤", "2026-10-28T01:00:00", "rejected")
        assert line == "〔候補に戻した〕 戻した: 佐藤・2026-10-28（前の判断: 見送り）"
        assert il.restore_reason_line("誤り") == "候補に戻した理由: 誤り"
        assert il.decision_outcome_fact("candidate", line).startswith("結び付きを候補に戻しました")
        assert "［候補に戻す］" in il.LINK_REJECTED_FACT

    def test_route_and_ui_three_point_set(self):
        from routes import deliberation as d
        from core.help_kb import admin_ui_anchors as anchors

        src = inspect.getsource(d.restore_identity_link)
        assert "_ensure_document_editable" in src and "status_code=422" in src and "status_code=409" in src
        assert "deliberation.identity-link-restore" in anchors.KNOWN_ADMIN_UI_ANCHOR_IDS
        assert anchors.ADMIN_UI_ANCHORS["deliberation.identity-link-restore"].endswith("#identity-link-restore")
        assert "{#identity-link-restore}" in DELIB_MANUAL
        assert 'data-ui-anchor="deliberation.identity-link-restore"' in ADMIN_JS
        assert 'data-ui-anchor="deliberation.identity-link-restore"' in DELIB_JS
        assert 'data-identity-action="restore"' in DELIB_JS
        assert "share8" in ADMIN_HTML


# ===========================================================================
# IK-1063 — 関係の判断の記録と向き
# ===========================================================================


class TestRelationDecisionRecord:
    def test_direction_line_by_viewer(self):
        from core.library import schema as ls

        assert "の上位です" in ls.relation_direction_line("broader", viewer_is_subject=True, counterpart_name="X")
        assert "の下位です" in ls.relation_direction_line("broader", viewer_is_subject=False, counterpart_name="X")
        assert ls.relation_kind_label_for_viewer("broader", viewer_is_subject=False) == "下位"
        assert ls.relation_kind_label_for_viewer("broader", viewer_is_subject=True) == "上位"
        assert "向きはありません" in ls.relation_direction_line("related", viewer_is_subject=False, counterpart_name="X")

    def test_decision_lines(self):
        from core.library import schema as ls

        assert ls.relation_decision_line("confirmed", "堀", "2026-10-28T00:00:00") == "〔確定〕 確定: 堀・2026-10-28"
        assert ls.relation_decision_line("dismissed", "", None) == "〔見送り〕 見送り: 記録なし"
        assert ls.relation_decision_line("candidate", "堀", None) == ""
        assert ls.relation_decision_reason_line("confirmed", "定義から") == "確定の理由: 定義から"
        # 第 9 波（IK-1063 の残り）: 理由の無い確定も黙らない。
        assert ls.relation_decision_reason_line("confirmed", "") == ls.RELATION_CONFIRMED_REASON_UNRECORDED
        assert ls.relation_added_line("堀", "2026-10-28", "教科書") == "追加: 堀・2026-10-28（理由: 教科書）"

    def test_route_annotates_and_ui_sends_reason(self):
        from routes import library as lib

        assert "_describe_relations(relations, entry_id" in inspect.getsource(lib.list_relations)
        assert "_describe_relations(" in inspect.getsource(lib.decide_relation)
        assert 'id="library-relation-reason"' in ADMIN_JS
        assert "reason: relationReasonEl" in ADMIN_JS
        assert "r.direction_line" in ADMIN_JS and "r.decision_line" in ADMIN_JS
        assert "「下位」" in LIBRARY_MANUAL or "下位" in LIBRARY_MANUAL


# ===========================================================================
# IK-1064 — 部品の名前を画面文脈で送り、説明の出所の問いを RAG へ
# ===========================================================================


class TestPartQuestionRouting:
    def test_app_sends_element_label(self):
        assert "element_label: String(openedComponent.label" in APP_JS
        assert "selection.element_label = elementLabel.slice(0, 40)" in APP_JS
        assert "share8" in INDEX_HTML

    def _body(self, message, selection):
        from schemas import LearningChatRequest

        return LearningChatRequest(
            message=message,
            screen_context={"screen": "learning", "selection": {"course_id": "c1", **selection}},
        )

    def test_explanation_provenance_question_goes_to_rag(self):
        from routes import learning as lr

        body = self._body("この部品の説明はどの先生が書いたの？", {"element_id": "comp_005", "element_label": "TOV residual"})
        assert lr._is_screen_fact_question(body, course_id="c1") is True
        assert lr._usage_help_goes_to_rag(body, course_id="c1") is True
        # 要素を選んでいなければ従来どおり（地図の場所も無い）。
        plain = self._body("この部品の説明はどの先生が書いたの？", {})
        assert lr._is_screen_fact_question(plain, course_id="c1") is False

    def test_classifier_prompt_mentions_screen_facts(self):
        from routes import learning as lr

        assert "誰が書いたか" in inspect.getsource(lr._classify_intent)

    def test_resolver_states_missing_explanation_and_author(self):
        from core.assistant_context.resolvers import learning as res

        base = {"instance": {"component": {"label": "TOV residual"}, "in_paper": {}}}
        facts = res._component_facts(base)
        assert any("承認済み" in f and "まだありません" in f for f in facts)
        with_exp = {"instance": {"component": {"label": "TOV residual"}, "in_paper": {},
                                 "explanation": {"kind": "personal", "body": "説明本文", "author_name": "江口 大地"}}}
        facts = res._component_facts(with_exp)
        assert any("江口 大地" in f for f in facts)


# ===========================================================================
# IK-0980 — 同じ論文の別版を「別の論文」と言わない
# ===========================================================================


class TestSamePaperReason:
    def test_reword(self):
        from core.deliberation import identity_links as il

        stored = "別の論文の記述と本文の意味が近いと測定されました。"
        assert il.reworded_same_paper_reason(stored, partner_is_same_paper=True) == il.SAME_PAPER_PROXY_REASON
        assert il.reworded_same_paper_reason(stored, partner_is_same_paper=False) == ""
        assert il.reworded_same_paper_reason("ほか", partner_is_same_paper=True) == ""

    def test_wired_in_both_surfaces(self):
        from routes import deliberation as d
        from routes import library as lib

        assert "_reword_same_paper_reasons(responses)" in inspect.getsource(d.list_identity_links_for_shared_part)
        assert "reworded_same_paper_reason(" in inspect.getsource(lib)
