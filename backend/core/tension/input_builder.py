"""Stage 1 — 会話窓の切り出し・LLM 入力構築（非LLM・純関数）。

構築規則（設計書 §5.2）:
- 会話窓: tension_hint=true の発話を核に前後 ±3 往復、複数ヒントが近接する場合は結合。
  窓上限 20 往復・入力上限 ~8k tokens（超過時は tutor 応答を先に短縮）。
- tutor 応答は 400 字に切り詰め（弁別に必要なのは学習者側の反応パターン）。
  tier は弁別材料（source_doubt は tier=approved への疑いで重み増）なので保持する。
- context blocks はその窓で実際に参照された component/chunk のみ（id 捏造防止）。
  chunk = ヒント痕跡の ``cited_chunk_ids``。component = その chunk を主に持つ live component、
  それが無ければコースの出典論文の親 component（worker 側の SQL・IK-0402）。
"""

from __future__ import annotations

import json

from core.tension.schema import ConversationTurn, ConversationWindow
from core.text_hygiene import scrub_internal_placeholders

# tutor 応答の切り詰め長
TUTOR_TEXT_LIMIT = 400
# 窓の前後往復数・窓上限
WINDOW_BEFORE = 3
WINDOW_AFTER = 3
MAX_WINDOW_TURNS = 20
# 入力上限 ~8k tokens の近似（日本語 1 token ≒ 1.5 字として文字数で管理）
MAX_INPUT_CHARS = 12_000
# 超過時の tutor 追加短縮長
TUTOR_TEXT_LIMIT_TIGHT = 120
# context blocks の件数・見出し長の上限（入力肥大の防止）
MAX_CONTEXT_CHUNKS = 12
MAX_CONTEXT_COMPONENTS = 20
CONTEXT_HEAD_LIMIT = 160


def cited_chunk_ids_from_hints(hint_payloads: list[dict]) -> list[str]:
    """ヒント痕跡の ``cited_chunk_ids``（その往復の回答が引用したチャンク）を出現順に集める。

    窓の中で実際に参照された chunk だけを context blocks にする（設計書 §5.2 の
    「id 捏造防止」— 窓と無関係な chunk を候補に並べない）。
    """
    out: list[str] = []
    for payload in hint_payloads or []:
        if not isinstance(payload, dict):
            continue
        for cid in payload.get("cited_chunk_ids") or []:
            cid = str(cid or "").strip()
            if cid and cid not in out:
                out.append(cid)
    return out[:MAX_CONTEXT_CHUNKS]


def build_context_blocks(
    chunk_rows: list, component_rows: list,
) -> tuple[list[dict], list[dict]]:
    """DB 行 → ``(components, chunks)`` の context blocks（IK-0402）。

    - ``chunk_rows``: ``(id, head)``。見出しは内部参照プレースホルダーを「（数式）」等に
      置き換えてから切り詰める（内部 ID を LLM に読ませない）。
    - ``component_rows``: ``(id, name)``。
    id が空の行は落とす（validator の allowed id 集合に空文字を入れない）。
    """
    chunks: list[dict] = []
    for row in chunk_rows or []:
        cid = str(row[0] or "").strip() if row else ""
        if not cid:
            continue
        head = scrub_internal_placeholders(str(row[1] or ""))
        head = " ".join(head.split())
        if len(head) > CONTEXT_HEAD_LIMIT:
            head = head[:CONTEXT_HEAD_LIMIT] + "…"
        chunks.append({"id": cid, "head": head})
    components: list[dict] = []
    seen: set[str] = set()
    for row in component_rows or []:
        comp_id = str(row[0] or "").strip() if row else ""
        if not comp_id or comp_id in seen:
            continue
        seen.add(comp_id)
        components.append({"id": comp_id, "label": " ".join(str(row[1] or "").split())[:CONTEXT_HEAD_LIMIT]})
    return components[:MAX_CONTEXT_COMPONENTS], chunks[:MAX_CONTEXT_CHUNKS]


