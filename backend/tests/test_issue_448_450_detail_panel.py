"""Issue #448 / #450: グラフ詳細パネルとツールチップの回帰チェック。

#448: 実装由来の内部 type 名（`*Node` 等）をユーザーに見せない。役割（legend のロール名）
      を単一のマッピング関数 `lsGraphRoleLabel` で badge / tooltip / fallback に一貫適用する。
#450: 詳細パネルを「説明 → 根拠リンク → 隣接」の三段に再構成。参照は共通の解決ロジック
      （`lsGraphResolveRef`）を介し、解決可能なら遷移リンク、解決不能なら明示する。

frontend に JS テストハーネスが無いため、原稿スタジオ（Lecture Script Studio。Tier 3-17b で
admin.js から admin-lecture-studio.js へ分離済み）の純粋ヘルパを Node で評価して
ドメイン横断の入力に対する挙動を検証する（Node が無い環境では source-assertion に縮退）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- #448: 内部 type 名を見せない / 役割名へマッピング ----------------------------


class TestIssue448RoleNotType:
    def test_role_mapping_helper_exists(self):
        source = _read(ADMIN_LS_JS)
        assert "function lsGraphRoleLabel" in source
        assert "LS_GRAPH_ROLE_LABELS" in source

    def test_tooltip_uses_role_not_internal_type(self):
        source = _read(ADMIN_LS_JS)
        start = source.index("function lsGraphNodeTooltip")
        body = source[start:start + 500]
        assert "lsGraphRoleLabel(node)" in body
        # 旧実装の内部 type 露出を除去したこと。
        assert "component_type_text || node.component_type || node.review_status" not in body

    def test_detail_badge_uses_role_not_internal_type(self):
        source = _read(ADMIN_LS_JS)
        start = source.index("function lsRenderGraphNodeDetail")
        body = source[start:start + 4000]
        assert "ls-graph-detail-badge" in body
        assert "lsGraphRoleLabel(node)" in body
        # badge に内部 type を埋め込む旧実装を除去。
        assert 'node.component_type_text || node.component_type || node.typeName' not in body


# --- #450: 三段構成 + 参照解決 + 遷移 ----------------------------------------------


class TestIssue450DetailPanelStructure:
    def test_three_sections_in_order(self):
        source = _read(ADMIN_LS_JS)
        start = source.index("function lsRenderGraphNodeDetail")
        body = source[start:start + 5000]
        i_expl = body.index("<b>説明</b>")
        i_evid = body.index("<b>根拠リンク</b>")
        i_neigh = body.index("<b>隣接</b>")
        assert i_expl < i_evid < i_neigh, "説明 → 根拠リンク → 隣接 の順で表示されること"

    def test_resolution_and_navigation_helpers_exist(self):
        source = _read(ADMIN_LS_JS)
        for fn in (
            "function lsGraphBuildResolver",
            "function lsGraphResolveRef",
            "function lsGraphRefChipHtml",
            "function lsGraphNavigateToNode",
        ):
            assert fn in source, fn
        # ナビゲーション結線が存在する。
        assert "data-ls-nav-node" in source
        assert "lsGraphNavigateToNode(el.getAttribute" in source

    def test_unresolved_reference_is_explicit(self):
        source = _read(ADMIN_LS_JS)
        assert "ls-graph-ref-unresolved" in source
        assert "未解決" in source

    def test_css_has_reference_styles(self):
        css = _read(STYLES_CSS)
        for cls in (".ls-graph-ref-nav", ".ls-graph-ref-unresolved", ".ls-graph-detail-neighbor-group"):
            assert cls in css, cls


class TestIssue450EdgeDetail:
    """#450 は Node/Edge 両方の詳細パネル。エッジ選択でも三段が出ること。"""

    def test_edge_detail_renderer_exists(self):
        source = _read(ADMIN_LS_JS)
        assert "function lsRenderGraphEdgeDetail" in source

    def test_edge_selection_is_wired(self):
        source = _read(ADMIN_LS_JS)
        # 単一の click ハンドラが node/edge 双方を処理する。
        assert 'network.on("click"' in source
        assert "lsRenderGraphEdgeDetail(edge, graph)" in source
        # edge id -> edge の解決マップを構築している。
        assert "edgeById" in source

    def test_edge_detail_has_three_sections(self):
        source = _read(ADMIN_LS_JS)
        start = source.index("function lsRenderGraphEdgeDetail")
        body = source[start:start + 4000]
        i_expl = body.index("<b>説明</b>")
        i_evid = body.index("<b>根拠リンク</b>")
        i_neigh = body.index("<b>隣接</b>")
        assert i_expl < i_evid < i_neigh
        # 共通の参照解決を介す。
        assert "lsGraphRefRowHtml(resolver" in body
        assert "lsGraphResolveRef(resolver" in body


