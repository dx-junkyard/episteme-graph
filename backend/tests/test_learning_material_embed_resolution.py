"""学習画面レンダラ（app.js renderMaterialChunk）の埋め込み解決の回帰・契約テスト。

問題: 学習画面は equation / figure 以外の ``![[kind:id]]`` を常に「未解決」表示に
していた。修正で、backend が渡す evidence_items（build_topic_evidence_items）を使って
component / claim / source も読み取り専用カードとして解決する。

このテストは JS 側を Node で評価し、以下を固定する:

- ID 正規化の**両画面一致**: app.js ``normalizeMaterialEvidenceId`` と
  admin-lecture-studio.js ``lsNormalizeEvidenceId`` が同一入力に同一出力を返す。
- renderMaterialChunk が evidence_items から component / claim / source を根拠カードに、
  equation を数式に解決し、未解決参照は安全なフォールバック（未解決カード）に留める。
- **契約**: 同じトピックに対し admin ``lsTopicEvidenceItems`` が解決する
  (kind, 正規化 id) の集合と、backend ``build_topic_evidence_items`` の集合が一致する
  （差分は トピック概要 ``source:topic_summary`` / ``source:summary`` のみ＝明文化された差分）。

Node が無い環境では source-assertion に縮退する（node 依存テストは skip）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"

_EXTRACT = r"""
function extractFrom(src, name){
  const s = src.indexOf("function " + name + "(");
  if (s<0) throw new Error("missing "+name);
  let d=0,st=false;
  for(let j=src.indexOf("{",s);j<src.length;j++){const c=src[j];if(c==="{"){d++;st=true;}else if(c==="}"){d--;if(st&&d===0)return src.slice(s,j+1);}}
  throw new Error("unbalanced "+name);
}
// 複数関数をまとめて1つのソース文字列にする。呼び出し側は必ず**トップレベル**で
// eval(extractMany(...)) すること（forEach などのコールバック内で eval すると宣言が
// そのコールバックのスコープに閉じ込められ、外から参照できなくなる）。
function extractMany(src, names){ return names.map(function(n){ return extractFrom(src,n); }).join("\n"); }
// function ではない "var NAME = { ... };" / "var NAME = [ ... ];" 形式のモジュール
// 定数（例: MATERIAL_EVIDENCE_KIND_LABELS / MATERIAL_ELEMENT_CONTEXT_TYPES）を抽出する。
// element-vocab.js（要素種別の表示名の正本 / window.ElementVocab）を app.js と同じ
// ディレクトリから読み込む。app.js は独自の種別辞書を持たず ElementVocab へ委譲するため、
// 表示名を検証する harness は必ずトップレベルで
// eval(loadElementVocab(process.argv[2])) すること（var window の宣言より後）。
function loadElementVocab(appPath){
  const pathmod=require("path");
  return require("fs").readFileSync(pathmod.join(pathmod.dirname(appPath),"element-vocab.js"),"utf8");
}
function extractVar(src, name){
  const s = src.indexOf("var " + name + " = ");
  if (s<0) throw new Error("missing var "+name);
  let open=-1,oc="",cc="";
  for(let j=src.indexOf("=",s)+1;j<src.length;j++){
    const c=src[j];
    if(c===" "||c==="\n"||c==="\r"||c==="\t") continue;
    if(c==="{"){open=j;oc="{";cc="}";}
    else if(c==="["){open=j;oc="[";cc="]";}
    break;
  }
  if(open<0) throw new Error("unsupported initializer for var "+name);
  let d=0;
  for(let j=open;j<src.length;j++){const c=src[j];if(c===oc){d++;}else if(c===cc){d--;if(d===0)return src.slice(s,j+1)+";";}}
  throw new Error("unbalanced var "+name);
}
"""


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class TestSourceGuards:
    """Node 不要の静的アサーション（実装の存在・両画面共通仕様）。"""

    def test_renderer_consumes_evidence_items(self):
        app = _read(APP_JS)
        assert "chunk.evidence_items" in app
        assert "function lookupEvidenceItem" in app
        assert "function renderMaterialMissingEmbed" in app

    def test_normalizers_share_eq_prefix_collapse(self):
        assert "(?:eq_){2,}" in _read(APP_JS)
        assert "(?:eq_){2,}" in _read(ADMIN_LS_JS)


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
class TestNodeBehaviour:
    def _run(self, script: str, tmp_path: Path, *args: str) -> dict:
        p = tmp_path / "h.js"
        p.write_text(_EXTRACT + script, encoding="utf-8")
        proc = subprocess.run(
            ["node", str(p), str(APP_JS), str(ADMIN_LS_JS), *args],
            capture_output=True, text=True, timeout=40,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    def test_normalizer_parity(self, tmp_path):
        script = r"""
