"""要素種別ごとの**ラベルラダー**（可読な一行の生成）の正本。

正本文書: `docs/features/element_context_presentation_redesign.md` §2.3 / §5
（不変条項 CP1「ラベルは見出し、説明は補足行」/ CP4「訳語の正本は1組」/
CP5「切り詰めは1実装」/ CP7「ラベルは区別可能」/ CP10「空は沈黙ではない」、
および LE6′「可読性の正本は W層」）。

なぜ必要か（RC1/RC2/RC3/RC4/RC6）: 従来 W層の投影は「plain_text→latex→id の
``[:80]`` 機械切り」でラベルを作っていたため、生 TeX・内部ID・英語 stage 名・
機械連結文が同じ ``label`` に混ざり、下流（学習者射影）は器ごと一般ラベルへ潰す
しかなくなっていた（「導出の流れ」が2件並んで区別不能）。本モジュールは
**内部ID・生 TeX・英語 stage 生名をラダーの候補に入れない**という制約のもとで、
種別ごとに「必ず人間可読な一行」+「1行の区別材料（``sublabel``）」を作る。

設計上の約束:

* **純粋関数のみ**。DB / FastAPI / LLM / sqlalchemy を import しない。入力は
  ``context_lens`` が扱う raw artifact 形の dict（``asdict`` 相当）と DB 行 dict。
* 生成した ``Label`` は ``text == sublabel`` にならない（同一なら sublabel を空に）。
* 解決に失敗したら一般ラベル + ``unresolved=True``。それでも ``sublabel`` に
  区別材料を残す（CP7）。
* TeX 判定は ``core.text_excerpt.looks_like_tex_math``、切り詰めは
  ``core.text_excerpt.excerpt`` のみを使う（第2実装を作らない）。
* 訳語は ``core.element_vocab`` のみから引く（訳語文字列を直書きしない）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from core.element_vocab import (
    chain_type_label,
    claim_type_label,
    definition_missing_fact,
    definition_status_label,
    equation_role_label,
    link_status_fact,
    operation_label,
    theory_stage_key,
    theory_stage_label,
)
from core.text_excerpt import excerpt, first_sentence, looks_like_tex_math, normalize_whitespace

__all__ = [
    "LABEL_SOURCE_CONTROLLED_VOCAB",
    "LABEL_SOURCE_EXPLANATION",
    "LABEL_SOURCE_GENERIC",
    "LABEL_SOURCE_PAPER_NUMBER",
    "LABEL_SOURCE_SEMANTIC_SUMMARY",
    "LABEL_SOURCE_TEXT",
    "Label",
    "claim_label",
    "component_label",
    "contains_internal_equation_id",
    "derivation_label",
    "derivation_operation_summary",
    "equation_label",
    "evidence_label",
    "figure_label",
    "format_equation_number",
    "graph_node_label",
    "is_internal_id_like",
    "looks_like_paper_equation_number",
    "self_described_role",
    "symbol_label",
]


# ── Label 契約（DTO v2 の ITEM が受け取る形。§4.1）────────────────────────────

LABEL_SOURCE_EXPLANATION = "explanation"
LABEL_SOURCE_SEMANTIC_SUMMARY = "semantic_summary"
LABEL_SOURCE_PAPER_NUMBER = "paper_number"
LABEL_SOURCE_TEXT = "text"
LABEL_SOURCE_CONTROLLED_VOCAB = "controlled_vocab"
LABEL_SOURCE_GENERIC = "generic"


@dataclass(frozen=True)
class Label:
    """一行ラベル + 区別材料。

    text
        見出しの一行。**TeX・内部IDを入れない**（CP1 / EC3′）。
    sublabel
        1行の区別材料・事実文（説明・理由・意味）。``text`` と同一文字列にしない。
    qualifier
        種別内サブ種別の**統制語彙キー**（chain_type / stage キー /
        definition_status / graph_layer など）。訳は表示側（element-vocab）。
    label_source
        来歴。教員向けにのみ出す（学習者射影で落とす）。
    unresolved
        ラダーが尽き一般名で代替したことの明示（カードが淡色表示に使う）。
    """

    text: str = ""
    sublabel: str = ""
    qualifier: str = ""
    label_source: str = ""
    unresolved: bool = False


# ── 内部 ID / 式番号の判定 ────────────────────────────────────────────────────
#
# ``core/element_context.py`` の遮断層（_UUID_LABEL_RE / _INTERNAL_ID_LABEL_RES /
# _EMBEDDED_INTERNAL_ID_RE）と整合させる。あちらは「最後の砦」として維持し、
# こちらは**生成時点で**候補から外すための判定（EC3′）。

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# ラベル先頭が裸の内部 ID である形。
_INTERNAL_ID_PREFIX_RES = (
    re.compile(r"^ev(?:idence)?_[0-9]", re.IGNORECASE),       # ev_0001
    re.compile(r"^synth_", re.IGNORECASE),                     # synth_claim_0001
    re.compile(r"^claim_", re.IGNORECASE),                     # claim_span_001
    re.compile(r"^span_[0-9]", re.IGNORECASE),                 # span_001
    re.compile(r"^support:"),                                  # support:<section>:<idx>
    re.compile(r"^node_", re.IGNORECASE),                      # graph node id
    re.compile(r"^comp_", re.IGNORECASE),                      # component id
    re.compile(r"^theory_op_", re.IGNORECASE),                 # main graph node id
    re.compile(r"^eq_op_", re.IGNORECASE),                     # equation_detail node id
    re.compile(r"^step_[0-9]", re.IGNORECASE),                 # step_001
    re.compile(r"^sys_[0-9]+_step_[0-9]+", re.IGNORECASE),
    re.compile(r"^(?:system_)?derivation_", re.IGNORECASE),
)

# 文中に**埋め込まれた**内部 ID（「導出「derivation_eq_tex_b16」のステップ
# 「step_001」」のような機械連結文を候補から外す）。
_EMBEDDED_INTERNAL_ID_RES = (
    re.compile(r"derivation_[A-Za-z0-9_]+", re.IGNORECASE),
    re.compile(r"system_derivation_[0-9]+", re.IGNORECASE),
    re.compile(r"(?:^|[^A-Za-z0-9])sys_[0-9]+_step_[0-9]+", re.IGNORECASE),
    re.compile(r"(?:^|[^A-Za-z0-9])step_[0-9]+", re.IGNORECASE),
    re.compile(r"(?:^|[^A-Za-z0-9])theory_op_[0-9]+", re.IGNORECASE),
    re.compile(r"(?:^|[^A-Za-z0-9])eq_op_[0-9]+", re.IGNORECASE),
    re.compile(r"(?:^|[^A-Za-z0-9])(?:ev|evidence)_[0-9]{3,}", re.IGNORECASE),
    re.compile(r"(?:^|[^A-Za-z0-9])synth_[A-Za-z0-9_]*[0-9]", re.IGNORECASE),
    re.compile(r"(?:^|[^A-Za-z0-9])span_[0-9]{3,}", re.IGNORECASE),
)

# 論文の式番号の**厳密形**: ``eq_2_7`` / ``eq.3.14`` / ``(3.14)`` / ``2.7``。
# 合成 ID ``eq_tex_b14`` は「eq の直後が数字でない」ため一致しない（§5.1）。
_PAPER_EQUATION_NUMBER_RE = re.compile(
    r"^\(?\s*(?:eq\.?|equation|式)?[\s_\-.]?([0-9]+(?:[._\-][0-9]+)*)\s*\)?$",
    re.IGNORECASE,
)

# 文中の式 ID トークン（``eq_tex_b16`` / ``eq_2_7``）。
_EQUATION_ID_TOKEN_RE = re.compile(r"(?:^|[^A-Za-z0-9])(eq[_\-.][A-Za-z0-9_.\-]+)", re.IGNORECASE)


def looks_like_paper_equation_number(value: object) -> bool:
    """論文の式番号（``eq_2_7`` / ``(3.14)``）かどうか。

    合成 ID（``eq_tex_b14`` / ``eq_block_3``）は式番号ではない（§5.1）。
    """
    raw = str(value if value is not None else "").strip()
    if not raw:
        return False
    return bool(_PAPER_EQUATION_NUMBER_RE.match(raw))


def contains_internal_equation_id(value: object) -> bool:
    """文中に「論文の式番号ではない式 ID」が含まれるか。

    ``Define eq_tex_b16`` 型の traceability ラベルを弾くための判定（§5.3）。
    ``eq_2_7`` のような論文式番号は含まれていても内部 ID とみなさない。
    """
    raw = str(value if value is not None else "")
    for match in _EQUATION_ID_TOKEN_RE.finditer(raw):
        token = match.group(1).rstrip(".,;:)]）」』")
        if not looks_like_paper_equation_number(token):
            return True
    return False


def is_internal_id_like(value: object) -> bool:
    """ラベル候補が内部 ID（またはそれを埋め込んだ機械連結文）かどうか。

    ``core/element_context.py`` の遮断層と同じ語彙を、生成側で先に弾くために使う。
    """
    raw = str(value if value is not None else "").strip()
    if not raw:
        return False
    if _UUID_RE.search(raw):
        return True
    if any(pattern.match(raw) for pattern in _INTERNAL_ID_PREFIX_RES):
        return True
    if any(pattern.search(raw) for pattern in _EMBEDDED_INTERNAL_ID_RES):
        return True
    if contains_internal_equation_id(raw):
        return True
    return False


def format_equation_number(value: object) -> str:
    """式番号を読み手向けの表記へ整える（``eq_2_7`` → ``式 (2.7)``）。"""
    raw = str(value if value is not None else "").strip()
    match = _PAPER_EQUATION_NUMBER_RE.match(raw)
    if not match:
        return ""
    digits = re.split(r"[._\-]", match.group(1))
    return "式 (" + ".".join(d for d in digits if d) + ")"


# ── 共通ヘルパ ────────────────────────────────────────────────────────────────


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _usable(value: Any) -> bool:
    """ラベル候補として採用してよい文字列か（空 / TeX / 内部 ID を弾く）。"""
    raw = _text(value)
    if not raw:
        return False
    if looks_like_tex_math(raw):
        return False
    if is_internal_id_like(raw):
        return False
    return True


def _finalize(
    text: str,
    *,
    sublabel: str = "",
    qualifier: str = "",
    label_source: str = "",
    unresolved: bool = False,
) -> Label:
    """``text == sublabel`` を作らない（CP1）で Label を組み立てる。"""
    head = _text(text)
    sub = _text(sublabel)
    if sub and sub == head:
        sub = ""
    return Label(
        text=head,
        sublabel=sub,
        qualifier=_text(qualifier),
        label_source=_text(label_source),
        unresolved=bool(unresolved),
    )


def _first_usable(candidates: list[Any], limit: int) -> str:
    for candidate in candidates:
        raw = _text(candidate)
        if not _usable(raw):
            continue
        cut = excerpt(raw, limit)
        if cut and _usable(cut):
            return cut
    return ""


def _joined(parts: list[str], separator: str = " ／ ") -> str:
    return separator.join([p for p in (_text(x) for x in parts) if p])


# ── 数式（§5.1）───────────────────────────────────────────────────────────────

_EQUATION_TEXT_LIMIT = 44
_EQUATION_SUBLABEL_LIMIT = 90
_GENERIC_EQUATION_LABEL = "数式"


def _equation_semantics(record: dict[str, Any]) -> dict[str, Any]:
    return _dict(record.get("semantics"))


def _equation_math_texts(record: dict[str, Any]) -> set[str]:
    """この式の「数式そのもの」の文字列集合（latex / plain_text / raw_text）。

    ラベル候補がここに含まれていたら採用しない。``needs_math_review`` の式でも
    そもそも latex 系を参照しない設計だが、equations.json 形（フラットキー）と
    asdict 形の両方を受けるため、**取り違えの安全網**として明示的に弾く（EH1）。
    """
    texts: set[str] = set()
    source = _dict(record.get("source_extraction"))
    reconstruction = _dict(record.get("reconstruction"))
    for holder in (record, source, reconstruction):
        for key in ("latex", "plain_text", "raw_text"):
            value = _text(holder.get(key))
            if value:
                texts.add(value)
    return texts


def _equation_role(record: dict[str, Any]) -> str:
    semantics = _equation_semantics(record)
    return _text(record.get("role_in_argument") or semantics.get("role_in_argument"))


def _equation_summary(record: dict[str, Any]) -> str:
    semantics = _equation_semantics(record)
    return _text(semantics.get("summary") or record.get("semantic_kind"))


def _equation_defined_symbols(
    record: dict[str, Any], symbols: list[dict[str, Any]] | None
) -> list[str]:
    """この式が定義する記号（決定論。推測しない）。"""
    out: list[str] = []
    seen: set[str] = set()

    def _add(symbol: Any) -> None:
        name = _text(symbol)
        if not name or name in seen or len(name) > 24:
            return
        seen.add(name)
        out.append(name)

    semantics = _equation_semantics(record)
    for raw in _list(semantics.get("defined_symbols")):
        entry = _dict(raw)
        if _text(entry.get("definition_status")) in ("defined", "redefined"):
            _add(entry.get("symbol"))
    # equations.json 形（to_equations_export の symbols[]）。
    for raw in _list(record.get("symbols")):
        entry = _dict(raw)
        if entry.get("defined_here") or _text(entry.get("definition_status")) in (
            "defined",
            "redefined",
        ):
            _add(entry.get("symbol"))
    if out:
        return out
    equation_id = _text(record.get("equation_id"))
    for raw in symbols or []:
        entry = _dict(raw)
        defining = [_text(x) for x in _list(entry.get("defining_equation_ids"))]
        if equation_id and equation_id in defining:
            _add(entry.get("canonical_symbol"))
    return out


def equation_label(
    record: dict[str, Any] | None,
    *,
    explanation: str | None = None,
    symbols: list[dict[str, Any]] | None = None,
) -> Label:
    """数式のラベルラダー（§5.1）。

    ① 二層説明 contextual の第1文（``explanation``。**呼び出し側が approved 済み
       本文を渡した場合のみ**。Phase 3 で結線）
    ② 論文の式番号（``label`` / ``equation_id`` の厳密形。合成 ID は式番号ではない）
    ③ 記号 + 役割の決定論合成（「δ(t,x) を定義する式」）
    ④ ``semantics.summary`` の第1文（原文言語のまま・切り詰め）
    ⑤ ``role_in_argument`` 訳 + 「式」（「定義式」）
    ⑥ 「数式」+ ``unresolved``

    **latex / plain_text / raw_text / 内部 ID はどの段でも使わない**（EH1 / RC2）。
    """
    data = _dict(record)
    if not data:
        return _finalize(
            _GENERIC_EQUATION_LABEL, label_source=LABEL_SOURCE_GENERIC, unresolved=True
        )

    math_texts = _equation_math_texts(data)
    role = _equation_role(data)
    summary = _equation_summary(data)
    defined = _equation_defined_symbols(data, symbols)
    semantics = _equation_semantics(data)
    # qualifier は「種別内サブ種別」の統制語彙キー。role_in_argument を優先するのは
    # 訳語表（EQUATION_ROLE_LABELS）が Python / JS 両方にあるため（equation_type は
    # 訳語表を持たないので、フロントは fail-closed でチップを出さない）。
    qualifier = role or _text(semantics.get("equation_type") or data.get("equation_type"))

    def _clean(candidate: str) -> str:
        value = _text(candidate)
        if not value or value in math_texts:
            return ""
        return value if _usable(value) else ""

    text = ""
    source = ""

    # ① 二層説明（approved のみ。渡されたときだけ）
    candidate = _clean(first_sentence(explanation))
    if candidate:
        cut = excerpt(candidate, _EQUATION_TEXT_LIMIT + 16)
        if cut and _usable(cut):
            text, source = cut, LABEL_SOURCE_EXPLANATION

    # ② 論文の式番号
    if not text:
        for raw in (data.get("label"), data.get("equation_id")):
            if looks_like_paper_equation_number(raw):
                text, source = format_equation_number(raw), LABEL_SOURCE_PAPER_NUMBER
                break

    # ③ 記号 + 役割の決定論合成
    if not text and defined and role == "definition":
        symbol_text = "・".join(defined[:2])
        if _clean(symbol_text):
            text = symbol_text + " を定義する式"
            source = LABEL_SOURCE_CONTROLLED_VOCAB

    # ④ semantics.summary の第1文
    if not text:
        candidate = _clean(first_sentence(summary))
        if candidate:
            cut = excerpt(candidate, _EQUATION_TEXT_LIMIT)
            if cut and _usable(cut):
                text, source = cut, LABEL_SOURCE_SEMANTIC_SUMMARY

    # ⑤ 役割訳 + 「式」
    if not text:
        role_ja = equation_role_label(role)
        if role_ja:
            text, source = role_ja + "式", LABEL_SOURCE_CONTROLLED_VOCAB

    unresolved = False
    if not text:
        text, source, unresolved = _GENERIC_EQUATION_LABEL, LABEL_SOURCE_GENERIC, True

    # sublabel = 区別材料（意味の一行 → 記号 → 前段が無い理由の事実文）
    sublabel = ""
    if source != LABEL_SOURCE_SEMANTIC_SUMMARY:
        candidate = _clean(first_sentence(summary))
        if candidate:
            sublabel = excerpt(candidate, _EQUATION_SUBLABEL_LIMIT)
            if sublabel and not _usable(sublabel):
                sublabel = ""
    if not sublabel and defined:
        joined = "・".join(defined[:3])
        if _clean(joined):
            sublabel = "記号: " + joined
    if not sublabel:
        sublabel = link_status_fact(semantics.get("link_status") or data.get("link_status")) or ""

    return _finalize(
        text,
        sublabel=sublabel,
        qualifier=qualifier,
        label_source=source,
        unresolved=unresolved,
    )


# ── 導出（§5.2）───────────────────────────────────────────────────────────────

_DERIVATION_TEXT_LIMIT = 60
_DERIVATION_SUBLABEL_LIMIT = 90
_GENERIC_DERIVATION_LABEL = "導出"

# ``teaching_takeaway`` の英語テンプレ形（"Derive result eq_..." 等）。無検査で採用すると
# 内部 ID とテンプレ英文がそのままラベルになる（§5.2 ②）。
_TAKEAWAY_TEMPLATE_RE = re.compile(r"^(?:Derive|Define|Eliminate|Extract)\s", re.IGNORECASE)

_FOCUS_ROLE_LABELS = {
    "input": "入力",
    "output": "出力",
    "intermediate": "中間",
    "condition": "条件",
}


def derivation_operation_summary(chain: dict[str, Any] | None, *, translate: bool = False) -> str:
    """chain の step 操作列を ``op1 → op2 → …`` に連結する（純粋関数・非LLM）。

    既定は artifact の生の操作キー（``transform → linearize``。設計書 §7 のモック表記）。
    ``translate=True`` で統制語彙の訳語（未知キーは生キーのまま fail-soft）を返す。
    """
    operations: list[str] = []
    for raw in _list(_dict(chain).get("steps")):
        step = _dict(raw)
        operation = _text(step.get("operation_subtype") or step.get("operation"))
        if not operation:
            continue
        if translate:
            operation = operation_label(operation) or operation
        operations.append(operation)
    return " → ".join(operations)


def _derivation_operation_key(chain: dict[str, Any]) -> str:
    return _text(chain.get("operation")) or _text(chain.get("operation_family"))


def derivation_label(
    chain: dict[str, Any] | None, *, focus_role: str | None = None
) -> Label:
    """導出のラベルラダー（§5.2）。

    ① ``operation``（無ければ ``operation_family``）訳 + ``chain_type`` 訳
    ② ``teaching_takeaway``（内部 ID 含有・英語テンプレ形は**不採用**）
    ③ steps の操作列の先頭2つ（「線形化 → 消去（3ステップ）」）
    ④ 「導出」+ ``unresolved``

    ``qualifier`` は ``chain_type``（区別不能な2件はこれだけで解ける）。
    ``focus_role`` はこの導出における中心要素の役割（input / output / intermediate /
    condition）で、与えられれば見出しへ「（入力）」のように添える。
    """
    data = _dict(chain)
    chain_type = _text(data.get("chain_type"))
    chain_ja = chain_type_label(chain_type)

    text = ""
    source = ""

    # ① 操作訳 + 種別訳
    operation_ja = operation_label(_derivation_operation_key(data))
    if operation_ja:
        text = operation_ja + ("（" + chain_ja + "）" if chain_ja else "")
        source = LABEL_SOURCE_CONTROLLED_VOCAB

    # ② teaching_takeaway（テンプレ形・内部 ID 含有は不採用）
    if not text:
        takeaway = first_sentence(data.get("teaching_takeaway"))
        if takeaway and not _TAKEAWAY_TEMPLATE_RE.match(takeaway) and _usable(takeaway):
            cut = excerpt(takeaway, _DERIVATION_TEXT_LIMIT)
            if cut and _usable(cut):
                text, source = cut, LABEL_SOURCE_TEXT

    # ③ 操作列の先頭2つ
    if not text:
        steps = [_dict(s) for s in _list(data.get("steps"))]
        translated = []
        for step in steps:
            label = operation_label(step.get("operation_subtype") or step.get("operation"))
            if label:
                translated.append(label)
        if translated:
            head = " → ".join(translated[:2])
            if len(steps) > 2:
                head += "（" + str(len(steps)) + "ステップ）"
            text, source = head, LABEL_SOURCE_CONTROLLED_VOCAB

    unresolved = False
    if not text:
        text, source, unresolved = _GENERIC_DERIVATION_LABEL, LABEL_SOURCE_GENERIC, True
        if chain_ja:
            text = chain_ja

    role_ja = _FOCUS_ROLE_LABELS.get(_text(focus_role))
    if role_ja:
        text = text + "（" + role_ja + "）"

    # sublabel = 操作列 + 成立条件（区別材料）
    parts: list[str] = []
    summary = derivation_operation_summary(data)
    if summary:
        parts.append("操作: " + summary)
    conditions = [_text(c) for c in _list(data.get("conditions")) if _text(c)]
    if conditions:
        condition = excerpt(first_sentence(conditions[0]), _DERIVATION_SUBLABEL_LIMIT)
        if condition:
            parts.append("条件: " + condition)
    sublabel = _joined(parts)
    if sublabel and is_internal_id_like(sublabel):
        sublabel = ""

    return _finalize(
        text,
        sublabel=sublabel,
        qualifier=chain_type,
        label_source=source,
        unresolved=unresolved,
    )


# ── 論理要素（TheoryOperationGraph ノード, §5.3）──────────────────────────────

_NODE_SUBLABEL_LIMIT = 90
_NODE_TEXT_LIMIT = 60
_GENERIC_STAGE_LABEL = "理論の段階"
_GENERIC_NODE_LABEL = "論理要素"

GRAPH_LAYER_MAIN = "main"
GRAPH_LAYER_EQUATION_DETAIL = "equation_detail"


def _node_description_sentence(node: dict[str, Any], limit: int) -> str:
    """``node.description``（#308 が「長い説明はここへ」と定めた欄）の第1文。"""
    candidate = first_sentence(node.get("description"))
    if not _usable(candidate):
        return ""
    cut = excerpt(candidate, limit)
    return cut if cut and _usable(cut) else ""


