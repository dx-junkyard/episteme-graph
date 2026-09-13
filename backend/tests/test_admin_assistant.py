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
    ActionTargetError,
    CoursePublishAction,
    CourseSetVisibilityAction,
    LectureRewriteChunkScriptAction,
    get_handler,
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


def _parse_registered_anchors(src: str) -> dict:
    """admin.js の `AA.registerUiAnchors("<screen>", {...})` を解析し
    `{screen: {anchor_id, ...}}` を返す。

    旧ガードレール（anchor 文字列が admin.js の**どこかに**存在するかの部分文字列一致）は
    screen の対応を検証しなかったため、「別画面に同名 anchor がある」「コメントに文字列
    だけ残っている」ケースを見逃した（vision_ux_gap_survey_2026-07-17.md §2-P5）。
    ここではオブジェクトリテラルを波括弧カウントで切り出し、`key: function` 形式の
    キーだけを anchor として数える。
    """
    anchors: dict = {}
    for m in re.finditer(r'registerUiAnchors\(\s*"([^"]+)"\s*,\s*\{', src):
        screen = m.group(1)
        start = src.index("{", m.end() - 1)
        depth = 0
        end = None
        for i in range(start, len(src)):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        assert end is not None, f"unbalanced braces in registerUiAnchors({screen!r})"
        block = src[start : end + 1]
        keys = set()
        for km in re.finditer(r'(?m)^\s*(?:"([^"]+)"|([A-Za-z_$][\w$]*))\s*:\s*function', block):
            keys.add(km.group(1) or km.group(2))
        anchors.setdefault(screen, set()).update(keys)
    return anchors


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

    def test_n13_n31_new_capabilities_registered(self):
        """N13/N31: 図分類レビュー + stumbles / schema-proposals の capability 登録。"""
        expected = {
            "materials.review_figures": "materials",
            "stumbles.view": "stumbles",
            "schema_proposals.review": "schema-proposals",
        }
        for cap_id, screen in expected.items():
            cap = caps.get_capability(cap_id)
            assert cap is not None, f"{cap_id} が未登録"
            assert cap.screen == screen
            assert cap.kind == "guidance_only", f"{cap_id} は guidance_only であるべき"
            assert cap.required_role == "TEACHER"
            assert cap.howto_doc, f"{cap_id} に howto_doc が無い"
            assert cap.locate_steps, f"{cap_id} に locate_steps が無い"

    def test_rewrite_capability_declares_l2_revert(self):
        """N12: rewrite は実 API が即時保存するため L2 永続可逆として宣言する。"""
        cap = caps.get_capability("lecture_studio.rewrite_chunk_script")
        assert cap.reversible is True
        assert cap.confirm is False  # 可逆なので確認ゲート不要（P2 と整合）
        assert cap.revert == {"strategy": "restore_chunk_script"}
        # 実エンドポイントは POST（routes/lecture_studio/scripts.py）
        assert cap.api["method"] == "POST"
        assert cap.api["path"] == "/api/admin/chunks/{chunk_id}/lecture-script/rewrite"
        # 既存 rewrite API は _require_teacher のみでコース所有チェックを持たない（P7）。
        # scope="own_course" のままだと route が chunk_id を user_owns_course に渡して
        # 常に 403 になるため、any でなければならない。
        assert cap.scope == "any"

    def test_rewrite_handler_is_registered(self):
        """N12: 原点動機の rewrite に代行ハンドラが存在する。"""
        handler = get_handler("lecture_studio.rewrite_chunk_script")
        assert handler is not None
        assert isinstance(handler, LectureRewriteChunkScriptAction)


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
# Group A-3b: 会話履歴の整形 + LLM プロンプトへの反映
# ===========================================================================


