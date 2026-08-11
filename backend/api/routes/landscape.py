"""知識ランドスケープ — 配置レビュー API（実パス ``/api/admin/landscape/...`` と
学習者向け ``/api/learning/courses/{course_id}/landscape``）。

正本: ``docs/features/knowledge_landscape_design.md`` §9（API 契約）。不変条項は §0:

- **LS1 地図は正解ではなく投影**: 1論文が複数領域×複数観点に置かれるのが既定。本ルータは
  配置行をそのまま並べ、どれか1件を「正しい配置」として選別しない。
- **LS2 A層非改変**: 読むのは ``landscape_placements``（W層と同じ立場の専用テーブル）と
  A層 artifact のみ。成果テーブルへは書かない。
- **LS3 確定は教員**: 生成（propose）は ``core.landscape.builder`` に一任し、本ルータは
  ``status`` を書かない。``confirmed`` / ``rejected`` / ``review_required`` への遷移は
  PATCH（教員の明示操作）だけが行い、実体は ``core.landscape.store.update_status``。
- **LS5 数値を見せない**: レスポンスは必ず ``core.landscape.projection`` の DTO を通す
  （``weight`` / ``confidence`` のキーごと出さない）。事実文にも上限値・スコアを書かない。
- **LS6 fail-closed**: document は ``services.resolve_document_access``（GET=view /
  PATCH・propose=edit）、コースは ``services.get_accessible_course_data`` でゲートする。
  不在・権限なしは **404**（403 を使わない — 既存の document 系リソースの作法に合わせる）。
  読む骨格は凍結版のみ（``atlas_store.load_learner_skeleton``）。
- **LS9 同期パスに LLM を入れない**: 読み取り系はすべて非LLM。LLM を呼ぶのは教員の明示
  操作である propose だけで、そこも ``builder`` 側の日次コストゲートを共有する。
- **P4 情報を落とさない**: DELETE ルートを作らない（却下は ``status='rejected'``、
  再解析での置換は ``superseded`` の状態遷移で保持する）。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

import services
from core import atlas_store
from core.course_data import course_cartridge_id, course_source_material_ids
from core.landscape import builder as landscape_builder
from core.landscape import projection
from core.landscape import schema as landscape_schema
from core.landscape import store as landscape_store
from core.schema import AUDIT_ENTITY_LANDSCAPE_PLACEMENT
from dependencies import ROLE_SYSTEM_ADMIN, _get_current_user, _require_teacher

logger = logging.getLogger(__name__)

# /api/admin/landscape/... （main.py から prefix="/api/admin" で直接登録する。
# CLAUDE.md: admin 系子ルーターは admin.router に include せず main.py へフラット登録）
router = APIRouter(prefix="/landscape", tags=["Landscape"])

# 学習者向け（main.py がそのまま登録する。フルパスを自前で持つ）
learning_router = APIRouter(prefix="/api/learning", tags=["Learning"])


# ---------------------------------------------------------------------------
# 事実文（LS5: 数値・上限値を書かない / LS8: 出所を正直に書く）
# ---------------------------------------------------------------------------

_DETAIL_DOCUMENT_NOT_FOUND = "Document not found"
_DETAIL_PLACEMENT_NOT_FOUND = "この配置は見つかりません。"
_DETAIL_DOMAIN_NOT_FROZEN = "この分野には凍結済みの骨格がありません。"
_DETAIL_PROPOSE_FAILED = "配置候補の生成に失敗しました。しばらく待ってから再度お試しください。"
_DETAIL_DAILY_LIMIT = "本日のAI呼び出し上限に達しました。明日以降に再実行できます。"

#: propose の skip 語彙 → HTTP 422 の事実文（§7.2 の語彙をそのまま写す）。
_PROPOSE_BLOCKING_REASONS: dict[str, str] = {
    landscape_builder.SKIPPED_REASON_NO_SKELETON: "凍結済みの分野マップ骨格がありません。",
    landscape_builder.SKIPPED_REASON_NO_MATERIAL: "この教材には配置に使える解析素材がありません。",
}


# ---------------------------------------------------------------------------
# セッション・権限ゲート
# ---------------------------------------------------------------------------


def _session():
    """配置テーブル用の DB セッション。取得不能は 503（永続化・読み出しが本体）。"""
    try:
        from core.postgres import get_session

        return get_session()
    except Exception as exc:  # noqa: BLE001
        logger.error("landscape DB session unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="データベースに接続できません") from exc


def _document_access_or_404(
    document_ref: str, current_user: dict, *, need_edit: bool
) -> Any:
    """``document_ref``（UUID / source_path）を解決し view / edit をゲートする（LS6）。

    不在・権限なしはどちらも **404**（``routes/theory_components.py`` の
    ``_ensure_document_viewable`` / ``_ensure_document_editable`` と同じ fail-closed の
    作法。403 は「存在は知れる」ため使わない）。SYSTEM_ADMIN は全権限
    （``_ensure_document_viewable`` と同条件）。
    """
    access = services.resolve_document_access(current_user.get("id"), document_ref)
    if not access.found:
        raise HTTPException(status_code=404, detail=_DETAIL_DOCUMENT_NOT_FOUND)
    if str(current_user.get("role") or "") == ROLE_SYSTEM_ADMIN:
        return access
    if not (access.can_edit if need_edit else access.can_view):
        raise HTTPException(status_code=404, detail=_DETAIL_DOCUMENT_NOT_FOUND)
    return access


# ---------------------------------------------------------------------------
# 読み出しヘルパー（すべて非LLM・読み取り専用）
# ---------------------------------------------------------------------------


def _document_rows(session, document_ids: Iterable[str]) -> list[tuple[str, str, str]]:
    """``[(document_id, title, source_path)]`` を1クエリで返す（N+1 を作らない）。

    ``title`` は空なら ``filename`` へ縮退する（どちらも無ければ空文字。推測しない）。
    """
    ids = [str(d).strip() for d in (document_ids or []) if str(d or "").strip()]
    if not ids:
        return []
    rows = session.execute(
        sa_text(
            """
            SELECT id::text,
                   COALESCE(NULLIF(title, ''), NULLIF(filename, ''), '') AS title,
                   COALESCE(source_path, '') AS source_path
              FROM documents
             WHERE id::text = ANY(:ids)
            """
        ),
        {"ids": ids},
    ).fetchall()
    return [(str(r[0]), str(r[1] or ""), str(r[2] or "")) for r in rows]


def _document_titles(session, document_ids: Iterable[str]) -> dict[str, str]:
    return {row[0]: row[1] for row in _document_rows(session, document_ids)}


def _last_run_at(session, document_id: str) -> str | None:
    """最新の解析 run の ``updated_at``（ISO 文字列 / 無ければ ``None``）。

    run の選び方は ``core.deliberation.refs.document_run_artifacts`` と同じ
    （completed を優先し、その中で最新）。同じ run の artifact を読んで
    ``unplaced_domains`` を出しているため、事実行の「生成日」もこの run に揃える（LS8）。
    """
    if not str(document_id or "").strip():
        return None
    row = session.execute(
        sa_text(
            """
            SELECT updated_at FROM document_analysis_runs
             WHERE document_id = :document_id
             ORDER BY (status = 'completed') DESC, updated_at DESC
             LIMIT 1
            """
        ),
        {"document_id": str(document_id)},
    ).fetchone()
    if not row or row[0] is None:
        return None
    isoformat = getattr(row[0], "isoformat", None)
    return isoformat() if callable(isoformat) else str(row[0])


def _domain_names(session) -> dict[str, str]:
    """``{domain_key: 表示名}``（``atlas_store.list_domains`` 由来。空名は載せない）。"""
    names: dict[str, str] = {}
    try:
        for entry in atlas_store.list_domains(session) or []:
            if not isinstance(entry, Mapping):
                continue
            key = str(entry.get("domain_key") or "").strip()
            name = str(entry.get("domain_name") or "").strip()
            if key and name:
                names[key] = name
    except Exception:  # noqa: BLE001
        logger.warning("landscape: failed to list atlas domains (non-fatal)", exc_info=True)
    return names


def _load_skeletons(session, domain_keys: Iterable[str]) -> dict[str, Any]:
    """``{domain_key: 凍結骨格}``。骨格の無いドメインは **キーごと落とす**（fail-soft）。

    骨格が引けないことをエラーにしない（骨格が改版・retire された後でも配置行は
    残る = P4。教員一覧では生 node_id のまま出続け、学習者側では
    ``projection.learner_landscape_dto`` が落とす）。
    """
    skeletons: dict[str, Any] = {}
    for key in domain_keys:
        domain_key = str(key or "").strip()
        if not domain_key or domain_key in skeletons:
            continue
        try:
            skeleton = atlas_store.load_learner_skeleton(domain_key, session)
        except Exception:  # noqa: BLE001
            logger.warning(
                "landscape: failed to load frozen skeleton for %s (non-fatal)",
                domain_key, exc_info=True,
            )
            skeleton = None
        if skeleton is not None:
            skeletons[domain_key] = skeleton
    return skeletons


def _domain_facts(
    skeletons: Mapping[str, Any], names: Mapping[str, str]
) -> list[dict]:
    """``[{domain_key, domain_name, frozen_version}]``（LS8 の事実行の材料）。

    骨格を引けたドメインだけを載せる（版が確認できないドメインの行を作らない）。
    """
    return [
        {
            "domain_key": key,
            "domain_name": str(names.get(key) or "") or key,
            "frozen_version": str(getattr(skeletons[key], "version", "") or ""),
        }
        for key in sorted(skeletons)
    ]


def _displayed_skeleton_version(domain_dtos: Iterable[Mapping[str, Any]]) -> str:
    """学習者に見せている地図の凍結版文字列（引けなければ空文字）。

    選び方はフロントの事実行（``landscape-layer.js`` の ``skeletonVersionText`` /
    ``app.js`` の ``paperPlacementCorpusFact``）と同じ — コースがバインドされた
    ドメイン（``is_course_map``）を優先し、無ければ先頭のドメイン。版が確認できない
    ときは**空文字を返す**（推測した版番号を事実文に書かせない）。
    """
    entries = [e for e in (domain_dtos or []) if isinstance(e, Mapping)]
    for entry in entries:
        if entry.get("is_course_map"):
            return str(entry.get("frozen_version") or "")
    return str(entries[0].get("frozen_version") or "") if entries else ""


def _gap_signal_domains(session, document_id: str) -> set[str]:
    """この論文について記録されている gap 信号の domain_key 集合（**件数は返さない**）。

    正本: ``docs/features/category_gap_candidates_design.md`` §4.1 裁定 — 1論文の画面に
    「地図を直す」ボタンは置かず、「この分野の地図への候補として記録されています →
    分野の地図タブで確認」の**案内一行**だけを出す。そのための真偽値の材料である。

    信号は反復閾値（2論文以上）に届く前から保存されるので、ここに現れることと
    レビューキューに出ることは別（LS5: 件数・閾値までの残りを出さない）。
    照会に失敗しても配置一覧は返す（fail-soft・案内が出ないだけ）。
    """
    try:
        from core.atlas_gaps import store as gap_store

        rows = gap_store.list_active_signals(session, document_id=str(document_id or ""))
    except Exception:  # noqa: BLE001
        logger.warning(
            "landscape: failed to read category gap signals for %s (non-fatal)",
            document_id, exc_info=True,
        )
        return set()
    return {str(row.get("domain_key") or "") for row in rows if row.get("domain_key")}


def _unplaced_from_artifacts(document_id: str, names: Mapping[str, str]) -> list[dict]:
    """最新 run の ``landscape_placement`` artifact から ``unplaced_domains``（LS10）。

    配置不能は失敗ではなく信号なので、artifact に残っていれば必ず提示する。
    artifact が読めない・ステージ未実行は空リスト（fail-soft・捏造しない）。
    """
    try:
        from core.deliberation.refs import document_run_artifacts

        artifacts = document_run_artifacts(str(document_id or ""))
    except Exception:  # noqa: BLE001
        logger.warning(
            "landscape: failed to load run artifacts for %s (non-fatal)",
            document_id, exc_info=True,
        )
        return []
    stage = artifacts.get("landscape_placement") if isinstance(artifacts, Mapping) else None
    raw = stage.get("unplaced_domains") if isinstance(stage, Mapping) else None
    out: list[dict] = []
    for entry in raw or []:
        if not isinstance(entry, Mapping):
            continue
        domain_key = str(entry.get("domain_key") or "").strip()
        if not domain_key:
            continue
        out.append(
            {
                "domain_key": domain_key,
                "domain_name": str(entry.get("domain_name") or "").strip()
                or str(names.get(domain_key) or "")
                or domain_key,
                "reason": str(entry.get("reason") or ""),
            }
        )
    return out


# ---------------------------------------------------------------------------
# 教員 API（§9.1。全経路 _require_teacher）
# ---------------------------------------------------------------------------


class UpdatePlacementStatusRequest(BaseModel):
    """PATCH body。``status`` は live 語彙のみ（``superseded`` は手動遷移不可）。"""

    status: str = Field(
        description="confirmed | rejected | review_required | inferred（live 語彙のみ）"
    )
    review_note: str = Field(
        default="",
        description="教員のメモ（空文字なら既存のメモを保持する — P4）",
    )


class ProposePlacementsRequest(BaseModel):
    """再提案 body。v1 は入力パラメータを持たない（空 body / body なしを許す）。"""


@router.get("/documents/{document_ref}/placements")
def list_document_landscape_placements(
    document_ref: str,
    include_history: bool = Query(
        False, description="true で superseded（旧候補）も含める（P4 の可視化）"
    ),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """1 document の配置一覧（§9.1）。``document_ref`` は UUID / source_path 両対応。

    返す形:
    ``{document_id, title, placements[], unplaced_domains[], domains[], last_run_at,
    gap_signals_recorded}``。
    ``placements`` は ``projection.admin_placement_dto``（生 weight / confidence なし・LS5）。
    ``gap_signals_recorded`` は「この論文から地図への候補が記録されているか」の真偽値
    （``unplaced_domains`` の各要素にも分野ごとの真偽値を載せる。件数は返さない —
    ``category_gap_candidates_design.md`` §4.1 / §5.4 の案内一行のため）。
    """
    access = _document_access_or_404(document_ref, current_user, need_edit=False)
    document_id = str(access.document_id or "")

    session = _session()
    try:
        rows = landscape_store.list_for_document(session, document_id, include_history)
        titles = _document_titles(session, [document_id])
        last_run_at = _last_run_at(session, document_id)
        names = _domain_names(session)
        unplaced = _unplaced_from_artifacts(document_id, names)
        gap_domains = _gap_signal_domains(session, document_id)
        domain_keys = {str(r.get("domain_key") or "") for r in rows}
        domain_keys.update(str(u.get("domain_key") or "") for u in unplaced)
        skeletons = _load_skeletons(session, {k for k in domain_keys if k})
    finally:
        session.close()

    for entry in unplaced:
        entry["gap_signals_recorded"] = entry.get("domain_key") in gap_domains

    node_index = projection.skeleton_node_index(skeletons)
    return {
        "document_id": document_id,
        "title": titles.get(document_id, ""),
        "placements": [
            projection.admin_placement_dto(row, node_index, domain_names=names)
            for row in rows
        ],
        "unplaced_domains": unplaced,
        "domains": _domain_facts(skeletons, names),
        "last_run_at": last_run_at,
        "gap_signals_recorded": bool(gap_domains),
    }


@router.patch("/placements/{placement_id}")
def update_landscape_placement_status(
    placement_id: str,
    body: UpdatePlacementStatusRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """配置の status を遷移させる（教員の確認 / 却下 / 再検討。LS3）。

    ゲートは当該配置の document の **edit**。不正な遷移（``superseded`` を指す /
    履歴行を動かす）は 422、行が無い・document が見えないは 404。監査は
    ``theory_review_events``（``entity_type='landscape_placement'``）。
    """
    session = _session()
    try:
        existing = landscape_store.get_placement(session, placement_id)
    finally:
        session.close()
    if existing is None:
        raise HTTPException(status_code=404, detail=_DETAIL_PLACEMENT_NOT_FOUND)

    access = _document_access_or_404(
        str(existing.get("document_id") or ""), current_user, need_edit=True
    )
    old_status = str(existing.get("status") or "")
    reviewer_id = str(current_user.get("id") or "").strip()

    session = _session()
    try:
        updated = landscape_store.update_status(
            session,
            placement_id,
            str(body.status or "").strip(),
            reviewer_id=reviewer_id,
            note=str(body.review_note or ""),
        )
        if updated is None:
            session.rollback()
            raise HTTPException(status_code=404, detail=_DETAIL_PLACEMENT_NOT_FOUND)
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

    services.record_review_event(
        AUDIT_ENTITY_LANDSCAPE_PLACEMENT,
        placement_id,
        old_status,
        str(updated.get("status") or ""),
        current_user.get("id"),
        {"action": "review", "document_id": str(access.document_id or "")},
    )

    session = _session()
    try:
        names = _domain_names(session)
        skeletons = _load_skeletons(session, [str(updated.get("domain_key") or "")])
    finally:
        session.close()
    node_index = projection.skeleton_node_index(skeletons)
    return {
        "placement": projection.admin_placement_dto(
            updated, node_index, domain_names=names
        )
    }


@router.post("/documents/{document_ref}/placements/propose")
def propose_landscape_placements(
    document_ref: str,
    body: ProposePlacementsRequest | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """配置候補を作り直す（教員の明示操作。パイプラインと同一ビルダー・同一日次予算）。

    200 = ステージ payload をそのまま返す（``skipped_reason`` が未知の語彙でも 200 の
    まま返し、フロントが素通しで提示する）。日次上限は 429、素材なし・骨格なしは 422
    で、いずれも事実文のみ（上限値や件数を detail に書かない — LS5）。
    """
    access = _document_access_or_404(document_ref, current_user, need_edit=True)
    document_id = str(access.document_id or "")
    user_id = str(current_user.get("id") or "").strip()
    created_by = f"teacher:{user_id}" if user_id else "teacher"

    try:
        payload = landscape_builder.build_and_store_placements(
            document_id, created_by=created_by
        ) or {}
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "landscape placement proposal failed for document=%s", document_id, exc_info=True
        )
        raise HTTPException(status_code=502, detail=_DETAIL_PROPOSE_FAILED) from exc

    if payload.get("skipped_by_limit"):
        raise HTTPException(status_code=429, detail=_DETAIL_DAILY_LIMIT)
    reason = str(payload.get("skipped_reason") or "")
    if reason in _PROPOSE_BLOCKING_REASONS:
        raise HTTPException(status_code=422, detail=_PROPOSE_BLOCKING_REASONS[reason])

    services.record_review_event(
        AUDIT_ENTITY_LANDSCAPE_PLACEMENT,
        document_id,
        "",
        landscape_schema.STATUS_INFERRED,
        current_user.get("id"),
        {
            "action": "propose",
            "document_id": document_id,
            "created": int(payload.get("created") or 0),
            "superseded": int(payload.get("superseded") or 0),
        },
    )
    return payload


# ---------------------------------------------------------------------------
# リリース前の確認（Release Review。正本 docs/features/release_review_flow_design.md）
# ---------------------------------------------------------------------------


class AcceptPlacementsRequest(BaseModel):
    """一括確認 body。v1 は入力パラメータを持たない（空 body / body なしを許す）。"""


#: RR3: 「次へ」経由の確認を個別レビューと区別するための事実文（review_note に残す）。
RELEASE_ACCEPT_NOTE = "リリース前の確認画面で一括確認"
#: RR3: 監査の action。個別レビュー（"review"）と混ぜない。
RELEASE_ACCEPT_ACTION = "accept_on_release"


def _course_source_access(
    course_id: str, current_user: dict
) -> tuple[dict, list[str], set[str], int]:
    """コースのソース論文の権限を1パスで解決する。

    戻り値は ``(course_data, 閲覧できる document_id 列, edit できる id の集合,
    除外件数)``。

    RR7（リリースを止めない）: 権限の無い document は **403 にせず静かに除外**し、件数だけ
    正直に返す（共同編集コースに自分の権限外の論文が混ざっていても確認・公開を止めない）。
    コース自体が見えない場合だけ 404（LS6 の fail-closed）。document ごとの解決は
    ``services.resolve_document_access`` 1回で view / edit / owner を得る（N+1 を作らない）。
    """
    course_data = services.get_accessible_course_data(current_user.get("id"), course_id)
    if course_data is None:
        raise HTTPException(status_code=404, detail="コースが見つかりません")

    is_system_admin = str(current_user.get("role") or "") == ROLE_SYSTEM_ADMIN
    viewable: list[str] = []
    editable: set[str] = set()
    hidden = 0
    for ref in services.list_course_source_document_ids(course_data):
        access = services.resolve_document_access(current_user.get("id"), str(ref))
        document_id = str(access.document_id or "")
        if not access.found or not document_id:
            hidden += 1
            continue
        if is_system_admin or access.can_view:
            viewable.append(document_id)
        else:
            hidden += 1
            continue
        if is_system_admin or access.can_edit:
            editable.add(document_id)
    return course_data, viewable, editable, hidden


@router.get("/courses/{course_id}/placements")
def list_course_landscape_placements(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースのソース論文の live 配置をまとめて返す（リリース前の確認 ステップ2）。

    document 単位のグループに ``unplaced_domains``（LS10）を同梱し、未確認件数
    （``pending_count``）を事実として返す。生 weight / confidence は
    ``projection.admin_placement_dto`` が構造的に落とす（LS5 / RR6）。
    """
    course_data, document_ids, editable_ids, hidden = _course_source_access(
        course_id, current_user
    )

    session = _session()
    try:
        rows = landscape_store.list_for_documents(
            session, document_ids, statuses=landscape_schema.LIVE_STATUSES
        )
        titles = _document_titles(session, document_ids)
        names = _domain_names(session)
        unplaced_by_document = {
            document_id: _unplaced_from_artifacts(document_id, names)
            for document_id in document_ids
        }
        domain_keys = {str(r.get("domain_key") or "") for r in rows}
        for entries in unplaced_by_document.values():
            domain_keys.update(str(u.get("domain_key") or "") for u in entries)
        skeletons = _load_skeletons(session, {k for k in domain_keys if k})
    finally:
        session.close()

    node_index = projection.skeleton_node_index(skeletons)
    by_document: dict[str, list[dict]] = {}
    pending = 0
    for row in rows:
        document_id = str(row.get("document_id") or "")
        by_document.setdefault(document_id, []).append(
            projection.admin_placement_dto(row, node_index, domain_names=names)
        )
        if str(row.get("status") or "") == landscape_schema.STATUS_INFERRED:
            pending += 1

    documents = [
        {
            "document_id": document_id,
            "title": titles.get(document_id, ""),
            "placements": by_document.get(document_id, []),
            "unplaced_domains": unplaced_by_document.get(document_id, []),
            "editable": document_id in editable_ids,
        }
        for document_id in document_ids
    ]

    return {
        "course_id": course_id,
        "course_title": str((course_data or {}).get("title") or ""),
        "cartridge_id": course_cartridge_id(course_data) or "",
        "documents": documents,
        "domains": _domain_facts(skeletons, names),
        "placement_count": len(rows),
        "pending_count": pending,
        "source_document_count": len(document_ids) + hidden,
        "hidden_document_count": hidden,
        "editable": bool(editable_ids),
    }


