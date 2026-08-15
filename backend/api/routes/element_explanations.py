"""要素説明（``element_explanations``）の承認 API（Phase 2, migration 056）。

正本: ``docs/features/hierarchical_context_explanation_design.md`` §5.2。

パイプライン（ContextualExplanationAgent 等・後続実装）が書く candidate を教員が
確認・承認・却下・編集するための最小 API。DELETE endpoint は作らない（P4。行削除は
document 削除経路の明示 DELETE に将来同乗させる想定 — 本エージェントのスコープ外。
「未配線」のまま報告する）。

セッション管理は ``core.atlas_store`` 系ルータと同型: ``core.element_explanations`` の
各関数は渡された session に対して素の SQL を発行するだけでコミット/ロールバックしない
ため、ここ（route 層）で1トランザクションとして束ねる。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from dependencies import _require_teacher
from core import element_explanations as store
from core import teacher_triage
from core.deliberation.context_lens import _claim_id_lookup, _component_id_lookup
from core.deliberation.identity_links import confidence_label
from core.deliberation.refs import document_run_artifacts
from core.discuss.authoring import compute_source_fingerprint, has_fingerprint_source
from core.postgres import get_session
from core.schema import AUDIT_ENTITY_ELEMENT_EXPLANATION
from routes.theory_components import _ensure_document_editable, _ensure_document_viewable
from services import record_review_event


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["element-explanations"])

# 一括承認/却下の1回あたり上限（正規化後の explanation_ids 件数）。
BULK_REVIEW_MAX_ITEMS = 200

# 鮮度（``discuss_opening_authoring_design.md`` §7.1）: 開幕素材
# （``element_type='document'`` / ``role='discussion_seed'``）の生成時に保存した
# ``evidence.source_fingerprint`` と、現在の解析結果の指紋が食い違っている状態を示す
# 事実文。**自動で非承認に落とさない** — 学習者に出ていたものが黙って消えるほうが
# 有害なので、``approved`` の行にも印だけを付けてレビューキューで見えるようにする。
STALE_NOTICE = "元の解析結果が変わっています"

_BULK_ACTION_TO_STATUS = {
    "approve": store.STATUS_APPROVED,
    "dismiss": store.STATUS_DISMISSED,
}


# ---------------------------------------------------------------------------
# 負荷順トリアージ（宣言された弁, 教員支援 Phase 4 §2 — 実体は core/teacher_triage.py）
# ---------------------------------------------------------------------------


def _validate_sort(value: str | None, *, param: str) -> None:
    """``sort`` / ``sort_order`` の語彙検証（``default`` | ``load``。不正値は 422）。"""
    if value is not None and value not in teacher_triage.SORT_ORDERS:
        raise HTTPException(
            status_code=422,
            detail=f"invalid {param}: {value!r} (must be one of {list(teacher_triage.SORT_ORDERS)})",
        )


def _apply_load_sort(document_id: str, rows: list[dict]) -> list[dict]:
    """sort=load: 各行に ``load_level`` / ``load_level_label`` を付与し負荷降順で返す。

    - element_id は claim / component が agent 側 ID のため、``context_lens`` の
      一括変換器（§5 精査③）で DB UUID に解決してから台帳を引く。equation はそのまま、
      figure / document スコープは導出不能（末尾 + 正直な縮退ラベル）。
    - 台帳読みはバッチ1クエリ + ``load_percentiles`` をキューにつき1回（§5 精査②）。
    - 索引・台帳の読み失敗は 500 にせず全件を導出不能として返す（並べ替えは計器であり
      レビューキュー自体を止めない）。生値は付与しない（TT2）。
    """
    claim_lookup: dict[str, str] = {}
    component_lookup: dict[str, str] = {}
    try:
        if any(r.get("element_type") == store.ELEMENT_TYPE_CLAIM for r in rows):
            claim_lookup = _claim_id_lookup(document_id)
        if any(r.get("element_type") == store.ELEMENT_TYPE_COMPONENT for r in rows):
            component_lookup = _component_id_lookup(document_id)
    except Exception:  # noqa: BLE001 — 索引失敗は導出不能扱いに縮退（キューを止めない）
        logger.warning(
            "load sort: agent-id lookup failed for document %s", document_id, exc_info=True
        )

    def _target(row: dict) -> tuple[str, str] | None:
        return teacher_triage.explanation_target_for_row(row, claim_lookup, component_lookup)

    levels: dict[tuple[str, str], str] = {}
    try:
        session = get_session()
        try:
            levels = teacher_triage.load_levels_for_targets(
                session, [_target(r) for r in rows]
            )
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — 台帳読み失敗も導出不能扱いに縮退
        logger.warning(
            "load sort: ledger batch read failed for document %s", document_id, exc_info=True
        )

    return teacher_triage.annotate_and_sort_by_load(rows, levels, target_for_item=_target)


def _canonical_document_id(chunks: list[dict], fallback: str) -> str:
    for chunk in chunks:
        if chunk.get("document_id"):
            return str(chunk["document_id"])
    return fallback


def _public_row(row: dict, *, stale: bool | None = None) -> dict:
    """API 応答用に confidence 生値を除去した1件を返す（E6: 段階ラベルのみ）。

    ``role``（migration 062）は素材の役割そのものなので常に返す（既存4要素型の行は
    ``None``＝二層説明の説明本文）。``stale`` は鮮度判定ができたときだけ渡され、
    真のときは事実文 ``stale_notice`` を添える（判定不能なら両キーとも付けない —
    「鮮度不明」と「新鮮」を同じ形で返さない）。
    """
    evidence = dict(row.get("evidence") or {})
    confidence_raw = evidence.pop("confidence", None)
    evidence["confidence_label"] = confidence_label(confidence_raw)
    public = {
        "id": row.get("id"),
        "document_id": row.get("document_id"),
        "element_type": row.get("element_type"),
        "element_id": row.get("element_id"),
        "kind": row.get("kind"),
        "role": row.get("role"),
        "body": row.get("body"),
        "evidence": evidence,
        "status": row.get("status"),
        "created_by": row.get("created_by"),
        "reviewed_by": row.get("reviewed_by"),
        "reviewed_at": row.get("reviewed_at"),
        "created_at": row.get("created_at"),
    }
    if stale is not None:
        public["stale"] = bool(stale)
        if stale:
            public["stale_notice"] = STALE_NOTICE
    return public


# ---------------------------------------------------------------------------
# 鮮度（設計書 §7.1）: document スコープの開幕素材だけを対象にする
# ---------------------------------------------------------------------------


def _is_freshness_tracked(row: dict) -> bool:
    """鮮度突合の対象行か（開幕素材のみ。既存4要素型の説明本文は対象外）。"""
    return (
        row.get("element_type") == store.ELEMENT_TYPE_DOCUMENT
        and row.get("role") == store.ROLE_DISCUSSION_SEED
    )


def _stored_fingerprint(row: dict) -> str:
    evidence = row.get("evidence")
    if not isinstance(evidence, dict):
        return ""
    return str(evidence.get("source_fingerprint") or "").strip()


def _current_source_fingerprint(document_id: str) -> str | None:
    """現在の解析結果の指紋。取得できなければ ``None``（fail-open）。

    artifact が読めない状況（run 無し・DB 不通等）で「変わっています」と誤って
    表示しないため、例外は握って鮮度判定そのものをスキップする。

    [D-3] 例外だけでは足りない: ``document_run_artifacts`` は run が無い場合や
    ``stage_outputs._artifacts`` が欠けている場合に**例外を投げず ``{}`` を返す**。
    ``compute_source_fingerprint({})`` は空入力の安定ハッシュを返すため、そのまま
    突合すると保存済み指紋と必ず食い違い「元の解析結果が変わっています」を誤表示する
    （①完了 run が無い ②candidate 挿入後に後続ステージが落ちて completed run が前の
    ものを指す、の両シナリオ）。指紋の素材が1つも無いときは判定不能として ``None``
    を返す（``has_fingerprint_source`` が素材の有無の正本）。
    """
    try:
        artifacts = document_run_artifacts(document_id)
        if not has_fingerprint_source(artifacts):
            logger.debug(
                "element_explanations: no fingerprint source for document %s; "
                "skipping freshness check",
                document_id,
            )
            return None
        return compute_source_fingerprint(artifacts)
    except Exception:  # noqa: BLE001
        logger.warning(
            "element_explanations: failed to compute source fingerprint for document %s",
            document_id,
            exc_info=True,
        )
        return None


def _public_rows_with_freshness(document_id: str, rows: list[dict]) -> list[dict]:
    """一覧応答（鮮度付き）。指紋計算は document 単位で高々1回（行ごとに再読しない）。"""
    needs_check = any(_is_freshness_tracked(r) and _stored_fingerprint(r) for r in rows)
    current = _current_source_fingerprint(document_id) if needs_check else None
    out: list[dict] = []
    for row in rows:
        stale: bool | None = None
        if current and _is_freshness_tracked(row):
            stored = _stored_fingerprint(row)
            if stored:
                stale = stored != current
        out.append(_public_row(row, stale=stale))
    return out


@router.get("/documents/{document_id}/element-explanations")
def list_document_element_explanations(
    document_id: str,
    element_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    role: str | None = Query(default=None),
    sort: str = Query(default=teacher_triage.SORT_DEFAULT),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """document 内の説明一覧（レビューキューの供給元）。

    ``element_type='document'``（開幕素材, migration 062）の行も同じ一覧に含まれる。
    開幕素材には鮮度（``stale`` / ``stale_notice``、設計書 §7.1）を付ける — 対象は
    ``candidate`` だけでなく ``approved`` も含む（承認済みでも元の解析結果が変わった
    ことをレビューキューで見えるようにする。自動で非承認へは落とさない）。

    ``sort=load``（負荷順トリアージ, 教員支援 Phase 4 §2）: D層台帳の負荷段階
    （低/中/高/最高位）で降順に並べ替え、各行に段階ラベルを付ける。**既定
    （``default``）は従来順・応答形も完全に不変**（TT1: 沈黙の並べ替えを作らない）。
    """
    _validate_sort(sort, param="sort")
    chunks = _ensure_document_viewable(document_id, current_user)
    canonical_document_id = _canonical_document_id(chunks, document_id)
    session = get_session()
    try:
        rows = store.list_for_document(
            session,
            canonical_document_id,
            element_type=element_type,
            status=status,
            kind=kind,
            role=role,
        )
    finally:
        session.close()
    explanations = _public_rows_with_freshness(canonical_document_id, rows)
    if sort == teacher_triage.SORT_LOAD:
        return {
            "explanations": _apply_load_sort(canonical_document_id, explanations),
            "sort": teacher_triage.SORT_LOAD,
        }
    return {"explanations": explanations}


@router.post("/element-explanations/{explanation_id}/approve")
def approve_element_explanation(
    explanation_id: str,
    sort_order: str | None = Query(default=None),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """承認。``sort_order``（``default`` | ``load``）は「どの並び順の下で確定したか」の
    来歴を監査 metadata に残すための申告（TT3。未指定なら metadata に載せない — 偽装しない）。"""
    _validate_sort(sort_order, param="sort_order")
    session = get_session()
    try:
        existing = store.get_by_id(session, explanation_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Element explanation not found")
        _ensure_document_editable(existing["document_id"], current_user)
        updated = store.approve(session, explanation_id, current_user.get("id"))
        if updated is None:
            raise HTTPException(status_code=404, detail="Element explanation not found")
        session.commit()
    except store.ElementExplanationError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    record_review_event(
        AUDIT_ENTITY_ELEMENT_EXPLANATION,
        explanation_id,
        existing.get("status", ""),
        "approved",
        current_user.get("id"),
        teacher_triage.sort_metadata(
            {
                "action": "element_explanation.approve",
                "document_id": existing.get("document_id"),
                "element_type": existing.get("element_type"),
                "element_id": existing.get("element_id"),
                "kind": existing.get("kind"),
            },
            sort_order,
        ),
    )
    return {"explanation": _public_row(updated)}


@router.post("/element-explanations/{explanation_id}/dismiss")
def dismiss_element_explanation(
    explanation_id: str,
    sort_order: str | None = Query(default=None),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """却下（保持）。``sort_order`` の意味は approve と同じ（TT3）。"""
    _validate_sort(sort_order, param="sort_order")
    session = get_session()
    try:
        existing = store.get_by_id(session, explanation_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Element explanation not found")
        _ensure_document_editable(existing["document_id"], current_user)
        updated = store.dismiss(session, explanation_id, current_user.get("id"))
        if updated is None:
            raise HTTPException(status_code=404, detail="Element explanation not found")
        session.commit()
    except store.ElementExplanationError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    record_review_event(
        AUDIT_ENTITY_ELEMENT_EXPLANATION,
        explanation_id,
        existing.get("status", ""),
        "dismissed",
        current_user.get("id"),
        teacher_triage.sort_metadata(
            {
                "action": "element_explanation.dismiss",
                "document_id": existing.get("document_id"),
                "element_type": existing.get("element_type"),
                "element_id": existing.get("element_id"),
                "kind": existing.get("kind"),
            },
            sort_order,
        ),
    )
    return {"explanation": _public_row(updated)}


class ElementExplanationBulkReview(BaseModel):
    action: str
    explanation_ids: list[str]
    # どの並び順の下で一括確定したかの来歴申告（TT3。任意・未指定は監査に載せない）。
    sort_order: str | None = None


@router.post("/documents/{document_id}/element-explanations/bulk-review")
def bulk_review_element_explanations(
    document_id: str,
    body: ElementExplanationBulkReview,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """一括承認・一括却下（``candidate → approved/dismissed``）。教員のレビュー負荷軽減用。

    candidate-only 原則は維持する（``core.element_explanations.bulk_transition`` 参照）。
    1件の競合や不正な id が混ざっていても全体を失敗させない部分成功セマンティクスで、
    遷移できなかった行は ``skipped`` に理由付きで正直に返す（P4）。DELETE endpoint は
    作らない。
    """
    new_status = _BULK_ACTION_TO_STATUS.get(body.action)
    if new_status is None:
        raise HTTPException(
            status_code=422,
            detail=f"invalid action: {body.action!r} (must be 'approve' or 'dismiss')",
        )
    _validate_sort(body.sort_order, param="sort_order")

    ids: list[str] = []
    seen: set[str] = set()
    for raw in body.explanation_ids or []:
        normalized = str(raw or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ids.append(normalized)
    if not ids:
        raise HTTPException(status_code=422, detail="explanation_ids is required")
    if len(ids) > BULK_REVIEW_MAX_ITEMS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"too many explanation_ids: {len(ids)} exceeds limit "
                f"{BULK_REVIEW_MAX_ITEMS}"
            ),
        )

    chunks = _ensure_document_editable(document_id, current_user)
    canonical_document_id = _canonical_document_id(chunks, document_id)

    session = get_session()
    try:
        result = store.bulk_transition(
            session,
            canonical_document_id,
            ids,
            new_status=new_status,
            user_id=current_user.get("id"),
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    action_label = f"element_explanation.{body.action}"
    for row in result["updated"]:
        record_review_event(
            AUDIT_ENTITY_ELEMENT_EXPLANATION,
            row.get("id"),
            "candidate",
            row.get("status", ""),
            current_user.get("id"),
            teacher_triage.sort_metadata(
                {
                    "action": action_label,
                    "document_id": row.get("document_id"),
                    "element_type": row.get("element_type"),
                    "element_id": row.get("element_id"),
                    "kind": row.get("kind"),
                    "bulk": True,
                },
                body.sort_order,
            ),
        )

    return {
        "updated": [_public_row(r) for r in result["updated"]],
        "skipped": result["skipped"],
    }


class ElementExplanationBodyPatch(BaseModel):
    body: str


@router.patch("/element-explanations/{explanation_id}")
def update_element_explanation_body(
    explanation_id: str,
    patch: ElementExplanationBodyPatch,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """本文を編集する。旧行は ``superseded`` に遷移し、新 revision 行を作る
    （``core.element_explanations.update_body`` 参照。履歴保持・P4）。"""
    session = get_session()
    try:
        existing = store.get_by_id(session, explanation_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Element explanation not found")
        _ensure_document_editable(existing["document_id"], current_user)
        new_row = store.update_body(
            session, explanation_id, current_user.get("id"), patch.body,
        )
        if new_row is None:
            raise HTTPException(status_code=404, detail="Element explanation not found")
        session.commit()
    except store.ElementExplanationError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    record_review_event(
        AUDIT_ENTITY_ELEMENT_EXPLANATION,
        new_row.get("id", explanation_id),
        existing.get("status", ""),
        new_row.get("status", ""),
        current_user.get("id"),
        {
            "action": "element_explanation.edit",
            "document_id": existing.get("document_id"),
            "element_type": existing.get("element_type"),
            "element_id": existing.get("element_id"),
            "kind": existing.get("kind"),
            "superseded_id": explanation_id,
        },
    )
    return {"explanation": _public_row(new_row)}
