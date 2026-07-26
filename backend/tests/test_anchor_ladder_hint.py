"""アンカー優先ラダー（設計 docs/features/learning_ui_inspect_hover_design.md §7、
IH6/IH7）の Phase 3 バックエンド実装のテスト。

対象: ``backend/api/routes/learning.py`` の ``_build_anchor_ladder_hint`` /
``learning_chat`` 内の呼び出し配線。

- ``_build_anchor_ladder_hint(body, history)`` は非LLM・非DBの純関数（ヒントを返すだけ）。
  優先順位（設計 §7）: 1) ラッチ中アンカー（element_label/element_type）
  2) 表示中のスライド/セグメント（position_anchor.segment_id / selection_segment_id）
  3) 現在のトピック（ランク1のヒント文中でのみ言及。単独では起動しない）
  4) 直近回答の第1根拠チャンク（history の直近 assistant sources[0]）。
  アンカー文脈が一切なければ None（従来と完全同一のプロンプトを維持する）。
- ``learning_chat`` は typed action（``action="EXPLAIN_GRAPH_ELEMENT"``）・
  usage_help pre-route の早期 return より後段（RAG 本文生成の直前）でのみこの関数を
  呼ぶ。既存の要素タップ typed 経路・HELP ルートの挙動は変更していない（回帰確認）。
- 追加の LLM コールは作らない（既存の1コールへのプロンプト同梱のみ、IH6）。

test_learning_chat_infra.py と同型の手法: DB・実 LLM には触れず、``routes.learning``
モジュールの境界関数を monkeypatch で差し替えたうえで、対象関数を FastAPI を経由せず
直接呼び出す。
"""

from __future__ import annotations

import inspect
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
import core.llm_worker.client as llm_worker_client  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402
from tests.guardrail_helpers import extract_function_source  # noqa: E402


LEARNING_SOURCE_PATH = BACKEND / "api" / "routes" / "learning.py"


CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}


def _course_data(**topic_overrides) -> dict:
    topic = {
        "id": "topic-1",
        "title": "テストトピック",
        "chapter_index": 0,
        "prerequisites": [],
    }
    topic.update(topic_overrides)
    return {
        "id": "course-1",
        "title": "テストコース",
        "domain": "テスト分野",
        "chapters": [],
        "topics": [topic],
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


def _make_body(message: str = "テストの質問です", history: list | None = None, **overrides) -> LearningChatRequest:
    kwargs: dict = dict(message=message, history=history or [], support_action="ask_question")
    kwargs.update(overrides)
    return LearningChatRequest(**kwargs)


@pytest.fixture
def chat_env(monkeypatch):
    """learning_chat() をフル実行できるよう DB/LLM 境界だけモックする共有フィクスチャ
    （test_learning_chat_infra.py の chat_env と同型）。"""
    settings = _fake_settings()
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_worker_client, "get_settings", lambda: settings)
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

    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    trace_mock = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)
    misconception_mock = MagicMock(return_value=None)
    monkeypatch.setattr(learning_mod, "detect_and_record_misconception", misconception_mock)

    return SimpleNamespace(
        settings=settings,
        persist_mock=persist_mock,
        trace_mock=trace_mock,
        misconception_mock=misconception_mock,
    )


def _assistant_turn_with_sources(source_title: str, chunk_id: str = "chunk-99") -> dict:
    return {
        "role": "assistant",
        "content": "前回の回答本文",
        "sources": [
            {
                "index": 1,
                "chunk_id": chunk_id,
                "source_title": source_title,
                "tier": "source",
                "score": 0.81,
                "quote": "抜粋",
                "meta": "",
                "origin": "course_material",
            }
        ],
    }


# ===========================================================================
# 1. _build_anchor_ladder_hint の純関数としての振る舞い（ランク1〜4）
# ===========================================================================


