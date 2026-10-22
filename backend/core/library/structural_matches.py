"""同じ構造のモジュールを持つ論文の DB 読み出し（``GET .../theory-modules/related``・§13.7）。

設計正本: ``docs/features/theory_module_layer_design.md`` §13.7。組み立て（純関数）と事実文は
``core/theory_modules/related.py`` にあり、route がこのモジュールの読み出しと合成する
（``core/theory_modules/`` は SQL を持たない規律 = TM2 のため、読み出しだけを L層側に置く）。

読むのは live ビュー ``knowledge_theory_modules_live`` だけ（基表を SQL で触ってよいのは書き手の
``core/document_pipeline/persistence.py`` だけ）。書き込みなし・LLM 0 回。本モジュールは
FastAPI を import しない。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from sqlalchemy import text as sa_text

from core.deliberation.schema import ELEMENT_THEORY_MODULE, IDENTITY_LINK_STATUS_REJECTED
from core.theory_modules import related as _related

from . import schema

_ELEMENT_THEORY_MODULE = ELEMENT_THEORY_MODULE
_LINK_STATUS_REJECTED = IDENTITY_LINK_STATUS_REJECTED
_ENTRY_STATUS_DISMISSED = schema.REVIEW_STATUS_DISMISSED


def _clean(value: Any) -> str:
    return str(value or "").strip()


def load_live_modules(session: Any, document_id: str) -> list[dict]:
    """当該 document の live 行（外枠・内側の両方。規則の版の食い違いの判定に使う）。"""
    rows = session.execute(
        sa_text(
            """
            SELECT id::text, agent_module_key, rule_version, level,
                   structure_fingerprint, identity_eligible,
                   label, visual_label, member_steps
              FROM knowledge_theory_modules_live
             WHERE document_id = CAST(:document_id AS uuid)
             ORDER BY created_at, id
            """
        ),
        {"document_id": document_id},
    ).fetchall()
    return [
        {
            "id": str(row[0]),
            "agent_module_key": _clean(row[1]),
            "rule_version": _clean(row[2]),
            "level": _clean(row[3]),
            "structure_fingerprint": _clean(row[4]),
            "identity_eligible": bool(row[5]),
            "label": _clean(_at(row, 6)),
            "visual_label": _clean(_at(row, 7)),
            "member_steps": _json_list(_at(row, 8)),
        }
        for row in rows
    ]


def _at(row: Any, index: int) -> Any:
    try:
        return row[index]
    except (IndexError, TypeError):
        return None


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            import json

            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def load_peer_outer_modules(session: Any, *, document_id: str, rule_version: str) -> list[dict]:
    """他 document の live 外枠行（近い構造 §22 B の材料。指紋は照合にだけ使い DTO に出さない）。"""
    rows = session.execute(
        sa_text(
            """
            SELECT document_id::text, label, visual_label, member_steps,
                   structure_fingerprint, rule_version
              FROM knowledge_theory_modules_live
             WHERE document_id <> CAST(:document_id AS uuid)
               AND level = 'outer'
               AND rule_version = :rule_version
             ORDER BY document_id, created_at, id
            """
        ),
        {"document_id": document_id, "rule_version": rule_version},
    ).fetchall()
    return [
        {
            "document_id": _clean(_at(row, 0)),
            "label": _clean(_at(row, 1)),
            "visual_label": _clean(_at(row, 2)),
            "member_steps": _json_list(_at(row, 3)),
            "structure_fingerprint": _clean(_at(row, 4)),
            "rule_version": _clean(_at(row, 5)),
            "level": "outer",
        }
        for row in rows
    ]


def module_match_states(
    session: Any, document_ids: Iterable[str], *, rule_version: str
) -> dict[str, dict]:
    """``{document_id: {"stale": bool, "fingerprints": set}}``（保存行を持つ document だけ）。

    レーダーの取り込み済みカードの構造照合の事実文（§22 A）の材料。``fingerprints`` は照合の
    対象（外枠・``identity_eligible``・現在の規則版）の指紋の集合で、**呼び出し側の内部で
    比較にだけ使い DTO に出さない**（TM12）。空入力は SQL を発行しない。
    """
    keys = sorted({_clean(d) for d in document_ids if _clean(d)})
    if not keys:
        return {}
    rows = session.execute(
        sa_text(
            """
            SELECT document_id::text, rule_version, level, identity_eligible, structure_fingerprint
              FROM knowledge_theory_modules_live
             WHERE document_id = ANY(CAST(:ids AS uuid[]))
            """
        ),
        {"ids": keys},
    ).fetchall()
    states: dict[str, dict] = {}
    for row in rows:
        doc = _clean(row[0])
        slot = states.setdefault(doc, {"stale": False, "fingerprints": set()})
        if _clean(row[1]) != rule_version:
            slot["stale"] = True
            continue
        if _clean(row[2]) == "outer" and bool(row[3]) and _clean(row[4]):
            slot["fingerprints"].add(_clean(row[4]))
    return states


def load_matching_modules(
    session: Any, *, document_id: str, rule_version: str, fingerprints: Iterable[str]
) -> list[dict]:
    """他 document の live 外枠行で、指紋が完全一致し ``identity_eligible`` のもの。"""
    keys = sorted({fp for fp in fingerprints if fp})
    if not keys:
        return []
    rows = session.execute(
        sa_text(
            """
            SELECT id::text, document_id::text, structure_fingerprint
              FROM knowledge_theory_modules_live
             WHERE document_id <> CAST(:document_id AS uuid)
               AND level = 'outer'
               AND identity_eligible
               AND rule_version = :rule_version
               AND structure_fingerprint = ANY(:fingerprints)
             ORDER BY document_id, created_at, id
            """
        ),
        {"document_id": document_id, "rule_version": rule_version, "fingerprints": keys},
    ).fetchall()
    return [
        {
            "id": str(row[0]),
            "document_id": _clean(row[1]),
            "structure_fingerprint": _clean(row[2]),
        }
        for row in rows
    ]


def load_structural_decisions(
    session: Any, candidate_keys: Iterable[str]
) -> dict[str, dict]:
    """``{candidate_key: {"dismissed": bool, "rejected_module_ids": set}}``（教員の判断）。

    構造エントリの見送り（``review_status='dismissed'``）と、理論モジュールから構造エントリへの
    リンクの却下（``status='rejected'``）だけを読む。
    """
    keys = sorted({key for key in candidate_keys if key})
    if not keys:
        return {}
    rows = session.execute(
        sa_text(
            """
            SELECT e.candidate_key, e.review_status, l.instance_element_id, l.status
              FROM library_entries e
              LEFT JOIN element_identity_links l
                ON l.shared_part_id = e.id
               AND l.instance_element_type = :element_type
             WHERE e.candidate_key = ANY(:keys)
            """
        ),
        {"element_type": _ELEMENT_THEORY_MODULE, "keys": keys},
    ).fetchall()
    decisions: dict[str, dict] = {}
    for row in rows:
        key = _clean(row[0])
        slot = decisions.setdefault(key, {"dismissed": False, "rejected_module_ids": set()})
        if _clean(row[1]) == _ENTRY_STATUS_DISMISSED:
            slot["dismissed"] = True
        if _clean(row[3]) == _LINK_STATUS_REJECTED and _clean(row[2]):
            slot["rejected_module_ids"].add(_clean(row[2]))
    return decisions


def load_document_titles(session: Any, document_ids: Iterable[str]) -> dict[str, str]:
    """``{document_id: title}``（内部 ID を画面に出さないための名前引き）。"""
    keys = sorted({_clean(d) for d in document_ids if _clean(d)})
    if not keys:
        return {}
    rows = session.execute(
        sa_text(
            "SELECT id::text, COALESCE(title, '') FROM documents "
            "WHERE id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": keys},
    ).fetchall()
    return {str(row[0]): _clean(row[1]) for row in rows}


def load_display_titles(
    session: Any, document_ids: Iterable[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """``({document_id: 表示題名}, {document_id: 題名に添える事実文})``（§22.4・読み取りのみ）。

    教材名がファイル名のまま（``arXiv-2508.06322v2``）なら、arXiv メタデータの**写し**
    （``paper_discovery_arxiv_metadata_cache``）の題名を表示題名にする。arXiv API は呼ばない
    （写しが無ければ「題名未取得」の事実文を添えて教材名のまま）。判定と文面の正本は
    ``core.paper_discovery.radar.display_title_for``。写しが読めなくても題名の列挙は成立する
    （fail-soft）。
    """
    keys = sorted({_clean(d) for d in document_ids if _clean(d)})
    if not keys:
        return {}, {}
    raw = load_document_titles(session, keys)
    from core.paper_discovery import radar as _radar  # 遅延 import（L層 → 発見層の表示規則のみ）

    filename_like = [doc for doc, title in raw.items() if _radar.title_looks_like_filename(title)]
    arxiv_titles: dict[str, str] = {}
    if filename_like:
        try:
            arxiv_titles = _cached_arxiv_titles(session, filename_like, raw)
        except Exception:  # noqa: BLE001 — 写しが読めなければ「未取得」と言うだけ（fail-soft）
            arxiv_titles = {}
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
    titles: dict[str, str] = {}
    notes: dict[str, str] = {}
    for doc, title in raw.items():
        display, note = _radar.display_title_for(title, arxiv_titles.get(doc, ""))
        titles[doc] = display
        if note:
            notes[doc] = note
    return titles, notes


def _cached_arxiv_titles(session: Any, document_ids: list[str], raw_titles: dict[str, str]) -> dict[str, str]:
    """ファイル名様の教材 → arXiv の写しの題名（``source_url`` → 教材名の順で ID を推定。API は呼ばない）。"""
    from core.paper_discovery import metadata_cache  # 遅延 import
    from core.paper_discovery.schema import arxiv_id_from_filename, normalize_arxiv_id

    rows = session.execute(
        sa_text(
            "SELECT id::text, COALESCE(source_url, '') FROM documents "
            "WHERE id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": sorted(document_ids)},
    ).fetchall()
    source_urls = {str(row[0]): _clean(row[1]) for row in rows}
    wanted: dict[str, str] = {}
    for doc in document_ids:
        arxiv_id = normalize_arxiv_id(source_urls.get(doc, "")) or arxiv_id_from_filename(
            raw_titles.get(doc, "")
        )
        if arxiv_id:
            wanted[doc] = arxiv_id
    if not wanted:
        return {}
    cached = metadata_cache.read_fresh(session, sorted(set(wanted.values())))
    out: dict[str, str] = {}
    for doc, arxiv_id in wanted.items():
        entry = cached.get(arxiv_id)
        title = _clean(getattr(entry, "title", "")) if entry is not None else ""
        if title:
            out[doc] = title
    return out



def documents_with_saved_modules(session: Any, document_ids: Iterable[str]) -> set[str]:
    """理論モジュールの保存行（live）を持つ document.id の集合（§21 C。件数は返さない）。

    空入力は SQL を発行しない。
    """
    keys = sorted({_clean(d) for d in document_ids if _clean(d)})
    if not keys:
        return set()
    rows = session.execute(
        sa_text(
            "SELECT DISTINCT document_id::text FROM knowledge_theory_modules_live "
            "WHERE document_id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": keys},
    ).fetchall()
    return {str(row[0]) for row in rows}


def build_related_for_document(
    session: Any,
    document_id: str,
    *,
    rule_version: str,
    can_view: Callable[[str], bool],
    candidate_key_for: Callable[[str], str] = schema.build_structural_candidate_key,
    peer_document_id: str = "",
) -> dict:
    """DB を読んで ``core.theory_modules.related.build_related_payload`` を呼ぶ（**権限ゲートは呼び出し側の責務**）。

    ``document_id`` は ``documents.id`` の UUID 文字列（route が material_id を解決してから渡す）。
    """
    own_rows = load_live_modules(session, document_id)
    fingerprints = _related.eligible_fingerprints(own_rows, rule_version=rule_version)
    matches = (
        load_matching_modules(
            session, document_id=document_id, rule_version=rule_version, fingerprints=fingerprints
        )
        if fingerprints
        else []
    )
    decisions = (
        load_structural_decisions(session, [candidate_key_for(fp) for fp in fingerprints])
        if matches
        else {}
    )
    # 近い構造（§22 B）の材料。完全一致の照合とは独立に、他 document の外枠を読む（表示専用）。
    peer_outer_rows: list[dict] = []
    if own_rows:
        try:
            peer_outer_rows = load_peer_outer_modules(
                session, document_id=document_id, rule_version=rule_version
            )
        except Exception:  # noqa: BLE001 — 近い構造は表示専用。読めなければ出さない（fail-soft）
            peer_outer_rows = []
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
    near_documents = _related.near_structure_peer_documents(
        own_rows, peer_outer_rows, document_id=document_id, rule_version=rule_version
    )
    visible_documents = [
        doc
        for doc in [match.get("document_id") for match in matches] + near_documents
        if doc and can_view(doc)
    ]
    titles, title_notes = (
        load_display_titles(session, visible_documents) if visible_documents else ({}, {})
    )
    # 照合の相手として名指しされた論文（閲覧できるものだけ）の保存の有無（§21 C）。
    unsaved_peer_titles: tuple[str, ...] = ()
    peer = _clean(peer_document_id)
    if own_rows and peer and peer != document_id and can_view(peer):
        if peer not in documents_with_saved_modules(session, [peer]):
            peer_title = load_document_titles(session, [peer]).get(peer, "")
            unsaved_peer_titles = (peer_title,)
    return _related.build_related_payload(
        document_id,
        own_rows=own_rows,
        matches=matches,
        decisions=decisions,
        rule_version=rule_version,
        can_view=can_view,
        titles=titles,
        candidate_key_for=candidate_key_for,
        unsaved_peer_titles=unsaved_peer_titles,
        peer_outer_rows=peer_outer_rows,
        title_notes=title_notes,
    )