const fs=require("fs");
const app=fs.readFileSync(process.argv[2],"utf8");
const admin=fs.readFileSync(process.argv[3],"utf8");
eval(extractFrom(app,"normalizeMaterialEvidenceId"));
eval(extractFrom(admin,"lsNormalizeEvidenceId"));
const inputs=["comp_001","  comp_001  ","[[claim_1]]","[[[[eq_x]]]]","eq_eq_F2","EQ_EQ_F2",
  "eq_F2","","  ","source:topic_summary","FIGURE_1","fig-uuid-1","[[eq_1]","eq_1]]"];
const mismatches=[];
inputs.forEach(function(v){
  if(normalizeMaterialEvidenceId(v)!==lsNormalizeEvidenceId(v)){
    mismatches.push([v, normalizeMaterialEvidenceId(v), lsNormalizeEvidenceId(v)]);
  }
});
process.stdout.write(JSON.stringify({mismatches:mismatches}));
"""
        out = self._run(script, tmp_path)
        assert out["mismatches"] == [], f"normalizers diverge: {out['mismatches']}"

    def test_render_resolves_all_kinds(self, tmp_path):
        """component/claim/source/equation それぞれの解決を検証する。component/claim は
        component_evidence_redesign（引用チップ化）以降、ブロックカードではなくインライン
        チップに解決される（summary はポップオーバー専用でチップの静的 HTML には出ない）。
        source/equation は従来どおりブロックカードのまま。"""
        script = r"""
