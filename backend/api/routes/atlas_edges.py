"""分野マップの関係表示（RE層）— 辺候補の教員レビュー API
（実パス ``/api/admin/cartridges/{id}/atlas/edge-candidates...``）。

正本: ``docs/features/atlas_relation_edges_design.md`` §5（管理 API + freeze 統合）
/ §8（語彙・監査）/ §9（ガードレール）。不変条項 RE1〜RE8 は §2。

本ルータの立場（``routes/atlas_gaps.py`` の**辺版**。同じ3段の弁を通す）:

- **RE3 恒久配線への経路は candidate → 教員確定 → 凍結のみ**: 本ルータは候補に対する
  教員の**判断**だけを書く。``atlas_skeletons`` への INSERT / UPDATE は一切持たない —
  次版下書きへの反映は「読み取り専用の patch プレビュー → 教員の既存 ``PUT draft``
  （revision 楽観ロック）→ ``mark-incorporated`` の刻印」の3手であり、骨格を書くのは
  常に教員の PUT である（AB4 / KN-3 の継承）。
- **RE6 候補は読み時導出**: レビューキューは毎回 ``derive_edge_candidates`` が導出する
  （候補行を蓄積しない）。導出は保存済みアンカーベクトルと配置行の読みだけで、
  **embedding API を呼ばない**。
- **RE4 数値非表示**: cosine・共起件数は返さない。近さは段階ラベル、共起の支持は
  論文タイトルの列挙（DTO を組むのは core 側で、本ルータは値を足さない）。
- **RE5 情報を落とさない**: DELETE ルートを作らない。見送りは ``dismissed``、その
  取り消しは ``candidate`` への状態遷移で表す。
- **RE1 主張は離散の辺のみ**: 判断の対象は無向の ``edge_key`` だけで、node の位置・
  地形には触れない。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import services
from core import atlas
from core import atlas_store
from core.atlas_edges import derive as edge_derive
from core.atlas_edges import patching as edge_patching
from core.atlas_edges import schema as edge_schema
from core.atlas_edges import store as edge_store
from core.schema import AUDIT_ENTITY_ATLAS_EDGE
from dependencies import _require_teacher

logger = logging.getLogger(__name__)

# /api/admin/cartridges/... 配下（main.py が prefix="/api/admin" でフラット登録する。
# CLAUDE.md Tier 3-17c: admin 系子ルーターは admin.router に include しない）
router = APIRouter(prefix="/cartridges", tags=["Admin"])


# ---------------------------------------------------------------------------
# 事実文（RE4: 件数・スコアを書かない。督促・欠陥語彙を使わない）
# ---------------------------------------------------------------------------

_DETAIL_NO_FROZEN_SKELETON = "この分野には凍結済みの骨格がありません。"
_DETAIL_UNKNOWN_ACTION = "操作の指定が正しくありません。"
_DETAIL_DOMAIN_MISMATCH = "この候補は別の分野のものです。"
_DETAIL_DECISION_NOT_FOUND = "この関係についての判断は記録されていません。"
_DETAIL_NOT_ACCEPTED = "この関係はまだ採用されていません。先に採用してください。"
_DETAIL_NO_DRAFT = (
    "次版の下書きがありません。「現在の版から次版の下書きを作る」を先に実行してください。"
)
_DETAIL_EDGE_NOT_IN_DRAFT = (
    "この辺は次版の下書きにありません。下書きを保存してから記録してください。"
)


# ---------------------------------------------------------------------------
# セッション・共通ヘルパー（atlas_gaps と同型）
# ---------------------------------------------------------------------------


def _session():
    """辺候補の導出・判断・骨格の読み書き用セッション。取得不能は 503。"""
    try:
        from core.postgres import get_session

        return get_session()
    except Exception as exc:  # noqa: BLE001
        logger.error("atlas edge DB session unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="データベースに接続できません") from exc


def _frozen_or_404(session, cartridge_id: str):
    """現行の凍結骨格（DB 優先・同梱ファイルへフォールバック）。無ければ 404。

    候補は「現行の地図にまだ無い関係」なので、比較対象の凍結版が無い分野では
    そもそもレビューが成立しない（既存の atlas 系読み取り API と同じ 404 の流儀）。
    """
    skeleton = atlas_store.load_learner_skeleton(cartridge_id, session)
    if skeleton is None:
        raise HTTPException(status_code=404, detail=_DETAIL_NO_FROZEN_SKELETON)
    return skeleton


def _draft_or_409(session, cartridge_id: str) -> dict:
    """編集中の次版下書き。無ければ 409（先に from-frozen で下書きを作る導線へ）。"""
    draft_row = atlas_store.load_draft(session, cartridge_id)
    if draft_row is None:
        raise HTTPException(status_code=409, detail=_DETAIL_NO_DRAFT)
    return draft_row


def _assert_edge_belongs_to_domain(edge_key: str, cartridge_id: str) -> None:
    """edge_key の domain とパスの分野が食い違う操作を弾く（fail-closed）。

    edge_key は ``edge|{domain_key}|{a}|{b}`` で domain を含むため、パスの分野と
    一致しない鍵での判断・取り込みは受け付けない（別分野の共同財行を、その分野を
    開いていない教員が動かせてしまう経路を作らない）。
    """
    domain_key, _left, _right = edge_schema.parse_edge_key(edge_key)
    if not domain_key or domain_key != str(cartridge_id or "").strip():
        raise HTTPException(status_code=422, detail=_DETAIL_DOMAIN_MISMATCH)


def _record_edge_event(
    edge_key: str,
    old_status: str,
    new_status: str,
    user_id: Any,
    metadata: dict,
) -> None:
    """判断の遷移を監査記録する（§8）。DB 不通でも操作自体は落とさない。"""
    try:
        services.record_review_event(
            AUDIT_ENTITY_ATLAS_EDGE,
            edge_key,
            old_status,
            new_status,
            str(user_id or "") or None,
            metadata,
        )
    except Exception:  # noqa: BLE001
        logger.warning("atlas edge review event skipped", exc_info=True)


def _anchors_for(session, cartridge_id: str, skeleton_version: str) -> tuple:
    """VA層の保存済みアンカー（fail-soft）。

    索引が未構築・版ずれ・ベクトル層の不調のときは空タプルを返す。その場合の候補は
    配置共起（co_occurrence）由来だけになり、レビューキューは形を変えずに縮退する
    （RE6: **ここで embedding を呼ばない** — 読むのは保存済みベクトルだけ）。
    """
    try:
        from core.atlas_vectors.builder import anchors_with_labels

        anchors, _version = anchors_with_labels(session, cartridge_id, skeleton_version)
        return tuple(anchors or ())
    except Exception:  # noqa: BLE001 — アンカー不在でレビューキューを壊さない
        logger.warning(
            "atlas edge candidates: anchor vectors unavailable for %s (non-fatal)",
            cartridge_id,
            exc_info=True,
        )
        return ()


# ---------------------------------------------------------------------------
# リクエストモデル
# ---------------------------------------------------------------------------


class DecideEdgeCandidateRequest(BaseModel):
    edge_key: str = Field(description="候補の edge_key（無向・版非依存キー）")
    action: str = Field(description="accept | dismiss | restore")
    kind: str = Field(
        default="",
        description="関係の種別（adjacent | depends | related）。採用（accept）では必須",
    )
    review_note: str = Field(
        default="", description="判断の理由。見送り（dismiss）では必須（空は 422）"
    )


class EdgeIncorporatePreviewRequest(BaseModel):
    edge_key: str = Field(description="採用済み候補の edge_key")


class EdgeMarkIncorporatedRequest(BaseModel):
    edge_key: str = Field(description="採用済み候補の edge_key")


# ---------------------------------------------------------------------------
# レビューキュー（読み時導出。§4 / §5）
# ---------------------------------------------------------------------------


@router.get("/{cartridge_id}/atlas/edge-candidates")
def list_atlas_edge_candidates(
    cartridge_id: str,
    include_dismissed: bool = Query(
        False, description="true で見送り済みも含める（復帰導線用）"
    ),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """関係（辺）の候補一覧（毎回導出。行として蓄積しない = RE6）。

    返す形: ``{cartridge_id, candidates, skeleton_version, draft_exists, draft_revision}``。
    ``candidates`` の各要素は ``core/atlas_edges/derive.py`` の DTO（生 cosine なし・
    件数フィールドなし。近さは段階ラベル、共起の支持は ``documents`` のタイトル列挙）に
    教員の判断（``decision``）をマージしたもの。``draft_exists`` / ``draft_revision`` は
    「次版の下書きへ反映する」導線の活性判断用。
    """
    session = _session()
    try:
        frozen = _frozen_or_404(session, cartridge_id)
        version = str(getattr(frozen, "version", "") or "")
        anchors = _anchors_for(session, cartridge_id, version)
        candidates = edge_derive.derive_edge_candidates(
            session,
            domain_key=cartridge_id,
            skeleton=frozen,
            anchors=anchors,
        )
        # 判断はキー一括で引く（候補ごとの ``get_decision`` は N+1 になる）。
        # ``_fetch_decisions`` は store 内で唯一の多件リーダーで、空入力では
        # SQL を発行しない（候補ゼロのときに全件走査へ転ばせない）。
        decisions = edge_store._fetch_decisions(
            session, [c.get("edge_key") for c in candidates]
        )
        candidates = edge_store.merge_decisions_into(
            candidates, decisions, include_dismissed=include_dismissed
        )
        draft_row = atlas_store.load_draft(session, cartridge_id)
    finally:
        session.close()

    return {
        "cartridge_id": cartridge_id,
        "candidates": candidates,
        "skeleton_version": version,
        "draft_exists": draft_row is not None,
        "draft_revision": draft_row["revision"] if draft_row else None,
    }


# ---------------------------------------------------------------------------
# 教員の判断（採用 / 見送り / 復帰。確定は人間のみ — RE3 / KN-3）
# ---------------------------------------------------------------------------


@router.post("/{cartridge_id}/atlas/edge-candidates/decide")
def decide_atlas_edge_candidate(
    cartridge_id: str,
    body: DecideEdgeCandidateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """辺候補を採用・見送りにする（または見送りを取り消す）。骨格 draft は変わらない。

    - ``accept``: 「関係として妥当」の判断 + 種別の確定（``kind`` 必須。語彙外は 422）。
      次版下書きへの反映は別操作（採用と反映の分離）
    - ``dismiss``: 理由必須（空は 422）。以降レビューキューにも糸レイヤーにも出ない（RE8）
    - ``restore``: 見送りを取り消して未判断に戻す（**行削除ではなく状態遷移** — RE5）

    遷移の可否・却下理由の必須は ``core/candidate_flow.py`` の CandidateFlow が判定する
    （本ルータは再実装しない）。監査は**コミット後**に記帳する — 書き込めていない遷移を
    監査に載せないため、core から渡される記帳内容はいったん退避しておく。
    """
    action = str(body.action or "").strip()
    if action not in edge_schema.DECIDE_ACTIONS:
        raise HTTPException(status_code=422, detail=_DETAIL_UNKNOWN_ACTION)
    edge_key = str(body.edge_key or "").strip()
    _assert_edge_belongs_to_domain(edge_key, cartridge_id)

    decided_by = str(current_user.get("id") or "").strip()
    # CandidateFlow は apply の直後（＝コミット前）に監査 callable を呼ぶ。ここでは
    # 内容を退避するだけにして、コミットが成功した後に本当の記帳を行う。
    pending_audits: list[dict] = []
    session = _session()
    try:
        result = edge_store.decide(
            session,
            edge_key=edge_key,
            action=action,
            actor_id=decided_by,
            review_note=str(body.review_note or ""),
            edge_kind=str(body.kind or ""),
            record_audit=lambda **kwargs: pending_audits.append(dict(kwargs)),
        )
        if result is None:
            session.rollback()
            raise HTTPException(status_code=404, detail=_DETAIL_DECISION_NOT_FOUND)
        session.commit()
    except ValueError as exc:
        # CandidateTransitionError（ValueError の派生）も含む。事実文のまま 422 にする。
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

    for audit in pending_audits:
        _record_edge_event(
            str(audit.get("entity_id") or edge_key),
            str(audit.get("old_status") or ""),
            str(audit.get("new_status") or ""),
            current_user.get("id"),
            {
                "action": str(audit.get("action") or action),
                "cartridge_id": cartridge_id,
            },
        )
    return {"cartridge_id": cartridge_id, "decision": result.get("decision")}


# ---------------------------------------------------------------------------
# 次版下書きへの反映（読み取り専用プレビュー → 教員の PUT → 刻印。§5）
# ---------------------------------------------------------------------------


@router.post("/{cartridge_id}/atlas/edge-candidates/incorporate-preview")
def preview_atlas_edge_incorporation(
    cartridge_id: str,
    body: EdgeIncorporatePreviewRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """採用済み候補の辺を次版下書きへ追加する JSON Patch を**提示するだけ**の読み取り操作。

    **DB を変更しない**（下書きは書かない。適用は教員の既存
    ``PUT /api/admin/cartridges/{id}/atlas/skeleton/draft`` が revision 楽観ロック付きで
    行う — RE3）。

    返す形: ``{cartridge_id, edge_key, patch, patched_draft, validation, revision,
    from_id, to_id, kind, summary}``。``patched_draft`` は適用後の ``atlas_skeleton``
    dict で、そのまま PUT の body.skeleton に使える。

    未採用は 409 / 下書きなしは 409 / 端点が下書きに無い・同じ辺が既にあるは 422（事実文）。
    """
    edge_key = str(body.edge_key or "").strip()
    _assert_edge_belongs_to_domain(edge_key, cartridge_id)
    _domain, from_id, to_id = edge_schema.parse_edge_key(edge_key)

    session = _session()
    try:
        decision = edge_store.get_decision(session, edge_key)
        if decision is None or decision["status"] != edge_schema.DECISION_STATUS_ACCEPTED:
            raise HTTPException(status_code=409, detail=_DETAIL_NOT_ACCEPTED)
        draft_row = _draft_or_409(session, cartridge_id)
    finally:
        session.close()

    draft_dict = atlas.skeleton_to_dict(draft_row["skeleton"])["atlas_skeleton"]
    try:
        built = edge_patching.build_edge_patch(
            draft_dict,
            from_id=from_id,
            to_id=to_id,
            kind=decision["edge_kind"],
        )
    except edge_patching.EdgePatchError as exc:
        # 種別が語彙外・端点が下書きに無い・重複（どれも「いま作れない patch を
        # 提示しない」の同じ扱い）。
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    patched, validation = _apply_and_validate(draft_dict, built["patch"])
    return {
        "cartridge_id": cartridge_id,
        "edge_key": edge_key,
        "patch": built["patch"],
        "from_id": built["from_id"],
        "to_id": built["to_id"],
        "kind": built["kind"],
        "summary": built["summary"],
        "patched_draft": patched,
        "validation": validation,
        "revision": draft_row["revision"],
    }


def _apply_and_validate(draft_dict: dict, patch: list[dict]) -> tuple[dict | None, dict]:
    """patch を非破壊適用し、既存の骨格バリデータで検証する（atlas_gaps と同型）。

    適用不能・スキーマ不適合でも提示は止めない（``patched_draft=None`` +
    ``validation.errors`` で正直に返し、最終的な拒否は教員の PUT の 422 に委ねる）。
    """
    from core.atlas_generator import PatchApplyError, apply_json_patch

    errors: list[dict] = []
    warnings: list[dict] = []
    patched: dict | None = None
    try:
        patched = apply_json_patch(draft_dict, patch)
    except PatchApplyError as exc:
        errors.append({"message": f"パッチを適用できませんでした: {exc}"})

    if patched is not None:
        try:
            report = atlas.validate_skeleton(atlas.parse_skeleton(patched))
            errors.extend(atlas.issue_to_dict(e) for e in report.errors)
            warnings.extend(atlas.issue_to_dict(w) for w in report.warnings)
        except atlas.SkeletonParseError as exc:
            patched = None
            errors.append({"message": f"適用後の骨格を読み取れませんでした: {exc}"})
    return patched, {"errors": errors, "warnings": warnings}


@router.post("/{cartridge_id}/atlas/edge-candidates/mark-incorporated")
def mark_atlas_edge_incorporated(
    cartridge_id: str,
    body: EdgeMarkIncorporatedRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """次版下書きへ**保存済み**の辺を、採用済み候補の反映として記録する。

    教員の ``PUT draft`` が成功した**後**に呼ばれる契約。無向ペアが現在の下書きの
    ``edges`` に実在しない場合は 409（PUT より前に呼ばれた誤順序を弾く）。

    判断の状態は変えない（``accepted`` のまま）。gap 側と違って「下書きのどこへ入ったか」
    を保持する列を持たない — **下書きに辺が実在すること自体が反映の印**であり、二重管理を
    しないため（設計書 §3）。実際に版へ入ったかどうかは凍結時に ``applied_version`` として
    刻印される（採用と反映の分離）。したがって本エンドポイントは検証と監査だけを行う。
    """
    edge_key = str(body.edge_key or "").strip()
    _assert_edge_belongs_to_domain(edge_key, cartridge_id)
    _domain, from_id, to_id = edge_schema.parse_edge_key(edge_key)

    session = _session()
    try:
        decision = edge_store.get_decision(session, edge_key)
        if decision is None:
            raise HTTPException(status_code=404, detail=_DETAIL_DECISION_NOT_FOUND)
        if decision["status"] != edge_schema.DECISION_STATUS_ACCEPTED:
            raise HTTPException(status_code=409, detail=_DETAIL_NOT_ACCEPTED)

        draft_row = _draft_or_409(session, cartridge_id)
        draft_pairs = {
            edge_schema.undirected_pair(
                getattr(edge, "from_id", ""), getattr(edge, "to_id", "")
            )
            for edge in getattr(draft_row["skeleton"], "edges", ()) or ()
        }
        if edge_schema.undirected_pair(from_id, to_id) not in draft_pairs:
            raise HTTPException(status_code=409, detail=_DETAIL_EDGE_NOT_IN_DRAFT)
    finally:
        session.close()

    _record_edge_event(
        edge_key,
        edge_schema.DECISION_STATUS_ACCEPTED,
        edge_schema.DECISION_STATUS_ACCEPTED,
        current_user.get("id"),
        {
            "action": edge_schema.AUDIT_ACTION_MARK_INCORPORATED,
            "cartridge_id": cartridge_id,
        },
    )
    return {"cartridge_id": cartridge_id, "decision": decision}
