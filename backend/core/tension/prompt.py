"""Stage 1 — 検出プロンプト定義（設計書 §5）。

規約準拠: 英語 instruction 本文、`system` ロール・`temperature` 不使用
（メッセージは user ロール1本に連結し core.llm.generate_text 経由で呼ぶ）。
学習者発話は日本語のまま渡し、paraphrase は学習者の言語で出力させる。
"""

from __future__ import annotations

_INSTRUCTION = """\
You are analyzing a tutoring conversation between a graduate student (learner)
and an AI tutor, to detect CANDIDATE "tensions" — moments where the learner
seems to feel that something is subtly off, inconsistent, or unsatisfying
about the material, EVEN THOUGH they may already understand it.

Your task is NOT to find what the learner failed to understand.
A comprehension gap ("I don't get X") is NOT a tension.
A tension is: "I follow the explanation, but something still feels wrong."

## Why this matters
Tensions are the raw material of new abstraction. They must be surfaced
gently and confirmed by the learner themself. You only PROPOSE candidates;
you never assert the learner's mental state.

## Discrimination criteria (tension vs. NOT tension)

Signals FOR a tension (any of):
- Hedged unease after a correct restatement: 「なんとなく気持ち悪い」「腑に落ちない」
- Repeated return to the same concept across turns despite adequate answers
- Producing a counterexample or edge case unprompted: 「でも〜の場合はどうなる?」
- Probing an unstated assumption: 「そもそもこれは〜を仮定していますよね」
- Sensing a cross-domain resemblance: 「これは〜に似ている気がする」
- Doubting the source itself, not their own understanding: 「論文のこの主張、本当に?」
- Absence of acknowledgement (no なるほど/わかりました) after a well-grounded answer,
  followed by a reformulated version of the SAME question

Signals AGAINST (classify as NOT tension; do not emit a candidate):
- Pure information requests or definition lookups
- Explicit statements of not understanding: 「わかりません」「意味が取れない」
  → these belong to the existing question/misconception pipeline
- Procedural/UI questions, small talk, scheduling
- Satisfaction expressed and the thread genuinely closed
- The tutor's own hedging (only LEARNER utterances can ground a tension)

## Tension taxonomy (tension_type)
- contradiction_sense : two explanations/sources feel mutually inconsistent to the learner
- definition_unease   : the learner can restate the definition but it does not "sit right"
- boundary_probe      : the learner keeps testing the edges of applicability/validity
- analogy_itch        : the learner senses a structural resemblance to something else
- source_doubt        : the learner doubts the material/paper itself
- unstated_assumption : the learner is disturbed by a hidden premise
- unclassified        : clearly a tension, but none of the above fits (keep it; do not drop)

## Hard output rules
1. Return ONLY valid JSON matching the schema below. No prose, no markdown fences.
2. evidence_quote MUST be a verbatim substring of a LEARNER utterance in the input
   (copy characters exactly; do not translate, trim only whitespace at ends).
3. turn_ids MUST reference turn ids present in the input.
4. paraphrase MUST be written in the learner's language, in a tentative,
   non-assertive register. In Japanese end with 「〜かもしれません」 or
   「〜ように見えます」. In English use a tentative form such as "may", "might",
   "seems" or "perhaps". NEVER use assertive forms like 「〜と感じています」
   「あなたは〜だ」 or "you feel", "definitely". Max 120 characters. Address the
   tension, not the learner's ability.
5. is_tension_not_gap: set false and DO NOT emit the entry as a candidate if the
   moment is a comprehension gap; instead list it under rejected_as_gap with a reason.
6. Emit AT MOST {max_candidates} candidates, ranked by confidence descending.
   Prefer precision over recall: an empty candidates list is a correct answer
   when nothing qualifies.
7. confidence calibration: 0.9+ only when multiple independent signals co-occur
   across turns; a single hedge word alone must not exceed 0.5.
8. target_refs: only ids present in the provided context blocks. Never invent ids.
   If no id applies, use empty lists and set topic_id from the session header.
9. reason: cite the concrete signals (which markers, which turns, what pattern).

## Output JSON schema
{
  "candidates": [
    {
      "tension_type": "<taxonomy id>",
      "evidence_quote": "<verbatim learner substring>",
      "turn_ids": ["<id>", ...],
      "target_refs": {"component_ids": [], "chunk_ids": [], "topic_id": "", "edge_ids": []},
      "is_tension_not_gap": true,
      "paraphrase": "<tentative sentence in learner's language>",
      "confidence": 0.0,
      "reason": "<signals cited>"
    }
  ],
  "rejected_as_gap": [
    {"turn_ids": ["<id>"], "reason": "<why this is a comprehension gap, not a tension>"}
  ]
}
"""

