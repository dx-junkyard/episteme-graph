"""教材の参照（document ref）の2つの ID 体系を1箇所で突き合わせる（IK-0550）。

教材には ID が2つある:

- ``documents.id``（UUID）— 解析成果（``theory_claims`` / ``chunks.document_id`` …）が指す
  document の正本 ID。**クライアントへは常にこれを ``document_id`` というキーで渡す**。
- ``documents.source_path``（= ``material_id``）— アップロード時に振られた ID
  （``uploads/{material_id}.pdf`` の名前・``chunks.material_id`` 列・コースの
  ``sources[].material_id``）。UUID に見えることがあるが ``documents.id`` ではない。
  ``material_id`` というキーは、この列を名指すところにだけ残す。

受け付ける側（クライアントから ref を受け取る経路）は、どちらの形が来ても同じ document に
着地させる。その突き合わせは API 層の単数なら ``api.services._resolve_document``（権限込みは
``services.resolve_document_access``）、それ以外（core・複数）は本モジュールの
:func:`resolve_document_rows` 系の **2つだけ**が行う（ガードレール
``test_document_refs.py`` / ``test_document_id_resolution_guardrails.py``）。同じ ref が一方の ``id`` と別の行の ``source_path``
の両方に一致するときは ``id`` 一致を採る（UUID は正本 ID なので）。

FastAPI を import しない（開発ルール2）。SQL はこのモジュールの中だけ。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
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


#: 解決と同じ 1 SQL で一緒に引いてよい ``documents`` の列（キー → SQL 式）。
#: 列名を呼び出し側の文字列で SQL に補間しないため、ここに無い列は ``ValueError``。
_EXTRA_COLUMN_SQL: dict[str, str] = {
    "title": "title",
    "filename": "filename",
    "status": "status",
    "chunk_count": "chunk_count",
    "created_at": "created_at",
    "updated_at": "updated_at",
    "source_url": "source_url",
    "uploaded_by": "uploaded_by::text",
    "visibility": "visibility",
    "doc_type": "doc_type",
}


def _cell(row: Any, index: int, key: str) -> Any:
    """行の 1 セル。SQLAlchemy の Row（タプル様）でも、mapping を返す呼び出し側の
    フェイクでも同じに読む。"""
    if isinstance(row, Mapping):
        return row.get(key)
    return row[index] if len(row) > index else None


def _fetch_document_rows(
    session: Any, cleaned: list[str], columns: Iterable[str]
) -> list[dict[str, Any]]:
    """両形のどちらかに一致する ``documents`` 行を新しい順に返す（1 SQL・本モジュール内専用）。"""
    extra = [str(c) for c in (columns or ())]
    unknown = [c for c in extra if c not in _EXTRA_COLUMN_SQL]
    if unknown:
        raise ValueError(f"unsupported documents columns: {unknown}")
    if not cleaned:
        return []
    select_extra = "".join(f", {_EXTRA_COLUMN_SQL[c]} AS {c}" for c in extra)
    rows = session.execute(
        sa_text(
            f"""
            SELECT id::text AS id, COALESCE(source_path, '') AS source_path{select_extra}
              FROM documents
             WHERE id::text = ANY(:refs) OR source_path = ANY(:refs)
             ORDER BY created_at DESC NULLS LAST, id
            """
        ),
        {"refs": cleaned},
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        doc_id = str(_cell(row, 0, "id") or "").strip()
        if not doc_id:
            continue
        entry: dict[str, Any] = {
            "document_id": doc_id,
            "material_id": str(_cell(row, 1, "source_path") or "").strip(),
        }
        for index, name in enumerate(extra, start=2):
            entry[name] = _cell(row, index, name)
        out.append(entry)
    return out


def resolve_document_rows(
    session: Any,
    refs: Iterable[Any] | None,
    *,
    columns: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    """``ref -> {"document_id", "material_id", <columns>...}`` を返す（1 SQL・1 ref = 1 行）。

    ``ref`` は ``documents.id``（UUID）か ``source_path``（= material_id）のどちらでもよい。
    同じ ref が一方の ``id`` と別の行の ``source_path`` の両方に一致するときは ``id`` 一致を採る。
    同じ ``source_path`` の行が複数あれば新しい行（``created_at`` 降順の先頭）を採る。
    ``columns`` は :data:`_EXTRA_COLUMN_SQL` のキーだけ（生の値。COALESCE は呼び出し側）。
    解決できない ref は結果に現れない。呼び出し側がセッションの寿命を持つ。
    """
    cleaned = _clean_refs(refs)
    rows = _fetch_document_rows(session, cleaned, columns)
    wanted = set(cleaned)
    by_id: dict[str, dict[str, Any]] = {}
    by_path: dict[str, dict[str, Any]] = {}
    for entry in rows:
        doc_id = entry["document_id"]
        source_path = entry["material_id"]
        if doc_id in wanted:
            by_id[doc_id] = entry
        if source_path and source_path in wanted:
            by_path.setdefault(source_path, entry)
    out: dict[str, dict[str, Any]] = {}
    for ref in cleaned:
        entry = by_id.get(ref) or by_path.get(ref)
        if entry:
            out[ref] = entry
    return out


def list_documents_for_refs(
    session: Any,
    refs: Iterable[Any] | None,
    *,
    columns: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """ref のどちらかの形に一致する ``documents`` 行を**すべて**返す（1 SQL・新しい順・id で重複除去）。

    一覧を作る経路（分野のコーパス・コースの解析対象）向け。同じ ``source_path`` を持つ行が
    複数あっても落とさない（:func:`resolve_document_rows` は 1 ref = 1 行に畳む）。
    """
    cleaned = _clean_refs(refs)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for entry in _fetch_document_rows(session, cleaned, columns):
        if entry["document_id"] in seen:
            continue
        seen.add(entry["document_id"])
        out.append(entry)
    return out


def resolve_document_refs(session: Any, refs: Iterable[Any] | None) -> dict[str, dict[str, str]]:
    """``ref -> {"document_id": documents.id, "material_id": source_path}`` を返す。

    ``ref`` は ``documents.id``（UUID）か ``source_path``（= material_id）のどちらでもよい。
    解決できない ref は結果に現れない。1 SQL。呼び出し側がセッションの寿命を持つ。
    """
    return resolve_document_rows(session, refs)  # type: ignore[return-value]


def resolve_document_row(
    session: Any, ref: Any, *, columns: Iterable[str] = ()
) -> dict[str, Any] | None:
    """単数版の :func:`resolve_document_rows`。解決できなければ ``None``。

    権限は見ない — 権限込みで 1 回に解決したい API 層は ``services.resolve_document_access``
    を使う（core からは services を import できないため、core 側の入口はこちら）。
    """
    text = str(ref or "").strip()
    if not text:
        return None
    return resolve_document_rows(session, [text], columns=columns).get(text)


def canonical_document_ids(session: Any, refs: Iterable[Any] | None) -> list[str]:
    """ref の並びを ``documents.id`` の並びに揃える（順序保持・重複除去・未解決は落とす）。"""
    resolved = resolve_document_refs(session, refs)
    out: list[str] = []
    seen: set[str] = set()
    for ref in _clean_refs(refs):
        doc_id = (resolved.get(ref) or {}).get("document_id") or ""
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            out.append(doc_id)
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


# ---------------------------------------------------------------------------
# 論文の同一性キー（IK-0846）
#
# ``documents.id`` は「取り込みの行」の ID であって「論文」の ID ではない。同じ arXiv 論文を
# PDF と TeX で二度取り込むと行は 2 本になるが、論文は 1 本である。「N 本以上の論文で反復
# したら候補にする」規則（カテゴリギャップ・関係の共起）と、学習者に見せる論文の一覧・厚みは、
# 行ではなく論文を数える。その「論文」の判定はここだけが行う（数える側に判定を書き写さない）。
#
# 優先順: ``source_url`` の arXiv ID（arxiv.org の URL のときだけ）→ ファイル名の arXiv ID →
# 題名の arXiv ID（PDF 取り込みで題名が番号のままの教材）→ 無ければ ``documents.id``。
# arXiv ID は version 抜き（v1 と v2 は同じ論文）。曖昧（相異なる ID が 2 つ以上）なら
# 推定しない（``arxiv_id_from_filename`` の規則）。純関数で DB を引かない — 材料の列
# （source_url / filename / title）は数える側が自分の SELECT で一緒に引く。
# ---------------------------------------------------------------------------

#: キーの接頭辞（内部値。表示に使わない）。
PAPER_KEY_ARXIV_PREFIX = "arxiv:"
PAPER_KEY_DOCUMENT_PREFIX = "document:"

#: 題名が arXiv 番号そのもの（``2605.26810v1`` / ``2605.26810v1.pdf``）かの判定。
_ARXIV_ONLY_TITLE_RE = re.compile(
    r"^\s*(?:arxiv[:\-_ ]?)?\d{4}\.\d{4,5}(?:v\d+)?(?:\.(?:pdf|tar\.gz|gz|tex))?\s*$",
    re.IGNORECASE,
)


def _arxiv_id_from_source_url(source_url: Any) -> str:
    text = str(source_url or "").strip()
    if not text or "arxiv.org" not in text.lower():
        return ""
    from core.paper_discovery.schema import normalize_arxiv_id  # 遅延 import（純関数のみ）

    return normalize_arxiv_id(text) or ""


def paper_identity_key(
    document_id: Any,
    *,
    source_url: Any = None,
    filename: Any = None,
    title: Any = None,
) -> str:
    """1 本の取り込み（document 行）が指す「論文」のキーを返す（純関数）。

    ``"arxiv:2605.26810"``（version 抜き）か ``"document:{documents.id}"``。キーは数える・
    畳むための内部値で、DTO・画面に出さない。
    """
    from core.paper_discovery.schema import arxiv_id_from_filename  # 遅延 import（純関数のみ）

    arxiv_id = (
        _arxiv_id_from_source_url(source_url)
        or arxiv_id_from_filename(str(filename or ""))
        or arxiv_id_from_filename(str(title or ""))
    )
    if arxiv_id:
        return PAPER_KEY_ARXIV_PREFIX + arxiv_id
    return PAPER_KEY_DOCUMENT_PREFIX + str(document_id or "").strip()


def title_is_arxiv_number(title: Any) -> bool:
    """題名が arXiv 番号そのもの（読める題名が採れていない取り込み）か。"""
    return bool(_ARXIV_ONLY_TITLE_RE.match(str(title or "")))


def representative_document_ids(entries: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """``document_id -> 同じ論文の代表 document_id`` を返す（純関数・決定論）。

    ``entries`` の各要素は ``document_id`` と、任意で ``source_url`` / ``filename`` /
    ``title`` を持つ mapping。同じ論文（:func:`paper_identity_key` が一致）の取り込みが
    複数あれば、**読める題名を持つ方**（題名が空でも arXiv 番号そのものでもない）を代表に
    する。どちらも読める／読めないときは ``document_id`` の昇順の先頭（入力順に依らない —
    一覧と地図が別々の SQL から同じ代表を選ぶため）。
    """
    groups: dict[str, list[tuple[bool, str]]] = {}
    for entry in entries or ():
        doc_id = str(entry.get("document_id") or "").strip()
        if not doc_id:
            continue
        key = paper_identity_key(
            doc_id,
            source_url=entry.get("source_url"),
            filename=entry.get("filename"),
            title=entry.get("title"),
        )
        title = str(entry.get("title") or "").strip()
        unreadable = (not title) or title_is_arxiv_number(title)
        members = groups.setdefault(key, [])
        if all(m[1] != doc_id for m in members):
            members.append((unreadable, doc_id))
    out: dict[str, str] = {}
    for members in groups.values():
        representative = min(members)[1]
        for _, doc_id in members:
            out[doc_id] = representative
    return out
