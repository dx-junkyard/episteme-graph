"""教材埋め込みの学習画面向け解決 DTO（evidence_items）の回帰テスト。

問題: 同じ教材 DSL（``![[kind:id]]``）に対し、管理画面（授業用ドラフト）は
``lsTopicEvidenceItems`` で全 kind を解決できていたのに、学習画面は equation / figure
以外を常に「未解決」表示にしていた（解決コンテキストが画面ごとに違う不整合）。

このテストは正本 ``core.course_content_builder.build_topic_evidence_items`` /
``normalize_evidence_id`` を検証する:

- 各 kind（component / claim / source / equation / figure）が公開済み参照から解決される。
- 未登録 ID・別トピック専用 ID・コース外 ID は解決されず、情報を露出しない
  （build_topic_evidence_items は「そのトピックに公開済みの参照」だけを返す＝
  DB 上の任意 ID をクライアント入力から解決する経路を作らない）。
- ID 正規化（空白・legacy 二重 eq_・``[[...]]`` 表記）が仕様どおり。
- ``get_topic_material`` レスポンスに evidence_items が載る / フォールバック経路では空。

外部依存なし（純関数）。route テストは境界（get_course_data /
_load_course_figures_by_id）のみモックする（test_learner_figure_delivery.py と同型）。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

# backend/api/routes/*.py は `from schemas import ...` の裸 import を使うため api/ を載せる。
_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


def _items_by_ref(items):
    return {f"{it['kind']}:{it['id']}": it for it in items}


class TestBuildTopicEvidenceItems:
    def test_all_kinds_resolved_from_evidence_links(self):
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "component", "target_id": "comp_001", "summary": "コンポ要約",
                 "support_role": "support", "confidence": "high"},
                {"kind": "claim", "target_id": "claim_001", "summary": "主張テキスト",
                 "support_role": "source_backed"},
                {"kind": "source", "target_id": "ev_001", "summary": "原文引用",
                 "support_role": "source_quote"},
                {"kind": "equation", "target_id": "eq_1", "latex": "a=b", "label": "L",
                 "support_role": "equation"},
                {"kind": "figure", "target_id": "fig1", "figure_id": "FIG-UUID",
                 "figure_key": "fk", "caption": "図の説明", "support_role": "figure"},
            ],
        }
        by_ref = _items_by_ref(build_topic_evidence_items(topic))

        assert by_ref["component:comp_001"]["summary"] == "コンポ要約"
        assert by_ref["component:comp_001"]["role"] == "support"
        assert by_ref["claim:claim_001"]["summary"] == "主張テキスト"
        assert by_ref["source:ev_001"]["summary"] == "原文引用"
        eq = by_ref["equation:eq_1"]
        assert eq["latex"] == "a=b"
        assert eq["title"] == "L"  # 生 LaTeX をタイトルに出さない
        fig = by_ref["figure:FIG-UUID"]
        assert fig["figure_id"] == "FIG-UUID"
        assert fig["caption"] == "図の説明"

    def test_equation_reading_fallback_fields_present(self):
        """LaTeX が無くても plain_text / raw_text を DTO に載せる（3段階フォールバック維持）。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "content_blocks": [{
                "type": "equations",
                "items": [
                    {"equation_id": "eq_plain", "plain_text": "F は a と b の和"},
                    {"equation_id": "eq_raw", "raw_text": "delta(k) = ..."},
                    {"equation_id": "eq_empty", "label": "空"},  # 表現ゼロ → 落とす
                ],
            }],
        }
        by_ref = _items_by_ref(build_topic_evidence_items(topic))
        assert by_ref["equation:eq_plain"]["plain_text"] == "F は a と b の和"
        assert by_ref["equation:eq_raw"]["raw_text"] == "delta(k) = ..."
        assert "equation:eq_empty" not in by_ref

    def test_source_equation_quote_moves_tex_to_latex(self):
        """生 TeX を summary に持つ source は latex に移す（220字切り詰めで壊さない）。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "source", "target_id": "ev_eq", "support_role": "equation_quote",
                 "summary": r"\begin{aligned} x&=y \\ z&=w \end{aligned}"},
            ],
        }
        item = _items_by_ref(build_topic_evidence_items(topic))["source:ev_eq"]
        assert item["latex"].startswith(r"\begin{aligned}")
        assert item["summary"] == ""
        assert item["title"] == "数式引用"

    def test_topic_summary_and_source_summary_resolve(self):
        from core.course_content_builder import build_topic_evidence_items

        by_ref = _items_by_ref(build_topic_evidence_items({"summary": "概要テキスト"}))
        assert by_ref["source:topic_summary"]["title"] == "トピック概要"
        assert by_ref["source:summary"]["title"] == "トピック概要"
        assert "概要テキスト" in by_ref["source:topic_summary"]["summary"]

    def test_component_fallback_from_content_blocks(self):
        """evidence_links に無い linked_component_id は content_blocks から補完する。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "linked_component_ids": ["comp_extra"],
            "content_blocks": [{
                "type": "components",
                "items": [{"component_id": "comp_extra", "label": "ラベルX",
                           "teaching_takeaway": "要点X"}],
            }],
        }
        item = _items_by_ref(build_topic_evidence_items(topic))["component:comp_extra"]
        assert item["title"] == "ラベルX"
        assert item["summary"] == "要点X"

    def test_source_excerpt_becomes_source_item(self):
        from core.course_content_builder import build_topic_evidence_items

        topic = {"source_excerpt": "原文抜粋テキスト", "linked_chunk_ids": ["chunk_9"]}
        item = _items_by_ref(build_topic_evidence_items(topic))["source:chunk_9"]
        assert item["title"] == "原文抜粋"
        assert item["summary"] == "原文抜粋テキスト"

    def test_dedup_prefers_evidence_link_over_content_block(self):
        """同一 (kind, id) は先勝ち（evidence_links を content_blocks より優先）。"""
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "component", "target_id": "comp_x", "summary": "正本の要約"},
            ],
            "linked_component_ids": ["comp_x"],
            "content_blocks": [{
                "type": "components",
                "items": [{"component_id": "comp_x", "label": "別ラベル", "summary": "重複"}],
            }],
        }
        items = build_topic_evidence_items(topic)
        comps = [it for it in items if it["kind"] == "component" and it["id"] == "comp_x"]
        assert len(comps) == 1
        assert comps[0]["summary"] == "正本の要約"

    def test_empty_topic_is_safe(self):
        from core.course_content_builder import build_topic_evidence_items

        assert build_topic_evidence_items({}) == []
        assert build_topic_evidence_items(None) == []


