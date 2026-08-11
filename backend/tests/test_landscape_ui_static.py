"""知識ランドスケープ（学習者側フロント）の静的ガードレール。

設計正本: ``docs/features/knowledge_landscape_design.md`` §4.2（受講者 UX）/
§9.2（学習者 API 契約）/ §10.1（landscape-layer.js）/ §10.2（出典タブ）/ §10.4（CSS）/ §11。
test_discuss_ui_static.py / test_learning_ui_phase2_static.py と同じ流儀で、ソース文字列の
部分一致・関数本体の抽出検査のみを行う（実サーバ・実DOM・実API 不使用）。

教員側（admin.js / admin_ui_anchors.py）の契約は別ファイル
``test_landscape_admin_ui_static.py`` の担当なので、ここでは扱わない。

検証観点:
1. index.html に landscape-layer.js の script タグがあり、personal-map.js より後に
   読み込まれる（atlas-overlay.js のフックから呼ばれる側）。
2. ``window.LandscapeLayer`` が §10.1 の6メソッド
   （init / mountControls / onLevelRendered / onOverlayClosed / getData / invalidate）
   を公開する。
3. atlas-overlay.js への変更は3行のフック呼び出し追加のみで、いずれも
   ``if (window.LandscapeLayer)`` でガードされている（未読込環境で壊れない）。
4. ``LIMITS.l1Regions`` が 12（§6.2 の骨格上限引き上げ・backend MAX_REGIONS と同期）。
5. LS6 fail-closed: 非 ok 応答は ``return null``、フィクスチャ（window.ATLAS_FIXTURE）へ
   退避しない。LS9: ポーリングしない（setInterval を書かない）。
6. LS5 数値非表示: 表示はサーバの ``perspective_label`` / ``weight_label`` /
   ``provenance_label`` から組み、生の ``weight`` / ``confidence`` を参照しない。
7. LS7 地図の安定性: 描画は ``.landscape-layer`` を1つ足すだけで、既存ノード・
   エッジ・ミニマップ（atlas-minimap）に触らない。
8. §10.2: app.js の出典タブに「分野の中の位置づけ」セクションがあり、
   ``data-ui-anchor="sources.paper-placement"`` が付与され、その値が
   ``core/help_kb/ui_anchors.py`` の正本（KNOWN_UI_ANCHOR_IDS / UI_ANCHORS）に登録済み。
9. コース切替でキャッシュを破棄する（switchCourse から invalidate を呼ぶ）。
10. §10.4: マーカー・「+N」・ポップオーバー・出典タブチップの CSS が実在し、
    index.html のキャッシュバスタ（?v=）が更新されている。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONT = ROOT / "frontend" / "public"
INDEX_HTML = FRONT / "index.html"
APP_JS = FRONT / "js" / "app.js"
ATLAS_OVERLAY_JS = FRONT / "js" / "atlas-overlay.js"
LANDSCAPE_JS = FRONT / "js" / "landscape-layer.js"
ATLAS_CSS = FRONT / "css" / "atlas.css"
STYLES_CSS = FRONT / "css" / "styles.css"

BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb.ui_anchors import (  # noqa: E402
    KNOWN_UI_ANCHOR_IDS,
    UI_ANCHORS,
)

LEARNER_ANCHOR_ID = "sources.paper-placement"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_js_comments(src: str) -> str:
    """コメント（``/* ... */`` と行末 ``// ...``）を落とす。

    「参照していないこと」を検査する否定形アサーションは、設計意図を書いた
    日本語コメント（「weight / confidence の生値は出さない」等）に引っかかっては
    意味がないため、実コードのみを対象にする。
    """
    without_block = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", without_block)


def _extract_function_body(src: str, signature: str) -> str:
    """`signature`（例: "function foo(...) {"）から対応する閉じ `}` までを、
    素朴な波括弧カウントで抽出する（test_discuss_ui_static.py と同じ流儀）。"""
    start = src.index(signature)
    brace_start = src.index("{", start)
    depth = 0
    i = brace_start
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("unterminated function body for: " + signature)


# ===========================================================================
# 1. 読み込み（index.html）
# ===========================================================================


class TestScriptLoading:
    def test_landscape_layer_script_tag_present(self):
        html = _read(INDEX_HTML)
        assert "/js/landscape-layer.js" in html, (
            "index.html に landscape-layer.js の script タグがありません"
        )

    def test_script_tag_has_cache_buster(self):
        html = _read(INDEX_HTML)
        assert re.search(r'/js/landscape-layer\.js\?v=[\w.\-]+', html), (
            "landscape-layer.js の script タグに ?v= キャッシュバスタがありません"
        )

    def test_loaded_after_personal_map(self):
        """atlas-overlay.js のフック（PersonalMap と並ぶ位置）から呼ばれる側なので、
        personal-map.js 系より後に読み込む（§10.1 の配置指定）。"""
        html = _read(INDEX_HTML)
        assert html.index("/js/personal-map.js") < html.index("/js/landscape-layer.js")

    def test_css_cache_busters_bumped(self):
        """§10.4: 学習者側マーカー/ポップオーバー・出典タブのスタイル追加に合わせて
        atlas.css / styles.css の ?v= を更新する（古い CSS のままだと効かない）。"""
        html = _read(INDEX_HTML)
        assert "/css/atlas.css?v=knowledge-landscape-20260804" in html
        assert "/css/styles.css?v=knowledge-landscape-20260804" in html

    def test_landscape_layer_file_exists(self):
        assert LANDSCAPE_JS.is_file(), "frontend/public/js/landscape-layer.js がありません"


# ===========================================================================
# 2. 公開 API（§10.1）
# ===========================================================================


class TestPublicApi:
    EXPECTED = (
        "init",
        "mountControls",
        "onLevelRendered",
        "onOverlayClosed",
        "getData",
        "invalidate",
    )

    def test_window_landscape_layer_exposed(self):
        src = _read(LANDSCAPE_JS)
        assert "window.LandscapeLayer = {" in src

    def test_all_six_methods_exported(self):
        src = _read(LANDSCAPE_JS)
        block = src[src.index("window.LandscapeLayer = {"):]
        block = block[: block.index("};") + 2]
        for name in self.EXPECTED:
            assert re.search(r"\b" + name + r"\b", block), (
                f"window.LandscapeLayer が {name} を公開していません"
            )

    def test_each_method_is_defined(self):
        src = _read(LANDSCAPE_JS)
        for name in self.EXPECTED:
            assert f"function {name}(" in src, f"{name}() の定義がありません"

    def test_get_data_returns_shared_fetch(self):
        """§10.2: 出典タブ（app.js）が同一 fetch を共有するための取得口。"""
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function getData(courseId) {")
        assert "loadLandscape(" in block

    def test_learner_api_path_matches_contract(self):
        """§9.2: GET /api/learning/courses/{course_id}/landscape。"""
        src = _read(LANDSCAPE_JS)
        assert '"/learning/courses/" + encodeURIComponent(courseId) + "/landscape"' in src


# ===========================================================================
# 3. atlas-overlay.js への結線（3行のフック + LIMITS のみ）
# ===========================================================================


class TestAtlasOverlayHooks:
    def test_mount_controls_hook(self):
        src = _read(ATLAS_OVERLAY_JS)
        assert "if (window.LandscapeLayer) window.LandscapeLayer.mountControls(sheet);" in src

    def test_on_level_rendered_hook(self):
        src = _read(ATLAS_OVERLAY_JS)
        assert (
            "if (window.LandscapeLayer) window.LandscapeLayer.onLevelRendered(level, canvas);"
            in src
        )

    def test_on_overlay_closed_hook(self):
        src = _read(ATLAS_OVERLAY_JS)
        assert "if (window.LandscapeLayer) window.LandscapeLayer.onOverlayClosed();" in src

    def test_hooks_are_guarded_and_exactly_three(self):
        """未読込環境で壊れないよう全て if (window.LandscapeLayer) でガードし、
        呼び出しは3箇所のみ（それ以上の侵入をしていない）。"""
        src = _read(ATLAS_OVERLAY_JS)
        calls = re.findall(r"window\.LandscapeLayer\.(\w+)\(", src)
        assert sorted(calls) == ["mountControls", "onLevelRendered", "onOverlayClosed"], calls
        guards = re.findall(r"if \(window\.LandscapeLayer\) window\.LandscapeLayer\.", src)
        assert len(guards) == 3

    def test_hooks_sit_next_to_personal_map_hooks(self):
        """PersonalMap の3フックと同じ seam に並べる（新しい呼び出し箇所を作らない）。"""
        src = _read(ATLAS_OVERLAY_JS)
        for method in ("mountControls", "onLevelRendered", "onOverlayClosed"):
            personal = src.index(f"window.PersonalMap.{method}(")
            landscape = src.index(f"window.LandscapeLayer.{method}(")
            assert 0 < landscape - personal < 400, (
                f"{method} のフックが PersonalMap の隣にありません"
            )

    def test_l1_regions_limit_raised_to_12(self):
        """§6.2: 骨格の領域数上限 7 → 12（backend/core/atlas.py MAX_REGIONS と同期）。"""
        src = _read(ATLAS_OVERLAY_JS)
        assert "l1Regions: 12" in src
        assert "l1Regions: 7" not in src

    def test_limits_line_keeps_other_values(self):
        src = _read(ATLAS_OVERLAY_JS)
        line = next(ln for ln in src.splitlines() if "const LIMITS = {" in ln)
        assert "l1Regions: 12" in line
        assert "l1ConceptsPerRegion: 6" in line
        assert "l2Nodes: 20" in line
        assert "l3Nodes: 12" in line


# ===========================================================================
# 4. fail-closed / ポーリング禁止（LS6 / LS9）
# ===========================================================================


class TestFailClosedAndNoPolling:
    def test_non_ok_response_returns_null(self):
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function loadLandscape(courseId) {")
        assert "if (!res.ok) return null;" in block, (
            "非 ok 応答が null に丸められていません（fail-closed）"
        )
        assert ".catch(" in block, "取得失敗の catch がありません（fail-closed）"

    def test_no_fixture_fallback(self):
        """LS6: 取得失敗時にフィクスチャへ退避しない（atlas-data.js の fail-closed と同型）。"""
        src = _strip_js_comments(_read(LANDSCAPE_JS))
        assert "ATLAS_FIXTURE" not in src
        assert "AtlasFixture" not in src

    def test_no_polling(self):
        src = _strip_js_comments(_read(LANDSCAPE_JS))
        assert "setInterval" not in src, "ポーリング禁止（LS9）"

    def test_toggle_requires_course_and_token(self):
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function onToggleChange(checked) {")
        assert "if (!courseId || !token()) {" in block
        assert "checked = false" in block

    def test_controls_hidden_until_data_exists(self):
        """配置データが無ければトグルごと出さない（§10.1「データがある時だけ表示」）。"""
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function refreshContext() {")
        assert "hasPlacements(" in block
        assert "hidden = true" in block

    def test_double_mount_guard(self):
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function mountControls(sheetEl) {")
        assert "state.controlsEl) return" in block, "二重マウントガードがありません"

    def test_stale_course_responses_discarded(self):
        """コース切替後に届いた遅延応答を別コースの UI に描かない。"""
        src = _read(LANDSCAPE_JS)
        assert src.count("if (courseId !== state.courseId) return;") >= 2


# ===========================================================================
# 5. 数値非表示（LS5）とラベル駆動の描画
# ===========================================================================


class TestNoNumericExposure:
    def test_renders_from_labels(self):
        src = _read(LANDSCAPE_JS)
        for key in ("perspective_label", "weight_label", "provenance_label"):
            assert key in src, f"{key} を使った描画がありません"

    def test_never_reads_raw_weight(self):
        src = _strip_js_comments(_read(LANDSCAPE_JS))
        assert not re.search(r"\.weight\b", src), "生の weight を参照しています（LS5 違反）"
        assert not re.search(r'"weight"', src)

    def test_never_reads_confidence(self):
        src = _strip_js_comments(_read(LANDSCAPE_JS))
        assert "confidence" not in src, "confidence を参照しています（LS5 違反）"

    def test_app_js_section_never_reads_raw_weight(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function buildPaperPlacementHtml(data, placedDocs) {")
        assert not re.search(r"\.weight\b", block), "出典タブが生の weight を参照しています"
        assert "confidence" not in block

    def test_evidence_quote_is_rendered(self):
        """LS4: 原文逐語 quote へ遡れる。"""
        src = _read(LANDSCAPE_JS)
        assert "ev.quote" in src
        assert "reason" in src


# ===========================================================================
# 6. 地図の安定性（LS7）: 1レイヤー追加のみ
# ===========================================================================


class TestNonDestabilizingLayer:
    def test_removes_prior_layer_and_appends_one_group(self):
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function renderMarkerLayer(canvasEl) {")
        assert 'querySelector(".landscape-layer")' in block
        assert "existing.remove();" in block
        assert 'svgEl("g", { class: "landscape-layer" })' in block
        assert "svg.appendChild(layer);" in block

    def test_level_one_only(self):
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function renderMarkerLayer(canvasEl) {")
        assert "state.lastLevel !== 1" in block

    def test_reads_coordinates_from_overlay_level1(self):
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function renderMarkerLayer(canvasEl) {")
        assert 'overlayData.levels && overlayData.levels["1"]' in block
        assert "levelData.nodes" in block
        assert "levelData.regions" in block

    def test_region_placements_positioned_inside_region_box(self):
        """node_kind = region の配置は領域の箱（x/y/w/h）の内側に置く。"""
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(
            src, "function anchorPoint(nodeId, posById, regionById) {"
        )
        assert "region.x + region.w" in block
        assert "region.y" in block

    def test_does_not_touch_minimap(self):
        src = _read(LANDSCAPE_JS)
        assert "AtlasMinimap" not in src, "ミニマップに触っています（LS7 違反）"

    def test_aggregates_over_four_documents(self):
        """同一ノードに5件以上あるときは1個のマーカー + 「+N」に集約する（§10.1）。"""
        src = _read(LANDSCAPE_JS)
        assert "MAX_MARKERS_PER_NODE" in src
        block = _extract_function_body(src, "function renderMarkerLayer(canvasEl) {")
        assert "entries.length > MAX_MARKERS_PER_NODE" in block
        assert '"+" + entries.length' in block

    def test_multiple_documents_offset_horizontally(self):
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function renderMarkerLayer(canvasEl) {")
        assert "i * MARKER_STEP" in block


# ===========================================================================
# 7. ポップオーバー（src-popup と同型の閉じ方）
# ===========================================================================


class TestPopoverDiscipline:
    def test_own_element_id(self):
        src = _read(LANDSCAPE_JS)
        assert 'popup.id = "landscape-popup";' in src
        # app.js の src-popup（id・DOM）を流用しない（id 衝突・相互破壊の防止）
        assert "src-popup" not in _strip_js_comments(src)

    def test_outside_click_and_escape_close(self):
        src = _read(LANDSCAPE_JS)
        assert 'document.addEventListener("mousedown", onDocMouseDownClosePopup, true);' in src
        assert 'document.addEventListener("keydown", onDocKeyDownClosePopup, true);' in src
        block = _extract_function_body(src, "function closePopup() {")
        assert 'document.removeEventListener("mousedown", onDocMouseDownClosePopup, true);' in block
        assert 'document.removeEventListener("keydown", onDocKeyDownClosePopup, true);' in block

    def test_escape_key_handler(self):
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function onDocKeyDownClosePopup(e) {")
        assert 'e.key === "Escape"' in block

    def test_popup_shows_title_and_corpus_fact(self):
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function showPopup(entries, evt) {")
        assert "entry.title" in block
        assert "corpusFactText(" in block

    def test_overlay_closed_cleans_popup(self):
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function onOverlayClosed() {")
        assert "closePopup();" in block


# ===========================================================================
# 8. コーパスの正直さ（LS8）
# ===========================================================================


class TestCorpusHonesty:
    def test_corpus_fact_built_from_contract_fields(self):
        """§9.2 の corpus / domains から組む（存在しないフィールドを発明しない）。"""
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function corpusFactText(data) {")
        assert "corpus.source_document_count" in block
        assert "skeletonVersionText(" in block
        assert "includesInferred(" in block
        assert "登録済み論文 " in block

    def test_skeleton_version_prefers_course_map_domain(self):
        src = _read(LANDSCAPE_JS)
        block = _extract_function_body(src, "function skeletonVersionText(data) {")
        assert "is_course_map" in block
        assert "frozen_version" in block

    def test_app_js_repeats_the_same_fact_line(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function paperPlacementCorpusFact(data) {")
        assert "source_document_count" in block
        assert "骨格版 " in block
        assert "AI推定を含む" in block


# ===========================================================================
# 9. 出典タブ「分野の中の位置づけ」（§10.2）
# ===========================================================================


class TestSourcesTabSection:
    def test_section_container_with_ui_anchor(self):
        js = _read(APP_JS)
        assert 'data-ui-anchor="sources.paper-placement"' in js
        assert 'id="lx-paper-placement"' in js

    def test_container_emitted_by_render_sources_tab_after_registered_materials(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderSourcesTab() {")
        assert 'id="lx-paper-placement"' in block
        # 「登録済み教材」ブロックの直後（§10.2）
        idx_materials = block.index("<h4>登録済み教材</h4>")
        idx_section = block.index('id="lx-paper-placement"')
        assert idx_materials < idx_section

    def test_render_sources_tab_calls_async_renderer(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderSourcesTab() {")
        assert "renderPaperPlacementSection(el);" in block

    def test_renderer_uses_shared_landscape_getter(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderPaperPlacementSection(root) {")
        assert "window.LandscapeLayer.getData(courseId)" in block
        assert "if (courseId !== state.courseId) return;" in block

    def test_section_removed_when_no_data(self):
        """データ無し・取得失敗はセクションごと消す（LS6 fail-closed）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderPaperPlacementSection(root) {")
        assert block.count("box.remove()") >= 2
        assert "!window.LandscapeLayer" in block

    def test_section_renders_chips_labels_and_details(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function buildPaperPlacementHtml(data, placedDocs) {")
        assert "分野の中の位置づけ" in block
        assert "node_label" in block
        assert "perspective_label" in block
        assert "weight_label" in block
        assert "provenance_label" in block
        assert "<details" in block
        assert "p.reason" in block
        assert "ev.quote" in block

    def test_section_escapes_server_text(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function buildPaperPlacementHtml(data, placedDocs) {")
        assert "escHtml(doc.title" in block
        assert "escHtml(p.reason)" in block
        assert "escHtml(ev.quote)" in block

    def test_course_switch_invalidates_cache(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function switchCourse(courseId) {")
        assert "window.LandscapeLayer.invalidate();" in block


# ===========================================================================
# 9b. 配置ゼロの事実文（category_gap_candidates_design.md §4.5 / AB1 / LS10）
# ===========================================================================


class TestUnplacedFactLine:
    """「置けなかった」を取得失敗と同じ沈黙に潰さないための表示（§4.5 の裁定）。

    - 事実文はリテラルで固定する（言い換えで欠陥語彙・行動喚起に滑らせない）
    - アイコン・エラー色・「追加してください」等の誘導を付けない（AB1）
    - 節内に配置済みが1件も無い場合は節ごと非表示のまま（fail-closed は不変）
    """

    FACT_LITERAL = "どの領域にも配置されていません"

    def test_fact_line_literal_exists(self):
        js = _read(APP_JS)
        assert self.FACT_LITERAL in js, "配置ゼロの事実文がありません（§4.5）"
        assert "この論文は、現在の分野の地図（版 " in js

    def test_fact_line_is_built_from_contract_field(self):
        """サーバの ``unplaced_documents`` から組む（クライアントで差集合を再計算しない）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function buildPaperPlacementUnplacedHtml(data) {")
        assert "unplaced_documents" in block
        assert self.FACT_LITERAL in block
        assert "escHtml(doc.title" in block

    def test_fact_line_rendered_inside_the_section(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function buildPaperPlacementHtml(data, placedDocs) {")
        assert "buildPaperPlacementUnplacedHtml(data)" in block

    def test_version_comes_from_shared_helper(self):
        """版は「いま見せている地図」の版（サーバの skeleton_version を優先）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function paperPlacementSkeletonVersion(data) {")
        assert "skeleton_version" in block
        assert "is_course_map" in block
        assert "frozen_version" in block
        # コーパス事実行と同じ版を使う（表示される版が2箇所で食い違わない）
        fact = _extract_function_body(js, "function paperPlacementCorpusFact(data) {")
        assert "paperPlacementSkeletonVersion(data)" in fact

    def test_no_line_without_a_known_version(self):
        """版が引けないときは何も出さない（推測した版番号を事実文に書かない）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function buildPaperPlacementUnplacedHtml(data) {")
        assert "if (!version) return \"\";" in block
        assert "if (!unplaced.length) return \"\";" in block

    def test_section_stays_hidden_when_nothing_is_placed(self):
        """配置済みゼロなら節ごと非表示のまま（データ無しと取得失敗を区別できない）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderPaperPlacementSection(root) {")
        assert "if (!placed.length) { box.remove(); return; }" in block

    def test_no_error_styling_or_icons(self):
        """AB1: エラー色・警告アイコン・強調クラスを付けない（通常テキストのみ）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function buildPaperPlacementUnplacedHtml(data) {")
        for banned in (
            "⚠", "❗", "❌", "🚨", "警告", "error", "danger", "alert", "warn",
            "color:var(--color-danger", "color: var(--color-danger",
        ):
            assert banned not in block, f"配置ゼロの行に {banned} を付けています（AB1 違反）"
        # 既存の中立クラスだけを使う（新しい強調スタイルを持ち込まない）
        classes = set(re.findall(r'class="([a-zA-Z0-9_\- ]+)"', block))
        assert classes <= {"lx-placement-doc", "lx-placement-title", "lx-placement-fact"}

    def test_no_call_to_action_or_deficiency_wording(self):
        """AB1 / LS1: 埋めさせる誘導・欠陥語彙を使わない。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function buildPaperPlacementUnplacedHtml(data) {")
        for banned in (
            "追加してください", "登録してください", "してください", "不足", "未整備",
            "欠け", "穴", "改善", "<button",
        ):
            assert banned not in block, f"配置ゼロの行に {banned}（AB1 / LS1 違反）"

    def test_no_numeric_exposure_in_the_fact_line(self):
        """LS5: 件数バッジ・weight・confidence を出さない。"""
        js = _strip_js_comments(_read(APP_JS))
        block = _extract_function_body(js, "function buildPaperPlacementUnplacedHtml(data) {")
        assert "confidence" not in block
        assert not re.search(r"\.weight\b", block)
        assert "unplaced.length +" not in block
        assert "件" not in block

    def test_no_gap_or_candidate_vocabulary_in_learner_ui(self):
        """候補機構（教員側）の語彙を学習者 UI に持ち込まない。"""
        js = _strip_js_comments(_read(APP_JS))
        block = _extract_function_body(js, "function buildPaperPlacementUnplacedHtml(data) {")
        for banned in ("候補", "gap", "candidate"):
            assert banned not in block, f"学習者 UI に {banned} が出ています"


# ===========================================================================
# 10. 学習者ヘルプアンカー（正本 = core/help_kb/ui_anchors.py）
# ===========================================================================


class TestLearnerHelpAnchor:
    def test_anchor_registered_in_known_ids(self):
        assert LEARNER_ANCHOR_ID in KNOWN_UI_ANCHOR_IDS

    def test_anchor_mapped_to_student_manual_section(self):
        assert UI_ANCHORS.get(LEARNER_ANCHOR_ID) == "student/02-student.md#paper-placement"

    def test_manual_section_exists_with_explicit_anchor(self):
        md = _read(ROOT / "docs" / "manual" / "student" / "02-student.md")
        assert "{#paper-placement}" in md
        assert "分野の中の位置づけ" in md
        # AI推定と教員確認済みの区別（AC-005）・評価に使われないこと・非表示になり得ること
        assert "AIによる推定（未確認）" in md
        assert "教員確認済み" in md
        assert "成績評価には" in md

    def test_frontend_anchor_value_is_known(self):
        js = _read(APP_JS)
        used = set(re.findall(r'data-ui-anchor="([a-zA-Z0-9_.\-]+)"', js))
        assert LEARNER_ANCHOR_ID in used
        assert not (used - set(KNOWN_UI_ANCHOR_IDS))


# ===========================================================================
# 11. CSS（§10.4）
# ===========================================================================


class TestStyles:
    def test_atlas_css_has_marker_and_popup_styles(self):
        css = _read(ATLAS_CSS)
        for selector in (
            ".landscape-controls",
            ".landscape-toggle",
            ".landscape-fact",
            ".landscape-marker",
            ".landscape-marker-badge",
            ".landscape-popup",
            ".landscape-popup-quote",
            ".landscape-provenance",
        ):
            assert selector in css, f"atlas.css に {selector} のスタイルがありません"

    def test_styles_css_has_sources_tab_chip_styles(self):
        css = _read(STYLES_CSS)
        for selector in (
            ".lx-placement-doc",
            ".lx-placement-chip",
            ".lx-placement-prov",
            ".lx-placement-quote",
            ".lx-placement-fact",
        ):
            assert selector in css, f"styles.css に {selector} のスタイルがありません"


# ===========================================================================
# 12. 文言（LS1: 断定しない）
# ===========================================================================


class TestWording:
    def test_no_ranking_or_score_wording_in_learner_ui(self):
        src = _strip_js_comments(_read(LANDSCAPE_JS))
        js_block = _extract_function_body(
            _read(APP_JS), "function buildPaperPlacementHtml(data, placedDocs) {"
        )
        for banned in ("スコア", "正答率", "偏差", "ランキング", "点数"):
            assert banned not in src, f"landscape-layer.js に禁止語彙 {banned}"
            assert banned not in js_block, f"出典タブに禁止語彙 {banned}"

    def test_multiple_regions_are_presented_as_normal(self):
        """LS1: 1論文が複数領域に載るのは既定。「正しい分類」と断定しない。"""
        js_block = _extract_function_body(
            _read(APP_JS), "function buildPaperPlacementHtml(data, placedDocs) {"
        )
        assert "複数の領域" in js_block
