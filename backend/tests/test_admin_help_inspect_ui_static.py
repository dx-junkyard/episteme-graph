"""管理画面「？使い方」インスペクト・モード（学習画面 index.html/app.js の同型移植）の
フロントエンド実装（frontend/public/admin.html, js/admin-help-inspect.js,
js/admin-assistant.js, css/styles.css）に対する静的ガードレール。

バックエンド（`GET /api/admin/assistant/help/ui-anchors` /
`POST /api/admin/assistant/help/ui-anchor-events` / ADMIN_UI_ANCHORS registry）は
並行実装中のため、ここでは JS/HTML/CSS 側の静的契約のみを検証する
（`backend/tests/test_help_usage_ui_static.py` の学習側と同じ流儀）:

1. ヘッダーに `#admin-help-usage-btn` / `#admin-inspect-banner` /
   `#admin-inspect-tooltip` が存在し、`data-ui-anchor="header.inspect"` を持つ。
2. admin.html が `js/admin-help-inspect.js` を読み込む（admin-assistant.js より前）。
3. `window.AdminHelpInspect` が契約どおりの4メソッドを公開する。
4. ホバー対象の解決は `closest("[data-ui-anchor]")` 委譲で行う（個別要素への
   バインドをしない）。
5. 未整備アンカーの固定事実文・no_hit の `ui-anchor-events` POST が存在する。
6. `setInterval`（ポーリング）を持ち込まない。
7. アンカー表のフェッチ（`fetchUiAnchorsOnce`）の呼び出し箇所は `init` の中だけ。
8. `admin-assistant.js` の `support_action` 付与は `AdminHelpInspect.isActive()`
   ガードの内側にある。
9. styles.css に `.admin-inspect-tooltip` / `.admin-inspect-banner` /
   `body.admin-inspect-mode` の定義と、disabled 要素の pointer-events:none ルールが
   存在する。

10. 網羅（統合フェーズで追加）: `KNOWN_ADMIN_UI_ANCHOR_IDS` の全IDが admin 側
    フロントソースに担体（`data-ui-anchor`）を持ち、逆に admin 側で使われる全IDが
    KNOWN に登録済みである（タイポ・登録漏れの双方向検出）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
ADMIN_HELP_INSPECT_JS = ROOT / "frontend" / "public" / "js" / "admin-help-inspect.js"
ADMIN_ASSISTANT_JS = ROOT / "frontend" / "public" / "js" / "admin-assistant.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class TestAdminHtmlHeader:
    def test_help_usage_button_present(self):
        src = _read(ADMIN_HTML)
        assert 'id="admin-help-usage-btn"' in src
        assert 'class="admin-help-usage-btn"' in src
        assert 'data-ui-anchor="header.inspect"' in src

    def test_inspect_banner_present(self):
        src = _read(ADMIN_HTML)
        assert 'id="admin-inspect-banner"' in src
        assert 'class="admin-inspect-banner"' in src
        assert "hidden" in src[src.index('id="admin-inspect-banner"') - 200 : src.index('id="admin-inspect-banner"') + 200]

    def test_inspect_tooltip_container_present(self):
        src = _read(ADMIN_HTML)
        assert 'id="admin-inspect-tooltip"' in src
        assert 'class="admin-inspect-tooltip"' in src

    def test_script_tag_loaded_before_admin_assistant(self):
        src = _read(ADMIN_HTML)
        assert "js/admin-help-inspect.js" in src
        idx_inspect = src.index("js/admin-help-inspect.js")
        idx_assistant = src.index("js/admin-assistant.js")
        assert idx_inspect < idx_assistant, (
            "admin-help-inspect.js は admin-assistant.js より前に読み込む必要がある"
        )


class TestAdminHelpInspectPublicApi:
    def test_window_admin_help_inspect_exposes_four_methods(self):
        src = _read(ADMIN_HELP_INSPECT_JS)
        assert "window.AdminHelpInspect = {" in src
        start = src.index("window.AdminHelpInspect = {")
        end = src.index("\n  };", start)
        block = src[start:end]
        assert "init:" in block
        assert "toggle:" in block
        assert "isActive:" in block
        assert "hoveredAnchorId:" in block

    def test_hover_resolution_uses_closest_data_ui_anchor_delegation(self):
        src = _read(ADMIN_HELP_INSPECT_JS)
        assert 'e.target.closest("[data-ui-anchor]")' in src
        # mouseover/mouseout の両方が委譲パターンを使うこと（個別要素バインドをしない）。
        assert src.count('e.target.closest("[data-ui-anchor]")') >= 2

    def test_no_hit_fixed_message_and_event_post(self):
        src = _read(ADMIN_HELP_INSPECT_JS)
        assert "この部分の説明はまだ用意されていません" in src
        assert "/admin/assistant/help/ui-anchor-events" in src
        start = src.index("function reportNoHit(anchorId) {")
        end = src.index("\n  }", start)
        block = src[start:end]
        assert '"POST"' in block
        assert '"no_hit"' in block

    def test_no_setinterval_polling(self):
        src = _read(ADMIN_HELP_INSPECT_JS)
        assert "setInterval" not in src

    def test_anchor_table_fetch_only_reachable_from_init(self):
        """`fetchUiAnchorsOnce()` の呼び出し箇所は `init` の中だけであること
        （ホバーごとの再フェッチや DOMContentLoaded 直結を禁止する）。"""
        src = _read(ADMIN_HELP_INSPECT_JS)
        assert "function fetchUiAnchorsOnce() {" in src
        call_sites = []
        idx = 0
        needle = "fetchUiAnchorsOnce();"
        while True:
            idx = src.find(needle, idx)
            if idx == -1:
                break
            call_sites.append(idx)
            idx += len(needle)
        assert call_sites, "fetchUiAnchorsOnce() の呼び出しが見つからない"
        init_start = src.index("init: function (opts) {")
        init_end = src.index("\n    },", init_start)
        for site in call_sites:
            assert init_start < site < init_end, (
                "fetchUiAnchorsOnce() は init 内以外から呼ばれてはならない"
            )

    def test_escape_key_and_click_release_only(self):
        src = _read(ADMIN_HELP_INSPECT_JS)
        assert '"Escape"' in src
        assert "setActive(false)" in src


class TestTooltipFitsWholeSection:
    """長いマニュアル節がツールチップで途中で切れないこと（学習画面側と同じ規約）。

    ツールチップは pointer-events:none で内部スクロールできないため、狭いまま縦に
    伸ばして max-height でクランプすると続きを読む手段がなくなる。まず幅を広げ、
    上下どちらにも収まらない場合は安全域いっぱいに出す。
    """

    def test_widths_escalate_before_clipping(self):
        src = _read(ADMIN_HELP_INSPECT_JS)
        assert "TOOLTIP_WIDTHS = [320, 460, 560];" in src
        assert "TOOLTIP_WIDTHS.length" in src
        assert "tip.style.maxWidth = tw" in src  # CSS の max-width を上書きする
        assert "th <= safeHeight" in src
        assert "tip.style.top = safeTop" in src

    def test_markdown_markers_stripped_for_display(self):
        src = _read(ADMIN_HELP_INSPECT_JS)
        assert "function plainManualText(text) {" in src
        assert "plainManualText(entry.body)" in src
        assert "escHtml(entry.body" not in src  # 生の Markdown をそのまま出さない


class TestAdminAssistantUsageHelpIntegration:
    def test_support_action_guarded_by_is_active(self):
        src = _read(ADMIN_ASSISTANT_JS)
        assert "window.AdminHelpInspect && window.AdminHelpInspect.isActive()" in src
        guard_idx = src.index("window.AdminHelpInspect && window.AdminHelpInspect.isActive()")
        block_end = src.index("\n    }", guard_idx)
        block = src[guard_idx:block_end]
        assert 'payload.support_action = "usage_help";' in block
        assert "payload.ui_anchor = window.AdminHelpInspect.hoveredAnchorId();" in block

    def test_support_action_assignment_not_unconditional(self):
        """`support_action` の代入自体が isActive() ガードより前に単独で
        現れないこと（無条件付与に戻っていないことの確認）。"""
        src = _read(ADMIN_ASSISTANT_JS)
        guard_idx = src.index("window.AdminHelpInspect && window.AdminHelpInspect.isActive()")
        before_guard = src[: src.index("function onSend()")]
        assert 'payload.support_action = "usage_help"' not in before_guard
        assert guard_idx > src.index("function onSend()")


class TestStylesCss:
    def test_admin_inspect_classes_defined(self):
        src = _read(STYLES_CSS)
        for selector in (
            ".admin-help-usage-btn {",
            ".admin-inspect-banner {",
            ".admin-inspect-tooltip {",
            ".admin-inspect-tooltip-source {",
            ".admin-inspect-tooltip-title {",
            ".admin-inspect-tooltip-body {",
        ):
            assert selector in src, f"{selector} が styles.css に見つかりません"
        assert "body.admin-inspect-mode [data-ui-anchor]" in src

    def test_disabled_elements_pointer_events_none_while_inspecting(self):
        """can_be_disabled 要素の無効化中もホバーを親ラッパーへ透過させるため、
        インスペクト中だけ disabled なフォーム部品を pointer-events:none にする。"""
        src = _read(STYLES_CSS)
        assert "body.admin-inspect-mode button:disabled" in src
        start = src.index("body.admin-inspect-mode button:disabled")
        end = src.index("}", start)
        block = src[start : end + 1]
        assert "select:disabled" in block
        assert "input:disabled" in block
        assert "textarea:disabled" in block
        assert "pointer-events: none;" in block


# admin 側フロントソース（学習側 app.js / index.html は対象外 — 学習アンカーは
# core/help_kb/ui_anchors.py + test_help_usage_ui_static.py が別途守る）。
_ADMIN_FRONTEND_SOURCES = [
    ADMIN_HTML,
    ADMIN_HELP_INSPECT_JS,
    ADMIN_ASSISTANT_JS,
    ROOT / "frontend" / "public" / "js" / "admin.js",
    ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js",
    ROOT / "frontend" / "public" / "js" / "admin-figure-studio.js",
    ROOT / "frontend" / "public" / "js" / "admin-llm-models.js",
    ROOT / "frontend" / "public" / "js" / "admin-release-review.js",
    ROOT / "frontend" / "public" / "js" / "admin-paper-discovery.js",
    ROOT / "frontend" / "public" / "js" / "admin-paper-radar.js",
    ROOT / "frontend" / "public" / "js" / "admin-next-steps.js",
    ROOT / "frontend" / "public" / "js" / "admin-manual-editor.js",
    ROOT / "frontend" / "public" / "js" / "admin-discuss-observation.js",
    ROOT / "frontend" / "public" / "js" / "admin-llm-usage.js",
    ROOT / "frontend" / "public" / "js" / "versioning.js",
    ROOT / "frontend" / "public" / "js" / "deliberation.js",
    ROOT / "frontend" / "public" / "js" / "doubt-atlas.js",
]


def _admin_frontend_blob() -> str:
    return "\n".join(_read(p) for p in _ADMIN_FRONTEND_SOURCES if p.exists())


class TestAnchorCoverage:
    """アンカー表とフロント担体の双方向網羅（登録漏れ・タイポの構造的検出）。"""

    def test_every_known_anchor_id_has_a_frontend_carrier(self):
        """KNOWN の全IDが admin フロントのどこかに文字列として現れること。

        属性直書き（HTML/JS 文字列テンプレート）・setAttribute・変数経由の組み立て
        （例: admin.js の ssGenerateButton が kind からIDを選ぶ）をまとめて拾うため、
        ID の素の出現で判定する（IDは purpose-built な語彙なので誤ヒットは実質ない）。
        """
        import sys

        backend_dir = str(ROOT / "backend")
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        from core.help_kb.admin_ui_anchors import KNOWN_ADMIN_UI_ANCHOR_IDS

        blob = _admin_frontend_blob()
        missing = sorted(a for a in KNOWN_ADMIN_UI_ANCHOR_IDS if a not in blob)
        assert not missing, (
            "KNOWN_ADMIN_UI_ANCHOR_IDS に登録されているのに frontend に担体が無い: "
            f"{missing}"
        )

    def test_every_frontend_anchor_value_is_known(self):
        """admin フロントの data-ui-anchor 値（静的に書かれたもの）が全て KNOWN にあること。

        1属性1ID（スペース区切りの複数IDは解決不能になるため禁止）もここで固定する。
        """
        import re
        import sys

        backend_dir = str(ROOT / "backend")
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        from core.help_kb.admin_ui_anchors import KNOWN_ADMIN_UI_ANCHOR_IDS

        blob = _admin_frontend_blob()
        used: set[str] = set()
        # HTML/JS 文字列テンプレート内の属性直書き（エスケープ引用符も許容）
        for m in re.finditer(r'data-ui-anchor\s*=\s*\\?["\']([^"\'\\]+)\\?["\']', blob):
            used.add(m.group(1))
        # setAttribute("data-ui-anchor", "<id>")
        for m in re.finditer(
            r'setAttribute\(\s*["\']data-ui-anchor["\']\s*,\s*["\']([^"\']+)["\']', blob
        ):
            used.add(m.group(1))
        bad = sorted(
            v for v in used
            if v not in KNOWN_ADMIN_UI_ANCHOR_IDS or " " in v
        )
        assert not bad, (
            "frontend の data-ui-anchor に KNOWN 未登録（または複数ID同居）の値がある: "
            f"{bad}"
        )
