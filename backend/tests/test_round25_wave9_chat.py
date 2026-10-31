"""第 25 周 第 9 波・横断（学習チャット）: 否定の言及の範囲・訂正の前置き・地図の場所の焦点・配置行の重複・節の見出し。"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as L  # noqa: E402
from core.assistant_context.schema import (  # noqa: E402
    LEARNING_VERIFICATION_OUTPUT_CONSTRAINT,
    ScreenContext,
)
from core.assistant_context.resolvers.learning import resolve_placement  # noqa: E402
from core.dialogue_shape import previous_correction_fact  # noqa: E402
from core.focus_document import (  # noqa: E402
    NO_FOCUS,
    SOURCE_OPENING,
    SOURCE_TOPIC,
    resolve_focus_document,
    with_screen_place_documents,
)


class TestCitationNegationScope:
    """IK-1078: 内容の否定を含む文の肯定の引用を落とさない。"""

    def test_content_negation_keeps_citation(self):
        ans = "外部の ODE ソルバーや事後分布のサンプリングを使わない点が違う、と書かれています [出典9]。骨子は一致[出典2]。"
        assert L._affirmative_citation_indices(ans) == [9, 2]

    def test_marker_subject_negation_still_excluded(self):
        assert L._affirmative_citation_indices("[出典3]と[出典5]は無関係なので使っていません。") == []
        assert L._affirmative_citation_indices("[出典2]を使っていません。") == []
        assert L._affirmative_citation_indices("[出典4] is unrelated to this.") == []

    def test_negation_of_later_marker_does_not_drop_earlier(self):
        assert L._affirmative_citation_indices("結果です[出典1]が、[出典3]は無関係です。") == [1]

    def test_sources_listing_keeps_content_negation_citation(self):
        ans = "サンプリングを使わない点が違う、と書かれています [出典9]。"
        out = L._sources_cited_in_answer(ans, [{"index": 9, "chunk_id": "c9"}, {"index": 1, "chunk_id": "c1"}])
        assert [s["index"] for s in out] == [9]


class TestCorrectionPreface:
    """製品頭脳 B 4296: 前置きだけの訂正文に中身を添える。"""

    def test_preface_carries_following_sentence(self):
        fact = previous_correction_fact([
            {"role": "assistant", "content": "一つ訂正させてください。式(2)は原文ではなくAIの復元です。ほかは同じです。"}
        ])
        assert fact == "一つ訂正させてください。 式(2)は原文ではなくAIの復元です。"

    def test_non_preface_unchanged(self):
        fact = previous_correction_fact([
            {"role": "assistant", "content": "この点については、Aと考えるとより正確です。次の話です。"}
        ])
        assert fact == "この点については、Aと考えるとより正確です。"


class TestScreenPlaceFocus:
    """製品頭脳 B 4293: 地図の場所に置かれた論文を焦点に加える（段・トピックの論文は変えない）。"""

    def test_place_documents_appended_after_topic(self):
        focus = resolve_focus_document(allowed_document_ids=["a", "b"], topic_document_ids=["a"])
        out = with_screen_place_documents(focus, ["b"], allowed_document_ids=["a", "b"])
        assert out.document_ids == ("a", "b") and out.source == SOURCE_TOPIC

    def test_scope_is_not_widened(self):
        focus = resolve_focus_document(allowed_document_ids=["a"], topic_document_ids=["a"])
        assert with_screen_place_documents(focus, ["z"], allowed_document_ids=["a"]).document_ids == ("a",)
        assert with_screen_place_documents(focus, ["a"], allowed_document_ids=[]) == focus

    def test_no_focus_becomes_opening_tier(self):
        out = with_screen_place_documents(NO_FOCUS, ["b"], allowed_document_ids=["b"])
        assert out.document_ids == ("b",) and out.source == SOURCE_OPENING

    def test_chat_core_resolves_focus_once_and_extends(self):
        src = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        core = src[src.index("def _learning_chat_core("):]
        assert core.count("resolve_focus_document(") == 1
        assert "with_screen_place_documents(" in core

    def test_atlas_node_id_ignores_other_course(self):
        class _P:
            def model_dump(self):
                return {"screen": "learning", "selection": {"course_id": "other"},
                        "view": {"atlas_node_id": "c_ns"}}

        class _B:
            screen_context = _P()

        assert L._screen_atlas_node_id(_B(), course_id="mine") == ""
        assert L._screen_atlas_node_id(_B(), course_id="other") == "c_ns"


class TestPlacementDedup:
    """製品頭脳 A 4286: 観点だけが違う配置で同じ事実文を 2 行渡さない。"""

    def test_same_fact_once(self):
        ctx = ScreenContext(screen="learning")
        sources = {"landscape": {"documents": [{"title": "P", "placements": [
            {"node_label": "中性子星", "domain_key": "astro", "provenance_label": "AIによる推定（未確認）", "perspective": "subject"},
            {"node_label": "中性子星", "domain_key": "astro", "provenance_label": "AIによる推定（未確認）", "perspective": "method"},
        ]}], "domains": [{"domain_key": "astro", "frozen_version": "2026.6"}]}}
        facts = resolve_placement(ctx, sources)
        assert len(facts) == 1


class TestVerificationConstraintPriority:
    """製品頭脳 A 4286: 一般知識の補完規則より検証記録の閉世界が勝つことを書く。"""

    def test_priority_sentence(self):
        assert "一般知識で補わず" in LEARNING_VERIFICATION_OUTPUT_CONSTRAINT


class TestSourceSectionNote:
    """製品頭脳 A 4299: 出典行に節の見出しを添える。"""

    def test_section_note(self):
        assert L._source_section_note({"section_title": "Dual-network architecture"}) == "・節「Dual-network architecture」"
        assert L._source_section_note({"section_title": ""}) == ""
        assert L._source_section_note({}) == ""
        assert L._source_section_note({"section_title": "x" * 200}) == ""