class TestRank1LatchedElementAnchor:
    def test_element_label_included_in_hint(self):
        body = _make_body(
            message="これはどういう意味ですか",
            element_id="eq_2_7", element_type="formula", element_label="式(2.7)",
        )
        hint = learning_mod._build_anchor_ladder_hint(body, [])
        assert hint is not None
        assert "式(2.7)" in hint

    def test_falls_back_to_element_id_when_label_missing(self):
        body = _make_body(message="これは何ですか", element_id="concept_resonance", element_type="concept")
        hint = learning_mod._build_anchor_ladder_hint(body, [])
        assert hint is not None
        assert "concept_resonance" in hint

    def test_hint_is_a_hint_not_an_instruction_and_asks_for_one_line_attribution(self):
        """指示ではなくヒントとして書く（IH6）。回答冒頭/末尾での一行明示を求める（設計 §7 Step3）。"""
        body = _make_body(element_id="e1", element_type="concept", element_label="共鳴")
        hint = learning_mod._build_anchor_ladder_hint(body, [])
        assert "関係しない場合は" in hint  # 断定せず代替候補を許容する文面であること
        assert "一行で明示" in hint

    def test_rank1_takes_priority_over_rank2_and_rank4(self):
        body = _make_body(
            element_id="e1", element_type="concept", element_label="共鳴現象",
            position_anchor={"segment_id": 3, "scroll_offset": 10},
        )
        history = [_assistant_turn_with_sources("無関係な論文")]
        hint = learning_mod._build_anchor_ladder_hint(body, history)
        assert "共鳴現象" in hint
        assert "無関係な論文" not in hint


class TestRank2PositionAnchor:
    def test_position_anchor_segment_only_produces_simplified_hint(self):
        body = _make_body(position_anchor={"segment_id": 3, "scroll_offset": 120})
        hint = learning_mod._build_anchor_ladder_hint(body, [])
        assert hint is not None
        assert "教材の区画" in hint or "セクション" in hint

    def test_selection_segment_id_also_triggers_rank2_when_no_position_anchor(self):
        body = _make_body(selection_segment_id=2, selection_text="")
        hint = learning_mod._build_anchor_ladder_hint(body, [])
        assert hint is not None

    def test_position_anchor_segment_zero_still_counts_as_present(self):
        """segment_id=0（先頭）は「アンカー無し」ではない — キー存在で判定する。"""
        body = _make_body(position_anchor={"segment_id": 0, "scroll_offset": 0})
        hint = learning_mod._build_anchor_ladder_hint(body, [])
        assert hint is not None

    def test_rank2_takes_priority_over_rank4(self):
        body = _make_body(position_anchor={"segment_id": 1, "scroll_offset": 0})
        history = [_assistant_turn_with_sources("別の論文")]
        hint = learning_mod._build_anchor_ladder_hint(body, history)
        assert "別の論文" not in hint


class TestRank4RecentAnswerSource:
    def test_derived_from_last_assistant_message_sources(self):
        body = _make_body(message="続きを教えて")
        history = [
            {"role": "user", "content": "最初の質問"},
            _assistant_turn_with_sources("Ideal resonance の理論"),
        ]
        hint = learning_mod._build_anchor_ladder_hint(body, history)
        assert hint is not None
        assert "Ideal resonance の理論" in hint

    def test_only_looks_at_most_recent_assistant_turn(self):
        """直近の assistant メッセージに sources が無ければ、それより前へは遡らない。"""
        body = _make_body(message="続きを教えて")
        history = [
            _assistant_turn_with_sources("古い論文"),
            {"role": "user", "content": "次の質問"},
            {"role": "assistant", "content": "根拠なしの回答", "sources": []},
        ]
        hint = learning_mod._build_anchor_ladder_hint(body, history)
        assert hint is None

    def test_no_history_and_no_anchor_context_returns_none(self):
        body = _make_body(message="こんにちは")
        assert learning_mod._build_anchor_ladder_hint(body, []) is None
        assert learning_mod._build_anchor_ladder_hint(body, None) is None


class TestNoAnchorContextIsNone:
    def test_completely_bare_request_returns_none(self):
        """アンカー文脈が一切無ければ何も注入しない（従来と完全同一のプロンプト）。"""
        body = LearningChatRequest(message="質問です")
        assert learning_mod._build_anchor_ladder_hint(body, []) is None


