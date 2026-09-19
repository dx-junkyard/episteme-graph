"""前提知識の3段解決（是正 F4）の挙動テスト。

正本: docs/architecture/six_lenses_2026-09-10/06_coldstart.md 提案6。

対象は ``backend/api/routes/learning.py`` の
``_prerequisite_terms`` / ``_resolve_prerequisite_context`` /
``_prerequisite_closed_world_note``。DB・LLM には触れず、モジュール境界の関数を
monkeypatch で差し替えて直接呼ぶ（test_learning_chat_infra.py と同型）。
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

USER = "11111111-1111-1111-1111-111111111111"
CURRENT_USER = {
    "id": USER,
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}


def _course_data():
    return {
        "sources": [{"material_id": "mat-course"}],
        "topics": [
            {
                "id": "t1",
                "title": "有効演算子",
                "prerequisites": [{"name": "グリーン関数"}, "有効演算子"],
            },
            {
                "id": "t-prereq",
                "title": "グリーン関数の基礎",
                "student_material": {"source_text": "グリーン関数の入門。"},
            },
        ],
    }


def _chunk(material_id: str, text: str, score: float = 0.8):
    return {
        "id": "chunk-1",
        "text": text,
        "source_title": "資料",
        "source_file": "paper.pdf",
        "tier": "source",
        "score": score,
        "material_id": material_id,
    }


def _route_course_data():
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
                "title": "テストトピック",
                "chapter_index": 0,
                "prerequisites": [{"name": "グリーン関数"}],
            }
        ],
    }


@pytest.fixture
def advice_env(monkeypatch):
    """LEARNING_ADVICE 分岐だけを完走させる境界モック（DB・実 LLM に触れない）。"""
    settings = SimpleNamespace(
        learning_chat_max_calls_per_day=300,
        learning_chat_llm_model="",
        llm_analysis_model="analysis-model-x",
        llm_fast_model="fast-model-x",
    )
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_policy_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())
    monkeypatch.setattr(
        learning_mod, "get_course_data", lambda user_id, course_id: _route_course_data()
    )
    monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: "前提知識の説明本文")
    monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_prerequisites", lambda *a, **k: None)
    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    monkeypatch.setattr(learning_mod, "record_interest_trace", MagicMock(return_value="trace-1"))
    return SimpleNamespace(settings=settings, persist_mock=persist_mock)


class TestPrerequisiteTerms:
    def test_mentioned_terms_come_first(self):
        topic_info = {"prerequisites": [{"name": "測度論"}, {"name": "グリーン関数"}]}
        terms = learning_mod._prerequisite_terms("グリーン関数について教えて", topic_info)
        assert terms == ["グリーン関数", "測度論"]

    def test_empty_topic_info_yields_no_terms(self):
        assert learning_mod._prerequisite_terms("なんでも", None) == []


class TestStageOneCourseTopic:
    def test_course_topic_material_resolves_as_course_material(self, monkeypatch):
        """① 同コース topic に一致すれば、その教材を文脈にして course_material になる。"""
        called = []
        monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata",
            lambda *a, **k: called.append(k) or [],
        )
        monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: [])

        out = learning_mod._resolve_prerequisite_context(
            USER, _course_data(), ["グリーン関数の基礎"],
        )
        assert out["content_grounding"] == "course_material"
        assert out["resolved"] == ["グリーン関数の基礎"]
        assert out["unresolved"] == []
        assert "グリーン関数の入門。" in out["context_block"]
        # ① で解決したら ② の検索は走らせない（余計な埋め込み呼び出しを増やさない）。
        assert called == []


class TestStageTwoVisibleChunks:
    def test_other_material_when_hit_outside_course_sources(self, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "list_visible_document_ids",
            lambda uid: ["11111111-1111-1111-1111-111111111111"],
        )
        monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata",
            lambda *a, **k: [_chunk("mat-other", "グリーン関数の定義はこうである。")],
        )

        out = learning_mod._resolve_prerequisite_context(USER, _course_data(), ["グリーン関数"])
        assert out["content_grounding"] == "other_material"
        assert out["resolved"] == ["グリーン関数"]
        assert out["cited_sources"][0]["origin"] == "other_material"
        assert out["cited_sources"][0]["index"] == 1
        assert "[出典1]" in out["context_block"]

    def test_course_material_when_hit_inside_course_sources(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: ["d1"])
        monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata",
            lambda *a, **k: [_chunk("mat-course", "グリーン関数を用いる。")],
        )
        out = learning_mod._resolve_prerequisite_context(USER, _course_data(), ["グリーン関数"])
        assert out["content_grounding"] == "course_material"

    def test_search_is_gated_by_visible_documents(self, monkeypatch):
        seen: dict = {}

        def _search(query, top_k=8, allowed_document_ids=None):
            seen["query"] = query
            seen["allowed"] = allowed_document_ids
            return []

        monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: ["d1", "d2"])
        monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", _search)

        learning_mod._resolve_prerequisite_context(USER, _course_data(), ["測度論"])
        assert seen["allowed"] == ["d1", "d2"]
        assert seen["query"] == "測度論"

    def test_vector_neighbour_without_verbatim_mention_stays_unresolved(self, monkeypatch):
        """近傍で引けただけの資料を「この前提を扱っている」とは言わない。"""
        monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: ["d1"])
        monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata",
            lambda *a, **k: [_chunk("mat-other", "全く別の話題である。")],
        )
        out = learning_mod._resolve_prerequisite_context(USER, _course_data(), ["測度論"])
        assert out["unresolved"] == ["測度論"]
        # 参考資料として引用はするので grounding は other_material のまま（出所は正直に）。
        assert out["content_grounding"] == "other_material"

    def test_low_score_hits_are_dropped(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: ["d1"])
        monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata",
            lambda *a, **k: [_chunk("mat-other", "測度論の定義。", score=0.1)],
        )
        out = learning_mod._resolve_prerequisite_context(USER, _course_data(), ["測度論"])
        assert out["cited_sources"] == []
        assert out["content_grounding"] == "model_generated"


class TestStageThreeModelGenerated:
    def test_no_source_yields_model_generated_and_closed_world_note(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: [])
        monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])

        out = learning_mod._resolve_prerequisite_context(USER, _course_data(), ["測度論"])
        assert out["content_grounding"] == "model_generated"
        assert out["context_block"] is None
        assert out["unresolved"] == ["測度論"]

        note = learning_mod._prerequisite_closed_world_note(out["unresolved"])
        assert learning_mod.PREREQUISITE_CLOSED_WORLD_FACT in note
        assert "「測度論」" in note

    def test_note_is_empty_when_everything_resolved(self):
        assert learning_mod._prerequisite_closed_world_note([]) == ""

    def test_no_terms_is_model_generated_without_search(self, monkeypatch):
        def _unexpected(*a, **k):
            raise AssertionError("前提名がゼロなら検索を呼ばない")

        monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", _unexpected)
        out = learning_mod._resolve_prerequisite_context(USER, _course_data(), [])
        assert out["content_grounding"] == "model_generated"
        assert out["unresolved"] == []


class TestAdviceSourceContextWiring:
    def test_source_context_is_injected_into_the_single_llm_call(self, monkeypatch):
        """抜粋は既存 advice の1コールへ同梱する（新しい LLM 経路を作らない）。"""
        captured: dict = {}

        def _generate_text(messages, **kwargs):
            captured["prompt"] = messages[0]["content"]
            return "説明本文"

        monkeypatch.setattr(learning_mod, "generate_text", _generate_text)
        monkeypatch.setattr(
            learning_mod, "get_llm_params",
            lambda tier: {"model": "m", "reasoning_effort": "low"},
        )

        answer = learning_mod._generate_learning_advice_response(
            "コース", "トピック", "前提知識を確認したい",
            topic_info={"prerequisites": [{"name": "グリーン関数"}]},
            course_data=_course_data(),
            source_context="## この前提知識に関連する資料の抜粋\n[出典1] 『資料』\n本文",
        )
        assert answer == "説明本文"
        assert "[出典1]" in captured["prompt"]
        assert "出所" in captured["prompt"]

    def test_route_response_carries_grounding_and_sources(self, advice_env, monkeypatch):
        """LEARNING_ADVICE の前提確認分岐が出所バッジの材料を返す（原則8）。"""
        monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: ["d1"])
        monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata",
            lambda *a, **k: [_chunk("mat-other", "グリーン関数の定義。")],
        )

        body = LearningChatRequest(
            message="前提知識を確認したい", history=[], support_action="check_prerequisites",
        )
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert resp.content_grounding == "other_material"
        assert resp.overall_tier
        assert resp.sources and resp.sources[0].chunk_id == "chunk-1"
        assert resp.support_mode == "prerequisite_review"
        # 履歴にも焼き込み、復元後もバッジ・出典チップが残る。
        meta = advice_env.persist_mock.call_args.kwargs["assistant_meta"]
        assert meta["content_grounding"] == "other_material"

    def test_route_response_is_model_generated_without_any_source(self, advice_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: [])
        monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])

        body = LearningChatRequest(
            message="前提知識を確認したい", history=[], support_action="check_prerequisites",
        )
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert resp.content_grounding == "model_generated"
        assert resp.sources == []
        assert learning_mod.PREREQUISITE_CLOSED_WORLD_FACT in resp.answer

    def test_route_general_advice_is_model_generated(self, advice_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "LEARNING_ADVICE")

        def _unexpected(*a, **k):
            raise AssertionError("一般アドバイスで前提知識の解決を走らせない")

        monkeypatch.setattr(learning_mod, "_resolve_prerequisite_context", _unexpected)

        body = LearningChatRequest(message="何から始めればいい？", history=[])
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)
        assert resp.content_grounding == "model_generated"

    def test_prompt_has_no_source_block_without_context(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(
            learning_mod, "generate_text",
            lambda messages, **k: captured.setdefault("prompt", messages[0]["content"]) or "本文",
        )
        monkeypatch.setattr(
            learning_mod, "get_llm_params",
            lambda tier: {"model": "m", "reasoning_effort": "low"},
        )
        learning_mod._generate_learning_advice_response(
            "コース", "トピック", "何から始めればいい？", course_data=_course_data(),
        )
        assert "抜粋" not in captured["prompt"]
