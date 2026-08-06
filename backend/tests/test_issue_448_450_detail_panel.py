"""Issue #448 / #450: グラフ詳細パネルとツールチップの回帰チェック。

#448: 実装由来の内部 type 名（`*Node` 等）をユーザーに見せない。役割（legend のロール名）
      を単一のマッピング関数 `lsGraphRoleLabel` で badge / tooltip / fallback に一貫適用する。
#450: 詳細パネルは「そのパーツが何で / 何に支えられ / 何につながるか」を構造化し、参照は
      共通の解決ロジック（`lsGraphResolveRef`）を介して辿れること。

**2026-08-01（admin_ux_issues_2026-08-01.md §3.3 Phase 2）**: 詳細ペインは独自 HTML 組み立て
（旧 `lsGraphRefRowHtml` / `lsGraphNeighborListHtml` / `lsGraphRefChipHtml`）を捨て、統一
パーツカード（`element-card.js`）へ移行した。三段（説明 → 根拠リンク → 隣接）はカードの
本文 → 上位レーン → 下位レーンに対応し、根拠リンクは下位レーンの ITEM になる。#448 / #450 の
受け入れ条件そのものは維持し、検証対象をカード経路へ差し替えている。

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
ELEMENT_CARD_JS = ROOT / "frontend" / "public" / "js" / "element-card.js"
ELEMENT_VOCAB_JS = ROOT / "frontend" / "public" / "js" / "element-vocab.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fn_body(source: str, name: str) -> str:
    """`function <name>` から次の 2 スペースインデント関数定義までを返す。"""
    start = source.index("function " + name)
    nxt = source.find("\n  function ", start + 1)
    return source[start:nxt] if nxt > 0 else source[start:]


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
        """役割は meta 行のバッジ（段階ラベル）として単一のマッピング関数から出す。"""
        source = _read(ADMIN_LS_JS)
        body = _fn_body(source, "lsGraphNodeCardOpts")
        assert "metaBadges" in body
        assert "lsGraphRoleLabel(node)" in body
        # badge に内部 type を埋め込む旧実装を除去。
        assert "node.component_type_text || node.component_type || node.typeName" not in body
        assert "component_type" not in body, "内部 type を meta バッジに出さないこと"

    def test_internal_type_stays_in_system_details(self):
        """情報は落とさない: 内部 type / レイヤー / ステータスは折りたたみに残す。"""
        source = _read(ADMIN_LS_JS)
        body = _fn_body(source, "lsGraphNodeSystemInfoHtml")
        assert "ls-graph-detail-system" in body
        assert "内部type" in body
        assert "node.component_type || node.type" in body


# --- #450: 構造化 + 参照解決 + 遷移 ------------------------------------------------


class TestIssue450DetailPanelStructure:
    def test_node_detail_mounts_the_unified_card(self):
        source = _read(ADMIN_LS_JS)
        body = _fn_body(source, "lsRenderGraphNodeDetail")
        assert "lsGraphNodeToCardDto(node, graph)" in body
        assert "lsGraphMountCard(" in body
        mount = _fn_body(source, "lsGraphMountCard")
        assert "window.ElementCard.mount(host, dto, opts)" in mount
        # ElementCard 未読み込みでも事実文に縮退する（fail-soft）。
        assert "if (!window.ElementCard)" in mount

    def test_card_is_the_editable_variant(self):
        source = _read(ADMIN_LS_JS)
        for name in ("lsGraphNodeCardOpts", "lsRenderGraphEdgeDetail"):
            body = _fn_body(source, name)
            assert "VARIANT_EDITABLE" in body, name

    def test_three_tiers_map_to_body_then_lanes(self):
        """説明 → 根拠リンク → 隣接 の三段は「本文 → 上位/下位レーン」に対応する。
        根拠リンクは下位レーン（この要素を支えるもの）の ITEM になる。"""
        source = _read(ADMIN_LS_JS)
        body = _fn_body(source, "lsGraphNodeToCardDto")
        # 出ていくエッジ = 上位 / 入ってくるエッジ = 下位。
        assert "upper.push(lsGraphRefItem(resolver, \"component\", target" in body
        assert "lower.push(lsGraphRefItem(resolver, \"component\", source" in body
        # 根拠リンク（式・主張・根拠箇所・導出）は下位レーンへ。
        for kind in ("equation", "claim", "evidence", "derivation"):
            assert 'lsGraphPushRefItems(lower, resolver, "' + kind + '"' in body, kind
        # カード側のレンダ順（本文 → レーン）が保たれていること。
        card = _read(ELEMENT_CARD_JS)
        render = card[card.index("function render(dto, opts)"):]
        assert render.index("bodyHtml(focus, ctx)") < render.index("lanesHtml(dto, ctx)")

    def test_resolution_and_navigation_helpers_exist(self):
        source = _read(ADMIN_LS_JS)
        for fn in (
            "function lsGraphBuildResolver",
            "function lsGraphResolveRef",
            "function lsGraphRefItem",
            "function lsGraphNavigateToNode",
        ):
            assert fn in source, fn
        # 近傍 ITEM のクリック（カードの onCenter）がグラフ内のノード選択へつながる。
        for name in ("lsGraphNodeCardOpts", "lsRenderGraphEdgeDetail"):
            body = _fn_body(source, name)
            assert "onCenter: lsGraphCenterOnItem" in body, name
        center = _fn_body(source, "lsGraphCenterOnItem")
        assert "lsGraphNavigateToNode(String(targetId))" in center
        # グラフ内に無い要素（サーバ文脈の DB UUID 等）は W層「深く検討」で中心に据える。
        assert "window.Deliberation.openElement(elementType" in center

    def test_unresolved_reference_is_explicit(self):
        """解決できない参照は ID のまま出し、事実文で正直に告知する（捏造しない）。"""
        source = _read(ADMIN_LS_JS)
        item = _fn_body(source, "lsGraphRefItem")
        assert "ref.resolved ? ref.label : id" in item
        assert "unresolved.push(id)" in item
        note = _fn_body(source, "lsGraphUnresolvedNote")
        assert "参照先の名称を解決できませんでした" in note

    def test_old_bespoke_markup_helpers_are_gone(self):
        source = _read(ADMIN_LS_JS)
        for fn in (
            "function lsGraphRefChipHtml",
            "function lsGraphRefRowHtml",
            "function lsGraphNeighborListHtml",
            "function lsGraphBindDetailNav",
        ):
            assert fn not in source, fn + " は撤去済みであること（カードへ一本化）"

    def test_css_has_card_styles(self):
        css = _read(STYLES_CSS)
        for cls in (".element-card-item", ".element-card-lane", ".ls-graph-detail-card"):
            assert cls in css, cls


class TestIssue450EdgeDetail:
    """#450 は Node/Edge 両方の詳細パネル。エッジ選択でも同じカードが出ること。"""

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

    def test_edge_detail_has_endpoints_and_evidence(self):
        source = _read(ADMIN_LS_JS)
        body = _fn_body(source, "lsGraphEdgeToCardDto")
        # 終点＝上位（この関係が支えるもの）/ 始点＝下位（この関係を支えるもの）。
        assert '"終点"' in body
        assert '"始点"' in body
        # 根拠は共通の参照解決を介す。
        assert "lsGraphResolveRef" in _fn_body(source, "lsGraphRefItem")
        for kind in ("equation", "claim", "derivation", "evidence"):
            assert 'lsGraphPushRefItems(lower, resolver, "' + kind + '"' in body, kind

    def test_edge_does_not_fetch_server_context(self):
        """エッジは W層の解決対象（refs.py の element_type）ではないので文脈を引かない。"""
        source = _read(ADMIN_LS_JS)
        body = _fn_body(source, "lsRenderGraphEdgeDetail")
        assert "apiFetch" not in body
        assert "lsGraphLoadNodeContext" not in body
        # 「深く検討」も出さない（対象として同定できないため）。
        assert "onDeliberate" not in body