def _equation_labeler_text(
    equation_labeler: Callable[[str], Any] | None, equation_id: str
) -> str:
    if not equation_labeler or not equation_id:
        return ""
    try:
        result = equation_labeler(equation_id)
    except Exception:  # noqa: BLE001 — 表示のための best-effort（fail-soft）
        return ""
    if isinstance(result, Label):
        return "" if result.unresolved else _text(result.text)
    return _text(result)


def graph_node_label(
    node: dict[str, Any] | None,
    *,
    equation_labeler: Callable[[str], Any] | None = None,
) -> Label:
    """TheoryOperationGraph ノードのラベル（``graph_layer`` で分岐。§5.3 / CP3）。

    main（理論段階）: ``label``（英語 stage 表示名）→ stage キー → **訳語**。
    sublabel は ``node.description`` の第1文、qualifier は stage キー。

    equation_detail（式単位の操作）: ① ``visual_label`` ② ``display_label``
    ③ ``operation`` 訳 + 対象（``theory_object`` または出力式の可読ラベル）
    ④ ``description`` 第1文 ⑤ 「論理要素」+ ``unresolved``。
    ``Define eq_tex_b16`` 型（動詞 + 式 ID）の label は**採用しない**。
    """
    data = _dict(node)
    layer = _text(data.get("graph_layer")) or GRAPH_LAYER_MAIN

    if layer == GRAPH_LAYER_MAIN:
        stage_key = theory_stage_key(data.get("label"))
        if not stage_key:
            stage_key = theory_stage_key(data.get("visual_label"))
        description = _node_description_sentence(data, _NODE_SUBLABEL_LIMIT)
        if stage_key:
            return _finalize(
                theory_stage_label(stage_key),
                sublabel=description,
                qualifier=stage_key,
                label_source=LABEL_SOURCE_CONTROLLED_VOCAB,
            )
        if description:
            return _finalize(
                excerpt(description, _NODE_TEXT_LIMIT),
                sublabel="",
                qualifier="",
                label_source=LABEL_SOURCE_TEXT,
            )
        return _finalize(
            _GENERIC_STAGE_LABEL,
            qualifier="",
            label_source=LABEL_SOURCE_GENERIC,
            unresolved=True,
        )

    # equation_detail（および未知の層は detail と同じ扱い。debug はレーンに出さない
    # 判断が呼び出し側の責務）。
    text = ""
    source = ""
    for key in ("visual_label", "display_label"):
        candidate = _text(data.get(key))
        if candidate and _usable(candidate):
            cut = excerpt(candidate, _NODE_TEXT_LIMIT)
            if cut and _usable(cut):
                text, source = cut, LABEL_SOURCE_TEXT
                break

    if not text:
        operation_ja = operation_label(data.get("operation"))
        if operation_ja:
            target = _text(data.get("theory_object"))
            if not _usable(target):
                target = ""
            if not target:
                outputs = [_text(x) for x in _list(data.get("output_equation_ids")) if _text(x)]
                if outputs:
                    target = _equation_labeler_text(equation_labeler, outputs[0])
            text = operation_ja + (": " + target if target else "")
            source = LABEL_SOURCE_CONTROLLED_VOCAB

    description = _node_description_sentence(data, _NODE_SUBLABEL_LIMIT)
    if not text and description:
        text, source = excerpt(description, _NODE_TEXT_LIMIT), LABEL_SOURCE_TEXT

    unresolved = False
    if not text:
        text, source, unresolved = _GENERIC_NODE_LABEL, LABEL_SOURCE_GENERIC, True

    sublabel = description
    if not sublabel:
        candidate = _text(data.get("theory_object"))
        sublabel = candidate if _usable(candidate) else ""

    return _finalize(
        text,
        sublabel=sublabel,
        qualifier=GRAPH_LAYER_EQUATION_DETAIL,
        label_source=source,
        unresolved=unresolved,
    )


