"""種別別ラベルラダー ``core/deliberation/labels.py`` のテスト（§2.3 / §5）。

`docs/features/element_context_presentation_redesign.md` Phase 0。ここで固定するのは
「どの段が採られるか」と、**どの段でも生 TeX・内部 ID を text に出さない**という
CP1 / EC3′ の下限。入力は artifact / DB 行の fake dict のみで、DB には触らない。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from core.deliberation import labels as L


def _code_string_literals(source: str) -> list[str]:
    """docstring を除いた「実行される文字列リテラル」を集める（コメントは AST に無い）。"""
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


# ---------------------------------------------------------------------------
# fixtures（artifact 形の fake）
# ---------------------------------------------------------------------------

SUMMARY_EN = (
    "Definition of the matter density contrast delta as the fractional overdensity "
    "of the matter field from its spatial mean. It is used throughout section 2."
)
LATEX = r"\delta(t,x) = \frac{\rho(t,x) - \bar{\rho}(t)}{\bar{\rho}(t)}"


def equation_record(**over):
    record = {
        "equation_id": "eq_tex_b14",
        "label": None,
        "source_extraction": {
            "raw_text": "delta(t,x) = (rho - rhobar)/rhobar",
            "latex": LATEX,
            "plain_text": None,
            "needs_math_review": False,
            "source_location": {"section_id": "sec_2_1", "block_id": "block_12"},
        },
        "reconstruction": {"latex": None, "plain_text": None, "status": "none"},
        "semantics": {
            "equation_type": "definition",
            "role_in_argument": "definition",
            "summary": SUMMARY_EN,
            "defined_symbols": [
                {"symbol": "δ(t,x)", "definition_status": "defined", "meaning": "density contrast"}
            ],
            "used_symbols": ["ρ", "ρ̄"],
            "link_status": "axiomatic",
        },
    }
    record.update(over)
    return record


def chain_record(**over):
    record = {
        "derivation_id": "derivation_eq_tex_b16",
        "chain_type": "equation_chain",
        "operation": "",
        "operation_family": "",
        "teaching_takeaway": "",
        "conditions": [],
        "steps": [
            {"step_id": "step_001", "operation": "transform"},
            {"step_id": "step_002", "operation": "linearize"},
        ],
    }
    record.update(over)
    return record


# ---------------------------------------------------------------------------
# 内部 ID / 式番号の判定
# ---------------------------------------------------------------------------


class TestIdHeuristics:
    def test_paper_equation_numbers(self):
        for value in ("eq_2_7", "eq.3.14", "(3.14)", "3.14", "eq-12", "式 2"):
            assert L.looks_like_paper_equation_number(value), value

    def test_synthetic_equation_ids_are_not_paper_numbers(self):
        for value in ("eq_tex_b14", "eq_tex_b16", "eq_block_3", "eq_candidate_a1"):
            assert not L.looks_like_paper_equation_number(value), value

    def test_format_equation_number(self):
        assert L.format_equation_number("eq_2_7") == "式 (2.7)"
        assert L.format_equation_number("(3.14)") == "式 (3.14)"
        assert L.format_equation_number("eq_tex_b14") == ""

    def test_internal_id_like(self):
        for value in (
            "8f14e45f-ceea-467a-9d8a-1b2c3d4e5f60",
            "ev_0001",
            "evidence_0004",
            "synth_claim_0001",
            "claim_span_001",
            "span_001",
            "support:methods:2",
            "node_7",
            "comp_0003",
            "theory_op_0001",
            "eq_op_0012",
            "step_001",
            "sys_3_step_2",
            "derivation_eq_tex_b16",
            "導出「derivation_eq_tex_b16」のステップ「step_001」",
            "Define eq_tex_b16",
            "Derive result eq_tex_b20",
        ):
            assert L.is_internal_id_like(value), value

    def test_readable_labels_are_not_internal_ids(self):
        for value in (
            "理論の土台",
            "eq_2_7",
            "式 (2.7)",
            "δ(t,x) を定義する式",
            "フーリエ変換による書き換え",
            "Definition of the matter density contrast",
        ):
            assert not L.is_internal_id_like(value), value

    def test_contains_internal_equation_id(self):
        assert L.contains_internal_equation_id("Define eq_tex_b16")
        assert not L.contains_internal_equation_id("see eq_2_7 above")


# ---------------------------------------------------------------------------
# 数式（§5.1）
# ---------------------------------------------------------------------------


class TestEquationLadder:
    def test_rung1_explanation_wins(self):
        label = L.equation_label(
            equation_record(),
            explanation="密度コントラストを背景密度からのずれとして定義する式です。続きの説明。",
        )
        assert label.text == "密度コントラストを背景密度からのずれとして定義する式です。"
        assert label.label_source == L.LABEL_SOURCE_EXPLANATION

    def test_rung2_paper_equation_number(self):
        label = L.equation_label(equation_record(label="eq_2_7"))
        assert label.text == "式 (2.7)"
        assert label.label_source == L.LABEL_SOURCE_PAPER_NUMBER

    def test_synthetic_id_is_not_used_as_a_paper_number(self):
        label = L.equation_label(equation_record())
        assert "eq_tex_b14" not in label.text

    def test_rung3_symbol_plus_role_composition(self):
        label = L.equation_label(equation_record())
        assert label.text == "δ(t,x) を定義する式"
        assert label.label_source == L.LABEL_SOURCE_CONTROLLED_VOCAB
        assert label.qualifier == "definition"

    def test_rung3_uses_the_symbol_registry_when_the_record_has_none(self):
        record = equation_record()
        record["semantics"] = dict(record["semantics"], defined_symbols=[])
        label = L.equation_label(
            record,
            symbols=[{"canonical_symbol": "δ", "defining_equation_ids": ["eq_tex_b14"]}],
        )
        assert label.text == "δ を定義する式"

    def test_rung4_semantic_summary(self):
        record = equation_record()
        record["semantics"] = dict(
            record["semantics"], defined_symbols=[], role_in_argument="", equation_type="relation"
        )
        label = L.equation_label(record)
        assert label.label_source == L.LABEL_SOURCE_SEMANTIC_SUMMARY
        assert label.text.startswith("Definition of the matter density contrast")
        assert label.text.endswith("…")
        # 単語途中で切らない（RC11）。
        assert not label.text.rstrip("…").endswith("mea")

    def test_rung5_role_translation(self):
        record = equation_record()
        record["semantics"] = dict(
            record["semantics"], defined_symbols=[], summary="", role_in_argument="constraint"
        )
        label = L.equation_label(record)
        assert label.text == "制約式"
        assert label.label_source == L.LABEL_SOURCE_CONTROLLED_VOCAB

    def test_rung6_generic_with_unresolved_and_a_fact_sublabel(self):
        record = equation_record()
        record["semantics"] = dict(
            record["semantics"],
            defined_symbols=[],
            summary="",
            role_in_argument="",
            equation_type="",
            link_status="axiomatic",
        )
        label = L.equation_label(record)
        assert label.text == "数式"
        assert label.unresolved is True
        assert label.label_source == L.LABEL_SOURCE_GENERIC
        # CP10「空は沈黙ではない」: 前段が無い理由を事実文で述べる。
        assert label.sublabel == "この式は定義であり、前段の式を持ちません。"

    def test_latex_never_appears_in_any_rung(self):
        variants = [
            equation_record(),
            equation_record(label="eq_2_7"),
        ]
        stripped = equation_record()
        stripped["semantics"] = dict(
            stripped["semantics"], defined_symbols=[], summary="", role_in_argument=""
        )
        variants.append(stripped)
        for record in variants:
            label = L.equation_label(record)
            for field in (label.text, label.sublabel):
                assert "\\frac" not in field
                assert LATEX not in field
                assert not L.looks_like_tex_math(field)

    def test_needs_math_review_record_does_not_leak_math_text(self):
        record = equation_record()
        record["source_extraction"] = dict(record["source_extraction"], needs_math_review=True)
        # summary が壊れた TeX でも表示候補にしない。
        record["semantics"] = dict(
            record["semantics"], defined_symbols=[], summary=LATEX, role_in_argument=""
        )
        label = L.equation_label(record)
        assert label.text == "数式"
        assert label.unresolved is True
        assert "\\" not in label.sublabel

    def test_math_text_is_rejected_even_if_it_lands_in_summary(self):
        record = equation_record()
        record["semantics"] = dict(
            record["semantics"], defined_symbols=[], summary=LATEX, role_in_argument="derived"
        )
        label = L.equation_label(record)
        assert label.text == "導出結果式"

    def test_text_and_sublabel_are_never_identical(self):
        record = equation_record()
        record["semantics"] = dict(
            record["semantics"], defined_symbols=[], summary="ある短い意味の一行。", role_in_argument=""
        )
        label = L.equation_label(record)
        assert label.text
        assert label.text != label.sublabel

    def test_none_record_degrades(self):
        label = L.equation_label(None)
        assert label.text == "数式"
        assert label.unresolved is True


# ---------------------------------------------------------------------------
# 導出（§5.2）
# ---------------------------------------------------------------------------


class TestDerivationLadder:
    def test_rung1_operation_plus_chain_type(self):
        label = L.derivation_label(chain_record(operation="eliminate", chain_type="system_level"))
        assert label.text == "消去（系レベルの操作）"
        assert label.qualifier == "system_level"
        assert label.label_source == L.LABEL_SOURCE_CONTROLLED_VOCAB

    def test_operation_family_is_used_when_operation_is_empty(self):
        label = L.derivation_label(chain_record(operation_family="linearize"))
        assert label.text == "線形化（式の導出）"

    def test_chain_type_distinguishes_two_otherwise_identical_chains(self):
        """スクショの「導出の流れ」×2（区別不能）が chain_type で解けること（RC3）。"""
        a = L.derivation_label(chain_record(operation="transform", chain_type="equation_chain"))
        b = L.derivation_label(chain_record(operation="eliminate", chain_type="system_level"))
        assert (a.text, a.qualifier) != (b.text, b.qualifier)

    def test_rung2_teaching_takeaway_prose(self):
        label = L.derivation_label(
            chain_record(steps=[], teaching_takeaway="この式から観測量どうしの関係が得られる。補足。")
        )
        assert label.text == "この式から観測量どうしの関係が得られる。"
        assert label.label_source == L.LABEL_SOURCE_TEXT

    def test_rung2_rejects_the_english_template_form(self):
        for takeaway in (
            "Derive result eq_tex_b16 from eq_tex_b14.",
            "Define eq_tex_b16.",
            "Eliminate b_s from the system.",
            "Extract the observable.",
        ):
            label = L.derivation_label(
                chain_record(steps=[], chain_type="", teaching_takeaway=takeaway)
            )
            assert label.text == "導出", takeaway
            assert label.unresolved is True

    def test_rung2_rejects_takeaways_that_embed_internal_ids(self):
        label = L.derivation_label(
            chain_record(
                steps=[], chain_type="", teaching_takeaway="ステップ step_001 で消去する。"
            )
        )
        assert label.text == "導出"
        assert label.unresolved is True

    def test_rung3_operation_sequence(self):
        chain = chain_record(
            steps=[
                {"step_id": "step_001", "operation": "linearize"},
                {"step_id": "step_002", "operation": "eliminate"},
                {"step_id": "step_003", "operation": "solve"},
            ]
        )
        label = L.derivation_label(chain)
        assert label.text == "線形化 → 消去（3ステップ）"
        assert label.label_source == L.LABEL_SOURCE_CONTROLLED_VOCAB

    def test_rung4_generic(self):
        label = L.derivation_label(chain_record(steps=[], chain_type=""))
        assert label.text == "導出"
        assert label.unresolved is True

    def test_sublabel_carries_the_operation_sequence_and_conditions(self):
        label = L.derivation_label(
            chain_record(operation="transform", conditions=["線形領域を仮定する。"])
        )
        assert "操作: transform → linearize" in label.sublabel
        assert "条件: 線形領域を仮定する。" in label.sublabel

    def test_focus_role_is_appended(self):
        label = L.derivation_label(chain_record(operation="transform"), focus_role="input")
        assert label.text.endswith("（入力）")

    def test_no_internal_id_in_label(self):
        label = L.derivation_label(chain_record(operation="transform"))
        assert "derivation_eq_tex_b16" not in label.text
        assert "step_001" not in label.text
        assert not L.is_internal_id_like(label.text)

    def test_operation_summary_helper(self):
        chain = chain_record()
        assert L.derivation_operation_summary(chain) == "transform → linearize"
        assert L.derivation_operation_summary(chain, translate=True) == "変換 → 線形化"


# ---------------------------------------------------------------------------
# グラフノード（§5.3）
# ---------------------------------------------------------------------------


class TestGraphNodeLabel:
    def test_main_node_uses_the_translated_stage_name(self):
        node = {
            "graph_layer": "main",
            "label": "Theory basis",
            "description": "観測量を密度ゆらぎとして定義する。以降の節で繰り返し使う。",
        }
        label = L.graph_node_label(node)
        assert label.text == "理論の土台"
        assert label.sublabel == "観測量を密度ゆらぎとして定義する。"
        assert label.qualifier == "theory_basis"
        assert label.label_source == L.LABEL_SOURCE_CONTROLLED_VOCAB

    def test_main_node_never_shows_the_english_stage_name(self):
        for display in (
            "Theory basis",
            "Observation model",
            "Observable construction",
            "Equation system",
            "Elimination",
            "Consistency relation",
            "Diagnostic / application",
        ):
            label = L.graph_node_label({"graph_layer": "main", "label": display})
            assert label.text
            assert not re.search(r"[A-Za-z]", label.text), display

    def test_main_node_accepts_the_legacy_stage_colon_form(self):
        label = L.graph_node_label(
            {"graph_layer": "main", "label": "Equation system: linearized fluid equations"}
        )
        assert label.text == "式の体系"
        assert label.qualifier == "equation_system"

    def test_detail_node_rejects_the_verb_plus_equation_id_label(self):
        node = {
            "graph_layer": "equation_detail",
            "visual_label": "Define eq_tex_b16",
            "display_label": "Define eq_tex_b16",
            "operation": "define",
            "theory_object": "フーリエ空間の密度コントラスト",
        }
        label = L.graph_node_label(node)
        assert "eq_tex_b16" not in label.text
        assert label.text == "定義: フーリエ空間の密度コントラスト"
        assert label.qualifier == "equation_detail"

    def test_detail_node_uses_the_equation_labeler_for_the_target(self):
        node = {
            "graph_layer": "equation_detail",
            "visual_label": "Define eq_tex_b16",
            "operation": "define",
            "output_equation_ids": ["eq_tex_b16"],
        }
        label = L.graph_node_label(
            node, equation_labeler=lambda eid: L.Label(text="δ_k を定義する式")
        )
        assert label.text == "定義: δ_k を定義する式"

    def test_detail_node_ignores_an_unresolved_equation_label(self):
        node = {
            "graph_layer": "equation_detail",
            "operation": "define",
            "output_equation_ids": ["eq_tex_b16"],
        }
        label = L.graph_node_label(
            node,
            equation_labeler=lambda eid: L.Label(text="数式", unresolved=True),
        )
        assert label.text == "定義"

    def test_detail_node_visual_label_is_used_when_readable(self):
        node = {"graph_layer": "equation_detail", "visual_label": "線形化ステップ"}
        label = L.graph_node_label(node)
        assert label.text == "線形化ステップ"
        assert label.label_source == L.LABEL_SOURCE_TEXT

    def test_detail_node_falls_back_to_description_then_generic(self):
        described = L.graph_node_label(
            {"graph_layer": "equation_detail", "description": "背景密度を割って正規化する。"}
        )
        assert described.text == "背景密度を割って正規化する。"
        empty = L.graph_node_label({"graph_layer": "equation_detail"})
        assert empty.text == "論理要素"
        assert empty.unresolved is True

    def test_main_node_without_a_resolvable_stage(self):
        label = L.graph_node_label({"graph_layer": "main", "label": "theory_op_0001"})
        assert label.text == "理論の段階"
        assert label.unresolved is True
        assert "theory_op_0001" not in label.text


# ---------------------------------------------------------------------------
# 記号（§5.4）
# ---------------------------------------------------------------------------


class TestSymbolDisplayText:
    """表示は原表記・照合はキー、の分離（2026-08-03 実機是正）。

    canonical_symbol は SymbolRegistry の normalize_symbol が波括弧・空白を
    全除去した**照合キー**（{\\bm{x}} → \\bmx）。キーを表示に流用すると
    TeX マクロ名が壊れる。表示は notation_variants の原表記を使う。
    """

    def test_plain_canonical_is_used_directly(self):
        rec = {"canonical_symbol": "b_1", "notation_variants": ["$b_1$", "b_1"]}
        assert L.symbol_display_text(rec) == "b_1"

    def test_tex_canonical_prefers_original_variant(self):
        rec = {
            "canonical_symbol": "\\bmx",  # 波括弧除去で壊れたキー
            "notation_variants": ["{\\bm{x}}", "\\bm{x}"],
        }
        assert L.symbol_display_text(rec) == "{\\bm{x}}"

    def test_composite_symbol_prefers_variant(self):
        rec = {
            "canonical_symbol": "δ(t,\\bmx)",
            "notation_variants": ["\\delta(t,{\\bm{x}})"],
        }
        assert L.symbol_display_text(rec) == "\\delta(t,{\\bm{x}})"

    def test_unbalanced_variants_are_skipped(self):
        rec = {
            "canonical_symbol": "\\bmx",
            "notation_variants": ["{\\bm{x}", "{\\bm{x}}"],  # 先頭は括弧が閉じない
        }
        assert L.symbol_display_text(rec) == "{\\bm{x}}"

    def test_dollar_wrapping_is_stripped(self):
        rec = {"canonical_symbol": "\\bmx", "notation_variants": ["$\\bm{x}$"]}
        assert L.symbol_display_text(rec) == "\\bm{x}"

    def test_no_usable_variant_falls_back_to_canonical(self):
        rec = {"canonical_symbol": "\\bmx", "notation_variants": ["{\\bm{x}"]}
        assert L.symbol_display_text(rec) == "\\bmx"

    def test_symbol_label_uses_display_text(self):
        label = L.symbol_label(
            {
                "canonical_symbol": "\\bmx",
                "notation_variants": ["{\\bm{x}}"],
                "meaning": "共動座標",
            }
        )
        assert label.text == "{\\bm{x}}"


class TestSymbolLabel:
    def test_symbol_is_shown_as_is_and_meaning_becomes_the_sublabel(self):
        label = L.symbol_label(
            {
                "canonical_symbol": "δ(t,x)",
                "definition_status": "defined",
                "definition_evidence_texts": [
                    "We define the density contrast as the fractional overdensity. It follows that…"
                ],
            }
        )
        assert label.text == "δ(t,x)"
        assert label.sublabel.startswith("We define the density contrast")
        assert label.qualifier == "defined"

    def test_defined_by_focus_is_annotated(self):
        label = L.symbol_label(
            {"canonical_symbol": "δ", "meaning": "密度コントラスト"}, defined_by_focus=True
        )
        assert label.sublabel == "密度コントラスト（この式で定義）"

    def test_definition_missing_is_stated_as_a_fact(self):
        label = L.symbol_label(
            {"canonical_symbol": "b_s", "review_reasons": ["definition_missing"]}
        )
        assert label.sublabel == "論文中に明示的な定義が見つかりません"
        assert label.qualifier == "definition_missing"

    def test_sublabel_is_never_empty(self):
        """RC5 の再発防止: 記号の意味欄を空のまま出さない（CP10）。"""
        for record in (
            {"canonical_symbol": "x"},
            {"canonical_symbol": "y", "definition_status": "unknown"},
            {"notation_variants": ["rho"]},
            {},
        ):
            assert L.symbol_label(record).sublabel

    def test_empty_symbol_degrades_to_generic(self):
        label = L.symbol_label({})
        assert label.text == "記号"
        assert label.unresolved is True


# ---------------------------------------------------------------------------
# 主張 / コンポーネント / 図 / 本文根拠（§5.5・§5.6）
# ---------------------------------------------------------------------------


class TestClaimLabel:
    def test_text_excerpt_and_type_sublabel(self):
        row = {
            "text": "密度コントラストは背景密度からの相対的なゆらぎとして定義され、" * 3,
            "claim_type": "definition",
            "evidence_text": "We define the density contrast as follows. Then…",
        }
        label = L.claim_label(row)
        assert label.text.endswith("…")
        assert len(label.text) <= 61
        assert label.qualifier == "definition"
        assert label.sublabel.startswith("定義")
        assert "「We define the density contrast as follows.」" in label.sublabel

    def test_falls_back_to_normalized_text_then_generic(self):
        assert L.claim_label({"text": "", "normalized_text": "短い主張"}).text == "短い主張"
        generic = L.claim_label({"text": "8f14e45f-ceea-467a-9d8a-1b2c3d4e5f60"})
        assert generic.text == "主張"
        assert generic.unresolved is True


class TestComponentLabel:
    def test_name_and_narrative_role(self):
        label = L.component_label(
            {
                "name": "密度コントラストの定義",
                "component_type": "definition",
                "narrative_role": "観測量を導入する土台",
                "summary": "背景密度からの相対ゆらぎを定義する。",
            }
        )
        assert label.text == "密度コントラストの定義"
        assert label.sublabel == "観測量を導入する土台"
        assert label.qualifier == "definition"

    def test_thesis_context_and_summary_fallbacks(self):
        label = L.component_label(
            {"name": "A", "thesis_context": {"role_in_thesis": "中心命題を支える"}}
        )
        assert label.sublabel == "中心命題を支える"
        summary_only = L.component_label({"name": "A", "summary": "最初の文。次の文。"})
        assert summary_only.sublabel == "最初の文。"


class TestFigureAndEvidenceLabel:
    def test_figure_uses_the_caption_first_sentence(self):
        label = L.figure_label(
            {
                "caption_text": "Fig. 3. Optical layout of the interferometer. Details in text.",
                "figure_label": "Fig. 3",
                "effective_mode": "functional_diagram",
            }
        )
        assert label.text.startswith("Fig. 3. Optical layout")
        assert label.qualifier == "functional_diagram"

    def test_figure_parts_become_the_sublabel(self):
        label = L.figure_label(
            {"caption_text": "Setup.", "suggested_mode": "functional_diagram"},
            part_labels=["レーザー", "ビームスプリッタ"],
        )
        assert label.sublabel == "レーザー・ビームスプリッタ"

    def test_figure_generic(self):
        label = L.figure_label({})
        assert label.text == "図"
        assert label.unresolved is True

    def test_evidence_label_is_the_verbatim_quote(self):
        label = L.evidence_label(
            {"evidence_text": "We define the density contrast as the fractional overdensity."},
            section_label="2.1 Density contrast",
        )
        assert label.text.startswith("「We define the density contrast")
        assert label.sublabel == "2.1 Density contrast"

    def test_evidence_tex_quote_degrades(self):
        label = L.evidence_label({"evidence_text": LATEX})
        assert label.text == "本文の根拠箇所"
        assert label.unresolved is True


# ---------------------------------------------------------------------------
# 自己記述する役割（§4.4）
# ---------------------------------------------------------------------------


class TestSelfDescribedRole:
    def test_equation_role_plus_stage(self):
        role = L.self_described_role("equation", equation_record(), stage_key="theory_basis")
        assert role == "理論の土台の段階で使われる定義式"

    def test_equation_role_only(self):
        assert L.self_described_role("equation", equation_record()) == "定義式"

    def test_equation_stage_display_name_is_accepted(self):
        role = L.self_described_role("equation", equation_record(), stage_key="Theory basis")
        assert role == "理論の土台の段階で使われる定義式"

    def test_no_role_returns_none(self):
        record = equation_record()
        record["semantics"] = dict(record["semantics"], role_in_argument="")
        assert L.self_described_role("equation", record) is None

    def test_component_uses_its_own_role_description(self):
        assert (
            L.self_described_role("theory_component", {"narrative_role": "中心命題を支える"})
            == "中心命題を支える"
        )
        assert L.self_described_role("theory_claim", {"text": "x"}) is None


# ---------------------------------------------------------------------------
# 横断ガードレール
# ---------------------------------------------------------------------------


ALL_LABELS = [
    lambda: L.equation_label(equation_record()),
    lambda: L.equation_label(equation_record(label="eq_2_7")),
    lambda: L.equation_label(None),
    lambda: L.derivation_label(chain_record(operation="transform")),
    lambda: L.derivation_label(chain_record(steps=[], chain_type="")),
    lambda: L.graph_node_label({"graph_layer": "main", "label": "Theory basis"}),
    lambda: L.graph_node_label(
        {"graph_layer": "equation_detail", "visual_label": "Define eq_tex_b16", "operation": "define"}
    ),
    lambda: L.symbol_label({"canonical_symbol": "δ"}),
    lambda: L.claim_label({"text": "ある主張。", "claim_type": "result"}),
    lambda: L.component_label({"name": "ある要素"}),
    lambda: L.figure_label({"caption_text": "Setup."}),
    lambda: L.evidence_label({"evidence_text": "quoted sentence."}),
]


class TestCrossCuttingRules:
    def test_no_label_text_is_an_internal_id(self):
        for factory in ALL_LABELS:
            label = factory()
            assert label.text
            assert not L.is_internal_id_like(label.text), label.text

    def test_no_label_text_is_raw_tex(self):
        for factory in ALL_LABELS:
            label = factory()
            assert not L.looks_like_tex_math(label.text), label.text

    def test_text_never_equals_sublabel(self):
        for factory in ALL_LABELS:
            label = factory()
            assert not (label.sublabel and label.sublabel == label.text)

    def test_label_source_is_from_the_controlled_set(self):
        allowed = {
            L.LABEL_SOURCE_EXPLANATION,
            L.LABEL_SOURCE_SEMANTIC_SUMMARY,
            L.LABEL_SOURCE_PAPER_NUMBER,
            L.LABEL_SOURCE_TEXT,
            L.LABEL_SOURCE_CONTROLLED_VOCAB,
            L.LABEL_SOURCE_GENERIC,
        }
        for factory in ALL_LABELS:
            assert factory().label_source in allowed

    def test_module_is_pure(self):
        """W層の投影は純粋関数（DB / FastAPI / LLM を import しない）。"""
        source = Path(L.__file__).read_text(encoding="utf-8")
        forbidden = re.compile(
            r"^\s*(?:from|import)\s+(?:fastapi|sqlalchemy|openai|core\.postgres)", re.M
        )
        assert forbidden.search(source) is None
        assert "get_session(" not in source

    def test_translations_are_only_read_from_element_vocab(self):
        """訳語文字列を labels.py に直書きしない（CP4）。

        許容するのは種別の一般ラベル・事実文・接続語（ラダー末端の固定文言）のみで、
        統制語彙の訳（stage / operation / chain_type / claim_type …）は
        element_vocab から引くこと。docstring / コメントでの言及は対象外なので、
        AST から**実行される文字列リテラル**だけを取り出して検査する。
        """
        for literal in _code_string_literals(Path(L.__file__).read_text(encoding="utf-8")):
            for translated in ("理論の土台", "式の体系", "系レベルの操作", "線形化", "整合関係"):
                assert translated not in literal, translated
