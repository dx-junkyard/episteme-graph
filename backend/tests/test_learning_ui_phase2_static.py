"""学習画面 UI 再編 Phase 2（インスペクト・モード, フロントエンド実装）の静的ガードレール。

設計正本: docs/features/learning_ui_inspect_hover_design.md §4/§5/§8/§12。

バックエンド（GET /api/learning/help/ui-anchors, POST /api/learning/help/ui-anchor-events,
LearningChatRequest.ui_anchor, backend/core/help_kb/ui_anchors.py の KNOWN_UI_ANCHOR_IDS /
UI_ANCHORS）は別エージェントが実装済み（test_help_ui_anchors.py 参照）。ここでは
frontend/public/{index.html, js/app.js, css/styles.css} 側の静的契約のみを検証する:

1. IH4: インスペクト中のクリックは capture フェーズで解除のみを行う
   （stopPropagation + preventDefault + setInspectMode(false) の共存、許可リスト）。
2. §4 常設バナーの文言・マークアップ・CSS。
3. IH8: 未整備アンカーの固定事実文（捏造しない）。
4. IH10: no_hit 記録は1秒滞留 + セッション内 anchor 単位 dedup（Set 使用）。
   ホバーの生イベント自体は記録しない。
5. IH2: ツールチップ表示経路に LLM/チャット送信呼び出しが無い。
6. data-ui-anchor の値が KNOWN_UI_ANCHOR_IDS（正本）の部分集合であること。
7. §4.1/§8: インスペクト中の発話（composer/音声）が ui_anchor を payload に載せて
   usage_help ルートへ確定すること。
8. §5.2: UI アンカー表はログイン時に1回だけフェッチしてキャッシュする。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"

BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb.ui_anchors import KNOWN_UI_ANCHOR_IDS  # noqa: E402


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


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


class TestInspectClickReleaseOnly:
    """IH4: インスペクト中のクリックは解除のみ。capture フェーズで stopPropagation +
    setInspectMode(false) を同時に行い、下の操作（送信・削除等）へ伝播させない。"""

    def test_capture_click_listener_stops_propagation_and_releases(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initInspectMode() {")
        click_start = block.index('document.addEventListener("click", function (e) {')
        click_end = block.index("}, true);", click_start)
        click_block = block[click_start:click_end]
        assert "e.preventDefault();" in click_block
        assert "e.stopPropagation();" in click_block
        assert "setInspectMode(false);" in click_block
        # 許可リスト（トグルボタン自身とバナー）は release より前に早期 return する。
        allow_idx = click_block.index("#help-usage-btn, #inspect-banner")
        release_idx = click_block.index("setInspectMode(false);")
        assert allow_idx < release_idx

    def test_click_listener_registered_with_capture_phase(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initInspectMode() {")
        assert '}, true);' in block  # addEventListener の第三引数 = capture:true

    def test_escape_key_also_releases(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initInspectMode() {")
        assert '"Escape"' in block
        assert "setInspectMode(false);" in block


class TestInspectBannerAndCss:
    def test_banner_markup_and_wording(self):
        html = _read(INDEX_HTML)
        assert 'id="inspect-banner"' in html
        idx = html.index('id="inspect-banner"')
        tag_start = html.rindex("<div", 0, idx)
        tag_end = html.index(">", idx)
        tag = html[tag_start:tag_end + 1]
        assert "hidden" in tag
        content_end = html.index("</div>", tag_end)
        content = html[tag_end + 1:content_end]
        assert "使い方を確認中" in content
        assert "Esc" in content

    def test_tooltip_container_present(self):
        html = _read(INDEX_HTML)
        assert 'id="inspect-tooltip"' in html

    def test_css_defines_inspect_classes(self):
        css = _read(STYLES_CSS)
        for selector in (
            ".inspect-banner {",
            ".inspect-tooltip {",
        ):
            assert selector in css, f"{selector} が styles.css に見つかりません"
        assert "cursor: help" in css

    def test_set_inspect_mode_toggles_body_class_and_cleans_up(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function setInspectMode(active) {")
        assert 'classList.toggle("inspect-mode", active)' in block
        assert "banner.hidden = !active;" in block
        assert "hideInspectTooltip();" in block
        assert "_inspectState.hoveredAnchorId = null;" in block


class TestUndocumentedFixedText:
    """IH8: 未整備は固定事実文 + no_hit 記録。捏造しない。"""

    def test_fixed_fact_text_present(self):
        js = _read(APP_JS)
        assert "この部分の説明はまだ用意されていません" in js

    def test_show_tooltip_reports_no_hit_only_when_undocumented(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function showInspectTooltip(anchorEl, anchorId) {")
        assert "この部分の説明はまだ用意されていません" in block
        undoc_idx = block.index("この部分の説明はまだ用意されていません")
        timer_idx = block.index("reportInspectNoHit(anchorId);")
        assert undoc_idx < timer_idx
        # entry がある（マップ済み）場合の分岐には no_hit 記録を呼ばない。
        entry_branch = block[block.index("if (entry) {"):block.index("} else {")]
        assert "reportInspectNoHit" not in entry_branch


class TestNoHitDwellAndDedup:
    """IH10: 1秒以上の滞留のみ記録・セッション内 anchor 単位で1回まで（生ホバーは記録しない）。"""

    def test_dwell_threshold_is_at_least_one_second(self):
        js = _read(APP_JS)
        assert "INSPECT_NO_HIT_DWELL_MS = 1000;" in js
        block = _extract_function_body(js, "function showInspectTooltip(anchorEl, anchorId) {")
        assert "setTimeout(function () {" in block
        assert "INSPECT_NO_HIT_DWELL_MS" in block

    def test_dedup_uses_set_and_checked_before_reporting(self):
        js = _read(APP_JS)
        assert "reportedNoHitIds: new Set()," in js
        block = _extract_function_body(js, "function reportInspectNoHit(anchorId) {")
        assert "_inspectState.reportedNoHitIds.has(anchorId)" in block
        assert "_inspectState.reportedNoHitIds.add(anchorId);" in block
        has_idx = block.index(".has(anchorId)")
        add_idx = block.index(".add(anchorId);")
        assert has_idx < add_idx

    def test_mouseover_alone_does_not_report_no_hit(self):
        """ホバーの生イベント（mouseover ハンドラ自体）は no_hit を直接呼ばない
        （滞留タイマー経由でのみ発火する、§5.4/IH10）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initInspectMode() {")
        mo_start = block.index('document.addEventListener("mouseover"')
        mo_end = block.index("});", mo_start)
        mouseover_block = block[mo_start:mo_end]
        assert "reportInspectNoHit" not in mouseover_block


class TestTooltipHasNoGenerativeCall:
    """IH2: AI アシスタント OFF（ツールチップ経路）は LLM・チャット API を一切呼ばない。"""

    def test_show_tooltip_does_not_call_chat_or_send_message(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function showInspectTooltip(anchorEl, anchorId) {")
        assert "sendMessage(" not in block
        assert "/chat" not in block
        assert "apiFetch(" not in block  # 既に持っているキャッシュ (_inspectState.anchors) のみ参照

    def test_fetch_ui_anchors_uses_dedicated_endpoint_not_chat(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function fetchUiAnchorsOnce() {")
        assert "/learning/help/ui-anchors" in block
        assert "/chat" not in block
        assert "sendMessage(" not in block


class TestUiAnchorIdsAreKnown:
    """data-ui-anchor 属性の値は backend/core/help_kb/ui_anchors.py の
    KNOWN_UI_ANCHOR_IDS（正本・17種）の部分集合であること。"""

    @staticmethod
    def _collect_anchor_ids() -> set[str]:
        html = _read(INDEX_HTML)
        js = _read(APP_JS)
        ids = set(re.findall(r'data-ui-anchor="([a-zA-Z0-9_.\-]+)"', html))
        ids |= set(re.findall(r'data-ui-anchor="([a-zA-Z0-9_.\-]+)"', js))
        return ids

    def test_all_used_anchor_ids_are_known(self):
        used = self._collect_anchor_ids()
        assert used, "data-ui-anchor がフロントに1つも見つかりません"
        unknown = used - set(KNOWN_UI_ANCHOR_IDS)
        assert not unknown, f"KNOWN_UI_ANCHOR_IDS に無いアンカーID: {unknown}"

    def test_expected_anchors_are_present(self):
        used = self._collect_anchor_ids()
        for expected in (
            "composer.input", "composer.send", "composer.voice",
            "topbar.inspect", "topbar.atlas", "topbar.my-map",
            "material.next-topic", "material.lecture-toggle",
            "rightpanel.tab-context", "rightpanel.tab-progress", "rightpanel.tab-sources",
            "rightpanel.clear-history", "sidebar.mode-sequential", "sidebar.mode-discuss",
            "sidebar.course-tree", "discuss.scope-toggle", "discuss.end",
        ):
            assert expected in used, f"{expected} が data-ui-anchor に見つかりません"


class TestInspectUtteranceCarriesUiAnchor:
    """§4.1/§8: インスペクト ON 中の発話（composer/音声）は無条件で usage_help ルートに
    確定し、ラッチ中（最後にホバーした）UI アンカーを ui_anchor として送る。"""

    def test_send_with_branches_to_usage_help_when_inspect_active(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function sendWith(mode) {")
        assert "_inspectState.active" in block
        assert "sendUsageHelpMessage(text);" in block
        # インスペクト分岐は on_path/explore/discuss 判定より手前で return する。
        inspect_idx = block.index("_inspectState.active")
        onpath_idx = block.index('mode === "on_path"')
        assert inspect_idx < onpath_idx

    def test_send_usage_help_message_includes_ui_anchor(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function sendUsageHelpMessage(typedText) {")
        assert "ui_anchor: _inspectState.hoveredAnchorId || null" in block
        assert 'support_action: "usage_help"' in block

    def test_voice_segment_branches_to_usage_help_when_inspect_active(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function handleVoiceSegment() {")
        assert "_inspectState.active" in block
        assert 'support_action: "usage_help"' in block
        assert "ui_anchor: _inspectState.hoveredAnchorId || null" in block
        # インスペクト OFF 時の既存 casual 経路も残っていること。
        assert '{ intent_mode: "casual" }' in block

    def test_hovered_anchor_id_updated_on_tooltip_show(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function showInspectTooltip(anchorEl, anchorId) {")
        assert "_inspectState.hoveredAnchorId = anchorId;" in block


class TestUiAnchorsFetchedOnce:
    """§5.2: ログイン時に1回だけフェッチしてキャッシュする（ホバーごとの API コールはしない）。"""

    def test_fetch_ui_anchors_once_has_idempotent_guard(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function fetchUiAnchorsOnce() {")
        assert "if (_inspectState.anchorsFetched) return;" in block
        assert "_inspectState.anchorsFetched = true;" in block
        guard_idx = block.index("if (_inspectState.anchorsFetched) return;")
        set_idx = block.index("_inspectState.anchorsFetched = true;")
        assert guard_idx < set_idx

    def test_fetch_ui_anchors_called_from_init_app_and_not_from_hover_handler(self):
        js = _read(APP_JS)
        init_block = _extract_function_body(js, "async function initApp() {")
        assert "fetchUiAnchorsOnce();" in init_block
        inspect_block = _extract_function_body(js, "function initInspectMode() {")
        mo_start = inspect_block.index('document.addEventListener("mouseover"')
        mo_end = inspect_block.index("});", mo_start)
        assert "fetchUiAnchorsOnce" not in inspect_block[mo_start:mo_end]

    def test_fetch_failure_falls_back_to_empty_object_fail_closed(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function fetchUiAnchorsOnce() {")
        assert "_inspectState.anchors = {};" in block
