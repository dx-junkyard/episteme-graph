"""確認問題の並置（DIFF）と自己確認の語彙 — 同期・非LLM・純関数。

是正 F1（`docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md` §4 第1波 #1、
出所は `docs/architecture/six_lenses_2026-09-10/01_learner.md` 提案2 /
`05_ai.md` 提案1）: 確認問題の合否を AI の確定から外し、
「要件との並置 + 開示（REVEAL） + 本人の1タップ自己確認」に分解する。

- **合否を生成しない**: ここには `passed` に相当する語彙が無い。LLM が返すのは
  要件ごとの観点（触れられている / 触れられていない可能性）だけで、進行の確定は
  本人の self-check（`SELF_CHECK_VALUES`）に移る。
- **新しい比較器を作らない**: 要件と回答の突き合わせは LLM の読みに委ね、ここでは
  閉世界（`answer_requirements`）への照合と文体の組み立てだけを決定論的に行う。
  文体は R層 REFLECT（`core/reconstruction/diff.py::build_reflection`）に揃える。
- **判定語彙を通さない**: LLM の観点文に合否・採点語彙が混じっていたらその文を落とし、
  決定論の事実文へ縮退する（数値・点数・達成度は元から禁止）。

FastAPI / LLM を import しない（テスタビリティ確保・開発ルール2）。
"""

from __future__ import annotations

import re
from typing import Any

from core.reconstruction.schema import SELF_CHECK_VALUES  # noqa: F401  (re-export)

# --- 語彙 -----------------------------------------------------------------

#: 回答で触れられているようだ（LLM の読み）。
OBSERVATION_COVERED = "covered"
#: 回答で触れられていない可能性がある（LLM の読み）。
OBSERVATION_NOT_MENTIONED = "not_mentioned"
#: 読み取れなかった / LLM が語彙外を返した。情報を落とさず保持する（P4）。
OBSERVATION_UNCLEAR = "unclear"

OBSERVATION_STATUSES = (OBSERVATION_COVERED, OBSERVATION_NOT_MENTIONED, OBSERVATION_UNCLEAR)

#: 観点は最大3点（押し付けない・読む量を増やさない）。
MAX_OBSERVATIONS = 3

#: 学習者の回答を事実文に差し込むときの上限（全文は入力欄にそのまま残る）。
_ANSWER_EXCERPT_CHARS = 300

#: LLM の観点提示が得られなかったときの固定文（判定を生まない縮退・原則9）。
DEGRADED_STATEMENT = (
    "AI の観点提示ができませんでした。出題の要件と自分の回答を見比べてください。"
)

#: 観点文に混じってはならない判定・採点語彙。1つでも含む文は落とす。
VERDICT_DENYLIST = (
    "合格", "不合格", "正解", "不正解", "採点", "点数", "正答率", "達成率", "達成度",
    "得点", "スコア",
)

#: 自己確認の問いかけと3択のラベル（R層 SELF-CHECK と同型・同文言）。
SELF_CHECK_QUESTION = "あなたの見立てはどうでしたか？"
SELF_CHECK_LABELS = {
    "agreed": "合っていた",
    "disagreed": "違っていた",
    "verdict_wrong": "観点がおかしい",
}

#: 自己確認のうち「本人が見比べて先へ進むと決めた」もの（完了の確定に使う）。
SELF_CHECK_ADVANCING = ("agreed", "disagreed")


# --- 純関数 ---------------------------------------------------------------


#: 「80点」「70%」のような採点表記（語彙だけでは拾えないので数値パターンでも落とす）。
_SCORE_PATTERN = re.compile(r"\d+\s*(?:点|%|％)")


def sanitize_statement(text: object) -> str:
    """観点文を検査する。判定・採点語彙を含む文は空にする（採点に戻さない）。"""
    s = str(text or "").strip()
    if not s:
        return ""
    for word in VERDICT_DENYLIST:
        if word in s:
            return ""
    if _SCORE_PATTERN.search(s):
        return ""
    return s


