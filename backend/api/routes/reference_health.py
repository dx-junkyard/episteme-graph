"""参照の健全性の照会 API（知識の転用層 P4-3 / §6）。

実パス: ``GET /api/admin/documents/{document_id}/reference-health``
（admin 系子ルーターとして main.py から ``prefix="/api/admin"`` で直接登録される —
``routes/cartridge_shape.py`` / ``routes/course_prerequisites.py`` と同型）。

正本: ``docs/features/knowledge_transfer_design.md`` §6。

- **読み取り専用**（DB を書かない・監査記帳しない・run を作らない）。**LLM を呼ばない**（KT3）。
- 権限は TEACHER 以上 + ``_ensure_document_viewable``（KT6 fail-closed。閲覧できない教材と
  存在しない教材は同一の 404）。
- 返すのは事実文と、切れている参照の**列挙**だけ（件数バッジ・比率を作らない = T-3）。
- **既定は保存済みの事実**（``stage_outputs.reference_health``。パイプライン完了時と
  束の取り込み時に run へ残る）を読む。``?recheck=true`` のときだけその場で検査し直す
  （検査はグラフ・主張・部品・単位の全走査なので、画面を開くたびには走らせない）。
  保存が無い教材（本層より前に解析した教材）は、空の画面を返さずその場で検査する。
  再検査の結果は**保存しない**（KT5「解決済みフラグではない」）。
- どちらを返したかは ``source``（``recorded`` / ``rechecked``）で正直に出す。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from core.postgres import get_session
from core.reference_health import (
    SOURCE_RECHECKED,
    check_document_references,
    load_recorded_reference_health,
)
from dependencies import _require_teacher
from routes.theory_components import _ensure_document_viewable
from services import resolve_document_access

logger = logging.getLogger(__name__)

# main.py で prefix="/api/admin" を付けて直接登録される admin 系子ルーター（prefix なし）
router = APIRouter(tags=["ReferenceHealth"])


@router.get("/documents/{document_id}/reference-health")
def get_reference_health(
    document_id: str,
    recheck: bool = Query(False, description="true ならその場で検査し直す（保存はしない）"),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """1 論文の参照の健全性を事実として返す（§6）。

    既定は run に保存済みの検査結果。``recheck=true`` でその場の再検査。
    """
    doc_ref = str(document_id or "").strip()
    if not doc_ref:
        raise HTTPException(status_code=422, detail="教材が指定されていません。")

    # 権限 fail-closed（既存の正本ゲート。閲覧不可・不在はどちらも 404）。
    _ensure_document_viewable(doc_ref, current_user)

    # 知識表は UUID 列（KO9）。material_id 形の参照は canonical な UUID に解決してから引く。
    canonical_id = doc_ref
    try:
        access = resolve_document_access(current_user.get("id"), doc_ref)
        if access.document_id:
            canonical_id = str(access.document_id)
    except Exception:  # noqa: BLE001 — 解決できなければ元の参照のまま（core 側が fail-soft）
        logger.warning("reference health: document id resolution failed", exc_info=True)

    # ``recheck`` は HTTP 経由では bool に解決されるが、直接呼び出しでは Query の
    # 既定オブジェクト（truthy）が入る。**True のときだけ**再検査する。
    recheck_now = recheck is True

    session = get_session()
    try:
        recorded = None if recheck_now else load_recorded_reference_health(session, canonical_id)
        if recorded is not None:
            result = recorded
        else:
            # 再検査の明示か、保存済みの事実が無い教材（本層より前に解析した教材）。
            # 後者で「未確認」とだけ返すと画面が空になるので、その場で検査して返す
            # （保存はしない = KT5）。保存がある教材では全走査しないので、開くたびに
            # グラフ・主張・部品・単位を舐める負荷は消える。
            result = {**check_document_references(session, canonical_id), "source": SOURCE_RECHECKED}
    finally:
        session.close()
    return {"document_id": canonical_id, **result}