# ── 記号（§5.4）───────────────────────────────────────────────────────────────

_SYMBOL_SUBLABEL_LIMIT = 90
_GENERIC_SYMBOL_LABEL = "記号"
_SYMBOL_MEANING_UNKNOWN_FACT = "この論文での意味は同定できていません"
_SYMBOL_DEFINED_HERE_NOTE = "この式で定義"


def symbol_label(record: dict[str, Any] | None, *, defined_by_focus: bool = False) -> Label:
    """記号のラベル（§5.4）。

    ``text`` は ``canonical_symbol`` **そのまま**（記号は式の再掲ではなく読解の部品
    なので表示する。TeX 形もそのまま渡し、レンダリング可否はフロントの
    ``looksLikeRenderableTex`` ゲートに委ねる）。

    ``sublabel`` は**意味**（本修正の核・RC5）: ``definition_evidence_texts[0]``
    の第1文 → ``meaning`` → 定義なしの事実文 → 定義状態の訳。空にはしない（CP10）。
    """
    data = _dict(record)
    symbol = _text(data.get("canonical_symbol"))
    if not symbol:
        variants = [_text(v) for v in _list(data.get("notation_variants")) if _text(v)]
        symbol = variants[0] if variants else ""

    definition_status = _text(data.get("definition_status"))
    review_reasons = [_text(r) for r in _list(data.get("review_reasons"))]
    missing = definition_status == "definition_missing" or "definition_missing" in review_reasons

    unresolved = False
    source = LABEL_SOURCE_TEXT
    if not symbol:
        symbol, source, unresolved = _GENERIC_SYMBOL_LABEL, LABEL_SOURCE_GENERIC, True

    meaning = ""
    evidences = [_text(e) for e in _list(data.get("definition_evidence_texts")) if _text(e)]
    for evidence in evidences:
        candidate = first_sentence(evidence)
        if _usable(candidate):
            meaning = excerpt(candidate, _SYMBOL_SUBLABEL_LIMIT)
            if meaning and _usable(meaning):
                break
            meaning = ""
    if not meaning:
        candidate = first_sentence(data.get("meaning"))
        if _usable(candidate):
            meaning = excerpt(candidate, _SYMBOL_SUBLABEL_LIMIT)
            if meaning and not _usable(meaning):
                meaning = ""
    if not meaning and missing:
        meaning = definition_missing_fact()
    if not meaning:
        meaning = definition_status_label(definition_status)
    if not meaning:
        meaning = _SYMBOL_MEANING_UNKNOWN_FACT

    if defined_by_focus:
        meaning = meaning + "（" + _SYMBOL_DEFINED_HERE_NOTE + "）"

    return _finalize(
        symbol,
        sublabel=meaning,
        qualifier="definition_missing" if missing else definition_status,
        label_source=source,
        unresolved=unresolved,
    )


