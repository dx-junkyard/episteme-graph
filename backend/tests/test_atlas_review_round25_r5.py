"""第 25 周 面 C（分野の地図の運用）第 5 波の是正（IK-0989〜IK-0995 + IK-0942 / 0964 / 0967 の残り）。

- IK-0989: 学習者の L2 はサーバが「いま学んでいる場所とそのつながり」を優先して代表選出し、
  描かなかった項目・糸があることを事実文で言う（件数なし）。
- IK-0990: 論文の海の縁は、現行凍結版で解消済みの主題を出さない（教員側の候補キューと同じ判定）。
- IK-0991: 次版の下書きは前の版の generated_by を写さず「前の版からの改訂」を記す。
- IK-0992: 下書きが無いとき、公開中の版の項目と関係を読み取り専用で読み返せる。
- IK-0993: 凍結の直後に裏で索引を作っている間は「作っています」と言い、関係の候補は理由を言う。
- IK-0994: 関係の候補の判断の結果を組の名前で言う。
- IK-0995: 「置けなかった主題の記録」の所在を言い、論文の画面で主題を名前で読める。
- IK-0942 残り: 凍結の確認の見出しを「地図 版 X」に。
- IK-0964 / 0967 残り: 「未訪問」は学んだ記録の軸で言う / 小文字の普通の語を warning に挙げない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import atlas_review_display as display  # noqa: E402
from core import atlas_state as st  # noqa: E402
from core import corpus_view  # noqa: E402
from core import label_vocab  # noqa: E402
from core.atlas_gaps import store as gap_store  # noqa: E402
from core.atlas_vectors import builder as vector_builder  # noqa: E402
from core.landscape import schema as ls_schema  # noqa: E402

JS = ROOT / "frontend" / "public" / "js"
ADMIN_JS = JS / "admin.js"
OVERLAY_JS = JS / "atlas-overlay.js"
THREADS_JS = JS / "atlas-threads-layer.js"
ROUTES = BACKEND / "api" / "routes"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# IK-0989
# ---------------------------------------------------------------------------


class TestL2Selection:
    def _select(self, **kwargs):
        from routes.atlas_view import select_l2_concept_ids

        return select_l2_concept_ids(**kwargs)

    def test_priority_then_neighbors_then_skeleton_order(self):
        concepts = [f"c{i}" for i in range(30)]
        edges = [("c25", "c3"), ("c25", "c28"), ("c0", "c1")]
        chosen = self._select(
            concept_ids=concepts, edges=edges, priority=["c25"], limit=4
        )
        # c25（いまここ）→ 1 hop の c3・c28 → 骨格の並び順の先頭 c0
        assert chosen == {"c25", "c3", "c28", "c0"}

    def test_without_priority_falls_back_to_skeleton_order(self):
        concepts = [f"c{i}" for i in range(25)]
        chosen = self._select(concept_ids=concepts, edges=[], priority=[], limit=20)
        assert chosen == {f"c{i}" for i in range(20)}

    def test_unknown_priority_ids_are_ignored(self):
        chosen = self._select(
            concept_ids=["a", "b"], edges=[], priority=["zzz", "b"], limit=20
        )
        assert chosen == {"a", "b"}

    def test_route_wires_omitted_note_and_drawn_edges(self):
        src = _read(ROUTES / "atlas_view.py")
        assert "select_l2_concept_ids(" in src
        # 第 6 波（IK-1017）: 事実文は使った手がかりで言い分ける関数に置き換えた。
        assert "label_vocab.atlas_l2_omitted_note(" in src
        assert "label_vocab.ATLAS_THREADS_UNDRAWN_NOTE" in src
        # 実線は描いた項目どうしの間だけ
        assert "if a in l2_drawn_ids and b in l2_drawn_ids" in src

    def test_notes_carry_no_numbers(self):
        for text in (label_vocab.ATLAS_L2_OMITTED_NOTE, label_vocab.ATLAS_THREADS_UNDRAWN_NOTE):
            assert not re.search(r"[0-9０-９]", text)

    def test_front_renders_server_notes(self):
        overlay = _read(OVERLAY_JS)
        assert "levelData.omitted_note" in overlay
        assert 'id: "atlas-level-note"' in overlay
        threads = _read(THREADS_JS)
        assert "threads.undrawn_note" in threads

    def test_index_html_cache_busters(self):
        html = _read(ROOT / "frontend" / "public" / "index.html")
        assert "/js/atlas-overlay.js?v=ik0989-20261027-atlas5" in html
        assert "/js/atlas-threads-layer.js?v=ik0989-20261027-atlas5" in html


# ---------------------------------------------------------------------------
# IK-0990
# ---------------------------------------------------------------------------


class TestFringeResolved:
    def test_resolved_signal_is_not_fringe(self):
        regions = {"r_ism": "星間物質・星形成"}
        rows = [
            ("r_ism", "Cep B の論文", "d1", "星間磁場"),
            ("r_ism", "別の論文", "d2", "未知の主題"),
        ]
        resolved = gap_store.resolved_label_set(
            {"regions": [{"id": "r_ism", "label": "星間物質・星形成",
                          "concepts": [{"id": "ism_field", "label": "星間磁場"}]}]}
        )
        out = corpus_view._build_fringe(rows, regions, resolved_labels=resolved)
        assert len(out) == 1
        assert out[0]["paper_titles"] == ["別の論文"]

    def test_all_resolved_region_has_no_fringe(self):
        resolved = gap_store.resolved_label_set(
            {"regions": [{"id": "r", "label": "R", "concepts": [{"id": "x", "label": "星間磁場"}]}]}
        )
        out = corpus_view._build_fringe(
            [("r", "Cep B の論文", "d1", "星間磁場")], {"r": "R"}, resolved_labels=resolved
        )
        assert out == []

    def test_shares_the_teacher_side_judgement(self):
        src = _read(BACKEND / "core" / "corpus_view.py")
        assert "gap_store.resolved_label_set(skeleton)" in src
        assert "s.normalized_label" in src
        # 教員の判断は読まない（§6.1）
        assert "atlas_gap_decisions" not in src


# ---------------------------------------------------------------------------
# IK-0991
# ---------------------------------------------------------------------------


class TestRevisionProvenance:
    def test_revision_token_label(self):
        token = display.revision_generated_by("2026.1")
        label = display.generated_by_label(token)
        assert label == "前の版（地図 版 2026.1）からの改訂（候補・関係の取り込みと教員の編集）"
        assert "revision_of" not in label

    def test_revision_without_version(self):
        assert display.generated_by_label(display.revision_generated_by("")) == (
            display.GENERATED_BY_REVISION_UNKNOWN
        )

    def test_from_frozen_does_not_copy_generated_by(self):
        src = _read(ROUTES / "atlas.py")
        block = src[src.index("revision_token = revision_generated_by(frozen.version)"):]
        block = block[: block.index("session.commit()")]
        assert "generated_by=frozen.generated_by" not in block
        assert block.count("generated_by=revision_token") == 2


# ---------------------------------------------------------------------------
# IK-0992
# ---------------------------------------------------------------------------


class TestPublishedContents:
    def test_admin_renders_published_view_without_draft(self):
        js = _read(ADMIN_JS)
        assert "function renderPublishedView(frozenSkeleton, data)" in js
        assert "renderPublishedView(frozen, data);" in js
        assert "{ published: true }" in js
        html = _read(ROOT / "frontend" / "public" / "admin.html")
        assert 'data-ui-anchor="atlas.published-contents"' in html

    def test_anchor_registered(self):
        from core.help_kb.admin_ui_anchors import ADMIN_UI_ANCHORS

        assert ADMIN_UI_ANCHORS["atlas.published-contents"] == (
            "teacher/17-admin-atlas.md#published-contents"
        )


# ---------------------------------------------------------------------------
# IK-0993
# ---------------------------------------------------------------------------


class TestBuildingState:
    def test_marks(self):
        vector_builder.mark_build_started("dom_x", "2026.2")
        try:
            assert vector_builder.build_in_progress("dom_x")
        finally:
            vector_builder.mark_build_finished("dom_x")
        assert not vector_builder.build_in_progress("dom_x")

    def test_status_facts_say_building_not_rebuild(self):
        facts = display.vector_status_facts(
            version="2026.2", total_nodes=50, embedded_nodes=0, stale=True, building=True
        )
        assert facts[-1] == display.VECTOR_INDEX_BUILDING.format(version="2026.2")
        assert display.VECTOR_INDEX_STALE not in facts

    def test_status_facts_unchanged_when_not_building(self):
        facts = display.vector_status_facts(
            version="2026.2", total_nodes=50, embedded_nodes=0, stale=True
        )
        assert display.VECTOR_INDEX_STALE in facts

    def test_freeze_thread_clears_mark_in_finally(self):
        src = _read(ROUTES / "atlas.py")
        hook = src[src.index("def _atlas_anchor_embed"):src.index('name="atlas-anchor-embed"')]
        assert "mark_build_started(cartridge_id" in hook
        assert "finally:" in hook and "mark_build_finished(cartridge_id)" in hook

    def test_edges_and_refresh_wire_building(self):
        edges = _read(ROUTES / "atlas_edges.py")
        assert "EDGE_INDEX_BUILDING_NOTE" in edges and "EDGE_INDEX_MISSING_NOTE" in edges
        vectors = _read(ROUTES / "atlas_vectors.py")
        assert '"building_in_progress"' in vectors
        assert "data.index_note" in _read(ADMIN_JS)


# ---------------------------------------------------------------------------
# IK-0994
# ---------------------------------------------------------------------------


class TestEdgeDecideResult:
    def test_dismiss_line_names_the_pair(self):
        line = display.edge_decide_result_line("dismiss", "ブラックホール", "暗黒エネルギー")
        assert "「ブラックホール」と「暗黒エネルギー」の関係の候補を見送りました" in line
        assert "［見送り済みも表示］" in line

    def test_unknown_action_or_missing_names_is_empty(self):
        assert display.edge_decide_result_line("merge", "a", "b") == ""
        assert display.edge_decide_result_line("dismiss", "", "b") == ""

    def test_admin_keeps_result_line_after_reload(self):
        js = _read(ADMIN_JS)
        assert "resp.result_line" in js
        assert "function loadEdgeCandidates(keepStatus)" in js
        assert "if (keepStatus !== true) setEdgesStatus(\"\");" in js


# ---------------------------------------------------------------------------
# IK-0995
# ---------------------------------------------------------------------------


class TestUnplacedPointer:
    def test_below_threshold_note_points_to_landscape_modal(self):
        assert "［位置づけ（分野マップ）…］" in display.EMPTY_BELOW_THRESHOLD

    def test_landscape_route_returns_topic_names(self):
        src = _read(ROUTES / "landscape.py")
        assert "def _gap_signal_topics(session, document_id: str)" in src
        assert 'entry["gap_topics"] = list(topics)' in src
        assert "item.gap_topics" in _read(ADMIN_JS)


# ---------------------------------------------------------------------------
# IK-0942 / IK-0964 / IK-0967 の残り
# ---------------------------------------------------------------------------


class TestResidues:
    def test_freeze_modal_title_uses_version_name(self):
        js = _read(ADMIN_JS)
        assert "分野の地図骨格を公開前チェック" not in js
        assert 'title: atlasVersionText(version) + " を公開する前の確認",' in js

    def test_unvisited_note_distinguishes_selecting(self):
        note = st.pill_note_for("unvisited", "unvisited")
        assert "地図で選ぶだけでは数えず" in note
        assert "まだこの場所を開いていません" not in note

    def test_plain_lowercase_words_are_not_flagged(self):
        note = ls_schema.reason_beyond_quote_note(
            "論文は dynamical dark energy と Ia 型超新星を論じる",
            ["We study the expansion history."],
        )
        assert "「Ia」" in note
        for word in ("dynamical", "dark", "energy"):
            assert f"「{word}」" not in note

    def test_only_lowercase_terms_give_no_note(self):
        assert ls_schema.reason_beyond_quote_note(
            "論文は dark energy を論じる", ["We study the expansion history."]
        ) == ""
