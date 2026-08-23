"""P0 — オブジェクトスコープ権限の fail-closed 化（正本:
``docs/features/security_and_context_phase3_implementation_directive.md`` §3）。

`_require_teacher` は「TEACHER 以上であること」しか保証しない。教材（document）・
コース（learning_courses）を ID 直指定するエンドポイントは、そのオブジェクトへの
**編集権限**をサーバ側で確認しなければならない。閲覧ゲート（`get_material()` /
`_ensure_document_viewable`）は public / viewer / コース経由の閲覧者も通すため、
変更系・学習痕跡の開示には使えない。

対象5経路:

| 経路 | 必要な境界 |
|---|---|
| ``GET /api/admin/courses/{cid}/unanswered-queries`` | course owner / editor（SYSTEM_ADMIN 可） |
| ``POST /api/admin/documents/{id}/reanalyze``        | document owner / editor（SYSTEM_ADMIN 可） |
| ``GET /api/admin/courses/{cid}/bridge-insights``    | course owner / editor（SYSTEM_ADMIN 可） |
| ``PUT /api/admin/materials/{mid}/pdf``              | document owner / editor（SYSTEM_ADMIN 可） |
| ``GET /api/learning/courses/{cid}/source-chunk/{chunk_id}`` | course アクセス可 かつ chunk の document が **その course の source** |

検証観点:
  1. 正例: owner / editor / SYSTEM_ADMIN はゲートを通過して後続処理へ到達する。
  2. 負例: 無関係な TEACHER / viewer のみ / public 直指定 / コース経由閲覧のみ /
     不明 ID は **すべて 404**、かつ detail が「存在するが権限なし」と「不在」で同一
     （レスポンス本文から対象の存在を判別できない）。
  3. 副作用前認可: 認可失敗時に SQL 集計・MinIO・background task・
     ``aggregate_bridge_candidates`` が呼ばれない（monkeypatch カウンタで 0 を確認）。
  4. source-chunk: sources 空集合なら SQL を発行せず 404 へ縮退する。

ハーネスは ``test_source_chunk_visibility.py``（route 関数を直接呼び、モジュール属性を
monkeypatch する流儀）を踏襲する。外部 DB・MinIO・LLM への実接続は行わない。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

# routes/*.py 内部は `from dependencies import ...` 等の裸 import に依存するため、
# backend/api を sys.path に載せる（既存テストと同型）。
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from api.routes import admin as admin_module  # noqa: E402
from api.routes import learning as learning_module  # noqa: E402
from api.services import DocumentAccess  # noqa: E402
from tests.guardrail_helpers import extract_function_source  # noqa: E402

_BACKEND = Path(__file__).resolve().parents[1]
_ADMIN_SRC = (_BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# フィクスチャ: 権限モデル（services の実 SQL は使わず、判定結果だけを再現する）
# ---------------------------------------------------------------------------

OWNER = "11111111-1111-1111-1111-111111111111"
EDITOR = "22222222-2222-2222-2222-222222222222"
VIEWER = "33333333-3333-3333-3333-333333333333"
OTHER_TEACHER = "44444444-4444-4444-4444-444444444444"
ADMIN = "55555555-5555-5555-5555-555555555555"

DOC_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
DOC_MATERIAL_ID = "arXiv-2407.01221v2"
PUBLIC_DOC_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PUBLIC_MATERIAL_ID = "arXiv-public-0001"
UNKNOWN_DOC_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"

COURSE_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
UNKNOWN_COURSE_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"

COURSE_DATA = {"title": "コース", "topics": [{"id": "t1", "title": "トピック"}],
               "sources": [{"material_id": DOC_MATERIAL_ID}]}

# document ごとの (owner, editor 集合, viewer 集合, public か)
_DOCUMENTS = {
    DOC_ID: (DOC_ID, DOC_MATERIAL_ID, OWNER, {EDITOR}, {VIEWER}, False),
    DOC_MATERIAL_ID: (DOC_ID, DOC_MATERIAL_ID, OWNER, {EDITOR}, {VIEWER}, False),
    # public 文書（所有者は別教員）。誰でも閲覧はできるが編集はできない。
    PUBLIC_DOC_ID: (PUBLIC_DOC_ID, PUBLIC_MATERIAL_ID, OTHER_TEACHER, set(), set(), True),
    PUBLIC_MATERIAL_ID: (PUBLIC_DOC_ID, PUBLIC_MATERIAL_ID, OTHER_TEACHER, set(), set(), True),
}

# course ごとの (owner, editor 集合, viewer 集合)
_COURSES = {COURSE_ID: (OWNER, {EDITOR}, {VIEWER})}


def _fake_resolve_document_access(user_id: str, ref: str) -> DocumentAccess:
    entry = _DOCUMENTS.get(ref)
    if entry is None:
        return DocumentAccess(document_id=None)
    doc_id, source_path, owner, editors, viewers, is_public = entry
    is_owner = user_id == owner
    can_edit = is_owner or user_id in editors
    can_view = can_edit or is_public or user_id in viewers
    return DocumentAccess(
        document_id=doc_id, source_path=source_path, uploaded_by=owner,
        is_owner=is_owner, can_view=can_view, can_edit=can_edit,
    )


def _fake_get_editable_course_data(user_id: str, course_id: str):
    entry = _COURSES.get(course_id)
    if entry is None:
        return None
    owner, editors, _viewers = entry
    if user_id == owner or user_id in editors:
        return dict(COURSE_DATA)
    return None


def _fake_fetch_course_data_row(course_id: str):
    return dict(COURSE_DATA) if course_id in _COURSES else None


def _user(user_id: str, role: str = "TEACHER") -> dict:
    return {"id": user_id, "username": "u", "email": "u@example.com", "role": role}


@pytest.fixture(autouse=True)
def _patch_permission_sources(monkeypatch):
    """admin.py が委譲する権限正本を、実 SQL を発行しないフェイクへ差し替える。"""
    monkeypatch.setattr(admin_module, "resolve_document_access", _fake_resolve_document_access)
    monkeypatch.setattr(admin_module, "get_editable_course_data", _fake_get_editable_course_data)
    monkeypatch.setattr(admin_module, "_fetch_course_data_row", _fake_fetch_course_data_row)


def _boom_session(*_args, **_kwargs):
    raise AssertionError("認可失敗時に DB セッションを開いてはならない")


# ---------------------------------------------------------------------------
# 0. 共通ゲート helper 単体
# ---------------------------------------------------------------------------


class TestSharedGates:
    @pytest.mark.parametrize("user_id", [OWNER, EDITOR])
    def test_document_gate_allows_owner_and_editor(self, user_id):
        access = admin_module._require_editable_document_or_404(DOC_ID, _user(user_id))
        assert access.document_id == DOC_ID
        assert access.source_path == DOC_MATERIAL_ID

    def test_document_gate_allows_system_admin(self):
        access = admin_module._require_editable_document_or_404(
            DOC_ID, _user(ADMIN, role="SYSTEM_ADMIN"),
        )
        assert access.document_id == DOC_ID

    def test_document_gate_resolves_material_id_reference(self):
        """UUID と source_path(material_id) の両参照を resolve_document_access に委譲する。"""
        access = admin_module._require_editable_document_or_404(DOC_MATERIAL_ID, _user(OWNER))
        assert access.document_id == DOC_ID

    @pytest.mark.parametrize("user_id", [VIEWER, OTHER_TEACHER])
    def test_document_gate_rejects_viewer_and_stranger(self, user_id):
        with pytest.raises(HTTPException) as exc:
            admin_module._require_editable_document_or_404(DOC_ID, _user(user_id))
        assert exc.value.status_code == 404

    def test_document_gate_rejects_public_document_for_non_owner(self):
        """public は閲覧根拠であって編集根拠ではない。"""
        with pytest.raises(HTTPException) as exc:
            admin_module._require_editable_document_or_404(PUBLIC_DOC_ID, _user(OTHER_TEACHER + "x"))
        assert exc.value.status_code == 404

    def test_document_gate_detail_is_identical_for_missing_and_forbidden(self):
        with pytest.raises(HTTPException) as missing:
            admin_module._require_editable_document_or_404(UNKNOWN_DOC_ID, _user(OWNER))
        with pytest.raises(HTTPException) as forbidden:
            admin_module._require_editable_document_or_404(DOC_ID, _user(OTHER_TEACHER))
        assert missing.value.status_code == forbidden.value.status_code == 404
        assert missing.value.detail == forbidden.value.detail

    def test_document_gate_404s_unknown_id_even_for_system_admin(self):
        """SYSTEM_ADMIN でも canonical 解決に失敗すれば 404（bypass は権限のみ）。"""
        with pytest.raises(HTTPException) as exc:
            admin_module._require_editable_document_or_404(
                UNKNOWN_DOC_ID, _user(ADMIN, role="SYSTEM_ADMIN"),
            )
        assert exc.value.status_code == 404

    @pytest.mark.parametrize("user_id", [OWNER, EDITOR])
    def test_course_gate_allows_owner_and_editor(self, user_id):
        assert admin_module._require_editable_course_or_404(COURSE_ID, _user(user_id))["title"] == "コース"

    def test_course_gate_allows_system_admin(self):
        data = admin_module._require_editable_course_or_404(
            COURSE_ID, _user(ADMIN, role="SYSTEM_ADMIN"),
        )
        assert data["title"] == "コース"

    @pytest.mark.parametrize("user_id", [VIEWER, OTHER_TEACHER])
    def test_course_gate_rejects_viewer_and_stranger(self, user_id):
        with pytest.raises(HTTPException) as exc:
            admin_module._require_editable_course_or_404(COURSE_ID, _user(user_id))
        assert exc.value.status_code == 404

    def test_course_gate_detail_is_identical_for_missing_and_forbidden(self):
        with pytest.raises(HTTPException) as missing:
            admin_module._require_editable_course_or_404(UNKNOWN_COURSE_ID, _user(OWNER))
        with pytest.raises(HTTPException) as forbidden:
            admin_module._require_editable_course_or_404(COURSE_ID, _user(OTHER_TEACHER))
        assert missing.value.status_code == forbidden.value.status_code == 404
        assert missing.value.detail == forbidden.value.detail

    def test_course_gate_404s_unknown_course_even_for_system_admin(self):
        with pytest.raises(HTTPException) as exc:
            admin_module._require_editable_course_or_404(
                UNKNOWN_COURSE_ID, _user(ADMIN, role="SYSTEM_ADMIN"),
            )
        assert exc.value.status_code == 404

    def test_gates_do_not_reuse_the_viewer_gate(self):
        """閲覧ゲート（get_material / _ensure_document_viewable）を編集認可に流用しない。"""
        for fn_name in ("_require_editable_document_or_404", "_require_editable_course_or_404"):
            src = extract_function_source(_ADMIN_SRC, fn_name)
            assert "get_material(" not in src
            assert "_ensure_document_viewable" not in src
            assert "user_can_view_document" not in src


# ---------------------------------------------------------------------------
# A. GET /api/admin/courses/{cid}/unanswered-queries
# ---------------------------------------------------------------------------


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed: list[str] = []

    def execute(self, stmt, params=None):
        self.executed.append(str(stmt))
        return _Rows(self.rows)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class TestUnansweredQueriesAuthorization:
    @pytest.mark.parametrize(
        "user", [_user(OWNER), _user(EDITOR), _user(ADMIN, role="SYSTEM_ADMIN")],
    )
    def test_allowed_users_receive_rows(self, monkeypatch, user):
        import datetime

        session = _FakeSession(
            rows=[("q1", "t1", "この式が分かりません", datetime.datetime(2026, 8, 1), "学生A")],
        )
        monkeypatch.setattr(admin_module, "_pg_session", lambda: session)

        result = admin_module.list_unanswered_queries(COURSE_ID, current_user=user)

        assert result[0]["student_name"] == "学生A"
        assert result[0]["question"] == "この式が分かりません"

    @pytest.mark.parametrize(
        "user_id,course_id",
        [
            (OTHER_TEACHER, COURSE_ID),   # 無関係な TEACHER
            (VIEWER, COURSE_ID),          # viewer のみ
            (OWNER, UNKNOWN_COURSE_ID),   # 存在しない course
        ],
    )
    def test_denied_users_get_404_without_touching_the_db(self, monkeypatch, user_id, course_id):
        monkeypatch.setattr(admin_module, "_pg_session", _boom_session)

        with pytest.raises(HTTPException) as exc:
            admin_module.list_unanswered_queries(course_id, current_user=_user(user_id))
        assert exc.value.status_code == 404

    def test_denied_response_never_returns_an_empty_list(self, monkeypatch):
        """空配列で返すと『権限が無い』と『未回答クエリが0件』が区別できてしまう。"""
        monkeypatch.setattr(admin_module, "_pg_session", _boom_session)
        with pytest.raises(HTTPException):
            admin_module.list_unanswered_queries(COURSE_ID, current_user=_user(OTHER_TEACHER))

    def test_detail_identical_for_missing_and_forbidden(self, monkeypatch):
        monkeypatch.setattr(admin_module, "_pg_session", _boom_session)
        with pytest.raises(HTTPException) as missing:
            admin_module.list_unanswered_queries(UNKNOWN_COURSE_ID, current_user=_user(OWNER))
        with pytest.raises(HTTPException) as forbidden:
            admin_module.list_unanswered_queries(COURSE_ID, current_user=_user(OTHER_TEACHER))
        assert missing.value.detail == forbidden.value.detail

    def test_gate_precedes_the_join_on_users(self):
        """静的検査: 学生名を引く JOIN より前にゲートがある。"""
        src = extract_function_source(_ADMIN_SRC, "list_unanswered_queries")
        assert src.index("_require_editable_course_or_404") < src.index("JOIN users")


# ---------------------------------------------------------------------------
# B. POST /api/admin/documents/{id}/reanalyze
# ---------------------------------------------------------------------------


class _BoomStorage:
    def get_object(self, *_args, **_kwargs):
        raise AssertionError("認可失敗時に MinIO を触ってはならない")


class TestReanalyzeAuthorization:
    @pytest.mark.parametrize(
        "user", [_user(OWNER), _user(EDITOR), _user(ADMIN, role="SYSTEM_ADMIN")],
    )
    def test_allowed_users_pass_the_gate_and_start_the_pipeline(self, monkeypatch, user):
        started: dict = {}

        class _Storage:
            def get_object(self, bucket, object_name):
                return b"%PDF-1.4 fake"

        class _Thread:
            def __init__(self, *args, **kwargs):
                started["args"] = kwargs.get("args") or (args[1] if len(args) > 1 else None)
                started["kwargs"] = kwargs

            def start(self):
                started["started"] = True

        monkeypatch.setattr(admin_module, "_pg_session", lambda: _FakeSession(rows=[]))
        monkeypatch.setattr(admin_module, "get_storage_client", lambda: _Storage())
        monkeypatch.setattr(admin_module, "create_background_task", lambda *a, **k: None)
        monkeypatch.setattr(admin_module.threading, "Thread", _Thread)

        result = admin_module.reanalyze_document(DOC_ID, body=None, current_user=user)

        assert started.get("started") is True
        assert result["document_id"] == DOC_ID
        assert result["material_id"] == DOC_MATERIAL_ID
        assert result["status"] == "pending"

    @pytest.mark.parametrize(
        "user_id,document_id",
        [
            (OTHER_TEACHER, DOC_ID),        # 他教員
            (VIEWER, DOC_ID),               # viewer のみ
            (VIEWER, PUBLIC_DOC_ID),        # public 文書 ID 直指定
            (OWNER, UNKNOWN_DOC_ID),        # 不明 ID
        ],
    )
    def test_denied_users_get_404_before_any_side_effect(self, monkeypatch, user_id, document_id):
        def _boom_task(*_a, **_k):
            raise AssertionError("認可失敗時に background task を作ってはならない")

        class _BoomThread:
            def __init__(self, *_a, **_k):
                raise AssertionError("認可失敗時に thread を起動してはならない")

        monkeypatch.setattr(admin_module, "_pg_session", _boom_session)
        monkeypatch.setattr(admin_module, "get_storage_client", lambda: _BoomStorage())
        monkeypatch.setattr(admin_module, "create_background_task", _boom_task)
        monkeypatch.setattr(admin_module.threading, "Thread", _BoomThread)

        with pytest.raises(HTTPException) as exc:
            admin_module.reanalyze_document(
                document_id, body=None, current_user=_user(user_id),
            )
        assert exc.value.status_code == 404

    def test_detail_identical_for_missing_and_forbidden(self, monkeypatch):
        monkeypatch.setattr(admin_module, "_pg_session", _boom_session)
        with pytest.raises(HTTPException) as missing:
            admin_module.reanalyze_document(UNKNOWN_DOC_ID, body=None, current_user=_user(OWNER))
        with pytest.raises(HTTPException) as forbidden:
            admin_module.reanalyze_document(DOC_ID, body=None, current_user=_user(OTHER_TEACHER))
        assert missing.value.detail == forbidden.value.detail

    def test_gate_precedes_storage_and_background_task(self):
        src = extract_function_source(_ADMIN_SRC, "reanalyze_document")
        gate = src.index("_require_editable_document_or_404")
        assert gate < src.index("get_storage_client()")
        assert gate < src.index("create_background_task(")
        assert gate < src.index("_previous_run_options(")
        assert gate < src.index("threading.Thread(")

    def test_canonical_ids_are_reused_without_a_second_access_resolution(self):
        """ゲートが返した canonical document_id / source_path を後続で使う（二重解決しない）。"""
        src = extract_function_source(_ADMIN_SRC, "reanalyze_document")
        assert "document_id = access.document_id or document_id" in src
        assert "material_id = access.source_path" in src
        assert src.count("resolve_document_access(") == 0


# ---------------------------------------------------------------------------
# C. GET /api/admin/courses/{cid}/bridge-insights
# ---------------------------------------------------------------------------


class TestBridgeInsightsAuthorization:
    @pytest.mark.parametrize(
        "user", [_user(OWNER), _user(EDITOR), _user(ADMIN, role="SYSTEM_ADMIN")],
    )
    def test_allowed_users_receive_the_aggregate(self, monkeypatch, user):
        from core.personal_graph import bridges as bridges_module

        calls: list[str] = []

        def _aggregate(course_id):
            calls.append(course_id)
            return [{"anchor_label": "アンカー", "learner_range": "3-5"}]

        monkeypatch.setattr(bridges_module, "aggregate_bridge_candidates", _aggregate)

        result = admin_module.get_bridge_insights(COURSE_ID, current_user=user)

        assert calls == [COURSE_ID]
        assert result["bridges"][0]["anchor_label"] == "アンカー"

    @pytest.mark.parametrize(
        "user_id,course_id",
        [
            (OTHER_TEACHER, COURSE_ID),
            (VIEWER, COURSE_ID),
            (OWNER, UNKNOWN_COURSE_ID),
        ],
    )
    def test_denied_users_get_404_and_the_aggregate_is_never_called(
        self, monkeypatch, user_id, course_id,
    ):
        from core.personal_graph import bridges as bridges_module

        calls: list[str] = []

        def _aggregate(cid):
            calls.append(cid)
            raise AssertionError("認可失敗時に集約処理を呼んではならない")

        monkeypatch.setattr(bridges_module, "aggregate_bridge_candidates", _aggregate)

        with pytest.raises(HTTPException) as exc:
            admin_module.get_bridge_insights(course_id, current_user=_user(user_id))
        assert exc.value.status_code == 404
        assert calls == []

    def test_detail_identical_for_missing_and_forbidden(self):
        with pytest.raises(HTTPException) as missing:
            admin_module.get_bridge_insights(UNKNOWN_COURSE_ID, current_user=_user(OWNER))
        with pytest.raises(HTTPException) as forbidden:
            admin_module.get_bridge_insights(COURSE_ID, current_user=_user(OTHER_TEACHER))
        assert missing.value.detail == forbidden.value.detail

    def test_gate_precedes_the_aggregate_call(self):
        src = extract_function_source(_ADMIN_SRC, "get_bridge_insights")
        assert src.index("_require_editable_course_or_404") < src.index("aggregate_bridge_candidates(course_id)")


# ---------------------------------------------------------------------------
# D. PUT /api/admin/materials/{mid}/pdf
# ---------------------------------------------------------------------------


class _FakeUpload:
    """認可前に読まれたら即座に失敗する UploadFile 代用。"""

    class _File:
        def __init__(self, data, guard):
            self._data = data
            self._guard = guard

        def read(self):
            if self._guard["deny"]:
                raise AssertionError("認可失敗時にアップロードファイルを読んではならない")
            return self._data

    def __init__(self, filename="paper.pdf", data=b"%PDF-1.4 fake", deny=False):
        self.filename = filename
        self._guard = {"deny": deny}
        self.file = self._File(data, self._guard)


class TestReuploadMaterialPdfAuthorization:
    @pytest.mark.parametrize(
        "user", [_user(OWNER), _user(EDITOR), _user(ADMIN, role="SYSTEM_ADMIN")],
    )
    def test_allowed_users_pass_the_gate_and_upload(self, monkeypatch, user):
        uploaded: dict = {}

        class _Storage:
            def upload_pdf(self, bucket, object_name, data):
                uploaded["object_name"] = object_name

        monkeypatch.setattr(admin_module, "_pg_session", lambda: _FakeSession(rows=[]))
        monkeypatch.setattr(admin_module, "get_storage_client", lambda: _Storage())
        monkeypatch.setattr(
            admin_module, "_backfill_missing_chunk_pages_from_pdf", lambda mid, data: 0,
        )

        result = admin_module.reupload_material_pdf(
            DOC_MATERIAL_ID, file=_FakeUpload(), current_user=user,
        )

        assert result["material_id"] == DOC_MATERIAL_ID
        assert uploaded["object_name"] == f"uploads/{DOC_MATERIAL_ID}.pdf"

    @pytest.mark.parametrize(
        "user_id,material_id",
        [
            (OTHER_TEACHER, DOC_MATERIAL_ID),   # 他教員
            (VIEWER, DOC_MATERIAL_ID),          # viewer のみ（= コース経由閲覧者も同じ扱い）
            (OTHER_TEACHER, PUBLIC_MATERIAL_ID + "-unknown"),  # 不明 material
            (VIEWER, PUBLIC_MATERIAL_ID),       # public 文書を閲覧できるだけ
        ],
    )
    def test_denied_users_get_404_before_reading_the_file(self, monkeypatch, user_id, material_id):
        class _BoomUploadStorage:
            def upload_pdf(self, *_a, **_k):
                raise AssertionError("認可失敗時に MinIO へアップロードしてはならない")

        monkeypatch.setattr(admin_module, "_pg_session", _boom_session)
        monkeypatch.setattr(admin_module, "get_storage_client", lambda: _BoomUploadStorage())

        with pytest.raises(HTTPException) as exc:
            admin_module.reupload_material_pdf(
                material_id, file=_FakeUpload(deny=True), current_user=_user(user_id),
            )
        assert exc.value.status_code == 404

    def test_detail_identical_for_missing_and_forbidden(self, monkeypatch):
        monkeypatch.setattr(admin_module, "_pg_session", _boom_session)
        with pytest.raises(HTTPException) as missing:
            admin_module.reupload_material_pdf(
                "unknown-material", file=_FakeUpload(deny=True), current_user=_user(OWNER),
            )
        with pytest.raises(HTTPException) as forbidden:
            admin_module.reupload_material_pdf(
                DOC_MATERIAL_ID, file=_FakeUpload(deny=True), current_user=_user(OTHER_TEACHER),
            )
        assert missing.value.detail == forbidden.value.detail

    def test_viewer_gate_is_no_longer_the_authorization(self):
        """`get_material()`（閲覧ゲート）を認可に使わない。"""
        src = extract_function_source(_ADMIN_SRC, "reupload_material_pdf")
        assert "get_material(material_id, current_user)" not in src
        gate = src.index("_require_editable_document_or_404")
        assert gate < src.index("file.file.read()")
        assert gate < src.index("upload_pdf(")


# ---------------------------------------------------------------------------
# E. GET /api/learning/courses/{cid}/source-chunk/{chunk_id}
# ---------------------------------------------------------------------------


class TestSourceChunkCourseScope:
    def test_course_source_chunk_is_served(self, monkeypatch):
        captured: dict = {}

        monkeypatch.setattr(
            learning_module, "get_accessible_course_data", lambda uid, cid: dict(COURSE_DATA),
        )
        monkeypatch.setattr(
            learning_module, "list_course_source_document_ids", lambda cd: {DOC_ID},
        )

        def _passage(chunk_id, allowed_document_ids=None):
            captured["allowed"] = allowed_document_ids
            return {"chunk_id": chunk_id, "text": "本文", "formulas": [],
                    "source_title": "論文", "source_file": "p.pdf", "section": ""}

        monkeypatch.setattr(learning_module, "get_chunk_passage", _passage)

        result = learning_module.get_source_chunk_route(
            COURSE_ID, "chunk-1", current_user=_user(VIEWER, role="STUDENT"),
        )
        assert result["chunk_id"] == "chunk-1"
        # スコープは course sources そのもの（全域可視集合を混ぜない）。
        assert captured["allowed"] == {DOC_ID}

    def test_inaccessible_course_yields_404_without_reading_the_chunk(self, monkeypatch):
        monkeypatch.setattr(learning_module, "get_accessible_course_data", lambda uid, cid: None)

        def _boom(*_a, **_k):
            raise AssertionError("course にアクセスできないならチャンクを読んではならない")

        monkeypatch.setattr(learning_module, "get_chunk_passage", _boom)

        with pytest.raises(HTTPException) as exc:
            learning_module.get_source_chunk_route(
                UNKNOWN_COURSE_ID, "chunk-1", current_user=_user(OTHER_TEACHER, role="STUDENT"),
            )
        assert exc.value.status_code == 404

    @pytest.mark.parametrize("scenario", ["other_course_chunk", "public_but_not_a_course_source"])
    def test_visible_chunk_outside_course_sources_is_404(self, monkeypatch, scenario):
        """同じユーザーが別経路で閲覧できる chunk でも、この course の source でなければ 404。

        `get_chunk_passage` は SQL 内 `document_id = ANY(...)` で強制するため、
        course sources 外の document のチャンクは行が返らない（None → 404）。
        """
        monkeypatch.setattr(
            learning_module, "get_accessible_course_data", lambda uid, cid: dict(COURSE_DATA),
        )
        monkeypatch.setattr(
            learning_module, "list_course_source_document_ids", lambda cd: {DOC_ID},
        )

        def _passage(chunk_id, allowed_document_ids=None):
            # 別 course / public 文書の document_id は allowed に入っていない。
            assert allowed_document_ids == {DOC_ID}
            return None

        monkeypatch.setattr(learning_module, "get_chunk_passage", _passage)

        with pytest.raises(HTTPException) as exc:
            learning_module.get_source_chunk_route(
                COURSE_ID, f"chunk-{scenario}", current_user=_user(VIEWER, role="STUDENT"),
            )
        assert exc.value.status_code == 404

    def test_empty_sources_short_circuits_without_sql(self, monkeypatch):
        """sources 空集合 → `get_chunk_passage` の fail-closed 短絡（SQL 非発行）で 404。"""
        from api import services

        monkeypatch.setattr(
            learning_module, "get_accessible_course_data", lambda uid, cid: {"sources": []},
        )
        monkeypatch.setattr(learning_module, "list_course_source_document_ids", lambda cd: set())
        monkeypatch.setattr(services, "_pg_session", _boom_session)

        with pytest.raises(HTTPException) as exc:
            learning_module.get_source_chunk_route(
                COURSE_ID, "chunk-1", current_user=_user(VIEWER, role="STUDENT"),
            )
        assert exc.value.status_code == 404

    def test_detail_identical_for_inaccessible_course_and_missing_chunk(self, monkeypatch):
        """course の存在・アクセス可否がレスポンス本文から判別できない。"""
        monkeypatch.setattr(learning_module, "get_accessible_course_data", lambda uid, cid: None)
        with pytest.raises(HTTPException) as no_course:
            learning_module.get_source_chunk_route(
                UNKNOWN_COURSE_ID, "chunk-1", current_user=_user(OTHER_TEACHER, role="STUDENT"),
            )

        monkeypatch.setattr(
            learning_module, "get_accessible_course_data", lambda uid, cid: dict(COURSE_DATA),
        )
        monkeypatch.setattr(
            learning_module, "list_course_source_document_ids", lambda cd: {DOC_ID},
        )
        monkeypatch.setattr(
            learning_module, "get_chunk_passage", lambda chunk_id, allowed_document_ids=None: None,
        )
        with pytest.raises(HTTPException) as no_chunk:
            learning_module.get_source_chunk_route(
                COURSE_ID, "chunk-1", current_user=_user(VIEWER, role="STUDENT"),
            )

        assert no_course.value.status_code == no_chunk.value.status_code == 404
        assert no_course.value.detail == no_chunk.value.detail
