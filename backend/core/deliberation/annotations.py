"""候補注釈の検証・status 遷移・コミットルーティング（設計書 §5・§6・§7）。

W2「確定は人間・AI は候補のみ」/ W3「evidence-based」/ W4「情報を落とさない」を
本モジュールが構造的に守る:

- LLM が対話中に提案した候補注釈は必ず :func:`validate_candidate_item` を通し、
  ``kind`` が語彙外、``evidence`` が空、``reason`` が空のいずれかなら **書き込まない**
  （W3。中途半端な根拠の注釈を候補にすら残さない）。
- 生成した候補は常に ``status='candidate'``（``store.create_annotation`` が固定）。
- コミット（確定）は :func:`commit` が既存構造へルーティングする（設計書 §5 表）。
  v1 は4経路:

  1. ``interpretation`` → C層 ``component_explanations``（``kind='personal'``）。
     ``theory_component`` のみ対応（component_explanations は component_id 必須のため）。
     component の ``course_id`` が NULL（document のみに紐づき、コース未登録）の場合は
     コミット不可（component_explanations.course_id は NOT NULL の FK）。
  2. ``meaning``/``decomposition`` → ``theory_components.summary``/``teacher_notes`` の更新。
     ``theory_component`` のみ対応。
  3. ``identity`` → W-β ``core.deliberation.identity_links.create_candidate``（confidence
     引数付き）を呼ぶ。identity_link 自体は candidate のまま（KN-3。確定は別途
     ``POST /identity-links/{id}/confirm``）だが、この注釈自体は ``committed`` になる
     （教員が「これを正式な同一性リンク候補として起こす」と判断したという事実の記録）。
  4. ``standardization`` → Phase S（知識ネットワークビジョン §3 修正③・migration 050）で
     追加された L層 ``library_entries.standardization_status`` の更新。``shared_part``
     （domain スコープ）のみ対応。この列はガバナンス列であり draft の
     ``revision``（楽観ロック）とは独立に更新する（本文編集とは別の関心事という §5.5 の
     決定）。教員が「この共通部品の標準化判定を候補どおり確定する」と判断した事実の記録。

  ``positioning_note``（D層起動。本層のスコープ外）は v1 で commit 非対応。commit 呼び出しは
  :class:`CommitRoutingError`（``kind='unsupported'``）を送出し、dismiss のみ許可する
  （正直に「後続フェーズ」と伝える）。

本モジュールは FastAPI にも ``routes``/``services`` にも依存しない（開発ルール2）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core.deliberation import identity_links, refs, store as store_mod
from core.figure_presentation import (
    commit_reviewed_analysis,
    normalize_figure_analysis_candidate,
)
from core.deliberation.schema import (
    ANNOTATION_KIND_DECOMPOSITION,
    ANNOTATION_KIND_IDENTITY,
    ANNOTATION_KIND_INTERPRETATION,
    ANNOTATION_KIND_MEANING,
    ANNOTATION_KIND_STANDARDIZATION,
    ANNOTATION_KINDS,
    ANNOTATION_KINDS_COMMIT_UNSUPPORTED,
    ANNOTATION_STATUS_CANDIDATE,
    ANNOTATION_STATUS_COMMITTED,
    ANNOTATION_STATUS_DISMISSED,
    ELEMENT_FIGURE,
    ELEMENT_SHARED_PART,
    ELEMENT_THEORY_COMPONENT,
    SCOPE_DOCUMENT,
    SCOPE_DOMAIN,
    ElementRef,
    ElementResolutionError,
)
from core.library.schema import STANDARDIZATION_STATUSES

logger = logging.getLogger(__name__)


class CommitRoutingError(Exception):
    """コミットルーティングの失敗。``kind`` を API 層が HTTP ステータスへマップする。

    ``kind``: ``'unsupported'``（v1 未対応の組み合わせ・422）/ ``'invalid'``
    （リクエスト不備・422）/ ``'not_found'``（対象が存在しない・404）/
    ``'conflict'``（既に candidate でない・409）。
    """

    def __init__(self, message: str, *, kind: str = "unsupported") -> None:
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# W3: 候補注釈の検証（evidence/reason 必須）
# ---------------------------------------------------------------------------


def _normalize_body(kind: str, body_raw: Any) -> dict | None:
    """kind ごとに body の形を正規化する。正規化できなければ ``None``。

    identity/standardization は構造化データ（shared_part_id 等）が必須なので、
    dict でなければ JSON 文字列としてパースを試みる（LLM 構造化出力の ``body`` は
    OpenAI Structured Outputs の制約を避けるため str 型で受け取る設計・dialogue.py 参照）。
    それ以外の kind は自由文として ``{"text": str}`` に包む。
    """
    if kind in (ANNOTATION_KIND_IDENTITY, ANNOTATION_KIND_STANDARDIZATION):
        parsed = body_raw if isinstance(body_raw, dict) else _try_parse_json(body_raw)
        if not isinstance(parsed, dict):
            return None
        if kind == ANNOTATION_KIND_IDENTITY and not str(parsed.get("shared_part_id") or "").strip():
            return None
        if kind == ANNOTATION_KIND_STANDARDIZATION and not str(parsed.get("standardization_status") or "").strip():
            return None
        return parsed

    if isinstance(body_raw, dict):
        text = str(body_raw.get("text") or "").strip()
    else:
        text = str(body_raw or "").strip()
    if not text:
        return None
    return {"text": text}


def _try_parse_json(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def validate_candidate_item(item: dict) -> tuple[dict | None, str | None]:
    """LLM が返した候補注釈1件を検証する（純粋関数・DB 非依存）。

    Returns
    -------
    (正規化済み dict, None) — 採用可。dict は ``{"kind","body","evidence","reason","confidence"}``。
    (None, エラー理由) — 不採用（W3: kind 語彙外 / evidence 空 / reason 空 / body 不正のいずれか）。
    """
    kind = str((item or {}).get("kind") or "").strip()
    if kind not in ANNOTATION_KINDS:
        return None, f"unknown kind: {kind!r}"

    evidence_raw = (item or {}).get("evidence")
    evidence = (
        [str(e).strip() for e in evidence_raw if str(e or "").strip()]
        if isinstance(evidence_raw, list)
        else []
    )
    if not evidence:
        return None, "evidence is required (W3: evidence-based)"

    reason = str((item or {}).get("reason") or "").strip()
    if not reason:
        return None, "reason is required (W3: evidence-based)"

    confidence_raw = (item or {}).get("confidence")
    try:
        confidence = float(confidence_raw) if confidence_raw is not None else None
    except (TypeError, ValueError):
        confidence = None

    body = _normalize_body(kind, (item or {}).get("body"))
    if body is None:
        return None, f"body could not be normalized for kind={kind!r}"

    return (
        {"kind": kind, "body": body, "evidence": evidence, "reason": reason, "confidence": confidence},
        None,
    )


def create_candidates_from_dialogue(
    ref: ElementRef,
    *,
    session_id: str,
    raw_items: list[dict],
    created_by: str | None,
) -> list[dict]:
    """対話ターンで LLM が提案した候補注釈のうち、検証を通ったものだけを永続化する。

    W3 を満たさない項目は黙って破棄する（応答本文自体は別途返るため、対話の価値は
    落ちない。中途半端な根拠の注釈を候補として残さないほうが P4「情報を落とさない」の
    精神——ここでいう情報とは「検証済みの候補」——に適う）。
    """
    created: list[dict] = []
    for raw in raw_items or []:
        normalized, error = validate_candidate_item(raw)
        if normalized is None:
            logger.info("deliberation candidate annotation dropped: %s", error)
            continue
        if ref.element_type == ELEMENT_FIGURE and normalized["kind"] in (
            ANNOTATION_KIND_MEANING,
            ANNOTATION_KIND_INTERPRETATION,
            ANNOTATION_KIND_DECOMPOSITION,
        ):
            # Free-form vision chat is useful as a reply but is not a safe
            # source for confirmed functions/ports.  Figure decomposition
            # candidates must come from the dedicated structured re-analysis.
            logger.info(
                "dialogue-generated figure annotation dropped: kind=%s",
                normalized["kind"],
            )
            continue
        created.append(
            store_mod.create_annotation(
                ref,
                kind=normalized["kind"],
                body=normalized["body"],
                evidence=normalized["evidence"],
                reason=normalized["reason"],
                confidence=normalized["confidence"],
                session_id=session_id,
                created_by=created_by,
            )
        )
    return created


# ---------------------------------------------------------------------------
# 却下（status 遷移のみ・W4）
# ---------------------------------------------------------------------------


def dismiss(annotation_id: str, *, updated_by: str | None) -> dict | None:
    """候補注釈を却下する（``candidate → dismissed``。行削除はしない）。"""
    return store_mod.set_annotation_status(
        annotation_id,
        status=ANNOTATION_STATUS_DISMISSED,
        committed_target=None,
        updated_by=updated_by,
    )


# ---------------------------------------------------------------------------
# コミットルーティング（設計書 §5 表・3経路のみ）
# ---------------------------------------------------------------------------


def _commit_interpretation(annotation: dict, current_user_id: str | None) -> dict:
    if annotation["scope"] != SCOPE_DOCUMENT or annotation["element_type"] != ELEMENT_THEORY_COMPONENT:
        raise CommitRoutingError(
            "interpretation のコミットは theory_component のみ対応です（後続フェーズ）",
            kind="unsupported",
        )
    component_id = annotation["element_id"]

    session = get_session()
    try:
        row = session.execute(
            sa_text("SELECT course_id FROM theory_components WHERE id = CAST(:id AS uuid) LIMIT 1"),
            {"id": component_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise CommitRoutingError(f"theory_component not found: {component_id}", kind="not_found")
    course_id = row[0]
    if not course_id:
        raise CommitRoutingError(
            "この theory_component はコースに紐づいていないため、"
            "解釈のコミット先（component_explanations）を作成できません",
            kind="unsupported",
        )

    body_text = str((annotation.get("body") or {}).get("text") or "")

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                INSERT INTO component_explanations
                    (component_id, course_id, kind, author_id, title, body,
                     backing_claims, origin_query_id, shared)
                VALUES
                    (CAST(:cid AS uuid), :course_id, 'personal', CAST(:author AS uuid),
                     '', :body, CAST('[]' AS jsonb), '', FALSE)
                RETURNING id
                """
            ),
            {"cid": component_id, "course_id": course_id, "author": current_user_id, "body": body_text},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return {"type": "component_explanation", "id": str(row[0]), "component_id": component_id}