class TestNormalizeHistory:
    """_normalize_history: 会話履歴の整形（重複除去・role フィルタ・上限）。"""

    def test_drops_trailing_duplicate_current_message(self):
        """フロントは送信前に現在発話を history へ push するため、末尾の重複を除く。"""
        hist = [
            {"role": "user", "content": "コースを作りたい"},
            {"role": "assistant", "content": "コースビルダーを開きます"},
            {"role": "user", "content": "それを公開して"},
        ]
        out = intent_mod._normalize_history(hist, "それを公開して")
        assert out == [
            {"role": "user", "content": "コースを作りたい"},
            {"role": "assistant", "content": "コースビルダーを開きます"},
        ]

    def test_filters_unknown_roles_and_empty(self):
        hist = [
            {"role": "system", "content": "無視される"},
            {"role": "user", "content": "   "},
            {"role": "user", "content": "有効"},
            {"content": "role なし"},
            "not a dict",
        ]
        out = intent_mod._normalize_history(hist, "現在の発話")
        assert out == [{"role": "user", "content": "有効"}]

    def test_caps_turn_count(self):
        hist = [{"role": "user", "content": "m%d" % i} for i in range(20)]
        out = intent_mod._normalize_history(hist, "現在")
        assert len(out) == intent_mod._HISTORY_MAX_TURNS

    def test_trims_long_content(self):
        out = intent_mod._normalize_history(
            [{"role": "assistant", "content": "あ" * 1000}], "x"
        )
        assert len(out) == 1
        assert len(out[0]["content"]) == intent_mod._HISTORY_MAX_CHARS

    def test_empty_or_none_history(self):
        assert intent_mod._normalize_history([], "x") == []
        assert intent_mod._normalize_history(None, "x") == []


class TestIntentHistoryInPrompt:
    """classify(allow_llm=True) が会話履歴を唯一の LLM コールの messages に含めること。

    デッドパラメータ化していた `history` の回帰防止（指示語解決のため）。
    """

    def test_history_reaches_llm_messages(self, monkeypatch):
        from core.admin_assistant.schema import LLMIntentResponse
        import core.llm as llm_mod

        captured = {}

        def fake_generate(messages, schema, model=None):
            captured["messages"] = messages
            return LLMIntentResponse(
                intent=INTENT_ACTION, capability_id="course.publish", answer=""
            )

        monkeypatch.setattr(llm_mod, "generate_text_with_structured_output", fake_generate)

        hist = [
            {"role": "user", "content": "このコースを学生に見せたい"},
            {"role": "assistant", "content": "course.publish で公開できます"},
            {"role": "user", "content": "それをやって"},
        ]
        r = intent_mod.classify(
            "それをやって",
            "TEACHER",
            history=hist,
            screen_context={"tab": "course-management", "selection": {"course_id": "c1"}},
            allow_llm=True,
        )

        msgs = captured["messages"]
        assert msgs[0]["role"] == "system"
        # 直前の会話（ユーザー発話・アシスタント応答の両方）が渡っている
        joined = " ".join(m["content"] for m in msgs)
        assert "このコースを学生に見せたい" in joined
        assert "course.publish で公開できます" in joined
        # 末尾は ctx ブロック（現在発話 + 操作カタログ）
        assert "ユーザー発話: それをやって" in msgs[-1]["content"]
        # 現在発話は history 側で二重に現れない（重複除去）
        assert {"role": "user", "content": "それをやって"} not in msgs
        assert r.source == "llm"

    def test_no_history_still_works(self, monkeypatch):
        """history 未指定でも従来どおり system + ctx の2要素で成立する。"""
        from core.admin_assistant.schema import LLMIntentResponse
        import core.llm as llm_mod

        captured = {}

        def fake_generate(messages, schema, model=None):
            captured["messages"] = messages
            return LLMIntentResponse(intent=INTENT_GUIDANCE, capability_id="", answer="")

        monkeypatch.setattr(llm_mod, "generate_text_with_structured_output", fake_generate)

        intent_mod.classify("何ができますか", "TEACHER", allow_llm=True)
        msgs = captured["messages"]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"


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

    def test_supported_capability_ids_are_exactly_the_implemented_three(self):
        """N12（実行可否の事前開示）の根拠: handler 実装済み = この3件のみ。"""
        from core.admin_assistant.actions import supported_capability_ids

        assert set(supported_capability_ids()) == {
            "course.set_visibility",
            "course.publish",
            "lecture_studio.rewrite_chunk_script",
        }


