"""教材区画の粒度（P2-R3 / P2-R13）— 学ぶ単位 Phase 2 レビューの是正。

固定する契約:

1. ``get_topic_material`` が返す chunks は ``core/lecture.py::build_topic_slides`` の
   ページ境界（表示・音声・readiness と同じ決定論分割）で割れる。
2. 痕跡帰属の区画番号解決（``_anchor_segment_texts``）が**同じ**材料を使う
   （``data-segment-index`` と番号の意味が食い違わない）。
3. 区画が1つしか立たない短い教材は従来どおり1区画（``seg_0`` が唯一の正解）。
4. 数式・図・evidence の索引は区画ごとに間引かない（``[[FORMULA_N]]`` /
   ``[[FIGURE_N]]`` の位置依存解決を壊さない）。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from core.structure_anchor.selection_segment import resolve_selection_segment  # noqa: E402


def _long_topic() -> dict:
    paragraphs = [
        "第一段落。" + "あ" * 400,
        "第二段落。" + "い" * 400,
        "第三段落。" + "う" * 400,
    ]
    return {
        "id": "topic-1",
        "title": "長いトピック",
        "student_material": {"source_text": "\n\n".join(paragraphs)},
    }


def _short_topic() -> dict:
    return {
        "id": "topic-1",
        "title": "短いトピック",
        "student_material": {"source_text": "短い本文だけ。"},
    }


def _course(topic: dict) -> dict:
    return {"topics": [topic], "sources": [{"material_id": "mat-1"}]}


class TestTopicMaterialSegments:
    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_long_topic_is_delivered_as_multiple_segments(self, mock_course, mock_figs):
        from api.routes.learning import get_topic_material

        topic = _long_topic()
        mock_course.return_value = _course(topic)
        mock_figs.return_value = {}

        resp = get_topic_material("c1", "topic-1", {"id": "u1"})

        assert len(resp.chunks) > 1, "長いトピックが1区画のまま（seg_0 固定が残っている）"
        # 章題は先頭区画にだけ（同じ題名を区画の数だけ繰り返さない）。
        assert resp.chunks[0].section == "長いトピック"
        assert all(c.section is None for c in resp.chunks[1:])
        # 本文は分割前の全体を過不足なく覆う。
        joined = "".join(c.text for c in resp.chunks)
        assert "第一段落。" in joined and "第三段落。" in joined

    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_short_topic_stays_a_single_segment(self, mock_course, mock_figs):
        from api.routes.learning import get_topic_material

        mock_course.return_value = _course(_short_topic())
        mock_figs.return_value = {}

        resp = get_topic_material("c1", "topic-1", {"id": "u1"})
        assert len(resp.chunks) == 1
        assert resp.chunks[0].text.strip() == "短い本文だけ。"

    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_indexes_are_not_thinned_per_segment(self, mock_course, mock_figs):
        """formulas / figures / evidence_items は各区画へ同じ索引を渡す。"""
        from api.routes.learning import get_topic_material

        mock_course.return_value = _course(_long_topic())
        mock_figs.return_value = {}

        resp = get_topic_material("c1", "topic-1", {"id": "u1"})
        first = resp.chunks[0]
        for chunk in resp.chunks[1:]:
            assert chunk.formulas == first.formulas
            assert chunk.figures == first.figures
            assert chunk.evidence_items == first.evidence_items
            assert chunk.id == first.id


class TestAnchorSegmentsMatchDelivery:
    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_anchor_segments_equal_the_delivered_segments(self, mock_course, mock_figs):
        from api.routes.learning import _anchor_segment_texts, get_topic_material

        topic = _long_topic()
        mock_course.return_value = _course(topic)
        mock_figs.return_value = {}

        delivered = [c.text for c in get_topic_material("c1", "topic-1", {"id": "u1"}).chunks]
        anchors = _anchor_segment_texts(topic)

        assert anchors is not None
        assert len(anchors) == len(delivered)

    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_a_selection_from_a_later_segment_resolves_to_that_segment(
        self, mock_course, mock_figs
    ):
        from api.routes.learning import _anchor_segment_texts

        topic = _long_topic()
        mock_course.return_value = _course(topic)
        mock_figs.return_value = {}

        segments = _anchor_segment_texts(topic)
        assert resolve_selection_segment(segments, "第三段落。") == len(segments) - 1
        assert resolve_selection_segment(segments, "第一段落。") == 0

    def test_topic_without_material_resolves_nothing(self):
        from api.routes.learning import _anchor_segment_texts

        assert _anchor_segment_texts({"id": "t", "title": "x"}) is None
        assert _anchor_segment_texts(None) is None


class TestSelectionMatchIsPlaceholderInsensitive:
    """P2-R13: 供給元（解決済み / 未解決）の違いで一致判定がぶれない。"""

    def test_resolved_and_unresolved_figure_embeds_match_the_same_selection(self):
        resolved = "導入本文 [[FIGURE_1]] まとめ"
        unresolved = "導入本文 ![[figure:abc-123]] まとめ"
        assert resolve_selection_segment([resolved], "導入本文 まとめ") == 0
        assert resolve_selection_segment([unresolved], "導入本文 まとめ") == 0

    def test_formula_placeholders_do_not_block_a_match(self):
        assert resolve_selection_segment(["式は [[FORMULA_2]] である。"], "式は である") == 0
