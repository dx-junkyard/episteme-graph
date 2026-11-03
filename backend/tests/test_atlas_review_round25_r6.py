"""第 25 周 面 C（分野の地図の運用）第 6 波の是正（IK-1017 / IK-1018 / IK-1019 + IK-0992 の残り）。

- IK-1017: 学習者の L2 の代表選出が、コースの論文の配置と凍結で加わった関係の両端を手がかりに
  使う。事実文は実際に使った手がかりを言い、手がかりが無いときは「並び順」と言う（数値なし）。
- IK-1018: 学習者の右パネルの承認の行に内部の欄名（スコープ欄）を出さない（閉世界の事実文）。
- IK-1019: 凍結の影響の［止めている候補を見る］は止めている採用のカードへ着地する。
- IK-0992 の残り: 下書きがある間も公開中の版の折りたたみを、下書きと区別できる見出しで出す。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import atlas_review_display as display  # noqa: E402
from core import atlas_state as st  # noqa: E402
from core import label_vocab  # noqa: E402

JS = ROOT / "frontend" / "public" / "js"
ADMIN_JS = JS / "admin.js"
FIXTURE_JS = JS / "atlas-fixture.js"
ROUTES = BACKEND / "api" / "routes"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _edge(a, b):
    return SimpleNamespace(from_id=a, to_id=b)


# ---------------------------------------------------------------------------
# IK-1017
# ---------------------------------------------------------------------------


class TestL2CourseHints:
    def _view(self):
        from routes import atlas_view

        return atlas_view

    def test_course_hints_come_after_learning_priority_and_before_neighbors(self):
        v = self._view()
        concepts = [f"c{i}" for i in range(30)]
        chosen = v.select_l2_concept_ids(
            concepts,
            [("c25", "c3")],
            priority=["c20"],
            course_hints=["c25", "c26"],
            limit=4,
        )
        # c20（いまここ）→ c25・c26（コースの論文の配置）→ c25 の 1 hop の c3
        assert chosen == {"c20", "c25", "c26", "c3"}

    def test_course_hints_alone_replace_skeleton_order(self):
        v = self._view()
        concepts = [f"c{i}" for i in range(30)]
        chosen = v.select_l2_concept_ids(
            concepts, [], priority=[], course_hints=["c28", "c29"], limit=20
        )
        assert {"c28", "c29"} <= chosen

    def test_course_placement_concepts_confirmed_first_and_domain_scoped(self):
        v = self._view()
        rows = [
            {"domain_key": "astro", "node_id": "a", "status": "inferred"},
            {"domain_key": "astro", "node_id": "b", "status": "confirmed"},
            {"domain_key": "other", "node_id": "c", "status": "confirmed"},
            {"domain_key": "astro", "node_id": "region_x", "status": "confirmed"},
            {"domain_key": "astro", "node_id": "old_a", "status": "confirmed"},
        ]
        got = v.course_placement_concept_ids(
            rows,
            domain_key="astro",
            concept_ids={"a", "b", "c", "new_a"},
            resolve=lambda nid: "new_a" if nid == "old_a" else "",
        )
        # confirmed（b・旧版の old_a を読み替えた new_a）→ inferred（a）。別分野・領域は落ちる。
        assert got == ["b", "new_a", "a"]

    def test_added_relation_pairs_compares_with_previous_frozen(self):
        v = self._view()
        prev = SimpleNamespace(version="2026.2", edges=[_edge("x", "y")])
        cur = SimpleNamespace(
            version="2026.3", edges=[_edge("y", "x"), _edge("p", "q")]
        )
        assert v.added_relation_pairs([prev, cur], "2026.3") == [("p", "q")]
        # 前の版が無い・現行版が末尾でないなら主張しない
        assert v.added_relation_pairs([cur], "2026.3") == []
        assert v.added_relation_pairs([prev, cur], "2026.4") == []

    def test_note_names_the_hints_actually_used(self):
        used = label_vocab.atlas_l2_omitted_note([label_vocab.ATLAS_L2_HINT_PLACEMENTS])
        assert "このコースの論文が置かれている場所" in used
        assert "いま学んでいる場所" not in used
        none = label_vocab.atlas_l2_omitted_note([])
        assert "並び順" in none and "優先しています" not in none
        # 学習の手がかりだけのときは第 5 波の文と同じ（後方互換）
        assert (
            label_vocab.atlas_l2_omitted_note([label_vocab.ATLAS_L2_HINT_LEARNING])
            == label_vocab.ATLAS_L2_OMITTED_NOTE
        )
        for hints in ([], list(label_vocab.ATLAS_L2_HINTS)):
            assert not re.search(r"[0-9０-９]", label_vocab.atlas_l2_omitted_note(hints))

    def test_route_passes_course_hints_and_reports_used_hints(self):
        src = _read(ROUTES / "atlas_view.py")
        # 第 8 波（IK-1045）: 改訂で加わった関係は組で枠を先に配る（course_hints は配置だけ）。
        assert "course_hints=list(course_placed_concepts)" in src
        assert "relation_pairs=list(added_pairs)" in src
        assert "_l2_course_hints(" in src
        assert "landscape_schema.LEARNER_VISIBLE_STATUSES" in src
        assert "services.list_course_source_document_ids(course_data)" in src
        assert "label_vocab.atlas_l2_omitted_note(" in src
        # 配置の node_id を書き換えない（読み替えるだけ = NC5）
        assert "UPDATE landscape_placements" not in src


# ---------------------------------------------------------------------------
# IK-1018
# ---------------------------------------------------------------------------


class TestEndorseLine:
    def test_assumed_endorse_line_has_no_internal_field_name(self):
        signals = st.ConceptSignals(key="dual")
        line = st.endorse_line_for(st.STATUS_ASSUMED, signals)
        assert line == label_vocab.ATLAS_ENDORSE_NONE_ASSUMED_LINE
        assert "このコーパスの中では" in line
        for term in label_vocab.ATLAS_LEARNER_PANEL_DENYLIST:
            assert term not in line

    def test_cached_legacy_line_is_projected_at_read_time(self):
        assert (
            st.learner_endorse_line(label_vocab.ATLAS_ENDORSE_LEGACY_SCOPE_LINE)
            == label_vocab.ATLAS_ENDORSE_NONE_ASSUMED_LINE
        )
        assert st.learner_endorse_line("承認: 2名の教員が承認") == "承認: 2名の教員が承認"
        src = _read(ROUTES / "atlas_view.py")
        assert 'row["endorse_line"],' not in src.replace(
            'learner_endorse_line(row["endorse_line"]),', ""
        )

    def test_learner_panel_sources_carry_no_denylisted_terms(self):
        state_src = _read(BACKEND / "core" / "atlas_state.py")
        fixture = _read(FIXTURE_JS)
        for term in label_vocab.ATLAS_LEARNER_PANEL_DENYLIST:
            assert term not in fixture
            # atlas_state はコメント・旧文の参照を除けば学習者向けの文にこの語を持たない
            for line in state_src.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""'):
                    continue
                if "return " in stripped and term in stripped:
                    raise AssertionError(f"{term} in learner text: {stripped}")


# ---------------------------------------------------------------------------
# IK-1019
# ---------------------------------------------------------------------------


class TestFreezeBlockerGoto:
    def test_pending_items_carry_target_keys(self):
        items = display.pending_freeze_items(
            None,
            gap_items=[{"proposed_label": "星間磁場", "cluster_key": "gap|astro|r|x"}],
            edge_items=[{"edge_key": "edge|astro|a|b"}],
            edge_label=lambda item: "惑星形成 — 星形成",
        )
        by_kind = {i["kind"]: i for i in items}
        assert by_kind["gap"]["target_key"] == "gap|astro|r|x"
        assert by_kind["edge"]["target_key"] == "edge|astro|a|b"

    def test_goto_lands_on_card_not_section_top(self):
        js = _read(ADMIN_JS)
        assert "function scrollToFreezeBlocker(pendingItems)" in js
        assert 'isEdge ? "data-edge-key" : "data-cluster-key"' in js
        assert 'isEdge ? "atlas-edges-group" : "atlas-gaps-group"' in js
        assert "scrollToFreezeBlocker(pendingItems);" in js
        assert 'openFreezeImpactModal(blockedLines, "", null, true, readiness.pending_items || []);' in js


# ---------------------------------------------------------------------------
# IK-0992 の残り
# ---------------------------------------------------------------------------


class TestPublishedViewWithDraft:
    def test_published_view_is_rendered_even_with_draft(self):
        js = _read(ADMIN_JS)
        start = js.index("function renderPublishedView(")
        body = js[start : start + 2000]
        assert "(data && data.draft) ||" not in body
        assert "ATLAS_PUBLISHED_VIEW_WITH_DRAFT_TEXT" in body
        assert "下で編集している次版の下書きとは別です" in js

    def test_cache_busters(self):
        admin_html = _read(ROOT / "frontend" / "public" / "admin.html")
        assert re.search(r"/js/admin\.js\?v=[^\"]*-atlas6", admin_html)
        index_html = _read(ROOT / "frontend" / "public" / "index.html")
        assert "/js/atlas-fixture.js?v=ik1018-20261027-atlas6" in index_html
