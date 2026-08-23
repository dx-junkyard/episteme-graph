"""鏡面化 move（EX-3b 裁定）の学習者フロントエンド静的ガードレール。

正本: docs/features/seminar_brief_mirroring_design.md §2（確定仕様 1〜7）+ §3 精査記録。
  ①    サーバが 〔鏡〕マーカーから決定論抽出した `LearningChatResponse.mirror`（{text}）
        のみを描画する — フロントに鏡の抽出 regex を書かせない（§3 精査①）
  ⑤    鏡文は AI 由来であることを本人発話と視覚区別する（.mirror-block + 固定ラベル）
  ⑥/⑤ 訂正チップは入力欄への文言プリフィルのみ（API は呼ばない — 送信は本人の通常発話
        として既存の tension/anchor digest 弁に流れる, §3 精査⑤）
  ④    鏡文を localStorage へ保存しない・鏡文そのものを再送信しない（窓の外へ持ち出さない）
  レガシー: 履歴に残った 〔鏡〕マーカーは剥がして本文表示（鏡ブロックの再構成はしない）

バックエンド（`core/discuss/mirroring.py` + learning.py の抽出位置）は
`test_mirroring_prompt_guardrails.py` / `test_discuss_mode.py` 側が担当。
ここでは app.js / styles.css の静的契約のみを検証する。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
MIRRORING_PY = BACKEND / "core" / "discuss" / "mirroring.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _fn_block(src: str, signature: str) -> str:
    start = src.index(signature)
    return src[start : src.index("\n  }", start)]


def _mirror_wiring_block() -> str:
    """renderChat 内の [data-mirror-prefill] 配線ブロックだけを取り出す。"""
    src = _read(APP_JS)
    start = src.index('ca.querySelectorAll("[data-mirror-prefill]")')
    return src[start : src.index("});", src.index("input.focus();", start)) + 3]


# ===========================================================================
# 1. 鏡ブロックの描画（EX-3b⑤: AI 由来の視覚区別）
# ===========================================================================


class TestMirrorBlockRendering:
    def test_render_function_exists_and_guards_the_shape(self):
        src = _read(APP_JS)
        block = _fn_block(src, "function renderMirrorBlock(msg) {")
        assert 'if (!msg || !msg.mirror || !msg.mirror.text) return "";' in block

    def test_block_and_label_markup(self):
        src = _read(APP_JS)
        block = _fn_block(src, "function renderMirrorBlock(msg) {")
        assert '<div class="mirror-block">' in block
        assert '<div class="mirror-label">AIによる言い直し</div>' in block

    def test_mirror_text_is_escaped(self):
        src = _read(APP_JS)
        block = _fn_block(src, "function renderMirrorBlock(msg) {")
        assert "escHtml(msg.mirror.text)" in block

    def test_mirror_block_is_rendered_before_the_answer_body(self):
        src = _read(APP_JS)
        stmt_start = src.index("renderMirrorBlock(msg) +")
        stmt = src[src.rindex("html +=", 0, stmt_start) : src.index(";", stmt_start)]
        assert '<div class="mg ai' in stmt
        assert stmt.index("renderMirrorBlock(msg)") < stmt.index("renderAiContent(msg.content, msg)")

    def test_frontend_never_extracts_the_mirror_itself(self):
        """§3 精査①: 構造化は必ずサーバ側。フロントは msg.mirror のみを読む。"""
        src = _read(APP_JS)
        block = _fn_block(src, "function renderMirrorBlock(msg) {")
        assert "msg.content" not in block
        assert "〔" not in block

    def test_response_field_is_kept_on_the_in_memory_message(self):
        src = _read(APP_JS)
        assert "mirror: data.mirror || null," in src


# ===========================================================================
# 2. 訂正チップ（プリフィルのみ・fetch なし・新確定経路なし）
# ===========================================================================


class TestCorrectionChips:
    def test_exactly_two_chips_with_fixed_prefill_texts(self):
        src = _read(APP_JS)
        block = _fn_block(src, "function renderMirrorBlock(msg) {")
        assert 'data-mirror-prefill="そのとおりです。">そのとおり</button>' in block
        assert 'data-mirror-prefill="少し違います。">少し違う</button>' in block
        assert block.count("data-mirror-prefill=") == 2

    def test_chips_only_prefill_the_input_and_focus(self):
        wiring = _mirror_wiring_block()
        assert 'document.getElementById("chat-input")' in wiring
        assert 'input.value = this.getAttribute("data-mirror-prefill") || "";' in wiring
        assert "input.focus();" in wiring

    def test_chips_never_call_the_network_or_send(self):
        """§3 精査⑤: チップは送信しない。送信は本人の通常発話（既存の弁）に委ねる。"""
        wiring = _mirror_wiring_block()
        for banned in ("apiFetch", "fetch(", "sendMessage", "XMLHttpRequest"):
            assert banned not in wiring, banned
        src = _read(APP_JS)
        block = _fn_block(src, "function renderMirrorBlock(msg) {")
        for banned in ("apiFetch", "fetch(", "sendMessage"):
            assert banned not in block, banned


# ===========================================================================
# 3. 窓の外へ持ち出さない（localStorage 非保存・鏡文の再送信なし）
# ===========================================================================


class TestNoEgressOutsideTheWindow:
    def test_mirror_never_touches_localstorage(self):
        src = _read(APP_JS)
        for line in src.splitlines():
            if "localStorage" in line:
                assert "mirror" not in line.lower(), line.strip()

    def test_mirror_text_is_never_resent_as_a_message(self):
        """再送信されるのはチップの固定文言（本人が編集可能なプリフィル）だけ。"""
        wiring = _mirror_wiring_block()
        assert "msg.mirror" not in wiring
        assert ".mirror.text" not in wiring


# ===========================================================================
# 4. レガシーマーカーの剥がし（履歴復元時に可視テキストとして漏らさない）
# ===========================================================================


class TestLegacyMarkerStripping:
    def test_render_ai_content_strips_the_mirror_markers(self):
        src = _read(APP_JS)
        block = _fn_block(src, "function renderAiContent(text, msg) {")
        assert re.search(r"\.replace\(/〔\\/\?鏡〕/g,\s*\"\"\)", block), (
            "renderAiContent に 〔鏡〕/〔/鏡〕 マーカー剥がしが無い"
        )

    def test_marker_vocabulary_matches_the_backend_extractor(self):
        """マーカー語彙のドリフト検出: サーバの決定論抽出と同じ 〔鏡〕/〔/鏡〕。"""
        backend_src = _read(MIRRORING_PY)
        assert "〔鏡〕" in backend_src
        assert "〔/鏡〕" in backend_src


# ===========================================================================
# 5. スタイル（控えめな区別 — 左ボーダー + 淡背景）
# ===========================================================================


class TestMirrorStyles:
    def test_mirror_block_class_is_styled(self):
        css = _read(STYLES_CSS)
        start = css.index(".mirror-block {")
        block = css[start : css.index("}", start)]
        assert "border-left" in block
        assert "background" in block

    def test_supporting_classes_exist(self):
        css = _read(STYLES_CSS)
        for cls in (".mirror-label", ".mirror-text", ".mirror-chips", ".mirror-chip"):
            assert cls in css, cls