def _match_requirement(value: str, answer_requirements: list[str]) -> str:
    """要件名を閉世界（出題の answer_requirements）へ照合する。

    リスト外の値は空文字にする（LLM が要件を創作しても、要件として表示しない。
    `paper_discovery/compare.py` の component_label と同じ扱い）。要件が未設定の
    出題では閉世界が無いので、LLM の表記をそのまま通す。
    """
    v = (value or "").strip()
    if not v:
        return ""
    if not answer_requirements:
        return v
    for req in answer_requirements:
        if v == req:
            return req
    low = v.casefold()
    for req in answer_requirements:
        if low == req.casefold():
            return req
    return ""


def normalize_observations(
    raw: object,
    answer_requirements: list[str],
    *,
    limit: int = MAX_OBSERVATIONS,
) -> list[dict[str, str]]:
    """LLM の観点候補を `{requirement, status, statement}` の列へ正規化する。

    - status が語彙外・欠落なら `unclear`（合否側へ倒さない）
    - requirement は閉世界照合（リスト外は空文字。statement は保持する）
    - statement に判定語彙が混じっていれば落とす（項目自体は残す）
    - 上限 `limit` 件で打ち切る
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if len(out) >= max(0, limit):
            break
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip()
        if status not in OBSERVATION_STATUSES:
            status = OBSERVATION_UNCLEAR
        requirement = _match_requirement(
            str(item.get("requirement") or item.get("element") or ""), answer_requirements,
        )
        statement = sanitize_statement(item.get("statement") or item.get("note"))
        if not requirement and not statement:
            continue
        out.append({"requirement": requirement, "status": status, "statement": statement})
    return out


def split_observations(observations: list[dict[str, str]]) -> tuple[list[str], list[str]]:
    """観点から `covered` / `not_mentioned` の要件名の列を取り出す（表示順を保つ）。"""
    covered: list[str] = []
    not_mentioned: list[str] = []
    for obs in observations:
        req = str(obs.get("requirement") or "").strip()
        if not req:
            continue
        status = obs.get("status")
        if status == OBSERVATION_COVERED and req not in covered:
            covered.append(req)
        elif status == OBSERVATION_NOT_MENTIONED and req not in not_mentioned:
            not_mentioned.append(req)
    return covered, not_mentioned


def _answer_excerpt(answer: str) -> str:
    text = (answer or "").strip()
    if len(text) <= _ANSWER_EXCERPT_CHARS:
        return text
    return text[:_ANSWER_EXCERPT_CHARS] + "…"


def build_statements(
    answer: str,
    observations: list[dict[str, str]],
    *,
    degraded: bool = False,
) -> list[str]:
    """並置の事実文を組み立てる（R層 REFLECT の文体）。

    合否・点数・順位・煽り文句を含めない。最後の一文で「判断は本人」と明示する。
    """
    if degraded:
        return [DEGRADED_STATEMENT]

    statements: list[str] = []
    excerpt = _answer_excerpt(answer)
    if excerpt:
        statements.append("あなたは「" + excerpt + "」と述べました。")

    covered, not_mentioned = split_observations(observations)
    if covered:
        statements.append(
            "出題が求めている「" + "、".join(covered) + "」への言及が、あなたの回答にあるようです。"
        )
    if not_mentioned:
        statements.append(
            "出題が求めている「" + "、".join(not_mentioned)
            + "」への言及は、あなたの回答には見当たらないようです。"
        )
    if not covered and not not_mentioned:
        statements.append("出題の要件と自分の回答を並べて、どこが違うかご自身で見比べてみてください。")

    statements.append("解答例と解説を読んだうえで、次に進むかどうかはご自身で決めてください。")
    return statements


def parsed_observations(parsed: dict[str, Any], answer_requirements: list[str]) -> list[dict[str, str]]:
    """LLM の JSON（`observations` キー）から観点列を取り出す薄いヘルパ。"""
    if not isinstance(parsed, dict):
        return []
    return normalize_observations(parsed.get("observations"), answer_requirements)
