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
                   structure_fingerprint, identity_eligible
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
        }
        for row in rows
    ]


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



def build_related_for_document(
    session: Any,
    document_id: str,
    *,
    rule_version: str,
    can_view: Callable[[str], bool],
    candidate_key_for: Callable[[str], str] = schema.build_structural_candidate_key,
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
    visible_documents = [
        match["document_id"] for match in matches if match.get("document_id") and can_view(match["document_id"])
    ]
    titles = load_document_titles(session, visible_documents) if visible_documents else {}
    return _related.build_related_payload(
        document_id,
        own_rows=own_rows,
        matches=matches,
        decisions=decisions,
        rule_version=rule_version,
        can_view=can_view,
        titles=titles,
        candidate_key_for=candidate_key_for,
    )
