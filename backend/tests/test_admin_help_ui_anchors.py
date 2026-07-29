"""管理画面インスペクト・モード（❓ 使い方, help-conventions.md）のテスト。

学習画面側 ``test_help_ui_anchors.py`` と同型・独立のテストファイル。対象:
  - ``backend/core/help_kb/admin_ui_anchors.py``（admin 用 UI アンカー表の正本、新規）
  - ``backend/core/help_kb/validator.py`` の ``check_admin_ui_anchor_mappings``
  - ``backend/api/main.py`` lifespan からの独立呼び出し
  - ``backend/api/routes/admin_assistant.py`` の
    ``GET /help/ui-anchors`` / ``POST /help/ui-anchor-events`` / chat の usage_help 分岐
  - ``backend/api/schemas.py`` の ``AssistantChatRequest.support_action`` / ``ui_anchor``
  - ``backend/api/services.py`` の ``recent_duplicate_ui_anchor_event``（学習側と共通化）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb import admin_ui_anchors as admin_anchors_mod  # noqa: E402
from core.help_kb import manual as kb_manual  # noqa: E402
from core.help_kb import validator as kb_validator  # noqa: E402
from tests.guardrail_helpers import assert_source_does_not_import  # noqa: E402

import routes.admin_assistant as admin_assistant_mod  # noqa: E402
import services as services_mod  # noqa: E402
from schemas import AssistantChatRequest, AssistantChatResponse  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_manual_cache():
    # 他テスト（test_help_kb_store 等）が合成ツリー・DB 状態で温めたモジュールキャッシュを
    # 持ち込まない／持ち出さない（実行順依存の flake 防止。学習側と同じ規約）。
    kb_manual.clear_manual_cache()
    yield
    kb_manual.clear_manual_cache()


def _source_of(fn) -> str:
    import inspect

    return inspect.getsource(fn)


# ===========================================================================
# 1. admin_ui_anchors.py — audience 越境禁止・FastAPI 非 import・実在解決
# ===========================================================================


class TestAdminUiAnchorsModule:
    def test_does_not_import_fastapi(self):
        src = Path(admin_anchors_mod.__file__).read_text(encoding="utf-8")
        assert_source_does_not_import(src, ["fastapi"], context="admin_ui_anchors.py")

    def test_all_mapped_values_reference_teacher_or_system_admin_only(self):
        for anchor_id, ref in admin_anchors_mod.ADMIN_UI_ANCHORS.items():
            assert ref.startswith("teacher/") or ref.startswith("system_admin/"), (
                f"{anchor_id} が teacher/system_admin 以外を参照: {ref}"
            )
            assert not ref.startswith("student/"), f"{anchor_id} が student/ を参照している: {ref}"

    def test_known_ids_is_superset_of_mapped_ids(self):
        assert set(admin_anchors_mod.ADMIN_UI_ANCHORS.keys()) <= admin_anchors_mod.KNOWN_ADMIN_UI_ANCHOR_IDS

    def test_all_anchors_currently_mapped(self):
        # 2026-07-29 時点（merged-manifest.json 由来・重複2件除去後）で全223アンカーがマップ済み。
        assert len(admin_anchors_mod.ADMIN_UI_ANCHORS) == len(admin_anchors_mod.KNOWN_ADMIN_UI_ANCHOR_IDS) == 223

    def test_resolve_against_real_docs_has_no_broken_mapping_for_system_admin(self):
        """docs/manual/{teacher,system_admin}/ の実データに対し、マップした全アンカーが解決できる。"""
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("SYSTEM_ADMIN")
        assert set(resolved.keys()) == set(admin_anchors_mod.ADMIN_UI_ANCHORS.keys())
        for anchor_id, sec in resolved.items():
            assert sec["title"], f"{anchor_id} の title が空"
            assert sec["body"], f"{anchor_id} の body が空"
            assert sec["manual_anchor"] == admin_anchors_mod.ADMIN_UI_ANCHORS[anchor_id]

    def test_teacher_role_never_receives_system_admin_refs(self):
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("TEACHER")
        for anchor_id, sec in resolved.items():
            assert sec["manual_anchor"].startswith("teacher/"), (
                f"{anchor_id} が system_admin/ 節を含めて TEACHER に配信された: {sec['manual_anchor']}"
            )
        sysadmin_only_ids = {
            aid for aid, ref in admin_anchors_mod.ADMIN_UI_ANCHORS.items()
            if ref.startswith("system_admin/")
        }
        assert sysadmin_only_ids, "テスト前提: system_admin 限定アンカーが実在すること"
        assert not (sysadmin_only_ids & set(resolved.keys()))

    def test_system_admin_role_receives_everything(self):
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("SYSTEM_ADMIN")
        assert set(resolved.keys()) == set(admin_anchors_mod.ADMIN_UI_ANCHORS.keys())

    def test_unknown_role_returns_empty(self):
        assert admin_anchors_mod.resolve_admin_ui_anchors("STUDENT") == {}
        assert admin_anchors_mod.resolve_admin_ui_anchors("") == {}
        assert admin_anchors_mod.resolve_admin_ui_anchors("BOGUS_ROLE") == {}

    def test_resolve_single_matches_bulk_resolution(self):
        bulk = admin_anchors_mod.resolve_admin_ui_anchors("SYSTEM_ADMIN")
        single = admin_anchors_mod.resolve_admin_ui_anchor("materials.upload-zone", "SYSTEM_ADMIN")
        assert single == bulk["materials.upload-zone"]

    def test_resolve_single_unknown_anchor_returns_none(self):
        assert admin_anchors_mod.resolve_admin_ui_anchor("no.such.anchor", "SYSTEM_ADMIN") is None

    def test_resolve_single_out_of_role_returns_none(self):
        sysadmin_only_id = next(
            aid for aid, ref in admin_anchors_mod.ADMIN_UI_ANCHORS.items()
            if ref.startswith("system_admin/")
        )
        assert admin_anchors_mod.resolve_admin_ui_anchor(sysadmin_only_id, "TEACHER") is None
        assert admin_anchors_mod.resolve_admin_ui_anchor(sysadmin_only_id, "SYSTEM_ADMIN") is not None

    def test_split_manual_ref(self):
        assert admin_anchors_mod.split_manual_ref("teacher/10-admin-common.md#inspect-mode") == (
            "10-admin-common.md", "inspect-mode",
        )


# ===========================================================================
# 2. validator.py — check_admin_ui_anchor_mappings
# ===========================================================================


class TestAdminValidatorIntegration:
    def test_check_admin_ui_anchor_mappings_clean_against_real_docs(self):
        assert admin_anchors_mod.resolve_admin_ui_anchors("SYSTEM_ADMIN")  # 前提: 実データで解決できている
        assert kb_validator.check_admin_ui_anchor_mappings() == []

    def test_validate_manual_unaffected_by_admin_ui_anchor_check(self):
        """check_admin_ui_anchor_mappings は validate_manual() に混ぜない（独立関数、学習側と同じ理由）。"""
        assert "check_admin_ui_anchor_mappings" not in _source_of(kb_validator.validate_manual)

    def test_student_reference_is_flagged(self, monkeypatch):
        monkeypatch.setattr(
            admin_anchors_mod, "ADMIN_UI_ANCHORS",
            {"bogus.anchor": "student/02-student.md#ai-chat"},
        )
        violations = kb_validator.check_admin_ui_anchor_mappings()
        assert any("bogus.anchor" in v for v in violations)

    def test_missing_section_is_flagged(self, monkeypatch):
        monkeypatch.setattr(
            admin_anchors_mod, "ADMIN_UI_ANCHORS",
            {"bogus.anchor": "teacher/does-not-exist.md#nope"},
        )
        violations = kb_validator.check_admin_ui_anchor_mappings()
        assert any("bogus.anchor" in v for v in violations)

    def test_missing_system_admin_section_is_flagged(self, monkeypatch):
        monkeypatch.setattr(
            admin_anchors_mod, "ADMIN_UI_ANCHORS",
            {"bogus.anchor": "system_admin/does-not-exist.md#nope"},
        )
        violations = kb_validator.check_admin_ui_anchor_mappings()
        assert any("bogus.anchor" in v for v in violations)

    def test_unknown_id_not_in_known_set_is_flagged(self, monkeypatch):
        monkeypatch.setattr(admin_anchors_mod, "KNOWN_ADMIN_UI_ANCHOR_IDS", frozenset())
        violations = kb_validator.check_admin_ui_anchor_mappings()
        assert any("KNOWN_ADMIN_UI_ANCHOR_IDS" in v for v in violations)

    def test_check_is_independent_of_manual_root_monkeypatch_used_by_other_tests(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kb_validator, "manual_root", lambda: tmp_path / "nope")
        assert kb_validator.validate_manual() == []


class TestMainLifespanWiring:
    def test_main_calls_check_admin_ui_anchor_mappings_in_separate_fail_open_block(self):
        main_src = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
        assert "check_admin_ui_anchor_mappings" in main_src
        idx = main_src.find("check_admin_ui_anchor_mappings")
        block = main_src[max(0, idx - 400): idx + 200]
        assert "except Exception" in block


# ===========================================================================
# 3. services.recent_duplicate_ui_anchor_event — 学習側・管理画面側の共有ヘルパー
# ===========================================================================


class TestSharedDedupHelper:
    def test_returns_false_on_db_error(self, monkeypatch):
        class _BoomSession:
            def execute(self, *a, **k):
                raise RuntimeError("boom")

            def close(self):
                pass

        monkeypatch.setattr(services_mod, "_pg_session", lambda: _BoomSession())
        assert services_mod.recent_duplicate_ui_anchor_event("uid", "ui:anchor") is False

    def test_learning_module_delegates_to_shared_helper(self):
        import routes.learning as learning_mod

        assert learning_mod._recent_duplicate_ui_anchor_event_shared is (
            services_mod.recent_duplicate_ui_anchor_event
        )


# ===========================================================================
# 4. AssistantChatRequest.support_action / ui_anchor フィールド
# ===========================================================================


class TestSchemaFields:
    def test_fields_exist_and_default_to_none(self):
        req = AssistantChatRequest(message="hi")
        assert req.support_action is None
        assert req.ui_anchor is None

    def test_fields_can_be_set(self):
        req = AssistantChatRequest(message="hi", support_action="usage_help", ui_anchor="header.inspect")
        assert req.support_action == "usage_help"
        assert req.ui_anchor == "header.inspect"


# ===========================================================================
# 5. API 結合テスト（TestClient）
# ===========================================================================

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark_api = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入（full API は Docker 内で）")

_UID_TEACHER = "55555555-5555-5555-5555-555555555555"
_UID_STUDENT = "66666666-6666-6666-6666-666666666666"
_UID_SYSTEM_ADMIN = "77777777-7777-7777-7777-777777777777"


def _headers(role: str, sub: str = _UID_TEACHER):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    return {"Authorization": "Bearer " + jwt.encode(payload, "test-secret-key", algorithm="HS256")}


@pytest.fixture
def client():
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI 未導入")
    for p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from api.main import app

    return TestClient(app)


# --- GET /api/admin/assistant/help/ui-anchors ---------------------------


@pytestmark_api
class TestGetAdminUiAnchorsApi:
    def test_requires_auth(self, client):
        resp = client.get("/api/admin/assistant/help/ui-anchors")
        assert resp.status_code in (401, 403)

    def test_student_forbidden(self, client):
        resp = client.get(
            "/api/admin/assistant/help/ui-anchors", headers=_headers("STUDENT", _UID_STUDENT),
        )
        assert resp.status_code == 403

    def test_teacher_gets_anchors(self, client):
        resp = client.get("/api/admin/assistant/help/ui-anchors", headers=_headers("TEACHER"))
        assert resp.status_code == 200
        anchors = resp.json()["anchors"]
        assert "materials.upload-zone" in anchors
        entry = anchors["materials.upload-zone"]
        assert set(entry.keys()) == {"title", "body", "manual_anchor"}
        sysadmin_only_ids = {
            aid for aid, ref in admin_anchors_mod.ADMIN_UI_ANCHORS.items()
            if ref.startswith("system_admin/")
        }
        assert not (sysadmin_only_ids & set(anchors.keys()))

    def test_system_admin_gets_everything(self, client):
        resp = client.get(
            "/api/admin/assistant/help/ui-anchors", headers=_headers("SYSTEM_ADMIN", _UID_SYSTEM_ADMIN),
        )
        assert resp.status_code == 200
        anchors = resp.json()["anchors"]
        assert set(anchors.keys()) == set(admin_anchors_mod.ADMIN_UI_ANCHORS.keys())

    def test_does_not_record_any_trace(self, client, monkeypatch):
        def _unexpected(*a, **k):
            raise AssertionError("GET ui-anchors は痕跡を書いてはならない（読み取り専用）")

        monkeypatch.setattr(services_mod, "record_interest_trace", _unexpected)
        resp = client.get("/api/admin/assistant/help/ui-anchors", headers=_headers("TEACHER"))
        assert resp.status_code == 200


# --- POST /api/admin/assistant/help/ui-anchor-events ---------------------


@pytestmark_api
class TestPostAdminUiAnchorEventsApi:
    def test_requires_auth(self, client):
        resp = client.post(
            "/api/admin/assistant/help/ui-anchor-events",
            json={"anchor_id": "header.inspect", "kind": "no_hit"},
        )
        assert resp.status_code in (401, 403)

    def test_student_forbidden(self, client):
        resp = client.post(
            "/api/admin/assistant/help/ui-anchor-events",
            json={"anchor_id": "header.inspect", "kind": "no_hit"},
            headers=_headers("STUDENT", _UID_STUDENT),
        )
        assert resp.status_code == 403

    def test_unknown_anchor_id_is_422(self, client):
        resp = client.post(
            "/api/admin/assistant/help/ui-anchor-events",
            json={"anchor_id": "not.a.real.anchor", "kind": "no_hit"},
            headers=_headers("TEACHER"),
        )
        assert resp.status_code == 422

    def test_bad_kind_is_422(self, client):
        resp = client.post(
            "/api/admin/assistant/help/ui-anchor-events",
            json={"anchor_id": "header.inspect", "kind": "shown"},
            headers=_headers("TEACHER"),
        )
        assert resp.status_code == 422

    def test_success_records_help_usage_trace_with_exact_payload(self, client, monkeypatch):
        monkeypatch.setattr(services_mod, "recent_duplicate_ui_anchor_event", lambda *a, **k: False)
        captured: dict = {}

        def _fake_record(user_id, course_id, topic_id, **kwargs):
            captured["args"] = (user_id, course_id, topic_id)
            captured["kwargs"] = kwargs
            return "trace-1"

        monkeypatch.setattr(services_mod, "record_interest_trace", _fake_record)

        resp = client.post(
            "/api/admin/assistant/help/ui-anchor-events",
            json={"anchor_id": "header.inspect", "kind": "no_hit"},
            headers=_headers("TEACHER"),
        )
        assert resp.status_code == 201
        assert resp.json() == {"ok": True, "recorded": True}
        assert captured["args"][1] == "_ui"  # 予約疑似コースID（学習側と同じセンチネル）
        assert captured["kwargs"]["kind"] == "help_usage"
        payload = captured["kwargs"]["extra_payload"]
        assert payload == {"help_anchor": "ui:header.inspect", "documented": False, "no_hit": True}
        # 逐語（自由文）を積まない — text は固定文言のみ。
        assert captured["kwargs"]["text"] == "UI要素の使い方（未整備）"

    def test_dedup_skips_recording(self, client, monkeypatch):
        monkeypatch.setattr(services_mod, "recent_duplicate_ui_anchor_event", lambda *a, **k: True)

        def _unexpected(*a, **k):
            raise AssertionError("直近重複がある場合は record_interest_trace を呼んではならない")

        monkeypatch.setattr(services_mod, "record_interest_trace", _unexpected)
        resp = client.post(
            "/api/admin/assistant/help/ui-anchor-events",
            json={"anchor_id": "header.inspect", "kind": "no_hit"},
            headers=_headers("TEACHER"),
        )
        assert resp.status_code == 201
        assert resp.json() == {"ok": True, "recorded": False}


# --- POST /api/admin/assistant/chat の usage_help 分岐 --------------------


@pytestmark_api
class TestChatUsageHelpBranch:
    def test_llm_and_intent_classification_not_called(self, client, monkeypatch):
        def _boom_classify(*a, **k):
            raise AssertionError("usage_help のとき intent_mod.classify を呼んではならない")

        def _boom_quota(*a, **k):
            raise AssertionError("usage_help のとき LLM quota reserve を呼んではならない")

        monkeypatch.setattr(admin_assistant_mod.intent_mod, "classify", _boom_classify)
        monkeypatch.setattr(admin_assistant_mod, "_reserve_llm_quota", _boom_quota)

        trace_calls: list = []
        monkeypatch.setattr(
            services_mod, "record_interest_trace",
            lambda *a, **k: trace_calls.append(k) or "trace-1",
        )

        resp = client.post(
            "/api/admin/assistant/chat",
            json={
                "message": "使い方を教えて",
                "support_action": "usage_help",
                "ui_anchor": "materials.upload-zone",
            },
            headers=_headers("TEACHER"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["intent"] == "guidance"
        assert body["answer"]
        assert len(trace_calls) == 1
        assert trace_calls[0]["kind"] == "help_usage"
        # 逐語（送信メッセージ本文）を payload/text に積まない。
        assert "使い方を教えて" not in trace_calls[0].get("text", "")

    def test_ui_anchor_resolves_directly_without_guidance_search(self, client, monkeypatch):
        def _unexpected_guidance(*a, **k):
            raise AssertionError("ui_anchor が解決できるとき _guidance_response を呼んではならない")

        monkeypatch.setattr(admin_assistant_mod, "_guidance_response", _unexpected_guidance)

        expected = admin_anchors_mod.resolve_admin_ui_anchor("materials.upload-zone", "TEACHER")
        assert expected is not None

        trace_calls: list = []
        monkeypatch.setattr(
            services_mod, "record_interest_trace",
            lambda *a, **k: trace_calls.append(k) or "trace-1",
        )

        resp = client.post(
            "/api/admin/assistant/chat",
            json={
                "message": "アップロードの使い方",
                "support_action": "usage_help",
                "ui_anchor": "materials.upload-zone",
            },
            headers=_headers("TEACHER"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"] == expected["body"]
        assert body["citations"] == [{"doc": "manual/teacher/11-admin-materials.md#upload-zone"}]

        payload = trace_calls[0]["extra_payload"]
        assert payload == {"help_anchor": "ui:materials.upload-zone", "documented": True, "no_hit": False}

    def test_unknown_ui_anchor_falls_back_to_guidance_response(self, client, monkeypatch):
        calls: list = []

        def _fake_guidance(message, role, cap, screen_context=None):
            calls.append((message, role, cap, screen_context))
            return AssistantChatResponse(
                answer="フォールバック応答", intent="guidance",
                citations=[{"doc": "admin_operations/x.md#y"}],
            )

        monkeypatch.setattr(admin_assistant_mod, "_guidance_response", _fake_guidance)
        trace_calls: list = []
        monkeypatch.setattr(
            services_mod, "record_interest_trace",
            lambda *a, **k: trace_calls.append(k) or "trace-1",
        )

        resp = client.post(
            "/api/admin/assistant/chat",
            json={
                "message": "使い方",
                "support_action": "usage_help",
                "ui_anchor": "no.such.anchor",
            },
            headers=_headers("TEACHER"),
        )
        assert resp.status_code == 200
        assert resp.json()["answer"] == "フォールバック応答"
        assert len(calls) == 1
        assert calls[0][2] is None  # cap は None で呼ばれる（capability 未特定）

        payload = trace_calls[0]["extra_payload"]
        assert payload == {"help_anchor": "ui:no.such.anchor", "documented": True, "no_hit": False}

    def test_unknown_ui_anchor_fallback_no_hit(self, client, monkeypatch):
        monkeypatch.setattr(
            admin_assistant_mod, "_guidance_response",
            lambda message, role, cap, screen_context=None: AssistantChatResponse(
                answer="できる操作の例: ...", intent="guidance", citations=[],
            ),
        )
        trace_calls: list = []
        monkeypatch.setattr(
            services_mod, "record_interest_trace",
            lambda *a, **k: trace_calls.append(k) or "trace-1",
        )

        resp = client.post(
            "/api/admin/assistant/chat",
            json={"message": "使い方", "support_action": "usage_help", "ui_anchor": "no.such.anchor"},
            headers=_headers("TEACHER"),
        )
        assert resp.status_code == 200
        payload = trace_calls[0]["extra_payload"]
        assert payload == {"help_anchor": "ui:no.such.anchor", "documented": False, "no_hit": True}

    def test_no_ui_anchor_uses_top_citation_as_help_anchor(self, client, monkeypatch):
        monkeypatch.setattr(
            admin_assistant_mod, "_guidance_response",
            lambda message, role, cap, screen_context=None: AssistantChatResponse(
                answer="ヒットしました", intent="guidance",
                citations=[{"doc": "manual/teacher/11-admin-materials.md#upload-zone"}],
            ),
        )
        trace_calls: list = []
        monkeypatch.setattr(
            services_mod, "record_interest_trace",
            lambda *a, **k: trace_calls.append(k) or "trace-1",
        )

        resp = client.post(
            "/api/admin/assistant/chat",
            json={"message": "アップロードのやり方", "support_action": "usage_help"},
            headers=_headers("TEACHER"),
        )
        assert resp.status_code == 200
        payload = trace_calls[0]["extra_payload"]
        assert payload == {
            "help_anchor": "manual/teacher/11-admin-materials.md#upload-zone",
            "documented": True,
            "no_hit": False,
        }

    def test_support_action_absent_uses_existing_intent_classification_path(self, client, monkeypatch):
        """既存の chat 動作（support_action なし）は一切変えない — 意図分類が通常どおり呼ばれる。"""
        from core.admin_assistant.schema import INTENT_GUIDANCE, IntentResult

        calls: list = []

        def _fake_classify(message, role, **kwargs):
            calls.append((message, role))
            return IntentResult(intent=INTENT_GUIDANCE, answer="", capability_id="", source="heuristic")

        monkeypatch.setattr(admin_assistant_mod.intent_mod, "classify", _fake_classify)
        monkeypatch.setattr(admin_assistant_mod, "_reserve_llm_quota", lambda uid: False)

        resp = client.post(
            "/api/admin/assistant/chat",
            json={"message": "コースを公開したい"},
            headers=_headers("TEACHER"),
        )
        assert resp.status_code == 200
        assert len(calls) == 1  # usage_help 分岐を通らず、通常の意図分類パスに到達する
