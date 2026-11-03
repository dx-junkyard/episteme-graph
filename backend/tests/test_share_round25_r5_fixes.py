"""第 25 周 面 A（share）第 5 波の是正（2026-10-27）。

- IK-0982 エントリの確定と結び付きの確定を分け、確定の前に問い・後に知らせる
- IK-0983 記号 → 概念の結び付きを作る入口（候補づくりの API・ライブラリの区画）
- IK-0984 著者のいない標準の説明は論文を編集できる教員が査読でき、ほかのコースへ広げるには
  もう一人の査読者が要る（オーナー判断の推奨どおり）
- IK-0985 査読済みに査読ボタンを出さない・共有の知らせは方向を言う

流儀は既存の ``test_share_round25_*`` / ``test_concept_registry_candidates_api.py``
（実 app + TestClient + monkeypatch / 静的検査）。
"""

from __future__ import annotations

import inspect
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core import explanation_governance as xgov  # noqa: E402

ADMIN_JS = (ROOT / "frontend/public/js/admin.js").read_text(encoding="utf-8")
STUDIO_JS = (ROOT / "frontend/public/js/admin-lecture-studio.js").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "frontend/public/admin.html").read_text(encoding="utf-8")
LIBRARY_MANUAL = (ROOT / "docs/manual/teacher/19-admin-knowledge-library.md").read_text(encoding="utf-8")
STUDIO_MANUAL = (ROOT / "docs/manual/teacher/14-admin-lecture-studio.md").read_text(encoding="utf-8")

AUTHOR = "aaaaaaaa-0000-0000-0000-000000000001"
OTHER = "aaaaaaaa-0000-0000-0000-000000000002"
EXP = "eeeeeeee-0000-0000-0000-000000000001"
COMP = "cccccccc-0000-0000-0000-000000000001"


def _function(src: str, name: str) -> str:
    start = src.index("function " + name + "(")
    return src[start:src.index("\n  function ", start + 10)]


def _row(*, kind="standard", author=None, review="teacher_approved", shared=False):
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    return (
        EXP, COMP, "", kind, author, "", "題", "本文", [], "", review, shared, now, now,
        0, 0, 0, 0, 0, False, False,
    )


# ===========================================================================
# IK-0984 — 標準の説明の査読者
# ===========================================================================


class TestStandardExplanationGovernance:
    def test_learner_scope_routes_standard_through_the_reviewer_rule(self):
        scope = xgov.LEARNER_EXPLANATION_SCOPE_SQL
        assert "e.kind = 'standard' AND " + xgov.STANDARD_DELIVERY_SQL in scope
        # 旧条件（標準なら無条件に全コース）に戻さない。
        assert "(e.kind = 'standard' OR" not in scope

    def test_reviewers_are_the_approval_events_of_the_current_window(self):
        sql = xgov.STANDARD_REVIEWERS_SQL
        assert "new_status = 'teacher_approved'" in sql
        # 本文の書き換え（old = new = approved）は査読に数えない。もう一人の査読者だけは数える。
        assert "rv.old_status <> 'teacher_approved'" in sql
        assert xgov.STANDARD_ADDITIONAL_REVIEW_ACTION in sql
        # 承認から外れたら数え直す（いまの承認の期間）。
        assert "lv.old_status = 'teacher_approved' AND lv.new_status <> 'teacher_approved'" in sql

    def test_delivery_needs_two_reviewers_or_the_reviewers_own_course(self):
        sql = xgov.STANDARD_DELIVERY_SQL
        assert ">= 2" in sql
        assert "COALESCE(lc.owner_id, lc.user_id) IN" in sql
        assert "lc.id = :course_id" in sql
        # 記帳の無い承認（部品の承認から引き継いだ・旧規則）は従来どおり。
        assert sql.startswith("(NOT EXISTS (")

    def test_delivery_note_for_one_reviewer(self):
        note = xgov.explanation_delivery_note
        base = dict(kind="standard", review_status="teacher_approved", shared=False,
                    has_source_course=False, non_author_reviewed=False)
        assert note(**base, standard_reviewers=1) == xgov.EXPLANATION_DELIVERY_STANDARD_ONE_REVIEWER
        assert note(**base, standard_reviewers=2) == xgov.EXPLANATION_DELIVERY_STANDARD
        assert note(**base, standard_reviewers=0) == xgov.EXPLANATION_DELIVERY_STANDARD
        assert note(**base) == xgov.EXPLANATION_DELIVERY_STANDARD

    def _out(self, viewer, **kw):
        from api.routes import theory_components as tc

        return tc._row_to_explanation_out(_row(**{k: v for k, v in kw.items() if k in ("review",)}), viewer,
                                          standard_reviewable=kw.get("reviewable", False),
                                          standard_reviewers=kw.get("reviewers"))

    def test_document_editor_can_review_but_not_edit_the_standard_body(self):
        out = self._out({"id": OTHER, "role": "teacher"}, reviewable=True, review="teacher_review_required")
        assert out.can_review is True
        assert out.can_edit is False
        assert out.edit_note == ""

    def test_teacher_who_cannot_edit_the_paper_gets_the_fact(self):
        out = self._out({"id": OTHER, "role": "teacher"}, reviewable=False)
        assert out.can_review is False
        assert out.edit_note == xgov.EXPLANATION_STANDARD_REVIEWER_NOTE
        # 存在しない「著者」を指さない。
        assert out.edit_note != xgov.EXPLANATION_AUTHOR_ONLY_NOTE

    def test_second_review_is_offered_to_a_different_reviewer_only(self):
        assert self._out({"id": OTHER, "role": "teacher"}, reviewable=True, reviewers=[AUTHOR]).second_review_available
        assert not self._out({"id": AUTHOR, "role": "teacher"}, reviewable=True, reviewers=[AUTHOR]).second_review_available
        assert not self._out({"id": OTHER, "role": "teacher"}, reviewable=True, reviewers=[AUTHOR, "x"]).second_review_available
        assert not self._out({"id": OTHER, "role": "teacher"}, reviewable=True, reviewers=[]).second_review_available
        one = self._out({"id": OTHER, "role": "teacher"}, reviewable=True, reviewers=[AUTHOR])
        assert one.delivery_note == xgov.EXPLANATION_DELIVERY_STANDARD_ONE_REVIEWER

    def test_patch_records_the_reviewer_and_keeps_the_body_admin_only(self):
        from api.routes import theory_components as tc

        src = inspect.getsource(tc.patch_explanation)
        assert "_can_review_standard(" in src
        assert "EXPLANATION_STANDARD_BODY_ADMIN_ONLY" in src
        assert "STANDARD_ADDITIONAL_REVIEW_ACTION" in src
        assert "EXPLANATION_ALREADY_REVIEWED_BY_YOU" in src
        assert "outcome_fact" in src

    def test_reviewer_permission_is_the_document_edit_gate(self):
        from api.routes import theory_components as tc

        src = inspect.getsource(tc._can_review_standard)
        assert "_ensure_component_editable(" in src
        assert "ROLE_SYSTEM_ADMIN" in src


