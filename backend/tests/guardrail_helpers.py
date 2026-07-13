"""ガードレールテスト共通ヘルパー。

複数の "○○層" ガードレールテスト（test_doubt_guardrails.py / test_admin_assistant.py /
test_next_steps_guardrails.py / test_image_library_guardrails.py /
test_llm_usage_guardrails.py / test_reconstruction_guardrails.py /
test_shared_versioning_guardrails.py / test_status_guardrails.py 等）で
ほぼ同一のまま5回以上コピーされていた「ディレクトリ配下の *.py に禁止語彙が
出現しないこと」「1ソース文字列に禁止語彙が出現しないこと」を検証する定型コードを
共通化する。

設計方針:
- ここに置くのは「読み込み → 文字列包含チェック」という機械的な走査だけ。
  何を禁止語彙にするか（fastapi import・DELETE FROM・煽り語彙・特定関数名 等）は
  すべて呼び出し側が明示的に渡す。ヘルパー自身はドメイン知識を持たない。
- 複数ファイル・複数語で違反があっても最初の1件で打ち切らず、まとめて報告する
  （個々の `assert word not in src` を並べるより診断性が高く、検証の強さが弱まることもない）。
- 「関数本体をそれっぽく切り出して中身を検査する」パターン（next_steps / admin_assistant
  ガードレールで重複していた素朴な `\\ndef ` 探索）も `extract_function_source` として共通化する。
  より厳密な抽出（例: `@router` も終端とみなす等）が必要な場合は呼び出し側で
  独自の正規表現ベース抽出を使うこと（例: test_image_library_guardrails.py の
  `_extract_function_body`）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence


def _iter_tree_paths(dir_paths: Path | Iterable[Path], glob: str = "*.py") -> list[Path]:
    """dir_paths（1つの Path、または Path の列）配下の *.py を再帰的に列挙する。"""
    if isinstance(dir_paths, Path):
        dir_paths = (dir_paths,)
    paths: list[Path] = []
    for d in dir_paths:
        paths.extend(sorted(d.rglob(glob)))
    return paths


def assert_paths_forbid(paths: Iterable[Path], forbidden_terms: Sequence[str]) -> None:
    """paths の各ファイルに forbidden_terms のいずれも含まれないことを検証する。

    複数ファイル・複数語にまたがる違反があっても、最初の1件で打ち切らず全違反を
    まとめて報告する。
    """
    offending: list[str] = []
    for path in paths:
        src = path.read_text(encoding="utf-8")
        for term in forbidden_terms:
            if term in src:
                offending.append(f"{path}:{term!r}")
    assert offending == [], f"forbidden terms found: {offending}"


def assert_module_tree_forbids(
    dir_paths: Path | Iterable[Path],
    forbidden_terms: Sequence[str],
    *,
    glob: str = "*.py",
) -> None:
    """dir_paths（1つ or 複数のディレクトリ）配下の全 *.py に forbidden_terms の
    いずれも含まれないことを検証する（例: DELETE FROM を発行しない・特定モジュール名を
    参照しない、等のコーパス横断チェック）。
    """
    assert_paths_forbid(_iter_tree_paths(dir_paths, glob=glob), forbidden_terms)


def assert_module_tree_does_not_import(
    dir_paths: Path | Iterable[Path],
    modules: Sequence[str],
    *,
    glob: str = "*.py",
) -> None:
    """dir_paths 配下の全 *.py が modules のいずれも import しないことを検証する
    （`import <module>` / `from <module>` の両形を弾く）。

    core/ 配下の各レイヤーで繰り返し書かれていた「core モジュールが FastAPI を
    import しない」ガードレールの定型実装。
    """
    terms = [t for m in modules for t in (f"import {m}", f"from {m}")]
    assert_module_tree_forbids(dir_paths, terms, glob=glob)


def assert_source_forbids(
    src: str,
    forbidden_terms: Sequence[str],
    *,
    context: str = "<source>",
) -> None:
    """既に読み込み済みの1ソース文字列に forbidden_terms のいずれも含まれないことを
    検証する（全違反語をまとめて報告する）。
    """
    offending = [term for term in forbidden_terms if term in src]
    assert offending == [], f"{context}: forbidden terms found: {offending}"


def assert_source_does_not_import(
    src: str,
    modules: Sequence[str],
    *,
    context: str = "<source>",
) -> None:
    """既に読み込み済みの1ソース文字列が modules のいずれも import しないことを検証する。"""
    terms = [t for m in modules for t in (f"import {m}", f"from {m}")]
    assert_source_forbids(src, terms, context=context)


def read_migration_sql(backend_dir: Path, number: int) -> str:
    """``backend_dir/db`` 配下の ``NNN_*.sql``（3桁ゼロ埋め番号プレフィックス）を
    1件読み込んで返す。

    マイグレーション正本は ``backend/db/*.sql``（``core/migrations.py`` が起動時に
    番号順に適用する）。ガードレールテストが「migration N が適用される」ことを
    検査する際は、``backend/api/main.py`` のインライン DDL ではなくこのヘルパー
    経由で正本 SQL ファイルを読むこと（2026-07 migration 正本一本化）。
    """
    matches = sorted((Path(backend_dir) / "db").glob(f"{number:03d}_*.sql"))
    assert len(matches) == 1, (
        f"expected exactly one migration file numbered {number:03d} in "
        f"{Path(backend_dir) / 'db'}, found {[m.name for m in matches]}"
    )
    return matches[0].read_text(encoding="utf-8")


def extract_function_source(src: str, fn_name: str) -> str:
    """``def {fn_name}`` の開始位置から、次のトップレベル ``\\ndef `` 直前
    （無ければ文字列末尾）までを素朴に切り出す。

    ネストした def を含む関数や、他の関数名の接頭辞になっている関数名がある場合は
    正しく切り出せない（元の重複コードと同じ制約）。より厳密な抽出が必要な場合は
    呼び出し側で独自の正規表現ベース抽出を使うこと。
    """
    start = src.index(f"def {fn_name}")
    tail = src[start + 1 :]
    rel_end = tail.find("\ndef ")
    end = start + 1 + rel_end if rel_end != -1 else len(src)
    return src[start:end]