class TestRewriteActionLogic:
    """N12: LectureRewriteChunkScriptAction の純ロジック（DB / LLM をモック）。"""

    def _handler(self):
        return LectureRewriteChunkScriptAction()

    def test_capture_before_requires_target(self):
        with pytest.raises(ActionArgError):
            self._handler().capture_before(
                ActionContext(user_id="u1", role="TEACHER", target_id=None)
            )

    def test_capture_before_missing_chunk(self, monkeypatch):
        import core.admin_assistant.actions as actions_mod

        monkeypatch.setattr(actions_mod, "_fetch_chunk_script", lambda cid: None)
        with pytest.raises(ActionTargetError):
            self._handler().capture_before(
                ActionContext(user_id="u1", role="TEACHER", target_id="ch1")
            )

    def test_apply_requires_prompt(self):
        with pytest.raises(ActionArgError):
            self._handler().apply(
                ActionContext(user_id="u1", role="TEACHER", target_id="ch1", args={}),
                {"display_text": "d", "spoken_text": "s", "formulas": []},
            )

    def test_apply_calls_existing_rewrite_api(self, monkeypatch):
        """P7: apply は既存 rewrite API 関数を呼ぶだけ。"""
        import core.admin_assistant.actions as actions_mod

        calls = []

        def fake_call(chunk_id, prompt, studio_view, user):
            calls.append((chunk_id, prompt, studio_view, user["id"]))
            return {"display_text": "new-d", "spoken_text": "new-s", "formulas": []}

        monkeypatch.setattr(actions_mod, "_call_rewrite_api", fake_call)
        after = self._handler().apply(
            ActionContext(user_id="u1", role="TEACHER", target_id="ch1",
                          args={"prompt": "やさしく"}),
            {"display_text": "old-d", "spoken_text": "old-s", "formulas": []},
        )
        assert after["display_text"] == "new-d"
        assert calls == [("ch1", "やさしく", "edit", "u1")]

    def test_apply_maps_http_404_to_target_error_without_fastapi_import(self, monkeypatch):
        """既存 API の HTTPException は duck-typing で写像する（fastapi 非 import）。"""
        import core.admin_assistant.actions as actions_mod

        class _FakeHTTPError(Exception):
            status_code = 404
            detail = "Chunk not found"

        def fake_call(chunk_id, prompt, studio_view, user):
            raise _FakeHTTPError()

        monkeypatch.setattr(actions_mod, "_call_rewrite_api", fake_call)
        with pytest.raises(ActionTargetError):
            self._handler().apply(
                ActionContext(user_id="u1", role="TEACHER", target_id="ch1",
                              args={"prompt": "p"}),
                {},
            )

    def test_revert_restores_before_snapshot(self, monkeypatch):
        """P3: revert は before スナップショットをそのまま復元する（L2）。"""
        import core.admin_assistant.actions as actions_mod

        written = []

        def fake_write(chunk_id, state):
            written.append((chunk_id, dict(state)))
            return True

        monkeypatch.setattr(actions_mod, "_write_chunk_script", fake_write)
        before = {"display_text": "old-d", "spoken_text": "old-s",
                  "formulas": [{"latex": "E=mc^2"}]}
        self._handler().revert(
            ActionContext(user_id="u1", role="TEACHER", target_id="ch1"), before
        )
        assert written == [("ch1", before)]

    def test_revert_missing_chunk(self, monkeypatch):
        import core.admin_assistant.actions as actions_mod

        monkeypatch.setattr(actions_mod, "_write_chunk_script", lambda cid, st: False)
        with pytest.raises(ActionTargetError):
            self._handler().revert(
                ActionContext(user_id="u1", role="TEACHER", target_id="ch1"), {}
            )


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

    def test_registry_anchor_ids_registered_for_declared_screen(self):
        """アンカー整合ガードレール（強化版, §5-5 仕組み化）。

        capability の locate_steps の各 anchor_id（base id）が、**そのステップの screen の**
        `registerUiAnchors` 登録に実在することを検査する。旧版の「admin.js のどこかに
        文字列が存在する」部分文字列一致では screen の対応を検証できなかった。
        """
        anchors = _parse_registered_anchors(_ADMIN_JS_MAIN)
        problems = []
        for cap in caps.all_capabilities():
            for st in cap.locate_steps:
                base = st.anchor_id.split(":")[0]
                if base not in anchors.get(st.screen, set()):
                    problems.append(
                        f"{cap.id}: anchor {base!r} が screen {st.screen!r} の "
                        "registerUiAnchors に未登録"
                    )
        assert problems == [], "\n".join(problems)

    def test_anchor_parser_sees_known_registrations(self):
        """パーサ自体の健全性: 既知の登録が拾えていること（誤って空 dict になり
        ガードレールが全 pass する事故を防ぐ）。"""
        anchors = _parse_registered_anchors(_ADMIN_JS_MAIN)
        assert "upload_dropzone" in anchors.get("materials", set())
        assert "ls-rewrite-prompt" in anchors.get("lecture-studio", set())  # quoted key
        assert "stumbles_course_select" in anchors.get("stumbles", set())
        assert "sp_proposals_list" in anchors.get("schema-proposals", set())

    def test_frontend_uses_prefix_and_spotlight_class(self):
        assert "admin-assistant-spotlight" in _ADMIN_JS
        assert "window.AdminAssistant" in _ADMIN_JS

    def test_unenforced_scope_capabilities_have_no_action_handler(self):
        """routes/admin_assistant.py が enforce する scope は `own_course` だけ。

        それ以外の scope（`own_material` / `editable_course` 等）を宣言した action
        capability に代行ハンドラを足すと、宣言だけあって強制の無い fail-open になる。
        ハンドラを足すときは route 側の scope 判定も同時に足すこと（P1）。
        """
        enforced = {"own_course", "any"}
        offenders = [
            cap.id for cap in caps.all_capabilities()
            if cap.kind == KIND_ACTION
            and cap.scope not in enforced
            and get_handler(cap.id) is not None
        ]
        assert offenders == [], (
            "scope が route で強制されていないのに代行ハンドラを持つ capability: "
            f"{offenders}"
        )


