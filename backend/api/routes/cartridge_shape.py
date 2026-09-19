"""カートリッジの形の宣言に対する「適合事実」API（概念レジストリ P3-7 / §8）。

実パス: ``GET /api/admin/cartridges/{cartridge_id}/fit?document_id=``
（admin 系子ルーターとして main.py から ``prefix="/api/admin"`` で直接登録される —
``routes/course_prerequisites.py`` / ``routes/seminar_brief.py`` と同型）。

正本: ``docs/features/concept_registry_design.md`` §8。

- **読み取り専用**（DB を書かない・監査記帳しない）。**LLM を呼ばない**（KR5）。
- 権限は TEACHER 以上 + ``_ensure_document_viewable``（KR10 fail-closed）。
- 返すのは事実文と**名前の列挙**だけ（一致件数・スコア・cosine を出さない = KR6）。
- 未知の分野は 404、材料が無い論文は 200 + 「まだ解析されていないため、適合の事実は
  ありません」（KR8: 不在は異常ではなく事実）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from core.cartridge_shape import fit_facts
from core.document_pipeline.persistence import document_run_artifacts
from core.postgres import get_session
from dependencies import _require_teacher
from routes.theory_components import _ensure_document_viewable
from services import resolve_document_access

logger = logging.getLogger(__name__)

# main.py で prefix="/api/admin" を付けて直接登録される admin 系子ルーター（prefix なし）
router = APIRouter(tags=["CartridgeShape"])


def _known_cartridge_ids() -> set[str]:
    """同梱カートリッジの ID 集合（実在しない分野の照会は 404 に落とす）。"""
    from core.cartridges import list_cartridges

    try:
        return {c.cartridge_id for c in list_cartridges()}
    except Exception:  # noqa: BLE001
        logger.warning("cartridge fit: cartridge listing failed", exc_info=True)
        return set()


@router.get("/cartridges/{cartridge_id}/fit")
def get_cartridge_fit(
    cartridge_id: str,
    document_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """分野（cartridge）と論文の「適合の事実」を返す（§8）。

    材料は採用 run（``policy="adopted"``）の artifact と ``landscape_placements``
    の live 行だけで、**新しい解析は走らせない**。アップロード直後（解析前）は
    材料が無いので「まだ解析されていない」という事実文 1 行になる。
    """
    key = str(cartridge_id or "").strip()
    if not key or key not in _known_cartridge_ids():
        raise HTTPException(status_code=404, detail="指定された分野が見つかりません。")

    doc_ref = str(document_id or "").strip()
    if not doc_ref:
        raise HTTPException(status_code=422, detail="教材が指定されていません。")

    # 権限 fail-closed（閲覧できない / 存在しない教材は同一の 404。コース経由の
    # 開示フォールバックを含む既存の正本ゲートをそのまま使う）。
    _ensure_document_viewable(doc_ref, current_user)

    # 参照は material_id 形でも来るので、知識表を引く前に canonical な UUID へ解決する
    # （KO9: document_id は UUID 列。解決できなければ元の参照のまま渡す）。
    canonical_id = doc_ref
    try:
        access = resolve_document_access(current_user.get("id"), doc_ref)
        if access.document_id:
            canonical_id = str(access.document_id)
    except Exception:  # noqa: BLE001
        logger.warning("cartridge fit: document id resolution failed", exc_info=True)

    session = get_session()
    try:
        artifacts = document_run_artifacts(canonical_id, policy="adopted", session=session)
        return fit_facts(
            session,
            cartridge_id=key,
            document_id=canonical_id,
            artifacts=artifacts or None,
        )
    finally:
        session.close()
