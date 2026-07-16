"""横断ユーティリティ層（Admin Copilot）— 権限・道案内・代行・戻す・ガードレールの検証。

構成（doubt / atlas API テストに倣う）:
  - Group A: 純粋ロジック + 構造テスト（DB 不要・FastAPI 不要）。
  - Group B: TestClient + インメモリ fake（実 DB なしで apply→revert / 確認ゲートを検証）。
    FastAPI 未導入の環境（CI 素の Python）では skip。

観点（設計 §12）:
  - P1 fail-closed: 権限外の capability は説明も道案内も代行もしない。
  - P2 確認ゲート: reversible=False は無確認実行を拒否。
  - P3 情報を落とさない: apply の before で revert が元に戻る。
  - P5 監査: apply / revert が theory_review_events（assistant_action）に記録される。
  - ガードレール: 全 reversible=False は confirm=True / core が FastAPI を import しない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "api"))

from core.admin_assistant import capabilities as caps  # noqa: E402
from core.admin_assistant import intent as intent_mod  # noqa: E402
from core.admin_assistant import knowledge as kb  # noqa: E402
from core.admin_assistant.actions import (  # noqa: E402
    ActionArgError,
    ActionContext,
    CoursePublishAction,
    CourseSetVisibilityAction,
)
from core.admin_assistant.schema import (  # noqa: E402
    INTENT_ACTION,
    INTENT_GUIDANCE,
    INTENT_LOCATE,
    INTENT_STATUS_QUERY,
    KIND_ACTION,
    role_satisfies,
)
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

_CORE_DIR = BACKEND / "core" / "admin_assistant"
_ROUTE_SRC = (BACKEND / "api" / "routes" / "admin_assistant.py").read_text(encoding="utf-8")
_ADMIN_JS = (ROOT / "frontend" / "public" / "js" / "admin-assistant.js").read_text(encoding="utf-8")
_ADMIN_JS_MAIN = (ROOT / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")


def _extract_js_function(src: str, fn_name: str) -> str:
    """``function fn_name(`` から対応する閉じ ``}`` までを波括弧カウントで切り出す。

    ``extract_function_source``（guardrail_helpers）は Python の ``def`` 構文専用のため、
    ES5 の ``function foo() {...}`` 宣言には使えない。admin-assistant.js は ES5 なので
    このテストファイル内だけの素朴な波括弧カウント版を使う。
    """
    marker = f"function {fn_name}("
    start = src.index(marker)
    brace_start = src.index("{", start)
    depth = 0
    for i in range(brace_start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise ValueError(f"unbalanced braces while extracting function {fn_name}")


# ===========================================================================
# Group A-1: Capability Registry（P1 / P2）
# ===========================================================================


class TestRegistry:
    def test_registry_validates(self):
        caps.validate_registry()  # 例外を投げない

    def test_all_irreversible_require_confirm(self):
        """P2: reversible=False の action は必ず confirm=True。"""
        for cap in caps.all_capabilities():
            if cap.kind == KIND_ACTION and not cap.reversible:
                assert cap.confirm, f"{cap.id} は reversible=False だが confirm=False"

    def test_teacher_cannot_reach_system_admin_capabilities(self):
        teacher_ids = {c.id for c in caps.capabilities_for("TEACHER")}
        assert "users.create_teacher" not in teacher_ids
        assert "system.view_stats" not in teacher_ids
        assert "system.view_error_logs" not in teacher_ids
        assert "llm_usage.view_metrics" not in teacher_ids  # G6: SYSTEM_ADMIN専用
        # 教員が使える安全な操作は含まれる
        assert "materials.upload" in teacher_ids
        assert "course.publish" in teacher_ids

    def test_known_screens_include_g6_new_tabs(self):
        """G6是正: knowledge-library / llm-usage は実装済みタブなのに未登録で
        「構造的に案内不能」だった（vision_ux_gap_survey_2026-07.md G6）。"""
        assert "knowledge-library" in caps.KNOWN_SCREENS
        assert "llm-usage" in caps.KNOWN_SCREENS

    def test_g6_new_capabilities_registered(self):
        """G6是正: C/D/R/W/L/V/U層・Phase Bの段階登録（新規11件）。"""
        expected = {
            "doubt.record_verification_status": ("doubt-atlas", "TEACHER"),
            "doubt.manage_challenge": ("doubt-atlas", "TEACHER"),
            "reconstruction.review_queue": ("lecture-studio", "TEACHER"),
            "deliberation.identity_links_standardization": ("lecture-studio", "TEACHER"),
            "library.view_and_freeze": ("knowledge-library", "TEACHER"),
            "materials.manage_shared_version": ("materials", "TEACHER"),
            "course.manage_shared_version": ("course-management", "TEACHER"),
            "llm_usage.view_metrics": ("llm-usage", "SYSTEM_ADMIN"),
            "materials.estimate_cost": ("materials", "TEACHER"),
            "interest_dashboard.bridge_insights": ("interest-dashboard", "TEACHER"),
            "course.sharing_dashboard": ("course-management", "TEACHER"),
        }
        for cap_id, (screen, role) in expected.items():
            cap = caps.get_capability(cap_id)
            assert cap is not None, f"{cap_id} が未登録"
            assert cap.screen == screen, f"{cap_id} の screen が不一致: {cap.screen}"
            assert cap.required_role == role, f"{cap_id} の required_role が不一致: {cap.required_role}"
            assert cap.kind == "guidance_only", f"{cap_id} は guidance_only であるべき"
            assert cap.howto_doc, f"{cap_id} に howto_doc が無い"

    def test_g6_capabilities_reachable_only_by_declared_role(self):
        assert caps.can_access("llm_usage.view_metrics", "TEACHER") is False
        assert caps.can_access("llm_usage.view_metrics", "SYSTEM_ADMIN") is True
        assert caps.can_access("doubt.record_verification_status", "TEACHER") is True
        assert caps.can_access("doubt.record_verification_status", "STUDENT") is False

    def test_course_publish_api_points_to_real_visibility_endpoint(self):
        """G1-6是正: `PUT .../publish` は撤去済み（test_publish_endpoint_removed）。
        capability の api メタデータが存在しないエンドポイントを指してはいけない。"""
        cap = caps.get_capability("course.publish")
        assert cap.api is not None
        assert cap.api["path"] == "/api/admin/courses/{course_id}/visibility"
        assert "/publish" not in cap.api["path"]

    def test_system_admin_reaches_everything(self):
        admin_ids = {c.id for c in caps.capabilities_for("SYSTEM_ADMIN")}
        assert admin_ids == {c.id for c in caps.all_capabilities()}

    def test_student_reaches_nothing(self):
        assert caps.capabilities_for("STUDENT") == []

    def test_can_access_fail_closed_on_unknown(self):
        assert caps.can_access("does.not.exist", "SYSTEM_ADMIN") is False
        assert caps.can_access("", "TEACHER") is False

    def test_role_hierarchy(self):
        assert role_satisfies("TEACHER", "SYSTEM_ADMIN") is True
        assert role_satisfies("SYSTEM_ADMIN", "TEACHER") is False
        assert role_satisfies("TEACHER", "STUDENT") is False

    def test_locate_steps_well_formed(self):
        for cap in caps.all_capabilities():
            for st in cap.locate_steps:
                assert st.screen in caps.KNOWN_SCREENS
                assert st.anchor_id and st.hint


# ===========================================================================
# Group A-2: 操作 KB（P4 / role フィルタ）
# ===========================================================================


class TestKnowledge:
    def test_kb_available(self):
        kb.clear_cache()
        assert kb.kb_available() is True

    def test_every_capability_has_a_kb_section(self):
        missing = [
            c.id for c in caps.all_capabilities()
            if c.howto_doc and (kb.section_for_howto(c.howto_doc) is None
                                or not kb.section_for_howto(c.howto_doc).get("body"))
        ]
        assert missing == [], f"KB 未整備の capability: {missing}"

    def test_role_filter_hides_out_of_role_docs(self):
        """TEACHER の guidance 検索は SYSTEM_ADMIN 専用手順を surface しない（P1）。"""
        res = kb.search("教員アカウントを作りたい", caps.capabilities_for("TEACHER"), limit=5)
        assert all(r["capability_id"] != "users.create_teacher" for r in res)

    def test_search_returns_citations(self):
        res = kb.search("教材をアップロードする方法", caps.capabilities_for("TEACHER"), limit=3)
        assert res and res[0]["citation"].startswith("admin_operations/")


# ===========================================================================
# Group A-3: intent ヒューリスティック（決定論・非LLM）
# ===========================================================================


class TestIntentHeuristic:
    def test_publish_is_action(self):
        r = intent_mod.classify(
            "このコースを学生に公開したい", "TEACHER",
            screen_context={"tab": "course-management", "selection": {"course_id": "c1"}},
        )
        assert r.intent == INTENT_ACTION
        assert r.capability_id == "course.publish"
        assert r.source == "heuristic"

    def test_where_is_locate(self):
        r = intent_mod.classify(
            "教材ってどこからアップロードするの？", "TEACHER",
            screen_context={"tab": "materials"},
        )
        assert r.intent == INTENT_LOCATE
        assert r.capability_id == "materials.upload"

    def test_teacher_resolving_admin_only_capability_is_not_accessible(self):
        """権限外 capability を解決しても、can_access は False（route が拒否する）。"""
        r = intent_mod.classify("教員アカウントはどこで作りますか", "TEACHER")
        assert r.capability_id == "users.create_teacher"
        assert caps.can_access(r.capability_id, "TEACHER") is False

    def test_parse_visibility(self):
        assert intent_mod.parse_visibility("全体に公開して") == "public"
        assert intent_mod.parse_visibility("グループ限定にして") == "group"
        assert intent_mod.parse_visibility("非公開にして") == "private"
        assert intent_mod.parse_visibility("よろしく") is None

    def test_status_query_progress_phrase(self):
        r = intent_mod.classify("解析どうなってる？", "TEACHER")
        assert r.intent == INTENT_STATUS_QUERY
        assert r.capability_id == ""

    def test_status_query_completion_phrase(self):
        r = intent_mod.classify("教材の処理は終わりましたか", "TEACHER")
        assert r.intent == INTENT_STATUS_QUERY

    def test_view_stats_keyword_alone_is_not_status_query(self):
        """「状況」単体（system.view_stats のキーワード）は状態照会に奪われない。"""
        r = intent_mod.classify("利用状況を見たい", "TEACHER")
        assert r.intent != INTENT_STATUS_QUERY
        assert r.intent == INTENT_GUIDANCE
        assert r.capability_id == "system.view_stats"

    def test_where_question_still_locates_over_status_query(self):
        r = intent_mod.classify(
            "教材アップロードはどこ？", "TEACHER", screen_context={"tab": "materials"},
        )
        assert r.intent == INTENT_LOCATE
        assert r.capability_id == "materials.upload"


# ===========================================================================
# Group A-4: Action handler の純粋ロジック
# ===========================================================================


class TestActionLogic:
    def test_visibility_public_sets_published_template(self):
        act = CourseSetVisibilityAction()
        before = {"visibility": "private", "group_id": None, "is_published": False, "is_template": False}
        after = act._target_state(before, {"visibility": "public"})
        assert after["visibility"] == "public"
        assert after["is_published"] is True
        assert after["is_template"] is True

    def test_visibility_group_requires_group_id(self):
        act = CourseSetVisibilityAction()
        before = {"visibility": "private", "group_id": None, "is_published": False, "is_template": False}
        with pytest.raises(ActionArgError):
            act._target_state(before, {"visibility": "group"})

    def test_visibility_invalid_value(self):
        act = CourseSetVisibilityAction()
        before = {"visibility": "private", "group_id": None, "is_published": False, "is_template": False}
        with pytest.raises(ActionArgError):
            act._target_state(before, {"visibility": "everyone"})

    def test_visibility_public_to_private_forces_unpublished(self):
        """G1-6是正の回帰テスト: admin.py::update_course_visibility は
        `is_published = (visibility = 'public')` を常に強制する（G1-1 是正）。
        Copilot 経由の代行がこれと異なる状態（is_published を旧値のまま残す）を
        作っていたバグを固定する。"""
        act = CourseSetVisibilityAction()
        before = {"visibility": "public", "group_id": None, "is_published": True, "is_template": True}
        after = act._target_state(before, {"visibility": "private"})
        assert after["visibility"] == "private"
        assert after["is_published"] is False
        # is_template は「作られたことがあるか」の意図を保つため離脱時にリセットしない
        assert after["is_template"] is True

    def test_visibility_public_to_group_forces_unpublished(self):
        act = CourseSetVisibilityAction()
        before = {"visibility": "public", "group_id": None, "is_published": True, "is_template": True}
        after = act._target_state(before, {"visibility": "group", "group_id": "g1"})
        assert after["is_published"] is False
        assert after["group_id"] == "g1"

    def test_publish_capability_id(self):
        assert CoursePublishAction.capability_id == "course.publish"


# ===========================================================================
# Group A-5: ガードレール（構造テスト）
# ===========================================================================


class TestGuardrails:
    def test_core_does_not_import_fastapi(self):
        """core/admin_assistant は FastAPI を import しない（開発ルール2 / testability）。"""
        assert_module_tree_does_not_import(_CORE_DIR, ["fastapi"])

    def test_audit_uses_assistant_action_entity_type(self):
        assert "'assistant_action'" in _ROUTE_SRC

    def test_apply_and_revert_are_audited(self):
        assert "_record_assistant_event" in _ROUTE_SRC
        # apply / revert 両経路で 'applied' / 'reverted' を記録している
        assert '"applied"' in _ROUTE_SRC or "'applied'" in _ROUTE_SRC
        assert '"reverted"' in _ROUTE_SRC or "'reverted'" in _ROUTE_SRC

    def test_confirm_gate_present(self):
        assert "confirm_pending" in _ROUTE_SRC
        assert "cap.confirm and not body.confirm" in _ROUTE_SRC

    def test_migration_034_present(self):
        """正本は backend/db/034_assistant_actions.sql（main.py のインライン DDL ではない）。"""
        sql = read_migration_sql(BACKEND, 34)
        assert "assistant_actions" in sql

    def test_registry_anchor_ids_registered_in_frontend(self):
        """registry の locate anchor（base id）が admin.js の registerUiAnchors に存在する。"""
        base_ids = set()
        for cap in caps.all_capabilities():
            for st in cap.locate_steps:
                base = st.anchor_id.split(":")[0]
                base_ids.add(base)
        for base in base_ids:
            assert base in _ADMIN_JS_MAIN, f"anchor {base} が admin.js に未登録"

    def test_frontend_uses_prefix_and_spotlight_class(self):
        assert "admin-assistant-spotlight" in _ADMIN_JS
        assert "window.AdminAssistant" in _ADMIN_JS


# ===========================================================================
# Group A-6: status_query（Phase 3, 状態管理・通知基盤との統合）
# ===========================================================================


class TestStatusQueryGuardrails:
    """状態照会 intent は読み取り専用（DB非変更・LLM非呼び出し）であること。"""

    def test_no_write_statements_in_status_query_handler(self):
        handler_src = extract_function_source(_ROUTE_SRC, "_status_query_response")
        assert_source_forbids(handler_src, ["INSERT", "UPDATE", "DELETE"], context="_status_query_response")

    def test_scoped_to_current_user(self):
        handler_src = extract_function_source(_ROUTE_SRC, "_status_query_response")
        assert "uploaded_by" in handler_src
        assert "user_id" in handler_src

    def test_dispatch_branch_present(self):
        assert "INTENT_STATUS_QUERY" in _ROUTE_SRC
        assert "res.intent == INTENT_STATUS_QUERY" in _ROUTE_SRC

    def test_intent_registered_in_schema(self):
        from core.admin_assistant.schema import INTENTS

        assert INTENT_STATUS_QUERY in INTENTS


# ===========================================================================
# Group B: API 結合テスト（TestClient + インメモリ fake）
# ===========================================================================

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark_api = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入（full API は Docker 内で）")

_UID_TEACHER = "11111111-1111-1111-1111-111111111111"
_UID_OTHER = "22222222-2222-2222-2222-222222222222"


def _headers(role: str, sub: str = _UID_TEACHER):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    return {"Authorization": "Bearer " + jwt.encode(payload, "test-secret-key", algorithm="HS256")}


class _FakeStore:
    """learning_courses + assistant_actions のインメモリ代替。"""

    def __init__(self):
        self.courses = {}   # course_id -> state
        self.actions = {}   # action_id -> row
        self.events = []    # (entity_id, new_status)
        self._seq = 0

    # --- courses ---
    def fetch_course(self, cid, uid):
        st = self.courses.get(cid)
        return dict(st) if st else None

    def write_course(self, cid, uid, state):
        if cid not in self.courses:
            return False
        self.courses[cid] = dict(state)
        return True

    # --- actions ---
    def create_action(self, **kw):
        self._seq += 1
        aid = "a_test_%d" % self._seq
        row = {
            "id": aid, "user_id": kw["user_id"], "session_id": kw.get("session_id"),
            "capability_id": kw["capability_id"], "screen": kw["screen"],
            "target_type": kw["target_type"], "target_id": kw.get("target_id"),
            "args": kw.get("args") or {}, "before_snapshot": kw.get("before_snapshot"),
            "after_snapshot": kw.get("after_snapshot"), "reversible": bool(kw.get("reversible", True)),
            "revert_spec": kw.get("revert_spec"), "status": kw.get("status", "applied"),
            "reverted_at": None, "created_at": "2026-07-07T00:00:00+00:00",
        }
        self.actions[aid] = row
        return dict(row)

    def get_action(self, aid):
        row = self.actions.get(aid)
        return dict(row) if row else None

    def mark_reverted(self, aid):
        row = self.actions.get(aid)
        if row and row["status"] == "applied":
            row["status"] = "reverted"
            row["reverted_at"] = "2026-07-07T01:00:00+00:00"
            return True
        return False

    def list_actions(self, uid, limit=20):
        return [dict(r) for r in self.actions.values() if r["user_id"] == uid][:limit]


@pytest.fixture
def api(monkeypatch):
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI 未導入")
    for p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)

    from fastapi.testclient import TestClient
    from api.main import app
    import core.admin_assistant.actions as actions_mod
    import core.admin_assistant.action_store as store_mod
    import routes.admin_assistant as route_mod
    import services as services_mod

    store = _FakeStore()

    # learning_courses（DB）を in-memory に差し替え。
    monkeypatch.setattr(actions_mod, "_fetch_course_state", store.fetch_course)
    monkeypatch.setattr(actions_mod, "_write_course_state", store.write_course)
    # assistant_actions（DB）を in-memory に差し替え。
    monkeypatch.setattr(store_mod, "create_action", lambda **kw: store.create_action(**kw))
    monkeypatch.setattr(store_mod, "get_action", store.get_action)
    monkeypatch.setattr(store_mod, "mark_reverted", store.mark_reverted)
    monkeypatch.setattr(store_mod, "list_actions", store.list_actions)
    # 監査（DB）を capture に差し替え（P5 検証）。
    monkeypatch.setattr(
        route_mod, "_record_assistant_event",
        lambda entity_id, old, new, uid, meta=None: store.events.append((entity_id, new)),
    )
    # 所有権チェックは既定 True（別テストで False に上書き）。
    monkeypatch.setattr(services_mod, "user_owns_course", lambda uid, cid: True)
    # LLM を使わず heuristic に固定（オフライン・決定論）。
    monkeypatch.setattr(route_mod, "_reserve_llm_quota", lambda uid: False)

    client = TestClient(app)
    return client, store, monkeypatch, services_mod


# --- chat ---------------------------------------------------------------


@pytestmark_api
class TestChatAPI:
    def test_student_forbidden(self, api):
        client, _store, _mp, _svc = api
        r = client.post("/api/admin/assistant/chat",
                        json={"message": "help"}, headers=_headers("STUDENT"))
        assert r.status_code == 403

    def test_guidance_with_citation(self, api):
        client, _store, _mp, _svc = api
        r = client.post("/api/admin/assistant/chat",
                        json={"message": "教材のアップロード方法を教えて",
                              "screen_context": {"tab": "materials"}},
                        headers=_headers("TEACHER"))
        assert r.status_code == 200
        data = r.json()
        assert data["intent"] == "guidance"
        assert "アップロード" in data["answer"]
        assert len(data["citations"]) >= 1

    def test_locate_returns_plan(self, api):
        client, _store, _mp, _svc = api
        r = client.post("/api/admin/assistant/chat",
                        json={"message": "教材ってどこからアップロードするの？",
                              "screen_context": {"tab": "materials"}},
                        headers=_headers("TEACHER"))
        data = r.json()
        assert data["intent"] == "locate"
        assert data["locate_plan"]["capability_id"] == "materials.upload"
        assert len(data["locate_plan"]["steps"]) >= 1

    def test_locate_fail_closed_for_out_of_role(self, api):
        """P1/P8: TEACHER が SYSTEM_ADMIN 専用操作の場所を尋ねても locate_plan を返さない。"""
        client, _store, _mp, _svc = api
        r = client.post("/api/admin/assistant/chat",
                        json={"message": "教員アカウントはどこで作成しますか",
                              "screen_context": {"tab": "groups"}},
                        headers=_headers("TEACHER"))
        data = r.json()
        assert data["locate_plan"] is None
        assert "実行できません" in data["answer"]

    def test_status_query_degrades_gracefully_without_live_db(self, api):
        """DB 未接続環境でも捏造せず fail-closed に縮退する（S5）。action_plan は出さない。"""
        client, _store, _mp, _svc = api
        r = client.post("/api/admin/assistant/chat",
                        json={"message": "解析どうなってる？"},
                        headers=_headers("TEACHER"))
        assert r.status_code == 200
        data = r.json()
        assert data["intent"] == "status_query"
        assert data["action_plan"] is None
        assert data["locate_plan"] is None
        assert data["answer"]

    def test_action_plan_confirm_required_for_publish(self, api):
        client, _store, _mp, _svc = api
        r = client.post("/api/admin/assistant/chat",
                        json={"message": "このコースを学生に公開して",
                              "screen_context": {"tab": "course-management",
                                                 "selection": {"course_id": "c1"}}},
                        headers=_headers("TEACHER"))
        data = r.json()
        assert data["intent"] == "action"
        plan = data["action_plan"]
        assert plan["capability_id"] == "course.publish"
        assert plan["confirm_required"] is True
        assert plan["supported"] is True


# --- actions apply / revert --------------------------------------------


@pytestmark_api
class TestActionAPI:
    def _seed_course(self, store, cid="c1"):
        store.courses[cid] = {"visibility": "private", "group_id": None,
                              "is_published": False, "is_template": False}

    def test_set_visibility_apply_and_revert(self, api):
        client, store, _mp, _svc = api
        self._seed_course(store)
        # apply（可逆・L2）
        r = client.post("/api/admin/assistant/actions",
                        json={"capability_id": "course.set_visibility",
                              "target": {"type": "course", "id": "c1"},
                              "args": {"visibility": "public"}},
                        headers=_headers("TEACHER"))
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "applied"
        assert data["after"]["visibility"] == "public"
        assert store.courses["c1"]["visibility"] == "public"
        assert store.courses["c1"]["is_published"] is True
        aid = data["action_id"]
        assert ("applied" in [e[1] for e in store.events])  # P5 監査

        # revert（before に戻る）
        r2 = client.post("/api/admin/assistant/actions/%s/revert" % aid, headers=_headers("TEACHER"))
        assert r2.status_code == 200
        assert r2.json()["status"] == "reverted"
        assert store.courses["c1"]["visibility"] == "private"       # P3: 元に戻る
        assert store.courses["c1"]["is_published"] is False
        assert ("reverted" in [e[1] for e in store.events])         # P5 監査

    def test_set_visibility_public_to_private_via_api_forces_unpublished(self, api):
        """G1-6 是正の回帰テスト（API 経由）: いったん public にしたコースを private へ
        戻すと is_published が False になる（admin.py の visibility エンドポイントと
        同一意味論。旧実装のバグでは is_published=True のまま残っていた）。"""
        client, store, _mp, _svc = api
        store.courses["c1"] = {"visibility": "public", "group_id": None,
                                "is_published": True, "is_template": True}
        r = client.post("/api/admin/assistant/actions",
                        json={"capability_id": "course.set_visibility",
                              "target": {"type": "course", "id": "c1"},
                              "args": {"visibility": "private"}},
                        headers=_headers("TEACHER"))
        assert r.status_code == 200
        data = r.json()
        assert data["after"]["visibility"] == "private"
        assert data["after"]["is_published"] is False
        assert store.courses["c1"]["is_published"] is False

    def test_publish_confirm_gate_then_apply(self, api):
        client, store, _mp, _svc = api
        self._seed_course(store)
        # 無確認 → confirm_pending（実行しない, P2）
        r = client.post("/api/admin/assistant/actions",
                        json={"capability_id": "course.publish",
                              "target": {"type": "course", "id": "c1"}, "confirm": False},
                        headers=_headers("TEACHER"))
        assert r.status_code == 200
        assert r.json()["status"] == "confirm_pending"
        assert store.courses["c1"]["is_published"] is False          # 変更されていない

        # 確認あり → applied（不可逆）
        r2 = client.post("/api/admin/assistant/actions",
                         json={"capability_id": "course.publish",
                               "target": {"type": "course", "id": "c1"}, "confirm": True},
                         headers=_headers("TEACHER"))
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["status"] == "applied"
        assert d2["reversible"] is False
        assert store.courses["c1"]["is_published"] is True
        aid = d2["action_id"]

        # 不可逆の revert → 409
        r3 = client.post("/api/admin/assistant/actions/%s/revert" % aid, headers=_headers("TEACHER"))
        assert r3.status_code == 409

    def test_action_forbidden_for_out_of_role(self, api):
        """P1: TEACHER が SYSTEM_ADMIN 専用 action を叩くと 403（handler 到達前）。"""
        client, _store, _mp, _svc = api
        r = client.post("/api/admin/assistant/actions",
                        json={"capability_id": "users.create_teacher", "target": {}},
                        headers=_headers("TEACHER"))
        assert r.status_code == 403

    def test_action_requires_ownership(self, api):
        client, store, monkeypatch, services_mod = api
        self._seed_course(store)
        monkeypatch.setattr(services_mod, "user_owns_course", lambda uid, cid: False)
        r = client.post("/api/admin/assistant/actions",
                        json={"capability_id": "course.set_visibility",
                              "target": {"type": "course", "id": "c1"},
                              "args": {"visibility": "public"}},
                        headers=_headers("TEACHER"))
        assert r.status_code == 403

    def test_revert_others_action_forbidden(self, api):
        client, store, _mp, _svc = api
        # 別ユーザーの action を仕込む
        row = store.create_action(
            user_id=_UID_OTHER, capability_id="course.set_visibility", screen="course-management",
            target_type="course", target_id="c1", args={}, before_snapshot={}, after_snapshot={},
            reversible=True, revert_spec=None, status="applied",
        )
        r = client.post("/api/admin/assistant/actions/%s/revert" % row["id"], headers=_headers("TEACHER"))
        assert r.status_code == 403

    def test_unknown_capability_404(self, api):
        client, _store, _mp, _svc = api
        r = client.post("/api/admin/assistant/actions",
                        json={"capability_id": "nope.nope", "target": {}},
                        headers=_headers("TEACHER"))
        assert r.status_code == 404

    def test_list_actions_returns_reversible_status_for_undo_reconstruction(self, api):
        """Copilot Undo 永続化（低優先度課題）: GET /actions が admin-assistant.js の
        loadServerActionHistory() が actionStack を再構成するのに必要な
        reversible / status / capability_id を返すこと。"""
        client, store, _mp, _svc = api
        self._seed_course(store)
        r = client.post("/api/admin/assistant/actions",
                        json={"capability_id": "course.set_visibility",
                              "target": {"type": "course", "id": "c1"},
                              "args": {"visibility": "public"}},
                        headers=_headers("TEACHER"))
        assert r.status_code == 200
        r2 = client.get("/api/admin/assistant/actions", headers=_headers("TEACHER"))
        assert r2.status_code == 200
        rows = r2.json()
        assert len(rows) == 1
        assert rows[0]["capability_id"] == "course.set_visibility"
        assert rows[0]["reversible"] is True
        assert rows[0]["status"] == "applied"
        assert "action_id" in rows[0]

    def test_list_actions_forbidden_for_student(self, api):
        client, _store, _mp, _svc = api
        r = client.get("/api/admin/assistant/actions", headers=_headers("STUDENT"))
        assert r.status_code == 403


# ===========================================================================
# Group A-7: Copilot Undo の永続化（低優先度課題）— フロント静的配線の検証
#
# 「Admin Copilot の Undo はメモリ内 actionStack のみで、サーバ側 assistant_actions
# 履歴はリロード後に一切見えない」（vision_ux_gap_survey_2026-07.md §2 G2 末尾）を
# 是正する。init() 時に GET /admin/assistant/actions からまだ取り消し可能な行だけを
# 積み直す。JS 実行はしない静的アサーションのみ（既存 TestFrontendIntegration 方式）。
# ===========================================================================


class TestUndoPersistenceFrontend:
    def test_load_server_action_history_function_exists(self):
        assert "function loadServerActionHistory" in _ADMIN_JS

    def test_init_calls_load_server_action_history(self):
        init_src = _extract_js_function(_ADMIN_JS, "init")
        assert "loadServerActionHistory()" in init_src

    def test_history_endpoint_is_actions_list(self):
        body = _extract_js_function(_ADMIN_JS, "loadServerActionHistory")
        assert '"/admin/assistant/actions"' in body or "'/admin/assistant/actions'" in body

    def test_only_reversible_and_applied_are_reconstructed(self):
        """revert 済み・不可逆（reversible=false）は積まない。"""
        body = _extract_js_function(_ADMIN_JS, "loadServerActionHistory")
        assert "reversible" in body
        assert '"applied"' in body or "'applied'" in body

    def test_reconstructed_entries_are_kind_server(self):
        body = _extract_js_function(_ADMIN_JS, "loadServerActionHistory")
        assert '"server"' in body or "'server'" in body

    def test_fails_closed_on_fetch_error(self):
        """取得失敗時は静かに空スタックのまま（既存 actionStack を壊さない）。"""
        body = _extract_js_function(_ADMIN_JS, "loadServerActionHistory")
        assert ".catch(" in body

    def test_admin_assistant_js_is_es5(self):
        # admin-assistant.js は既存コードが ES5 のため、追加分も規約を維持する
        # （既存の renderMarkdown は正規表現中のバッククォートであってテンプレート
        # リテラルではないため、追加した関数だけを対象に確認する）。
        added = _extract_js_function(_ADMIN_JS, "loadServerActionHistory")
        assert "=>" not in added
        assert re.search(r"\bconst\s", added) is None
        assert re.search(r"\blet\s", added) is None
        assert "`" not in added
