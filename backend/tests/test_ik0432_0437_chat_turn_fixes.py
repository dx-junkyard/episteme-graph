"""学習チャットの往復の是正（IK-0432〜IK-0437・ペルソナ通し受講 第 9 周）。

- IK-0432 出典番号を (course, topic) の会話の中で固定する
- IK-0433 学習者向けの出典 DTO に類似度の生値（score）を載せない
- IK-0434 お礼・締めくくりだけの発話は検索・生成をせず固定の1文で応じる
- IK-0430 前提知識の説明プロンプト: 番号付き出典が無い抜粋に [出典N] を求めない・埋め込み記法を外す
- IK-0436 document 直付けの議論に論文の問い・主張の要旨ブロックを渡す
- IK-0437 候補ゼロの digest に事実文を添える

DB・実 LLM には触れない（境界 monkeypatch。test_chat_history_source_persistence.py と同型）。
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
from core import label_vocab  # noqa: E402
from core.discuss import opening as opening_mod  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from schemas import LearningChatRequest, SourceTierItem  # noqa: E402

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
        "topics": [{"id": "topic-1", "title": "テストトピック", "chapter_index": 0, "prerequisites": []}],
        "concepts": [],
        "sources": [{"material_id": "mat-1", "title": "このコースの論文"}],
    }


def _chunk(idx: int, *, score: float = 0.9) -> dict:
    return {
        "id": f"chunk-{idx}",
        "text": f"チャンク本文 {idx}",
        "score": score,
        "source_title": f"論文 {idx}",
        "source_file": f"paper{idx}.pdf",
        "material_id": "mat-1",
        "tier": "source",
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
    monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda *a, **k: ["doc-1"])
    monkeypatch.setattr(learning_mod, "list_course_source_document_ids", lambda *a, **k: ["doc-1"])
    monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_prerequisites", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
    monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "record_interest_trace", MagicMock(return_value="trace-1"))
    monkeypatch.setattr(learning_mod, "detect_and_record_misconception", MagicMock(return_value=None))
    monkeypatch.setattr(learning_mod, "document_thesis_fact_lines", lambda *a, **k: [])

    stored: dict = {"history": []}
    monkeypatch.setattr(learning_mod, "load_stored_chat_history", lambda *a, **k: list(stored["history"]))

    def _persist(user_id, course_id, topic_id, history, user_message, answer, **kwargs):
        entry = {"role": "assistant", "content": answer}
        for key, value in (kwargs.get("assistant_meta") or {}).items():
            if value not in (None, "", [], {}):
                entry[key] = value
        stored["history"] = list(history) + [{"role": "user", "content": user_message}, entry]
        return {"user_message_id": "msg-1"}

    persist_mock = MagicMock(side_effect=_persist)
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)

    prompts: list = []

    def _generate(**kwargs):
        prompts.append(kwargs.get("messages"))
        return "回答です [出典2][出典3]"

    monkeypatch.setattr(learning_mod, "generate_text", _generate)
    return SimpleNamespace(monkeypatch=monkeypatch, stored=stored, prompts=prompts, persist_mock=persist_mock)


def _ask(message: str, history: list | None = None, **extra):
    return learning_mod.learning_chat(
        "course-1", "topic-1",
        LearningChatRequest(message=message, history=history or [], support_action="ask_question", **extra),
        current_user=CURRENT_USER,
    )


# ---------------------------------------------------------------------------
# IK-0432 出典番号の固定
# ---------------------------------------------------------------------------


class TestSessionCitationNumbers:
    def test_fresh_numbering_starts_at_one(self):
        numbers = learning_mod._SessionCitationNumbers()
        assert [numbers.assign("a"), numbers.assign("b")] == [1, 2]

    def test_reuses_and_continues_after_max(self):
        history = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a", "sources": [
                {"index": 1, "chunk_id": "a"}, {"index": 2, "chunk_id": "b"},
            ]},
        ]
        numbers = learning_mod._SessionCitationNumbers(history)
        assert numbers.assign("c") == 3
        assert numbers.assign("b") == 2
        assert numbers.assign("d") == 4

    def test_legacy_renumbered_history_never_collides_within_turn(self):
        """旧来の振り直しで同じ番号が別チャンクに付いた履歴でも、1往復の中で番号は重複しない。"""
        history = [
            {"role": "assistant", "content": "a1", "sources": [{"index": 1, "chunk_id": "a"}]},
            {"role": "assistant", "content": "a2", "sources": [{"index": 1, "chunk_id": "b"}]},
        ]
        numbers = learning_mod._SessionCitationNumbers(history)
        got = [numbers.assign("a"), numbers.assign("b")]
        assert got[0] == 1
        assert len(set(got)) == 2


class TestRehydrateHistorySources:
    def test_restores_sources_for_stripped_client_turns(self):
        stored = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "回答  A", "sources": [{"index": 1, "chunk_id": "a"}]},
        ]
        client = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "回答 A"}]
        out = learning_mod._rehydrate_history_sources(client, stored)
        assert out[1]["sources"] == [{"index": 1, "chunk_id": "a"}]
        assert "sources" not in client[1]  # 入力は変更しない

    def test_does_not_guess_when_content_differs(self):
        stored = [{"role": "assistant", "content": "別の回答", "sources": [{"index": 1, "chunk_id": "a"}]}]
        client = [{"role": "assistant", "content": "回答 A"}]
        assert learning_mod._rehydrate_history_sources(client, stored)[0] == {"role": "assistant", "content": "回答 A"}


class TestStableNumberingAcrossTurns:
    def test_second_turn_reuses_number_and_continues(self, chat_env):
        chat_env.monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(1), _chunk(2)],
        )
        first = _ask("一つ目の質問")
        assert [(s.index, s.chunk_id) for s in first.sources] == [(1, "chunk-1"), (2, "chunk-2")]

        # クライアントは {role, content} だけを送り返す（uxsim の API runner と同じ形）。
        client_history = [{"role": m["role"], "content": m["content"]} for m in chat_env.stored["history"]]
        chat_env.monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(3), _chunk(2)],
        )
        second = _ask("二つ目の質問", history=client_history)
        assert [(s.index, s.chunk_id) for s in second.sources] == [(3, "chunk-3"), (2, "chunk-2")]

        # プロンプトの出典ラベルも振った番号と一致する（位置の 1..k ではない）。
        context = second_prompt_context(chat_env.prompts[-1])
        assert "[出典3] 『論文 3』" in context
        assert "[出典2] 『論文 2』" in context
        assert "[出典1]" not in context
        # 回答本文の [出典2][出典3] は根拠のある番号として残る。
        assert "[出典2]" in second.answer and "[出典3]" in second.answer
        # 保存される履歴は1往復目の sources を失わない（描画メタを戻した）。
        assistant_turns = [m for m in chat_env.stored["history"] if m["role"] == "assistant"]
        assert assistant_turns[0]["sources"][0]["chunk_id"] == "chunk-1"


def second_prompt_context(messages: list) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")


# ---------------------------------------------------------------------------
# IK-0433 score を学習者に返さない
# ---------------------------------------------------------------------------


class TestNoRawScoreInLearnerDto:
    def test_source_tier_item_has_no_score_field(self):
        assert "score" not in SourceTierItem.model_fields
        assert "score" not in SourceTierItem(index=1, score=0.9).model_dump()

    def test_chat_response_sources_have_no_score(self, chat_env):
        chat_env.monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(1)],
        )
        resp = _ask("質問です")
        dumped = resp.model_dump()
        assert dumped["sources"] and all("score" not in s for s in dumped["sources"])

    def test_app_js_no_longer_writes_data_score(self):
        app_js = (BACKEND.parent / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
        assert "data-score" not in app_js


# ---------------------------------------------------------------------------
# IK-0434 お礼・締めくくり
# ---------------------------------------------------------------------------


class TestClosingUtterance:
    @pytest.mark.parametrize("message", [
        "Thank you, that is very helpful!",
        "ありがとうございました、今日はここまでにします",
        "Thanks!",
        "ありがとうございます。とても参考になりました",
    ])
    def test_detects_pure_closing(self, message):
        assert learning_mod._is_closing_utterance(message)

    @pytest.mark.parametrize("message", [
        "Thank you, but why is mu negative?",
        "ありがとう、でも µ はなぜ負ですか？",
        "Thanks for the explanation of BAO",
        "helpfulness of the dark energy model",
        "",
    ])
    def test_does_not_catch_content(self, message):
        assert not learning_mod._is_closing_utterance(message)

    @pytest.mark.parametrize("intent_mode", [None, "discuss"])
    def test_fixed_reply_without_retrieval_or_llm(self, chat_env, intent_mode):
        def _no_search(*a, **k):
            raise AssertionError("closing utterance must not run retrieval")

        chat_env.monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", _no_search)
        chat_env.monkeypatch.setattr(
            learning_mod, "generate_text", lambda **k: (_ for _ in ()).throw(AssertionError("no LLM")),
        )
        resp = learning_mod.learning_chat(
            "course-1", "topic-1",
            LearningChatRequest(message="ありがとうございました、今日はここまでにします", intent_mode=intent_mode),
            current_user=CURRENT_USER,
        )
        assert resp.answer == label_vocab.CLOSING_UTTERANCE_REPLY
        # IK-0447: 締めくくりの定型文は内容の説明ではないので出所の分類を付けない。
        assert resp.content_grounding is None
        assert resp.sources == []
        assert resp.stance is None
        assert "？" not in resp.answer and "?" not in resp.answer

    def test_english_reply_for_latin_message(self, chat_env):
        chat_env.monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
        resp = learning_mod.learning_chat(
            "course-1", "topic-1",
            LearningChatRequest(message="Thanks!"),
            current_user=CURRENT_USER,
        )
        assert resp.answer == label_vocab.CLOSING_UTTERANCE_REPLY_EN


# ---------------------------------------------------------------------------
# IK-0430 前提知識の説明プロンプト
# ---------------------------------------------------------------------------


class TestPrerequisiteExplanationPrompt:
    def _prompt(self, monkeypatch, source_context: str) -> str:
        captured: dict = {}

        def _gen(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return "説明"

        monkeypatch.setattr(learning_mod, "generate_text", _gen)
        learning_mod._generate_learning_advice_response(
            "コース", "トピック", "いいえ、前提から教えてください",
            source_context=source_context, explain_prerequisite="前提A",
        )
        return captured["prompt"]

    def test_no_marker_instruction_without_numbered_sources(self, monkeypatch):
        prompt = self._prompt(monkeypatch, "## 抜粋\n[コース内トピック『前提A』の教材]\n本文")
        assert "`[出典N]` を本文に自然に挿入" not in prompt
        assert "出典マーカーは書かないこと" in prompt

    def test_marker_instruction_with_numbered_sources(self, monkeypatch):
        prompt = self._prompt(monkeypatch, "## 抜粋\n[出典1] 『論文』\n本文")
        assert "`[出典N]` を本文に自然に挿入" in prompt

    def test_excerpt_embeds_are_scrubbed(self):
        text = (
            "本文です。\n\n![[source:topic_summary]]\n\n式は ![[equation:eq_3]] です。\n\n"
            "### この節で参照する図\n- FIG. 1. Solutions\n![[figure:d687f713-e9fe-4950-93df-cd6b7b88136d]]\n"
        )
        out = learning_mod._scrub_excerpt_embeds(text)
        assert "![[" not in out
        assert "（数式）" in out
        assert "d687f713" not in out
        assert "この節で参照する図" not in out

    def test_topic_material_excerpt_is_scrubbed_in_context(self, monkeypatch):
        course = _course_data()
        course["topics"].append({
            "id": "topic-2", "title": "前提A",
            "student_material": {"source_text": "前提の本文 ![[figure:abc-123]] と ![[source:topic_summary]]"},
        })
        monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
        out = learning_mod._resolve_prerequisite_context(CURRENT_USER["id"], course, ["前提A"])
        assert "![[" not in out["context_block"]
        assert "（図）" in out["context_block"]


# ---------------------------------------------------------------------------
# IK-0436 document 直付け議論の要旨ブロック
# ---------------------------------------------------------------------------


class TestDocumentThesisFacts:
    def test_fact_lines_from_artifacts(self, monkeypatch):
        monkeypatch.setattr(opening_mod, "document_run_artifacts", lambda doc_id: {
            "thesis_reconstruction": {
                "central_question": "What drives mu(a)?",
                "central_thesis": {"text": "The sound speed constrains mu(a)."},
            },
            "paper_skeleton": {"entries": []},
        })
        lines = opening_mod.document_thesis_fact_lines("doc-1")
        assert any("この論文が答えようとした問い: What drives mu(a)?" in line for line in lines)
        assert any("The sound speed constrains mu(a)." in line for line in lines)

    def test_no_artifacts_no_lines(self, monkeypatch):
        monkeypatch.setattr(opening_mod, "document_run_artifacts", lambda doc_id: {})
        assert opening_mod.document_thesis_fact_lines("doc-1") == []

    def test_document_discuss_prompt_carries_thesis_block(self, chat_env):
        chat_env.monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
        chat_env.monkeypatch.setattr(
            learning_mod, "document_thesis_fact_lines",
            lambda doc_id: ["- この論文の主張（解析で再構成した文）: The sound speed constrains mu(a)."],
        )
        captured: list = []
        chat_env.monkeypatch.setattr(
            learning_mod, "generate_text", lambda **k: captured.append(k["messages"]) or "要旨によれば…",
        )
        body = LearningChatRequest(message="What is the most important result, in one sentence?", intent_mode="discuss")
        course_data = learning_mod._document_discuss_course_data("doc-1", "", "論文")
        resp = learning_mod._run_learning_turn(learning_mod._learning_chat_core(
            "_doc:doc-1", learning_mod.DISCUSSION_TOPIC_ID, body, CURRENT_USER,
            course_data=course_data, scope_document_ids={"doc-1"},
        ))
        messages = captured[-1]
        user_context = messages[1]["content"]
        assert learning_mod._DOCUMENT_THESIS_HEADING in user_context
        assert "The sound speed constrains mu(a)." in user_context
        assert learning_mod._DOCUMENT_THESIS_GUARD_PRECEDENCE in messages[0]["content"]
        # 番号付き出典には数えない（DM1: スコープも広げていない）。
        assert resp.sources == []
        assert resp.content_grounding == "model_generated"

    def test_course_discuss_does_not_add_thesis_block(self, chat_env):
        called: list = []
        chat_env.monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
        chat_env.monkeypatch.setattr(
            learning_mod, "document_thesis_fact_lines", lambda doc_id: called.append(doc_id) or ["- x"],
        )
        learning_mod.learning_chat(
            "course-1", learning_mod.DISCUSSION_TOPIC_ID,
            LearningChatRequest(message="この論文の結論は？", intent_mode="discuss"),
            current_user=CURRENT_USER,
        )
        assert called == []


# ---------------------------------------------------------------------------
# IK-0437 候補ゼロの digest
# ---------------------------------------------------------------------------


class _NoDbSession:
    def execute(self, *a, **k):
        raise RuntimeError("no db")

    def close(self):
        pass


class TestEmptyDigestFacts:
    def test_tension_digest_empty_has_fact(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "_pg_session", lambda: _NoDbSession())
        monkeypatch.setattr(learning_mod, "get_tension_digest", lambda uid, cid: {"course_id": cid, "items": []})
        out = learning_mod.get_tension_digest_route("course-1", current_user=CURRENT_USER)
        assert out["facts"] == [label_vocab.TENSION_DIGEST_EMPTY_FACT]

    def test_tension_digest_with_items_has_no_fact(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "_pg_session", lambda: _NoDbSession())
        monkeypatch.setattr(
            learning_mod, "get_tension_digest", lambda uid, cid: {"course_id": cid, "items": [{"trace_id": "t"}]},
        )
        assert "facts" not in learning_mod.get_tension_digest_route("course-1", current_user=CURRENT_USER)

    def test_anchor_digest_empty_has_fact(self, monkeypatch):
        monkeypatch.setattr(learning_mod, "_pg_session", lambda: _NoDbSession())
        monkeypatch.setattr(learning_mod, "get_anchor_digest", lambda uid, cid: {"course_id": cid, "items": []})
        out = learning_mod.get_anchor_digest_route("course-1", current_user=CURRENT_USER)
        assert out["facts"] == [label_vocab.ANCHOR_DIGEST_EMPTY_FACT]
