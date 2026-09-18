"""課題ナレッジ（docs/issue_knowledge/）の読み込み・検証・索引生成。

正本: docs/issue_knowledge/taxonomy.md（語彙の定義）/ TEMPLATE.md（記入様式）。
本モジュールの語彙定数は taxonomy.md と一致していることを
``backend/tests/test_issue_knowledge_guardrails.py`` が固定する（片方だけ変えると落ちる）。

使い方（リポジトリルートから）::

    backend/.venv/bin/python backend/scripts/issue_knowledge_index.py            # index.md を再生成
    backend/.venv/bin/python backend/scripts/issue_knowledge_index.py --check    # 差分があれば非0
    backend/.venv/bin/python backend/scripts/issue_knowledge_index.py --validate # 検証結果だけ表示

stdlib + PyYAML のみ。FastAPI / sqlalchemy / core.* は import しない（docs 専用の道具）。
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
IK_DIR = ROOT / "docs" / "issue_knowledge"
ENTRIES_DIR = IK_DIR / "entries"
TAXONOMY_DOC = IK_DIR / "taxonomy.md"
LAYERS_DOC = IK_DIR / "layers.md"
ARCH_DIR = ROOT / "docs" / "architecture"
TEMPLATE_DOC = IK_DIR / "TEMPLATE.md"
DICTIONARY_DOC = IK_DIR / "dictionary.md"
INDEX_DOC = IK_DIR / "index.md"

# ---------------------------------------------------------------------------
# 語彙（正本は taxonomy.md。ここはミラー）
# ---------------------------------------------------------------------------

PRIMARIES: tuple[str, ...] = ("local", "structure", "connection", "governance")
GROUP_OF: dict[str, str] = {
    "local": "局所",
    "structure": "構造・接続・統制",
    "connection": "構造・接続・統制",
    "governance": "構造・接続・統制",
}
PRIMARY_LABELS: dict[str, str] = {
    "local": "局所",
    "structure": "構造",
    "connection": "接続",
    "governance": "統制",
}

FACETS: tuple[str, ...] = (
    "local.input_handling",
    "local.logic",
    "local.resource",
    "local.wording",
    "local.regression",
    "structure.representation",
    "structure.responsibility",
    "structure.decomposition",
    "structure.aggregation",
    "connection.information",
    "connection.meaning",
    "connection.condition",
    "connection.target",
    "connection.version",
    "connection.contract",
    "governance.assignment",
    "governance.ordering",
    "governance.budget",
    "governance.review",
    "governance.resume",
    "governance.completion",
)

CAUSE_STATUSES: tuple[str, ...] = ("confirmed", "hypothesis")
REVIEW_STATES: tuple[str, ...] = ("candidate", "confirmed")
MAX_PERSPECTIVES = 2            # 観点は最大 2・先頭が主（taxonomy §5）
TYPE_ESTABLISHED_MIN_CONFIRMED = 2  # 型の成立 = 確定エントリ 2 件以上（taxonomy §6.1）
STATUSES: tuple[str, ...] = ("open", "resolved", "deferred", "rejected")
GENERALIZATION_LEVELS: tuple[str, ...] = ("instance", "repo_pattern", "general")

DISCOVERY_PERSPECTIVES: tuple[str, ...] = (
    "symptom_report",
    "invariant_audit",
    "boundary_walk",
    "doc_code_diff",
    "data_inspection",
    "trace_walk",
    "adversarial_review",
    "inventory",
    "guardrail_failure",
    "reproduction",
    "external_constraint",
)

RESOLUTION_PERSPECTIVES: tuple[str, ...] = (
    "single_point_fix",
    "canonical_source",
    "explicit_contract",
    "required_argument",
    "vocabulary_table",
    "first_class_state",
    "representation_change",
    "responsibility_move",
    "carry_through",
    "fail_closed",
    "state_transition",
    "order_and_budget",
    "guardrail_fix",
    "doc_correction",
    "deferred_decision",
    "pending",
)

STATUS_LABELS = {"open": "未解決", "resolved": "解決済み", "deferred": "保留", "rejected": "不成立"}
REVIEW_LABELS = {"candidate": "候補", "confirmed": "確定"}
CAUSE_LABELS = {"confirmed": "確定", "hypothesis": "仮説"}
LEVEL_LABELS = {"instance": "固有", "repo_pattern": "リポジトリ内の型", "general": "一般"}

REQUIRED_TOP_KEYS = (
    "id",
    "title",
    "status",
    "recorded_at",
    "resolved_at",
    "sources",
    "feature_context",
    "classification",
    "generalization",
    "pattern",
    "discovery",
    "resolution",
    "related",
    "view_of",
    "history",
)
REQUIRED_CLASSIFICATION_KEYS = ("primary", "facets", "cause_status", "review", "reviewed_by", "reviewed_at", "basis")
REQUIRED_BODY_HEADINGS = ("## 課題", "## 発見の観点", "## 解決の観点", "## 一般化")

ENTRY_FILE_RE = re.compile(r"^(IK-\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
ID_RE = re.compile(r"^IK-\d{4}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PATTERN_HEADING_RE = re.compile(r"(?m)^#### ([a-z0-9]+(?:-[a-z0-9]+)*)\s*$")
FAMILY_HEADING_RE = re.compile(r"(?m)^### ([a-z0-9]+(?:-[a-z0-9]+)*)\s*$")
LAYER_ROW_RE = re.compile(r"(?m)^\| `([a-z0-9_]+)` \|")
COMMIT_HASH_RE = re.compile(r"^[0-9a-f]{7,40}$")
TENDENCY_RE = re.compile(r"\| 主分類の傾向 \| `(local|structure|connection|governance)`")

# 分類の根拠に使ってはならない書き方（taxonomy §1.2）。症状の場所・修正量・修正手段。
FORBIDDEN_BASIS_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 修正量としての「N 行」だけを弾く（データの行数「1 行の JSONB」は分類の根拠として妥当）
    re.compile(r"\d+\s*行(の|だけの|で|しか)?(修正|変更|差分|書き換え|直し)"),
    re.compile(r"(修正|変更|差分)(は|が|も)?\s*\d+\s*行"),
    re.compile(r"行数"),
    re.compile(r"ファイル数"),
    re.compile(r"修正量"),
    re.compile(r"規模が(小さ|大き)"),
    re.compile(r"(フロント|バックエンド|サーバ|DB|フロントエンド)(側|エンド)?(のバグ|の不具合|の問題)(なので|だから|のため|であり)"),
)

# general_form に含めてはならないもの（機能名・層名・ファイル名を書かない）。
FORBIDDEN_GENERAL_FORM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\.(py|js|md|sql|html|json|yaml)\b"),
    re.compile(r"[/`]"),
)


@dataclass
class Entry:
    path: Path
    meta: dict
    body: str
    errors: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(self.meta.get("id", self.path.name))

    @property
    def rel(self) -> str:
        return f"entries/{self.path.name}"


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------

def split_front_matter(text: str) -> tuple[str | None, str]:
    """先頭の ``---`` ブロックを (yaml_text, body) に分ける。無ければ (None, text)。"""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text
    return text[4:end], text[end + 5 :]


def load_entries(entries_dir: Path = ENTRIES_DIR) -> list[Entry]:
    entries: list[Entry] = []
    for path in sorted(entries_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fm, body = split_front_matter(text)
        errors: list[str] = []
        meta: dict = {}
        if fm is None:
            errors.append("front-matter（先頭の --- ブロック）が無い")
        else:
            try:
                loaded = yaml.safe_load(fm)
            except yaml.YAMLError as exc:  # pragma: no cover - 内容依存
                errors.append(f"front-matter が YAML として読めない: {exc}")
                loaded = None
            if loaded is not None and not isinstance(loaded, dict):
                errors.append("front-matter の最上位が mapping ではない")
            elif isinstance(loaded, dict):
                meta = loaded
        entries.append(Entry(path=path, meta=meta, body=body, errors=errors))
    return entries


def dictionary_patterns(dictionary_text: str) -> list[str]:
    return PATTERN_HEADING_RE.findall(dictionary_text)


def dictionary_families(dictionary_text: str) -> dict[str, list[str]]:
    """{族 slug: [型 slug, ...]}（辞書の見出し順）。族に属さない型は空文字キーに入る。"""
    families: dict[str, list[str]] = {}
    current = ""
    for line in dictionary_text.splitlines():
        fm = FAMILY_HEADING_RE.match(line)
        if fm:
            current = fm.group(1)
            families.setdefault(current, [])
            continue
        pm = PATTERN_HEADING_RE.match(line)
        if pm:
            families.setdefault(current, []).append(pm.group(1))
    return families


def dictionary_tendencies(dictionary_text: str) -> dict[str, str]:
    """{型 slug: 辞書が書く主分類の傾向}。書いていない型は含めない。"""
    out: dict[str, str] = {}
    for m in re.finditer(r"(?ms)^#### ([a-z0-9-]+)\s*$(.*?)(?=^#### |^### |^## |\Z)", dictionary_text):
        tm = TENDENCY_RE.search(m.group(2))
        if tm:
            out[m.group(1)] = tm.group(1)
    return out


def layer_vocabulary(layers_text: str | None = None) -> list[str]:
    text = layers_text if layers_text is not None else LAYERS_DOC.read_text(encoding="utf-8")
    return LAYER_ROW_RE.findall(text)


def _looks_like_code_landing(item: str, root: Path) -> bool:
    plain = item.strip().strip("`")
    if COMMIT_HASH_RE.match(plain):
        return True
    path = re.split(r"\s|§|#|::", plain, maxsplit=1)[0]
    if not path or path.startswith("docs/") or path == "CLAUDE.md" or path.endswith(".md"):
        return False
    return (root / path).exists()


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------

def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _resolve_source(root: Path, source: str) -> Path:
    plain = source.strip().strip("`")
    # 「docs/foo.md §2」「docs/foo.md#anchor」「docs/foo.md F-7」の後ろ（節・行の指示）を落とす。
    # パス本体は最初の空白・§・# まで。
    plain = re.split(r"\s|§|#", plain, maxsplit=1)[0].strip().strip("`")
    return root / plain


def validate_entry(
    entry: Entry,
    *,
    patterns: set[str],
    known_ids: set[str],
    root: Path = ROOT,
    layers: set[str] | None = None,
) -> list[str]:
    """1 エントリの規約違反を人が読める文で返す（空なら合格）。"""
    errs = list(entry.errors)
    m = entry.meta
    name = entry.path.name

    fm_match = ENTRY_FILE_RE.match(name)
    if not fm_match:
        errs.append("ファイル名が IK-NNNN-<slug>.md の形ではない")
    if not errs and m:
        pass
    if not m:
        return errs

    for key in REQUIRED_TOP_KEYS:
        if key not in m:
            errs.append(f"必須キー `{key}` が無い")
    if errs:
        return errs

    ident = str(m["id"])
    if not ID_RE.match(ident):
        errs.append(f"id `{ident}` が IK-NNNN の形ではない")
    elif fm_match and fm_match.group(1) != ident:
        errs.append(f"id `{ident}` とファイル名 `{name}` の番号が一致しない")

    if not isinstance(m["title"], str) or not m["title"].strip():
        errs.append("title が空")

    status = m["status"]
    if status not in STATUSES:
        errs.append(f"status `{status}` は語彙外 {STATUSES}")

    for key in ("recorded_at",):
        if not DATE_RE.match(str(m[key])):
            errs.append(f"{key} `{m[key]}` は YYYY-MM-DD ではない")
    resolved_at = m["resolved_at"]
    if status == "resolved":
        if resolved_at is None or not DATE_RE.match(str(resolved_at)):
            errs.append("status=resolved なのに resolved_at が YYYY-MM-DD ではない")
    elif resolved_at is not None and not DATE_RE.match(str(resolved_at)):
        errs.append(f"resolved_at `{resolved_at}` は YYYY-MM-DD か null")

    sources = _as_list(m["sources"])
    if not sources:
        errs.append("sources が空（起票元の docs 文書を最低 1 つ）")
    for src in sources:
        if not isinstance(src, str):
            errs.append(f"sources の要素が文字列ではない: {src!r}")
            continue
        target = _resolve_source(root, src)
        if not target.exists():
            errs.append(f"sources のパスが実在しない: `{src}`")
        elif not str(target.resolve()).startswith(str((root / "docs").resolve())) and target.resolve() != (root / "CLAUDE.md").resolve():
            errs.append(f"sources は docs/ 配下（または CLAUDE.md）に限る: `{src}`")

    fc = m["feature_context"]
    if not isinstance(fc, dict):
        errs.append("feature_context が mapping ではない")
    else:
        if not isinstance(fc.get("realizing"), str) or not fc["realizing"].strip():
            errs.append("feature_context.realizing が空（どの機能を実現するときに出るかを一文で）")
        entry_layers = _as_list(fc.get("layers"))
        if not entry_layers:
            errs.append("feature_context.layers が空（関係する層を最低 1 つ）")
        if layers is not None:
            for layer in entry_layers:
                if layer not in layers:
                    errs.append(f"feature_context.layers `{layer}` が layers.md の語彙に無い（自由記述は索引を壊す）")

    cl = m["classification"]
    if not isinstance(cl, dict):
        errs.append("classification が mapping ではない")
    else:
        primary = cl.get("primary")
        if primary not in PRIMARIES:
            errs.append(f"classification.primary `{primary}` は語彙外 {PRIMARIES}")
        facets = _as_list(cl.get("facets"))
        if not facets:
            errs.append("classification.facets が空（<primary>.<sub> を最低 1 つ）")
        for f in facets:
            if f not in FACETS:
                errs.append(f"facet `{f}` は語彙外（taxonomy §2）")
        if primary in PRIMARIES and facets and not any(
            isinstance(f, str) and f.startswith(f"{primary}.") for f in facets
        ):
            errs.append(f"facets に主分類 `{primary}` の接頭辞を持つものが無い")
        for key in REQUIRED_CLASSIFICATION_KEYS:
            if key not in cl:
                errs.append(f"classification.{key} が無い")
        cause = cl.get("cause_status")
        if cause not in CAUSE_STATUSES:
            errs.append(f"classification.cause_status `{cause}` は語彙外 {CAUSE_STATUSES}")
        review = cl.get("review")
        if review not in REVIEW_STATES:
            errs.append(f"classification.review `{review}` は語彙外 {REVIEW_STATES}")
        if review == "confirmed":
            if not isinstance(cl.get("reviewed_by"), str) or not cl["reviewed_by"].strip():
                errs.append("review=confirmed なのに reviewed_by が空")
            if not DATE_RE.match(str(cl.get("reviewed_at"))):
                errs.append("review=confirmed なのに reviewed_at が YYYY-MM-DD ではない")
        elif review == "candidate":
            if cl.get("reviewed_by") is not None or cl.get("reviewed_at") is not None:
                errs.append("review=candidate では reviewed_by / reviewed_at は null")
        basis = cl.get("basis")
        if not isinstance(basis, str) or not basis.strip():
            errs.append("classification.basis が空（原因の性質のどこが定義に当たるか）")
        else:
            b = basis.strip()
            if cause == "hypothesis" and not b.startswith("仮説"):
                errs.append("cause_status=hypothesis の basis は「仮説:」で始める")
            if cause == "confirmed" and b.startswith("仮説"):
                errs.append("cause_status=confirmed の basis が「仮説」で始まっている（確度を揃える）")
            for pat in FORBIDDEN_BASIS_PATTERNS:
                if pat.search(b):
                    errs.append(
                        f"classification.basis に分類の根拠にしてはならない記述（{pat.pattern}）— "
                        "症状の場所・修正量で分類しない（taxonomy §1.2）"
                    )

    gen = m["generalization"]
    if not isinstance(gen, dict):
        errs.append("generalization が mapping ではない")
    else:
        level = gen.get("level")
        if level not in GENERALIZATION_LEVELS:
            errs.append(f"generalization.level `{level}` は語彙外 {GENERALIZATION_LEVELS}")
        gf = gen.get("general_form")
        if not isinstance(gf, str) or not gf.strip():
            errs.append("generalization.general_form が空")
        else:
            for pat in FORBIDDEN_GENERAL_FORM_PATTERNS:
                if pat.search(gf):
                    errs.append(
                        f"generalization.general_form にファイル名・パス・コード片が含まれる（{pat.pattern}）— "
                        "機能名・層名を含まない一文にする"
                    )

    pattern = m["pattern"]
    if not isinstance(pattern, str) or not SLUG_RE.match(pattern):
        errs.append(f"pattern `{pattern}` が slug（英小文字・数字・ハイフン）ではない")
    elif pattern not in patterns:
        errs.append(f"pattern `{pattern}` が dictionary.md の `### ` 見出しに無い（辞書に型を足すか既存の型に畳む）")

    disc = m["discovery"]
    if not isinstance(disc, dict):
        errs.append("discovery が mapping ではない")
    else:
        dp = _as_list(disc.get("perspective"))
        if not dp:
            errs.append("discovery.perspective が空")
        if len(dp) > MAX_PERSPECTIVES:
            errs.append(f"discovery.perspective は最大 {MAX_PERSPECTIVES}（先頭が主）: {dp}")
        for p in dp:
            if p not in DISCOVERY_PERSPECTIVES:
                errs.append(f"discovery.perspective `{p}` は語彙外（taxonomy §4）")
        if not isinstance(disc.get("note"), str) or not disc["note"].strip():
            errs.append("discovery.note が空")

    res = m["resolution"]
    if not isinstance(res, dict):
        errs.append("resolution が mapping ではない")
    else:
        rp = _as_list(res.get("perspective"))
        if not rp:
            errs.append("resolution.perspective が空（未解決なら [pending]）")
        if len(rp) > MAX_PERSPECTIVES:
            errs.append(f"resolution.perspective は最大 {MAX_PERSPECTIVES}（先頭が主）: {rp}")
        if rp and rp[0] == "guardrail_fix":
            errs.append("resolution.perspective の主観点に guardrail_fix は置けない（再発防止は別の見立ての従）")
        for p in rp:
            if p not in RESOLUTION_PERSPECTIVES:
                errs.append(f"resolution.perspective `{p}` は語彙外（taxonomy §5）")
        landed = _as_list(res.get("landed_in"))
        if status == "resolved":
            if "pending" in rp:
                errs.append("status=resolved なのに resolution.perspective に pending がある")
            if not landed:
                errs.append("status=resolved なのに resolution.landed_in が空")
            elif not any(isinstance(x, str) and _looks_like_code_landing(x, root) for x in landed):
                errs.append(
                    "status=resolved の landed_in に実在するコードのパスかコミットハッシュが 1 つも無い"
                    "（文書だけでは、どう解いたかを実装まで辿れない）"
                )
        elif status == "open":
            if "pending" not in rp:
                errs.append("status=open の resolution.perspective は pending を含める")
        elif status == "deferred":
            if not ({"pending", "deferred_decision"} & set(rp)):
                errs.append("status=deferred の resolution.perspective は deferred_decision か pending を含める")
        if not isinstance(res.get("note"), str) or not res["note"].strip():
            errs.append("resolution.note が空（未解決なら何が分かれば解けるか）")

    for rid in _as_list(m["related"]):
        if rid not in known_ids:
            errs.append(f"related の `{rid}` が実在しない")
    for rid in _as_list(m["view_of"]):
        if rid not in known_ids:
            errs.append(f"view_of の `{rid}` が実在しない")
        if rid == m.get("id"):
            errs.append("view_of に自分自身は置けない")

    hist = _as_list(m["history"])
    for h in hist:
        if not isinstance(h, dict) or not {"date", "field", "from", "to", "reason"} <= set(h):
            errs.append("history の要素は {date, field, from, to, reason} を持つ mapping")

    for heading in REQUIRED_BODY_HEADINGS:
        if not re.search(rf"(?m)^{re.escape(heading)}\s*$", entry.body):
            errs.append(f"本文に見出し `{heading}` が無い")

    return errs


def validate_all(
    entries: list[Entry], dictionary_text: str, *, root: Path = ROOT
) -> tuple[dict[str, list[str]], list[str]]:
    """(エントリ別の違反, 全体の違反) を返す。"""
    patterns = set(dictionary_patterns(dictionary_text))
    known_ids = {str(e.meta.get("id")) for e in entries if e.meta.get("id")}
    layers = set(layer_vocabulary()) if LAYERS_DOC.exists() else None
    per_entry: dict[str, list[str]] = {}
    for e in entries:
        errs = validate_entry(e, patterns=patterns, known_ids=known_ids, root=root, layers=layers)
        if errs:
            per_entry[e.path.name] = errs

    global_errs: list[str] = []
    ids = Counter(str(e.meta.get("id")) for e in entries if e.meta.get("id"))
    for ident, n in ids.items():
        if n > 1:
            global_errs.append(f"id `{ident}` が {n} 回使われている")
    used_patterns = {str(e.meta.get("pattern")) for e in entries}
    for p in sorted(patterns - used_patterns):
        global_errs.append(f"dictionary.md の型 `{p}` を参照するエントリが無い（エントリの無い型は置かない）")
    dup_headings = [p for p, n in Counter(dictionary_patterns(dictionary_text)).items() if n > 1]
    for p in dup_headings:
        global_errs.append(f"dictionary.md の型 `{p}` が重複している")
    families = dictionary_families(dictionary_text)
    for orphan in families.get("", []):
        global_errs.append(f"dictionary.md の型 `{orphan}` が族（### 見出し）の下にない")
    for fam, types in families.items():
        if fam and not types:
            global_errs.append(f"dictionary.md の族 `{fam}` に型が無い")
    # view_of は対称に張る（片方向だと束が索引で割れる）
    by_id = {str(e.meta.get("id")): e for e in entries if e.meta.get("id")}
    for e in entries:
        for rid in _as_list(e.meta.get("view_of")):
            other = by_id.get(rid)
            if other is not None and e.meta.get("id") not in _as_list(other.meta.get("view_of")):
                global_errs.append(f"view_of が片方向: {e.meta.get('id')} → {rid}（相手側にも書く）")
    return per_entry, global_errs


# ---------------------------------------------------------------------------
# 索引生成
# ---------------------------------------------------------------------------

def _link(e: Entry) -> str:
    return f"[{e.id}]({e.rel})"


def _cell(value) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")


def _row(e: Entry) -> str:
    m = e.meta
    cl = m.get("classification", {}) or {}
    disc = m.get("discovery", {}) or {}
    res = m.get("resolution", {}) or {}
    primary = cl.get("primary", "")
    facets = ", ".join(_as_list(cl.get("facets")))
    return (
        f"| {_link(e)} | {_cell(m.get('title'))} | {GROUP_OF.get(primary, '')} | "
        f"{PRIMARY_LABELS.get(primary, primary)} | {_cell(facets)} | "
        f"{CAUSE_LABELS.get(cl.get('cause_status'), '')} | {REVIEW_LABELS.get(cl.get('review'), '')} | "
        f"{STATUS_LABELS.get(m.get('status'), '')} | "
        f"`{_cell(m.get('pattern'))}` | {_cell(', '.join(_as_list(disc.get('perspective'))))} | "
        f"{_cell(', '.join(_as_list(res.get('perspective'))))} |"
    )


_TABLE_HEADER = (
    "| ID | 題名 | 群 | 主分類 | 副分類（facets） | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|"
)


def _table(entries: list[Entry]) -> str:
    if not entries:
        return "（該当なし）"
    return "\n".join([_TABLE_HEADER, *(_row(e) for e in entries)])


def render_index(entries: list[Entry], dictionary_text: str) -> str:
    entries = sorted(entries, key=lambda e: e.id)
    lines: list[str] = []
    lines.append("# 課題ナレッジ — 索引（機械生成）")
    lines.append("")
    lines.append(
        "[← 課題ナレッジの入口](README.md) ｜ [分類体系](taxonomy.md) ｜ [辞書](dictionary.md) ｜ "
        "[記入様式](TEMPLATE.md)"
    )
    lines.append("")
    lines.append(
        "> **状態:** 生きたリファレンス（機械生成）。手で編集しない。"
        "`backend/.venv/bin/python backend/scripts/issue_knowledge_index.py` で再生成する"
        "（`backend/tests/test_issue_knowledge_guardrails.py` が同期を検査する）。"
    )
    lines.append("")

    by_group: dict[str, list[Entry]] = defaultdict(list)
    by_primary: dict[str, list[Entry]] = defaultdict(list)
    by_pattern: dict[str, list[Entry]] = defaultdict(list)
    by_disc: dict[str, list[Entry]] = defaultdict(list)
    by_res: dict[str, list[Entry]] = defaultdict(list)
    by_layer: dict[str, list[Entry]] = defaultdict(list)
    by_level: dict[str, list[Entry]] = defaultdict(list)
    hypotheses: list[Entry] = []
    unresolved: list[Entry] = []
    confirmed_by_pattern: Counter = Counter()
    for e in entries:
        cl = e.meta.get("classification", {}) or {}
        primary = cl.get("primary", "")
        by_group[GROUP_OF.get(primary, "")].append(e)
        by_primary[primary].append(e)
        by_pattern[str(e.meta.get("pattern"))].append(e)
        for p in _as_list((e.meta.get("discovery") or {}).get("perspective")):
            by_disc[str(p)].append(e)
        for p in _as_list((e.meta.get("resolution") or {}).get("perspective")):
            by_res[str(p)].append(e)
        for layer in _as_list((e.meta.get("feature_context") or {}).get("layers")):
            by_layer[str(layer)].append(e)
        by_level[str((e.meta.get("generalization") or {}).get("level"))].append(e)
        if cl.get("cause_status") == "hypothesis":
            hypotheses.append(e)
        if e.meta.get("status") in ("open", "deferred"):
            unresolved.append(e)
        if cl.get("review") == "confirmed":
            confirmed_by_pattern[str(e.meta.get("pattern"))] += 1
    n_confirmed = sum(confirmed_by_pattern.values())

    lines.append("## 0. 概況")
    lines.append("")
    lines.append("| 軸 | 内訳 |")
    lines.append("|---|---|")
    lines.append(
        "| 群 | "
        + " / ".join(f"{g or '（未分類）'} {len(v)}" for g, v in sorted(by_group.items()))
        + " |"
    )
    lines.append(
        "| 主分類 | "
        + " / ".join(
            f"{PRIMARY_LABELS.get(p, p or '（未分類）')} {len(by_primary.get(p, []))}" for p in PRIMARIES
        )
        + " |"
    )
    lines.append(
        "| 状態 | "
        + " / ".join(
            f"{STATUS_LABELS[s]} {sum(1 for e in entries if e.meta.get('status') == s)}" for s in STATUSES
        )
        + " |"
    )
    lines.append(f"| 分類の確定状態 | 候補 {len(entries) - n_confirmed} / 確定 {n_confirmed} |")
    lines.append(f"| 原因が仮説 | {len(hypotheses)} |")
    lines.append(
        "| 一般化 | "
        + " / ".join(f"{LEVEL_LABELS.get(l, l)} {len(by_level.get(l, []))}" for l in GENERALIZATION_LEVELS)
        + " |"
    )
    lines.append("")

    lines.append("## 1. 全エントリ")
    lines.append("")
    lines.append(_table(entries))
    lines.append("")

    lines.append("## 2. 群・主分類別")
    lines.append("")
    for p in PRIMARIES:
        lines.append(f"### {GROUP_OF[p]} › {PRIMARY_LABELS[p]}（`{p}`）")
        lines.append("")
        lines.append(_table(by_primary.get(p, [])))
        lines.append("")

    lines.append("## 3. 族・型別（dictionary.md の見出し）")
    lines.append("")
    lines.append(
        f"型の成立 = 分類が確定したエントリ {TYPE_ESTABLISHED_MIN_CONFIRMED} 件以上。"
        "それ未満は**暫定**（族への畳み込み候補）。"
    )
    lines.append("")
    tendencies = dictionary_tendencies(dictionary_text)
    mismatches: list[tuple[Entry, str, str]] = []
    for fam, types in dictionary_families(dictionary_text).items():
        lines.append(f"### 族 `{fam or '（族なし）'}`" + (f" → [辞書](dictionary.md#{fam})" if fam else ""))
        lines.append("")
        for slug in types:
            items = by_pattern.get(slug, [])
            state = "成立" if confirmed_by_pattern.get(slug, 0) >= TYPE_ESTABLISHED_MIN_CONFIRMED else "暫定"
            lines.append(f"#### `{slug}`（{state}）")
            lines.append("")
            lines.append(f"→ [辞書の定義](dictionary.md#{slug})")
            lines.append("")
            lines.append(_table(items))
            lines.append("")
            tend = tendencies.get(slug)
            if tend:
                for e in items:
                    prim = (e.meta.get("classification") or {}).get("primary")
                    if prim and prim != tend:
                        mismatches.append((e, slug, tend))
    lines.append("### 型の「主分類の傾向」とエントリの主分類が食い違うもの（分類レビューの入口）")
    lines.append("")
    if mismatches:
        lines.append("| ID | 型 | 辞書の傾向 | エントリの主分類 | 分類 |")
        lines.append("|---|---|---|---|---|")
        for e, slug, tend in mismatches:
            cl = e.meta.get("classification") or {}
            lines.append(
                f"| {_link(e)} | `{slug}` | {PRIMARY_LABELS[tend]} | {PRIMARY_LABELS.get(cl.get('primary'), '')} | "
                f"{REVIEW_LABELS.get(cl.get('review'), '')} |"
            )
    else:
        lines.append("（該当なし）")
    lines.append("")
    stray = sorted(set(by_pattern) - set(dictionary_patterns(dictionary_text)))
    if stray:
        lines.append("### （辞書に無い型 — ガードレール違反）")
        lines.append("")
        for slug in stray:
            lines.append(f"- `{slug}`: " + ", ".join(_link(e) for e in by_pattern[slug]))
        lines.append("")

    lines.append("## 4. 発見観点別")
    lines.append("")
    for p in DISCOVERY_PERSPECTIVES:
        items = by_disc.get(p, [])
        lines.append(f"- `{p}`: " + (", ".join(_link(e) for e in items) if items else "（該当なし）"))
    lines.append("")

    lines.append("## 5. 解決観点別")
    lines.append("")
    for p in RESOLUTION_PERSPECTIVES:
        items = by_res.get(p, [])
        lines.append(f"- `{p}`: " + (", ".join(_link(e) for e in items) if items else "（該当なし）"))
    lines.append("")

    lines.append("## 6. 層・モジュール別（場所の記録。分類の根拠ではない）")
    lines.append("")
    for layer in sorted(by_layer):
        lines.append(f"- `{layer}`: " + ", ".join(_link(e) for e in by_layer[layer]))
    lines.append("")

    lines.append("## 7. 原因が仮説のまま（分類も仮説）")
    lines.append("")
    lines.append(_table(hypotheses))
    lines.append("")

    lines.append("## 8. 未解決・保留")
    lines.append("")
    lines.append(_table(unresolved))
    lines.append("")

    lines.append("## 9. 同じ原因の別視点（`view_of` の束）")
    lines.append("")
    by_id = {e.id: e for e in entries}
    seen: set[str] = set()
    bundles: list[list[Entry]] = []
    for e in entries:
        if e.id in seen:
            continue
        members = {e.id}
        stack = [e.id]
        while stack:
            cur = stack.pop()
            for rid in _as_list(by_id[cur].meta.get("view_of")) if cur in by_id else []:
                if rid in by_id and rid not in members:
                    members.add(rid)
                    stack.append(rid)
        if len(members) > 1:
            bundles.append(sorted((by_id[i] for i in members), key=lambda x: x.id))
        seen |= members
    if bundles:
        for bundle in bundles:
            lines.append(f"- 代表 {_link(bundle[0])}: " + " / ".join(f"{_link(x)} {_cell(x.meta.get('title'))}" for x in bundle))
    else:
        lines.append("（該当なし）")
    lines.append("")

    lines.append("## 10. 出典文書の被覆（調査・レビュー系文書ごとのエントリ有無）")
    lines.append("")
    src_count: Counter = Counter()
    for e in entries:
        seen_paths: set[str] = set()
        for s in _as_list(e.meta.get("sources")):
            if isinstance(s, str):
                rel = str(_resolve_source(ROOT, s).resolve().relative_to(ROOT.resolve())) if _resolve_source(ROOT, s).exists() else s
                seen_paths.add(rel)
        for rel in seen_paths:
            src_count[rel] += 1
    lines.append("| 出典文書 | エントリ |")
    lines.append("|---|---|")
    for rel, n in sorted(src_count.items()):
        lines.append(f"| [{rel}](../../{rel}) | {n} |")
    lines.append("")
    survey_like = sorted(
        p for p in ARCH_DIR.rglob("*.md")
        if re.search(r"(survey|review|findings|issues|debate|audit|known_issues|proposal)", p.name)
    )
    zero = [p for p in survey_like if str(p.relative_to(ROOT)) not in src_count]
    lines.append("調査・レビュー系（ファイル名に survey / review / findings / issues / debate / audit / proposal を含む）で、"
                 "まだ 1 件もエントリの出典になっていない文書:")
    lines.append("")
    for p in zero:
        rel = str(p.relative_to(ROOT))
        lines.append(f"- [{rel}](../../{rel})")
    if not zero:
        lines.append("（該当なし）")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_validation(entries: list[Entry], dictionary_text: str) -> int:
    per_entry, global_errs = validate_all(entries, dictionary_text)
    for name, errs in sorted(per_entry.items()):
        print(f"[{name}]")
        for err in errs:
            print(f"  - {err}")
    for err in global_errs:
        print(f"[全体] {err}")
    total = sum(len(v) for v in per_entry.values()) + len(global_errs)
    print(f"entries={len(entries)} violations={total}")
    return 1 if total else 0


def _scaffold(slug: str) -> int:
    if not SLUG_RE.match(slug):
        print(f"slug `{slug}` は英小文字・数字・ハイフンのみ", file=sys.stderr)
        return 2
    numbers = [int(m.group(1)[3:]) for p in ENTRIES_DIR.glob("*.md") if (m := ENTRY_FILE_RE.match(p.name))]
    next_no = (max(numbers) + 1) if numbers else 1
    ident = f"IK-{next_no:04d}"
    template = TEMPLATE_DOC.read_text(encoding="utf-8")
    m = re.search(r"```markdown\n(.*?)```", template, re.S)
    if not m:
        print("TEMPLATE.md の ```markdown ブロックが見つからない", file=sys.stderr)
        return 2
    body = m.group(1).replace("id: IK-0000", f"id: {ident}")
    target = ENTRIES_DIR / f"{ident}-{slug}.md"
    if target.exists():
        print(f"{target} は既に存在する", file=sys.stderr)
        return 2
    target.write_text(body, encoding="utf-8")
    print(f"created {target.relative_to(ROOT)}（TEMPLATE のコメントを埋めてから --validate）")
    return 0


def _print_stats(entries: list[Entry]) -> int:
    def show(title: str, counter: Counter) -> None:
        print(f"[{title}]")
        for key, n in counter.most_common():
            print(f"  {key}: {n}")

    show("主分類", Counter((e.meta.get("classification") or {}).get("primary") for e in entries))
    show("分類の確定状態", Counter((e.meta.get("classification") or {}).get("review") for e in entries))
    show("状態", Counter(e.meta.get("status") for e in entries))
    show("型", Counter(str(e.meta.get("pattern")) for e in entries))
    show("発見観点（主）", Counter((_as_list((e.meta.get("discovery") or {}).get("perspective")) or [None])[0] for e in entries))
    show("解決観点（主）", Counter((_as_list((e.meta.get("resolution") or {}).get("perspective")) or [None])[0] for e in entries))
    show("層", Counter(l for e in entries for l in _as_list((e.meta.get("feature_context") or {}).get("layers"))))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="index.md が最新か検査するだけ（差分があれば非0）")
    parser.add_argument("--validate", action="store_true", help="エントリの規約検証だけ行う")
    parser.add_argument("--stats", action="store_true", help="分布（主分類・型・観点・層）を表示する")
    parser.add_argument("--new", metavar="SLUG", help="次の番号で TEMPLATE から雛形エントリを作る")
    args = parser.parse_args(argv)

    if args.new:
        return _scaffold(args.new)

    entries = load_entries()
    dictionary_text = DICTIONARY_DOC.read_text(encoding="utf-8")
    if args.validate:
        return _print_validation(entries, dictionary_text)
    if args.stats:
        return _print_stats(entries)

    rendered = render_index(entries, dictionary_text) + "\n"
    if args.check:
        current = INDEX_DOC.read_text(encoding="utf-8") if INDEX_DOC.exists() else ""
        if current != rendered:
            print("index.md が最新ではない。再生成すること:", file=sys.stderr)
            print("  backend/.venv/bin/python backend/scripts/issue_knowledge_index.py", file=sys.stderr)
            return 1
        print("index.md は最新")
        return 0
    INDEX_DOC.write_text(rendered, encoding="utf-8")
    print(f"wrote {INDEX_DOC.relative_to(ROOT)} ({len(entries)} entries)")
    return _print_validation(entries, dictionary_text)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
