"""help_kb Phase 3 — DB draft/freeze ストア（core/help_kb/store.py, migration 059）のテスト。

設計は docs/features/manual_help_kb_design.md §7-2。atlas_store.py / library/store.py の
draft/凍結/楽観ロックパターンを core/revision_store.py 経由で踏襲する。

構成:
  - フェイクセッション（``_ManualKbFake``）で DB を経由せず core/help_kb/store.py の
    SQL 面を検証する（draft シード冪等性・revision 楽観ロック・freeze 検証ゲート・
    配信ソース切替・版の append-only 性）。
  - core/help_kb/manual.py の配信ソース解決（files 既定 / db 明示切替 / DB 障害 fail-open）。
  - API（backend/api/routes/admin_assistant.py の help_kb_router）の権限 fail-closed・
    監査記帳・path traversal 拒否。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb import manual as kb_manual  # noqa: E402
from core.help_kb import store as kb_store  # noqa: E402
from core.help_kb import validator as kb_validator  # noqa: E402

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False


# ---------------------------------------------------------------------------
# フェイクセッション（manual_kb_drafts / manual_kb_versions / manual_kb_state）
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def scalar(self):
        row = self.fetchone()
        return row[0] if row else None


class ManualKbFake:
    """``manual_kb_drafts`` / ``manual_kb_versions`` / ``manual_kb_state`` のインメモリ実装。

    セッションとテーブルを兼ねる（commit/rollback/close は no-op）。
    """

    def __init__(self):
        self.drafts: dict[tuple[str, str], dict] = {}
        self.versions: list[dict] = []
        self.state: dict = {"serving_source": "files", "active_version_no": None, "updated_at": ""}
        self.calls: list[tuple[str, dict]] = []
        self.fallback: Callable[[str, dict], FakeResult] = lambda sql, p: (_ for _ in ()).throw(
            AssertionError(f"unhandled SQL: {sql}")
        )

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.calls.append((sql, p))
        return self._dispatch(sql, p)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    # -- dispatch --
    def _dispatch(self, sql: str, p: dict) -> FakeResult:  # noqa: C901
        if "pg_advisory_xact_lock" in sql:
            # フェイクは単一プロセス内で逐次実行されるため実ロックは不要。
            # freeze() が採番前にロックを取得していることの検証は calls ログで行う。
            return FakeResult([(None,)])
        if "manual_kb_drafts" in sql:
            return self._dispatch_drafts(sql, p)
        if "manual_kb_versions" in sql:
            return self._dispatch_versions(sql, p)
        if "manual_kb_state" in sql:
            return self._dispatch_state(sql, p)
        return self.fallback(sql, p)

    def _dispatch_drafts(self, sql: str, p: dict) -> FakeResult:  # noqa: C901
        if sql.startswith("SELECT 1 FROM manual_kb_drafts"):
            key = (p["audience"], p["file"])
            return FakeResult([(1,)] if key in self.drafts else [])
        if sql.startswith("SELECT revision FROM manual_kb_drafts"):
            key = (p["audience"], p["file"])
            row = self.drafts.get(key)
            return FakeResult([(row["revision"],)] if row else [])
        if sql.startswith("UPDATE manual_kb_drafts"):
            key = (p["audience"], p["file"])
            row = self.drafts.get(key)
            if not row or row["revision"] != int(p["expected_revision"]):
                return FakeResult(rowcount=0)
            row["content"] = p["content"]
            row["revision"] += 1
            row["updated_by"] = p.get("updated_by")
            row["updated_at"] = "updated"
            return FakeResult(rowcount=1)
        if sql.startswith("INSERT INTO manual_kb_drafts"):
            key = (p["audience"], p["file"])
            if key in self.drafts:
                return FakeResult(rowcount=0)
            self.drafts[key] = {
                "content": p["content"],
                "revision": 1,
                "updated_by": p.get("updated_by"),
                "updated_at": "created",
            }
            return FakeResult(rowcount=1)
        if "ORDER BY audience, file" in sql and "revision, updated_at, updated_by" in sql:
            rows = [
                (aud, file, d["content"], d["revision"], d["updated_at"], d["updated_by"])
                for (aud, file), d in sorted(self.drafts.items())
            ]
            return FakeResult(rows)
        if "ORDER BY audience, file" in sql:
            rows = [(aud, file, d["content"]) for (aud, file), d in sorted(self.drafts.items())]
            return FakeResult(rows)
        if "WHERE audience = :audience AND file = :file" in sql:
            key = (p["audience"], p["file"])
            d = self.drafts.get(key)
            if not d:
                return FakeResult([])
            return FakeResult(
                [(key[0], key[1], d["content"], d["revision"], d["updated_at"], d["updated_by"])]
            )
        raise AssertionError(f"unhandled manual_kb_drafts SQL: {sql}")

    def _dispatch_versions(self, sql: str, p: dict) -> FakeResult:
        if sql.startswith("SELECT COALESCE(MAX(version_no)"):
            next_no = (max((v["version_no"] for v in self.versions), default=0)) + 1
            return FakeResult([(next_no,)])
        if sql.startswith("INSERT INTO manual_kb_versions"):
            files = json.loads(p["files"]) if isinstance(p["files"], str) else p["files"]
            validation = (
                json.loads(p["validation"]) if isinstance(p["validation"], str) else p["validation"]
            )
            self.versions.append(
                {
                    "version_no": int(p["version_no"]),
                    "files": files,
                    "validation": validation,
                    "created_by": p.get("created_by"),
                    "created_at": None,
                }
            )
            return FakeResult(rowcount=1)
        if sql.startswith("SELECT files FROM manual_kb_versions"):
            row = next((r for r in self.versions if r["version_no"] == int(p["v"])), None)
            if not row:
                return FakeResult([])
            return FakeResult([(json.dumps(row["files"], ensure_ascii=False),)])
        if sql.startswith("SELECT version_no, created_at, created_by, files"):
            rows = sorted(self.versions, key=lambda r: -r["version_no"])
            return FakeResult(
                [
                    (r["version_no"], r["created_at"], r["created_by"], json.dumps(r["files"], ensure_ascii=False))
                    for r in rows
                ]
            )
        raise AssertionError(f"unhandled manual_kb_versions SQL: {sql}")

    def _dispatch_state(self, sql: str, p: dict) -> FakeResult:
        if sql.startswith("SELECT active_version_no FROM manual_kb_state"):
            return FakeResult([(self.state["active_version_no"],)])
        if sql.startswith("SELECT serving_source, active_version_no, updated_at"):
            return FakeResult(
                [(self.state["serving_source"], self.state["active_version_no"], self.state["updated_at"])]
            )
        if sql.startswith("UPDATE manual_kb_state"):
            if "serving_source = 'db'" in sql:
                self.state["serving_source"] = "db"
                self.state["active_version_no"] = int(p["version_no"])
            else:
                self.state["serving_source"] = p["source"]
            self.state["updated_at"] = "updated"
            return FakeResult(rowcount=1)
        raise AssertionError(f"unhandled manual_kb_state SQL: {sql}")


@pytest.fixture
def fake():
    return ManualKbFake()


@pytest.fixture(autouse=True)
def _patch_store_session(fake, monkeypatch):
    monkeypatch.setattr(kb_store, "get_session", lambda: fake)
    yield


@pytest.fixture(autouse=True)
def _clear_caches():
    kb_manual.clear_manual_cache()
    yield
    kb_manual.clear_manual_cache()


VALID_TEACHER_MD = """---
audience: teacher
---

