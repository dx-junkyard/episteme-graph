"""理論モジュール層 — グラフレビュー画面（UI）の静的検証。

正本: ``docs/features/theory_module_layer_design.md`` §7.3 / §8.1（TM5 / TM6 / TM8 / TM10）。
admin-graph-review.js（ES5・GraphReview）の理論モジュール図・(b-読) の目印・初期表示・
管理UI 3 点セットを、Node 実行なしのソース検査で確認する。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS_PATH = ROOT / "frontend" / "public" / "js" / "admin-graph-review.js"
JS_SRC = JS_PATH.read_text(encoding="utf-8")
HTML_SRC = (ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
CSS_SRC = (ROOT / "frontend" / "public" / "css" / "styles.css").read_text(encoding="utf-8")
MANUAL_SRC = (ROOT / "docs" / "manual" / "teacher" / "26-admin-graph-review.md").read_text(encoding="utf-8")


def _function_block(name: str) -> str:
    start = JS_SRC.index("  function " + name + "(")
    end = JS_SRC.find("\n  function ", start + 1)
    return JS_SRC[start: end if end >= 0 else len(JS_SRC)]


def _module_section() -> str:
    start = JS_SRC.index("  // 理論モジュール層（theory_module_layer_design.md §4 / §7.3 / §8.1）")
    end = JS_SRC.index("  // ノード詳細ペイン（レビュー専用の投影", start)
    return JS_SRC[start:end]


class TestEs5:
    def test_no_es6_syntax_in_module_section(self):
        section = _module_section()
        assert "=>" not in section
        assert not re.search(r"\bconst\s", section)
        assert not re.search(r"\blet\s", section)
        assert "`" not in section

    def test_whole_file_stays_es5(self):
        assert "=>" not in JS_SRC
        assert not re.search(r"\bconst\s", JS_SRC)
        assert not re.search(r"\blet\s", JS_SRC)
        assert "`" not in JS_SRC


class TestFetch:
    def test_theory_modules_endpoint_fetched_lazily_once_per_open(self):
        block = _function_block("loadTheoryModules")
        assert '"/theory-modules"' in block
        # 別教材の遅延応答は破棄する（論文層と同じ作法）。
        assert "state.documentId !== documentId" in block
        # 失敗は事実文（例外で画面を止めない）。
        assert "state.theoryModulesError = MODULE_ERROR_TEXT" in block
        opener = _function_block("open")
        assert opener.count("loadTheoryModules(documentId)") == 1

    def test_no_polling(self):
        section = _module_section()
        assert "setInterval" not in section
        assert "setTimeout" not in section


class TestGraphViewDelegation:
    """GR8: モジュール図は graphView の既存関数へ合成ノードを渡して描く。"""

    def test_module_map_uses_existing_graph_view_functions(self):
        block = _function_block("renderModuleNetwork")
        for api in ("g.layoutPositions(", "g.visNodeSpec(", "g.visEdgeSpec(", "g.networkOptions("):
            assert api in block, api
        assert "withSavedPositions(" in block

    def test_no_new_svg_or_canvas_renderer(self):
        section = _module_section()
        assert "<svg" not in section
        assert "createElementNS" not in section
        assert "getContext(" not in section

    def test_layer_options_not_modified(self):
        # 原稿スタジオと共有の layerOptions は変えず、グラフレビュー側でボタンを1つ足す。
        toolbar = _function_block("renderLayerToolbar")
        assert "gv().layerOptions(graphNodes())" in toolbar
        assert "moduleLayerButtonHtml(options)" in toolbar

    def test_foundation_hand_offs_drawn_as_dashed_edges(self):
        # 外枠どうしの edges は 0 本になり得る（2 つの TeX fixture とも）。共通に使う式の
        # producer → consumer を点線の受け渡しとして描く。導くモジュールの無い式は辺にしない。
        block = _function_block("renderModuleNetwork")
        assert "data.foundations" in block
        assert "producer_module_keys" in block
        assert "consumer_module_keys" in block
        assert "if (edge.via_foundation) spec.dashes = true" in block

    def test_sink_drawn_with_distinct_shape(self):
        block = _function_block("renderModuleNetwork")
        assert 'spec.shape = "database"' in block

    def test_color_and_border_from_dto(self):
        node = _function_block("moduleSyntheticNode")
        assert "module.dominant_stage" in node
        assert "module.source_backing_status" in node


class TestNoInternalIdsDrawn:
    """TM10: module_key / eq_op_ / theory_op_ / eq_tex_ を描画文字列に使わない。"""

    def test_labels_come_from_dto_label_fields(self):
        block = _function_block("renderModuleNetwork")
        assert "spec.label = labels[id]" in block
        assert "moduleCanvasLabel(node.display_label)" in block
        assert "moduleDisplayLabel(module)" in _function_block("moduleSyntheticNode")

    def test_internal_id_prefixes_absent_from_module_section(self):
        section = _module_section()
        for token in ("eq_op_", "theory_op_", "eq_tex_", '"m1:'):
            assert token not in section, token

    def test_module_key_never_escaped_into_html(self):
        section = _module_section()
        assert "esc(module.module_key" not in section
        assert "esc(String(module.module_key" not in section
        assert "esc(key" not in section

    def test_assumption_texts_rendered_as_premises_not_escaped_raw(self):
        # assumption_ids は前提の本文（ID ではない）。「要求される前提」に本文として並べる。
        block = _function_block("moduleDetailHtml")
        assert "module.assumption_ids" in block
        assert "premiseItems" in block


class TestNoCounts:
    """TM6: 層ボタン・詳細ペインに件数・接点の本数を出さない。"""

    def test_module_button_has_no_count(self):
        button = _function_block("moduleLayerButtonHtml")
        assert "graph-review-layer-count" not in button
        assert "count" not in button
        assert "length" not in button.replace("!options.length", "")

    def test_detail_has_no_count_text(self):
        for name in ("moduleDetailHtml", "sinkDetailHtml", "renderModuleDetail", "moduleMembersHtml"):
            block = _function_block(name)
            assert "件" not in block, name
            assert ".length +" not in block, name
            assert "String(" + "module.members.length" not in block, name


class TestDegradedFacts:
    def test_unavailable_fact_text_verbatim(self):
        assert (
            '"この教材では式の導出が再現されていないため、理論モジュールを組めません。"' in JS_SRC
        )
        block = _function_block("renderModuleNetwork")
        assert "MODULE_UNAVAILABLE_TEXT" in block
        assert "moduleFacts()" in block

    def test_available_false_means_no_map(self):
        block = _function_block("moduleMapAvailable")
        assert "data.available === false" in block

    def test_claim_sequence_empty_fact_text_verbatim(self):
        assert (
            '"この教材では式の導出を再現できていません。主張の並びは『論文の順』で見られます。"'
            in JS_SRC
        )
        network = _function_block("renderNetwork")
        assert "view.claimSequenceHidden" in network
        assert "MODULE_CLAIM_SEQUENCE_EMPTY_TEXT" in network

    def test_isolated_reason_facts(self):
        assert '"導出のつながりに循環があるため単独にしています"' in JS_SRC
        assert '"隣の手順と接点が多く、まとめていません"' in JS_SRC
        assert "MODULE_ISOLATED_TEXTS[" in _function_block("moduleDetailHtml")

    def test_comparison_heading(self):
        assert '"照合用（AI の原案）"' in JS_SRC
        assert "MODULE_COMPARISON_HEADING" in _function_block("moduleDetailHtml")


class TestClaimSequenceMarker:
    """(b-読): 判定はサーバの目印だけを見る。フロントで claim_chain を判定しない。"""

    def test_marker_applied_in_layer_filter(self):
        index = _function_block("claimSequenceNodeIndex")
        assert "data.claim_sequence_node_ids" in index
        view = _function_block("visibleGraphView")
        assert "gv().filterByLayer(state.graph, state.layer)" in view
        assert 'state.layer !== "equation_detail" && state.layer !== "all"' in view
        assert "claimSequenceNodeIndex()" in view

    def test_marker_used_by_canvas_and_counts(self):
        assert "var view = visibleGraphView();" in _function_block("renderNetwork")
        assert "visibleGraphView()" in _function_block("unreviewedNodesInView")

    def test_no_frontend_reimplementation_of_chain_type(self):
        section = _module_section()
        assert "claim_chain" not in section
        assert "chain_type" not in section
        assert "linked_derivation_ids" not in section

    def test_fail_to_current_when_not_fetched(self):
        # 取得前・失敗時は目印が無い（null）= 従来どおり全部描く。
        view = _function_block("visibleGraphView")
        assert "if (!hidden) return view;" in view


class TestInitialDisplay:
    """O-3 (b): モジュールが導出できる教材だけモジュール図から始める。"""

    def test_initial_module_view_rule(self):
        block = _function_block("maybeStartWithModuleView")
        assert "state.moduleAutoDecided" in block
        assert "state.layerChosenByUser" in block
        assert "moduleMapAvailable()" in block
        assert "state.moduleView = true" in block

    def test_decided_after_both_arrive(self):
        assert "maybeStartWithModuleView();" in _function_block("loadGraph")
        assert "maybeStartWithModuleView()" in _function_block("afterTheoryModulesArrived")

    def test_open_starts_on_main(self):
        opener = _function_block("open")
        assert 'state.layer = "main"' in opener
        assert "state.moduleView = false" in opener


class TestMemberNavigation:
    def test_member_step_switches_to_detail_layer_and_focuses(self):
        block = _function_block("focusModuleMemberStep")
        assert 'state.layer = "equation_detail"' in block
        assert "state.focusNodeOnce = nodeId" in block
        assert "state.moduleView = false" in block
        members = _function_block("moduleMembersHtml")
        assert "nodeIds[0]" in members
        assert 'data-ui-anchor="graph-review.module-member"' in members


class TestScreenContextUnchanged:
    def test_get_screen_context_does_not_know_modules(self):
        block = _function_block("getScreenContext")
        assert "module" not in block.lower()
        assert "module" not in _function_block("screenContextLayer").lower()
        assert "module" not in _function_block("screenContextEntities").lower()

    def test_module_view_does_not_reuse_layer_state(self):
        # state.layer に "module" を入れない（getScreenContext の層語彙を変えない）。
        assert 'state.layer = "module"' not in JS_SRC


class TestAnchorsAndAssets:
    def test_anchor_carriers_present(self):
        assert 'data-ui-anchor="graph-review.module-view"' in JS_SRC
        assert 'data-ui-anchor="graph-review.module-member"' in JS_SRC

    def test_manual_sections_present(self):
        assert "{#theory-modules}" in MANUAL_SRC
        assert "{#theory-module-member}" in MANUAL_SRC
        # 無効化・事実文だけになる場合の理由と解消方法を持つ。
        assert "この教材では式の導出が再現されていないため、理論モジュールを組めません。" in MANUAL_SRC
        assert "TeX" in MANUAL_SRC

    def test_cache_busting_version_bumped(self):
        match = re.search(r'/js/admin-graph-review\.js\?v=([^"]+)"', HTML_SRC)
        assert match, "admin-graph-review.js の読み込みが見つからない"
        assert match.group(1) not in ("chat-overview-20260922-1", "reproduction-wave2-20260919-1")
        assert "theory-modules" in match.group(1)

    def test_css_defined(self):
        assert ".graph-review-canvas-fact" in CSS_SRC
        fact_css = CSS_SRC[CSS_SRC.index(".graph-review-canvas-fact"):]
        fact_css = fact_css[: fact_css.index("}")]
        assert "red" not in fact_css and "#ef4444" not in fact_css
