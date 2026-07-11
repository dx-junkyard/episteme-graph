"""ガイダンス層（G層）Next Steps — fail-closed・却下保持・ガードレールの検証。

構成（backend/tests/test_admin_assistant.py に倣う）:
  - Group A: 純粋ロジック + 構造テスト（DB 不要・FastAPI 不要）。
  - Group B: TestClient（実 DB なし。DB 未接続時の fail-closed 縮退と
    dismiss/restore が next_steps ストア関数を呼ぶことだけを検証する）。
    FastAPI 未導入の環境（CI 素の Python）では skip。

観点（設計 §10）:
  - 全ルールの capability_id が registry に存在し required_role が TEACHER 以下。
  - 権限外ロール（STUDENT）で compute_next_steps を呼んでも項目が出ない（G3 fail-closed）。
    このとき DB には一切アクセスしない（session=None でも例外にならないことで確認する）。
  - dismiss / restore が行削除しない（G5/P4）— ソースに DELETE 文が無いことを構造的に検証。
  - core/admin_assistant/next_steps.py が FastAPI / LLM クライアントを import しない（G2/G7）。
  - reason / title テンプレートに禁止語彙（煽り・督促）を含まない（G6）。
  - 返却件数上限 10 件と truncated の整合（fake データでの純ロジックテスト）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.admin_assistant import capabilities as caps  # noqa: E402
from core.admin_assistant import next_steps as next_steps_mod  # noqa: E402
from core.admin_assistant.schema import ROLE_TEACHER  # noqa: E402

_NEXT_STEPS_SRC = (BACKEND / "core" / "admin_assistant" / "next_steps.py").read_text(encoding="utf-8")
_ROUTE_SRC = (BACKEND / "api" / "routes" / "admin_assistant.py").read_text(encoding="utf-8")

_FORBIDDEN_WORDS = ("！", "今すぐ", "急いで", "必ず", "早く", "至急")


# ===========================================================================
# Group A-1: ルールカタログ（G3 fail-closed の前提）
# ===========================================================================


class TestRuleCatalog:
    def test_all_rule_capabilities_exist_in_registry(self):
        for rule_id, rule in next_steps_mod.RULE_CATALOG.items():
            cap = caps.get_capability(rule["capability_id"])
            assert cap is not None, f"{rule_id} が参照する capability {rule['capability_id']} が未登録"

    def test_all_rule_capabilities_reachable_by_teacher(self):
        """TEACHER 以下（= TEACHER が満たせる）required_role であること。"""
        for rule_id, rule in next_steps_mod.RULE_CATALOG.items():
            assert caps.can_access(rule["capability_id"], ROLE_TEACHER), (
                f"{rule_id} の capability {rule['capability_id']} は TEACHER からアクセスできない"
            )

    def test_all_severities_are_known(self):
        for rule_id, rule in next_steps_mod.RULE_CATALOG.items():
            assert rule["severity"] in next_steps_mod.SEVERITIES, f"{rule_id} の severity が不正"

    def test_new_capabilities_registered(self):
        """設計 §3.2 の新規 capability 2 件が登録されていること。"""
        assert caps.get_capability("course.atlas_binding") is not None
        assert caps.get_capability("lecture_studio.generate_audio") is not None

    def test_new_capabilities_have_locate_steps(self):
        for cap_id in ("course.atlas_binding", "lecture_studio.generate_audio"):
            cap = caps.get_capability(cap_id)
            assert cap.locate_steps, f"{cap_id} に locate_steps が無い"


# ===========================================================================
# Group A-2: fail-closed（STUDENT には何も出さない）
# ===========================================================================


class TestFailClosed:
    def test_student_gets_nothing_without_touching_db(self):
        """STUDENT はどのルールの capability にも到達できないため、DB に触れずに空を返す。

        session に None を渡し、万一 DB アクセスを試みれば AttributeError で即座に
        検出できるようにする（G3: 評価すらしないことの確認）。
        """
        result = next_steps_mod.compute_next_steps(None, {"id": "u-student", "role": "STUDENT"})
        assert result == {"steps": [], "hidden": [], "truncated": False}

    def test_missing_user_id_returns_empty(self):
        result = next_steps_mod.compute_next_steps(None, {"role": "TEACHER"})
        assert result == {"steps": [], "hidden": [], "truncated": False}

    def test_empty_user_returns_empty(self):
        result = next_steps_mod.compute_next_steps(None, {})
        assert result == {"steps": [], "hidden": [], "truncated": False}

    def test_unknown_role_gets_nothing(self):
        result = next_steps_mod.compute_next_steps(None, {"id": "u1", "role": "BOGUS"})
        assert result == {"steps": [], "hidden": [], "truncated": False}


# ===========================================================================
# Group A-3: 却下は保持する（G5/P4）— ソース構造の検証
# ===========================================================================


class TestDismissalPersistence:
    def test_no_delete_statement_in_next_steps_module(self):
        assert "DELETE FROM assistant_step_dismissals" not in _NEXT_STEPS_SRC
        assert "DELETE  FROM assistant_step_dismissals" not in _NEXT_STEPS_SRC

    def test_restore_uses_revoked_update_not_delete(self):
        assert "def restore_step" in _NEXT_STEPS_SRC
        start = _NEXT_STEPS_SRC.index("def restore_step")
        end = _NEXT_STEPS_SRC.index("\ndef ", start + 1)
        body = _NEXT_STEPS_SRC[start:end]
        assert "DELETE" not in body.upper().replace("REVOKED", "")
        assert "revoked = TRUE" in body or "revoked=TRUE" in body

    def test_dismiss_upserts_without_delete(self):
        assert "def dismiss_step" in _NEXT_STEPS_SRC
        start = _NEXT_STEPS_SRC.index("def dismiss_step")
        end = _NEXT_STEPS_SRC.index("\ndef ", start + 1)
        body = _NEXT_STEPS_SRC[start:end]
        assert "ON CONFLICT" in body
        assert "DELETE" not in body.upper()

    def test_routes_dismiss_restore_do_not_delete_rows(self):
        for fn_name in ("dismiss_next_step", "restore_next_step"):
            assert f"def {fn_name}" in _ROUTE_SRC
            start = _ROUTE_SRC.index(f"def {fn_name}")
            end = _ROUTE_SRC.index("\ndef ", start + 1) if "\ndef " in _ROUTE_SRC[start + 1:] else len(_ROUTE_SRC)
            body = _ROUTE_SRC[start:end]
            assert "DELETE" not in body.upper()

    def test_cue_reuses_dismissal_table_key(self):
        """§8: 初回ログイン cue は専用テーブルを増やさず assistant_step_dismissals を流用する。"""
        assert next_steps_mod.CUE_FIRST_LOGIN_KEY == "cue:first_login"
        assert "def is_cue_pending" in _NEXT_STEPS_SRC


# ===========================================================================
# Group A-4: ガードレール（構造テスト, G2/G7）
# ===========================================================================


class TestGuardrails:
    def test_next_steps_does_not_import_fastapi(self):
        assert "import fastapi" not in _NEXT_STEPS_SRC
        assert "from fastapi" not in _NEXT_STEPS_SRC

    def test_next_steps_does_not_import_llm_client(self):
        assert "import openai" not in _NEXT_STEPS_SRC
        assert "from openai" not in _NEXT_STEPS_SRC
        assert "core.llm" not in _NEXT_STEPS_SRC
        assert "llm_client" not in _NEXT_STEPS_SRC

    def test_next_steps_uses_sqlalchemy_text_only(self):
        """SQLAlchemy セッション（sqlalchemy.text 等）の利用は可（タスク条件）。"""
        assert "from sqlalchemy import text as sa_text" in _NEXT_STEPS_SRC

    def test_route_registers_next_steps_endpoints(self):
        assert '"/next-steps"' in _ROUTE_SRC
        assert '"/next-steps/{step_key}/dismiss"' in _ROUTE_SRC
        assert '"/next-steps/{step_key}/restore"' in _ROUTE_SRC

    def test_audit_uses_next_step_entity_type(self):
        assert "'next_step'" in _ROUTE_SRC

    def test_only_documents_and_learning_courses_read_by_uploaded_by_or_user_id(self):
        """本人所有のみを対象にする（uploaded_by / user_id）。"""
        assert "uploaded_by = CAST(:uid AS uuid)" in _NEXT_STEPS_SRC
        assert "user_id = CAST(:uid AS uuid)" in _NEXT_STEPS_SRC


# ===========================================================================
# Group A-5: reason / title に禁止語彙が無い（G6）
# ===========================================================================


class TestReasonWording:
    def test_no_forbidden_vocabulary_in_source_literals(self):
        for word in _FORBIDDEN_WORDS:
            assert word not in _NEXT_STEPS_SRC, f"next_steps.py に禁止語彙が含まれている: {word}"

    def test_reasons_are_factual_statements_not_commands(self):
        """reason の f-string テンプレートに命令調の「〜してください」を含めない。"""
        assert "してください" not in _NEXT_STEPS_SRC


# ===========================================================================
# Group A-6: finalize_next_steps の純ロジック（DB 非依存, fake データ）
# ===========================================================================


def _fake_step(key: str, severity: str) -> next_steps_mod.NextStep:
    return next_steps_mod.NextStep(
        step_key=key,
        rule_id="test.rule",
        severity=severity,
        title=f"title-{key}",
        reason=f"reason-{key}",
        capability_id="materials.upload",
        locate_plan={"capability_id": "materials.upload", "steps": []},
        target={},
    )


class TestFinalizeNextSteps:
    def test_orders_by_severity_then_oldest_first(self):
        entries = [
            (_fake_step("a", next_steps_mod.SEVERITY_OPTIONAL), "2020-01-03"),
            (_fake_step("b", next_steps_mod.SEVERITY_REQUIRED), "2020-01-02"),
            (_fake_step("c", next_steps_mod.SEVERITY_REQUIRED), "2020-01-01"),
            (_fake_step("d", next_steps_mod.SEVERITY_RECOMMENDED), "2020-01-01"),
        ]
        result = next_steps_mod.finalize_next_steps(entries, set())
        keys = [s["step_key"] for s in result["steps"]]
        assert keys == ["c", "b", "d", "a"]
        assert result["truncated"] is False

    def test_truncates_at_max_steps(self):
        entries = [
            (_fake_step(f"k{i}", next_steps_mod.SEVERITY_OPTIONAL), f"2020-01-{i:02d}")
            for i in range(1, next_steps_mod.MAX_STEPS + 3)
        ]
        result = next_steps_mod.finalize_next_steps(entries, set())
        assert len(result["steps"]) == next_steps_mod.MAX_STEPS
        assert result["truncated"] is True

    def test_no_truncation_when_within_limit(self):
        entries = [
            (_fake_step(f"k{i}", next_steps_mod.SEVERITY_REQUIRED), f"2020-01-{i:02d}")
            for i in range(1, next_steps_mod.MAX_STEPS + 1)
        ]
        result = next_steps_mod.finalize_next_steps(entries, set())
        assert len(result["steps"]) == next_steps_mod.MAX_STEPS
        assert result["truncated"] is False

    def test_dismissed_moved_to_hidden_not_dropped(self):
        """G5/P4: 却下は捨てず hidden に分離する。"""
        entries = [
            (_fake_step("a", next_steps_mod.SEVERITY_REQUIRED), "t"),
            (_fake_step("b", next_steps_mod.SEVERITY_REQUIRED), "t"),
        ]
        result = next_steps_mod.finalize_next_steps(entries, {"a"})
        assert [s["step_key"] for s in result["steps"]] == ["b"]
        assert [s["step_key"] for s in result["hidden"]] == ["a"]

    def test_step_to_dict_has_expected_keys(self):
        step = _fake_step("x", next_steps_mod.SEVERITY_RECOMMENDED)
        d = step.to_dict()
        for key in (
            "step_key", "rule_id", "severity", "title", "reason",
            "capability_id", "locate_plan", "target", "dismissible",
        ):
            assert key in d


# ===========================================================================
# Group A-7: コース⇄地図 binding 判定（course.no_atlas_binding のロジック）
# ===========================================================================


class TestCourseAtlasBindingLogic:
    def test_needs_binding_when_nothing_set(self):
        assert next_steps_mod._course_needs_atlas_binding({}) is True

    def test_no_binding_needed_when_cartridge_present(self):
        """設計 §3.1 の条件は AND: cartridge_id さえあれば（途中でも）対象外。"""
        data = {"cartridge_id": "particle_physics", "topics": []}
        assert next_steps_mod._course_needs_atlas_binding(data) is False

    def test_no_binding_needed_when_topic_node_bound_without_cartridge(self):
        data = {"topics": [{"id": "t1", "atlas_node_id": "n1"}]}
        # cartridge_id が無くても、topics 側に atlas_node_id があれば対象外
        assert next_steps_mod._course_needs_atlas_binding(data) is False

    def test_finds_atlas_node_nested_in_chapters(self):
        data = {"chapters": [{"topics": [{"atlas_node_id": "n1"}]}]}
        assert next_steps_mod._course_has_atlas_node(data) is True


# ===========================================================================
# Group B: API 結合テスト（TestClient。実 DB なしでの fail-closed 縮退を検証）
# ===========================================================================

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark_api = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入（full API は Docker 内で）")

_UID_TEACHER = "11111111-1111-1111-1111-111111111111"


def _headers(role: str, sub: str = _UID_TEACHER):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    return {"Authorization": "Bearer " + jwt.encode(payload, "test-secret-key", algorithm="HS256")}


@pytest.fixture
def api():
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI 未導入")
    for p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from fastapi.testclient import TestClient
    from api.main import app
    import routes.admin_assistant as route_mod

    client = TestClient(app)
    return client, route_mod


@pytestmark_api
class TestNextStepsAPI:
    def test_student_forbidden(self, api):
        client, _route_mod = api
        r = client.get("/api/admin/assistant/next-steps", headers=_headers("STUDENT"))
        assert r.status_code == 403

    def test_get_returns_expected_keys_even_without_live_db(self, api):
        """DB 未接続環境でも捏造せず fail-closed に縮退する（S5 と同型）。"""
        client, _route_mod = api
        r = client.get("/api/admin/assistant/next-steps", headers=_headers("TEACHER"))
        assert r.status_code == 200
        data = r.json()
        for key in ("steps", "hidden", "truncated", "assistant_cue_pending"):
            assert key in data
        assert isinstance(data["steps"], list)
        assert isinstance(data["hidden"], list)

    def test_dismiss_calls_store_and_returns_status(self, api, monkeypatch):
        client, route_mod = api
        calls = []
        monkeypatch.setattr(
            route_mod.next_steps_mod, "dismiss_step",
            lambda session, uid, key: calls.append(("dismiss", uid, key)),
        )
        monkeypatch.setattr(route_mod, "_record_next_step_event", lambda *a, **k: None)
        r = client.post(
            "/api/admin/assistant/next-steps/materials.none:global/dismiss",
            headers=_headers("TEACHER"),
        )
        assert r.status_code == 200
        assert r.json() == {"status": "dismissed", "step_key": "materials.none:global"}
        assert calls == [("dismiss", _UID_TEACHER, "materials.none:global")]

    def test_restore_calls_store_and_returns_status(self, api, monkeypatch):
        client, route_mod = api
        calls = []
        monkeypatch.setattr(
            route_mod.next_steps_mod, "restore_step",
            lambda session, uid, key: calls.append(("restore", uid, key)) or True,
        )
        monkeypatch.setattr(route_mod, "_record_next_step_event", lambda *a, **k: None)
        r = client.post(
            "/api/admin/assistant/next-steps/materials.none:global/restore",
            headers=_headers("TEACHER"),
        )
        assert r.status_code == 200
        assert r.json() == {"status": "restored", "step_key": "materials.none:global"}
        assert calls == [("restore", _UID_TEACHER, "materials.none:global")]

    def test_dismiss_forbidden_for_student(self, api):
        client, _route_mod = api
        r = client.post(
            "/api/admin/assistant/next-steps/materials.none:global/dismiss",
            headers=_headers("STUDENT"),
        )
        assert r.status_code == 403


# ===========================================================================
# Group A-6: migration 039 の配線 + フロント統合（構造テスト。DB / FastAPI 不要）
# ===========================================================================

import re  # noqa: E402

_MAIN_SRC = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
_MIGRATION_SQL = (BACKEND / "db" / "039_assistant_step_dismissals.sql").read_text(encoding="utf-8")
_NEXT_STEPS_JS = (ROOT / "frontend" / "public" / "js" / "admin-next-steps.js").read_text(encoding="utf-8")
_ADMIN_HTML = (ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
_ADMIN_JS_MAIN = (ROOT / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")


class TestMigrationWiring:
    """migration 039 が正本 SQL・_run_migrations()・ORM の三点で一致していること。"""

    def test_sql_reference_defines_table(self):
        assert "CREATE TABLE IF NOT EXISTS assistant_step_dismissals" in _MIGRATION_SQL
        assert "UNIQUE (user_id, step_key)" in _MIGRATION_SQL
        assert "revoked" in _MIGRATION_SQL

    def test_main_registers_migration_039(self):
        # 起動時 _run_migrations() で実テーブルが作られる（配線漏れの再発防止）。
        assert "CREATE TABLE IF NOT EXISTS assistant_step_dismissals" in _MAIN_SRC
        assert "idx_assistant_step_dismissals_user" in _MAIN_SRC
        # 適用完了ログの範囲は後続 migration 追加で末尾が伸びるため prefix で確認する。
        assert "Migrations (002-" in _MAIN_SRC

    def test_orm_model_exists(self):
        from core import models

        assert hasattr(models, "AssistantStepDismissal")
        assert models.AssistantStepDismissal.__tablename__ == "assistant_step_dismissals"


class TestFrontendIntegration:
    """G層フロントの構造的不変条項（test_admin_assistant.py の frontend 検証と同方式）。"""

    def test_admin_html_includes_badge_and_script(self):
        assert "admin-next-steps-toggle" in _ADMIN_HTML
        assert "admin-next-steps.js" in _ADMIN_HTML

    def test_next_steps_js_is_es5(self):
        # 管理画面 JS の規約（ES5）: アロー関数・const/let・テンプレートリテラルを使わない。
        assert "=>" not in _NEXT_STEPS_JS
        assert re.search(r"\bconst\s", _NEXT_STEPS_JS) is None
        assert re.search(r"\blet\s", _NEXT_STEPS_JS) is None
        assert "`" not in _NEXT_STEPS_JS

    def test_no_polling(self):
        # G4: ポーリング禁止（再取得は画面イベントからの refresh() のみ）。
        assert "setInterval" not in _NEXT_STEPS_JS

    def test_locate_delegates_to_admin_assistant(self):
        # G8: 道案内は AdminAssistant.runLocatePlan に委譲し spotlight を二重実装しない。
        assert "runLocatePlan" in _NEXT_STEPS_JS
        assert "admin-assistant-spotlight" not in _NEXT_STEPS_JS

    def test_admin_js_wires_next_steps(self):
        assert "AdminNextSteps.init" in _ADMIN_JS_MAIN
        assert "AdminNextSteps.refresh" in _ADMIN_JS_MAIN

    def test_atlas_data_has_no_default_cartridge_fallback(self):
        # Phase 0（設計 §9）: 既定カートリッジへのフォールバック経路が復活していないこと。
        atlas_data = (ROOT / "frontend" / "public" / "js" / "atlas-data.js").read_text(encoding="utf-8")
        assert "DEFAULT_CARTRIDGE" not in atlas_data
        assert "particle_physics" not in atlas_data
