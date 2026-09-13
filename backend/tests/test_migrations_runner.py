"""薄いマイグレーションランナー（core/migrations.py）のテスト。

構成:
- ``TestDiscovery``: ``discover_migration_files`` のファイル探索・順序・
  異常系（命名規則違反 / 番号重複 / init.sql 欠落）を一時ディレクトリの
  fixture ファイルで検証する。
- ``TestExecutionMechanics``: ``run_migrations`` の実行機構を、
  ``exec_driver_sql`` / ``commit`` / ``rollback`` / ``close`` を記録する
  duck-typed fake connection/engine で検証する（実 DB 接続なし）。
- ``TestRealFilesNumberingContinuity`` / ``TestIdempotencyLint``: 実際の
  ``backend/db/*.sql`` に対する静的検査。特に ``TestIdempotencyLint`` は
  「新しい migration ファイル（044以降）が冪等規約を守ること」を将来にわたって
  強制するガードレールであり、既存ファイルの並行整合作業が完了するまでは
  fail する可能性がある（既知。意図的に緩めない）。
- ``TestMainPyHasNoInlineDdl``: 2026-07 の「正本一本化」（``backend/api/main.py``
  の ``_run_migrations()`` を削除し ``core/migrations.py::run_migrations()`` に
  一本化）が再度崩れない（DDL の正本が2つに戻らない）ことを検証するガードレール。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pytest

from core.migrations import (
    MIGRATION_LOCK_KEY,
    MIGRATIONS_DIR,
    discover_migration_files,
    run_migrations,
)
from tests.guardrail_helpers import assert_source_forbids, extract_function_source

_LOCK_SQL = f"SELECT pg_advisory_lock({MIGRATION_LOCK_KEY})"
_UNLOCK_SQL = f"SELECT pg_advisory_unlock({MIGRATION_LOCK_KEY})"


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_init_first_then_numeric_order(self, tmp_path: Path):
        (tmp_path / "init.sql").write_text("-- init", encoding="utf-8")
        (tmp_path / "003_c.sql").write_text("-- 3", encoding="utf-8")
        (tmp_path / "002_b.sql").write_text("-- 2", encoding="utf-8")
        (tmp_path / "010_j.sql").write_text("-- 10", encoding="utf-8")

        files = discover_migration_files(tmp_path)

        assert [p.name for p in files] == [
            "init.sql",
            "002_b.sql",
            "003_c.sql",
            "010_j.sql",
        ]

    def test_naming_violation_raises(self, tmp_path: Path):
        (tmp_path / "init.sql").write_text("-- init", encoding="utf-8")
        (tmp_path / "not_numbered.sql").write_text("-- oops", encoding="utf-8")

        with pytest.raises(ValueError, match="naming convention"):
            discover_migration_files(tmp_path)

    def test_naming_violation_two_digit_prefix_raises(self, tmp_path: Path):
        # 3桁ゼロ埋めでない (例: 02_x.sql) は命名規則違反として fail loud
        (tmp_path / "init.sql").write_text("-- init", encoding="utf-8")
        (tmp_path / "02_x.sql").write_text("-- oops", encoding="utf-8")

        with pytest.raises(ValueError, match="naming convention"):
            discover_migration_files(tmp_path)

    def test_duplicate_number_raises(self, tmp_path: Path):
        (tmp_path / "init.sql").write_text("-- init", encoding="utf-8")
        (tmp_path / "004_a.sql").write_text("-- a", encoding="utf-8")
        (tmp_path / "004_b.sql").write_text("-- b", encoding="utf-8")

        with pytest.raises(ValueError, match="duplicate migration number 004"):
            discover_migration_files(tmp_path)

    def test_missing_init_raises(self, tmp_path: Path):
        (tmp_path / "002_a.sql").write_text("-- a", encoding="utf-8")

        with pytest.raises(ValueError, match="init.sql"):
            discover_migration_files(tmp_path)

    def test_missing_directory_raises(self, tmp_path: Path):
        with pytest.raises(ValueError):
            discover_migration_files(tmp_path / "does-not-exist")

    def test_default_directory_is_backend_db(self):
        assert MIGRATIONS_DIR.name == "db"
        assert MIGRATIONS_DIR.parent.name == "backend"


# ---------------------------------------------------------------------------
# execution mechanics (fake connection/engine — no real DB)
# ---------------------------------------------------------------------------


class _FakeConnection:
    """exec_driver_sql / commit / rollback / close を記録する duck-typed fake。"""

    def __init__(self, fail_on: Optional[str] = None):
        self.executed: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._fail_on = fail_on

    def exec_driver_sql(self, sql: str) -> None:
        self.executed.append(sql)
        if self._fail_on is not None and self._fail_on in sql:
            raise RuntimeError("boom: forced failure")

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class _FakeEngine:
    def __init__(self, conn: _FakeConnection):
        self._conn = conn

    def connect(self) -> _FakeConnection:
        return self._conn


class TestExecutionMechanics:
    def test_applies_all_files_in_order_and_returns_names(self, tmp_path: Path):
        (tmp_path / "init.sql").write_text(
            "CREATE TABLE IF NOT EXISTS a (id int);", encoding="utf-8"
        )
        (tmp_path / "002_b.sql").write_text(
            "CREATE TABLE IF NOT EXISTS b (id int);", encoding="utf-8"
        )
        conn = _FakeConnection()
        engine = _FakeEngine(conn)

        applied = run_migrations(engine=engine, directory=tmp_path)

        assert applied == ["init.sql", "002_b.sql"]
        assert "CREATE TABLE IF NOT EXISTS a (id int);" in conn.executed
        assert "CREATE TABLE IF NOT EXISTS b (id int);" in conn.executed

    def test_commits_per_file_not_once_at_the_end(self, tmp_path: Path):
        (tmp_path / "init.sql").write_text(
            "CREATE TABLE IF NOT EXISTS a (id int);", encoding="utf-8"
        )
        (tmp_path / "002_b.sql").write_text(
            "CREATE TABLE IF NOT EXISTS b (id int);", encoding="utf-8"
        )
        conn = _FakeConnection()
        engine = _FakeEngine(conn)

        run_migrations(engine=engine, directory=tmp_path)

        # lock-acquire commit(1) + 2 file commits(2) + unlock commit(1) = 4
        assert conn.commits == 4
        assert conn.rollbacks == 0
        assert conn.closed is True

    def test_advisory_lock_acquired_before_files_and_released_after(
        self, tmp_path: Path
    ):
        (tmp_path / "init.sql").write_text(
            "CREATE TABLE IF NOT EXISTS a (id int);", encoding="utf-8"
        )
        conn = _FakeConnection()
        engine = _FakeEngine(conn)

        run_migrations(engine=engine, directory=tmp_path)

        lock_idx = conn.executed.index(_LOCK_SQL)
        unlock_idx = conn.executed.index(_UNLOCK_SQL)
        file_idx = conn.executed.index("CREATE TABLE IF NOT EXISTS a (id int);")
        assert lock_idx < file_idx < unlock_idx

    def test_failure_rolls_back_file_and_message_includes_filename(
        self, tmp_path: Path
    ):
        (tmp_path / "init.sql").write_text(
            "CREATE TABLE IF NOT EXISTS a (id int);", encoding="utf-8"
        )
        (tmp_path / "002_bad.sql").write_text("THIS WILL FAIL;", encoding="utf-8")
        conn = _FakeConnection(fail_on="THIS WILL FAIL")
        engine = _FakeEngine(conn)

        with pytest.raises(RuntimeError) as exc_info:
            run_migrations(engine=engine, directory=tmp_path)

        assert "002_bad.sql" in str(exc_info.value)
        assert conn.rollbacks == 1

    def test_lock_is_released_even_when_a_file_fails(self, tmp_path: Path):
        (tmp_path / "init.sql").write_text(
            "CREATE TABLE IF NOT EXISTS a (id int);", encoding="utf-8"
        )
        (tmp_path / "002_bad.sql").write_text("THIS WILL FAIL;", encoding="utf-8")
        conn = _FakeConnection(fail_on="THIS WILL FAIL")
        engine = _FakeEngine(conn)

        with pytest.raises(RuntimeError):
            run_migrations(engine=engine, directory=tmp_path)

        assert _UNLOCK_SQL in conn.executed
        assert conn.closed is True

    def test_skips_blank_files_without_executing_or_appending(self, tmp_path: Path):
        (tmp_path / "init.sql").write_text("   \n\n  ", encoding="utf-8")
        (tmp_path / "002_real.sql").write_text(
            "CREATE TABLE IF NOT EXISTS a (id int);", encoding="utf-8"
        )
        conn = _FakeConnection()
        engine = _FakeEngine(conn)

        applied = run_migrations(engine=engine, directory=tmp_path)

        assert applied == ["002_real.sql"]

    def test_skips_comment_only_files_without_executing_or_appending(
        self, tmp_path: Path
    ):
        """行コメント（--）だけのファイル（統合済み no-op スタブ、例: 010/035）は
        DB に何も実行せずスキップすること（コメントのみの文字列実行でドライバが
        エラーを返す事態を避けるガード）。"""
        (tmp_path / "init.sql").write_text(
            "-- このファイルは統合済みで no-op\n-- 正本は 044 を参照\n",
            encoding="utf-8",
        )
        (tmp_path / "002_real.sql").write_text(
            "CREATE TABLE IF NOT EXISTS a (id int);", encoding="utf-8"
        )
        conn = _FakeConnection()
        engine = _FakeEngine(conn)

        applied = run_migrations(engine=engine, directory=tmp_path)

        assert applied == ["002_real.sql"]
        assert not any("--" in sql for sql in conn.executed)

    def test_skips_files_with_comments_and_blank_lines_mixed(self, tmp_path: Path):
        """行コメントと空白行が混在するだけ（実行可能な SQL 文が無い）ファイルも
        スキップすること。"""
        (tmp_path / "init.sql").write_text(
            "CREATE TABLE IF NOT EXISTS a (id int);", encoding="utf-8"
        )
        (tmp_path / "002_stub.sql").write_text(
            "-- header comment\n\n   \n-- another comment\n\n", encoding="utf-8"
        )
        conn = _FakeConnection()
        engine = _FakeEngine(conn)

        applied = run_migrations(engine=engine, directory=tmp_path)

        assert applied == ["init.sql"]

    def test_uses_core_postgres_get_engine_when_engine_not_provided(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        (tmp_path / "init.sql").write_text(
            "CREATE TABLE IF NOT EXISTS a (id int);", encoding="utf-8"
        )
        conn = _FakeConnection()
        engine = _FakeEngine(conn)
        monkeypatch.setattr("core.postgres.get_engine", lambda: engine)

        applied = run_migrations(directory=tmp_path)

        assert applied == ["init.sql"]


# ---------------------------------------------------------------------------
# static checks against the real backend/db/*.sql corpus
# ---------------------------------------------------------------------------

_NUMBERED_RE = re.compile(r"^(\d{3})_")


def _existing_numbers(directory: Path) -> list[int]:
    numbers = []
    for path in sorted(directory.glob("*.sql")):
        match = _NUMBERED_RE.match(path.name)
        if match:
            numbers.append(int(match.group(1)))
    return sorted(numbers)


class TestRealFilesNumberingContinuity:
    def test_no_gaps_between_lowest_and_highest_number(self):
        numbers = _existing_numbers(MIGRATIONS_DIR)
        assert numbers, "expected at least one numbered migration file"

        expected = list(range(numbers[0], numbers[-1] + 1))
        missing = sorted(set(expected) - set(numbers))
        assert missing == [], f"missing migration numbers: {missing}"

    def test_real_directory_is_discoverable(self):
        # discover_migration_files 自体が実ディレクトリに対して例外を出さないこと
        files = discover_migration_files(MIGRATIONS_DIR)
        assert files[0].name == "init.sql"
        assert len(files) == len(_existing_numbers(MIGRATIONS_DIR)) + 1


# --- idempotency lint --------------------------------------------------------
#
# 新しい migration ファイル（044以降）が冪等規約を守ることを将来にわたって
# 強制するガードレール。既存ファイル（002〜043）は本タスク時点で並行の整合
# 作業が進行中のため、このテストが fail する場合がある（意図的に緩めない —
# fail する場合は「どのファイルの何が違反か」を報告すること）。

_DOLLAR_BLOCK_RE = re.compile(r"\$([A-Za-z_]*)\$.*?\$\1\$", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_DO_UPDATE_RE = re.compile(r"\bDO\s+UPDATE\b", re.IGNORECASE)

_SIMPLE_FORBIDDEN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"\bCREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS\b)", re.IGNORECASE),
        "CREATE TABLE without IF NOT EXISTS",
    ),
    (
        re.compile(r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+CONCURRENTLY\b", re.IGNORECASE),
        "CREATE INDEX CONCURRENTLY is forbidden in migrations "
        "(cannot run inside a transaction)",
    ),
    (
        re.compile(
            r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?!CONCURRENTLY\b)(?!IF\s+NOT\s+EXISTS\b)",
            re.IGNORECASE,
        ),
        "CREATE INDEX without IF NOT EXISTS",
    ),
    (
        re.compile(r"\bADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS\b)", re.IGNORECASE),
        "ADD COLUMN without IF NOT EXISTS",
    ),
    (
        re.compile(r"\bCREATE\s+EXTENSION\s+(?!IF\s+NOT\s+EXISTS\b)", re.IGNORECASE),
        "CREATE EXTENSION without IF NOT EXISTS",
    ),
    (
        re.compile(r"^[ \t]*(BEGIN|COMMIT)\s*;", re.IGNORECASE | re.MULTILINE),
        "explicit BEGIN;/COMMIT; (migration runner manages one transaction per file)",
    ),
    (
        re.compile(r"\bDROP\s+TABLE\s+(?!IF\s+EXISTS\b)", re.IGNORECASE),
        "DROP TABLE without IF EXISTS",
    ),
    (
        re.compile(r"\bADD\s+CONSTRAINT\b", re.IGNORECASE),
        "ADD CONSTRAINT outside a DO $$ guard (constraint swap must be guarded)",
    ),
    # -- 以下、毎起動・全ファイル再実行方式で2回目に失敗する / 黙ってデータを変える DDL。
    #    現行 002〜076 はいずれも違反ゼロ（追加時に確認済み）で、将来の追加を防ぐための
    #    網である。いずれも DO $$ ガード内に書けばマスクされる。
    (
        re.compile(r"\bCREATE\s+TYPE\b", re.IGNORECASE),
        "CREATE TYPE outside a DO $$ guard (duplicate_object on the second boot; "
        "PostgreSQL has no CREATE TYPE IF NOT EXISTS)",
    ),
    (
        re.compile(r"\bCREATE\s+VIEW\b", re.IGNORECASE),
        "CREATE VIEW without OR REPLACE (duplicate_table on the second boot)",
    ),
    (
        re.compile(r"\bCREATE\s+MATERIALIZED\s+VIEW\s+(?!IF\s+NOT\s+EXISTS\b)", re.IGNORECASE),
        "CREATE MATERIALIZED VIEW without IF NOT EXISTS",
    ),
    (
        re.compile(r"\bCREATE\s+SEQUENCE\s+(?!IF\s+NOT\s+EXISTS\b)", re.IGNORECASE),
        "CREATE SEQUENCE without IF NOT EXISTS",
    ),
    (
        re.compile(r"\bCREATE\s+TRIGGER\b", re.IGNORECASE),
        "CREATE TRIGGER outside a DO $$ guard (no IF NOT EXISTS in PostgreSQL)",
    ),
    (
        re.compile(r"\bCREATE\s+FUNCTION\b", re.IGNORECASE),
        "CREATE FUNCTION without OR REPLACE",
    ),
    (
        re.compile(r"\bDROP\s+INDEX\s+(?!IF\s+EXISTS\b)", re.IGNORECASE),
        "DROP INDEX without IF EXISTS",
    ),
    (
        re.compile(r"\bDROP\s+CONSTRAINT\s+(?!IF\s+EXISTS\b)", re.IGNORECASE),
        "DROP CONSTRAINT without IF EXISTS",
    ),
    (
        re.compile(r"\bDROP\s+COLUMN\s+(?!IF\s+EXISTS\b)", re.IGNORECASE),
        "DROP COLUMN without IF EXISTS",
    ),
    (
        re.compile(r"\bRENAME\s+(?:COLUMN|TO|CONSTRAINT)\b", re.IGNORECASE),
        "RENAME outside a DO $$ guard (the second boot renames nothing and errors)",
    ),
    (
        re.compile(r"\bADD\s+PRIMARY\s+KEY\b", re.IGNORECASE),
        "ADD PRIMARY KEY outside a DO $$ guard",
    ),
    (
        re.compile(r"\bALTER\s+COLUMN\s+\w+\s+TYPE\b", re.IGNORECASE),
        "ALTER COLUMN ... TYPE outside a DO $$ guard "
        "(re-running a type change can silently rewrite/erase data — the documented "
        "worst case is a vector column retype wiping every embedding on each boot)",
    ),
]

#: シード INSERT の検出（``migration でシードしない`` — 設計書 070 / 071 / 076）。
#: ``ON CONFLICT`` の無い INSERT は、2回目の起動で行を二重に入れるか、管理者が
#: 削除した行を復活させて「削除が効かない」状態を作る。
_INSERT_RE = re.compile(r"\bINSERT\s+INTO\b", re.IGNORECASE)


def _strip_comments_and_dollar_blocks(sql: str) -> str:
    """SQL 行コメント（--）とドル引用ブロック（$$...$$ / $tag$...$tag$）を除去する。

    行番号をレポートで使えるように、除去した箇所は同じ改行数の空行に
    置き換える（文字位置は変わっても改行の総数は変わらない）。
    """
    sql = _LINE_COMMENT_RE.sub("", sql)

    def _blank(match: "re.Match[str]") -> str:
        return "\n" * match.group(0).count("\n")

    return _DOLLAR_BLOCK_RE.sub(_blank, sql)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _collect_idempotency_violations(directory: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        raw = path.read_text(encoding="utf-8")
        stripped = _strip_comments_and_dollar_blocks(raw)

        for pattern, description in _SIMPLE_FORBIDDEN_PATTERNS:
            for match in pattern.finditer(stripped):
                violations.append(
                    f"{path.name}:{_line_of(stripped, match.start())}: {description}"
                )

        # ON CONFLICT (...) DO UPDATE SET ... は許可されたイディオムなので
        # 「素の UPDATE」検出の前にマスクしておく。
        masked = _DO_UPDATE_RE.sub(lambda m: "X" * len(m.group(0)), stripped)
        # DO $$ ガード外の UPDATE / DELETE は、WHERE 句で自己収束する
        # バックフィル（旧 _run_migrations() 時代から毎起動実行されてきた形）
        # のみ許可する。WHERE の無い全行更新・全行削除は違反。
        for match in re.finditer(
            r"\b(UPDATE\s|DELETE\s+FROM\b)", masked, re.IGNORECASE
        ):
            end = masked.find(";", match.start())
            statement = masked[match.start(): end if end != -1 else len(masked)]
            if re.search(r"\bWHERE\b", statement, re.IGNORECASE):
                continue
            violations.append(
                f"{path.name}:{_line_of(masked, match.start())}: "
                "UPDATE/DELETE without WHERE outside a DO $$ guard "
                "(ON CONFLICT ... DO UPDATE and WHERE-guarded backfills are fine)"
            )

        # シード INSERT（ON CONFLICT の無い INSERT）。毎起動再実行で行が二重に入るか、
        # 管理者が削除した行が復活して削除が効かなくなる。
        for match in _INSERT_RE.finditer(stripped):
            end = stripped.find(";", match.start())
            statement = stripped[match.start(): end if end != -1 else len(stripped)]
            if re.search(r"\bON\s+CONFLICT\b", statement, re.IGNORECASE):
                continue
            violations.append(
                f"{path.name}:{_line_of(stripped, match.start())}: "
                "INSERT without ON CONFLICT outside a DO $$ guard "
                "(migrations must not seed rows: the second boot duplicates them, "
                "or resurrects rows an admin deleted)"
            )

    return violations


class TestIdempotencyLint:
    """新しい migration ファイルが冪等規約を守ることを強制するガードレール。"""

    def test_all_files_are_idempotent(self):
        violations = _collect_idempotency_violations(MIGRATIONS_DIR)
        assert violations == [], (
            f"{len(violations)} idempotency violation(s) found:\n"
            + "\n".join(violations)
        )


class TestIdempotencyLintDetectsNonIdempotentDdl:
    """lint そのものの単体テスト（パターン自体の退行を検出する）。

    ``_collect_idempotency_violations`` は実ディレクトリにしか当てられておらず、
    「現行ファイルが違反ゼロ」であることは、パターンが**機能している**ことの
    証拠にならない（全パターンを壊しても緑のまま）。1件ずつ合成ファイルを
    食わせて、確かに検出することを固定する。
    """

    NON_IDEMPOTENT = [
        ("CREATE TABLE t (id int);", "CREATE TABLE"),
        ("CREATE INDEX idx_a ON t (a);", "CREATE INDEX"),
        ("CREATE INDEX CONCURRENTLY idx_b ON t (b);", "CONCURRENTLY"),
        ("ALTER TABLE t ADD COLUMN c int;", "ADD COLUMN"),
        ("CREATE EXTENSION vector;", "CREATE EXTENSION"),
        ("DROP TABLE t;", "DROP TABLE"),
        ("ALTER TABLE t ADD CONSTRAINT t_chk CHECK (a > 0);", "ADD CONSTRAINT"),
        ("CREATE TYPE mood AS ENUM ('a');", "CREATE TYPE"),
        ("CREATE VIEW v AS SELECT 1;", "CREATE VIEW"),
        ("CREATE MATERIALIZED VIEW mv AS SELECT 1;", "MATERIALIZED VIEW"),
        ("CREATE SEQUENCE s;", "CREATE SEQUENCE"),
        ("CREATE TRIGGER trg BEFORE INSERT ON t EXECUTE FUNCTION f();", "CREATE TRIGGER"),
        ("CREATE FUNCTION f() RETURNS int AS 'SELECT 1' LANGUAGE sql;", "CREATE FUNCTION"),
        ("DROP INDEX idx_a;", "DROP INDEX"),
        ("ALTER TABLE t DROP CONSTRAINT t_chk;", "DROP CONSTRAINT"),
        ("ALTER TABLE t DROP COLUMN c;", "DROP COLUMN"),
        ("ALTER TABLE t RENAME COLUMN a TO b;", "RENAME"),
        ("ALTER TABLE t ADD PRIMARY KEY (id);", "ADD PRIMARY KEY"),
        ("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1536);", "ALTER COLUMN"),
        ("INSERT INTO seeds (id) VALUES (1);", "INSERT without ON CONFLICT"),
        ("UPDATE t SET a = 1;", "without WHERE"),
        ("DELETE FROM t;", "without WHERE"),
        ("BEGIN;", "BEGIN"),
    ]

    IDEMPOTENT = [
        "CREATE TABLE IF NOT EXISTS t (id int);",
        "CREATE INDEX IF NOT EXISTS idx_a ON t (a);",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_u ON t (a);",
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS c int;",
        "CREATE EXTENSION IF NOT EXISTS vector;",
        "DROP TABLE IF EXISTS t;",
        "CREATE OR REPLACE VIEW v AS SELECT 1;",
        "CREATE OR REPLACE FUNCTION f() RETURNS int AS 'SELECT 1' LANGUAGE sql;",
        "INSERT INTO seeds (id) VALUES (1) ON CONFLICT (id) DO NOTHING;",
        "INSERT INTO t (id, n) VALUES (1, 2) ON CONFLICT (id) DO UPDATE SET n = 2;",
        "UPDATE t SET a = 1 WHERE a IS NULL;",
        "DELETE FROM t WHERE id = 1;",
        "ALTER TABLE t ALTER COLUMN c DROP NOT NULL;",
    ]

    def _lint(self, tmp_path: Path, body: str) -> list[str]:
        (tmp_path / "init.sql").write_text("-- init\n", encoding="utf-8")
        (tmp_path / "002_probe.sql").write_text(body, encoding="utf-8")
        return _collect_idempotency_violations(tmp_path)

    @pytest.mark.parametrize("statement,needle", NON_IDEMPOTENT)
    def test_flags_non_idempotent_statement(self, tmp_path: Path, statement, needle):
        violations = self._lint(tmp_path, statement + "\n")
        assert violations, f"lint missed a non-idempotent statement: {statement!r}"
        assert any(needle.lower() in v.lower() for v in violations), (
            f"{statement!r} flagged, but not for {needle!r}: {violations}"
        )

    @pytest.mark.parametrize("statement", IDEMPOTENT)
    def test_accepts_idempotent_statement(self, tmp_path: Path, statement):
        assert self._lint(tmp_path, statement + "\n") == [], (
            f"lint false-positives on an idempotent statement: {statement!r}"
        )

    def test_do_block_masks_guarded_statements(self, tmp_path: Path):
        """DO $$ ガード内は既存方針どおりマスクする（挙動の明示）。"""
        body = (
            "DO $$ BEGIN\n"
            "  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'mood') THEN\n"
            "    CREATE TYPE mood AS ENUM ('a');\n"
            "  END IF;\n"
            "END $$;\n"
        )
        assert self._lint(tmp_path, body) == []

    def test_line_comments_are_not_linted(self, tmp_path: Path):
        assert self._lint(tmp_path, "-- CREATE TABLE t (id int);\n") == []


# ---------------------------------------------------------------------------
# main.py must not regain a second, inline DDL source of truth
# ---------------------------------------------------------------------------

_MAIN_PY = MIGRATIONS_DIR.parent / "api" / "main.py"

_FORBIDDEN_DDL_KEYWORDS = [
    "CREATE TABLE",
    "ALTER TABLE",
    "CREATE INDEX",
    "ADD COLUMN",
    "ADD CONSTRAINT",
    "CREATE EXTENSION",
    "DROP TABLE",
]


class TestMainPyHasNoInlineDdl:
    """``backend/api/main.py`` に旧 ``_run_migrations()`` のインライン DDL が
    復活していないことを検証するガードレール。

    マイグレーションの正本は ``backend/db/*.sql``
    （``core/migrations.py::run_migrations()`` が番号順に適用する）の一本のみ
    であるべきで、main.py に DDL 文字列が紛れ込むと「正本が2つ」問題
    （どちらが実際に適用されるスキーマなのか分からなくなる）が再発する。
    このテストは main.py のソースを機械的に走査してその再発を防ぐ。
    """

    @staticmethod
    def _main_py_source() -> str:
        return _MAIN_PY.read_text(encoding="utf-8")

    def test_main_py_exists(self):
        assert _MAIN_PY.is_file(), f"expected main.py at {_MAIN_PY}"

    def test_run_migrations_function_removed(self):
        src = self._main_py_source()
        assert "def _run_migrations" not in src

    def test_no_ddl_keywords_in_main_py(self):
        assert_source_forbids(
            self._main_py_source(),
            _FORBIDDEN_DDL_KEYWORDS,
            context=str(_MAIN_PY),
        )

    def test_lifespan_calls_run_migrations(self):
        src = self._main_py_source()
        lifespan_src = extract_function_source(src, "_lifespan")
        assert "run_migrations()" in lifespan_src
