"""理論モジュール層 — 定数・事実文・表示ラベルの正本（純データ + 純関数）。

設計: ``docs/features/theory_module_layer_design.md``（TM1〜TM10・§5 導出規則・§8.1 DTO）。

このモジュールは FastAPI / sqlalchemy / LLM / embedding を import しない。
表示に関わる規律は3つ:

- TM6 数値非表示: 閾値 k・接点の本数・工程の型の多重度・構造の指紋・モジュールの数を
  DTO に載せない。``FORBIDDEN_KEYS`` をガードレールが DTO 全体に再帰走査する。
- TM10 内部 ID 非表示: 式は印字番号（「式 (12)」）か「番号なし: 要約の先頭」で示し、
  ``eq_tex_*`` / ``eq_op_*`` / ``theory_op_*`` をラベルに使わない。
- TM5 分野中立: ラベルの語は ``core/element_vocab.py`` の既存訳語表と論文データからだけ
  取る。新しい訳語表をここに作らない。

**閾値はコード定数であって環境変数から読まない**（§5.4 / SA7 と同じ扱い）。値を変える
ときは設計書 §3 に実測を足して変え、規則を変えたら ``RULE_VERSION`` を上げる。

モジュールの読み時キー ``module_key``（§5.7 / §13.3）について: 材料は「document_id + 成員 step が
生む式の **equation stable_key**（KO2・内容由来）の昇順列 + level」である。式の stable_key は
呼び出し側（route とパイプラインステージ）が ``persistence.equation_stable_key_map`` で計算して
builder に渡す（builder は DB も persistence も import しない）。写像に無い式は
``EQUATION_ID_KEY_PREFIX + equation_id`` を材料にする。Phase 0（``m1``）は ``equation_id`` の
昇順列を材料にしていた。差し替えに合わせて ``RULE_VERSION`` を ``m2`` に上げた（旧キーとの対応は
取らない）。
"""

from __future__ import annotations

import re
from typing import Any

from core import display_projection as _dp
from core.text_hygiene import strip_control_sequences

# ---------------------------------------------------------------------------
# ``$…$`` → 表示用プレーンテキスト（正本・TM10）
#
# 事実文に載せるモジュールのラベル（related の対象外の名指し・近い構造の対）、保存行の
# ``visual_label``、グラフレビューのノード名がこの 1 つの関数を通る（judge-deep-07 ②:
# 保存時の旧規則で落とした ``textbfx`` が事実文にそのまま出ていた）。
# ---------------------------------------------------------------------------

_PLAIN_WRAPPER_COMMANDS = (
    "mathrm|rm|bm|mathbf|boldsymbol|text|textrm|textbf|textit|textsf|texttt|textnormal|emph|mathsf|mathtt|"
    "mathcal|mathbb|mathfrak|mathit|mathscr|operatorname|"
    "tilde|hat|bar|vec|dot|ddot|overline|underline|widetilde|widehat"
)
_PLAIN_WRAPPERS_RE = re.compile(r"\\(?:" + _PLAIN_WRAPPER_COMMANDS + r")\s*\{([^{}]*)\}")
_PLAIN_WRAPPERS_BARE_RE = re.compile(
    r"\\(?:" + _PLAIN_WRAPPER_COMMANDS + r")(?:\s+([A-Za-z0-9])|(?![A-Za-z])\s*([^\sA-Za-z0-9{}\\_^$]))"
)
#: 書体の切り替え命令（引数を取らず以降の字の書体を変える — ``{\\cal L}`` / ``\\bf x``）。
#: 命令名を字として残さない（IK-0835: ``\\cal L_\\star`` が「cal L_star」になった）。
#: 書体は記号の同一性に関わらない（``\\mathcal{L}`` も「L」— 囲む形の書体命令と同じ扱い）。
FONT_SWITCH_RE = re.compile(
    r"\\(?:rm|bf|it|bm|cal|sf|tt|sl|sc|mit|boldmath|displaystyle|textstyle|scriptstyle|scriptscriptstyle)"
    r"(?![A-Za-z])\s*"
)
_PLAIN_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω", "odot": "⊙", "infty": "∞", "partial": "∂",
    "nabla": "∇", "times": "×", "cdot": "·", "pm": "±", "ell": "ℓ", "hbar": "ℏ",
    # 区切り・省略・関係の記号（``\\langle \\cdots \\rangle`` が「langle cdots rangle」に
    # なっていた — judge-deep-07 ②）。
    "langle": "⟨", "rangle": "⟩", "cdots": "⋯", "ldots": "…", "dots": "…", "parallel": "∥",
    "perp": "⊥", "prime": "′", "to": "→", "leq": "≤", "geq": "≥", "le": "≤", "ge": "≥",
    "sim": "∼", "approx": "≈", "propto": "∝", "neq": "≠",
    # 添字の印（``\\mathcal{L}_\\star`` → 「L_⋆」 — 印は落とさず字にする。IK-0835）。
    "star": "⋆", "ast": "∗",
}


