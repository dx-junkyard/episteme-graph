"""表示投影層（Display Projection）— 画面へ出す前の単一の関所（非LLM・純関数）。

正本設計書: ``docs/features/display_projection_design.md``（DP1〜DP6）。

解析層の内部 ID（``eq_blk_*`` / ``eq_tex_*`` / ``claim_span_*`` / ``theory_op_*`` /
``eq_op_*`` / ``ev_*`` / ``synth_claim_*`` / ``sym_N`` / ``k1:`` 安定キー / UUID …）・
生の数値（confidence / weight / score / 件数）・英語の生成ラベル文が、画面ごとに別々の
遮断器（9 系統以上）でしか止められていなかった。本モジュールはその**語彙を 1 箇所に集め**、
学習者向けルート（``api/display_route.py::LearnerDisplayRoute``）が必ず通る射影
:func:`project_for_learner` を提供する。

配置の規約:

- FastAPI / sqlalchemy / LLM クライアントを import しない（``core.text_excerpt`` の
  純関数だけを読む）。pydantic も import せず、``model_dump`` を持つかで判定する。
- 旧来の遮断器（``learner_context_common`` / ``deliberation.labels`` /
  ``theory_modules.schema`` / ``graph_paper_layer.schema`` / ``course_content_builder`` /
  ``reference_health`` / ``discuss.opening`` / ``doubt.seminar_brief``）は、それぞれの
  挙動を保ったまま**ここで定義した正規表現・キー集合を参照する**（語彙の再定義禁止 = DP2。
  ガードレールが同一オブジェクトであることを検査する）。旧系統ごとに意図的に異なる判定
  （論文の式番号を例外にするか等）は、系統別の定数としてここに並べて残す。
- 入力を変更しない（新しい dict / list を組み立てて返す）。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from core.text_excerpt import looks_like_tex_math

Audience = Literal["learner", "teacher"]

#: 内部 ID を置き換える読み手向けの語（旧 ``theory_modules.schema`` から移設）。
UNIDENTIFIED_ELEMENT_TEXT = "（本文を特定できない要素）"

#: 英語の生成ラベルを置き換える既定の一般ラベル（element_type が分からないとき）。
GENERIC_LABEL_FALLBACK = "関連する要素"

#: element_type 別の一般ラベル（旧 ``learner_context_common._GENERIC_ITEM_LABELS``）。
GENERIC_ITEM_LABELS: dict[str, str] = {
    "theory_claim": "関連する主張",
    "theory_component": "関連する論理要素",
    "equation": "関連する数式",
    "figure": "図",
    "evidence": "本文の根拠箇所",
    "section": "掲載セクション",
    "thesis": "中心命題",
    "derivation": "導出の流れ",
    "symbol": "記号",
    "stage": "理論の段階",
    "part": "構成部品",
}


def generic_label(element_type: Any) -> str:
    """element_type 別の一般ラベル（未知は :data:`GENERIC_LABEL_FALLBACK`）。"""
    return GENERIC_ITEM_LABELS.get(str(element_type or ""), GENERIC_LABEL_FALLBACK)


# ===========================================================================
# 1. 統合語彙（本層の判定の正本）
# ===========================================================================

_UUID_BODY = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

#: 文字列全体が UUID。
UUID_FULL_RE = re.compile(r"^" + _UUID_BODY + r"$")
#: 文中のどこかに UUID。
UUID_ANY_RE = re.compile(_UUID_BODY)

#: 論文の式番号の厳密形: ``eq_2_7`` / ``eq.3.14`` / ``(3.14)`` / ``2.7``（旧
#: ``deliberation.labels._PAPER_EQUATION_NUMBER_RE``）。合成 ID ``eq_tex_b14`` は一致しない。
PAPER_EQUATION_NUMBER_RE = re.compile(
    r"^\(?\s*(?:eq\.?|equation|式)?[\s_\-.]?([0-9]+(?:[._\-][0-9]+)*)\s*\)?$",
    re.IGNORECASE,
)

# 数字を含むことを要求するプレフィックス群（``claim_type`` / ``derivation_in`` /
# ``node_id`` のような語彙語・キー名は数字を含まないため一致しない）。長いものを先に置く。
# 直後が数字であることを要求するもの（``step_001`` は捕まえ、人が付けた骨格 ID
# ``step_d1`` のような綴りは捕まえない — 旧系統も ``step_[0-9]`` / ``span_[0-9]`` 形）。
_IMMEDIATE_DIGIT_PREFIXES = (
    r"theory_op_",
    r"evidence_",
    r"ev_",
    r"comp_",
    r"node_",
    r"span_",
    r"sys_[0-9]+_step_",
    r"step_",
    r"blk_",
    r"section_",
    r"sec_",
    r"figure_",
    r"fig_",
    r"sym_",
)
# 本体のどこかに数字があればよいもの（``derivation_eq_tex_b16`` / ``synth_claim_0001`` /
# ``eq_eqcand_inline_blk_3df32664_``）。
_ANYWHERE_DIGIT_PREFIXES = (
    r"system_derivation_",
    r"derivation_",
    r"synth_",
    r"claim_span_",
    r"claim_",
    r"eqcand_",
    # TeX 経路の裸のブロック ID（``tex_b14``。``latex_…`` は語境界で当たらない）。
    r"tex_",
    # 汎用の式 ID（eq_op_ / eq_blk_ / eq_tex_ / eq_eqcand_ を含む）。論文の式番号
    # （eq_12 / eq_2_7）は直後が「数字と区切りだけで終わる」ため除外する。
    r"eq[_\-](?![0-9]+(?:[._\-][0-9]+)*(?![A-Za-z0-9_]))",
)

_TAIL = r"[A-Za-z0-9_\-]*(?:\.[A-Za-z0-9_\-]+)*"

#: 内部 ID トークン（語境界付き）。本層の統合語彙。
INTERNAL_ID_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?:" + "|".join(_IMMEDIATE_DIGIT_PREFIXES) + r")[0-9]" + _TAIL
    + r"|(?:" + "|".join(_ANYWHERE_DIGIT_PREFIXES) + r")(?=[A-Za-z0-9_.\-]*[0-9])" + _TAIL
    + r"|k1:[0-9a-f]{8,}(?:#[0-9]+)?"
    + r"|support:[A-Za-z0-9_\-]+(?::[A-Za-z0-9_\-]+)*"
    + r"|" + _UUID_BODY
    + r")",
    re.IGNORECASE,
)


def is_internal_id_token(value: Any) -> bool:
    """文字列**全体**が内部 ID トークン 1 つか。"""
    text = str(value if value is not None else "").strip()
    return bool(text) and bool(INTERNAL_ID_TOKEN_RE.fullmatch(text))


def contains_internal_id(value: Any) -> bool:
    """文字列のどこかに内部 ID トークンが含まれるか（論文の式番号は含まない）。"""
    return bool(INTERNAL_ID_TOKEN_RE.search(str(value if value is not None else "")))


#: 本文に埋め込まれた解決可能な参照（番地）: ``![[component:comp_001]]`` /
#: ``![[equation: [[eq_x]] ]]`` / ``[[FORMULA_3]]``。クライアントが ``evidence_items[].id`` や
#: ``formulas`` に照合して描くので、中の ID を置き換えると描画が壊れる（DP3）。1 段の入れ子まで。
EMBED_MARKER_RE = re.compile(r"!?\[\[(?:[^\[\]\n]|\[\[[^\[\]\n]*\]\])*\]\]")


def mask_internal_ids(value: Any, replacement: str = UNIDENTIFIED_ELEMENT_TEXT) -> str:
    """文字列中の内部 ID トークンを ``replacement`` に置き換える（前後の空白は保つ）。

    埋め込み参照（:data:`EMBED_MARKER_RE`）の内側は番地なので置き換えない。
    """
    text = str(value if value is not None else "")
    parts: list[str] = []
    cursor = 0
    for marker in EMBED_MARKER_RE.finditer(text):
        parts.append(INTERNAL_ID_TOKEN_RE.sub(replacement, text[cursor:marker.start()]))
        parts.append(marker.group(0))
        cursor = marker.end()
    parts.append(INTERNAL_ID_TOKEN_RE.sub(replacement, text[cursor:]))
    return "".join(parts)


#: 値が「画面遷移・再取得のための番地」であるキー（DP3: 値を遮断しない）。
#: ``id`` / ``*_id`` / ``*_ids`` / ``*_key`` / ``*_ref`` と、グラフの端点・選択肢の値。
ID_KEY_RE = re.compile(
    r"^(?:id|ids|ref|refs|href|src|url|urls|path|source|target|from|to|value)$"
    r"|_(?:id|ids|key|keys|ref|refs|uuid|url|urls|href|src|path)$"
)

#: 値そのものが番地である文字列（API のパス・外部 URL）。キーを問わず遮断しない。
ADDRESS_VALUE_RE = re.compile(r"^(?:/api/|https?://)", re.IGNORECASE)


def is_address_key(key: Any) -> bool:
    return bool(ID_KEY_RE.search(str(key or "")))


def is_address_value(value: Any) -> bool:
    return isinstance(value, str) and bool(ADDRESS_VALUE_RE.match(value.strip()))


#: 学習者向け応答に出してはならないキー（全系統の和。値ごと落とす）。
FORBIDDEN_KEYS_LEARNER: frozenset[str] = frozenset({
    "confidence",
    "weight",
    "score",
    "candidate_score",
    "load_score",
    "qualification_reason",
    "stable_key",
    "produced_by_run_id",
    "superseded_at",
    "superseded_by_run_id",
    "fingerprint",
    "structure_fingerprint",
    "interface_width",
    "interface_size",
    "k",
    "n",
    "count",
    "counts",
    "n_users",
    "n_items",
    "dependent_count",
    "member_count",
    "module_count",
    "consumer_count",
    "multiplicity",
    "cosine",
    "similarity",
    "distance",
})

#: 表示文のキー（値が 1 トークンの内部 ID でも置き換える）。``*_label`` / ``*_text`` /
#: ``*_title`` / ``*_note`` / ``*_line`` / ``*_fact`` も表示文として扱う（:func:`is_display_key`）。
DISPLAY_KEYS: frozenset[str] = frozenset({
    "label", "sublabel", "title", "name", "text", "summary", "description", "caption",
    "heading", "message", "detail", "note", "notice", "fact", "facts", "statement",
    "quote", "passage", "answer", "content", "prompt", "reason", "paraphrase", "role",
    "contextual_role", "narrative_role", "operation_line", "display_label", "role_label",
    "section", "meta", "hint", "body", "question", "explanation",
})
_DISPLAY_KEY_SUFFIX_RE = re.compile(r"_(?:label|labels|text|title|note|line|fact|facts|message|summary)$")


def is_display_key(key: Any) -> bool:
    """値が表示文であるキーか（番地キーは表示文ではない）。"""
    name = str(key or "")
    if is_address_key(name):
        return False
    return name in DISPLAY_KEYS or bool(_DISPLAY_KEY_SUFFIX_RE.search(name))


#: 生成ラベルが入るキー（学習者向けでは英語の生成文を一般ラベルへ置き換える）。
#: ``title`` / ``text`` / ``passage`` / ``quote`` / ``summary`` は論文本文なので含めない。
LABEL_KEYS: frozenset[str] = frozenset({
    "label",
    "sublabel",
    "narrative_role",
    "operation_line",
    "display_label",
    "role_label",
})

_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
#: 解析層が組み立てる英語ラベル文の目印（件数入りの定型・操作動詞の定型）。
_GENERATED_LABEL_MARKERS = (
    re.compile(r"^logical progression\b", re.IGNORECASE),
    re.compile(r"\b[0-9]+\s+(?:claims?|steps?|equations?|nodes?|spans?|sections?)\b", re.IGNORECASE),
    re.compile(
        r"^(?:define|derive|transform|linearize|solve|eliminate|constrain|diagnose|compare|"
        r"construct|normalize|substitute|approximate|apply|relate|connect|associate|support)"
        r"\b[^:]{0,60}:",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:define|derive|apply|solve|eliminate)\s+(?:result\s+)?eq", re.IGNORECASE),
)
#: 英語文とみなす最小語数。
GENERATED_LABEL_MIN_WORDS = 4


def looks_like_generated_english_label(value: Any) -> bool:
    """英語の生成ラベル文か（学習者に出さない側か）。

    CJK を含む・TeX・論文の式番号・短いトークンは対象外。英語文（4 語以上）かつ
    解析層の定型の目印（件数入り / 操作動詞 + コロン / ``Define eq…``）を持つものだけ。
    論文タイトルのような人間の英語は目印を持たないので置き換えない。
    """
    text = str(value if value is not None else "").strip()
    if not text or _CJK_RE.search(text):
        return False
    if looks_like_tex_math(text) or PAPER_EQUATION_NUMBER_RE.match(text):
        return False
    if len(_WORD_RE.findall(text)) < GENERATED_LABEL_MIN_WORDS and not _GENERATED_LABEL_MARKERS[3].match(text):
        return False
    return any(marker.search(text) for marker in _GENERATED_LABEL_MARKERS)


# ===========================================================================
# 2. 系統別の語彙（旧遮断器の定義をそのまま移設。各系統が参照する）
# ===========================================================================

# --- learner_context_common（学習者向け要素文脈の遮断層・LE4 / EC3） -------------

#: ラベル先頭が裸の内部 ID である形。
LEARNER_INTERNAL_ID_LABEL_RES = (
    re.compile(r"^ev(?:idence)?_[0-9]", re.IGNORECASE),
    re.compile(r"^synth_", re.IGNORECASE),
    re.compile(r"^claim_", re.IGNORECASE),
    re.compile(r"^span_[0-9]", re.IGNORECASE),
    re.compile(r"^support:"),
    re.compile(r"^node_", re.IGNORECASE),
)
#: 文中に埋め込まれた導出・ステップ ID。
LEARNER_EMBEDDED_INTERNAL_ID_RE = re.compile(
    r"derivation_[A-Za-z0-9_]+"
    r"|system_derivation_[0-9]+"
    r"|(?:^|[^A-Za-z0-9])sys_[0-9]+_step_[0-9]+"
    r"|(?:^|[^A-Za-z0-9])step_[0-9]+",
    re.IGNORECASE,
)
#: ``eq_2_7`` 形（論文の式番号）のラベル。
LEARNER_EQUATION_NUMBER_LABEL_RE = re.compile(r"^eq[_\-.]?[0-9]", re.IGNORECASE)
#: 役割文に混ざった内部 ID。
LEARNER_ROLE_INTERNAL_TOKEN_RE = re.compile(
    _UUID_BODY
    + r"|synth_[A-Za-z0-9_]*[0-9]"
    r"|claim_span_[0-9]"
    r"|claim_[0-9]{3,}"
    r"|ev(?:idence)?_[0-9]{3,}"
    r"|span_[0-9]{3,}"
    r"|support:",
    re.IGNORECASE,
)
#: TheoryOperationGraph ノード ID と component の agent 側 ID。
LEARNER_EXTRA_INTERNAL_TOKEN_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:theory_op|eq_op)_[0-9]"
    r"|(?:^|[^A-Za-z0-9])comp_[0-9]",
    re.IGNORECASE,
)

# --- deliberation.labels（W層の生成時点の判定・EC3′） ----------------------------

DELIBERATION_INTERNAL_ID_PREFIX_RES = (
    re.compile(r"^ev(?:idence)?_[0-9]", re.IGNORECASE),
    re.compile(r"^synth_", re.IGNORECASE),
    re.compile(r"^claim_", re.IGNORECASE),
    re.compile(r"^span_[0-9]", re.IGNORECASE),
    re.compile(r"^support:"),
    re.compile(r"^node_", re.IGNORECASE),
    re.compile(r"^comp_", re.IGNORECASE),
    re.compile(r"^theory_op_", re.IGNORECASE),
    re.compile(r"^eq_op_", re.IGNORECASE),
    re.compile(r"^step_[0-9]", re.IGNORECASE),
    re.compile(r"^sys_[0-9]+_step_[0-9]+", re.IGNORECASE),
    re.compile(r"^(?:system_)?derivation_", re.IGNORECASE),
)
DELIBERATION_EMBEDDED_INTERNAL_ID_RES = (
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
#: 文中の式 ID トークン（``eq_tex_b16`` / ``eq_2_7``）。
DELIBERATION_EQUATION_ID_TOKEN_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])(eq[_\-.][A-Za-z0-9_.\-]+)", re.IGNORECASE
)

# --- theory_modules.schema（TM10 / 第 15 周 te-02） -------------------------------

#: 表示ラベル・本文に出してはならない内部 ID の形（**論文の式番号 eq_2_7 も含めて**
#: 捕まえる — TM10 は「式は印字番号『式 (N)』で示す」ので eq_* 形そのものを出さない）。
THEORY_MODULE_INTERNAL_ID_RE = re.compile(
    r"\b(?:eq_op|theory_op|eq|synth_claim|claim_span|claim|ev|comp|derivation|system_derivation)_[0-9A-Za-z_.:\-]*[0-9A-Za-z]",
)
THEORY_MODULE_EQUATION_ID_TOKEN_RE = re.compile(r"\beq_[0-9A-Za-z_.\-]*[0-9A-Za-z]")
#: 途中で切れた式 ID も末尾の区切りごと拾う。
THEORY_MODULE_EQUATION_ID_WITH_TAIL_RE = re.compile(r"\beq_(?!op_)[0-9A-Za-z_.\-]*")
THEORY_MODULE_OTHER_INTERNAL_ID_WITH_TAIL_RE = re.compile(
    r"\b(?:eq_op|theory_op|synth_claim|claim_span|claim|ev|comp|derivation|system_derivation)_[0-9A-Za-z_.:\-]*",
)

# --- course_content_builder（教材投影） --------------------------------------------

#: 学習者に見せない抽出段の式 ID。
COURSE_CONTENT_INTERNAL_EQUATION_ID_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:eq_)?eqcand_|(?:^|[^A-Za-z0-9])eq_op_\d", re.IGNORECASE
)
#: 学習者向けの文（到達目標など）に出してはならない内部 ID（PL7）。
COURSE_CONTENT_LEARNER_INTERNAL_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:eq_[A-Za-z0-9_]+|comp_[A-Za-z0-9_]+|claim_[A-Za-z0-9_]+"
    r"|synth_claim_[0-9]+|ev_[0-9]+"
    r"|" + _UUID_BODY + r")"
    r"(?![A-Za-z0-9])"
)

# --- reference_health（教員向け運用情報の表示ラベル） -----------------------------

REFERENCE_HEALTH_INTERNAL_ID_RE = re.compile(
    r"^(?:eq|ev|eq_op|theory_op|comp|span|step|synth_claim|claim|node|blk|sec)[_\-]?\w*\d[\w\-.]*$",
    re.IGNORECASE,
)

# --- 数値キーの系統別集合（各層の安全網。値は従来どおり） --------------------------

#: TM6（理論モジュール DTO）。
THEORY_MODULE_FORBIDDEN_KEYS: tuple[str, ...] = (
    "confidence",
    "weight",
    "score",
    "candidate_score",
    "fingerprint",
    "structure_fingerprint",
    "interface_width",
    "interface_size",
    "k",
    "limit",
    "count",
    "counts",
    "module_count",
    "member_count",
    "consumer_count",
    "multiplicity",
)
#: PL4（論文層 DTO）。
PAPER_LAYER_FORBIDDEN_KEYS: tuple[str, ...] = (
    "confidence",
    "weight",
    "candidate_score",
    "qualification_reason",
)
#: PL7: display_label に出してはいけない内部 ID のプレフィックス（テスト用）。
PAPER_LAYER_INTERNAL_ID_PREFIXES: tuple[str, ...] = (
    "eq_op_",
    "theory_op_",
    "eq_",
    "ev_",
    "claim_",
)
#: discuss 開幕（W8 / DM6）。
DISCUSS_OPENING_FORBIDDEN_NUMERIC_KEYS: tuple[str, ...] = ("confidence", "load_score", "score")
#: ゼミ前ブリーフ（SB2）と学習者向け台帳。
SEMINAR_BRIEF_FORBIDDEN_NUMERIC_KEYS: frozenset[str] = frozenset({
    "dependent_count", "n_items", "load_score", "confidence", "score",
    "n", "n_users", "count", "weight",
})

#: 教員向け応答に出してはならないキー（TM6 ∪ PL4。段階ラベル ``*_label`` は残す）。
FORBIDDEN_KEYS_TEACHER: frozenset[str] = frozenset(THEORY_MODULE_FORBIDDEN_KEYS) | frozenset(
    PAPER_LAYER_FORBIDDEN_KEYS
)


def strip_keys(value: Any, forbidden: Any) -> Any:
    """``forbidden`` に含まれるキーを再帰的に落とす（新しい容器を返す・入力不変）。"""
    if isinstance(value, dict):
        return {k: strip_keys(v, forbidden) for k, v in value.items() if k not in forbidden}
    if isinstance(value, list):
        return [strip_keys(v, forbidden) for v in value]
    return value


# ===========================================================================
# 3. 射影
# ===========================================================================


#: 選択肢の列（中の ``label`` は互いを区別するための文なので一般ラベルへ畳まない — 畳むと
#: 「関連する要素」が並んで選べなくなる）。
OPTION_LIST_KEYS: frozenset[str] = frozenset({"response_space", "next_actions", "sources", "options", "choices"})


def _project_string(
    key: Any, value: str, *, audience: Audience, element_type: Any, in_options: bool = False
) -> str:
    if is_address_key(key) or is_address_value(value):
        return value
    text = value
    if contains_internal_id(text):
        if is_internal_id_token(text):
            # 値全体が 1 トークンの内部 ID: 表示文のキーなら置き換え、それ以外のキー
            # （``chain`` / ``edges`` / ``footprints`` のような番地の列）は番地として残す。
            if not is_display_key(key):
                return value
            if audience == "learner" and str(key or "") in LABEL_KEYS and element_type:
                return generic_label(element_type)
        text = mask_internal_ids(text)
    # 英語の生成文の置換は、要素の種類（element_type）が分かり、選択肢の列の中でないときだけ
    # （種類が分からない・選択肢のラベルを一般ラベルへ畳むと、別々の項目が同じ語になる）。
    if (
        audience == "learner"
        and element_type in GENERIC_ITEM_LABELS
        and not in_options
        and str(key or "") in LABEL_KEYS
        and looks_like_generated_english_label(text)
    ):
        text = generic_label(element_type)
    return text


def _project(
    value: Any,
    *,
    key: Any,
    audience: Audience,
    forbidden: frozenset[str],
    element_type: Any,
    in_options: bool = False,
) -> Any:
    if hasattr(value, "model_dump") and callable(getattr(value, "model_dump")):
        value = value.model_dump()
    if isinstance(value, dict):
        own_type = value.get("element_type") if isinstance(value.get("element_type"), str) else None
        out: dict = {}
        for k, v in value.items():
            if k in forbidden:
                continue
            out[k] = _project(
                v, key=k, audience=audience, forbidden=forbidden, element_type=own_type, in_options=in_options
            )
        return out
    if isinstance(value, (list, tuple)):
        child_in_options = in_options or str(key or "") in OPTION_LIST_KEYS
        items = [
            _project(
                v,
                key=key,
                audience=audience,
                forbidden=forbidden,
                element_type=element_type,
                in_options=child_in_options,
            )
            for v in value
        ]
        return items if isinstance(value, list) else tuple(items)
    if isinstance(value, str):
        return _project_string(key, value, audience=audience, element_type=element_type, in_options=in_options)
    return value


def project(payload: Any, *, audience: Audience) -> Any:
    """応答を画面向けに射影する（再帰・入力不変）。

    - 禁止キー（:data:`FORBIDDEN_KEYS_LEARNER` / :data:`FORBIDDEN_KEYS_TEACHER`）は値ごと落とす。
    - 番地キー（:data:`ID_KEY_RE`）の文字列値はそのまま（画面遷移に要る）。
    - それ以外の文字列に埋め込まれた内部 ID は :data:`UNIDENTIFIED_ELEMENT_TEXT` に置き換える。
      値全体が 1 トークンの内部 ID のときは、表示文のキー（:func:`is_display_key`）でだけ
      置き換え、それ以外のキーでは番地の値として残す（``chain`` / ``edges`` 等）。
    - 学習者向けでは :data:`LABEL_KEYS` の英語生成文を一般ラベルへ置き換える。
    - pydantic モデルは ``model_dump()`` で dict にしてから射影する。
    """
    if audience not in ("learner", "teacher"):
        raise ValueError(f"unknown audience: {audience!r}")
    forbidden = FORBIDDEN_KEYS_LEARNER if audience == "learner" else FORBIDDEN_KEYS_TEACHER
    return _project(payload, key=None, audience=audience, forbidden=forbidden, element_type=None)


def project_for_learner(payload: Any) -> Any:
    return project(payload, audience="learner")


def project_for_teacher(payload: Any) -> Any:
    return project(payload, audience="teacher")


__all__ = [
    "ADDRESS_VALUE_RE",
    "Audience",
    "COURSE_CONTENT_INTERNAL_EQUATION_ID_RE",
    "COURSE_CONTENT_LEARNER_INTERNAL_ID_RE",
    "DELIBERATION_EMBEDDED_INTERNAL_ID_RES",
    "DELIBERATION_EQUATION_ID_TOKEN_RE",
    "DELIBERATION_INTERNAL_ID_PREFIX_RES",
    "DISCUSS_OPENING_FORBIDDEN_NUMERIC_KEYS",
    "DISPLAY_KEYS",
    "EMBED_MARKER_RE",
    "FORBIDDEN_KEYS_LEARNER",
    "FORBIDDEN_KEYS_TEACHER",
    "GENERIC_ITEM_LABELS",
    "GENERIC_LABEL_FALLBACK",
    "ID_KEY_RE",
    "INTERNAL_ID_TOKEN_RE",
    "LABEL_KEYS",
    "LEARNER_EMBEDDED_INTERNAL_ID_RE",
    "LEARNER_EQUATION_NUMBER_LABEL_RE",
    "LEARNER_EXTRA_INTERNAL_TOKEN_RE",
    "LEARNER_INTERNAL_ID_LABEL_RES",
    "LEARNER_ROLE_INTERNAL_TOKEN_RE",
    "OPTION_LIST_KEYS",
    "PAPER_EQUATION_NUMBER_RE",
    "PAPER_LAYER_FORBIDDEN_KEYS",
    "PAPER_LAYER_INTERNAL_ID_PREFIXES",
    "REFERENCE_HEALTH_INTERNAL_ID_RE",
    "SEMINAR_BRIEF_FORBIDDEN_NUMERIC_KEYS",
    "THEORY_MODULE_EQUATION_ID_TOKEN_RE",
    "THEORY_MODULE_EQUATION_ID_WITH_TAIL_RE",
    "THEORY_MODULE_FORBIDDEN_KEYS",
    "THEORY_MODULE_INTERNAL_ID_RE",
    "THEORY_MODULE_OTHER_INTERNAL_ID_WITH_TAIL_RE",
    "UNIDENTIFIED_ELEMENT_TEXT",
    "UUID_ANY_RE",
    "UUID_FULL_RE",
    "contains_internal_id",
    "generic_label",
    "is_address_key",
    "is_address_value",
    "is_display_key",
    "is_internal_id_token",
    "looks_like_generated_english_label",
    "mask_internal_ids",
    "project",
    "project_for_learner",
    "project_for_teacher",
    "strip_keys",
]
