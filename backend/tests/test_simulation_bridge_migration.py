"""検証シミュレーション層（SB層）— migration 088 の静的検査。

正本: ``docs/features/simulation_bridge_design.md`` §4。語彙の正本は
``core/simulation_bridge/schema.py``。SQL をパースして、シードのラベル・CHECK の値・
追加列がコード側と一致すること、users(id) を参照しないこと、DELETE が無いことを固定する。
DB には接続しない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.library import schema as library_schema  # noqa: E402
from core.schema import LIBRARY_ENTRY_TYPES  # noqa: E402
from core.simulation_bridge import schema  # noqa: E402

MIGRATION = BACKEND / "db" / "088_simulation_bridge.sql"
#: v1.1（§14.2）: 所有・共有・予想の列追加と origin CHECK の張り直し。
MIGRATION_089 = BACKEND / "db" / "089_simulation_learner.sql"
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _sql(path: Path = MIGRATION) -> str:
    return _LINE_COMMENT_RE.sub("", path.read_text(encoding="utf-8"))


def _check_values(constraint: str, path: Path = MIGRATION) -> tuple[str, ...]:
    match = re.search(
        rf"CONSTRAINT\s+{constraint}\s+CHECK\s*\(\s*\w+\s+IN\s*\(([^)]*)\)\s*\)",
        _sql(path),
        re.IGNORECASE,
    )
    assert match, f"CHECK {constraint} が {path.name} に無い"
    return tuple(re.findall(r"'([^']+)'", match.group(1)))


def test_entry_type_seed_matches_part_labels():
    match = re.search(
        r"INSERT\s+INTO\s+knowledge_entry_types\s*\([^)]*\)\s*VALUES(.*?)ON\s+CONFLICT",
        _sql(),
        re.IGNORECASE | re.DOTALL,
    )
    assert match
    pairs = dict(re.findall(r"\('([^']+)',\s*'([^']*)'\)", match.group(1)))
    assert pairs == schema.PART_TYPE_LABELS
    for entry_type, label in schema.PART_TYPE_LABELS.items():
        assert entry_type in LIBRARY_ENTRY_TYPES
        assert library_schema.ENTRY_TYPE_LABELS[entry_type] == label


@pytest.mark.parametrize(
    "constraint,vocabulary",
    [
        ("simulation_programs_review_status_check", schema.PROGRAM_STATUSES),
        ("instrument_wirings_review_status_check", schema.WIRING_STATUSES),
        ("simulation_runs_status_check", schema.RUN_STATUSES),
        ("simulation_programs_language_check", (schema.PROGRAM_LANGUAGE,)),
    ],
)
def test_check_vocabularies_match_schema(constraint, vocabulary):
    assert set(_check_values(constraint)) == set(vocabulary)


def test_tables_and_columns():
    sql = _sql()
    for table in ("simulation_programs", "instrument_wirings", "simulation_runs"):
        assert re.search(rf"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+{table}\b", sql), table
    assert re.search(
        r"ALTER\s+TABLE\s+epistemic_ledger\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+simulation_candidates\s+JSONB",
        sql,
        re.IGNORECASE,
    )
    # document_id は documents の FK CASCADE（教材の物理削除に追随する）。
    assert sql.count("REFERENCES documents(id) ON DELETE CASCADE") == 3
    assert "superseded_by_id" in sql
    assert "parent_wiring_id" in sql
    assert "ledger_candidate_id" in sql


def test_no_users_reference_and_no_delete_and_no_confidence():
    sql = _sql()
    assert "REFERENCES users" not in sql
    assert not re.search(r"\bDELETE\s+FROM\b", sql, re.IGNORECASE)
    assert "confidence" not in sql.lower()


def test_plain_sql_percent():
    assert "%%" not in MIGRATION.read_text(encoding="utf-8")


def test_origin_check_is_rebuilt_with_learner_in_089():
    """088 の CHECK は v1 の 2 語。089 が 'learner' 入りで張り直し、最終状態はコードの語彙と一致する。"""
    assert set(_check_values("instrument_wirings_origin_check", MIGRATION_089)) == set(schema.WIRING_ORIGINS)
    assert schema.WIRING_ORIGIN_LEARNER in schema.WIRING_ORIGINS
    assert set(_check_values("instrument_wirings_origin_check")) == set(schema.WIRING_ORIGINS) - {
        schema.WIRING_ORIGIN_LEARNER
    }


def test_089_adds_owner_shared_prediction_without_users_fk():
    sql = _sql(MIGRATION_089)
    for table in ("simulation_programs", "instrument_wirings", "simulation_runs"):
        for column in ("owner_user_id UUID", "course_id TEXT"):
            assert re.search(
                rf"ALTER\s+TABLE\s+{table}\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+{column}", sql
            ), (table, column)
    assert re.search(r"ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+shared_at\s+TIMESTAMPTZ", sql)
    assert re.search(r"ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+prediction\s+JSONB\s+NOT\s+NULL", sql)
    assert "WHERE shared_at IS NOT NULL" in sql
    assert "REFERENCES users" not in sql
    assert not re.search(r"\bDELETE\s+FROM\b", sql, re.IGNORECASE)
    assert "%%" not in MIGRATION_089.read_text(encoding="utf-8")


def test_089_creates_the_equations_live_view():
    """078 は knowledge_equations の基表だけを作っていた（KO5 の live ビューが無く、SB層の式の
    読みが UndefinedTable で落ちていた）。089 がビューを作る。"""
    sql = _sql(MIGRATION_089)
    assert re.search(
        r"CREATE\s+OR\s+REPLACE\s+VIEW\s+knowledge_equations_live\s+AS\s+SELECT\s+\*\s+FROM\s+"
        r"knowledge_equations\s+WHERE\s+superseded_at\s+IS\s+NULL",
        sql,
        re.IGNORECASE,
    )
