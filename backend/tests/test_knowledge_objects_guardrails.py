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

#: 基表（``theory_claims`` / ``theory_components``）に触ってよい **シンボル** と、その理由。
#: キーは ``"<リポジトリ相対パス>:<関数名>"``（モジュール直下は ``:<module>``）。
#:
#: 粒度がファイルではなくシンボルなのは、``persistence.py`` のような「書き手と読み手が
#: 同居するファイル」を丸ごと免除すると、そのファイルに後から足した**読み手**が
#: 素通りしてしまうため（2026-09-13 の敵対的レビュー P1-R11）。
#:
#: **追加するときは理由を書くこと。** 監査・履歴目的で superseded 行を見せる読み手を
#: 足す場合は、返す DTO に ``superseded_at`` を含めること（設計書 §9。学習者向け DTO は
#: KO10 でそもそも出せないので、教員向けに限る）。
BASE_TABLE_ALLOWLIST: dict[str, str] = {
    # ---- 削除経路 ---------------------------------------------------------
    "backend/core/versioning/deletion.py:_purge_document": (
        "削除経路。物理削除の対象 id を集めるため superseded 行も含めて走査する必要がある"
        "（live だけ消すと superseded 行が孤児として残る）。"
    ),
    # ---- 動的なテーブル名（静的に解決できない補間）------------------------
    # ここに載っているのは「テーブル名を f-string で受ける関数」で、**何が流れ込むかを
    # 呼び出し側が固定していること**が登録の条件。新しく足すときは、渡し元の定数を
    # 理由に明記すること（そうでないと基表が黙って流れ込む余地が残る）。
    "backend/core/deliberation/refs.py:_resolve_by_legacy_id": (
        "``_LEGACY_ID_TABLES`` の値だけを受ける（現在は live ビュー2つ）。element_type から"
        "引いた定数以外は流れ込まない。"
    ),
    "backend/core/descent/resolve.py:_resolve_row": (
        "呼び出し側（``resolve_element``）が live ビュー定数（``VIEW_COMPONENTS_LIVE`` / "
        "``VIEW_CLAIMS_LIVE``）のみを渡す。"
    ),
    "backend/core/admin_assistant/next_steps.py:_approved_refs": (
        "``table`` は live ビュー定数のみ（承認済みかの判定に基表を使わない）。"
    ),
    "backend/core/knowledge_import/apply.py:live_row_counts": (
        "``view`` は ``VIEW_CLAIMS_LIVE`` / ``VIEW_COMPONENTS_LIVE`` の2定数のみ。"
    ),
    "backend/core/knowledge_import/apply.py:select_sql": (
        "``self.source`` は種別宣言の表名リテラルのみ。claim / component は live ビュー、"
        "新4表は ``live_view=False`` のとき ``superseded_at IS NULL`` を自分で足す。"
    ),
    "backend/api/routes/export.py:_load_knowledge_object_keys": (
        "``_KNOWLEDGE_KEY_SOURCES`` の表名リテラルのみ（claim / component は live ビュー、"
        "新4表は ``superseded_at IS NULL`` を明示的に足す）。"
    ),
    "backend/core/account_lifecycle.py:_delete_sql": (
        "アカウント purge。``PURGE_TABLES`` の宣言に並ぶ表名のみを受ける（AL1 の網羅性検査が"
        "別テストで固定）。``theory_claims`` / ``theory_components`` は RETAIN 側なので"
        "ここには流れ込まない。"
    ),
    "backend/core/account_lifecycle.py:_leftover_counts": (
        "アカウント purge の残存確認。``PURGE_TABLES`` / ``RETAIN_TABLES`` の表名のみを受ける"
        "（行を消さない COUNT）。"
    ),
}

