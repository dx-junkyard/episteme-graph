"""``document_id`` の UUID 統一と削除経路の一本化（knowledge_objects_design.md §4.3 / §8）。

不変条項 KO9 —「``document_id`` は UUID + ``REFERENCES documents(id) ON DELETE CASCADE``。
教材の物理削除経路は ``_purge_document`` 1本（``delete_material`` は委譲）」— を構造的に固定する。

検査するもの:

(a) migration 080 が対象14列すべてに型変更と FK を持つ（SQL 文字列検査）。
(b) live ビュー（078）を落としてから型変更し、同一定義で作り直している（順序検査）。
(c) ``backend/core`` / ``backend/api`` に TEXT 時代の突合の残骸が無い
    （``document_id IN (:a, :b)`` の両形バインド / ``CAST(... AS uuid)::text`` の
    document_id 比較 / ``d.id::text = t.document_id`` 型の join / ``document_id = ''``）。
    **allowlist は持たない** — 例外を作らずに全部直す。
(d) ``delete_material`` が ``_purge_document`` に委譲し、自前の DB 削除本体を持たない。

DB にも LLM にも接続しない（すべてソース・SQL の静的検査）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.guardrail_helpers import extract_function_source, read_migration_sql

BACKEND = Path(__file__).resolve().parents[1]

#: 080 が UUID 化する (テーブル, 列)。設計書 §4.3 の一覧と同じ並び。
TARGET_COLUMNS: tuple[tuple[str, str], ...] = (
    ("theory_claims", "document_id"),
    ("theory_components", "document_id"),
    ("theory_component_links", "document_id"),
    ("theory_component_graphs", "document_id"),
    ("document_analysis_runs", "document_id"),
    ("document_embeddings", "document_id"),
    ("document_figures", "document_id"),
    ("epistemic_ledger", "document_id"),
    ("counterfactual_sessions", "document_id"),
    ("reconstruction_items", "document_id"),
    ("section_assembly_status", "document_id"),
    ("deliberation_sessions", "document_id"),
    ("element_annotations", "document_id"),
    ("element_identity_links", "instance_document_id"),
)

#: live ビューを持つ2表（078 §8）。
LIVE_TABLES = ("theory_claims", "theory_components")


@pytest.fixture(scope="module")
def migration_080() -> str:
    return read_migration_sql(BACKEND, 80)


# ---------------------------------------------------------------------------
# (a) 対象表すべてに型変更と FK
# ---------------------------------------------------------------------------


class TestMigrationCoversEveryTargetColumn:
    def test_every_target_pair_is_listed_in_both_do_blocks(self, migration_080: str):
        """型変更ブロックと FK ブロックの両方が14組すべてを列挙している。

        080 は ``quote_ident()`` + ``||`` で動的 SQL を組む（``format('%I', ...)`` を使うと
        psycopg2 が ``%`` を補間対象とみなして落ちるため — 078 / 079 の ``%%`` と同じ理由）。
        したがって「対象表の一覧」は VALUES の並びが正本で、ここではその並びを固定する。
        """
        blocks = migration_080.split("DO $$")
        assert len(blocks) == 3, "080 は型変更ブロックと FK ブロックの2つの DO $$ を持つ"
        for index, block in enumerate(blocks[1:], start=1):
            for table, column in TARGET_COLUMNS:
                pair = f"('{table}',"
                assert pair in block, f"DO ブロック{index} に {table} が無い"
                assert f"'{column}')" in block, f"DO ブロック{index} に {column} が無い"

    def test_type_change_statement_is_present(self, migration_080: str):
        """``ALTER COLUMN <col> TYPE uuid USING NULLIF(<col>, '')::uuid`` を組み立てている。"""
        assert "' ALTER COLUMN ' || q_col || ' TYPE uuid'" in migration_080
        assert "USING NULLIF(" in migration_080
        assert "')::uuid'" in migration_080
        # DEFAULT '' は uuid にキャストできないので型変更より前に落とす。
        default_at = migration_080.index("' DROP DEFAULT'")
        type_at = migration_080.index("' TYPE uuid'")
        assert default_at < type_at, "DROP DEFAULT は ALTER COLUMN ... TYPE より前"

    def test_foreign_key_statement_is_present(self, migration_080: str):
        assert "REFERENCES documents(id) ON DELETE CASCADE" in migration_080
        assert "'_document_fk'" in migration_080
        # 既存制約があれば張り直さない（毎起動再実行で落ちない）。
        assert "FROM pg_constraint WHERE conname = fk" in migration_080

    def test_orphan_cleanup_is_guarded_and_announced(self, migration_080: str):
        """唯一の破壊的ステップ（§8.3）は理由がファイル冒頭にあり、件数を NOTICE する。"""
        assert "唯一の破壊的ステップ" in migration_080
        assert "RAISE NOTICE" in migration_080
        assert "orphan row(s)" in migration_080
        # 空文字の行は「未紐づけ」であって孤児ではない（消さずに NULL へ倒す）。
        assert "NOT EXISTS (" in migration_080.replace("\n", " ").replace("  ", " ")

    def test_material_id_form_is_normalized_not_deleted(self, migration_080: str):
        assert "= d.source_path" in migration_080

    def test_migration_writes_no_percent_outside_raise_notice(self, migration_080: str):
        """``%`` は psycopg2 が補間しようとするため、RAISE NOTICE の ``%%`` 以外に書かない。"""
        stripped = re.sub(r"--[^\n]*", "", migration_080)
        singles = [
            m.start()
            for m in re.finditer(r"(?<!%)%(?!%)", stripped)
        ]
        assert singles == [], f"エスケープされていない % がある: {singles}"


# ---------------------------------------------------------------------------
# (b) live ビューの DROP → ALTER → 再作成
# ---------------------------------------------------------------------------


class TestLiveViewSwapOrder:
    def test_views_are_dropped_before_the_type_change_and_recreated_after(
        self, migration_080: str
    ):
        """``ALTER COLUMN ... TYPE`` は依存ビューがあると失敗するため、
        DROP VIEW → ALTER → CREATE OR REPLACE VIEW の順であること。"""
        for table in LIVE_TABLES:
            drop_at = migration_080.index(f"DROP VIEW IF EXISTS {table}_live")
            create_at = migration_080.index(f"CREATE OR REPLACE VIEW {table}_live")
            alter_at = migration_080.index("' TYPE uuid'")
            assert drop_at < alter_at < create_at, f"{table}_live の作り直し順が違う"

    def test_recreated_views_match_078(self, migration_080: str):
        """078 と同一定義（``superseded_at IS NULL``）で作り直す（KO5）。"""
        sql_078 = read_migration_sql(BACKEND, 78)
        for table in LIVE_TABLES:
            definition = (
                f"CREATE OR REPLACE VIEW {table}_live AS\n"
                f"            SELECT * FROM {table} WHERE superseded_at IS NULL;"
            )
            assert definition in migration_080, f"{table}_live の定義が 078 と違う"
            assert f"SELECT * FROM {table} WHERE superseded_at IS NULL" in sql_078

    def test_view_swap_is_guarded_so_the_second_boot_does_nothing(self, migration_080: str):
        assert "swap_views" in migration_080
        assert "data_type = 'text'" in migration_080


# ---------------------------------------------------------------------------
# (c) TEXT 時代の突合の残骸が無い（allowlist なし）
# ---------------------------------------------------------------------------


#: 禁止パターン（正規表現, 説明）。
FORBIDDEN_SQL_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"document_id\s+IN\s*\(\s*:",
        "document_id IN (:a, :b) の両形バインド（080 以降 document_id は uuid 単形）",
    ),
    (
        r"document_id\s*=\s*CAST\(\s*:\w+\s+AS\s+uuid\s*\)\s*::\s*text",
        "document_id を CAST(... AS uuid)::text と比較している（uuid 同士で比べる）",
    ),
    (
        r"document_id\s+IN\s*\(\s*CAST\(\s*:\w+\s+AS\s+uuid\s*\)\s*::\s*text",
        "document_id を CAST(... AS uuid)::text の IN 句で比較している",
    ),
    (
        r"(?<![\w.])[\w.]*\bid::text\s*=\s*[\w.]*document_id",
        "documents.id を文字列化して document_id と join している（uuid 同士で join する）",
    ),
    (
        r"document_id\s*=\s*[\w.]*\bid::text",
        "document_id を documents.id::text と比較している（uuid 同士で比べる）",
    ),
    (
        r"document_id\s*(?:=|<>|!=)\s*''",
        "document_id を空文字と比較している（080 以降は NULL）",
    ),
)


def _python_sources() -> list[Path]:
    paths: list[Path] = []
    for base in (BACKEND / "core", BACKEND / "api"):
        paths.extend(sorted(base.rglob("*.py")))
    return paths


class TestNoLegacyTextComparisons:
    @pytest.mark.parametrize("pattern,description", FORBIDDEN_SQL_PATTERNS)
    def test_pattern_is_absent(self, pattern: str, description: str):
        regex = re.compile(pattern)
        offenders: list[str] = []
        for path in _python_sources():
            text = path.read_text(encoding="utf-8")
            for match in regex.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(BACKEND)}:{line}: {match.group(0)!r}")
        assert offenders == [], f"{description}\n" + "\n".join(offenders)

    def test_in_clause_placeholders_are_cast_to_uuid(self):
        """``document_id IN ({placeholders})`` の placeholders は uuid へキャストして組む。

        f-string で組み立てる IN 句はパターン検査では見えないので、同じファイル内の
        placeholder 生成式が ``AS uuid`` を含むことを確認する。
        """
        offenders: list[str] = []
        seen = 0
        interp = re.compile(r"document_id\s+IN\s*\(\{(\w+)\}\)")
        for path in _python_sources():
            text = path.read_text(encoding="utf-8")
            for match in interp.finditer(text):
                seen += 1
                name = match.group(1)
                # IN 句より **前** にある最後の代入（同じ関数の中の組み立て）を見る。
                assigns = list(
                    re.finditer(rf"\b{name}\s*=\s*[^\n]*join\([^\n]*", text[: match.start()])
                )
                assign = assigns[-1] if assigns else None
                line = text.count("\n", 0, match.start()) + 1
                if not assign or "AS uuid" not in assign.group(0):
                    offenders.append(
                        f"{path.relative_to(BACKEND)}:{line}: "
                        f"{name} が CAST(:x AS uuid) で組まれていない"
                    )
        assert offenders == [], "\n".join(offenders)
        # 検査が空振り（IN 句が1件も見つからない）していないことも固定する。
        assert seen >= 5, f"document_id IN ({{placeholders}}) の検査対象が少なすぎる: {seen}"


# ---------------------------------------------------------------------------
# (d) delete_material → _purge_document の委譲
# ---------------------------------------------------------------------------


ADMIN_SRC = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
DELETION_SRC = (BACKEND / "core" / "versioning" / "deletion.py").read_text(encoding="utf-8")


class TestDeleteMaterialDelegates:
    def test_delete_material_calls_purge_document(self):
        body = extract_function_source(ADMIN_SRC, "delete_material")
        assert "_purge_document(" in body

    def test_delete_material_has_no_db_deletion_of_its_own(self):
        """HTTP 層に DB の削除本体を書き戻さない（削除範囲の正本を2つに割らない）。"""
        body = extract_function_source(ADMIN_SRC, "delete_material")
        assert "DELETE FROM" not in body, (
            "delete_material に自前の DELETE が戻っている "
            "（DB 削除本体は core/versioning/deletion.py::_purge_document が正本）"
        )

    def test_delete_material_keeps_the_http_layer_concerns(self):
        """委譲しても HTTP 層の責務（確認名照合・監査・V層 teardown）は残る。"""
        body = extract_function_source(ADMIN_SRC, "delete_material")
        assert "confirm_name" in body
        assert "AUDIT_ENTITY_MATERIAL" in body
        assert "_versioning_teardown_after_delete(" in body
        assert "_versioning_collect_recipients(" in body

    def test_purge_document_returns_course_ids_and_figure_keys(self):
        body = extract_function_source(DELETION_SRC, "_purge_document")
        assert "PurgedDocument(" in body
        assert "SELECT minio_key FROM document_figures" in body
        assert "class PurgedDocument" in DELETION_SRC

    def test_purge_document_deletes_documents_before_runs(self):
        """``documents.active_analysis_run_id``（NO ACTION）があるため、runs を先に
        消すと参照が切れる。documents を先に消す順序を固定する。"""
        body = extract_function_source(DELETION_SRC, "_purge_document")
        docs_at = body.index("DELETE FROM documents WHERE id =")
        runs_at = body.index("DELETE FROM document_analysis_runs")
        assert docs_at < runs_at

    def test_core_does_not_touch_storage(self):
        """MinIO の削除は呼び出し側の best-effort（core は storage を触らない）。"""
        body = extract_function_source(DELETION_SRC, "_purge_document")
        assert "remove_object" not in body
        assert "get_storage_client" not in body
