"""第 25 周・面 A 第 3 周の是正 — 説明カード系（IK-0923〜IK-0926）。

DB へ行かない単体・静的検査で、是正の振る舞いを固定する:

- IK-0926 支持の人数から著者を除く（記録は残す）・コースをまたぐ配信は著者以外の査読承認が要件・
  学生向けの段階ラベルの定義を制度指標カタログに宣言
- IK-0924 学生への公開（査読）と教員の支持を別の語で言う（label_vocab 正本 + JS 逐語ミラー）・
  引用は件数でなく題名・意図・引用者の列挙
- IK-0925 説明一覧の各行に can_edit。著者でない教員には公開・共有の操作を出さず事実文
- IK-0923 標準の説明の本文を部品の要約へ追随させ、旧本文は監査に退避・既存の査読・支持は事実文
"""

from __future__ import annotations

import inspect
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
for path in (str(BACKEND), str(BACKEND / "api")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import explanation_governance as xgov  # noqa: E402
from core import indicator_catalog as ic  # noqa: E402
from core import label_vocab  # noqa: E402

STUDIO_JS = (REPO / "frontend/public/js/admin-lecture-studio.js").read_text(encoding="utf-8")
STUDENT_SPEC = (REPO / "docs/manual/student/01-specification.md").read_text(encoding="utf-8")
TEACHER_STUDIO = (REPO / "docs/manual/teacher/14-admin-lecture-studio.md").read_text(encoding="utf-8")
ADMIN_HTML = (REPO / "frontend/public/admin.html").read_text(encoding="utf-8")

EXP = "44444444-4444-4444-8444-444444444444"
COMP = "33333333-3333-4333-8333-333333333333"
AUTHOR = "55555555-5555-4555-8555-555555555555"
OTHER = "66666666-6666-4666-8666-666666666666"


class _Rows:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar


class _ScriptedSession:
    """SQL の部分文字列で応答を選ぶフェイク（最初に一致した規則を使う）。"""

    def __init__(self, rules):
        self.rules = rules
        self.executed: list[tuple[str, dict]] = []
        self.committed = False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append((sql, dict(params or {})))
        for needle, result in self.rules:
            if needle in sql:
                return result(params or {}) if callable(result) else result
        return _Rows()

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


def _explanation_row(*, kind="personal", author=AUTHOR, course="c1", review="teacher_approved",
                     shared=True, endorsers=(1, 0, 0, 0), author_endorsed=False, non_author_reviewed=False):
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    return (
        EXP, COMP, course, kind, author, "著者さん", "題", "本文", [], "", review, shared, now, now,
        endorsers[0], endorsers[1], endorsers[2], endorsers[3], 2, author_endorsed, non_author_reviewed,
    )


# ===========================================================================
# IK-0926
# ===========================================================================


class TestCountedEndorsements:
    def test_summary_excludes_author_and_revoked(self):
        sql = xgov.COUNTED_ENDORSEMENT_SUMMARY_SQL
        assert "NOT en.revoked" in sql
        assert "en.endorser_id IS DISTINCT FROM ex.author_id" in sql
        for col in ("endorser_count", "strong_count", "provisional_count", "expertise_breadth"):
            assert col in sql

    def test_every_reader_uses_the_counted_summary_not_the_raw_view(self):
        readers = [
            BACKEND / "api/routes/theory_components.py",
            BACKEND / "api/routes/learning.py",
            BACKEND / "core/atlas_state.py",
            BACKEND / "core/deliberation/positioning.py",
            BACKEND / "core/doubt/ledger_builder.py",
        ]
        for path in readers:
            src = path.read_text(encoding="utf-8")
            code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
            assert not re.search(r"JOIN\s+component_explanation_endorsement_summary", code), path
            assert not re.search(r"FROM\s+component_explanation_endorsement_summary", code), path
            assert "COUNTED_ENDORSEMENT_SUMMARY_SQL" in src, path

    def test_endorsement_row_is_still_written_for_the_author(self):
        from api.routes import theory_components as tc

        src = inspect.getsource(tc.endorse_explanation)
        # 著者本人の支持を拒まない（記録は残す — 数えないのは集計側）。
        assert "INSERT INTO component_endorsements" in src
        assert "403" not in src

    def test_author_endorser_is_marked_in_the_list(self):
        from api.routes import theory_components as tc

        src = inspect.getsource(tc.list_endorsements)
        assert "is_author=" in src
        assert "EXPLANATION_AUTHOR_ENDORSER_ROLE_LABEL" in src
        assert "EXPLANATION_AUTHOR_SUPPORT_NOT_COUNTED_NOTE" in src


class TestCrossCourseDelivery:
    def test_scope_requires_non_author_review_for_shared(self):
        scope = xgov.LEARNER_EXPLANATION_SCOPE_SQL
        assert "e.review_status = 'teacher_approved'" in scope
        assert "e.kind = 'standard'" in scope
        assert "e.course_id = :course_id" in scope
        assert "e.shared = TRUE AND" in scope
        assert xgov.NON_AUTHOR_REVIEWED_SQL in scope
        # 旧条件（共有 ON なら誰の査読でも届く）に戻さない。
        assert "OR e.shared = TRUE OR" not in scope

    def test_non_author_review_is_fail_closed(self):
        sql = xgov.NON_AUTHOR_REVIEWED_SQL
        assert "theory_review_events" in sql
        assert "new_status = 'teacher_approved'" in sql
        assert "ORDER BY ev.created_at DESC LIMIT 1" in sql
        # 記帳が無い（''）・記帳者が著者 → 偽。
        assert "NOT IN ('', COALESCE(e.author_id::text, ''))" in sql

    def test_learning_routes_use_the_governance_scope(self):
        from api.routes import learning as learning_mod

        assert learning_mod._LEARNER_EXPLANATION_SCOPE_SQL is xgov.LEARNER_EXPLANATION_SCOPE_SQL

    def test_learner_response_names_the_indicator(self):
        from api.routes import learning as learning_mod

        src = inspect.getsource(learning_mod.get_component_explanations_for_learner)
        assert '"indicator_id": "explanation-support-label"' in src

    def test_delivery_note_follows_the_same_conditions(self):
        note = xgov.explanation_delivery_note
        base = dict(kind="personal", review_status="teacher_approved", has_source_course=True)
        assert note(**base, shared=True, non_author_reviewed=False) == xgov.EXPLANATION_DELIVERY_SELF_REVIEWED
        assert note(**base, shared=True, non_author_reviewed=True) == xgov.EXPLANATION_DELIVERY_CROSS_COURSE
        assert note(**base, shared=False, non_author_reviewed=True) == xgov.EXPLANATION_DELIVERY_OWN_COURSE_ONLY
        assert note(kind="personal", review_status="teacher_review_required", shared=True,
                    has_source_course=True, non_author_reviewed=True) == xgov.EXPLANATION_DELIVERY_NOT_APPROVED
        assert note(kind="standard", review_status="teacher_approved", shared=False,
                    has_source_course=False, non_author_reviewed=False) == xgov.EXPLANATION_DELIVERY_STANDARD
        assert note(kind="personal", review_status="teacher_approved", shared=True,
                    has_source_course=False, non_author_reviewed=False) == xgov.EXPLANATION_DELIVERY_NO_SOURCE_COURSE

    def test_facts_carry_no_numbers(self):
        for name in dir(xgov):
            value = getattr(xgov, name)
            if name.startswith("EXPLANATION_") and isinstance(value, str):
                assert not re.search(r"\d", value), name


class TestIndicatorDeclared:
    def test_spec_is_declared(self):
        spec = ic.get_indicator("explanation-support-label")
        assert spec is not None
        assert "説明を書いた本人を除き" in spec.definition
        assert "取り消した人を除いた" in spec.definition
        assert spec.route == "/api/learning/courses/{course_id}/components/{component_id}/explanations"
        assert spec.granularity == ic.GRANULARITY_PER_ITEM_ATTRIBUTED

    def test_label_is_in_the_student_manual(self):
        spec = ic.get_indicator("explanation-support-label")
        assert spec.label in STUDENT_SPEC


# ===========================================================================
# IK-0924
# ===========================================================================


def _js_table(name: str) -> dict[str, str]:
    marker = "var " + name + " = {"
    assert marker in STUDIO_JS, name
    start = STUDIO_JS.index(marker)
    end = STUDIO_JS.index("\n  };", start)
    block = STUDIO_JS[start + len(marker):end]
    return dict(re.findall(r"([A-Za-z_]+)\s*:\s*\"([^\"]*)\"", block))


class TestTwoAxesVocabulary:
    MIRRORED = (
        "EXPLANATION_AXIS_LABELS",
        "EXPLANATION_REVIEW_STATUS_LABELS",
        "EXPLANATION_ACTION_LABELS",
        "EXPLANATION_SUPPORT_LEVEL_LABELS",
    )

    def test_js_mirrors_label_vocab_verbatim(self):
        for name in self.MIRRORED:
            assert _js_table(name) == dict(getattr(label_vocab, name)), name

    def test_review_vocabulary_matches_the_check_constraint(self):
        assert set(label_vocab.EXPLANATION_REVIEW_STATUS_LABELS) == {
            "teacher_review_required", "teacher_approved", "needs_revision", "rejected",
        }

    def test_support_label_uses_the_support_word(self):
        f = label_vocab.explanation_support_label
        assert f({"endorser_count": 0}) == label_vocab.EXPLANATION_SUPPORT_LABEL_NONE
        assert f({"endorser_count": 1, "provisional_count": 1}) == "暫定的に1名の教員が支持"
        assert f({"endorser_count": 3, "strong_count": 1, "expertise_breadth": 2}) == "強い支持: 3名の教員が支持(専門2分野)"
        for value in (f({"endorser_count": 0}), f({"endorser_count": 2})):
            assert "承認" not in value

    def test_route_label_delegates_to_the_canon(self):
        from api.routes import theory_components as tc

        assert "explanation_support_label(summary)" in inspect.getsource(tc._endorsement_label)

    def test_card_uses_two_axes_and_separate_buttons(self):
        src = STUDIO_JS[STUDIO_JS.index("function lsRenderExplanationCard"):]
        src = src[:src.index("function lsBindExplanationActions")]
        assert "EXPLANATION_AXIS_LABELS.support" in src
        assert "EXPLANATION_AXIS_LABELS.review" in src
        assert "EXPLANATION_ACTION_LABELS.approve" in src
        assert "EXPLANATION_ACTION_LABELS.endorse" in src
        assert "査読承認" not in src
        assert ">承認する<" not in src
        # 引用は件数を描かず、列挙する。
        assert "citation_count" not in src
        assert "lsRenderExplanationCitations(exp)" in src

    def test_citations_are_listed_by_course_title_and_intent(self):
        src = STUDIO_JS[STUDIO_JS.index("function lsRenderExplanationCitations"):]
        src = src[:src.index("function lsRenderExplanationCard")]
        for key in ("course_title", "citation_intent_label", "citer_name", "cited_at"):
            assert key in src

    def test_list_endpoint_attaches_citations(self):
        from api.routes import theory_components as tc

        session = _ScriptedSession([
            ("FROM component_citations ci", _Rows([
                (EXP, "宇宙物理入門", "uses_method_in", "教員B", datetime(2026, 10, 23, tzinfo=timezone.utc)),
            ])),
        ])
        with patch.object(tc, "_pg_session", return_value=session):
            out = tc._citations_for_explanations([EXP])
        assert out[EXP][0]["course_title"] == "宇宙物理入門"
        assert out[EXP][0]["citer_name"] == "教員B"
        assert out[EXP][0]["cited_at"].startswith("2026-10-23")
        assert "citation_intent_label" in out[EXP][0]

    def test_admin_html_cache_buster_moved(self):
        assert "admin-lecture-studio.js?v=round25-share-r3-20261023-1" in ADMIN_HTML

    def test_teacher_manual_names_both_axes(self):
        for phrase in ("学生への公開", "教員の支持", "学生に公開してよいと査読する", "支持する",
                       xgov.EXPLANATION_AUTHOR_ONLY_NOTE, xgov.EXPLANATION_AUTHOR_SUPPORT_NOT_COUNTED_NOTE):
            assert phrase in TEACHER_STUDIO, phrase


# ===========================================================================
# IK-0925
# ===========================================================================


class TestCanEdit:
    def _out(self, viewer, **kw):
        from api.routes import theory_components as tc

        return tc._row_to_explanation_out(_explanation_row(**kw), viewer)

    def test_author_can_edit(self):
        out = self._out({"id": AUTHOR, "role": "teacher"})
        assert out.can_edit is True
        assert out.edit_note == ""

    def test_other_teacher_cannot_edit_and_gets_the_fact(self):
        out = self._out({"id": OTHER, "role": "teacher"})
        assert out.can_edit is False
        assert out.edit_note == xgov.EXPLANATION_AUTHOR_ONLY_NOTE

    def test_system_admin_can_edit(self):
        from dependencies import ROLE_SYSTEM_ADMIN

        assert self._out({"id": OTHER, "role": ROLE_SYSTEM_ADMIN}).can_edit is True

    def test_standard_explanation_is_not_teacher_editable(self):
        assert self._out({"id": OTHER, "role": "teacher"}, kind="standard", author=None).can_edit is False

    def test_card_hides_author_only_actions(self):
        src = STUDIO_JS[STUDIO_JS.index("function lsRenderExplanationCard"):]
        src = src[:src.index("function lsBindExplanationActions")]
        assert "exp.can_edit" in src
        assert "exp.edit_note" in src
        # 公開・共有の操作は can_edit の分岐の中にだけある。
        branch = src[src.index("var editControls"):src.index("var editNote")]
        assert 'data-exp-action="approve"' in branch
        assert 'data-exp-action="share"' in branch
        rest = src.replace(branch, "")
        assert 'data-exp-action="approve"' not in rest
        assert 'data-exp-action="share"' not in rest
        assert 'data-exp-action="endorse"' in rest
        assert 'data-exp-action="cite"' in rest

    def test_support_and_delivery_notes(self):
        out = self._out({"id": AUTHOR, "role": "teacher"}, author_endorsed=True, non_author_reviewed=False)
        assert out.support_note == xgov.EXPLANATION_AUTHOR_SUPPORT_NOT_COUNTED_NOTE
        assert out.delivery_note == xgov.EXPLANATION_DELIVERY_SELF_REVIEWED
        out = self._out({"id": AUTHOR, "role": "teacher"}, non_author_reviewed=True)
        assert out.support_note == ""
        assert out.delivery_note == xgov.EXPLANATION_DELIVERY_CROSS_COURSE


# ===========================================================================
# IK-0923
# ===========================================================================


class TestStandardExplanationFollowsSummary:
    def _component(self, summary="新しい要約", name="部品"):
        return SimpleNamespace(id=COMP, summary=summary, name=name, review_status="teacher_approved", course_id="")

    def test_body_is_refreshed_and_old_body_kept_in_audit(self):
        from api.routes import theory_components as tc

        sessions = [
            _ScriptedSession([("FROM theory_review_events", _Rows())]),  # 人の編集の記帳なし
            _ScriptedSession([]),  # UPDATE
        ]
        events = []
        with patch.object(tc, "_pg_session", side_effect=sessions), patch.object(
            tc, "_record_review_event", side_effect=lambda *a: events.append(a)
        ):
            changed = tc._refresh_standard_explanation_body(
                self._component(), (EXP, "部品", "Reusable theory unit ...", "teacher_approved")
            )
        assert changed is True
        update_sql, params = sessions[1].executed[0]
        assert "UPDATE component_explanations SET body = :body" in update_sql
        assert params["body"] == "新しい要約"
        assert sessions[1].committed
        assert len(events) == 1
        meta = events[0][5]
        assert meta["action"] == xgov.STANDARD_BODY_REFRESHED_ACTION
        assert meta["previous_body"] == "Reusable theory unit ..."
        # 査読の状態は変えない（old == new）。
        assert events[0][2] == events[0][3] == "teacher_approved"

    def test_no_refresh_when_unchanged_or_empty(self):
        from api.routes import theory_components as tc

        with patch.object(tc, "_pg_session", side_effect=AssertionError("DB を呼ばない")):
            assert tc._refresh_standard_explanation_body(
                self._component(summary="同じ"), (EXP, "部品", "同じ", "teacher_approved")
            ) is False
            assert tc._refresh_standard_explanation_body(
                self._component(summary=""), (EXP, "部品", "旧", "teacher_approved")
            ) is False

    def test_no_refresh_after_a_person_edited_the_body(self):
        from api.routes import theory_components as tc

        session = _ScriptedSession([("FROM theory_review_events", _Rows([(1,)]))])
        with patch.object(tc, "_pg_session", return_value=session), patch.object(
            tc, "_record_review_event", side_effect=AssertionError("記帳しない")
        ):
            assert tc._refresh_standard_explanation_body(
                self._component(), (EXP, "部品", "教員が直した本文", "teacher_approved")
            ) is False

    def test_ensure_calls_refresh_for_existing_row(self):
        from api.routes import theory_components as tc

        session = _ScriptedSession([("kind = 'standard'", _Rows([(EXP, "部品", "旧", "teacher_approved")]))])
        with patch.object(tc, "_pg_session", return_value=session), patch.object(
            tc, "_refresh_standard_explanation_body", return_value=True
        ) as refresh:
            assert tc._ensure_standard_explanation(self._component()) is True
        assert refresh.called

    def test_prior_approval_note(self):
        from api.routes import theory_components as tc

        at = datetime(2026, 10, 23, tzinfo=timezone.utc)
        session = _ScriptedSession([
            ("SELECT MAX(created_at)", _Rows(scalar=at)),
            ("FROM component_endorsements", _Rows([(1,)])),
            ("new_status = 'teacher_approved'", _Rows()),
        ])
        with patch.object(tc, "_pg_session", return_value=session):
            assert tc._standard_body_note(EXP, "teacher_approved") == xgov.STANDARD_BODY_PRIOR_APPROVAL_NOTE

    def test_no_note_without_refresh(self):
        from api.routes import theory_components as tc

        session = _ScriptedSession([("SELECT MAX(created_at)", _Rows(scalar=None))])
        with patch.object(tc, "_pg_session", return_value=session):
            assert tc._standard_body_note(EXP, "teacher_approved") == ""

    def test_no_note_when_reviewed_again_after_refresh(self):
        from api.routes import theory_components as tc

        at = datetime(2026, 10, 23, tzinfo=timezone.utc)
        session = _ScriptedSession([
            ("SELECT MAX(created_at)", _Rows(scalar=at)),
            ("FROM component_endorsements", _Rows()),
            ("new_status = 'teacher_approved'", _Rows([(1,)])),
        ])
        with patch.object(tc, "_pg_session", return_value=session):
            assert tc._standard_body_note(EXP, "teacher_approved") == ""

    def test_patch_records_body_edits(self):
        from api.routes import theory_components as tc

        src = inspect.getsource(tc.patch_explanation)
        assert "EXPLANATION_BODY_EDIT_ACTION" in src

    def test_list_endpoint_sets_body_note_for_standard(self):
        from api.routes import theory_components as tc

        src = inspect.getsource(tc.list_component_explanations)
        assert "_standard_body_note(" in src
        assert "_citations_for_explanations(" in src
        assert "_row_to_explanation_out(" in src
        # IK-0984: 標準の説明の査読者と査読できるかを一覧と同じ規則で渡す。
        assert "standard_reviewable=" in src and "_standard_reviewers(" in src
