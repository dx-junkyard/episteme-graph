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

#: PL4: DTO に載せてはいけないキー（ガードレールが再帰走査する）。
FORBIDDEN_KEYS: tuple[str, ...] = (
    "confidence",
    "weight",
    "candidate_score",
    "qualification_reason",
)

#: PL7: display_label に出してはいけない内部 ID のプレフィックス（テスト用）。
INTERNAL_ID_PREFIXES: tuple[str, ...] = (
    "eq_op_",
    "theory_op_",
    "eq_",
    "ev_",
    "claim_",
)

#: coverage の ``unbound_claims`` に載せる claim の条件（§3.2）。
UNBOUND_CLAIM_SUPPORT_STATUS = "source_backed"

#: contextual 説明の採用順（approved が無ければ candidate。dismissed / superseded は
#: 呼び出し側が問い合わせの時点で除外する）。
EXPLANATION_STATUS_PRIORITY: tuple[str, ...] = ("approved", "candidate")

# ---------------------------------------------------------------------------
# 事実文（PL8）
#
# 「その部品だけ空にして facts に1行足す」ための固定文。個別の文字列定数として
# 置く（日本語ラベル表の重複走査 = test_label_vocab_guardrails の対象にしない。
# これは語彙表ではなく事実文である）。
# ---------------------------------------------------------------------------

FACT_NO_GRAPH = "理論操作グラフが構築されていないため、論文層を表示できません。"
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
    （閉じない ``$`` を残さない）。
    """
    value = str(text or "")
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


def equation_display_label(record: Any) -> str:
    """式の表示ラベル（PL7）。印字番号があれば ``式 (12)``、無ければ本文先頭。

    ``equation_id`` は**決して**ラベルに使わない。
    """
    rec = record if isinstance(record, dict) else {}
    label = str(rec.get("label") or "").strip()
    if label:
        return f"式 ({label})"
    body = equation_body(rec)
    snippet = _first_text(body["plain_text"], body["latex"])
    if snippet:
        return f"番号なし: {snippet[:LABEL_SNIPPET_MAX]}"
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
