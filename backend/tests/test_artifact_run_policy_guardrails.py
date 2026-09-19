"""成果物 run の選び方を1本に保つガードレール（知識構造の見直し 2026-09-12 P0-8 / C-8）。

C-8 の指摘: 「どの run の ``stage_outputs._artifacts`` を読むか」のポリシが
①採用 run（``resolve_artifact_runs``）②completed 優先・無ければ最新
（旧 ``deliberation/refs.document_run_artifacts``）③status 無視の最新
（``get_latest_analysis_run``）④``DISTINCT ON (document_id)`` の自前 SQL
の4種に分裂していて、同じ document でも画面ごとに別 run の成果物が出得た。

是正後の規約:
- 成果物（``_artifacts``）を読むのは ``persistence.document_run_artifacts`` /
  ``resolve_artifact_runs`` だけ。policy 語彙は ``ARTIFACT_RUN_POLICIES`` の2値。
- ``get_latest_analysis_run`` は「いま走っている run を見たい」用途
  （resume / 進捗表示 / 前回 run の options・cartridge 継承）専用。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for p in (str(ROOT / "src"), str(BACKEND), str(BACKEND / "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.document_pipeline import persistence  # noqa: E402

CORE_DIR = BACKEND / "core"
API_DIR = BACKEND / "api"

# ``get_latest_analysis_run`` を呼んでよいファイル（成果物参照ではない用途）。
# - orchestrator: resume 判定と前回 run の options 継承
# - api/routes/admin.py: 再解析時の「前回 run の options / cartridge」継承
# - persistence.py: 自己参照（resolve_artifact_run の後方互換 fallback）
_LATEST_RUN_ALLOWLIST = {
    CORE_DIR / "document_pipeline" / "orchestrator.py",
    CORE_DIR / "document_pipeline" / "persistence.py",
    API_DIR / "routes" / "admin.py",
}


def _source_files() -> list[Path]:
    return [
        p
        for d in (CORE_DIR, API_DIR)
        for p in sorted(d.rglob("*.py"))
        if "__pycache__" not in p.parts
    ]


def _calls_function(path: Path, name: str) -> bool:
    """``name(...)`` の**呼び出し**が現れるか（import 行は数えない）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == name:
            return True
        if isinstance(func, ast.Attribute) and func.attr == name:
            return True
    return False


# ---------------------------------------------------------------------------
# (a) policy 語彙
# ---------------------------------------------------------------------------


def test_artifact_run_policies_vocabulary_is_fixed():
    assert persistence.ARTIFACT_RUN_POLICIES == ("adopted", "latest")
    assert set(persistence._ARTIFACT_RUN_SELECT_SQL) == set(
        persistence.ARTIFACT_RUN_POLICIES
    )


@pytest.mark.parametrize(
    "call",
    [
        lambda: persistence.document_run_artifacts("doc-1", policy="newest"),
        lambda: persistence.document_run_cartridge_id("doc-1", policy="newest"),
        lambda: persistence.resolve_artifact_runs(None, ["doc-1"], policy="newest"),
    ],
)
def test_unknown_policy_raises_value_error(call):
    with pytest.raises(ValueError):
        call()


def test_default_policy_is_adopted_everywhere():
    import inspect

    for fn in (
        persistence.document_run_artifacts,
        persistence.document_run_cartridge_id,
        persistence.resolve_artifact_runs,
    ):
        assert inspect.signature(fn).parameters["policy"].default == "adopted"


def test_adopted_sql_prefers_active_run_then_completed():
    sql = persistence._ARTIFACT_RUN_SELECT_SQL["adopted"]
    assert "active_analysis_run_id" in sql
    assert "status = 'completed'" in sql
    # latest は status を問わない（成果物には使わない経路）。
    assert "status" not in persistence._ARTIFACT_RUN_SELECT_SQL["latest"]


# ---------------------------------------------------------------------------
# (b) get_latest_analysis_run の呼び出し元は許可リストのみ
# ---------------------------------------------------------------------------


