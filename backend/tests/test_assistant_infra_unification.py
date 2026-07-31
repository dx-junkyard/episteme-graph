"""チャット型 AI 支援の共通基盤整理 Phase 2-E の検証（正本:
docs/features/assistant_common_infra_design.md §0/§1/§2-2/§3）。

対象:
  - Admin Copilot（``routes/admin_assistant.py``）の CostGate 一本化 + resolve_model 統一。
  - ``core/admin_assistant/intent.py::_normalize_history`` の window_history 委譲。
  - Field Atlas（``core/atlas_generator.py``）の resolve_model 統一（analysis フォールバック維持）。
  - W層対話（``routes/deliberation.py``）の会話履歴ウィンドウ化（head_keep=1 で
    grounding アンカーの先頭メッセージを保護すること）。

いずれも DB・実 LLM は使わず、``core.llm_worker`` 側の関数呼び出しを monkeypatch で
差し替えて検証する（既存の test_deliberation_dialogue.py / test_admin_assistant.py と同型）。
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import core.llm_worker.client as llm_worker_client  # noqa: E402
import core.llm_policy as llm_policy_mod  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from core.admin_assistant import intent as intent_mod  # noqa: E402
import core.atlas_generator as atlas_generator  # noqa: E402

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark_api = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入")


def _fake_client_settings(**overrides) -> SimpleNamespace:
    """resolve_model の実体（core.llm_policy.resolve_for_setting）が参照する settings のフェイク。"""
    base = dict(
        assistant_llm_model="",
        llm_fast_model="gpt-5.4-nano",
        atlas_assist_llm_model="",
        llm_analysis_model="o3-mini",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ===========================================================================
# 1. Admin Copilot: CostGate 一本化（§1）
# ===========================================================================


class TestAdminCopilotCostGate:
    """``_reserve_llm_quota`` が独自 dict でなく共通 CostGate に委譲していること。"""

    def test_daily_llm_calls_dict_is_removed(self):
        import routes.admin_assistant as route_mod

        assert not hasattr(route_mod, "_DAILY_LLM_CALLS")

    def test_uses_shared_cost_gate_instance(self):
        import routes.admin_assistant as route_mod

        assert isinstance(route_mod._cost_gate, CostGate)

    def test_blocks_after_cap_reached_same_user(self, monkeypatch):
        import routes.admin_assistant as route_mod

        monkeypatch.setattr(
            route_mod, "get_settings", lambda: SimpleNamespace(assistant_max_calls_per_day=2)
        )
        uid = f"user-{uuid.uuid4()}"
        assert route_mod._reserve_llm_quota(uid) is True
        assert route_mod._reserve_llm_quota(uid) is True
        assert route_mod._reserve_llm_quota(uid) is False

    def test_zero_cap_always_denies_llm(self, monkeypatch):
        """既定どおり cap<=0 は常に heuristic 縮退（LLM を一切使わない）。"""
        import routes.admin_assistant as route_mod

        monkeypatch.setattr(
            route_mod, "get_settings", lambda: SimpleNamespace(assistant_max_calls_per_day=0)
        )
        uid = f"user-{uuid.uuid4()}"
        assert route_mod._reserve_llm_quota(uid) is False
        assert route_mod._reserve_llm_quota(uid) is False

    def test_counters_are_isolated_per_user(self, monkeypatch):
        import routes.admin_assistant as route_mod

        monkeypatch.setattr(
            route_mod, "get_settings", lambda: SimpleNamespace(assistant_max_calls_per_day=1)
        )
        uid_a = f"user-{uuid.uuid4()}"
        uid_b = f"user-{uuid.uuid4()}"
        assert route_mod._reserve_llm_quota(uid_a) is True
        assert route_mod._reserve_llm_quota(uid_a) is False
        # 別ユーザーはまだ1回目 — カウンタが混線していない。
        assert route_mod._reserve_llm_quota(uid_b) is True


@pytestmark_api
class TestAdminCopilotCostGateChatIntegration:
    """quota 超過は 429 にせず allow_llm=False のヒューリスティック縮退にする（I2/P6）。"""

    def _headers(self, role: str = "TEACHER") -> dict:
        import jwt

        payload = {
            "sub": "11111111-1111-1111-1111-111111111111",
            "role": role, "username": "u1", "email": "u1@test.com",
        }
        return {"Authorization": "Bearer " + jwt.encode(payload, "test-secret-key", algorithm="HS256")}

    def test_quota_exhausted_returns_200_with_allow_llm_false(self, monkeypatch):
        from fastapi.testclient import TestClient
        from api.main import app
        import routes.admin_assistant as route_mod

        client = TestClient(app)
        monkeypatch.setattr(
            route_mod, "get_settings", lambda: SimpleNamespace(assistant_max_calls_per_day=0)
        )

        captured = {}
        original_classify = route_mod.intent_mod.classify

        def spy_classify(message, role, **kwargs):
            captured["allow_llm"] = kwargs.get("allow_llm")
            return original_classify(message, role, **kwargs)

        monkeypatch.setattr(route_mod.intent_mod, "classify", spy_classify)

        response = client.post(
            "/api/admin/assistant/chat",
            json={"message": "教材のアップロード方法を教えて",
                  "screen_context": {"tab": "materials"}},
            headers=self._headers(),
        )
        assert response.status_code == 200
        assert captured["allow_llm"] is False


# ===========================================================================
# 2. Admin Copilot: resolve_model 統一（§3）
# ===========================================================================


class TestAssistantModelResolution:
    def test_env_unset_falls_back_to_fast_tier(self, monkeypatch):
        import routes.admin_assistant as route_mod

        monkeypatch.setattr(
            llm_policy_mod, "get_settings",
            lambda: _fake_client_settings(assistant_llm_model="", llm_fast_model="gpt-5.4-nano"),
        )
        assert route_mod._assistant_model() == "gpt-5.4-nano"

    def test_explicit_env_is_used_over_fast_tier(self, monkeypatch):
        import routes.admin_assistant as route_mod

        monkeypatch.setattr(
            llm_policy_mod, "get_settings",
            lambda: _fake_client_settings(assistant_llm_model="custom-assistant-model"),
        )
        assert route_mod._assistant_model() == "custom-assistant-model"

    def test_both_empty_resolves_to_none(self, monkeypatch):
        """後方互換: 旧実装は空文字を None に変換していた。"""
        import routes.admin_assistant as route_mod

        monkeypatch.setattr(
            llm_policy_mod, "get_settings",
            lambda: _fake_client_settings(assistant_llm_model="", llm_fast_model=""),
        )
        assert route_mod._assistant_model() is None


# ===========================================================================
# 3. Field Atlas: resolve_model 統一（analysis フォールバック維持, I1）
# ===========================================================================


class TestAtlasAssistModelResolution:
    def test_env_unset_falls_back_to_analysis_tier(self, monkeypatch):
        monkeypatch.setattr(
            llm_policy_mod, "get_settings",
            lambda: _fake_client_settings(atlas_assist_llm_model="", llm_analysis_model="o3-mini"),
        )
        assert atlas_generator._assist_model(None) == "o3-mini"

    def test_explicit_env_is_used_over_analysis_tier(self, monkeypatch):
        monkeypatch.setattr(
            llm_policy_mod, "get_settings",
            lambda: _fake_client_settings(atlas_assist_llm_model="atlas-custom-model"),
        )
        assert atlas_generator._assist_model(None) == "atlas-custom-model"

    def test_caller_supplied_model_wins_over_settings(self, monkeypatch):
        monkeypatch.setattr(
            llm_policy_mod, "get_settings",
            lambda: _fake_client_settings(atlas_assist_llm_model="atlas-custom-model"),
        )
        assert atlas_generator._assist_model("teacher-picked-model") == "teacher-picked-model"


# ===========================================================================
# 4. intent.py::_normalize_history の window_history 委譲（§2-2）
# ===========================================================================


class TestNormalizeHistoryDelegatesToWindowHistory:
    def test_delegates_with_expected_parameters(self, monkeypatch):
        captured = {}
        real_window_history = intent_mod.window_history

        def spy(history, **kwargs):
            captured["history"] = history
            captured["kwargs"] = kwargs
            return real_window_history(history, **kwargs)

        monkeypatch.setattr(intent_mod, "window_history", spy)

        hist = [{"role": "user", "content": "こんにちは"}]
        out = intent_mod._normalize_history(hist, "現在の発話")

        assert captured["history"] is hist
        assert captured["kwargs"] == {
            "max_messages": intent_mod._HISTORY_MAX_TURNS,
            "max_chars": intent_mod._HISTORY_MAX_CHARS,
            "current_message": "現在の発話",
        }
        assert out == [{"role": "user", "content": "こんにちは"}]

    def test_constants_unchanged(self):
        """既存挙動を保存するための定数の回帰防止（I1）。"""
        assert intent_mod._HISTORY_MAX_TURNS == 8
        assert intent_mod._HISTORY_MAX_CHARS == 500


# ===========================================================================
# 5. W層対話: 履歴ウィンドウ化（§2-2、head_keep=1 で grounding アンカーを保護）
# ===========================================================================


def _alternating_messages(n_pairs: int) -> list[dict]:
    """user/assistant が交互に並ぶダミー履歴（先頭は必ず user）。"""
    out: list[dict] = []
    for i in range(n_pairs):
        out.append({"role": "user", "content": f"user-turn-{i}"})
        out.append({"role": "assistant", "content": f"assistant-turn-{i}"})
    return out


@pytestmark_api
class TestDeliberationHistoryWindowing:
    """routes/deliberation.py の POST .../messages が prior_messages を
    window_history(max_messages=16, max_chars=4000, head_keep=1) でウィンドウ化すること。
    """

    _OWNER_ID = "22222222-2222-2222-2222-222222222222"

    def _client_and_tokens(self):
        from fastapi.testclient import TestClient
        from api.main import app
        from dependencies import _create_token, ROLE_TEACHER

        client = TestClient(app)
        teacher = _create_token(self._OWNER_ID, "tea", "tea@x", ROLE_TEACHER)
        return client, teacher

    def _patch_owned_session(self, monkeypatch, route_mod, *, messages):
        from core.deliberation.schema import ElementRef, SCOPE_DOCUMENT

        monkeypatch.setattr(
            route_mod.delib_store, "get_session_by_id",
            lambda _id: {
                "id": _id, "created_by": self._OWNER_ID, "scope": "document",
                "element_type": "theory_claim", "element_id": "c1", "document_id": "doc-1",
                "domain_key": None, "title": "", "messages": messages,
                "created_at": "2026-07-20T00:00:00",
            },
        )
        monkeypatch.setattr(
            route_mod.refs, "resolve",
            lambda *a, **k: ElementRef(
                scope=SCOPE_DOCUMENT, element_type="theory_claim", element_id="c1", document_id="doc-1",
            ),
        )
        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.dialogue, "check_and_count_llm_call", lambda *a, **k: True)
        monkeypatch.setattr(
            route_mod.dialogue, "build_grounding",
            lambda ref: {"breakdown": {}, "positioning": {"available": False}},
        )
        monkeypatch.setattr(route_mod.dialogue, "grounding_to_text", lambda g: "GROUNDING")
        monkeypatch.setattr(route_mod.delib_store, "append_messages", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.delib_annotations, "create_candidates_from_dialogue", lambda *a, **k: [])
        monkeypatch.setattr(route_mod, "record_review_event", lambda *a, **k: None)

    def test_long_history_is_windowed_but_keeps_first_message(self, monkeypatch):
        """40件の履歴でも run_turn に渡る prior_messages は<=17件で、先頭(grounding
        アンカー)と直近の会話は保持される。"""
        import routes.deliberation as route_mod
        from core.deliberation.dialogue import DialogueTurnResult

        original_messages = _alternating_messages(20)  # 40 messages
        self._patch_owned_session(monkeypatch, route_mod, messages=original_messages)

        captured = {}

        def fake_run_turn(*args, **kwargs):
            captured["prior_messages"] = kwargs["prior_messages"]
            return DialogueTurnResult(reply="ok", annotations=[], degraded=False)

        monkeypatch.setattr(route_mod.dialogue, "run_turn", fake_run_turn)

        client, teacher = self._client_and_tokens()
        response = client.post(
            "/api/admin/deliberation/sessions/s1/messages",
            json={"content": "続きの質問です"},
            headers={"Authorization": "Bearer " + teacher},
        )
        assert response.status_code == 200

        windowed = captured["prior_messages"]
        # head_keep=1 + max_messages=16 = 17 件が上限。
        assert len(windowed) <= 17
        assert len(windowed) < len(original_messages)
        # 先頭(grounding が注入される場所)は元の最初のメッセージのまま保護される。
        assert windowed[0] == original_messages[0]
        # 直近の会話も保持される。
        assert windowed[-1] == original_messages[-1]

    def test_short_history_is_not_truncated(self, monkeypatch):
        import routes.deliberation as route_mod
        from core.deliberation.dialogue import DialogueTurnResult

        original_messages = _alternating_messages(3)  # 6 messages, well under the 17 cap
        self._patch_owned_session(monkeypatch, route_mod, messages=original_messages)

        captured = {}

        def fake_run_turn(*args, **kwargs):
            captured["prior_messages"] = kwargs["prior_messages"]
            return DialogueTurnResult(reply="ok", annotations=[], degraded=False)

        monkeypatch.setattr(route_mod.dialogue, "run_turn", fake_run_turn)

        client, teacher = self._client_and_tokens()
        response = client.post(
            "/api/admin/deliberation/sessions/s1/messages",
            json={"content": "続きの質問です"},
            headers={"Authorization": "Bearer " + teacher},
        )
        assert response.status_code == 200
        assert captured["prior_messages"] == original_messages


class TestGroundingSurvivesWindowing:
    """window_history(head_keep=1) 後も build_llm_messages が grounding を先頭
    メッセージへ正しく注入できること（generate_conversation_turn をキャプチャして検証）。
    """

    def test_grounding_text_reaches_final_llm_messages_after_windowing(self, monkeypatch):
        from core.deliberation import dialogue
        from core.deliberation.schema import ElementRef, SCOPE_DOCUMENT
        from core.llm_worker.history import window_history

        raw_prior_messages = _alternating_messages(20)  # 40 messages, same shape as the route builds

        windowed = window_history(raw_prior_messages, max_messages=16, max_chars=4000, head_keep=1)
        assert len(windowed) <= 17
        assert windowed[0] == raw_prior_messages[0]

        captured = {}

        def fake_generate(messages, response_format, *, images=None, model=None):
            captured["messages"] = messages
            return SimpleNamespace(reply="回答です", annotations=[])

        monkeypatch.setattr(dialogue, "generate_conversation_turn", fake_generate)

        ref = ElementRef(scope=SCOPE_DOCUMENT, element_type="theory_claim", element_id="c1", document_id="doc-1")
        result = dialogue.run_turn(
            ref,
            prior_messages=windowed,
            user_content="続きの質問です",
            grounding_text="GROUNDING_MARKER",
            user_id="u1",
        )

        assert result.degraded is False
        messages = captured["messages"]
        # windowed(<=17) + 今回の発話の1件。
        assert len(messages) == len(windowed) + 1
        assert messages[0]["role"] == "user"
        assert "GROUNDING_MARKER" in messages[0]["content"]
        # grounding は最初の user メッセージにのみ注入される — 他のメッセージには出ない。
        assert all("GROUNDING_MARKER" not in m["content"] for m in messages[1:])
