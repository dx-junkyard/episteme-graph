"""再構成ループ（Reconstruction Loop, R層）API。

実パス:
  - 学習者向け: /api/learning/... （本人のみ・受講ゲート）
  - 教員向け: /api/admin/reconstruction/... と /api/admin/documents/{id}/claims/stumble-summary
    （admin.router 経由, TEACHER 以上）

設計原則（docs/features/reconstruction_loop_design.md）:
  - 実行時 DIFF は非LLM・同期（P6）。LLM はオーサリング worker のみ。
  - 判定は「仮説」、権威は出典リビール、点数は出さない（P1/P7）。
  - `next` は伏せフィールド（text / equation / evidence_text）を返さない（§3.2）。
  - item は削除せず auto → flagged → retired の状態遷移（P4）。
  - 監査: item 生成 / status 遷移 / self-check(verdict_wrong) / descend を
    theory_review_events（'reconstruction_item' / 'reconstruction_response'）に記録。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text as sa_text

from dependencies import _get_current_user, _require_teacher
from services import get_accessible_course_data, record_review_event, user_can_view_course
from core.course_data import course_source_material_ids, course_topics
from core.postgres import get_session as _pg_session
from core.reconstruction import diff as recon_diff
from core.reconstruction import item_builder
from core.reconstruction.health import get_review_queue
from core.reconstruction.stumble import get_stumble_summary
from core.reconstruction.schema import (
    APPROVED_REVIEW_STATUSES,
    DELIVERABLE_STATUSES,
    ENTITY_ITEM,
    ENTITY_RESPONSE,
    ITEM_STATUSES,
    SELF_CHECK_VALUES,
    SOURCE_BACKED,
)
from core.reconstruction.worker import run_item_authoring_for_document

logger = logging.getLogger(__name__)

# main.py で直接 include される学習者向け router
learning_router = APIRouter(prefix="/api/learning", tags=["Learning"])
# admin.router（prefix=/api/admin）に include される教員向け router（prefix なし）
admin_router = APIRouter(tags=["Reconstruction"])


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _record_recon_event(
    entity_type: str,
    entity_id: str,
    old_status: str,
    new_status: str,
    user_id: str | None,
    metadata: dict | None = None,
) -> None:
    """theory_review_events への監査記録（実体は services.record_review_event, 提案7）。"""
    record_review_event(entity_type, entity_id, old_status, new_status, user_id, metadata)


def _course_scope(session, course_data: dict, topic_id: str | None = None) -> dict:
    """course_data から claim 照合スコープ（material_ids / doc_refs / topic_chunk_ids）を作る。

    theory_claims.document_id は documents.id 由来のことが多く、コースの
    sources[].material_id（=documents.source_path）と一致しない。chunks は両方を
    持つため、claim → course のブリッジは chunks.material_id を主に使う。
    """
    material_ids = course_source_material_ids(course_data)
    doc_refs = list(material_ids)
    if material_ids:
        try:
            rows = session.execute(
                sa_text("SELECT id::text FROM documents WHERE source_path = ANY(:mids) OR id::text = ANY(:mids)"),
                {"mids": material_ids},
            ).fetchall()
            for r in rows:
                if r[0] and r[0] not in doc_refs:
                    doc_refs.append(r[0])
        except Exception:
            logger.debug("document resolution failed", exc_info=True)

    topic_chunk_ids: list[str] = []
    if topic_id:
        for t in course_topics(course_data):
            if isinstance(t, dict) and str(t.get("id")) == str(topic_id):
                topic_chunk_ids = [str(c) for c in (t.get("material_chunk_ids") or []) if str(c).strip()]
                break
    return {"material_ids": material_ids, "doc_refs": doc_refs, "topic_chunk_ids": topic_chunk_ids}


def _claim_row_to_dict(row: Any) -> dict:
    """theory_claims 行（下記 SELECT 順）を dict にする。"""
    return {
        "id": str(row[0]),
        "document_id": row[1] or "",
        "claim_type": row[2] or "",
        "text": row[3] or "",
        "normalized_text": row[4] or "",
        "concepts": _json(row[5], []),
        "equation": _json(row[6], {}),
        "source_scope": _json(row[7], {}),
        "evidence_text": row[8] or "",
        "support_status": row[9] or "",
        "review_status": row[10] or "",
    }


_CLAIM_COLS = (
    "c.id::text, c.document_id, c.claim_type, c.text, c.normalized_text, "
    "c.concepts, c.equation, c.source_scope, c.evidence_text, "
    "c.support_status, c.review_status"
)


def _fetch_item_and_claim(session, item_id: str) -> tuple[dict | None, dict | None]:
    """item + その claim を取得する（deliverable / 出題対象条件は呼び出し側で判定）。"""
    row = session.execute(
        sa_text(f"""
            SELECT i.id::text, i.claim_id::text, i.document_id, i.elicit_mode, i.prompt,
                   i.response_space, i.expected, i.author, i.status,
                   {_CLAIM_COLS}
            FROM reconstruction_items i
            JOIN theory_claims c ON c.id = i.claim_id
            WHERE i.id = CAST(:item_id AS uuid)
        """),
        {"item_id": item_id},
    ).fetchone()
    if not row:
        return None, None
    item = {
        "id": row[0],
        "claim_id": row[1],
        "document_id": row[2] or "",
        "elicit_mode": row[3] or "restate",
        "prompt": row[4] or "",
        "response_space": _json(row[5], []),
        "expected": _json(row[6], {}),
        "author": row[7] or "llm",
        "status": row[8] or "auto",
    }
    claim = _claim_row_to_dict(row[9:])
    return item, claim


def _claim_in_course_scope(session, claim_id: str, scope: dict) -> bool:
    """claim がコーススコープに属するか（cross-course 提出防止）。

    next と同じ照合（chunk.material_id ∈ course material_ids OR claim.document_id ∈ doc_refs）を
    使い、chunk 経由で配信された item が submit で弾かれないようにする。
    """
    row = session.execute(
        sa_text("""
            SELECT 1
            FROM theory_claims c
            LEFT JOIN chunks ch ON ch.id = c.chunk_id
            WHERE c.id = CAST(:claim_id AS uuid)
              AND (ch.material_id = ANY(:mids) OR c.document_id = ANY(:doc_refs))
            LIMIT 1
        """),
        {
            "claim_id": claim_id,
            "mids": scope.get("material_ids") or [""],
            "doc_refs": scope.get("doc_refs") or [""],
        },
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# 学習者向け API（本人のみ）
# ---------------------------------------------------------------------------


class ReconSubmitRequest(BaseModel):
    course_id: str
    response: dict = {}
    revision_of: str | None = None


class ReconSelfCheckRequest(BaseModel):
    result: str  # agreed | disagreed | verdict_wrong


@learning_router.get("/courses/{course_id}/topics/{topic_id}/reconstruction/next")
def get_next_item(
    course_id: str,
    topic_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """次の再構成 item を 1 件返す（伏せフィールドは返さない）。

    出題対象は status='auto'/'confirmed'、source_backed かつ承認済みの claim のみ。
    本人が未回答の item を優先する。無ければ item=None を返す。
    """
    course_data = get_accessible_course_data(current_user["id"], course_id)
    if course_data is None:
        raise HTTPException(status_code=404, detail="Course not found")

    session = _pg_session()
    try:
        scope = _course_scope(session, course_data, topic_id)
        if not scope["material_ids"] and not scope["doc_refs"]:
            return {"item": None, "exhausted": True}

        # 記号葉プローブ（elicit_mode='symbol'）は descend 経由でのみ提示する（§3.4）。
        conditions = [
            "i.status = ANY(:statuses)",
            "i.elicit_mode <> 'symbol'",
            "c.support_status = :backed",
            "c.review_status = ANY(:approved)",
            "NOT EXISTS (SELECT 1 FROM learner_reconstructions r "
            "WHERE r.item_id = i.id AND r.user_id = CAST(:uid AS uuid))",
        ]
        params: dict[str, Any] = {
            "statuses": list(DELIVERABLE_STATUSES),
            "backed": SOURCE_BACKED,
            "approved": list(APPROVED_REVIEW_STATUSES),
            "uid": current_user["id"],
        }
        params["mids"] = scope["material_ids"] or [""]
        params["doc_refs"] = scope["doc_refs"] or [""]

        def _next_row(extra_clause: str):
            return session.execute(
                sa_text(f"""
                    SELECT i.id::text, i.claim_id::text, i.document_id, i.elicit_mode, i.prompt,
                           i.response_space, i.expected, i.author, i.status,
                           {_CLAIM_COLS}
                    FROM reconstruction_items i
                    JOIN theory_claims c ON c.id = i.claim_id
                    LEFT JOIN chunks ch ON ch.id = c.chunk_id
                    WHERE {' AND '.join(conditions + [extra_clause])}
                    ORDER BY i.author_confidence DESC, i.created_at ASC
                    LIMIT 1
                """),
                params,
            ).fetchone()

        # トピック chunk に紐づく item を優先し、無ければコース全体（doc_refs）へ縮退する。
        row = None
        if scope["topic_chunk_ids"]:
            params["topic_chunks"] = scope["topic_chunk_ids"]
            row = _next_row("ch.id::text = ANY(:topic_chunks)")
        if row is None:
            row = _next_row("(ch.material_id = ANY(:mids) OR c.document_id = ANY(:doc_refs))")
    finally:
        session.close()

    if not row:
        return {"item": None, "exhausted": True}

    item = {
        "id": row[0],
        "claim_id": row[1],
        "document_id": row[2] or "",
        "elicit_mode": row[3] or "restate",
        "prompt": row[4] or "",
        "response_space": _json(row[5], []),
        "author": row[7] or "llm",
    }
    claim = _claim_row_to_dict(row[9:])
    return {"item": item_builder.public_item_view(item, claim), "exhausted": False}


def _submit_response(
    user_id: str,
    item_id: str,
    course_id: str,
    response: dict,
    revision_of: str | None,
) -> dict:
    """応答を保存 → DIFF → verdict + リビール内容を返す（同期・非LLM）。"""
    course_data = get_accessible_course_data(user_id, course_id)
    if course_data is None:
        raise HTTPException(status_code=404, detail="Course not found")
    # CAPTURE なしの REVEAL を許さない: 空応答では出典（伏せフィールド）を開示しない（§3.1）。
    if not recon_diff.response_has_content(response or {}):
        raise HTTPException(status_code=422, detail="response is required before reveal")
    if revision_of is not None:
        try:
            uuid.UUID(str(revision_of))
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid revision_of")

    session = _pg_session()
    try:
        item, claim = _fetch_item_and_claim(session, item_id)
        if not item or not claim:
            raise HTTPException(status_code=404, detail="Item not found")
        if item["status"] not in DELIVERABLE_STATUSES:
            raise HTTPException(status_code=409, detail="Item is not available")
        scope = _course_scope(session, course_data)
        if not _claim_in_course_scope(session, item["claim_id"], scope):
            raise HTTPException(status_code=404, detail="Item not found")
        if revision_of is not None:
            # 改訂元は本人の・同じ item への産出物のみ（他人の recon_id を鎖に繋げない）。
            prev = session.execute(
                sa_text("""
                    SELECT 1 FROM learner_reconstructions
                    WHERE id = CAST(:rev AS uuid) AND user_id = CAST(:uid AS uuid)
                      AND item_id = CAST(:item_id AS uuid)
                """),
                {"rev": revision_of, "uid": user_id, "item_id": item_id},
            ).fetchone()
            if not prev:
                raise HTTPException(status_code=404, detail="Reconstruction not found")

        diff_result = recon_diff.run_diff(item, claim, response or {})
        reflection = recon_diff.build_reflection(item, claim, response or {}, diff_result)

        row = session.execute(
            sa_text("""
                INSERT INTO learner_reconstructions
                (user_id, course_id, item_id, claim_id, response, machine_verdict, revision_of)
                VALUES (CAST(:uid AS uuid), :cid, CAST(:item_id AS uuid), CAST(:claim_id AS uuid),
                        CAST(:response AS jsonb), :verdict,
                        CASE WHEN :rev = '' THEN NULL ELSE CAST(:rev AS uuid) END)
                RETURNING id::text
            """),
            {
                "uid": user_id,
                "cid": course_id,
                "item_id": item_id,
                "claim_id": item["claim_id"],
                "response": json.dumps(response or {}, ensure_ascii=False),
                "verdict": diff_result.machine_verdict,
                "rev": revision_of or "",
            },
        ).fetchone()
        session.commit()
        recon_id = str(row[0]) if row else ""
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        logger.exception("reconstruction submit failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to submit reconstruction")
    finally:
        session.close()

    _record_recon_event(
        ENTITY_RESPONSE, recon_id, "", diff_result.machine_verdict, user_id,
        {"item_id": item_id, "claim_id": item["claim_id"], "elicit_mode": item["elicit_mode"],
         "revision_of": revision_of or ""},
    )
    return {"recon_id": recon_id, "reflection": reflection, "self_check_required": True}


@learning_router.post("/reconstruction/{item_id}/submit")
def submit_reconstruction(
    item_id: str,
    body: ReconSubmitRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    return _submit_response(current_user["id"], item_id, body.course_id, body.response, None)


@learning_router.post("/reconstruction/{item_id}/revise")
def revise_reconstruction(
    item_id: str,
    body: ReconSubmitRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """再挑戦（revision_of 付きの新規行を作り submit と同フロー）。"""
    return _submit_response(current_user["id"], item_id, body.course_id, body.response, body.revision_of)


@learning_router.post("/reconstruction/{recon_id}/self-check")
def self_check_reconstruction(
    recon_id: str,
    body: ReconSelfCheckRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """リビール後の 1 タップ自己確認（必須ステップ）。合っていた / 違っていた / 判定がおかしい。"""
    if body.result not in SELF_CHECK_VALUES:
        raise HTTPException(status_code=422, detail="invalid self-check result")
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                UPDATE learner_reconstructions
                SET self_check = :result, updated_at = now()
                WHERE id = CAST(:rid AS uuid) AND user_id = CAST(:uid AS uuid)
                RETURNING id::text, item_id::text
            """),
            {"result": body.result, "rid": recon_id, "uid": current_user["id"]},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("reconstruction self-check failed")
        raise HTTPException(status_code=500, detail="Failed to record self-check")
    finally:
        session.close()
    if not row:
        raise HTTPException(status_code=404, detail="Reconstruction not found")
    # verdict_wrong（機械判定への異議）は最も精度の高い故障検出器なので監査する（§3.1）。
    if body.result == "verdict_wrong":
        _record_recon_event(
            ENTITY_RESPONSE, recon_id, "", "verdict_wrong", current_user["id"],
            {"item_id": str(row[1]), "signal": "self_check_verdict_wrong"},
        )
    return {"ok": True}


