"""知識ランドスケープ — ``landscape_placements``（migration 065）の DB プリミティブ。

正本: ``docs/features/knowledge_landscape_design.md`` §5 / §8。

``core/atlas_store.py`` / ``core/element_explanations.py`` と同じ流儀:

- セッションは **呼び出し側が管理する**（``core/postgres.get_session()`` + try/finally）。
  本モジュールは commit / rollback / close を行わない — 呼び出し側（API 層・
  パイプライン）が1トランザクションとして束ねる。
- 第1引数は必ず ``session``。SQL は ``sqlalchemy.text`` で書き、ORM を使わない。
- FastAPI / services / LLM SDK に依存しない（開発ルール2）。

不変条項（§0）:

- **LS3**: AI 由来の行は常に ``status='inferred'``。``confirmed`` / ``rejected`` /
  ``review_required`` へ動かせるのは :func:`update_status`（教員の明示操作）だけで、
  再解析（:func:`supersede_and_insert_candidates`）は ``inferred`` 行しか
  ``superseded`` にしない。
- **P4 情報を落とさない**: 本モジュールに DELETE 文は無い（行削除 API を作らない）。
  却下は ``rejected``、再解析での置き換えは ``superseded`` の状態遷移で保持する。
  孤児掃除は ``documents(id) ON DELETE CASCADE``（migration 065）に委ねる。
- **LS5 数値を見せない**: ``weight`` は DB 界面（本モジュールの戻り値 dict）までで、
  API 応答へは :mod:`core.landscape.projection` が段階ラベルへ変換して載せる。
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import text as sa_text

from core.landscape import schema

_COLUMNS_SQL = """
    id::text, document_id::text, domain_key, skeleton_version, node_id, node_kind,
    perspective, weight, reason, evidence, status, provenance, run_id::text,
    created_by, reviewed_by::text, reviewed_at, review_note, created_at, updated_at
