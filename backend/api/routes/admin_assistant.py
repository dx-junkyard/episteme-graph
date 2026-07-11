"""横断ユーティリティ層（Admin Copilot）API — /api/admin/assistant/...

管理画面の統合 AI アシスタント。3 モード（guidance / locate / action）を単一チャットで扱う。

設計原則（docs/features/admin_assistant_design.md §2）:
  - P1 権限を越えない（fail-closed）: 説明も道案内も代行も capability registry に登録され、
    かつ現在ユーザーのロールで許可された操作のみ。判定はサーバ側（フロント表示を信頼しない）。
  - P2 破壊的操作（reversible=False）は必ず確認ゲート。無確認で実行しない。
  - P3 情報を落とさない: apply 前に before スナップショット、取り消しは状態遷移。
  - P4 断定・捏造しない: 説明は登録済み KB に基づき根拠併記、無ければ「未整備」。
  - P5 監査必須: apply / revert / confirm を theory_review_events
    （entity_type='assistant_action'）に記録。
  - P6 同期パスを重くしない: chat は 1 LLM コール上限、失敗時は非LLMヒューリスティックへ縮退。
  - P7 既存 A/B/C/D 層のコードを変更しない（既存 API を呼ぶ側）。
  - P8 道案内は誘導まで（画面遷移＋点灯のみ。値入力・送信・保存は本人）。
"""

from __future__ import annotations

import datetime
import json
import logging
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text as sa_text

from dependencies import _get_current_user, _require_teacher  # noqa: F401
from core.config import get_settings
from core.postgres import get_session as _pg_session
from core.admin_assistant import capabilities as caps
from core.admin_assistant import intent as intent_mod
from core.admin_assistant import knowledge as kb
from core.admin_assistant import action_store
from core.admin_assistant.actions import (
    ActionArgError,
    ActionContext,
    ActionError,
    ActionTargetError,
    get_handler,
)
from core.admin_assistant.schema import (
    INTENT_ACTION,
    INTENT_CLARIFY,
    INTENT_GUIDANCE,
    INTENT_LOCATE,
    INTENT_STATUS_QUERY,
)
from core.status import projector as status_projector
from core.status import schema as status_schema
from schemas import (
    AssistantActionPlan,
    AssistantActionRequest,
    AssistantActionResponse,
    AssistantActionSummary,
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantLocatePlan,
    AssistantLocateStep,
    AssistantRevertResponse,
)

logger = logging.getLogger(__name__)

# admin.router（prefix=/api/admin）に include される
admin_router = APIRouter(prefix="/assistant", tags=["Admin Assistant"])

_ROLE_LABELS = {"TEACHER": "教員", "SYSTEM_ADMIN": "システム管理者", "STUDENT": "学生"}
_TARGET_LABELS = {
    "course": "コース",
    "material": "教材",
    "chunk": "チャンク",
    "cartridge": "カートリッジ",
    "user": "ユーザー",
}
# target_type -> screen_context.selection のキー
_TARGET_SELECTION_KEY = {
    "course": "course_id",
    "material": "material_id",
    "chunk": "chunk_id",
    "cartridge": "cartridge_id",
}

# 1 ユーザー 1 日あたりの LLM コール上限（P6）。プロセス内カウンタ（MVP。DB を汚さない）。
_DAILY_LLM_CALLS: dict[str, tuple] = {}


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


