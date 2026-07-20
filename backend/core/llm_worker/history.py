"""チャット型 AI 支援の会話履歴ウィンドウ化の正本。

学習チャット本体・グラフ要素説明・コースビルダー・W層対話・Admin Copilot の
5つのチャット型呼び出し元は、いずれも「フロントが送る ``[{role, content}]`` を
LLM messages 用に整形する（未知 role/空 content の除去・文字数トリム・直近N件への
絞り込み）」という同型の前処理を個別実装していた（正本:
docs/features/assistant_common_infra_design.md §2）。このモジュールはその
前処理だけを一般化して集約する。

Admin Copilot の ``core/admin_assistant/intent.py::_normalize_history`` を
一般化したもの（本モジュール新設時点では intent.py 側は未移行 — 既存テストを
壊さないため、委譲への置換は別 Phase で行う）。

FastAPI / LLM SDK は import しない。
"""

from __future__ import annotations


def window_history(
    history: list | None,
    *,
    max_messages: int = 20,
    max_chars: int = 2000,
    head_keep: int = 0,
    current_message: str | None = None,
) -> list[dict]:
    """フロントの会話履歴を LLM messages 用に正規化・ウィンドウ化する。

    セマンティクス:

    1. dict 以外の要素・``role`` が ``user``/``assistant`` 以外・空 content は
       スキップする。
    2. content は文字列化・``strip()``・``max_chars`` でトリムする。
    3. ``current_message`` が指定され、正規化後の末尾が同内容（トリム後の比較）の
       user メッセージであれば、その1件を除去する（フロントが送信直前に現在発話を
       履歴へ push する実装との二重化防止）。
    4. 正規化後の件数が ``head_keep + max_messages`` を超える場合、先頭
       ``head_keep`` 件 + 末尾 ``max_messages`` 件を返す（head 保護。閾値を
       超えたときのみ切り詰めるため、この2つのスライスが重複することはない）。
       それ以下ならすべて返す。

    戻り値は ``{"role", "content"}`` のみを持つ新しい list（元の dict の他の
    キーは保持しない）。
    """
    normalized: list[dict] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in ("user", "assistant"):
            continue
        content = item.get("content")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        content = content.strip()
        if not content:
            continue
        normalized.append({"role": role, "content": content[:max_chars]})

    if current_message is not None:
        cur = current_message.strip()[:max_chars]
        if (
            cur
            and normalized
            and normalized[-1]["role"] == "user"
            and normalized[-1]["content"] == cur
        ):
            normalized.pop()

    if len(normalized) > head_keep + max_messages:
        head = normalized[:head_keep] if head_keep > 0 else []
        tail = normalized[-max_messages:] if max_messages > 0 else []
        return head + tail

    return normalized