"""


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _row_to_dict(row: Any) -> dict:
    """DB 行 → dict（列順は :data:`_COLUMNS_SQL` と一致させること）。

    ``weight`` は DB 界面としてそのまま載せる（LS5 の適用点は API DTO 側 =
    :mod:`core.landscape.projection`）。
    """
    return {
        "id": str(row[0]),
        "document_id": str(row[1]) if row[1] is not None else "",
        "domain_key": row[2] or "",
        "skeleton_version": row[3] or "",
        "node_id": row[4] or "",
        "node_kind": row[5] or schema.NODE_KIND_REGION,
        "perspective": row[6] or "",
        "weight": float(row[7]) if row[7] is not None else schema.DEFAULT_WEIGHT,
        "reason": row[8] or "",
        "evidence": _json_list(row[9]),
        "status": row[10] or "",
        "provenance": row[11] or "",
        "run_id": row[12] or None,
        "created_by": row[13] or "",
        "reviewed_by": row[14] or None,
        "reviewed_at": _iso(row[15]),
        "review_note": row[16] or "",
        "created_at": _iso(row[17]) or "",
        "updated_at": _iso(row[18]) or "",
    }


# ---------------------------------------------------------------------------
# 候補の投入（パイプライン / 教員の手動再提案。再解析セマンティクス §5）
# ---------------------------------------------------------------------------


def _normalize_evidence(raw: Any) -> list[dict]:
    """evidence を ``[{"quote": str, "claim_id": str|null}]`` に正規化する（§5）。

    素の文字列も quote として受ける。それ以外のキーは落とす（DB の形を一定に保ち、
    confidence 等の生数値が evidence 経由で DTO へ漏れる経路を作らない — LS5）。
    """
    items: list[dict] = []
    for entry in raw or []:
        if isinstance(entry, dict):
            quote = str(entry.get("quote") or "").strip()
            claim_id_raw = entry.get("claim_id")
            claim_id = str(claim_id_raw).strip() if claim_id_raw else ""
        else:
            quote = str(entry or "").strip()
            claim_id = ""
        if not quote and not claim_id:
            continue
        items.append({"quote": quote, "claim_id": claim_id or None})
    return items


def _validate_candidate(raw: Any) -> dict:
    """1候補を検証・正規化する（agent の validator を通った後の最終ガード）。

    語彙外・必須欠落は ``ValueError``（fail-closed）。``weight`` だけは
    :func:`schema.normalize_weight` でクランプする（生値は DB 内部にしか出ず、
    表示は段階ラベルのため、破棄よりクランプの方が情報を落とさない）。
    """
    item = raw if isinstance(raw, dict) else {}
    domain_key = str(item.get("domain_key") or "").strip()
    if not domain_key:
        raise ValueError("domain_key is required")
    node_id = str(item.get("node_id") or "").strip()
    if not node_id:
        raise ValueError("node_id is required")
    node_kind = str(item.get("node_kind") or schema.NODE_KIND_REGION).strip()
    if not schema.is_valid_node_kind(node_kind):
        raise ValueError(f"invalid node_kind: {node_kind!r}")
    perspective = str(item.get("perspective") or "").strip()
    if not schema.is_valid_perspective(perspective):
        raise ValueError(f"invalid perspective: {perspective!r}")
    provenance = str(item.get("provenance") or schema.PROVENANCE_LLM).strip()
    if not schema.is_valid_provenance(provenance):
        raise ValueError(f"invalid provenance: {provenance!r}")
    return {
        "domain_key": domain_key,
        "skeleton_version": str(item.get("skeleton_version") or ""),
        "node_id": node_id,
        "node_kind": node_kind,
        "perspective": perspective,
        "weight": schema.normalize_weight(item.get("weight")),
        "reason": str(item.get("reason") or "").strip(),
        "evidence": _normalize_evidence(item.get("evidence")),
        "provenance": provenance,
        "created_by": str(item.get("created_by") or "").strip()
        or schema.PIPELINE_CREATED_BY,
    }


def supersede_and_insert_candidates(
    session: Any,
    document_id: str,
    run_id: str | None,
    candidates: Iterable[dict],
) -> dict:
    """再解析セーフに配置候補を投入する（設計書 §5 の再解析セマンティクス・LS3）。

    手順:

    1. 当該 document の ``status='inferred'`` 行を全て ``superseded`` に遷移させる
       （``confirmed`` / ``rejected`` / ``review_required`` には触れない）。
    2. 新候補のうち、生きている行（= 手順1の後に残っている教員判断済みの行）と
       ``(domain_key, node_id, perspective)`` が衝突するものは **挿入しない**
       （教員の判断を AI が上書きしない）。
    3. 残りを ``status='inferred'`` で挿入する。

    ``candidates`` の要素は
    ``{domain_key, skeleton_version?, node_id, node_kind?, perspective, weight?,
    reason?, evidence?, provenance?, created_by?}``。同一バッチ内の重複キーは
    先勝ちで1件だけ挿入する（部分一意インデックス ``uq_landscape_placements_live``
    への違反を事前に防ぐ）。

    ``candidates`` が空のときは **DB を一切触らず** ゼロ件を返す（fail-safe）。
    「解析は走ったがどの領域にも置けなかった」ケース（LS10）は行ではなく
    ステージ payload の ``unplaced_domains`` として記録する契約なので、空入力で
    既存の候補を消しに行かない。

    戻り値: ``{"created": int, "superseded": int, "skipped_existing": int,
    "created_ids": [str]}``（``created`` / ``superseded`` / ``skipped_existing``
    は件数。ステージ payload にそのまま載せられる）。
    """
    validated = [_validate_candidate(raw) for raw in (candidates or [])]
    if not validated:
        return {"created": 0, "superseded": 0, "skipped_existing": 0, "created_ids": []}

    superseded_rows = session.execute(
        sa_text(
            """
            UPDATE landscape_placements
               SET status = :superseded, updated_at = now()
             WHERE document_id = CAST(:document_id AS uuid)
               AND status = ANY(:supersedable)
            RETURNING id::text
            """
        ),
        {
            "document_id": document_id,
            "superseded": schema.STATUS_SUPERSEDED,
            "supersedable": list(schema.SUPERSEDABLE_STATUSES),
        },
    ).fetchall()

    live_rows = session.execute(
        sa_text(
            """
            SELECT domain_key, node_id, perspective FROM landscape_placements
             WHERE document_id = CAST(:document_id AS uuid)
               AND status <> :superseded
            """
        ),
        {"document_id": document_id, "superseded": schema.STATUS_SUPERSEDED},
    ).fetchall()
    live_keys = {(str(r[0] or ""), str(r[1] or ""), str(r[2] or "")) for r in live_rows}

    created_ids: list[str] = []
    skipped_existing = 0
    batch_keys: set[tuple[str, str, str]] = set()
    for item in validated:
        key = (item["domain_key"], item["node_id"], item["perspective"])
        if key in live_keys or key in batch_keys:
            skipped_existing += 1
            continue
        batch_keys.add(key)
        row = session.execute(
            sa_text(
                f"""
                INSERT INTO landscape_placements (
                    document_id, domain_key, skeleton_version, node_id, node_kind,
                    perspective, weight, reason, evidence, status, provenance,
                    run_id, created_by
                ) VALUES (
                    CAST(:document_id AS uuid), :domain_key, :skeleton_version, :node_id,
                    :node_kind, :perspective, :weight, :reason, CAST(:evidence AS jsonb),
                    :status, :provenance, CAST(:run_id AS uuid), :created_by
                )
                RETURNING {_COLUMNS_SQL}
                """
            ),
            {
                "document_id": document_id,
                "domain_key": item["domain_key"],
                "skeleton_version": item["skeleton_version"],
                "node_id": item["node_id"],
                "node_kind": item["node_kind"],
                "perspective": item["perspective"],
                "weight": item["weight"],
                "reason": item["reason"],
                "evidence": json.dumps(item["evidence"], ensure_ascii=False),
                # LS3: AI 由来の投入は必ず inferred（confirmed を書く経路を作らない）。
                "status": schema.STATUS_INFERRED,
                "provenance": item["provenance"],
                "run_id": run_id or None,
                "created_by": item["created_by"],
            },
        ).fetchone()
        if row is not None:
            created_ids.append(_row_to_dict(row)["id"])

    return {
        "created": len(created_ids),
        "superseded": len(superseded_rows),
        "skipped_existing": skipped_existing,
        "created_ids": created_ids,
    }


# ---------------------------------------------------------------------------
# 読み出し
# ---------------------------------------------------------------------------


def get_placement(session: Any, placement_id: str) -> dict | None:
    """1行を id で取得する（無ければ ``None`` = 呼び出し側は 404）。"""
    if not str(placement_id or "").strip():
        return None
    row = session.execute(
        sa_text(
            f"SELECT {_COLUMNS_SQL} FROM landscape_placements "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": placement_id},
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_for_document(
    session: Any, document_id: str, include_history: bool = False
) -> list[dict]:
    """1 document の配置一覧。

    既定は生きている行のみ（``superseded`` を除く）。``include_history=True`` で
    履歴（``superseded``）も含める（教員モーダルの「履歴を表示」用。P4 の可視化）。
    """
    if not str(document_id or "").strip():
        return []
    clauses = ["document_id = CAST(:document_id AS uuid)"]
    params: dict[str, Any] = {"document_id": document_id}
    if not include_history:
        clauses.append("status <> :superseded")
        params["superseded"] = schema.STATUS_SUPERSEDED
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_COLUMNS_SQL} FROM landscape_placements
             WHERE {" AND ".join(clauses)}
             ORDER BY domain_key, node_id, perspective, created_at DESC
            """
        ),
        params,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_for_documents(
    session: Any, document_ids: Iterable[str], *, statuses: Iterable[str]
) -> list[dict]:
    """複数 document の配置を status で絞って返す（学習者 API / overview 用）。

    ``document_ids`` または ``statuses`` が空なら **SQL を発行せず** ``[]`` を返す
    （fail-closed。「空集合 = 全件」に転ばせない — LS6）。
    """
    ids = [str(d).strip() for d in (document_ids or []) if str(d or "").strip()]
    status_list = [str(s).strip() for s in (statuses or []) if str(s or "").strip()]
    if not ids or not status_list:
        return []
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_COLUMNS_SQL} FROM landscape_placements
             WHERE document_id::text = ANY(:ids)
               AND status = ANY(:statuses)
             ORDER BY domain_key, node_id, perspective, created_at DESC
            """
        ),
        {"ids": ids, "statuses": status_list},
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 状態遷移（教員の明示操作のみ・LS3）
# ---------------------------------------------------------------------------


def update_status(
    session: Any,
    placement_id: str,
    new_status: str,
    *,
    reviewer_id: str,
    note: str = "",
) -> dict | None:
    """配置の status を遷移させる（教員の確認 / 却下 / 再検討）。

    許可される遷移は :data:`schema.LIVE_STATUSES` の内側だけ:

    - ``new_status`` が live 語彙以外（``superseded`` や未知の値）→ ``ValueError``
    - 対象行が既に ``superseded``（履歴）→ ``ValueError``（復帰させない）
    - 対象行が存在しない → ``None``（呼び出し側は 404）

    ``provenance`` は触らない（生成手段の記録であり、教員の確認は ``status`` と
    ``reviewed_by`` / ``reviewed_at`` で表す）。``note`` は空文字なら既存の
    ``review_note`` を保持する（P4: 状態だけ変えたときに理由文を消さない）。
    """
    if not str(reviewer_id or "").strip():
        raise ValueError("reviewer_id is required")
    if new_status not in schema.LIVE_STATUSES:
        raise ValueError(
            f"invalid status transition target: {new_status!r} "
            f"(must be one of {schema.LIVE_STATUSES!r})"
        )

    existing = get_placement(session, placement_id)
    if existing is None:
        return None
    if existing["status"] == schema.STATUS_SUPERSEDED:
        raise ValueError(
            "cannot transition a superseded placement (history is immutable)"
        )

    row = session.execute(
        sa_text(
            f"""
            UPDATE landscape_placements
               SET status = :new_status,
                   reviewed_by = CAST(:reviewer_id AS uuid),
                   reviewed_at = now(),
                   review_note = CASE WHEN :note <> '' THEN :note ELSE review_note END,
                   updated_at = now()
             WHERE id = CAST(:id AS uuid) AND status <> :superseded
            RETURNING {_COLUMNS_SQL}
            """
        ),
        {
            "id": placement_id,
            "new_status": new_status,
            "reviewer_id": reviewer_id,
            "note": str(note or ""),
            "superseded": schema.STATUS_SUPERSEDED,
        },
    ).fetchone()
    if row is None:
        # 直前の SELECT と UPDATE の間に別リクエストが superseded にした（競合）。
        raise ValueError("concurrent update detected on landscape placement")
    return _row_to_dict(row)


__all__ = [
    "get_placement",
    "list_for_document",
    "list_for_documents",
    "supersede_and_insert_candidates",
    "update_status",
]
