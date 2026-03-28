"""schema_registry モジュールの単体テスト。

DB接続を伴わないロジックテストのみ。
seed / load 関数はモック化してテストする。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.schema_registry import (
    _BUILTIN_ONTOLOGY_DESCRIPTIONS,
    _BUILTIN_PREDICATE_DESCRIPTIONS,
    build_ontology_type_prompt,
    build_predicate_prompt,
    get_ontology_type_names,
    get_predicate_names,
    invalidate_cache,
)
from core.schema import CorePredicate, OntologyType


class TestBuiltinDescriptions:
    """ビルトイン定義の網羅性をテスト。"""

    def test_all_ontology_types_have_descriptions(self):
        for ot in OntologyType:
            assert ot.value in _BUILTIN_ONTOLOGY_DESCRIPTIONS, (
                f"OntologyType {ot.value} has no builtin description"
            )

    def test_all_predicates_have_descriptions(self):
        for cp in CorePredicate:
            assert cp.value in _BUILTIN_PREDICATE_DESCRIPTIONS, (
                f"CorePredicate {cp.value} has no builtin description"
            )


class TestCacheInvalidation:
    """キャッシュ無効化のテスト。"""

    def test_invalidate_cache_resets(self):
        from core.schema_registry import _cache, _cache_lock

        with _cache_lock:
            _cache["ontology_types"] = [{"id": "Test"}]
            _cache["predicates"] = [{"id": "TEST_PRED"}]
            _cache["ts"] = 999999.0

        invalidate_cache()

        with _cache_lock:
            assert _cache["ontology_types"] is None
            assert _cache["predicates"] is None
            assert _cache["ts"] == 0.0


class TestPromptBuilding:
    """動的プロンプト生成のテスト。"""

    @patch("core.schema_registry.get_ontology_types")
    def test_build_ontology_type_prompt(self, mock_get):
        mock_get.return_value = [
            {"id": "Agent", "label": "Agent", "description": "主体", "is_builtin": True},
            {"id": "Experiment", "label": "Experiment", "description": "実験手法", "is_builtin": False},
        ]
        result = build_ontology_type_prompt()
        assert "Agent" in result
        assert "主体" in result
        assert "Experiment" in result
        assert "実験手法" in result

    @patch("core.schema_registry.get_predicates")
    def test_build_predicate_prompt(self, mock_get):
        mock_get.return_value = [
            {"id": "CAUSES", "label": "CAUSES", "description": "因果関係", "is_builtin": True},
            {"id": "PROVED_BY", "label": "PROVED_BY", "description": "証明", "is_builtin": False},
        ]
        result = build_predicate_prompt()
        assert "CAUSES" in result
        assert "因果関係" in result
        assert "PROVED_BY" in result

    @patch("core.schema_registry.get_ontology_types")
    def test_get_ontology_type_names(self, mock_get):
        mock_get.return_value = [
            {"id": "Agent", "label": "Agent", "description": "", "is_builtin": True},
            {"id": "Event", "label": "Event", "description": "", "is_builtin": True},
        ]
        names = get_ontology_type_names()
        assert names == ["Agent", "Event"]

    @patch("core.schema_registry.get_predicates")
    def test_get_predicate_names(self, mock_get):
        mock_get.return_value = [
            {"id": "CAUSES", "label": "CAUSES", "description": "", "is_builtin": True},
        ]
        names = get_predicate_names()
        assert names == ["CAUSES"]

    @patch("core.schema_registry.get_ontology_types")
    def test_empty_description_no_suffix(self, mock_get):
        mock_get.return_value = [
            {"id": "X", "label": "X", "description": "", "is_builtin": False},
        ]
        result = build_ontology_type_prompt()
        assert result.strip() == "X"
        assert " — " not in result

    @patch("core.schema_registry.get_ontology_types")
    def test_build_ontology_type_prompt_empty_list(self, mock_get):
        """空リストの場合は空文字列を返す。"""
        mock_get.return_value = []
        result = build_ontology_type_prompt()
        assert result == ""

    @patch("core.schema_registry.get_predicates")
    def test_build_predicate_prompt_empty_list(self, mock_get):
        """空リストの場合は空文字列を返す。"""
        mock_get.return_value = []
        result = build_predicate_prompt()
        assert result == ""

    @patch("core.schema_registry.get_ontology_types")
    def test_get_ontology_type_names_empty(self, mock_get):
        """空リストの場合は空リストを返す。"""
        mock_get.return_value = []
        names = get_ontology_type_names()
        assert names == []

    @patch("core.schema_registry.get_predicates")
    def test_get_predicate_names_empty(self, mock_get):
        """空リストの場合は空リストを返す。"""
        mock_get.return_value = []
        names = get_predicate_names()
        assert names == []

    @patch("core.schema_registry.get_ontology_types")
    def test_build_prompt_multiple_types_multiline(self, mock_get):
        """複数タイプが改行区切りで出力される。"""
        mock_get.return_value = [
            {"id": "A", "label": "A", "description": "desc_a", "is_builtin": True},
            {"id": "B", "label": "B", "description": "desc_b", "is_builtin": False},
        ]
        result = build_ontology_type_prompt()
        lines = result.strip().split("\n")
        assert len(lines) == 2
        assert "A" in lines[0]
        assert "B" in lines[1]


class TestCacheTimestamp:
    """キャッシュのタイムスタンプ管理テスト。"""

    def test_invalidate_sets_ts_to_zero(self):
        from core.schema_registry import _cache, _cache_lock

        with _cache_lock:
            _cache["ts"] = 12345.0

        invalidate_cache()

        with _cache_lock:
            assert _cache["ts"] == 0.0

    def test_multiple_invalidations(self):
        """連続した無効化呼び出しが安全に動作する。"""
        invalidate_cache()
        invalidate_cache()
        invalidate_cache()

        from core.schema_registry import _cache, _cache_lock

        with _cache_lock:
            assert _cache["ontology_types"] is None