def test_get_latest_analysis_run_callers_are_allowlisted():
    offending = [
        str(p.relative_to(BACKEND))
        for p in _source_files()
        if p not in _LATEST_RUN_ALLOWLIST
        and _calls_function(p, "get_latest_analysis_run")
    ]
    assert offending == [], (
        "成果物を読む経路は document_run_artifacts / resolve_artifact_runs に寄せる"
        f"（C-8）。許可外の呼び出し: {offending}"
    )


def test_allowlisted_files_exist():
    for path in _LATEST_RUN_ALLOWLIST:
        assert path.exists(), path


# ---------------------------------------------------------------------------
# (c) stage_outputs から _artifacts を直接読むのは persistence.py だけ
# ---------------------------------------------------------------------------


def _selects_bare_stage_outputs(path: Path) -> bool:
    """``FROM document_analysis_runs`` から ``stage_outputs`` 列を素で SELECT する
    SQL 文字列がファイル内にあるか。

    ``stage_outputs#>>'{...}'`` のような JSON パス集計（apparatus の日次使用量など）は
    run 選択ではないので対象外にする。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover
        return False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        sql = node.value
        if "FROM document_analysis_runs" not in sql:
            continue
        if re.search(r"\bstage_outputs\b(?!\s*(#>>|->))", sql):
            return True
    return False


def _reads_artifacts_key(path: Path) -> bool:
    src = path.read_text(encoding="utf-8")
    return '"_artifacts"' in src or "'_artifacts'" in src


# ``stage_outputs`` を素で SELECT する自前 run クエリが**残ってよい**ファイル。
# いずれも「いま走っている run の進捗（status / current_stage / error_message /
# stage_outputs[current_stage]）」を見るためのもので、成果物（``_artifacts``）を
# 読むためではない。成果物は ``persistence.document_run_artifacts`` /
# ``resolve_artifact_runs`` を通す（C-8）。
_PROGRESS_RUN_QUERY_FILES = {
    CORE_DIR / "status" / "projector.py",              # 教材・コース状態の導出
    API_DIR / "routes" / "lecture_studio" / "pipeline.py",  # パイプライン進捗表示
    API_DIR / "services.py",                           # 非同期タスクの進捗 enrich
    API_DIR / "routes" / "admin.py",                   # 教材一覧の進捗列（成果物は別途 adopted）
}

# 「自前 SQL で run を選び、その stage_outputs から ``_artifacts`` を読む」箇所は
# **ゼロ**（P0-8 で admin.py の2箇所・lecture_studio/topics.py を解消済み）。
# 残件リストは空のまま保つ。項目を足すのは「今は直せない」ことの明示宣言なので、
# 足したら test_adhoc_artifact_reader_backlog_is_empty が落ちて気づける。
_ADHOC_ARTIFACT_READER_BACKLOG: set[Path] = set()


def test_bare_stage_outputs_selects_are_progress_only():
    """``stage_outputs`` を素で SELECT する自前 run クエリを増やさない（C-8）。

    許されるのは persistence（正本）と進捗表示の4ファイルだけ。新しい画面が
    成果物を読みたくなったら ``document_run_artifacts`` を通すこと。
    """
    allowed = (
        {CORE_DIR / "document_pipeline" / "persistence.py"}
        | _PROGRESS_RUN_QUERY_FILES
        | _ADHOC_ARTIFACT_READER_BACKLOG
    )
    offending = [
        str(path.relative_to(BACKEND))
        for path in _source_files()
        if path not in allowed and _selects_bare_stage_outputs(path)
    ]
    assert offending == [], (
        "run 選択の自前 SQL を書かないこと（C-8）: " f"{offending}"
    )


def test_progress_only_files_do_not_read_artifacts_from_their_own_query():
    """進捗用クエリのファイルが ``_artifacts`` を読むなら、採用 run 経由であること。

    admin.py だけは同じファイルに両方（進捗の自前クエリと成果物読み）があるので、
    成果物側が ``resolve_artifact_runs`` を通っていることを関数単位で確かめる。
    """
    from tests.guardrail_helpers import extract_function_source

    for path in _PROGRESS_RUN_QUERY_FILES:
        if not _reads_artifacts_key(path):
            continue
        assert path == API_DIR / "routes" / "admin.py", (
            f"進捗用クエリのファイルが成果物を読んでいる: {path}"
        )
        src = path.read_text(encoding="utf-8")
        for fn in ("list_materials", "_build_material_context"):
            body = extract_function_source(src, fn)
            assert '"_artifacts"' in body, f"{fn}: 検査対象が移動している"
            assert "resolve_artifact_runs(" in body, (
                f"{fn}: 成果物は採用 run（resolve_artifact_runs）から読むこと（C-8）"
            )


def test_adhoc_artifact_reader_backlog_is_empty():
    """残件リストは空であること（新しい「例外」を黙って積まない）。"""
    assert _ADHOC_ARTIFACT_READER_BACKLOG == set(), (
        "成果物の自前 run 選択は残っていないはず。例外を足すなら理由を設計記録に残すこと: "
        f"{sorted(str(p) for p in _ADHOC_ARTIFACT_READER_BACKLOG)}"
    )


def test_progress_run_query_files_still_query_runs():
    """許可リストが実態とずれたら気づけるようにする（解消済みなら外す）。"""
    stale = [
        str(path.relative_to(BACKEND))
        for path in _PROGRESS_RUN_QUERY_FILES
        if not (path.exists() and _selects_bare_stage_outputs(path))
    ]
    assert stale == [], f"進捗用クエリが無くなったファイルは許可リストから外すこと: {stale}"


def test_lecture_studio_topics_document_structure_uses_adopted_run():
    """原稿スタジオの文書構造が自前の最新 run クエリに戻っていないこと（C-8）。"""
    from tests.guardrail_helpers import extract_function_source

    src = (API_DIR / "routes" / "lecture_studio" / "topics.py").read_text(encoding="utf-8")
    body = extract_function_source(src, "get_lecture_studio_document_structure")
    assert "resolve_artifact_runs(" in body
    assert "FROM document_analysis_runs" not in body


def test_lecture_studio_equation_previews_use_resolve_artifact_runs():
    """原稿スタジオの式プレビューが自前 DISTINCT ON に戻っていないこと。"""
    from tests.guardrail_helpers import extract_function_source

    src = (API_DIR / "routes" / "lecture_studio" / "_shared.py").read_text(encoding="utf-8")
    body = extract_function_source(src, "_load_equation_formula_previews")
    assert "resolve_artifact_runs(" in body
    assert "FROM document_analysis_runs" not in body
    # run 選択の SQL を自前で発行しない（resolve_artifact_runs に一本化）。
    assert "session.execute(" not in body


def test_refs_document_run_artifacts_delegates_to_persistence():
    """W層 refs の同名関数は persistence への委譲であること（独自 SQL に戻さない）。"""
    src = (CORE_DIR / "deliberation" / "refs.py").read_text(encoding="utf-8")
    from tests.guardrail_helpers import extract_function_source

    body = extract_function_source(src, "document_run_artifacts")
    assert "document_run_artifacts as _document_run_artifacts" in body
    assert "FROM document_analysis_runs" not in body


def test_positioning_cartridge_id_delegates_to_persistence():
    """W層 positioning の分野解決が6種目の独自 SQL に戻らないこと（C-8）。"""
    from tests.guardrail_helpers import extract_function_source

    src = (CORE_DIR / "deliberation" / "positioning.py").read_text(encoding="utf-8")
    body = extract_function_source(src, "_document_cartridge_id")
    assert "document_run_cartridge_id(" in body
    assert "FROM document_analysis_runs" not in body


def test_refs_document_run_artifacts_signature_unchanged():
    import inspect

    from core.deliberation import refs

    params = list(inspect.signature(refs.document_run_artifacts).parameters)
    assert params == ["document_id"]