@router.post("/courses/{course_id}/placements/accept")
def accept_course_landscape_placements(
    course_id: str,
    body: AcceptPlacementsRequest | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """未確認（``inferred``）の配置を一括で ``confirmed`` にする（「次へ」= 承認。RR2）。

    - 対象は **edit 権限のある** ソース論文のみ。権限外は静かに除外して件数で返す（RR7）。
    - 教員が個別に却下・再検討した行は動かさない（``store`` 側で ``inferred`` 限定。RR4）。
    - 監査は1件ごとに ``theory_review_events``（``action='accept_on_release'``）で、
      個別レビューと区別できる形で残す（RR3）。
    """
    del body  # v1 はパラメータなし（将来の拡張のために受け口だけ残す）
    _, viewable, editable_ids, hidden = _course_source_access(course_id, current_user)
    # edit できないソース論文は確認の対象外（除外件数として正直に返す — RR7）。
    skipped_documents = hidden + len([d for d in viewable if d not in editable_ids])
    reviewer_id = str(current_user.get("id") or "").strip()

    session = _session()
    try:
        updated = landscape_store.accept_inferred_for_documents(
            session,
            sorted(editable_ids),
            reviewer_id=reviewer_id,
            note=RELEASE_ACCEPT_NOTE,
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    for row in updated:
        services.record_review_event(
            AUDIT_ENTITY_LANDSCAPE_PLACEMENT,
            str(row.get("id") or ""),
            landscape_schema.STATUS_INFERRED,
            landscape_schema.STATUS_CONFIRMED,
            current_user.get("id"),
            {
                "action": RELEASE_ACCEPT_ACTION,
                "course_id": course_id,
                "document_id": str(row.get("document_id") or ""),
            },
        )

    return {
        "course_id": course_id,
        "confirmed": len(updated),
        "skipped_documents": skipped_documents,
    }


@router.get("/overview")
def get_landscape_overview(
    domain_key: str = Query("", description="集約する分野の domain_key（凍結済みのみ）"),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """本人可視 document の live 配置をノード別に集約する（§9.1 / LS8）。

    可視集合は ``services.list_visible_document_ids``（document 直接可視 ∪ アクセス可能
    コースの sources）。骨格に存在しないノードの配置は地図に描けないため集約に載せない
    （教員個別一覧では ``list_document_landscape_placements`` が出し続ける — P4）。
    """
    key = str(domain_key or "").strip()
    user_id = str(current_user.get("id") or "").strip()

    visible = services.list_visible_document_ids(user_id) if user_id else set()

    session = _session()
    try:
        skeleton = atlas_store.load_learner_skeleton(key, session) if key else None
        if skeleton is None:
            raise HTTPException(status_code=404, detail=_DETAIL_DOMAIN_NOT_FROZEN)
        names = _domain_names(session)
        rows = [
            row
            for row in landscape_store.list_for_documents(
                session, visible, statuses=landscape_schema.LIVE_STATUSES
            )
            if str(row.get("domain_key") or "") == key
        ]
        placed_ids: list[str] = []
        for row in rows:
            document_id = str(row.get("document_id") or "")
            if document_id and document_id not in placed_ids:
                placed_ids.append(document_id)
        titles = _document_titles(session, placed_ids)
    except HTTPException:
        raise
    finally:
        session.close()

    node_index = projection.skeleton_node_index({key: skeleton})
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        node_id = str(row.get("node_id") or "")
        if (key, node_id) not in node_index:
            continue
        document_id = str(row.get("document_id") or "")
        status = str(row.get("status") or "")
        grouped.setdefault(node_id, []).append(
            {
                "document_id": document_id,
                "title": titles.get(document_id, ""),
                "perspective_label": landscape_schema.perspective_label(
                    str(row.get("perspective") or "")
                ),
                # LS5: 生 weight は返さない（段階ラベルのみ）。
                "weight_label": landscape_schema.weight_label(row.get("weight")),
                "status": status,
            }
        )

    nodes: list[dict] = []
    for region in getattr(skeleton, "regions", ()) or ():
        region_id = str(getattr(region, "id", "") or "")
        for node_id in [region_id] + [
            str(getattr(c, "id", "") or "") for c in getattr(region, "concepts", ()) or ()
        ]:
            if not node_id or node_id not in grouped:
                continue
            info = node_index.get((key, node_id)) or {}
            nodes.append(
                {
                    "node_id": node_id,
                    "label": str(info.get("label") or "") or node_id,
                    "region_id": str(info.get("region_id") or ""),
                    "documents": grouped[node_id],
                }
            )

    return {
        "domain_key": key,
        "domain_name": str(names.get(key) or "") or key,
        "skeleton_version": str(getattr(skeleton, "version", "") or ""),
        "corpus": {
            "visible_document_count": len(visible),
            "placed_document_count": len(placed_ids),
        },
        "nodes": nodes,
    }


# ---------------------------------------------------------------------------
# 学習者 API（§9.2）
# ---------------------------------------------------------------------------


@learning_router.get("/courses/{course_id}/landscape")
def get_course_landscape(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """受講コースのソース論文の「論文の位置づけ」（§9.2）。

    - ゲート: ``services.get_accessible_course_data``（受講 / 所有 / 公開テンプレート）
    - 対象 document: ``services.list_course_source_document_ids``（コースのソースのみ）
    - status: ``LEARNER_VISIBLE_STATUSES`` のみ（rejected / superseded は出さない）
    - 配置ゼロ・骨格なしでも **200 で空構造**（非表示への縮退はフロントの責務）
    - 数値は corpus の件数のみ。weight / confidence / claim_id は
      ``projection.learner_landscape_dto`` が構造的に落とす（LS5・§9.2）
    - ``unplaced_documents``: コースのソースのうち、現行の凍結骨格のどの領域にも
      配置が無い論文（``document_id`` / ``title`` だけ）。``skeleton_version`` は
      いま見せている地図の版。**「置けなかった」ことを取得失敗と同じ沈黙に潰さない**
      ための事実材料で（AB1 / LS10。正本 ``category_gap_candidates_design.md`` §4.5）、
      配置済みが1件も無いとき節ごと非表示にする fail-closed はフロント側に残る。
      gap / 候補の語彙・件数は載せない（候補機構は教員側の別実装）。
    """
    course_data = services.get_accessible_course_data(current_user["id"], course_id)
    if course_data is None:
        raise HTTPException(status_code=404, detail="コースが見つかりません")

    document_ids = services.list_course_source_document_ids(course_data)
    course_domain_key = course_cartridge_id(course_data) or ""

    session = _session()
    try:
        placements = landscape_store.list_for_documents(
            session,
            document_ids,
            statuses=landscape_schema.LEARNER_VISIBLE_STATUSES,
        )
        domain_keys = {str(p.get("domain_key") or "") for p in placements}
        if course_domain_key:
            domain_keys.add(course_domain_key)
        skeletons = _load_skeletons(session, {k for k in domain_keys if k})
        names = _domain_names(session)
        rows = _document_rows(session, document_ids)
    finally:
        session.close()

    # コースの sources 順を保つ（DTO の documents 並び順は document_titles の順序）。
    title_by_id = {row[0]: row[1] for row in rows}
    id_by_source = {row[2]: row[0] for row in rows if row[2]}
    ordered: list[str] = []
    for material_id in course_source_material_ids(course_data):
        document_id = id_by_source.get(str(material_id))
        if document_id and document_id not in ordered:
            ordered.append(document_id)
    for document_id in sorted(title_by_id):
        if document_id not in ordered:
            ordered.append(document_id)
    titles = {document_id: title_by_id.get(document_id, "") for document_id in ordered}

    dto = projection.learner_landscape_dto(
        placements,
        projection.skeleton_node_index(skeletons),
        _domain_facts(skeletons, names),
        course_domain_key or None,
        document_titles=titles,
        source_document_count=len(document_ids),
    )

    # 配置ゼロの論文（LS10 / AB1）。DTO の documents に載らなかったソース論文がそれで、
    # 骨格に無いノードの配置しか持たない論文（改版で消えた等）もここに入る — 学習者が
    # 見ている「現在の地図」にはどの領域も無い、という事実は同じだからである。
    # 件数・理由・候補は出さない（タイトルの列挙だけ。LS5 / 候補機構は教員側）。
    placed_ids = {str(d.get("document_id") or "") for d in dto.get("documents") or []}
    unplaced_documents = [
        {"document_id": document_id, "title": titles.get(document_id, "")}
        for document_id in ordered
        if document_id not in placed_ids
    ]

    return {
        "course_id": course_id,
        **dto,
        "unplaced_documents": unplaced_documents,
        "skeleton_version": _displayed_skeleton_version(dto.get("domains") or []),
    }
