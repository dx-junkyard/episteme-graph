"""学ぶ単位 P2-7 — 選択箇所の区画解決（フロントエンドの静的検証）。

正本: ``docs/features/learning_units_design.md`` §8。親文書 C-11 の原因は
「ここについて質問」が ``Session.currentAnchor().segment_id`` を使い、レクチャー
非再生時はそれが常に 0 だったこと。

Node を起動せず ``app.js`` のソース検査で固定する（``test_learning_screen_context_ui_static.py``
と同じ作法）。固定する事実:

- 教材区画の担体 ``data-segment-index`` が描画側にある（サーバの区画番号解決と同じ単位）。
- 選択の区画は**選択範囲の DOM 祖先**から取る（``Session.currentAnchor()`` に戻さない）。
- 決まらないときは ``|| 0`` で埋めず、``selection_segment_id`` を**送らない**。
- レクチャー再生中は従来どおり表示中スライドの区画。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = (ROOT / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")

_NEXT_TOP_LEVEL_FN = re.compile(r"\n  (?:async )?function ")


def _function_block(name: str) -> str:
    start = APP_JS.index("function %s(" % name)
    m = _NEXT_TOP_LEVEL_FN.search(APP_JS, start + 1)
    assert m is not None, name
    return APP_JS[start:m.start()]


class TestSegmentIndexCarrier:
    def test_material_chunk_declares_its_segment_index(self):
        block = _function_block("renderMaterialRegion")
        assert "data-segment-index" in block
        # 番号は配信された教材区画の表示順（forEach の index）。
        assert "forEach(function (chunk, segmentIndex)" in block
        assert "+ segmentIndex +" in block

    def test_carrier_is_on_the_material_chunk_element(self):
        block = _function_block("renderMaterialRegion")
        chunk_div = block.split('class="material-chunk"')[1].split(">")[0]
        assert "data-segment-index" in chunk_div


class TestSelectionSegmentResolution:
    def test_resolver_reads_the_selection_ancestor(self):
        block = _function_block("selectionSegmentIndex")
        assert "commonAncestorContainer" in block
        assert 'closest("[data-segment-index]")' in block

    def test_resolver_returns_null_when_undetermined(self):
        block = _function_block("selectionSegmentIndex")
        # 既定値 0 を返さない（C-11 の原因を再導入しない）。
        assert "return null;" in block
        assert "|| 0" not in block

    def test_lecture_playback_keeps_the_slide_segment(self):
        block = _function_block("selectionSegmentIndex")
        assert "lectureState.active" in block
        assert "lectureState.currentSegmentIndex" in block

    def test_ask_button_uses_the_resolver(self):
        block = _function_block("initSelectionAnchor")
        assert "state.pendingSelection = { text: text, segment_id: seg }" in block
        assert "var seg = selectionSegmentIndex(range);" in block
        # 旧実装（常に 0 を返す経路）へ戻さない。
        assert "Session.currentAnchor() || {}).segment_id || 0" not in block

    def test_quick_anchor_omits_the_key_when_undetermined(self):
        block = _function_block("initSelectionAnchor")
        assert "if (seg !== null) anchorPayload.selection_segment_id = seg;" in block
        assert "selection_segment_id: seg," not in block


class TestSendPath:
    def test_pending_selection_does_not_send_a_default_zero(self):
        block = _function_block("sendMessage")
        assert "payload.selection_text = state.pendingSelection.text;" in block
        segment_line = "payload.selection_segment_id = state.pendingSelection.segment_id;"
        assert segment_line in block
        guard = block.split(segment_line)[0]
        assert "state.pendingSelection.segment_id !== null" in guard
        assert "state.pendingSelection.segment_id !== undefined" in guard

    def test_screen_context_still_reads_the_declared_segment(self):
        """画面文脈の区画申告は従来どおり payload の明示値から取る（§11.2）。"""
        block = _function_block("getScreenContext")
        assert "p.selection_segment_id" in block
