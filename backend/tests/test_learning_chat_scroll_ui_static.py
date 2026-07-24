"""学習チャットUXの2機能に対する静的ガードレール。

対象:
- frontend/public/js/app.js
  - 機能1（回答着地点の変更）: renderChat() は送信直後に限り「新しい問い」
    （user メッセージ `#msg-<id>`）の先頭へスクロールし、消費後にモジュール変数
    `_pendingScrollMsgId` をクリアする。それ以外の renderChat 呼び出し経路
    （トピック切替・履歴ロード・送信中の typing 表示・メッセージ編集/削除・
    クリア等）は従来どおり `ca.scrollTop = ca.scrollHeight` の最下部スクロールを
    維持しなければならない。
  - 機能2（教材区画の自動圧縮/復元）: `sendMessage` 冒頭で
    `collapseMaterialForChat()` を呼び、対話に属する領域の外側クリックで
    `restoreMaterialRegion()` を呼ぶ。分割ハンドルを手動でつかんだら自動制御を
    放棄する。どちらの関数も、ユーザーが手動ドラッグで調整した高さの永続化先
    （localStorage の SPLIT_STORAGE_KEY）へは一切書き込まない。
- frontend/public/css/styles.css
  - `.material-region.mr-animating` の圧縮/復元アニメーション用 transition。

guardrail_helpers.extract_function_source は Python の `def` を前提にしており
JS の `function` 宣言には使えないため、ここでは素朴な文字列スライスで関数本体を
切り出す（他の *_ui_static.py と同じ流儀）。app.js のトップレベル関数は
2-space インデントで宣言され、ネストしたブロックの閉じ括弧は必ずそれより
深い字下げになるため、次の "\\n  }\\n"（2-space 閉じ括弧）を関数の終端とみなせる。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _extract_js_function(src: str, signature: str) -> str:
    """`signature`（例: "function renderChat() {"）から、次のトップレベル
    （2-space インデント）閉じ括弧までを切り出す。"""
    start = src.index(signature)
    end = src.index("\n  }\n", start)
    return src[start:end]


# ---------------------------------------------------------------------------
# 機能1: renderChat の着地点分岐
# ---------------------------------------------------------------------------


class TestFeature1PendingScrollBranch:
    def test_render_chat_branches_on_pending_scroll_msg_id(self):
        src = _read(APP_JS)
        body = _extract_js_function(src, "function renderChat() {")
        assert "if (_pendingScrollMsgId) {" in body

    def test_render_chat_scrolls_to_msg_element(self):
        src = _read(APP_JS)
        body = _extract_js_function(src, "function renderChat() {")
        assert 'document.getElementById("msg-" + _pendingScrollMsgId)' in body

    def test_render_chat_clears_pending_scroll_after_consuming(self):
        """消費後にクリアしないと、無関係な後続 renderChat 呼び出し（トピック切替・
        履歴ロード等）へ誤ってスクロール指示が漏れ出す。"""
        src = _read(APP_JS)
        body = _extract_js_function(src, "function renderChat() {")
        assert "_pendingScrollMsgId = null;" in body
        # クリアは "if (_pendingScrollMsgId) { ... }" の内側（外側の else に入る前）で
        # 行われること。内部にはネストした else（scrollTo 分岐・要素未検出分岐）も
        # あるため、外側の if と同じ 4-space インデントの "} else {" を終端マーカーに
        # 使って区別する。
        branch_start = body.index("    if (_pendingScrollMsgId) {")
        clear_idx = body.index("_pendingScrollMsgId = null;")
        outer_else_idx = body.index("\n    } else {", branch_start)
        assert branch_start < clear_idx < outer_else_idx

    def test_render_chat_still_falls_back_to_bottom_scroll(self):
        """トピック切替・履歴ロード・送信中の typing 表示など、送信直後以外の
        renderChat 呼び出しは従来どおり最下部スクロールを維持する。"""
        src = _read(APP_JS)
        body = _extract_js_function(src, "function renderChat() {")
        # else 節（_pendingScrollMsgId 未設定時）と、msg 要素が見つからない場合の
        # フォールバックの両方に残っている必要がある。
        assert body.count("ca.scrollTop = ca.scrollHeight;") >= 2


# ---------------------------------------------------------------------------
# 機能1/2: sendMessage の配線
# ---------------------------------------------------------------------------


class TestSendMessageWiring:
    def test_send_message_collapses_material_region_at_start(self):
        src = _read(APP_JS)
        body = _extract_js_function(src, "async function sendMessage(text, actionPayload) {")
        assert "collapseMaterialForChat();" in body

    def test_send_message_sets_pending_scroll_id_before_final_render(self):
        src = _read(APP_JS)
        body = _extract_js_function(src, "async function sendMessage(text, actionPayload) {")
        assert "_pendingScrollMsgId = userMsgId;" in body
        # 同期的に直後の renderChat() で消費されること（間に await 等の隙間を空けない）。
        set_idx = body.index("_pendingScrollMsgId = userMsgId;")
        render_idx = body.index("renderChat();", set_idx)
        assert set_idx < render_idx
        assert "await" not in body[set_idx:render_idx]


# ---------------------------------------------------------------------------
# 機能2: 教材区画の自動圧縮/復元
# ---------------------------------------------------------------------------


class TestMaterialCollapseRestore:
    def test_collapse_and_restore_never_write_to_local_storage(self):
        """一時的な UI 状態（自動圧縮/復元）は、ユーザーが分割ハンドルで手動調整した
        高さの永続化先（localStorage）に書き込んではならない。"""
        src = _read(APP_JS)
        collapse_body = _extract_js_function(src, "function collapseMaterialForChat() {")
        restore_body = _extract_js_function(src, "function restoreMaterialRegion() {")
        assert "localStorage.setItem" not in collapse_body
        assert "localStorage.setItem" not in restore_body

    def test_outside_click_listener_exists_and_excludes_dialog_widgets(self):
        src = _read(APP_JS)
        init_input_body = _extract_js_function(src, "function initInput() {")
        assert 'document.addEventListener("click", function (e) {' in init_input_body
        click_start = init_input_body.index('document.addEventListener("click", function (e) {')
        click_block = init_input_body[click_start:]
        assert "_matCollapse.active" in click_block
        assert "restoreMaterialRegion();" in click_block
        assert "#chat-area" in click_block
        assert "#src-popup" in click_block

    def test_split_handle_pointerdown_relinquishes_auto_control(self):
        """分割ハンドルを手動でつかんだら、以後の自動圧縮/復元制御を放棄する
        （_matCollapse.active を落として手動優先にする）。"""
        src = _read(APP_JS)
        init_split_body = _extract_js_function(src, "function initSplitHandle() {")
        pd_sig = 'handle.addEventListener("pointerdown", function (e) {'
        assert pd_sig in init_split_body
        pd_start = init_split_body.index(pd_sig)
        pd_end = init_split_body.index("\n    });", pd_start)
        pd_block = init_split_body[pd_start:pd_end]
        assert "_matCollapse.active = false;" in pd_block
        # 手動優先の解除は、ドラッグ開始フラグを立てるより前に行われること。
        assert pd_block.index("_matCollapse.active = false;") < pd_block.index("dragging = true;")


# ---------------------------------------------------------------------------
# styles.css
# ---------------------------------------------------------------------------


class TestStylesCssMrAnimating:
    def test_mr_animating_transition_defined(self):
        src = _read(STYLES_CSS)
        assert ".material-region.mr-animating {" in src
        start = src.index(".material-region.mr-animating {")
        end = src.index("}", start)
        block = src[start:end]
        assert "transition" in block
        assert "height" in block