#: 走査で見張る SQL の書き方（読み手の経路 = ``FROM`` / ``JOIN``）。``DELETE FROM`` も
#: ``FROM`` として引っかかる（基表からの行削除は KO3 違反なので、むしろ見たい）。
#: ``UPDATE`` / ``INSERT INTO`` は書き手の正規の経路なので対象外
#: （KO5 が言うのは「**読み手**は live ビューを読む」）。
_SQL_VERBS = r"(?:FROM|JOIN)"

#: ①素の基表名（``FROM theory_claims`` — 改行をまたぐ整形も拾うため全文走査する）。
_STATIC_BASE_TABLE_RE = re.compile(
    rf"\b{_SQL_VERBS}\s+theory_(?:claims|components)\b(?!_live)", re.IGNORECASE
)

#: ②動的なテーブル名（``FROM {table}`` / ``UPDATE {TABLE_CLAIMS}``）。
#: f-string 補間はテーブル名を隠すので、regex だけの検査は素通りする（P1-R11）。
_DYNAMIC_TABLE_RE = re.compile(
    rf"\b{_SQL_VERBS}\s*\{{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}}", re.IGNORECASE
)

#: 基表そのものを指す値（``core.knowledge_objects.schema`` の定数はここで解決する）。
_BASE_TABLE_VALUES = {"theory_claims", "theory_components"}


def _resolve_table_expression(name: str) -> str | None:
    """``{name}`` が静的に解決できるなら実テーブル名を返す（できなければ ``None``）。"""
    from core.knowledge_objects import schema as ko_schema

    if "." in name:
        return None
    value = getattr(ko_schema, name, None)
    return value if isinstance(value, str) else None


def _symbol_ranges(source: str) -> list[tuple[int, int, str]]:
    """``(開始行, 終了行, 関数/クラス名)`` の一覧（内側ほどリストの後ろ）。"""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - 構文エラーは他のテストが拾う
        return []
    ranges: list[tuple[int, int, str]] = []

    def _walk(node) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                end = getattr(child, "end_lineno", None) or child.lineno
                ranges.append((child.lineno, end, child.name))
            _walk(child)

    _walk(tree)
    return ranges


def _symbol_for_line(ranges: list[tuple[int, int, str]], lineno: int) -> str:
    """その行を含む**最も内側の**シンボル名（無ければ ``<module>``）。"""
    best: tuple[int, str] | None = None
    for start, end, name in ranges:
        if start <= lineno <= end:
            span = end - start
            if best is None or span <= best[0]:
                best = (span, name)
    return best[1] if best else "<module>"


def _base_table_hits(path: Path) -> list[tuple[str, str]]:
    """``[(シンボル名, "行番号: 抜粋")]``。基表への静的/動的アクセスを全文走査で集める。"""
    source = path.read_text(encoding="utf-8")
    ranges = _symbol_ranges(source)
    lines = source.splitlines()
    hits: list[tuple[str, str]] = []

    def _record(match: re.Match, note: str) -> None:
        lineno = source.count("\n", 0, match.start()) + 1
        excerpt = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
        hits.append((_symbol_for_line(ranges, lineno), f"{lineno}: {note}{excerpt}"))

    for match in _STATIC_BASE_TABLE_RE.finditer(source):
        _record(match, "")
    for match in _DYNAMIC_TABLE_RE.finditer(source):
        resolved = _resolve_table_expression(match.group(1))
        if resolved is None:
            # 静的に解決できない補間 = 基表が流れ込み得る。明示 allowlist を要求する。
            _record(match, "[dynamic] ")
        elif resolved in _BASE_TABLE_VALUES:
            _record(match, "[dynamic->base] ")
    return hits


def _scanned_sources() -> list[Path]:
    paths = sorted(CORE_DIR.rglob("*.py")) + sorted(API_DIR.rglob("*.py"))
    # knowledge_objects/ 自身は表名の正本なので走査対象から外す（定数定義が引っかかる）。
    return [p for p in paths if KO_DIR not in p.parents and p.parent != KO_DIR]


