"""IK-0382: 検索が1件も当たらない問いで「📘 教材から回答」と表示しない。

表示中のトピック教材は毎ターン `[現在表示中の教材]` として注入される。従来は注入した
だけで ``content_grounding="course_material"`` / tier 下限 ``source`` になり、教材と
無関係な問い（別トピックの概念・一般知識）で出典ゼロなのに「教材に基づく」と表示された
（ペルソナ通し受講 第 8 周: 楽屋の「ジーンズ質量とは」、Cep B のトピックでの宇宙膨張の問い）。
教材の注入（プロンプト）は変えず、出所分類と tier 下限だけを、教材が問いに関わるかの
決定論判定（``_topic_material_engages_message``）に従わせる。
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

TOPIC_MATERIAL = (
    "Cep B フィラメントでは、近くの大質量星からの外部フィードバックが磁場の形を変えている。"
    "偏光の観測から DCF 法で磁場強度を見積もり、質量対磁束比を求めた。"
)


def _course_data():
    return {
        "id": "course-1",
        "title": "テストコース",
        "domain": "テスト分野",
        "chapters": [],
        "concepts": [],
        "sources": [{"material_id": "mat-course"}],
        "topics": [
            {
                "id": "topic-1",
                "title": "問題設定と観測データ",
                "chapter_index": 0,
                "prerequisites": [],
                "student_material": {"source_text": TOPIC_MATERIAL},
            }
        ],
    }


def _chunk(material_id: str, score: float = 0.8):
    return {
        "id": "chunk-1",
        "text": "本文の抜粋。",
        "source_title": "資料",
        "source_file": "paper.pdf",
        "tier": "source",
        "score": score,
        "material_id": material_id,
    }


@pytest.fixture
def chat_env(monkeypatch):
    settings = SimpleNamespace(
        learning_chat_max_calls_per_day=300,
        learning_chat_llm_model="",
        llm_analysis_model="analysis-model-x",
        llm_fast_model="fast-model-x",
    )
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_policy_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())
    monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
    monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
    monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: ["d1"])
    monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_prerequisites", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
    monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "detect_and_record_misconception", MagicMock(return_value=None))
    monkeypatch.setattr(learning_mod, "record_interest_trace", MagicMock(return_value="trace-1"))
    captured: dict = {}

    def _generate_text(**kwargs):
        captured["messages"] = kwargs.get("messages")
        return "回答本文 [出典1]"

    monkeypatch.setattr(learning_mod, "generate_text", _generate_text)
    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    return SimpleNamespace(persist_mock=persist_mock, captured=captured)


def _ask(message: str, **extra):
    body = LearningChatRequest(
        message=message, history=[], support_action="ask_question", **extra
    )
    return learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)


class TestEngagementHelper:
    def test_terms_include_quoted_and_compound_runs(self):
        terms = learning_mod._grounding_content_terms("「外部からのフィードバック」ってどういう意味ですか。")
        assert terms[0] == "外部からのフィードバック"
        assert "フィードバック" in terms
        # 汎用語は内容語に数えない。
        assert "意味" not in terms

    def test_ascii_counts_only_acronyms(self):
        assert learning_mod._grounding_content_terms("What is the DCF method?") == ["DCF"]

    def test_deictic_question_engages_the_displayed_material(self):
        body = LearningChatRequest(message="ここはどういう意味？", history=[])
        assert learning_mod._topic_material_engages_message(body, TOPIC_MATERIAL) is True

    def test_selection_counts_as_engagement(self):
        body = LearningChatRequest(
            message="銀河の大きさも伸びますか", history=[], selection_text="磁場の形",
        )
        assert learning_mod._topic_material_engages_message(body, TOPIC_MATERIAL) is True

    def test_unrelated_question_does_not_engage(self):
        body = LearningChatRequest(message="空間が伸びるなら、銀河そのものの大きさも伸びているんですか。", history=[])
        assert learning_mod._topic_material_engages_message(body, TOPIC_MATERIAL) is False

    def test_related_question_engages(self):
        body = LearningChatRequest(message="「外部からのフィードバック」ってどういう意味ですか。", history=[])
        assert learning_mod._topic_material_engages_message(body, TOPIC_MATERIAL) is True

    def test_empty_material_never_engages(self):
        body = LearningChatRequest(message="ここは？", history=[])
        assert learning_mod._topic_material_engages_message(body, "") is False


class TestRouteGrounding:
    def test_zero_sources_and_unrelated_question_is_model_generated(self, chat_env):
        resp = _ask("空間が伸びるなら、銀河そのものの大きさも伸びているんですか。")
        assert resp.sources == []
        assert resp.content_grounding == "model_generated"
        # tier も下限を引き上げない（「AIの一般知識」と「原典」を並べない）。
        assert resp.overall_tier == "out_of_source"
        meta = chat_env.persist_mock.call_args.kwargs["assistant_meta"]
        assert meta["content_grounding"] == "model_generated"
        # 教材の注入（プロンプト）は従来どおり。
        joined = " ".join(str(m.get("content")) for m in chat_env.captured["messages"])
        assert "[現在表示中の教材]" in joined

    def test_backstage_unrelated_question_is_model_generated(self, chat_env):
        resp = _ask("すごく初歩的なんですが、「ジーンズ質量」ってどういう意味ですか。", backstage=True)
        assert resp.sources == []
        assert resp.content_grounding == "model_generated"

    def test_zero_sources_but_related_question_stays_course_material(self, chat_env):
        resp = _ask("「外部からのフィードバック」ってどういう意味ですか。")
        assert resp.content_grounding == "course_material"
        assert resp.overall_tier == "source"

    def test_course_chunk_hit_is_course_material_regardless(self, chat_env, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk("mat-course")]
        )
        resp = _ask("空間が伸びるなら、銀河そのものの大きさも伸びているんですか。")
        assert resp.content_grounding == "course_material"

    def test_other_material_hit_with_unrelated_question_is_other_material(self, chat_env, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk("mat-other")]
        )
        resp = _ask("空間が伸びるなら、銀河そのものの大きさも伸びているんですか。")
        assert resp.content_grounding == "other_material"


class TestUncitedAnswerIsModelGenerated:
    """TRIAGE14: 検索で出典を採用しても本文が1つも引用していなければ model_generated。"""

    def test_uncited_course_chunk_answer_is_model_generated(self, chat_env, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk("mat-course")]
        )
        monkeypatch.setattr(learning_mod, "generate_text", lambda **k: "一般的な説明です")
        resp = _ask("空間が伸びるなら、銀河そのものの大きさも伸びているんですか。")
        assert resp.content_grounding == "model_generated"