# ===========================================================================
# 2. IH7: 数値（confidence・%・スコア）を見せない
# ===========================================================================


class TestNoNumbersInHintText:
    def test_rank2_hint_has_no_digits(self):
        body = _make_body(position_anchor={"segment_id": 7, "scroll_offset": 999})
        hint = learning_mod._build_anchor_ladder_hint(body, [])
        assert not any(ch.isdigit() for ch in hint), hint

    def test_rank1_hint_has_no_digits_when_label_has_none(self):
        body = _make_body(element_id="e1", element_type="concept", element_label="共鳴現象")
        hint = learning_mod._build_anchor_ladder_hint(body, [])
        assert not any(ch.isdigit() for ch in hint), hint

    def test_rank4_hint_has_no_digits_when_title_has_none(self):
        body = _make_body(message="続き")
        history = [_assistant_turn_with_sources("共鳴理論の論文")]
        hint = learning_mod._build_anchor_ladder_hint(body, history)
        assert not any(ch.isdigit() for ch in hint), hint

    def test_no_confidence_or_percent_wording(self):
        cases = [
            _make_body(element_id="e1", element_type="concept", element_label="共鳴"),
            _make_body(position_anchor={"segment_id": 1, "scroll_offset": 0}),
        ]
        for body in cases:
            hint = learning_mod._build_anchor_ladder_hint(body, [])
            assert hint is not None
            assert "confidence" not in hint.lower()
            assert "%" not in hint
            assert "スコア" not in hint


# ===========================================================================
# 3. 純関数であること（LLM/DB を呼ばない）
# ===========================================================================


class TestPurity:
    def test_source_contains_no_llm_or_db_calls(self):
        src = LEARNING_SOURCE_PATH.read_text(encoding="utf-8")
        body_src = extract_function_source(src, "_build_anchor_ladder_hint")
        forbidden = [
            "generate_text(", "_classify_intent(", "_pg_session(", "session.execute(",
            "search_chunks_with_metadata(", ".commit(", "usage_context(",
        ]
        offending = [term for term in forbidden if term in body_src]
        assert offending == [], f"purity 違反: {offending}"

    def test_calling_with_llm_and_db_patched_to_explode_still_works(self, monkeypatch):
        """呼び出し時に LLM/DB 境界関数が例外を投げる状態にしても、この関数自体は
        それらに一切触れないため無傷で動作する（実行時の裏取り）。"""
        def _boom(*a, **k):
            raise AssertionError("純関数のはずが LLM/DB 境界に触れている")

        monkeypatch.setattr(learning_mod, "generate_text", _boom)
        monkeypatch.setattr(learning_mod, "_pg_session", _boom)
        monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", _boom)

        body = _make_body(element_id="e1", element_type="concept", element_label="共鳴")
        hint = learning_mod._build_anchor_ladder_hint(body, [])
        assert hint is not None


# ===========================================================================
# 4. learning_chat 本体への配線（RAG 本文生成の直前にのみヒントを同梱する）
# ===========================================================================


class TestInjectionIntoMainRagFlow:
    def test_hint_injected_into_system_prompt_for_latched_free_text_send(self, chat_env, monkeypatch):
        """自由文メッセージに element_label が添付された場合、system プロンプトへ
        ヒントが同梱される（新しい「ラッチ後送信」シナリオ。action は typed ではない）。"""
        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return "回答本体"

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        body = _make_body(
            message="これってどういう意味ですか？",
            element_id="eq_2_7", element_type="formula", element_label="式(2.7)",
        )
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        system_message = captured["messages"][0]["content"]
        assert "式(2.7)" in system_message
        assert "[アンカーヒント]" in system_message

    def test_no_hint_when_no_anchor_context(self, chat_env, monkeypatch):
        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return "回答本体"

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        body = _make_body(message="こんにちは、質問があります")
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        system_message = captured["messages"][0]["content"]
        assert "[アンカーヒント]" not in system_message

    def test_llm_call_count_is_not_increased(self, chat_env, monkeypatch):
        """ラダーヒントは既存の1コールへの同梱のみで、追加のLLMコールを作らない（IH6）。"""
        call_count = {"n": 0}

        def _fake_generate_text(**kwargs):
            call_count["n"] += 1
            return "回答本体"

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        body = _make_body(
            message="これってどういう意味ですか？",
            element_id="eq_2_7", element_type="formula", element_label="式(2.7)",
        )
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert call_count["n"] == 1

    def test_structure_anchor_method_a_still_recorded_for_latched_free_text_send(self, chat_env, monkeypatch):
        """回帰: ラッチ後の自由文送信でも既存の構造帰属 方法A（learner_selected）が
        従来どおり記録される（設計 §6.2: バックエンド変更ゼロでこの経路に乗る）。"""
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: "回答本体")

        body = _make_body(
            message="これってどういう意味ですか？",
            element_id="eq_2_7", element_type="formula", element_label="式(2.7)",
        )
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert resp.structure_anchor is not None
        assert resp.structure_anchor["attribution_source"] == "learner_selected"

        _, kwargs = chat_env.trace_mock.call_args
        assert kwargs["extra_payload"]["structure_anchor"]["attribution_source"] == "learner_selected"


