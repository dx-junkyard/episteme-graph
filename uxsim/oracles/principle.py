"""審判 B — 原則（決定論・製品の正本を**参照**する — PE6）。

学習者に見える文（学習側の行為の応答のうち、表示に使われる欄）について:

1. 数値・パーセント・件数・点数（vision 原則 4）
2. 学生向けの禁止語彙 — ``core/help_kb/validator.py::STUDENT_DENYLIST``
3. 内部 ID — ``core/learner_context_common.py::contains_internal_id`` + 代表的な接頭辞
4. 表示ラベルの生 TeX — ``$…$`` 区切りの外に残る TeX 命令と ``\\(`` ``\\[`` の残骸（区切り付きは KaTeX が描く）
5. 制御文字・ANSI 残骸 — ``core/text_hygiene.py::strip_control_sequences`` を掛けて差分があれば
6. 言語の一致 — 事実文・ラベル（facts / fact_line / label / notice）に、日本語話者なら英語の文、英語話者なら
   日本語の固定文（ペルソナファイルの ``language``）
7. 採点しない面（cycle / 確認問題 / 再構成 / cycle_mode=diff）に正誤・点数の語彙 — 語彙は製品のガードレール
   テスト（``test_reconstruction_guardrails.py::TestNoRawScoreToLearners.BANNED`` /
   ``test_understanding_cycle_ui_static.py::TestDiffHasNoGradingVocabulary.FORBIDDEN``）から ast で読む
8. 教員向け理論モジュール・論文層に内部の量 — ``theory_modules.schema.FORBIDDEN_KEYS`` ∪
   ``graph_paper_layer.schema.FORBIDDEN_KEYS`` のキーと構造の指紋（``RULE_VERSION|``）
内部 ID は製品の ``contains_internal_id`` と ``theory_modules.schema.INTERNAL_ID_RE`` に加え、それらが拾わない
接頭辞（``INTERNAL_ID_PREFIXES``）を字句の前方一致で見る。

限界（意図して単純にしている）:
- 数値の検査は「数字 + 件 / 回 / 点 / スコア」と「スコア / 正答率 / 一致度 / 理解度 / 進捗 + 数字（% を含む）」
  だけを見る。式番号（式 (12)）・年（2026年）・arXiv ID・「第 N 回」は除外する。本文中の物理量（z = 0.5 等）は
  数値の欠陥として扱わない（単位語が付かないので当たらない）。**裸の百分率（68% CL・1% 精度）は論文の内容**
  なので当てない（第 11 周で A/B の誤検出 6 件の原因 — 製品指標の百分率は必ず指標語を伴う）。
- 表示欄の判定はキー名による近似（``DISPLAY_KEYS``）。UI が実際に描かない欄も入りうる。
- 応答の抜粋は先頭 2000 字なので、それより後ろの露出は見えない（browser runner が補う）。
"""
from __future__ import annotations

import ast
import json
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from uxsim.config import REPO_ROOT
from uxsim.oracles.findings import FindingFactory, excerpt_json
from uxsim.schema import Finding, TranscriptStep

if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from core.help_kb.validator import STUDENT_DENYLIST  # noqa: E402  製品の正本（読み取りのみ）
from core.learner_context_common import contains_internal_id  # noqa: E402
from core.text_hygiene import strip_control_sequences  # noqa: E402
from core.theory_modules.schema import FORBIDDEN_KEYS as MODULE_FORBIDDEN_KEYS  # noqa: E402
from core.theory_modules.schema import INTERNAL_ID_RE as MODULE_INTERNAL_ID_RE  # noqa: E402
from core.theory_modules.schema import RULE_VERSION as MODULE_RULE_VERSION  # noqa: E402
from core.graph_paper_layer.schema import FORBIDDEN_KEYS as PAPER_FORBIDDEN_KEYS  # noqa: E402
from core.label_vocab import AI_READING_LABEL  # noqa: E402,F401  contract.py が再利用
from core.element_vocab import THEORY_STAGE_LABELS  # noqa: E402,F401  dialogue.py が再利用

DISPLAY_KEYS = frozenset({
    "answer", "label", "title", "text", "fact_line", "facts", "notice", "statement", "statements", "description",
    "summary", "message", "detail", "quote", "body", "reason", "name", "caption", "note", "paraphrase",
    "question", "explanation", "model_answer", "hint", "node_label", "region_label", "anchor_label",
    "doubt_type_label", "status_label", "latex_note", "label_note", "subject", "heading",
})
NUMBER_KEYS = frozenset({"answer", "facts", "fact_line", "notice", "statements", "statement", "message", "note",
                         "status_label", "summary"})
LABEL_KEYS = frozenset({"label", "title", "name", "node_label", "region_label", "anchor_label"})

