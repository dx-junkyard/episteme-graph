"""図のコース流通（設計書 docs/features/hierarchical_context_explanation_design.md
Phase4 §7）のフロントエンド描画に対する静的ガードレール。

対象:
- frontend/public/js/app.js: 学習者向け `[[FIGURE_N]]` プレースホルダの解決
  （renderMaterialChunk / renderMaterialFigureCard / hydrateMaterialFigures。
  [[FORMULA_N]] と同方式・セグメント内1始まり）。
- frontend/public/js/admin-lecture-studio.js: 原稿スタジオ `![[figure:id]]` 埋め込みの
  解決（lsTopicEvidenceItems の kind='figure' 分岐 / lsRenderCourseMaterialPreview の
  embed.kind==='figure' 分岐 / lsHydrateFigureEmbeds）。
- frontend/public/css/styles.css: .material-figure 系クラス。

バックエンドは並行実装中のため、ここでは「figures / evidence が無い payload では
何もしない（防御的スキップ）」という契約をソース上で固定する。Node が使える環境では
実際に renderMaterialChunk を評価して振る舞いも検証する（無ければ skip、
test_unresolved_equation_resolution.py と同じ方針）。
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
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# app.js: [[FIGURE_N]] プレースホルダの解決
# ---------------------------------------------------------------------------


class TestAppJsFigurePlaceholderResolution:
    def test_figure_map_built_from_chunk_figures_defensively(self):
        """figures が無い payload では空配列にフォールバックする（防御的スキップ）。"""
        src = _read(APP_JS)
        assert "var figures = chunk.figures || [];" in src

    def test_generic_bracket_placeholder_falls_back_to_figure_lookup(self):
        src = _read(APP_JS)
        start = src.index("preserved = preserved.replace(/\\[\\[([^\\[\\]:]+)\\]\\]/g, function (m, id) {")
        end = src.index("});", start)
        block = src[start:end]
        assert "figureById[m] || figureById[id]" in block
        assert "preserveFigure(figure)" in block

    def test_figure_card_renderer_defined(self):
        src = _read(APP_JS)
        assert "function renderMaterialFigureCard(figure)" in src

    def test_figure_card_degrades_gracefully_without_image_url(self):
        src = _read(APP_JS)
        start = src.index("function renderMaterialFigureCard(figure)")
        end = src.index("function hydrateMaterialFigures", start)
        body = src[start:end]
        assert "if (!imgUrl) {" in body
        assert "material-figure-missing" in body

    def test_hydrate_function_defined_and_only_reads_images(self):
        src = _read(APP_JS)
        start = src.index("function hydrateMaterialFigures(container)")
        end = src.index("function markMaterialFigureFailed", start)
        body = src[start:end]
        assert "if (!container || !container.querySelectorAll) return;" in body
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert f'"{method}"' not in body

    def test_hydrate_wired_into_all_render_call_sites(self):
        src = _read(APP_JS)
        # 定義1 + コメント言及1 + 呼び出し4箇所（material region / source popup /
        # voice material / lecture stage）= 6以上。
        assert src.count("hydrateMaterialFigures(") >= 6


class TestAppJsFigureCallSiteWiring:
    def test_material_region_hydrates_figures_right_after_render(self):
        src = _read(APP_JS)
        start = src.index("function renderMaterialRegion")
        idx_html = src.index("body.innerHTML = html;", start)
        idx_hydrate = src.index("hydrateMaterialFigures(body);", idx_html)
        assert idx_hydrate - idx_html < 80

    def test_source_popup_passes_figures_and_hydrates(self):
        src = _read(APP_JS)
        assert (
            "renderMaterialChunk({ text: data.text, formulas: data.formulas, figures: data.figures })"
            in src
        )

    def test_voice_material_hydrates_figures(self):
        src = _read(APP_JS)
        idx = src.index("async function showVoiceSourceMaterial")
        end = src.index("\n  }\n", idx)
        block = src[idx:end]
        assert "figures: data.figures" in block
        assert "hydrateMaterialFigures(el);" in block

    def test_lecture_stage_pseudo_chunk_carries_figures(self):
        src = _read(APP_JS)
        assert (
            'var pseudoChunk = { text: slide.display_text || "", formulas: slide.formulas || [], figures: slide.figures || [] };'
            in src
        )
        idx = src.index("function renderLectureStage")
        end = src.index("function fitLectureSlideContent", idx)
        block = src[idx:end]
        assert "hydrateMaterialFigures(inner);" in block

    def test_lecture_deck_carries_figures_through(self):
        """buildLectureDeck が segment/slide の figures をデッキへ引き継ぐ
        （slides が無い旧バックエンド互換の1スライド合成でも figures を落とさない）。"""
        src = _read(APP_JS)
        idx = src.index("function buildLectureDeck")
        end = src.index("function showLectureSlideNotice", idx)
        block = src[idx:end]
        assert "figures: seg.figures || []" in block
        assert "figures: slide.figures || seg.figures || []" in block


# ---------------------------------------------------------------------------
# admin-lecture-studio.js: ![[figure:id]] 埋め込みの解決
# ---------------------------------------------------------------------------


class TestAdminLectureStudioFigureEvidenceItem:
    def test_evidence_items_include_figure_kind_with_confirmed_fields(self):
        src = _read(ADMIN_LS_JS)
        start = src.index('if (kind === "figure") {')
        end = src.index("return;", start)
        block = src[start:end]
        for field in ("figure_id", "figure_key", "document_id", "caption"):
            assert field in block, f"確定契約のフィールド {field} が evidence item に無い"

    def test_evidence_title_is_a_short_label_not_full_caption(self):
        """要求仕様: 「図: <caption先頭>」程度のラベル。"""
        src = _read(ADMIN_LS_JS)
        start = src.index('if (kind === "figure") {')
        end = src.index("return;", start)
        block = src[start:end]
        assert '"図: "' in block


class TestAdminLectureStudioFigureEmbedResolution:
    def test_embed_resolution_has_figure_branch(self):
        src = _read(ADMIN_LS_JS)
        assert 'if (embed.kind === "figure") {' in src

    def test_figure_branch_builds_admin_image_fetch_path(self):
        src = _read(ADMIN_LS_JS)
        start = src.index('if (embed.kind === "figure") {')
        end = src.index('if (embed.kind === "source" && (embedId === "summary"', start)
        block = src[start:end]
        assert "/admin/documents/" in block
        assert "/figures/" in block
        assert "/image" in block
        assert "data-figure-fetch-path" in block
        # ref は figure:<figure_id>（確定契約）。key は embed.kind ("figure") から作られる。
        assert 'lsCourseEvidenceKey(embed.kind, embedId)' in src

    def test_figure_branch_falls_back_to_unresolved_card_without_document(self):
        src = _read(ADMIN_LS_JS)
        start = src.index('if (embed.kind === "figure") {')
        end = src.index('if (embed.kind === "source" && (embedId === "summary"', start)
        block = src[start:end]
        assert "未解決の図" in block

    def test_hydrate_figure_embeds_defined_and_read_only(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsHydrateFigureEmbeds(container)")
        end = src.index("function lsMarkFigureEmbedFailed", start)
        body = src[start:end]
        assert "if (!container || !container.querySelectorAll) return;" in body
        calls = re.findall(r"api(?:Fetch|FetchRaw)\(", body)
        assert calls, "lsHydrateFigureEmbeds 内に API 呼び出しが見つかりません"
        assert all(c == "apiFetchRaw(" for c in calls), (
            f"想定外の API 呼び出しがあります（GET 相当の apiFetchRaw のみを想定）: {calls}"
        )
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert f'"{method}"' not in body

    def test_hydrate_called_right_after_course_draft_render(self):
        src = _read(ADMIN_LS_JS)
        idx = src.index("sourceEl.innerHTML = lsCourseDraftHtml(topic);")
        end = src.index("\n    }\n", idx)
        block = src[idx:end]
        assert "lsHydrateFigureEmbeds(sourceEl);" in block


class TestAdminLectureStudioFigureRegionEs5Guard:
    """追加分（figure 分岐 + hydrate 関数）の ES5 準拠（開発ルール5）。"""

    def _regions(self):
        src = _read(ADMIN_LS_JS)
        r1_start = src.index('if (kind === "figure") {')
        r1_end = src.index("return;", r1_start) + len("return;")
        r2_start = src.index('if (embed.kind === "figure") {')
        r2_end = src.index(
            'if (embed.kind === "source" && (embedId === "summary"', r2_start
        )
        r3_start = src.index("function lsHydrateFigureEmbeds(container)")
        r3_end = src.index("function lsRenderWorkspace", r3_start)
        return [src[r1_start:r1_end], src[r2_start:r2_end], src[r3_start:r3_end]]

    def test_no_arrow_functions(self):
        for region in self._regions():
            assert "=>" not in region

    def test_no_const_or_let(self):
        for region in self._regions():
            assert re.search(r"\bconst\s+\w", region) is None
            assert re.search(r"\blet\s+\w", region) is None

    def test_no_template_literals(self):
        for region in self._regions():
            assert "`" not in region

    def test_uses_var(self):
        for region in self._regions():
            assert "var " in region


# ---------------------------------------------------------------------------
# styles.css: .material-figure 系クラス
# ---------------------------------------------------------------------------


class TestStylesCssFigureClasses:
    def test_material_figure_classes_defined(self):
        src = _read(STYLES_CSS)
        for selector in (
            ".material-figure {",
            ".material-figure-frame {",
            ".material-figure-img {",
            ".material-figure-placeholder {",
            ".material-figure-caption {",
            ".material-figure-explanation {",
        ):
            assert selector in src, f"{selector} が styles.css に見つかりません"

    def test_lecture_slide_figure_size_capped(self):
        """レクチャースライド内の図がフォント段階縮小より先に暴走しないよう
        上限を設けている（fitLectureSlideContent の等比縮小フォールバックと共存）。"""
        src = _read(STYLES_CSS)
        assert ".lecture-slide-inner .material-figure-img {" in src
        start = src.index(".lecture-slide-inner .material-figure-img {")
        end = src.index("}", start)
        assert "max-height" in src[start:end]


# ---------------------------------------------------------------------------
# 実行時の振る舞い（Node が使える環境のみ）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_figure_placeholder_behaviour_node(tmp_path):
    """renderMaterialChunk を実評価し、[[FIGURE_N]] の解決と防御的スキップを検証する
    （test_unresolved_equation_resolution.py と同じ抽出方式）。"""
    harness = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extractFrom(src, name){
  const s = src.indexOf("function " + name);
  if (s<0) throw new Error("missing "+name);
  let d=0,st=false;
  for(let j=src.indexOf("{",s);j<src.length;j++){const c=src[j];if(c==="{"){d++;st=true;}else if(c==="}"){d--;if(st&&d===0)return src.slice(s,j+1);}}
  throw new Error("unbalanced "+name);
}
function escHtml(s){ if(!s) return ""; return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function normalizeMaterialLineBreaks(t){ return String(t||"").replace(/\r\n/g,"\n").replace(/\r/g,"\n").replace(/<br\s*\/?>/gi,"\n"); }
function normalizeMaterialEvidenceId(v){ return String(v||"").trim().replace(/^\[\[/,"").replace(/\]\]$/,"").replace(/^equation:/,"").trim().replace(/^(?:eq_){2,}/i,"eq_"); }
function renderMaterialKatex(){ return "<span class=\"katex\"></span>"; }
function renderMaterialEquationBody(){ return ""; }
eval(extractFrom(src, "renderMaterialFigureCard"));
eval(extractFrom(src, "mdBlocksToHtml"));
eval(extractFrom(src, "renderMaterialChunk"));
const out = {};
// figures 無し → プレースホルダは手つかずのまま（防御的スキップ・後方互換）
out.untouchedWithoutFigures = renderMaterialChunk({ text: "見よ [[FIGURE_1]] を。" }).indexOf("[[FIGURE_1]]") >= 0;
// figures ありで解決される（1始まり）
const withFigure = renderMaterialChunk({
  text: "見よ [[FIGURE_1]] を。",
  figures: [{ figure_id: "fig1", caption: "装置の概観", image_url: "/api/learning/courses/c1/figures/fig1/image" }],
});
out.resolvesImg = withFigure.indexOf("data-figure-fetch-url") >= 0;
out.resolvesCaption = withFigure.indexOf("装置の概観") >= 0;
out.noRawPlaceholderLeft = withFigure.indexOf("[[FIGURE_1]]") < 0;
// image_url が空でも壊れず「取得できませんでした」に縮退する（情報を落とさない）
const missingUrl = renderMaterialChunk({
  text: "[[FIGURE_1]]",
  figures: [{ figure_id: "fig1", caption: "" }],
});
out.missingUrlDegradesGracefully = missingUrl.indexOf("material-figure-missing") >= 0;
process.stdout.write(JSON.stringify(out));
"""
    script = tmp_path / "h.js"
    script.write_text(harness, encoding="utf-8")
    proc = subprocess.run(["node", str(script), str(APP_JS)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["untouchedWithoutFigures"], "figures が無ければプレースホルダをそのまま残す（防御的スキップ）"
    assert out["resolvesImg"]
    assert out["resolvesCaption"]
    assert out["noRawPlaceholderLeft"]
    assert out["missingUrlDegradesGracefully"]
