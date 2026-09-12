"""画面文脈アダプター Phase 4（学習チャット） — フロントエンドの静的検証。

正本: ``docs/features/assistant_screen_adapter_design.md`` §11.2 / §11.8 / §11.9。

学習画面が AI 対話へ渡すのは**参照だけ**（SA1）— 選択中の要素の種別と ID・表示中の
トピックとスライド・表示モード・画面に出ている ⚓ チップの ID と 40 字以内の題名まで。
描画された本文（チャンク text / display_text / summary / latex）も、選択した逐語
（``selection_text`` は従来どおり独立フィールド = §11.2 の理由②）も入れない。

Node を起動せず ``app.js`` のソース検査で固定する（``test_graph_review_ui_static.py``
の ``TestScreenContext`` と同じ作法）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = (ROOT / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")


_NEXT_TOP_LEVEL_FN = re.compile(r"\n  (?:async )?function ")


def _function_block(name: str) -> str:
    """``function <name>(`` から次のトップレベル関数宣言までを切り出す。"""
    start = APP_JS.index("function %s(" % name)
    m = _NEXT_TOP_LEVEL_FN.search(APP_JS, start + 1)
    assert m is not None, name
    return APP_JS[start:m.start()]


def _screen_context_source() -> str:
    """getScreenContext と、そこから呼ぶ画面文脈ヘルパ群のソース。"""
    return "\n".join(
        _function_block(name)
        for name in (
            "screenContextTitle",
            "screenContextElementType",
            "screenContextEntities",
            "getScreenContext",
        )
    )


class TestPublicApi:
    def test_screen_id_is_learning(self):
        assert 'SCREEN_CONTEXT_SCREEN = "learning"' in APP_JS

    def test_window_contract(self):
        # 契約: window.<Screen>.getScreenContext()（SA1 / §11.8）。
        assert "window.LearningScreen" in APP_JS
        assert "getScreenContext: getScreenContext" in APP_JS


class TestContractShape:
    def test_top_level_keys(self):
        block = _function_block("getScreenContext")
        for key in (
            "screen: SCREEN_CONTEXT_SCREEN",
            "selection: selection",
            "view: view",
            "visible_entities:",
        ):
            assert key in block, key

    def test_selection_keys(self):
        block = _function_block("getScreenContext")
        for key in (
            "course_id:",
            "topic_id:",
            "kind:",
            "segment_id",
            "element_id",
            "element_type",
            "chunk_id",
        ):
            assert key in block, key

    def test_selection_kind_vocabulary(self):
        block = _function_block("getScreenContext")
        # element_id → element / segment_id → segment / それ以外 topic（§11.2）。
        assert '"element"' in block and '"segment"' in block and '"topic"' in block

    def test_segment_is_only_declared_when_real(self):
        """通常チャットの currentAnchor().segment_id は常に 0。そのまま載せない。"""
        block = _function_block("getScreenContext")
        assert "p.selection_segment_id" in block
        assert 'resolveScreenMode() === "lecture"' in block

    def test_view_keys(self):
        block = _function_block("getScreenContext")
        assert "mode: resolveScreenMode()" in block
        assert "precision_reading: isPrecisionReadingOn(" in block
        # discuss のときだけスコープを申告する。
        assert "discuss_scope" in block

    def test_element_type_vocabulary(self):
        block = _function_block("screenContextElementType")
        assert 'SCREEN_CONTEXT_ELEMENT_TYPES = ["component", "claim", "equation", "figure"]' in APP_JS
        # structure_anchor 語彙の "formula" は契約語彙 "equation" に寄せる。
        assert '"formula"' in block and '"equation"' in block
        # 判別できなければ種別を名乗らない（推測で埋めない）。
        assert 'return "";' in block

    def test_latch_keeps_raw_kind(self):
        # ラッチ元の生 kind（data-evidence-ref の "kind:id" 前半）を additive に保持する。
        assert "kind: kind," in APP_JS
        # 送信ボディへは載せず、screen_context の種別解決にだけ使う。
        assert "_latchKind = _latchState.anchor.kind" in APP_JS
        send = _function_block("sendMessage")
        latch_at = send.index("_latchKind = _latchState.anchor.kind")
        clear_at = send.index("clearMaterialLatch();", latch_at)
        assert latch_at < clear_at  # クリアより前に控える


class TestSendBody:
    def test_body_carries_screen_context(self):
        send = _function_block("sendMessage")
        assert "screen_context: getScreenContext(" in send

    def test_message_field_is_unchanged(self):
        send = _function_block("sendMessage")
        assert "message: text," in send
        assert "message_id: userMsgId," in send
        assert "screen_mode: resolveScreenMode()," in send

    def test_single_post_path(self):
        """全送信経路（テキスト・🤖 音声・チップ・discuss）が sendMessage に合流する。

        ``/chat`` への POST が sendMessage 以外に実装されていないこと（二重実装禁止）。
        履歴取得（GET）・履歴削除（DELETE）は対象外。
        """
        posts = []
        for m in re.finditer(r'"/chat"', APP_JS):
            window = APP_JS[m.end(): m.end() + 400]
            if 'method: "POST"' in window:
                posts.append(m.start())
        assert len(posts) == 1, posts
        send_start = APP_JS.index("async function sendMessage(")
        send_end = _NEXT_TOP_LEVEL_FN.search(APP_JS, send_start + 1).start()
        assert send_start < posts[0] < send_end


class TestNoBodyText:
    """SA1: 描画された本文・選択逐語を screen_context に入れない。"""

    def test_forbidden_fields(self):
        source = _screen_context_source()
        for forbidden in (
            "innerText",
            "textContent",
            "display_text",
            ".summary",
            ".latex",
            "selection_text",
            "querySelector",
        ):
            assert forbidden not in source, forbidden

    def test_selection_text_stays_its_own_field(self):
        # 逐語は従来どおり独立フィールドで送る（§11.2 の理由②）。
        send = _function_block("sendMessage")
        assert "payload.selection_text = state.pendingSelection.text;" in send


class TestBounded:
    def test_title_is_truncated(self):
        assert "SCREEN_CONTEXT_MAX_TITLE_CHARS = 40" in APP_JS
        block = _function_block("screenContextTitle")
        assert "slice(0, SCREEN_CONTEXT_MAX_TITLE_CHARS)" in block

    def test_entities_are_capped(self):
        assert "SCREEN_CONTEXT_MAX_ENTITIES = 20" in APP_JS
        block = _function_block("screenContextEntities")
        assert "SCREEN_CONTEXT_MAX_ENTITIES" in block
        # 供給元は既存の evidence_items（新しい取得・DOM 走査をしない）。
        assert "state.topicMaterial" in block
        assert "evidence_items" in block

    def test_entities_are_deduplicated(self):
        block = _function_block("screenContextEntities")
        assert "seen[key]" in block


class TestFailSoft:
    def test_getter_never_throws(self):
        block = _function_block("getScreenContext")
        assert "try {" in block
        assert "catch (_)" in block
        assert "return null;" in block
