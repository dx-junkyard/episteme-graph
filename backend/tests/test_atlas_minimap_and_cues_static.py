"""分野の地図 — 常設ミニマップと「見晴らしの瞬間」の導線 (Issue F: F-1/F-2)。

既存の atlas 静的テスト (test_atlas_detail_panel_transitions.py) と同様、
フロントエンドソースの静的検証で受け入れ条件を固定する。

受け入れ条件との対応:
1. ミニマップは「いまここ + 簡略ドット + 灯り/霧の面積表現」のみ
   (数値・ラベル・凡例を描くコードが存在しない)
2. ミニマップクリック → オーバーレイを L1・いまここ中心で開く
3. 4導線すべてが focus つきでオーバーレイを開き、導線3は戻った位置をハイライト
4. 初回ログイン自動表示は一度きり (サーバ永続フラグを開く前に記録)
5. 導線は提示に留める — 自動モーダルは first_login のみ
6. 骨格なしカートリッジでミニマップ・導線が出ない (404 → null → 非表示)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
MINIMAP_JS = ROOT / "frontend" / "public" / "js" / "atlas-minimap.js"
CUES_JS = ROOT / "frontend" / "public" / "js" / "atlas-cues.js"
OVERLAY_JS = ROOT / "frontend" / "public" / "js" / "atlas-overlay.js"
DATA_JS = ROOT / "frontend" / "public" / "js" / "atlas-data.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
ATLAS_CSS = ROOT / "frontend" / "public" / "css" / "atlas.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestMinimap:
    """F-1: 常設ミニマップ (左パネル下)。"""

    def test_index_includes_scripts(self):
        html = _read(INDEX_HTML)
        assert '<script src="/js/atlas-minimap.js"></script>' in html
        assert '<script src="/js/atlas-cues.js"></script>' in html

    def test_minimap_mounted_under_sidebar(self):
        app = _read(APP_JS)
        assert "AtlasMinimap.mount(sb)" in app

    def test_minimap_draws_no_text_or_numbers(self):
        """受け入れ条件1: ラベル・数値・凡例を描かない (text 要素を一切作らない)。"""
        src = _read(MINIMAP_JS)
        assert 'svgEl("text"' not in src
        assert "textContent" not in src
        assert "legend" not in src.lower()

    def test_minimap_has_now_dots_and_fog_only(self):
        src = _read(MINIMAP_JS)
        assert "atlas-mini-fog-hatch" in src  # 霧の面積表現
        assert '"now"' in src                 # いまここ (二重円の簡略形)
        assert "dotForNode" in src            # 状態ドット

    def test_minimap_opens_overlay_l1(self):
        """受け入れ条件2: クリックで L1・いまここ中心のオーバーレイ。"""
        src = _read(MINIMAP_JS)
        assert 'window.AtlasOverlay.open(data, { level: 1, source: "minimap" })' in src

    def test_minimap_a11y(self):
        src = _read(MINIMAP_JS)
        assert '"role", "button"' in src
        assert "分野の地図を開く。現在地と全体の広がりの縮図" in src
        assert '"tabindex", "0"' in src

    def test_minimap_refresh_is_event_driven(self):
        """更新はトピック遷移・オーバーレイ閉時のみ (ポーリングしない)。"""
        mini = _read(MINIMAP_JS)
        app = _read(APP_JS)
        assert "setInterval" not in mini
        assert 'document.addEventListener("atlas:closed"' in mini
        assert "AtlasMinimap.refresh()" in app

    def test_hidden_without_skeleton(self):
        """受け入れ条件6: 骨格なし (API 404 → null) で領域ごと非表示。"""
        data = _read(DATA_JS)
        mini = _read(MINIMAP_JS)
        assert "if (res.status === 404) return null" in data
        assert "setAvailable(false)" in mini
        assert "#atlas-minimap-slot:empty" in _read(ATLAS_CSS)


class TestCues:
    """F-2: 見晴らしの瞬間の導線 (4箇所)。"""

    def test_four_entrypoints_wired(self):
        app = _read(APP_JS)
        # 導線1・2: トピック完了直後 (章末判定を含む)
        assert "showAtlasCueAfterAdvance(completedTopic, next)" in app
        assert "showAtlasCueAfterAdvance(directCompleted, directNext)" in app
        # 導線2: 講義の章末 (完了バナー末尾)
        assert '"chapter_end"' in app
        # 導線3: 寄り道復帰 (戻った位置をハイライト)
        assert '"detour_return"' in app
        assert "highlight: true" in app
        # 導線4: 初回ログイン (一度きりの自動表示)
        assert "AtlasCues.maybeAutoOpenFirstLogin()" in app

    def test_cue_card_does_not_auto_open(self):
        """カードは提示のみ。オーバーレイを開くのはクリックハンドラの中だけ。"""
        src = _read(CUES_JS)
        assert "地図で現在地を見る" in src
        # showCue 本体に AtlasOverlay.open が無い (openFromCue = クリック時のみ)
        show_cue_body = src.split("function showCue", 1)[1].split("async function maybeAutoOpen", 1)[0]
        assert "AtlasOverlay.open" not in show_cue_body

    def test_detour_return_highlights_position(self):
        """受け入れ条件3: 導線3で戻った位置がハイライトされる。"""
        overlay = _read(OVERLAY_JS)
        assert "function pulseNode" in overlay
        assert "if (opts.highlight) pulseNode" in overlay
        assert "atlas-pulse" in _read(ATLAS_CSS)

    def test_first_login_flag_persisted_before_open(self):
        """受け入れ条件4: 開く前に first_login/opened をサーバへ永続化する。"""
        src = _read(CUES_JS)
        idx_flag = src.index('await postEvent("first_login", "opened"')
        idx_open = src.index('{ level: 1, intro: true, source: "first_login" }')
        assert idx_flag < idx_open
        assert '"/api/learning/atlas/cues/state"' in src

    def test_suppression_rule(self):
        """未決事項の決定: 直近10分は導線1・2のカードを抑制。導線3は対象外。"""
        src = _read(CUES_JS)
        assert "10 * 60 * 1000" in src
        assert 'kind !== "detour_return"' in src

    def test_metrics_are_internal_only(self):
        """計測は POST のみ (取得して表示する API 呼び出しがフロントに存在しない)。

        導線状態 GET は first_login_seen フラグのみで、開封率などの数値は返らない。
        """
        src = _read(CUES_JS)
        assert '"/api/learning/atlas/cues/events"' in src
        assert "first_login_seen" in src
        for word in ("開封率", "score", "バッジ"):
            assert word not in src.split("*/", 1)[1]  # 冒頭コメント以外に出さない

    def test_no_evaluative_or_solicitation_words(self):
        """§1.2: カード文言に誘導・煽りの語彙を使わない。"""
        for path in (CUES_JS, MINIMAP_JS):
            body = _read(path)
            for word in ("学びましょう", "おすすめ", "候補地帯", "ここを学べ"):
                assert word not in body
