"""審判 B — 原則（決定論・製品の正本を**参照**する — PE6）。

学習者に見える文（学習側の行為の応答のうち、表示に使われる欄）について:

1. 数値・パーセント・件数・点数（vision 原則 4）
2. 学生向けの禁止語彙 — ``core/help_kb/validator.py::STUDENT_DENYLIST``
3. 内部 ID — ``core/learner_context_common.py::contains_internal_id`` + 代表的な接頭辞
4. 表示ラベルの生 TeX — ``core/text_excerpt.py::looks_like_tex_math``
5. 制御文字・ANSI 残骸 — ``core/text_hygiene.py::strip_control_sequences`` を掛けて差分があれば

限界（意図して単純にしている）:
- 数値の検査は「数字 + 件 / 回 / 点 / スコア」と「スコア / 正答率 / 一致度 / 理解度 / 進捗 + 数字（% を含む）」
  だけを見る。式番号（式 (12)）・年（2026年）・arXiv ID・「第 N 回」は除外する。本文中の物理量（z = 0.5 等）は
  数値の欠陥として扱わない（単位語が付かないので当たらない）。**裸の百分率（68% CL・1% 精度）は論文の内容**
  なので当てない（第 11 周で A/B の誤検出 6 件の原因 — 製品指標の百分率は必ず指標語を伴う）。
- 表示欄の判定はキー名による近似（``DISPLAY_KEYS``）。UI が実際に描かない欄も入りうる。
- 応答の抜粋は先頭 2000 字なので、それより後ろの露出は見えない（browser runner が補う）。
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any, Callable, Iterator

from uxsim.config import REPO_ROOT
from uxsim.oracles.findings import FindingFactory, excerpt_json
from uxsim.schema import Finding, TranscriptStep

if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from core.help_kb.validator import STUDENT_DENYLIST  # noqa: E402  製品の正本（読み取りのみ）
from core.learner_context_common import contains_internal_id  # noqa: E402
from core.text_excerpt import looks_like_tex_math  # noqa: E402
from core.text_hygiene import strip_control_sequences  # noqa: E402

DISPLAY_KEYS = frozenset({
    "answer", "label", "title", "text", "fact_line", "facts", "notice", "statement", "statements", "description",
    "summary", "message", "detail", "quote", "body", "reason", "name", "caption", "note", "paraphrase",
    "question", "explanation", "model_answer", "hint", "node_label", "region_label", "anchor_label",
    "doubt_type_label", "status_label", "latex_note", "label_note", "subject", "heading",
})
NUMBER_KEYS = frozenset({"answer", "facts", "fact_line", "notice", "statements", "statement", "message", "note",
                         "status_label", "summary"})
LABEL_KEYS = frozenset({"label", "title", "name", "node_label", "region_label", "anchor_label"})

_NUMBER_WITH_UNIT = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*(?:件|回|点|スコア)")
_SCORE_WORD = re.compile(r"(?:スコア|得点|正答率|一致度|理解度|進捗|達成率)\s*[:：は]?\s*\d+(?:\.\d+)?\s*[%％]?")
_EXCLUDE = re.compile(r"第\s*\d+\s*回|式\s*[（(]\s*\d+|\b(?:19|20)\d{2}\s*年|\b\d{4}\.\d{4,5}(?:v\d+)?")
_ID_PATTERNS = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\beq_op_\w+|\btheory_op_\w+|\bev_\w{3,}"
    r"|\bk1:[0-9a-f]{6,}", re.IGNORECASE)
_KV_RE = re.compile(r'"([A-Za-z_]+)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def display_texts(body: Any, excerpt: str = "") -> Iterator[tuple[str, str]]:
    """(キー, 文) を返す。body が取れないとき（抜粋が途中で切れた）は正規表現で拾う。"""
    if body is None:
        for m in _KV_RE.finditer(excerpt or ""):
            if m.group(1) in DISPLAY_KEYS:
                try:
                    yield m.group(1), json.loads(f'"{m.group(2)}"')
                except json.JSONDecodeError:
                    yield m.group(1), m.group(2)
        return

    def walk(v: Any, key: str) -> Iterator[tuple[str, str]]:
        if isinstance(v, dict):
            for k, x in v.items():
                yield from walk(x, k)
        elif isinstance(v, list):
            for x in v:
                yield from walk(x, key)
        elif isinstance(v, str) and key in DISPLAY_KEYS and v.strip():
            yield key, v

    yield from walk(body, "")


def _numbers(text: str) -> str:
    cleaned = _EXCLUDE.sub(" ", text)
    m = _NUMBER_WITH_UNIT.search(cleaned) or _SCORE_WORD.search(cleaned)
    return m.group(0) if m else ""


def _internal_id(text: str) -> str:
    m = _ID_PATTERNS.search(text)
    if m:
        return m.group(0)
    for token in re.split(r"[\s、。，,「」()（）]+", text):
        if token and contains_internal_id(token):
            return token
    return ""


Check = Callable[[str, str], str]


def _checks() -> list[tuple[str, str, Check]]:
    return [
        ("numbers", "学習者に数値（件数・回数・点数・割合）が見えている",
         lambda k, t: _numbers(t) if k in NUMBER_KEYS else ""),
        ("denylist", "学習者向けの文に内部名（学生向け禁止語彙）が出ている",
         lambda k, t: next((w for w in STUDENT_DENYLIST if w in t), "")),
        ("internal_id", "学習者向けの文に内部 ID が出ている", lambda k, t: _internal_id(t)),
        ("raw_tex", "表示ラベルに生の TeX が出ている",
         lambda k, t: t[:80] if k in LABEL_KEYS and looks_like_tex_math(t) else ""),
        ("control", "学習者向けの文に制御文字・ANSI の残骸が混じっている",
         lambda k, t: repr(t[:60]) if strip_control_sequences(t) != t else ""),
    ]


def check(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    checks = _checks()
    for step in steps:
        if step.screen != "learning" or step.action_id in ("auth.login",) or step.action_id.startswith("unsupported"):
            continue
        for t in step.http:
            if t.status is None or t.status >= 500 or not t.response_excerpt:
                continue
            body = excerpt_json(t.response_excerpt)
            seen: set[str] = set()
            for key, text in display_texts(body, t.response_excerpt):
                for name, hypothesis, fn in checks:
                    hit = fn(key, text)
                    if hit and name not in seen:
                        seen.add(name)
                        out.append(factory.make(oracle="B", severity="principle", step=step,
                                                quote=f"{key}: {hit}",
                                                hypothesis=f"{hypothesis}（{step.action_id}）"))
    return out
