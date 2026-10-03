"""グラフの論文層 — 語彙・表示ラベル・事実文の正本。

設計: ``docs/features/graph_paper_layer_design.md``（PL1〜PL8）。

このモジュールは **純データ + 純関数だけ**を置く（FastAPI / sqlalchemy / LLM を
import しない）。表示に関わる規律は2つ:

- PL4 数値非表示: ``FORBIDDEN_KEYS`` を DTO に載せない。
- PL7 内部 ID 非表示: ``display_label`` に ``eq_`` / ``eq_op_`` / ``theory_op_`` /
  ``ev_`` / ``claim_`` のような内部 ID を出さない。式は印字番号、図表は
  ``figure_label`` / caption から作る。
"""

from __future__ import annotations

import re
from typing import Any

from core import display_projection as _dp
from core.text_hygiene import strip_control_sequences

# ---------------------------------------------------------------------------
# 語彙
# ---------------------------------------------------------------------------

#: ノードから見た式の役割（DTO ``nodes[].equations[].role``）。
#: 優先順位でもある（同じ式が複数の役割リストに現れたら先勝ち）。
EQUATION_ROLE_INPUT = "input"
EQUATION_ROLE_INTERMEDIATE = "intermediate"
EQUATION_ROLE_OUTPUT = "output"
EQUATION_ROLE_DEFINITION = "definition"
EQUATION_ROLE_CONSTRAINT = "constraint"
EQUATION_ROLE_LINKED = "linked"

EQUATION_ROLES: tuple[str, ...] = (
    EQUATION_ROLE_INPUT,
    EQUATION_ROLE_INTERMEDIATE,
    EQUATION_ROLE_OUTPUT,
    EQUATION_ROLE_DEFINITION,
    EQUATION_ROLE_CONSTRAINT,
    EQUATION_ROLE_LINKED,
)

#: 各役割に対応するノード側のキー（``linked`` は残り全部の受け皿なので持たない）。
EQUATION_ROLE_NODE_KEYS: tuple[tuple[str, str], ...] = (
    (EQUATION_ROLE_INPUT, "input_equation_ids"),
    (EQUATION_ROLE_INTERMEDIATE, "intermediate_equation_ids"),
    (EQUATION_ROLE_OUTPUT, "output_equation_ids"),
    (EQUATION_ROLE_DEFINITION, "definition_equation_ids"),
    (EQUATION_ROLE_CONSTRAINT, "constraint_equation_ids"),
    (EQUATION_ROLE_LINKED, "linked_equation_ids"),
)

#: 記号の役割（DTO ``nodes[].symbols[].role``）。
SYMBOL_ROLE_ELIMINATED = "eliminated"
SYMBOL_ROLE_RETAINED = "retained"
SYMBOL_ROLES: tuple[str, ...] = (SYMBOL_ROLE_ELIMINATED, SYMBOL_ROLE_RETAINED)

SYMBOL_ROLE_NODE_KEYS: tuple[tuple[str, str], ...] = (
    (SYMBOL_ROLE_ELIMINATED, "eliminated_symbols"),
    (SYMBOL_ROLE_RETAINED, "retained_symbols"),
)

#: ノード側の claim ID キー（``_NODE_CLAIM_ID_KEYS`` と同じ4キー。旧版グラフでは
#: ``linked_claim_ids`` が和集合になっていないことがあるため全部読む）。
NODE_CLAIM_ID_KEYS: tuple[str, ...] = (
    "linked_claim_ids",
    "input_claim_ids",
    "output_claim_ids",
    "required_claim_ids",
)

#: main ノードが detail ノードを指すキー。
NODE_MEMBER_ID_KEYS: tuple[str, ...] = ("member_component_ids", "detail_node_ids")

#: ノードから component_assembly の agent ID を引くためのキー（単値 / 配列）。
NODE_COMPONENT_REF_KEYS: tuple[str, ...] = (
    "agent_component_id",
    "representative_component_id",
    "parent_component_id",
)
NODE_COMPONENT_REF_LIST_KEYS: tuple[str, ...] = ("linked_component_ids",)