def plain_math(text: str) -> str:
    """``$…$`` の LaTeX を、ノードのラベル（プレーンテキスト）用に読める形へ落とす。

    数式描画の代替ではなく、``\\Delta\\Sigma(R)`` を「ΔΣ(R)」程度に整えるだけ。詳細ペインは
    ``theory_object`` の ``$…$`` を数式として描く。
    """
    out = str(text or "").replace("$", "")
    # 二重化した区切り（JSON 由来の ``\\Theta`` / TeX の改行 ``\\``）を先に畳む
    # （第 14 周: 「\Θ^2_GW\」の残骸）。関係記号は読める記号へ（``z\mid Λ`` →「z | Λ」）。
    out = re.sub(r"\\\\+(?=[A-Za-z])", r"\\", out)
    out = re.sub(r"\\\\+", " ", out)
    out = re.sub(r"\\(?:mid|vert)\b", " | ", out)
    # エスケープした波括弧・記号（``\{\Theta\}`` / ``\_``）はバックスラッシュごと畳む
    # （第 17 周: 波括弧だけ消えて「\Θ^2_GW\」が残った）。
    out = re.sub(r"\\[{}]", "", out)
    out = re.sub(r"\\([_%&#])", r"\1", out)
    # 記号名の置換は装飾コマンドを剥がす前に行う（剥がした後だと ``\\cdot\\vec{k}`` が
    # ``\\cdotk`` になって名前の境界が消える）。
    out = re.sub(r"\\([A-Za-z]+)", lambda m: _PLAIN_GREEK.get(m.group(1), m.group(0)), out)
    for _ in range(3):
        out = _PLAIN_WRAPPERS_RE.sub(r"\1", out)
        out = _PLAIN_WRAPPERS_BARE_RE.sub(lambda m: m.group(1) or m.group(2) or "", out)
    out = FONT_SWITCH_RE.sub("", out)
    out = re.sub(r"\\(?:left|right|,|;|!|quad|qquad)", "", out)
    out = re.sub(r"\\([A-Za-z]+)", r"\1", out)
    out = out.replace("{", "").replace("}", "")
    # 名前の付かない残りのバックスラッシュ（``\ `` 等）は表示に出さない。
    out = out.replace("\\", "")
    return re.sub(r"\s+", " ", out).strip()



# ---------------------------------------------------------------------------
# 規則の版と閾値（env から読まない）
# ---------------------------------------------------------------------------

#: 導出規則の版（§5.7 / §13.3）。§5.2〜5.5 の規則や閾値を変えたら上げる。
#: ``m2``（Phase 1・2026-09-23）: ``module_key`` の材料を式の ``equation_id`` から
#: **equation stable_key**（KO2・内容由来）へ差し替えた。保存行の stable_key も規則の版を
#: 材料に含むので、版を上げると保存済みの行は全て superseded になる（旧キーとの対応は取らない）。
RULE_VERSION = "m2"

#: 外枠モジュールの接点の上限 k（§5.4）。
OUTER_INTERFACE_LIMIT = 3

