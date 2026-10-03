"""台帳行（``epistemic_ledger``）の作成に要る ``document_id`` の解決（D層・読み時・非LLM）。

migration 080 で ``epistemic_ledger.document_id`` は ``TEXT NOT NULL DEFAULT ''`` から
``UUID NOT NULL``（既定なし）になった。それ以前の「(target_id, target_type) だけで INSERT」は
NotNullViolation で落ちる（2026-10-03 の実機検証で判明・IK 記帳）。台帳行を新しく作る経路は
すべて ``ensure_ledger_row`` を通し、``document_id`` が渡されなければ対象から決定論で引く。

FastAPI / LLM 非 import。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text as sa_text

# 対象種別 → 所属 document を引く SQL（live ビューのみ。KO5）
_LOOKUPS: dict[str, str] = {
    "claim": "SELECT document_id::text FROM theory_claims_live WHERE id::text = :tid LIMIT 1",
    "component": "SELECT document_id::text FROM theory_components_live WHERE id::text = :tid LIMIT 1",
    "equation": (
        "SELECT document_id::text FROM knowledge_equations_live "
        "WHERE id::text = :tid OR agent_equation_id = :tid ORDER BY created_at DESC LIMIT 1"
    ),
    # assumption_nodes は document_ids JSONB（複数論文にまたがる前提）。先頭を所属とみなす。
    "assumption": (
        "SELECT (document_ids ->> 0) FROM assumption_nodes WHERE id::text = :tid LIMIT 1"
    ),
}

LEDGER_TARGET_TYPES: tuple[str, ...] = tuple(_LOOKUPS)


def resolve_target_document_id(session: Any, target_type: str, target_id: str) -> str | None:
    """対象の所属 document_id（UUID 文字列）。引けなければ None（捏造しない）。"""
    sql = _LOOKUPS.get(str(target_type or ""))
    tid = str(target_id or "").strip()
    if not sql or not tid:
        return None
    try:
        row = session.execute(sa_text(sql), {"tid": tid}).fetchone()
    except Exception:  # noqa: BLE001 — 失敗は None（呼び出し側が事実文にする）。session は戻す
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
    value = row[0] if row else None
    return str(value) if value else None


def ensure_ledger_row(
    session: Any,
    *,
    target_type: str,
    target_id: str,
    document_id: str | None = None,
    course_id: str | None = None,
) -> str:
    """台帳行を（無ければ）作る。戻り値は使った document_id。

    ``document_id`` が無ければ対象から引く。それでも無ければ ``ValueError``（日本語の事実文）。
    既存行があれば INSERT は何もしない（``ON CONFLICT DO NOTHING``）。
    """
    tid = str(target_id or "").strip()
    tt = str(target_type or "").strip()
    doc = str(document_id or "").strip() or resolve_target_document_id(session, tt, tid)
    if not doc:
        raise ValueError("この対象がどの論文に属するか引けないため、台帳の行を作れません。")
    session.execute(
        sa_text(
            """
            INSERT INTO epistemic_ledger (target_id, target_type, document_id, course_id)
            VALUES (:tid, :ttype, CAST(:doc AS uuid), :course)
            ON CONFLICT (target_id, target_type) DO NOTHING
            """
        ),
        {"tid": tid, "ttype": tt, "doc": doc, "course": str(course_id or "")},
    )
    return doc


__all__ = ["LEDGER_TARGET_TYPES", "ensure_ledger_row", "resolve_target_document_id"]
