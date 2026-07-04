"""Stage 2(B) — 帰属プロンプト定義。

規約準拠: 英語 instruction 本文、`system` ロール・`temperature` 不使用
（メッセージは user ロール1本に連結し core.llm.generate_text 経由で呼ぶ）。
質問文は日本語のまま渡し、evidence_quote は逐語で出力させる。
"""

from __future__ import annotations

_INSTRUCTION = """\
You are analyzing questions a graduate student asked an AI tutor while studying
course material. For EACH question, determine:

1. WHERE in the presented information structure the question is anchored
   (anchor_type + anchor_id chosen ONLY from the provided candidate blocks), and
2. HOW the learner is stuck (doubt_type).

You only PROPOSE attributions; the learner confirms or corrects them later.
Never assert the learner's mental state.

## Granularity rule (degrade, do not invent)
Prefer the finest granularity that the question clearly refers to:
claim / equation / derivation_step > concept > stage > chunk > segment.
If the material has no fine structure (e.g. course-builder topics), degrade to a
coarser anchor (chunk, then segment). Never force a fine anchor you are not
confident about. If NO candidate anchor applies at all, put the question under
"unattributed" with a reason (do not drop it silently).

## Doubt taxonomy (doubt_type)
- definition        : the meaning of a term / symbol is unclear
- justification_gap : why does this hold (a leap in the derivation)
- premise           : doubt about the premise itself
- prior_conflict    : conflict with the learner's prior knowledge
- scope             : how far does this hold (range of applicability)
- connection        : how does this connect to other concepts
- unclassified      : clearly anchored somewhere, but the mode of doubt is unclear

## Hard output rules
1. Return ONLY valid JSON matching the schema below. No prose, no markdown fences.
2. At most ONE anchor per question. Each trace_id may appear at most once in
   "anchors"; every input trace_id must appear in either "anchors" or "unattributed".
3. anchor_id MUST be one of the ids in the candidate blocks for that anchor_type
   (for anchor_type="stage" use one of the stage vocabulary strings verbatim).
   Never invent ids.
4. evidence_quote MUST be a verbatim substring of THAT question's text
   (copy characters exactly; do not translate; trim only whitespace at ends).
5. doubt_type MUST be one of the taxonomy ids. Use "unclassified" rather than
   guessing (do not drop the question).
6. confidence calibration: 0.9+ only when the question explicitly names the
   anchored element; anchors inferred only from topical overlap must not
   exceed 0.6; degraded coarse anchors (chunk/segment) must not exceed 0.7.
7. reason: cite the concrete phrases in the question and the matching candidate
   (which words point to which anchor).
8. anchor_label: a short human-readable label for the anchor (<= 40 chars),
   taken from the candidate block text; do not write full sentences.

## Output JSON schema
{
  "anchors": [
    {
      "trace_id": "<input trace_id>",
      "anchor_type": "claim | equation | derivation_step | concept | stage | chunk | segment",
      "anchor_id": "<id from candidate blocks>",
      "anchor_label": "<short label>",
      "doubt_type": "<taxonomy id>",
      "evidence_quote": "<verbatim substring of the question text>",
      "confidence": 0.0,
      "reason": "<signals cited>"
    }
  ],
  "unattributed": [
    {"trace_id": "<input trace_id>", "reason": "<why no candidate anchor applies>"}
  ]
}
"""

# Few-shot（陽性: 細粒度 / 陽性: 縮退 / 陰性: unattributed を各1例）。
# 「縮退してよい」「unattributed が正解になり得る」ことをモデルに固定する
# （無理な細粒度帰属＝id 捏造がこのエージェント最大の失敗モード）。
_FEW_SHOT = """\

## Worked examples

### Example (a) — fine-grained: derivation_step + justification_gap
Candidate blocks include:
derivation_steps: [{"id": "clm_042", "text": "線形化により高次項を無視して式(12)を得る"}]
Input question:
[question trace_id=tr_001] 式(12)の線形化で高次項を無視できるのはなぜですか。摂動が小さいという仮定だけで正当化できるのでしょうか。
Correct output:
{
  "anchors": [
    {
      "trace_id": "tr_001",
      "anchor_type": "derivation_step",
      "anchor_id": "clm_042",
      "anchor_label": "線形化により高次項を無視",
      "doubt_type": "justification_gap",
      "evidence_quote": "高次項を無視できるのはなぜですか",
      "confidence": 0.86,
      "reason": "The question explicitly names the linearization step (「式(12)の線形化」「高次項を無視」) matching clm_042, and asks WHY it holds (「なぜですか」「正当化できる」)."
    }
  ],
  "unattributed": []
}

### Example (b) — degraded: segment + definition (no fine structure available)
Candidate blocks include:
claims: []
segments: [{"id": "seg_3", "label": "第2章 · 熱力学第二法則"}]
Input question:
[question trace_id=tr_002 segment_id=seg_3] ここに出てくるエントロピーという言葉の意味がまだよくわかりません。
Correct output:
{
  "anchors": [
    {
      "trace_id": "tr_002",
      "anchor_type": "segment",
      "anchor_id": "seg_3",
      "anchor_label": "第2章 · 熱力学第二法則",
      "doubt_type": "definition",
      "evidence_quote": "エントロピーという言葉の意味がまだよくわかりません",
      "confidence": 0.62,
      "reason": "No claim/concept candidates exist; the question points at the current segment (「ここに出てくる」, segment_id=seg_3) and asks for the meaning of a term (definition)."
    }
  ],
  "unattributed": []
}

### Example (c) — unattributed (no candidate applies)
Candidate blocks include:
segments: [{"id": "seg_1", "label": "序論"}]
Input question:
[question trace_id=tr_003] 来週の課題の締め切りはいつですか。
Correct output:
{
  "anchors": [],
  "unattributed": [
    {"trace_id": "tr_003", "reason": "Administrative question about a deadline; it does not refer to any element of the presented material."}
  ]
}
"""


def build_instruction() -> str:
    """instruction 本文 + few-shot（JSON 例に波括弧を含むため str.format は使わない）。"""
    return _INSTRUCTION + _FEW_SHOT


def build_repair_prompt(previous_output: str, errors: list[str]) -> str:
    """validation 失敗時の修復プロンプト（tension / A層と同方式）。"""
    error_lines = "\n".join(f"- {e}" for e in errors)
    return (
        "Your previous output violated these rules:\n"
        f"{error_lines}\n"
        "Fix ONLY the violations. Do not add new anchors. "
        "Return the corrected JSON only.\n\n"
        "## Your previous output\n"
        f"{previous_output}"
    )