def _commit_meaning_or_decomposition(annotation: dict, current_user_id: str | None) -> dict:
    if (
        annotation["scope"] == SCOPE_DOCUMENT
        and annotation["element_type"] == ELEMENT_FIGURE
        and annotation["kind"] == ANNOTATION_KIND_DECOMPOSITION
    ):
        try:
            target = commit_reviewed_analysis(
                str(annotation.get("document_id") or ""),
                str(annotation.get("element_id") or ""),
                annotation.get("body") or {},
                current_user_id,
                str(annotation.get("id") or ""),
            )
        except ValueError as exc:
            raise CommitRoutingError(str(exc), kind="invalid") from exc
        if target is None:
            raise CommitRoutingError(
                f"figure not found: {annotation.get('element_id')}", kind="not_found"
            )
        return target
    if annotation["scope"] != SCOPE_DOCUMENT or annotation["element_type"] != ELEMENT_THEORY_COMPONENT:
        raise CommitRoutingError(
            "この意味づけ・内訳候補には確定先がありません",
            kind="unsupported",
        )
    column = "summary" if annotation["kind"] == ANNOTATION_KIND_MEANING else "teacher_notes"
    text_value = str((annotation.get("body") or {}).get("text") or "")
    component_id = annotation["element_id"]

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                UPDATE theory_components
                SET {column} = :value, updated_at = now()
                WHERE id = CAST(:id AS uuid)
                RETURNING id
                """
            ),
            {"value": text_value, "id": component_id},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    if not row:
        raise CommitRoutingError(f"theory_component not found: {component_id}", kind="not_found")
    return {"type": "theory_component", "id": str(row[0]), "field": column}


def _commit_identity(annotation: dict, current_user_id: str | None) -> dict:
    if annotation["scope"] != SCOPE_DOCUMENT:
        raise CommitRoutingError("identity source must be a document-scoped instance", kind="invalid")
    body = annotation.get("body") or {}
    shared_part_id = str(body.get("shared_part_id") or "").strip()
    if not shared_part_id:
        raise CommitRoutingError("identity annotation body is missing shared_part_id", kind="invalid")
    # 人間直叩き経路（POST /identity-links）と同じく shared_part の実在検証を通す。
    # LLM 候補由来の body には存在しない ID や UUID 非形式が入りうるため、無検証で
    # identity_links.create_candidate（CAST :shared_part_id AS uuid + FK）へ渡すと
    # IntegrityError/DataError が API 層の CommitRoutingError 捕捉を素通りして 500 になる。
    # 解決失敗は CommitRoutingError に変換し、API 層で 404/422 にマップさせる。
    try:
        shared_part_ref = refs.resolve(ELEMENT_SHARED_PART, shared_part_id)
    except ElementResolutionError as exc:
        kind = "invalid" if getattr(exc, "kind", "not_found") == "invalid" else "not_found"
        raise CommitRoutingError(str(exc), kind=kind) from exc
    local_expression = body.get("local_expression")
    local_expression = local_expression if isinstance(local_expression, dict) else {}

    instance_ref = ElementRef(
        scope=SCOPE_DOCUMENT,
        element_type=annotation["element_type"],
        element_id=annotation["element_id"],
        document_id=annotation["document_id"],
    )
    link = identity_links.create_candidate(
        instance_ref,
        shared_part_ref.element_id,
        local_expression=local_expression,
        evidence=annotation.get("evidence") or [],
        reason=annotation.get("reason") or "",
        confidence=annotation.get("confidence"),
        created_by=current_user_id,
    )
    return {"type": "identity_link", "id": link["id"], "status": link["status"]}


def _commit_standardization(annotation: dict, current_user_id: str | None) -> dict:
    """standardization 候補を確定し、L層 ``library_entries.standardization_status`` へ
    反映する（Phase S・migration 050）。

    ``standardization_status`` はガバナンス列であり、draft 本文編集の楽観ロック
    （``revision``）とは独立に更新する——``revision`` は増やさない（§5.5 の決定。
    本文編集と標準化判定の確定は別の関心事のため）。
    """
    if annotation["scope"] != SCOPE_DOMAIN or annotation["element_type"] != ELEMENT_SHARED_PART:
        raise CommitRoutingError(
            "standardization のコミットは shared_part（共通部品）のみ対応です",
            kind="invalid",
        )
    body = annotation.get("body") or {}
    status = str(body.get("standardization_status") or "").strip()
    if status not in STANDARDIZATION_STATUSES:
        raise CommitRoutingError(
            f"standardization annotation body has invalid standardization_status: {status!r}",
            kind="invalid",
        )
    entry_id = annotation["element_id"]

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                UPDATE library_entries
                SET standardization_status = :status, updated_at = now()
                WHERE id = CAST(:id AS uuid)
                RETURNING id
                """
            ),
            {"status": status, "id": entry_id},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    if not row:
        raise CommitRoutingError(f"library entry not found: {entry_id}", kind="not_found")
    return {"type": "library_entry", "id": str(row[0]), "field": "standardization_status", "value": status}


