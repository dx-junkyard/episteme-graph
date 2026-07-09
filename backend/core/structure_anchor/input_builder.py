"""Stage 2(B) — LLM 入力構築（非LLM・純関数）。

構築規則:
- 1コール = 1トピック分の未帰属の問いバッチ + アンカー候補ブロック。
- アンカー候補は実在 id のみ（id 捏造防止。tension の context blocks と同方式）。
- 入力上限 ~8k tokens 近似（超過時は候補ブロックの text を短縮 → 古い問いから落とす）。
"""

from __future__ import annotations

import json

from core.structure_anchor.schema import AnchorContext, QuestionRecord

# 候補ブロック各行の text/head 切り詰め長
BLOCK_TEXT_LIMIT = 160
BLOCK_TEXT_LIMIT_TIGHT = 60
# 入力上限 ~8k tokens の近似（日本語 1 token ≒ 1.5 字として文字数で管理）
MAX_INPUT_CHARS = 12_000
# 1バッチで扱う問いの上限（多すぎる場合は新しい側を優先）
MAX_QUESTIONS = 10


def _trim_block(items: list[dict], limit: int) -> list[dict]:
    out = []
    for b in items:
        b2 = dict(b)
        for key in ("text", "head", "label"):
            if isinstance(b2.get(key), str) and len(b2[key]) > limit:
                b2[key] = b2[key][:limit] + "…"
        out.append(b2)
    return out


def _format_question(q: QuestionRecord) -> str:
    attrs = f"[question trace_id={q.trace_id}"
    if q.segment_id is not None:
        attrs += f" segment_id=seg_{int(q.segment_id)}"
    if q.cited_chunk_ids:
        attrs += f" cited_chunks={','.join(q.cited_chunk_ids[:3])}"
    attrs += "]"
    return f"{attrs} {q.text}"


def build_user_content(context: AnchorContext) -> str:
    """AnchorContext → LLM user コンテンツ。

    文字数上限超過時は候補ブロックを先に短縮し、それでも超えるなら古い問いから落とす
    （問いは新しい側を優先して残す）。
    """
    def render(questions: list[QuestionRecord], block_limit: int) -> str:
        head = (
            "## Session\n"
            f"course_id: {context.course_id}\n"
            f"topic_id: {context.topic_id}\n"
            f"topic_title: {context.topic_title}\n\n"
            "## Anchor candidates (the ONLY ids you may reference; never invent ids)\n"
            f"stages:           {json.dumps(list(context.stages), ensure_ascii=False)}\n"
            f"claims:           {json.dumps(_trim_block(context.claims, block_limit), ensure_ascii=False)}\n"
            f"equations:        {json.dumps(_trim_block(context.equations, block_limit), ensure_ascii=False)}\n"
            f"derivation_steps: {json.dumps(_trim_block(context.derivation_steps, block_limit), ensure_ascii=False)}\n"
            f"concepts:         {json.dumps(_trim_block(context.concepts, block_limit), ensure_ascii=False)}\n"
            f"chunks:           {json.dumps(_trim_block(context.chunks, block_limit), ensure_ascii=False)}\n"
            f"segments:         {json.dumps(_trim_block(context.segments, block_limit), ensure_ascii=False)}\n\n"
            "## Learner questions (attribute each one)\n"
        )
        body = "\n".join(_format_question(q) for q in questions)
        tail = "\n\n## Task\nAttribute each question per the rules. At most ONE anchor per question.\n"
        return head + body + tail

    questions = list(context.questions)[-MAX_QUESTIONS:]
    content = render(questions, BLOCK_TEXT_LIMIT)
    if len(content) > MAX_INPUT_CHARS:
        content = render(questions, BLOCK_TEXT_LIMIT_TIGHT)
    while len(content) > MAX_INPUT_CHARS and len(questions) > 1:
        questions = questions[1:]  # 古い問いから落とす
        content = render(questions, BLOCK_TEXT_LIMIT_TIGHT)
    return content