def _all_hits() -> dict[str, list[str]]:
    """``{"<rel>:<symbol>": ["行番号: 抜粋", ...]}``（走査対象の全ファイル分）。"""
    found: dict[str, list[str]] = {}
    for path in _scanned_sources():
        rel = path.relative_to(ROOT).as_posix()
        for symbol, excerpt in _base_table_hits(path):
            found.setdefault(f"{rel}:{symbol}", []).append(excerpt)
    return found


class TestReadersUseLiveViews:
    def test_only_allowlisted_symbols_touch_the_base_tables(self):
        """KO5: 基表アクセス（静的名・動的補間とも）は allowlist のシンボルだけ。"""
        offending = {
            key: excerpts
            for key, excerpts in _all_hits().items()
            if key not in BASE_TABLE_ALLOWLIST
        }
        assert offending == {}, (
            "読み手は live ビューを読むこと（KO5）。基表に触る必要があるなら "
            "BASE_TABLE_ALLOWLIST に `ファイル:シンボル` 粒度で理由付き登録する: "
            f"{ {k: v[:2] for k, v in offending.items()} }"
        )

    def test_allowlist_entries_exist_and_have_reasons(self):
        """allowlist は実在ファイル + 非空の理由でなければならない（形骸化の防止）。"""
        for key, reason in BASE_TABLE_ALLOWLIST.items():
            rel, _, symbol = key.rpartition(":")
            assert rel and symbol, f"allowlist のキーが `ファイル:シンボル` 形でない: {key}"
            assert (ROOT / rel).exists(), f"allowlist に存在しないファイル: {rel}"
            assert reason.strip(), f"allowlist の理由が空: {key}"

    def test_allowlist_entries_actually_touch_the_base_tables(self):
        """理由が残っているのに基表を触らなくなったら allowlist から外す。"""
        found = _all_hits()
        stale = [key for key in BASE_TABLE_ALLOWLIST if key not in found]
        assert stale == [], f"基表を触らなくなった allowlist エントリ: {stale}"

    def test_detector_catches_dynamic_table_interpolation(self, tmp_path: Path):
        """検出器そのものの退行検査（f-string 補間を見逃さないこと）。"""
        probe = tmp_path / "probe.py"
        probe.write_text(
            "def reader(table):\n"
            '    return sa_text(f"SELECT * FROM {table} WHERE id = :id")\n',
            encoding="utf-8",
        )
        hits = _base_table_hits(probe)
        assert [symbol for symbol, _ in hits] == ["reader"], hits
        assert "[dynamic]" in hits[0][1]

    def test_detector_accepts_live_view_constants(self, tmp_path: Path):
        """``{VIEW_CLAIMS_LIVE}`` のように静的に live へ解決できる補間は通す。"""
        probe = tmp_path / "probe_live.py"
        probe.write_text(
            "def reader():\n"
            '    return sa_text(f"SELECT * FROM {VIEW_CLAIMS_LIVE}")\n',
            encoding="utf-8",
        )
        assert _base_table_hits(probe) == []

    def test_detector_catches_base_table_constants(self, tmp_path: Path):
        """``{TABLE_CLAIMS}`` は基表なので検出する。"""
        probe = tmp_path / "probe_base.py"
        probe.write_text(
            "def reader():\n"
            '    return sa_text(f"SELECT id FROM {TABLE_CLAIMS}")\n',
            encoding="utf-8",
        )
        hits = _base_table_hits(probe)
        assert [symbol for symbol, _ in hits] == ["reader"], hits
        assert "[dynamic->base]" in hits[0][1]

    def test_detector_catches_multiline_sql(self, tmp_path: Path):
        """整形で改行をまたいだ ``FROM\\n theory_claims`` も拾う（全文走査）。"""
        probe = tmp_path / "probe_multiline.py"
        probe.write_text(
            "def reader():\n"
            '    return """\n'
            "        SELECT id\n"
            "        FROM\n"
            "            theory_claims\n"
            '    """\n',
            encoding="utf-8",
        )
        assert [symbol for symbol, _ in _base_table_hits(probe)] == ["reader"]

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