# 「N 点」単独は列挙（「次の 3 点」）であって点数ではないので拾わない。点数は採点の文脈（点満点・N/10・点数/得点 語）だけ
_NUMBER_WITH_UNIT = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*(?:件|回|点満点|スコア)|(?<![\w./])\d+\s*/\s*10(?![\d.])")
_SCORE_WORD = re.compile(r"(?:スコア|点数|得点|正答率|一致度|理解度|進捗|達成率)\s*[:：は]?\s*\d+(?:\.\d+)?\s*[%％]?")
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


# 製品の正規表現（contains_internal_id / theory_modules.INTERNAL_ID_RE）が拾わない内部 ID の接頭辞。
# 正規表現ではなく字句の前方一致で見る（図・区画・モジュールの鍵 — 設計書 §18.1）
INTERNAL_ID_PREFIXES = ("fig_", "sec_runin", "module_key", "eq_tex_", f"{MODULE_RULE_VERSION}|")


def _internal_id(text: str) -> str:
    m = _ID_PATTERNS.search(text) or MODULE_INTERNAL_ID_RE.search(text)
    if m:
        return m.group(0)
    for token in re.split(r"[\s、。，,「」()（）]+", text):
        if token and (contains_internal_id(token) or token.startswith(INTERNAL_ID_PREFIXES)):
            return token
    return ""


# $…$ / $$…$$ で区切った数式は UI（KaTeX）が描く正規の形。区切りの外に残る TeX 命令と \( \[ の残骸だけを生 TeX とする
# 末尾の開いた $…（ラベルの切り詰めで閉じ $ が落ちた）も区切り付きとみなす
_DELIMITED_MATH = re.compile(r"\$\$.+?\$\$|\$[^$]+\$|\$[^$]*…?$", re.S)
_TEX_REMNANT = re.compile(r"\\(?:frac|mathrm|mathbf|mathcal|sqrt|left|right|begin|end|partial|alpha|beta|gamma|delta|chi|sum|int)\b"
                          r"|\\[(\[)\]]")


def _raw_tex(text: str) -> str:
    outside = _DELIMITED_MATH.sub(" ", text)
    return text[:80] if _TEX_REMNANT.search(outside) else ""


Check = Callable[[str, str], str]


def _checks() -> list[tuple[str, str, Check]]:
    return [
        ("numbers", "学習者に数値（件数・回数・点数・割合）が見えている",
         lambda k, t: _numbers(t) if k in NUMBER_KEYS else ""),
        ("denylist", "学習者向けの文に内部名（学生向け禁止語彙）が出ている",
         lambda k, t: next((w for w in STUDENT_DENYLIST if w in t), "")),
        ("internal_id", "学習者向けの文に内部 ID が出ている", lambda k, t: _internal_id(t)),
        ("raw_tex", "表示ラベルに生の TeX が出ている",
         lambda k, t: _raw_tex(t) if k in LABEL_KEYS else ""),
        ("control", "学習者向けの文に制御文字・ANSI の残骸が混じっている",
         lambda k, t: repr(t[:60]) if strip_control_sequences(t) != t else ""),
    ]


# ---------------------------------------------------------------------------
# 採点語彙（R層・UC層）— 製品のガードレールテストの禁止語彙をそのまま読む（PE6。自前の表を作らない）
# ---------------------------------------------------------------------------
_SCORE_VOCAB_SOURCES = (
    ("backend/tests/test_reconstruction_guardrails.py", "TestNoRawScoreToLearners", "BANNED"),
    ("backend/tests/test_understanding_cycle_ui_static.py", "TestDiffHasNoGradingVocabulary", "FORBIDDEN"),
)
SCORING_ACTIONS = ("learning.cycle.", "learning.check.", "learning.reconstruction.")
# 教材本文・出典の引用が入る欄（論文の「68%」「正解」を拾わない）
_QUOTE_KEYS = frozenset({"quote", "text", "body", "caption", "question", "model_answer", "evidence_quote", "answer"})
_SYMBOL_ONLY = ("%", "％")


def _tuple_constants(path: Path, cls: Optional[str], name: str) -> list[tuple[str, ...]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    out: list[tuple[str, ...]] = []
    for node in ast.walk(tree):
        if cls is not None and not (isinstance(node, ast.ClassDef) and node.name == cls):
            continue
        body = node.body if isinstance(node, ast.ClassDef) else []
        for stmt in body:
            if (isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets)
                    and isinstance(stmt.value, ast.Tuple)):
                vals = tuple(e.value for e in stmt.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str))
                out.append(vals)
    return out


@lru_cache(maxsize=1)
def scoring_vocab() -> tuple[str, ...]:
    """R層 REFLECT の BANNED と UC層 DIFF 表示の FORBIDDEN の和（全クラスの同名タプル）。"""
    words: list[str] = []
    for rel, cls, name in _SCORE_VOCAB_SOURCES:
        for vals in _tuple_constants(REPO_ROOT / rel, cls, name):
            # UI static の FORBIDDEN は複数クラスにある。日本語の採点語だけを採る（識別子の禁止語は対象外）
            words += [v for v in vals if not v.isascii() or v in _SYMBOL_ONLY]
    return tuple(dict.fromkeys(words))