## 使い方 {#usage}

本文です。
"""

VALID_STUDENT_MD = """---
audience: student
---

## 使い方 {#usage}

学生向け本文です。
"""

MISSING_ANCHOR_MD = """---
audience: teacher
---

## 使い方

anchor がありません。
"""

DENYLIST_STUDENT_MD = """---
audience: student
---

## 設定 {#config}

ADMIN_PASSWORD を使います。
"""

WRONG_AUDIENCE_MD = """---
audience: system_admin
---

## 使い方 {#usage}

audience 不一致。
"""


def _seed_draft(fake: ManualKbFake, audience: str, file: str, content: str, *, revision: int = 1) -> None:
    fake.drafts[(audience, file)] = {
        "content": content, "revision": revision, "updated_by": None, "updated_at": "",
    }


# ---------------------------------------------------------------------------
# draft: シード冪等性
# ---------------------------------------------------------------------------


class TestSeedDraftsFromServed:
    def _setup_files(self, tmp_path: Path, monkeypatch) -> Path:
        root = tmp_path / "manual"
        (root / "teacher").mkdir(parents=True)
        (root / "student").mkdir(parents=True)
        (root / "system_admin").mkdir(parents=True)
        (root / "teacher" / "a.md").write_text(VALID_TEACHER_MD, encoding="utf-8")
        (root / "student" / "b.md").write_text(VALID_STUDENT_MD, encoding="utf-8")
        monkeypatch.setattr(kb_store, "manual_root", lambda: root)
        return root

    def test_seed_creates_rows_from_served_files(self, fake, tmp_path, monkeypatch):
        self._setup_files(tmp_path, monkeypatch)
        result = kb_store.seed_drafts_from_served(user_id="u1")
        assert result["imported"] == 2
        assert result["skipped"] == 0
        drafts = kb_store.list_drafts()
        keys = {(d["audience"], d["file"]) for d in drafts}
        assert keys == {("teacher", "a.md"), ("student", "b.md")}

    def test_seed_is_idempotent_on_second_call(self, fake, tmp_path, monkeypatch):
        self._setup_files(tmp_path, monkeypatch)
        first = kb_store.seed_drafts_from_served(user_id="u1")
        assert first["imported"] == 2
        second = kb_store.seed_drafts_from_served(user_id="u1")
        assert second["imported"] == 0
        assert second["skipped"] == 2
        # 既存 draft の内容が上書きされていない（revision が変わらない）。
        drafts = {(d["audience"], d["file"]): d["revision"] for d in kb_store.list_drafts()}
        assert drafts[("teacher", "a.md")] == 1

    def test_seed_no_manual_dir_is_safe_and_empty(self, fake, tmp_path, monkeypatch):
        monkeypatch.setattr(kb_store, "manual_root", lambda: tmp_path / "nope")
        result = kb_store.seed_drafts_from_served()
        assert result == {"imported": 0, "skipped": 0, "errors": []}
        assert kb_store.list_drafts() == []


# ---------------------------------------------------------------------------
# draft: revision 楽観ロック
# ---------------------------------------------------------------------------


class TestDraftRevisionLock:
    def test_update_success_increments_revision(self, fake):
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        updated = kb_store.update_draft(
            "teacher", "a.md", VALID_TEACHER_MD + "\n追記", expected_revision=1, user_id="u1",
        )
        assert updated["revision"] == 2
        assert "追記" in updated["content"]

    def test_update_conflict_returns_current_revision(self, fake):
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD, revision=3)
        with pytest.raises(kb_store.DraftConflictError) as exc:
            kb_store.update_draft("teacher", "a.md", "new content", expected_revision=1)
        assert exc.value.current_revision == 3

    def test_update_not_found_raises(self, fake):
        with pytest.raises(kb_store.DraftNotFoundError):
            kb_store.update_draft("teacher", "missing.md", "x", expected_revision=1)

    def test_get_draft_missing_returns_none(self, fake):
        assert kb_store.get_draft("teacher", "missing.md") is None

    def test_invalid_audience_rejected(self, fake):
        with pytest.raises(ValueError):
            kb_store.get_draft("bogus_role", "a.md")

    def test_invalid_filename_extension_rejected(self, fake):
        with pytest.raises(ValueError):
            kb_store.get_draft("teacher", "a.txt")

    def test_invalid_filename_path_traversal_rejected(self, fake):
        with pytest.raises(ValueError):
            kb_store.get_draft("teacher", "../a.md")

    def test_invalid_filename_slash_rejected(self, fake):
        with pytest.raises(ValueError):
            kb_store.update_draft("teacher", "sub/a.md", "x", expected_revision=1)


# ---------------------------------------------------------------------------
# freeze: 凍結検証ゲート
# ---------------------------------------------------------------------------


class TestFreezeValidationGate:
    def test_freeze_rejects_missing_anchor(self, fake):
        _seed_draft(fake, "teacher", "a.md", MISSING_ANCHOR_MD)
        with pytest.raises(kb_store.FreezeValidationError) as exc:
            kb_store.freeze()
        assert any("anchor" in v or "{#anchor}" in v for v in exc.value.violations)
        assert fake.versions == []
        assert fake.state["serving_source"] == "files"

    def test_freeze_rejects_student_denylist(self, fake):
        _seed_draft(fake, "student", "s.md", DENYLIST_STUDENT_MD)
        with pytest.raises(kb_store.FreezeValidationError) as exc:
            kb_store.freeze()
        assert any("ADMIN_PASSWORD" in v for v in exc.value.violations)
        assert fake.versions == []

    def test_freeze_rejects_audience_mismatch(self, fake):
        _seed_draft(fake, "teacher", "w.md", WRONG_AUDIENCE_MD)
        with pytest.raises(kb_store.FreezeValidationError) as exc:
            kb_store.freeze()
        assert any("不一致" in v for v in exc.value.violations)
        assert fake.versions == []

    def test_freeze_violations_do_not_partially_apply(self, fake):
        """一部合格・一部違反でも versions/state は一切変更されない（部分適用しない）。"""
        _seed_draft(fake, "teacher", "good.md", VALID_TEACHER_MD)
        _seed_draft(fake, "student", "bad.md", DENYLIST_STUDENT_MD)
        with pytest.raises(kb_store.FreezeValidationError):
            kb_store.freeze()
        assert fake.versions == []
        assert kb_store.get_state()["serving_source"] == "files"

    def test_freeze_success_creates_version_and_switches_to_db(self, fake):
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        _seed_draft(fake, "student", "b.md", VALID_STUDENT_MD)
        result = kb_store.freeze(user_id="admin1")
        assert result["version_no"] == 1
        assert result["audience_file_counts"] == {"teacher": 1, "student": 1}
        state = kb_store.get_state()
        assert state["serving_source"] == "db"
        assert state["active_version_no"] == 1

    def test_freeze_appends_new_version_number_on_second_freeze(self, fake):
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        first = kb_store.freeze()
        assert first["version_no"] == 1
        second = kb_store.freeze()
        assert second["version_no"] == 2
        # append-only: 旧版が消えず両方残る。
        assert {v["version_no"] for v in fake.versions} == {1, 2}

    def test_freeze_with_no_drafts_produces_empty_version(self, fake):
        result = kb_store.freeze()
        assert result["version_no"] == 1
        assert result["audience_file_counts"] == {}

    def test_freeze_takes_advisory_lock_before_version_numbering(self, fake):
        """version_no 採番（MAX+1）の直列化用 advisory lock が採番より前に取得される。

        同時 freeze の PK 衝突（生 IntegrityError）を防ぐための
        ``pg_advisory_xact_lock`` 呼び出しが実在し、``SELECT COALESCE(MAX(version_no)``
        より先に発行されていることを確認する（``atlas_store`` の
        ``test_write_paths_take_domain_lock`` と同型の存在確認）。
        """
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        kb_store.freeze()
        lock_idx = next(
            (i for i, (sql, _p) in enumerate(fake.calls) if "pg_advisory_xact_lock" in sql), None
        )
        version_select_idx = next(
            (
                i
                for i, (sql, _p) in enumerate(fake.calls)
                if sql.startswith("SELECT COALESCE(MAX(version_no)")
            ),
            None,
        )
        assert lock_idx is not None, "freeze() が advisory lock を取得していない"
        assert version_select_idx is not None
        assert lock_idx < version_select_idx


# ---------------------------------------------------------------------------
# 配信ソース切替
# ---------------------------------------------------------------------------


class TestServingSourceSwitch:
    def test_switch_to_db_without_freeze_rejected(self, fake):
        with pytest.raises(kb_store.ServingSourceError):
            kb_store.set_serving_source("db")

    def test_switch_to_db_after_freeze_succeeds(self, fake):
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        kb_store.freeze()
        # freeze 自体が db へ切り替えるが、明示切替 API も同じ状態に到達できることを確認。
        state = kb_store.set_serving_source("db")
        assert state["serving_source"] == "db"

    def test_switch_back_to_files(self, fake):
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        kb_store.freeze()
        state = kb_store.set_serving_source("files")
        assert state["serving_source"] == "files"
        # active_version_no は履歴として残る（fail-open の後始末として files に戻すだけ）。
        assert state["active_version_no"] == 1

    def test_invalid_source_rejected(self, fake):
        with pytest.raises(ValueError):
            kb_store.set_serving_source("vector")

    def test_default_state_is_files(self, fake):
        state = kb_store.get_state()
        assert state == {"serving_source": "files", "active_version_no": None, "updated_at": ""}


# ---------------------------------------------------------------------------
# 版一覧: append-only（削除 API 不在）
# ---------------------------------------------------------------------------


class TestVersionsAppendOnly:
    def test_list_versions_desc_order(self, fake):
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        kb_store.freeze()
        kb_store.freeze()
        versions = kb_store.list_versions()
        assert [v["version_no"] for v in versions] == [2, 1]

    def test_store_module_has_no_delete_function(self):
        assert not hasattr(kb_store, "delete_version")
        assert not hasattr(kb_store, "delete_draft")


# ---------------------------------------------------------------------------
# manual.py: 配信ソース解決（files 既定 / db 明示切替 / fail-open）
# ---------------------------------------------------------------------------


class TestManualServingSourceResolution:
    def _setup_files(self, tmp_path: Path, monkeypatch) -> Path:
        root = tmp_path / "manual"
        (root / "teacher").mkdir(parents=True)
        (root / "student").mkdir(parents=True)
        (root / "system_admin").mkdir(parents=True)
        (root / "teacher" / "a.md").write_text(
            "---\naudience: teacher\n---\n\n## ファイル版 {#file-ver}\n\nfiles content\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(kb_manual, "manual_root", lambda: root)
        monkeypatch.setattr(kb_store, "manual_root", lambda: root)
        return root

    def test_default_serving_source_is_files(self, fake, tmp_path, monkeypatch):
        self._setup_files(tmp_path, monkeypatch)
        sections = kb_manual._sections_for_audience("teacher")
        bodies = " ".join(s["body"] for s in sections)
        assert "files content" in bodies

    def test_after_freeze_and_db_switch_serves_db_content(self, fake, tmp_path, monkeypatch):
        self._setup_files(tmp_path, monkeypatch)
        _seed_draft(
            fake, "teacher", "a.md",
            "---\naudience: teacher\n---\n\n## DB版 {#db-ver}\n\nDB content\n",
        )
        kb_store.freeze()
        kb_manual.clear_manual_cache()
        sections = kb_manual._sections_for_audience("teacher")
        bodies = " ".join(s["body"] for s in sections)
        assert "DB content" in bodies
        assert "files content" not in bodies

    def test_db_failure_falls_open_to_files(self, fake, tmp_path, monkeypatch):
        self._setup_files(tmp_path, monkeypatch)
        _seed_draft(fake, "teacher", "a.md", "---\naudience: teacher\n---\n\n## X {#x}\n\nY\n")
        kb_store.freeze()
        kb_manual.clear_manual_cache()

        def _boom():
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(kb_store, "get_state", _boom)
        sections = kb_manual._sections_for_audience("teacher")
        bodies = " ".join(s["body"] for s in sections)
        assert "files content" in bodies

    def test_clear_manual_cache_reflects_switch(self, fake, tmp_path, monkeypatch):
        self._setup_files(tmp_path, monkeypatch)
        # 最初は files
        sections = kb_manual._sections_for_audience("teacher")
        assert any("files content" in s["body"] for s in sections)
        # freeze + db 切替後、キャッシュクリアなしでは古い(files)索引のまま。
        _seed_draft(fake, "teacher", "a.md", "---\naudience: teacher\n---\n\n## X {#x}\n\nDB内容\n")
        kb_store.freeze()
        stale = kb_manual._sections_for_audience("teacher")
        assert any("files content" in s["body"] for s in stale)
        # clear_manual_cache すると反映される。
        kb_manual.clear_manual_cache()
        fresh = kb_manual._sections_for_audience("teacher")
        assert any("DB内容" in s["body"] for s in fresh)


# ---------------------------------------------------------------------------
# validator.validate_manual(): 配信中ソースへの拡張
# ---------------------------------------------------------------------------


class TestValidateManualServingSource:
    def test_validate_manual_uses_db_when_active(self, fake, tmp_path, monkeypatch):
        monkeypatch.setattr(kb_store, "manual_root", lambda: tmp_path / "nope")
        # freeze の凍結検証ゲート自体を通過する有効な draft を凍結し、validate_manual() が
        # ディスクではなく凍結済み DB content を検証していることを確認する。
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        kb_store.freeze()
        violations = kb_validator.validate_manual()
        assert violations == []

    def test_validate_manual_falls_open_to_files_on_db_error(self, monkeypatch):
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(kb_store, "get_state", _boom)
        # 例外を投げず、files ベースの検証結果（実 docs/manual、もしくは空）を返す。
        violations = kb_validator.validate_manual()
        assert isinstance(violations, list)


# ---------------------------------------------------------------------------
# API（backend/api/routes/admin_assistant.py の help_kb_router）
# ---------------------------------------------------------------------------


pytestmark_api = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入（full API は Docker 内で）")

_UID_ADMIN = "44444444-4444-4444-4444-444444444444"


def _headers(role: str, sub: str = _UID_ADMIN):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    return {"Authorization": "Bearer " + jwt.encode(payload, "test-secret-key", algorithm="HS256")}


@pytest.fixture
def api(fake, monkeypatch):
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI 未導入")
    from fastapi.testclient import TestClient
    from api.main import app
    import routes.admin_assistant as route_mod

    events: list = []
    monkeypatch.setattr(
        route_mod, "_record_manual_event",
        lambda entity_id, new_status, uid, meta=None: events.append((entity_id, new_status, uid, meta)),
    )
    monkeypatch.setattr(route_mod.kb, "clear_cache", lambda: None)
    if route_mod._help_kb_manual is not None:
        monkeypatch.setattr(route_mod._help_kb_manual, "clear_manual_cache", lambda: None)

    client = TestClient(app)
    return client, events, route_mod


@pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入（full API は Docker 内で）")
class TestHelpKbStoreApiPermissions:
    @pytest.mark.parametrize("role", ["TEACHER", "STUDENT"])
    def test_list_drafts_forbidden_for_non_admin(self, api, role):
        client, events, _route_mod = api
        resp = client.get("/api/admin/help-kb/drafts", headers=_headers(role))
        assert resp.status_code == 403
        assert events == []

    @pytest.mark.parametrize("role", ["TEACHER", "STUDENT"])
    def test_freeze_forbidden_for_non_admin(self, api, role):
        client, events, _route_mod = api
        resp = client.post("/api/admin/help-kb/freeze", headers=_headers(role))
        assert resp.status_code == 403
        assert events == []

    def test_no_auth_rejected(self, api):
        client, _events, _route_mod = api
        resp = client.get("/api/admin/help-kb/drafts")
        assert resp.status_code in (401, 403)


@pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入（full API は Docker 内で）")
class TestHelpKbStoreApiBehavior:
    def test_list_drafts_returns_state(self, api, fake):
        client, _events, _route_mod = api
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        resp = client.get("/api/admin/help-kb/drafts", headers=_headers("SYSTEM_ADMIN"))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["drafts"]) == 1
        assert body["state"]["serving_source"] == "files"

    def test_update_draft_success_records_audit(self, api, fake):
        client, events, _route_mod = api
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        resp = client.put(
            "/api/admin/help-kb/drafts/teacher/a.md",
            headers=_headers("SYSTEM_ADMIN"),
            json={"content": VALID_TEACHER_MD + "\n追記", "expected_revision": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["revision"] == 2
        assert len(events) == 1
        assert events[0][1] == "updated"

    def test_update_draft_conflict_returns_409(self, api, fake):
        client, _events, _route_mod = api
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD, revision=5)
        resp = client.put(
            "/api/admin/help-kb/drafts/teacher/a.md",
            headers=_headers("SYSTEM_ADMIN"),
            json={"content": "x", "expected_revision": 1},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["current_revision"] == 5

    def test_update_draft_not_found_returns_404(self, api, fake):
        client, _events, _route_mod = api
        resp = client.put(
            "/api/admin/help-kb/drafts/teacher/missing.md",
            headers=_headers("SYSTEM_ADMIN"),
            json={"content": "x", "expected_revision": 1},
        )
        assert resp.status_code == 404

    def test_path_traversal_encoded_slash_rejected(self, api, fake):
        client, _events, _route_mod = api
        resp = client.get(
            "/api/admin/help-kb/drafts/teacher/a%2Fb.md", headers=_headers("SYSTEM_ADMIN"),
        )
        # 生 SQL 経路に達する前に拒否される（400 = ValueError／404 = ルーティング段階で
        # 単一セグメントとしてマッチしなかった場合。いずれにせよ store 層まで届かない）。
        assert resp.status_code in (400, 404)

    def test_path_traversal_dotdot_rejected(self, api, fake):
        client, _events, _route_mod = api
        resp = client.get(
            "/api/admin/help-kb/drafts/teacher/..%2Fetc.md", headers=_headers("SYSTEM_ADMIN"),
        )
        assert resp.status_code in (400, 404)

    def test_seed_endpoint_records_audit(self, api, fake, tmp_path, monkeypatch):
        client, events, route_mod = api
        root = tmp_path / "manual"
        (root / "teacher").mkdir(parents=True)
        (root / "teacher" / "a.md").write_text(VALID_TEACHER_MD, encoding="utf-8")
        monkeypatch.setattr(kb_store, "manual_root", lambda: root)
        resp = client.post("/api/admin/help-kb/drafts/seed", headers=_headers("SYSTEM_ADMIN"))
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1
        assert any(e[1] == "seeded" for e in events)

    def test_freeze_validation_failure_returns_422_with_violations(self, api, fake):
        client, events, _route_mod = api
        _seed_draft(fake, "teacher", "a.md", MISSING_ANCHOR_MD)
        resp = client.post("/api/admin/help-kb/freeze", headers=_headers("SYSTEM_ADMIN"))
        assert resp.status_code == 422
        assert resp.json()["detail"]["violations"]
        assert any(e[1] == "rejected" for e in events)

    def test_freeze_success_returns_version_and_switches_source(self, api, fake):
        client, events, _route_mod = api
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        resp = client.post("/api/admin/help-kb/freeze", headers=_headers("SYSTEM_ADMIN"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["version_no"] == 1
        assert body["serving_source"] == "db"
        assert any(e[1] == "frozen" for e in events)

    def test_serving_source_switch_requires_freeze_first(self, api, fake):
        client, _events, _route_mod = api
        resp = client.post(
            "/api/admin/help-kb/serving-source",
            headers=_headers("SYSTEM_ADMIN"),
            json={"source": "db"},
        )
        assert resp.status_code == 409

    def test_serving_source_invalid_value_rejected(self, api, fake):
        client, _events, _route_mod = api
        resp = client.post(
            "/api/admin/help-kb/serving-source",
            headers=_headers("SYSTEM_ADMIN"),
            json={"source": "vector"},
        )
        assert resp.status_code == 400

    def test_versions_endpoint_lists_after_freeze(self, api, fake):
        client, _events, _route_mod = api
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        client.post("/api/admin/help-kb/freeze", headers=_headers("SYSTEM_ADMIN"))
        resp = client.get("/api/admin/help-kb/versions", headers=_headers("SYSTEM_ADMIN"))
        assert resp.status_code == 200
        versions = resp.json()["versions"]
        assert len(versions) == 1
        assert versions[0]["version_no"] == 1

    def test_no_delete_route_for_versions_or_drafts(self):
        import routes.admin_assistant as route_mod

        for route in route_mod.help_kb_router.routes:
            methods = getattr(route, "methods", set()) or set()
            if "DELETE" in methods:
                pytest.fail(f"unexpected DELETE route on help_kb_router: {route.path}")

    def test_audit_uses_catalog_entity_type(self, api, fake):
        from core.schema import AUDIT_ENTITY_MANUAL

        client, events, _route_mod = api
        _seed_draft(fake, "teacher", "a.md", VALID_TEACHER_MD)
        client.put(
            "/api/admin/help-kb/drafts/teacher/a.md",
            headers=_headers("SYSTEM_ADMIN"),
            json={"content": "x", "expected_revision": 1},
        )
        assert AUDIT_ENTITY_MANUAL == "manual"
        assert len(events) == 1
