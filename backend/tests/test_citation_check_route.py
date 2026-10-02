"""IK-0545: 学習チャット経路の逐語引用一致検査（DTO 形・数値なし・保存文不変・観測）。"""

from __future__ import annotations

from tests.test_chat_history_source_persistence import (  # noqa: F401  (sys.path を整える)
    CURRENT_USER,
    _chunk,
    chat_env,
)

import routes.learning as learning_mod  # noqa: E402
from api.schemas import LearningChatRequest  # noqa: E402
from core.citation_check import CITATION_UNMATCHED_NOTE  # noqa: E402


def _run(chat_env, answer: str, chunks):
    chat_env.monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: chunks)
    chat_env.monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: answer)
    events: list[str] = []
    chat_env.monkeypatch.setattr(
        learning_mod, "_record_document_discuss_event", lambda ev, *a, **k: events.append(ev)
    )
    resp = learning_mod.learning_chat(
        "course-1", "topic-1",
        LearningChatRequest(message="質問です", support_action="ask_question"),
        current_user=CURRENT_USER,
    )
    return resp, events


def test_misattributed_quote_is_reported_as_fact(chat_env):
    # チャンク 2 の本文を [出典1] に帰属させた回答（seq135 の型）。
    answer = "論文は「チャンク本文 2 です」と述べています[出典1]。"
    chunks = [dict(_chunk(1), text="チャンク本文 1 です"), dict(_chunk(2), text="チャンク本文 2 です")]
    resp, events = _run(chat_env, answer, chunks)
    assert resp.citation_check == {
        "checked": ["1"],
        "unmatched": [{"citation": "1", "quotes": ["チャンク本文 2 です"], "note": CITATION_UNMATCHED_NOTE}],
    }
    assert "citation_quote_unmatched" in events
    # 本文は書き換えない・保存文にも検査結果を焼き込まない（RS5）。
    assert "「チャンク本文 2 です」" in resp.answer
    saved = chat_env.persist_mock.call_args.args[5]
    assert saved == resp.answer
    meta = chat_env.persist_mock.call_args.kwargs["assistant_meta"]
    assert "citation_check" not in meta
    for s in meta["sources"]:
        assert "passage" not in s


def test_matched_quote_has_empty_unmatched_and_no_event(chat_env):
    answer = "論文は「チャンク本文 1 です」と述べています[出典1]。"
    chunks = [dict(_chunk(1), text="チャンク本文 1 です")]
    resp, events = _run(chat_env, answer, chunks)
    assert resp.citation_check == {"checked": ["1"], "unmatched": []}
    assert "citation_quote_unmatched" not in events


def test_answer_without_quotes_has_no_check(chat_env):
    resp, events = _run(chat_env, "回答です [出典1][出典2]", [_chunk(1), _chunk(2)])
    assert resp.citation_check is None
    assert "citation_quote_unmatched" not in events


def test_passage_never_reaches_response_sources(chat_env):
    answer = "「チャンク本文 1 です」[出典1]"
    resp, _ = _run(chat_env, answer, [dict(_chunk(1), text="チャンク本文 1 です")])
    dumped = resp.model_dump()
    for s in dumped["sources"]:
        assert "passage" not in s
    # 数値を持たない。
    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        else:
            assert isinstance(v, str)
    walk(dumped["citation_check"])


def test_degraded_turn_has_no_check(chat_env):
    chat_env.monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(1)])

    def _boom(**kwargs):
        raise RuntimeError("llm down")

    chat_env.monkeypatch.setattr(learning_mod, "generate_text", _boom)
    resp = learning_mod.learning_chat(
        "course-1", "topic-1",
        LearningChatRequest(message="質問です", support_action="ask_question"),
        current_user=CURRENT_USER,
    )
    assert resp.degraded is True
    assert resp.citation_check is None


def test_display_projection_keeps_the_check(chat_env):
    """学習者向け表示投影（LearnerDisplayRoute / SSE final）を通しても事実が残る。"""
    from api.display_route import project_learner_model

    answer = "論文は「チャンク本文 2 です」と述べています[出典1]。"
    chunks = [dict(_chunk(1), text="チャンク本文 1 です"), dict(_chunk(2), text="チャンク本文 2 です")]
    resp, _ = _run(chat_env, answer, chunks)
    projected = project_learner_model(resp)
    assert projected.citation_check == resp.citation_check
