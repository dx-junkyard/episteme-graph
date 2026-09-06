"""meta_analyzer モジュールの単体テスト。

DB・LLM呼び出しをモック化してロジックをテストする。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.meta_analyzer import (
    ProposedSchemaItem,
    SchemaAnalysisResult,
)


class TestSchemaAnalysisResult:
    """LLM構造化出力モデルのテスト。"""

    def test_has_proposals_false(self):
        result = SchemaAnalysisResult(
            has_proposals=False,
            summary="拡張不要",
            reasoning="現在のスキーマで十分",
            items=[],
        )
        assert result.has_proposals is False
        assert result.items == []

    def test_has_proposals_true_with_items(self):
        result = SchemaAnalysisResult(
            has_proposals=True,
            summary="実験手法カテゴリの追加を提案",
            reasoning="実験に関する未回答が多い",
            items=[
                ProposedSchemaItem(
                    item_type="ontology_type",
                    key="Experiment",
                    label="Experiment",
                    description="実験手法・実験装置",
                ),
                ProposedSchemaItem(
                    item_type="predicate",
                    key="PROVED_BY",
                    label="PROVED_BY",
                    description="実験的に証明された関係",
                ),
            ],
        )
        assert result.has_proposals is True
        assert len(result.items) == 2
        assert result.items[0].item_type == "ontology_type"
        assert result.items[0].key == "Experiment"
        assert result.items[1].item_type == "predicate"
        assert result.items[1].key == "PROVED_BY"


class TestProposedSchemaItem:
    """個別スキーマアイテムのテスト。"""

    def test_ontology_type_item(self):
        item = ProposedSchemaItem(
            item_type="ontology_type",
            key="Instrument",
            label="Instrument",
            description="観測器・測定装置",
        )
        assert item.item_type == "ontology_type"
        assert item.key == "Instrument"

    def test_predicate_item(self):
        item = ProposedSchemaItem(
            item_type="predicate",
            key="OBSERVED_BY",
            label="OBSERVED_BY",
            description="観測された関係",
        )
        assert item.item_type == "predicate"

    def test_model_dump(self):
        item = ProposedSchemaItem(
            item_type="ontology_type",
            key="Test",
            label="Test",
            description="test desc",
        )
        d = item.model_dump()
        assert d["item_type"] == "ontology_type"
        assert d["key"] == "Test"
        assert d["label"] == "Test"
        assert d["description"] == "test desc"


class TestAnalyzeUnansweredQueries:
    """analyze_unanswered_queries のテスト（DB・LLMモック）。"""

    @patch("core.meta_analyzer._pg_session")
    def test_returns_none_when_too_few_queries(self, mock_pg):
        """最小クエリ数未満の場合はNoneを返す。"""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("question1", "topic1", "course1", None),
        ]
        mock_pg.return_value = mock_session

        from core.meta_analyzer import analyze_unanswered_queries
        result = analyze_unanswered_queries(min_queries=3)
        assert result is None

    @patch("core.meta_analyzer._pg_session")
    @patch("core.meta_analyzer.get_ontology_types")
    @patch("core.meta_analyzer.get_predicates")
    @patch("core.meta_analyzer.generate_text_with_structured_output")
    def test_returns_none_when_llm_says_no_proposals(
        self, mock_llm, mock_preds, mock_types, mock_pg,
    ):
        """LLMがhas_proposals=Falseを返す場合はNoneを返す。"""
        mock_session = MagicMock()
        rows = [
            (f"question{i}", f"topic{i}", "course1", None)
            for i in range(5)
        ]
        mock_session.execute.return_value.fetchall.return_value = rows
        mock_pg.return_value = mock_session

        mock_types.return_value = [{"id": "Agent", "description": "agent"}]
        mock_preds.return_value = [{"id": "CAUSES", "description": "causes"}]

        mock_llm.return_value = SchemaAnalysisResult(
            has_proposals=False,
            summary="不要",
            reasoning="十分",
            items=[],
        )

        from core.meta_analyzer import analyze_unanswered_queries
        result = analyze_unanswered_queries(min_queries=3)
        assert result is None

    @patch("core.meta_analyzer._pg_session")
    @patch("core.meta_analyzer.get_ontology_types")
    @patch("core.meta_analyzer.get_predicates")
    @patch("core.meta_analyzer.generate_text_with_structured_output")
    def test_returns_none_when_llm_raises_exception(
        self, mock_llm, mock_preds, mock_types, mock_pg,
    ):
        """LLM呼び出しが例外を投げた場合はNoneを返す。"""
        mock_session = MagicMock()
        rows = [
            (f"question{i}", f"topic{i}", "course1", None)
            for i in range(5)
        ]
        mock_session.execute.return_value.fetchall.return_value = rows
        mock_pg.return_value = mock_session

        mock_types.return_value = [{"id": "Agent", "description": "agent"}]
        mock_preds.return_value = [{"id": "CAUSES", "description": "causes"}]

        mock_llm.side_effect = RuntimeError("LLM API error")

        from core.meta_analyzer import analyze_unanswered_queries
        result = analyze_unanswered_queries(min_queries=3)
        assert result is None

    @patch("core.meta_analyzer._pg_session")
    def test_returns_none_when_zero_queries(self, mock_pg):
        """クエリが0件の場合はNoneを返す。"""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = []
        mock_pg.return_value = mock_session

        from core.meta_analyzer import analyze_unanswered_queries
        result = analyze_unanswered_queries(min_queries=1)
        assert result is None


class TestSchemaAnalysisResultSerialization:
    """SchemaAnalysisResult のシリアライズテスト。"""

    def test_model_dump_includes_all_fields(self):
        result = SchemaAnalysisResult(
            has_proposals=True,
            summary="テスト",
            reasoning="理由",
            items=[
                ProposedSchemaItem(
                    item_type="ontology_type",
                    key="X",
                    label="X",
                    description="desc",
                ),
            ],
        )
        d = result.model_dump()
        assert "has_proposals" in d
        assert "summary" in d
        assert "reasoning" in d
        assert "items" in d
        assert len(d["items"]) == 1

    def test_empty_items_default(self):
        result = SchemaAnalysisResult(
            has_proposals=False,
            summary="",
            reasoning="",
        )
        assert result.items == []


class TestCourseScopeLimitsTheAnalysis:
    """目的外利用の禁止: 分析対象は呼び出し側が渡したコースに限定される。

    未回答クエリの本文（学習者の質問原文）はそのコースの運営者にだけ開かれる情報で、
    全コース横断でプロンプトに埋めてよいものではない。``course_ids`` を渡した場合は
    SQL 側で絞り、空リストなら SQL も LLM も呼ばない（fail-closed）。
    """

    @patch("core.meta_analyzer._pg_session")
    def test_empty_scope_reads_nothing_and_calls_no_llm(self, mock_pg):
        def _boom():
            raise AssertionError("対象コースがゼロなら DB を触ってはならない")

        mock_pg.side_effect = _boom

        from core.meta_analyzer import analyze_unanswered_queries
        assert analyze_unanswered_queries(min_queries=1, course_ids=[]) is None

    @patch("core.meta_analyzer._pg_session")
    def test_scope_is_pushed_into_the_sql(self, mock_pg):
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = []
        mock_pg.return_value = mock_session

        from core.meta_analyzer import analyze_unanswered_queries
        analyze_unanswered_queries(min_queries=99, course_ids=["c1", "c2"])

        stmt, params = mock_session.execute.call_args[0]
        assert "uql.course_id = ANY(:course_ids)" in str(stmt)
        assert params["course_ids"] == ["c1", "c2"]

    @patch("core.meta_analyzer._pg_session")
    def test_none_scope_keeps_the_cross_course_query(self, mock_pg):
        """``None``（= SYSTEM_ADMIN 経路）のときだけ従来どおり全件を読む。"""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = []
        mock_pg.return_value = mock_session

        from core.meta_analyzer import analyze_unanswered_queries
        analyze_unanswered_queries(min_queries=99, course_ids=None)

        stmt, params = mock_session.execute.call_args[0]
        assert "course_id = ANY" not in str(stmt)
        assert "course_ids" not in params