class TestScopingAndPrivacy:
    """解決対象は「そのトピックに公開済みの参照」だけ — DB 上の任意 ID は解決しない。"""

    def test_only_this_topic_refs_are_resolvable(self):
        from core.course_content_builder import build_topic_evidence_items

        topic_a = {"evidence_links": [{"kind": "claim", "target_id": "claim_A", "summary": "A"}]}
        topic_b = {"evidence_links": [{"kind": "claim", "target_id": "claim_B", "summary": "B"}]}

        refs_a = set(_items_by_ref(build_topic_evidence_items(topic_a)))
        refs_b = set(_items_by_ref(build_topic_evidence_items(topic_b)))

        assert "claim:claim_A" in refs_a
        # 別トピック専用の ID は解決集合に入らない（露出しない）。
        assert "claim:claim_B" not in refs_a
        assert "claim:claim_A" not in refs_b

    def test_unregistered_id_is_not_present(self):
        from core.course_content_builder import build_topic_evidence_items

        topic = {"evidence_links": [{"kind": "component", "target_id": "comp_1", "summary": "x"}]}
        refs = set(_items_by_ref(build_topic_evidence_items(topic)))
        assert "component:comp_999" not in refs
        assert "claim:comp_1" not in refs  # kind 違いキーも生成しない


class TestNormalization:
    def test_normalize_matches_frontend_spec(self):
        from core.course_content_builder import normalize_evidence_id

        assert normalize_evidence_id("  comp_001  ") == "comp_001"
        assert normalize_evidence_id("[[claim_1]]") == "claim_1"
        assert normalize_evidence_id("[[[[eq_x]]]]") == "eq_x"
        # legacy 二重 eq_ プレフィックス（case-insensitive）
        assert normalize_evidence_id("eq_eq_F2") == "eq_F2"
        assert normalize_evidence_id("EQ_EQ_F2") == "eq_F2"
        assert normalize_evidence_id("eq_F2") == "eq_F2"
        assert normalize_evidence_id(None) == ""

    def test_ids_in_items_are_normalized(self):
        from core.course_content_builder import build_topic_evidence_items

        topic = {
            "evidence_links": [
                {"kind": "equation", "target_id": "eq_eq_F2", "latex": "a=b"},
                {"kind": "component", "target_id": "[[comp_7]]", "summary": "s"},
            ],
        }
        refs = set(_items_by_ref(build_topic_evidence_items(topic)))
        assert "equation:eq_F2" in refs
        assert "component:comp_7" in refs


class TestGetTopicMaterialResponse:
    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_response_includes_resolved_evidence_items(self, mock_course, mock_figs):
        from api.routes.learning import get_topic_material

        mock_course.return_value = {
            "topics": [{
                "id": "topic-1",
                "student_material": {"source_text": "本文 ![[component:comp_1]] と ![[claim:claim_1]] と ![[source:ev_1]]"},
                "evidence_links": [
                    {"kind": "component", "target_id": "comp_1", "summary": "コンポ"},
                    {"kind": "claim", "target_id": "claim_1", "summary": "主張"},
                    {"kind": "source", "target_id": "ev_1", "summary": "原文"},
                ],
            }],
            "sources": [{"material_id": "mat-1"}],
        }
        mock_figs.return_value = {}

        resp = get_topic_material("c1", "topic-1", {"id": "u1"})
        chunk = resp.chunks[0]
        by_ref = _items_by_ref(chunk.evidence_items)
        assert "component:comp_1" in by_ref
        assert "claim:claim_1" in by_ref
        assert "source:ev_1" in by_ref

    @patch("api.routes.learning.get_course_chunks_ordered")
    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_fallback_chunk_path_has_empty_evidence_items(self, mock_course, mock_figs, mock_chunks):
        """topic content 未生成の PDF チャンク経路では evidence_items は空（後方互換）。"""
        from api.routes.learning import get_topic_material

        mock_course.return_value = {
            "topics": [{"id": "topic-1"}],  # student_material / content / summary なし
            "sources": [{"material_id": "mat-1"}],
        }
        mock_figs.return_value = {}
        mock_chunks.return_value = [{
            "id": "chunk-1", "text": "PDF由来チャンク", "chunk_index": 0,
            "formulas": [], "chapter": None, "section": None,
            "material_id": "mat-1", "graph_mentions": [],
        }]

        resp = get_topic_material("c1", "topic-1", {"id": "u1"})
        assert resp.chunks[0].evidence_items == []
