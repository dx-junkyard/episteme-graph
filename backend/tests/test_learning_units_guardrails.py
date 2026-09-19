"""学ぶ単位の不変条項ガードレール（learning_units_design.md §2 / §10・P2-1 / P2-2）。

構造的に守るもの:

- LU3: 導出は決定論・非LLM（``core/knowledge_objects/learning_units.py`` が
  FastAPI / sqlalchemy / ``core.llm`` を import しない）
- LU4 / KO5: 読み手は ``learning_units_live`` を読む。基表を SQL で触ってよいのは
  ``persistence.py`` / ``versioning/deletion.py`` だけ
- 語彙の正本一致: migration 081 の ``knowledge_unit_kinds`` シード ==
  ``core/schema.py::LEARNING_UNIT_KINDS``、label == ``label_vocab.LEARNING_UNIT_KIND_LABELS``
- live ビューの再作成規律（078 §8）を 081 が守る
- LU2 / P4: migration に ``DELETE FROM`` が無い・確定列が既定 ``candidate``
- LU5: 導出モジュールが confidence を単位に載せない
- LU6: 学習者の痕跡・習熟推定の語彙が導出モジュールに入っていない
- PL7 と同じ規律: 内部 ID を label にしない

DB にも LLM にも接続しない（すべてソース・SQL の静的検査）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from tests.guardrail_helpers import (
    assert_module_tree_does_not_import,
    assert_source_does_not_import,
    assert_source_forbids,
    read_migration_sql,
)

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CORE_DIR = BACKEND / "core"
API_DIR = BACKEND / "api"
KO_DIR = CORE_DIR / "knowledge_objects"
UNITS_MODULE = KO_DIR / "learning_units.py"

MIGRATION_NUMBER = 81


@pytest.fixture(scope="module")
def migration_081() -> str:
    return read_migration_sql(BACKEND, MIGRATION_NUMBER)


# ===========================================================================
# LU3: 決定論・非LLM
# ===========================================================================


class TestDerivationIsPure:
    def test_module_exists(self):
        assert UNITS_MODULE.is_file(), "core/knowledge_objects/learning_units.py が無い"

    def test_does_not_import_framework_db_or_llm(self):
        """導出モジュールは純関数。sqlalchemy も掴まない。

        ``knowledge_objects/`` の他ファイル（sync / remap / backfill）は SQL ヘルパ
        なので ``sqlalchemy.text`` を使う（Phase 1 が禁じているのは
        ``sqlalchemy.orm`` / ``core.llm`` / FastAPI）。ここで強い禁止を掛けるのは
        導出モジュール 1 本に閉じる。
        """
        assert_source_does_not_import(
            UNITS_MODULE.read_text(encoding="utf-8"),
            ["fastapi", "sqlalchemy", "core.llm", "core.postgres", "openai"],
            context="core/knowledge_objects/learning_units.py",
        )

    def test_package_keeps_the_phase1_purity(self):
        assert_module_tree_does_not_import(
            KO_DIR, ("fastapi", "sqlalchemy.orm", "core.llm")
        )

    def test_does_not_open_a_session(self):
        src = UNITS_MODULE.read_text(encoding="utf-8")
        assert "get_session" not in src
        assert "execute(" not in src

    def test_no_llm_vocabulary(self):
        assert_source_forbids(
            UNITS_MODULE.read_text(encoding="utf-8"),
            ("generate_text", "generate_structured", "prompt", "usage_context"),
            context="core/knowledge_objects/learning_units.py",
        )


# ===========================================================================
# LU4 / KO5: 読み手は live ビューを読む
# ===========================================================================

#: 基表 ``learning_units`` を SQL で触ってよいファイルと、その理由。
#:
#: **逆向きの「使われなくなった entry」検査は置かない** — 書き手は表名を
#: ``TABLE_LEARNING_UNITS`` 定数や表名タプル経由で渡すので、リテラルの有無で
#: allowlist の鮮度を測ると壊れやすい。ここで固定したいのは「他の誰も基表を
#: SQL で触らない」という一方向だけ。
BASE_TABLE_ALLOWLIST: dict[str, str] = {
    "backend/core/document_pipeline/persistence.py": (
        "書き手。learning_units の同期（stable_key 一致で UPDATE / 不一致で "
        "supersede・INSERT）を発行する本体（LU4）。"
    ),
    "backend/core/versioning/deletion.py": (
        "削除経路。document の物理削除で明示 DELETE を並べる（FK CASCADE でも消えるが、"
        "何が一緒に消えるのかをコードで読めるようにするため）。"
    ),
}

_BASE_TABLE_SQL_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+learning_units\b(?!_live)", re.IGNORECASE
)


def _scanned_sources() -> list[Path]:
    paths = sorted(CORE_DIR.rglob("*.py")) + sorted(API_DIR.rglob("*.py"))
    # knowledge_objects/ 自身は表名の正本なので走査対象から外す。
    return [p for p in paths if KO_DIR not in p.parents and p.parent != KO_DIR]


class TestReadersUseTheLiveView:
    def test_only_allowlisted_files_touch_the_base_table(self):
        offending: dict[str, list[str]] = {}
        for path in _scanned_sources():
            rel = path.relative_to(ROOT).as_posix()
            if rel in BASE_TABLE_ALLOWLIST:
                continue
            hits = [
                f"{lineno}: {line.strip()}"
                for lineno, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), start=1
                )
                if _BASE_TABLE_SQL_RE.search(line)
            ]
            if hits:
                offending[rel] = hits
        assert offending == {}, (
            "読み手は learning_units_live を読むこと（KO5）。基表を触る必要があるなら "
            f"BASE_TABLE_ALLOWLIST に理由付きで登録する: {offending}"
        )

    def test_allowlist_entries_exist_and_have_reasons(self):
        for rel, reason in BASE_TABLE_ALLOWLIST.items():
            assert (ROOT / rel).exists(), f"allowlist に存在しないファイル: {rel}"
            assert reason.strip(), f"allowlist の理由が空: {rel}"

    def test_writer_addresses_the_table_through_the_constant(self):
        src = (CORE_DIR / "document_pipeline" / "persistence.py").read_text(encoding="utf-8")
        assert "TABLE_LEARNING_UNITS" in src, (
            "persistence は表名を core/knowledge_objects/schema.py の定数で渡すこと"
        )

    def test_view_name_is_declared_and_created(self, migration_081: str):
        from core.knowledge_objects import schema as ko_schema

        assert ko_schema.VIEW_LEARNING_UNITS_LIVE == "learning_units_live"
        assert re.search(
            rf"CREATE OR REPLACE VIEW {ko_schema.VIEW_LEARNING_UNITS_LIVE} AS\s+"
            rf"SELECT \* FROM {ko_schema.TABLE_LEARNING_UNITS}\s+WHERE superseded_at IS NULL",
            migration_081,
        ), "learning_units_live の定義が想定形でない"


# ===========================================================================
# 語彙の正本一致（KO7 と同じ作法）
# ===========================================================================


def _seeded_unit_kinds(sql: str) -> list[tuple[str, str]]:
    match = re.search(
        r"INSERT\s+INTO\s+knowledge_unit_kinds\s*\(kind,\s*label\)\s*VALUES(.*?)ON\s+CONFLICT",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, "knowledge_unit_kinds のシード INSERT が 081 に無い"
    return re.findall(r"\(\s*'([^']+)'\s*,\s*'([^']*)'\s*\)", match.group(1))


class TestVocabularyMatchesTheCanon:
    def test_seed_matches_learning_unit_kinds(self, migration_081: str):
        from core.schema import LEARNING_UNIT_KINDS

        seeded = [kind for kind, _label in _seeded_unit_kinds(migration_081)]
        assert set(seeded) == set(LEARNING_UNIT_KINDS), (
            "knowledge_unit_kinds のシードが core.schema.LEARNING_UNIT_KINDS と食い違っています。\n"
            f"  SQL のみ: {sorted(set(seeded) - set(LEARNING_UNIT_KINDS))}\n"
            f"  コードのみ: {sorted(set(LEARNING_UNIT_KINDS) - set(seeded))}"
        )
        assert len(seeded) == len(set(seeded)), "シードに重複があります"

    def test_seed_labels_match_label_vocab(self, migration_081: str):
        from core.label_vocab import LEARNING_UNIT_KIND_LABELS

        assert dict(_seeded_unit_kinds(migration_081)) == dict(LEARNING_UNIT_KIND_LABELS)

    def test_course_kinds_are_a_subset(self):
        from core.schema import LEARNING_UNIT_KINDS, LEARNING_UNIT_KINDS_FOR_COURSE

        assert set(LEARNING_UNIT_KINDS_FOR_COURSE) <= set(LEARNING_UNIT_KINDS)

    def test_default_review_status_is_a_declared_status(self):
        from core.knowledge_objects.schema import DEFAULT_UNIT_REVIEW_STATUS
        from core.schema import LEARNING_UNIT_REVIEW_STATUSES

        assert DEFAULT_UNIT_REVIEW_STATUS == "candidate"
        assert DEFAULT_UNIT_REVIEW_STATUS in LEARNING_UNIT_REVIEW_STATUSES

    def test_derivation_module_covers_every_kind(self):
        from core.knowledge_objects import learning_units as units
        from core.schema import LEARNING_UNIT_KINDS

        declared = {
            units.KIND_SECTION_BLOCK, units.KIND_THESIS_SUPPORT,
            units.KIND_PARENT_COMPONENT, units.KIND_DSL_NODE, units.KIND_FIGURE,
        }
        assert declared == set(LEARNING_UNIT_KINDS)


# ===========================================================================
# migration の規約
# ===========================================================================


class TestMigrationDiscipline:
    def test_no_delete_statements(self, migration_081: str):
        body = re.sub(r"--[^\n]*", "", migration_081)
        assert not re.search(r"\bDELETE\s+FROM\b", body, re.IGNORECASE), (
            "081 に DELETE FROM があります（LU4 / KO3: 再解析は supersede で表す）"
        )

    def test_document_id_is_uuid_with_cascade(self, migration_081: str):
        assert "document_id          UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE" in (
            migration_081
        )

    def test_unit_kind_references_the_vocabulary_table(self, migration_081: str):
        assert re.search(
            r"unit_kind\s+TEXT NOT NULL REFERENCES knowledge_unit_kinds\(kind\)",
            migration_081,
        )

    def test_live_uniqueness_is_partial(self, migration_081: str):
        assert re.search(
            r"CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_units_stable_key_live\s+"
            r"ON learning_units \(document_id, stable_key\)\s+"
            r"WHERE superseded_at IS NULL AND stable_key IS NOT NULL",
            migration_081,
        )

    def test_review_status_defaults_to_candidate(self, migration_081: str):
        assert re.search(r"review_status\s+TEXT NOT NULL DEFAULT 'candidate'", migration_081)

    def test_parent_columns_are_added_without_a_foreign_key(self, migration_081: str):
        assert "ADD COLUMN IF NOT EXISTS parent_component_id UUID;" in migration_081
        assert "ADD COLUMN IF NOT EXISTS parent_agent_component_id TEXT;" in migration_081
        # 親が superseded になっても子の参照を壊さないため FK は張らない（§5.1）。
        assert "parent_component_id UUID REFERENCES" not in migration_081

    def test_live_views_are_recreated(self, migration_081: str):
        """078 §8 の規律: theory_components に列を足したら live ビューを作り直す。"""
        for table in ("theory_claims", "theory_components"):
            assert re.search(
                rf"CREATE OR REPLACE VIEW {table}_live AS\s+SELECT \* FROM {table}\s+"
                r"WHERE superseded_at IS NULL",
                migration_081,
            ), f"{table}_live の再作成が 081 に無い"

    def test_no_percent_sign_anywhere(self, migration_081: str):
        """ランナーは空パラメータで exec_driver_sql するので ``%%`` 以外の ``%`` を書かない。"""
        assert not re.search(r"(?<!%)%(?!%)", migration_081)


# ===========================================================================
# LU5 / LU6: 数値を載せない・適応評価をしない
# ===========================================================================


class TestNoNumbersAndNoAdaptiveEvaluation:
    _FORBIDDEN_ADAPTIVE = (
        "mastery", "習熟", "proficiency", "learner_model", "interest_traces",
        "learning_states", "skill_level",
    )

    def test_derivation_has_no_adaptive_evaluation_vocabulary(self):
        assert_source_forbids(
            UNITS_MODULE.read_text(encoding="utf-8"),
            self._FORBIDDEN_ADAPTIVE,
            context="core/knowledge_objects/learning_units.py",
        )

    def test_units_do_not_carry_confidence(self):
        import types as _types

        from core.knowledge_objects import learning_units as units

        skeleton = _types.SimpleNamespace(logical_blocks=[
            _types.SimpleNamespace(
                block_id="lb_1", block_type="assumptions", label="L",
                section_ids=[], evidence_block_ids=["b1"], summary="s",
                reason="r", confidence=0.91,
            ),
        ])
        items = units.build_learning_unit_items("doc-1", skeleton=skeleton)
        assert items
        for item in items:
            assert "confidence" not in str(item["values"]), item["values"]

    def test_label_is_never_an_internal_id(self):
        import types as _types

        from core.knowledge_objects import learning_units as units

        figures = _types.SimpleNamespace(figures=[
            _types.SimpleNamespace(
                figure_id="fig_3.3", document_id="doc-1", figure_type="",
                source_location=_types.SimpleNamespace(
                    page=1, caption_block_id="", section_id=None),
                caption="", linked_claim_ids=[],
            ),
        ])
        items = units.build_learning_unit_items("doc-1", figures=figures)
        assert items
        # caption も figure_type も無いときは空ラベルで正直に出す（内部 ID で埋めない）。
        assert items[0]["values"]["label"] == ""


# ===========================================================================
# KO10: 学習者向け DTO に内部キーを出さない（Phase 1 と同じ）
# ===========================================================================


class TestLearnerDtoDoesNotLeakUnitInternals:
    _LEARNER_DTO_SOURCES = (
        "backend/core/learner_context_common.py",
        "backend/core/element_context.py",
        "backend/core/component_context.py",
    )

    @pytest.mark.parametrize("rel", _LEARNER_DTO_SOURCES)
    def test_no_unit_internal_key(self, rel: str):
        path = ROOT / rel
        assert path.exists(), rel
        src = path.read_text(encoding="utf-8")
        for term in ("agent_unit_id", "produced_by_run_id", "superseded_at"):
            assert term not in src, f"{rel}: 学習者向け DTO に {term} を出さない（KO10）"