#: 内側モジュール（外枠の再分割）の接点の上限（§5.4 / §12.3）。内側の接点は「外枠の中で他の
#: 内側モジュールと受け渡す式の本数」（共有の基礎と、外枠そのものの入出力は数えない）。境目の
#: 候補（``INNER_HUB_MIN_CONSUMERS`` の分岐点と循環のまとまりの手前）は、切ったあとの全ての
#: 内側モジュールの接点がこの値以下のときだけ採る。
INNER_INTERFACE_LIMIT = 2

#: 内側の分岐点とみなす「外枠の中でその式を消費する他の初期モジュールの数」の下限（§5.4 / §12.3）。
#: この本数以上に使われる式（共有の基礎を除く）を生む手順は、内側モジュールの先頭になる。
INNER_HUB_MIN_CONSUMERS = 2

#: 共有の基礎とみなす「消費する成員 step の数」の下限（§5.3。入次数は問わない）。
SHARED_FOUNDATION_MIN_CONSUMERS = 4

#: 構造の指紋による同一性候補（規則 ⑤）の対象とする外枠の、成員 step の下限（§13.4）。
MODULE_IDENTITY_MIN_MEMBERS = 3

#: 同上。汎用でない（``generic == false``）工程の型（``edge_type``）の種類の下限（§13.4）。
MODULE_IDENTITY_MIN_PROCESS_KINDS = 2

#: ``module_key`` / 保存行の材料で、equation stable_key が引けない式に付ける接頭辞（§13.3）。
#: 内容由来のキーでないことが材料の字面で分かるようにする。
EQUATION_ID_KEY_PREFIX = "eqid:"

#: 構造の指紋で、A層の語彙が読めず工程の型が分類できない手順の表記（§13.4）。
#: この値を含む指紋のモジュールは同一性候補の対象にしない。
UNCLASSIFIED_EDGE_TYPE = "unclassified"

# ---------------------------------------------------------------------------
# 語彙（DTO の列挙値）
# ---------------------------------------------------------------------------

LEVEL_OUTER = "outer"
LEVEL_INNER = "inner"

ISOLATED_CYCLE = "cycle"
ISOLATED_INTERFACE_TOO_WIDE = "interface_too_wide"

#: 成員にする step を持つチェーンの種別（§5.2-1）。
MEMBER_CHAIN_TYPES: tuple[str, ...] = ("equation_chain", "mixed_chain")
#: 成員にせず sink として持つチェーンの種別（§5.2-3）。
SINK_CHAIN_TYPES: tuple[str, ...] = ("system_level",)
#: 主張の列（依存ではない。§5.2-2 / §7）。
CLAIM_CHAIN_TYPES: tuple[str, ...] = ("claim_chain",)

#: 詳細ノードとして読む graph_layer（main は集約なので step と対応付けない）。
DETAIL_GRAPH_LAYERS: tuple[str, ...] = ("equation_detail", "debug")
GRAPH_LAYER_DEBUG = "debug"

#: 裏付けの強さの順（弱いほど後ろ）。モジュールの裏付けは成員の最も弱いもの（§5.6）。
BACKING_ORDER: tuple[str, ...] = (
    "source_backed",
    "partially_source_backed",
    "review_required",
)
BACKING_INFERRED = "inferred"

#: 本文スニペットの上限（論文層の reference index と同じ 200 字）。
TEXT_SNIPPET_MAX = 200
#: 「番号なし: 要約の先頭」の上限（TM10）。
LABEL_SNIPPET_MAX = 40
#: 理論対象の上限（normalizer の ``_theory_object`` と同じ 120 字）。
THEORY_OBJECT_MAX = 120
#: 理論対象に印字番号を並べるときの上限。
THEORY_OBJECT_LABELS_MAX = 3

#: TM6: DTO に出してはならないキー（ガードレールが再帰走査する）。正本は display_projection（DP2）。
FORBIDDEN_KEYS: tuple[str, ...] = _dp.THEORY_MODULE_FORBIDDEN_KEYS

#: TM10: 表示ラベル・本文に出してはならない内部 ID の形（テスト兼用）。
INTERNAL_ID_RE = _dp.THEORY_MODULE_INTERNAL_ID_RE
#: 式 ID の形（本文中の式 ID を印字番号へ置き換えるための抽出）。
EQUATION_ID_TOKEN_RE = _dp.THEORY_MODULE_EQUATION_ID_TOKEN_RE

