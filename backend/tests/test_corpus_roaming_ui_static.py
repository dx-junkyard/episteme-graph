"""コーパス回遊層（学習者フロント）の静的ガードレール。

正本設計書: ``docs/features/corpus_roaming_design.md``（§4.2 UI / §5 コース無し論文議論 /
§6 地図の端 / §7 関心信号）。検査は他の ``*_ui_static.py`` と同じ流儀で、ソース文字列の
部分一致・関数本体の抽出のみを行う（実サーバ・実DOM不使用）。

固定する不変条項:

- **CR2 既存のコース学習を壊さない** — 「論文の海」はサイドバーに**並置される**別の入口で
  あり、コース側の DOM（#material-body / #chat-area / #chat-input …）に触らない。
  discuss.js の開幕画面はコース文脈では従来どおりのパスで動く（第2引数が文字列のとき、
  取得口・キャッシュキー・観測イベントは一切変わらない）。
- **CR3 数値を見せない** — weight / confidence / score / 件数の描画が無い。配置の出所は
  サーバの ``source_label`` をそのまま出す。
- **CR4 閉世界の正直さ** — 端の文言（``fact_line``）はサーバが唯一の正本。クライアントで
  組み立てない・言い換えない。閉世界 denylist（「世界初」「未踏」等）が出現しない。
- **CR5 好奇心の文法** — 自動表示・ポーリング・バッジをしない。関心タップは明示ボタンのみ。
- **CR6 学習者を監視しない** — frontier-interest の POST は明示ボタンのハンドラからだけ。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "public"
CORPUS_JS = FRONTEND / "js" / "corpus-sea.js"
APP_JS = FRONTEND / "js" / "app.js"
DISCUSS_JS = FRONTEND / "js" / "discuss.js"
INDEX_HTML = FRONTEND / "index.html"
STYLES_CSS = FRONTEND / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(src: str) -> str:
    """コメント（ブロック / 行頭の行コメント）を落としたコード部分だけを返す。

    禁止語の検査は**実装**に対して行う（設計意図をコメントに書けなくなるのを避ける）。
    """
    out: list[str] = []
    i = 0
    while i < len(src):
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = len(src) if end == -1 else end + 2
            continue
        nl = src.find("\n", i)
        line_end = len(src) if nl == -1 else nl + 1
        line = src[i:line_end]
        if not line.lstrip().startswith("//"):
            out.append(line)
        i = line_end
    return "".join(out)


def _extract_function_body(src: str, signature: str) -> str:
    """`signature` から対応する閉じ `}` までを素朴な波括弧カウントで抽出する。"""
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


class TestModulePresence:
    """モジュールの存在と読み込み（§4.2）。"""

    def test_corpus_sea_js_exists(self):
        assert CORPUS_JS.exists(), "frontend/public/js/corpus-sea.js が無い"

    def test_public_contract_is_a_single_window_object(self):
        js = _read(CORPUS_JS)
        assert "window.CorpusSea = {" in js
        for name in ("init:", "open:", "close:", "invalidate:"):
            assert name in js, f"window.CorpusSea に {name} が無い"

    def test_index_html_loads_corpus_sea_after_discuss(self):
        """開幕画面は discuss.js を document 文脈で再利用するため、その後に読む。"""
        html = _read(INDEX_HTML)
        assert "/js/corpus-sea.js" in html
        assert html.index("/js/discuss.js") < html.index("/js/corpus-sea.js")

    def test_css_namespace_defined(self):
        css = _read(STYLES_CSS)
        for selector in (
            ".corpus-sea-entry-btn",
            ".corpus-sea-overlay",
            ".corpus-sea-edge",
            ".corpus-sea-frontier-btn",
            ".corpus-sea-discuss",
            ".corpus-sea-anchor-dot",
            ".corpus-sea-density-legend",
            ".corpus-sea-filter-clear",
        ):
            assert selector in css, f"missing CSS selector: {selector}"


class TestSidebarEntry:
    """§4.2: サイドバーの常設ボタン。コース未選択でも押せる（CR2: 並置であって置換でない）。"""

    def test_entry_button_html_defined(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function corpusSeaEntryHtml() {")
        assert 'id="corpus-sea-btn"' in block
        assert "論文の海" in block

    def test_entry_is_rendered_without_a_selected_course(self):
        """コース未選択の早期 return 分岐にも入口を出す。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderSidebar() {")
        head = block[: block.index("const course = state.course;")]
        assert "corpusSeaEntryHtml()" in head
        assert "bindCorpusSeaEntry();" in head

    def test_entry_is_rendered_with_a_selected_course_too(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderSidebar() {")
        assert block.count("corpusSeaEntryHtml()") >= 2
        assert block.count("bindCorpusSeaEntry();") >= 2

    def test_entry_opens_only_on_click(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function bindCorpusSeaEntry() {")
        assert 'addEventListener("click"' in block
        assert "window.CorpusSea.open()" in block

    def test_existing_two_mode_switch_is_untouched(self):
        """CR2: コースの二枚看板（順番に学ぶ／論文と議論）は不変で並置される。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderSidebar() {")
        assert ">順番に学ぶ</button>" in block
        assert ">論文と議論</button>" in block
        assert block.count('class="discuss-mode-btn') >= 2


class TestDomainDtoTolerance:
    """ドメイン一覧の DTO は表示名・bool の綴りが2通りありうる（route 層の投影名と
    core の生成名）。どちらでも受け、無ければ domain_key を出す（作らない）。"""

    def test_label_falls_back_without_inventing_a_name(self):
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function domainLabelOf(entry) {")
        assert "entry.label" in block
        assert "entry.domain_name" in block
        assert "entry.domain_key" in block

    def test_has_papers_is_a_bool_never_a_count(self):
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function domainHasPapers(entry) {")
        assert "entry.has_placements" in block
        assert "entry.has_visible_papers" in block
        assert "length" not in block, "件数から真偽を作らない（CR3）"


class TestNoAutoOpenAndNoPolling:
    """CR5: 自動表示しない・ポーリングしない・バッジを出さない。"""

    def test_no_timers_in_corpus_sea(self):
        js = _code(_read(CORPUS_JS))
        for banned in ("setInterval", "setTimeout", "requestAnimationFrame"):
            assert banned not in js, f"corpus-sea.js に {banned} がある（ポーリング/自動表示の温床）"

    def test_open_is_not_called_on_boot(self):
        js = _read(CORPUS_JS)
        assert "DOMContentLoaded" not in js, "自前の起動フックから自動で開かない"
        init_block = _extract_function_body(js, "function init(deps) {")
        assert "open(" not in init_block, "init() から開かない"

    def test_app_js_never_auto_opens_corpus_sea(self):
        js = _read(APP_JS)
        # open() の呼び出しはクリックハンドラ1箇所だけ（初回ログイン導線・cue を作らない）
        assert js.count("window.CorpusSea.open()") == 1
        init_block = _extract_function_body(js, "async function initApp() {")
        assert "window.CorpusSea.init({})" in init_block
        assert "window.CorpusSea.open()" not in init_block

    def test_no_badge_or_count_markup(self):
        """回答チップ（grounding-badge / tier-badge）はコース版と同じものを再利用するが、
        回遊の入口・地図・端に「未読 N 件」の類のバッジを作らない（CR5）。"""
        js = _code(_read(CORPUS_JS))
        for banned in ("corpus-sea-badge", "未読", "新着", "件の論文", "NEW"):
            assert banned not in js, f"corpus-sea.js にバッジ/件数表現 {banned!r} がある"


class TestServerStringsArePassedThrough:
    """CR4: 端の文言はサーバが正本。クライアントで作らない・言い換えない。"""

    def test_fact_lines_are_rendered_verbatim(self):
        js = _read(CORPUS_JS)
        assert "esc(f.fact_line" in js, "縁の fact_line を素通しで描いていない"
        assert "esc(outer.fact_line" in js, "外の fact_line を素通しで描いていない"

    def test_client_does_not_compose_edge_wording(self):
        """サーバ側の固定文・テンプレート断片がクライアントに複製されていない。"""
        js = _code(_read(CORPUS_JS))
        for fragment in (
            "まだ地図に置かれていない主題",   # core/corpus_view.py FACT_FRINGE
            "arXiv",                          # 外の輪のテンプレート（時点付き）
            "時点）",
            "検索条件",
        ):
            assert fragment not in js, f"閉世界の文言 {fragment!r} をクライアントで組み立てている"

    def test_closed_world_denylist_absent(self):
        js = _code(_read(CORPUS_JS))
        for banned in ("世界初", "未踏", "誰も", "この分野には論文がない", "存在しません"):
            assert banned not in js, f"閉世界の断定語 {banned!r} が出現している"

    def test_source_label_is_rendered_verbatim(self):
        """CR3: 配置の出所はサーバの source_label をそのまま出す（自前の対応表を作らない）。"""
        js = _read(CORPUS_JS)
        assert "esc(p.source_label)" in js
        js = _code(js)
        for banned in ("AIによる推定", "教員確認済み"):
            assert banned not in js, f"出所ラベル {banned!r} をクライアントで組み立てている"


class TestNoNumbersShown:
    """CR3: weight / confidence / 件数 / 類似度を描かない。"""

    def test_no_numeric_projection_keys(self):
        js = _code(_read(CORPUS_JS))
        for banned in ("weight", "confidence", "score", "similarity", "percent", "%"):
            assert banned not in js, f"数値キー {banned!r} を参照/描画している"

    def test_anchor_density_is_drawn_as_one_binned_dot(self):
        """アンカーに置かれた論文は円1つで表す。件数の羅列も「+N」も作らない。"""
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function renderMap() {")
        assert "data-corpus-anchor" in block, "アンカー円に識別属性が無い"
        assert "anchorRadius(" in block, "半径を段階ビン関数から取っていない"
        for banned in ('"+" + entries.length', "entries.length *", "Math.sqrt"):
            assert banned not in block, f"連続比例・件数表示 {banned!r} がある"


class TestDensityIsGradedNotNumeric:
    """情報密度の段階表現（案A）。段階ビンだけを描き、連続比例・数値は描かない（CR3）。"""

    def _decl_line(self, js: str, name: str) -> str:
        for line in js.splitlines():
            if name in line and "=" in line and "const" in line:
                return line
        raise AssertionError(f"定数宣言 {name} が無い")

    def test_region_density_has_exactly_four_fixed_bins(self):
        js = _read(CORPUS_JS)
        assert "[1, 3, 6]" in self._decl_line(js, "REGION_DENSITY_THRESHOLDS")
        block = _extract_function_body(js, "function regionDensityLevel(count) {")
        assert "REGION_DENSITY_THRESHOLDS" in block
        for banned in ("Math.sqrt", "Math.log", "Math.pow", "/ total", "* count"):
            assert banned not in block, f"段階ビンでない連続計算 {banned!r} がある"

    def test_region_classes_are_the_four_graded_steps(self):
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function renderMap() {")
        assert '"corpus-sea-region d" + regionDensityLevel(' in block
        css = _read(STYLES_CSS)
        for cls in (".corpus-sea-region.d1", ".corpus-sea-region.d2", ".corpus-sea-region.d3"):
            assert cls in css, f"密度クラス {cls} の定義が無い"

    def test_fog_regions_keep_their_own_expression(self):
        """霧の領域は骨格側の表現。密度クラスを重ねない（分岐で切り分ける）。"""
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function renderMap() {")
        assert 'const fog = r.kind === "fog";' in block
        assert '? "corpus-sea-region fog"' in block

    def test_anchor_radius_comes_from_fixed_bins(self):
        js = _read(CORPUS_JS)
        assert "[1, 2, 4]" in self._decl_line(js, "ANCHOR_RADIUS_THRESHOLDS")
        assert "[6, 9, 12]" in self._decl_line(js, "ANCHOR_RADII")
        block = _extract_function_body(js, "function anchorRadius(count) {")
        assert "ANCHOR_RADII" in block
        for banned in ("Math.sqrt", "Math.log", "Math.pow", "* count", "count *"):
            assert banned not in block, f"段階ビンでない連続計算 {banned!r} がある"

    def test_counts_never_reach_the_dom(self):
        """段階に落とす前の数は描画にも aria にも出さない。"""
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function renderMap() {")
        for banned in ("countOf(density.anchors, anchorId) +", "+ countOf("):
            assert banned not in block, "件数を文字列に連結している"
        anchor = _extract_function_body(js, "function selectAnchor(anchorId) {")
        assert "length" not in anchor, "絞り込みで件数を作らない"

    def test_legend_labels_are_words_not_numbers(self):
        js = _read(CORPUS_JS)
        line = self._decl_line(js, "DENSITY_LEGEND_LABELS")
        for label in ("なし", "少しある", "蓄積あり", "厚い"):
            assert label in line, f"凡例の段階ラベル {label} が無い"
        assert not any(ch.isdigit() for ch in line), "凡例のラベルに数字がある（CR3）"
        assert "閲覧できる論文の厚み" in self._decl_line(js, "DENSITY_LEGEND_TITLE")

    def test_legend_is_rendered_only_with_a_map(self):
        """骨格が無いときは地図ごと出さないので、凡例もそこに出ない（fail-closed）。"""
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function renderMap() {")
        head = block[: block.index("if (!lvl) {")]
        assert "densityLegendHtml()" not in head
        assert "box.innerHTML = svg + densityLegendHtml();" in block


class TestAnchorFilter:
    """アンカー円は右の論文リストを絞るだけ。地図・端・詳細の見え方は変えない（CR5）。"""

    def test_clicking_an_anchor_filters_the_paper_list(self):
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function selectAnchor(anchorId) {")
        assert "state.anchorFilter" in block
        assert "renderPapers();" in block
        for banned in ("renderMap()", "renderEdges()", "renderDetail()"):
            assert banned not in block, f"絞り込みが {banned} まで書き換えている"

    def test_filter_can_be_cleared(self):
        js = _read(CORPUS_JS)
        assert "絞り込みを解除" in js
        block = _extract_function_body(js, "function clearAnchorFilter() {")
        assert "state.anchorFilter = null;" in block
        papers = _extract_function_body(js, "function renderPapers() {")
        assert "corpus-sea-filter-clear" in papers
        assert "clearAnchorFilter" in papers

    def test_filtered_heading_names_the_place_without_a_count(self):
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function renderPapers() {")
        assert "に置かれている論文" in block
        for banned in ("rows.length +", "+ rows.length", "docIds).length"):
            assert banned not in block, f"見出しに件数を連結している（{banned!r}）"

    def test_filter_is_reset_on_domain_switch_and_invalidate(self):
        js = _read(CORPUS_JS)
        for signature in (
            "async function selectDomain(domainKey) {",
            "function invalidate() {",
        ):
            block = _extract_function_body(js, signature)
            assert "state.anchorFilter = null;" in block, f"{signature} で絞り込みを残している"


class TestFrontierInterest:
    """§7 / CR6: 端への関心は明示タップのときだけ記録する。"""

    def test_post_is_only_reachable_from_the_explicit_toggle(self):
        js = _read(CORPUS_JS)
        assert js.count('"/learning/corpus/frontier-interest"') == 1
        block = _extract_function_body(
            js, "async function toggleFrontierInterest(ring, regionId, btn) {"
        )
        assert '"/learning/corpus/frontier-interest"' in block
        assert 'method: "POST"' in block

    def test_button_is_bound_to_a_click_handler(self):
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function renderEdges() {")
        assert "data-corpus-ring" in block
        assert 'addEventListener("click"' in block

    def test_withdraw_is_a_state_transition_not_a_delete(self):
        """CR8: 取り消しは withdraw（状態遷移）。DELETE の行削除 API を叩かない。"""
        js = _read(CORPUS_JS)
        block = _extract_function_body(
            js, "async function toggleFrontierInterest(ring, regionId, btn) {"
        )
        assert "/withdraw" in block
        assert 'method: "DELETE"' not in block

    def test_ring_vocabulary_matches_backend(self):
        js = _read(CORPUS_JS)
        assert '"fringe"' in js
        assert '"outer"' in js

    def test_pressing_does_not_change_what_is_shown(self):
        """CR5: 押した結果で提示内容を変えない（変わるのはボタン自身のラベルだけ）。"""
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function frontierButtonHtml(ring, regionId) {")
        assert "この先を知りたい" in block
        assert "気になるに追加済み（取り消す）" in block
        # 端カードの本文（fact_line / paper_titles）は関心状態を参照しない
        edges = _extract_function_body(js, "function renderEdges() {")
        assert "state.interest" not in edges


class TestDocumentDiscussReusesCourseDiscussUi:
    """§5: 既存の discuss UI を document 文脈で再利用する（コース版の挙動・DOM は不変）。"""

    def test_opening_is_rendered_by_discuss_js(self):
        js = _read(CORPUS_JS)
        assert "window.Discuss.renderOpening(" in js, "開幕画面を自前で再実装している"

    def test_discuss_js_signature_is_unchanged(self):
        """コース版の呼び出し形（文字列 courseId）を壊さない。"""
        js = _read(DISCUSS_JS)
        assert "async function renderOpening(containerEl, courseId) {" in js
        app = _read(APP_JS)
        assert "window.Discuss.renderOpening(body, state.courseId)" in app

    def test_context_switch_is_a_single_normalizer(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function normalizeOpeningContext(arg) {")
        assert '"/learning/courses/"' in block
        assert '"/learning/documents/"' in block
        assert 'kind: "document"' in block
        assert 'kind: "course"' in block

    def test_course_context_keeps_its_own_state(self):
        """CR2: document 文脈の描画がコース discuss セッションの文脈を壊さない。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function renderOpening(containerEl, courseId) {")
        assert 'if (cx.kind === "course") ctx.courseId = cx.courseId;' in block

    def test_document_context_does_not_emit_course_metrics(self):
        """観測イベントは course_id を持つ計器。document 文脈から送らない（偽の帰属を作らない）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function sendDiscussMetric(event, payload) {")
        assert 'if (ctx.kind === "document") return;' in block

    def test_document_context_is_exited_explicitly(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function exitDocumentContext() {")
        assert "ctx.courseId" not in block, "コース側の状態に触れてはいけない（CR2）"
        corpus = _read(CORPUS_JS)
        assert "window.Discuss.exitDocumentContext()" in corpus

    def test_document_endpoints_match_the_contract(self):
        js = _read(CORPUS_JS)
        assert '"/learning/documents/"' in js
        for suffix in ("/discuss/chat", "/discuss/history", "/discuss/messages/"):
            assert suffix in js, f"契約の経路 {suffix} が無い"

    def test_scope_values_match_backend_contract(self):
        """値は契約のまま（course_sources 既定）。ラベルだけ document 文脈に合わせる。"""
        js = _read(CORPUS_JS)
        assert '{ value: "course_sources", label: "この論文のみ" }' in js
        assert '{ value: "all_visible", label: "閲覧できる周辺資料まで" }' in js

    def test_mode_bar_says_it_is_outside_a_course(self):
        js = _read(CORPUS_JS)
        assert "論文と議論中（コース外）" in js

    def test_course_only_features_are_not_called_in_document_context(self):
        """§5.4: 着地画面・tension/anchor digest は document 文脈では呼ばない。"""
        js = _code(_read(CORPUS_JS))
        for banned in ("maybeShowLanding", "tension/digest", "anchors/digest", "cycle/intention"):
            assert banned not in js, f"コース専用機構 {banned} を document 文脈から呼んでいる"

    def test_corpus_sea_does_not_touch_course_dom(self):
        """CR2: コース学習画面の DOM を書き換えない（別オーバーレイに閉じる）。"""
        js = _read(CORPUS_JS)
        for banned in (
            '"material-body"', '"chat-area"', '"chat-input"', '"sidebar"',
            '"mode-bar"', '"discuss-bar"', '"material-region"',
        ):
            assert banned not in js, f"コース側の DOM {banned} を参照している"

    def test_edit_and_delete_use_truncate_semantics(self):
        """書き直し・削除はコース版と同じ truncate セマンティクス。"""
        js = _read(CORPUS_JS)
        send = _extract_function_body(js, "async function sendDiscussMessage(text) {")
        assert "replace_message_id" in send
        delete_block = _extract_function_body(js, "async function deleteFrom(msgId) {")
        assert 'method: "DELETE"' in delete_block


class TestFailClosed:
    """CR1 と同族: 取得失敗・骨格なしは領域ごと非表示。作り話・代替データで埋めない。"""

    def test_fetch_helper_swallows_errors_into_null(self):
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "async function getJson(path) {")
        assert "if (!res.ok) return null;" in block

    def test_map_is_hidden_without_a_skeleton(self):
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function renderMap() {")
        assert "if (!lvl) {" in block

    def test_no_fallback_data_module_is_referenced(self):
        """atlas のフィクスチャ・モックへ退避しない（本番で偽の地図を出さない）。"""
        js = _read(CORPUS_JS)
        js = _code(js)
        for banned in ("ATLAS_FIXTURE", "AtlasData", "atlas-fixture"):
            assert banned not in js, f"{banned} を参照している"

    def test_open_requires_a_token(self):
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function open() {")
        assert "if (!token()) return;" in block

    def test_invalidate_drops_per_user_results(self):
        js = _read(CORPUS_JS)
        block = _extract_function_body(js, "function invalidate() {")
        for key in ("state.domains", "state.documents", "state.interest", "state.discuss"):
            assert key in block
        app = _read(APP_JS)
        assert "window.CorpusSea.invalidate()" in app


class TestNoExternalCalls:
    """CR7: 学習者起点で外部 API を呼ばない（叩くのは自サーバの読み取り API だけ）。"""

    def test_all_fetches_go_through_the_local_api_helper(self):
        js = _code(_read(CORPUS_JS))
        assert 'const API = "/api";' in js
        assert "http://" not in js
        assert "https://" not in js
        for banned in ("export.arxiv.org", "semanticscholar", "arxiv.org"):
            assert banned not in js