_COMMIT_ROUTES = {
    ANNOTATION_KIND_INTERPRETATION: _commit_interpretation,
    ANNOTATION_KIND_MEANING: _commit_meaning_or_decomposition,
    ANNOTATION_KIND_DECOMPOSITION: _commit_meaning_or_decomposition,
    ANNOTATION_KIND_IDENTITY: _commit_identity,
    ANNOTATION_KIND_STANDARDIZATION: _commit_standardization,
}


def commit_capability(annotation: dict) -> tuple[bool, str]:
    """Return whether the UI may offer confirm for this exact candidate."""
    kind = str(annotation.get("kind") or "")
    element_type = str(annotation.get("element_type") or "")
    scope = str(annotation.get("scope") or "")
    if kind == ANNOTATION_KIND_INTERPRETATION:
        if element_type == ELEMENT_THEORY_COMPONENT and scope == SCOPE_DOCUMENT:
            return True, ""
        return False, "この候補は現在の要素には反映できません"
    if kind in (ANNOTATION_KIND_MEANING, ANNOTATION_KIND_DECOMPOSITION):
        if element_type == ELEMENT_THEORY_COMPONENT and scope == SCOPE_DOCUMENT:
            return True, ""
        if (
            kind == ANNOTATION_KIND_DECOMPOSITION
            and element_type == ELEMENT_FIGURE
            and scope == SCOPE_DOCUMENT
        ):
            if normalize_figure_analysis_candidate(annotation.get("body") or {}) is not None:
                return True, ""
            return False, "旧形式の候補です。図を再解析すると構造化候補を確定できます"
        return False, "この候補は現在の要素には反映できません"
    if kind == ANNOTATION_KIND_IDENTITY:
        return (scope == SCOPE_DOCUMENT, "" if scope == SCOPE_DOCUMENT else "対象外の候補です")
    if kind == ANNOTATION_KIND_STANDARDIZATION:
        supported = element_type == ELEMENT_SHARED_PART and scope == SCOPE_DOMAIN
        return supported, "" if supported else "対象外の候補です"
    return False, "この候補はメモとして保持できますが、確定先は未実装です"