# ---------------------------------------------------------------------------
# 事実文（TM8）。語彙表ではなく固定文として個別に置く。
# ---------------------------------------------------------------------------

FACT_NO_DERIVATIONS = "導出の解析結果が無いため、理論モジュールを組めません。"
FACT_NO_EQUATION_STEPS = "この教材では式の導出が再現されていないため、理論モジュールを組めません。"
FACT_CLAIM_CHAINS_ONLY = (
    "導出の解析結果は主張の並びだけで、式の手順を含みません。主張の並びは「論文の順」で見られます。"
)
FACT_NO_EQUATIONS = "式の解析結果が無いため、式は印字番号ではなく「番号なし」で示します。"
FACT_NO_CLAIMS = "主張の解析結果が無いため、理論対象は式の印字番号で示します。"
FACT_NO_COMPONENTS = "コンポーネントの解析結果が無いため、部品との照合は表示できません。"
FACT_NO_GRAPH = "理論操作グラフが無いため、式の詳細ノードとの対応と裏付けは表示できません。"
FACT_INFERRED_STEPS_EXCLUDED = "推定扱いの手順は理論モジュールに含めていません。"
#: 循環（§5.5 の読み替え = 実装記録 §12 の逸脱 1）。末尾に式の表示ラベルを「、」で並べる。
FACT_CYCLE_PREFIX = "導出のつながりに循環があるため、次の式を含む手順はひとまとまりとして扱っています: "
#: 接点が単独で上限を超え、隣と併合できなかった手順（§5.5）。
FACT_INTERFACE_TOO_WIDE = "受け渡す式が多く、隣の手順とまとめられなかった手順を単独のモジュールとして残しています。"
FACT_SINKS = "論文全体をまとめる手順は、モジュールではなく結果の吸い込み口として別に示します。"
FACT_BUILD_FAILED = "理論モジュールの導出に失敗したため表示できません。"

# ---------------------------------------------------------------------------
# 保存用出力（build_theory_module_records）の skip 語彙（§13.4 / TM14）
# ---------------------------------------------------------------------------

#: builder が例外を出した（保存しない）。
RECORDS_SKIP_BUILD_FAILED = "build_failed"
#: 導出の解析結果（derivation_chain artifact）が無い（保存しない = 素材欠落を「全部消えた」と読まない）。
RECORDS_SKIP_NO_DERIVATIONS = "no_derivations"

# ---------------------------------------------------------------------------
# 純関数
# ---------------------------------------------------------------------------


def clean_text(value: Any) -> str:
    """制御文字を除き、前後の空白を落とす。"""
    return strip_control_sequences(str(value or "")).strip()


def truncate_snippet(value: Any, limit: int = TEXT_SNIPPET_MAX) -> str:
    """本文の丸め。``$...$`` の途中で切らない（論文層と同じ規則）。"""
    text = clean_text(value)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if cut.count("$") % 2 == 1:
        marker = cut.rfind("$")
        cut = cut[:marker] if marker >= 0 else cut
    return cut.rstrip()


def contains_internal_id(value: Any) -> bool:
    """文字列に内部 ID が混ざっているか（TM10）。"""
    return bool(INTERNAL_ID_RE.search(str(value or "")))


def equation_summary(record: Any) -> str:
    """式レコードの要約（``semantics.summary``）→ 本文（plain_text / latex）の順。"""
    rec = record if isinstance(record, dict) else {}
    semantics = rec.get("semantics") if isinstance(rec.get("semantics"), dict) else {}
    reconstruction = rec.get("reconstruction") if isinstance(rec.get("reconstruction"), dict) else {}
    extraction = rec.get("source_extraction") if isinstance(rec.get("source_extraction"), dict) else {}
    for candidate in (
        semantics.get("summary"),
        reconstruction.get("plain_text"),
        extraction.get("plain_text"),
        reconstruction.get("latex"),
        extraction.get("latex"),
    ):
        text = clean_text(candidate)
        if text and not contains_internal_id(text):
            return text
    return ""