def _is_scoring_step(step: TranscriptStep) -> bool:
    return step.action_id.startswith(SCORING_ACTIONS) or str(step.args.get("cycle_mode") or "") == "diff"


def _scoring_hit(key: str, text: str) -> str:
    if key in _QUOTE_KEYS:
        return ""
    return next((w for w in scoring_vocab() if w in text), "")


# ---------------------------------------------------------------------------
# 言語の一致 — 事実文・ラベルが利用者の言語で書かれているか（決定論）
# ---------------------------------------------------------------------------
LANGUAGE_KEYS = frozenset({"facts", "fact_line", "label", "notice"})
_ENGLISH_SENTENCE = re.compile(r"(?:^|[.!?]\s+)[A-Z][A-Za-z'’,;:()\- ]*(?:\s[A-Za-z'’,;:()\-]+){3,}\.(?:\s|$)")
_JAPANESE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]{4,}")


def _language_hit(language: str, key: str, text: str) -> str:
    if key not in LANGUAGE_KEYS:
        return ""
    if language == "en":
        m = _JAPANESE.search(text)
        return m.group(0) if m else ""
    m = _ENGLISH_SENTENCE.search(text)
    return m.group(0).strip()[:80] if m else ""


@lru_cache(maxsize=64)
def _persona_language(domain: str, persona_id: str) -> str:
    try:
        from uxsim.persona.compose import find_persona_file, load_yaml
        data = load_yaml(find_persona_file(domain, persona_id)) or {}
        return str(data.get("language") or "ja")
    except Exception:  # noqa: BLE001 — ペルソナファイルが無い（テストの偽 transcript 等）は日本語話者とみなす
        return "ja"


# ---------------------------------------------------------------------------
# 教員向け理論モジュール・論文層に内部の量が出ない（TM6・PL4）
# ---------------------------------------------------------------------------
TEACHER_STRUCTURE_PATHS = ("/theory-modules", "/paper-layer")
TEACHER_FORBIDDEN_KEYS = tuple(dict.fromkeys(MODULE_FORBIDDEN_KEYS + PAPER_FORBIDDEN_KEYS))
_FINGERPRINT_MARK = f'"{MODULE_RULE_VERSION}|'


def _forbidden_keys(body: Any, excerpt: str) -> list[str]:
    if body is None:
        return [k for k in TEACHER_FORBIDDEN_KEYS if f'"{k}":' in excerpt or f'"{k}" :' in excerpt]
    hits: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k in TEACHER_FORBIDDEN_KEYS:
                    hits.add(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(body)
    return sorted(hits)


def check_teacher_structure(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    seen: set[str] = set()
    for step in steps:
        for t in step.http:
            if t.status != 200 or not any(p in (t.path or "") for p in TEACHER_STRUCTURE_PATHS):
                continue
            keys = _forbidden_keys(excerpt_json(t.response_excerpt), t.response_excerpt or "")
            fp = _FINGERPRINT_MARK in (t.response_excerpt or "")
            if not keys and not fp:
                continue
            surface = next(p for p in TEACHER_STRUCTURE_PATHS if p in t.path)
            if surface in seen:
                continue
            seen.add(surface)
            quote = ", ".join(keys) + (" / 構造の指紋" if fp else "")
            out.append(factory.make(oracle="B", severity="principle", step=step, quote=quote,
                                    hypothesis=f"教員向けの応答（{surface}）に内部の量（本数・閾値・指紋）が載っている",
                                    layers=["graph_review", "frontend_admin_ui"]))
    return out


def check(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = check_teacher_structure(steps, factory)
    checks = _checks()
    domain = factory.meta.domain if factory.meta else ""
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
                lang = _persona_language(domain, step.persona_id)
                hit = _language_hit(lang, key, text)
                if hit and "language" not in seen:
                    seen.add("language")
                    what = ("日本語話者の学習者向け事実文に英語の生成文が混じっている" if lang != "en"
                            else "英語話者の学習者に日本語の固定文が出ている（言語不一致）")
                    out.append(factory.make(oracle="B", severity="principle", step=step, quote=f"{key}: {hit}",
                                            hypothesis=f"{what}（{step.action_id}）"))
                if _is_scoring_step(step):
                    hit = _scoring_hit(key, text)
                    if hit and "scoring" not in seen:
                        seen.add("scoring")
                        out.append(factory.make(oracle="B", severity="principle", step=step, quote=f"{key}: {hit}",
                                                hypothesis=f"採点しない面に正誤・点数の語彙が出ている（{step.action_id}）"))
    return out