# ===========================================================================
# Group A-5b: capability.api の実ルート突合（2026-09-03 追加）
# ===========================================================================


def _normalize_route_path(path: str) -> tuple:
    """パスをセグメント列にし、`{param}` は `None`（任意リテラルに一致）にする。"""
    segs = []
    for seg in (path or "").strip("/").split("/"):
        segs.append(None if seg.startswith("{") and seg.endswith("}") else seg)
    return tuple(segs)


def _route_matches(cap_segs: tuple, route_segs: tuple) -> bool:
    """capability 側のパスが route のパスに一致するか。

    route 側の `{param}` はどのセグメントにも一致する（capability が
    `/shared/document/{object_id}/...` のように object_type を具体値で固定した
    宣言をしていても実在扱いにする）。capability 側の `{param}` は route 側も
    `{param}` であること（実リテラルをプレースホルダで隠さない）。
    """
    if len(cap_segs) != len(route_segs):
        return False
    for cap_seg, route_seg in zip(cap_segs, route_segs):
        if route_seg is None:
            continue           # route 側がパラメータ: 何でもよい
        if cap_seg != route_seg:
            return False
    return True


class TestCapabilityApiPathsExist:
    """`Capability.api` が実在するルートを指していること。

    2026-09-03: `atlas.generate_skeleton` / `atlas.freeze_skeleton` が
    `/api/admin/{cartridge_id}/atlas/skeleton/...`（実際は `cartridges/` が必要）を
    指したまま長く放置されていた。api は説明・KB の材料として利用者に見えるので、
    実ルートと機械照合して黙った分裂を防ぐ。
    """

    def _app_routes(self):
        pytest.importorskip("fastapi")
        from api.main import app

        routes = set()
        for route in app.routes:
            path = getattr(route, "path", "")
            if not path:
                continue
            for method in getattr(route, "methods", None) or []:
                routes.add((method.upper(), _normalize_route_path(path)))
        return routes

    def test_all_declared_api_paths_resolve(self):
        routes = self._app_routes()
        assert routes, "app.routes が空（パーサの健全性チェック）"
        problems = []
        for cap in caps.all_capabilities():
            if not cap.api:
                continue
            method = str(cap.api.get("method", "")).upper()
            path = str(cap.api.get("path", ""))
            assert method and path, f"{cap.id}: api に method/path が無い"
            cap_segs = _normalize_route_path(path)
            if not any(
                m == method and _route_matches(cap_segs, r)
                for (m, r) in routes
            ):
                problems.append(f"{cap.id}: {method} {path} に一致するルートが無い")
        assert problems == [], "\n".join(problems)

    def test_matcher_rejects_missing_prefix(self):
        """マッチャの健全性: 2026-09-03 に是正した誤りを再現して検出できること。"""
        routes = self._app_routes()
        bad = _normalize_route_path("/api/admin/{cartridge_id}/atlas/skeleton/freeze")
        assert not any(
            m == "POST" and _route_matches(bad, r) for (m, r) in routes
        )
        good = _normalize_route_path("/api/admin/cartridges/{cartridge_id}/atlas/skeleton/freeze")
        assert any(m == "POST" and _route_matches(good, r) for (m, r) in routes)


