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
