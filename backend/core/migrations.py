"""薄いマイグレーションランナー。

運用ルール:
- ``backend/db/*.sql`` が正本。トラッキングテーブルは持たず、毎起動時に
  ``init.sql`` → ``002_*.sql`` 〜 ``NNN_*.sql`` を数値昇順で**全ファイル再実行**する
  （現行 ``main.py::_run_migrations()`` と同じ「冪等再実行で収束」セマンティクス）。
- そのため全ファイルは冪等でなければならない（``CREATE TABLE IF NOT EXISTS`` /
  ``ADD COLUMN IF NOT EXISTS`` / ``DO $$ ... $$`` ガード等）。
- 新しいスキーマ変更は**新しい番号のファイルを追加**すること。既存ファイルの編集は
  最終状態の是正（typo・冪等性の修正等）に限る — 過去に適用済みの意味を変えない。

このモジュール自体は DB アクセス方法（Connection の取り方）以外のドメイン知識を持たない。
``backend/db/`` の SQL 内容には一切依存しない（ファイルを読んでそのまま流すだけ）。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# リポジトリ内では backend/db/、Docker イメージ内では /app/db/ に解決される
# （backend/Dockerfile が backend/core/ → /app/core/、backend/db/ → /app/db/ に
# それぞれコピーするため、core/migrations.py から見た相対位置は変わらない）。
MIGRATIONS_DIR: Path = Path(__file__).resolve().parents[1] / "db"

# pg_advisory_lock に使う定数キー。複数レプリカ（uvicorn worker / 複数コンテナ）が
# 同時起動しても、この lock を握った1プロセスだけが migration の DDL を実行するための
# ガード。pg_advisory_lock はセッション（＝この Connection）に紐づくロックで、
# トランザクションの commit/rollback をまたいで保持され続けるため、
# 「ファイル単位で commit する」実行方式と両立する
# （pg_advisory_xact_lock は commit 時に自動解放されてしまうため使わない）。
# 値そのものに意味はなく、DB 内で他に同じ key を使っていないことだけが前提。
MIGRATION_LOCK_KEY = 4155251701

_INIT_FILENAME = "init.sql"
_NUMBERED_RE = re.compile(r"^(\d{3})_.+\.sql$")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _is_effectively_blank(sql_text: str) -> bool:
    """SQL 本文が空、または行コメント（``--``）を取り除くと空白のみになるかを判定する。

    「統合済みで no-op になった旧マイグレーション」のスタブ（説明コメントだけを
    残したファイル。例: 010/035 が migration 044 に統合された後の姿）を実行しようと
    してドライバがエラーを返す事態を避けるためのガード。ドル引用ブロック
    （``$$...$$``）内の ``--`` は対象外（本リポジトリの migration にその組み合わせは
    現状無く、単純な行コメント除去のみで十分なため）。
    """
    if not sql_text.strip():
        return True
    return not _LINE_COMMENT_RE.sub("", sql_text).strip()


def discover_migration_files(directory: Optional[Path] = None) -> list[Path]:
    """適用順に並んだ migration ファイルの一覧を返す。

    順序: ``init.sql`` を先頭に、続いて ``NNN_*.sql``
    （3桁ゼロ埋め数字プレフィックス）を数値昇順で並べる。

    以下の場合は ``ValueError``（黙ってスキップせず fail loud にする）:
    - ``directory`` が存在しない
    - ``init.sql`` が存在しない
    - ``.sql`` 拡張子だが ``init.sql`` でも ``NNN_*.sql`` でもないファイルがある
    - 同じ番号のファイルが複数存在する（例: ``044_a.sql`` と ``044_b.sql``）
    """
    directory = directory if directory is not None else MIGRATIONS_DIR

    if not directory.is_dir():
        raise ValueError(f"migrations directory not found: {directory}")

    init_path = directory / _INIT_FILENAME
    if not init_path.is_file():
        raise ValueError(f"{_INIT_FILENAME} not found in {directory}")

    numbered: dict[int, Path] = {}
    for path in sorted(directory.glob("*.sql")):
        if path.name == _INIT_FILENAME:
            continue
        match = _NUMBERED_RE.match(path.name)
        if not match:
            raise ValueError(
                "migration file does not follow naming convention "
                f"(expected NNN_*.sql or {_INIT_FILENAME}): {path.name}"
            )
        number = int(match.group(1))
        if number in numbered:
            raise ValueError(
                f"duplicate migration number {number:03d}: "
                f"{numbered[number].name} and {path.name}"
            )
        numbered[number] = path

    return [init_path] + [numbered[n] for n in sorted(numbered)]


def run_migrations(engine=None, directory: Optional[Path] = None) -> list[str]:
    """``directory``（既定 ``MIGRATIONS_DIR``）配下の migration ファイルを順番に適用する。

    - トラッキングテーブルは持たない。呼ばれるたびに全ファイルを再実行する
      （各ファイルが冪等であることが前提）。
    - 1つの Connection を通しで使い、advisory lock
      （:data:`MIGRATION_LOCK_KEY`）を握ってから実行する。複数プロセス/レプリカが
      同時に起動しても DDL が競合しないためのガード。
    - ファイルごとに ``exec_driver_sql`` で SQL 全体を1回で流し、成功したら
      ``commit()``（ファイル単位トランザクション）。``sqlalchemy.text()`` は使わない
      （bind パラメータ扱いされる ``:xxx`` 記法が SQL 本文に含まれていても
      誤解釈されないようにするため）。
    - 失敗したファイルは ``rollback()`` した上で、どのファイルで失敗したかを
      メッセージに含めた例外を送出する（呼び出し元の起動リトライ→最終的な
      起動中断という現行セマンティクスを壊さない）。

    ``engine`` を省略した場合は ``core.postgres.get_engine()``
    （プロセス共有のシングルトン Engine）を使う。テストでは
    ``.connect()`` が ``exec_driver_sql`` / ``commit`` / ``rollback`` / ``close`` を
    持つ duck-typed connection を返す fake engine を渡せる。

    戻り値は実際に適用したファイル名のリスト（`init.sql` を含む）。
    """
    if engine is None:
        from core.postgres import get_engine

        engine = get_engine()

    files = discover_migration_files(directory)
    applied: list[str] = []

    conn = engine.connect()
    try:
        conn.exec_driver_sql(f"SELECT pg_advisory_lock({MIGRATION_LOCK_KEY})")
        conn.commit()

        for path in files:
            sql_text = path.read_text(encoding="utf-8")
            if _is_effectively_blank(sql_text):
                logger.info("Skipping empty (or comment-only) migration file: %s", path.name)
                continue

            try:
                conn.exec_driver_sql(sql_text)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                raise RuntimeError(
                    f"migration failed while applying {path.name}: {exc}"
                ) from exc

            applied.append(path.name)
            logger.info(
                "Applied migration: %s (%d/%d)", path.name, len(applied), len(files)
            )
    finally:
        try:
            conn.exec_driver_sql(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_KEY})")
            conn.commit()
        except Exception:  # noqa: BLE001
            logger.warning("failed to release migration advisory lock", exc_info=True)
        conn.close()

    return applied