def _record_assistant_event(
    entity_id: str,
    old_status: str,
    new_status: str,
    user_id: str | None,
    metadata: dict | None = None,
) -> None:
    """theory_review_events への監査記録（P5。C/D 層と同型・entity_type 拡張のみ）。"""
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO theory_review_events
                (entity_type, entity_id, old_status, new_status, changed_by, metadata)
                VALUES ('assistant_action', :entity_id, :old_status, :new_status,
                        CAST(:changed_by AS uuid), CAST(:metadata AS jsonb))
            """),
            {
                "entity_id": entity_id,
                "old_status": old_status or "",
                "new_status": new_status or "",
                "changed_by": user_id or None,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to record assistant_action event for %s", entity_id, exc_info=True)
    finally:
        session.close()


def _assistant_model() -> str | None:
    settings = get_settings()
    return (
        getattr(settings, "assistant_llm_model", "")
        or getattr(settings, "llm_fast_model", "")
        or None
    )


def _reserve_llm_quota(user_id: str) -> bool:
    """本日の LLM コール枠が残っていれば 1 消費して True。上限なら False（heuristic へ縮退）。"""
    settings = get_settings()
    cap = int(getattr(settings, "assistant_max_calls_per_day", 20) or 0)
    if cap <= 0:
        return False
    today = datetime.date.today().isoformat()
    day, count = _DAILY_LLM_CALLS.get(user_id, (today, 0))
    if day != today:
        day, count = today, 0
    if count >= cap:
        _DAILY_LLM_CALLS[user_id] = (day, count)
        return False
    _DAILY_LLM_CALLS[user_id] = (day, count + 1)
    return True


def _target_from_context(cap, screen_context: dict) -> dict:
    selection = (screen_context or {}).get("selection") or {}
    key = _TARGET_SELECTION_KEY.get(cap.target_type)
    tid = selection.get(key) if key else None
    return {"type": cap.target_type, "id": tid}


def _navigate(screen: str) -> list:
    return [{"type": "navigate", "screen": screen}] if screen else []


def _locate_plan_for(cap, screen_context: dict) -> AssistantLocatePlan | None:
    if not cap.locate_steps:
        return None
    ctx = dict((screen_context or {}).get("selection") or {})
    steps = [
        AssistantLocateStep(
            screen=s["screen"],
            anchor_id=s["anchor_id"],
            hint=s.get("hint", ""),
            precondition=s.get("precondition"),
        )
        for s in cap.locate_steps_as_dicts(ctx)
    ]
    return AssistantLocatePlan(capability_id=cap.id, steps=steps)


# ---------------------------------------------------------------------------
# 各 intent の応答組み立て
# ---------------------------------------------------------------------------


def _denial_response(cap, role: str) -> AssistantChatResponse:
    """権限外（P1）。説明も道案内も代行もしない honest な拒否。"""
    need = _ROLE_LABELS.get(cap.required_role, cap.required_role)
    return AssistantChatResponse(
        answer=f"「{cap.title}」は {need} のみが実行できる操作です。"
        "あなたの権限では実行できません。",
        intent=INTENT_GUIDANCE,
    )


def _guidance_response(message: str, role: str, cap) -> AssistantChatResponse:
    allowed = caps.capabilities_for(role)
    results = kb.search(message, allowed, limit=3)
    citations: list = []
    parts: list[str] = []
    screen = ""

    primary = None
    if cap is not None:
        primary = {
            "capability_id": cap.id,
            "screen": cap.screen,
            "title": cap.title,
            "citation": "",
            "documented": False,
        }
        section = kb.section_for_howto(cap.howto_doc)
        if section:
            primary.update(
                title=section["title"],
                body=section["body"],
                citation=f"admin_operations/{section['file']}#{section['anchor']}",
                documented=bool(section["body"]),
            )
    # cap の節が無ければ検索トップを主にする。
    if primary is None or not primary.get("documented"):
        if results:
            primary = results[0]

    if primary and primary.get("documented"):
        parts.append(primary["body"])
        screen = primary.get("screen", "")
        if primary.get("citation"):
            citations.append({"doc": primary["citation"]})
    elif primary is not None:
        # KB 未整備 — 手順をでっち上げない（P4）。
        parts.append(
            f"「{primary.get('title', '')}」の詳しい手順はまだ整備されていません。"
            f"操作は「{primary.get('screen', '')}」タブで行います。"
        )
        screen = primary.get("screen", "")
    else:
        examples = "・".join(c.title for c in allowed[:5])
        parts.append(
            "できる操作の例: " + examples + "。"
            "知りたい操作を具体的に教えてください（例: コースの公開、教材のアップロード）。"
        )

    # 追加の関連候補を軽く併記（根拠つき）。
    for r in results[1:3]:
        if r.get("citation"):
            citations.append({"doc": r["citation"]})

    return AssistantChatResponse(
        answer="\n\n".join(p for p in parts if p),
        intent=INTENT_GUIDANCE,
        next_actions=_navigate(screen),
        citations=citations,
    )


def _locate_response(role: str, cap, screen_context: dict) -> AssistantChatResponse:
    plan = _locate_plan_for(cap, screen_context)
    if plan is None:
        # 点灯先が無い → 説明に縮退（捏造しない, P4）。
        return AssistantChatResponse(
            answer=f"「{cap.title}」は「{cap.screen}」タブで行います。",
            intent=INTENT_GUIDANCE,
            next_actions=_navigate(cap.screen),
        )
    first_screen = plan.steps[0].screen if plan.steps else cap.screen
    return AssistantChatResponse(
        answer=f"「{cap.title}」の場所をご案内します。画面を切り替えて、操作すべき箇所を"
        "順に光らせます。値の入力や実行はご自身で行ってください。",
        intent=INTENT_LOCATE,
        locate_plan=plan,
        next_actions=_navigate(first_screen),
    )


def _infer_args(cap, message: str) -> tuple[dict, str | None]:
    """capability ごとに message から args を推定。clarify が必要なら理由文言を返す。"""
    if cap.id in ("course.set_visibility", "materials.set_visibility"):
        vis = intent_mod.parse_visibility(message)
        if not vis:
            return {}, "開示範囲を public（全体）/ group（グループ限定）/ private（自分のみ）の" \
                       "どれにしますか？"
        return {"visibility": vis}, None
    return {}, None


def _action_response(message: str, role: str, cap, screen_context: dict) -> AssistantChatResponse:
    if not cap.is_action():
        return _guidance_response(message, role, cap)

    target = _target_from_context(cap, screen_context)
    tlabel = _TARGET_LABELS.get(cap.target_type, "対象")

    # 対象が未特定 → 代行できないので聞き返す（実行に踏み込まない）。
    if cap.target_type and not target.get("id"):
        return AssistantChatResponse(
            answer=f"どの{tlabel}を対象にしますか？「{cap.screen}」タブで{tlabel}を選んでから"
            "もう一度指示してください。",
            intent=INTENT_CLARIFY,
            next_actions=_navigate(cap.screen),
        )

    args, clarify = _infer_args(cap, message)
    if clarify:
        return AssistantChatResponse(answer=clarify, intent=INTENT_CLARIFY)

    supported = get_handler(cap.id) is not None
    plan = AssistantActionPlan(
        capability_id=cap.id,
        title=cap.title,
        target=target,
        args=args,
        reversible=cap.reversible,
        confirm_required=cap.confirm,
        supported=supported,
    )

    if not supported:
        # 段階登録（P1）: 代行 handler 未実装。画面での操作に誘導する。
        return AssistantChatResponse(
            answer=f"「{cap.title}」の自動実行は現在このアシスタントでは未対応です。"
            f"「{cap.screen}」タブで操作してください。場所をご案内することもできます。",
            intent=INTENT_ACTION,
            action_plan=plan,
            next_actions=_navigate(cap.screen),
        )

    if cap.confirm:
        answer = (
            f"「{cap.title}」を実行します。これは取り消せない操作です。"
            "よろしければ確認のうえ実行してください。"
        )
    else:
        answer = f"「{cap.title}」を実行できます。よろしければ実行します（あとで戻せます）。"
    return AssistantChatResponse(answer=answer, intent=INTENT_ACTION, action_plan=plan)


_MATERIAL_STATE_LABELS = {
    status_schema.MATERIAL_STATE_UPLOADED: "アップロード済み（未解析）",
    status_schema.MATERIAL_STATE_CHUNKING: "解析待ち",
    status_schema.MATERIAL_STATE_ANALYZING: "解析実行中",
    status_schema.MATERIAL_STATE_ANALYZED: "解析完了",
    status_schema.MATERIAL_STATE_ANALYSIS_FAILED: "解析失敗",
    status_schema.MATERIAL_STATE_UNKNOWN: "状態不明",
}
_SCRIPT_STATUS_LABELS = {
    status_schema.SCRIPT_STATUS_DRAFT: "未生成",
    status_schema.SCRIPT_STATUS_PARTIAL: "一部生成",
    status_schema.SCRIPT_STATUS_GENERATED: "生成済み",
}
_AUDIO_STATUS_LABELS = {
    status_schema.AUDIO_STATUS_NONE: "未生成",
    status_schema.AUDIO_STATUS_PARTIAL: "一部生成",
    status_schema.AUDIO_STATUS_GENERATED: "生成済み",
}

# 「対応が必要」とみなす教材状態（詳細列挙の対象。解析完了は詳細列挙しない）。
_MATERIAL_NEEDS_ATTENTION = {
    status_schema.MATERIAL_STATE_ANALYZING,
    status_schema.MATERIAL_STATE_ANALYSIS_FAILED,
    status_schema.MATERIAL_STATE_CHUNKING,
    status_schema.MATERIAL_STATE_UPLOADED,
    status_schema.MATERIAL_STATE_UNKNOWN,
}


def _material_status_line(title: str, ms) -> str:
    label = _MATERIAL_STATE_LABELS.get(ms.state, ms.state)
    detail = ""
    if ms.state == status_schema.MATERIAL_STATE_ANALYZING and ms.stage:
        detail = f"（stage: {ms.stage}）"
    elif ms.state == status_schema.MATERIAL_STATE_ANALYSIS_FAILED and ms.reason:
        detail = f"（{ms.reason}）"
    when = f" — {ms.updated_at}" if ms.updated_at else ""
    return f"教材『{title}』: {label}{detail}{when}"


def _course_status_line(title: str, cs) -> str:
    script = _SCRIPT_STATUS_LABELS.get(cs.script_status, cs.script_status)
    audio = _AUDIO_STATUS_LABELS.get(cs.audio_status, cs.audio_status)
    published = "公開済み" if cs.published else "未公開"
    return f"コース『{title}』: 原稿 {script} / 音声 {audio} / {published}"


def _status_query_response(message: str, current_user: dict) -> AssistantChatResponse:
    """状態照会（guidance 相当・DB 非変更）。core.status.projector を直接呼び、

    自分が所有する教材・コースのみを事実文で回答する（P4: 根拠併記・断定捏造しない）。
    LLM は呼ばない（同期パスを重くしない, P6）。DB アクセス失敗時は fail-closed で
    「取得できませんでした」に縮退し、状態を捏造しない（S5）。
    """
    uid = str(current_user.get("id") or "")
    citations: list = []
    try:
        session = _pg_session()
        try:
            doc_rows = session.execute(
                sa_text(
                    "SELECT id::text, title FROM documents WHERE uploaded_by = CAST(:uid AS uuid) "
                    "ORDER BY updated_at DESC"
                ),
                {"uid": uid},
            ).mappings().fetchall()
            course_rows = session.execute(
                sa_text(
                    "SELECT id, data FROM learning_courses WHERE user_id = CAST(:uid AS uuid) "
                    "ORDER BY updated_at DESC"
                ),
                {"uid": uid},
            ).mappings().fetchall()

            materials = []
            for row in doc_rows:
                ms = status_projector.project_material_status(session, row["id"])
                title = row["title"] or row["id"]
                materials.append((title, ms))

            courses = []
            for row in course_rows:
                cs = status_projector.project_course_status(session, row["id"])
                data = row["data"] if isinstance(row["data"], dict) else {}
                title = data.get("title") or row["id"]
                courses.append((title, cs))
        finally:
            session.close()
    except Exception:
        logger.warning("status_query: failed to read status projections", exc_info=True)
        return AssistantChatResponse(
            answer="状態を取得できませんでした。",
            intent=INTENT_STATUS_QUERY,
        )

    if not materials and not courses:
        return AssistantChatResponse(
            answer="教材・コースが見つかりませんでした。",
            intent=INTENT_STATUS_QUERY,
        )

    total = len(materials) + len(courses)
    lines: list[str] = []

    if total > 8:
        # 件数が多いときは状態別サマリーを先に出し、対応が必要なものだけ詳細列挙する（P4/S5）。
        mat_counts = Counter(ms.state for _, ms in materials)
        if materials:
            summary = "・".join(
                f"{_MATERIAL_STATE_LABELS.get(state, state)}{count}"
                for state, count in mat_counts.items()
            )
            lines.append(f"教材{len(materials)}件: {summary}")
        if courses:
            lines.append(f"コース{len(courses)}件。")
        detail_materials = [(t, ms) for t, ms in materials if ms.state in _MATERIAL_NEEDS_ATTENTION]
        for title, ms in detail_materials:
            lines.append(_material_status_line(title, ms))
            citations.append({"doc": f"status:material:{ms.material_id or ms.document_id}"})
        for title, cs in courses:
            lines.append(_course_status_line(title, cs))
            citations.append({"doc": f"status:course:{cs.course_id}"})
    else:
        for title, ms in materials:
            lines.append(_material_status_line(title, ms))
            citations.append({"doc": f"status:material:{ms.material_id or ms.document_id}"})
        for title, cs in courses:
            lines.append(_course_status_line(title, cs))
            citations.append({"doc": f"status:course:{cs.course_id}"})

    return AssistantChatResponse(
        answer="\n".join(lines),
        intent=INTENT_STATUS_QUERY,
        citations=citations,
    )


# ---------------------------------------------------------------------------
# 8.1 POST /chat
# ---------------------------------------------------------------------------


@admin_router.post("/chat", response_model=AssistantChatResponse)
def assistant_chat(
    body: AssistantChatRequest,
    current_user: dict = Depends(_require_teacher),
) -> AssistantChatResponse:
    role = current_user["role"]
    screen_context = body.screen_context.model_dump() if body.screen_context else {}

    allow_llm = _reserve_llm_quota(str(current_user.get("id") or ""))
    res = intent_mod.classify(
        body.message,
        role,
        history=body.history,
        screen_context=screen_context,
        allow_llm=allow_llm,
        model=_assistant_model(),
    )

    cap = caps.get_capability(res.capability_id) if res.capability_id else None

    # P1: 解決した capability が権限外なら、説明も道案内も代行もしない。
    if cap is not None and not caps.can_access(cap.id, role):
        resp = _denial_response(cap, role)
        resp.source = res.source
        return resp

    if res.intent == INTENT_LOCATE and cap is not None:
        resp = _locate_response(role, cap, screen_context)
    elif res.intent == INTENT_ACTION and cap is not None:
        resp = _action_response(body.message, role, cap, screen_context)
    elif res.intent == INTENT_STATUS_QUERY:
        resp = _status_query_response(body.message, current_user)
    elif res.intent == INTENT_CLARIFY and not cap:
        resp = AssistantChatResponse(
            answer=res.answer
            or "もう少し詳しく教えてください。どの操作について知りたいですか？"
            "（例: コースの公開、教材のアップロード、原稿の書き換え）",
            intent=INTENT_CLARIFY,
        )
    else:
        # guidance（および cap 未解決の locate/action）は説明にまとめる。
        resp = _guidance_response(body.message, role, cap)

    resp.source = res.source
    return resp


# ---------------------------------------------------------------------------
# 8.2 POST /actions（代行実行）
# ---------------------------------------------------------------------------


@admin_router.post("/actions", response_model=AssistantActionResponse)
def assistant_execute_action(
    body: AssistantActionRequest,
    current_user: dict = Depends(_require_teacher),
) -> AssistantActionResponse:
    role = current_user["role"]
    user_id = str(current_user["id"])

    cap = caps.get_capability(body.capability_id)
    if cap is None or not cap.is_action():
        raise HTTPException(status_code=404, detail="unknown or non-action capability")

    # P1: fail-closed の権限判定はサーバ側。
    if not caps.can_access(cap.id, role):
        raise HTTPException(status_code=403, detail="あなたの権限では実行できません")

    target_id = (body.target or {}).get("id")

    # scope=own_course は既存 helper に委譲（二重実装を避ける）。
    if cap.scope == "own_course":
        try:
            from services import user_owns_course

            if not target_id or not user_owns_course(user_id, target_id):
                raise HTTPException(status_code=403, detail="このコースの所有者ではありません")
        except HTTPException:
            raise
        except Exception:
            logger.warning("ownership check failed for %s", cap.id, exc_info=True)
            raise HTTPException(status_code=403, detail="所有権を確認できませんでした")

    # P2: reversible=False は confirm 必須。無確認では実行しない。
    if cap.confirm and not body.confirm:
        return AssistantActionResponse(
            action_id="",
            status="confirm_pending",
            capability_id=cap.id,
            reversible=cap.reversible,
            after=None,
            message=f"「{cap.title}」は取り消せない操作です。実行するには確認が必要です。",
        )

    handler = get_handler(cap.id)
    if handler is None:
        raise HTTPException(status_code=501, detail="この操作の代行は未対応です")

    ctx = ActionContext(user_id=user_id, role=role, target_id=target_id, args=body.args or {})

    try:
        before = handler.capture_before(ctx)
    except ActionTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ActionArgError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        after = handler.apply(ctx, before)
    except ActionArgError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ActionTargetError, ActionError) as exc:
        # 変更は適用されない。失敗も監査に残す（P5）。
        failed = action_store.create_action(
            user_id=user_id, capability_id=cap.id, screen=cap.screen,
            target_type=cap.target_type, target_id=target_id, args=body.args or {},
            before_snapshot=before, after_snapshot=None, reversible=cap.reversible,
            revert_spec=cap.revert, session_id=body.session_id, status="failed",
        )
        _record_assistant_event(failed["id"], "", "failed", user_id,
                                {"capability_id": cap.id, "error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    action = action_store.create_action(
        user_id=user_id, capability_id=cap.id, screen=cap.screen,
        target_type=cap.target_type, target_id=target_id, args=body.args or {},
        before_snapshot=before, after_snapshot=after, reversible=cap.reversible,
        revert_spec=cap.revert, session_id=body.session_id, status="applied",
    )
    _record_assistant_event(
        action["id"], "", "applied", user_id,
        {"capability_id": cap.id, "target": body.target, "args": body.args,
         "confirm": bool(body.confirm)},
    )
    return AssistantActionResponse(
        action_id=action["id"],
        status="applied",
        capability_id=cap.id,
        reversible=cap.reversible,
        after=after,
        message=f"「{cap.title}」を実行しました。"
        + ("" if cap.reversible else "（この操作は取り消せません）"),
    )


# ---------------------------------------------------------------------------
# 8.3 POST /actions/{action_id}/revert
# ---------------------------------------------------------------------------


@admin_router.post("/actions/{action_id}/revert", response_model=AssistantRevertResponse)
def assistant_revert_action(
    action_id: str,
    current_user: dict = Depends(_require_teacher),
) -> AssistantRevertResponse:
    user_id = str(current_user["id"])
    role = current_user["role"]

    action = action_store.get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="action not found")
    if action["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="他のユーザーの操作は取り消せません")
    if not action["reversible"]:
        raise HTTPException(status_code=409, detail="not_reversible")
    if action["status"] != "applied":
        raise HTTPException(status_code=409, detail=f"cannot revert action in status={action['status']}")

    cap = caps.get_capability(action["capability_id"])
    handler = get_handler(action["capability_id"]) if cap else None
    if handler is None:
        raise HTTPException(status_code=409, detail="この操作は取り消せません")

    ctx = ActionContext(
        user_id=user_id, role=role,
        target_id=action.get("target_id"), args=action.get("args") or {},
    )
    before = action.get("before_snapshot") or {}
    try:
        handler.revert(ctx, before)
    except ActionTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ActionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    action_store.mark_reverted(action_id)
    _record_assistant_event(action_id, "applied", "reverted", user_id,
                            {"capability_id": action["capability_id"]})
    return AssistantRevertResponse(
        action_id=action_id,
        status="reverted",
        restored=before,
        message="操作を取り消しました。",
    )


# ---------------------------------------------------------------------------
# 8.4 GET /actions（戻す履歴）
# ---------------------------------------------------------------------------


@admin_router.get("/actions", response_model=list[AssistantActionSummary])
def assistant_list_actions(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: dict = Depends(_require_teacher),
) -> list[AssistantActionSummary]:
    rows = action_store.list_actions(str(current_user["id"]), limit=limit)
    return [
        AssistantActionSummary(
            action_id=r["id"],
            capability_id=r["capability_id"],
            screen=r.get("screen", ""),
            target_type=r.get("target_type", ""),
            target_id=r.get("target_id"),
            reversible=bool(r.get("reversible", True)),
            status=r.get("status", "applied"),
            created_at=r.get("created_at", "") or "",
            reverted_at=r.get("reverted_at"),
        )
        for r in rows
    ]