# ===========================================================================
# IK-0985 — 状態と操作・知らせ
# ===========================================================================


class TestExplanationCardState:
    def _card(self):
        start = STUDIO_JS.index("function lsRenderExplanationCard")
        return STUDIO_JS[start:STUDIO_JS.index("function lsBindExplanationActions")]

    def test_approve_button_only_when_not_yet_approved(self):
        branch = self._card()
        branch = branch[branch.index("var editControls"):branch.index("var editNote")]
        assert 'exp.review_status !== "teacher_approved"' in branch
        assert "exp.can_review" in self._card()

    def test_second_review_button_and_anchor(self):
        card = self._card()
        assert "exp.second_review_available" in card
        assert 'data-ui-anchor="lecture-studio.explanation-second-review"' in card
        assert "EXPLANATION_ACTION_LABELS.second_review" in card

    def test_share_toggle_is_not_offered_for_standard(self):
        assert 'exp.kind !== "standard"' in self._card()

    def test_notice_prefers_the_server_outcome_fact(self):
        call = STUDIO_JS[STUDIO_JS.index("function lsCallExplanation"):]
        call = call[:call.index("\n  }\n")]
        assert "data.outcome_fact" in call
        assert "共有設定を更新しました" not in STUDIO_JS

    def test_share_facts_name_the_direction(self):
        assert "ON" in xgov.EXPLANATION_SHARED_ON_FACT
        assert "OFF" in xgov.EXPLANATION_SHARED_OFF_FACT


# ===========================================================================
# IK-0982 / IK-0917 / IK-0983 — ライブラリ
# ===========================================================================


class TestLibraryWave5Static:
    def test_entry_confirm_asks_first_and_shows_the_fact_after(self):
        render = _function(ADMIN_JS, "renderLibraryIdentityCandidates")
        assert "data-confirm-question" in render
        assert "window.confirm(question)" in render
        # 第 7 波（IK-1050）: 判断のボタンは候補のときだけ（見送り済みにも出さない）。
        assert 'entry.review_status === "candidate"' in render
        decide = _function(ADMIN_JS, "decideLibraryEntryReview")
        assert "data.facts" in decide
        assert "entry.review_confirm_question" in ADMIN_JS

    def test_link_rows_carry_reason_version_and_basis(self):
        render = _function(ADMIN_JS, "renderLibraryIdentityCandidates")
        for key in ("link.reason", "link.document_version_label", "link.basis_label", "link.instance_summary"):
            assert key in render, key
        # IK-0985 と同じ規律: 決まった行に判断のボタンを並べない。
        assert 'link.status === "candidate"' in render
        assert "_libraryConfirmIdentityLinkDialog(" in render

    def test_symbol_section_and_derive_button(self):
        assert 'data-ui-anchor="knowledge-library.symbol-derive"' in ADMIN_JS
        assert '"knowledge-library.bound-symbols"' in ADMIN_JS
        derive = _function(ADMIN_JS, "_libraryBindSymbolDerive")
        assert "/symbol-candidates/derive" in derive

    def test_cache_buster_moved(self):
        assert re.search(r"/js/admin\.js\?v=[^\"]*-share5", ADMIN_HTML)
        assert re.search(r"/js/admin-lecture-studio\.js\?v=[^\"]*-share5", ADMIN_HTML)

    def test_manual_sections_exist(self):
        for anchor in ("{#bound-part-decide}", "{#bound-symbols}", "{#symbol-derive}"):
            assert anchor in LIBRARY_MANUAL
        assert "{#explanation-second-review}" in STUDIO_MANUAL
        assert xgov.EXPLANATION_STANDARD_REVIEWER_NOTE in STUDIO_MANUAL


