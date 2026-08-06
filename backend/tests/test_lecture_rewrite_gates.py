"""原稿スタジオ rewrite/save の権限ゲート + コスト上限のテスト（Phase 2-D）。

正本: docs/features/assistant_common_infra_design.md §5（🔴 権限ゲート）/ §1（コスト上限）。

対象:
  - routes/lecture_studio/_shared.py
    - ``_ensure_chunk_editable`` / ``_material_id_for_chunk``（chunk_id 直指定の書き換え
      が他教員の教材にも及んでいた欠如の是正）
    - ``consume_lecture_rewrite_quota`` / ``_lecture_rewrite_cost_gate``
      （scripts.py / topics.py の両 rewrite 経路が共有する day-only CostGate）
  - routes/lecture_studio/scripts.py: ``rewrite_lecture_script`` / ``save_lecture_script``
  - routes/lecture_studio/topics.py: ``rewrite_lecture_studio_course_topic``

TestClient + monkeypatch（test_reconstruction_api.py / test_deliberation_api.py と同型）。
実 DB・実 LLM は使わず、DB アクセス層（chunk の material_id 解決・
``services.resolve_document_access``・LLM 呼び出し・本体の SELECT/UPDATE）をモックする。
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

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入（full API は Docker 内で）")

_UID_OWNER = "22222222-2222-2222-2222-222222222222"
_UID_EDITOR = "33333333-3333-3333-3333-333333333333"
_UID_OTHER = "44444444-4444-4444-4444-444444444444"
_UID_ADMIN = "55555555-5555-5555-5555-555555555555"

CHUNK_ID = "66666666-6666-6666-6666-666666666666"
MATERIAL_ID = "material-1"


@pytest.fixture
def client_and_tokens():
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_SYSTEM_ADMIN, ROLE_TEACHER, _create_token

    client = TestClient(app)
    owner = _create_token(_UID_OWNER, "owner", "owner@x", ROLE_TEACHER)
    editor = _create_token(_UID_EDITOR, "editor", "editor@x", ROLE_TEACHER)
    other = _create_token(_UID_OTHER, "other", "other@x", ROLE_TEACHER)
    admin = _create_token(_UID_ADMIN, "admin", "admin@x", ROLE_SYSTEM_ADMIN)
    return client, owner, editor, other, admin


def _auth(tok):
    return {"Authorization": "Bearer " + tok}


@pytest.fixture(autouse=True)
def _reset_rewrite_cost_gate(monkeypatch):
    """テスト間でプロセスローカル CostGate が汚染されないよう、毎テスト新しいインスタンスに
    差し替える（既定 cap=100 のままだが、各テストが独立したカウンタで走る）。"""
    import routes.lecture_studio._shared as shared_mod
    from core.llm_worker.cost_gate import CostGate

    monkeypatch.setattr(shared_mod, "_lecture_rewrite_cost_gate", CostGate())


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


class _FakeSession:
    """endpoint 本体の SELECT/UPDATE/DELETE をスタブする。

    save_lecture_script / rewrite_lecture_script はどちらも1回だけ SELECT を発行し、
    残りは UPDATE/DELETE のため、SELECT にだけ ``select_row`` を返せば足りる。
    """

    def __init__(self, select_row=None):
        self.select_row = select_row
        self.committed = False
        self.rolled_back = False

    def execute(self, stmt, params=None):
        sql = str(stmt).upper()
        if "SELECT" in sql:
            return _FakeResult(self.select_row)
        return _FakeResult(None)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def _allow_chunk_edit(monkeypatch, *, can_edit=True):
    """`_shared._material_id_for_chunk` / `resolve_document_access` を mock する。"""
    import routes.lecture_studio._shared as shared_mod

    monkeypatch.setattr(shared_mod, "_material_id_for_chunk", lambda cid: MATERIAL_ID)

    class _FakeAccess:
        def __init__(self, ce):
            self.can_edit = ce

    monkeypatch.setattr(shared_mod, "resolve_document_access", lambda uid, mid: _FakeAccess(can_edit))


def _stub_rewrite_llm(monkeypatch):
    import routes.lecture_studio.scripts as scripts_mod

    monkeypatch.setattr(
        scripts_mod,
        "_pg_session",
        lambda: _FakeSession(select_row=("source", "disp", "spoken", [], "", None, None)),
    )
    monkeypatch.setattr(
        scripts_mod,
        "generate_text",
        lambda **kw: '{"display_text": "d", "spoken_text": "s", "formulas": []}',
    )


# ---------------------------------------------------------------------------
# 1. `_shared._ensure_chunk_editable` 単体テスト（ロジックそのもの）
# ---------------------------------------------------------------------------


class TestEnsureChunkEditableUnit:
    def _call(self, monkeypatch, *, material_id, can_edit=True, role="TEACHER", user_id=_UID_OTHER):
        import routes.lecture_studio._shared as shared_mod

        monkeypatch.setattr(shared_mod, "_material_id_for_chunk", lambda cid: material_id)

        class _FakeAccess:
            def __init__(self, ce):
                self.can_edit = ce

        called = {"n": 0}

        def _fake_resolve(uid, mid):
            called["n"] += 1
            return _FakeAccess(can_edit)

        monkeypatch.setattr(shared_mod, "resolve_document_access", _fake_resolve)
        result = shared_mod._ensure_chunk_editable(CHUNK_ID, {"id": user_id, "role": role})
        return result, called["n"]

    def test_chunk_not_found_raises_404(self, monkeypatch):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            self._call(monkeypatch, material_id=None)
        assert exc.value.status_code == 404

    def test_material_id_unresolvable_raises_404(self, monkeypatch):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            self._call(monkeypatch, material_id="", can_edit=True)
        assert exc.value.status_code == 404

    def test_non_owner_without_edit_permission_raises_404(self, monkeypatch):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            self._call(monkeypatch, material_id=MATERIAL_ID, can_edit=False)
        assert exc.value.status_code == 404

    def test_owner_or_editor_returns_material_id(self, monkeypatch):
        result, calls = self._call(monkeypatch, material_id=MATERIAL_ID, can_edit=True)
        assert result == MATERIAL_ID
        assert calls == 1

    def test_system_admin_bypasses_resolve_document_access(self, monkeypatch):
        result, calls = self._call(
            monkeypatch, material_id=MATERIAL_ID, can_edit=False, role="SYSTEM_ADMIN", user_id=_UID_ADMIN,
        )
        assert result == MATERIAL_ID
        assert calls == 0  # SYSTEM_ADMIN は resolve_document_access を呼ばない

    def test_system_admin_still_gets_404_for_missing_chunk(self, monkeypatch):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            self._call(monkeypatch, material_id=None, role="SYSTEM_ADMIN", user_id=_UID_ADMIN)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 2. `consume_lecture_rewrite_quota` 単体テスト
# ---------------------------------------------------------------------------


class TestConsumeLectureRewriteQuotaUnit:
    def _with_cap(self, monkeypatch, cap):
        import routes.lecture_studio._shared as shared_mod

        class _FakeSettings:
            lecture_rewrite_max_calls_per_day = cap

        monkeypatch.setattr(shared_mod, "get_settings", lambda: _FakeSettings())
        return shared_mod

    def test_under_cap_does_not_raise(self, monkeypatch):
        shared_mod = self._with_cap(monkeypatch, 2)
        shared_mod.consume_lecture_rewrite_quota("user-a")
        shared_mod.consume_lecture_rewrite_quota("user-a")

    def test_exceeding_cap_raises_429_without_digits_in_detail(self, monkeypatch):
        from fastapi import HTTPException

        shared_mod = self._with_cap(monkeypatch, 1)
        shared_mod.consume_lecture_rewrite_quota("user-b")
        with pytest.raises(HTTPException) as exc:
            shared_mod.consume_lecture_rewrite_quota("user-b")
        assert exc.value.status_code == 429
        assert not any(ch.isdigit() for ch in exc.value.detail)

    def test_per_user_isolation(self, monkeypatch):
        shared_mod = self._with_cap(monkeypatch, 1)
        shared_mod.consume_lecture_rewrite_quota("user-c")
        # 別ユーザーのキーは独立しているため上限に達していない
        shared_mod.consume_lecture_rewrite_quota("user-d")


# ---------------------------------------------------------------------------
# 3. scripts.py: rewrite_lecture_script / save_lecture_script の権限ゲート統合テスト
# ---------------------------------------------------------------------------


class TestRewriteLectureScriptPermissionGate:
    PATH = f"/api/admin/chunks/{CHUNK_ID}/lecture-script/rewrite"

    def test_non_owner_teacher_gets_404(self, client_and_tokens, monkeypatch):
        client, _owner, _editor, other, _admin = client_and_tokens
        _allow_chunk_edit(monkeypatch, can_edit=False)

        r = client.post(self.PATH, headers=_auth(other), json={"prompt": "書き換えて"})
        assert r.status_code == 404

    def test_owner_can_rewrite(self, client_and_tokens, monkeypatch):
        client, owner, _editor, _other, _admin = client_and_tokens
        _allow_chunk_edit(monkeypatch, can_edit=True)
        _stub_rewrite_llm(monkeypatch)

        r = client.post(self.PATH, headers=_auth(owner), json={"prompt": "書き換えて"})
        assert r.status_code == 200
        assert r.json()["display_text"] == "d"

    def test_editor_via_group_share_can_rewrite(self, client_and_tokens, monkeypatch):
        """resolve_document_access が editor 権限 (can_edit=True) を返すケース。"""
        client, _owner, editor, _other, _admin = client_and_tokens
        _allow_chunk_edit(monkeypatch, can_edit=True)
        _stub_rewrite_llm(monkeypatch)

        r = client.post(self.PATH, headers=_auth(editor), json={"prompt": "書き換えて"})
        assert r.status_code == 200

    def test_system_admin_can_rewrite_without_edit_permission(self, client_and_tokens, monkeypatch):
        client, _owner, _editor, _other, admin = client_and_tokens
        _allow_chunk_edit(monkeypatch, can_edit=False)  # SYSTEM_ADMIN はこれを無視できる
        _stub_rewrite_llm(monkeypatch)

        r = client.post(self.PATH, headers=_auth(admin), json={"prompt": "書き換えて"})
        assert r.status_code == 200

    def test_permission_denied_requests_do_not_consume_quota(self, client_and_tokens, monkeypatch):
        """拒否されたリクエストはコストゲートを消費しない（設計書 §5 末尾）。"""
        import routes.lecture_studio._shared as shared_mod

        client, _owner, _editor, other, _admin = client_and_tokens

        class _FakeSettings:
            lecture_rewrite_max_calls_per_day = 1

        monkeypatch.setattr(shared_mod, "get_settings", lambda: _FakeSettings())
        _allow_chunk_edit(monkeypatch, can_edit=False)

        # cap=1 のもとで同一ユーザーが2回拒否されても消費されていないはず
        for _ in range(2):
            r = client.post(self.PATH, headers=_auth(other), json={"prompt": "書き換えて"})
            assert r.status_code == 404

        # 権限を許可に切り替えると、まだ1回分の枠が残っている
        _allow_chunk_edit(monkeypatch, can_edit=True)
        _stub_rewrite_llm(monkeypatch)
        r = client.post(self.PATH, headers=_auth(other), json={"prompt": "書き換えて"})
        assert r.status_code == 200


class TestRewriteLectureScriptCostGate:
    PATH = f"/api/admin/chunks/{CHUNK_ID}/lecture-script/rewrite"

    def test_exceeding_daily_cap_returns_429(self, client_and_tokens, monkeypatch):
        import routes.lecture_studio._shared as shared_mod

        client, owner, _editor, _other, _admin = client_and_tokens

        class _FakeSettings:
            lecture_rewrite_max_calls_per_day = 1

        monkeypatch.setattr(shared_mod, "get_settings", lambda: _FakeSettings())
        _allow_chunk_edit(monkeypatch, can_edit=True)
        _stub_rewrite_llm(monkeypatch)

        r1 = client.post(self.PATH, headers=_auth(owner), json={"prompt": "1"})
        assert r1.status_code == 200

        r2 = client.post(self.PATH, headers=_auth(owner), json={"prompt": "2"})
        assert r2.status_code == 429
        assert not any(ch.isdigit() for ch in r2.json()["detail"])

    def test_llm_is_not_called_once_quota_is_exhausted(self, client_and_tokens, monkeypatch):
        import routes.lecture_studio._shared as shared_mod
        import routes.lecture_studio.scripts as scripts_mod

        client, owner, _editor, _other, _admin = client_and_tokens

        class _FakeSettings:
            lecture_rewrite_max_calls_per_day = 0

        monkeypatch.setattr(shared_mod, "get_settings", lambda: _FakeSettings())
        _allow_chunk_edit(monkeypatch, can_edit=True)
        # quota チェックより前段の chunk データ取得 SELECT はコストゲートと無関係に走るため
        # フェイクセッションを用意する（LLM 呼び出しに到達しないことだけを検証したい）。
        monkeypatch.setattr(
            scripts_mod,
            "_pg_session",
            lambda: _FakeSession(select_row=("source", "disp", "spoken", [], "", None, None)),
        )

        def _boom(**kw):
            raise AssertionError("quota 超過時に LLM を呼んではいけない")

        monkeypatch.setattr(scripts_mod, "generate_text", _boom)

        r = client.post(self.PATH, headers=_auth(owner), json={"prompt": "1"})
        assert r.status_code == 429


class TestSaveLectureScriptPermissionGate:
    PATH = f"/api/admin/chunks/{CHUNK_ID}/lecture-script"

    def _stub_save_db(self, monkeypatch):
        import routes.lecture_studio.scripts as scripts_mod

        monkeypatch.setattr(scripts_mod, "_pg_session", lambda: _FakeSession(select_row=(1,)))

    def test_non_owner_teacher_gets_404(self, client_and_tokens, monkeypatch):
        client, _owner, _editor, other, _admin = client_and_tokens
        _allow_chunk_edit(monkeypatch, can_edit=False)

        r = client.put(self.PATH, headers=_auth(other), json={"spoken_text": "hello"})
        assert r.status_code == 404

    def test_owner_can_save(self, client_and_tokens, monkeypatch):
        client, owner, _editor, _other, _admin = client_and_tokens
        _allow_chunk_edit(monkeypatch, can_edit=True)
        self._stub_save_db(monkeypatch)

        r = client.put(self.PATH, headers=_auth(owner), json={"spoken_text": "hello"})
        assert r.status_code == 200

    def test_editor_via_group_share_can_save(self, client_and_tokens, monkeypatch):
        client, _owner, editor, _other, _admin = client_and_tokens
        _allow_chunk_edit(monkeypatch, can_edit=True)
        self._stub_save_db(monkeypatch)

        r = client.put(self.PATH, headers=_auth(editor), json={"spoken_text": "hello"})
        assert r.status_code == 200

    def test_system_admin_can_save_without_edit_permission(self, client_and_tokens, monkeypatch):
        client, _owner, _editor, _other, admin = client_and_tokens
        _allow_chunk_edit(monkeypatch, can_edit=False)
        self._stub_save_db(monkeypatch)

        r = client.put(self.PATH, headers=_auth(admin), json={"spoken_text": "hello"})
        assert r.status_code == 200

    def test_save_is_not_subject_to_rewrite_cost_gate(self, client_and_tokens, monkeypatch):
        """手動保存は rewrite の LLM 呼び出しではないため、コスト上限の対象外。"""
        import routes.lecture_studio._shared as shared_mod

        client, owner, _editor, _other, _admin = client_and_tokens

        class _FakeSettings:
            lecture_rewrite_max_calls_per_day = 0  # rewrite なら即座に枯渇する設定

        monkeypatch.setattr(shared_mod, "get_settings", lambda: _FakeSettings())
        _allow_chunk_edit(monkeypatch, can_edit=True)
        self._stub_save_db(monkeypatch)

        for _ in range(3):
            r = client.put(self.PATH, headers=_auth(owner), json={"spoken_text": "hello"})
            assert r.status_code == 200


# ---------------------------------------------------------------------------
# 4. topics.py: rewrite_lecture_studio_course_topic の権限ゲート統合テスト
# ---------------------------------------------------------------------------


def _fake_course_data():
    return {
        "topics": [
            {
                "id": "t1",
                "title": "Topic 1",
                "key_concepts": [],
                "student_material": {},
                "spoken_script": "",
                "cautions": [],
                "check_questions": [],
            }
        ]
    }


def _stub_topic_rewrite_llm(monkeypatch):
    import routes.lecture_studio.topics as topics_mod

    def _fake_structured(**kw):
        response_format = kw["response_format"]
        return response_format(spoken_script="rewritten")

    monkeypatch.setattr(topics_mod, "generate_text_with_structured_output", _fake_structured)


class TestRewriteCourseTopicPermissionGate:
    """権限ゲートの seam は ``_shared._course_data_for_studio_editable``。

    教材図スタジオ（`teaching_figure_studio_design.md` §8）で同じ編集権限ゲートを共有する
    ため、実装は topics.py から ``_shared.py`` へ移った（topics.py は委譲。挙動・URL・
    ステータスコードは不変）。よって ``get_editable_course_data`` /
    ``_get_system_admin_course_data`` のパッチ先も ``shared_mod`` である。
    """

    PATH = "/api/admin/courses/course-1/lecture-studio/course-topics/t1/draft/rewrite"

    def test_viewer_only_teacher_gets_404(self, client_and_tokens, monkeypatch):
        """editor 権限が無い（閲覧のみの）教員は 404（旧実装は viewable のみで通っていた欠如の是正）。"""
        client, _owner, _editor, other, _admin = client_and_tokens
        import routes.lecture_studio._shared as shared_mod

        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: None)
        monkeypatch.setattr(shared_mod, "_get_system_admin_course_data", lambda cid: None)

        r = client.post(self.PATH, headers=_auth(other), json={"prompt": "書き換えて"})
        assert r.status_code == 404

    def test_owner_can_rewrite(self, client_and_tokens, monkeypatch):
        client, owner, _editor, _other, _admin = client_and_tokens
        import routes.lecture_studio._shared as shared_mod

        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: _fake_course_data())
        _stub_topic_rewrite_llm(monkeypatch)

        r = client.post(self.PATH, headers=_auth(owner), json={"prompt": "書き換えて"})
        assert r.status_code == 200
        assert r.json()["spoken_script"] == "rewritten"

    def test_editor_via_group_share_can_rewrite(self, client_and_tokens, monkeypatch):
        client, _owner, editor, _other, _admin = client_and_tokens
        import routes.lecture_studio._shared as shared_mod

        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: _fake_course_data())
        _stub_topic_rewrite_llm(monkeypatch)

        r = client.post(self.PATH, headers=_auth(editor), json={"prompt": "書き換えて"})
        assert r.status_code == 200

    def test_system_admin_can_rewrite_via_fallback(self, client_and_tokens, monkeypatch):
        client, _owner, _editor, _other, admin = client_and_tokens
        import routes.lecture_studio._shared as shared_mod

        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: None)
        monkeypatch.setattr(shared_mod, "_get_system_admin_course_data", lambda cid: _fake_course_data())
        _stub_topic_rewrite_llm(monkeypatch)

        r = client.post(self.PATH, headers=_auth(admin), json={"prompt": "書き換えて"})
        assert r.status_code == 200

    def test_exceeding_daily_cap_returns_429(self, client_and_tokens, monkeypatch):
        import routes.lecture_studio._shared as shared_mod

        client, owner, _editor, _other, _admin = client_and_tokens

        class _FakeSettings:
            lecture_rewrite_max_calls_per_day = 1

        monkeypatch.setattr(shared_mod, "get_settings", lambda: _FakeSettings())
        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: _fake_course_data())
        _stub_topic_rewrite_llm(monkeypatch)

        r1 = client.post(self.PATH, headers=_auth(owner), json={"prompt": "1"})
        assert r1.status_code == 200

        r2 = client.post(self.PATH, headers=_auth(owner), json={"prompt": "2"})
        assert r2.status_code == 429
        assert not any(ch.isdigit() for ch in r2.json()["detail"])


class TestSharedCostGateAcrossScriptsAndTopics:
    """scripts.py の rewrite と topics.py の rewrite が同一 CostGate インスタンスを
    共有すること（設計書 §1: `lecture_rewrite_max_calls_per_day` は両経路合算）。"""

    def test_scripts_rewrite_exhausts_quota_for_topics_rewrite_too(self, client_and_tokens, monkeypatch):
        import routes.lecture_studio._shared as shared_mod
        import routes.lecture_studio.topics as topics_mod

        client, owner, _editor, _other, _admin = client_and_tokens

        class _FakeSettings:
            lecture_rewrite_max_calls_per_day = 1

        monkeypatch.setattr(shared_mod, "get_settings", lambda: _FakeSettings())
        _allow_chunk_edit(monkeypatch, can_edit=True)
        _stub_rewrite_llm(monkeypatch)

        r1 = client.post(
            f"/api/admin/chunks/{CHUNK_ID}/lecture-script/rewrite",
            headers=_auth(owner),
            json={"prompt": "1"},
        )
        assert r1.status_code == 200

        monkeypatch.setattr(shared_mod, "get_editable_course_data", lambda uid, cid: _fake_course_data())

        def _boom(**kw):
            raise AssertionError("quota 超過時に LLM を呼んではいけない（topics.py 経路）")

        monkeypatch.setattr(topics_mod, "generate_text_with_structured_output", _boom)

        r2 = client.post(
            "/api/admin/courses/course-1/lecture-studio/course-topics/t1/draft/rewrite",
            headers=_auth(owner),
            json={"prompt": "2"},
        )
        assert r2.status_code == 429