const fs=require("fs");
const app=fs.readFileSync(process.argv[2],"utf8");
var window={katex:null};
function escHtml(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
var materialEvidenceChipItems = {};
eval(loadElementVocab(process.argv[2]));
eval(extractFrom(app,"materialEvidenceKindLabel"));
eval(extractVar(app,"MATERIAL_ELEMENT_CONTEXT_TYPES"));
eval(extractMany(app,["normalizeMaterialEvidenceId","normalizeMaterialLineBreaks","normalizeKatexFormula",
 "renderMaterialKatex","renderMaterialEquationBody","renderMaterialMissingEmbed",
 "shortMaterialEvidenceSummary","renderMaterialFigureCard","registerMaterialEvidenceChipEntry",
 "renderMaterialEvidenceChip","renderMaterialElementContextButton","mdBlocksToHtml","renderMaterialChunk"]));
const chunk={
  text:"本文 ![[component:comp_1]] つづき ![[claim:claim_1]] さらに ![[source:ev_1]] "
    +"数式 ![[equation:eq_1]] 未知 ![[component:missing_1]]",
  formulas:[],
  figures:[],
  evidence_items:[
    {kind:"component",id:"comp_1",title:"コンポーネントA",summary:"コンポの要約",role:"support",confidence:"high"},
    {kind:"claim",id:"claim_1",title:"主張A",summary:"主張の要約",role:"source_backed"},
    {kind:"source",id:"ev_1",title:"原文引用",summary:"引用テキスト",role:"source_quote"},
    {kind:"equation",id:"eq_1",title:"式L",latex:"a=b",role:"equation"}
  ]
};
const html=renderMaterialChunk(chunk);
const out={
  component: html.indexOf("コンポーネントA")>=0 && html.indexOf("ls-material-evidence-chip")>=0,
  claim: html.indexOf("主張A")>=0,
  source: html.indexOf("原文引用")>=0,
  equation: /ls-material-formula-only/.test(html),
  // component/claim はチップ化され、summary はチップの静的 HTML に出ない（ポップオーバー専用）
  componentSummaryNotInline: html.indexOf("コンポの要約")<0,
  // 解決済み参照は「未解決」カードにならない
  resolvedNotMissing: html.indexOf("comp_1")<0 || html.indexOf("ls-material-missing")>=0,
  // 未知 ID は安全なフォールバック（未解決カード）に留まる
  missingFallback: /ls-material-missing/.test(html) && html.indexOf("missing_1")>=0,
  // チップ2件（component + claim）
  chips: (html.match(/ls-material-evidence-chip"/g)||[]).length,
  // source の解決済みカード + 未解決1カード = 2（component/claim はもうカードにならない）
  cards: (html.match(/ls-material-evidence-card/g)||[]).length
};
process.stdout.write(JSON.stringify(out));
"""
        out = self._run(script, tmp_path)
        assert out["component"], "component embed must render as an inline chip with its label"
        assert out["claim"], "claim embed must resolve"
        assert out["source"], "source embed must resolve"
        assert out["equation"], "equation embed must render as math via evidence_items latex"
        assert out["componentSummaryNotInline"], "component summary must stay popover-only, not leak into chip HTML"
        assert out["missingFallback"], "unknown id must fall back to the safe 未解決 card"
        assert out["chips"] == 2, out
        assert out["cards"] == 2, out

    def test_kind_mismatch_falls_back_by_id(self, tmp_path):
        """本文が kind を取り違えても（実体は component なのに ``![[claim:comp_1]]``）、
        同一 ID の公開済み項目へ解決する（admin lsEvidenceItemByRef と同一の id フォールバック）。"""
        script = r"""
const fs=require("fs");
const app=fs.readFileSync(process.argv[2],"utf8");
var window={katex:null};
function escHtml(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
var materialEvidenceChipItems = {};
eval(loadElementVocab(process.argv[2]));
eval(extractFrom(app,"materialEvidenceKindLabel"));
eval(extractVar(app,"MATERIAL_ELEMENT_CONTEXT_TYPES"));
eval(extractMany(app,["normalizeMaterialEvidenceId","normalizeMaterialLineBreaks","normalizeKatexFormula",
 "renderMaterialKatex","renderMaterialEquationBody","renderMaterialMissingEmbed",
 "shortMaterialEvidenceSummary","renderMaterialFigureCard","registerMaterialEvidenceChipEntry",
 "renderMaterialEvidenceChip","renderMaterialElementContextButton","mdBlocksToHtml","renderMaterialChunk"]));
const chunk={
  text:"取り違え ![[claim:comp_1]]",
  formulas:[], figures:[],
  evidence_items:[{kind:"component",id:"comp_1",title:"コンポA",summary:"要約",role:"support"}]
};
const html=renderMaterialChunk(chunk);
process.stdout.write(JSON.stringify({
  resolved: html.indexOf("コンポA")>=0,
  notMissing: !/ls-material-missing/.test(html)
}));
"""
        out = self._run(script, tmp_path)
        assert out["resolved"], "kind 違いでも同一 ID の項目に解決する"
        assert out["notMissing"], "id フォールバックが効けば未解決カードにしない"

    def test_admin_learner_resolution_contract(self, tmp_path):
        """admin lsTopicEvidenceItems と backend build_topic_evidence_items の解決キー一致。

        両者が解決する (kind, 正規化 id) 集合を比較する。唯一の意図的差分は、学習者
        DTO が自己完結するために足す トピック概要（source:topic_summary / source:summary）。
        """
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "content_confidence": "medium",
            "summary": "トピックの概要テキスト。",
            "source_excerpt": "原文からの抜粋テキスト。",
            "linked_chunk_ids": ["chunk_9"],
            "linked_component_ids": ["comp_extra", "comp_001"],
            "content_blocks": [
                {"type": "components", "items": [
                    {"component_id": "comp_extra", "label": "ラベルX", "teaching_takeaway": "要点X"},
                ]},
                {"type": "equations", "items": [
                    {"equation_id": "eq_local", "latex": "a=b", "label": "L"},
                ]},
            ],
            "evidence_links": [
                {"kind": "component", "target_id": "comp_001", "summary": "コンポ要約",
                 "support_role": "support", "confidence": "high"},
                {"kind": "claim", "target_id": "claim_001", "summary": "主張テキスト",
                 "support_role": "source_backed"},
                {"kind": "source", "target_id": "ev_001", "summary": "原文引用",
                 "support_role": "source_quote"},
                {"kind": "equation", "target_id": "eq_1", "latex": "x=y", "label": "L1"},
                {"kind": "source", "target_id": "ev_eq", "support_role": "equation_quote",
                 "summary": r"\begin{aligned} p&=q \end{aligned}"},
                {"kind": "figure", "target_id": "fig1", "figure_id": "FIG-UUID",
                 "figure_key": "fk", "caption": "図の説明"},
            ],
        }

        backend_keys = {f"{it['kind']}:{it['id']}" for it in build_topic_evidence_items(topic)}

        topic_file = tmp_path / "topic.json"
        topic_file.write_text(json.dumps(topic), encoding="utf-8")
        script = r"""
const fs=require("fs");
const admin=fs.readFileSync(process.argv[3],"utf8");
const topic=JSON.parse(fs.readFileSync(process.argv[4],"utf8"));
var lsState={courseComponents:{components:[]}};  // backend は courseComponents を持たない
eval(extractMany(admin,["lsNormalizeEvidenceId","lsCourseEvidenceKey","lsShortSummary","lsTopicComponentById",
  "lsCourseComponentById","lsTopicFormulas","lsLooksLikeTexMath","lsTopicEvidenceItems"]));
const keys=lsTopicEvidenceItems(topic).map(function(it){return lsCourseEvidenceKey(it.kind,it.id);});
process.stdout.write(JSON.stringify({keys:keys}));
"""
        out = self._run(script, tmp_path, str(topic_file))
        admin_keys = set(out["keys"])

        summary_only = {"source:topic_summary", "source:summary"}
        # 学習者 DTO 固有のトピック概要を除けば、両画面の解決キーは完全一致する。
        assert backend_keys - summary_only == admin_keys, (
            f"backend(minus summary)={sorted(backend_keys - summary_only)} "
            f"admin={sorted(admin_keys)}"
        )
        # 差分は明文化されたトピック概要のみ（backend 側だけが持つ）。
        assert summary_only <= backend_keys
        assert not (summary_only & admin_keys)
