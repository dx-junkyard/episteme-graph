"""migration 083（D層・C層の表現語彙）の構成テスト。

正本: docs/features/knowledge_transfer_design.md §8（P4-5）。

ここで固定するのは「SQL ファイルの内容」だけ（DB へ接続しない）:

1. 3 表（challenges / epistemic_ledger / component_citations）への列追加であること
2. 冪等（ADD COLUMN IF NOT EXISTS + 名前付き CHECK の DO $$ ガード）
3. **語彙表（コード）と CHECK（SQL）が一致**していること
   — core/doubt/schema.py::CHALLENGE_MODES / core/schema.py::CITATION_INTENTS
4. DELETE / DROP / CREATE TABLE / シード INSERT が無いこと（KT5）

全 migration 共通の冪等性 lint は tests/test_migrations_runner.py が担保する。
ここでは 083 固有の構成を明示的に固定する（067 の TestMigration067 と同じ立場）。
"""

from __future__ import annotations

import re
from pathlib import Path

from core import schema as core_schema
from core.doubt.schema import CHALLENGE_MODES, EVIDENCE_LINE_KINDS
from tests.guardrail_helpers import read_migration_sql

_BACKEND = Path(__file__).resolve().parents[1]
_MIGRATION_083_SRC = read_migration_sql(_BACKEND, 83)


def _statements(sql: str) -> list[str]:
    """行コメントを落とし、DO $$ ブロックを 1 文として扱った文の列を返す。"""
    without_comments = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    # DO $$ ... END $$; を丸ごと 1 文として退避する
    blocks: list[str] = []

    def _stash(match: "re.Match[str]") -> str:
        blocks.append(match.group(0))
        return f"@@BLOCK{len(blocks) - 1}@@;"

    masked = re.sub(r"DO \$\$[\s\S]*?END \$\$;", _stash, without_comments)
    out = []
    for raw in masked.split(";"):
        stmt = " ".join(raw.split())
        if not stmt:
            continue
        match = re.fullmatch(r"@@BLOCK(\d+)@@", stmt)
        out.append(blocks[int(match.group(1))] if match else stmt)
    return out


class TestMigration083Targets:
    def test_targets_the_three_expected_tables(self):
        for table in ("challenges", "epistemic_ledger", "component_citations"):
            assert table in _MIGRATION_083_SRC

    def test_adds_challenge_mode_and_target_element_ref(self):
        assert "challenge_mode" in _MIGRATION_083_SRC
        assert "target_element_ref" in _MIGRATION_083_SRC

    def test_adds_evidence_lines(self):
        assert "evidence_lines" in _MIGRATION_083_SRC

    def test_adds_citation_intent(self):
        assert "citation_intent" in _MIGRATION_083_SRC

    def test_references_the_design_document(self):
        assert "knowledge_transfer_design.md" in _MIGRATION_083_SRC


class TestMigration083Idempotency:
    def test_every_statement_is_add_column_or_a_guarded_do_block(self):
        for stmt in _statements(_MIGRATION_083_SRC):
            if stmt.startswith("DO $$"):
                # CHECK 制約の追加は「無ければ足す」ガードの中だけ
                assert "IF NOT EXISTS" in stmt
                assert "pg_constraint" in stmt
                continue
            assert stmt.startswith("ALTER TABLE"), stmt
            assert "ADD COLUMN IF NOT EXISTS" in stmt, stmt

    def test_no_table_creation_or_destructive_ddl(self):
        statement = " ".join(_statements(_MIGRATION_083_SRC))
        for forbidden in ("CREATE TABLE", "DROP TABLE", "DROP COLUMN", "TRUNCATE"):
            assert forbidden not in statement

    def test_no_delete_or_seed_insert(self):
        """行を消さない（KT5）／migration でシードしない。"""
        statement = " ".join(_statements(_MIGRATION_083_SRC))
        assert "DELETE FROM" not in statement
        assert "INSERT INTO" not in statement

    def test_named_constraints_are_used_so_the_check_survives_a_reboot(self):
        """列が既に在る再起動でも CHECK が付き損ねないよう名前付きで足す。"""
        assert "challenges_challenge_mode_check" in _MIGRATION_083_SRC
        assert "component_citations_citation_intent_check" in _MIGRATION_083_SRC


class TestMigration083MatchesCodeVocabulary:
    """DB の CHECK と**コード側の語彙表**が一致していること（KO7 と同じ作法）。"""

    def _check_body(self, constraint_name: str) -> str:
        match = re.search(
            rf"ADD CONSTRAINT {constraint_name}\s+CHECK \(([\s\S]*?)\);",
            _MIGRATION_083_SRC,
        )
        assert match, f"{constraint_name} の CHECK 本体が見つからない"
        return match.group(1)

    def test_challenge_mode_check_matches_challenge_modes(self):
        body = self._check_body("challenges_challenge_mode_check")
        assert set(re.findall(r"'([a-z_]+)'", body)) == set(CHALLENGE_MODES)

    def test_citation_intent_check_matches_citation_intents(self):
        body = self._check_body("component_citations_citation_intent_check")
        assert set(re.findall(r"'([a-z_]+)'", body)) == set(core_schema.CITATION_INTENTS)

    def test_evidence_line_kinds_are_documented_in_the_migration_comment(self):
        """evidence_lines は JSONB で CHECK を持たない（語彙の強制は API 層）。

        代わりに、どの語彙を入れる列なのかがファイルのコメントから読めること
        （語彙の正本は core/doubt/schema.py::EVIDENCE_LINE_KINDS）を固定する。
        """
        for kind in EVIDENCE_LINE_KINDS:
            assert kind in _MIGRATION_083_SRC

    def test_defaults_keep_existing_rows_meaningful(self):
        """既存行は DEFAULT で意味不変（challenge_mode='direct' / 空 JSONB / NULL）。"""
        assert "challenge_mode TEXT NOT NULL DEFAULT 'direct'" in _MIGRATION_083_SRC
        assert "target_element_ref JSONB NOT NULL DEFAULT '{}'::jsonb" in _MIGRATION_083_SRC
        assert "evidence_lines JSONB NOT NULL DEFAULT '[]'::jsonb" in _MIGRATION_083_SRC
        # citation_intent は NULL 可（既存行を推測で埋めない, KT5）
        assert re.search(
            r"ADD COLUMN IF NOT EXISTS citation_intent TEXT\s*;", _MIGRATION_083_SRC
        )