# ── 主張（§5.5）───────────────────────────────────────────────────────────────

_CLAIM_TEXT_LIMIT = 60
_CLAIM_QUOTE_LIMIT = 70
_GENERIC_CLAIM_LABEL = "主張"


def claim_label(row_or_record: dict[str, Any] | None) -> Label:
    """主張のラベル（§5.5）。``theory_claims`` の DB 行と ClaimObjectRecord の両対応。"""
    data = _dict(row_or_record)
    text = _first_usable([data.get("text"), data.get("normalized_text")], _CLAIM_TEXT_LIMIT)
    unresolved = False
    source = LABEL_SOURCE_TEXT
    if not text:
        text, source, unresolved = _GENERIC_CLAIM_LABEL, LABEL_SOURCE_GENERIC, True

    claim_type = _text(data.get("claim_type"))
    parts = [claim_type_label(claim_type)]
    quote = first_sentence(data.get("evidence_text") or data.get("evidence_quote"))
    if _usable(quote):
        cut = excerpt(quote, _CLAIM_QUOTE_LIMIT)
        if cut and _usable(cut):
            parts.append("「" + cut + "」")

    return _finalize(
        text,
        sublabel=_joined(parts),
        qualifier=claim_type,
        label_source=source,
        unresolved=unresolved,
    )


