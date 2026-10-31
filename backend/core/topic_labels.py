"""予約疑似トピックの表示名（バックグラウンド worker 向けの中立な入口）。

会話機構には実在トピックでない予約 topic_id がある（正本は ``core.discuss.context``）。
tension / structure_anchor の worker は LLM 入力のセッション見出しに topic_title を
載せるが、予約 id をそのまま渡すと内部名が LLM 入力に出る（IK-0403）。ここは
「予約 id → 表示名」の変換だけを持ち、解析対象から外すような特別扱いはしない
（worker 側のモジュールが予約トピックの語彙を直接参照しないための薄い入口 —
``test_discuss_guardrails.py`` の「worker が予約トピックを特別扱いしない」を保つ）。

FastAPI / DB / LLM を import しない純関数。
"""

from __future__ import annotations

from core.discuss.context import DISCUSSION_TOPIC_ID, discussion_topic_label

__all__ = ["is_reserved_topic_id", "reserved_topic_label"]


def is_reserved_topic_id(topic_id: str | None) -> bool:
    """``topic_id`` が実在トピックでない予約 id か。"""
    return str(topic_id or "") == DISCUSSION_TOPIC_ID


def reserved_topic_label(course_id: str | None, topic_id: str | None) -> str | None:
    """予約 id なら表示名、そうでなければ ``None``。"""
    return discussion_topic_label(course_id, topic_id)
