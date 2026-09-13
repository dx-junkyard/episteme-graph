"""概念レジストリの語彙一致（concept_registry_design.md §4.1 / KR4）。

migration 082 の語彙表シードとコード側の列挙（``core/schema.py``）、および日本語
ラベル表（``core/library/schema.py``）が黙って分裂しないよう、SQL をパースして
機械的に突き合わせる（``test_knowledge_objects_vocab.py`` と同じ作法）。

併せて DB 側の構造（FK / CHECK / 部分 UNIQUE / live ビュー / ``symbol`` の追加）が
設計どおり書かれていることを静的に検査する。DB にも LLM にも接続しない。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.library import schema as library_schema
from core.schema import (
    CONCEPT_LABEL_KINDS,
    CONCEPT_RELATION_KINDS,
    CONCEPT_REVIEW_STATUSES,
    LIBRARY_ENTRY_TYPES,
    MAPPING_JUSTIFICATIONS,
)
from core.knowledge_objects.schema import VIEW_SYMBOLS_LIVE

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "db" / "082_concept_registry.sql"

_PAIR_RE = re.compile(r"\('([^']+)',\s*'([^']*)'\)")


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _seeded_pairs(table: str) -> list[tuple[str, str]]:
    """``INSERT INTO <table> (...) VALUES (...) ON CONFLICT`` の (値, ラベル) を返す。"""
    match = re.search(
        rf"INSERT\s+INTO\s+{table}\s*\([^)]*\)\s*VALUES(.*?)ON\s+CONFLICT",
        _sql(),
        re.IGNORECASE | re.DOTALL,
    )
    assert match, f"seed INSERT for {table} not found in {MIGRATION.name}"
    return _PAIR_RE.findall(match.group(1))


# ---------------------------------------------------------------------------
# (a) migration のシード == core/schema.py の列挙
# ---------------------------------------------------------------------------


class TestSeedMatchesCodeVocabulary:
    @pytest.mark.parametrize(
        "table,vocabulary",
        [
            ("knowledge_entry_types", LIBRARY_ENTRY_TYPES),
            ("knowledge_label_kinds", CONCEPT_LABEL_KINDS),
            ("knowledge_relation_kinds", CONCEPT_RELATION_KINDS),
            ("knowledge_mapping_justifications", MAPPING_JUSTIFICATIONS),
        ],
    )
    def test_seed_matches(self, table, vocabulary):
        seeded = [value for value, _label in _seeded_pairs(table)]
        assert set(seeded) == set(vocabulary), (
            f"{table} のシードが core/schema.py の列挙と食い違っています。\n"
            f"  SQL のみ: {sorted(set(seeded) - set(vocabulary))}\n"
            f"  コードのみ: {sorted(set(vocabulary) - set(seeded))}"
        )
        assert len(seeded) == len(set(seeded)), f"{table} のシードに重複があります"

    @pytest.mark.parametrize(
        "table,labels",
        [
            ("knowledge_entry_types", library_schema.ENTRY_TYPE_LABELS),
            ("knowledge_label_kinds", library_schema.LABEL_KIND_LABELS),
            ("knowledge_relation_kinds", library_schema.RELATION_KIND_LABELS),
            ("knowledge_mapping_justifications", library_schema.JUSTIFICATION_LABELS),
        ],
    )
    def test_seed_labels_match_japanese_tables(self, table, labels):
        """DB の label 列と core/library/schema.py の日本語表が逐語一致すること。"""
        seeded = dict(_seeded_pairs(table))
        assert seeded == dict(labels), (
            f"{table} の label 列が core/library/schema.py の表と食い違っています。\n"
            f"  SQL: {seeded}\n  コード: {dict(labels)}"
        )

    def test_library_entry_types_is_a_reexport(self):
        """``core/library/schema.py::ENTRY_TYPES`` は core/schema.py の再エクスポート。"""
        assert library_schema.ENTRY_TYPES is LIBRARY_ENTRY_TYPES
        # 既存の2値は語彙に残っている（migration 042 の CHECK 値が FK で弾かれない）。
        assert "apparatus" in LIBRARY_ENTRY_TYPES
        assert "theory_component" in LIBRARY_ENTRY_TYPES

    def test_review_status_vocabulary(self):
        assert library_schema.REVIEW_STATUSES is CONCEPT_REVIEW_STATUSES
        assert set(CONCEPT_REVIEW_STATUSES) == {"candidate", "confirmed", "dismissed"}
        assert set(library_schema.REVIEW_STATUS_LABELS) == set(CONCEPT_REVIEW_STATUSES)

    def test_review_status_check_matches_vocabulary(self):
        match = re.search(
            r"CHECK\s*\(review_status\s+IN\s*\(([^)]*)\)\)", _sql(), re.IGNORECASE
        )
        assert match, "review_status の CHECK が 082 に無い"
        values = re.findall(r"'([^']+)'", match.group(1))
        assert set(values) == set(CONCEPT_REVIEW_STATUSES)


# ---------------------------------------------------------------------------
# (b) DB 構造（FK / 部分 UNIQUE / 新表 / live ビュー）
# ---------------------------------------------------------------------------


class TestMigrationStructure:
    def test_entry_type_check_is_replaced_by_a_foreign_key(self):
        sql = _sql()
        assert "DROP CONSTRAINT IF EXISTS library_entries_entry_type_check" in sql
        assert "library_entries_entry_type_fk" in sql
        assert "REFERENCES knowledge_entry_types(entry_type)" in sql

    @pytest.mark.parametrize(
        "column",
        ["review_status", "review_note", "mapping_justification", "candidate_key", "decided_by", "decided_at"],
    )
    def test_library_entries_gains_governance_columns(self, column):
        assert re.search(
            rf"ALTER\s+TABLE\s+library_entries\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+{column}\b",
            _sql(),
            re.IGNORECASE,
        ), f"library_entries.{column} の追加が 082 に無い"

    def test_candidate_key_is_partially_unique(self):
        sql = _sql()
        assert "uq_library_entries_candidate_key" in sql
        assert "WHERE candidate_key IS NOT NULL" in sql

    @pytest.mark.parametrize(
        "table",
        ["library_entry_labels", "library_entry_relations", "library_atlas_node_links"],
    )
    def test_new_tables_are_created_idempotently(self, table):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in _sql()

    def test_relations_forbid_self_links(self):
        assert re.search(
            r"CHECK\s*\(subject_entry_id\s*<>\s*object_entry_id\)", _sql(), re.IGNORECASE
        ), "subject <> object の CHECK が無い（自己リンクを作らせない）"

    def test_node_links_are_version_independent(self):
        """KR9: レジストリ ↔ node のリンクに ``skeleton_version`` を持たせない。"""
        block = re.search(
            r"CREATE TABLE IF NOT EXISTS library_atlas_node_links\s*\((.*?)\n\);",
            _sql(),
            re.DOTALL,
        )
        assert block, "library_atlas_node_links の定義が見つからない"
        assert "skeleton_version" not in block.group(1)
        assert "anode|" in _sql()

    @pytest.mark.parametrize(
        "table",
        [
            "element_identity_links",
            "atlas_anchor_aliases",
            "atlas_gap_decisions",
            "atlas_edge_decisions",
            "landscape_placements",
        ],
    )
    def test_mapping_justification_is_added_to_existing_tables(self, table):
        assert re.search(
            rf"ALTER\s+TABLE\s+{table}\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+mapping_justification",
            _sql(),
            re.IGNORECASE,
        ), f"{table}.mapping_justification の追加が 082 に無い"

    def test_backfill_only_covers_deterministically_derivable_tables(self):
        """KR4: 導出できない表は NULL のまま（推測で埋めない）。"""
        sql = _sql()
        updates = re.findall(r"UPDATE\s+(\w+)\s+\n?\s*SET mapping_justification", sql)
        assert set(updates) == {"landscape_placements", "atlas_anchor_aliases"}, updates
        # 自己収束（毎起動の再実行で何も起きない）。
        for match in re.finditer(r"UPDATE\s+\w+\s*\n\s*SET mapping_justification(.*?);", sql, re.DOTALL):
            assert "mapping_justification IS NULL" in match.group(1)

    def test_landscape_backfill_matches_code_mapping(self):
        """バックフィルの写像が ``core/landscape/schema.py`` と同じ規則であること。"""
        from core.landscape import schema as landscape_schema

        sql = _sql()
        for provenance in landscape_schema.PROVENANCES:
            expected = landscape_schema.justification_for_provenance(provenance)
            assert expected in MAPPING_JUSTIFICATIONS
            assert re.search(
                rf"SET mapping_justification = '{expected}'\s*\n\s*WHERE mapping_justification IS NULL"
                rf" AND provenance = '{provenance}'",
                sql,
            ), f"provenance={provenance} のバックフィルが写像と食い違っています"

    def test_identity_links_check_gains_symbol(self):
        sql = _sql()
        assert "element_identity_links_instance_element_type_check" in sql
        assert "'symbol'" in sql

    def test_symbols_live_view_is_created(self):
        assert re.search(
            rf"CREATE\s+OR\s+REPLACE\s+VIEW\s+{VIEW_SYMBOLS_LIVE}\s+AS", _sql(), re.IGNORECASE
        )
        assert "superseded_at IS NULL" in _sql()

    def test_no_delete_statements(self):
        """KR7: 行を消さない（却下も見送りも status 遷移で表す）。"""
        body = re.sub(r"--[^\n]*", "", _sql())
        assert not re.search(r"\bDELETE\s+FROM\b", body, re.IGNORECASE)

    def test_does_not_write_to_atlas_skeletons(self):
        """KR2 / LS7 / AB4: 骨格へ書き込む経路を増やさない。"""
        body = re.sub(r"--[^\n]*", "", _sql())
        assert "atlas_skeletons" not in body
