"""個人知識ネットワーク（Phase P-3）—「わたしの地図」最上位パネル統合の静的ガードレール。

正本: /Users/Shared/issues/episteme_graph_personal_knowledge_network_ux_proposal.md
（§2 UX原則・§3.1/3.2 情報設計・§7.1 プライバシー・§8 避けるべきUX）と、それを継承する
docs/features/personal_knowledge_network_design.md（PN-1〜PN-7）。

既存の test_personal_map_ui_guardrails.py と同様、フロントエンドソースの静的検証で
受け入れ条件を固定する（実ブラウザ・実APIには依存しない）。

受け入れ条件との対応:
1. personal-map-home.js: ポーリング禁止 (PN-5) / 禁止語彙なし (PN-4) /
   `/api/me/personal-network` を fetch / user_id をクエリ・URL に含めない (PN-1) /
   プライバシー注記の文言が存在する (§7.1)
2. index.html: personal-map-home.js の script タグ + #my-map-btn が存在する
3. app.js: PersonalMapHome への参照は window. 経由でガードされている
4. personal-map.js: コース横断の橋 (cross_course_hint) / 本人による訂正
   (map-exclude / map-restore) の語彙が結線されている (P-2 / 提案書§6)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
PERSONAL_MAP_JS = ROOT / "frontend" / "public" / "js" / "personal-map.js"
PERSONAL_MAP_HOME_JS = ROOT / "frontend" / "public" / "js" / "personal-map-home.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"

FORBIDDEN_WORDS = ("踏破", "達成率", "ランキング", "獲得", "成長しました", "おすすめ", "スコア")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_js_comment_lines(src: str) -> str:
    """行頭がコメントの行を落とす（denylist 検査をコード行に限るための保守的な前処理）。"""
    kept = [
        line
        for line in src.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    ]
    return "\n".join(kept)


class TestPersonalMapHomeModule:
    """personal-map-home.js 単体の受け入れ条件 (PN-1 / PN-4 / PN-5 / §7.1)。"""

    def test_file_exists(self):
        assert PERSONAL_MAP_HOME_JS.exists(), (
            "frontend/public/js/personal-map-home.js が存在しません。"
            "Phase P-3 の実装が着地すればこのテストは pass するようになります。"
        )

    def test_no_polling(self):
        """PN-5: 自動更新・ポーリングをしない。fetch は明示操作 (open()) 起点のみ。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "setInterval" not in src

    def test_no_forbidden_vocabulary(self):
        """PN-4: 数値・進捗・ゲーミフィケーションを匂わせる語彙を出さない。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        hits = [w for w in FORBIDDEN_WORDS if w in src]
        assert not hits, f"禁止語彙が見つかりました: {hits}"

    def test_fetches_me_personal_network(self):
        """§5.3: 正本API `/api/me/personal-network`（コース横断・本人スコープ）を使う。
        コース単位の `/api/learning/courses/{id}/personal-network` ではない。
        """
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "/me/personal-network" in src

    def test_fetches_me_personal_network_journey(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "/me/personal-network/journey" in src

    def test_no_user_id_in_request(self):
        """PN-1: 本人スコープは既存認証 (JWT) で解決し、user_id をクエリ・URL に含めない。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "user_id" not in src

    def test_no_localstorage_toggle_persistence(self):
        """タブ選択・キャッシュ等の表示状態を localStorage に永続化しない
        (毎回サーバ状態から導出する PN-2 と同族)。
        """
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "localStorage.setItem" not in src

    def test_privacy_note_present(self):
        """§7.1: 「この地図はあなたにだけ表示されます。成績評価には使用されません。」を
        常設の注記として出す。
        """
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "この地図はあなたにだけ表示されます" in src

    def test_journey_is_explicit_action_only(self):
        """PN-5: 旅は「ここから旅に出る」ボタンでのみ開く（自動で開かない）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "ここから旅に出る" in src

    def test_public_contract_methods_present(self):
        """公開契約: init / open / close / invalidate の4メソッドを window.PersonalMapHome
        として公開する。
        """
        src = _read(PERSONAL_MAP_HOME_JS)
        assert re.search(r"window\.PersonalMapHome\s*=\s*\{", src)
        for name in ("init", "open", "close", "invalidate"):
            assert re.search(r"\b" + name + r"\b", src), f"{name} が見つかりません"

    def test_journey_fetch_is_get_only(self):
        """journey 用 fetch に method 指定 (POST 等) が無いことを検証する
        (journey は DB 非変更の読み取り専用でなければならない)。
        """
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"function fetchJourney\(.*?\n  \}\n", src, re.S)
        assert match, "fetchJourney 関数が見つかりません"
        assert "method" not in match.group(0).lower()


class TestNearbyTab:
    """「いまここの周り」（近傍関係ビュー）の UI 契約。

    正本: docs/features/personal_map_nearby_design.md §5。
    """

    def test_nearby_tab_is_first(self):
        """既定タブが近傍関係ビューであること（3タブ構成。順序・挙動は不変のまま先頭に足す）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"const TABS = \[(.*?)\];", src, re.S)
        assert match, "TABS 定義が見つかりません"
        keys = re.findall(r'key:\s*"([a-z]+)"', match.group(1))
        assert keys[0] == "nearby", f"先頭タブが nearby ではありません: {keys}"
        assert keys[1:] == ["now", "journeys"], f"既存タブの順序が変わっています: {keys}"
        assert re.search(r'activeTab:\s*"nearby"', src)

    def test_uses_nearby_endpoint_with_mode(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "/me/personal-network/nearby" in src
        assert "&mode=" in src

    def test_nearby_fetch_is_get_only(self):
        """読み取り専用（DB 非変更）。method 指定を持たない。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"function fetchNearby\(.*?\n  \}\n", src, re.S)
        assert match, "fetchNearby 関数が見つかりません"
        assert "method" not in match.group(0).lower()

    def test_axis_meaning_is_stated(self):
        """縦軸が依存の向きであることを画面に明記する（位置の意味を言葉で担保する）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "これが前提にしていること" in src
        assert "これに依存していること" in src
        assert "土台" in src

    def test_verification_legend_is_closed_world(self):
        """PMN-3: 検証の不在は「このコーパスの中では」に限定して書く。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "このコーパスの中では検証記録がない" in src
        for banned in ("この分野では未検証", "誰も検証していない", "世界初", "未踏"):
            assert banned not in src

    def test_symbols_reuse_the_existing_vocabulary(self):
        """記号はコースビューと同じ .personal-map-dot-* / スウォッチ定義を共有する。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "personal-map-dot-" in src
        assert "personal-map-legend-swatch" in src

    def test_nearby_styles_exist(self):
        css = _read(ROOT / "frontend" / "public" / "css" / "styles.css")
        for cls in (
            ".pm-home-nb-node",
            ".pm-home-nb-node.unverified",
            ".pm-home-nb-node.no-ledger",
            ".pm-home-nb-node.is-center",
            ".pm-home-nb-lane-label",
            ".pm-home-nb-facts",
            ".pm-home-nb-node.untouched",
            ".pm-home-nb-range-head",
            ".pm-home-nb-doc-head",
        ):
            assert cls in css, f"{cls} のスタイルがありません"

    def test_no_numeric_or_progress_rendering(self):
        """PMN-4: 件数・割合・進捗の描画語彙を持たない。

        検査対象はコード行のみ（「件数・進捗率は出さない」と規約を説明するコメントまで
        denylist に掛けると、規約の説明そのものが書けなくなる）。
        """
        src = _strip_js_comment_lines(_read(PERSONAL_MAP_HOME_JS))
        for banned in ("confidence", "load_score", "件中", "％", "進捗率"):
            assert banned not in src


class TestNearbyRangeMode:
    """範囲モード（topic アンカーの事実ベース粗表示）の UI 契約。

    正本: docs/features/personal_map_nearby_design.md（本タブ実装時の追加分。
    topic アンカーは中心1点ではなく range_documents を描く）。
    """

    def test_topic_anchor_type_included(self):
        """topic アンカーの痕跡も中心の選択肢に出す（点ビューに解決できない代わりに
        範囲モードのDTOが返る）。
        """
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"const NEARBY_ANCHOR_TYPES = \[(.*?)\];", src)
        assert match, "NEARBY_ANCHOR_TYPES 定義が見つかりません"
        assert '"topic"' in match.group(1)

    def test_range_mode_branch_present(self):
        """dto.mode === 'range' の分岐と range_documents の描画が存在する。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        assert re.search(r'dto\.mode\s*===\s*"range"', src)
        assert "range_documents" in src

    def test_range_head_and_self_records_heading_present(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "この話題が触れている範囲" in src
        assert "このトピックでの自分の記録" in src

    def test_untouched_class_assigned_from_touched_flag(self):
        """サーバの `touched` フラグから `untouched` CSS クラスを付与するロジックがある。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        assert re.search(r"!n\.touched", src)
        assert '" untouched"' in src

    def test_mode_toggle_hidden_for_unresolved_topic_center(self):
        """範囲モード（topic アンカー、中心未移動）では near/root モード切替を隠す
        （anchorType 判定によるもので、中心移動後は通常どおり出す）。
        """
        src = _read(PERSONAL_MAP_HOME_JS)
        assert re.search(r'currentCenter\.anchorType\s*===\s*"topic"', src)
        assert "hideModes" in src

    def test_nearby_centers_expose_anchor_type(self):
        """nearbyCenters() の返却物に anchorType / anchorId が含まれる
        （範囲モード判定・自分の記録の絞り込みの両方に使うため）。
        """
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"function nearbyCenters\(.*?\n  \}\n", src, re.S)
        assert match, "nearbyCenters 関数が見つかりません"
        body = match.group(0)
        assert "anchorType" in body
        assert "anchorId" in body

    def test_range_legend_uses_non_numeric_contrast_wording(self):
        """凡例は「濃い枠/淡色」の事実対比のみで、件数・割合を言わない（PMN-4）。"""
        src = _strip_js_comment_lines(_read(PERSONAL_MAP_HOME_JS))
        assert "この話題が触れている場所" in src
        assert "この論文のその他の理論構成" in src

    def test_range_mode_reuses_existing_move_and_row_helpers(self):
        """範囲モードのチップも既存の中心移動 (data-pm-home-nearby-move) を使い、
        自分の記録一覧も既存の nodeRowHtml（旅・地図には反映しない導線つき）を再利用する。
        """
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"function renderNearbyRangeGraph\(.*?\n  \}\n", src, re.S)
        assert match, "renderNearbyRangeGraph 関数が見つかりません"
        assert "data-pm-home-nearby-move" in match.group(0)
        mine_match = re.search(r"function renderNearbyRangeMine\(.*?\n  \}\n", src, re.S)
        assert mine_match, "renderNearbyRangeMine 関数が見つかりません"
        assert "nodeRowHtml(" in mine_match.group(0)

    def test_no_forbidden_vocabulary_in_range_additions(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        hits = [w for w in FORBIDDEN_WORDS if w in src]
        assert not hits, f"禁止語彙が見つかりました: {hits}"


class TestIndexHtmlIntegration:
    def test_personal_map_home_script_tag_present(self):
        html = _read(INDEX_HTML)
        assert re.search(
            r'<script src="/js/personal-map-home\.js(\?[^"]*)?"></script>', html
        ), "index.html に personal-map-home.js の script タグがありません"

    def test_my_map_button_present(self):
        html = _read(INDEX_HTML)
        assert re.search(r'id="my-map-btn"', html), "index.html に #my-map-btn がありません"


class TestAppJsIntegration:
    """app.js 側の統合: PersonalMapHome 参照はすべて window. 経由でガードされている。"""

    def test_personal_map_home_referenced(self):
        src = _read(APP_JS)
        assert "PersonalMapHome" in src, "app.js が PersonalMapHome と統合されていません"

    def test_no_naked_personal_map_home_calls(self):
        src = _read(APP_JS)
        naked = re.search(r"(?<!window\.)\bPersonalMapHome\.\w+\s*\(", src)
        assert naked is None, (
            f"window. を伴わない PersonalMapHome の直呼びがあります: {naked.group(0)!r}"
        )

    def test_my_map_btn_wired_to_open(self):
        src = _read(APP_JS)
        assert "my-map-btn" in src
        assert "PersonalMapHome.open()" in src

    def test_init_hooked(self):
        src = _read(APP_JS)
        assert "PersonalMapHome.init(" in src

    def test_invalidate_called_on_course_switch(self):
        src = _read(APP_JS)
        assert "PersonalMapHome.invalidate()" in src


class TestPersonalMapCrossCourseAndCorrections:
    """personal-map.js への P-2/§6 結線確認 (コース横断の橋 + 本人による訂正)。"""

    def test_cross_course_hint_wired(self):
        src = _read(PERSONAL_MAP_JS)
        assert "cross_course_hint" in src

    def test_cross_course_uses_me_endpoint(self):
        """コース横断版の旅は本人スコープの正本API (`/api/me/...`) を使う。"""
        src = _read(PERSONAL_MAP_JS)
        assert "/me/personal-network/journey" in src

    def test_map_exclude_wired(self):
        src = _read(PERSONAL_MAP_JS)
        assert "map-exclude" in src

    def test_map_restore_wired(self):
        src = _read(PERSONAL_MAP_JS)
        assert "map-restore" in src

    def test_map_exclude_button_label_present(self):
        src = _read(PERSONAL_MAP_JS)
        assert "地図には反映しない" in src

    def test_map_restore_chip_label_present(self):
        src = _read(PERSONAL_MAP_JS)
        assert "地図に戻す" in src

    def test_map_exclude_not_offered_for_reconstruction(self):
        """対象は tension/question のみ。reconstruction ノードには「地図には反映しない」を
        出さない (提案書§6 の対象範囲)。
        """
        src = _read(PERSONAL_MAP_JS)
        match = re.search(r"\n  function showPopup\(.*?\n  \}\n", src, re.S)
        assert match, "showPopup 関数が見つかりません"
        body = match.group(0)
        assert "buildMapExcludeButton" in body
        assert 'node.node_kind === "tension" || node.node_kind === "question"' in body

    def test_still_no_forbidden_vocabulary_or_polling(self):
        """既存の禁止語彙・ポーリング禁止が新規コード追加後も保たれている。"""
        src = _read(PERSONAL_MAP_JS)
        assert "setInterval" not in src
        hits = [w for w in FORBIDDEN_WORDS if w in src]
        assert not hits, f"禁止語彙が見つかりました: {hits}"


class TestOverlayToggleRenamedToAvoidLabelCollision:
    """N17（2026-07-17）: オーバーレイ内トグルと最上位パネルが同じ「わたしの地図」ラベルを
    使っていた重複の解消。オーバーレイ内トグル（personal-map.js）は「自分の記録を重ねる」、
    最上位パネル名（personal-map-home.js）は「わたしの地図」のまま — 名前で機能を
    区別できることを固定する。
    """

    def test_overlay_toggle_uses_new_label(self):
        src = _read(PERSONAL_MAP_JS)
        assert "自分の記録を重ねる" in src

    def test_overlay_toggle_no_longer_creates_my_map_text_node(self):
        """トグルのラベルテキストとして「わたしの地図」を生成しない（コメント・docstring
        での言及は許容 — createTextNode の引数だけを検査する）。"""
        src = _read(PERSONAL_MAP_JS)
        assert 'createTextNode("わたしの地図")' not in src

    def test_home_panel_keeps_my_map_title(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "わたしの地図" in src


class TestHomePanelMapExclude:
    """N17（2026-07-17）: 最上位パネルのノード行から「地図には反映しない」を使えること。
    既存 API `POST /api/learning/traces/{id}/map-exclude` を再利用し、コースビュー
    （personal-map.js）と同じフィードバック文言・fail-closed 様式に合わせる。
    """

    def test_map_exclude_wired_in_home_panel(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "/map-exclude" in src
        assert "data-pm-home-map-exclude" in src

    def test_map_exclude_button_label_present_in_home_panel(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "地図には反映しない" in src

    def test_map_exclude_only_for_tension_and_question_in_node_row(self):
        """対象は tension/question のみ。reconstruction ノードには出さない
        （コースビュー personal-map.js の showPopup と同じ対象範囲）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function nodeRowHtml\(.*?\n  \}\n", src, re.S)
        assert match, "nodeRowHtml 関数が見つかりません"
        body = match.group(0)
        assert "data-pm-home-map-exclude" in body
        assert 'node.node_kind === "tension" || node.node_kind === "question"' in body

    def test_map_exclude_feedback_wording_matches_course_view(self):
        """フィードバック文言はコースビュー（personal-map.js）と同一に揃える。"""
        home_src = _read(PERSONAL_MAP_HOME_JS)
        map_src = _read(PERSONAL_MAP_JS)
        wording = "地図には反映しません（問いの軌跡には残ります）"
        assert wording in home_src
        assert wording in map_src

    def test_map_exclude_refetches_and_rerenders(self):
        """操作後はキャッシュ破棄 → 再取得 → 再描画（PN-2: 導出はサーバ状態が正）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function requestMapExclude\(.*?\n  \}\n", src, re.S)
        assert match, "requestMapExclude 関数が見つかりません"
        body = match.group(0)
        assert "state.cache = null" in body
        assert "loadNetwork()" in body
        assert "renderPanel()" in body

    def test_map_exclude_fail_closed(self):
        """失敗時は何も出さない（エラーバナー・alert を出さない fail-closed）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function requestMapExclude\(.*?\n  \}\n", src, re.S)
        assert match
        body = match.group(0)
        assert ".catch(() => {})" in body
        assert "alert(" not in body

    def test_no_delete_or_dismiss_calls_from_home_panel(self):
        """訂正操作は map-exclude（表示除外）のみ。行削除・dismiss（候補の当落判定）を
        最上位パネルから呼ばない（P4 / 提案書 §6 の独立性）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "/dismiss" not in src
        assert 'method: "DELETE"' not in src


class TestNamedFog:
    """「名前のある霧」（いまの地図タブ・現在地の隣にある骨格概念を名前だけ淡く見せる
    好奇心装置）の UI 契約。不変条項: 数値・件数・進捗を出さない / ポーリング禁止 /
    明示操作起点の fetch のみ / fail-closed（取得失敗・対象なしは何も描かない。
    エラー文言も出さない） / 推薦・助言文言禁止。
    """

    def test_fetches_atlas_neighbors_endpoint(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "/me/personal-network/atlas-neighbors" in src
        assert "node_id=" in src

    def test_atlas_neighbors_fetch_is_get_only(self):
        """読み取り専用（DB 非変更）。method 指定を持たない。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"function fetchAtlasNeighbors\(.*?\n  \}\n", src, re.S)
        assert match, "fetchAtlasNeighbors 関数が見つかりません"
        assert "method" not in match.group(0).lower()

    def test_fog_heading_present(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "この場所の隣にあるもの" in src

    def test_fog_is_rendered_after_current_location_block(self):
        """霧ブロックは「いまの地図」タブの現在地ブロック直後に描く。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function renderNow\(.*?\n  \}\n", src, re.S)
        assert match, "renderNow 関数が見つかりません"
        body = match.group(0)
        current_idx = body.index("pm-home-current-heading")
        fog_idx = body.index("renderFog(current)")
        assert current_idx < fog_idx, "renderFog の呼び出しが現在地ブロックより前にあります"

    def test_fog_fetch_is_explicit_tab_render_only(self):
        """タブ描画時に1回だけ取りにいく（renderPanel からの呼び出し。ポーリングしない）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function renderPanel\(.*?\n  \}\n", src, re.S)
        assert match, "renderPanel 関数が見つかりません"
        assert "requestFog(" in match.group(0)

    def test_no_new_polling(self):
        """霧の追加によって新たなポーリング（setInterval）が増えていない。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "setInterval" not in src

    def test_fog_dedupes_by_node_id(self):
        """キー（nodeId）単位で重複抑制する（fogCache / fogLoadingId）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"function requestFog\(.*?\n  \}\n", src, re.S)
        assert match, "requestFog 関数が見つかりません"
        body = match.group(0)
        assert "state.fogCache[nodeId]" in body
        assert "state.fogLoadingId" in body

    def test_fog_fail_closed_renders_nothing(self):
        """available:false・neighbors 空・未取得は何も描かない。エラー文言・
        「表示できません」を霧の描画経路で出さない。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function renderFog\(.*?\n  \}\n", src, re.S)
        assert match, "renderFog 関数が見つかりません"
        body = match.group(0)
        assert 'dto.available !== true' in body or "dto.available === true" in body
        assert "表示できません" not in body
        assert "note" not in body  # dto.note は表示に使わない

    def test_fog_chip_not_interactive(self):
        """チップは非インタラクティブ（button にしない・click ハンドラを付けない）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function renderFog\(.*?\n  \}\n", src, re.S)
        assert match, "renderFog 関数が見つかりません"
        body = match.group(0)
        assert "<button" not in body
        assert "onclick" not in body
        assert "tabindex" not in body
        # onOverlayClick 側にも霧チップ専用のクリック分岐を作らない
        click_src = _read(PERSONAL_MAP_HOME_JS)
        assert "data-pm-home-fog" not in click_src

    def test_fog_edge_group_before_sibling_group(self):
        """relation==='edge' の群を relation==='sibling' の群より先に並べる。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function renderFog\(.*?\n  \}\n", src, re.S)
        assert match, "renderFog 関数が見つかりません"
        body = match.group(0)
        edge_idx = body.index('n.relation === "edge"')
        concat_idx = body.index("edges.concat(siblings)")
        assert edge_idx < concat_idx

    def test_no_forbidden_or_advisory_vocabulary_in_fog(self):
        """PN-4 に加え、推薦・助言文言（「見てみよう」等）も霧の描画コードには書かない
        （名前の提示だけに限る）。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function renderFog\(.*?\n  \}\n", src, re.S)
        assert match, "renderFog 関数が見つかりません"
        body = _strip_js_comment_lines(match.group(0))
        for banned in FORBIDDEN_WORDS + (
            "見てみよう",
            "おすすめ",
            "ぜひ",
            "しましょう",
            "件中",
            "％",
        ):
            assert banned not in body, f"霧の描画コードに禁止語彙が見つかりました: {banned}"

    def test_fog_styles_exist(self):
        css = _read(ROOT / "frontend" / "public" / "css" / "styles.css")
        for cls in (
            ".pm-home-fog-head",
            ".pm-home-fog-here",
            ".pm-home-fog-chips",
            ".pm-home-fog-chip",
            ".pm-home-fog-region",
        ):
            assert cls in css, f"{cls} のスタイルがありません"


class TestReflectTabRemoved:
    """オーナー裁定: 「振り返り」タブは削除。タブ・ラベル・専用描画関数のいずれも
    ソースに存在しないことを固定する。
    """

    def test_reflect_key_and_label_absent(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        assert '"reflect"' not in src
        assert "振り返り" not in src
        assert "renderReflect" not in src

    def test_reflect_styles_removed(self):
        css = _read(ROOT / "frontend" / "public" / "css" / "styles.css")
        assert ".pm-home-reflect-month" not in css
        assert ".pm-home-reflect-item" not in css


class TestInitialTabConsistency:
    """T2: open() と invalidate() が同じ既定タブ（いまここの周り）に揃っていること。
    以前は invalidate() だけ "now" に戻す不整合があった。
    """

    def test_invalidate_resets_to_nearby_tab(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function invalidate\(.*?\n  \}\n", src, re.S)
        assert match, "invalidate 関数が見つかりません"
        assert 'state.activeTab = "nearby"' in match.group(0)

    def test_open_resets_to_first_tab(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function open\(.*?\n  \}\n", src, re.S)
        assert match, "open 関数が見つかりません"
        assert "state.activeTab = TABS[0].key" in match.group(0)


class TestNearbyJumpBridge:
    """T3: 「いまの地図」「問いからの旅」のノード行から「いまここの周り」への橋
    (`この場所の周りを見る`)。中心になれるかどうかの判定は nearbyCenters() の
    絞り込みと同じ述語を共有する（二重実装しない）。
    """

    def test_jump_button_present_in_node_row(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function nodeRowHtml\(.*?\n  \}\n", src, re.S)
        assert match, "nodeRowHtml 関数が見つかりません"
        body = match.group(0)
        assert "data-pm-home-nearby-jump" in body
        assert "この場所の周りを見る" in body

    def test_predicate_is_shared_not_duplicated(self):
        """canBeNearbyCenter 述語が定義され、nearbyCenters() とジャンプ判定の両方から
        呼ばれている（NEARBY_ANCHOR_TYPES.indexOf のチェックを2箇所に書かない）。
        """
        src = _read(PERSONAL_MAP_HOME_JS)
        assert "function canBeNearbyCenter(" in src
        # nearbyCenters() 自体が述語関数を使って絞り込んでいる
        centers_match = re.search(r"\n  function nearbyCenters\(.*?\n  \}\n", src, re.S)
        assert centers_match, "nearbyCenters 関数が見つかりません"
        assert "canBeNearbyCenter" in centers_match.group(0)
        # ジャンプ先の解決も同じ述語を経由する
        jump_match = re.search(r"\n  function nearbyCenterIdForNode\(.*?\n  \}\n", src, re.S)
        assert jump_match, "nearbyCenterIdForNode 関数が見つかりません"
        assert "canBeNearbyCenter" in jump_match.group(0)
        # NEARBY_ANCHOR_TYPES.indexOf の判定自体は canBeNearbyCenter の1箇所だけに書く
        assert src.count("NEARBY_ANCHOR_TYPES.indexOf") == 1

    def test_jump_click_handler_switches_to_nearby_tab(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\[data-pm-home-nearby-jump\]\"\);\s*\n\s*if \(nbJump\) \{(.*?)\n    \}", src, re.S)
        assert match, "data-pm-home-nearby-jump のクリックハンドラが見つかりません"
        body = match.group(1)
        assert "state.nearbyCenterNodeId" in body
        assert 'state.activeTab = "nearby"' in body
        assert "renderPanel()" in body


class TestJourneysTabReusesNodeRow:
    """T4: 「問いからの旅」の起点リストは nodeRowHtml() を再利用し、自前の HTML 組み立てを
    やめる（「地図には反映しない」「この場所の周りを見る」が旅タブでも一貫して出る）。
    """

    def test_render_journeys_calls_node_row_html(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function renderJourneys\(.*?\n  \}\n", src, re.S)
        assert match, "renderJourneys 関数が見つかりません"
        body = match.group(0)
        assert "nodeRowHtml(" in body
        # 旧来の自前組み立て（kind/label を直接連結する断片）が残っていないこと
        assert 'pm-home-journey-card-item' not in body

    def test_journeys_list_wrapper_and_limit_unchanged(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function renderJourneys\(.*?\n  \}\n", src, re.S)
        assert match
        body = match.group(0)
        assert "pm-home-journeys-list" in body
        assert "slice(0, 20)" in body


class TestEmptyJourneyNotice:
    """T5: 旅の steps が空でも無言にしない。サーバが notice/facts を添えた応答は、
    nearby の「対象なし」分岐と同じ見出し・同じ描画部品 (renderNearbyFacts) で
    「この記録について」を表示する。notice の無い旧型応答は従来どおり非表示
    （後方互換）。
    """

    def test_render_journey_area_handles_empty_notice(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function renderJourneyArea\(.*?\n  \}\n", src, re.S)
        assert match, "renderJourneyArea 関数が見つかりません"
        body = match.group(0)
        assert "data.notice" in body
        assert "renderNearbyFacts(" in body
        assert "この記録について" in body

    def test_backward_compatible_when_no_notice(self):
        """notice も steps も無い旧型応答では従来どおり area.hidden = true に丸める。"""
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function renderJourneyArea\(.*?\n  \}\n", src, re.S)
        assert match
        body = match.group(0)
        assert "if (!data.notice) {" in body

    def test_fetch_error_and_404_paths_unchanged(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function renderJourneyArea\(.*?\n  \}\n", src, re.S)
        assert match
        body = match.group(0)
        assert "data._fetch_error" in body
        assert "旅の経路を読み込めませんでした" in body


class TestJourneyRestartFocusesList:
    """T6: 「別の問いから旅に出る」は旅タブ表示中に押されてもノーオペにならない。
    switchTab() の同タブ早期 return を避け、closeJourneyArea() を先に呼んでから
    起点リストへフォーカス移動する。別タブからは従来どおりタブ切替する。
    """

    def test_restart_calls_close_journey_area_first(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(
            r"data-pm-home-journey-restart\]\"\);\s*\n\s*if \(journeyRestart\) \{(.*?)\n    \}",
            src,
            re.S,
        )
        assert match, "journeyRestart のクリックハンドラが見つかりません"
        body = match.group(1)
        assert "closeJourneyArea()" in body

    def test_restart_scrolls_list_when_already_on_journeys_tab(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(
            r"data-pm-home-journey-restart\]\"\);\s*\n\s*if \(journeyRestart\) \{(.*?)\n    \}",
            src,
            re.S,
        )
        assert match
        body = match.group(1)
        assert 'state.activeTab === "journeys"' in body
        assert "pm-home-journeys-list" in body
        assert "scrollIntoView" in body

    def test_restart_switches_tab_from_other_tabs(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(
            r"data-pm-home-journey-restart\]\"\);\s*\n\s*if \(journeyRestart\) \{(.*?)\n    \}",
            src,
            re.S,
        )
        assert match
        body = match.group(1)
        assert 'switchTab("journeys")' in body


class TestInvalidateClearsDerivedCaches:
    """invalidate() はログアウト・別ユーザーのログインを跨いで、コース横断の派生
    キャッシュ（近傍関係ビュー・名前のある霧）を残さない（PN-1: 本人のみ可視）。
    """

    def test_invalidate_clears_nearby_cache(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function invalidate\(.*?\n  \}\n", src, re.S)
        assert match, "invalidate 関数が見つかりません"
        assert "state.nearbyCache = {}" in match.group(0)

    def test_invalidate_clears_fog_cache(self):
        src = _read(PERSONAL_MAP_HOME_JS)
        match = re.search(r"\n  function invalidate\(.*?\n  \}\n", src, re.S)
        assert match, "invalidate 関数が見つかりません"
        assert "state.fogCache = {}" in match.group(0)