def equation_display_label(record: Any) -> str:
    """式の表示ラベル（TM10）。``label`` があれば「式 (N)」、無ければ「番号なし: 要約の先頭」。

    ``equation_id`` は**決して**ラベルに使わない。レコードが無い式は「番号なし」。
    """
    rec = record if isinstance(record, dict) else {}
    label = clean_text(rec.get("label")).strip("()").strip()
    if label and not contains_internal_id(label):
        return f"式 ({label})"
    # 番号なしの式の表示は論文層の正本（LaTeX の先頭 → 定義する記号 → 平文）に委ねる（第 21 周 R2・P-0014:
    # ここに英語要約を先に取る複製があり、学習者の一覧が「番号なし: Defines the …」で埋まった）。
    from core.graph_paper_layer.schema import equation_display_label as _paper_label  # 遅延 import（循環回避）

    paper = _paper_label(rec)
    if paper and paper != "番号なし" and not contains_internal_id(paper):
        return paper
    summary = equation_summary(rec)
    # 要約の候補は式本文（LaTeX）に落ちうる。文字数で切った生の TeX は描けないので出さない。
    from core.latex_display import looks_like_bare_tex
    from core.text_excerpt import excerpt

    if summary and not looks_like_bare_tex(summary):
        cut = excerpt(summary, LABEL_SNIPPET_MAX)
        if cut:
            return f"番号なし: {cut}"
    return "番号なし"


def has_printed_label(record: Any) -> bool:
    rec = record if isinstance(record, dict) else {}
    label = clean_text(rec.get("label")).strip("()").strip()
    # TeX の \label キー（eq:mod_dcf）は印字番号ではない（IK-0858 —「式 (eq:mod_dcf)」としない）。
    return bool(label) and not contains_internal_id(label) and not _dp.is_tex_label_key(label)


# ---------------------------------------------------------------------------
# 表示文字列の内部 ID 置換（第 15 周 te-02/te-05・PL7 / TM10）
# ---------------------------------------------------------------------------

#: 途中で切れた式 ID（``eq_eqcand_inline_blk_3df32664_``）も末尾の区切りごと拾う。
_EQUATION_ID_WITH_TAIL_RE = _dp.THEORY_MODULE_EQUATION_ID_WITH_TAIL_RE
#: その他の内部 ID（途中切れの末尾区切り込み）。
_OTHER_INTERNAL_ID_WITH_TAIL_RE = _dp.THEORY_MODULE_OTHER_INTERNAL_ID_WITH_TAIL_RE
#: 式の左辺として添える記号列の上限（長い式は添えない）。
_LHS_MAX_CHARS = 24
UNIDENTIFIED_ELEMENT_TEXT = _dp.UNIDENTIFIED_ELEMENT_TEXT
UNNUMBERED_EQUATION_TEXT = "番号なしの式"


def equation_lhs_symbol(record: Any) -> str:
    """式の左辺の短い記号列（``=`` の手前）。取れない・長いときは空。"""
    rec = record if isinstance(record, dict) else {}
    latex = clean_text(rec.get("latex") or rec.get("normalized_latex") or rec.get("raw_text") or "")
    if "=" not in latex:
        return ""
    lhs = " ".join(latex.split("=", 1)[0].split()).strip().strip("&").strip()
    if not lhs or len(lhs) > _LHS_MAX_CHARS or contains_internal_id(lhs):
        return ""
    return lhs


def equation_display_name(record: Any) -> str:
    """式の表示名: 印字番号があれば「式 (N)」、無ければ「番号なしの式（左辺 X）」。"""
    rec = record if isinstance(record, dict) else {}
    if has_printed_label(rec):
        return f"式 ({clean_text(rec.get('label')).strip('()').strip()})"
    lhs = equation_lhs_symbol(rec)
    return f"{UNNUMBERED_EQUATION_TEXT}（左辺 {lhs}）" if lhs else UNNUMBERED_EQUATION_TEXT