# --- Node 実行によるドメイン横断の挙動検証 ----------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_role_and_resolution_cross_domain_behaviour(tmp_path):
    """admin.js の純粋ヘルパを抽出し、物理・生物 2 ドメインで挙動を検証する。"""
    harness = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extract(marker){const s=src.indexOf(marker);const e=src.indexOf("\n  }\n",s)+5;return src.slice(s,e);}
function extractVar(marker){const s=src.indexOf(marker);const e=src.indexOf("\n  };\n",s)+6;return src.slice(s,e);}
function extractArrayVar(marker){const s=src.indexOf(marker);const e=src.indexOf("];",s)+2;return src.slice(s,e);}
function escHtml(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
function lsGraphNodeId(n){return n&&(n.component_id||n.id||n.node_id);}
function lsGraphSemanticLabel(n){return n?(n.visual_label||n.label||lsGraphNodeId(n)||""):"";}
function lsRenderKatex(e){return '<span class="katex">'+escHtml(e)+'</span>';}
function lsGraphActiveDocumentId(){return "doc-1";}
var lsState={claimsByChunk:{}};
eval(extractVar("var LS_GRAPH_ROLE_LABELS = {"));
eval(extractVar("var LS_GRAPH_REF_ELEMENT_TYPES = {"));
eval(extractArrayVar("var LS_GRAPH_DELIBERABLE_ELEMENT_TYPES = ["));
eval(extractArrayVar("var LS_GRAPH_DOCUMENT_SCOPED_ELEMENT_TYPES = ["));
eval(extract("function lsGraphNodeGroup(node)"));
eval(extract("function lsGraphRoleLabel(node)"));
eval(extract("function lsGraphBuildResolver(graph)"));
eval(extract("function lsGraphSnippet(text, max)"));
eval(extract("function lsGraphResolveRef(resolver, kind, id)"));
eval(extract("function lsGraphUniqueIds(ids)"));
eval(extract("function lsGraphRefItem(resolver, kind, id, relationLabel, unresolved)"));
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
// ITEM 化: 解決できた component は navigable、未解決は ID のまま navigable=false。
const unresolved=[];
const navItem=lsGraphRefItem(resolver,"component","theory_op_1","依存",unresolved);
const ghostItem=lsGraphRefItem(resolver,"component","ghost","依存",unresolved);
const claimItem=lsGraphRefItem(resolver,"claim","c_bio","根拠",unresolved);
out.navItem=navItem.navigable===true&&navItem.element_type==="theory_component";
out.ghostItem=ghostItem.navigable===false&&ghostItem.label==="ghost";
out.claimItemType=claimItem.element_type==="theory_claim";
out.claimItemLabel=/Gene X/.test(claimItem.label);
out.unresolvedTracked=unresolved.length===1&&unresolved[0]==="ghost";
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
    assert out["navItem"], "resolved component ITEM must be navigable"
    assert out["ghostItem"], "unresolved ITEM keeps the raw id and is not navigable"
    assert out["claimItemType"], "claim ITEM must carry the backend element_type"
    assert out["claimItemLabel"], "claim ITEM must use the resolved snippet"
    assert out["unresolvedTracked"], "unresolved ids must be collected for the fact sentence"


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_edge_detail_card_cross_domain(tmp_path):
    """エッジ選択時も統一パーツカードで本文・レーン・遷移が出ることを 2 ドメインで検証する。"""
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
function extractVar(marker){const s=src.indexOf(marker);const e=src.indexOf("\n  };\n",s)+6;return src.slice(s,e);}
function extractArrayVar(marker){const s=src.indexOf(marker);const e=src.indexOf("];",s)+2;return src.slice(s,e);}
// 実物の統一パーツカード + 語彙正本を読み込む（描き方の二重実装が無いことの担保）。
global.window = {};
eval(fs.readFileSync(process.argv[3], "utf8")); // element-vocab.js
eval(fs.readFileSync(process.argv[4], "utf8")); // element-card.js
function escHtml(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
function lsRenderKatex(e){return '<span class="katex">'+escHtml(e)+'</span>';}
function lsGraphNodeId(n){return n&&(n.component_id||n.id||n.node_id);}
function lsGraphSemanticLabel(n){return n?(n.visual_label||n.label||lsGraphNodeId(n)||""):"";}
function lsElementTypeLabel(t){return window.ElementVocab.elementTypeLabel(t);}
function lsGraphNavigateToNode(){return false;}
function lsGraphCenterOnItem(){}
function lsGraphActiveDocumentId(){return "doc-1";}
var lsState={claimsByChunk:{}};
var lsGraphSelectedNodeId="";
var LS_GRAPH_CARD_HOST_ID="ls-graph-detail-card";
function fakeEl(){return {innerHTML:"",classList:{contains:function(){return false;}},
  querySelector:function(){return null;},querySelectorAll:function(){return [];}};}
var detailEl=fakeEl(), hostEl=fakeEl();
global.document={getElementById:function(id){return id===LS_GRAPH_CARD_HOST_ID?hostEl:detailEl;}};
eval(extractVar("var LS_GRAPH_REF_ELEMENT_TYPES = {"));
eval(extractArrayVar("var LS_GRAPH_DELIBERABLE_ELEMENT_TYPES = ["));
eval(extractArrayVar("var LS_GRAPH_DOCUMENT_SCOPED_ELEMENT_TYPES = ["));
var names=["lsGraphEdgeLabel","lsGraphSourceBackingLabel","lsGraphReviewReasonLabel",
  "lsGraphBuildResolver","lsGraphSnippet","lsGraphResolveRef","lsGraphUniqueIds",
  "lsGraphRefItem","lsGraphPushRefItems","lsGraphUnresolvedNote","lsGraphEdgeToCardDto",
  "lsGraphBackingBadge","lsGraphMountCard","lsGraphEdgeSystemInfoHtml","lsRenderGraphEdgeDetail"];
eval(names.map(extractFn).join("\n\n"));
const out={};
// Domain 1 (physics): resolvable equation + claim, unresolvable evidence
lsState.claimsByChunk={c1:[{claim_id:"c_epr",text:"Wavefunction is incomplete.",equation:{equation_id:"eq_2_7",label:"(2.7)",latex:"\\psi"}}]};
const pg={nodes:[{component_id:"A",label:"Assumption"},{component_id:"B",label:"Conclusion"}],edges:[]};
const pe={edge_id:"e1",source_component_id:"A",target_component_id:"B",relation:"DERIVES",edge_type:"derives",
  source_backing_status:"partially_source_backed",
  evidence:{evidence_equation_ids:["eq_2_7"],evidence_claim_ids:["c_epr"],source_evidence_ids:["ev_x"],reason:"From locality."}};
lsRenderGraphEdgeDetail(pe,pg);
const h1=hostEl.innerHTML, s1=detailEl.innerHTML;
out.pIsCard=h1.indexOf('class="element-card')>=0;
out.pHeaderNote=h1.indexOf("選択中の関係")>=0;
out.pTitle=h1.indexOf("導出")>=0;
out.pBody=h1.indexOf("From locality.")>=0;
// レーン本体の数（見出し要素 element-card-lane-title を数えないよう容器で数える）。
out.pLanes=(h1.match(/element-card-lane element-card-lane-/g)||[]).length===2;
out.pNav=(h1.match(/data-element-card-item=/g)||[]).length>=2;
out.pVocab=h1.indexOf("数式")>=0&&h1.indexOf("主張")>=0&&h1.indexOf("根拠箇所")>=0;
out.pUnresolvedNote=h1.indexOf("ev_x")>=0;
out.pBackingBadge=h1.indexOf("部分的に出典あり")>=0;
out.pNoType=!/TheoryOperationNode|EquationOperationNode/.test(h1);
out.pSystemInfo=s1.indexOf("内部relation")>=0;
out.pNoDeliberate=h1.indexOf("data-element-card-deliberate")<0;
// Domain 2 (biology): no evidence -> lanes still rendered with explicit facts
const bg={nodes:[{component_id:"G",label:"Gene model"},{component_id:"R",label:"Growth result"}],edges:[]};
const be={edge_id:"e2",source_component_id:"G",target_component_id:"R",relation:"CONSTRAINS",evidence:{}};
lsRenderGraphEdgeDetail(be,bg);
const h2=hostEl.innerHTML;
out.bIsCard=h2.indexOf('class="element-card')>=0;
out.bLanes=(h2.match(/element-card-lane element-card-lane-/g)||[]).length===2;
out.bNav=(h2.match(/data-element-card-item=/g)||[]).length===2;
out.bEndpoints=h2.indexOf("Gene model")>=0&&h2.indexOf("Growth result")>=0;
process.stdout.write(JSON.stringify(out));
"""
    script = tmp_path / "edge.js"
    script.write_text(harness, encoding="utf-8")
    proc = subprocess.run(
        [
            "node", str(script), str(ADMIN_LS_JS),
            str(ELEMENT_VOCAB_JS), str(ELEMENT_CARD_JS),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["pIsCard"], "physics edge: rendered through the unified card"
    assert out["pHeaderNote"], "physics edge: 「選択中の関係」の見出し注記"
    assert out["pTitle"], "physics edge: relation label as the title"
    assert out["pBody"], "physics edge: reason as the card body"
    assert out["pLanes"], "physics edge: both lanes rendered"
    assert out["pNav"], "physics edge: endpoints navigable as card items"
    assert out["pVocab"], "physics edge: kind chips come from the vocabulary source"
    assert out["pUnresolvedNote"], "physics edge: unresolvable evidence still reported"
    assert out["pBackingBadge"], "physics edge: 出所バッジ is in the meta row"
    assert out["pNoType"], "physics edge: no internal *Node type in body"
    assert out["pSystemInfo"], "physics edge: internal relation kept in system details"
    assert out["pNoDeliberate"], "physics edge: no 深く検討 for a relation"
    assert out["bIsCard"], "biology edge: rendered through the unified card"
    assert out["bLanes"], "biology edge: lanes present even with no evidence"
    assert out["bNav"], "biology edge: both endpoints navigable"
    assert out["bEndpoints"], "biology edge: endpoint labels resolved"
