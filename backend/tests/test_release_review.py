"""リリース前の確認（Release Review）— store プリミティブと API のテスト。

正本: ``docs/features/release_review_flow_design.md``（不変条項 RR1〜RR7）。
ハーネスは ``tests/test_landscape_api.py`` の流儀（実 app + ``TestClient`` +
core 関数の monkeypatch）を踏襲する。

検証観点:
  1. store: ``inferred`` 以外を触らない（RR4）/ 空入力で SQL を発行しない（fail-closed）/
     ``note`` 空文字で既存メモを保持（P4）/ DELETE を持ち込まない（RR5）
  2. API GET: envelope（フロントが読むキー）・未確認件数・document 別 editable・
     権限外 document の除外件数（RR7）・生数値の非漏洩（RR6）
  3. API POST accept: ``inferred`` のみ確認済みに / edit 権限のない document は対象外 /
     監査 action が ``accept_on_release``（個別レビューと区別できる — RR3）
  4. 新エンドポイントに DELETE を作っていないこと（LS3 / P4）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.landscape import schema as landscape_schema  # noqa: E402
from core.landscape import store as landscape_store  # noqa: E402
from tests.guardrail_helpers import extract_function_source  # noqa: E402

_COURSE = "33333333-3333-3333-3333-333333333333"
_DOC = "11111111-1111-1111-1111-111111111111"
_DOC_LOCKED = "55555555-5555-5555-5555-555555555555"
_PLACEMENT = "44444444-4444-4444-4444-444444444444"
_PLACEMENT_2 = "66666666-6666-6666-6666-666666666666"
_TEACHER_ID = "22222222-2222-2222-2222-222222222222"
_DOMAIN = "astrophysics"

STORE_SRC = (BACKEND / "core" / "landscape" / "store.py").read_text(encoding="utf-8")
ROUTE_SRC = (BACKEND / "api" / "routes" / "landscape.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. store プリミティブ（FastAPI 不要）
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _RecordingSession:
    """実行された SQL とパラメータを記録するだけのフェイク。"""

    def __init__(self, rows=()):
        self.calls: list[tuple[str, dict]] = []
        self.rows = list(rows)

    def execute(self, stmt, params=None):
        self.calls.append((" ".join(str(stmt).split()), dict(params or {})))
        return _Result(self.rows)


def _db_row(placement_id: str = _PLACEMENT, status: str = "confirmed") -> tuple:
    """``_COLUMNS_SQL`` の列順に一致する行タプル。"""
    return (
        placement_id, _DOC, _DOMAIN, "2026.1", "cosmology", "region",
        "subject", 0.8, "宇宙膨張の測定を主題にしている。", [], status, "llm",
        None, "pipeline", _TEACHER_ID, "2026-08-05T00:00:00+00:00",
        "リリース前の確認画面で一括確認", "2026-08-04T09:00:00+00:00",
        "2026-08-05T00:00:00+00:00",
    )


class TestAcceptStorePrimitive:
    def test_empty_document_ids_issues_no_sql(self):
        """fail-closed: 空集合を「全件」に転ばせない。"""
        session = _RecordingSession()
        assert landscape_store.accept_inferred_for_documents(
            session, [], reviewer_id=_TEACHER_ID
        ) == []
        assert landscape_store.accept_inferred_for_documents(
            session, ["", "  ", None], reviewer_id=_TEACHER_ID
        ) == []
        assert session.calls == []

    def test_reviewer_id_is_required(self):
        session = _RecordingSession()
        with pytest.raises(ValueError):
            landscape_store.accept_inferred_for_documents(
                session, [_DOC], reviewer_id=""
            )
        assert session.calls == []

    def test_updates_only_inferred_rows(self):
        """RR4: 教員が却下・再検討した行、既に確認済みの行、履歴には触らない。"""
        session = _RecordingSession([_db_row()])
        rows = landscape_store.accept_inferred_for_documents(
            session, [_DOC], reviewer_id=_TEACHER_ID, note="リリース前の確認画面で一括確認"
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "confirmed"
        sql, params = session.calls[0]
        assert "UPDATE landscape_placements" in sql
        assert "AND status = :inferred" in sql
        assert params["inferred"] == landscape_schema.STATUS_INFERRED
        assert params["confirmed"] == landscape_schema.STATUS_CONFIRMED
        # 却下・再検討・履歴の語彙が WHERE に紛れ込んでいない（inferred 限定であること）。
        for forbidden in (
            landscape_schema.STATUS_REJECTED,
            landscape_schema.STATUS_REVIEW_REQUIRED,
            landscape_schema.STATUS_SUPERSEDED,
        ):
            assert forbidden not in params.values()

    def test_empty_note_preserves_existing_review_note(self):
        """P4: 状態だけ変えたときに理由文を消さない（update_status と同じ規則）。"""
        session = _RecordingSession([_db_row()])
        landscape_store.accept_inferred_for_documents(
            session, [_DOC], reviewer_id=_TEACHER_ID
        )
        sql, params = session.calls[0]
        assert "CASE WHEN :note <> '' THEN :note ELSE review_note END" in sql
        assert params["note"] == ""

    def test_records_reviewer_identity(self):
        session = _RecordingSession([_db_row()])
        landscape_store.accept_inferred_for_documents(
            session, [_DOC], reviewer_id=_TEACHER_ID
        )
        sql, params = session.calls[0]
        assert "reviewed_by = CAST(:reviewer_id AS uuid)" in sql
        assert "reviewed_at = now()" in sql
        assert params["reviewer_id"] == _TEACHER_ID

    def test_function_has_no_delete_and_no_transaction_management(self):
        src = extract_function_source(STORE_SRC, "accept_inferred_for_documents")
        for forbidden in (
            "DELETE FROM",
            "delete(",
            "session.commit(",
            "session.rollback(",
            "session.close(",
        ):
            assert forbidden not in src


# ---------------------------------------------------------------------------
# 2〜4. API 層
# ---------------------------------------------------------------------------

pytestmark_api = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")


class _Concept:
    def __init__(self, cid: str, label: str):
        self.id = cid
        self.label = label


class _Region:
    def __init__(self, rid: str, label: str, concepts=()):
        self.id = rid
        self.label = label
        self.concepts = list(concepts)


class _Skeleton:
    def __init__(self, version: str = "2026.1"):
        self.version = version
        self.regions = [
            _Region("cosmology", "宇宙論・大規模構造", [_Concept("dark_matter", "暗黒物質")]),
        ]


class FakeSession:
    """route 層が自分で書く SELECT（documents / analysis runs）だけを答えるフェイク。"""

    def __init__(self, documents=None):
        self.documents = list(
            documents
            if documents is not None
            else [(_DOC, "重力波観測の系統誤差", "mat-1"), (_DOC_LOCKED, "他人の論文", "mat-2")]
        )
        self.commits = 0
        self.rollbacks = 0

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        if "FROM documents" in sql:
            ids = set(params.get("ids") or [])
            return _Result([row for row in self.documents if row[0] in ids])
        if "FROM document_analysis_runs" in sql:
            return _Result([("2026-08-04T09:00:00+00:00",)])
        raise AssertionError(f"unexpected SQL in route layer: {sql!r}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _placement_row(**overrides) -> dict:
    row = {
        "id": _PLACEMENT,
        "document_id": _DOC,
        "domain_key": _DOMAIN,
        "skeleton_version": "2026.1",
        "node_id": "cosmology",
        "node_kind": "region",
        "perspective": "subject",
        "weight": 0.82,
        "reason": "宇宙膨張の測定を主題にしている。",
        "evidence": [{"quote": "we measure the expansion rate", "claim_id": "claim_1"}],
        "status": "inferred",
        "provenance": "llm",
        "run_id": None,
        "created_by": "pipeline",
        "reviewed_by": None,
        "reviewed_at": None,
        "review_note": "",
        "created_at": "2026-08-04T09:00:00+00:00",
        "updated_at": "2026-08-04T09:00:00+00:00",
    }
    row.update(overrides)
    return row


def _keys_recursive(payload) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(str(key))
            found |= _keys_recursive(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= _keys_recursive(item)
    return found


@pytest.fixture
def client_and_teacher():
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_TEACHER, _create_token

    return TestClient(app), _create_token(_TEACHER_ID, "tea", "tea@x", ROLE_TEACHER)


@pytest.fixture
def env(monkeypatch):
    import routes.landscape as routes
    import services as services_module
    from core import atlas_store
    from core.deliberation import refs as refs_module

    session = FakeSession()
    monkeypatch.setattr(routes, "_session", lambda: session)
    monkeypatch.setattr(
        atlas_store, "list_domains",
        lambda _session: [{"domain_key": _DOMAIN, "domain_name": "宇宙物理"}],
    )
    monkeypatch.setattr(
        atlas_store, "load_learner_skeleton",
        lambda domain_key, _session=None: _Skeleton() if domain_key == _DOMAIN else None,
    )
    monkeypatch.setattr(
        refs_module, "document_run_artifacts",
        lambda document_id: {
            "landscape_placement": {
                "unplaced_domains": [
                    {"domain_key": _DOMAIN, "reason": "対象が一致しません"}
                ]
            }
        },
    )
    events: list[tuple] = []
    monkeypatch.setattr(
        services_module, "record_review_event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )
    monkeypatch.setattr(
        services_module, "get_accessible_course_data",
        lambda user_id, course_id: {
            "title": "宇宙物理入門",
            "sources": [{"material_id": "mat-1"}, {"material_id": "mat-2"}],
        },
    )
    monkeypatch.setattr(
        services_module, "list_course_source_document_ids",
        lambda course_data: [_DOC, _DOC_LOCKED],
    )

    def _access(user_id, ref):
        # mat-2 / _DOC_LOCKED は閲覧のみ（edit 不可）= accept の対象外になる。
        locked = str(ref) in (_DOC_LOCKED, "mat-2")
        return services_module.DocumentAccess(
            document_id=_DOC_LOCKED if locked else _DOC,
            source_path="mat-2" if locked else "mat-1",
            uploaded_by=None if locked else user_id,
            is_owner=not locked,
            can_view=True,
            can_edit=not locked,
        )

    monkeypatch.setattr(services_module, "resolve_document_access", _access)
    return {
        "routes": routes,
        "services": services_module,
        "session": session,
        "events": events,
        "monkeypatch": monkeypatch,
    }


@pytestmark_api
class TestCoursePlacementsGet:
    def test_envelope_and_pending_count(self, client_and_teacher, env):
        client, teacher = client_and_teacher
        routes = env["routes"]
        env["monkeypatch"].setattr(
            routes.landscape_store, "list_for_documents",
            lambda _s, ids, statuses=(): [
                _placement_row(),
                _placement_row(id=_PLACEMENT_2, status="confirmed", node_id="dark_matter"),
            ],
        )

        response = client.get(
            f"/api/admin/landscape/courses/{_COURSE}/placements",
            headers={"Authorization": "Bearer " + teacher},
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "course_id", "course_title", "cartridge_id", "documents", "domains",
            "placement_count", "pending_count", "source_document_count",
            "hidden_document_count", "editable",
        }
        assert body["course_title"] == "宇宙物理入門"
        assert body["placement_count"] == 2
        # RR6: 件数は事実として出す（未確認 = inferred の件数）。
        assert body["pending_count"] == 1
        assert body["editable"] is True

        docs = {d["document_id"]: d for d in body["documents"]}
        assert set(docs) == {_DOC, _DOC_LOCKED}
        assert docs[_DOC]["editable"] is True
        # edit 権限のない論文は表示されるが確認の対象外であることが分かる（RR7）。
        assert docs[_DOC_LOCKED]["editable"] is False
        # LS10: 配置できなかった分野は失敗ではなく信号として同梱する。
        assert docs[_DOC]["unplaced_domains"][0]["domain_name"] == "宇宙物理"
        assert docs[_DOC]["placements"][0]["node_label"] == "宇宙論・大規模構造"

    def test_no_raw_weight_or_confidence_keys(self, client_and_teacher, env):
        """RR6 / LS5: 生数値のキーごと出さない。"""
        client, teacher = client_and_teacher
        routes = env["routes"]
        env["monkeypatch"].setattr(
            routes.landscape_store, "list_for_documents",
            lambda _s, ids, statuses=(): [_placement_row()],
        )
        body = client.get(
            f"/api/admin/landscape/courses/{_COURSE}/placements",
            headers={"Authorization": "Bearer " + teacher},
        ).json()
        keys = _keys_recursive(body)
        assert "weight" not in keys
        assert "confidence" not in keys

    def test_unknown_course_is_404(self, client_and_teacher, env):
        client, teacher = client_and_teacher
        env["monkeypatch"].setattr(
            env["services"], "get_accessible_course_data", lambda user_id, course_id: None
        )
        response = client.get(
            f"/api/admin/landscape/courses/{_COURSE}/placements",
            headers={"Authorization": "Bearer " + teacher},
        )
        assert response.status_code == 404

    def test_student_is_rejected(self, client_and_teacher, env):
        from dependencies import ROLE_STUDENT, _create_token

        client, _teacher = client_and_teacher
        student = _create_token("11111111-1111-1111-1111-111111111111", "s", "s@x", ROLE_STUDENT)
        response = client.get(
            f"/api/admin/landscape/courses/{_COURSE}/placements",
            headers={"Authorization": "Bearer " + student},
        )
        assert response.status_code in (401, 403)


@pytestmark_api
class TestCoursePlacementsAccept:
    def test_accepts_inferred_and_audits_with_release_action(self, client_and_teacher, env):
        client, teacher = client_and_teacher
        routes = env["routes"]
        captured: dict = {}

        def _accept(_session, document_ids, *, reviewer_id, note=""):
            captured["document_ids"] = list(document_ids)
            captured["reviewer_id"] = reviewer_id
            captured["note"] = note
            return [_placement_row(status="confirmed")]

        env["monkeypatch"].setattr(
            routes.landscape_store, "accept_inferred_for_documents", _accept
        )
        response = client.post(
            f"/api/admin/landscape/courses/{_COURSE}/placements/accept",
            headers={"Authorization": "Bearer " + teacher},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["confirmed"] == 1
        # edit 権限のない論文1件は対象外として正直に返す（RR7）。
        assert body["skipped_documents"] == 1

        # RR4: 対象は edit できる document のみ。
        assert captured["document_ids"] == [_DOC]
        assert captured["reviewer_id"] == _TEACHER_ID
        # RR3: 一括確認であることを review_note に残す。
        assert captured["note"] == routes.RELEASE_ACCEPT_NOTE

        # RR3: 監査は個別レビュー（action="review"）と区別できる語彙で記帳する。
        assert len(env["events"]) == 1
        args, _kwargs = env["events"][0]
        from core.schema import AUDIT_ENTITY_LANDSCAPE_PLACEMENT

        assert args[0] == AUDIT_ENTITY_LANDSCAPE_PLACEMENT
        assert args[1] == _PLACEMENT
        assert args[2] == landscape_schema.STATUS_INFERRED
        assert args[3] == landscape_schema.STATUS_CONFIRMED
        assert args[5]["action"] == routes.RELEASE_ACCEPT_ACTION
        assert args[5]["action"] != "review"
        assert args[5]["course_id"] == _COURSE

    def test_nothing_to_accept_is_success_with_zero(self, client_and_teacher, env):
        client, teacher = client_and_teacher
        routes = env["routes"]
        env["monkeypatch"].setattr(
            routes.landscape_store, "accept_inferred_for_documents",
            lambda *args, **kwargs: [],
        )
        response = client.post(
            f"/api/admin/landscape/courses/{_COURSE}/placements/accept",
            headers={"Authorization": "Bearer " + teacher},
        )
        assert response.status_code == 200
        assert response.json()["confirmed"] == 0
        assert env["events"] == []

    def test_unknown_course_is_404(self, client_and_teacher, env):
        client, teacher = client_and_teacher
        env["monkeypatch"].setattr(
            env["services"], "get_accessible_course_data", lambda user_id, course_id: None
        )
        response = client.post(
            f"/api/admin/landscape/courses/{_COURSE}/placements/accept",
            headers={"Authorization": "Bearer " + teacher},
        )
        assert response.status_code == 404


class TestRouteInvariants:
    """新エンドポイントが不変条項を壊していないことの構造的検査。"""

    def test_no_delete_route_for_course_placements(self):
        assert "@router.delete" not in ROUTE_SRC
        assert 'methods=["DELETE"]' not in ROUTE_SRC

    def test_accept_uses_the_store_primitive_only(self):
        """route 層が status を直接 UPDATE しない（遷移の実体は store 側 = LS3）。"""
        src = extract_function_source(ROUTE_SRC, "accept_course_landscape_placements")
        assert "landscape_store.accept_inferred_for_documents" in src
        assert "UPDATE landscape_placements" not in src
        assert "STATUS_CONFIRMED" in src  # 監査の to_status に使うのみ

    def test_accept_targets_editable_documents_only(self):
        src = extract_function_source(ROUTE_SRC, "accept_course_landscape_placements")
        assert "editable_ids" in src
        assert "sorted(editable_ids)" in src

    def test_permission_failure_is_not_403(self):
        """LS6 の作法（存在を漏らさない 404 に統一。権限外 document は静かに除外）。"""
        src = extract_function_source(ROUTE_SRC, "_course_source_access")
        assert "status_code=404" in src
        assert "status_code=403" not in src
        assert "HTTPException(status_code=403" not in src