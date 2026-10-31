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
    trim_at_boundary: bool = False,
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

    ``trim_at_boundary=True``（既定 False = 従来どおりの素の文字数トリム）のときは、
    ``max_chars`` を超える content を段落境界 → 文境界 → 文字数の順で切り、末尾に
    「…」を付ける（切り詰め後も ``max_chars`` 以内）。``$…$`` の数式区間の途中では
    切らない。長い回答を途中の語や数式で割ったまま LLM に再注入しないため
    （学習チャット, IK-0394）。3. の同一判定は切り詰め前の本文で行う。
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
        trimmed = (
            _trim_at_boundary(content, max_chars) if trim_at_boundary else content[:max_chars]
        )
        normalized.append({"role": role, "content": trimmed, "_full": content})

    if current_message is not None:
        cur_full = current_message.strip()
        cur = cur_full[:max_chars]
        if (
            cur
            and normalized
            and normalized[-1]["role"] == "user"
            and (
                normalized[-1]["_full"] == cur_full
                if trim_at_boundary
                else normalized[-1]["content"] == cur
            )
        ):
            normalized.pop()

    normalized = [{"role": m["role"], "content": m["content"]} for m in normalized]

    if len(normalized) > head_keep + max_messages:
        head = normalized[:head_keep] if head_keep > 0 else []
        tail = normalized[-max_messages:] if max_messages > 0 else []
        return head + tail

    return normalized


_BOUNDARY_ELLIPSIS = "…"
_SENTENCE_ENDS = "。．！？!?\n"
#: 境界で切るのは max_chars の半分以上を残せるときだけ（極端に短い断片にしない）。
_BOUNDARY_MIN_KEEP_RATIO = 0.5


def _outside_dollar_math(text: str, cut: int) -> int:
    """``text[:cut]`` が ``$`` の数式区間の内側で終わるなら、区間の開始位置へ戻す。"""
    head = text[:cut]
    count = 0
    index = 0
    while index < len(head):
        if head[index] == "\\":
            index += 2
            continue
        if head[index] == "$":
            count += 1
        index += 1
    if count % 2 == 1:
        # 開いたままの ``$``（``$$`` も同じ扱い）の直前まで戻す。
        opening = head.rfind("$")
        while opening > 0 and head[opening - 1] == "$":
            opening -= 1
        return opening
    return cut


def _trim_at_boundary(content: str, max_chars: int) -> str:
    """``content`` を ``max_chars`` 以内に、段落 → 文 → 文字数の境界で切る（末尾「…」）。"""
    if max_chars <= 0:
        return ""
    if len(content) <= max_chars:
        return content
    budget = max_chars - len(_BOUNDARY_ELLIPSIS)
    if budget <= 0:
        return content[:max_chars]
    window = content[:budget]
    floor = int(budget * _BOUNDARY_MIN_KEEP_RATIO)
    cut = budget
    paragraph = window.rfind("\n\n")
    if paragraph >= floor:
        cut = paragraph
    else:
        sentence = max(window.rfind(ch) for ch in _SENTENCE_ENDS)
        if sentence + 1 >= floor:
            cut = sentence + 1
    cut = _outside_dollar_math(content, cut)
    if cut <= 0:
        cut = budget
    return content[:cut].rstrip() + _BOUNDARY_ELLIPSIS
