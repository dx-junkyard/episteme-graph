"""既存行への stable_key バックフィル（knowledge_objects_design.md §5.6）。

`main.py` の lifespan から migration 適用後に **fail-open で 1 回**呼ぶ。対象は
``stable_key IS NULL`` かつ ``superseded_at IS NULL`` の live 行だけで、冪等
（2 回目は対象ゼロ）。

agent 側と同じ材料（evidence の block_id 集合・operation）が旧行には揃わないため、
ここで付くのは**近似キー**である。label / 本文が変わらなければ次の再解析で agent 側の
計算結果と一致し、その行は UUID を保ったまま更新される（KO3）。一致しなければ旧行は
superseded になり、行そのものは残る（KO3 / P4）。

FastAPI / LLM を import しない。``session`` は呼び出し側が開閉・commit する。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import text as sa_text

from .stable_key import claim_stable_key, component_stable_key, dedupe_stable_keys

logger = logging.getLogger(__name__)

#: 1 回の起動で処理する上限（巨大 DB で起動を止めないための安全弁。残りは次回起動で処理する）。
MAX_ROWS_PER_RUN = 20000


# ---------------------------------------------------------------------------
# 衝突解消
# ---------------------------------------------------------------------------


def _assign_keys(
    rows: list[dict[str, Any]],
    *,
    taken: dict[str, set[str]],
) -> dict[str, str]:
    """``{row_id: stable_key}`` を決定論的に決める。

    ①同一 document 内の新規割り当て同士の衝突は ``dedupe_stable_keys``（id 昇順で
    ``#2`` …）で解く ②さらに既存 live 行が同じキーを持っていれば（部分一意索引に
    当たるため）空いている ``#n`` まで送る。
    """
    by_document: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_document.setdefault(str(row["document_id"] or ""), []).append(row)

    assigned: dict[str, str] = {}
    for document_id, group in by_document.items():
        occupied = set(taken.get(document_id, set()))
        base = dedupe_stable_keys(
            group,
            key_of=lambda item: item["stable_key"],
            agent_id_of=lambda item: item["id"],
        )
        for row in sorted(group, key=lambda item: item["id"]):
            key = base.get(row["id"], row["stable_key"])
            if key in occupied:
                root = key.split("#", 1)[0]
                suffix = 2
                while f"{root}#{suffix}" in occupied:
                    suffix += 1
                key = f"{root}#{suffix}"
            occupied.add(key)
            assigned[row["id"]] = key
    return assigned


def _taken_keys(session, table: str, document_ids: Iterable[str]) -> dict[str, set[str]]:
    documents = sorted({str(d or "") for d in document_ids})
    if not documents:
        return {}
    rows = session.execute(
        sa_text(
            f"""
            SELECT document_id::text AS document_id, stable_key
              FROM {table}
             WHERE stable_key IS NOT NULL
               AND superseded_at IS NULL
               AND document_id = ANY(CAST(:documents AS uuid[]))
            """
        ),
        {"documents": documents},
    ).mappings().all()
    out: dict[str, set[str]] = {}
    for row in rows:
        out.setdefault(str(row["document_id"] or ""), set()).add(str(row["stable_key"]))
    return out


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------


def _backfill_claims(session) -> int:
    pending = session.execute(
        sa_text(
            """
            SELECT id::text AS id,
                   document_id::text AS document_id,
                   COALESCE(NULLIF(normalized_text, ''), text) AS claim_text,
                   source_scope ->> 'block_id' AS block_id
              FROM theory_claims
             WHERE stable_key IS NULL
               AND superseded_at IS NULL
             ORDER BY id
             LIMIT :limit
            """
        ),
        {"limit": MAX_ROWS_PER_RUN},
    ).mappings().all()
    if not pending:
        return 0

    rows: list[dict[str, Any]] = []
    for row in pending:
        document_id = str(row["document_id"] or "")
        block_id = str(row["block_id"] or "")
        rows.append({
            "id": str(row["id"]),
            "document_id": document_id,
            "stable_key": claim_stable_key(
                document_id,
                str(row["claim_text"] or ""),
                [block_id] if block_id else [],
            ),
        })

    taken = _taken_keys(session, "theory_claims", (r["document_id"] for r in rows))
    assigned = _assign_keys(rows, taken=taken)

    for row_id, stable_key in sorted(assigned.items()):
        session.execute(
            sa_text(
                """
                UPDATE theory_claims
                   SET stable_key = :stable_key, updated_at = now()
                 WHERE id = CAST(:id AS uuid) AND stable_key IS NULL
                """
            ),
            {"id": row_id, "stable_key": stable_key},
        )
    return len(assigned)


# ---------------------------------------------------------------------------
# component
# ---------------------------------------------------------------------------


def _component_block_ids(session) -> dict[str, set[str]]:
    """``evidence_claims`` の claim UUID から出典 block_id 集合を引く。

    ``evidence_claims`` には agent 側 ID が混ざり得るため、UUID へキャストせず
    ``theory_claims.id::text`` と突き合わせる（引けない参照は静かに落ちる）。
    """
    rows = session.execute(
        sa_text(
            """
            SELECT c.id::text AS id, tc.source_scope ->> 'block_id' AS block_id
              FROM theory_components c
              CROSS JOIN LATERAL jsonb_array_elements_text(
                  CASE WHEN jsonb_typeof(c.evidence_claims) = 'array'
                       THEN c.evidence_claims ELSE '[]'::jsonb END
              ) AS ec(claim_ref)
              JOIN theory_claims tc ON tc.id::text = ec.claim_ref
             WHERE c.stable_key IS NULL
               AND c.superseded_at IS NULL
            """
        )
    ).mappings().all()
    out: dict[str, set[str]] = {}
    for row in rows:
        block_id = str(row["block_id"] or "")
        if block_id:
            out.setdefault(str(row["id"]), set()).add(block_id)
    return out


def _backfill_components(session) -> int:
    pending = session.execute(
        sa_text(
            """
            SELECT id::text AS id,
                   document_id::text AS document_id,
                   name,
                   agent_component_id,
                   source_scope -> 'legacy_ids' ->> 0 AS legacy_id
              FROM theory_components
             WHERE stable_key IS NULL
               AND superseded_at IS NULL
             ORDER BY id
             LIMIT :limit
            """
        ),
        {"limit": MAX_ROWS_PER_RUN},
    ).mappings().all()
    if not pending:
        return 0

    block_ids = _component_block_ids(session)
    rows: list[dict[str, Any]] = []
    agent_ids: dict[str, str] = {}
    for row in pending:
        row_id = str(row["id"])
        document_id = str(row["document_id"] or "")
        rows.append({
            "id": row_id,
            "document_id": document_id,
            # operation は旧行に無い（列は 078 で追加・既定 ''）。agent 側と同じ材料が
            # 揃わない分だけ近似キーになる（§5.6）。
            "stable_key": component_stable_key(
                document_id, str(row["name"] or ""), "", block_ids.get(row_id, set())
            ),
        })
        if not str(row["agent_component_id"] or "").strip():
            legacy_id = str(row["legacy_id"] or "").strip()
            if legacy_id:
                agent_ids[row_id] = legacy_id

    taken = _taken_keys(session, "theory_components", (r["document_id"] for r in rows))
    assigned = _assign_keys(rows, taken=taken)

    for row_id, stable_key in sorted(assigned.items()):
        session.execute(
            sa_text(
                """
                UPDATE theory_components
                   SET stable_key = :stable_key,
                       agent_component_id = COALESCE(NULLIF(agent_component_id, ''), :agent_id),
                       updated_at = now()
                 WHERE id = CAST(:id AS uuid) AND stable_key IS NULL
                """
            ),
            {"id": row_id, "stable_key": stable_key, "agent_id": agent_ids.get(row_id)},
        )
    return len(assigned)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def backfill_stable_keys(session) -> dict[str, int]:
    """live 行のうち ``stable_key`` が未設定のものへキーを付ける。

    Returns:
        ``{"claims": n, "components": n}``（この呼び出しで更新した行数）。
    """
    counts = {"claims": _backfill_claims(session), "components": _backfill_components(session)}
    if counts["claims"] or counts["components"]:
        logger.info(
            "knowledge_objects: backfilled stable_key for %d claim(s) / %d component(s)",
            counts["claims"],
            counts["components"],
        )
    return counts