# ===========================================================================
# 5. 既存の typed 経路は不変（要素説明 typed 経路・usage_help pre-route）
# ===========================================================================


class TestTypedActionPathsUnaffected:
    def test_explain_graph_element_path_never_builds_ladder_hint(self, chat_env, monkeypatch):
        """要素タップ typed 経路（action=EXPLAIN_GRAPH_ELEMENT）は、ラダーヒント関数の
        呼び出しに到達する前に return する（既存経路の挙動は一切変えない）。"""
        from schemas import LearningChatResponse

        monkeypatch.setattr(
            learning_mod, "_generate_graph_element_explanation",
            lambda **kwargs: LearningChatResponse(answer="説明します"),
        )
        monkeypatch.setattr(learning_mod, "record_student_stumble_event", lambda *a, **k: None)

        spy = MagicMock(side_effect=AssertionError("EXPLAIN_GRAPH_ELEMENT 経路で呼ばれてはならない"))
        monkeypatch.setattr(learning_mod, "_build_anchor_ladder_hint", spy)

        body = _make_body(
            message="",
            action="EXPLAIN_GRAPH_ELEMENT",
            chunk_id="chunk-1", element_id="eq_2_7", element_type="formula", element_label="式(2.7)",
        )
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert "説明します" in resp.answer
        spy.assert_not_called()

    def test_usage_help_typed_action_never_builds_ladder_hint(self, chat_env, monkeypatch):
        """usage_help pre-route（support_action="usage_help"）もラダーヒント関数の
        呼び出しより前に return する。"""
        monkeypatch.setattr(learning_mod, "_search_manual", lambda *a, **k: [])
        monkeypatch.setattr(learning_mod, "_vector_search_manual", None)

        spy = MagicMock(side_effect=AssertionError("usage_help 経路で呼ばれてはならない"))
        monkeypatch.setattr(learning_mod, "_build_anchor_ladder_hint", spy)

        body = _make_body(message="これはどう使うんですか", support_action="usage_help")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert resp.answer
        spy.assert_not_called()


# ===========================================================================
# 6. 呼び出し位置の静的検査（既存 typed 経路・usage_help pre-route より後段であること）
# ===========================================================================


class TestCallSitePlacement:
    def test_ladder_hint_call_appears_after_explain_graph_element_branch_and_usage_help_preroute(self):
        src = LEARNING_SOURCE_PATH.read_text(encoding="utf-8")
        call_idx = src.index("_build_anchor_ladder_hint(body, body.history)")
        explain_branch_idx = src.index('if body.action == "EXPLAIN_GRAPH_ELEMENT":')
        usage_help_preroute_idx = src.index("_is_usage_help = (")
        assert explain_branch_idx < call_idx
        assert usage_help_preroute_idx < call_idx

    def test_ladder_hint_call_is_in_learning_chat_function(self):
        src = LEARNING_SOURCE_PATH.read_text(encoding="utf-8")
        body_src = extract_function_source(src, "learning_chat(")
        assert "_build_anchor_ladder_hint(body, body.history)" in body_src