#: 本文スニペットの上限（reference index と同じ 200 字）。
TEXT_SNIPPET_MAX = 200

#: display_label 用の短い本文上限（PL7 の「番号なし: 本文先頭」）。
LABEL_SNIPPET_MAX = 40

#: PL4: DTO に載せてはいけないキー（ガードレールが再帰走査する）。正本は display_projection（DP2）。
FORBIDDEN_KEYS: tuple[str, ...] = _dp.PAPER_LAYER_FORBIDDEN_KEYS

#: PL7: display_label に出してはいけない内部 ID のプレフィックス（テスト用）。
INTERNAL_ID_PREFIXES: tuple[str, ...] = _dp.PAPER_LAYER_INTERNAL_ID_PREFIXES

#: coverage の ``unbound_claims`` に載せる claim の条件（§3.2）。
UNBOUND_CLAIM_SUPPORT_STATUS = "source_backed"

#: contextual 説明の採用順（approved が無ければ candidate。dismissed / superseded は
#: 呼び出し側が問い合わせの時点で除外する）。
EXPLANATION_STATUS_PRIORITY: tuple[str, ...] = ("approved", "candidate")

#: 論文層が読む contextual 説明の ``element_type``。語彙の正本は
#: ``core/element_explanations.py``（本表はそのうち論文層が扱う3種の部分集合）。
#: component だけを読んでいたため、component の説明が無い論文ではノードの
#: 「論文での対応」が空になっていた — 実データでは説明行の多数が claim / 式に付く。
EXPLANATION_ELEMENT_COMPONENT = "theory_component"
EXPLANATION_ELEMENT_CLAIM = "theory_claim"
EXPLANATION_ELEMENT_EQUATION = "equation"
EXPLANATION_ELEMENT_TYPES: tuple[str, ...] = (
    EXPLANATION_ELEMENT_COMPONENT,
    EXPLANATION_ELEMENT_CLAIM,
    EXPLANATION_ELEMENT_EQUATION,
)

#: DSL エッジの極性（P0-9）。語彙の正本は
#: ``src/episteme_graph/agents/dsl_linking/schema.py::POLARITIES``（``+`` / ``-`` /
#: ``+/-`` / ``?``）で、ここに置くのはその**表示語**だけである。記号のまま画面に出すと
#: 「A —[derives +]→ B」のように読めないため、DTO 側で語に開いてから渡す
#: （PL7 の「内部表現を表示ラベルにしない」と同じ趣旨）。
#:
#: ``core/label_vocab.py`` に置かないのは、あちらが「段階ラベル（数値→語）と状態投影」
#: の正本であって DSL の関係語彙を持たないため。将来 DSL 層の語彙が学習者側にも出る
#: ようになったら label_vocab へ移す（そのときは本表を削除して委譲する）。
DSL_POLARITY_LABELS: dict[str, str] = {
    "+": "順方向",
    "-": "逆方向",
    "+/-": "両方向",
    "?": "向きは未確定",
}

# ---------------------------------------------------------------------------
# 事実文（PL8）
#
# 「その部品だけ空にして facts に1行足す」ための固定文。個別の文字列定数として
# 置く（日本語ラベル表の重複走査 = test_label_vocab_guardrails の対象にしない。
# これは語彙表ではなく事実文である）。
# ---------------------------------------------------------------------------

