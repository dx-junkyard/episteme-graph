"""知識オブジェクト層の不変条項ガードレール（knowledge_objects_design.md §2 / §10）。

読み手側（Agent C 担当）の固定項目:

- KO5: 読み手は live ビュー（``theory_claims_live`` / ``theory_components_live``）を読む。
  基表を直接 SELECT / JOIN してよいのは書き手と、明示的に許した by-id 読み手だけ。
- KO1: A 層（``src/episteme_graph/``）に本層の語彙を持ち込まない。
- KO10: 学習者向け DTO に ``stable_key`` / ``produced_by_run_id`` / ``superseded_at`` を出さない。
- ``core/knowledge_objects/`` は FastAPI / sqlalchemy.orm / core.llm を import しない。
- live ビュー名の正本は ``core.knowledge_objects.schema.VIEW_*`` で、その値が migration に現れる。

書き手側（stable_key の決定性・同期・再係留・語彙シード）は
``test_knowledge_objects_{stable_key,sync,remap,vocab}.py`` が受け持つ。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.guardrail_helpers import (
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
)

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
CORE_DIR = BACKEND / "core"
API_DIR = BACKEND / "api"
KO_DIR = CORE_DIR / "knowledge_objects"
SRC_DIR = ROOT / "src" / "episteme_graph"
DB_DIR = BACKEND / "db"


# ===========================================================================
# KO5: 読み手は live ビューを読む
# ===========================================================================

#: 基表（``theory_claims`` / ``theory_components``）を直接 SELECT / JOIN してよい
#: ファイルと、その理由。ここに無いファイルが基表を読んだらガードレールが落ちる。
#:
#: **追加するときは理由を書くこと。** 監査・履歴目的で superseded 行を見せる読み手を
#: 足す場合は、返す DTO に ``superseded_at`` を含めること（設計書 §9。学習者向け DTO は
#: KO10 でそもそも出せないので、教員向けに限る）。
BASE_TABLE_ALLOWLIST: dict[str, str] = {
    "backend/core/document_pipeline/persistence.py": (
        "書き手。知識行の同期（stable_key 一致で UPDATE / 不一致で supersede・INSERT）と"
        "旧経路の DELETE を発行する本体（KO3 / KO5）。"
    ),
    "backend/core/versioning/deletion.py": (
        "削除経路。物理削除の対象 id を集めるため superseded 行も含めて走査する必要がある"
        "（live だけ消すと superseded 行が孤児として残る）。"
    ),
}


def _base_table_hits(path: Path) -> list[str]:
    """``FROM|JOIN theory_claims|theory_components``（``_live`` 付きを除く）の行を返す。"""
    pattern = re.compile(r"\b(?:FROM|JOIN)\s+theory_(?:claims|components)\b(?!_live)")
    hits: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if pattern.search(line):
            hits.append(f"{lineno}: {line.strip()}")
    return hits


def _scanned_sources() -> list[Path]:
    paths = sorted(CORE_DIR.rglob("*.py")) + sorted(API_DIR.rglob("*.py"))
    # knowledge_objects/ 自身は表名の正本なので走査対象から外す（定数定義が引っかかる）。
    return [p for p in paths if KO_DIR not in p.parents and p.parent != KO_DIR]


class TestReadersUseLiveViews:
    def test_only_allowlisted_files_read_the_base_tables(self):
        """KO5: 基表 SELECT / JOIN は allowlist のファイルだけ。"""
        offending: dict[str, list[str]] = {}
        for path in _scanned_sources():
            rel = path.relative_to(ROOT).as_posix()
            if rel in BASE_TABLE_ALLOWLIST:
                continue
            hits = _base_table_hits(path)
            if hits:
                offending[rel] = hits
        assert offending == {}, (
            "読み手は live ビューを読むこと（KO5）。基表を読む必要があるなら "
            "BASE_TABLE_ALLOWLIST に理由付きで登録する: "
            f"{ {k: v[:2] for k, v in offending.items()} }"
        )

    def test_allowlist_entries_exist_and_have_reasons(self):
        """allowlist は実在ファイル + 非空の理由でなければならない（形骸化の防止）。"""
        for rel, reason in BASE_TABLE_ALLOWLIST.items():
            assert (ROOT / rel).exists(), f"allowlist に存在しないファイル: {rel}"
            assert reason.strip(), f"allowlist の理由が空: {rel}"

    def test_allowlist_entries_actually_read_the_base_tables(self):
        """理由が残っているのに基表を読まなくなったら allowlist から外す。"""
        stale = [
            rel for rel in BASE_TABLE_ALLOWLIST if not _base_table_hits(ROOT / rel)
        ]
        assert stale == [], f"基表を読まなくなった allowlist エントリ: {stale}"

    def test_live_views_are_actually_referenced(self):
        """置換が実際に行われていること（0 件なら regex か作業の取りこぼし）。"""
        count = sum(
            len(re.findall(r"\b(?:FROM|JOIN)\s+theory_(?:claims|components)_live\b",
                           p.read_text(encoding="utf-8")))
            for p in _scanned_sources()
        )
        assert count >= 90, f"live ビュー参照が少なすぎる: {count}"


# ===========================================================================
# 表名・ビュー名の正本（core.knowledge_objects.schema）
# ===========================================================================


class TestViewNameIsCanonical:
    def test_schema_declares_the_live_view_names(self):
        from core.knowledge_objects import schema as ko_schema

        assert ko_schema.VIEW_CLAIMS_LIVE == "theory_claims_live"
        assert ko_schema.VIEW_COMPONENTS_LIVE == "theory_components_live"

    def test_migration_creates_the_declared_views(self):
        """ビュー名の正本は schema.VIEW_* で、その値が migration の SQL に現れる。"""
        from core.knowledge_objects import schema as ko_schema

        sql_files = sorted(DB_DIR.glob("*.sql"))
        joined = "\n".join(p.read_text(encoding="utf-8") for p in sql_files)
        for view in (ko_schema.VIEW_CLAIMS_LIVE, ko_schema.VIEW_COMPONENTS_LIVE):
            assert f"CREATE OR REPLACE VIEW {view}" in joined, (
                f"{view} を作る migration が無い（読み手の切替が空振りする）"
            )

    def test_live_views_filter_on_superseded_at(self):
        """live の定義は「superseded_at IS NULL」でなければならない（KO3）。"""
        from core.knowledge_objects import schema as ko_schema

        joined = "\n".join(
            p.read_text(encoding="utf-8") for p in sorted(DB_DIR.glob("*.sql"))
        )
        for view, table in (
            (ko_schema.VIEW_CLAIMS_LIVE, ko_schema.TABLE_CLAIMS),
            (ko_schema.VIEW_COMPONENTS_LIVE, ko_schema.TABLE_COMPONENTS),
        ):
            match = re.search(
                rf"CREATE OR REPLACE VIEW {view} AS\s+SELECT \* FROM {table}\s+"
                r"WHERE superseded_at IS NULL",
                joined,
            )
            assert match, f"{view} の定義が想定形（SELECT * ... WHERE superseded_at IS NULL）でない"


# ===========================================================================
# KO1: A層に本層の語彙を持ち込まない
# ===========================================================================


class TestAgentLayerIsUntouched:
    #: 知識オブジェクト層でだけ使う語彙（agent 側の出力スキーマ・ID 採番は非改変）。
    _KO_TERMS = (
        "stable_key",
        "superseded_at",
        "superseded_by_run_id",
        "produced_by_run_id",
        "knowledge_equations",
        "knowledge_evidence",
        "knowledge_derivation_steps",
        "knowledge_symbols",
        "element_id_remap",
        "theory_claims_live",
        "theory_components_live",
        # Phase 2（学ぶ単位）の表・語彙表・親参照列も A層には持ち込まない。
        "learning_units",
        "knowledge_unit_kinds",
        "parent_agent_component_id",
    )

    def test_src_does_not_learn_the_knowledge_object_vocabulary(self):
        """KO1: 永続化層の語彙が A層（src/episteme_graph/）に漏れていないこと。"""
        assert_module_tree_forbids(SRC_DIR, self._KO_TERMS)


# ===========================================================================
# KO10: 学習者向け DTO に内部キーを出さない
# ===========================================================================


class TestLearnerDtoDoesNotLeakInternalKeys:
    #: 学習者向け DTO を組み立てるモジュール（正本は learner_context_common.py）。
    _LEARNER_DTO_SOURCES = (
        "backend/core/learner_context_common.py",
        "backend/core/element_context.py",
        "backend/core/component_context.py",
        "backend/api/routes/learning.py",
    )

    #: DTO キーにしてはならない内部列（KO10）。ソース文字列としての出現を丸ごと
    #: 禁止する単純検査 — これらの列は学習者向け経路では SELECT する理由すら無いので、
    #: 「キーにしていないが読んではいる」状態も許さない方が固定として強い。
    _FORBIDDEN = ("stable_key", "produced_by_run_id", "superseded_at", "superseded_by_run_id")

    @pytest.mark.parametrize("rel", _LEARNER_DTO_SOURCES)
    def test_no_internal_key_in_learner_facing_source(self, rel: str):
        path = ROOT / rel
        assert path.exists(), rel
        src = path.read_text(encoding="utf-8")
        offending = [term for term in self._FORBIDDEN if term in src]
        assert offending == [], (
            f"{rel}: 学習者向け DTO に内部列を出さない（KO10）: {offending}"
        )


# ===========================================================================
# core/knowledge_objects の純粋性
# ===========================================================================


class TestCoreModulePurity:
    def test_does_not_import_fastapi_orm_or_llm(self):
        assert KO_DIR.is_dir(), "core/knowledge_objects/ が無い"
        assert_module_tree_does_not_import(
            KO_DIR, ("fastapi", "sqlalchemy.orm", "core.llm")
        )