# ── コンポーネント / 図 / 本文根拠（§5.6）─────────────────────────────────────

_COMPONENT_TEXT_LIMIT = 60
_COMPONENT_SUBLABEL_LIMIT = 90
_GENERIC_COMPONENT_LABEL = "論理要素"

_FIGURE_TEXT_LIMIT = 70
_FIGURE_SUBLABEL_LIMIT = 90
_GENERIC_FIGURE_LABEL = "図"

_EVIDENCE_TEXT_LIMIT = 70
_GENERIC_EVIDENCE_LABEL = "本文の根拠箇所"


def component_label(record: dict[str, Any] | None) -> Label:
    """コンポーネントのラベル（§5.6）。

    ``component_context.py`` の rich 投影と同じ材料に寄せる:
    text = ``name`` → ``label``、sublabel = ``narrative_role`` →
    ``thesis_context.role_in_thesis`` → ``teaching_takeaway`` → ``summary`` 第1文。
    """
    data = _dict(record)
    text = _first_usable([data.get("name"), data.get("label")], _COMPONENT_TEXT_LIMIT)
    unresolved = False
    source = LABEL_SOURCE_TEXT
    if not text:
        text, source, unresolved = _GENERIC_COMPONENT_LABEL, LABEL_SOURCE_GENERIC, True

    thesis_context = _dict(data.get("thesis_context"))
    sublabel = _first_usable(
        [
            data.get("narrative_role"),
            thesis_context.get("role_in_thesis"),
            data.get("teaching_takeaway"),
            first_sentence(data.get("summary")),
        ],
        _COMPONENT_SUBLABEL_LIMIT,
    )
    return _finalize(
        text,
        sublabel=sublabel,
        qualifier=_text(data.get("component_type")),
        label_source=source,
        unresolved=unresolved,
    )


