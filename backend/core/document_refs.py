"""教材の参照（document ref）の2つの ID 体系を1箇所で突き合わせる（IK-0550）。

教材には ID が2つある:

- ``documents.id``（UUID）— 解析成果（``theory_claims`` / ``chunks.document_id`` …）が指す
  document の正本 ID。**クライアントへは常にこれを ``document_id`` というキーで渡す**。
- ``documents.source_path``（= ``material_id``）— アップロード時に振られた ID
  （``uploads/{material_id}.pdf`` の名前・``chunks.material_id`` 列・コースの
  ``sources[].material_id``）。UUID に見えることがあるが ``documents.id`` ではない。
  ``material_id`` というキーは、この列を名指すところにだけ残す。

受け付ける側（クライアントから ref を受け取る経路）は、どちらの形が来ても同じ document に
着地させる。その突き合わせは単数なら ``api.services._resolve_document``、複数なら本モジュールの
:func:`resolve_document_refs` の **2つだけ**が行う（ガードレール
``test_document_refs_guardrails.py``）。同じ ref が一方の ``id`` と別の行の ``source_path``
の両方に一致するときは ``id`` 一致を採る（UUID は正本 ID なので）。

FastAPI を import しない（開発ルール2）。SQL はこのモジュールの中だけ。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import text as sa_text

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def is_document_uuid(value: object) -> bool:
    """``documents.id`` の形（UUID 文字列）か。``material_id`` も UUID に見え得るので、
    形だけで「documents.id である」とは決めない — 判定は :func:`resolve_document_refs`。"""
    return bool(_UUID_RE.match(str(value or "").strip()))


def _clean_refs(refs: Iterable[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for ref in refs or []:
        text = str(ref or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def resolve_document_refs(session: Any, refs: Iterable[Any] | None) -> dict[str, dict[str, str]]:
    """``ref -> {"document_id": documents.id, "material_id": source_path}`` を返す。

    ``ref`` は ``documents.id``（UUID）か ``source_path``（= material_id）のどちらでもよい。
    解決できない ref は結果に現れない。1 SQL。呼び出し側がセッションの寿命を持つ。
    """
    cleaned = _clean_refs(refs)
    if not cleaned:
        return {}
    rows = session.execute(
        sa_text(
            """
            SELECT id::text AS id, COALESCE(source_path, '') AS source_path
              FROM documents
             WHERE id::text = ANY(:refs) OR source_path = ANY(:refs)
            """
        ),
        {"refs": cleaned},
    ).fetchall()
    wanted = set(cleaned)
    by_id: dict[str, dict[str, str]] = {}
    by_path: dict[str, dict[str, str]] = {}
    for row in rows or []:
        doc_id = str(row[0] or "").strip()
        source_path = str(row[1] or "").strip()
        if not doc_id:
            continue
        entry = {"document_id": doc_id, "material_id": source_path}
        if doc_id in wanted:
            by_id[doc_id] = entry
        if source_path and source_path in wanted:
            by_path.setdefault(source_path, entry)
    out: dict[str, dict[str, str]] = {}
    for ref in cleaned:
        entry = by_id.get(ref) or by_path.get(ref)
        if entry:
            out[ref] = entry
    return out


def canonical_material_ids(session: Any, refs: Iterable[Any] | None) -> list[str]:
    """ref の並びを ``source_path``（material_id）の並びに揃える（順序保持・重複除去）。

    ``documents.id`` で渡された教材も ``source_path`` へ寄せる。解決できない ref と
    ``source_path`` を持たない document は落とす（推測で ID を作らない）。
    """
    resolved = resolve_document_refs(session, refs)
    out: list[str] = []
    seen: set[str] = set()
    for ref in _clean_refs(refs):
        material_id = (resolved.get(ref) or {}).get("material_id") or ""
        if material_id and material_id not in seen:
            seen.add(material_id)
            out.append(material_id)
    return out
