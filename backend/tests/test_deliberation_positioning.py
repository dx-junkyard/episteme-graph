"""W層 面②「文脈的位置づけ」（``core/deliberation/positioning.py``）の純粋部の単体テスト。

設計書 `docs/features/element_deliberation_workspace_design.md` §4。DB 実接続は必要とせず、
fake データ（artifact 相当の dict / 手組みの AtlasSkeleton）で各レンズの組み立てロジックを
検証する。DB を読み出す薄いラッパ（``_document_cartridge_id`` 等）自体はここでは検証しない
（docker 復帰後に実データで確認する）。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import atlas as atlas_module  # noqa: E402
from core.deliberation import positioning  # noqa: E402


# ---------------------------------------------------------------------------
# §4.1 論文内: derivation_chain / thesis_reconstruction の位置づけ
# ---------------------------------------------------------------------------


class TestChainPositionForClaim:
    def test_step_level_input_claim(self):
        chains = [
            {
                "derivation_id": "d1",
                "steps": [
                    {"step_id": "s1", "input_claim_ids": ["c1"], "output_claim_ids": []},
                ],
            }
        ]
        assert positioning._chain_position_for_claim(chains, "c1") == (
            "導出チェーン「d1」のステップ「s1」の入力 claim"
        )

    def test_chain_level_assumption(self):
        chains = [{"derivation_id": "d1", "steps": [], "assumption_ids": ["c9"]}]
        result = positioning._chain_position_for_claim(chains, "c9")
        assert result == "導出チェーン「d1」の前提 claim"

    def test_no_match_returns_none(self):
        chains = [{"derivation_id": "d1", "steps": []}]
        assert positioning._chain_position_for_claim(chains, "does-not-exist") is None

    def test_empty_chains_returns_none(self):
        assert positioning._chain_position_for_claim([], "c1") is None


class TestChainPositionForEquation:
    def test_step_output_equation(self):
        chains = [
            {
                "derivation_id": "d1",
                "steps": [
                    {"step_id": "s1", "input_equation_ids": [], "output_equation_ids": ["eq_1"]},
                ],
            }
        ]
        assert positioning._chain_position_for_equation(chains, "eq_1") == (
            "導出チェーン「d1」のステップ「s1」の出力式"
        )

    def test_system_level_intermediate_equation(self):
        chains = [{"derivation_id": "d2", "steps": [], "intermediate_equation_ids": ["eq_mid"]}]
        assert positioning._chain_position_for_equation(chains, "eq_mid") == (
            "導出チェーン「d2」の中間式"
        )

    def test_no_match_returns_none(self):
        chains = [{"derivation_id": "d1", "steps": []}]
        assert positioning._chain_position_for_equation(chains, "eq_x") is None


class TestComponentChainLinks:
    def test_collects_all_linked_chains(self):
        chains = [
            {"derivation_id": "d1", "linked_component_ids": ["comp_a"]},
            {"derivation_id": "d2", "linked_component_ids": ["comp_b"]},
            {"derivation_id": "d3", "linked_component_ids": ["comp_a", "comp_b"]},
        ]
        assert positioning._component_chain_links(chains, "comp_a") == ["d1", "d3"]

    def test_no_link_returns_empty_list(self):
        chains = [{"derivation_id": "d1", "linked_component_ids": []}]
        assert positioning._component_chain_links(chains, "comp_z") == []


class TestThesisPositionForClaim:
    def test_central_thesis_claim(self):
        thesis = {"central_thesis": {"claim_ids": ["c1"]}}
        assert positioning._thesis_position_for_claim(thesis, "c1") == (
            "中心命題（central thesis）を構成する claim"
        )

    def test_supporting_subclaim(self):
        thesis = {"central_thesis": {}, "supporting_subclaim_ids": ["c2"]}
        assert positioning._thesis_position_for_claim(thesis, "c2") == "中心命題を支持するサブclaim"

    def test_support_structure_section_label(self):
        thesis = {
            "central_thesis": {},
            "support_structure": {"derivation_core": [{"claim_ids": ["c3"]}]},
        }
        assert positioning._thesis_position_for_claim(thesis, "c3") == "支持構造「導出の核」に位置づく"

    def test_unknown_section_name_falls_back_to_raw_name(self):
        thesis = {"support_structure": {"some_new_section": [{"claim_ids": ["c4"]}]}}
        assert positioning._thesis_position_for_claim(thesis, "c4") == (
            "支持構造「some_new_section」に位置づく"
        )

    def test_no_match_returns_none(self):
        thesis = {"central_thesis": {"claim_ids": []}, "support_structure": {}}
        assert positioning._thesis_position_for_claim(thesis, "unknown") is None


class TestThesisAnchorForComponent:
    def test_anchor_hit(self):
        thesis = {"anchor_node_ids": ["comp_1"]}
        assert positioning._thesis_anchor_for_component(thesis, "comp_1") == (
            "thesis の anchor node（グラフ走査の到達点）"
        )

    def test_not_anchor_returns_none(self):
        thesis = {"anchor_node_ids": ["comp_1"]}
        assert positioning._thesis_anchor_for_component(thesis, "comp_2") is None


# ---------------------------------------------------------------------------
# document_structure artifact の索引ヘルパ
# ---------------------------------------------------------------------------


class TestStructureIndexHelpers:
    def test_sections_by_id(self):
        artifacts = {
            "document_structure": {
                "sections": [
                    {"section_id": "s1", "title": "Introduction"},
                    {"section_id": "s2", "title": "Method"},
                ]
            }
        }
        by_id = positioning._sections_by_id(artifacts)
        assert by_id["s1"]["title"] == "Introduction"
        assert by_id["s2"]["title"] == "Method"

    def test_sections_by_id_missing_artifact_returns_empty(self):
        assert positioning._sections_by_id({}) == {}

    def test_blocks_by_id(self):
        artifacts = {
            "document_structure": {
                "blocks": [{"block_id": "b1", "section_id": "s1", "page": 3}]
            }
        }
        by_id = positioning._blocks_by_id(artifacts)
        assert by_id["b1"]["page"] == 3

    def test_section_label_handles_none(self):
        assert positioning._section_label(None) == ""
        assert positioning._section_label({"title": "  Discussion  "}) == "Discussion"


# ---------------------------------------------------------------------------
# §4.3 分野の地図: atlas マッチング（match_topic_to_concept の再利用）
# ---------------------------------------------------------------------------


def _make_skeleton():
    concept = atlas_module.SkeletonConcept(id="concept_gauge", label="ゲージ不変性")
    region = atlas_module.SkeletonRegion(id="region_symmetry", label="対称性", concepts=(concept,))
    return atlas_module.AtlasSkeleton(cartridge="demo_cartridge", regions=(region,))


class TestAtlasItemsFromSkeleton:
    def test_matches_concept_label_and_includes_region(self):
        skeleton = _make_skeleton()
        items = positioning._atlas_items_from_skeleton(skeleton, ["ゲージ不変性"])
        assert items == [
            {"label": "分野の地図での対応", "value": "対称性の「ゲージ不変性」"}
        ]

    def test_no_label_match_returns_empty_list(self):
        skeleton = _make_skeleton()
        assert positioning._atlas_items_from_skeleton(skeleton, ["まったく無関係の概念"]) == []

    def test_deduplicates_multiple_labels_matching_same_concept(self):
        skeleton = _make_skeleton()
        items = positioning._atlas_items_from_skeleton(
            skeleton, ["ゲージ不変性", "ゲージ不変性"]
        )
        assert len(items) == 1


# ---------------------------------------------------------------------------
# §4.4 C層 endorsement / D層 epistemic のフォーマッタ
# ---------------------------------------------------------------------------


class TestEndorsementItemsFromRows:
    def test_no_rows_returns_no_explanation_item(self):
        assert positioning._endorsement_items_from_rows([]) == [
            {"label": "説明・承認", "value": "説明バージョンなし"}
        ]

    def test_standard_and_endorsed(self):
        rows = [("standard", False, "teacher_approved", 2)]
        items = positioning._endorsement_items_from_rows(rows)
        labels_values = {i["label"]: i["value"] for i in items}
        assert labels_values["標準説明"] == "あり"
        assert labels_values["承認"] == "承認あり"
        assert "共有設定" not in labels_values

    def test_no_endorsement_yet(self):
        rows = [("personal", True, "teacher_review_required", 0)]
        items = positioning._endorsement_items_from_rows(rows)
        labels_values = {i["label"]: i["value"] for i in items}
        assert labels_values["教員の独自解釈"] == "あり"
        assert labels_values["承認"] == "承認なし"
        assert labels_values["共有設定"] == "他教員への共有可"

    def test_never_leaks_raw_endorser_count(self):
        rows = [("standard", False, "teacher_approved", 7)]
        items = positioning._endorsement_items_from_rows(rows)
        for item in items:
            assert "7" not in item["value"]

    # -- citation 集約（設計書 §4.4「誰が承認・引用したか」・レビュー指摘 2026-07-15） --

    def test_citation_present_reports_presence_label(self):
        rows = [("standard", False, "teacher_approved", 2, 3)]
        items = positioning._endorsement_items_from_rows(rows)
        labels_values = {i["label"]: i["value"] for i in items}
        assert labels_values["引用"] == "引用あり"

    def test_no_citation_reports_absence_label(self):
        rows = [("standard", False, "teacher_approved", 2, 0)]
        items = positioning._endorsement_items_from_rows(rows)
        labels_values = {i["label"]: i["value"] for i in items}
        assert labels_values["引用"] == "引用なし"

    def test_backward_compatible_with_four_tuples_missing_citation_count(self):
        # citation を持たない呼び出し元（旧テストや将来の呼び出し）が4要素タプルを
        # 渡しても壊れず、citation なし扱いにフォールバックする。
        rows = [("standard", False, "teacher_approved", 2)]
        items = positioning._endorsement_items_from_rows(rows)
        labels_values = {i["label"]: i["value"] for i in items}
        assert labels_values["引用"] == "引用なし"

    def test_never_leaks_raw_citation_count(self):
        rows = [("standard", False, "teacher_approved", 1, 9)]
        items = positioning._endorsement_items_from_rows(rows)
        for item in items:
            assert "9" not in item["value"]

    def test_any_row_with_citation_marks_presence(self):
        # 複数 explanation バージョンのうち1つでも引用があれば「引用あり」。
        rows = [
            ("standard", False, "teacher_approved", 2, 0),
            ("personal", True, "teacher_review_required", 0, 1),
        ]
        items = positioning._endorsement_items_from_rows(rows)
        labels_values = {i["label"]: i["value"] for i in items}
        assert labels_values["引用"] == "引用あり"


class TestEpistemicItemsFromLedger:
    def test_no_ledger_row_reports_unrecorded(self):
        items = positioning._epistemic_items_from_ledger(None, None, [])
        assert items == [{"label": "検証状態", "value": "台帳未記帳"}]

    def test_verification_status_label_mapping(self):
        items = positioning._epistemic_items_from_ledger("indirectly_supported", [], [])
        labels_values = {i["label"]: i["value"] for i in items}
        assert labels_values["検証状態"] == "間接的に支持"
        assert labels_values["検証スコープ"] == "検証スコープは未記帳"

    def test_empty_scope_is_not_treated_as_error(self):
        # D層の不変条項: スコープ0件は異常ではなく発見。禁止語彙（警告的な文言）を含まない。
        items = positioning._epistemic_items_from_ledger("untested", [], [])
        scope_item = next(i for i in items if i["label"] == "検証スコープ")
        assert scope_item["value"] == "検証スコープは未記帳"
        assert "エラー" not in scope_item["value"]
        assert "異常" not in scope_item["value"]

    def test_recorded_scope(self):
        items = positioning._epistemic_items_from_ledger(
            "directly_verified", [{"scope_id": "s1"}], []
        )
        labels_values = {i["label"]: i["value"] for i in items}
        assert labels_values["検証状態"] == "直接検証済み"
        assert labels_values["検証スコープ"] == "検証スコープを記帳済み"

    def test_challenge_types_are_deduplicated_and_labeled(self):
        items = positioning._epistemic_items_from_ledger(
            "unknown", [], ["definitional", "definitional", "hidden_lemma"]
        )
        challenge_item = next(i for i in items if i["label"] == "疑義の種類")
        assert "定義の曖昧さ・循環" in challenge_item["value"]
        assert "暗黙の補題への依存" in challenge_item["value"]


# ---------------------------------------------------------------------------
# build() の外形（fail-soft の合成）
# ---------------------------------------------------------------------------


class TestBuildContract:
    def test_build_returns_all_five_lens_keys_only(self):
        # Phase 1 で cross_corpus（§4.2）が5番目のレンズとして加わった。存在しない要素 id を
        # 渡すと各レンズは DB 上で見つからず（DB 接続自体が無い test 環境では例外）fail-soft で
        # None になるが、キー自体は必ず5つ揃う。
        ref = positioning.ElementRef(
            scope="document",
            element_type="theory_claim",
            element_id="00000000-0000-0000-0000-000000000000",
            document_id="does-not-exist",
        )
        result = positioning.build(ref)
        assert set(result.keys()) == {
            "intra_document",
            "cross_corpus",
            "atlas",
            "endorsement",
            "epistemic",
        }


# ---------------------------------------------------------------------------
# §4.2 コーパス横断（cross_corpus）— chunk-proxy 方式の純粋部
# ---------------------------------------------------------------------------


class TestCrossCorpusRepresentativeText:
    """代表テキストの非LLM連結（設計書 §4.2・§15 未決2: 要約 LLM ではなく連結を採用）。"""

    def test_theory_claim_joins_text_and_normalized_text(self):
        parts = {"text": "運動量は保存される。", "normalized_text": "運動量保存則"}
        result = positioning._cross_corpus_representative_text("theory_claim", parts)
        assert result == "運動量は保存される。\n運動量保存則"

    def test_theory_component_joins_name_and_summary(self):
        parts = {"name": "ラグランジアン形式", "summary": "作用原理から運動方程式を導く枠組み"}
        result = positioning._cross_corpus_representative_text("theory_component", parts)
        assert result == "ラグランジアン形式\n作用原理から運動方程式を導く枠組み"

    def test_equation_joins_plain_text_and_symbol_descriptions(self):
        parts = {"plain_text": "F = m a", "symbol_descriptions": "F: 力、m: 質量"}
        result = positioning._cross_corpus_representative_text("equation", parts)
        assert result == "F = m a\nF: 力、m: 質量"

    def test_figure_uses_caption_text_only(self):
        parts = {"caption_text": "図1: 実験装置の概略図"}
        assert positioning._cross_corpus_representative_text("figure", parts) == "図1: 実験装置の概略図"

    def test_shared_part_joins_name_and_summary(self):
        parts = {"name": "ゲージ場", "summary": "局所対称性から導入される場"}
        result = positioning._cross_corpus_representative_text("shared_part", parts)
        assert result == "ゲージ場\n局所対称性から導入される場"

    def test_empty_parts_returns_empty_string(self):
        assert positioning._cross_corpus_representative_text("theory_claim", {}) == ""

    def test_missing_field_is_skipped_not_stringified(self):
        # normalized_text が無いとき "None" のような文字列を連結してはいけない。
        parts = {"text": "  Aだけがある。  "}
        assert positioning._cross_corpus_representative_text("theory_claim", parts) == "Aだけがある。"

    def test_whitespace_only_parts_returns_empty_string(self):
        parts = {"text": "   ", "normalized_text": None}
        assert positioning._cross_corpus_representative_text("theory_claim", parts) == ""

    def test_unknown_element_type_returns_empty_string(self):
        assert positioning._cross_corpus_representative_text("unknown_type", {"text": "x"}) == ""


class TestCrossCorpusItemsFromResults:
    """検索結果 → items 契約への変換（自 document 除外・label/value/document_id のみ）。"""

    def test_builds_items_with_title_and_document_id(self):
        results = [{"material_id": "mat-a", "score": 0.91, "text": "..."}]
        documents_by_material_id = {"mat-a": {"document_id": "doc-a", "title": "論文A"}}
        items = positioning._cross_corpus_items_from_results(results, documents_by_material_id, None)
        assert items == [{"label": "関連する教材", "value": "論文A", "document_id": "doc-a"}]

    def test_falls_back_to_material_id_when_title_missing(self):
        results = [{"material_id": "mat-a"}]
        documents_by_material_id = {"mat-a": {"document_id": "doc-a", "title": ""}}
        items = positioning._cross_corpus_items_from_results(results, documents_by_material_id, None)
        assert items[0]["value"] == "mat-a"

    def test_excludes_self_document(self):
        # exclude_material_id の一次防御に加え、material_id → document_id 変換後にも
        # 自 document を二重に除外する（設計書 §4.2「自 document は除外」）。
        results = [
            {"material_id": "mat-self"},
            {"material_id": "mat-other"},
        ]
        documents_by_material_id = {
            "mat-self": {"document_id": "doc-self", "title": "自分の論文"},
            "mat-other": {"document_id": "doc-other", "title": "他の論文"},
        }
        items = positioning._cross_corpus_items_from_results(
            results, documents_by_material_id, "doc-self"
        )
        assert [i["document_id"] for i in items] == ["doc-other"]

    def test_unresolvable_material_id_is_skipped(self):
        results = [{"material_id": "mat-unknown"}]
        items = positioning._cross_corpus_items_from_results(results, {}, None)
        assert items == []

    def test_empty_results_returns_empty_list(self):
        assert positioning._cross_corpus_items_from_results([], {}, None) == []

    def test_items_never_carry_score_or_confidence_keys(self):
        # W8: 近傍検索の一致度・生数値は items に持ち出さない。
        results = [{"material_id": "mat-a", "score": 0.99}]
        documents_by_material_id = {"mat-a": {"document_id": "doc-a", "title": "論文A"}}
        items = positioning._cross_corpus_items_from_results(results, documents_by_material_id, None)
        for item in items:
            assert set(item.keys()) == {"label", "value", "document_id"}

    def test_respects_top_k_constant(self):
        # 設計書 §4.2: 上位 k（既定5）。定数自体の値を固定する。
        assert positioning._CROSS_CORPUS_TOP_K == 5


class TestEquationSymbolDescriptions:
    """SymbolRegistry（#355）artifact から equation の記号説明を連結する。"""

    def test_collects_defining_and_used_symbols(self):
        artifacts = {
            "symbol_registry": {
                "records": [
                    {
                        "canonical_symbol": "F",
                        "defining_equation_ids": ["eq_1"],
                        "used_in_equation_ids": [],
                        "definition_evidence_texts": ["力を表す"],
                    },
                    {
                        "canonical_symbol": "m",
                        "defining_equation_ids": [],
                        "used_in_equation_ids": ["eq_1"],
                        "definition_evidence_texts": [],
                    },
                    {
                        "canonical_symbol": "q",
                        "defining_equation_ids": ["eq_2"],
                        "used_in_equation_ids": [],
                        "definition_evidence_texts": [],
                    },
                ]
            }
        }
        result = positioning._equation_symbol_descriptions(artifacts, "eq_1")
        assert "F: 力を表す" in result
        assert "m" in result
        assert "q" not in result

    def test_no_symbol_registry_artifact_returns_empty_string(self):
        assert positioning._equation_symbol_descriptions({}, "eq_1") == ""

    def test_no_matching_records_returns_empty_string(self):
        artifacts = {"symbol_registry": {"records": [{"canonical_symbol": "x", "defining_equation_ids": ["eq_9"]}]}}
        assert positioning._equation_symbol_descriptions(artifacts, "eq_1") == ""
