"""Issue #451 / #452: edge polarity visualization and recommended reading path.

#451: 負極性エッジを正・非極性と視覚的に区別する。判定は `polarity` フィールドに基づき、
      ラベル文字列の推測に依存しない。
#452: 主グラフに前提→主張アンカーの推奨経路を順序付きで重畳。経路は到達可能ノードのみ
      （reachability 検証）。特定ノードID列をハードコードしない。

frontend に JS テストハーネスが無いため、admin.js の純粋ヘルパを Node で評価して
ドメイン横断の入力に対する挙動を検証する（Node が無い環境では source-assertion に縮退）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
SCHEMAS = ROOT / "backend" / "api" / "schemas.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestIssue451PolaritySource:
    def test_polarity_helper_and_field_based(self):
        source = _read(ADMIN_JS)
        assert "function lsGraphEdgePolarity" in source
        # 判定は edge.polarity フィールドに基づく。
        assert "edge.polarity" in source
        # color / dashed が polarity 対応。
        ec = source[source.index("function lsGraphEdgeColor"):][:800]
        ed = source[source.index("function lsGraphEdgeDashed"):][:800]
        assert "lsGraphEdgePolarity(edge)" in ec
        assert "lsGraphEdgePolarity(edge)" in ed

    def test_polarity_passes_through_api_schema(self):
        schemas = _read(SCHEMAS)
        start = schemas.index("class ComponentGraphEdge")
        end = schemas.index("class ComponentGraphResponse", start)
        assert "polarity: str = \"\"" in schemas[start:end]

    def test_legend_explains_polarity(self):
        source = _read(ADMIN_JS)
        assert "ls-legend-line-negative" in source
        css = _read(STYLES_CSS)
        assert ".ls-legend-line-negative" in css

    def test_polarity_on_component_edge_dataclass_serializes(self):
        from episteme_graph.agents.component_graph.schema import (
            ComponentGraphEdge,
            ComponentGraphResult,
        )
        edge = ComponentGraphEdge(edge_id="e", source="a", target="b", edge_type="derives",
                                  support_status="x", evidence_claims=[], reasoning="", polarity="-")
        result = ComponentGraphResult(document_id="d", graph_schema_version="1", cartridge_id=None,
                                      nodes=[], edges=[edge], review_notes=[], confidence=1.0)
        assert result.to_graph_payload()["edges"][0]["polarity"] == "-"


class TestIssue452ReadingPath:
    def test_path_helpers_exist(self):
        source = _read(ADMIN_JS)
        for fn in ("function lsGraphComputeReadingPath", "function lsGraphReadingPathToggleHtml",
                   "function lsBindGraphReadingPathToggle", "function lsCircledNumber"):
            assert fn in source, fn

    def test_path_uses_reachability_and_anchor_not_hardcoded(self):
        source = _read(ADMIN_JS)
        body = source[source.index("function lsGraphComputeReadingPath"):][:3000]
        # アンカーは node フラグ由来、順序は stage 由来、到達可能性で検証。
        assert "is_thesis_anchor" in body
        assert "LS_MAIN_STAGE_LABELS" in source
        assert "canReachAnchor" in body and "reachableFromRoot" in body

    def test_path_overlay_and_css(self):
        source = _read(ADMIN_JS)
        assert "pathOrder" in source
        assert "lsCircledNumber(pathOrder[id])" in source
        css = _read(STYLES_CSS)
        assert ".ls-graph-reading-path" in css


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_polarity_and_path_cross_domain_behaviour(tmp_path):
    harness = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extractFn(name){
  const s = src.indexOf("function " + name);
  if (s<0) throw new Error("missing "+name);
  let d=0,st=false;
  for(let j=src.indexOf("{",s);j<src.length;j++){const c=src[j];if(c==="{"){d++;st=true;}else if(c==="}"){d--;if(st&&d===0)return src.slice(s,j+1);}}
  throw new Error("unbalanced "+name);
}
function extractVar(marker){const s=src.indexOf(marker);const e=src.indexOf("\n  ];\n",s)+6;return src.slice(s,e);}
function lsGraphNodeId(n){return n&&(n.component_id||n.id||n.node_id);}
eval(extractVar("var LS_MAIN_STAGE_LABELS = ["));
eval(["lsGraphMainStageLabel","lsGraphStageIndex","lsCircledNumber","lsGraphComputeReadingPath","lsGraphEdgePolarity","lsGraphEdgeDashed","lsGraphEdgeColor"].map(extractFn).join("\n\n"));
const out={};
// #451: field-based, not label-based
out.negFieldCrimson = lsGraphEdgeColor({relation:"DERIVES",polarity:"-"})==="#dc2626";  // positive label, neg field
out.negFieldDashed = lsGraphEdgeDashed({relation:"DERIVES",polarity:"-"})===true;
out.posFieldNotCrimson = lsGraphEdgeColor({relation:"INHIBITS",polarity:"+"})!=="#dc2626"; // neg label, pos field
out.nonpolarNotCrimson = lsGraphEdgeColor({relation:"DERIVES",polarity:""})!=="#dc2626";
// #452: two domains
function mk(id,stage,extra){return Object.assign({component_id:id,component_type:"TheoryOperationNode",graph_layer:"main",label:stage},extra||{});}
const pN=[mk("p1","Theory basis"),mk("p2","Equation system"),mk("p3","Consistency relation"),mk("p4","Diagnostic / application",{is_thesis_anchor:true}),mk("iso","Observation model")];
const pE=[{source:"p1",target:"p2"},{source:"p2",target:"p3"},{source:"p3",target:"p4"}];
const pp=lsGraphComputeReadingPath({nodes:pN,edges:pE});
out.physOrdered = JSON.stringify(pp)===JSON.stringify(["p1","p2","p3","p4"]);
out.physIsoExcluded = pp.indexOf("iso")<0;
const bN=[mk("b1","Observation model"),mk("b2","Observable construction"),mk("b3","Elimination",{is_thesis_anchor:true}),mk("bx","Diagnostic / application")];
const bE=[{source:"b1",target:"b2"},{source:"b2",target:"b3"},{source:"bx",target:"b1"}];
const bp=lsGraphComputeReadingPath({nodes:bN,edges:bE});
out.bioEndsAnchor = bp[bp.length-1]==="b3";
out.bioExtraExcluded = bp.indexOf("bx")<0;
out.disconnectedEmpty = lsGraphComputeReadingPath({nodes:[mk("x","Theory basis"),mk("y","Elimination")],edges:[]}).length===0;
process.stdout.write(JSON.stringify(out));
"""
    script = tmp_path / "h.js"
    script.write_text(harness, encoding="utf-8")
    proc = subprocess.run(["node", str(script), str(ADMIN_JS)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    # #451 — distinction is from the polarity field, independent of label
    assert out["negFieldCrimson"], "negative polarity field must override positive-looking label"
    assert out["negFieldDashed"], "negative polarity must dash"
    assert out["posFieldNotCrimson"], "positive field must not get negative styling despite label"
    assert out["nonpolarNotCrimson"], "non-polar must not get negative styling"
    # #452 — ordered, reachability-validated path to the anchor, no hardcoded ids
    assert out["physOrdered"], "physics path must be stage-ordered to the anchor"
    assert out["physIsoExcluded"], "unreachable node must be excluded"
    assert out["bioEndsAnchor"], "biology path must end at the flagged anchor"
    assert out["bioExtraExcluded"], "node not on the root→anchor route must be excluded"
    assert out["disconnectedEmpty"], "disconnected main nodes yield no path"
