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


# ---------------------------------------------------------------------------
# Phase 1 UI（設計書 §13.9 / §12.6）: 同じ構造のモジュールを持つ論文・同一性候補の
# リンク行・教材行のパイプライン再実行メニュー。
# ---------------------------------------------------------------------------

ADMIN_JS_SRC = (ROOT / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")
VOCAB_JS_SRC = (ROOT / "frontend" / "public" / "js" / "element-vocab.js").read_text(encoding="utf-8")
LIBRARY_MANUAL_SRC = (
    ROOT / "docs" / "manual" / "teacher" / "19-admin-knowledge-library.md"
).read_text(encoding="utf-8")
MATERIALS_MANUAL_SRC = (
    ROOT / "docs" / "manual" / "teacher" / "11-admin-materials.md"
).read_text(encoding="utf-8")


def _admin_function_block(name: str) -> str:
    start = ADMIN_JS_SRC.index("  function " + name + "(")
    end = ADMIN_JS_SRC.find("\n  function ", start + 1)
    return ADMIN_JS_SRC[start: end if end >= 0 else len(ADMIN_JS_SRC)]


class TestRelatedModules:
    """§13.7 / §13.9: 外枠を初めて選んだときに 1 回だけ取得し、タイトルと事実文だけを描く。"""

    def test_related_fetched_once_per_open_and_lazily(self):
        block = _function_block("loadRelatedModules")
        assert '"/theory-modules/related"' in block
        assert "if (state.relatedModulesRequested) return;" in block
        assert "state.relatedModulesRequested = true;" in block
        # 別教材の遅延応答は破棄する。
        assert "state.documentId !== documentId" in block
        # 開くたびに状態を戻す（別教材のキャッシュを持ち越さない）。
        opener = _function_block("open")
        for reset in (
            "state.relatedModules = null;",
            "state.relatedModulesRequested = false;",
            "state.relatedModulesFailed = false;",
        ):
            assert reset in opener, reset
        # モーダルを開いただけでは取らない（外枠モジュールの選択時だけ）。
        assert "loadRelatedModules" not in opener
        select = _function_block("selectModule")
        assert "loadRelatedModules();" in select
        assert '!== "inner"' in select
        assert JS_SRC.count('"/theory-modules/related"') == 1

    def test_related_is_fail_soft(self):
        block = _function_block("loadRelatedModules")
        assert "state.relatedModulesFailed = true;" in block
        html = _function_block("relatedModulesHtml")
        assert "if (state.relatedModulesFailed) return \"\";" in html
        assert "if (!data) return \"\";" in html
        # 失敗の事実文を新設しない（区画ごと出さない）。
        assert "MODULE_ERROR_TEXT" not in html

    def test_related_renders_titles_and_server_facts_only(self):
        html = _function_block("relatedModulesHtml")
        assert "doc && doc.title" in html
        assert "data.facts" in html
        assert "paperFactLines(facts" in html
        assert "data.available === false" in html
        assert "MODULE_RELATED_UNMATCHED_TEXT" in html
        # 区画の説明文と固定文は設計書 §13.7 / §13.9 の逐語。
        assert '"同じ構造のモジュールを持つ論文"' in JS_SRC
        assert (
            '"工程の型と、受け渡す式の形が同じモジュールを持つ論文です（閲覧できる論文だけを示します）。"'
            in JS_SRC
        )
        assert (
            '"このモジュールは保存済みのモジュールと対応が取れないため、同じ構造の論文を示していません。"'
            in JS_SRC
        )
        # 表示は外枠モジュールの詳細にだけ足す。
        assert "moduleDetailHtml(module) + relatedModulesHtml(module)" in _function_block("renderModuleDetail")

    def test_related_draws_no_counts_keys_or_status(self):
        for name in ("relatedModulesHtml", "relatedEntryFor", "loadRelatedModules"):
            block = _function_block(name)
            assert "件" not in block, name
            assert ".length +" not in block, name
            assert "structure_fingerprint" not in block, name
            assert "review_status" not in block, name
            assert "esc(wanted" not in block, name
            assert "esc(entry.module_key" not in block, name
            assert "data.hidden" not in block, name  # hidden はサーバの事実文で語る

    def test_related_section_has_no_ui_anchor(self):
        # 事実の区画（タイトルはリンクにしない v1）なので data-ui-anchor を付けない。
        for name in ("relatedModulesHtml", "relatedEntryFor", "loadRelatedModules"):
            assert "data-ui-anchor" not in _function_block(name), name
            assert "<button" not in _function_block(name), name
            assert "<a " not in _function_block(name), name

    def test_manual_describes_related(self):
        assert "同じ構造のモジュールを持つ論文" in MANUAL_SRC
        assert "この教材の理論モジュールはまだ保存されていないため、同じ構造の論文を照合できません。" in MANUAL_SRC
        assert "理論モジュールの保存" in MANUAL_SRC


class TestIdentityCandidateTheoryModule:
    """§13.9: 同一性候補のリンク行に要素型の訳語と local_expression.name を出す。"""

    def test_element_type_label_registered_in_vocab(self):
        assert 'theory_module: "理論モジュール"' in VOCAB_JS_SRC

    def test_link_row_uses_vocab_and_expression_name(self):
        label = _admin_function_block("_libraryInstanceTypeLabel")
        assert "vocab.elementTypeLabel(type)" in label
        # 未知の型で生の element_type を出さない。
        assert "label !== type" in label
        expression = _admin_function_block("_libraryLocalExpressionText")
        assert "localExpression.name" in expression
        render = _admin_function_block("renderLibraryIdentityCandidates")
        assert "_libraryInstanceTypeLabel(inst.element_type)" in render
        assert "_libraryLocalExpressionText(link.local_expression)" in render
        # オブジェクトをそのまま文字列にしない（旧実装の "[object Object]"）。
        assert "escHtml(link.local_expression)" not in render

    def test_justification_label_structural_match(self):
        assert 'structural_match: "構造の一致"' in ADMIN_JS_SRC

    def test_manual_describes_structural_candidates(self):
        assert "理論モジュール由来の候補（構造の一致）" in LIBRARY_MANUAL_SRC
        assert "工程の型" in LIBRARY_MANUAL_SRC


class TestPipelineMenuStages:
    """§13.5: 教材行「パイプラインを実行 ▼」に保存後の 2 ステージを足す。"""

    def _groups_source(self) -> str:
        start = ADMIN_JS_SRC.index("var materialPipelineStageGroups = [")
        end = ADMIN_JS_SRC.index("var materialPipelineStages =", start)
        return ADMIN_JS_SRC[start:end]

    def test_stages_listed_with_server_labels(self):
        groups = self._groups_source()
        assert '["theory_modules", "理論モジュールの保存"]' in groups
        assert '["identity_candidates", "共通する概念の候補づくり"]' in groups
        # 実行順（theory_modules → identity_candidates）でメニューにも並べる。
        assert groups.index('"theory_modules"') < groups.index('"identity_candidates"')
        assert groups.index('"export_validation"') < groups.index('"theory_modules"')

    def test_labels_match_server_progress_labels(self):
        # import せずソースから読む（FastAPI 依存を UI 静的テストに持ち込まない）。
        pipeline_src = (
            ROOT / "backend" / "api" / "routes" / "lecture_studio" / "pipeline.py"
        ).read_text(encoding="utf-8")
        groups = self._groups_source()
        for stage in ("theory_modules", "identity_candidates"):
            match = re.search(r'"' + stage + r'":\s*"([^"]+)"', pipeline_src)
            assert match, stage
            label = match.group(1)
            assert '["' + stage + '", "' + label + '"]' in groups, stage

    def test_menu_items_carry_no_new_anchor(self):
        # 個別ステージの項目は親メニューのアンカー（materials.row-pipeline-run）の内側で、
        # 項目ごとのアンカーを持たない（既存の流儀）。
        menu = _admin_function_block("materialPipelineMenuHtml")
        assert 'data-ui-anchor="materials.row-pipeline-run"' in menu
        assert "data-ui-anchor=\"materials.row-pipeline-stage" not in menu

    def test_manual_describes_both_stages(self):
        start = MATERIALS_MANUAL_SRC.index("{#pipeline-run}")
        end = MATERIALS_MANUAL_SRC.index("\n### ", start)
        section = MATERIALS_MANUAL_SRC[start:end]
        assert "理論モジュールの保存" in section
        assert "共通する概念の候補づくり" in section


class TestPhase1CacheBusting:
    def test_versions_bumped(self):
        for path, old in (
            ("element-vocab.js", "element-vocab-20260801-2"),
            ("admin.js", "reproduction-wave2-20260919-1"),
            ("admin-graph-review.js", "theory-modules-20260923-1"),
        ):
            match = re.search(r'/js/' + re.escape(path) + r'\?v=([^"]+)"', HTML_SRC)
            assert match, path
            assert match.group(1) != old, path