FACT_NO_GRAPH = "理論操作グラフが構築されていないため、論文層を表示できません。"
#: `theory_component_graphs` 行が無く、component 一覧からの読み時組み立てに
#: フォールバックしたとき（設計 §5.2）。ノードは並ぶが式・根拠・章のリンクを持たない
#: ため、論文との対応は導出できない — それを「空」ではなく事実として言う。
FACT_NO_STORED_GRAPH = (
    "理論操作グラフが未構築のため、論文との対応は導出できません（再解析で構築されます）。"
)
FACT_NO_DOCUMENT_STRUCTURE = "章構成の解析結果が無いため、論文の順では表示できません。"
FACT_NO_EQUATIONS = "式の解析結果が無いため、式の対応は表示できません。"
FACT_NO_EVIDENCE = "根拠の解析結果が無いため、逐語引用は表示できません。"
FACT_NO_CLAIMS = "主張の解析結果が無いため、主張と章の対応は表示できません。"
FACT_NO_SYMBOLS = "記号の解析結果が無いため、記号の定義は表示できません。"
FACT_NO_DERIVATIONS = "導出の解析結果が無いため、導出ステップは表示できません。"
FACT_NO_FIGURES = "図表の解析結果が無いため、図表の対応は表示できません。"
FACT_NO_SKELETON = "論文骨格の解析結果が無いため、論文の目的と論理ブロックは表示できません。"
FACT_NO_THESIS = "中心命題の解析結果が無いため、命題上の役割は表示できません。"
FACT_NO_COMPONENTS = "コンポーネントの解析結果が無いため、要約は表示できません。"
#: P0-9: 文章層（中心命題の支持構造）と DSL 層を論文層に出すための事実文。
#: ``FACT_NO_THESIS`` は支持構造の欠落も兼ねる（同じ artifact なので行を分けない）。
FACT_NO_DSL = "概念関係（DSL）の解析結果が無いため、概念どうしの関係は表示できません。"

#: artifact のステージ名 → 欠落時の事実文 + 主たるコレクションのキー。
#: （値は事実文の**参照**なので、ラベル表の重複走査には当たらない。）
MISSING_ARTIFACT_FACTS: tuple[tuple[str, str, str], ...] = (
    ("document_structure", "sections", FACT_NO_DOCUMENT_STRUCTURE),
    ("equation_semantics", "equations", FACT_NO_EQUATIONS),
    ("evidence_registry", "records", FACT_NO_EVIDENCE),
    ("claim_object_builder", "claims", FACT_NO_CLAIMS),
    ("symbol_registry", "records", FACT_NO_SYMBOLS),
    ("derivation_chain", "chains", FACT_NO_DERIVATIONS),
    ("figure_table_semantics", "figures", FACT_NO_FIGURES),
    ("paper_skeleton", "logical_blocks", FACT_NO_SKELETON),
    ("thesis_reconstruction", "central_thesis", FACT_NO_THESIS),
    ("component_assembly", "components", FACT_NO_COMPONENTS),
    ("dsl_linking", "nodes", FACT_NO_DSL),
)

# ---------------------------------------------------------------------------
# 純関数
# ---------------------------------------------------------------------------

# ``core/document_pipeline/figure_images.py::normalize_figure_join_key`` と**同一規則**。
# 本層は sqlalchemy / MinIO を import しない（PL: core は DB 非依存）ため、あちらを
# import せずに3行だけ写している。生成側の正本規則は figure_images 側のまま。
_FIGURE_JOIN_NON_ALNUM = re.compile(r"[^0-9A-Za-z]+")


def normalize_figure_join_key(value: Any) -> str:
    """図ID表記ゆれの突合用正規化（``fig_3.3`` と ``fig_3_3`` を同じキーにする）。"""
    text = str(value or "").strip()
    if not text:
        return ""
    return _FIGURE_JOIN_NON_ALNUM.sub("_", text).strip("_").lower()


def truncate_snippet(text: Any, limit: int = TEXT_SNIPPET_MAX) -> str:
    """本文スニペットの丸め。``$...$`` の途中で切らない。

    ``routes/theory_components.py::_truncate_reference_text`` と同じ規則
    （閉じない ``$`` を残さない）。artifact 由来のスニペットには ANSI 残骸
    （``[0m`` 等）が混ざることがあるため、丸める前に除去する
    （2026-09-10・graph_dialogue_review_design.md §15）。
    """
    value = strip_control_sequences(str(text or ""))
    if len(value) <= limit:
        return value
    cut = value[:limit]
    if cut.count("$") % 2 == 1:
        marker = cut.rfind("$")
        cut = cut[:marker] if marker >= 0 else cut
    return cut