# --- Node 実行によるドメイン横断の挙動検証 ----------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_role_and_resolution_cross_domain_behaviour(tmp_path):
    """admin.js の純粋ヘルパを抽出し、物理・生物 2 ドメインで挙動を検証する。"""
    harness = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extract(marker){const s=src.indexOf(marker);const e=src.indexOf("\n  }\n",s)+5;return src.slice(s,e);}
function extractVar(marker){const s=src.indexOf(marker);const e=src.indexOf("\n  };\n",s)+6;return src.slice(s,e);}
function escHtml(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
function lsGraphNodeId(n){return n&&(n.component_id||n.id||n.node_id);}
function lsGraphSemanticLabel(n){return n?(n.visual_label||n.label||lsGraphNodeId(n)||""):"";}
function lsRenderKatex(e){return '<span class="katex">'+escHtml(e)+'</span>';}
var lsState={claimsByChunk:{}};
eval(extractVar("var LS_GRAPH_ROLE_LABELS = {"));
eval(extract("function lsGraphNodeGroup(node)"));
eval(extract("function lsGraphRoleLabel(node)"));
eval(extract("function lsGraphBuildResolver(graph)"));
eval(extract("function lsGraphSnippet(text, max)"));
eval(extract("function lsGraphResolveRef(resolver, kind, id)"));
eval(extract("function lsGraphRefChipHtml(ref)"));
const ROLES=Object.keys(LS_GRAPH_ROLE_LABELS).map(k=>LS_GRAPH_ROLE_LABELS[k]);
const phys={component_id:"theory_op_1",component_type:"TheoryOperationNode",label:"Incompleteness conclusion"};
const bio={component_id:"eq_op_2",component_type:"EquationOperationNode",label:"Construct growth assumption"};
const out={};
out.roles=[phys,bio].map(n=>lsGraphRoleLabel(n));
out.roleIsLegend=out.roles.every(r=>ROLES.indexOf(r)>=0);
out.roleNoType=out.roles.every(r=>!/Node/.test(r));
lsState.claimsByChunk={c1:[
  {claim_id:"c_epr",text:"The wavefunction description is incomplete."},
  {claim_id:"c_bio",text:"Gene X regulates growth.",equation:{equation_id:"eq_growth",label:"(3.1)",latex:"\\frac{dN}{dt}=rN"}}]};
const resolver=lsGraphBuildResolver({nodes:[phys,bio],edges:[]});
out.compNav=(r=>r.resolved&&r.navNodeId==="theory_op_1")(lsGraphResolveRef(resolver,"component","theory_op_1"));
out.compMissing=!lsGraphResolveRef(resolver,"component","ghost").resolved;
out.claimResolved=lsGraphResolveRef(resolver,"claim","c_epr").resolved;
out.eqLatex=/dN/.test(lsGraphResolveRef(resolver,"equation","eq_growth").latex);
out.evUnresolved=!lsGraphResolveRef(resolver,"evidence","ev_x").resolved;
out.navChip=/data-ls-nav-node="theory_op_1"/.test(lsGraphRefChipHtml(lsGraphResolveRef(resolver,"component","theory_op_1")));
out.unresolvedChip=/未解決/.test(lsGraphRefChipHtml(lsGraphResolveRef(resolver,"evidence","ev_x")));
process.stdout.write(JSON.stringify(out));
"""
    script = tmp_path / "h.js"
    script.write_text(harness, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(script), str(ADMIN_LS_JS)], capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    # #448
    assert out["roleIsLegend"], out["roles"]
    assert out["roleNoType"], out["roles"]
    # #450
    assert out["compNav"], "component ref must resolve and be navigable"
    assert out["compMissing"], "missing component ref must be unresolved"
    assert out["claimResolved"], "physics claim must resolve"
    assert out["eqLatex"], "biology equation must resolve with latex"
    assert out["evUnresolved"], "unknown evidence must be unresolved"
    assert out["navChip"], "resolved component chip must carry navigation attr"
    assert out["unresolvedChip"], "unresolved chip must show 未解決"


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_edge_detail_three_tier_cross_domain(tmp_path):
    """エッジ選択時も説明→根拠リンク→隣接の三段が出ることを 2 ドメインで検証する。"""
    harness = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extractFn(name){
  const s = src.indexOf("function " + name);
  if (s < 0) throw new Error("missing " + name);
  let depth = 0, started = false;
  for (let j = src.indexOf("{", s); j < src.length; j++){
    const c = src[j];
    if (c === "{"){ depth++; started = true; }
    else if (c === "}"){ depth--; if (started && depth === 0) return src.slice(s, j + 1); }
  }
  throw new Error("unbalanced " + name);
}
function escHtml(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
function lsRenderKatex(e){return '<span class="katex">'+escHtml(e)+'</span>';}
function lsGraphNodeId(n){return n&&(n.component_id||n.id||n.node_id);}
function lsGraphSemanticLabel(n){return n?(n.visual_label||n.label||lsGraphNodeId(n)||""):"";}
var lsState={claimsByChunk:{}};
var lastDetail={innerHTML:"",querySelectorAll:function(){return [];}};
global.document={getElementById:function(){return lastDetail;}};
function lsGraphNavigateToNode(){}
var names=["lsGraphEdgeLabel","lsGraphSourceBackingLabel","lsGraphReviewReasonLabel","lsGraphBuildResolver","lsGraphSnippet","lsGraphResolveRef","lsGraphRefChipHtml","lsGraphRefRowHtml","lsGraphBindDetailNav","lsRenderGraphEdgeDetail"];
eval(names.map(extractFn).join("\n\n"));
const out={};
// Domain 1 (physics): resolvable equation + claim, unresolvable evidence
lsState.claimsByChunk={c1:[{claim_id:"c_epr",text:"Wavefunction is incomplete.",equation:{equation_id:"eq_2_7",label:"(2.7)",latex:"\\psi"}}]};
const pg={nodes:[{component_id:"A",label:"Assumption"},{component_id:"B",label:"Conclusion"}],edges:[]};
const pe={edge_id:"e1",source_component_id:"A",target_component_id:"B",relation:"DERIVES",edge_type:"derives",
  evidence:{evidence_equation_ids:["eq_2_7"],evidence_claim_ids:["c_epr"],source_evidence_ids:["ev_x"],reason:"From locality."}};
lsRenderGraphEdgeDetail(pe,pg); const h1=lastDetail.innerHTML;
out.pOrder=h1.indexOf("<b>説明</b>")>=0 && h1.indexOf("<b>説明</b>")<h1.indexOf("<b>根拠リンク</b>") && h1.indexOf("<b>根拠リンク</b>")<h1.indexOf("<b>隣接</b>");
out.pLatex=h1.indexOf("katex")>=0;
out.pUnresolved=h1.indexOf("未解決")>=0;
out.pNav=(h1.match(/data-ls-nav-node/g)||[]).length>=2;
out.pNoType=!/TheoryOperationNode|EquationOperationNode/.test(h1);
// Domain 2 (biology): no evidence -> still three tiers, empty made explicit
const bg={nodes:[{component_id:"G",label:"Gene model"},{component_id:"R",label:"Growth result"}],edges:[]};
const be={edge_id:"e2",source_component_id:"G",target_component_id:"R",relation:"CONSTRAINS",evidence:{}};
lsRenderGraphEdgeDetail(be,bg); const h2=lastDetail.innerHTML;
out.bThree=h2.indexOf("<b>説明</b>")>=0 && h2.indexOf("<b>根拠リンク</b>")>=0 && h2.indexOf("<b>隣接</b>")>=0;
out.bEmpty=h2.indexOf("根拠リンクはありません")>=0;
out.bNav=(h2.match(/data-ls-nav-node/g)||[]).length===2;
process.stdout.write(JSON.stringify(out));
"""
    script = tmp_path / "edge.js"
    script.write_text(harness, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(script), str(ADMIN_LS_JS)], capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["pOrder"], "physics edge: 説明→根拠リンク→隣接 in order"
    assert out["pLatex"], "physics edge: resolvable equation rendered as latex"
    assert out["pUnresolved"], "physics edge: unresolvable evidence shown explicitly"
    assert out["pNav"], "physics edge: both endpoints navigable"
    assert out["pNoType"], "physics edge: no internal *Node type in body"
    assert out["bThree"], "biology edge: three tiers present"
    assert out["bEmpty"], "biology edge: empty evidence shown explicitly"
    assert out["bNav"], "biology edge: both endpoints navigable"