class TestReachabilityFixes20260903:
    """2026-09-03 の docs 照合で見つかった「到達できない道案内」の是正を固定する。"""

    def test_course_delete_points_at_course_builder(self):
        """コース削除の UI はコース管理タブに無く、コース構築タブのモーダル内にある。"""
        cap = caps.get_capability("course.delete")
        assert cap.screen == "course-builder"
        anchors = [st.anchor_id for st in cap.locate_steps]
        assert anchors == ["import_course_button", "course_delete_button"]
        assert all(st.screen == "course-builder" for st in cap.locate_steps)
        # 実 API は user_can_edit_course（所有者 or editor）。own_course は狭すぎた。
        assert cap.scope == "editable_course"

    def test_materials_menu_capabilities_open_the_row_menu_first(self):
        """「⋯」メニュー内の要素を指す道案内は、先にメニューのトリガーを点灯する。

        メニューを開くまでメニュー項目は DOM に存在しないため、この step が無いと
        道案内が「どこを押せばよいか分からない」状態で止まる。
        """
        menu_items = {
            "material_share_button",
            "material_delete_button",
            "material_estimate_button",
            "shared_version_button",
            "material_figures_button",
            "material_inventory_button",
            # material_graph_review_button は 2026-09-06 に行のアイコンボタンへ昇格
            #（⋯ メニュー外）したため対象外。
            "material_landscape_button",
            # 2026-09-05: ゼミ前ブリーフも「⋯」メニュー内なので同じ規律に載せる。
            "seminar_brief_button",
        }
        problems = []
        for cap in caps.all_capabilities():
            anchors = [st.anchor_id for st in cap.locate_steps if st.screen == "materials"]
            for i, anchor in enumerate(anchors):
                if anchor in menu_items and "material_row_menu" not in anchors[:i]:
                    problems.append(f"{cap.id}: {anchor} の前に material_row_menu が無い")
        assert problems == [], "\n".join(problems)

    def test_row_icon_capabilities_do_not_require_the_menu(self):
        """行に直接出ているアイコン（グラフレビュー / 論文レーダー）は「⋯」を開かせない。

        2026-09-06 に ⋯ メニューから行のアイコンボタンへ昇格した2入口。道案内が不要な
        「メニューを開きます」ステップを挟むと、開いたメニューに項目が無く迷わせる。
        """
        row_icons = {"material_graph_review_button", "paper_radar_row_button"}
        problems = []
        for cap in caps.all_capabilities():
            anchors = [st.anchor_id for st in cap.locate_steps if st.screen == "materials"]
            if not row_icons & set(anchors):
                continue
            if "material_row_menu" in anchors:
                problems.append(f"{cap.id}: 行アイコンの入口に material_row_menu が残っている")
            for st in cap.locate_steps:
                if st.anchor_id in row_icons and st.precondition == "material_menu_open":
                    problems.append(f"{cap.id}: {st.anchor_id} が material_menu_open を前提にしている")
        assert problems == [], "\n".join(problems)

    def test_stumbles_declares_editable_course_scope(self):
        cap = caps.get_capability("stumbles.view")
        assert cap.scope == "editable_course"

    def test_newly_documented_sections_are_wired(self):
        """2026-09-03 に新設した操作KB の節が capability から参照されていること。"""
        expected = {
            "course.discuss_opening_review": "admin_operations/materials.md#discuss-opening-review",
            "materials.url_upload": "admin_operations/materials.md#url-upload",
            "materials.row_actions_menu": "admin_operations/materials.md#row-menu",
            "materials.graph_review": "admin_operations/materials.md#graph-review",
            "materials.review_landscape": "admin_operations/materials.md#landscape",
            "atlas.review_reports": "admin_operations/atlas.md#review-queue",
            "atlas.vector_index_aliases": "admin_operations/atlas.md#vector-aliases",
            "llm_models.manage_url_fetch_domains": "admin_operations/llm_models.md#url-fetch-domains",
            "course.release_review": "admin_operations/course.md#release-review",
            "doubt.record_falsification_conditions": "admin_operations/doubt.md#falsification-conditions",
        }
        for cap_id, howto in expected.items():
            cap = caps.get_capability(cap_id)
            assert cap is not None, f"{cap_id} が未登録"
            assert cap.howto_doc == howto, f"{cap_id}: howto_doc が {cap.howto_doc!r}"
            assert cap.kind == "guidance_only", f"{cap_id} は guidance_only であるべき"
            section = kb.section_for_howto(cap.howto_doc)
            assert section and section.get("body"), f"{cap_id}: KB 節が引けない"

    def test_url_fetch_domain_management_is_system_admin_only(self):
        """許可ドメインの追加・削除は SYSTEM_ADMIN のみ（fail-closed）。"""
        cap = caps.get_capability("llm_models.manage_url_fetch_domains")
        assert cap.required_role == "SYSTEM_ADMIN"
        assert caps.can_access(cap.id, "TEACHER") is False

    def test_atlas_freeze_409_handles_pending_edges(self):
        """RE層の `detail.pending_edges` でも理由が出る（gap と同列の弁）。"""
        assert "detail.pending_edges" in _ADMIN_JS_MAIN
        assert "EDGE_FREEZE_PENDING_TEXT" in _ADMIN_JS_MAIN

    def test_load_stumbles_checks_response_ok(self):
        """404（所有者・編集権限なし）を事実文にする。res.ok 未判定に戻さない。"""
        start = _ADMIN_JS_MAIN.index("function loadStumbles()")
        body = _ADMIN_JS_MAIN[start:start + 2600]
        assert "unanswered-queries" in body
        assert "res.ok" in body
        assert "所有者または編集権限" in body


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