def _first_text(*candidates: Any) -> str:
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def equation_body(record: Any) -> dict:
    """式レコード（ネスト形）から本文・所在を取り出す。

    本文 = ``reconstruction.latex ?? source_extraction.latex``（設計 §3.1）。
    """
    rec = record if isinstance(record, dict) else {}
    reconstruction = rec.get("reconstruction") if isinstance(rec.get("reconstruction"), dict) else {}
    extraction = rec.get("source_extraction") if isinstance(rec.get("source_extraction"), dict) else {}
    location = extraction.get("source_location") if isinstance(extraction.get("source_location"), dict) else {}
    return {
        "latex": _first_text(reconstruction.get("latex"), extraction.get("latex")),
        "plain_text": _first_text(
            reconstruction.get("plain_text"),
            extraction.get("plain_text"),
            extraction.get("raw_text"),
        ),
        "page": location.get("page"),
        "section_id": str(location.get("section_id") or ""),
        "block_id": str(location.get("block_id") or ""),
        "needs_math_review": bool(extraction.get("needs_math_review", False)),
    }


#: 式の本文スニペット（``member_equations[].latex_snippet``）の上限。表示ラベルより長く取る。
LATEX_SNIPPET_MAX = 120

#: 定義する記号を表示ラベルに並べる上限。
LABEL_SYMBOLS_MAX = 3

