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
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _sql() -> str:
    return _LINE_COMMENT_RE.sub("", MIGRATION.read_text(encoding="utf-8"))


def _check_values(constraint: str) -> tuple[str, ...]:
    match = re.search(
        rf"CONSTRAINT\s+{constraint}\s+CHECK\s*\(\s*\w+\s+IN\s*\(([^)]*)\)\s*\)",
        _sql(),
        re.IGNORECASE,
    )
    assert match, f"CHECK {constraint} が 088 に無い"
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
        ("instrument_wirings_origin_check", schema.WIRING_ORIGINS),
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
