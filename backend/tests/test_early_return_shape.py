"""応答の骨格を早期 return 経路にも通す（dialogue_response_shape_design.md §8.2）。

- 生成本文の早期経路（グラフ要素の説明・HELP の音声 / casual 整形）は ``assemble`` で
  問い返しを 1 つまでにする（保存文も同じ本文）。
- 本文を組み替えない経路（HELP のマニュアル本文素通し・無ヒット固定文・点数 / 締め / 地図 /
  学習パスに戻る の固定文）は ``verbatim_shape_dto`` で区画を読むだけ（本文はバイト一致）。
- どの早期経路でも ``LearningChatResponse.shape`` を埋める（固定 3 キー・数値なし）。
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
from core.dialogue_shape import SHAPE_DTO_KEYS, verbatim_shape_dto  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402

from tests.test_ik0383_prerequisite_followup import _chat, chat_env  # noqa: E402,F401


# ---------------------------------------------------------------------------
# core: verbatim_shape_dto
# ---------------------------------------------------------------------------


class TestVerbatimShapeDto:
    def test_keys_are_fixed_and_have_no_numbers(self):
        dto = verbatim_shape_dto("答えです。")
        assert tuple(dto) == SHAPE_DTO_KEYS
        assert dto == {"closing_question": None, "has_gate": False, "mirror_kept": False}

    def test_single_trailing_question_is_reported(self):
        dto = verbatim_shape_dto("答えです。\n\nどこが気になりましたか？")
        assert dto["closing_question"] == "どこが気になりましたか？"

    def test_text_that_would_need_trimming_reports_none(self):
        # 末尾に問いが連なる本文は組み替えていないので、問い返しとして報告しない。
        assert verbatim_shape_dto("答え。わかりましたか？ 試してみますか？")["closing_question"] is None

    def test_does_not_modify_input(self):
        text = "マニュアル本文。できますか？\n\n[出典1]\n\n教材の内容についての質問なら、そのまま送り直してください。"
        before = str(text)
        verbatim_shape_dto(text)
        assert text == before


# ---------------------------------------------------------------------------
# HELP pre-route
# ---------------------------------------------------------------------------

_HIT = {
    "file": "getting_started.md",
    "anchor": "voice-mode",
    "title": "音声モードの使い方",
    # マニュアル本文の中の問いの形（FAQ の見出し）を切り詰めない・言い換えない。
    "body": "音声で質問できますか？ できますか？ 入力欄横のマイクボタンを押すと音声入力が始まります。",
    "audience": "student",
    "citation": "manual/student/getting_started.md#voice-mode",
    "documented": True,
}


@pytest.fixture
def help_env(monkeypatch):
    monkeypatch.setattr(learning_mod, "persist_chat_history", MagicMock(return_value={}))
    monkeypatch.setattr(learning_mod, "record_interest_trace", MagicMock(return_value="t"))
    monkeypatch.setattr(learning_mod, "_search_manual", lambda *a, **k: [dict(_HIT)])
    monkeypatch.setattr(learning_mod, "_vector_search_manual", None)
    monkeypatch.setattr(
        learning_mod, "get_llm_params", lambda tier: {"model": "m", "reasoning_effort": None}
    )
    return monkeypatch


class TestUsageHelp:
    def test_text_path_passes_manual_body_through_and_fills_shape(self, help_env):
        body = LearningChatRequest(message="音声の使い方を教えて", history=[])
        resp = learning_mod._usage_help_response("u", "c", "t", body)
        assert resp.answer.startswith(_HIT["body"])  # 本文はそのまま（切り詰め・言い換えなし）
        assert resp.shape == {"closing_question": None, "has_gate": False, "mirror_kept": False}

    def test_no_hit_fixed_text_is_unchanged(self, help_env):
        help_env.setattr(learning_mod, "_search_manual", lambda *a, **k: [])
        body = LearningChatRequest(message="音声の使い方を教えて", history=[])
        resp = learning_mod._usage_help_response("u", "c", "t", body)
        assert resp.answer == (
            f"{learning_mod._USAGE_HELP_NOT_DOCUMENTED}\n\n{learning_mod._USAGE_HELP_FOOTER}"
        )
        assert resp.shape is not None and resp.shape["closing_question"] is None

    def test_casual_formatting_keeps_one_closing_question(self, help_env):
        help_env.setattr(
            learning_mod, "generate_text",
            lambda **k: "マイクのボタンを押せば話せます。わかりましたか？ 試してみますか？",
        )
        body = LearningChatRequest(message="音声の使い方を教えて", history=[], intent_mode="casual")
        resp = learning_mod._usage_help_response("u", "c", "t", body)
        assert "わかりましたか？" not in resp.answer
        assert "試してみますか？" in resp.answer
        assert resp.answer.endswith(learning_mod._USAGE_HELP_FOOTER)

    def test_casual_formatting_failure_falls_back_to_raw_body(self, help_env):
        def _boom(**k):
            raise RuntimeError("x")

        help_env.setattr(learning_mod, "generate_text", _boom)
        body = LearningChatRequest(message="音声の使い方を教えて", history=[], intent_mode="casual")
        resp = learning_mod._usage_help_response("u", "c", "t", body)
        assert resp.answer.startswith(_HIT["body"])
        assert resp.degraded is True


# ---------------------------------------------------------------------------
# typed action: EXPLAIN_GRAPH_ELEMENT
# ---------------------------------------------------------------------------


@pytest.fixture
def graph_env(monkeypatch):
    persisted: list[str] = []
    monkeypatch.setattr(
        learning_mod, "persist_chat_history",
        lambda *a, **k: persisted.append(a[5]) or {},
    )
    monkeypatch.setattr(learning_mod, "list_course_source_document_ids", lambda cd: set())
    monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: set())
    monkeypatch.setattr(learning_mod, "_approved_graph_element_answer", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "get_personal_layer", lambda *a, **k: "")
    monkeypatch.setattr(
        learning_mod, "get_llm_params", lambda tier: {"model": "m", "reasoning_effort": None}
    )
    monkeypatch.setattr(
        learning_mod, "get_graph_element_context",
        lambda *a, **k: {
            "chunk_text": "本文", "element_label": "概念", "element_type": "concept",
            "source_title": "論文", "graph_description": "", "related_chunks": [],
        },
    )
    return SimpleNamespace(persisted=persisted, monkeypatch=monkeypatch)


def _explain(graph_env, reply):
    graph_env.monkeypatch.setattr(learning_mod, "generate_text", lambda **k: reply)
    body = LearningChatRequest(message="", chunk_id="c1", element_id="e1", element_type="concept")
    return learning_mod._generate_graph_element_explanation(
        user_id="u", course_id="c", topic_id="t", course_title="コース",
        topic_title="トピック", course_data={}, body=body,
    )


class TestGraphElementExplanation:
    def test_trailing_questions_are_cut_to_one_and_persisted_alike(self, graph_env):
        resp = _explain(graph_env, "概念の説明です。\n\nわかりましたか？ どこが気になりますか？")
        assert "わかりましたか？" not in resp.answer
        assert resp.answer.endswith("どこが気になりますか？")
        assert graph_env.persisted == [resp.answer]
        assert resp.shape == {
            "closing_question": "どこが気になりますか？", "has_gate": False, "mirror_kept": False,
        }

    def test_single_question_answer_is_byte_identical(self, graph_env):
        reply = "概念の説明です。\n\nどこが気になりますか？"
        assert _explain(graph_env, reply).answer == reply


# ---------------------------------------------------------------------------
# 固定文の早期経路（route 経由）
# ---------------------------------------------------------------------------


class TestFixedTextEarlyReturns:
    def test_score_request_fills_shape(self, chat_env):
        from core import label_vocab

        resp = _chat("私の理解度は何点ですか？")
        assert resp.answer == label_vocab.UNDERSTANDING_SCORE_REQUEST_REPLY
        assert resp.shape == {"closing_question": None, "has_gate": False, "mirror_kept": False}

    def test_return_to_path_fills_shape(self, chat_env):
        resp = _chat("戻る", support_action="return_to_learning_path")
        assert resp.shape is not None and resp.shape["has_gate"] is False


# ---------------------------------------------------------------------------
# ガードレール: 学習チャットの応答を組み立てる全箇所が shape を埋める
# ---------------------------------------------------------------------------

_SHAPE_REQUIRED_FUNCTIONS = (
    "_learning_chat_core",
    "_usage_help_response",
    "_atlas_action_response",
    "_generate_graph_element_explanation",
)


def test_every_learning_chat_response_sets_shape():
    """早期 return を足すときも ``shape=`` を書く（書き忘れた経路は DTO が None になる）。"""
    import ast

    src = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    seen = set()
    missing = []
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name in _SHAPE_REQUIRED_FUNCTIONS:
            seen.add(fn.name)
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "LearningChatResponse"
                    and "shape" not in {k.arg for k in node.keywords}
                ):
                    missing.append(f"{fn.name}:{node.lineno}")
    assert seen == set(_SHAPE_REQUIRED_FUNCTIONS)
    assert not missing, f"shape= の無い LearningChatResponse: {missing}"
