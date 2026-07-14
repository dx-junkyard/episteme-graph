"""個人知識ネットワーク（Phase P-1）—「わたしの地図」UI 統合の静的ガードレール。

正本: docs/features/personal_knowledge_network_design.md（PN-1〜PN-7、特に
§9 フロント / §11 ガードレール）。frontend/public/js/personal-map.js は本テストと
並行して着地する別実装であり、着地前はここに並ぶ存在チェック系のテストが
fail する（skip しない — 未実装を正直に反映する。着地後は pass する想定）。

既存の atlas 静的テスト (test_atlas_minimap_and_cues_static.py) と同様、
フロントエンドソースの静的検証で受け入れ条件を固定する。

受け入れ条件との対応:
1. personal-map.js: ポーリング禁止 (PN-5) / 禁止語彙なし (PN-4) /
   personal-network を fetch / user_id をクエリ・URL に含めない (PN-1) /
   localStorage によるトグル永続化をしない (PN-2 同族)
2. atlas-overlay.js: PersonalMap への参照はすべて window.PersonalMap 経由
   （window. なしの直呼びが無い）かつ呼び出しはガードされている
3. atlas-minimap.js: PersonalMap・personal への参照が無い（F-1 非改変）
4. index.html: personal-map.js の script タグがある
5. app.js: data-trace-id の付与 + PersonalMap 参照は window. ガード付き
6. personal-map.js の表示文言に件数・%表示を匂わせる終端パターンが無い (PN-4)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
PERSONAL_MAP_JS = ROOT / "frontend" / "public" / "js" / "personal-map.js"
ATLAS_OVERLAY_JS = ROOT / "frontend" / "public" / "js" / "atlas-overlay.js"
ATLAS_MINIMAP_JS = ROOT / "frontend" / "public" / "js" / "atlas-minimap.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_personal_map_calls_guarded(src: str, label: str) -> None:
    """window.PersonalMap.<method>(...) の呼び出しがすべてガードされていることを検証する。

    許容形:
      - 同一行に "if (window.PersonalMap)" または "window.PersonalMap &&" を含む
        (例: `if (window.PersonalMap) window.PersonalMap.onOverlayClosed();`)
      - `if (window.PersonalMap) { ... }` ブロックの内側にある
    加えて、window. を伴わない素の "PersonalMap." 直呼びが無いことも検証する。
    """
    naked = re.search(r"(?<!window\.)\bPersonalMap\.\w+\s*\(", src)
    assert naked is None, (
        f"{label}: window. を伴わない PersonalMap の直呼びがあります: {naked.group(0)!r}"
    )

    calls = list(re.finditer(r"window\.PersonalMap\.\w+\s*\(", src))
    assert calls, f"{label}: window.PersonalMap の API 呼び出しが見つかりません"

    # if (window.PersonalMap) { <非貪欲マッチ> } ブロックの範囲を収集する。
    # ガード対象は素通しの mountControls/onLevelRendered 等の薄いパススルー呼び出しが
    # 想定されるため、ブロック内で最初に閉じる "}" までを対象とする単純な非貪欲マッチで足りる。
    guarded_spans = [
        (m.start(1), m.end(1))
        for m in re.finditer(r"if\s*\(window\.PersonalMap\)\s*\{(.*?)\}", src, re.S)
    ]

    lines = src.splitlines(keepends=True)
    line_starts = []
    pos = 0
    for line in lines:
        line_starts.append(pos)
        pos += len(line)

    def _line_of(offset: int) -> str:
        idx = 0
        for i, start in enumerate(line_starts):
            if start <= offset:
                idx = i
            else:
                break
        return lines[idx]

    for m in calls:
        call_line = _line_of(m.start())
        same_line_guard = (
            "if (window.PersonalMap)" in call_line or "window.PersonalMap &&" in call_line
        )
        in_block = any(start <= m.start() < end for start, end in guarded_spans)
        assert same_line_guard or in_block, (
            f"{label}: window.PersonalMap 呼び出しがガードされていません: {call_line.strip()!r}"
        )


class TestPersonalMapModule:
    """personal-map.js 単体の受け入れ条件 (PN-1 / PN-4 / PN-5)。"""

    def test_file_exists(self):
        assert PERSONAL_MAP_JS.exists(), (
            "frontend/public/js/personal-map.js が存在しません。"
            "Phase P-1 の並行実装 (personal-map.js) が着地すればこのテストは pass するようになります。"
        )

    def test_no_polling(self):
        """PN-5: 地図の更新はポーリングしない (トピック遷移・オーバーレイ閉時のみ)。"""
        src = _read(PERSONAL_MAP_JS)
        assert "setInterval" not in src

    def test_no_forbidden_vocabulary(self):
        """PN-4: 踏破率・達成率・ランキング等の煽り語彙を出さない。"""
        src = _read(PERSONAL_MAP_JS)
        for word in ("踏破", "達成率", "ランキング"):
            assert word not in src

    def test_fetches_personal_network(self):
        src = _read(PERSONAL_MAP_JS)
        assert "personal-network" in src

    def test_no_user_id_in_request(self):
        """PN-1: 本人スコープは既存認証 (JWT) で解決し、user_id をクエリ・URL に含めない。"""
        src = _read(PERSONAL_MAP_JS)
        assert "user_id" not in src

    def test_no_localstorage_toggle_persistence(self):
        """トグル状態を localStorage に永続化しない
        (毎回サーバ状態から導出する PN-2 と同族。"localStorage.setItem" の不在で判定する)。
        """
        src = _read(PERSONAL_MAP_JS)
        assert "localStorage.setItem" not in src

    def test_no_count_or_percentage_display_literals(self):
        """PN-4: 件数・%表示を匂わせる文言終端パターンが無いことを軽量に検査する。

        "件" 単体だと「条件」等で誤爆するため、表示連結でよく使われる
        終端パターン (件+閉じ引用符/閉じタグ/読点/閉じ括弧、%+閉じ引用符/閉じタグ) に限定する。
        """
        src = _read(PERSONAL_MAP_JS)
        forbidden_patterns = ['件"', "件<", "件、", "件）", "件)", '%"', "%<"]
        hits = [p for p in forbidden_patterns if p in src]
        assert not hits, f"数値表示の可能性がある文言が見つかりました: {hits}"
        assert "達成率" not in src


class TestAtlasOverlayIntegration:
    """atlas-overlay.js からの PersonalMap 参照はすべてガード形で行われている。"""

    def test_personal_map_calls_are_guarded(self):
        src = _read(ATLAS_OVERLAY_JS)
        assert "PersonalMap" in src, "atlas-overlay.js が PersonalMap と統合されていません"
        _assert_personal_map_calls_guarded(src, "atlas-overlay.js")


class TestAtlasMinimapUnchanged:
    """F-1: ミニマップは「いまここ + 状態ドット + 霧」規約のまま変更しない。"""

    def test_no_personal_map_references(self):
        src = _read(ATLAS_MINIMAP_JS)
        assert "PersonalMap" not in src
        assert "personal" not in src


class TestIndexHtml:
    def test_personal_map_script_tag_present(self):
        html = _read(INDEX_HTML)
        assert re.search(r'<script src="/js/personal-map\.js(\?[^"]*)?"></script>', html), (
            "index.html に personal-map.js の script タグがありません"
        )


class TestAppJsIntegration:
    """app.js 側の統合: data-trace-id 付与 + PersonalMap 参照のガード。"""

    def test_data_trace_id_attribute_present(self):
        src = _read(APP_JS)
        assert "data-trace-id" in src

    def test_personal_map_calls_are_guarded(self):
        src = _read(APP_JS)
        assert "PersonalMap" in src, "app.js が PersonalMap と統合されていません"
        _assert_personal_map_calls_guarded(src, "app.js")

    def test_open_trajectory_hooked_into_init(self):
        """起動時に一度だけ PersonalMap.init へ openTrajectory を渡している。"""
        src = _read(APP_JS)
        assert "PersonalMap.init(" in src
        assert "openTrajectory" in src

    def test_invalidate_called_on_course_switch(self):
        src = _read(APP_JS)
        assert "PersonalMap.invalidate()" in src

    def test_annotate_trajectory_list_called_after_render(self):
        src = _read(APP_JS)
        assert "PersonalMap.annotateTrajectoryList(" in src
