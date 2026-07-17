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


def _extract_function(src: str, name: str) -> str:
    """personal-map.js の IIFE トップレベル関数 (2-space インデント) の本体を切り出す。

    既存の全関数が `  function <name>(...) {` で始まり `  }` (2-space) で閉じる
    様式に合わせて簡易抽出する (ネストしたブロックはより深いインデントのため
    誤って早期終了しない)。
    """
    match = re.search(
        r"\n  function " + re.escape(name) + r"\(.*?\n  \}\n", src, re.S
    )
    assert match, f"{name} 関数が personal-map.js に見つかりません"
    return match.group(0)


class TestJourneyCard:
    """旅の経路探索 (Phase P-2) — 旅カードの静的ガードレール (§6/§9)。

    journey API は明示操作 (「ここから旅に出る」ボタン) でのみ fetch する非LLM・
    決定論の traversal 結果を表示するだけであり、書き込みを一切行わない。
    """

    def test_fetches_journey_endpoint(self):
        """§7: journey は `.../personal-network/journey` を叩く。"""
        src = _read(PERSONAL_MAP_JS)
        assert "/personal-network/journey" in src

    def test_journey_button_label_present(self):
        """§9: 「ここから旅に出る」ボタンが存在する (明示操作のみ・PN-5)。"""
        src = _read(PERSONAL_MAP_JS)
        assert "ここから旅に出る" in src

    def test_journey_fetch_is_get_only(self):
        """journey 用 fetch 呼び出しに method 指定 (POST 等) が無いことを検証する
        (fetch のデフォルトは GET。明示 method があれば書き込み API 化の兆候であり、
        journey は DB 非変更の読み取り専用でなければならない)。
        """
        src = _read(PERSONAL_MAP_JS)
        fn_src = _extract_function(src, "fetchJourney")
        assert "/personal-network/journey" in fn_src
        assert "method" not in fn_src.lower()

    def test_journey_code_has_no_forbidden_vocabulary_or_polling(self):
        """旅カード関連コードにも禁止語彙・setInterval が無いことを確認する。

        test_no_polling / test_no_forbidden_vocabulary は既にファイル全体
        (旅カードのコードを含む) を検査しているため、ここでは旅カードの
        コードが実在した上でそれらの検査が効いていることを明示的に固定する。
        """
        src = _read(PERSONAL_MAP_JS)
        assert "/personal-network/journey" in src  # 旅カードのコードが実在すること
        assert "setInterval" not in src
        for word in ("踏破", "達成率", "ランキング"):
            assert word not in src

    def test_journey_card_is_single_and_replaced(self):
        """旅カードは常に最新1枚 — 新しい旅を開くと前のカードが置き換わる (§6/§9)。"""
        src = _read(PERSONAL_MAP_JS)
        fn_src = _extract_function(src, "requestJourney")
        assert "closeJourneyCard" in fn_src

    def test_journey_card_destroyed_on_overlay_close_toggle_off_and_invalidate(self):
        """旅カードはオーバーレイ閉時 (onOverlayClosed) / トグル OFF / invalidate で破棄する。"""
        src = _read(PERSONAL_MAP_JS)
        assert "closeJourneyCard" in _extract_function(src, "onOverlayClosed")
        assert "closeJourneyCard" in _extract_function(src, "invalidate")
        # トグル OFF はコース切替による強制 OFF も含め updateLegendTrayVisibility に
        # 一本化されている (state.enabled が false のときのみ破棄する)。
        assert "closeJourneyCard" in _extract_function(src, "updateLegendTrayVisibility")

    def test_atlas_node_ref_uses_existing_overlay_api(self):
        """ref.kind === "atlas_node" のリンクは既存 AtlasOverlay API でフォーカスする
        (独自のフォーカス演出を実装しない)。
        """
        src = _read(PERSONAL_MAP_JS)
        fn_src = _extract_function(src, "focusAtlasNodeFromJourney")
        assert "window.AtlasOverlay" in fn_src
        assert "atlas_node" not in fn_src  # kind 判定は呼び出し側が担う


class TestCourseSwitchRaceGuard:
    """review 指摘4（P2）: コース切替中の非同期応答が別コースの UI に表示されるバグの回帰防止。

    ``loadNetwork(courseId)`` / ``fetchJourney(courseId, nodeId)`` の消費側
    （``onToggleChange`` / ``annotateTrajectoryList`` / ``requestJourney`` /
    ``refreshAfterMapExclusionChange``）は、応答到着時に要求開始時の courseId が現在の
    courseId と一致するかを確認し、不一致なら描画を破棄する（``invalidate()`` は進行中の
    Promise 自体はキャンセルできないため）。admin-lecture-studio.js のコース切替と同じ
    「遅延応答は courseId 不一致で破棄」パターン。
    """

    def test_on_toggle_change_discards_stale_course_response(self):
        src = _read(PERSONAL_MAP_JS)
        fn_src = _extract_function(src, "onToggleChange")
        assert "courseId !== state.courseId" in fn_src

    def test_annotate_trajectory_list_discards_stale_course_response(self):
        src = _read(PERSONAL_MAP_JS)
        fn_src = _extract_function(src, "annotateTrajectoryList")
        assert "courseId !== currentCourseId()" in fn_src

    def test_request_journey_discards_stale_course_response(self):
        src = _read(PERSONAL_MAP_JS)
        fn_src = _extract_function(src, "requestJourney")
        assert "courseId !== state.courseId" in fn_src

    def test_refresh_after_map_exclusion_change_discards_stale_course_response(self):
        """指摘1（本チケット）: 「地図には反映しない/戻す」後の再取得
        (``refreshAfterMapExclusionChange``) にも同型ガードが必要
        （``loadNetwork`` の遅延応答が別コースの UI に描画されるのを防ぐ）。
        """
        src = _read(PERSONAL_MAP_JS)
        fn_src = _extract_function(src, "refreshAfterMapExclusionChange")
        assert "courseId !== state.courseId" in fn_src

    def test_all_load_network_consumers_have_course_switch_guard(self):
        """構造検査: ``loadNetwork(`` を ``.then`` で消費する関数を IIFE 全体から列挙し、
        すべてに courseId 突き合わせガードがあることを固定する（個別関数名の列挙し忘れに
        よる回帰を防ぐ）。函数名の列挙は ``_extract_function`` と同じ 2-space 関数宣言様式に
        依存する。関数自身の宣言行（例: ``function loadNetwork(courseId) {``）による
        自己名一致の誤検知を避けるため、本体は最初の ``{`` より後だけを見る。
        """
        src = _read(PERSONAL_MAP_JS)
        names = re.findall(r"\n  function (\w+)\(", src)
        assert names, "personal-map.js の関数宣言が見つかりません"

        consumers = []
        for name in names:
            fn_src = _extract_function(src, name)
            body_only = fn_src[fn_src.index("{") + 1 :]
            if "loadNetwork(" in body_only:
                consumers.append((name, fn_src))

        # 現在判明している消費者が列挙から漏れていないこと（列挙ロジック自体の回帰防止）
        found_names = {name for name, _ in consumers}
        assert {
            "onToggleChange",
            "annotateTrajectoryList",
            "refreshAfterMapExclusionChange",
        } <= found_names

        for name, fn_src in consumers:
            has_guard = (
                "courseId !== state.courseId" in fn_src
                or "courseId !== currentCourseId()" in fn_src
            )
            assert has_guard, (
                f"{name}: loadNetwork(...) の消費側に courseId 突き合わせガードがありません"
            )


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