# --- capabilities（実行可否の事前開示, N12） ------------------------------


@pytestmark_api
class TestCapabilitiesAPI:
    def test_student_forbidden(self, api):
        client, _store, _mp, _svc = api
        r = client.get("/api/admin/assistant/capabilities", headers=_headers("STUDENT"))
        assert r.status_code == 403

    def test_executable_flag_marks_only_implemented_actions(self, api):
        client, _store, _mp, _svc = api
        r = client.get("/api/admin/assistant/capabilities", headers=_headers("TEACHER"))
        assert r.status_code == 200
        rows = {row["id"]: row for row in r.json()}
        # 実装済み action は executable=True
        assert rows["course.publish"]["executable"] is True
        assert rows["course.set_visibility"]["executable"] is True
        assert rows["lecture_studio.rewrite_chunk_script"]["executable"] is True
        # handler 未実装の action は False（道案内のみ対応と提示される）
        assert rows["materials.delete"]["executable"] is False
        assert rows["course.delete"]["executable"] is False
        # guidance_only は常に False
        assert rows["materials.upload"]["executable"] is False

    def test_role_filtered(self, api):
        """P1: TEACHER の一覧に SYSTEM_ADMIN 専用 capability が混ざらない。"""
        client, _store, _mp, _svc = api
        r = client.get("/api/admin/assistant/capabilities", headers=_headers("TEACHER"))
        ids = {row["id"] for row in r.json()}
        assert "users.create_teacher" not in ids
        assert "llm_usage.view_metrics" not in ids


# --- rewrite 代行（N12） ---------------------------------------------------