def turns_from_history(
    history: list[dict],
    hint_texts: list[str] | None = None,
    tier_by_index: dict[int, str] | None = None,
) -> list[ConversationTurn]:
    """learning_chat_history.history（[{role, content}]）→ ConversationTurn 列。

    turn_id は履歴配列 index から決定論的に振る（msg_{index:04d}）。
    hint_texts に一致（前方一致・500字切り詰め互換）する学習者発話に tension_hint を立てる。
    """
    hints = [h for h in (hint_texts or []) if h]
    tiers = tier_by_index or {}
    turns: list[ConversationTurn] = []
    for i, msg in enumerate(history or []):
        role = "learner" if (msg.get("role") == "user") else "tutor"
        text = str(msg.get("content") or "")
        hint = False
        if role == "learner" and hints:
            # record_interest_trace は text を 500 字で切るため前方一致で照合する
            hint = any(text.startswith(h[:500]) or h.startswith(text[:500]) for h in hints)
        turns.append(ConversationTurn(
            turn_id=f"msg_{i:04d}",
            role=role,
            text=text,
            tier=tiers.get(i),
            tension_hint=hint,
        ))
    return turns


def select_window_turns(
    turns: list[ConversationTurn],
    before: int = WINDOW_BEFORE,
    after: int = WINDOW_AFTER,
    max_turns: int = MAX_WINDOW_TURNS,
) -> list[ConversationTurn]:
    """tension_hint 発話を核に前後 ±before/after 往復を切り出す（近接ヒントは結合）。

    ヒントが無い場合は末尾 max_turns を返す（セッション終了トリガーの保険）。
    """
    if not turns:
        return []
    hint_indices = [i for i, t in enumerate(turns) if t.tension_hint]
    if not hint_indices:
        return turns[-max_turns:]

    # 往復（user+assistant ≒ 2 turn）単位の前後幅を turn 数に換算して区間を作り、重なりを結合
    span = []
    for i in hint_indices:
        lo = max(0, i - before * 2)
        hi = min(len(turns), i + after * 2 + 1)
        if span and lo <= span[-1][1]:
            span[-1] = (span[-1][0], max(span[-1][1], hi))
        else:
            span.append((lo, hi))
    selected: list[ConversationTurn] = []
    for lo, hi in span:
        selected.extend(turns[lo:hi])
    # 窓上限: 最新側（＝最後のヒント周辺）を優先して残す
    if len(selected) > max_turns:
        selected = selected[-max_turns:]
    return selected


def _format_turn(turn: ConversationTurn, tutor_limit: int) -> str:
    attrs = f"[turn id={turn.turn_id} role={turn.role}"
    if turn.role == "tutor" and turn.tier:
        attrs += f" tier={turn.tier}"
    if turn.role == "learner" and turn.tension_hint:
        attrs += " tension_hint=true"
    attrs += "]"
    text = turn.text
    if turn.role == "tutor" and len(text) > tutor_limit:
        text = text[:tutor_limit] + "…"
    return f"{attrs} {text}"


def build_user_content(window: ConversationWindow, max_candidates: int) -> str:
    """会話窓 → LLM user コンテンツ（設計書 §5.2 のフォーマット）。

    文字数上限超過時は tutor 応答を先に短縮し、それでも超えるなら古い turn から落とす。
    """
    def render(turns: list[ConversationTurn], tutor_limit: int) -> str:
        head = (
            "## Session\n"
            f"course_id: {window.course_id}\n"
            f"topic_id: {window.topic_id}\n"
            f"topic_title: {window.topic_title}\n\n"
            "## Context blocks (ids you may reference in target_refs)\n"
            f"components: {json.dumps(window.components, ensure_ascii=False)}\n"
            f"chunks:     {json.dumps(window.chunks, ensure_ascii=False)}\n\n"
            "## Conversation window (chronological)\n"
        )
        body = "\n".join(_format_turn(t, tutor_limit) for t in turns)
        tail = f"\n\n## Task\nDetect tension candidates per the rules. max_candidates = {int(max_candidates)}\n"
        return head + body + tail

    turns = list(window.turns)
    content = render(turns, TUTOR_TEXT_LIMIT)
    if len(content) > MAX_INPUT_CHARS:
        content = render(turns, TUTOR_TEXT_LIMIT_TIGHT)
    while len(content) > MAX_INPUT_CHARS and len(turns) > 2:
        turns = turns[1:]  # 古い turn から落とす（ヒントは新しい側に寄っている）
        content = render(turns, TUTOR_TEXT_LIMIT_TIGHT)
    return content