@learning_router.post("/reconstruction/{recon_id}/descend")
def descend_reconstruction(
    recon_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """記号葉へ降下: SymbolRegistry 由来のプローブを返し descended_to_symbol=TRUE を記録。

    プローブは elicit_mode='symbol' の item として永続化し（claim ごとに1つ・非LLM・
    author='system'）、学習者の記述は通常の submit で「同じ器」に保存できる（§3.4）。
    symbol item は next では配信されない（descend 経由のみ）。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(f"""
                SELECT r.item_id::text, {_CLAIM_COLS}
                FROM learner_reconstructions r
                JOIN theory_claims c ON c.id = r.claim_id
                WHERE r.id = CAST(:rid AS uuid) AND r.user_id = CAST(:uid AS uuid)
            """),
            {"rid": recon_id, "uid": current_user["id"]},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Reconstruction not found")
        session.execute(
            sa_text("""
                UPDATE learner_reconstructions
                SET descended_to_symbol = TRUE, updated_at = now()
                WHERE id = CAST(:rid AS uuid) AND user_id = CAST(:uid AS uuid)
            """),
            {"rid": recon_id, "uid": current_user["id"]},
        )
        claim = _claim_row_to_dict(row[1:])
        item_id = str(row[0])
        probe = item_builder.symbol_probe(claim)

        # 既存の（非 retired）symbol item を再利用し、無ければ決定論的に生成する。
        probe_created = False
        sym = session.execute(
            sa_text("""
                SELECT id::text, prompt FROM reconstruction_items
                WHERE claim_id = CAST(:cid AS uuid) AND elicit_mode = 'symbol'
                  AND status <> 'retired'
                ORDER BY created_at ASC
                LIMIT 1
            """),
            {"cid": claim["id"]},
        ).fetchone()
        if sym:
            probe_item_id = str(sym[0])
            if sym[1]:
                probe["prompt"] = str(sym[1])
        else:
            created = session.execute(
                sa_text("""
                    INSERT INTO reconstruction_items
                    (claim_id, document_id, elicit_mode, prompt, response_space, expected,
                     claim_fields_used, author, author_confidence, status)
                    VALUES (CAST(:cid AS uuid), :doc, 'symbol', :prompt,
                            '[]'::jsonb, '{}'::jsonb, CAST(:fields AS jsonb),
                            'system', 0.0, 'auto')
                    RETURNING id::text
                """),
                {
                    "cid": claim["id"],
                    "doc": claim.get("document_id") or "",
                    "prompt": probe["prompt"],
                    "fields": json.dumps(["equation", "concepts"], ensure_ascii=False),
                },
            ).fetchone()
            probe_item_id = str(created[0]) if created else ""
            probe_created = bool(probe_item_id)
        session.commit()
        probe["item_id"] = probe_item_id
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("reconstruction descend failed")
        raise HTTPException(status_code=500, detail="Failed to descend")
    finally:
        session.close()
    _record_recon_event(
        ENTITY_RESPONSE, recon_id, "", "descended_to_symbol", current_user["id"],
        {"item_id": item_id, "claim_id": claim["id"], "probe_item_id": probe.get("item_id", "")},
    )
    if probe_created:
        _record_recon_event(
            ENTITY_ITEM, probe["item_id"], "", "auto", current_user["id"],
            {"claim_id": claim["id"], "author": "system", "mode": "symbol"},
        )
    return {"probe": probe}


# ---------------------------------------------------------------------------
# 教員向け API（TEACHER 以上）
# ---------------------------------------------------------------------------


class ItemPatchRequest(BaseModel):
    status: str | None = None            # confirmed | retired | auto | flagged
    prompt: str | None = None
    expected: dict | None = None


@admin_router.get("/reconstruction/items/review-queue")
def review_queue(
    document_id: str | None = None,
    course_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """疑わしさランク順の item 一覧（health 集計を段階ラベル / レンジで付与）。

    レビュー指摘2: 複数教材コースでは先頭教材の項目しか返らなかった欠落を解消する。
    ``course_id`` 指定時は ``document_id`` より優先し、コースの全 source 教材に
    紐づく item を集約する。権限が無い / コースが見つからない場合は 404 で
    fail-closed（``admin.py`` の既存コース閲覧ガード, admin.py の
    ``list_course_group_permissions`` と同じ規約 — 403 ではなく 404）。
    ``course_id`` 未指定時は従来どおり ``document_id`` 指定 / 無指定の挙動を維持する。
    """
    if course_id:
        if not user_can_view_course(current_user["id"], course_id):
            raise HTTPException(status_code=404, detail="Course not found")
        from services import _fetch_course_data_row

        course_data = _fetch_course_data_row(course_id)
        if course_data is None:
            raise HTTPException(status_code=404, detail="Course not found")

        session = _pg_session()
        try:
            scope = _course_scope(session, course_data)
        finally:
            session.close()
        # sources が空のコースは doc_refs も空になり、get_review_queue が
        # fail-closed で {"items": [], ...} を返す（全件フォールバックしない）。
        return get_review_queue(document_ids=scope["doc_refs"])
    return get_review_queue(document_id)


@admin_router.patch("/reconstruction/items/{item_id}")
def patch_item(
    item_id: str,
    body: ItemPatchRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """status 遷移（confirmed / retired / auto / flagged）+ prompt / expected 修正。

    削除 API は無い（retire のみ。P4）。
    """
    session = _pg_session()
    try:
        existing = session.execute(
            sa_text("SELECT status, claim_id::text FROM reconstruction_items WHERE id = CAST(:id AS uuid)"),
            {"id": item_id},
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Item not found")
        old_status = existing[0] or "auto"
        claim_id = existing[1] or ""

        sets = ["updated_at = now()"]
        params: dict[str, Any] = {"id": item_id}
        new_status = old_status
        if body.status is not None:
            if body.status not in ITEM_STATUSES:
                raise HTTPException(status_code=422, detail="invalid status")
            sets.append("status = :status")
            params["status"] = body.status
            new_status = body.status
        if body.prompt is not None:
            sets.append("prompt = :prompt")
            params["prompt"] = body.prompt
            sets.append("author = 'teacher'")
        if body.expected is not None:
            sets.append("expected = CAST(:expected AS jsonb)")
            params["expected"] = json.dumps(body.expected, ensure_ascii=False)

        session.execute(
            sa_text(f"UPDATE reconstruction_items SET {', '.join(sets)} WHERE id = CAST(:id AS uuid)"),
            params,
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("patch reconstruction item failed")
        raise HTTPException(status_code=500, detail="Failed to update item")
    finally:
        session.close()

    if new_status != old_status:
        _record_recon_event(
            ENTITY_ITEM, item_id, old_status, new_status, current_user.get("id"),
            {"claim_id": claim_id, "by": "teacher"},
        )
    return {"ok": True, "status": new_status}


@admin_router.post("/reconstruction/documents/{document_id}/author")
def author_document_items(
    document_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """手動バッチ: ドキュメントの未オーサリング claim（source_backed + 承認済み）に item を生成する。

    LLM を使うため同期呼び出し（教員操作・件数上限あり）。item 生成は解析成果への
    書き込みなのでゲートは document 編集可否（viewer=閲覧・引用 / editor=再解析等の編集）。
    """
    from routes.theory_components import _ensure_document_editable

    _ensure_document_editable(document_id, current_user)
    created = run_item_authoring_for_document(document_id)
    return {"ok": True, "created": created}


@admin_router.get("/documents/{document_id}/claims/stumble-summary")
def document_stumble_summary(
    document_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """claim 単位つまづきサマリー（4 軸・k-匿名・段階ラベル）。原稿スタジオのトグルが呼ぶ。"""
    from routes.theory_components import _ensure_document_viewable

    _ensure_document_viewable(document_id, current_user)
    return get_stumble_summary(document_id)