def figure_label(row: dict[str, Any] | None, *, part_labels: list[str] | None = None) -> Label:
    """図のラベル（§5.6）。caption 第1文 → ``figure_label`` → 一般ラベル。

    ``qualifier`` は提示モードの統制語彙キー（``effective_mode``。訳は表示側）。
    """
    data = _dict(row)
    caption = first_sentence(data.get("caption_text") or data.get("caption"))
    text = _first_usable([caption, data.get("figure_label")], _FIGURE_TEXT_LIMIT)
    unresolved = False
    source = LABEL_SOURCE_TEXT
    if not text:
        text, source, unresolved = _GENERIC_FIGURE_LABEL, LABEL_SOURCE_GENERIC, True

    parts = [_text(p) for p in (part_labels or []) if _text(p)]
    sublabel = ""
    if parts:
        sublabel = excerpt("・".join(parts[:4]), _FIGURE_SUBLABEL_LIMIT)
    if not sublabel:
        # caption の第1文を見出しに使ったので、sublabel は**残り**（重複させない）。
        full_caption = normalize_whitespace(data.get("caption_text") or data.get("caption"))
        remainder = full_caption[len(caption) :].strip() if caption else full_caption
        if remainder and _usable(remainder):
            sublabel = excerpt(remainder, _FIGURE_SUBLABEL_LIMIT)

    mode = _text(data.get("effective_mode") or data.get("reviewed_mode") or data.get("suggested_mode"))
    return _finalize(
        text,
        sublabel=sublabel,
        qualifier=mode,
        label_source=source,
        unresolved=unresolved,
    )


