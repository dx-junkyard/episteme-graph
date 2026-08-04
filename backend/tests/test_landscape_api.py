"""知識ランドスケープ — API 層（``api/routes/landscape.py``）のテスト。

正本: ``docs/features/knowledge_landscape_design.md`` §9（API 契約）・§0（LS1〜LS10）。

``tests/test_figure_presentation_api.py`` の流儀（実 app + ``TestClient`` + core 関数の
monkeypatch）を踏襲する。DB には触らず、``routes.landscape._session`` が返すセッションを
「documents / document_analysis_runs の SELECT だけを答えるフェイク」に差し替え、
``core.landscape.store`` / ``core.atlas_store`` / ``services`` の各関数は monkeypatch する。

検証観点:
  1. 教員 GET の envelope（placements / unplaced_domains / domains / last_run_at）—
     フロント（admin.js `openLandscapeModal`）が読むキーを固定する
  2. 権限 fail-closed（view / edit / 受講いずれも 404。403 を使わない — LS6）
  3. PATCH の監査記帳（``AUDIT_ENTITY_LANDSCAPE_PLACEMENT`` 定数）・不正遷移 422・不在 404
  4. propose の status 写像（200 payload / 429 上限 / 422 素材なし・骨格なし）と
     detail に数値を書かないこと（LS5）
  5. overview が本人可視 document のみを集約すること
  6. 学習者 DTO が可視 status のみ・コースのソースのみ・数値キー非漏洩（LS5）
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

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_DOC = "11111111-1111-1111-1111-111111111111"
_DOC_OTHER = "22222222-2222-2222-2222-222222222222"
_PLACEMENT = "44444444-4444-4444-4444-444444444444"
_TEACHER_ID = "22222222-2222-2222-2222-222222222222"
_STUDENT_ID = "11111111-1111-1111-1111-111111111111"
_DOMAIN = "astrophysics"
_LAST_RUN = "2026-08-04T09:00:00+00:00"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


# ---------------------------------------------------------------------------
# フェイク骨格（projection / overview は getattr でしか触らない）
# ---------------------------------------------------------------------------


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
            _Region("galaxies", "銀河・銀河団"),
        ]


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    """``routes/landscape.py`` が自分で書いている SELECT だけを答えるフェイク。

    store / atlas_store 経由の SQL はテスト側で monkeypatch するため、ここに来るのは
    documents（タイトル解決）と document_analysis_runs（最終生成日）の2種だけである。
    """

    def __init__(self, documents=None, last_run_at: str | None = _LAST_RUN):
        # documents: [(id, title, source_path)]
        self.documents = list(
            documents if documents is not None else [(_DOC, "重力波観測の系統誤差", "mat-1")]
        )
        self.last_run_at = last_run_at
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        if "FROM documents" in sql:
            ids = set(params.get("ids") or [])
            return _Result([row for row in self.documents if row[0] in ids])
        if "FROM document_analysis_runs" in sql:
            if self.last_run_at is None:
                return _Result([])
            return _Result([(self.last_run_at,)])
        raise AssertionError(f"unexpected SQL in route layer: {sql!r}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


@pytest.fixture
def client_and_tokens():
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_STUDENT, ROLE_TEACHER, _create_token

    client = TestClient(app)
    student = _create_token(_STUDENT_ID, "stu", "stu@x", ROLE_STUDENT)
    teacher = _create_token(_TEACHER_ID, "tea", "tea@x", ROLE_TEACHER)
    return client, student, teacher


@pytest.fixture
def env(monkeypatch):
    """共通の monkeypatch セット。テストごとに必要な部分だけ上書きして使う。"""
    import routes.landscape as routes
    import services as services_module
    from core import atlas_store

    session = FakeSession()
    monkeypatch.setattr(routes, "_session", lambda: session)
    monkeypatch.setattr(
        atlas_store,
        "list_domains",
        lambda _session: [{"domain_key": _DOMAIN, "domain_name": "宇宙物理"}],
    )
    monkeypatch.setattr(
        atlas_store, "load_learner_skeleton",
        lambda domain_key, _session=None: _Skeleton() if domain_key == _DOMAIN else None,
    )
    events: list[tuple] = []
    monkeypatch.setattr(
        services_module,
        "record_review_event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )
    monkeypatch.setattr(
        services_module,
        "resolve_document_access",
        lambda user_id, ref: services_module.DocumentAccess(
            document_id=_DOC, source_path="mat-1", uploaded_by=user_id,
            is_owner=True, can_view=True, can_edit=True,
        ),
    )
    _patch_artifacts(monkeypatch, {})
    return {"routes": routes, "session": session, "events": events, "monkeypatch": monkeypatch}


def _patch_artifacts(monkeypatch, stage_payload: dict) -> None:
    from core.deliberation import refs as refs_module

    monkeypatch.setattr(
        refs_module,
        "document_run_artifacts",
        lambda document_id: {"landscape_placement": stage_payload},
    )


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


# ---------------------------------------------------------------------------
# 1. 教員 GET の envelope
# ---------------------------------------------------------------------------


class TestAdminListPlacements:
    def test_envelope_contains_frontend_pinned_keys(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        routes = env["routes"]
        env["monkeypatch"].setattr(
            routes.landscape_store,
            "list_for_document",
            lambda _s, document_id, include_history=False: [_placement_row()],
        )
        _patch_artifacts(
            env["monkeypatch"],
            {"unplaced_domains": [{"domain_key": _DOMAIN, "reason": "対象が一致しません"}]},
        )

        response = client.get(
            f"/api/admin/landscape/documents/{_DOC}/placements", headers=_auth(teacher)
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "document_id", "title", "placements", "unplaced_domains", "domains", "last_run_at",
        }
        assert body["document_id"] == _DOC
        assert body["title"] == "重力波観測の系統誤差"
        assert body["last_run_at"] == _LAST_RUN

        placement = body["placements"][0]
        assert placement["node_label"] == "宇宙論・大規模構造"
        assert placement["domain_name"] == "宇宙物理"
        assert placement["perspective_label"] == "対象から"
        assert placement["weight_label"] == "強い関連"
        assert placement["status_label"] and placement["provenance_label"]
        assert placement["skeleton_version"] == "2026.1"
        # LS5: 生数値のキーごと出さない。
        assert "weight" not in placement and "confidence" not in placement

        # unplaced は domain_name を補って返す（LS10 の事実文をフロントが組める）。
        assert body["unplaced_domains"] == [
            {"domain_key": _DOMAIN, "domain_name": "宇宙物理", "reason": "対象が一致しません"}
        ]
        assert body["domains"] == [
            {"domain_key": _DOMAIN, "domain_name": "宇宙物理", "frozen_version": "2026.1"}
        ]

    def test_domains_omits_entries_without_a_frozen_skeleton(self, client_and_tokens, env):
        """骨格の無いドメインは domains から落とす（fail-soft・版を推測しない）。"""
        client, _student, teacher = client_and_tokens
        routes = env["routes"]
        env["monkeypatch"].setattr(
            routes.landscape_store,
            "list_for_document",
            lambda _s, document_id, include_history=False: [
                _placement_row(domain_key="retired_domain", node_id="ghost")
            ],
        )
        response = client.get(
            f"/api/admin/landscape/documents/{_DOC}/placements", headers=_auth(teacher)
        )
        assert response.status_code == 200
        body = response.json()
        assert body["domains"] == []
        # 行そのものは隠さない（P4）。地図に描けないことが分かる形（生 node_id）で残る。
        assert body["placements"][0]["node_label"] == "ghost"
        assert body["placements"][0]["region_id"] == ""

    def test_include_history_flag_is_forwarded_to_store(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        routes = env["routes"]
        seen: list[bool] = []

        def fake_list(_s, document_id, include_history=False):
            seen.append(include_history)
            return []

        env["monkeypatch"].setattr(routes.landscape_store, "list_for_document", fake_list)
        client.get(
            f"/api/admin/landscape/documents/{_DOC}/placements", headers=_auth(teacher)
        )
        client.get(
            f"/api/admin/landscape/documents/{_DOC}/placements?include_history=1",
            headers=_auth(teacher),
        )
        assert seen == [False, True]

    def test_store_receives_normalized_document_id_for_source_path_ref(
        self, client_and_tokens, env
    ):
        """``document_ref`` は source_path でも UUID に正規化してから store へ渡す。"""
        client, _student, teacher = client_and_tokens
        routes = env["routes"]
        seen: list[str] = []

        def fake_list(_s, document_id, include_history=False):
            seen.append(document_id)
            return []

        env["monkeypatch"].setattr(routes.landscape_store, "list_for_document", fake_list)
        response = client.get(
            "/api/admin/landscape/documents/mat-1/placements", headers=_auth(teacher)
        )
        assert response.status_code == 200
        assert seen == [_DOC]

    def test_requires_teacher(self, client_and_tokens):
        client, student, _teacher = client_and_tokens
        path = f"/api/admin/landscape/documents/{_DOC}/placements"
        assert client.get(path).status_code in (401, 403)
        assert client.get(path, headers=_auth(student)).status_code == 403

    def test_missing_document_is_404(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        import services as services_module

        env["monkeypatch"].setattr(
            services_module,
            "resolve_document_access",
            lambda user_id, ref: services_module.DocumentAccess(document_id=None),
        )
        response = client.get(
            f"/api/admin/landscape/documents/{_DOC}/placements", headers=_auth(teacher)
        )
        assert response.status_code == 404

    def test_non_viewable_document_is_404_not_403(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        import services as services_module

        env["monkeypatch"].setattr(
            services_module,
            "resolve_document_access",
            lambda user_id, ref: services_module.DocumentAccess(
                document_id=_DOC, can_view=False, can_edit=False
            ),
        )
        response = client.get(
            f"/api/admin/landscape/documents/{_DOC}/placements", headers=_auth(teacher)
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 2. PATCH（教員の確認 / 却下 / 再検討）
# ---------------------------------------------------------------------------


class TestAdminPatchPlacement:
    def _patch_store(self, env, *, existing=None, updated=None, error=None):
        routes = env["routes"]
        env["monkeypatch"].setattr(
            routes.landscape_store,
            "get_placement",
            lambda _s, pid: existing if existing is not None else None,
        )

        def fake_update(_s, pid, new_status, *, reviewer_id, note=""):
            if error is not None:
                raise error
            return updated

        env["monkeypatch"].setattr(routes.landscape_store, "update_status", fake_update)

    def test_confirm_records_audit_with_catalog_constant(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        from core.schema import AUDIT_ENTITY_LANDSCAPE_PLACEMENT

        self._patch_store(
            env,
            existing=_placement_row(),
            updated=_placement_row(status="confirmed", reviewed_by=_TEACHER_ID),
        )
        response = client.patch(
            f"/api/admin/landscape/placements/{_PLACEMENT}",
            json={"status": "confirmed"},
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        placement = response.json()["placement"]
        assert placement["status"] == "confirmed"
        assert placement["provenance_label"] == "教員確認済み"
        assert "weight" not in placement

        assert len(env["events"]) == 1
        args, _kwargs = env["events"][0]
        assert args[0] == AUDIT_ENTITY_LANDSCAPE_PLACEMENT
        assert args[1] == _PLACEMENT
        assert args[2] == "inferred"
        assert args[3] == "confirmed"
        assert args[4] == _TEACHER_ID
        assert args[5]["action"] == "review"
        assert args[5]["document_id"] == _DOC
        assert env["session"].commits == 1

    def test_review_note_is_forwarded(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        routes = env["routes"]
        seen: list[tuple] = []

        env["monkeypatch"].setattr(
            routes.landscape_store, "get_placement", lambda _s, pid: _placement_row()
        )

        def fake_update(_s, pid, new_status, *, reviewer_id, note=""):
            seen.append((new_status, reviewer_id, note))
            return _placement_row(status=new_status, review_note=note)

        env["monkeypatch"].setattr(routes.landscape_store, "update_status", fake_update)
        response = client.patch(
            f"/api/admin/landscape/placements/{_PLACEMENT}",
            json={"status": "review_required", "review_note": "観測観点の方が近い"},
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        assert seen == [("review_required", _TEACHER_ID, "観測観点の方が近い")]

    def test_invalid_transition_is_422(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        self._patch_store(
            env,
            existing=_placement_row(),
            error=ValueError("invalid status transition target: 'superseded'"),
        )
        response = client.patch(
            f"/api/admin/landscape/placements/{_PLACEMENT}",
            json={"status": "superseded"},
            headers=_auth(teacher),
        )
        assert response.status_code == 422
        assert "superseded" in response.json()["detail"]
        assert env["events"] == []
        assert env["session"].rollbacks == 1

    def test_missing_placement_is_404(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        self._patch_store(env, existing=None)
        response = client.patch(
            f"/api/admin/landscape/placements/{_PLACEMENT}",
            json={"status": "confirmed"},
            headers=_auth(teacher),
        )
        assert response.status_code == 404
        assert env["events"] == []

    def test_vanished_placement_during_update_is_404(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        self._patch_store(env, existing=_placement_row(), updated=None)
        response = client.patch(
            f"/api/admin/landscape/placements/{_PLACEMENT}",
            json={"status": "confirmed"},
            headers=_auth(teacher),
        )
        assert response.status_code == 404
        assert env["events"] == []

    def test_requires_edit_permission(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        import services as services_module

        self._patch_store(env, existing=_placement_row(), updated=_placement_row())
        env["monkeypatch"].setattr(
            services_module,
            "resolve_document_access",
            lambda user_id, ref: services_module.DocumentAccess(
                document_id=_DOC, can_view=True, can_edit=False
            ),
        )
        response = client.patch(
            f"/api/admin/landscape/placements/{_PLACEMENT}",
            json={"status": "confirmed"},
            headers=_auth(teacher),
        )
        assert response.status_code == 404
        assert env["events"] == []

    def test_requires_teacher(self, client_and_tokens):
        client, student, _teacher = client_and_tokens
        path = f"/api/admin/landscape/placements/{_PLACEMENT}"
        assert client.patch(path, json={"status": "confirmed"}).status_code in (401, 403)
        assert client.patch(
            path, json={"status": "confirmed"}, headers=_auth(student)
        ).status_code == 403


# ---------------------------------------------------------------------------
# 3. propose（教員の明示操作。ビルダー payload → HTTP 写像）
# ---------------------------------------------------------------------------


class TestAdminPropose:
    def _patch_builder(self, env, payload):
        routes = env["routes"]
        calls: list[tuple] = []

        def fake_build(document_id, *, artifacts=None, run_id=None, created_by="pipeline"):
            calls.append((document_id, created_by))
            return payload

        env["monkeypatch"].setattr(
            routes.landscape_builder, "build_and_store_placements", fake_build
        )
        return calls

    def test_empty_body_and_missing_body_both_return_payload(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        payload = {
            "status": "completed", "created": 2, "superseded": 1,
            "skipped_existing": 0, "placements_total": 3, "llm_calls": 1,
            "unplaced_domains": [{"domain_key": "particle_physics", "reason": "対象が異なる"}],
        }
        calls = self._patch_builder(env, payload)
        path = f"/api/admin/landscape/documents/{_DOC}/placements/propose"

        empty_body = client.post(path, json={}, headers=_auth(teacher))
        assert empty_body.status_code == 200
        assert empty_body.json() == payload

        no_body = client.post(path, headers=_auth(teacher))
        assert no_body.status_code == 200
        assert no_body.json() == payload

        assert [c[0] for c in calls] == [_DOC, _DOC]
        # 手動再提案の帰属は教員（パイプラインの 'pipeline' と区別できる）。
        assert all(c[1].startswith("teacher") for c in calls)
        assert len(env["events"]) == 2

    def test_daily_limit_is_429_without_numbers(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        self._patch_builder(
            env,
            {
                "status": "completed",
                "skipped_by_limit": True,
                "skipped_reason": "daily_call_limit_reached",
            },
        )
        response = client.post(
            f"/api/admin/landscape/documents/{_DOC}/placements/propose",
            json={},
            headers=_auth(teacher),
        )
        assert response.status_code == 429
        detail = response.json()["detail"]
        assert detail == "本日のAI呼び出し上限に達しました。明日以降に再実行できます。"
        # LS5: 上限値・件数を事実文に書かない。
        assert not any(ch.isdigit() for ch in detail)
        assert env["events"] == []

    @pytest.mark.parametrize(
        "reason,expected",
        [
            ("no_frozen_skeleton", "凍結済みの分野マップ骨格がありません。"),
            ("no_source_material", "この教材には配置に使える解析素材がありません。"),
        ],
    )
    def test_blocking_skip_reasons_are_422(self, client_and_tokens, env, reason, expected):
        client, _student, teacher = client_and_tokens
        self._patch_builder(env, {"status": "completed", "skipped_reason": reason})
        response = client.post(
            f"/api/admin/landscape/documents/{_DOC}/placements/propose",
            json={},
            headers=_auth(teacher),
        )
        assert response.status_code == 422
        assert response.json()["detail"] == expected
        assert env["events"] == []

    def test_degraded_llm_failure_stays_200(self, client_and_tokens, env):
        """LLM 失敗（llm_call_failed）は正直に記録された payload をそのまま 200 で返す。"""
        client, _student, teacher = client_and_tokens
        payload = {
            "status": "completed", "created": 0, "llm_calls": 1,
            "skipped_reason": "llm_call_failed", "unplaced_domains": [],
        }
        self._patch_builder(env, payload)
        response = client.post(
            f"/api/admin/landscape/documents/{_DOC}/placements/propose",
            json={},
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        assert response.json()["skipped_reason"] == "llm_call_failed"

    def test_builder_exception_is_502(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        routes = env["routes"]

        def boom(*_args, **_kwargs):
            raise RuntimeError("agent exploded")

        env["monkeypatch"].setattr(
            routes.landscape_builder, "build_and_store_placements", boom
        )
        response = client.post(
            f"/api/admin/landscape/documents/{_DOC}/placements/propose",
            json={},
            headers=_auth(teacher),
        )
        assert response.status_code == 502
        assert env["events"] == []

    def test_requires_edit_permission(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        import services as services_module

        calls = self._patch_builder(env, {"status": "completed"})
        env["monkeypatch"].setattr(
            services_module,
            "resolve_document_access",
            lambda user_id, ref: services_module.DocumentAccess(
                document_id=_DOC, can_view=True, can_edit=False
            ),
        )
        response = client.post(
            f"/api/admin/landscape/documents/{_DOC}/placements/propose",
            json={},
            headers=_auth(teacher),
        )
        assert response.status_code == 404
        assert calls == []

    def test_requires_teacher(self, client_and_tokens):
        client, student, _teacher = client_and_tokens
        path = f"/api/admin/landscape/documents/{_DOC}/placements/propose"
        assert client.post(path, json={}).status_code in (401, 403)
        assert client.post(path, json={}, headers=_auth(student)).status_code == 403


# ---------------------------------------------------------------------------
# 4. overview（本人可視 document のノード別集約）
# ---------------------------------------------------------------------------


class TestAdminOverview:
    def test_groups_visible_placements_per_node(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        import services as services_module

        routes = env["routes"]
        env["session"].documents = [
            (_DOC, "重力波観測の系統誤差", "mat-1"),
            (_DOC_OTHER, "銀河団の質量関数", "mat-2"),
        ]
        env["monkeypatch"].setattr(
            services_module, "list_visible_document_ids", lambda uid: {_DOC, _DOC_OTHER}
        )
        seen: list[tuple] = []

        def fake_list(_s, document_ids, *, statuses):
            seen.append((set(document_ids), tuple(statuses)))
            return [
                _placement_row(),
                _placement_row(
                    id="55555555-5555-5555-5555-555555555555",
                    document_id=_DOC_OTHER, node_id="dark_matter",
                    perspective="method", weight=0.2, status="confirmed",
                ),
                # 骨格に無いノードは地図に描けないので集約に載せない。
                _placement_row(
                    id="66666666-6666-6666-6666-666666666666", node_id="ghost_node"
                ),
                # 別ドメインの行は domain_key で落ちる。
                _placement_row(
                    id="77777777-7777-7777-7777-777777777777",
                    domain_key="particle_physics", node_id="cosmology",
                ),
            ]

        env["monkeypatch"].setattr(routes.landscape_store, "list_for_documents", fake_list)

        response = client.get(
            f"/api/admin/landscape/overview?domain_key={_DOMAIN}", headers=_auth(teacher)
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "domain_key", "domain_name", "skeleton_version", "corpus", "nodes"
        }
        assert body["domain_key"] == _DOMAIN
        assert body["domain_name"] == "宇宙物理"
        assert body["skeleton_version"] == "2026.1"
        assert body["corpus"] == {
            "visible_document_count": 2, "placed_document_count": 2
        }
        # 骨格順（region → 配下 concept）で並ぶ。documents が無いノードは出ない。
        assert [n["node_id"] for n in body["nodes"]] == ["cosmology", "dark_matter"]
        cosmology = body["nodes"][0]
        assert cosmology["label"] == "宇宙論・大規模構造"
        assert cosmology["region_id"] == "cosmology"
        assert cosmology["documents"] == [
            {
                "document_id": _DOC,
                "title": "重力波観測の系統誤差",
                "perspective_label": "対象から",
                "weight_label": "強い関連",
                "status": "inferred",
            }
        ]
        assert body["nodes"][1]["documents"][0]["weight_label"] == "弱い関連"
        assert body["nodes"][1]["region_id"] == "cosmology"

        # 可視集合以外を store に渡さない（LS6）。live 語彙のみ集約する。
        visible, statuses = seen[0]
        assert visible == {_DOC, _DOC_OTHER}
        assert "superseded" not in statuses

        # LS5: 生数値は返さない。
        assert "weight" not in _keys_recursive(body)
        assert "confidence" not in _keys_recursive(body)

    def test_unknown_domain_is_404(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        import services as services_module

        env["monkeypatch"].setattr(
            services_module, "list_visible_document_ids", lambda uid: {_DOC}
        )
        response = client.get(
            "/api/admin/landscape/overview?domain_key=not_a_domain", headers=_auth(teacher)
        )
        assert response.status_code == 404

    def test_missing_domain_key_is_404(self, client_and_tokens, env):
        client, _student, teacher = client_and_tokens
        import services as services_module

        env["monkeypatch"].setattr(
            services_module, "list_visible_document_ids", lambda uid: {_DOC}
        )
        response = client.get("/api/admin/landscape/overview", headers=_auth(teacher))
        assert response.status_code == 404

    def test_requires_teacher(self, client_and_tokens):
        client, student, _teacher = client_and_tokens
        path = f"/api/admin/landscape/overview?domain_key={_DOMAIN}"
        assert client.get(path).status_code in (401, 403)
        assert client.get(path, headers=_auth(student)).status_code == 403


# ---------------------------------------------------------------------------
# 5. 学習者 API（§9.2）
# ---------------------------------------------------------------------------


class TestLearnerLandscape:
    def _patch_course(self, env, course_data, document_ids):
        import services as services_module

        env["monkeypatch"].setattr(
            services_module,
            "get_accessible_course_data",
            lambda uid, cid: course_data,
        )
        env["monkeypatch"].setattr(
            services_module,
            "list_course_source_document_ids",
            lambda data: set(document_ids),
        )

    def test_enrollment_gate_is_404(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        self._patch_course(env, None, set())
        response = client.get(
            "/api/learning/courses/course-1/landscape", headers=_auth(student)
        )
        assert response.status_code == 404

    def test_returns_visible_statuses_only_and_no_numeric_keys(
        self, client_and_tokens, env
    ):
        client, student, _teacher = client_and_tokens
        routes = env["routes"]
        from core.landscape import schema as landscape_schema

        self._patch_course(
            env,
            {
                "cartridge_id": _DOMAIN,
                "sources": [{"material_id": "mat-1"}, {"material_id": "mat-2"}],
            },
            {_DOC, _DOC_OTHER},
        )
        env["session"].documents = [
            (_DOC, "重力波観測の系統誤差", "mat-1"),
            (_DOC_OTHER, "銀河団の質量関数", "mat-2"),
        ]
        seen: list[tuple] = []

        def fake_list(_s, document_ids, *, statuses):
            seen.append((set(document_ids), tuple(statuses)))
            return [
                _placement_row(),
                _placement_row(
                    id="55555555-5555-5555-5555-555555555555",
                    document_id=_DOC_OTHER, node_id="galaxies", status="confirmed",
                ),
                # 呼び出し側のフィルタ漏れがあっても projection が二重に落とす。
                _placement_row(
                    id="66666666-6666-6666-6666-666666666666", status="rejected"
                ),
            ]

        env["monkeypatch"].setattr(routes.landscape_store, "list_for_documents", fake_list)

        response = client.get(
            "/api/learning/courses/course-1/landscape", headers=_auth(student)
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "course_id", "course_domain_key", "domains", "documents", "corpus"
        }
        assert body["course_id"] == "course-1"
        assert body["course_domain_key"] == _DOMAIN
        assert body["domains"] == [
            {
                "domain_key": _DOMAIN,
                "domain_name": "宇宙物理",
                "frozen_version": "2026.1",
                "is_course_map": True,
            }
        ]
        # コースの sources 順（mat-1 → mat-2）を保つ。
        assert [d["document_id"] for d in body["documents"]] == [_DOC, _DOC_OTHER]
        assert body["documents"][0]["title"] == "重力波観測の系統誤差"
        assert body["corpus"] == {
            "source_document_count": 2, "placed_document_count": 2
        }

        statuses = seen[0][1]
        assert set(statuses) == set(landscape_schema.LEARNER_VISIBLE_STATUSES)
        assert "rejected" not in statuses and "superseded" not in statuses

        placement = body["documents"][0]["placements"][0]
        assert placement["provenance_label"] == "AIによる推定（未確認）"
        assert placement["weight_label"] == "強い関連"
        assert placement["evidence"] == [{"quote": "we measure the expansion rate"}]

        keys = _keys_recursive(body)
        assert "weight" not in keys
        assert "confidence" not in keys
        # 学習者 DTO は claim_id を落とす（chunk 遡及は Phase 2）。
        assert "claim_id" not in keys

    def test_only_course_source_documents_are_queried(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        routes = env["routes"]
        self._patch_course(
            env, {"sources": [{"material_id": "mat-1"}]}, {_DOC}
        )
        seen: list[set] = []

        def fake_list(_s, document_ids, *, statuses):
            seen.append(set(document_ids))
            return []

        env["monkeypatch"].setattr(routes.landscape_store, "list_for_documents", fake_list)
        response = client.get(
            "/api/learning/courses/course-1/landscape", headers=_auth(student)
        )
        assert response.status_code == 200
        assert seen == [{_DOC}]

    def test_empty_course_returns_200_with_empty_structure(self, client_and_tokens, env):
        client, student, _teacher = client_and_tokens
        routes = env["routes"]
        self._patch_course(env, {}, set())
        env["monkeypatch"].setattr(
            routes.landscape_store, "list_for_documents", lambda *a, **k: []
        )
        response = client.get(
            "/api/learning/courses/course-1/landscape", headers=_auth(student)
        )
        assert response.status_code == 200
        assert response.json() == {
            "course_id": "course-1",
            "course_domain_key": None,
            "domains": [],
            "documents": [],
            "corpus": {"source_document_count": 0, "placed_document_count": 0},
        }

    def test_course_map_domain_is_listed_even_without_placements(
        self, client_and_tokens, env
    ):
        """コースの地図は配置ゼロでも出所として提示する（骨格の版が事実行に必要）。"""
        client, student, _teacher = client_and_tokens
        routes = env["routes"]
        self._patch_course(env, {"cartridge_id": _DOMAIN, "sources": []}, set())
        env["monkeypatch"].setattr(
            routes.landscape_store, "list_for_documents", lambda *a, **k: []
        )
        response = client.get(
            "/api/learning/courses/course-1/landscape", headers=_auth(student)
        )
        body = response.json()
        assert body["course_domain_key"] == _DOMAIN
        assert body["domains"] == [
            {
                "domain_key": _DOMAIN,
                "domain_name": "宇宙物理",
                "frozen_version": "2026.1",
                "is_course_map": True,
            }
        ]
        assert body["documents"] == []

    def test_placements_on_missing_skeleton_nodes_are_dropped(
        self, client_and_tokens, env
    ):
        """現行凍結骨格に無いノードの配置は学習者側に出さない（地図に描けない）。"""
        client, student, _teacher = client_and_tokens
        routes = env["routes"]
        self._patch_course(env, {"sources": [{"material_id": "mat-1"}]}, {_DOC})
        env["monkeypatch"].setattr(
            routes.landscape_store,
            "list_for_documents",
            lambda *a, **k: [_placement_row(node_id="ghost_node")],
        )
        response = client.get(
            "/api/learning/courses/course-1/landscape", headers=_auth(student)
        )
        assert response.status_code == 200
        assert response.json()["documents"] == []

    def test_student_role_is_allowed(self, client_and_tokens, env):
        """学習者向けエンドポイントは STUDENT でも通る（受講ゲートのみ）。"""
        client, student, _teacher = client_and_tokens
        routes = env["routes"]
        self._patch_course(env, {"sources": []}, set())
        env["monkeypatch"].setattr(
            routes.landscape_store, "list_for_documents", lambda *a, **k: []
        )
        assert client.get(
            "/api/learning/courses/course-1/landscape", headers=_auth(student)
        ).status_code == 200
        assert client.get("/api/learning/courses/course-1/landscape").status_code in (
            401, 403
        )


# ---------------------------------------------------------------------------
# 6. ルータ登録・P4（削除 API を作らない）
# ---------------------------------------------------------------------------


class TestRouterRegistration:
    def test_landscape_paths_are_mounted(self):
        from api.main import app

        paths = app.openapi()["paths"]
        assert set(paths["/api/admin/landscape/documents/{document_ref}/placements"]) == {"get"}
        assert set(paths["/api/admin/landscape/placements/{placement_id}"]) == {"patch"}
        assert set(
            paths["/api/admin/landscape/documents/{document_ref}/placements/propose"]
        ) == {"post"}
        assert set(paths["/api/admin/landscape/overview"]) == {"get"}
        assert set(paths["/api/learning/courses/{course_id}/landscape"]) == {"get"}

    def test_no_delete_route_exists(self):
        """P4: 配置行を消す API を作らない（却下・置換は status 遷移で保持する）。"""
        import routes.landscape as routes

        for router in (routes.router, routes.learning_router):
            for route in router.routes:
                methods = getattr(route, "methods", set()) or set()
                assert "DELETE" not in methods, getattr(route, "path", "")
