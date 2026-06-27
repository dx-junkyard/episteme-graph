"""未解決の数式: 数式埋め込みの解決を回帰チェックする。

授業用ドラフト / 教材スタジオで `![[equation:<id>]]` を解決する純粋ヘルパを admin.js /
app.js から抽出し、Node で評価する:

- 二重 `eq_` プレフィックス (`eq_eq_F2`) を正規化で `eq_F2` に畳み込み、解決できること。
- LaTeX が無くても plain_text / raw_text を読みとして表示し、「未解決」にしないこと。
- 本当に表現が何も無い場合のみ未解決になること。

Node が無い環境では source-assertion に縮退する。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class TestSourceGuards:
    def test_eq_prefix_collapsed_in_normalizers(self):
        assert "(?:eq_){2,}" in _read(ADMIN_JS)
        assert "(?:eq_){2,}" in _read(APP_JS)

    def test_fallback_render_helpers_exist(self):
        assert "function lsEquationEmbedBody" in _read(ADMIN_JS)
        assert "function renderMaterialEquationBody" in _read(APP_JS)

    def test_topic_formulas_keep_latexless_items(self):
        body = _read(ADMIN_JS)
        start = body.index("function lsTopicFormulas")
        snippet = body[start:start + 700]
        # No longer dropped purely for lacking latex.
        assert "!item.latex && !item.plain_text && !item.raw_text" in snippet


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_equation_resolution_behaviour(tmp_path):
    harness = r"""
const fs = require("fs");
const adminSrc = fs.readFileSync(process.argv[2], "utf8");
function extractFrom(src, name){
  const s = src.indexOf("function " + name);
  if (s<0) throw new Error("missing "+name);
  let d=0,st=false;
  for(let j=src.indexOf("{",s);j<src.length;j++){const c=src[j];if(c==="{"){d++;st=true;}else if(c==="}"){d--;if(st&&d===0)return src.slice(s,j+1);}}
  throw new Error("unbalanced "+name);
}
function escHtml(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function lsRenderKatex(expr){return expr ? '<span class="katex">'+escHtml(expr)+'</span>' : "";}
eval(extractFrom(adminSrc,"lsNormalizeEvidenceId"));
eval(extractFrom(adminSrc,"lsEquationEmbedBody"));
const out={};
// id collapse: legacy double prefix resolves to the corrected single prefix
out.collapse = lsNormalizeEvidenceId("eq_eq_F2")===lsNormalizeEvidenceId("eq_F2") && lsNormalizeEvidenceId("eq_eq_F2")==="eq_F2";
out.singleUntouched = lsNormalizeEvidenceId("eq_F2")==="eq_F2";
out.nonEqUntouched = lsNormalizeEvidenceId("comp_001")==="comp_001";
// fallback rendering
out.latex = /katex/.test(lsEquationEmbedBody({latex:"a=b"}));
out.plain = /formula-plain/.test(lsEquationEmbedBody({latex:"",plain_text:"aはbに等しい"}));
out.raw = /formula-raw/.test(lsEquationEmbedBody({plain_text:"",raw_text:"F2 = a+b"}));
out.empty = lsEquationEmbedBody({latex:"",plain_text:"",raw_text:""})==="";
out.nullEmpty = lsEquationEmbedBody(null)==="";
process.stdout.write(JSON.stringify(out));
"""
    script = tmp_path / "h.js"
    script.write_text(harness, encoding="utf-8")
    proc = subprocess.run(["node", str(script), str(ADMIN_JS)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["collapse"], "eq_eq_F2 must normalize to eq_F2"
    assert out["singleUntouched"]
    assert out["nonEqUntouched"], "non-equation ids must not be altered"
    assert out["latex"], "latex renders as math"
    assert out["plain"], "plain_text renders as a reading fallback"
    assert out["raw"], "raw_text renders as a fallback"
    assert out["empty"], "no representation → empty (caller shows 未解決)"
    assert out["nullEmpty"]
