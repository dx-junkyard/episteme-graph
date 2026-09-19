"""入口統合 Phase 1 のルーティング（``learning.py`` 本体への配線）のテスト。

正本: ``docs/features/learning_chat_entry_unification_design.md``（§4.2 / §4.3 / §4.4 / §8）。

``test_anchor_ladder_hint.py`` / ``test_learning_chat_infra.py`` と同型の手法:
DB・実 LLM には触れず、``routes.learning`` の境界関数を monkeypatch で差し替えて
``learning_chat`` を直接呼び、**振る舞い**で固定する。

固定する事実:

- CHIT_CHAT 判定は拒否せず casual_light 様相で通常 RAG フローへ合流する（§4.3）
- 非LLM 一次判定が DOMAIN_RAG を決めたら ``_classify_intent`` を呼ばない（LC5）
- 明示（casual / discuss / typed action / 地図）は推定に勝つ（LC2）
- discuss 中は casual を推定しない（§4.5）
- 分類が例外・未知でも tutor へ倒れる（fail-safe）
- 様相は痕跡 payload に enum 2つで焼き込まれ、楽屋には焼き込まれない（§7 / SD4）
- casual プロンプトの spoken / text 二枚（§4.4）
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as learning_mod  # noqa: E402
import core.llm_policy as llm_policy_mod  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402


CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}


def _course_data() -> dict:
    return {
        "id": "course-1",
        "title": "テストコース",
        "domain": "テスト分野",
        "chapters": [],
        "topics": [{
            "id": "topic-1", "title": "テストトピック",
            "chapter_index": 0, "prerequisites": [],
        }],
        "concepts": [],
        "sources": [],
    }


def _fake_settings(**overrides) -> SimpleNamespace:
    base = dict(
        learning_chat_max_calls_per_day=300,
        learning_chat_llm_model="",
        llm_analysis_model="analysis-model-x",
        llm_fast_model="fast-model-x",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_body(message: str = "これはどういう意味ですか", **overrides) -> LearningChatRequest:
    kwargs: dict = dict(message=message, history=[])
    kwargs.update(overrides)
    return LearningChatRequest(**kwargs)


@pytest.fixture
def chat_env(monkeypatch):
    settings = _fake_settings()
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_policy_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())

    monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
    monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
    monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_prerequisites", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
    monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda *a, **k: [])
    monkeypatch.setattr(learning_mod, "list_course_source_document_ids", lambda *a, **k: [])
    monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda *a, **k: {})

    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    trace_mock = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)
    monkeypatch.setattr(learning_mod, "detect_and_record_misconception", MagicMock(return_value=None))

    return SimpleNamespace(settings=settings, persist_mock=persist_mock, trace_mock=trace_mock)


def _set_generation(monkeypatch, captured: dict | None = None, answer: str = "回答本体"):
    """本文生成の generate_text を差し替え、呼び出し回数と最後の kwargs を記録する。"""
    state = {"calls": 0, "kwargs": {}}

    def _fake(**kwargs):
        state["calls"] += 1
        state["kwargs"] = kwargs
        if captured is not None:
            captured.update(kwargs)
        return answer

    monkeypatch.setattr(learning_mod, "generate_text", _fake)
    return state


def _trace_payload(chat_env) -> dict:
    return chat_env.trace_mock.call_args.kwargs["extra_payload"]


# ===========================================================================
# 1. CHIT_CHAT → casual_light 合流（§4.3、オーナー判断 §12-1）
# ===========================================================================


class TestChitChatMergesIntoCasualLight:
    def test_chit_chat_is_not_refused_and_flows_through_rag(self, chat_env, monkeypatch):
        state = _set_generation(monkeypatch, answer="そういう日もあるよね。")
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "CHIT_CHAT")

        resp = learning_mod.learning_chat(
            "course-1", "topic-1", _make_body("今日は疲れたなあ"), current_user=CURRENT_USER,
        )

        # 拒否文ではなく、生成された本文が返る（= 通常フローを通っている）。
        assert resp.answer == "そういう日もあるよね。"
        assert "学習支援に特化したAI" not in resp.answer
        assert state["calls"] == 1
        # 根拠の一線は落ちていない（出所を正直に返す）。
        assert resp.content_grounding == "model_generated"

    def test_chit_chat_is_reported_as_inferred_casual_light(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "CHIT_CHAT")

        resp = learning_mod.learning_chat(
            "course-1", "topic-1", _make_body("おなかすいた"), current_user=CURRENT_USER,
        )

        assert resp.stance == {
            "stance": "casual_light",
            "source": "inferred",
            "label": "気軽な調子で",
        }

    def test_chit_chat_uses_the_text_variant_of_the_casual_prompt(self, chat_env, monkeypatch):
        """推定 casual_light（テキスト）は spoken=False で LaTeX と出典を許可する（§4.4）。"""
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "CHIT_CHAT")

        learning_mod.learning_chat(
            "course-1", "topic-1", _make_body("ひまだなあ"), current_user=CURRENT_USER,
        )

        system_prompt = captured["messages"][0]["content"]
        assert "気軽に話せる先生" in system_prompt
        assert "LaTeX" in system_prompt      # 許可の明示（禁止文ではない）
        assert "2〜4文" not in system_prompt  # 音声向けの長さ制約は載らない

    def test_chit_chat_trace_records_the_inferred_stance(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "CHIT_CHAT")

        learning_mod.learning_chat(
            "course-1", "topic-1", _make_body("雑談したい"), current_user=CURRENT_USER,
        )

        payload = _trace_payload(chat_env)
        assert payload["stance"] == "casual_light"
        assert payload["stance_source"] == "inferred"
        # 既存キーの意味は変わっていない（下流は無改変 = LC8）。
        assert payload["casual"] is True


# ===========================================================================
# 2. 非LLM 一次判定（LC5: LLM 回数を増やさない・むしろ減らす）
# ===========================================================================


class TestPrejudgeSkipsTheClassifier:
    def test_no_additional_llm_call_per_turn(self, chat_env, monkeypatch):
        state = _set_generation(monkeypatch)

        def _must_not_be_called(*a, **k):
            raise AssertionError("一次判定が DOMAIN_RAG を決めた往復で _classify_intent が呼ばれた")

        monkeypatch.setattr(learning_mod, "_classify_intent", _must_not_be_called)

        resp = learning_mod.learning_chat(
            "course-1", "topic-1",
            _make_body("この定理の証明はどこから来ているのですか"),
            current_user=CURRENT_USER,
        )

        # 生成の1コールだけ（現行の tutor 経路は分類 + 生成の2コールだった）。
        assert state["calls"] == 1
        assert resp.stance == {
            "stance": "tutor", "source": "inferred", "label": "ふつうの質問として",
        }

    def test_undecided_message_still_uses_the_classifier(self, chat_env, monkeypatch):
        """迷ったら既存の LLM 分類へ落とす（縮退はこの1本だけ）。"""
        state = _set_generation(monkeypatch)
        classify = MagicMock(return_value="DOMAIN_RAG")
        monkeypatch.setattr(learning_mod, "_classify_intent", classify)

        learning_mod.learning_chat(
            "course-1", "topic-1", _make_body("そうなんだ"), current_user=CURRENT_USER,
        )

        assert classify.call_count == 1
        assert state["calls"] == 1  # 分類は差し替え済みなので本文生成の1回だけ

    def test_greeting_shortcut_is_not_stolen_by_prejudge(self, chat_env, monkeypatch):
        """挨拶は _classify_intent 冒頭の決定論ショートカット（LEARNING_ADVICE）のまま。"""
        _set_generation(monkeypatch)
        classify = MagicMock(side_effect=learning_mod._classify_intent)
        monkeypatch.setattr(learning_mod, "_classify_intent", classify)
        monkeypatch.setattr(
            learning_mod, "_generate_learning_advice_response", lambda *a, **k: "案内です",
        )

        resp = learning_mod.learning_chat(
            "course-1", "topic-1", _make_body("こんにちは"), current_user=CURRENT_USER,
        )

        assert classify.call_count == 1
        assert resp.answer == "案内です"
        # 早期 return の経路では様相を返さない（RAG 応答でのみ設定）。
        assert resp.stance is None

    def test_typed_action_is_not_routed_through_the_estimator(self, chat_env, monkeypatch):
        """typed action は決定論ルート。推定器にも分類にも入らず、様相は tutor/explicit。"""
        state = _set_generation(monkeypatch)

        def _must_not_be_called(*a, **k):
            raise AssertionError("typed action で _classify_intent が呼ばれた")

        monkeypatch.setattr(learning_mod, "_classify_intent", _must_not_be_called)

        resp = learning_mod.learning_chat(
            "course-1", "topic-1",
            _make_body("これってどういう意味", support_action="ask_question"),
            current_user=CURRENT_USER,
        )

        assert state["calls"] == 1
        assert resp.stance == {
            "stance": "tutor", "source": "explicit", "label": "ふつうの質問として",
        }


# ===========================================================================
# 3. 明示は推定に勝つ（LC2）/ discuss 中は推定しない（§4.5）
# ===========================================================================


class TestExplicitWinsOverInference:
    def test_explicit_intent_mode_wins_over_inference(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        def _must_not_be_called(*a, **k):
            raise AssertionError("明示 casual で意図分類が走った")

        monkeypatch.setattr(learning_mod, "_classify_intent", _must_not_be_called)

        resp = learning_mod.learning_chat(
            "course-1", "topic-1",
            _make_body("今日はどんな話しようか", intent_mode="casual"),
            current_user=CURRENT_USER,
        )

        assert resp.stance == {
            "stance": "casual_light", "source": "explicit", "label": "気軽な調子で",
        }

    def test_explicit_casual_without_screen_mode_keeps_the_spoken_prompt(self, chat_env, monkeypatch):
        """後方互換: intent_mode="casual" 単独は従来どおり音声向けの本文（§4.4）。"""
        captured: dict = {}
        _set_generation(monkeypatch, captured)

        learning_mod.learning_chat(
            "course-1", "topic-1",
            _make_body("さっきの話のつづき", intent_mode="casual"),
            current_user=CURRENT_USER,
        )

        system_prompt = captured["messages"][0]["content"]
        assert "2〜4文" in system_prompt
        assert "音声で読み上げられます" in system_prompt

    def test_voice_screen_mode_keeps_the_spoken_prompt_even_when_inferred(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "CHIT_CHAT")

        learning_mod.learning_chat(
            "course-1", "topic-1",
            _make_body("いい天気だね", screen_mode="voice"),
            current_user=CURRENT_USER,
        )

        system_prompt = captured["messages"][0]["content"]
        assert "2〜4文" in system_prompt

    def test_casual_not_inferred_during_discuss(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        def _must_not_be_called(*a, **k):
            raise AssertionError("discuss 中に意図分類（= casual 推定の入口）が走った")

        monkeypatch.setattr(learning_mod, "_classify_intent", _must_not_be_called)

        resp = learning_mod.learning_chat(
            "course-1", "topic-1",
            _make_body("この論文の主張には無理があると思う", intent_mode="discuss"),
            current_user=CURRENT_USER,
        )

        assert resp.stance == {
            "stance": "discuss", "source": "explicit", "label": "議論として",
        }
        payload = _trace_payload(chat_env)
        assert payload["stance"] == "discuss"
        assert payload["stance_source"] == "explicit"
        # discuss の既存キーは不変（entry_mode / discuss_scope）。
        assert payload["entry_mode"] == "discuss"
        assert payload["discuss_scope"] == "course_sources"

    def test_cycle_modes_are_explicit(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        for mode, stance, label in (
            ("elicit", "cycle_elicit", "予想を先に聞く形で"),
            ("diff", "cycle_diff", "予想と照らし合わせる形で"),
        ):
            resp = learning_mod.learning_chat(
                "course-1", "topic-1",
                _make_body("こう予想します", intent_mode="discuss", cycle_mode=mode),
                current_user=CURRENT_USER,
            )
            assert resp.stance == {"stance": stance, "source": "explicit", "label": label}


# ===========================================================================
# 4. fail-safe / 楽屋 / 数値非含有
# ===========================================================================


class TestFailSafeAndPrivacy:
    def test_stance_falls_back_to_tutor_on_failure(self, chat_env, monkeypatch):
        """_classify_intent 内部の例外は DOMAIN_RAG へ倒れる（既存挙動）→ 様相は tutor。"""
        _set_generation(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("classifier down")

        # 本文生成は成功し、分類コールだけ失敗する状況を作る。
        real_generate = learning_mod.generate_text

        def _routed(**kwargs):
            if any("4つのルートに分類" in str(m.get("content", "")) for m in kwargs.get("messages", [])):
                return _boom(**kwargs)
            return real_generate(**kwargs)

        monkeypatch.setattr(learning_mod, "generate_text", _routed)

        resp = learning_mod.learning_chat(
            "course-1", "topic-1", _make_body("うーん"), current_user=CURRENT_USER,
        )

        assert resp.stance == {
            "stance": "tutor", "source": "inferred", "label": "ふつうの質問として",
        }

    def test_unknown_classifier_label_falls_back_to_tutor(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "SOMETHING_NEW")

        resp = learning_mod.learning_chat(
            "course-1", "topic-1", _make_body("んー"), current_user=CURRENT_USER,
        )

        assert resp.stance["stance"] == "tutor"

    def test_backstage_does_not_record_the_stance(self, chat_env, monkeypatch):
        """SD4: 「集計に入りません」と宣言した枠に観測用キーを足さない。"""
        _set_generation(monkeypatch)
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")

        learning_mod.learning_chat(
            "course-1", "topic-1",
            _make_body("この式が腑に落ちない", backstage=True),
            current_user=CURRENT_USER,
        )

        payload = _trace_payload(chat_env)
        assert "stance" not in payload
        assert "stance_source" not in payload
        assert payload["backstage"] is True

    def test_backstage_response_still_reports_the_stance_to_the_learner(self, chat_env, monkeypatch):
        """記録しないのは痕跡だけ。本人への事実提示（LC6）は楽屋でも同じ。"""
        _set_generation(monkeypatch)
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")

        resp = learning_mod.learning_chat(
            "course-1", "topic-1",
            _make_body("この式が腑に落ちない", backstage=True),
            current_user=CURRENT_USER,
        )

        assert resp.stance["stance"] == "tutor"

    def test_stance_response_has_no_numeric_confidence(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "CHIT_CHAT")

        resp = learning_mod.learning_chat(
            "course-1", "topic-1", _make_body("ねむい"), current_user=CURRENT_USER,
        )

        def _walk(node, path="stance"):
            if isinstance(node, dict):
                for key, value in node.items():
                    assert key not in ("confidence", "score", "weight", "probability"), path
                    _walk(value, f"{path}.{key}")
            elif isinstance(node, (list, tuple)):
                for idx, item in enumerate(node):
                    _walk(item, f"{path}[{idx}]")
            else:
                assert not isinstance(node, (int, float, bool)), f"{path} に数値が入っている"

        _walk(resp.stance)
        assert set(resp.stance) == {"stance", "source", "label"}


# ===========================================================================
# 5. 推定が切り替えないもの（LC1）
# ===========================================================================


class TestInferenceNeverChangesScopeOrMode:
    def test_inferred_casual_light_keeps_the_default_search_scope(self, chat_env, monkeypatch):
        """様相の推定で検索範囲（allowed_document_ids）が変わらない（DM1 / LC1）。"""
        seen: dict = {}

        def _search(message, *, top_k, allowed_document_ids):
            seen["allowed"] = allowed_document_ids
            return []

        monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", _search)
        monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda *a, **k: ["doc-a"])
        _set_generation(monkeypatch)
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "CHIT_CHAT")

        learning_mod.learning_chat(
            "course-1", "topic-1", _make_body("ひといき入れたい"), current_user=CURRENT_USER,
        )

        # 通常経路と同じ「本人の可視集合」のまま（discuss のスコープ2段には入らない）。
        assert seen["allowed"] == ["doc-a"]

    def test_inferred_casual_light_does_not_set_discuss_scope_in_the_trace(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "CHIT_CHAT")

        learning_mod.learning_chat(
            "course-1", "topic-1", _make_body("なんとなく話したい"), current_user=CURRENT_USER,
        )

        payload = _trace_payload(chat_env)
        assert "discuss_scope" not in payload
        assert "entry_mode" not in payload
        assert "backstage" not in payload
