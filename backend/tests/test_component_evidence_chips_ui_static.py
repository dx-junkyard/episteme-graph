"""component 根拠カード再設計（設計書 docs/features/component_evidence_redesign.md）の
学習UI（app.js）実装に対する静的ガードレール + 挙動検証。

対象:
- frontend/public/js/app.js: component/claim をブロックカードではなくインライン
  チップに解決する renderMaterialEvidenceChip、kind の日本語ラベル
  MATERIAL_EVIDENCE_KIND_LABELS、チップのクリック配線
  initMaterialEvidenceChipDelegation、文脈ポップオーバー
  openEvidenceChipPopover、Phase 2 文脈API 呼び出し
  fetchComponentContextAndRender、Phase 3 の 1-hop 近傍グラフ
  buildEvidenceContextGraphSvg。
- frontend/public/css/styles.css: .ls-material-evidence-chip 系クラス。

バックエンドの Phase 2 API（GET .../components/{id}/context）は並行実装中のため、
ここでは JS 側の静的契約と、Node が使える環境での renderMaterialChunk の実際の
描画結果を検証する（node 依存テストは他ファイルと同じ方針で skip）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# app.js: 静的ガードレール
# ---------------------------------------------------------------------------


class TestKindLabelsAndMetaRemoval:
    def test_kind_labels_map_defined_in_japanese(self):
        src = _read(APP_JS)
        assert "var MATERIAL_EVIDENCE_KIND_LABELS = {" in src
        start = src.index("var MATERIAL_EVIDENCE_KIND_LABELS = {")
        end = src.index("};", start)
        block = src[start:end]
        assert "論理要素" in block  # component
        assert "主張" in block      # claim
        assert "出典" in block      # source
        assert "図" in block        # figure

    def test_figure_and_source_cards_use_kind_label_map(self):
        src = _read(APP_JS)
        assert "MATERIAL_EVIDENCE_KIND_LABELS.figure" in src
        assert "MATERIAL_EVIDENCE_KIND_LABELS[evidenceItem.kind]" in src

    def test_learner_ui_never_prints_internal_meta(self):
        """role/confidence（パイプライン照合の来歴メタデータ）は学習UIに一切出さない。"""
        src = _read(APP_JS)
        assert "ls-material-embed-meta" not in src

    def test_render_material_chunk_no_longer_builds_meta_line(self):
        src = _read(APP_JS)
        start = src.index("function renderMaterialChunk(chunk) {")
        end = src.index("\n  function renderMaterialMissingEmbed", start)
        body = src[start:end]
        assert "evidenceItem.role" not in body
        assert "evidenceItem.confidence" not in body


class TestComponentClaimChipRendering:
    def test_component_claim_branch_renders_chip_not_card(self):
        src = _read(APP_JS)
        assert 'if (embed.kind === "component" || embed.kind === "claim") {' in src
        start = src.index('if (embed.kind === "component" || embed.kind === "claim") {')
        end = src.index("return renderMaterialMissingEmbed(embed, embedId);\n      }", start)
        block = src[start:end]
        assert "renderMaterialEvidenceChip(" in block

    def test_chip_renderer_defined_with_expected_markup(self):
        src = _read(APP_JS)
        assert "function renderMaterialEvidenceChip(kind, embedId, evidenceItem, resolver)" in src
        start = src.index("function renderMaterialEvidenceChip(kind, embedId, evidenceItem, resolver)")
        end = src.index("\n  }", start)
        block = src[start:end]
        assert "ls-material-evidence-chip" in block
        assert "data-evidence-ref" in block
        assert "data-evidence-kind" in block
        assert "ls-material-evidence-chip-icon" in block
        assert "ls-material-evidence-chip-label" in block

    def test_chip_registers_item_and_resolver_for_later_popover_lookup(self):
        src = _read(APP_JS)
        assert "function registerMaterialEvidenceChipEntry(ref, item, resolver)" in src
        assert "var materialEvidenceChipItems = {};" in src

    def test_source_and_equation_keep_block_card_rendering(self):
        """source/equation はそれ自体が内容を持つためブロックカードのまま
        （component/claim だけがチップに格下げされる）。source の解決分岐は
        component/claim 分岐より後ろにあり、evidenceItem を
        ls-material-evidence-card として描画し続ける。"""
        src = _read(APP_JS)
        assert 'ls-material-formula-only" data-evidence-ref="equation:' in src
        idx_component_branch = src.index('if (embed.kind === "component" || embed.kind === "claim") {')
        idx_source_comment = src.index("// source: 原文抜粋", idx_component_branch)
        idx_source_card = src.index('ls-material-embed ls-material-evidence-card" data-evidence-ref="', idx_source_comment)
        assert idx_component_branch < idx_source_comment < idx_source_card


class TestClickDelegation:
    def test_delegation_registered_once_on_document(self):
        src = _read(APP_JS)
        assert "function initMaterialEvidenceChipDelegation()" in src
        start = src.index("function initMaterialEvidenceChipDelegation()")
        end = src.index("\n  }", start)
        block = src[start:end]
        assert "_materialEvidenceChipDelegationWired" in block
        assert 'document.addEventListener("click"' in block
        assert "closest(\".ls-material-evidence-chip\")" in block
        assert "openEvidenceChipPopover(" in block

    def test_delegation_wired_into_init_app(self):
        src = _read(APP_JS)
        start = src.index("async function initApp()")
        end = src.index("\n  }\n", start)
        block = src[start:end]
        assert "initMaterialEvidenceChipDelegation();" in block


class TestPopoverAndContextPanel:
    def test_popover_opener_defined(self):
        src = _read(APP_JS)
        assert "function openEvidenceChipPopover(anchor, entry)" in src

    def test_popover_shows_position_supports_and_more_button_for_component(self):
        src = _read(APP_JS)
        assert "function renderComponentChipPopoverBody(item, resolver)" in src
        start = src.index("function renderComponentChipPopoverBody(item, resolver)")
        end = src.index("\n  }", start)
        block = src[start:end]
        assert "renderSupportsSummaryLine" in block
        assert "renderSupportsTextSection" in block
        assert "renderSupportsEquationSection" in block
        assert "renderSupportsDependencySection" in block
        assert "renderSupportsClaimSection" in block
        assert "evidenceChipPopoverMoreButton" in block

    def test_claim_popover_has_no_more_button(self):
        src = _read(APP_JS)
        start = src.index("function renderClaimChipPopoverBody(item)")
        end = src.index("\n  }", start)
        block = src[start:end]
        assert "evidenceChipPopoverMoreButton" not in block

    def test_context_api_path_matches_learning_courses_components_context(self):
        src = _read(APP_JS)
        assert '"/learning/courses/" + state.courseId +' in src
        assert '"/components/" + encodeURIComponent(componentId) + "/context"' in src

    def test_context_fetch_failure_shows_fail_closed_message(self):
        src = _read(APP_JS)
        assert "async function fetchComponentContextAndRender(pop, body, componentId, resolver)" in src
        start = src.index("async function fetchComponentContextAndRender(pop, body, componentId, resolver)")
        end = src.index("\n  }", start)
        block = src[start:end]
        assert block.count("詳細情報を取得できませんでした。") >= 2  # !res.ok と catch の両方

    def test_shared_part_tab_only_rendered_when_present(self):
        src = _read(APP_JS)
        start = src.index("function renderComponentContextPanel(pop, body, data, resolver)")
        end = src.index("\n  }\n", start)
        block = src[start:end]
        assert "sharedPart ?" in block
        assert "共通部品として" in block
        assert "この論文では" in block

    def test_standardization_status_labels_defined(self):
        src = _read(APP_JS)
        assert "var MATERIAL_LIBRARY_STATUS_LABELS = {" in src
        start = src.index("var MATERIAL_LIBRARY_STATUS_LABELS = {")
        end = src.index("};", start)
        block = src[start:end]
        for label in ("標準", "分野標準", "共通化進行中", "新規", "未判定"):
            assert label in block


class TestGraphView:
    def test_graph_svg_builder_defined(self):
        src = _read(APP_JS)
        assert "function buildEvidenceContextGraphSvg(graph, onNodeClick)" in src

    def test_graph_uses_svg_namespace_and_rect_nodes(self):
        src = _read(APP_JS)
        assert 'var EVIDENCE_GRAPH_SVG_NS = "http://www.w3.org/2000/svg";' in src
        assert 'function evidenceGraphSvgEl(tag, attrs)' in src
        assert 'document.createElementNS(EVIDENCE_GRAPH_SVG_NS, tag)' in src
        start = src.index("function buildEvidenceContextGraphSvg(graph, onNodeClick)")
        end = src.index("\n  }\n", start)
        block = src[start:end]
        assert "evidenceGraphSvgEl(" in block
        assert '"rect"' in block
        assert '"line"' in block

    def test_graph_toggle_only_rendered_when_graph_present(self):
        src = _read(APP_JS)
        start = src.index("function renderComponentContextPanel(pop, body, data, resolver)")
        end = src.index("\n  }\n", start)
        block = src[start:end]
        assert "graph ?" in block
        assert "グラフで見る" in block

    def test_only_navigable_theory_component_nodes_are_clickable(self):
        src = _read(APP_JS)
        start = src.index("function navigateEvidenceContextGraph(pop, body, node)")
        end = src.index("\n  }", start)
        block = src[start:end]
        assert 'node.element_type !== "theory_component"' in block
        assert "!node.navigable" in block

        start2 = src.index("function buildEvidenceContextGraphSvg(graph, onNodeClick)")
        end2 = src.index("\n  }\n", start2)
        block2 = src[start2:end2]
        assert 'node.element_type === "theory_component"' in block2
        assert "node.navigable" in block2


# ---------------------------------------------------------------------------
# styles.css
# ---------------------------------------------------------------------------


class TestStylesCssChipClasses:
    def test_chip_classes_defined(self):
        src = _read(STYLES_CSS)
        for selector in (
            ".ls-material-evidence-chip {",
            ".ls-material-evidence-chip-icon {",
            ".ls-material-evidence-chip-label {",
        ):
            assert selector in src, f"{selector} が styles.css に見つかりません"

    def test_context_tabs_and_graph_classes_defined(self):
        src = _read(STYLES_CSS)
        for selector in (
            ".evidence-chip-context-tabs {",
            ".evidence-chip-context-tab {",
            ".evidence-chip-context-graph-svg {",
            ".evidence-chip-context-graph-node-rect {",
        ):
            assert selector in src, f"{selector} が styles.css に見つかりません"

    def test_ls_material_embed_classes_untouched(self):
        """admin 側が使う既存 .ls-material-embed 系クラス定義自体は変更しない。"""
        src = _read(STYLES_CSS)
        assert ".ls-material-embed {" in src
        assert ".ls-material-evidence-card {" in src


# ---------------------------------------------------------------------------
# 実行時の振る舞い（Node が使える環境のみ）
# ---------------------------------------------------------------------------

_EXTRACT = r"""
function extractFrom(src, name){
  const s = src.indexOf("function " + name + "(");
  if (s<0) throw new Error("missing "+name);
  let d=0,st=false;
  for(let j=src.indexOf("{",s);j<src.length;j++){const c=src[j];if(c==="{"){d++;st=true;}else if(c==="}"){d--;if(st&&d===0)return src.slice(s,j+1);}}
  throw new Error("unbalanced "+name);
}
function extractMany(src, names){ return names.map(function(n){ return extractFrom(src,n); }).join("\n"); }
// function ではない "var NAME = { ... };" 形式のモジュール定数を抽出する。
function extractVar(src, name){
  const s = src.indexOf("var " + name + " = {");
  if (s<0) throw new Error("missing var "+name);
  let d=0,st=false;
  for(let j=src.indexOf("{",s);j<src.length;j++){const c=src[j];if(c==="{"){d++;st=true;}else if(c==="}"){d--;if(st&&d===0)return src.slice(s,j+1)+";";}}
  throw new Error("unbalanced var "+name);
}
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_chip_rendering_behaviour_node(tmp_path):
    """renderMaterialChunk を実評価し、component/claim がチップ化されメタデータを
    出さないこと、equation は従来どおりブロックカードのままであることを検証する。"""
    script = _EXTRACT + r"""
const fs = require("fs");
const app = fs.readFileSync(process.argv[2], "utf8");
var window = { katex: null };
function escHtml(s){ return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
var materialEvidenceChipItems = {};
eval(extractVar(app, "MATERIAL_EVIDENCE_KIND_LABELS"));
eval(extractMany(app, ["normalizeMaterialEvidenceId","normalizeMaterialLineBreaks","normalizeKatexFormula",
  "renderMaterialKatex","renderMaterialEquationBody","renderMaterialMissingEmbed",
  "shortMaterialEvidenceSummary","renderMaterialFigureCard","registerMaterialEvidenceChipEntry",
  "renderMaterialEvidenceChip","renderMaterialChunk"]));

const chunk = {
  text: "本文 ![[component:comp_1]] 続き 数式 ![[equation:eq_1]] 出典 ![[source:ev_1]]",
  formulas: [],
  figures: [],
  evidence_items: [
    {kind:"component", id:"comp_1", title:"共鳴条件", summary:"詳しい要約文", role:"support", confidence:"high"},
    {kind:"equation", id:"eq_1", title:"式1", latex:"a=b", role:"equation"},
    {kind:"source", id:"ev_1", title:"原文引用", summary:"引用テキスト", role:"source_quote"}
  ]
};
const html = renderMaterialChunk(chunk);
const out = {
  hasChip: html.indexOf('ls-material-evidence-chip"') >= 0,
  chipHasLabel: html.indexOf("共鳴条件") >= 0,
  noMeta: html.indexOf("ls-material-embed-meta") < 0,
  noRoleConfidenceLeak: html.indexOf("support") < 0 && html.indexOf("high") < 0,
  equationStaysBlockCard: /ls-material-formula-only/.test(html),
  // source は引き続きブロックカードで、kind バッジが日本語化されている（"出典"）。
  sourceKindLabelIsJapanese: /ls-material-embed-kind">出典</.test(html),
  registryPopulated: Object.keys(materialEvidenceChipItems).length === 1
};
process.stdout.write(JSON.stringify(out));
"""
    harness = tmp_path / "h.js"
    harness.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(APP_JS)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["hasChip"], "component embed must render as a .ls-material-evidence-chip"
    assert out["chipHasLabel"]
    assert out["noMeta"], "no ls-material-embed-meta output anywhere"
    assert out["sourceKindLabelIsJapanese"], "source card kind badge must use the Japanese label map"
    assert out["noRoleConfidenceLeak"], "role/confidence must not leak into learner-facing HTML"
    assert out["equationStaysBlockCard"], "equation embeds must remain a block card, not a chip"
    assert out["registryPopulated"], "the chip must register its item for the delegated click handler"
