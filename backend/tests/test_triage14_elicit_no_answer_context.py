"""TRIAGE14: 予想を引き出す往復（cycle_mode=elicit）では論文本文（答え）を LLM に渡さない。

第 14 周 seq136: elicit の user 区画に「disfavors low values of c_s^2…」の本文が入っていた。
資料名だけを置き、本文は置かない（Elicit の契約フレーズは system 側で不変）。diff は従来どおり本文を渡す。
"""

from __future__ import annotations

import json

from tests.test_ik0432_0437_chat_turn_fixes import (  # noqa: F401
    _ask,
    _chunk,
    chat_env,
)

import routes.learning as learning_mod  # noqa: E402


def _context_text(messages) -> str:
    return json.dumps(messages, ensure_ascii=False)


def test_elicit_prompt_has_titles_but_not_chunk_bodies(chat_env):
    chat_env.monkeypatch.setattr(
        learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(1), _chunk(2)]
    )
    _ask("予想を立てる前に、考えるための問いを一つください。", cycle_mode="elicit")
    text = _context_text(chat_env.prompts[-1])
    assert "チャンク本文 1" not in text
    assert "チャンク本文 2" not in text
    assert "本文は予想の前には示しません" in text


def test_diff_prompt_still_has_chunk_bodies(chat_env):
    chat_env.monkeypatch.setattr(
        learning_mod, "search_chunks_with_metadata", lambda *a, **k: [_chunk(1)]
    )
    _ask("私の予想と比べてください", cycle_mode="diff")
    assert "チャンク本文 1" in _context_text(chat_env.prompts[-1])
