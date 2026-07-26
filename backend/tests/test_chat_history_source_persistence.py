"""チャット履歴に描画メタ（sources 等）を焼き込む修正の検証。

背景: 本文中の連番出典 ``[出典N]`` を出典チップへ変換する
``frontend/public/js/app.js::linkifyCitations`` は ``msg.sources`` に依存するが、
``services.persist_chat_history`` は assistant メッセージを
``{role, content, id}`` の3キーでしか保存していなかった。そのため
``GET /courses/{id}/topics/{tid}/chat`` から復元したメッセージは常に
``sources`` を欠き、リロード・トピック切替・discuss モード遷移のあと
``[出典N]`` がプレーンテキストへ退化していた（応答直後だけチップになる）。

対象:
  - ``backend/api/services.py::persist_chat_history``（``assistant_meta`` 引数）
  - ``backend/api/routes/learning.py::learning_chat``（本体 RAG の焼き込み）
  - ``backend/api/routes/learning.py::_usage_help_response``（マニュアル出典の焼き込み）
  - ``frontend/public/js/app.js``（復元済み出典の 404 表示）

DB・実 LLM には触れない。services 側はフェイクセッション（
test_source_chunk_visibility.py と同型）、routes 側は境界 monkeypatch（
test_learning_chat_infra.py / test_help_usage_route.py と同型）で検証する。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from api import services  # noqa: E402
import routes.learning as learning_mod  # noqa: E402
import core.llm_worker.client as llm_worker_client  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402

_APP_JS = Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "app.js"

CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}


# ---------------------------------------------------------------------------
# services.persist_chat_history — assistant_meta の焼き込み
# ---------------------------------------------------------------------------


class _CapturingSession:
    """execute() に渡された params を記録するフェイクセッション。"""

    def __init__(self):
        self.executed_params: dict | None = None
        self.committed = False
        self.closed = False

    def execute(self, sql, params=None):
        self.executed_params = params or {}
        return None

    def commit(self):
        self.committed = True

    def rollback(self):  # pragma: no cover - 正常系では呼ばれない
        pass

    def close(self):
        self.closed = True


def _persist(monkeypatch, **kwargs) -> list[dict]:
    """persist_chat_history を実行し、DB へ渡された history JSON を返す。"""
    session = _CapturingSession()
    monkeypatch.setattr(services, "_pg_session", lambda: session)
    services.persist_chat_history(
        CURRENT_USER["id"], "course-1", "topic-1",
        kwargs.pop("history", []),
        kwargs.pop("user_message", "質問です"),
        kwargs.pop("assistant_answer", "回答です [出典1]"),
        **kwargs,
    )
    assert session.committed and session.closed
    return json.loads(session.executed_params["history"])


class TestPersistChatHistoryAssistantMeta:
    def test_meta_is_baked_into_assistant_entry(self, monkeypatch):
        sources = [{"index": 1, "chunk_id": "chunk-a", "source_title": "論文", "tier": "source", "score": 0.9}]
        history = _persist(
            monkeypatch,
            assistant_meta={"sources": sources, "overall_tier": "source", "content_grounding": "course_material"},
        )
        assistant = history[-1]
        assert assistant["role"] == "assistant"
        assert assistant["sources"] == sources
        assert assistant["overall_tier"] == "source"
        assert assistant["content_grounding"] == "course_material"

    def test_user_entry_is_untouched(self, monkeypatch):
        history = _persist(monkeypatch, assistant_meta={"sources": [{"index": 1}]})
        user_entry = history[-2]
        assert user_entry["role"] == "user"
        assert set(user_entry) == {"role", "content", "id"}

    def test_empty_values_are_not_stored(self, monkeypatch):
        """空の値は既存行にキーが無いのと同じ扱いにし、履歴 JSONB を太らせない。"""
        history = _persist(
            monkeypatch,
            assistant_meta={
                "sources": [], "overall_tier": None, "content_grounding": "",
                "manual_citations": None, "extra": {},
            },
        )
        assert set(history[-1]) == {"role", "content", "id"}

    def test_protected_keys_cannot_be_overridden(self, monkeypatch):
        history = _persist(
            monkeypatch,
            assistant_answer="本物の回答",
            assistant_message_id="assistant-1",
            assistant_meta={"role": "user", "content": "すり替え", "id": "spoofed", "sources": [{"index": 1}]},
        )
        assistant = history[-1]
        assert assistant["role"] == "assistant"
        assert assistant["content"] == "本物の回答"
        assert assistant["id"] == "assistant-1"
        assert assistant["sources"] == [{"index": 1}]

    def test_default_is_backward_compatible(self, monkeypatch):
        """assistant_meta を渡さない既存の呼び出し側は従来どおり3キーのまま。"""
        history = _persist(monkeypatch)
        assert set(history[-1]) == {"role", "content", "id"}

    def test_prior_history_metadata_round_trips(self, monkeypatch):
        """クライアントが送り返した過去メッセージのメタも失われない（往復の保持）。"""
        prior = [
            {"role": "user", "content": "前の質問", "id": "u0"},
            {"role": "assistant", "content": "前の回答 [出典1]", "id": "a0",
             "sources": [{"index": 1, "chunk_id": "chunk-old"}]},
        ]
        history = _persist(monkeypatch, history=prior)
        assert history[1]["sources"] == [{"index": 1, "chunk_id": "chunk-old"}]


# ---------------------------------------------------------------------------
# learning_chat — 本体 RAG の焼き込み
# ---------------------------------------------------------------------------


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


def _chunk(idx: int, *, material_id: str = "mat-1", score: float = 0.9) -> dict:
    return {
        "id": f"chunk-{idx}",
        "text": f"チャンク本文 {idx}",
        "score": score,
        "source_title": f"論文 {idx}",
        "source_file": f"paper{idx}.pdf",
        "material_id": material_id,
        "tier": "source",
    }


@pytest.fixture
def chat_env(monkeypatch):
    """learning_chat() をフル実行できるよう DB/LLM 境界だけモックする。"""
    settings = SimpleNamespace(
        learning_chat_max_calls_per_day=300,
        learning_chat_llm_model="",
        llm_analysis_model="analysis-model-x",
        llm_fast_model="fast-model-x",
    )
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_worker_client, "get_settings", lambda: settings)
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
    monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: "回答です [出典1][出典2]")

    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    return SimpleNamespace(settings=settings, persist_mock=persist_mock, monkeypatch=monkeypatch)


def _persisted_meta(persist_mock) -> dict:
    persist_mock.assert_called_once()
    return persist_mock.call_args.kwargs["assistant_meta"]


class TestLearningChatPersistsSources:
    def test_sources_are_persisted_with_response(self, chat_env):
        chat_env.monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(1), _chunk(2)],
        )
        resp = learning_mod.learning_chat(
            "course-1", "topic-1",
            LearningChatRequest(message="質問です", support_action="ask_question"),
            current_user=CURRENT_USER,
        )
        meta = _persisted_meta(chat_env.persist_mock)
        # レスポンスに載る出典と、履歴へ焼き込む出典の index / chunk_id は一致する
        # （これが崩れると復元後のチップが別チャンクを開く）。
        assert [s["index"] for s in meta["sources"]] == [s.index for s in resp.sources]
        assert [s["chunk_id"] for s in meta["sources"]] == [s.chunk_id for s in resp.sources]
        assert meta["overall_tier"] == resp.overall_tier
        assert meta["content_grounding"] == resp.content_grounding

    def test_persisted_sources_carry_only_chip_fields(self, chat_env):
        """チップ描画とポップアップ起動に要る最小フィールドのみ（履歴を太らせない）。"""
        chat_env.monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(1)],
        )
        learning_mod.learning_chat(
            "course-1", "topic-1",
            LearningChatRequest(message="質問です", support_action="ask_question"),
            current_user=CURRENT_USER,
        )
        meta = _persisted_meta(chat_env.persist_mock)
        assert set(meta["sources"][0]) == {"index", "chunk_id", "source_title", "tier", "score"}

    def test_no_matching_chunks_persists_no_sources(self, chat_env):
        """該当チャンクなし（score 足切り）は空のまま — 空値は保存されない。"""
        chat_env.monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(1, score=0.1)],
        )
        learning_mod.learning_chat(
            "course-1", "topic-1",
            LearningChatRequest(message="質問です", support_action="ask_question"),
            current_user=CURRENT_USER,
        )
        assert _persisted_meta(chat_env.persist_mock)["sources"] == []

    def test_discuss_mode_persists_sources_too(self, chat_env):
        """discuss（論文と話す）は毎回履歴を再取得する経路のため、ここが要になる。"""
        chat_env.monkeypatch.setattr(
            learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(1), _chunk(2)],
        )
        learning_mod.learning_chat(
            "course-1", learning_mod.DISCUSSION_TOPIC_ID,
            LearningChatRequest(
                message="この論文の前提は？", intent_mode="discuss", discuss_scope="course_sources",
            ),
            current_user=CURRENT_USER,
        )
        assert len(_persisted_meta(chat_env.persist_mock)["sources"]) == 2


# ---------------------------------------------------------------------------
# _usage_help_response — マニュアル出典の焼き込み
# ---------------------------------------------------------------------------


class TestUsageHelpPersistsManualCitations:
    def test_manual_citations_are_persisted(self, monkeypatch):
        persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
        monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
        monkeypatch.setattr(learning_mod, "record_interest_trace", MagicMock(return_value="trace-1"))
        monkeypatch.setattr(learning_mod, "_vector_search_manual", lambda *a, **k: [])
        monkeypatch.setattr(learning_mod, "_search_manual", lambda *a, **k: [{
            "file": "student/02-student.md",
            "anchor": "voice-mode",
            "title": "音声で話す",
            "body": "マイクボタンを押します。",
            "audience": "student",
            "citation": "student/02-student.md#voice-mode",
            "documented": True,
        }])

        resp = learning_mod._usage_help_response(
            CURRENT_USER["id"], "course-1", "topic-1",
            LearningChatRequest(message="音声モードの使い方を教えて"),
        )
        meta = persist_mock.call_args.kwargs["assistant_meta"]
        assert meta["manual_citations"] == resp.manual_citations
        assert meta["manual_citations"][0]["title"] == "音声で話す"


# ---------------------------------------------------------------------------
# フロント側の契約（静的検査）
# ---------------------------------------------------------------------------


class TestFrontendContract:
    def test_linkify_reads_msg_sources(self):
        """バックエンドが焼き込む先は msg.sources（この参照が消えたら復元チップも消える）。"""
        src = _APP_JS.read_text(encoding="utf-8")
        body = re.search(r"function linkifyCitations\(html, msg\) \{(.+?)\n  \}", src, re.DOTALL)
        assert body, "linkifyCitations が見つからない"
        assert "msg.sources" in body.group(1)
        assert "出典" in body.group(1)

    def test_source_popup_handles_missing_chunk(self):
        """履歴に焼き込まれた古い出典はチャンクが消えていることがある（404 を事実で返す）。"""
        src = _APP_JS.read_text(encoding="utf-8")
        assert "res.status === 404" in src
        assert "この出典は現在参照できません" in src