def mask_internal_ids_readable(text: Any, equation_records: dict | None = None) -> str:
    """表示文字列中の内部 ID を読める語に置き換える（ID を出さない）。

    式 ID → :func:`equation_display_name`（記録が無ければ「番号なしの式」）、
    その他の内部 ID → 「（本文を特定できない要素）」。途中切れの ID も末尾ごと置き換える。
    """
    records = equation_records or {}

    def _eq(match: "re.Match[str]") -> str:
        token = match.group(0)
        record = records.get(token) or records.get(token.rstrip("_.-"))
        return equation_display_name(record) if record else UNNUMBERED_EQUATION_TEXT

    masked = _EQUATION_ID_WITH_TAIL_RE.sub(_eq, str(text or ""))
    return _OTHER_INTERNAL_ID_WITH_TAIL_RE.sub(UNIDENTIFIED_ELEMENT_TEXT, masked).strip()


# ---------------------------------------------------------------------------
# モジュールの「受け取る記号 → 定める記号」（第 22 周・IK-0608）
#
# 外枠の境界をまたぐ式（inputs / outputs）だけでは、孤立したモジュールが何を受け取り何を出す
# 計算かが読めない。成員の式の ``defined_symbols`` / ``used_symbols``（equation_semantics の記録）
# から決定論で導く。表示だけ — 指紋・stable_key・identity_eligible・module_key の材料は変えない（TM12）。
# 記号は「名前だけ」（dict の repr を作らない — IK-0627）。
# ---------------------------------------------------------------------------

#: 受け取る記号の表示上限。
MODULE_INPUT_SYMBOLS_MAX = 8
#: 定める記号の表示上限。
MODULE_OUTPUT_SYMBOLS_MAX = 4
#: 上限で切ったことを示す末尾の印（件数は書かない）。
SYMBOL_LIST_TRUNCATED_MARK = "…"
#: 1 記号の表示上限（引数並びまで抱えた長い「記号」は一覧に出さない — 式本文は member_equations で読む）。
SYMBOL_DISPLAY_MAX_CHARS = 40

#: 照合キーで外す書体の命令（囲む形）。アクセント（``\\hat`` 等）は記号の区別に関わるので外さない。
_SYMBOL_NORM_RE = re.compile(
    r"\\(?:mathrm|mathcal|mathbf|mathit|rm|text|textrm|textbf|textit|textsf|texttt|textnormal|mathsf|mathtt|"
    r"mathbb|mathfrak|mathscr|boldsymbol|bm|pmb|operatorname)\s*\{([^{}]*)\}"
)
_SYMBOL_NORM_BARE_RE = re.compile(
    r"\\(?:mathrm|mathcal|mathbf|mathit|textrm|textbf|textit|mathsf|mathtt|mathbb|mathfrak|mathscr|"
    r"boldsymbol|bm|pmb)(?![A-Za-z])\s*(?=[A-Za-z0-9\\])"
)
_COMMAND_RE = re.compile(r"\\([A-Za-z]+)")
_BARE_COMMAND_RE = re.compile(r"\\([A-Za-z]+)\s*(?:_.*)?")
_PROSE_RE = re.compile(r"[A-Za-z]{2,}\s+[A-Za-z]{2,}")
#: dict の repr（``{'symbol': 'z'}``）は記号名ではない（IK-0627）。
_DICT_REPR_RE = re.compile(r"^\{\s*['\"]")
#: 関係式（``q < q_c`` / ``k \\in \\{…\\}``）は記号ではない。
_RELATION_RE = re.compile(r"[<>=]|\\(?:in|leq?|geq?|neq|approx|sim|simeq|equiv|propto|ll|gg)(?![A-Za-z])")


