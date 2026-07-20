"""横断インボックス fan-out（N14）のテスト。

対象: `core/status/cross_layer_notify.py`（共通ヘルパー）と、それを呼ぶ4箇所
（C層 endorse_explanation / D層 create_challenge / R層 patch_item / L層 retire_entry）。

DB 接続は行わず、`_pg_session` / `get_session` をフェイクセッションで差し替えて、
「通知が入る／自己操作では入らない／fan-out 失敗でも本処理は成功する」を検証する
（test_reconstruction_api.py の `route_mod` monkeypatch 流儀に合わせる）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# 共通フェイク
# ---------------------------------------------------------------------------


class _FakeFetchOne:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def scalar(self):
        return self._row[0] if self._row else None


class _ScriptedSession:
    """execute() の呼び出し順に応じて fetchone 結果を返すフェイクセッション。

    rows のリストを順番に消費する（1回目の execute には rows[0]、2回目には
    rows[1]、…）。リストが尽きたら None を返す。
    """

    def __init__(self, rows: list):
        self._rows = list(rows)
        self.commit_called = False
        self.rollback_called = False
        self.closed = False

    def execute(self, *a, **k):
        row = self._rows.pop(0) if self._rows else None
        return _FakeFetchOne(row)

    def commit(self):
        self.commit_called = True

    def rollback(self):
        self.rollback_called = True

    def close(self):
        self.closed = True


class _RaisingSession:
    """execute() が例外を投げる（cross_layer_notify の best-effort を検証するため）。"""

    def execute(self, *a, **k):
        raise RuntimeError("boom")

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------------------
# core/status/cross_layer_notify.py
# ---------------------------------------------------------------------------


class TestCrossLayerNotifyCore:
    def test_notify_user_inserts_with_status_source(self, monkeypatch):
        from core.status import cross_layer_notify as notif

        captured = {}
        session = _ScriptedSession([None])

        def _fake_execute(stmt, params=None):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _FakeFetchOne(None)

        session.execute = _fake_execute
        import core.postgres

        monkeypatch.setattr(core.postgres, "get_session", lambda: session)

        ok = notif.notify_user(
            "user-1", notif.NOTIF_EXPLANATION_ENDORSED, "explanation", "expl-1", {"k": "v"},
        )
        assert ok is True
        assert session.commit_called is True
        assert "user_notifications" in captured["sql"]
        assert "'status'" in captured["sql"]
        assert captured["params"]["uid"] == "user-1"
        assert captured["params"]["kind"] == notif.NOTIF_EXPLANATION_ENDORSED

    def test_notify_user_no_recipient_is_noop(self, monkeypatch):
        from core.status import cross_layer_notify as notif

        called = []
        import core.postgres

        monkeypatch.setattr(core.postgres, "get_session", lambda: called.append("should not be called"))
        assert notif.notify_user(None, notif.NOTIF_EXPLANATION_ENDORSED, "explanation", "e1") is False
        assert notif.notify_user("", notif.NOTIF_EXPLANATION_ENDORSED, "explanation", "e1") is False
        assert called == []

    def test_notify_user_rejects_unknown_kind(self, monkeypatch):
        from core.status import cross_layer_notify as notif

        called = []
        import core.postgres

        monkeypatch.setattr(core.postgres, "get_session", lambda: called.append("should not be called"))
        assert notif.notify_user("user-1", "not_a_real_kind", "explanation", "e1") is False
        assert called == []

    def test_notify_user_swallows_db_errors(self, monkeypatch):
        from core.status import cross_layer_notify as notif

        import core.postgres

        monkeypatch.setattr(core.postgres, "get_session", lambda: _RaisingSession())
        # 例外を投げず False を返す（best-effort）。
        assert notif.notify_user("user-1", notif.NOTIF_EXPLANATION_ENDORSED, "explanation", "e1") is False

    def test_vocab_is_the_v1_kinds(self):
        """v1 の4種（C/D/R/L層）+ atlas_binding_lifecycle_design.md §3.4 で追加された
        2種（分野の地図: 骨格凍結 / ドメイン retire）。"""
        from core.status import cross_layer_notify as notif

        assert set(notif.CROSS_LAYER_NOTIFICATION_KINDS) == {
            notif.NOTIF_EXPLANATION_ENDORSED,
            notif.NOTIF_LEDGER_TARGET_CHALLENGED,
            notif.NOTIF_RECONSTRUCTION_ITEM_FLAGGED,
            notif.NOTIF_LIBRARY_ENTRY_RETIRED,
            notif.NOTIF_ATLAS_SKELETON_FROZEN,
            notif.NOTIF_ATLAS_DOMAIN_RETIRED,
        }

    def test_cross_layer_kinds_disjoint_from_status_layer_kinds(self):
        """watcher 専用の6種（NOTIFICATION_KINDS）とは別語彙であること。"""
        from core.status import cross_layer_notify as notif
        from core.status import schema as status_schema

        assert not (set(notif.CROSS_LAYER_NOTIFICATION_KINDS) & set(status_schema.NOTIFICATION_KINDS))


# ---------------------------------------------------------------------------
# C層: endorse_explanation
# ---------------------------------------------------------------------------


class TestEndorsementNotification:
    def _setup(self, monkeypatch, *, author_id, inserted, notify_raises=False):
        import routes.theory_components as tc

        monkeypatch.setattr(tc, "_explanation_context", lambda eid: ("comp-1", "course-1", author_id, False, "teacher_review_required"))
        monkeypatch.setattr(tc, "_ensure_viewable", lambda cid, user: None)
        monkeypatch.setattr(tc, "_pg_session", lambda: _ScriptedSession([(inserted,)]))
        monkeypatch.setattr(tc, "_record_review_event", lambda *a, **k: None)
        monkeypatch.setattr(
            tc, "_get_explanation_out",
            lambda eid: tc.ExplanationOut(id=eid, component_id="comp-1", course_id="course-1", kind="standard"),
        )
        calls = []

        def _notify(*a, **k):
            if notify_raises:
                raise RuntimeError("notify boom")
            calls.append(a)
            return True

        monkeypatch.setattr(tc.cross_layer_notify, "notify_user", _notify)
        return tc, calls

    def test_new_endorsement_notifies_author(self, monkeypatch):
        tc, calls = self._setup(monkeypatch, author_id="author-1", inserted=True)
        body = tc.EndorsementRequest(level="endorsed")
        out = tc.endorse_explanation("expl-1", body, {"id": "endorser-1", "role": "TEACHER"})
        assert out.id == "expl-1"
        assert len(calls) == 1
        recipient, kind, entity_type, entity_id = calls[0][:4]
        assert recipient == "author-1"
        assert kind == tc.cross_layer_notify.NOTIF_EXPLANATION_ENDORSED
        assert entity_type == "explanation"
        assert entity_id == "expl-1"

    def test_self_endorsement_does_not_notify(self, monkeypatch):
        tc, calls = self._setup(monkeypatch, author_id="same-user", inserted=True)
        body = tc.EndorsementRequest(level="endorsed")
        tc.endorse_explanation("expl-1", body, {"id": "same-user", "role": "TEACHER"})
        assert calls == []

    def test_standard_explanation_without_author_does_not_notify(self, monkeypatch):
        tc, calls = self._setup(monkeypatch, author_id="", inserted=True)
        body = tc.EndorsementRequest(level="endorsed")
        tc.endorse_explanation("expl-1", body, {"id": "endorser-1", "role": "TEACHER"})
        assert calls == []

    def test_re_endorsement_upsert_does_not_notify_again(self, monkeypatch):
        """ON CONFLICT で UPDATE に倒れた場合（xmax<>0）は再通知しない。"""
        tc, calls = self._setup(monkeypatch, author_id="author-1", inserted=False)
        body = tc.EndorsementRequest(level="strong")
        tc.endorse_explanation("expl-1", body, {"id": "endorser-1", "role": "TEACHER"})
        assert calls == []

    def test_notify_failure_does_not_break_endorsement(self, monkeypatch):
        tc, _calls = self._setup(monkeypatch, author_id="author-1", inserted=True, notify_raises=True)
        body = tc.EndorsementRequest(level="endorsed")
        out = tc.endorse_explanation("expl-1", body, {"id": "endorser-1", "role": "TEACHER"})
        assert out.id == "expl-1"


# ---------------------------------------------------------------------------
# D層: create_challenge
# ---------------------------------------------------------------------------


class TestChallengeNotification:
    def _setup(self, monkeypatch, *, recorder_id, notify_raises=False):
        import routes.doubt as doubt_routes

        monkeypatch.setattr(doubt_routes, "_pg_session", lambda: _ScriptedSession([("challenge-1",)]))
        monkeypatch.setattr(doubt_routes, "_record_doubt_event", lambda *a, **k: None)
        monkeypatch.setattr(doubt_routes, "_challenge_target_recorder", lambda ttype, tid: recorder_id)
        calls = []

        def _notify(*a, **k):
            if notify_raises:
                raise RuntimeError("notify boom")
            calls.append(a)
            return True

        monkeypatch.setattr(doubt_routes.cross_layer_notify, "notify_user", _notify)
        return doubt_routes, calls

    def test_challenge_notifies_target_recorder(self, monkeypatch):
        doubt_routes, calls = self._setup(monkeypatch, recorder_id="recorder-1")
        body = doubt_routes.ChallengeCreateRequest(challenge_type="scope_extrapolation", reason="なぜこの前提で一般化できるのか")
        out = doubt_routes.create_challenge("claim", "claim-1", body, {"id": "challenger-1", "role": "TEACHER"})
        assert out["challenge_id"] == "challenge-1"
        assert len(calls) == 1
        recipient, kind, target_type, target_id = calls[0][:4]
        assert recipient == "recorder-1"
        assert kind == doubt_routes.cross_layer_notify.NOTIF_LEDGER_TARGET_CHALLENGED
        assert target_type == "claim"
        assert target_id == "claim-1"

    def test_self_challenge_does_not_notify(self, monkeypatch):
        doubt_routes, calls = self._setup(monkeypatch, recorder_id="challenger-1")
        body = doubt_routes.ChallengeCreateRequest(challenge_type="hidden_lemma", reason="自分で記帳した前提に自分で疑義")
        doubt_routes.create_challenge("assumption", "assump-1", body, {"id": "challenger-1", "role": "TEACHER"})
        assert calls == []

    def test_no_recorder_does_not_notify(self, monkeypatch):
        doubt_routes, calls = self._setup(monkeypatch, recorder_id=None)
        body = doubt_routes.ChallengeCreateRequest(challenge_type="definitional", reason="定義が循環している")
        doubt_routes.create_challenge("claim", "claim-2", body, {"id": "challenger-1", "role": "TEACHER"})
        assert calls == []

    def test_notify_failure_does_not_break_challenge_creation(self, monkeypatch):
        doubt_routes, _calls = self._setup(monkeypatch, recorder_id="recorder-1", notify_raises=True)
        body = doubt_routes.ChallengeCreateRequest(challenge_type="untested_in_domain", reason="この領域では未検証")
        out = doubt_routes.create_challenge("claim", "claim-3", body, {"id": "challenger-1", "role": "TEACHER"})
        assert out["ok"] is True


class TestChallengeTargetRecorder:
    def test_assumption_prefers_confirmed_by(self, monkeypatch):
        import routes.doubt as doubt_routes

        session = _ScriptedSession([("confirmer-1", "creator-1")])
        monkeypatch.setattr(doubt_routes, "_pg_session", lambda: session)
        assert doubt_routes._challenge_target_recorder("assumption", "a1") == "confirmer-1"

    def test_assumption_falls_back_to_created_by(self, monkeypatch):
        import routes.doubt as doubt_routes

        session = _ScriptedSession([(None, "creator-1")])
        monkeypatch.setattr(doubt_routes, "_pg_session", lambda: session)
        assert doubt_routes._challenge_target_recorder("assumption", "a1") == "creator-1"

    def test_assumption_missing_row_returns_none(self, monkeypatch):
        import routes.doubt as doubt_routes

        monkeypatch.setattr(doubt_routes, "_pg_session", lambda: _ScriptedSession([None]))
        assert doubt_routes._challenge_target_recorder("assumption", "missing") is None

    def test_claim_uses_most_recent_scope_recorded_by(self, monkeypatch):
        import routes.doubt as doubt_routes

        scopes = [
            {"scope_id": "s1", "recorded_by": "teacher-old"},
            {"scope_id": "s2", "recorded_by": "teacher-new"},
        ]
        session = _ScriptedSession([(scopes,)])
        monkeypatch.setattr(doubt_routes, "_pg_session", lambda: session)
        assert doubt_routes._challenge_target_recorder("claim", "c1") == "teacher-new"

    def test_claim_with_empty_scopes_returns_none(self, monkeypatch):
        """空欄スコープは D層の正常状態（エラーではない）— 通知なしで fail-closed。"""
        import routes.doubt as doubt_routes

        session = _ScriptedSession([([],)])
        monkeypatch.setattr(doubt_routes, "_pg_session", lambda: session)
        assert doubt_routes._challenge_target_recorder("claim", "c1") is None


# ---------------------------------------------------------------------------
# R層: patch_item (flagged 遷移)
# ---------------------------------------------------------------------------


class TestReconstructionFlaggedNotification:
    def _setup(self, monkeypatch, *, old_status, new_status, owner_id, notify_raises=False):
        import routes.reconstruction as route_mod

        row = (old_status, "claim-1", "doc-1")
        monkeypatch.setattr(route_mod, "_pg_session", lambda: _ScriptedSession([row, None]))
        monkeypatch.setattr(route_mod, "_record_recon_event", lambda *a, **k: None)
        monkeypatch.setattr(route_mod, "_resolve_document", lambda ref: {"uploaded_by": owner_id} if owner_id is not None else None)
        calls = []

        def _notify(*a, **k):
            if notify_raises:
                raise RuntimeError("notify boom")
            calls.append(a)
            return True

        monkeypatch.setattr(route_mod.cross_layer_notify, "notify_user", _notify)
        body = route_mod.ItemPatchRequest(status=new_status)
        return route_mod, body, calls

    def test_flagged_transition_notifies_document_owner(self, monkeypatch):
        route_mod, body, calls = self._setup(monkeypatch, old_status="auto", new_status="flagged", owner_id="owner-1")
        out = route_mod.patch_item("item-1", body, {"id": "teacher-1", "role": "TEACHER"})
        assert out["status"] == "flagged"
        assert len(calls) == 1
        recipient, kind, entity_type, entity_id = calls[0][:4]
        assert recipient == "owner-1"
        assert kind == route_mod.cross_layer_notify.NOTIF_RECONSTRUCTION_ITEM_FLAGGED
        assert entity_type == "reconstruction_item"
        assert entity_id == "item-1"

    def test_actor_is_owner_does_not_notify(self, monkeypatch):
        route_mod, body, calls = self._setup(monkeypatch, old_status="auto", new_status="flagged", owner_id="teacher-1")
        route_mod.patch_item("item-1", body, {"id": "teacher-1", "role": "TEACHER"})
        assert calls == []

    def test_non_flagged_transition_does_not_notify(self, monkeypatch):
        route_mod, body, calls = self._setup(monkeypatch, old_status="auto", new_status="confirmed", owner_id="owner-1")
        route_mod.patch_item("item-1", body, {"id": "teacher-1", "role": "TEACHER"})
        assert calls == []

    def test_no_status_change_does_not_notify(self, monkeypatch):
        route_mod, _body, calls = self._setup(monkeypatch, old_status="flagged", new_status="flagged", owner_id="owner-1")
        # status を変えない patch（prompt のみ更新）
        body2 = route_mod.ItemPatchRequest(prompt="new prompt text")
        route_mod.patch_item("item-1", body2, {"id": "teacher-1", "role": "TEACHER"})
        assert calls == []

    def test_missing_document_does_not_notify_or_raise(self, monkeypatch):
        route_mod, body, calls = self._setup(monkeypatch, old_status="auto", new_status="flagged", owner_id=None)
        out = route_mod.patch_item("item-1", body, {"id": "teacher-1", "role": "TEACHER"})
        assert out["status"] == "flagged"
        assert calls == []

    def test_notify_failure_does_not_break_patch(self, monkeypatch):
        route_mod, body, _calls = self._setup(
            monkeypatch, old_status="auto", new_status="flagged", owner_id="owner-1", notify_raises=True,
        )
        out = route_mod.patch_item("item-1", body, {"id": "teacher-1", "role": "TEACHER"})
        assert out["status"] == "flagged"


# ---------------------------------------------------------------------------
# L層: retire_entry
# ---------------------------------------------------------------------------


class TestLibraryRetireNotification:
    def _links(self, entries):
        return [
            {"instance_document_id": doc_id, "status": status}
            for doc_id, status in entries
        ]

    def _setup(self, monkeypatch, *, previous_status, links, owners, notify_raises=False):
        import routes.library as library_routes

        monkeypatch.setattr(
            library_routes.library_store, "retire_entry",
            lambda entry_id, updated_by=None: ({"id": entry_id, "domain_key": "particle_physics"}, previous_status),
        )
        monkeypatch.setattr(library_routes, "_audit", lambda *a, **k: None)
        monkeypatch.setattr(library_routes._identity_links, "list_for_shared_part", lambda entry_id: links)
        monkeypatch.setattr(library_routes.services, "_resolve_document", lambda ref: {"uploaded_by": owners.get(ref)})
        calls = []

        def _notify(*a, **k):
            if notify_raises:
                raise RuntimeError("notify boom")
            calls.append(a)
            return True

        monkeypatch.setattr(library_routes.cross_layer_notify, "notify_user", _notify)
        return library_routes, calls

    def test_confirmed_citers_are_notified(self, monkeypatch):
        links = self._links([("doc-a", "confirmed"), ("doc-b", "confirmed")])
        owners = {"doc-a": "owner-a", "doc-b": "owner-b"}
        library_routes, calls = self._setup(monkeypatch, previous_status="active", links=links, owners=owners)
        library_routes.retire_entry("entry-1", {"id": "teacher-1", "role": "TEACHER"})
        recipients = {c[0] for c in calls}
        assert recipients == {"owner-a", "owner-b"}
        for c in calls:
            assert c[1] == library_routes.cross_layer_notify.NOTIF_LIBRARY_ENTRY_RETIRED
            assert c[2] == "library_entry"
            assert c[3] == "entry-1"

    def test_candidate_links_are_excluded(self, monkeypatch):
        links = self._links([("doc-a", "candidate"), ("doc-b", "rejected")])
        owners = {"doc-a": "owner-a", "doc-b": "owner-b"}
        library_routes, calls = self._setup(monkeypatch, previous_status="active", links=links, owners=owners)
        library_routes.retire_entry("entry-1", {"id": "teacher-1", "role": "TEACHER"})
        assert calls == []

    def test_actor_excluded_from_recipients(self, monkeypatch):
        links = self._links([("doc-a", "confirmed"), ("doc-b", "confirmed")])
        owners = {"doc-a": "teacher-1", "doc-b": "owner-b"}
        library_routes, calls = self._setup(monkeypatch, previous_status="active", links=links, owners=owners)
        library_routes.retire_entry("entry-1", {"id": "teacher-1", "role": "TEACHER"})
        recipients = {c[0] for c in calls}
        assert recipients == {"owner-b"}

    def test_idempotent_retire_of_already_retired_entry_does_not_renotify(self, monkeypatch):
        links = self._links([("doc-a", "confirmed")])
        owners = {"doc-a": "owner-a"}
        library_routes, calls = self._setup(monkeypatch, previous_status="retired", links=links, owners=owners)
        library_routes.retire_entry("entry-1", {"id": "teacher-1", "role": "TEACHER"})
        assert calls == []

    def test_notify_failure_does_not_break_retire(self, monkeypatch):
        links = self._links([("doc-a", "confirmed")])
        owners = {"doc-a": "owner-a"}
        library_routes, _calls = self._setup(
            monkeypatch, previous_status="active", links=links, owners=owners, notify_raises=True,
        )
        entry = library_routes.retire_entry("entry-1", {"id": "teacher-1", "role": "TEACHER"})
        assert entry["id"] == "entry-1"
