"""出口の弁 — export bundle の権限・来歴・監査、および group メンバーの email 非開示。

対象（六つのレンズ §4 第1波 #8 = 是正 F10 / B4①。正本:
``docs/architecture/six_lenses_2026-09-10/04_community.md`` 提案4）:

| 経路 | 境界 |
|---|---|
| ``POST /api/courses/{cid}/export-bundle``   | コース閲覧可（所有 / editor・viewer 共有 / 公開テンプレート・グループ / SYSTEM_ADMIN） |
| ``POST /api/documents/{did}/export-bundle`` | document 成果物の閲覧可（所有 / public / group / 共有 / コース経由 / SYSTEM_ADMIN） |
| ``GET /api/groups/{gid}``                   | メンバーの email はグループ admin / SYSTEM_ADMIN にのみ返す |

検証観点:
  1. 負例: 無関係な TEACHER / 不明 ID は **404**（detail も同一で、対象の存在を判別させない）。
  2. 副作用前認可: 認可失敗時に DB セッションを開かない（ZIP 生成にも入らない）。
  3. 正例: 所有者・共有先・SYSTEM_ADMIN は通過し、束が生成され、監査が記帳される。
  4. 来歴: `manifest.provenance` に出所（object_type / object_id / 解析 run / 発行状態 /
     生成日時）が入り、**書き出した人は伏せられ**、confidence 等の数値スコアが入らない。
  5. 監査: `entity_type` はカタログ定数 ``AUDIT_ENTITY_EXPORT``、資料本文・逐語引用を載せない。
  6. group: 一般メンバーには email を返さず、表示名で足りる（フロントも列ごと省く）。

ハーネスは ``test_object_scope_authorization.py``（route 関数を直接呼び、モジュール属性を
monkeypatch する流儀）を踏襲する。外部 DB・MinIO・LLM への実接続は行わない。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

# routes/*.py 内部は `from services import ...` の裸 import に依存するため、
# テスト側も同じ名前空間（`services` / `routes.*`）で掴む（`api.services` は
# 別モジュールオブジェクトになり monkeypatch が効かない）。
import services as services_module  # noqa: E402
from routes import export as export_module  # noqa: E402
from routes import groups as groups_module  # noqa: E402
from routes import theory_components as tc_module  # noqa: E402
from services import DocumentAccess  # noqa: E402
from core.schema import AUDIT_ENTITY_EXPORT, AUDIT_ENTITY_TYPES  # noqa: E402
from tests.guardrail_helpers import extract_function_source  # noqa: E402

_BACKEND = Path(__file__).resolve().parents[1]
_EXPORT_SRC = (_BACKEND / "api" / "routes" / "export.py").read_text(encoding="utf-8")
_GROUPS_SRC = (_BACKEND / "api" / "routes" / "groups.py").read_text(encoding="utf-8")
_ADMIN_JS = (
    Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "admin.js"
).read_text(encoding="utf-8")

# autouse フィクスチャで差し替える前の実体（fail-soft 検証用）。
_REAL_SHARED_RELEASE_STATE = export_module._shared_release_state

OWNER = "11111111-1111-1111-1111-111111111111"
EDITOR = "22222222-2222-2222-2222-222222222222"
VIEWER = "33333333-3333-3333-3333-333333333333"
OTHER_TEACHER = "44444444-4444-4444-4444-444444444444"
ADMIN = "55555555-5555-5555-5555-555555555555"

DOC_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
DOC_MATERIAL_ID = "arXiv-2407.01221v2"
UNKNOWN_DOC_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"

COURSE_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
UNKNOWN_COURSE_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"

_DOCUMENTS = {
    DOC_ID: (DOC_ID, DOC_MATERIAL_ID, OWNER, {EDITOR}, {VIEWER}),
    DOC_MATERIAL_ID: (DOC_ID, DOC_MATERIAL_ID, OWNER, {EDITOR}, {VIEWER}),
}

_COURSES = {COURSE_ID: (OWNER, {EDITOR}, {VIEWER})}


def _user(user_id: str, role: str = "TEACHER") -> dict:
    return {"id": user_id, "username": "u", "email": "u@example.com", "role": role}


def _fake_resolve_document_access(user_id: str, ref: str) -> DocumentAccess:
    entry = _DOCUMENTS.get(ref)
    if entry is None:
        return DocumentAccess(document_id=None)
    doc_id, source_path, owner, editors, viewers = entry
    is_owner = user_id == owner
    can_edit = is_owner or user_id in editors
    can_view = can_edit or user_id in viewers
    return DocumentAccess(
        document_id=doc_id, source_path=source_path, uploaded_by=owner,
        is_owner=is_owner, can_view=can_view, can_edit=can_edit,
    )


def _fake_get_viewable_course_data(user_id: str, course_id: str):
    entry = _COURSES.get(course_id)
    if entry is None:
        return None
    owner, editors, viewers = entry
    if user_id == owner or user_id in editors or user_id in viewers:
        return {"title": "コース", "topics": [], "sources": []}
    return None


def _fake_get_accessible_course_data(user_id: str, course_id: str):
    # v1 のフィクスチャでは公開テンプレート経路を持たない（閲覧共有のみで判定する）。
    return None


def _no_course_fallback(document_id: str, current_user: dict):
    raise HTTPException(status_code=404, detail="Document not found")


@pytest.fixture(autouse=True)
def _patch_permission_sources(monkeypatch):
    """export.py が遅延 import で委譲する権限正本をフェイクへ差し替える。"""
    monkeypatch.setattr(services_module, "resolve_document_access", _fake_resolve_document_access)
    monkeypatch.setattr(services_module, "get_viewable_course_data", _fake_get_viewable_course_data)
    monkeypatch.setattr(services_module, "get_accessible_course_data", _fake_get_accessible_course_data)
    monkeypatch.setattr(tc_module, "_ensure_document_viewable", _no_course_fallback)
    # 来歴の V層読み出しは best-effort。テストでは未発行（None）に固定する。
    monkeypatch.setattr(export_module, "_shared_release_state", lambda *_a, **_k: None)


def _boom_session(*_args, **_kwargs):
    raise AssertionError("認可失敗時に DB セッションを開いてはならない")


class _Result:
    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def scalar(self):
        return None


class _FakeSession:
    """execute が常に空を返すセッション（ローダは空入力で素通しする）。"""

    def __init__(self):
        self.closed = False

    def execute(self, *_a, **_k):
        return _Result()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# 1. ゲート helper 単体
# ---------------------------------------------------------------------------


class TestExportGates:
    @pytest.mark.parametrize("user_id", [OWNER, EDITOR, VIEWER])
    def test_document_gate_allows_viewers(self, user_id):
        assert export_module._require_viewable_document_or_404(DOC_ID, _user(user_id)) == DOC_ID

    def test_document_gate_resolves_material_id_to_canonical(self):
        assert export_module._require_viewable_document_or_404(
            DOC_MATERIAL_ID, _user(OWNER)
        ) == DOC_ID

    def test_document_gate_allows_system_admin(self):
        assert export_module._require_viewable_document_or_404(
            DOC_ID, _user(ADMIN, role="SYSTEM_ADMIN")
        ) == DOC_ID

    def test_document_gate_404_for_unrelated_teacher_and_unknown_id(self):
        with pytest.raises(HTTPException) as unrelated:
            export_module._require_viewable_document_or_404(DOC_ID, _user(OTHER_TEACHER))
        with pytest.raises(HTTPException) as missing:
            export_module._require_viewable_document_or_404(UNKNOWN_DOC_ID, _user(OWNER))
        assert unrelated.value.status_code == missing.value.status_code == 404
        # 不在と権限なしで detail が同一（本文から存在を判別できない）。
        assert unrelated.value.detail == missing.value.detail

    def test_document_gate_uses_course_fallback_when_document_scope_fails(self, monkeypatch):
        called: list[str] = []

        def _allow(document_id, current_user):
            called.append(document_id)
            return [{"material_id": DOC_MATERIAL_ID}]

        monkeypatch.setattr(tc_module, "_ensure_document_viewable", _allow)
        assert export_module._require_viewable_document_or_404(
            DOC_ID, _user(OTHER_TEACHER)
        ) == DOC_ID
        assert called == [DOC_ID]

    @pytest.mark.parametrize("user_id", [OWNER, EDITOR, VIEWER])
    def test_course_gate_allows_viewers(self, user_id):
        assert export_module._require_viewable_course_or_404(COURSE_ID, _user(user_id)) is None

    def test_course_gate_allows_accessible_public_template(self, monkeypatch):
        monkeypatch.setattr(
            services_module, "get_accessible_course_data",
            lambda _uid, _cid: {"title": "公開テンプレート"},
        )
        assert export_module._require_viewable_course_or_404(
            COURSE_ID, _user(OTHER_TEACHER)
        ) is None

    def test_course_gate_allows_system_admin(self):
        assert export_module._require_viewable_course_or_404(
            COURSE_ID, _user(ADMIN, role="SYSTEM_ADMIN")
        ) is None

    def test_course_gate_404_for_unrelated_teacher_and_unknown_id(self):
        with pytest.raises(HTTPException) as unrelated:
            export_module._require_viewable_course_or_404(COURSE_ID, _user(OTHER_TEACHER))
        with pytest.raises(HTTPException) as missing:
            export_module._require_viewable_course_or_404(UNKNOWN_COURSE_ID, _user(OTHER_TEACHER))
        assert unrelated.value.status_code == missing.value.status_code == 404
        assert unrelated.value.detail == missing.value.detail


# ---------------------------------------------------------------------------
# 2. エンドポイント — 副作用前認可
# ---------------------------------------------------------------------------


class TestEndpointsAuthorizeBeforeSideEffects:
    def test_course_export_denied_without_touching_db(self, monkeypatch):
        monkeypatch.setattr(export_module, "_pg_session", _boom_session)
        with pytest.raises(HTTPException) as exc:
            export_module.export_course_bundle(COURSE_ID, current_user=_user(OTHER_TEACHER))
        assert exc.value.status_code == 404

    def test_document_export_denied_without_touching_db(self, monkeypatch):
        monkeypatch.setattr(export_module, "_pg_session", _boom_session)
        with pytest.raises(HTTPException) as exc:
            export_module.export_document_bundle(DOC_ID, current_user=_user(OTHER_TEACHER))
        assert exc.value.status_code == 404

    def test_gate_precedes_session_in_source(self):
        for fn, gate in (
            ("export_course_bundle", "_require_viewable_course_or_404"),
            ("export_document_bundle", "_require_viewable_document_or_404"),
        ):
            src = extract_function_source(_EXPORT_SRC, fn)
            assert gate in src, f"{fn} がゲートを通っていない"
            assert src.index(gate) < src.index("_pg_session()"), (
                f"{fn}: 認可は DB セッション取得より先でなければならない"
            )
            assert src.index(gate) < src.index("_build_zip("), (
                f"{fn}: 認可は ZIP 生成より先でなければならない"
            )


# ---------------------------------------------------------------------------
# 3. エンドポイント — 正例（束の生成・来歴・監査）
# ---------------------------------------------------------------------------


@pytest.fixture
def _stub_export_io(monkeypatch):
    """DB を触るローダだけを空に差し替え、manifest / 監査呼び出しを捕捉する。"""
    captured: dict = {"manifests": [], "audits": []}

    monkeypatch.setattr(export_module, "_pg_session", lambda: _FakeSession())
    monkeypatch.setattr(
        export_module, "_load_course",
        lambda _s, cid: {
            "course_id": cid, "title": "コース", "description": "",
            "topics": [], "chapters": [], "sources": [],
        },
    )
    monkeypatch.setattr(
        export_module, "_load_document",
        lambda _s, did: {"document_id": did, "title": "論文", "status": "", "created_at": ""},
    )
    monkeypatch.setattr(export_module, "_get_document_ids_for_course", lambda _s, _cid: [DOC_ID])
    monkeypatch.setattr(export_module, "_load_analysis_artifacts", lambda _s, _ids: {})
    monkeypatch.setattr(export_module, "_load_latest_run_ids", lambda _s, _ids: {DOC_ID: "run-1"})

    real_manifest = export_module._build_manifest

    def _spy_manifest(**kwargs):
        out = real_manifest(**kwargs)
        captured["manifests"].append(out)
        return out

    monkeypatch.setattr(export_module, "_build_manifest", _spy_manifest)

    def _spy_audit(**kwargs):
        captured["audits"].append(kwargs)

    monkeypatch.setattr(export_module, "_record_export_audit", _spy_audit)
    return captured


class TestAuthorizedExportProducesBundle:
    def test_course_owner_gets_bundle_with_provenance_and_audit(self, _stub_export_io):
        resp = export_module.export_course_bundle(COURSE_ID, current_user=_user(OWNER))
        assert resp.media_type == "application/zip"

        manifest = _stub_export_io["manifests"][-1]
        prov = manifest["provenance"]
        assert prov["object_type"] == "course"
        assert prov["object_id"] == COURSE_ID
        assert prov["generated_at"]
        assert prov["content_source"] == "working_copy_head"
        assert prov["release_available"] is False
        assert prov["analysis_runs"] == [{"document_id": DOC_ID, "analysis_run_id": "run-1"}]

        audit = _stub_export_io["audits"][-1]
        assert audit["scope_type"] == "course"
        assert audit["scope_id"] == COURSE_ID
        assert audit["user_id"] == OWNER

    def test_document_owner_gets_bundle_with_provenance_and_audit(self, _stub_export_io):
        resp = export_module.export_document_bundle(DOC_ID, current_user=_user(OWNER))
        assert resp.media_type == "application/zip"

        prov = _stub_export_io["manifests"][-1]["provenance"]
        assert prov["object_type"] == "document"
        assert prov["object_id"] == DOC_ID

        audit = _stub_export_io["audits"][-1]
        assert audit["scope_type"] == "document"
        assert audit["scope_id"] == DOC_ID
        assert audit["user_id"] == OWNER


# ---------------------------------------------------------------------------
# 4. 来歴ブロックの中身
# ---------------------------------------------------------------------------


class TestProvenanceBlock:
    def _build(self, **overrides):
        kwargs = {
            "scope_type": "document",
            "scope_id": DOC_ID,
            "document_ids": [DOC_ID],
            "run_ids": {DOC_ID: "run-1"},
            "options": {
                "include_source_snippets": True,
                "include_review_fields": True,
                "include_debug_data": False,
                "include_llm_raw_outputs": False,
                "include_ndjson": False,
            },
        }
        kwargs.update(overrides)
        return export_module._build_provenance(**kwargs)

    def test_exporter_identity_is_not_disclosed_in_bundle(self):
        prov = self._build()
        assert prov["exported_by"] == {"disclosed": False, "recorded_in": "audit_log"}
        # user_id そのものが束の中に現れない。
        assert OWNER not in repr(prov)

    def test_no_numeric_scores_in_provenance(self):
        prov = self._build()
        for banned in ("confidence", "weight", "score"):
            assert banned not in repr(prov).lower(), f"provenance に {banned} を載せない"

    def test_release_state_is_projected_when_published(self, monkeypatch):
        monkeypatch.setattr(
            export_module, "_shared_release_state",
            lambda *_a, **_k: {
                "active_release_id": "rel-1", "latest_version_no": 3,
                "lifecycle": "active", "updated_at": "2026-09-10T00:00:00+00:00",
                "delete_scheduled_by": ADMIN,  # 個人 id は投影しない
            },
        )
        prov = self._build()
        assert prov["release_available"] is True
        assert prov["shared_release"]["active_release_id"] == "rel-1"
        assert prov["shared_release"]["latest_version_no"] == 3
        assert ADMIN not in repr(prov["shared_release"])

    def test_release_lookup_failure_is_fail_soft(self, monkeypatch):
        """V層の読み出しが落ちても書き出しは止めない（来歴だけが欠ける）。"""
        from core.versioning import releases as _vreleases

        def _boom(*_a, **_k):
            raise RuntimeError("db down")

        monkeypatch.setattr(_vreleases, "get_state", _boom)
        # autouse フィクスチャは export_module 側を固定しているため、
        # import 時に掴んだ実体を直接呼んで例外吸収を検証する。
        assert _REAL_SHARED_RELEASE_STATE("document", DOC_ID) is None

    def test_manifest_carries_provenance_and_keeps_existing_keys(self):
        manifest = export_module._build_manifest(
            export_id="export_test",
            scope_type="document",
            scope_id=DOC_ID,
            material_ids=[],
            document_ids=[DOC_ID],
            claims=[],
            dsl_graph={"nodes": [], "edges": []},
            components=[],
            component_graph={"nodes": [], "edges": []},
            evidence_snippets=[],
            options={},
            provenance=self._build(),
        )
        assert manifest["provenance"]["object_id"] == DOC_ID
        # 既存キーは不変（additive）。
        for key in ("export_schema_version", "exported_at", "scope", "files", "counts"):
            assert key in manifest

    def test_manifest_provenance_defaults_to_empty(self):
        manifest = export_module._build_manifest(
            export_id="export_test",
            scope_type="course",
            scope_id=COURSE_ID,
            material_ids=[],
            document_ids=[],
            claims=[],
            dsl_graph={},
            components=[],
            component_graph={},
            evidence_snippets=[],
            options={},
        )
        assert manifest["provenance"] == {}

    def test_readme_documents_provenance_in_japanese(self):
        readme = export_module._README_TEMPLATE
        assert "来歴" in readme
        assert "provenance" in readme
        assert "review_status" in readme


# ---------------------------------------------------------------------------
# 5. 監査記帳
# ---------------------------------------------------------------------------


class TestExportAudit:
    def test_catalog_registers_export_entity_type(self):
        assert AUDIT_ENTITY_EXPORT == "export"
        assert AUDIT_ENTITY_EXPORT in AUDIT_ENTITY_TYPES

    def test_audit_uses_catalog_constant_and_omits_document_text(self, monkeypatch):
        calls: list[tuple] = []
        monkeypatch.setattr(
            services_module, "record_review_event",
            lambda *args: calls.append(args),
        )
        export_module._record_export_audit(
            scope_type="document",
            scope_id=DOC_ID,
            export_id="export_x",
            document_ids=[DOC_ID],
            options={"include_source_snippets": True},
            user_id=OWNER,
        )
        assert len(calls) == 1
        entity_type, entity_id, old_status, new_status, user_id, metadata = calls[0]
        assert entity_type == AUDIT_ENTITY_EXPORT
        assert entity_id == DOC_ID
        assert (old_status, new_status) == ("", "exported")
        assert user_id == OWNER
        assert metadata["action"] == "exported"
        assert metadata["object_type"] == "document"
        assert metadata["export_id"] == "export_x"
        assert metadata["document_ids"] == [DOC_ID]
        # 資料本文・逐語引用は監査に載せない。
        for banned in ("evidence_text", "claims", "snippet", "text"):
            assert banned not in metadata, f"監査 metadata に {banned} を入れない"

    def test_export_module_has_no_delete_route(self):
        assert "@router.delete" not in _EXPORT_SRC


# ---------------------------------------------------------------------------
# 6. group メンバーの email 非開示（是正 B4①）
# ---------------------------------------------------------------------------


class _GroupSession:
    """`_require_member` → group 行 → メンバー行 の3クエリだけを返すフェイク。"""

    def __init__(self, my_role: str):
        self.my_role = my_role

    def execute(self, sql, params=None):  # noqa: ANN001
        text = str(sql)
        if "SELECT role FROM group_members" in text:
            return _Rows([(self.my_role,)])
        if "FROM groups WHERE id" in text:
            import datetime as _dt

            now = _dt.datetime(2026, 9, 10)
            return _Rows([("g1", "ゼミ", "説明", "code-1", OWNER, now, now)])
        if "FROM group_members gm" in text and "JOIN users u" in text:
            import datetime as _dt

            now = _dt.datetime(2026, 9, 10)
            return _Rows([
                (OWNER, "owner-san", "owner@example.com", "admin", now),
                (VIEWER, "member-san", "member@example.com", "member", now),
            ])
        return _Rows([])

    def close(self):
        pass


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class TestGroupMemberEmailDisclosure:
    def _detail(self, monkeypatch, *, my_role: str, user: dict):
        monkeypatch.setattr(groups_module, "_pg_session", lambda: _GroupSession(my_role))
        return groups_module.get_group_detail("g1", current_user=user)

    def test_plain_member_does_not_see_emails(self, monkeypatch):
        detail = self._detail(monkeypatch, my_role="member", user=_user(VIEWER, role="STUDENT"))
        assert [m.email for m in detail.members] == ["", ""]
        # 表示名・ロールは返る（email 不在でも一覧は成立する）。
        assert [m.username for m in detail.members] == ["owner-san", "member-san"]
        assert [m.role for m in detail.members] == ["admin", "member"]
        assert "owner@example.com" not in repr(detail)

    def test_group_admin_sees_emails(self, monkeypatch):
        detail = self._detail(monkeypatch, my_role="admin", user=_user(OWNER))
        assert [m.email for m in detail.members] == ["owner@example.com", "member@example.com"]

    def test_system_admin_sees_emails_even_as_plain_member(self, monkeypatch):
        detail = self._detail(
            monkeypatch, my_role="member", user=_user(ADMIN, role="SYSTEM_ADMIN")
        )
        assert [m.email for m in detail.members] == ["owner@example.com", "member@example.com"]

    def test_source_gates_email_on_role(self):
        src = extract_function_source(_GROUPS_SRC, "get_group_detail")
        assert "show_email" in src
        assert "ROLE_SYSTEM_ADMIN" in src

    def test_frontend_omits_email_column_when_absent(self):
        assert "showEmail" in _ADMIN_JS
        # 列見出しとセルの両方を条件付きにする（片方だけだと列がずれる）。
        assert "showEmail ? '<th>メール</th>' : \"\"" in _ADMIN_JS
        assert "var emailCell = showEmail ?" in _ADMIN_JS
