"""型語彙の正本一致（knowledge_objects_design.md §7 / KO7）。

DB 側は CHECK ではなく語彙表（``knowledge_claim_types`` / ``knowledge_component_types``）への
FK で守られ、その語彙表は migration 078 が ``core/schema.py`` と**同じ列挙**をシードする。
コードと SQL は別ファイルなので、黙って分裂しないようここで機械的に突き合わせる。

併せて
  - A層（``src/episteme_graph/agents/``）側の語彙が core の語彙に含まれること（KO1 —
    src は非改変なので「src ⊆ core」という向きで固定する）
  - live ビューの再作成規律（078 以降で theory_claims / theory_components に列を足す
    migration は同じファイルで ``CREATE OR REPLACE VIEW`` を再実行する）
を検査する。DB にも LLM にも接続しない。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.schema import (  # noqa: E402
    CLAIM_TIERS,
    CLAIM_TYPES,
    COMPONENT_TYPES,
    CorePredicate,
)

MIGRATIONS_DIR = BACKEND / "db"
KNOWLEDGE_OBJECTS_MIGRATION = MIGRATIONS_DIR / "078_knowledge_objects.sql"

_VALUE_RE = re.compile(r"'([^']+)'")


def _seeded_values(table: str) -> list[str]:
    """078 の ``INSERT INTO <table> ... ON CONFLICT`` から値の列を取り出す。"""
    sql = KNOWLEDGE_OBJECTS_MIGRATION.read_text(encoding="utf-8")
    match = re.search(
        rf"INSERT\s+INTO\s+{table}\s*\(value\)\s*VALUES(.*?)ON\s+CONFLICT",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, f"seed INSERT for {table} not found in {KNOWLEDGE_OBJECTS_MIGRATION.name}"
    return _VALUE_RE.findall(match.group(1))


# ---------------------------------------------------------------------------
# (a) migration のシード == core/schema.py の列挙
# ---------------------------------------------------------------------------


class TestSeedMatchesCodeVocabulary:
    def test_claim_types_seed_matches(self):
        seeded = _seeded_values("knowledge_claim_types")
        assert set(seeded) == set(CLAIM_TYPES), (
            "knowledge_claim_types のシードが core.schema.CLAIM_TYPES と食い違っています。\n"
            f"  SQL のみ: {sorted(set(seeded) - set(CLAIM_TYPES))}\n"
            f"  コードのみ: {sorted(set(CLAIM_TYPES) - set(seeded))}"
        )

    def test_component_types_seed_matches(self):
        seeded = _seeded_values("knowledge_component_types")
        assert set(seeded) == set(COMPONENT_TYPES), (
            "knowledge_component_types のシードが core.schema.COMPONENT_TYPES と食い違っています。\n"
            f"  SQL のみ: {sorted(set(seeded) - set(COMPONENT_TYPES))}\n"
            f"  コードのみ: {sorted(set(COMPONENT_TYPES) - set(seeded))}"
        )

    @pytest.mark.parametrize("table", ["knowledge_claim_types", "knowledge_component_types"])
    def test_seed_has_no_duplicates(self, table):
        seeded = _seeded_values(table)
        assert len(seeded) == len(set(seeded)), f"{table} のシードに重複があります"

    def test_fallback_values_are_in_vocabulary(self):
        # 語彙外の型を丸める先（core/knowledge_objects/schema.py）が語彙表に在ること。
        assert "unknown" in CLAIM_TYPES
        assert "theory" in COMPONENT_TYPES


# ---------------------------------------------------------------------------
# (b)(c)(d) A層の語彙 ⊆ core の語彙（src は非改変 = KO1）
# ---------------------------------------------------------------------------


class TestAgentVocabularyIsCovered:
    def test_core_predicates_subset_of_core_predicate_enum(self):
        from episteme_graph.agents.dsl_linking.schema import CORE_PREDICATES

        enum_values = {p.value for p in CorePredicate}
        missing = [p for p in CORE_PREDICATES if p not in enum_values]
        assert missing == [], (
            f"dsl_linking の述語が core.schema.CorePredicate に無い: {missing}"
        )

    def test_produces_is_declared(self):
        # P1-4 で足した PRODUCES（dsl_linking が出すが enum に無かった）。
        assert CorePredicate.PRODUCES.value == "PRODUCES"

    def test_claim_tiers_match_agent_vocabulary(self):
        from episteme_graph.agents.claim_qualification.schema import (
            CLAIM_TIERS as AGENT_CLAIM_TIERS,
        )

        assert tuple(AGENT_CLAIM_TIERS) == tuple(CLAIM_TIERS)

    def test_claim_type_ontology_subset(self):
        from episteme_graph.agents.claim_object_builder.schema import CLAIM_TYPE_ONTOLOGY

        missing = [c for c in CLAIM_TYPE_ONTOLOGY if c not in CLAIM_TYPES]
        assert missing == [], f"claim_object_builder の型が CLAIM_TYPES に無い: {missing}"

    def test_cartridge_component_types_subset(self):
        cartridge_files = sorted((BACKEND / "cartridges").glob("*/component_types.json"))
        assert cartridge_files, "expected at least one cartridge component_types.json"
        for path in cartridge_files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            ids = [
                str(entry.get("id") or "")
                for entry in (payload.get("component_types") or [])
                if str(entry.get("id") or "")
            ]
            missing = [i for i in ids if i not in COMPONENT_TYPES]
            assert missing == [], f"{path.name} の型が COMPONENT_TYPES に無い: {missing}"


# ---------------------------------------------------------------------------
# (e) live ビューの再作成規律（KO5）
# ---------------------------------------------------------------------------


_NUMBERED_RE = re.compile(r"^(\d{3})_")
_LIVE_TABLES = ("theory_claims", "theory_components")


class TestLiveViewRecreationDiscipline:
    """``SELECT *`` のビューは作成時の列で固定されるため、2 表に列を足す migration は
    同じファイルの末尾で ``CREATE OR REPLACE VIEW`` を再実行しなければならない。"""

    def test_migrations_adding_columns_recreate_the_view(self):
        violations: list[str] = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            match = _NUMBERED_RE.match(path.name)
            if not match or int(match.group(1)) < 78:
                continue
            sql = path.read_text(encoding="utf-8")
            for table in _LIVE_TABLES:
                adds_column = re.search(
                    rf"ALTER\s+TABLE\s+{table}\s+ADD\s+COLUMN", sql, re.IGNORECASE
                )
                if not adds_column:
                    continue
                recreates = re.search(
                    rf"CREATE\s+OR\s+REPLACE\s+VIEW\s+{table}_live", sql, re.IGNORECASE
                )
                if not recreates:
                    violations.append(
                        f"{path.name}: {table} に列を足しているのに "
                        f"CREATE OR REPLACE VIEW {table}_live が無い"
                    )
        assert violations == [], "\n".join(violations)

    def test_078_defines_both_live_views(self):
        sql = KNOWLEDGE_OBJECTS_MIGRATION.read_text(encoding="utf-8")
        for table in _LIVE_TABLES:
            assert re.search(
                rf"CREATE\s+OR\s+REPLACE\s+VIEW\s+{table}_live\s+AS", sql, re.IGNORECASE
            ), f"{table}_live のビュー定義が 078 に無い"
            assert "superseded_at IS NULL" in sql


# ---------------------------------------------------------------------------
# 行を消さない（KO3 / P4）
# ---------------------------------------------------------------------------


class TestKnowledgeMigrationsDoNotDeleteRows:
    @pytest.mark.parametrize(
        "name", ["078_knowledge_objects.sql", "079_analysis_artifacts.sql"]
    )
    def test_no_delete_statements(self, name):
        sql = (MIGRATIONS_DIR / name).read_text(encoding="utf-8")
        # 行コメントを除いた本文に DELETE FROM が無いこと。
        body = re.sub(r"--[^\n]*", "", sql)
        assert not re.search(r"\bDELETE\s+FROM\b", body, re.IGNORECASE), (
            f"{name} に DELETE FROM があります（KO3: 再解析は supersede で表す）"
        )