@pytestmark_api
class TestRewriteActionAPI:
    def _patch_rewrite(self, monkeypatch, store):
        import core.admin_assistant.actions as actions_mod

        chunk = {"display_text": "old-d", "spoken_text": "old-s", "formulas": []}
        calls = {"rewrite": [], "restore": []}

        def fake_fetch(cid):
            return dict(chunk) if cid == "ch1" else None

        def fake_call(cid, prompt, studio_view, user):
            calls["rewrite"].append((cid, prompt, studio_view))
            chunk.update({"display_text": "new-d", "spoken_text": "new-s"})
            return dict(chunk)

        def fake_write(cid, state):
            if cid != "ch1":
                return False
            calls["restore"].append((cid, dict(state)))
            chunk.update(state)
            return True

        monkeypatch.setattr(actions_mod, "_fetch_chunk_script", fake_fetch)
        monkeypatch.setattr(actions_mod, "_call_rewrite_api", fake_call)
        monkeypatch.setattr(actions_mod, "_write_chunk_script", fake_write)
        return chunk, calls

    def test_chat_returns_supported_action_plan_with_prompt(self, api):
        client, _store, _mp, _svc = api
        r = client.post("/api/admin/assistant/chat",
                        json={"message": "この原稿をもっとやさしく書き換えて",
                              "screen_context": {"tab": "lecture-studio",
                                                 "selection": {"chunk_id": "ch1"}}},
                        headers=_headers("TEACHER"))
        data = r.json()
        assert data["intent"] == "action"
        plan = data["action_plan"]
        assert plan["capability_id"] == "lecture_studio.rewrite_chunk_script"
        assert plan["supported"] is True
        assert plan["confirm_required"] is False
        assert plan["reversible"] is True
        assert plan["args"]["prompt"] == "この原稿をもっとやさしく書き換えて"
        assert plan["target"] == {"type": "chunk", "id": "ch1"}

    def test_apply_then_revert_restores_script(self, api, monkeypatch):
        """L2 永続可逆: apply（既存 API 呼び出し）→ revert で before に戻る（P3）。"""
        client, store, _mp, _svc = api
        chunk, calls = self._patch_rewrite(monkeypatch, store)

        r = client.post("/api/admin/assistant/actions",
                        json={"capability_id": "lecture_studio.rewrite_chunk_script",
                              "target": {"type": "chunk", "id": "ch1"},
                              "args": {"prompt": "やさしく"}},
                        headers=_headers("TEACHER"))
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "applied"
        assert data["reversible"] is True
        assert data["after"]["display_text"] == "new-d"
        assert calls["rewrite"] == [("ch1", "やさしく", "edit")]
        assert ("applied" in [e[1] for e in store.events])  # P5 監査

        aid = data["action_id"]
        r2 = client.post("/api/admin/assistant/actions/%s/revert" % aid,
                         headers=_headers("TEACHER"))
        assert r2.status_code == 200
        assert r2.json()["status"] == "reverted"
        assert chunk["display_text"] == "old-d"   # before に復元
        assert chunk["spoken_text"] == "old-s"
        assert calls["restore"] and calls["restore"][0][1]["display_text"] == "old-d"
        assert ("reverted" in [e[1] for e in store.events])  # P5 監査

    def test_apply_without_prompt_is_400(self, api, monkeypatch):
        client, store, _mp, _svc = api
        self._patch_rewrite(monkeypatch, store)
        r = client.post("/api/admin/assistant/actions",
                        json={"capability_id": "lecture_studio.rewrite_chunk_script",
                              "target": {"type": "chunk", "id": "ch1"},
                              "args": {}},
                        headers=_headers("TEACHER"))
        assert r.status_code == 400

    def test_apply_unknown_chunk_is_404(self, api, monkeypatch):
        client, store, _mp, _svc = api
        self._patch_rewrite(monkeypatch, store)
        r = client.post("/api/admin/assistant/actions",
                        json={"capability_id": "lecture_studio.rewrite_chunk_script",
                              "target": {"type": "chunk", "id": "nope"},
                              "args": {"prompt": "p"}},
                        headers=_headers("TEACHER"))
        assert r.status_code == 404


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


# ===========================================================================
# Group A-8: 実行可否の事前開示（N12）— フロント静的配線の検証
#
# 挨拶文の「代行できます」例示は、サーバの GET /admin/assistant/capabilities が
# executable=true と申告した操作に限定し、未実装の action は「道案内のみ対応」と
# 明示する（vision_ux_gap_survey_2026-07-17.md §2-P5 / §5-5）。
# ===========================================================================