# 表示用 LaTeX から落とす語（数式環境の枠・番号付けの制御。KaTeX のインラインで描けない／意味を持たない）。
_LATEX_ENV_RE = re.compile(
    r"\\(?:begin|end)\{(?:equation|align|eqnarray|gather|multline|split|aligned|gathered|displaymath|math)\*?\}"
)
_LATEX_LABEL_RE = re.compile(r"\\(?:label|tag)\*?\{[^{}]*\}")
_LATEX_NOISE_RE = re.compile(r"\\(?:nonumber|notag)\b")
_LATEX_OUTER_DELIMS = (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$"))
_LATEX_LEFT_RIGHT_RE = re.compile(r"\\(?:left|right)(?![A-Za-z])")


def _clean_latex(latex: str) -> str:
    """表示用に LaTeX を整える（環境の枠・``\\label``・``&``・外側の区切りを落とし空白を畳む）。"""
    text = strip_control_sequences(str(latex or "")).strip()
    for opener, closer in _LATEX_OUTER_DELIMS:
        if len(text) >= len(opener) + len(closer) and text.startswith(opener) and text.endswith(closer):
            text = text[len(opener):len(text) - len(closer)].strip()
            break
    text = re.sub(r"(?<!\\)%[^\n]*", " ", text)  # LaTeX のコメント
    text = _LATEX_ENV_RE.sub(" ", text)
    text = _LATEX_LABEL_RE.sub(" ", text)
    text = _LATEX_NOISE_RE.sub(" ", text)
    text = text.replace("&", " ").replace("\\\\", " ").replace("$", " ")
    return re.sub(r"\s+", " ", text).strip()


#: 引数（波括弧の群）を取る LaTeX コマンドと、その群の数。切れ目の直前に引数が欠けたまま
#: 残ると KaTeX が描けないため、そのコマンドごと落とす。
_LATEX_ARG_COMMANDS: dict[str, int] = {
    "frac": 2, "dfrac": 2, "tfrac": 2, "binom": 2, "dbinom": 2, "tbinom": 2,
    "sqrt": 1, "hat": 1, "widehat": 1, "bar": 1, "overline": 1, "underline": 1, "tilde": 1,
    "widetilde": 1, "vec": 1, "dot": 1, "ddot": 1, "mathrm": 1, "mathbf": 1, "mathit": 1,
    "mathcal": 1, "mathbb": 1, "mathsf": 1, "mathfrak": 1, "boldsymbol": 1, "bm": 1, "text": 1,
    "textrm": 1, "textbf": 1, "operatorname": 1, "overbrace": 1, "underbrace": 1,
}


def _group_start(text: str, close_index: int) -> int:
    """``text[close_index] == "}"`` に対応する ``{`` の位置（見つからなければ -1）。"""
    depth = 0
    for index in range(close_index, -1, -1):
        char = text[index]
        escaped = index > 0 and text[index - 1] == "\\"
        if char == "}" and not escaped:
            depth += 1
        elif char == "{" and not escaped:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _trim_incomplete_tail(text: str) -> str:
    """末尾に残った「引数の足りないコマンド」「上付き・下付きの記号」を落とす（決定論）。"""
    while True:
        text = text.rstrip()
        if text.endswith(("^", "_", "\\", "{")) and not text.endswith("\\{"):
            text = text[:-1]
            continue
        # 末尾の完結した群を遡り、その手前のコマンドが群を足りなく持つなら群ごと落とす。
        position = len(text)
        groups = 0
        while position > 0 and text[position - 1] == "}":
            begin = _group_start(text, position - 1)
            if begin < 0:
                break
            position = begin
            groups += 1
            while position > 0 and text[position - 1] == " ":
                position -= 1
        matched = re.search(r"\\([A-Za-z]+)\*?$", text[:position])
        if matched and groups < _LATEX_ARG_COMMANDS.get(matched.group(1), 0):
            text = text[:matched.start()]
            continue
        return text


def _cut_latex(text: str, limit: int) -> tuple[str, bool]:
    """``limit`` 字で切る。``\\コマンド`` の途中・開いた波括弧の中・引数の足りないコマンドの
    後ろでは切らず、``\\left`` / ``\\right`` の片割れを残さない（KaTeX で描ける形に保つ）。"""
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    # 末尾が ``\\abc`` の途中（次の字も英字）なら、そのコマンドごと落とす。
    marker = cut.rfind("\\")
    if (
        marker >= 0
        and re.fullmatch(r"\\[A-Za-z]*", cut[marker:])
        and (len(cut) - marker == 1 or text[limit].isalpha())
    ):
        cut = cut[:marker]
    cut = _LATEX_LEFT_RIGHT_RE.sub("", cut)
    # 開いたままの群の中の末尾を整えてから、開いた波括弧を閉じる。閉じた結果、引数の足りない
    # コマンド（``\\frac{a}`` 等）が残れば、そのコマンドごと落とす。
    cut = _trim_incomplete_tail(cut)
    depth = 0
    for index, char in enumerate(cut):
        escaped = index > 0 and cut[index - 1] == "\\"
        if char == "{" and not escaped:
            depth += 1
        elif char == "}" and not escaped and depth > 0:
            depth -= 1
    return _trim_incomplete_tail(cut + "}" * depth), True


def equation_latex_snippet(record: Any, limit: int = LATEX_SNIPPET_MAX) -> str:
    """式本文（LaTeX）の表示用スニペット ``$…$``（切ったときは後ろに ``…``）。本文が無ければ ""。"""
    body = equation_body(record)
    text = _clean_latex(body["latex"])
    if not text:
        return ""
    cut, truncated = _cut_latex(text, limit)
    if not cut:
        return ""
    return f"${cut}$" + ("…" if truncated else "")


def equation_defined_symbols(record: Any) -> list[str]:
    """式が定義する記号名（``semantics.defined_symbols`` → 無ければ式レコード直下の同名キー）。

    dict 形は ``definition_status`` が ``defined`` / ``redefined``（または ``defined_here``）の
    ものだけを数える（``equation_semantics`` の export と同じ規則）。推測しない。
    """
    rec = record if isinstance(record, dict) else {}
    semantics = rec.get("semantics") if isinstance(rec.get("semantics"), dict) else {}
    raws: list = []
    for source in (semantics.get("defined_symbols"), rec.get("defined_symbols")):
        if isinstance(source, list):
            raws.extend(source)
    out: list[str] = []
    for raw in raws:
        name = ""
        if isinstance(raw, str):
            name = raw.strip()
        elif isinstance(raw, dict):
            if raw.get("defined_here") or str(raw.get("definition_status") or "") in ("defined", "redefined"):
                name = str(raw.get("symbol") or "").strip()
        name = strip_control_sequences(name).strip().strip("$").strip()
        if name and name not in out:
            out.append(name)
    return out


def equation_display_label(record: Any) -> str:
    """式の表示ラベル（PL7）。

    印字番号があれば ``式 (12)``。無ければ ``番号なし: `` に続けて、
    ①式本文の先頭（``$…$``・``LABEL_SNIPPET_MAX`` 字）②定義する記号の列挙
    ③どちらも無いときだけ平文（A層の英語要約になりうる）の先頭、の順。
    第 21 周: TeX 経路の式で ③ が先に出て、一覧が英語の断片で埋まっていた。

    ``equation_id`` は**決して**ラベルに使わない。
    """
    rec = record if isinstance(record, dict) else {}
    label = str(rec.get("label") or "").strip()
    if label:
        return f"式 ({label})"
    snippet = equation_latex_snippet(rec, LABEL_SNIPPET_MAX)
    if snippet:
        return f"番号なし: {snippet}"
    symbols = equation_defined_symbols(rec)
    if symbols:
        listed = "、".join(f"${sym}$" for sym in symbols[:LABEL_SYMBOLS_MAX])
        more = " ほか" if len(symbols) > LABEL_SYMBOLS_MAX else ""
        return f"番号なし: {listed}{more} を定義する式"
    plain = _first_text(equation_body(rec)["plain_text"])
    if plain:
        return f"番号なし: {plain[:LABEL_SNIPPET_MAX]}"
    return "番号なし"


_FIGURE_KEY_NUMBER_RE = re.compile(r"^(?:fig|figure)[_\-. ]+(?P<number>[0-9A-Za-z][0-9A-Za-z_\-.]*)$", re.IGNORECASE)
_TABLE_KEY_NUMBER_RE = re.compile(r"^(?:tab|table)[_\-. ]+(?P<number>[0-9A-Za-z][0-9A-Za-z_\-.]*)$", re.IGNORECASE)


def figure_display_label(
    element_id: Any,
    *,
    figure_label: Any = None,
    caption: Any = "",
    kind: str = "figure",
) -> str:
    """図・表の表示ラベル（PL7）。

    ``figure_label``（``document_figures`` の印字ラベル）→ ID から導ける印字番号
    （``fig_3.3`` → ``Figure 3.3``）→ caption 先頭、の順。どれも取れない場合は
    ``図`` / ``表`` に縮退し、内部 ID（``p2_i0`` 等）は出さない。
    """
    explicit = str(figure_label or "").strip()
    if explicit:
        return explicit
    pattern = _TABLE_KEY_NUMBER_RE if kind == "table" else _FIGURE_KEY_NUMBER_RE
    prefix = "Table" if kind == "table" else "Figure"
    matched = pattern.match(str(element_id or "").strip())
    if matched:
        number = matched.group("number").replace("_", ".")
        return f"{prefix} {number}"
    caption_text = str(caption or "").strip()
    if caption_text:
        return caption_text[:LABEL_SNIPPET_MAX]
    return "表" if kind == "table" else "図"


def thesis_ref_for(section: str | None, index: int | None) -> str:
    """thesis_ref の規約（``persistence._thesis_ref_nodes`` と同一）。"""
    if section is None or index is None:
        return "central_thesis"
    return f"support:{section}:{index}"
