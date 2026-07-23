"""教材本文の Markdown ブロック変換（見出し #..###### / 箇条書き - * + /
番号付きリスト 1. 1)) の回帰テスト。

背景: かつて受講画面（app.js renderMaterialChunk）と授業用ドラフト・スライド
プレビュー（admin-lecture-studio.js の各レンダラ）は見出しを `##`/`###` と太字しか
変換せず、`####` や `- ` / `1. ` が記号のまま学習者・教員に表示されていた
（教材プレビュー・受講画面での Markdown 記号の露出）。修正で両画面ともブロック変換の
共通ロジック（app.js: mdBlocksToHtml / admin: lsMdBlocksToHtml。教材は headingBase=1）
を通す。

- 静的契約: 各レンダラが共通ブロック変換を通り、旧来の限定的な `^### ` 置換や
  `split("\\n\\n")` の直接 `<p>` ラップを使っていないこと。
- 実挙動（node があれば）: `mdBlocksToHtml` / `lsMdBlocksToHtml` を実評価し、
  `####`→h5、`- `→<ul><li>、`1. `→<ol><li> に変換され、記号が素通しされないこと。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 静的契約: レンダラが共通ブロック変換を通ること
# ---------------------------------------------------------------------------
class TestStaticContract:
    def test_app_defines_block_converter_with_heading_base(self):
        src = _read(APP_JS)
        assert "function mdBlocksToHtml(escaped, inline, headingBase)" in src
        # 見出しレベルは headingBase 分シフトし h6 で頭打ち。
        assert "Math.min(6, h[1].length + base)" in src
        # 箇条書き・番号付きリストの検出。
        assert r"/^\s*[-*+]\s+(.*)$/" in src
        assert r"/^\s*(\d+)[.)]\s+(.*)$/" in src

    def test_render_material_chunk_routes_through_block_converter(self):
        src = _read(APP_JS)
        start = src.index("function renderMaterialChunk(chunk) {")
        end = src.index("function renderMaterialMissingEmbed", start)
        body = src[start:end]
        # 教材本文は headingBase=1 でブロック変換する（#→h2 を維持しつつ #### 以降にも対応）。
        assert "mdBlocksToHtml(escHtml(preserved)" in body
        # 旧来の限定的な見出し置換は残っていないこと。
        assert r'html.replace(/^### (.+)$/gm' not in body
        assert 'html.split("\\n\\n")' not in body

    def test_admin_defines_shared_block_converter(self):
        src = _read(ADMIN_LS_JS)
        assert "function lsMdBlocksToHtml(escaped, inline, headingBase)" in src
        assert "function lsInlineMd(s)" in src
        assert "Math.min(6, h[1].length + base)" in src
        assert r"/^\s*[-*+]\s+(.*)$/" in src
        assert r"/^\s*(\d+)[.)]\s+(.*)$/" in src

    def test_admin_material_renderers_route_through_block_converter(self):
        src = _read(ADMIN_LS_JS)
        for fn in (
            "function lsRenderCourseMaterialPreview(text, topic) {",
            "function lsRenderTextWithFormulas(text, formulas) {",
            "function lsRenderDisplayPreview() {",
        ):
            start = src.index(fn)
            # 次の関数宣言までを当該レンダラ本文とみなし、共通ブロック変換
            # lsMdBlocksToHtml の呼び出しがあることを確認する。
            nxt = src.index("\n  function ", start + 1)
            body = src[start:nxt]
            assert "lsMdBlocksToHtml(" in body, fn
        # 旧来の限定的な見出し置換が撤去されていること。
        assert r'html.replace(/^### (.+)$/gm, "<h4>$1</h4>")' not in src

    def test_css_styles_material_headings_and_lists(self):
        css = _read(STYLES_CSS)
        # 受講画面（教材本文・レクチャースライド）の見出し h2..h6 とリスト。
        assert ".material-chunk-text h5" in css
        assert ".material-chunk-text ul" in css or ".material-chunk-text ol" in css
        assert ".lecture-slide-text h5" in css
        # 授業用ドラフト・スライドプレビューの見出し・リスト。
        assert ".ls-course-material-preview h5" in css
        assert ".ls-display-preview h5" in css


# ---------------------------------------------------------------------------
# 実挙動（node があれば）: ブロック変換の出力を検証
# ---------------------------------------------------------------------------
_EXTRACT = r"""
function extractFrom(src, name){
  const s = src.indexOf("function " + name);
  if (s<0) throw new Error("missing "+name);
  let d=0,st=false;
  for(let j=src.indexOf("{",s);j<src.length;j++){const c=src[j];if(c==="{"){d++;st=true;}else if(c==="}"){d--;if(st&&d===0)return src.slice(s,j+1);}}
  throw new Error("unbalanced "+name);
}
function boldInline(s){ return s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>"); }
const SAMPLE = [
  "#### この節で押さえるべきこと",
  "",
  "- 共振器増幅の主張は、**特定の理想キャビティ領域**の中で評価する。",
  "- 次節で扱う側帯波の対称性を導く準備をする。",
  "",
  "1. 理想共振条件は、増幅を議論するための基準点である。",
  "2. ideal cavity regime は、近似の適用範囲を限定する枠組みである。",
  "",
  "## 大見出し",
].join("\n");
function checks(html){
  return {
    // #### は h5（headingBase=1 で 4+1）に変換され、記号が素通ししない。
    h5: /<h5>この節で押さえるべきこと<\/h5>/.test(html),
    noRawHashes: html.indexOf("####") < 0,
    // 箇条書きはリスト化され、行頭の "- " が残らない。
    ul: html.indexOf("<ul>") >= 0 && html.indexOf("<li>") >= 0,
    bold: html.indexOf("<strong>") >= 0,
    noRawDash: /(^|>)-\s/.test(html) === false,
    // 番号付きは <ol> 化され "1. " が残らない。
    ol: html.indexOf("<ol>") >= 0,
    noRawNum: html.indexOf("1. 理想共振条件") < 0,
    // ## は h3（2+1）。
    h3: /<h3>大見出し<\/h3>/.test(html),
  };
}
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_app_block_converter_behaviour(tmp_path):
    script = _EXTRACT + r"""
const fs = require("fs");
const app = fs.readFileSync(process.argv[2], "utf8");
eval(extractFrom(app, "mdBlocksToHtml"));
const html = mdBlocksToHtml(SAMPLE, boldInline, 1);
process.stdout.write(JSON.stringify(checks(html)));
"""
    harness = tmp_path / "h.js"
    harness.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(APP_JS)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for key, ok in out.items():
        assert ok, f"app.js mdBlocksToHtml failed check: {key} ({out})"


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_admin_block_converter_behaviour(tmp_path):
    script = _EXTRACT + r"""
const fs = require("fs");
const admin = fs.readFileSync(process.argv[2], "utf8");
eval(extractFrom(admin, "lsMdBlocksToHtml"));
const html = lsMdBlocksToHtml(SAMPLE, boldInline, 1);
process.stdout.write(JSON.stringify(checks(html)));
"""
    harness = tmp_path / "h.js"
    harness.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(ADMIN_LS_JS)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for key, ok in out.items():
        assert ok, f"admin-lecture-studio.js lsMdBlocksToHtml failed check: {key} ({out})"