class TestLibraryWave5Facts:
    def test_entry_facts_have_no_numbers_and_name_the_separate_judgement(self):
        from api.routes import library as lib

        for text in (lib.ENTRY_CONFIRM_QUESTION, lib.ENTRY_CONFIRMED_FACT, lib.ENTRY_LINKS_PENDING_NOTE,
                     lib.STALE_SAME_PAPER_PROXY_NOTE, lib.SYMBOL_CANDIDATES_CREATED_FACT,
                     lib.SYMBOL_CANDIDATES_NONE_FACT):
            assert not re.search(r"\d", text)
        assert "結び付き" in lib.ENTRY_CONFIRM_QUESTION
        assert "結び付き" in lib.ENTRY_CONFIRMED_FACT

    def test_review_route_returns_facts(self):
        from api.routes import library as lib

        src = inspect.getsource(lib.review_entry)
        assert "ENTRY_CONFIRMED_FACT" in src
        assert '"facts"' in src


try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_TEACHER_ID = "99999999-9999-9999-9999-999999999999"
_ENTRY_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client_and_teacher():
    from fastapi.testclient import TestClient
    from main import app
    from dependencies import ROLE_TEACHER, _create_token

    return TestClient(app), _create_token(_TEACHER_ID, "tea", "tea@x", ROLE_TEACHER)


class _NullSession:
    def execute(self, *a, **k):  # pragma: no cover — 呼ばれたら落とす
        raise AssertionError("unexpected SQL")

    def close(self):
        pass


@pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")
class TestSymbolDeriveRoute:
    def _patch(self, monkeypatch, *, review_status="confirmed", created=1):
        from routes import library as lib
        from core.library import symbol_concepts as sc

        monkeypatch.setattr(lib, "_audit", lambda *a, **k: None)
        monkeypatch.setattr(lib, "get_session", lambda: _NullSession())
        monkeypatch.setattr(lib, "_get_entry_or_404", lambda entry_id: {
            "id": _ENTRY_ID, "name": "stellar radius", "review_status": review_status, "status": "active",
        })
        monkeypatch.setattr(lib.services, "list_visible_document_ids", lambda uid: {"d1"})
        seen = {}

        def _derive(session, *, document_ids, entry_ids=None, limit=20):
            seen["docs"] = list(document_ids)
            seen["entries"] = list(entry_ids or [])
            return {"symbols_scanned": 3, "links_created": created, "truncated": False}

        monkeypatch.setattr(sc, "derive_symbol_concept_candidates", _derive)
        return lib, seen

    def test_creates_candidates_for_visible_documents_only(self, client_and_teacher, monkeypatch):
        lib, seen = self._patch(monkeypatch)
        client, token = client_and_teacher
        res = client.post(f"/api/admin/library/entries/{_ENTRY_ID}/symbol-candidates/derive",
                          headers={"Authorization": "Bearer " + token})
        assert res.status_code == 200
        assert res.json()["facts"] == [lib.SYMBOL_CANDIDATES_CREATED_FACT]
        assert seen == {"docs": ["d1"], "entries": [_ENTRY_ID]}

    def test_none_found_says_so_without_numbers(self, client_and_teacher, monkeypatch):
        lib, _seen = self._patch(monkeypatch, created=0)
        client, token = client_and_teacher
        res = client.post(f"/api/admin/library/entries/{_ENTRY_ID}/symbol-candidates/derive",
                          headers={"Authorization": "Bearer " + token})
        assert res.json() == {"facts": [lib.SYMBOL_CANDIDATES_NONE_FACT]}

    def test_candidate_entry_is_409(self, client_and_teacher, monkeypatch):
        lib, _seen = self._patch(monkeypatch, review_status="candidate")
        client, token = client_and_teacher
        res = client.post(f"/api/admin/library/entries/{_ENTRY_ID}/symbol-candidates/derive",
                          headers={"Authorization": "Bearer " + token})
        assert res.status_code == 409
        assert res.json()["detail"] == lib.SYMBOL_CANDIDATES_NOT_CONFIRMED_DETAIL
