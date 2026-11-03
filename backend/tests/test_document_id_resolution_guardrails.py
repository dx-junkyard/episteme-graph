"""「どの document 行か」の解決を 1 箇所に寄せるガードレール（IK-0594・IK-0550 の続き）。

教材には ID が 2 つある（``documents.id`` = UUID / ``documents.source_path`` = material_id）。
クライアントやコースの ``sources[].material_id`` からはどちらの形でも届くので、
「どの documents 行か」を決める SQL は次の 2 つだけが書く:

- 単数・権限込み（API 層）: ``services._resolve_document`` / ``services.resolve_document_access``
- それ以外（core・複数・一覧）: ``core.document_refs``（``resolve_document_rows`` /
  ``resolve_document_row`` / ``resolve_document_refs`` / ``list_documents_for_refs``）

検査は 2 段:

1. 両形の突き合わせ（``id::text = :x OR source_path = :x`` 型）は正本 2 ファイル以外に無い
   （``test_document_refs.py`` の既存ガードレールの legacy allowlist は空になった）。
2. material_id 形だけの照合（``source_path = :x`` / ``= ANY(:x)`` / ``IN (...)``）は、
   入力が**定義上 material_id**であるか正本である関数（下の allowlist・理由付き）にしか
   無い。新しい経路で教材 ref を受けたら ``core.document_refs`` を使う。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.guardrail_helpers import extract_function_source

_BACKEND = Path(__file__).resolve().parents[1]

_BOTH_FORMS_RE = re.compile(
    r"id::text\s*=\s*(?:ANY\()?:\w+\)?\s+OR\s+(?:\w+\.)?source_path"
    r"|source_path\s*=\s*(?:ANY\()?:\w+\)?\s+OR\s+(?:\w+\.)?id::text"
)
_MATERIAL_ONLY_RE = re.compile(r"(?:\w+\.)?source_path\s*(?:=\s*(?:ANY\()?:|IN\s*\()")

_CANONICAL_FILES = {"api/services.py", "core/document_refs.py"}

#: ``(ファイル, 関数)`` → material_id 形だけで ``documents`` を引いてよい理由。
#: ここに足すときは「入力が定義上 material_id である」か「両形を同じ条件で扱っている」ことを書く。
_MATERIAL_ONLY_ALLOWLIST: dict[tuple[str, str], str] = {
    ("api/services.py", "_resolve_document"): "単数の正本（両形）",
    ("core/document_refs.py", "_fetch_document_rows"): "複数の正本（両形）",
    ("api/services.py", "list_visible_document_ids"): (
        "コース経由開示。source_path 形と documents.id 形の両方を同じ集合に当てる"
    ),
    ("api/routes/admin.py", "_material_visibility_clauses"): (
        "教材一覧・詳細のコース経由開示。source_path 形と documents.id 形の両方を当てる"
    ),
    ("api/routes/admin.py", "_build_material_context"): (
        "入力は呼び出し元で _canonical_selected_material_ids（document_refs）済みの material_id"
    ),
    ("api/routes/admin.py", "course_builder_chat"): (
        "body.selected_material_ids は同じ関数の先頭で document_refs により material_id に揃えてある"
    ),
    ("core/deliberation/positioning.py", "_documents_by_material_id"): (
        "入力は chunks.material_id（定義上 source_path）"
    ),
    ("core/embedder.py", "embed_and_store"): "取り込み時の documents 行の作成（material_id で冪等）",
    ("core/embedder.py", "embed_and_store_pattern"): "パターン文書の作成（pattern:{id} で冪等）",
}


def _scan_paths() -> list[Path]:
    return sorted(list((_BACKEND / "api").rglob("*.py")) + list((_BACKEND / "core").rglob("*.py")))


def _enclosing_function(tree: ast.AST, lineno: int) -> str:
    best: tuple[int, str] | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= lineno <= end:
                span = end - node.lineno
                if best is None or span < best[0]:
                    best = (span, node.name)
    return best[1] if best else "<module>"


def _material_only_sites() -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for path in _scan_paths():
        src = path.read_text(encoding="utf-8")
        if not _MATERIAL_ONLY_RE.search(src):
            continue
        tree = ast.parse(src)
        rel = path.relative_to(_BACKEND).as_posix()
        for match in _MATERIAL_ONLY_RE.finditer(src):
            lineno = src[: match.start()].count("\n") + 1
            sites.add((rel, _enclosing_function(tree, lineno)))
    return sites


def test_both_forms_only_in_canonical_files():
    offenders = [
        path.relative_to(_BACKEND).as_posix()
        for path in _scan_paths()
        if _BOTH_FORMS_RE.search(path.read_text(encoding="utf-8"))
        and path.relative_to(_BACKEND).as_posix() not in _CANONICAL_FILES
    ]
    assert offenders == [], (
        "教材 ref の両形（documents.id / source_path）の突き合わせは services._resolve_document か "
        f"core.document_refs に委ねる（IK-0594）: {offenders}"
    )


def test_material_only_lookups_are_declared():
    undeclared = sorted(_material_only_sites() - set(_MATERIAL_ONLY_ALLOWLIST))
    assert undeclared == [], (
        "material_id 形（source_path）だけで documents を引く経路は、入力が定義上 material_id で"
        "ない限り core.document_refs を使う。理由があるなら _MATERIAL_ONLY_ALLOWLIST に書く: "
        f"{undeclared}"
    )


def test_allowlist_has_no_stale_entries():
    stale = sorted(set(_MATERIAL_ONLY_ALLOWLIST) - _material_only_sites())
    assert stale == [], f"既に該当 SQL の無い allowlist 項目は消す: {stale}"


def test_allowlist_reasons_are_written():
    assert all(reason.strip() for reason in _MATERIAL_ONLY_ALLOWLIST.values())


def test_resolution_paths_use_document_refs():
    """IK-0594 で寄せた経路が core.document_refs を通っていること（独自 SQL に戻さない）。"""
    expected = {
        "api/routes/admin.py": ["get_material", "update_material_visibility", "delete_material"],
        "api/routes/lecture_studio/pipeline.py": [
            "_course_pipeline_documents", "_get_editable_material_document",
        ],
        "core/status/projector.py": ["_fetch_document", "_fetch_documents_bulk"],
        "core/paper_discovery/radar.py": ["_document_row"],
        "core/paper_discovery/corpus.py": [
            "document_domain_keys", "domain_document_refs", "domain_document_rows",
        ],
        "core/doubt/seminar_brief.py": ["_resolve_document_id"],
        "core/admin_assistant/next_steps.py": ["_ref_to_document_id"],
        "core/descent/resolve.py": ["course_document_ids"],
        "api/services.py": ["reanalyze_course_structure_background"],
    }
    missing: list[str] = []
    for rel, functions in expected.items():
        src = (_BACKEND / rel).read_text(encoding="utf-8")
        for fn in functions:
            body = extract_function_source(src, fn)
            if "_document_refs." not in body:
                missing.append(f"{rel}:{fn}")
    assert missing == [], missing


def test_get_material_no_longer_prefers_source_path_over_id():
    """旧 get_material は ``ORDER BY (d.source_path = :material_id) DESC`` で source_path 一致を
    優先し、正本（id 一致を優先）と逆の規則を持っていた。"""
    src = (_BACKEND / "api/routes/admin.py").read_text(encoding="utf-8")
    body = extract_function_source(src, "get_material")
    assert "ORDER BY (d.source_path" not in body
    assert "d.id = CAST(:document_id AS uuid)" in body