# Few-shot 3例（examples/ と同一ソース）。
# IK-0474: 例の turn id は実会話の ``msg_{index:04d}`` と衝突しない形（``example_*``）にする
# （実会話の msg_0010 / msg_0011 と同じ id だと、モデルが例の発話を実会話の発話と取り違える）。
# (b)(c) の陰性例は「空出力が正解になり得る」ことをモデルに固定するために入れる
# （過検出がこのエージェント最大の失敗モード）。
_FEW_SHOT = """\

## Worked examples

(The turn ids below such as `example_a_1` belong to these examples only. They never appear in the Session; cite only turn ids that appear in the Session.)

### Example (a) — positive: definition_unease
Input excerpt:
[turn id=example_a_1 role=learner] 有効場理論のカットオフの定義はわかりました。高エネルギーの自由度を積分で落とすんですよね。
[turn id=example_a_2 role=tutor tier=approved] その通りです。（説明…）
[turn id=example_a_3 role=learner tension_hint=true] 定義は復唱できるんですが、カットオフの選び方が恣意的に思えて、なんとなく腑に落ちないんです。
[turn id=example_a_4 role=learner tension_hint=true] さっきのカットオフの話に戻るんですが、物理量が本当に依存しないと言い切れるんでしょうか。
Correct output:
{
  "candidates": [
    {
      "tension_type": "definition_unease",
      "evidence_quote": "定義は復唱できるんですが、カットオフの選び方が恣意的に思えて、なんとなく腑に落ちないんです。",
      "turn_ids": ["example_a_3", "example_a_4"],
      "target_refs": {"component_ids": [], "chunk_ids": [], "topic_id": "t_01", "edge_ids": []},
      "is_tension_not_gap": true,
      "paraphrase": "定義を理解した上で、カットオフの恣意性に引っかかりが残っているのかもしれません",
      "confidence": 0.82,
      "reason": "Correct restatement (example_a_1, example_a_3) followed by hedged unease markers 「なんとなく」「腑に落ちない」 (example_a_3) and a revisit of the same concept without acknowledgement (example_a_4)."
    }
  ],
  "rejected_as_gap": []
}

### Example (b) — negative: comprehension gap (NOT a tension)
Input excerpt:
[turn id=example_b_1 role=learner] 群論がまだ全然わからないので、リー群の定義から教えてください。
Correct output:
{
  "candidates": [],
  "rejected_as_gap": [
    {"turn_ids": ["example_b_1"], "reason": "Explicit statement of not understanding (「わからない」) with a definition lookup request; belongs to the question/misconception pipeline, not a tension."}
  ]
}

### Example (c) — negative: satisfied close
Input excerpt:
[turn id=example_c_1 role=learner] 繰り込み群方程式の導出はどうやるんですか。
[turn id=example_c_2 role=tutor tier=source] （導出の説明…）
[turn id=example_c_3 role=learner] なるほど、納得しました。ありがとうございます。
Correct output:
{
  "candidates": [],
  "rejected_as_gap": []
}
"""


def build_instruction(max_candidates: int) -> str:
    """instruction 本文 + few-shot。JSON 例に波括弧を含むため str.format は使わない。"""
    return _INSTRUCTION.replace("{max_candidates}", str(int(max_candidates))) + _FEW_SHOT


def build_repair_prompt(previous_output: str, errors: list[str]) -> str:
    """validation 失敗時の修復プロンプト（設計書 §7、A層と同方式）。

    直前の出力が読めなかった（空・JSON 以外）うえに、それより前に読めた出力も無いときは、
    空の「Your previous output」区画を渡さない（「違反だけを直せ」と言いながら直す対象を
    渡さない矛盾を避ける — IK-0402）。その場合は最初から出力し直すよう指示する。
    """
    error_lines = "\n".join(f"- {e}" for e in errors)
    if not str(previous_output or "").strip():
        return (
            "Your previous output violated these rules:\n"
            f"{error_lines}\n"
            "No readable previous output was received, so produce the full JSON again "
            "from the conversation above, following all rules. Return the JSON only."
        )
    return (
        "Your previous output violated these rules:\n"
        f"{error_lines}\n"
        "Fix ONLY the violations. Do not add new candidates. "
        "Return the corrected JSON only.\n\n"
        "## Your previous output\n"
        f"{previous_output}"
    )