def evidence_label(record: dict[str, Any] | None, *, section_label: str = "") -> Label:
    """本文根拠のラベル（§5.6）。**逐語そのものをラベルにする**。

    逐語がラベルなら「本文の根拠箇所」という情報量ゼロの置換が要らなくなる
    （RC6 の構造的解決）。TeX（equation_quote）の逐語は表示に耐えないため一般
    ラベルへ縮退させる。
    """
    data = _dict(record)
    quote = _text(data.get("evidence_text") or data.get("text"))
    text = ""
    source = LABEL_SOURCE_TEXT
    unresolved = False
    if _usable(quote):
        cut = excerpt(quote, _EVIDENCE_TEXT_LIMIT)
        if cut and _usable(cut):
            text = "「" + cut + "」"
    if not text:
        text, source, unresolved = _GENERIC_EVIDENCE_LABEL, LABEL_SOURCE_GENERIC, True

    sublabel = _text(section_label)
    if sublabel and not _usable(sublabel):
        sublabel = ""
    return _finalize(
        text,
        sublabel=sublabel,
        qualifier=_text(data.get("evidence_role")),
        label_source=source,
        unresolved=unresolved,
    )


# ── 自己記述する役割（§4.4 ②self_described）──────────────────────────────────


def self_described_role(
    element_type: str,
    record: dict[str, Any] | None,
    *,
    stage_key: str | None = None,
) -> str | None:
    """要素が**自分で**名乗る「この文脈での役割」を決定論的に合成する。

    equation は ``role_in_argument`` 訳 + 参加する theory stage 訳から
    「理論の土台の段階で使われる定義式」を作る（stage が無ければ「定義式」）。
    theory_component は既に決定論的に導出済みの役割説明
    （``narrative_role`` / ``thesis_context.role_in_thesis``）をそのまま使う。
    合成できなければ None（推測で穴埋めしない）。
    """
    data = _dict(record)
    kind = _text(element_type)
    if kind == "equation":
        role_ja = equation_role_label(_equation_role(data))
        if not role_ja:
            return None
        stage_ja = theory_stage_label(theory_stage_key(stage_key) or stage_key)
        if stage_ja:
            return stage_ja + "の段階で使われる" + role_ja + "式"
        return role_ja + "式"
    if kind == "theory_component":
        thesis_context = _dict(data.get("thesis_context"))
        for candidate in (data.get("narrative_role"), thesis_context.get("role_in_thesis")):
            value = _text(candidate)
            if value and _usable(value):
                return excerpt(value, _COMPONENT_SUBLABEL_LIMIT)
        return None
    return None