def _route_commit(annotation: dict, current_user_id: str | None) -> dict:
    kind = annotation["kind"]
    if kind in ANNOTATION_KINDS_COMMIT_UNSUPPORTED:
        raise CommitRoutingError(
            "この種別のコミットは未対応（後続フェーズ）", kind="unsupported",
        )
    handler = _COMMIT_ROUTES.get(kind)
    if handler is None:
        raise CommitRoutingError(f"unknown kind: {kind!r}", kind="invalid")
    return handler(annotation, current_user_id)


def commit(annotation_id: str, *, current_user_id: str | None) -> dict:
    """候補注釈を確定し、既存構造へルーティングする（W2: 常に candidate からのみ）。

    Returns
    -------
    dict
        更新後の注釈行（``store.set_annotation_status`` の戻り値。``committed_target``
        にルーティング先の参照が入る）。

    Raises
    ------
    CommitRoutingError
        対象が存在しない（``kind='not_found'``）/ 既に candidate でない
        （``kind='conflict'``）/ v1 未対応の組み合わせ（``kind='unsupported'``/``'invalid'``）。
    """
    annotation = store_mod.get_annotation(annotation_id)
    if annotation is None:
        raise CommitRoutingError(f"annotation not found: {annotation_id}", kind="not_found")
    if annotation["status"] != ANNOTATION_STATUS_CANDIDATE:
        raise CommitRoutingError("annotation is not a candidate (already decided)", kind="conflict")

    committed_target = _route_commit(annotation, current_user_id)

    updated = store_mod.set_annotation_status(
        annotation_id,
        status=ANNOTATION_STATUS_COMMITTED,
        committed_target=committed_target,
        updated_by=current_user_id,
    )
    if updated is None:  # pragma: no cover - race condition guard
        raise CommitRoutingError("annotation is not a candidate (already decided)", kind="conflict")
    return updated