def symbol_match_key(name: Any) -> str:
    """記号名の照合キー。``core.simulation_bridge.codegen.normalize_symbol`` と同じ規則
    （逐語の一致はテストで固定。theory_modules は simulation_bridge を import しない層なので
    ここに置き、正本を 1 つにするときは codegen 側がこちらへ委譲する）。"""
    text = str(name or "").replace("$", "")
    for _ in range(3):
        text = _SYMBOL_NORM_RE.sub(r"\1", text)
    # 書体の切り替え（``{\\cal L}``）は命令名ごと外す（「call」にしない — IK-0835）。
    text = FONT_SWITCH_RE.sub("", text)
    # 引数を波括弧で囲まない書体の命令（``\\mathcal L`` / ``\\boldsymbol\\theta``）は命令だけ外す。
    text = _SYMBOL_NORM_BARE_RE.sub("", text)
    # 添字の印は書き方によらず同じ記号（``L_\\star`` と ``L_*``）。
    text = re.sub(r"\\(?:star|ast)(?![A-Za-z])", "*", text)
    text = re.sub(r"\\[A-Za-z]+", lambda m: m.group(0)[1:], text)
    return re.sub(r"[{}\\\s]", "", text).lower()


def _latex_token_sets() -> tuple[frozenset[str], frozenset[str]]:
    """(記号でない LaTeX 命令名, ギリシャ文字名)。正本は A層 equation_semantics（関数内 import・
    読めない環境では空集合に縮退 = 除外が弱まるだけで落とさない）。"""
    try:
        from episteme_graph.agents.equation_semantics import schema as es_schema
    except Exception:  # noqa: BLE001
        return frozenset(), frozenset()
    return (
        frozenset(getattr(es_schema, "_LATEX_NON_SYMBOL_TOKENS", ()) or ()),
        frozenset(getattr(es_schema, "_LATEX_GREEK_NAMES", ()) or ()),
    )


def clean_symbol_name(raw: Any) -> str:
    """表示してよい記号名（文字列）を返す。表示に向かないものは ""。

    除くもの: 文字列でない値 / 内部 ID を含む（``sym_N`` を含む表示投影の語彙）/ dict の repr / 数字で始まる（``180\\degree/\\pi`` / ``4\\pi``）/
    関係式（``q < q_c``）/ 数値だけ（``0.75`` / ``-1`` / ``1/3``）/
    記号でない LaTeX 命令だけ（``\\ln`` / ``\\sum_i`` / ``\\nabla_\\mu``）/ 英単語の並び（散文）/
    ``SYMBOL_DISPLAY_MAX_CHARS`` を超える長さ。
    """
    if not isinstance(raw, str):
        return ""
    name = strip_control_sequences(raw).strip().strip("$").strip()
    if not name or len(name) > SYMBOL_DISPLAY_MAX_CHARS:
        return ""
    if contains_internal_id(name) or _dp.contains_internal_id(name) or _DICT_REPR_RE.match(name):
        return ""
    if name[0].isdigit() or _RELATION_RE.search(name):
        return ""
    non_symbol, greek = _latex_token_sets()
    bare = _BARE_COMMAND_RE.fullmatch(name)
    if bare and bare.group(1) in non_symbol:
        return ""
    has_greek = any(cmd in greek for cmd in _COMMAND_RE.findall(name))
    letters = re.sub(r"\\[A-Za-z]+", " ", name)
    if not has_greek and not re.search(r"[A-Za-z]", letters):
        return ""
    if _PROSE_RE.search(re.sub(r"[{}]", " ", letters)):
        return ""
    return name


def equation_used_symbols(record: Any) -> list[str]:
    """式が使う記号名（``semantics.used_symbols`` → 無ければ式レコード直下）。名前だけ・重複なし。"""
    rec = record if isinstance(record, dict) else {}
    semantics = rec.get("semantics") if isinstance(rec.get("semantics"), dict) else {}
    raws: list = []
    for source in (semantics.get("used_symbols"), rec.get("used_symbols")):
        if isinstance(source, list):
            raws.extend(source)
    out: list[str] = []
    for raw in raws:
        name = raw.get("symbol") if isinstance(raw, dict) else raw
        name = clean_symbol_name(name)
        if name and name not in out:
            out.append(name)
    return out


def cap_symbol_list(names: list[str], limit: int) -> list[str]:
    """上限で切る。切ったときだけ末尾に ``SYMBOL_LIST_TRUNCATED_MARK`` を足す（件数は書かない）。"""
    if len(names) <= limit:
        return list(names)
    return list(names[:limit]) + [SYMBOL_LIST_TRUNCATED_MARK]
