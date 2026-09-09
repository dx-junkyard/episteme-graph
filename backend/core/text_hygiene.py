"""制御シーケンスの除去（LLM 入出力・artifact スニペットの共通衛生層）。

正本: ``docs/features/graph_dialogue_review_design.md`` §15（応答文体の改訂）。

パイプラインの artifact には、ログ着色などの経路で紛れ込んだ ANSI エスケープ
（``\\x1b[0m``）や、ESC が失われた**裸の残骸**（``[0m`` / ``[1m``）が混ざることが
ある。これがそのまま grounding に載ると LLM の入力を汚し、応答にも転写されて
画面・読み上げに漏れる（オーナー報告 2026-09-10）。

本モジュールは除去だけを行う純粋関数を持つ（FastAPI / DB / LLM を import しない）。
``core.tts.strip_text_for_speech``（読み上げ整形）とは役割が違う — こちらは
**表示テキストとして残してよい形**へ整えるだけで、数式・markdown には触らない。
"""

from __future__ import annotations

import re

__all__ = ["strip_control_sequences"]

#: ESC 付きの ANSI エスケープシーケンス（CSI）。
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: ESC が落ちた裸の SGR 残骸（``[0m`` / ``[1;32m`` 等）。
_BARE_SGR_RE = re.compile(r"\[[0-9;]{1,6}m")

#: C0 制御文字（``\n`` / ``\t`` は残す）+ DEL。
_C0_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_control_sequences(text: str) -> str:
    """ANSI エスケープ・裸の SGR 残骸・C0 制御文字を除去する（``\\n`` / ``\\t`` は保持）。

    None・非文字列は空文字へ倒す（呼び出し側に try/except を書かせない）。
    """
    if not isinstance(text, str) or not text:
        return "" if not isinstance(text, str) else text
    cleaned = _ANSI_ESCAPE_RE.sub("", text)
    cleaned = _BARE_SGR_RE.sub("", cleaned)
    cleaned = _C0_CONTROL_RE.sub("", cleaned)
    return cleaned