class TestExecutableDisclosureFrontend:
    def test_greet_fetches_capabilities(self):
        body = _extract_js_function(_ADMIN_JS, "greet")
        assert '"/admin/assistant/capabilities"' in body or "'/admin/assistant/capabilities'" in body

    def test_greeting_distinguishes_guide_only(self):
        body = _extract_js_function(_ADMIN_JS, "buildGreeting")
        assert "executable" in body
        assert "道案内のみ対応" in body

    def test_greet_fails_closed_to_static_text(self):
        """capability 一覧が取れないときは実装済み例のみの静的文面へ縮退（捏造しない, P4）。"""
        greet_body = _extract_js_function(_ADMIN_JS, "greet")
        assert ".catch(" in greet_body
        build_body = _extract_js_function(_ADMIN_JS, "buildGreeting")
        # 静的フォールバックの代行例示は実装済みの course.publish の言い回しのみ
        assert "このコースを公開して" in build_body

    def test_new_frontend_functions_are_es5(self):
        for fn in ("greet", "buildGreeting"):
            added = _extract_js_function(_ADMIN_JS, fn)
            assert "=>" not in added, fn
            assert re.search(r"\bconst\s", added) is None, fn
            assert re.search(r"\blet\s", added) is None, fn
            assert "`" not in added, fn

    def test_route_exposes_executable_flag(self):
        assert "def assistant_list_capabilities" in _ROUTE_SRC
        assert "executable" in _ROUTE_SRC
        assert "_capability_is_executable" in _ROUTE_SRC

    def test_guidance_fallback_marks_guide_only(self):
        """capability 提示（できる操作の例）でも未実装 action を明示する。"""
        assert "道案内のみ対応" in _ROUTE_SRC
        body = extract_function_source(_ROUTE_SRC, "_guidance_response")
        assert "_capability_display_title" in body


class TestRegistryGaps20260905:
    """2026-09-05 是正: 実装済みなのに registry 未登録で「構造的に案内不能」だった4件。

    P1 の registry は単一の真実源なので、載っていない機能は Copilot が説明も道案内も
    できない。いずれも guidance_only（読み取り中心 / 確定は人間の明示操作）。
    """

    _EXPECTED = {
        "materials.paper_layer": (
            "materials", "admin_operations/materials.md#paper-layer",
            "/api/admin/documents/{document_id}/paper-layer",
        ),
        "materials.seminar_brief": (
            "materials", "admin_operations/materials.md#seminar-brief",
            "/api/admin/documents/{document_ref}/seminar-brief",
        ),
        "indicators.view_catalog": (
            "interest-dashboard", "admin_operations/interest_dashboard.md#indicator-catalog",
            "/api/indicators",
        ),
        "lecture_studio.figure_studio": (
            "lecture-studio", "admin_operations/lecture_studio.md#figure-studio",
            "/api/admin/courses/{course_id}/figure-studio/turn",
        ),
    }

    def test_registered_as_guidance_only(self):
        for cap_id, (screen, howto, path) in self._EXPECTED.items():
            cap = caps.get_capability(cap_id)
            assert cap is not None, f"{cap_id} が未登録"
            assert cap.screen == screen, f"{cap_id}: screen が {cap.screen!r}"
            assert cap.kind == "guidance_only", f"{cap_id} は guidance_only であるべき"
            assert cap.required_role == "TEACHER"
            assert cap.howto_doc == howto, f"{cap_id}: howto_doc が {cap.howto_doc!r}"
            assert cap.api and cap.api["path"] == path, f"{cap_id}: api path"
            assert cap.locate_steps, f"{cap_id} に locate_steps が無い"

    def test_kb_sections_exist(self):
        """P4: 説明の根拠になる KB 節が実在する（無ければ「未整備」としか言えない）。"""
        for cap_id, (_screen, howto, _path) in self._EXPECTED.items():
            section = kb.section_for_howto(howto)
            assert section and section.get("body"), f"{cap_id}: KB 節が引けない"

    def test_no_action_handler_registered(self):
        """guidance_only なので代行ハンドラを持たない（確定は人間の明示操作のまま）。"""
        for cap_id in self._EXPECTED:
            assert get_handler(cap_id) is None, f"{cap_id} に代行ハンドラがある"

    def test_locate_anchors_exist_for_declared_screen(self):
        anchors = _parse_registered_anchors(_ADMIN_JS_MAIN)
        for cap_id in self._EXPECTED:
            cap = caps.get_capability(cap_id)
            for st in cap.locate_steps:
                base = st.anchor_id.split(":")[0]
                assert base in anchors.get(st.screen, set()), (
                    f"{cap_id}: anchor {base!r} が screen {st.screen!r} に未登録"
                )

    def test_teacher_can_reach_all_four(self):
        teacher_ids = {c.id for c in caps.capabilities_for("TEACHER")}
        assert set(self._EXPECTED) <= teacher_ids
