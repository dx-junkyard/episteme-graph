"""Episteme Graph — 学習エンドポイント (/api/learning)。"""

from __future__ import annotations

import base64
import logging
import re
import threading
import uuid
from contextlib import nullcontext
from dataclasses import asdict
from typing import Callable

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import text as sa_text

from dependencies import _get_current_user
from schemas import (
    ChunkContent,
    CourseCreateRequest,
    CourseUpdateRequest,
    LearningChatHistoryResponse,
    LearningChatRequest,
    LearningChatResponse,
    LearningCheckQuestionRequest,
    LearningCheckQuestionResponse,
    LearningCourseDetail,
    LearningCourseLayeredResponse,
    LearningCourseOut,
    LearningProgress,
    PersonalLayer,
    TopicMaterialResponse,
)
from services import (
    calculate_progress,
    check_prerequisites,
    confirm_anchor_trace,
    confirm_tension_trace,
    connect_tension_trace,
    detect_and_record_misconception,
    dismiss_anchor_trace,
    dismiss_tension_trace,
    get_accessible_course_data,
    get_anchor_digest,
    get_tension_digest,
    resolve_course_source_titles,
    course_deletion_notice,
    enroll_user_in_course,
    get_course_chunks_ordered,
    get_course_completion,
    get_course_data,
    get_course_live_llm_models,
    get_editable_course_data,
    get_viewable_course_data,
    get_chunk_passage,
    get_chunk_claim_refs,
    get_graph_element_context,
    get_interest_traces,
    get_personal_layer,
    get_trace_map_exclusion_flags,
    get_user_group_ids,
    log_unanswered_query,
    persist_chat_history,
    truncate_chat_and_supersede,
    recent_duplicate_ui_anchor_event as _recent_duplicate_ui_anchor_event_shared,
    record_internalization,
    record_interest_trace,
    record_learner_articulated_tension,
    record_student_stumble_event,
    record_topic_check_pass,
    resolve_document_access,
    resolve_interest_trace,
    save_course_data,
    set_trace_map_exclusion,
    delete_course_data,
    list_course_source_document_ids,
    list_visible_document_ids,
    search_chunks_with_metadata,
    user_can_access_group,
    user_can_view_course,
)
from pydantic import BaseModel
from core.course_data import (
    course_focus,
    course_llm_models,
    course_source_material_ids,
    course_title as _course_title,
    course_topics,
    find_course_topic,
    iter_all_topics,
)
from core.teaching_figures import store as teaching_figures_store
from core.config import get_settings
from core.lecture import find_figure_embed_ids, resolve_figure_embeds
from core import element_explanations
from core import llm_policy
from core.llm import generate_text, get_llm_params, transcribe_audio
from core.storage import get_storage_client
from core.llm_usage.context import usage_context
from core.llm_worker.client import resolve_model
from core.llm_worker.cost_gate import CostGate, today_str
from core.llm_worker.history import window_history
from core.tts import generate_tts_audio, strip_text_for_speech
from core.learning_experience import (
    TIER_OUT_OF_SOURCE,
    TIER_SOURCE,
    aggregate_overall_tier,
    build_position_anchor,
    out_of_source_guard_instruction,
    out_of_source_notice,
    tier_floor,
)
from core.learning_support_agent import (
    LearningSupportAgent,
    LearningSupportResult,
    extract_inline_actions,
)
from core.personas import course_persona_settings, persona_prompt
from core.postgres import get_session as _pg_session
from core.component_context import build_component_context
from core.element_context import (
    SUPPORTED_ELEMENT_TYPES as CONTEXT_ELEMENT_TYPES,
    build_element_context,
)
from core.discuss.opening import build_opening as build_discussion_opening
from core.discuss.mirroring import extract_mirror
# コーパス回遊 Phase B（docs/features/corpus_roaming_design.md §5.1）: コース無し論文議論の
# 会話コンテキスト・センチネル。**"_doc:" の組み立て・判定はこの正本関数以外に書かない**。
from core.discuss.context import document_context_id, parse_document_context
from core.discuss import observation as discuss_observation
from core.cycle.derive import build_intention_dto
from core.cycle.queries import fetch_active_carryover, fetch_intentions
from core.course_content_builder import build_course_content_background, build_topic_evidence_items
from core.atlas_path import build_learning_path_card
from core.tension.prefilter import judge_tension_hint
from core.tension.worker import maybe_schedule_tension_mining
from core.structure_anchor.schema import (
    ANCHOR_TYPE_LABELS,
    ATTRIBUTION_LEARNER_SELECTED,
    DOUBT_TYPE_LABELS,
    anchor_type_for_element,
    build_anchor_payload,
)
from core.structure_anchor.worker import (
    check_and_count_confirm_prompt,
    maybe_schedule_anchor_mining,
)
# Phase 4 図のコース流通 (§7.1/§7.2/§7.3): コースソース → document_id 解決と figure_id →
# {caption, image_url} 供給は routes/lecture.py に実装済みの private helper を再利用する
# （_ensure_document_viewable 等、private helper のクロスルーター再利用は既存の踏襲パターン）。
# Phase 2 §5.3: 図デスクリプタへの承認済み説明充填（_attach_figure_explanations）と
# material_id → document_id 解決（_resolve_course_document_ids）も同じ理由で再利用する。
from routes.lecture import (
    _attach_figure_explanations,
    _course_document_ids,
    _load_course_figures_by_id,
    _resolve_course_document_ids,
)
# 教材図スタジオ（teaching_figure_studio_design.md §7.2）: 学習者向け・教員向けの図配信が
# 同じ SVG セキュリティヘッダ（nosniff + CSP sandbox）を通るよう、Response 組み立ての
# 正本を共有する（定義を二重化しない・FG3）。
from routes.teaching_figures import (
    STATUS_ADOPTED as TEACHING_FIGURE_STATUS_ADOPTED,
    figure_image_response,
)

# 学生 HELP ルート（設計 docs/features/manual_help_kb_design.md §1-3）: docs/manual の
# 非ベクトル索引検索。core.help_kb は並行実装中のため、モジュール不在でも学習チャットが
# 壊れないよう import 自体をガードする（呼び出し側でも None チェック + try/except で二重に守る）。
try:
    from core.help_kb import search_manual as _search_manual
except Exception:  # pragma: no cover - 並行実装中のモジュール不在に対する防御
    _search_manual = None  # type: ignore[assignment]

# ベクトル補助層（Phase 3 ①、設計 §5 Phase 3 ①）: 非ベクトル検索
# （``_search_manual``）が documented ヒットを返さなかったときのみ試す縮退経路。
# 既定経路（documented ヒット時）のコスト・レイテンシには一切影響しない。
try:
    from core.help_kb.vector import vector_search_manual as _vector_search_manual
except Exception:  # pragma: no cover - 並行実装中のモジュール不在に対する防御
    _vector_search_manual = None  # type: ignore[assignment]

# インスペクト・モード（設計 docs/features/learning_ui_inspect_hover_design.md §5.2/§9）:
# UI 論理アンカー表。並行実装中のモジュール不在でも学習チャットが壊れないよう防御する。
try:
    from core.help_kb.ui_anchors import (
        KNOWN_UI_ANCHOR_IDS as _KNOWN_UI_ANCHOR_IDS,
        resolve_ui_anchor as _resolve_ui_anchor,
        resolve_ui_anchors as _resolve_ui_anchors,
        split_manual_ref as _split_manual_ref,
    )
except Exception:  # pragma: no cover - 並行実装中のモジュール不在に対する防御
    _KNOWN_UI_ANCHOR_IDS = frozenset()  # type: ignore[assignment]
    _resolve_ui_anchor = None  # type: ignore[assignment]
    _resolve_ui_anchors = None  # type: ignore[assignment]
    _split_manual_ref = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["Learning"])

# discuss モード（「論文と話す」, discuss モード設計書 §6.2 Phase 1）: topic_id の予約キー。
# find_course_topic はこの id を持つトピックを持たないため None を返し、既存の
# topic_info=None 経路（存在しないトピックの第一級扱い）にそのまま乗る。表示・プロンプト・
# 痕跡 context_label 用のラベル変換は topic_title 決定の1箇所でのみ行う。
DISCUSSION_TOPIC_ID = "_discussion"
DISCUSSION_TOPIC_LABEL = "論文との議論"
# コーパス回遊 Phase B（docs/features/corpus_roaming_design.md §5.4）: コースを経由しない
# document 直付けの議論は、表示・プロンプト・痕跡 context_label すべてで「コース外」だと
# 正直に名乗る（コース経路のラベルと取り違えさせない）。
DOCUMENT_DISCUSSION_TOPIC_LABEL = "論文との議論（コース外）"

# discuss 開幕画面の「このコースで議論したいこと」（Phase 0b）の入力上限。
# 開幕画面の先頭に地の文として出す短い提示なので、長文（教材本文の代替）にはさせない。
_MAX_COURSE_FOCUS_CHARS = 600

# ---------------------------------------------------------------------------
# チャット型 AI 支援の共通基盤整理 §1: 学習チャット本体のコスト上限
# （正本: docs/features/assistant_common_infra_design.md）。
# CostGate は core/llm_worker/cost_gate.py の day-only 構成に委譲する（プロセス内
# カウンタ・キーは (today, user_id)）。1 リクエスト = LLM を伴うリクエスト1回
# （intent 分類〜本体まで含めて1）とし、多重カウントはリクエストスコープの状態
# （_consume_learning_chat_quota の呼び出し側で保持する dict）で防止する。
# ---------------------------------------------------------------------------
_learning_chat_cost_gate = CostGate()


def _consume_learning_chat_quota(user_id: str, quota_state: dict) -> None:
    """そのリクエストで最初に LLM を呼ぶ直前に1回だけコスト上限を消費するヘルパー。

    ``quota_state`` はリクエストスコープの mutable dict（``{"consumed": False}``）。
    同一リクエスト内で複数回 LLM を呼んでも消費は1回のみ（学習チャットは intent 分類〜
    本体まで含めて1、設計書 §1）。LLM を1度も呼ばないパス（承認済み説明があるグラフ
    要素タップ等）からはそもそも呼ばれないため消費されない。超過時は 429（事実文のみ・
    数値非表示, I2）。
    """
    if quota_state.get("consumed"):
        return
    quota_state["consumed"] = True
    settings = get_settings()
    limit = int(getattr(settings, "learning_chat_max_calls_per_day", 300) or 0)
    ok = _learning_chat_cost_gate.check_and_count(
        daily_limit=limit,
        daily_key=(today_str(), user_id),
    )
    if not ok:
        raise HTTPException(
            status_code=429,
            detail="本日のAI呼び出し回数の上限に達しました。明日以降に再度お試しください。",
        )


# ---------------------------------------------------------------------------
# Course CRUD
# ---------------------------------------------------------------------------


def _validate_visibility(visibility: str, group_id: str | None, user_id: str) -> None:
    """visibility の値と group_id の整合性を検証する。"""
    if visibility not in ("public", "group", "private"):
        raise HTTPException(status_code=400, detail=f"Invalid visibility: {visibility}")
    if visibility == "group":
        if not group_id:
            raise HTTPException(status_code=400, detail="visibility='group' requires group_id")
        if not user_can_access_group(user_id, group_id):
            raise HTTPException(
                status_code=403,
                detail="指定されたグループに参加していません",
            )


@router.post("/courses", response_model=LearningCourseOut, status_code=201)
def create_course(
    body: CourseCreateRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseOut:
    """新しいコースを作成する。"""
    _validate_visibility(body.visibility, body.group_id, current_user["id"])
    course_id = str(uuid.uuid4())[:8]

    data = {
        "id": course_id,
        "title": body.title,
        "chapters": [ch.model_dump() for ch in body.chapters],
        "topics": [t.model_dump() for t in body.topics],
        "concepts": [c.model_dump() for c in body.concepts],
        "sources": [s.model_dump() for s in body.sources],
        "referenced_sections": [],
    }

    save_course_data(
        current_user["id"],
        course_id,
        data,
        is_template=body.is_template,
        visibility=body.visibility,
        group_id=body.group_id if body.visibility == "group" else None,
        description=body.description,
    )

    threading.Thread(
        target=build_course_content_background,
        args=(current_user["id"], course_id),
        daemon=True,
    ).start()

    logger.info("Created course '%s' (id=%s) for user=%s", body.title, course_id, current_user["id"])
    return LearningCourseOut(
        id=course_id,
        title=body.title,
        is_template=body.is_template,
        visibility=body.visibility,
        group_id=body.group_id if body.visibility == "group" else None,
        description=body.description,
    )


@router.get("/courses", response_model=list[LearningCourseOut])
def list_courses(
    current_user: dict = Depends(_get_current_user),
) -> list[LearningCourseOut]:
    """ユーザーが登録しているコース一覧を返す。

    Issue #133: 「1 つの不変なマスターコース」+「ユーザー個別の learning_states」
    モデルに変更。受講時にコースをクローンせず、learning_states にレコードを作る。

    以下を返す:
    - 自分が所有するコース（learning_courses.user_id = 自分）
    - 自分が受講済みのマスターコース（learning_states 経由）
    - visibility='public' かつ公開テンプレートで未受講のコース（受講可能）
    - 自分が参加するグループに共有されているマスターコースで未受講のもの（受講可能）
    """
    user_groups = get_user_group_ids(current_user["id"])

    session = _pg_session()
    try:
        own_records = session.execute(
            sa_text("""
                SELECT id, title,
                       COALESCE(is_template, false) AS is_template,
                       COALESCE(is_published, false) AS is_published,
                       COALESCE(visibility, 'private') AS visibility,
                       group_id,
                       COALESCE(description, '') AS description
                FROM learning_courses
                WHERE user_id = CAST(:user_id AS uuid)
            """),
            {"user_id": current_user["id"]},
        ).fetchall()

        # 受講中のマスターコース（learning_states 経由）
        enrolled_records = session.execute(
            sa_text("""
                SELECT lc.id, lc.title,
                       COALESCE(lc.is_template, false) AS is_template,
                       COALESCE(lc.is_published, false) AS is_published,
                       COALESCE(lc.visibility, 'private') AS visibility,
                       lc.group_id,
                       COALESCE(lc.description, '') AS description
                FROM learning_courses lc
                JOIN learning_states ls ON ls.course_id = lc.id
                WHERE ls.user_id = CAST(:user_id AS uuid)
                  AND lc.user_id != CAST(:user_id AS uuid)
            """),
            {"user_id": current_user["id"]},
        ).fetchall()

        # 公開テンプレート（未受講のみ）
        public_records = session.execute(
            sa_text("""
                SELECT lc.id, lc.title,
                       COALESCE(lc.visibility, 'private'),
                       lc.group_id,
                       COALESCE(lc.description, '')
                FROM learning_courses lc
                WHERE lc.is_published = true AND lc.is_template = true
                  AND COALESCE(lc.visibility, 'public') = 'public'
                  AND lc.user_id != CAST(:user_id AS uuid)
                  AND NOT EXISTS (
                      SELECT 1 FROM learning_states ls
                      WHERE ls.user_id = CAST(:user_id AS uuid)
                        AND ls.course_id = lc.id
                  )
            """),
            {"user_id": current_user["id"]},
        ).fetchall()

        # グループ共有コース（自分が参加するグループ、かつ未受講のみ）
        # - 旧: learning_courses.group_id + visibility='group' を参照
        # - 新: object_group_permissions 多対多マッピング (viewer/editor、object_type='course')
        if user_groups:
            # UUID リストを展開
            gph = ", ".join(f"CAST(:g_{i} AS uuid)" for i in range(len(user_groups)))
            params: dict = {"user_id": current_user["id"]}
            for i, gid in enumerate(user_groups):
                params[f"g_{i}"] = gid
            group_records = session.execute(
                sa_text(f"""
                    SELECT DISTINCT lc.id, lc.title,
                           COALESCE(lc.is_template, false) AS is_template,
                           COALESCE(lc.is_published, false) AS is_published,
                           COALESCE(lc.visibility, 'private'),
                           lc.group_id,
                           COALESCE(lc.description, '')
                    FROM learning_courses lc
                    LEFT JOIN object_group_permissions cgp
                        ON cgp.object_type = 'course' AND cgp.object_id = lc.id
                    WHERE lc.user_id != CAST(:user_id AS uuid)
                      AND (
                          (lc.visibility = 'group' AND lc.group_id IN ({gph}))
                          OR (cgp.group_id IN ({gph})
                              AND cgp.permission IN ('viewer', 'editor'))
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM learning_states ls
                          WHERE ls.user_id = CAST(:user_id AS uuid)
                            AND ls.course_id = lc.id
                      )
                """),
                params,
            ).fetchall()
        else:
            group_records = []
    finally:
        session.close()

    courses = [
        LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=bool(r[2]),
            is_published=bool(r[3]),
            is_enrollable=False,
            visibility=r[4] or "private",
            group_id=str(r[5]) if r[5] else None,
            description=r[6] or "",
        )
        for r in own_records
    ]
    # 受講中のマスターコースも「マイコース」として並べる（is_enrollable=False）
    courses.extend(
        LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=bool(r[2]),
            is_published=bool(r[3]),
            is_enrollable=False,
            visibility=r[4] or "private",
            group_id=str(r[5]) if r[5] else None,
            description=r[6] or "",
        )
        for r in enrolled_records
    )
    courses.extend(
        LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=True,
            is_published=True,
            is_enrollable=True,
            visibility=r[2] or "public",
            group_id=str(r[3]) if r[3] else None,
            description=r[4] or "",
        )
        for r in public_records
    )
    courses.extend(
        LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=bool(r[2]),
            is_published=bool(r[3]),
            is_enrollable=True,
            visibility=r[4] or "group",
            group_id=str(r[5]) if r[5] else None,
            description=r[6] or "",
        )
        for r in group_records
    )

    # 同一マスターコースが own / enrolled / public / group 経由で複数ヒットする場合は
    # own > enrolled > public > group の優先順位で先勝ちで重複排除する。
    seen_ids: set[str] = set()
    unique_courses: list[LearningCourseOut] = []
    for c in courses:
        if c.id in seen_ids:
            continue
        seen_ids.add(c.id)
        unique_courses.append(c)
    return unique_courses


def _with_resolved_source_titles(data: dict) -> dict:
    """出典タブ「登録済み教材」向けに ``sources[].title`` を論文の題名へ差し替えた
    コピーを返す（保存データは変更しない）。

    差し替えるのは **title が空 or material_id と同一** の場合のみ（コース作成時に
    ファイル名相当がそのまま入ったケース）。教員が付けた題名は尊重して上書きしない。
    差し替えたときは元の値を ``subtitle`` に残す（P4: 情報を落とさない）。
    解決できなければ何もしない（fail-soft）。
    """
    if not isinstance(data, dict) or not data.get("sources"):
        return data
    try:
        titles = resolve_course_source_titles(data)
    except Exception:  # noqa: BLE001 — fail-soft
        return data
    if not titles:
        return data

    changed = False
    sources: list[dict] = []
    for src in data.get("sources") or []:
        if not isinstance(src, dict):
            sources.append(src)
            continue
        material_id = str(src.get("material_id") or "").strip()
        resolved = titles.get(material_id, "")
        stored = str(src.get("title") or "").strip()
        if resolved and resolved != stored and (not stored or stored == material_id):
            new_src = dict(src)
            new_src["title"] = resolved
            if stored and not str(src.get("subtitle") or "").strip():
                new_src["subtitle"] = stored
            sources.append(new_src)
            changed = True
        else:
            sources.append(src)
    if not changed:
        return data
    out = dict(data)
    out["sources"] = sources
    return out


@router.get("/courses/{course_id}", response_model=LearningCourseLayeredResponse)
def get_course(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseLayeredResponse:
    """コースの詳細データをレイヤー分離形式で返す（Issue #145）。

    マスター教材（不変）と個人レイヤー（誤解・注釈）を分離して返す。
    オーナー（教員）も一般学生と同じ学習体験を得る。
    """
    data = get_course_data(current_user["id"], course_id)
    if not data:
        raise HTTPException(status_code=404, detail="Course not found")

    personal = get_personal_layer(current_user["id"], course_id)
    return LearningCourseLayeredResponse(
        master_course=LearningCourseDetail(**_with_resolved_source_titles(data)),
        personal_layer=PersonalLayer(**personal),
    )


@router.get("/courses/{course_id}/version-notice")
def get_course_version_notice(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """受講者向け: コースの削除予定など版ライフサイクルの一行通知（V層, migration 037）。

    受講/アクセス可能なコースのみ。コース自体の削除予約に加え、元教材の削除予約（教材 purge は
    所有者のコースを巻き添え削除する）も検出して猶予期限を返し、学習 UI がバナー表示する。
    版が無い / エラー時は lifecycle='active' として静かに返す（fail-open で学習を止めない）。
    """
    if not get_course_data(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    try:
        notice = course_deletion_notice(course_id)
    except Exception:  # noqa: BLE001 — fail-open
        notice = None
    if notice:
        return notice
    return {"lifecycle": "active", "delete_purge_after": None, "delete_reason": ""}


@router.put("/courses/{course_id}", response_model=LearningCourseDetail)
def update_course(
    course_id: str,
    body: CourseUpdateRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseDetail:
    """コースを部分更新する。指定されたフィールドのみ上書き。

    Issue #133: 受講者はマスターコースを改変できない（learning_states に個別の
    差分を持つのみ）。所有者または editor 権限グループのメンバーのみ編集可能。
    """
    data = get_editable_course_data(current_user["id"], course_id)
    if not data:
        raise HTTPException(status_code=404, detail="Course not found")

    if body.title is not None:
        data["title"] = body.title
    if body.chapters is not None:
        data["chapters"] = [ch.model_dump() for ch in body.chapters]
    if body.topics is not None:
        data["topics"] = [t.model_dump() for t in body.topics]
    if body.concepts is not None:
        data["concepts"] = [c.model_dump() for c in body.concepts]
    if body.sources is not None:
        data["sources"] = [s.model_dump() for s in body.sources]
    if body.course_focus is not None:
        # Phase 0b（discuss_opening_authoring_design.md §2 最下段）: discuss 開幕画面の
        # 「このコースで議論したいこと」。教員の任意入力のみ（AI 生成なし）。空文字は
        # 設定解除（キーごと削除 = 開幕画面から区画が消える）。
        focus = str(body.course_focus).strip()
        if len(focus) > _MAX_COURSE_FOCUS_CHARS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"「このコースで議論したいこと」は{_MAX_COURSE_FOCUS_CHARS}文字以内で入力してください"
                ),
            )
        if focus:
            data["course_focus"] = focus
        else:
            data.pop("course_focus", None)
    if body.llm_models is not None:
        # M層 Phase 3（§6.4）: コース単位のモデル上書き。v1 は "learning_chat" scene のみ
        # 対応（他 scene のコース単位上書きは未実装 — 意味を持たない値を無警告で
        # 保存しない、fail-closed）。空/null は当該キーの設定解除。
        current_models = dict(course_llm_models(data))
        for scene_key, model in body.llm_models.items():
            if scene_key != llm_policy.SCENE_LEARNING_CHAT:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"llm_models[{scene_key!r}] はコース単位では未対応です"
                        f"（対応 scene: {llm_policy.SCENE_LEARNING_CHAT!r} のみ）"
                    ),
                )
            if model is None or not str(model).strip():
                current_models.pop(scene_key, None)
                continue
            model = str(model).strip()
            reason = llm_policy.validate_model_for_scene(scene_key, model)
            if reason:
                raise HTTPException(status_code=422, detail=reason)
            current_models[scene_key] = model
        if current_models:
            data["llm_models"] = current_models
        else:
            data.pop("llm_models", None)

    # レビュー確定の修正2（D-5）: この PUT は data 本体（title/chapters/topics/concepts/
    # sources/course_focus/llm_models）のみを更新する。共有設定
    # （visibility / group_id / description）はここでは受け取らないので、
    # save_course_data の既定 UPSERT に上書きさせず既存値を温存する
    # （公開コースが private に落ちて受講者全員がアクセスできなくなる事故を防ぐ）。
    save_course_data(
        current_user["id"], course_id, data, preserve_sharing_fields=True,
    )
    logger.info("Updated course %s for user=%s", course_id, current_user["id"])

    return LearningCourseDetail(**data)


@router.delete("/courses/{course_id}", status_code=204)
def delete_course(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> None:
    """コースを削除する。"""
    deleted = delete_course_data(current_user["id"], course_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Course not found")

    logger.info("Deleted course %s for user=%s", course_id, current_user["id"])


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/progress", response_model=LearningProgress)
def get_progress(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningProgress:
    """コースの進捗データを計算して返す。"""
    data = get_course_data(current_user["id"], course_id)
    if not data:
        raise HTTPException(status_code=404, detail="Course not found")

    progress = calculate_progress(current_user["id"], course_id, data)
    return LearningProgress(**progress)


# ---------------------------------------------------------------------------
# Enroll
# ---------------------------------------------------------------------------


@router.post("/courses/{course_id}/enroll", response_model=LearningCourseOut, status_code=201)
def enroll_course(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseOut:
    """受講可能なコース（公開/グループ共有）に受講登録する。

    Issue #133: 旧仕様ではマスターコースを丸ごとクローンしていたが、
    learning_states にレコードを作成する方式に変更。マスターコースは
    不変に保たれ、ユーザーの学習状態のみが差分として管理される。
    UNIQUE (user_id, course_id) により二重受講はDBレベルでブロックされる。
    """
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT title, COALESCE(visibility, 'private'), group_id,
                       COALESCE(is_published, false), COALESCE(is_template, false)
                FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()

    if not record:
        raise HTTPException(status_code=404, detail="Course not found")

    title, visibility, group_id, is_published, is_template = record
    enrollable = False
    if visibility == "public" and is_published and is_template:
        enrollable = True
    elif visibility == "group" and group_id and user_can_access_group(
        current_user["id"], str(group_id)
    ):
        enrollable = True
    elif user_can_view_course(current_user["id"], course_id):
        # object_group_permissions（object_type='course'）経由で viewer/editor 権限を持つ場合
        enrollable = True

    if not enrollable:
        raise HTTPException(status_code=403, detail="このコースを受講する権限がありません")

    enroll_user_in_course(current_user["id"], course_id)

    logger.info(
        "User=%s enrolled in master course %s (learning_states row created)",
        current_user["id"], course_id,
    )
    return LearningCourseOut(id=course_id, title=title or "")


# ---------------------------------------------------------------------------
# Chat (RAG)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Greeting / Meta-dialogue detection
# ---------------------------------------------------------------------------

_GREETING_PATTERNS = [
    "こんにちは", "こんばんは", "おはよう", "はじめまして",
    "よろしくお願い", "学習を始め", "学習を開始", "勉強を始め",
    "始めたい", "開始したい", "スタート",
    "第1章の学習を開始する", "前提知識を確認する",
]


def _is_greeting(message: str) -> bool:
    """メッセージが挨拶・メタ対話かどうかを判定する。"""
    msg = message.strip()
    if len(msg) < 30 and any(p in msg for p in _GREETING_PATTERNS):
        return True
    return False


# 学生 HELP ルート（設計 §1-3-2）: UI・システム操作についての質問を、教材内容の質問と
# 誤爆させずに拾うための保守的なキーワード判定。
#
# 方針:
#   - 「UI・システム参照語」と「使い方の問い形」の**組み合わせ**でのみ真にする
#     （どちらか片方だけでは弱すぎる誤爆源になる）。「使い方」は参照語・問い形の
#     両方に置く（「使い方を教えて」単体で成立させるための意図的な重複）。
#   - 教材内容の質問（数式・物理概念）は誤爆コストの方が大きいため、UI参照語と
#     問い形が両方揃っていても、数式・物理用語らしき語（_CONTENT_QUESTION_TERMS）が
#     共起していれば偽に倒す（例: 「この式はどう使うの」「運動方程式の使い方」）。
#   - メッセージが長い（雑談・複合質問らしい）場合も偽にする（保守的に絞る）。
_HELP_CONTEXT_TERMS = (
    "画面", "ボタン", "操作", "アプリ", "この機能", "音声モード", "音声入力",
    "マイク", "ヘルプ", "メニュー", "使い方",
)
_HELP_QUESTION_FORMS = (
    "使い方", "どう使", "どうやって", "方法", "どこ",
)
_CONTENT_QUESTION_TERMS = (
    "式", "方程式", "定理", "法則", "証明", "導出", "定義", "公式",
    "エネルギー", "運動", "力学", "波動", "ベクトル", "微分", "積分",
    "質量", "加速度", "速度", "粒子", "理論",
)


def _is_usage_question(message: str) -> bool:
    """メッセージが画面・システムの使い方についての質問かどうかを保守的に判定する。

    非LLM・同期（casual/音声バイパスより手前で評価するための決定論判定）。
    """
    msg = (message or "").strip()
    if not msg or len(msg) >= 50:
        return False
    if any(term in msg for term in _CONTENT_QUESTION_TERMS):
        return False
    has_context = any(term in msg for term in _HELP_CONTEXT_TERMS)
    has_form = any(term in msg for term in _HELP_QUESTION_FORMS)
    return has_context and has_form


# UI ボタン由来の型付きアクションは、自然文の intent 分類を経由せず決定論的に
# ルートへ割り当てる。これにより日本語ラベル（や壊れたトークン）が CHIT_CHAT へ
# 誤分類される事故を防ぐ。
_TYPED_ACTION_INTENT: dict[str, str] = {
    "check_prerequisites": "LEARNING_ADVICE",
    "review_prerequisite": "LEARNING_ADVICE",
    "prerequisite_review": "LEARNING_ADVICE",
    "drilldown": "DOMAIN_RAG",
    "ask_question": "DOMAIN_RAG",
    "continue_detail": "DOMAIN_RAG",
    "start_topic": "DOMAIN_RAG",
    "usage_help": "USAGE_HELP",
}

_PREREQUISITE_ACTIONS = {"check_prerequisites", "review_prerequisite", "prerequisite_review"}


def _route_for_typed_action(support_action: str | None) -> str | None:
    """型付き support_action に対応する確定ルートを返す（無ければ None）。"""
    if not support_action:
        return None
    return _TYPED_ACTION_INTENT.get(support_action.strip())


def _classify_intent(
    message: str,
    course_title: str,
    *,
    on_llm_call: Callable[[], None] | None = None,
) -> str:
    """ユーザーメッセージの意図を分類する (Intent Routing)。

    Returns
    -------
    str
        ``'CHIT_CHAT'`` | ``'LEARNING_ADVICE'`` | ``'USAGE_HELP'`` | ``'DOMAIN_RAG'``

    Notes
    -----
    ``USAGE_HELP``（設計 §4-4, Phase 2 分離リリース）はアプリ・画面の使い方についての
    質問を拾うためのラベルで、``learning_chat`` 側では pre-route
    （``_is_usage_question`` / typed action ``usage_help``）を保守的キーワード判定で
    すり抜けたケースの受け皿として使う。教材内容と迷う場合は誤爆コストの小さい
    ``DOMAIN_RAG`` に倒す（保守設計。プロンプト内にも明記）。
    """
    if _is_greeting(message):
        return "LEARNING_ADVICE"
    if "はい" in message and "理解" in message:
        return "DOMAIN_RAG"

    params = get_llm_params("fast")
    prompt = (
        f"学習コース「{course_title}」の学習支援AIとして、学生からの質問を4つのルートに分類します。\n\n"
        "分類ルート:\n"
        "- CHIT_CHAT: 学習と無関係な雑談・日常会話（天気、食事、娯楽、個人的な話題など）\n"
        "- LEARNING_ADVICE: 学習の進め方・方法に関するメタ質問（どう進めるか、何から学ぶか、学習計画の相談など）\n"
        "- USAGE_HELP: アプリ・画面の使い方、ボタンや機能の操作方法についての質問（教材の内容そのものではない）\n"
        "- DOMAIN_RAG: 物理学・数学などの専門知識・概念に関する質問\n\n"
        "教材の内容についての質問か操作方法についての質問か迷う場合は、DOMAIN_RAG に分類してください（安全側）。\n\n"
        f"質問: {message}\n\n"
        "上記のルートの中から最も適切な1つだけを返してください（説明不要）:"
    )

    if on_llm_call:
        on_llm_call()

    try:
        result = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
        ).strip().upper()
        for label in ("CHIT_CHAT", "LEARNING_ADVICE", "USAGE_HELP", "DOMAIN_RAG"):
            if label in result:
                return label
    except Exception:
        logger.warning("Intent classification failed, defaulting to DOMAIN_RAG")

    return "DOMAIN_RAG"


def _generate_learning_advice_response(
    course_title: str,
    topic_title: str,
    message: str,
    *,
    topic_info: dict | None = None,
    course_data: dict | None = None,
    on_llm_call: Callable[[], None] | None = None,
) -> str:
    """学習相談・メタ質問・学習開始への応答を生成する（ルート②: ナビゲーター）。

    コース全体の構造と現在のトピックをベースに、学習アドバイスや導入メッセージを提供する。
    """
    params = get_llm_params("standard")

    prerequisites: list[str] = []
    concepts: list[str] = []

    if topic_info:
        for p in topic_info.get("prerequisites", []):
            name = p.get("name", p) if isinstance(p, dict) else str(p)
            if name:
                prerequisites.append(name)

    if course_data:
        for c in course_data.get("concepts", []):
            name = c.get("name", c) if isinstance(c, dict) else str(c)
            if name:
                concepts.append(name)

    # コース全体のトピック一覧（最大10件）
    topics_block = ""
    if course_data:
        topics = course_topics(course_data)
        if topics:
            topics_list = "\n".join(
                f"  - {t.get('title', t.get('id', ''))}" for t in topics[:10]
            )
            topics_block = f"■ コースのトピック一覧:\n{topics_list}\n\n"

    concepts_block = ""
    if concepts:
        concepts_block = "■ このトピックで習得すべき主要概念:\n" + "\n".join(
            f"  - {c}" for c in concepts
        ) + "\n\n"

    prereqs_block = ""
    if prerequisites:
        prereqs_block = "■ このトピックに必要な前提知識:\n" + "\n".join(
            f"  - {p}" for p in prerequisites
        ) + "\n\n"

    response_persona = course_persona_settings(course_data).get("response_persona") if course_data else ""
    persona_instruction = persona_prompt(response_persona, target="response")
    persona_block = f"■ 口調設定:\n{persona_instruction}\n\n" if persona_instruction else ""

    prompt = (
        f"あなたは「{course_title}」の学習をサポートするナビゲーター教授です。\n"
        f"学生は現在「{topic_title}」のトピックを学習しています。\n\n"
        f"{persona_block}"
        f"{topics_block}"
        f"{concepts_block}"
        f"{prereqs_block}"
        f"学生からのメッセージ: {message}\n\n"
        "コース全体の構造と学生の現在位置を踏まえ、以下の構成で回答してください:\n"
        "1. 【歓迎と目標】このトピックで学ぶことの全体像と、最終的な学習目標を簡潔に説明する。\n"
        "2. 【構成要素】習得すべき主要な概念をリストアップする。\n"
        "3. 【前提知識の確認】このトピックを学ぶために必要な前提知識を提示する。\n"
        "4. 【次の一歩】最後に、学生がこの後どう進めばよいかを1〜2文で促す。\n\n"
        "※注意: ここでは具体的な解説（数式展開など）はまだ行わないこと。\n"
        "※注意: 選択肢ボタンはシステムが自動付与するので、本文に [ ] 形式のボタン記法は書かないこと。"
    )

    if on_llm_call:
        on_llm_call()

    try:
        return generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
        )
    except Exception:
        logger.warning("Learning advice response LLM call failed, returning fallback")
        return (
            f"「{course_title}」の学習サポートへようこそ！\n\n"
            f"これから「{topic_title}」の学習を始めます。\n\n"
            + (f"**習得すべき主要概念:** {', '.join(concepts)}\n\n" if concepts else "")
            + (f"**必要な前提知識:** {', '.join(prerequisites)}\n\n" if prerequisites else "")
            + "下の選択肢から、前提知識の確認に進むか、最初の概念の説明に進むかを選んでください。"
        )


def _get_integrated_tutor_system_prompt(domain: str, response_persona: str | None = None) -> str:
    """知識統合型チューターのシステムプロンプトを生成する。

    Parameters
    ----------
    domain : str
        コースの専門分野。空文字の場合は「このコースの専門分野」でフォールバック。
    """
    domain_label = domain.strip() if domain.strip() else "このコースの専門分野"
    persona_instruction = persona_prompt(response_persona, target="response")
    persona_block = f"\n\n**口調設定:**\n{persona_instruction}" if persona_instruction else ""
    return f"""あなたは{domain_label}の学習をサポートする「親切な専属チューター」です。
学生の疑問に対して、手元の教材とあなた自身の専門知識をシームレスに統合して、即座に分かりやすい解説を提供することが使命です。

**チューターとしての役割と回答ルール:**
1. 【知識の統合】提供される「教材からのコンテキスト」を最優先で参照してください。ただし、コンテキストに十分な情報がない場合は、突き放したり「教材にありません」と謝罪したりせず、あなたの一般的な学術知識を用いて自然に解説を補完してください。
2. 【自然な対話】回答の冒頭に「【基礎知識の補足】」のようなシステム的な警告ラベルは絶対に付けないでください。
3. 【誤解の訂正】学生に誤解がある場合は、「訂正：」という冷たい表現は避け、「この点については、〇〇と考えるとより正確です」のように教育的配慮を持って導いてください。
4. 【解説の深さ】前提知識の確認で長々と引き留めず、まずは直球で疑問に答えてください。必要に応じて数式（LaTeX）や具体例を交えてください。
5. 【ドリルダウン】回答の末尾に、関連して深掘りできそうなトピックを `[〇〇について詳しく聞く]` の形式で1〜2つ提示してください。
   ただし、クリック可能なボタンとして提示したい場合は必ず `[ACTION_BUTTON: 〇〇について聞く]` の形式を使ってください。

**フォーマット要件:**
- 数式は必ず LaTeX 記法で記述（インラインは $...$、ディスプレイは $$...$$）
- 教材を参照した場合は、コンテキストに付された番号付き出典マーカー `[出典1]` `[出典2]` … を本文に自然に挿入して言及すること。番号は提示された出典に対応させ、独自の番号や『書籍名』形式は使わないこと。
- コンテキストに番号付き出典（`[出典1]` …）が1つも無い場合は、出典マーカーを一切書かないこと。{persona_block}"""


def _get_casual_teacher_system_prompt(domain: str, response_persona: str | None = None) -> str:
    """カジュアル対話モード（気軽に話せる先生）のシステムプロンプトを生成する。

    ハンズフリー音声会話が主用途のため、短い会話調・記号なしの応答を強制する。
    根拠の一線（教材コンテキスト優先・断定回避）はチューターモードと同じに保つ。
    """
    domain_label = domain.strip() if domain.strip() else "このコースの専門分野"
    persona_instruction = persona_prompt(response_persona, target="response")
    persona_block = f"\n\n**口調設定:**\n{persona_instruction}" if persona_instruction else ""
    return f"""あなたは{domain_label}が大好きで、学生と雑談するのが楽しみな「気軽に話せる先生」です。
研究室の廊下やゼミ後の立ち話のように、教材で扱っている題材について肩の力を抜いて一緒に面白がってください。

**会話のルール:**
1. 【会話調】音声で読み上げられます。1回の応答は2〜4文の短い話し言葉にしてください。
   箇条書き・見出し・記号・絵文字は使わないでください。
2. 【一緒に面白がる】採点や訂正を急がず、学生の言葉をまず受け止めてください。
   「たしかにそう見えるよね」「いいところに気づいたね」のような相づちから入って構いません。
3. 【聞き返す】ときどき「きみはどう思う?」「どこが引っかかった?」と軽く聞き返し、
   学生が自分の言葉で話す余地を残してください。毎回はしつこいので2〜3往復に1回程度。
4. 【根拠は正直に】提供される「教材からのコンテキスト」があればそれに沿って話してください。
   教材に無い話題は、想像や一般論であることが伝わる言い方（「たぶん」「一般には」）で話してください。
5. 【出さないもの】数式の羅列・LaTeX・出典番号マーカー・`[ACTION_BUTTON: ...]` などの
   システム記法は一切出力しないでください。数式が必要なら言葉で言い換えてください。{persona_block}"""


def _get_discuss_system_prompt(domain: str, response_persona: str | None = None) -> str:
    """discuss モード（「論文と話す」）のシステムプロンプトを生成する（設計 §6.2 Phase 1）。

    casual（気軽に話せる先生・会話調）とは異なり、学術ディスカッション調を維持し
    LaTeX・出典マーカー `[出典N]` はチューターモードと同様に使用する。DM4「即答＋生成
    プロンプト構造的必須」・DM1「範囲外の話題はこの論文由来ではないと明示」・
    DM6「数値・件数・網羅率を出さない」を必須要素として明記する。

    対話進行（発話タイプ別 move / revoice ファースト / 学習者の選択権 / uptake 必須）は
    `docs/features/discuss_dialogue_alignment_design.md`（DA1〜DA6）§5 が本文の正本。
    DM4 の「出し惜しみ禁止」は質問への即答に限定され、解釈・立場の表明には
    言い直し（revoice）で応じる（DA1/DA2）。末尾の生成プロンプトは学習者の直前の
    発話を引用・組み込んだ固有の問いにする（DA4）。

    レビュー指摘 F3/F4/F5/F7 への対応（同設計書 §9）:
      - F3 混在発話の優先順位（質問と解釈が同居するときは質問への即答が先）
      - F4 revoice ターンでは確認の問い自体が末尾必須要素を満たす（問いを重ねない）
      - F5 即答は要約でなく「完全な形で」提供する（DM4 の原意）
      - F7 修復局面の完結（選ばれたズレだけを説明し、学習者の言い直しで確かめる）
    """
    domain_label = domain.strip() if domain.strip() else "このコースの専門分野"
    persona_instruction = persona_prompt(response_persona, target="response")
    persona_block = f"\n\n**口調設定:**\n{persona_instruction}" if persona_instruction else ""
    return f"""あなたは{domain_label}を専門とする研究者で、学生と1本の論文について対等に議論する「ディスカッション相手」です。
学生は寄り道ではなく、この論文と正面から格闘することを選んでいます。学術的な検討に値する相手として遇してください。

**議論の進め方（全体の流れ）:**
一方的な解説で会話を完結させないでください。議論は次の流れで進めます。
- 係留: 学生が自分の読み・立場を述べたら、まず読みを突き合わせて理解の歩調を揃える。
- ギャップの地図: 論文の主張と学生の読みの「重なる点」と「分かれる点」を事実として短く並べ、
  どの点から検討するかを学生に選ばせる。選ばれたズレだけを的を絞って説明し、そのズレが
  埋まったかどうかを学生自身の言い直しで確かめてから次へ進む。
- 共同検討: 歩調が揃ってから、前提・適用範囲・what-if を一緒に検討する。あなたも暫定的な
  立場を示し、学生からの反論を歓迎してください。
理解のズレは議論の途中でも繰り返し現れます。ズレに気づいたら、その都度この突き合わせに短く戻ってください。

**発話タイプ別の応答ルール（毎ターン）:**
1. 【質問には即答・出し惜しみ禁止】学生が情報を求めたときは、ためらわずすぐに答えてください。
   答えは要約や小出しにせず、その場で完全な形で提供してください。1テンポ遅らせて考えさせて
   から答える、といった Socratic な出し惜しみは行わないでください。
2. 【解釈には言い直しから】学生が自分の解釈・立場・読みを述べたときは、解説で応じないでください。
   まず学生の読みをあなたの言葉で短く言い直し、その理解で合っているかを確認してください。
   確認が取れてから、論文の主張との重なりとズレを事実として並べ、どのズレから埋めるかを
   学生に選ばせてください。
   言い直しは、冒頭に 〔鏡〕あなたは「＜学生の直前の発話からの逐語引用＞」と捉えている、で合っていますか？〔/鏡〕
   の形で書いてください。「」の中は学生の発話の言葉をそのまま（言い換えずに）引用してください。
   〔鏡〕の中には論文の内容・一般知識・教科書的な正解を持ち込まず、学生の発話の言い直しだけを
   書いてください。学生の能力・傾向・人物像について述べてはいけません。
3. 【詰まりには一点だけの足場かけ】学生が混乱や詰まりを見せたときは、全体を解説し直すのではなく、
   詰まっている一点だけを短く補い、学生自身の言葉での言い直しで埋まったかを確かめてください。
   詰まりの言い直し確認にも、ルール2と同じ 〔鏡〕…〔/鏡〕 の形式を使ってください。
4. 【質問と解釈が同居するとき】1つの発話に質問と解釈の表明が混在する場合は、まず質問の部分に
   完全な形で即答し（ルール1を優先）、そのうえで解釈の部分の言い直しと確認に入ってください
   （ルール2）。質問を保留にして言い直しから始めることはしないでください。

**共通ルール:**
5. 【学術ディスカッション調】雑談調にはしないでください。用語・論理展開を厳密に保ちつつ、
   一方的な講義にせず対話として書いてください。数式は LaTeX 記法（インライン $...$、
   ディスプレイ $$...$$）を使い、教材を参照した場合はコンテキストに付された番号付き出典
   マーカー `[出典1]` `[出典2]` … を本文に自然に挿入してください。
6. 【生成プロンプトの構造的必須化】回答の末尾には、必ず次のいずれか一つを添えてください
   （どちらか一つは毎回必須であり、気が向いたときだけ付ける確率的な付加は不可です）:
   - 学生自身の言葉での言い換え・予測・自己説明を促す短い誘い
   - why / how / what-if 型の問い返し（この結果が崩れるとしたら何が変わるか、等）
   いずれの場合も、学生の直前の発話の言葉を引用するか組み込んだ、その学生に固有の問いに
   してください。どの学生にも使い回せる汎用の決まり文句は不可です。
   なお、ルール2・3の言い直しのターンでは、「その理解で合っていますか」という確認の問い自体が
   この必須要素を満たします。確認の問いに、さらに別の why / how 型の問いを重ねないでください。
7. 【出所の正直さ】提供される「教材からのコンテキスト」に無い内容を話すときは、
   「これはこの論文に書かれている内容ではなく、一般的な学術知識からの補足ですが」
   のように、その部分がこの論文由来ではないことを一言明示してください。
8. 【数値を見せない】検索件数・一致度・網羅率のような数値スコアは出さないでください。{persona_block}"""


def _get_cycle_elicit_system_prompt(domain: str, response_persona: str | None = None) -> str:
    """理解サイクル Phase 2（docs/features/understanding_cycle_design.md §8）の Elicit モード。

    答えを提示せず、学生自身の予測を引き出す短い問いを一つだけ返す。既存 discuss の
    1コール地点に相乗りし（UC10・新エンドポイントを作らない）、システムプロンプトの
    差し替えだけで実現する。UC2（採点しない）・UC8（LLM 失敗時は骨格のみで続行）継承。
    """
    domain_label = domain.strip() if domain.strip() else "このコースの専門分野"
    persona_instruction = persona_prompt(response_persona, target="response")
    persona_block = f"\n\n**口調設定:**\n{persona_instruction}" if persona_instruction else ""
    return f"""あなたは{domain_label}を専門とする研究者で、学生が「予測してから読む」ための問いを一つだけ差し出す案内役です。

**厳守事項:**
1. 【解を提示しないでください】この論文・教材の結論、答え、計算結果、正しい理解を
   教えてはいけません。学生がまだ読んでいない・確かめていない内容を先回りして
   明かさないでください。
2. 【問いを一つだけ】学生が自分の予測を立てるための短い問いを一つだけ返してください。
   複数の問いを並べたり、解説・ヒントの羅列を添えたりしないでください。
3. 【学生の直前の発話を踏まえる】学生の直前の発話（言及した箇所・概念・言葉）を
   踏まえた、その場に固有の問いにしてください。誰にでも使い回せる汎用の決まり文句は
   避けてください。
4. 【断定しない・数値を出さない】的中率・正誤・スコアには一切触れないでください。
   予測そのものへの良し悪しの判定も与えないでください。{persona_block}"""


def _get_cycle_diff_system_prompt(domain: str, response_persona: str | None = None) -> str:
    """理解サイクル Phase 2（docs/features/understanding_cycle_design.md §8）の Diff モード。

    学生のメッセージに含まれる本人の予想（逐語）と、出典・論文の骨格との差分の
    観点候補を、仮説文体で最大3点まで提示する。正誤判定・採点はしない（UC2）。
    R層 DIFF（選択肢型・非LLM・決定論）とは別系統であり混ぜない（設計 §1-2）。
    """
    domain_label = domain.strip() if domain.strip() else "このコースの専門分野"
    persona_instruction = persona_prompt(response_persona, target="response")
    persona_block = f"\n\n**口調設定:**\n{persona_instruction}" if persona_instruction else ""
    return f"""あなたは{domain_label}を専門とする研究者で、学生が立てた予想と論文・出典の内容を突き合わせる案内役です。

**厳守事項:**
1. 【断定しないでください】学生のメッセージに含まれる本人の予想と、提供された
   出典・論文の骨格とを比べ、「食い違いの可能性」がある観点を仮説文体で挙げて
   ください。これが正しい・間違っているという断定はしないでください。
2. 【候補は最大3点】観点の候補は多くとも3点までとし、それぞれ短く述べてください。
   すべてを網羅しようとしないでください。
3. 【採点や点数評価をしないでください】正解/不正解の判定、点数、一致度、的中率などは
   一切出力しないでください。学生の予想の良し悪しを評価しないでください。
4. 【権威は出典】判断の根拠は必ず提供された出典・論文の記述に置き、出典に無い推測を
   断定的に述べないでください。{persona_block}"""


# 確認問題の壁打ちモード（LearningChatRequest.check_scaffold）で system プロンプトへ
# 追記する拘束。要素（定義・事実・関係）の伝授は行い、組み立て（要素をどう繋いで答えに
# するか）は学習者に委ねる。既存の system プロンプトを置き換えるのではなく、選ばれた
# プロンプトの末尾に追記して応答様式だけを変える（RAG 検索・痕跡記録・コスト計上は不変）。
_CHECK_SCAFFOLD_INSTRUCTION = """**確認問題の壁打ちモード（厳守）:**
1. 【解答そのものを出さない】確認問題への解答そのもの・模範解答・結論の言い切りを
   提示しないでください。
2. 【構成要素は説明してよい】回答に必要な知識の構成要素（定義・事実・関係）は、
   求められれば個々に説明してかまいません。
3. 【組み立ては学習者】要素をどのように組み合わせると答えに結びつくか（組み立て）は
   学習者自身が行います。組み立ての手順や結論への道筋を先回りして示さないでください。
4. 【壁打ち相手として応じる】学習者が組み立てを試みたら壁打ち相手として応じ、
   合っている部分・まだ使われていない要素を事実として指摘してください。
   正誤の断定や完成形の提示はしないでください。
5. 【問いかけを1つ添える】応答の末尾に、学習者自身が次の一歩を組み立てられる
   問いかけを1つだけ添えてください。
6. 【答えを求められても】学習者が答えを直接求めても、構成要素の説明と問いかけで
   自力の再回答を促してください。"""


# 本文中の出典マーカー。フロント（app.js linkifyCitations）が扱えるのは半角 [出典N] のみ
# だが、LLM は全角括弧・全角数字の表記ゆれを出すことがあるため広めに受ける。
_CITATION_MARKER_RE = re.compile(r"[\[［【〔]\s*出典\s*([0-9０-９]+)\s*[\]］】〕]")
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _reconcile_citation_markers(answer: str, valid_indices: set[int]) -> str:
    """回答本文の出典マーカーを cited_sources と突き合わせて正規化する。

    LLM は書式指示（「[出典N] を本文に挿入せよ」）に引きずられ、文脈に番号付き出典が
    無い・番号が足りない場合でも [出典N] を捏造することがある。フロントの
    linkifyCitations は対応する根拠の無い番号を素通しするため、「リンクにならない
    出典番号」として学習者に見えてしまう（出典の内容を確認できない）。ここで
    ①根拠のある番号は半角 [出典N] に正規化（表記ゆれをリンク可能な形に戻す）、
    ②根拠の無い番号は本文から取り除く（確認できない出典番号を見せない）。
    """

    def _sub(m: re.Match) -> str:
        n = int(m.group(1).translate(_FULLWIDTH_DIGITS))
        return f"[出典{n}]" if n in valid_indices else ""

    text = _CITATION_MARKER_RE.sub(_sub, answer or "")
    # マーカー除去で句読点の直前に残った空白だけを軽く掃除する（本文には触れない）。
    return re.sub(r"[ \t]+([。、．，])", r"\1", text)


def _learner_selected_anchor(body: LearningChatRequest) -> dict | None:
    """発話時の明示アンカー（構造帰属・方法A）を非LLMで構築する。無ければ None。

    「どこ（anchor）」はこの操作で確定するが「どう（doubt_type）」までは分からないため
    unclassified のまま保持する（P4。方法B/C が後から補い得る）。
    """
    if body.element_id:
        atype = anchor_type_for_element(body.element_type)
        # 出典系（reference/citation→chunk）はタップ元チャンクをアンカーにする
        anchor_id = body.chunk_id if (atype == "chunk" and body.chunk_id) else body.element_id
        return build_anchor_payload(
            anchor_type=atype,
            anchor_id=anchor_id,
            anchor_label=body.element_label or body.element_id,
            doubt_type="unclassified",
            attribution_source=ATTRIBUTION_LEARNER_SELECTED,
            evidence_quote="",
            reason="element_tap",
            confidence=1.0,
        )
    sel = (body.selection_text or "").strip()
    if sel:
        seg = body.selection_segment_id
        return build_anchor_payload(
            anchor_type="segment",
            anchor_id=f"seg_{int(seg)}" if seg is not None else "",
            anchor_label=(sel[:40] + "…") if len(sel) > 40 else sel,
            doubt_type="unclassified",
            attribution_source=ATTRIBUTION_LEARNER_SELECTED,
            evidence_quote=sel,
            reason="text_selection",
            confidence=1.0,
        )
    return None


# ---------------------------------------------------------------------------
# アンカー優先ラダー（設計 docs/features/learning_ui_inspect_hover_design.md §7、
# IH6/IH7）: ホバー+ラッチ機能（Phase 3・フロント未実装）が element_label を自由文
# メッセージに添付して送ってきたときのために、既存の1 LLM コールへヒントを同梱する。
# 追加の分類・生成コールは作らない（IH6）。
# ---------------------------------------------------------------------------

_ANCHOR_LADDER_HINT_PREFIX = "[アンカーヒント] "


def _build_anchor_ladder_hint(
    body: LearningChatRequest, history: list[dict] | None,
) -> str | None:
    """アンカー優先ラダーのヒントブロックを構築する（設計 §7）。

    非LLM・副作用なしの純関数（DB・LLM を一切呼ばない）。既存の学習チャット system
    プロンプトへ追記する短いヒント文字列を返す。優先順位（設計 §7）:

      1. ラッチ中アンカー — ``element_label``（+ ``element_type``）が自由文メッセージに
         添付されている場合。既存の要素タップ typed 経路（``action="EXPLAIN_GRAPH_ELEMENT"``）
         は本関数の呼び出しに到達する前に早期 return するため、ここに渡ってくる
         ``element_label`` は常に「ラッチ後の自由文送信」（設計 §6.2、Phase 3 フロント
         実装分）由来になる。呼び出し側はこの関数を RAG 本文生成の直前（typed action /
         usage_help pre-route の早期 return より後段）でのみ呼ぶこと。
      2. 表示中のスライド / セグメント — ``position_anchor.segment_id`` /
         ``selection_segment_id``。
      3. 現在のトピック — 呼び出し側の user プロンプトに既にトピック名が入っているため、
         単独のランクとしては起動しない（ランク1のヒント文中で候補の一つとして言及するのみ）。
      4. 直近回答の第1根拠チャンク — ``history`` の直近 assistant メッセージが持つ
         ``sources[0]``（サーバ側の保存履歴・クライアント再送履歴のいずれにも同じ形で
         乗っている。§9 参照）。

    アンカー文脈が一切無ければ ``None``（従来と完全同一のプロンプトを維持する）。
    指示ではなくヒントとして書く（断定・強制をしない, IH6）。confidence 等の生数値は
    一切含めない（IH7）。
    """
    label = (body.element_label or body.element_id or "").strip()
    if label:
        anchor_type = anchor_type_for_element(body.element_type)
        type_label = ANCHOR_TYPE_LABELS.get(anchor_type, "")
        target = f"{type_label}〈{label}〉" if type_label else f"〈{label}〉"
        return (
            f"{_ANCHOR_LADDER_HINT_PREFIX}学習者は{target}に注目した状態でこの発言をしています。"
            f"発言がそれに関係する場合は{target}を最優先の文脈として答えてください。"
            "関係しない場合は、表示中のセクション・現在のトピック・直前の回答の根拠のうち"
            "最も近いものを選んで答えてください。"
            "回答の冒頭または末尾に、何について答えたかを一行で明示してください"
            "（例:「〈○○〉についてお答えしています」）。"
        )

    seg = None
    if isinstance(body.position_anchor, dict) and "segment_id" in body.position_anchor:
        seg = body.position_anchor.get("segment_id")
    if seg is None:
        seg = body.selection_segment_id
    if seg is not None:
        return (
            f"{_ANCHOR_LADDER_HINT_PREFIX}学習者は、いま表示している教材の区画に注目した"
            "状態でこの発言をしています。発言がその内容に関係する場合は、その文脈を優先"
            "して答えてください。"
        )

    for turn in reversed(history or []):
        if not isinstance(turn, dict) or turn.get("role") != "assistant":
            continue
        sources = turn.get("sources")
        if isinstance(sources, list) and sources:
            first = sources[0] if isinstance(sources[0], dict) else {}
            title = str(first.get("source_title") or "").strip()
            if title:
                return (
                    f"{_ANCHOR_LADDER_HINT_PREFIX}直前の回答は〈{title}〉を根拠にしています。"
                    "発言がその続きの話題であれば、その文脈を優先して答えてください。"
                )
        break  # 直近の assistant メッセージのみを見る（それより前へは遡らない）

    return None


# 方法C の1タップ選択肢（unclassified は「その他」として提示しない — 未選択のまま
# 閉じれば unclassified が保たれる）
_ANCHOR_CONFIRM_DOUBT_OPTIONS = [
    {"doubt_type": d, "label": DOUBT_TYPE_LABELS[d]}
    for d in ("definition", "justification_gap", "premise", "prior_conflict", "scope", "connection")
]


def _document_id_for_material(material_id: str | None) -> str | None:
    """学習チャットの ``material_id``（``documents.source_path``）を ``documents.id``
    （UUID 文字列）へ解決する（Phase 2 §5.3: element_explanations は document_id
    スコープのため）。既存の Phase 4 ヘルパー（``routes.lecture._resolve_course_document_ids``）
    をそのまま再利用する（1件解決も含め同じ関数で扱う）。
    """
    mid = str(material_id or "").strip()
    if not mid:
        return None
    ids = _resolve_course_document_ids([mid])
    return ids[0] if ids else None


def _element_explanation_ref_for_graph_context(context: dict) -> tuple[str, str] | None:
    """グラフ要素ポップアップの (element_type, element_id) を、element_explanations の
    ポリモーフィック語彙（figure/theory_component/theory_claim/equation）へマップする。

    現状 ``services._derive_graph_mentions`` が生成する graph_mentions は、legacy な
    ``documents.knowledge_graph``（PaperStructure 由来の concept/relationship）と
    TeX 由来の citation/reference のみで、これらは theory_components/theory_claims と
    ID 体系が異なるため対応不能（誤結合を避けるためマップしない）。唯一 ``formula`` は
    ``chunks.formulas[].id`` が equation_semantics の ``equation_id`` と同一の ID 体系
    （``persist_equation_previews_to_chunks`` 参照）のため ``equation`` としてマップできる。
    ``element_type in ('component','theory_component')`` / ``('claim','theory_claim')`` は
    現状フロント（app.js）が C層（component_explanations）の別導線へバイパスしており本関数
    には到達しない想定だが、将来この endpoint 経由で来ても正しく解決できるよう対応させておく。

    注意（2026-07-19 確認・未修正 — 現状フロント未到達のため対応は将来の課題）:
    ``context.get("element_id")`` をそのまま ``ELEMENT_TYPE_COMPONENT``/``ELEMENT_TYPE_CLAIM``
    として返すが、``element_explanations`` の theory_component/theory_claim 行は
    ``_stage_contextual_explanation`` が ``persist_claims_components_graph`` より前に走る
    ため agent 側 ID（``ComponentRecord.component_id`` / ``ClaimObjectRecord.claim_id``）で
    保存されている（``contextual_explanation_inputs.py`` 冒頭 docstring）。この
    ``context.get("element_id")`` が DB UUID（例えば component_graph node id 由来）だと、
    直後の ``approved_for_elements`` は ``core.deliberation.decomposition.
    explanations_for_element`` と同じ ID 形式の不一致で行を引けない可能性がある
    （decomposition.py 側は :func:`core.deliberation.decomposition._agent_id_candidates_for_focus`
    で修正済み。本関数を実際に配線する際は同じ legacy_ids 突合を適用すること）。
    """
    target_formula = context.get("target_formula")
    if isinstance(target_formula, dict):
        formula_id = str(target_formula.get("id") or "").strip()
        if formula_id:
            return (element_explanations.ELEMENT_TYPE_EQUATION, formula_id)

    element_type = str(context.get("element_type") or "").strip()
    element_id = str(context.get("element_id") or "").strip()
    if element_id and element_type in ("component", element_explanations.ELEMENT_TYPE_COMPONENT):
        return (element_explanations.ELEMENT_TYPE_COMPONENT, element_id)
    if element_id and element_type in ("claim", element_explanations.ELEMENT_TYPE_CLAIM):
        return (element_explanations.ELEMENT_TYPE_CLAIM, element_id)
    return None


def _approved_graph_element_answer(
    context: dict,
    element_label: str,
    source_title: str,
) -> str | None:
    """承認済み element_explanations があれば、それを主文にした回答を組み立てる（Phase 2 §5.3）。

    学習者ポップアップの優先順位: approved contextual → C層承認済み（別導線、
    ``showComponentExplanations`` 等）→ ローカル生成。本関数が None を返す場合は
    呼び出し側が既存のローカル生成へフォールバックする。

    contextual を主文にし、generic があれば「一般には…」として続ける
    （既存の出典表記 ``[出典: ...]`` の流儀は維持）。``approved_for_elements`` は
    ``status='approved'`` の行のみ返すため candidate/dismissed/superseded は混入しない
    （E2）。confidence 等の生値は使わず body 文字列のみを組み込む。
    """
    element_ref = _element_explanation_ref_for_graph_context(context)
    if element_ref is None:
        return None
    document_id = _document_id_for_material(context.get("material_id"))
    if not document_id:
        return None

    session = _pg_session()
    try:
        approved = element_explanations.approved_for_elements(session, document_id, [element_ref])
    except Exception:
        logger.warning("Failed to load approved element_explanations for graph element", exc_info=True)
        return None
    finally:
        session.close()

    rows = approved.get(element_ref) or []
    contextual_body = next(
        (
            r.get("body") for r in rows
            if r.get("kind") == element_explanations.KIND_CONTEXTUAL and r.get("body")
        ),
        None,
    )
    generic_body = next(
        (
            r.get("body") for r in rows
            if r.get("kind") == element_explanations.KIND_GENERIC and r.get("body")
        ),
        None,
    )
    if not contextual_body and not generic_body:
        return None

    lines = [f"**{element_label}** について説明します。", ""]
    if contextual_body:
        lines.append(contextual_body)
        lines.append("")
    if generic_body:
        lines.append(f"一般には、{generic_body}")
        lines.append("")
    lines.append(f"[出典: 『{source_title}』]")
    return "\n".join(lines).strip()


def _generate_graph_element_explanation(
    *,
    user_id: str,
    course_id: str,
    topic_id: str,
    course_title: str,
    topic_title: str,
    course_data: dict,
    body: LearningChatRequest,
    on_llm_call: Callable[[], None] | None = None,
) -> LearningChatResponse:
    """グラフ要素サジェストのクリックを、通常チャットとは独立して処理する。"""
    if not body.chunk_id or not body.element_id:
        raise HTTPException(status_code=400, detail="EXPLAIN_GRAPH_ELEMENT requires chunk_id and element_id")

    # レビュー確定の修正1（セキュリティ / DM2）: 主チャンク取得に可視性ゲートが無く、
    # 受講中の学習者が任意の chunk_id を送るだけで他教員の Private 論文の本文・数式・
    # 承認済み説明を引き出せていた。get_chunk_claim_refs と同じ複合集合
    # （コース sources ∪ 本人可視 document）を必須引数として渡し、範囲外の chunk_id は
    # 「チャンクが見つからない」（下の 404）へ落とす。
    allowed_document_ids = set(list_course_source_document_ids(course_data)) | set(
        list_visible_document_ids(user_id)
    )
    context = get_graph_element_context(
        course_data,
        body.chunk_id,
        body.element_id,
        body.element_type,
        body.element_label,
        allowed_document_ids=allowed_document_ids,
    )
    if not context:
        raise HTTPException(status_code=404, detail="Chunk not found")

    element_label = context.get("element_label") or body.element_label or body.element_id
    instructor_id = context.get("instructor_id")
    material_id = context.get("material_id")
    source_title = context.get("source_title") or "教材"
    user_message = body.message or f"{element_label}を説明"

    # Phase 2 §5.3: 承認済み element_explanations があれば最優先で使い、ローカル LLM 生成
    # をスキップする（candidate/dismissed/superseded・confidence 生値は出さない、E2/E6）。
    approved_answer = _approved_graph_element_answer(context, element_label, source_title)
    if approved_answer is not None:
        persist_chat_history(
            user_id, course_id, topic_id,
            body.history, user_message, approved_answer,
        )
        return LearningChatResponse(answer=approved_answer, course_update=None)

    graph_description = (context.get("graph_description") or "").strip()
    related_chunks = context.get("related_chunks") or []
    target_formula = context.get("target_formula") or {}
    target_formula_latex = str(target_formula.get("latex") or "").strip() if isinstance(target_formula, dict) else ""

    if graph_description:
        answer = (
            f"**{element_label}** について説明します。\n\n"
            f"{graph_description}\n\n"
            f"現在のチャンクでは、この要素が周辺の議論を理解するための足場になります。"
            f"[出典: 『{source_title}』]"
        )
    else:
        related_block = "\n\n".join(
            f"[出典: 『{r.get('source_title') or source_title}』]\n{r.get('text', '')[:1200]}"
            for r in related_chunks[:3]
        )
        personal = get_personal_layer(user_id, course_id)
        # チャット型AI支援の共通基盤整理 §2-2: 直近6件・2000字/件へウィンドウ化
        # （正本ユーティリティへの委譲。挙動は現行とほぼ同一）。
        recent_history = "\n".join(
            f"{h.get('role')}: {h.get('content', '')}"
            for h in window_history(body.history, max_messages=6, max_chars=2000)
        )
        response_persona = course_persona_settings(course_data)["response_persona"]
        persona_instruction = persona_prompt(response_persona, target="response")
        params = get_llm_params("standard")
        prompt = (
            f"あなたは「{course_title}」の学習を支援するチューターです。\n"
            f"現在のトピック: {topic_title}\n"
            f"説明対象: {element_label} ({context.get('element_type')})\n\n"
            + (f"口調設定:\n{persona_instruction}\n\n" if persona_instruction else "")
            + (
                f"対象数式（この式を必ずそのまま使って説明すること）:\n"
                f"$${target_formula_latex}$$\n\n"
                if target_formula_latex else ""
            )
            + f"現在表示中のチャンク:\n{context.get('chunk_text', '')[:2400]}\n\n"
            f"関連教材:\n{related_block or '明示的な説明は見つかりませんでした。'}\n\n"
            f"学習者の個人レイヤー:\n{personal}\n\n"
            f"直近の会話:\n{recent_history}\n\n"
            "上記を踏まえ、学生に合わせて説明してください。"
            "既存教材に明示的な説明がない場合は、現在のチャンクの文脈から補って説明してください。"
            "数式が関係する場合は、インライン数式は必ず $...$、別行数式は必ず $$...$$ で囲んでください。"
            "裸の \\mathcal や \\frac など、区切り文字のないLaTeXコマンドは出力しないでください。"
            "[[FORMULA_0]] のようなプレースホルダー名は説明文に出さないでください。"
            "最後に短い確認文を1つ添えてください。"
        )
        if on_llm_call:
            on_llm_call()
        answer = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
            temperature=0.3,
        )
        if target_formula_latex:
            formula_id = str(target_formula.get("id") or "").strip() if isinstance(target_formula, dict) else ""
            if formula_id:
                answer = answer.replace(formula_id, f"${target_formula_latex}$")
            if target_formula_latex not in answer:
                answer = f"対象の数式は次の式です。\n\n$${target_formula_latex}$$\n\n" + answer
    persist_chat_history(
        user_id, course_id, topic_id,
        body.history, user_message, answer,
    )
    return LearningChatResponse(answer=answer, course_update=None)


def _topic_student_material(topic: dict) -> str:
    material = topic.get("student_material")
    if isinstance(material, dict):
        text = str(material.get("source_text") or "").strip()
        if text:
            return text
    return str(topic.get("content") or topic.get("summary") or "").strip()


def _normalize_check_question_item(item: object) -> dict:
    if isinstance(item, dict):
        question = str(item.get("question") or item.get("text") or "").strip()
        requirements = item.get("answer_requirements") or item.get("required_elements") or []
        if isinstance(requirements, str):
            requirements = [line.strip() for line in requirements.splitlines() if line.strip()]
        elif isinstance(requirements, list):
            requirements = [str(v).strip() for v in requirements if str(v).strip()]
        else:
            requirements = []
        return {
            "question": question,
            "model_answer": str(item.get("model_answer") or item.get("answer") or "").strip(),
            "answer_requirements": requirements,
            "explanation": str(item.get("explanation") or item.get("rationale") or "").strip(),
        }
    return {
        "question": str(item or "").strip(),
        "model_answer": "",
        "answer_requirements": [],
        "explanation": "",
    }


def _select_check_question(topic: dict, requested_question: str = "", request_item: dict | None = None) -> dict:
    if request_item:
        normalized = _normalize_check_question_item(request_item)
        if normalized.get("question"):
            return normalized
    questions = topic.get("check_questions") or topic.get("assessment_prompts") or []
    normalized_questions = [_normalize_check_question_item(item) for item in questions]
    requested = (requested_question or "").strip()
    if requested:
        for item in normalized_questions:
            if item.get("question") == requested:
                return item
        return _normalize_check_question_item(requested)
    for item in normalized_questions:
        if item.get("question"):
            return item
    return _normalize_check_question_item("このセクションの要点を説明してください。")


def _topic_formulas_from_content_blocks(topic: dict) -> list[dict]:
    """topic.content_blocks の equations から、UIの数式埋め込み解決用 formulas を作る。

    未解決の数式 fix: LaTeX が無くても reading(plain_text) や原文(raw_text) があれば
    数式項目として渡す（フロントは latex → plain_text → raw_text の順にフォールバック
    描画する）。これで `![[equation:id]]` 埋め込みが「未解決」にならずに済む。描画材料が
    一切無い項目だけを除外する。
    """
    formulas: list[dict] = []
    for block in topic.get("content_blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "equations":
            continue
        for item in block.get("items") or []:
            if not isinstance(item, dict):
                continue
            latex = item.get("latex") or ""
            plain_text = item.get("plain_text") or ""
            raw_text = item.get("raw_text") or ""
            if not (latex or plain_text or raw_text):
                continue
            formulas.append({
                "id": item.get("equation_id") or f"TOPIC_FORMULA_{len(formulas)}",
                "latex": latex,
                "label": item.get("label") or "",
                "plain_text": plain_text,
                "raw_text": raw_text,
                "is_display": True,
            })
    return formulas


@router.get(
    "/courses/{course_id}/topics/{topic_id}/material",
    response_model=TopicMaterialResponse,
)
def get_topic_material(
    course_id: str,
    topic_id: str,
    current_user: dict = Depends(_get_current_user),
) -> TopicMaterialResponse:
    """トピック本文を受講画面用の教材として返す。

    受講体験の主ソースは ``learning_courses.data.topics[].content``。
    PDF復元チャンクは、topic content が未生成の場合だけ後方互換のフォールバックに使う。
    """
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    topics = course_topics(course_data)
    topic_index = None
    topic = None
    for i, t in enumerate(topics):
        if t.get("id") == topic_id:
            topic_index = i
            topic = t
            break
    if topic_index is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    topic_text = _topic_student_material(topic or {})
    if topic_text.strip():
        formulas = _topic_formulas_from_content_blocks(topic or {})
        # ![[figure:id]] 埋め込みを [[FIGURE_N]] プレースホルダーに解決する
        # （Phase 4 図のコース流通 §7.2。レクチャー表示の build_topic_slides と同じ解決を通す）。
        figures_by_id = _load_course_figures_by_id(course_id, course_data)
        resolved_text, figures = resolve_figure_embeds(topic_text, figures_by_id)
        # 承認済み contextual 説明を充填する（Phase 2 §5.3。無ければ explanation は None のまま）。
        _attach_figure_explanations(figures, figures_by_id)
        # ``![[component:id]]`` / ``![[claim:id]]`` / ``![[source:id]]`` を学習画面でも
        # 解決できるよう、トピックに公開済みの参照だけから読み取り専用 DTO を渡す
        # （管理画面 lsTopicEvidenceItems と同一規則。DB 上の任意 ID は解決しない）。
        evidence_items = build_topic_evidence_items(topic or {})
        chunks = [ChunkContent(
            id=f"topic:{topic_id}",
            text=resolved_text,
            chunk_index=topic_index,
            formulas=formulas,
            figures=figures,
            evidence_items=evidence_items,
            chapter=None,
            section=(topic or {}).get("title"),
            material_id=None,
            graph_mentions=[],
        )]
        return TopicMaterialResponse(topic_id=topic_id, chunks=chunks)

    all_chunks = get_course_chunks_ordered(course_data)
    if topic_index < len(all_chunks):
        raw = all_chunks[topic_index]
        chunks = [ChunkContent(
            id=raw["id"],
            text=raw["text"],
            chunk_index=raw["chunk_index"],
            formulas=raw.get("formulas", []),
            chapter=raw["chapter"],
            section=raw["section"],
            material_id=raw.get("material_id"),
            graph_mentions=raw.get("graph_mentions", []),
        )]
    else:
        chunks = []

    return TopicMaterialResponse(topic_id=topic_id, chunks=chunks)


# ---------------------------------------------------------------------------
# 図画像配信（学習者向け, Phase 4 図のコース流通 §7.3）
# ---------------------------------------------------------------------------
#
# admin 側の図配信エンドポイント（routes/admin.py::get_document_figure_image、
# _require_teacher・教材横断アクセス）は変更・流用しない。学習者向けは3条件 AND の
# fail-closed ゲート（受講ゲート / 図の document がコース sources に含まれる / 図が
# コース content から実際に参照されている）を独自に通す。


def _load_figure_row_by_id(figure_id: str) -> dict | None:
    """``document_figures`` を id 単位で取得する（学習者向け画像配信の単一行ルックアップ）。

    admin 側は document_id 単位で ``load_document_figures()`` を使うが、学習者向け
    エンドポイントは URL に document_id を持たないため figure_id から直接引く。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT id::text, document_id, minio_key
                FROM document_figures
                WHERE id = CAST(:figure_id AS uuid)
                LIMIT 1
            """),
            {"figure_id": figure_id},
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "document_id": row[1], "minio_key": row[2]}
    except Exception:
        logger.warning("Failed to load figure row %s", figure_id, exc_info=True)
        return None
    finally:
        session.close()


def _topic_linked_figure_ids(topic) -> list[str]:
    """並行実装中の ``CourseTopic.linked_figure_ids`` を防御的に読む。

    ``course_topics()`` が返す実体は常に dict（JSONB からの読み取り）だが、
    ``CourseTopic``（``extra="allow"``）インスタンスが渡された場合にも備えて
    ``getattr`` にフォールバックする。
    """
    if isinstance(topic, dict):
        value = topic.get("linked_figure_ids")
    else:
        value = getattr(topic, "linked_figure_ids", None)
    if not value:
        return []
    return [str(v) for v in value if v]


def _course_references_figure(course_data: dict, figure_id: str) -> bool:
    """figure_id がコース content（トピック本文の ``![[figure:id]]`` embed または
    ``linked_figure_ids``）から実際に参照されているかを判定する（§7.3 条件3）。

    走査は ``iter_all_topics``（フラット ``topics[]`` + 章ネスト ``chapters[].topics[]``）。
    旧実装は ``course_topics()``（フラットのみ）だったため章ネスト形のトピックから
    参照されている図を取りこぼしていた（教材図スタジオ設計書 §7.2 条件3 の既存バグ
    修正。抽出図側もこの修正の恩恵を受ける）。
    """
    for topic in iter_all_topics(course_data):
        text = _topic_student_material(topic)
        if figure_id in find_figure_embed_ids(text):
            return True
        if figure_id in _topic_linked_figure_ids(topic):
            return True
    return False


def _load_teaching_figure_row(course_id: str, figure_id: str) -> dict | None:
    """``course_teaching_figures`` を id 単位で取得する（教材図スタジオ設計書 §7.2）。

    ``document_figures`` に無い figure_id を引く second lookup。取得失敗は None
    （fail-closed で 404 になる）。course_id 一致・status の判定は呼び出し側で行う。
    """
    session = _pg_session()
    try:
        # 配信に必要な列のみの lean 投影（revisions 数MB を読まない）
        return teaching_figures_store.get_teaching_figure_for_delivery(session, figure_id)
    except Exception:
        logger.warning(
            "Failed to load teaching figure row course=%s figure=%s",
            course_id, figure_id, exc_info=True,
        )
        return None
    finally:
        session.close()


@router.get("/courses/{course_id}/figures/{figure_id}/image")
def get_course_figure_image(
    course_id: str,
    figure_id: str,
    current_user: dict = Depends(_get_current_user),
) -> Response:
    """学習者向け図画像配信（Phase 4 図のコース流通 §7.3 + 教材図スタジオ §7.2）。

    **抽出図**（``document_figures``）は3条件の AND、いずれか欠ければ 404（fail-closed）:
    1. 受講ゲート（``get_accessible_course_data`` — 本人が当該コースを閲覧できる）
    2. 図の document がコースの ``sources[].document_id`` / ``material_id`` に含まれる
    3. 図がコース content（``topics[].linked_figure_ids`` または student_material 内の
       ``![[figure:id]]`` 参照）から実際に参照されている

    ``document_figures`` に無い figure_id は**採用済み教材図**（``course_teaching_figures``）
    として引き、4条件の AND で判定する（FG4）: 受講ゲート / 図の ``course_id`` 一致 /
    条件3（同じ ``_course_references_figure``）/ ``status='adopted'``。draft・retired は
    学習者に出ない。
    """
    course_data = get_accessible_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    try:
        uuid.UUID(figure_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Figure not found")

    figure_row = _load_figure_row_by_id(figure_id)
    if figure_row and figure_row.get("minio_key"):
        # --- 抽出図（document_figures）経路 ---
        # 条件2: 図の document がコースの sources に含まれる
        course_document_ids = set(_course_document_ids(course_data))
        if str(figure_row.get("document_id")) not in course_document_ids:
            raise HTTPException(status_code=404, detail="Figure not found")

        # 条件3: 図がコース content から実際に参照されている
        if not _course_references_figure(course_data, figure_id):
            raise HTTPException(status_code=404, detail="Figure not found")

        try:
            image_bytes = get_storage_client().get_object("figure-images", figure_row["minio_key"])
        except Exception:
            logger.warning(
                "get_course_figure_image: MinIO fetch failed course=%s figure=%s",
                course_id, figure_id, exc_info=True,
            )
            raise HTTPException(status_code=404, detail="Figure image not found")

        # 抽出図は常に PNG（document_figures に content_type 列は無い）。
        return figure_image_response(image_bytes, None)

    # --- 採用済み教材図（course_teaching_figures）経路 ---
    teaching_row = _load_teaching_figure_row(course_id, figure_id)
    if not teaching_row or not teaching_row.get("minio_key"):
        raise HTTPException(status_code=404, detail="Figure not found")
    # 条件2': 図の course_id が一致する（他コースの図は出さない）
    if str(teaching_row.get("course_id") or "") != str(course_id):
        raise HTTPException(status_code=404, detail="Figure not found")
    # 条件4: status='adopted' のみ（draft / retired は学習者に出ない）
    if str(teaching_row.get("status") or "") != TEACHING_FIGURE_STATUS_ADOPTED:
        raise HTTPException(status_code=404, detail="Figure not found")
    # 条件3: 図がコース content から実際に参照されている（抽出図と同じ判定）
    if not _course_references_figure(course_data, figure_id):
        raise HTTPException(status_code=404, detail="Figure not found")

    try:
        image_bytes = get_storage_client().get_object("figure-images", teaching_row["minio_key"])
    except Exception:
        # 正本は DB の svg_source。MinIO スナップショットが未反映・欠落でも
        # 採用済み図の配信を止めない（教員向けエンドポイントと同じフェイルソフト）。
        logger.warning(
            "get_course_figure_image: MinIO fetch failed (teaching), falling back to svg_source "
            "course=%s figure=%s",
            course_id, figure_id, exc_info=True,
        )
        svg_source = teaching_row.get("svg_source") or ""
        if not svg_source:
            raise HTTPException(status_code=404, detail="Figure image not found")
        image_bytes = svg_source.encode("utf-8")

    # 教材図は SVG（``figure_image_response`` が nosniff + CSP sandbox を付ける・FG3）。
    return figure_image_response(image_bytes, teaching_row.get("content_type"))


@router.post(
    "/courses/{course_id}/topics/{topic_id}/check",
    response_model=LearningCheckQuestionResponse,
)
def check_topic_understanding(
    course_id: str,
    topic_id: str,
    body: LearningCheckQuestionRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningCheckQuestionResponse:
    """次セクションへ進む前の確認問題を採点し、未理解ならつまづきとして記録する。"""
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    topic = find_course_topic(course_data, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    check_item = _select_check_question(topic, body.question, body.check_question)
    question = check_item.get("question") or "このセクションの要点を説明してください。"
    expected_model_answer = str(check_item.get("model_answer") or "").strip()
    answer_requirements = [
        str(v).strip() for v in (check_item.get("answer_requirements") or [])
        if str(v).strip()
    ]
    explanation = str(check_item.get("explanation") or "").strip()

    material_text = _topic_student_material(topic)
    requirements_text = "\n".join(f"- {r}" for r in answer_requirements) or "(未設定)"
    params = get_llm_params("fast")
    prompt = (
        "あなたは確認問題を採点する大学教員です。JSONのみを返してください。\n"
        "形式: {\"passed\": true/false, \"feedback\": \"短い講評\", \"model_answer\": \"模範解答\", \"explanation\": \"必要なら解説\"}\n\n"
        f"コース: {_course_title(course_data, default=course_id)}\n"
        f"セクション: {topic.get('title', topic_id)}\n"
        f"教材:\n{material_text[:5000]}\n\n"
        f"確認問題: {question}\n"
        f"模範解答（設定済みの場合はこれを基準にする）:\n{expected_model_answer or '(未設定)'}\n\n"
        f"回答に必要な要素:\n{requirements_text}\n\n"
        f"解説（設定済みの場合はフィードバックに反映する）:\n{explanation or '(未設定)'}\n\n"
        f"受講者の回答: {body.answer}\n\n"
        "判定基準: 回答に必要な要素を概ね満たし、自分の言葉で説明できていれば passed=true。"
        "核心が抜けている、逆に理解している、空欄に近い場合は false。\n"
        "feedback は合否に関わらず必ず書いてください（合格時もフロントで学習者に提示します）。"
        "passed=true のときは、回答が押さえられている点を事実として述べ、さらに踏み込める"
        "観点があれば1つだけ添えてください。passed=false のときは、何が抜けているかを述べて"
        "ください。いずれの場合も点数・正解率・達成度のような数値や評価の言い切りは書かず、"
        "褒め言葉の羅列にもしないでください。"
    )

    # M層 Phase 3（§6.4）: コース単位の学習チャットモデル上書きが設定されていれば
    # 採点にも適用する（live 設定、版ピンと独立）。未設定時は従来どおり fast tier 固定
    # （params）を使う — 挙動を変えない。
    _course_chat_model = get_course_live_llm_models(course_id).get(llm_policy.SCENE_LEARNING_CHAT)

    parsed: dict = {}
    try:
        with usage_context("learning:understanding_check", user_id=current_user["id"], course_id=course_id):
            if _course_chat_model:
                # override 時は呼び出し引数として直接渡す（call_argument が最優先, §3-1）。
                # reasoning_effort は明示しない（カタログの既定 effort に委ねる）。
                raw = generate_text(
                    messages=[{"role": "user", "content": prompt}],
                    model=_course_chat_model,
                    temperature=0.1,
                )
            else:
                raw = generate_text(
                    messages=[{"role": "user", "content": prompt}],
                    model=params["model"],
                    reasoning_effort=params["reasoning_effort"],
                    temperature=0.1,
                )
        import json
        import re
        match = re.search(r"\{[\s\S]*\}", raw or "")
        parsed = json.loads(match.group(0) if match else raw)
    except Exception:
        logger.warning("Check question grading failed; using conservative fallback", exc_info=True)
        passed = len((body.answer or "").strip()) >= 40
        parsed = {
            "passed": passed,
            "feedback": "回答の具体性をもとに暫定判定しました。",
            "model_answer": expected_model_answer or material_text[:800],
            "explanation": explanation,
        }

    passed = bool(parsed.get("passed"))
    feedback = str(parsed.get("feedback") or "")
    model_answer = str(parsed.get("model_answer") or expected_model_answer or material_text[:800])
    response_explanation = str(parsed.get("explanation") or explanation or "")

    if not passed:
        instructor_id = None
        session = _pg_session()
        try:
            row = session.execute(
                sa_text("SELECT user_id FROM learning_courses WHERE id = :course_id LIMIT 1"),
                {"course_id": course_id},
            ).fetchone()
            instructor_id = str(row[0]) if row and row[0] else None
        finally:
            session.close()
        record_student_stumble_event(
            instructor_id=instructor_id,
            student_id=current_user["id"],
            course_id=course_id,
            material_id=None,
            chunk_id=None,
            element_id=topic_id,
            element_label=topic.get("title", topic_id),
            event_type="misconception",
            user_message=f"確認問題: {question}\n回答: {body.answer}",
            generated_explanation=model_answer[:4000],
        )

    # コース完了判定のサーバー正本化: 採点結果だけでなく、合格トピック・コース完了状態を
    # learning_states.progress_data に永続化する（フロントが「次のトピックが無い」ことだけで
    # 完走と断定していた問題の是正）。永続化の失敗で採点レスポンス自体は落とさない（fail-open）。
    topic_completed = False
    course_completed = False
    completed_topic_ids: list[str] = []
    try:
        if passed:
            completion = record_topic_check_pass(
                current_user["id"], course_id, topic_id, course_data,
            )
            topic_completed = bool(completion.get("topic_completed"))
            course_completed = bool(completion.get("course_completed"))
            completed_topic_ids = list(completion.get("completed_topic_ids") or [])
        else:
            completion = get_course_completion(current_user["id"], course_id, course_data)
            course_completed = bool(completion.get("course_completed"))
            completed_topic_ids = list(completion.get("completed_topic_ids") or [])
    except Exception:
        logger.warning(
            "Failed to persist topic check completion for user=%s course=%s topic=%s",
            current_user["id"], course_id, topic_id, exc_info=True,
        )

    return LearningCheckQuestionResponse(
        passed=passed,
        feedback=feedback,
        model_answer=model_answer,
        answer_requirements=answer_requirements,
        explanation=response_explanation,
        topic_completed=topic_completed,
        course_completed=course_completed,
        completed_topic_ids=completed_topic_ids,
    )


@router.get(
    "/courses/{course_id}/topics/{topic_id}/chat",
    response_model=LearningChatHistoryResponse,
)
def get_chat_history(
    course_id: str,
    topic_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningChatHistoryResponse:
    """トピックのチャット履歴を返す。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT history FROM learning_chat_history
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id AND topic_id = :topic_id
                LIMIT 1
            """),
            {"user_id": current_user["id"], "course_id": course_id, "topic_id": topic_id},
        ).fetchone()
    finally:
        session.close()

    if not record or not record[0]:
        return LearningChatHistoryResponse(history=[])

    history = record[0] if isinstance(record[0], list) else []
    return LearningChatHistoryResponse(history=history)


@router.delete(
    "/courses/{course_id}/topics/{topic_id}/chat",
)
def delete_chat_history(
    course_id: str,
    topic_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """受講者本人のトピック別チャット履歴だけを削除する。

    質疑応答から派生した個人レイヤー、誤解記録、未回答ログなどは削除しない。
    """
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                DELETE FROM learning_chat_history
                WHERE user_id = CAST(:user_id AS uuid)
                  AND course_id = :course_id
                  AND topic_id = :topic_id
            """),
            {"user_id": current_user["id"], "course_id": course_id, "topic_id": topic_id},
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Failed to delete learning chat history for user=%s topic=%s", current_user["id"], topic_id)
        raise HTTPException(status_code=500, detail="Failed to delete chat history")
    finally:
        session.close()

    return {"status": "deleted"}


@router.delete(
    "/courses/{course_id}/topics/{topic_id}/chat/messages/{message_id}",
)
def delete_chat_message_from(
    course_id: str,
    topic_id: str,
    message_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """機能3（削除）: 指定メッセージ以降の往復を本人の履歴から取り除く。

    書き直しと同じ ``truncate_chat_and_supersede`` を使い、当該メッセージ・その回答・以降の
    往復を履歴から削除し、派生 interest_traces を status='superseded' にする（保持はする。P4）。
    再送は行わない（純粋な削除）。
    """
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    try:
        result = truncate_chat_and_supersede(
            current_user["id"], course_id, topic_id, message_id
        )
    except Exception:
        logger.exception(
            "Failed to delete chat message for user=%s topic=%s msg=%s",
            current_user["id"], topic_id, message_id,
        )
        raise HTTPException(status_code=500, detail="Failed to delete chat message")

    if result is None:
        raise HTTPException(status_code=404, detail="Message not found")

    return {"status": "deleted", "removed_count": result["removed_count"]}


# ---------------------------------------------------------------------------
# 分野の地図 (Issue C-2/C-3) — ↗ アクションの型付き処理
# ---------------------------------------------------------------------------

def _atlas_safe_int(value, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _atlas_step_dicts(raw) -> list[dict]:
    """クライアント添付の related / juxtapose を検証済みの dict 列に正規化する。"""
    out: list[dict] = []
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, dict):
                out.append({
                    "node_id": str(e.get("node_id") or ""),
                    "label": str(e.get("label") or ""),
                    "status": str(e.get("status") or ""),
                    "pill": str(e.get("pill") or ""),
                })
    return out


def _atlas_attribution(ctx: dict) -> dict:
    """帰属つき記録に焼き込む構造化ペイロード (自由文のみに依存しない)。"""
    return {
        "node_id": str(ctx.get("node_id") or ""),
        "level": _atlas_safe_int(ctx.get("level")),
        "skeleton_version": str(ctx.get("skeleton_version") or ""),
        "action": str(ctx.get("action") or ""),
        "node_label": str(ctx.get("node_label") or ""),
    }


def _atlas_topic_attribution(course_data: dict, topic_info: dict | None) -> dict | None:
    """通常学習 (地図アクション以外) の往復から topic → 骨格概念を解決し、個人層の
    「いまここ」を動かすための atlas 帰属を返す (gap1)。

    cheap path: 明示 binding + ラベル一致のみで解決する (corpus 経路は使わない)。
    骨格が無い / 対応概念が引けない場合は None。best-effort — 例外はチャットを止めない。
    """
    if not isinstance(topic_info, dict):
        return None
    try:
        from core import atlas as atlas_module
        from core import atlas_state
        from core import atlas_store

        session = _pg_session()
        try:
            cartridge_id = atlas_state.resolve_course_cartridge(session, course_data)
            if not cartridge_id:
                return None
            # migration 027: 骨格は DB 凍結版が正本 (同梱ファイルはフォールバック)
            skeleton = atlas_store.load_learner_skeleton(cartridge_id, session)
        finally:
            session.close()
        if skeleton is None:
            return None
        node_id = atlas_module.match_topic_to_concept(topic_info, skeleton)
        if not node_id:
            return None
        return {
            "node_id": node_id,
            "level": 1,
            "skeleton_version": skeleton.version,
            "action": "study",
            "node_label": str(topic_info.get("title") or ""),
        }
    except Exception:  # noqa: BLE001
        logger.warning("atlas topic attribution failed", exc_info=True)
        return None


def _atlas_action_response(
    user_id: str,
    course_id: str,
    topic_id: str,
    body: LearningChatRequest,
    course_data: dict,
    ctx: dict,
) -> LearningChatResponse | None:
    """地図の ↗ アクションのうち、決定論的に応答するもの (mind / learn) を処理する。

    - mind (気になる ↗): 学習者本人が宣言した違和感。既存 tension 記録経路
      (interest_traces kind='tension') に帰属つきで記録する。本人発の宣言なので
      candidate ではなく open (P1: 違和感を生成するのは人間 — ここでは人間が押している)。
    - learn (ここから学ぶ ↗): 学習パス提案カード (§8) を決定論的に生成して返す。
      リアルタイム LLM 生成はしない。
    - evid ほかは None を返し、通常の RAG フローに流す (帰属は呼び出し側で焼き込む)。
    """
    action = str(ctx.get("action") or "")
    node_label = str(ctx.get("node_label") or ctx.get("node_id") or "")
    attribution = _atlas_attribution(ctx)

    if action == "mind":
        record_interest_trace(
            user_id, course_id, topic_id,
            kind="tension",
            text=body.message,
            context_label=node_label,
            extra_payload={"atlas": attribution, "origin": "atlas_mind"},
            status="open",
        )
        answer = (
            f"「{node_label}」への引っかかりを、あなたの違和感として帰属つきで記録しました。\n"
            "記録は「問いの軌跡」に残ります。言葉にできるようになったら、"
            "いつでも自分の言葉で書き直せます。"
        )
        persist_chat_history(
            user_id, course_id, topic_id,
            body.history, body.message, answer,
            user_message_id=body.message_id or None,
        )
        return LearningChatResponse(answer=answer, course_update=None)

    if action == "learn":
        interest_view = get_interest_traces(user_id, course_id, topic_id)
        card = build_learning_path_card(
            node_id=str(ctx.get("node_id") or ""),
            node_label=node_label,
            level=_atlas_safe_int(ctx.get("level")),
            skeleton_version=str(ctx.get("skeleton_version") or ""),
            node_status=str(ctx.get("node_status") or ""),
            node_pill=str(ctx.get("node_pill") or ""),
            related=_atlas_step_dicts(ctx.get("related")),
            juxtapose=_atlas_step_dicts(ctx.get("juxtapose")),
            course_topics=course_topics(course_data),
            interest_traces=(interest_view or {}).get("traces") or [],
        )
        answer = (
            f"「{node_label}」からの学習パスの候補です。"
            "各ステップに出所（教材 / AI一般知識）と台帳の状態を添えています。"
        )
        record_interest_trace(
            user_id, course_id, topic_id,
            kind="question",
            text=body.message,
            context_label=node_label,
            extra_payload={"atlas": attribution, "atlas_path_proposed": True},
        )
        persist_chat_history(
            user_id, course_id, topic_id,
            body.history, body.message, answer,
            user_message_id=body.message_id or None,
        )
        return LearningChatResponse(answer=answer, course_update=None, atlas_path_card=card)

    return None


_USAGE_HELP_FOOTER = "教材の内容についての質問なら、そのまま送り直してください。"
_USAGE_HELP_NOT_DOCUMENTED = "その使い方の説明はまだ整備されていません。"


def _usage_help_response(
    user_id: str,
    course_id: str,
    topic_id: str,
    body: LearningChatRequest,
    *,
    on_llm_call: Callable[[], None] | None = None,
) -> LearningChatResponse:
    """学生 HELP ルートのハンドラ（設計 §1-3、ui_anchor 優先は §5.2/§9-1）。

    docs/manual/student/ の凍結索引を検索し、テキスト経路は本文素通し（パラフレーズ
    禁止・quota 非消費）、音声・casual 経路は 1 LLM コールで会話調へ整形する
    （quota は既存 ``on_llm_call`` で消費）。無ヒット・未整備時は LLM を呼ばず固定文
    （捏造禁止, P4）。CHIT_CHAT/LEARNING_ADVICE と同型の早期 return ハンドラで、
    呼び出し側（learning_chat）はここより後段の意図分類・前提知識チェック・
    誤解検出・tension prefilter に到達しない。

    ``body.ui_anchor``（インスペクト・モード中にラッチされていた UI 論理アンカー）が
    あり、かつマップ済みなら、対応マニュアル節を検索より優先して直接解決する
    （マップ未整備・解決失敗なら通常の ``search_manual`` にフォールバック）。
    ``ui_anchor`` が指定された場合、記録する help_usage 痕跡の anchor 値は
    documented/no_hit を問わず常に ``"ui:<ui_anchor>"``（demand 追跡を UI 要素単位に
    一本化する。実際に応答した節は ``manual_citations`` 側の file/anchor/title に
    保持される）。
    """
    ui_anchor_id = (body.ui_anchor or "").strip() or None

    hits: list[dict] = []
    if ui_anchor_id and _resolve_ui_anchor is not None:
        try:
            resolved = _resolve_ui_anchor(ui_anchor_id)
        except Exception:
            logger.warning(
                "resolve_ui_anchor failed for usage help ui_anchor priority; "
                "falling back to keyword search",
                exc_info=True,
            )
            resolved = None
        if resolved and _split_manual_ref is not None:
            manual_file, manual_anchor = _split_manual_ref(resolved.get("manual_anchor", ""))
            hits = [{
                "file": manual_file,
                "anchor": manual_anchor,
                "title": resolved.get("title", ""),
                "body": resolved.get("body", ""),
                "audience": "student",
                "citation": f"manual/student/{manual_file}#{manual_anchor}",
                "documented": True,
            }]

    if not hits and _search_manual is not None:
        try:
            hits = _search_manual(
                body.message, audience="student", limit=3, screen=body.screen_mode,
            ) or []
        except Exception:
            logger.warning("search_manual failed for usage help route; falling back to no-hit", exc_info=True)
            hits = []

    top = hits[0] if hits else None
    documented = bool(top) and bool(top.get("documented", True))

    # ベクトル補助層フォールバック（Phase 3 ①）: 非ベクトル検索が documented
    # ヒットを返さなかったときのみ試す。ヒットすれば通常の documented 経路と
    # 同じ応答（素通し + manual_citations + quota 非消費）に合流する。
    used_vector = False
    if not documented and _vector_search_manual is not None:
        try:
            vector_hits = _vector_search_manual(body.message, audience="student", limit=3) or []
        except Exception:
            logger.warning(
                "vector_search_manual failed for usage help fallback; falling back to no-hit",
                exc_info=True,
            )
            vector_hits = []
        if vector_hits:
            top = vector_hits[0]
            documented = bool(top.get("documented", True))
            used_vector = True

    is_casual = (body.intent_mode or "").strip() == "casual"
    degraded = False

    # ui_anchor 指定時は痕跡の anchor を常に "ui:<ui_anchor>" に一本化する（§9-1）。
    # 未指定時は従来どおり search_manual/vector が見つけた節の citation を使う。
    trace_anchor = f"ui:{ui_anchor_id}" if ui_anchor_id else None

    if not documented:
        answer = f"{_USAGE_HELP_NOT_DOCUMENTED}\n\n{_USAGE_HELP_FOOTER}"
        manual_citations = None
        record_interest_trace(
            user_id, course_id, topic_id,
            kind="help_usage",
            text="使い方の質問",
            extra_payload={"help_anchor": trace_anchor, "documented": False, "no_hit": True},
        )
    else:
        citation = str(top.get("citation") or "")
        manual_citations = [{
            "file": top.get("file", ""),
            "anchor": top.get("anchor", ""),
            "title": top.get("title", ""),
        }]
        body_text = str(top.get("body") or "")
        if is_casual:
            # 音声・casual 経路: 生 Markdown の読み上げは体験として成立しないため
            # 1 LLM コールで会話調へ整形する（quota は on_llm_call で消費）。
            if on_llm_call:
                on_llm_call()
            params = get_llm_params("fast")
            prompt = (
                "以下はシステムの使い方マニュアルの抜粋です。この内容だけを根拠に、"
                "学習者からの音声での質問に短い話し言葉で分かりやすく答えてください。"
                "マニュアルに書かれていない情報を付け足したり断定したりしないでください。\n\n"
                f"質問: {body.message}\n\nマニュアル抜粋:\n{body_text}"
            )
            try:
                formatted = generate_text(
                    messages=[{"role": "user", "content": prompt}],
                    model=params["model"],
                    reasoning_effort=params["reasoning_effort"],
                ).strip()
                if not formatted:
                    raise ValueError("empty response")
                answer = f"{formatted}\n\n{_USAGE_HELP_FOOTER}"
            except Exception:
                logger.warning("usage help casual LLM formatting failed; falling back to raw body", exc_info=True)
                answer = f"{body_text}\n\n{_USAGE_HELP_FOOTER}"
                degraded = True
        else:
            # テキスト経路: 凍結本文を素通し（パラフレーズによる意味ドリフトをゼロにする）。
            answer = f"{body_text}\n\n[出典1]\n\n{_USAGE_HELP_FOOTER}"
        trace_payload = {
            "help_anchor": trace_anchor or (citation or None),
            "documented": True,
            "no_hit": False,
        }
        if used_vector:
            # P4（出所の正直さ）: 非ベクトル索引ではなくベクトル補助層で
            # ヒットしたことを痕跡に残す（質問逐語は積まない）。
            trace_payload["vector"] = True
        record_interest_trace(
            user_id, course_id, topic_id,
            kind="help_usage",
            text=top.get("title") or "使い方の質問",
            extra_payload=trace_payload,
        )

    persist_chat_history(
        user_id, course_id, topic_id,
        body.history, body.message, answer,
        user_message_id=body.message_id or None,
        # 履歴復元後もマニュアル出典チップ（📖）を保つ。chunk ではないので出典チップ
        # （/source-chunk/）には繋がらず、本文の [出典1] は素通しのままでよい。
        assistant_meta={"manual_citations": manual_citations},
    )
    return LearningChatResponse(
        answer=answer,
        course_update=None,
        manual_citations=manual_citations,
        degraded=degraded,
    )


@router.post(
    "/courses/{course_id}/topics/{topic_id}/chat",
    response_model=LearningChatResponse,
)
def learning_chat(
    course_id: str,
    topic_id: str,
    body: LearningChatRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningChatResponse:
    """RAG統合された学習チャットエンドポイント（意図分類ルーティング付き）。

    本体は ``_learning_chat_core``。コーパス回遊 Phase B（コース無し論文議論、
    ``docs/features/corpus_roaming_design.md`` §5.3）の document 直付けファサード
    （``document_discuss_chat``）と**同じコア**を通すための薄い委譲で、コース経路の
    挙動・シグネチャ・処理順序は完全に不変（CR2）。
    """
    return _learning_chat_core(course_id, topic_id, body, current_user)


def _learning_chat_core(
    course_id: str,
    topic_id: str,
    body: LearningChatRequest,
    current_user: dict,
    *,
    course_data: dict | None = None,
    scope_document_ids: set[str] | None = None,
) -> LearningChatResponse:
    """学習チャット本体（コース経路 / document 直付け経路の共通コア）。

    コース経路（``learning_chat``）は追加引数を渡さず、従来どおり
    ``get_course_data`` でコースを解決する（処理順序を含め挙動不変）。

    コーパス回遊 Phase B の document 直付け経路（``document_discuss_chat``）は
    ``course_id`` にセンチネル（``core.discuss.context.document_context_id``）、
    ``course_data`` に document 由来の合成データ、``scope_document_ids`` に
    RAG スコープ（当該 document のみ）を渡す。可視性ゲート
    （``user_can_view_document``）は呼び出し側で済ませている前提（CR1）。

    - ``course_data``: 解決済みのコースデータ。``None`` なら従来どおり本関数内で解決する。
    - ``scope_document_ids``: RAG の ``allowed_document_ids`` の明示指定。
      ``None`` ならコース経路の従来ロジック（discuss_scope / 可視集合）。
    """
    # チャット型AI支援の共通基盤整理 §1: このリクエストで最初に LLM を呼ぶ直前に1回だけ
    # コスト上限を消費する（リクエストスコープの quota_state で多重カウントを防止）。
    _quota_state: dict = {"consumed": False}

    def _consume_quota() -> None:
        _consume_learning_chat_quota(current_user["id"], _quota_state)

    # レビュー確定の修正3: discuss_scope の値検証（不正値 422）は、以降の
    # truncate_chat_and_supersede（機能3の書き直し）より前に行う。従来はこの検証が
    # RAG 検索直前まで遅延しており、不正な discuss_scope を伴う replace_message_id
    # リクエストがサーバ正本の履歴を巻き戻したうえで 422 になっていた
    # （履歴だけ消えて処理は失敗する片手落ちを防ぐ）。詳細文言は後段の本検証
    # （discuss_scope 解決ブロック）と一致させる。
    if (body.intent_mode or "").strip() == "discuss":
        _discuss_scope_precheck = (body.discuss_scope or "course_sources").strip()
        if _discuss_scope_precheck not in ("course_sources", "all_visible"):
            raise HTTPException(
                status_code=422,
                detail=(
                    "discuss_scope には course_sources か all_visible を指定してください"
                    f"（受信値: {_discuss_scope_precheck!r}）。"
                ),
            )

    # 理解サイクル Phase 2（docs/features/understanding_cycle_design.md §8）: cycle_mode の
    # 値検証も discuss_scope precheck と同型で、truncate（機能3の書き直し）より前に行う。
    _cycle_mode_precheck = (body.cycle_mode or "").strip()
    if _cycle_mode_precheck and _cycle_mode_precheck not in ("elicit", "diff"):
        raise HTTPException(
            status_code=422,
            detail=(
                "cycle_mode には elicit か diff を指定してください"
                f"（受信値: {_cycle_mode_precheck!r}）。"
            ),
        )

    # 楽屋モード（構造の降下路 docs/features/structure_descent_design.md §4）の判定は
    # ハンドラ冒頭で前倒しする（2026-08-15 レビュー是正）: 現行フロントは楽屋から
    # typed action / 地図アクションを送らないが、サーバ側防御として backstage のときは
    # EXPLAIN_GRAPH_ELEMENT（body.action）と地図 ↗（body.atlas_context）の early-return
    # 記録経路（kind='question' + structure_anchor / atlas 帰属の焼き込み）に流さず、
    # 常に通常の楽屋質問（kind='backstage_question'）として処理する（SD4）。
    # 非 backstage のときは何も変更しない（既存挙動は完全不変）。
    _is_backstage = bool(body.backstage)
    if _is_backstage:
        body.action = None
        body.atlas_context = None

    # 1. コースデータを取得（document 直付けファサードは解決済みの合成データを渡すため
    #    ここでのコース解決自体を行わない = センチネル course_id が
    #    get_course_data / _apply_course_version_view に流れ込まない）。
    if course_data is None:
        course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")
    # コーパス回遊 Phase B（設計 §5.1）: センチネル判定はここ1箇所で行い、以降の
    # ラベル・コース単位設定の読み出しの分岐に使う（文字列組み立ては core/discuss/context.py が正本）。
    _document_context_id = parse_document_context(course_id)

    # 機能3（書き直し）: replace_message_id 指定時は、その往復以降をサーバ正本の履歴から
    # 取り除き、派生 interest_traces を supersede してから、message を同じ位置から再処理する。
    # サーバの履歴を正本にするため、切り詰め済みの履歴で body.history を上書きし、
    # 以降の文脈構築・永続化（persist_chat_history が全体を UPSERT）を一貫させる。
    if body.replace_message_id:
        _trunc = truncate_chat_and_supersede(
            current_user["id"], course_id, topic_id, body.replace_message_id
        )
        if _trunc is not None:
            body.history = _trunc["truncated_history"]

    topic_info = find_course_topic(course_data, topic_id)
    # discuss モード（設計 §6.2）: 予約 topic_id は既存トピックに存在しないため
    # find_course_topic は None を返し topic_title は生の topic_id にフォールバックする。
    # ここでラベル変換することで、表示・プロンプト・痕跡 context_label すべてに一括で効く。
    if topic_id == DISCUSSION_TOPIC_ID:
        # コーパス回遊 Phase B（設計 §5.4）: コース外（document 直付け）の議論は
        # 「論文との議論（コース外）」と正直に名乗る。ラベル変換はここ1箇所なので、
        # 表示・プロンプト・痕跡 context_label すべてに一括で効く。
        topic_title = (
            DOCUMENT_DISCUSSION_TOPIC_LABEL if _document_context_id else DISCUSSION_TOPIC_LABEL
        )
        _origin_topic_info = {"id": DISCUSSION_TOPIC_ID, "title": topic_title}
    else:
        topic_title = topic_info["title"] if topic_info else topic_id
        _origin_topic_info = topic_info
    course_title = _course_title(course_data, default=course_id)
    # domain が未設定の場合は course_title にフォールバック
    domain = course_data.get("domain") or course_title
    response_persona = course_persona_settings(course_data)["response_persona"]
    support_agent = LearningSupportAgent(course_id, course_data)
    # L2 位置・復帰: クライアントが報告した現在位置（segment/scroll）を origin に取り込み、
    # 寄り道に入っても同じ位置へ正確に復帰できるようにする。
    _anchor = body.position_anchor or {}
    _seg = int(_anchor.get("segment_id") or 0)
    _scroll = int(_anchor.get("scroll_offset") or 0)
    # レビュー確定の修正4: origin_for_topic に topic_info=None を渡すと
    # LearningSupportOrigin.topic_title が生の topic_id（"_discussion"）にフォールバック
    # してしまう（EXPLAIN_GRAPH_ELEMENT 経路等で UI に露出しうる）。ラベル変換は上の1箇所に
    # 留め、discussion のときは _origin_topic_info（変換後ラベル入り）を渡す。
    support_origin = support_agent.origin_for_topic(
        topic_id, _origin_topic_info, segment_id=_seg, scroll_offset=_scroll
    )

    if body.support_action == "return_to_learning_path":
        result = support_agent.return_to_path_result((body.support_context or {}).get("origin"))
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, result.answer,
        )
        return LearningChatResponse(**result.model_dump(), course_update=None)

    # UIサジェスト由来の明示アクションは自然文の意図分類より優先する。
    if body.action == "EXPLAIN_GRAPH_ELEMENT":
        with usage_context("learning:chat", user_id=current_user["id"], course_id=course_id):
            graph_response = _generate_graph_element_explanation(
                user_id=current_user["id"],
                course_id=course_id,
                topic_id=topic_id,
                course_title=course_title,
                topic_title=topic_title,
                course_data=course_data,
                body=body,
                on_llm_call=_consume_quota,
            )
        # グラフ要素の説明は常に detour（origin=現在アンカー）として扱い、
        # どの入口由来でも「学習パスに戻る」を提示する。
        clean_answer, inline_actions = extract_inline_actions(graph_response.answer)
        result = support_agent.with_learning_actions(
            answer=clean_answer,
            mode="detail_explanation",
            origin=support_origin,
            include_continue=False,
            extra_actions=inline_actions,
        )
        # 構造帰属（方法A）: 要素タップは ground truth のアンカー。問いとして
        # learner_selected 帰属付きで記録する（同期・非LLM）。
        _tap_anchor = _learner_selected_anchor(body)
        record_interest_trace(
            current_user["id"], course_id, topic_id,
            kind="question",
            text=body.message or f"{body.element_label or body.element_id}について質問",
            context_label=" · ".join(
                [s for s in [support_origin.chapter_title, topic_title] if s]
            ),
            extra_payload={
                "position_anchor": build_position_anchor(topic_id, _seg, _scroll),
                "structure_anchor": _tap_anchor,
            } if _tap_anchor else None,
        )
        return LearningChatResponse(
            **result.model_dump(),
            course_update=graph_response.course_update,
            structure_anchor=_tap_anchor,
        )

    # 学生 HELP ルート（設計 §1-3）: casual バイパス（次の _is_casual 判定）より手前に
    # 置く非LLM pre-route。typed action (usage_help) または保守的なキーワード判定
    # (_is_usage_question) でヒットしたら、ここで早期 return し、以降の意図分類・
    # 前提知識チェック・誤解検出・tension prefilter に構造的に到達させない
    # （ハンズフリー中の「これどう使うの」に効く唯一の位置）。分野の地図の ↗ アクション
    # （atlas_context）とは競合させないため、atlas_context が無いときのみ判定する。
    if not (isinstance(body.atlas_context, dict) and body.atlas_context):
        _is_usage_help = (
            _route_for_typed_action(body.support_action) == "USAGE_HELP"
            or _is_usage_question(body.message)
        )
        if _is_usage_help:
            with usage_context("learning:help_usage", user_id=current_user["id"], course_id=course_id):
                return _usage_help_response(
                    current_user["id"], course_id, topic_id, body,
                    on_llm_call=_consume_quota,
                )

    # カジュアル対話モード（気軽に話せる先生・ハンズフリー音声会話）:
    # 意図分類（雑談拒否）・前提知識ゲート・誤解検出をバイパスし、RAG検索と
    # tier 集約（根拠の一線）はそのまま通す。
    _is_casual = (body.intent_mode or "").strip() == "casual"
    # discuss モード（「論文と話す」, 設計 §6.2 Phase 1）: casual と同型の3点バイパス
    # （意図分類・前提知識ゲート・detour化）を共有するが、応答スタイルは会話調ではなく
    # 学術ディスカッション調（_get_discuss_system_prompt）にする。
    _is_discuss = (body.intent_mode or "").strip() == "discuss"
    # 理解サイクル Phase 2（設計 §8）: AI 4モードのうち Elicit/Diff は既存 discuss の
    # 1コール地点に相乗りする。値検証（elicit/diff 以外は 422）は本処理より前で完了済み。
    # フロントは cycle_mode を intent_mode=discuss と併せて送るため _is_discuss は
    # 通常 true だが、_chat_feature の分岐は cycle_mode を独立に優先させる。
    _cycle_mode = (body.cycle_mode or "").strip()
    _cycle_chat_feature = {
        "elicit": "learning:cycle_elicit",
        "diff": "learning:cycle_diff",
    }.get(_cycle_mode)
    # 楽屋モード（構造の降下路 docs/features/structure_descent_design.md §4）:
    # 楽屋からの質問は既存 learning_chat に相乗りし、**記録面だけを私有化**する
    # （痕跡 kind='backstage_question'・教員集約/tension mining の対象外）。
    # v1 では system prompt を追加しない — 楽屋は記録の私有化であって
    # 応答様式の変更ではない（通常 RAG 回答のまま）。
    # ※ _is_backstage 自体はハンドラ冒頭で判定済み（typed action / atlas の
    #   early-return 経路より前 — 2026-08-15 レビュー是正）。

    # 分野の地図 (Issue C-2/C-3): ↗ アクションは型付きなので意図分類を経由しない。
    # mind / learn は決定論的に応答し、evid ほかは通常の RAG フローへ流す。
    _atlas_ctx = body.atlas_context if isinstance(body.atlas_context, dict) else None
    if _atlas_ctx:
        _atlas_response = _atlas_action_response(
            current_user["id"], course_id, topic_id, body, course_data, _atlas_ctx
        )
        if _atlas_response is not None:
            return _atlas_response

    # 2. 意図分類（Intent Routing）— UI ボタン由来の型付きアクションは分類を経由しない。
    #    discuss は casual と同様に意図分類（雑談拒否）をバイパスする（設計 §6.2）。
    with usage_context("learning:chat", user_id=current_user["id"], course_id=course_id):
        intent = None if (_is_casual or _is_discuss or _atlas_ctx) else (
            _route_for_typed_action(body.support_action)
            or _classify_intent(body.message, course_title, on_llm_call=_consume_quota)
        )

    # ルート①: 雑談・無関係な質問 → 学習に関する質問を促す
    if intent == "CHIT_CHAT":
        chit_chat_answer = (
            "申し訳ありませんが、私は物理学の学習支援に特化したAIです。\n\n"
            "物理学・数学の概念についての質問や、学習の進め方についての相談でしたら、"
            "喜んでお答えします。学習に関する質問をぜひ聞かせてください！\n\n"
            "画面の使い方についての質問にもお答えできます。"
        )
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, chit_chat_answer,
        )
        return LearningChatResponse(answer=chit_chat_answer, course_update=None)

    # ルート①-b（設計 §4-4, Phase 2）: 意図分類 LLM が USAGE_HELP と判定した場合も
    # Phase 1 の HELP ハンドラへ委譲する。pre-route（_is_usage_question / typed action
    # usage_help）の保守的キーワード判定をすり抜けたケースの受け皿。ハンドラ自体の挙動
    # （テキスト経路は quota 非消費・本文素通し、音声/casual 経路は 1 LLM コール）は
    # Phase 1 と同一で、二重に interest_trace を記録することもない
    # （pre-route はここに到達する前に早期 return しているため一度しか通らない）。
    if intent == "USAGE_HELP":
        with usage_context("learning:help_usage", user_id=current_user["id"], course_id=course_id):
            return _usage_help_response(
                current_user["id"], course_id, topic_id, body,
                on_llm_call=_consume_quota,
            )

    # ルート②: 学習相談・メタ質問 → RAGをスキップし、コース情報をベースにアドバイス
    if intent == "LEARNING_ADVICE":
        with usage_context("learning:chat", user_id=current_user["id"], course_id=course_id):
            advice_answer = _generate_learning_advice_response(
                course_title, topic_title, body.message,
                topic_info=topic_info, course_data=course_data,
                on_llm_call=_consume_quota,
            )
        advice_answer, inline_actions = extract_inline_actions(advice_answer)
        is_prereq = (
            body.support_action in _PREREQUISITE_ACTIONS
            or LearningSupportAgent.is_prerequisite_request(body.message)
        )
        if is_prereq:
            # 前提確認は detour（origin=現在アンカー）。復帰導線を必ず付ける。
            result = support_agent.with_learning_actions(
                answer=advice_answer,
                mode="prerequisite_review",
                origin=support_origin,
                extra_actions=inline_actions,
            )
            persist_chat_history(
                current_user["id"], course_id, topic_id,
                body.history, body.message, result.answer,
            )
            return LearningChatResponse(**result.model_dump(), course_update=None)
        # 学習開始・一般アドバイスはパス上（detour ではない）。前進アクションを型付きで提示。
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, advice_answer,
        )
        first_concept = ""
        for _c in (course_data.get("concepts") or []):
            _name = _c.get("name", _c) if isinstance(_c, dict) else str(_c)
            if _name:
                first_concept = str(_name)
                break
        advice_next = support_agent.advice_actions(support_origin, topic_title, first_concept)
        return LearningChatResponse(
            answer=advice_answer,
            course_update=None,
            origin=asdict(support_origin),
            next_actions=[asdict(a) for a in (advice_next + inline_actions)],
        )

    # 3. Adaptive Routing: 前提知識の自動判定 (ルート③/④の前に実行)
    # casual / discuss モードでは会話を止めない（前提確認の逆質問ゲートを挟まない）。
    prerequisite_intervention = None if (_is_casual or _is_discuss or _atlas_ctx) else check_prerequisites(
        current_user["id"], course_id, course_data, topic_title, body.message
    )
    if prerequisite_intervention:
        choice_actions = support_agent.prerequisite_choice_actions(
            prerequisite_intervention.get("first_prerequisite", "")
        )
        result = support_agent.with_learning_actions(
            answer=prerequisite_intervention["message"],
            mode="prerequisite_review",
            origin=support_origin,
            include_continue=False,
            extra_actions=choice_actions,
        )
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, result.answer,
        )
        return LearningChatResponse(**result.model_dump(), course_update=None)

    # 4. RAG: システム全域のチャンクを検索し、コンテキストを構築
    #    search_chunks_with_metadata は各チャンクに tier(L1信頼性) を付与して返す。
    # このコース自身の教材（material_id）の集合。出典が「教材」か「別の資料」かの分類に使う。
    course_material_ids = set(course_source_material_ids(course_data))
    # Phase 0（discuss モード設計書 §6.1）: 全域検索は本人が閲覧可能な document に fail-closed で絞る。
    # discuss モード（設計 §6.2 Phase 1）: スコープ2段切替。既定/明示 "course_sources" は
    # このコースのソース論文のみ、"all_visible" は Phase 0 の可視集合まで。該当チャンクが
    # 無くても他スコープへ無断で広げない（DM1）ため、discuss_scope が空集合でもそのまま渡す。
    _discuss_scope = (body.discuss_scope or "course_sources").strip() if _is_discuss else None
    if _is_discuss and _discuss_scope not in ("course_sources", "all_visible"):
        raise HTTPException(
            status_code=422,
            detail=f"discuss_scope には course_sources か all_visible を指定してください（受信値: {_discuss_scope!r}）。",
        )
    # コーパス回遊 Phase B（設計 §5.2）: document 直付けの既定スコープは**当該 document のみ**。
    # 呼び出し側（document_discuss_chat）が解決済みの集合を渡す。"all_visible" を明示された
    # ときだけ本人可視集合まで広げる（コース経路の意味論と対応）。
    if scope_document_ids is not None and not (_is_discuss and _discuss_scope == "all_visible"):
        allowed_document_ids = scope_document_ids
    elif _is_discuss and _discuss_scope == "all_visible":
        allowed_document_ids = list_visible_document_ids(current_user["id"])
    elif _is_discuss:
        allowed_document_ids = list_course_source_document_ids(course_data)
    else:
        allowed_document_ids = list_visible_document_ids(current_user["id"])
    chunk_results = search_chunks_with_metadata(
        body.message, top_k=8, allowed_document_ids=allowed_document_ids,
    )
    cited_chunks = []
    cited_sources: list[dict] = []  # L1: 文脈に採用した根拠の tier 一覧
    has_topic_material = False
    if topic_info:
        topic_material = _topic_student_material(topic_info)
        if topic_material:
            has_topic_material = True
            cited_chunks.append(f"[現在表示中の教材]\n{topic_material[:5000]}")
    for r in chunk_results:
        if r["score"] >= 0.30:
            _n = len(cited_sources) + 1  # 連番出典 [出典N]（cited_sources と 1 対 1）
            cited_chunks.append(f"[出典{_n}] 『{r['source_title']}』\n{r['text']}")
            _quote = (r.get("text") or "").strip().replace("\n", " ")
            _origin = "course_material" if r.get("material_id") in course_material_ids else "other_material"
            cited_sources.append({
                "index": _n,
                "chunk_id": r.get("id", ""),
                "source_title": r.get("source_title", "不明な教材"),
                "tier": r.get("tier", TIER_OUT_OF_SOURCE),
                "score": round(float(r.get("score", 0.0)), 3),
                "quote": (_quote[:80] + "…") if len(_quote) > 80 else _quote,
                "meta": r.get("source_file") or "",
                "origin": _origin,
            })

    # L1: 回答全体の格を最弱根拠へ安全側集約。採用根拠が無ければ未踏(out_of_source)。
    overall_tier = aggregate_overall_tier([s["tier"] for s in cited_sources])
    # トピック教材をコンテキストに注入済みなら回答には実根拠があり、out_of_source
    # （「教材の裏づけなし」バナー + 未踏ガード）は事実と矛盾する。承認チェーン由来
    # ではないため approved には昇格させず、source を下限に引き上げる。
    if has_topic_material:
        overall_tier = tier_floor(overall_tier, TIER_SOURCE)
    # 回答内容の出所分類（tier=教員承認状況とは別軸）:
    #   教材(このコース) > 別の資料 > 出典を追えないモデル生成、の優先度で決める。
    if has_topic_material or any(s["origin"] == "course_material" for s in cited_sources):
        content_grounding = "course_material"
    elif cited_sources:
        content_grounding = "other_material"
    else:
        content_grounding = "model_generated"

    if cited_chunks:
        context_block = "## 関連する教材のコンテキスト\n" + "\n---\n".join(cited_chunks)
    elif _is_discuss:
        # DM1（出所の正直さ）: discuss は該当チャンクが無くても他スコープへ無断で
        # 広げない。範囲を広げていない事実と、範囲外知識を使う場合の出所明示を指示する。
        context_block = (
            "※選択中の検索範囲には、この質問に直接関連する箇所は見当たりませんでした。"
            "範囲は広げていません。一般的な学術知識で回答する場合は、この論文由来ではないことを明示してください。"
        )
        log_unanswered_query(current_user["id"], course_id, topic_id, body.message)
    else:
        context_block = "※この質問に直接関連する教材セクションは見つかりませんでした。一般的な学術知識を用いて回答してください。"
        log_unanswered_query(current_user["id"], course_id, topic_id, body.message)

    # 5. 回答の生成（ルート統合）
    # L1 OutOfSourceGuard: 未踏なら生成前に順序ゲート（断定回避・予想促し）を system へ注入する。
    # casual / discuss モードでも guard の注入（振る舞い）は維持する — 気軽さ・自由さ≠根拠の放棄。
    # discuss は casual と判定が競合しないが（intent_mode は単一値）、設計上 discuss を先に判定する。
    if _cycle_mode == "elicit":
        _system_prompt = _get_cycle_elicit_system_prompt(domain, response_persona)
    elif _cycle_mode == "diff":
        _system_prompt = _get_cycle_diff_system_prompt(domain, response_persona)
    elif _is_discuss:
        _system_prompt = _get_discuss_system_prompt(domain, response_persona)
    elif _is_casual:
        _system_prompt = _get_casual_teacher_system_prompt(domain, response_persona)
    else:
        _system_prompt = _get_integrated_tutor_system_prompt(domain, response_persona)
    # 確認問題の壁打ちモード: どのモードの system プロンプトに対しても、解答の直接提示を
    # 禁じ・要素の説明は許し・組み立ては学習者に委ねる拘束を末尾へ追記する。
    if body.check_scaffold:
        _system_prompt += "\n\n" + _CHECK_SCAFFOLD_INSTRUCTION
    if overall_tier == TIER_OUT_OF_SOURCE:
        _system_prompt += "\n\n" + out_of_source_guard_instruction()
    # アンカー優先ラダー（設計 §7、IH6）: typed action（EXPLAIN_GRAPH_ELEMENT）・
    # usage_help pre-route はこの行より前段で早期 return 済みのため、ここに到達する
    # のは通常の学習チャット（casual/discuss 含む）のみ。追加の LLM コールは作らず、
    # 既存の1コールへヒントを同梱するだけ（純関数・DB/LLM 非使用）。
    _anchor_ladder_hint = _build_anchor_ladder_hint(body, body.history)
    if _anchor_ladder_hint:
        _system_prompt += "\n\n" + _anchor_ladder_hint
    # レビュー確定の修正3（DA1/DA2）: 足場メッセージ（context 注入の user ターンと
    # それを受ける assistant ターン）は全モード共通で「以下の質問に答えてください」/
    # 「お答えします」という Q&A フレームを強制していた。system プロンプトが
    # 「解釈には解説で応じない」（revoice ファースト）と指示した直後に、発話直近の
    # 文脈がこのフレームを再導入するため、学習者が立場を述べても完全解説が返る
    # （設計書 §0 の症状の再生産）。discuss のときだけ足場を中立化し、発話タイプ別の
    # 応答ルールへ橋渡しする。casual・通常モードの足場は変更しない。
    # 確認問題の壁打ちモードも同型の問題を抱える（system で「解答そのものを出さない」と
    # 指示した直後に、足場の「以下の質問に答えてください」が直答を再誘導する）ため、
    # discuss より優先して足場を中立化する。
    if body.check_scaffold:
        _scaffold_user_instruction = (
            "上記のコンテキストを踏まえ（不足している場合は補完して）、"
            "壁打ちモードの規則に従って学習者の発話に応じてください。"
        )
        _scaffold_assistant_ack = (
            "はい。答えの組み立ては学習者に委ね、構成要素の説明と問いかけで支援します。"
        )
    elif _is_discuss:
        _scaffold_user_instruction = (
            "上記のコンテキストを踏まえ（不足している場合は補完して）、"
            "発話タイプ別の応答ルールに従って、以下の学生の発話に応じてください。"
        )
        _scaffold_assistant_ack = (
            "はい。学生の発話のタイプ（質問 / 解釈・立場の表明 / 詰まり）を見きわめて応じます。"
        )
    else:
        _scaffold_user_instruction = (
            "上記のコンテキストを踏まえ（不足している場合は補完して）、以下の質問に答えてください。"
        )
        _scaffold_assistant_ack = f"はい、「{topic_title}」についてですね。お答えします。"
    messages: list[dict] = [
        {"role": "system", "content": _system_prompt},
        {"role": "user", "content": (
            f"コース: {course_title}\n"
            f"現在のトピック: {topic_title}\n\n"
            f"{context_block}\n\n"
            f"{_scaffold_user_instruction}"
        )},
        {"role": "assistant", "content": _scaffold_assistant_ack},
    ]
    # チャット型AI支援の共通基盤整理 §2-2: 直近20メッセージ・2000字/件へウィンドウ化
    # （教材・RAGコンテキストは上の messages で毎回別途注入されるため先頭保護は不要, head_keep=0）。
    for turn in window_history(body.history, max_messages=20, max_chars=2000):
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": body.message})

    # discuss モード（設計 §6.2 Phase 1）: U層タグを "learning:chat_discuss" に分離し、
    # casual / 通常チャットと独立にコストを実測する（専用上限は Phase 3 で実測後に判断）。
    if _cycle_chat_feature:
        _chat_feature = _cycle_chat_feature
    elif _is_discuss:
        _chat_feature = "learning:chat_discuss"
    elif _is_casual:
        _chat_feature = "learning:chat_casual"
    else:
        _chat_feature = "learning:chat"
    # この時点でリクエスト全体を通じて最初の（あるいは唯一の）LLM 呼び出しなら消費する
    # （intent 分類等ですでに消費済みなら no-op、§1）。
    _consume_quota()
    degraded = False
    # M層 Phase 3（§6.4）: コース単位の学習チャットモデル上書き。運用パラメータのため
    # 版ピン中の学習者にも所有者の live（HEAD）設定を適用する — course_data は非所有者に
    # 版スナップショットを返しうるため、専用の live-only SELECT を別途使う
    # （get_course_live_llm_models）。未設定なら resolve_model() 内の既存解決順序
    # （user policy → system policy → env → tier既定）がそのまま効く（挙動不変）。
    # コーパス回遊 Phase B: センチネル course_id は実在コース行を持たないため
    # learning_courses への無駄な SELECT を出さない（結果は常に未設定 = 既存の解決順序）。
    _course_chat_model = (
        None
        if _document_context_id
        else get_course_live_llm_models(course_id).get(llm_policy.SCENE_LEARNING_CHAT)
    )
    _course_chat_override = (
        llm_policy.model_override(_course_chat_model, source=llm_policy.SOURCE_COURSE_OVERRIDE)
        if _course_chat_model else nullcontext()
    )
    try:
        with usage_context(_chat_feature, user_id=current_user["id"], course_id=course_id), _course_chat_override:
            answer = generate_text(
                messages=messages,
                temperature=0.3,
                model=resolve_model("learning_chat_llm_model", fallback="analysis"),
            )
    except Exception:
        # 会話は死なせない（設計書 I3）: 500 即死をやめ、degraded 固定文 + 200 へ縮退する。
        # 履歴は保存し、回答本文に依存する後処理（誤解検出・ドリルダウン抽出）はスキップする（I4）。
        logger.exception("Learning chat LLM call failed for topic %s", topic_id)
        answer = "AI 応答を生成できませんでした。しばらくしてからもう一度お試しください。"
        degraded = True

    # 出典マーカーの突き合わせ: 根拠の無い [出典N]（捏造・番号超過）を本文から取り除き、
    # 根拠のある番号は表記ゆれを半角 [出典N] へ正規化する。ここで整えることで、
    # レスポンス・履歴焼き込み・関心痕跡のすべてに同じ本文が流れる。
    if not degraded:
        answer = _reconcile_citation_markers(answer, {s["index"] for s in cited_sources})

    # L1 OutOfSourceGuard: 未踏なら断定せず、根拠が弱い旨を先頭に明示する。
    # casual では可視プレフィックスのみ省略（音声で毎回読み上げると会話が壊れるため）。
    # tier 自体はレスポンスで返し、UI のバッジ表示で担保する。degraded な固定文には
    # 付与しない（回答本文に依存する装飾のため、設計書 §4）。
    # discuss では意図的にこの明示を維持する（DM1: 出所の正直さを弱めない）。
    if overall_tier == TIER_OUT_OF_SOURCE and not _is_casual:
        if not degraded:
            answer = out_of_source_notice() + "\n\n" + answer

    # 誤解検出（マイルドな表現にも対応）。casual では採点・訂正の圧を掛けない。
    # degraded ターンは回答本文が根拠を伴わない固定文のため、本文依存の後処理はスキップする
    # （設計書 §4・I3/I4: 会話は死なせない・履歴保存はそのまま行う）。
    course_update = None
    if not degraded:
        if not _is_casual and topic_info and any(
            kw in answer for kw in ["訂正", "より正確です", "誤解"]
        ):
            course_update = detect_and_record_misconception(
                current_user["id"], course_id, course_data, topic_id, body.message, answer
            )

    _persisted = persist_chat_history(
        current_user["id"], course_id, topic_id,
        body.history, body.message, answer,
        user_message_id=body.message_id or None,
        # 履歴復元後も本文中の [出典N] を出典チップに戻せるようにする（sources を焼き込まないと
        # リロード・トピック切替・discuss モード遷移のあとプレーンテキストに退化する）。
        # 保存するのはチップ描画とポップアップ起動に要る最小フィールドのみ（quote / meta /
        # origin は「いまの回答」を扱う出典タブ専用で、復元メッセージからは参照されない）。
        assistant_meta={
            "sources": [
                {
                    "index": s["index"],
                    "chunk_id": s["chunk_id"],
                    "source_title": s["source_title"],
                    "tier": s["tier"],
                    "score": s["score"],
                }
                for s in cited_sources
            ],
            "overall_tier": overall_tier,
            "content_grounding": content_grounding,
        },
    )
    # L2: クライアント報告の実位置で position_anchor を構築（mock ではない）。
    position_anchor = build_position_anchor(topic_id, _seg, _scroll)
    # L3 資産化: この往復を関心痕跡として安価に記録（LLM不使用）。
    # kind は既存シグナルから決定: 楽屋→backstage_question（記録は本人のみ・集計対象外）/
    # 誤解検出→misconception / それ以外→question。
    _trace_kind = (
        "backstage_question" if _is_backstage
        else ("misconception" if course_update else "question")
    )
    _ctx_label = " · ".join([s for s in [support_origin.chapter_title, topic_title] if s])
    # Stage 0 TensionPrefilter（同期・非LLM・数ms）: ヘッジ/逆接マーカーと直近3往復の
    # 同語再訪でヒントを立てるだけ。LLM 分類は非同期バッチ（P6: 応答を遅延させない）。
    _recent_user_texts = [t.get("content", "") for t in body.history if t.get("role") == "user"][-3:]
    # 楽屋ガード（構造の降下路 §6 精査記録②）: tension worker（_fetch_pending_hints）は
    # payload_flag 方式（kind 条件なし）のため kind では自動除外されない。送信側で
    # tension_hint を立てない・tension mining をスケジュールしないことで、楽屋の質問を
    # 解析対象から構造的に外す（SD4）。
    _tension_hint = False if _is_backstage else judge_tension_hint(body.message, _recent_user_texts)
    # 構造帰属（方法A・同期・非LLM）: テキスト選択・要素タップの明示アンカーがあれば
    # learner_selected で確定記録する。無ければ方法B（非同期LLM）の帰属対象になる。
    _sel_anchor = _learner_selected_anchor(body)
    _trace_payload = {
        "overall_tier": overall_tier,
        "position_anchor": position_anchor,
        "tension_hint": _tension_hint,
        "casual": _is_casual,
        # 「この問いに戻る」で元の往復へジャンプするための逆引き（この問いを発した user メッセージ id）。
        "message_id": _persisted.get("user_message_id"),
        # 方法Bの帰属コンテキスト用: この回答が実際に引用したチャンク（上位3件）。
        "cited_chunk_ids": [s["chunk_id"] for s in cited_sources[:3] if s.get("chunk_id")],
        # 分野の地図由来の質問 (根拠を見る ↗ など) は帰属を構造化して焼き込む (Issue C-2)
        **({"atlas": _atlas_attribution(_atlas_ctx)} if _atlas_ctx else {}),
        # discuss モード（設計 §6.2 Phase 1）: 後から U層・k-匿名集計・personal_graph が
        # discuss 由来の痕跡を区別できるように焼き込む。楽屋の質問には焼き込まない
        # （discuss 観測基盤 core/discuss/observation.py は kind フィルタなしで
        # payload->>'entry_mode'='discuss' を数えるため、焼き込むと SD4「楽屋は集計に
        # 入らない」に反して混入する — 2026-08-15 レビュー是正）。
        **({"entry_mode": "discuss"} if _is_discuss and not _is_backstage else {}),
        # discuss 観測基盤（docs/features/discuss_observation_design.md §2-1）: 全モード共通で
        # 回答の出所分類を焼き込む（記録開始日以前の痕跡には無いキーなので、集計側は
        # 「記録済み件数」を分母として明示する — U1 と同じ誠実さ）。
        "content_grounding": content_grounding,
        # discuss のときのみスコープも焼き込む（discuss 以外は None のまま）。
        **({"discuss_scope": _discuss_scope} if _is_discuss else {}),
        # 楽屋（構造の降下路 §4）: 本人の台帳表示・後方検証のために焼き込む
        # （kind='backstage_question' と対。楽屋以外にはキー自体を足さない）。
        **({"backstage": True} if _is_backstage else {}),
    }
    # gap1: 地図アクション由来でない通常学習でも、topic → 骨格概念を解決して atlas 帰属を
    # 焼き込む (個人層の「いまここ」を動かす)。地図由来 (_atlas_ctx) は上書きしない。
    if not _atlas_ctx:
        _topic_atlas = _atlas_topic_attribution(course_data, topic_info)
        if _topic_atlas:
            _trace_payload["atlas"] = _topic_atlas
    if _sel_anchor:
        _trace_payload["structure_anchor"] = _sel_anchor
    _trace_id = record_interest_trace(
        current_user["id"], course_id, topic_id,
        kind=_trace_kind,
        text=body.message,
        context_label=_ctx_label,
        extra_payload=_trace_payload,
    )
    # ヒント累積が閾値に達していればバックグラウンドで TensionMiningAgent を起動
    # （best-effort: 失敗してもチャット応答を止めない）。楽屋の質問は対象外
    # （_tension_hint は backstage で常に False だが、ガードを明示して二重に守る）。
    if _tension_hint and not _is_backstage:
        maybe_schedule_tension_mining(current_user["id"], course_id, topic_id)
    # 未帰属の問いが累積していればバックグラウンドで StructureAnchorAgent を起動
    # （方法B・非同期。明示アンカー付きの問いは最初から対象外。楽屋の質問は
    # _trace_kind='backstage_question' のためこの条件で自動的に対象外になる）。
    if _trace_kind == "question" and not _sel_anchor:
        maybe_schedule_anchor_mining(current_user["id"], course_id, topic_id)
    # 方法C: 回答末尾の帰属確認プロンプト。tension_hint が立った往復か、明示アンカーは
    # あるが疑いの様相が未分類の往復に限り、セッション内上限までゲートして提示する（P7）。
    # 楽屋では出さない — 「集計に入りません」と宣言した枠で帰属確定 UI を出さない
    # （SD4、2026-08-15 レビュー是正。_tension_hint は backstage で常に False だが、
    # 明示アンカー経由でも出ないよう明示ガード）。
    _anchor_confirm = None
    if (
        _trace_id
        and not _is_casual
        and not _is_backstage
        and (_tension_hint or _sel_anchor is not None)
        and check_and_count_confirm_prompt(current_user["id"], course_id, topic_id)
    ):
        _anchor_confirm = {
            "trace_id": _trace_id,
            "question": (body.message or "")[:120],
            "options": _ANCHOR_CONFIRM_DOUBT_OPTIONS,
        }
    # 本文中のドリルダウンマーカーは構造化アクションへ正規化する。degraded ターンは
    # 根拠を伴わない固定文のため本文依存の後処理をスキップする（設計書 §4）。
    if degraded:
        clean_answer, inline_actions = answer, []
    else:
        clean_answer, inline_actions = extract_inline_actions(answer)
    # 鏡面化 move（seminar_brief_mirroring_design.md §2/§3 精査①③、EX-3b）: discuss の
    # ときのみ、本文中の 〔鏡〕…〔/鏡〕 マーカーをサーバ側で決定論抽出して構造化フィールド
    # （LearningChatResponse.mirror）へ正規化する（extract_inline_actions と同じ規律 —
    # フロントに regex を書かせない）。verbatim 検査（鏡文中の「」引用が学習者の直前発話の
    # 逐語部分文字列であること）に不合格ならマーカーだけ剥がして本文へ縮退（再生成なし・P6）。
    # 鏡文は痕跡（record_interest_trace）・専用テーブル・assistant_meta には書かない
    # （窓の外への持ち出しの禁止）。会話履歴 JSONB（persist_chat_history は上で実行済み）に
    # 生 answer がマーカー込みで残るのは既存挙動のままで、これは window_history の窓内
    # 再注入として設計が許容する範囲（§3 精査③）。
    _mirror = None
    if _is_discuss and not degraded:
        clean_answer, _mirror = extract_mirror(clean_answer, body.message)

    # 送信意図で分岐（教材/チャット2区画 UX）:
    #  - on_path : 本筋維持。detour にせず origin/status_label を返さない（フロントは寄り道化しない）
    #  - casual  : 気軽に話せる先生。detour 化も復帰導線も付けない（会話を UI 遷移で邪魔しない）
    #  - discuss : 論文と話す（設計 §6.2）。「寄り道」化しない — origin=None により既存フロントの
    #              寄り道バナーは自動的に出ない（対等併記, DM5）
    #  - explore : 従来どおり寄り道（detail_explanation, 復帰導線つき）
    if (body.intent_mode or "").strip() in ("on_path", "casual", "discuss"):
        result = LearningSupportResult(
            answer=clean_answer,
            mode="normal",
            origin=None,
            next_actions=inline_actions,
        )
    else:
        result = support_agent.with_learning_actions(
            answer=clean_answer,
            mode="detail_explanation",
            origin=support_origin,
            include_continue=False,
            extra_actions=inline_actions,
        )
    # L1 tier・L2 位置ともに実データ化済み（Stage 1/2）。チャット応答に mock は含まれない。
    return LearningChatResponse(
        **result.model_dump(),
        course_update=course_update,
        sources=cited_sources,
        overall_tier=overall_tier,
        content_grounding=content_grounding,
        position_anchor=position_anchor,
        structure_anchor=_sel_anchor,
        anchor_confirm=_anchor_confirm,
        mirror=_mirror,
        mock=False,
        degraded=degraded,
    )


@router.get("/courses/{course_id}/discuss/opening")
def get_discussion_opening(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """discuss モード（「論文と話す」）の開幕画面（設計書 §3.3・非LLM・読み取り専用）。

    白紙のチャット欄で始めないための3要素のうち、非LLM・A層成果の読み出しだけで
    組み立てられる分を返す（最初の一手の固定チップはフロント側で描く）:
    中心命題・支持構造（thesis_reconstruction artifact）／理論のバックボーン
    （TheoryOperationGraph の main 層・theory stage 順）／「最も脆い一手」
    （D層台帳の未検証合意リスト + review_required なバックボーンノードの事実提示）。

    加えて投影の是正（`discuss_opening_authoring_design.md` §3 Phase 0）で、agent が
    既に合成していた「この論文が答えようとした問い」（central_question / paper_goal）・
    中心命題の合成文（central_thesis.text）・支持構造の合成文・「別の見方」
    （alternative_theses、出所ラベル付き）も投影する。脆い箇所は主語ごとに
    （論文 / システム）分離できる形（fragile_points[].subject）で返す。
    Phase 0b の `course_focus`（教員の任意入力「このコースで議論したいこと」）も同梱する。

    「議論のきっかけ」（同 §7 Phase 3、`documents[].discussion_seeds`）だけは投影ではなく、
    解析パイプラインが生成し**教員が承認した**素材（`element_explanations` の
    `status='approved'` / `role='discussion_seed'` 行）の配信で、各件に出所表示
    （`authored` / `authored_by_label`）が付く。承認済みが1件も無い document は投影のまま
    （Phase 0 と同一の DTO）で、`available` の判定もこの素材の有無では変わらない。

    LLM 呼び出し 0 回・痕跡記録なし・migration なし（DM8）。confidence / load_score
    等の生数値は一切含めない（``core/discuss/opening.py::build_opening`` が
    ホワイトリスト射影 + 再帰除去の二重で保証する）。
    """
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    document_ids = list_course_source_document_ids(course_data)
    # Phase 0b: 教員の任意入力（AI 生成なし）。course_data への素の dict アクセスは
    # しない（Tier 3-18: 正本は core/course_data.py のアクセサ）。
    result = build_discussion_opening(
        course_id, document_ids, course_focus=course_focus(course_data)
    )
    # 理解サイクル（UCサイクル §5.1/§5.2）: OPEN の一枠（初回動機 / 持ち越し問いの
    # 再提示）を optional キーとして同梱する。DB 取得失敗は fail-open — intention
    # キーを付けずに返し、opening 本体は壊さない（既存キー・ゲート・シグネチャは不変）。
    try:
        carryover = fetch_active_carryover(current_user["id"], course_id)
        intentions = fetch_intentions(current_user["id"], course_id)
        result["intention"] = build_intention_dto(carryover, bool(intentions))
    except Exception:
        logger.warning("Failed to build cycle intention for discuss opening", exc_info=True)
    return result


class DiscussReflectionRequest(BaseModel):
    text: str = ""


@router.post("/courses/{course_id}/discuss/reflection", status_code=201)
def record_discuss_reflection(
    course_id: str,
    body: DiscussReflectionRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """着地画面の「今日の理解を自分の言葉で」を本人の tension 痕跡として残す。

    着地画面に並ぶ候補（tension / anchor）は、いずれも学習者が既に書いた発話から
    非同期 LLM が起こしたものであり、質問しかしていない対話からは「残す価値のある
    理解」が生まれない。この API は候補の生成を待たず、**本人が書いた一文をそのまま**
    確定済み（``status='articulated'``）の tension として記録する導線を与える。

    LLM 呼び出し 0 回・migration 不要（DM8）。確定するのは常に本人（P1）で、
    AI が代わりに理解を要約して置くことはしない。空文字は 422。
    """
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")
    result = record_learner_articulated_tension(
        current_user["id"], course_id, DISCUSSION_TOPIC_ID, text,
        context_label=DISCUSSION_TOPIC_LABEL,
        origin="discuss_landing",
    )
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to record reflection")
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# コーパス回遊 Phase B — コース無し論文議論（document 直付け discuss）
# 正本設計書: docs/features/corpus_roaming_design.md §5（CR1/CR2/CR8/CR9）
#
# 会話は既存の learning_chat_history / interest_traces に、予約センチネル
# course_id="_doc:{document_id}" + topic_id="_discussion" で載せる（migration 0）。
# アクセスゲートは受講ゲートではなく **document 可視性のみ**（CR1・fail-closed）。
# ---------------------------------------------------------------------------


def _resolve_discuss_document(user_id: str, document_ref: str) -> tuple[str, str, str]:
    """document_ref（documents.id UUID / source_path=material_id）を解決し、
    閲覧可否を fail-closed で判定して ``(document_id, source_path, title)`` を返す。

    CR1: ゲートは ``user_can_view_document`` と同一判定（``resolve_document_access``
    の ``can_view``）。**不可・不在はいずれも 404 に統一**する（存在推測をさせない
    既存流儀 — 403 と 404 を撃ち分けない）。
    """
    access = resolve_document_access(user_id, document_ref)
    if not access.found or not access.can_view:
        raise HTTPException(status_code=404, detail="Document not found")
    document_id = str(access.document_id)
    title = ""
    try:
        from core.personal_graph.queries import fetch_document_titles

        title = (fetch_document_titles([document_id]) or {}).get(document_id, "") or ""
    except Exception:  # noqa: BLE001 — タイトルは表示用。取得失敗で議論を止めない。
        logger.warning("document discuss: title lookup failed for %s", document_id, exc_info=True)
    return document_id, access.source_path or "", title or access.source_path or document_id


def _document_discuss_course_data(document_id: str, source_path: str, title: str) -> dict:
    """document 直付け議論のための合成 course_data（DB には保存しない読み時の器）。

    ``_learning_chat_core`` がコースから読む項目（title / domain / sources / topics）だけを
    最小限で満たす。``sources`` に当該 document を入れることで、出所分類
    （``content_grounding``）がこの論文由来のチャンクを ``course_material``
    （＝いま議論している論文）として扱う。
    """
    source: dict = {"document_id": document_id, "title": title}
    if source_path:
        source["material_id"] = source_path
    return {
        "title": title,
        "sources": [source],
        "topics": [],
        "concepts": [],
    }


def _record_document_discuss_event(event: str, user_id: str, context_id: str) -> None:
    """discuss 観測イベント（設計 §5.5）を best-effort で1件記録する。

    DO6（計測失敗で UX を止めない）: 例外は握り潰す。payload は常に空
    （DO1: 本文非含有）。学習者にはこの数値を一切返さない（DO3）。
    """
    try:
        discuss_observation.insert_metric_events(
            user_id, [{"event": event, "course_id": context_id, "payload": {}}]
        )
    except Exception:  # noqa: BLE001
        logger.warning("document discuss: metric event %s failed", event, exc_info=True)


@router.get("/documents/{document_ref}/discuss/opening")
def get_document_discussion_opening(
    document_ref: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """コース無し論文議論の開幕画面（設計 §5.3・非LLM・読み取り専用）。

    コース版（``GET /courses/{course_id}/discuss/opening``）と**同じ**
    ``core.discuss.opening.build_opening`` を、センチネル course_id と単一 document で
    呼ぶだけ。``documents[].discussion_seeds``（教員承認済みの議論のきっかけ）は
    document 単位の素材なのでそのまま出る。LLM 呼び出し 0 回（CR9）。

    既知の縮退（設計 §5.4）: ``fragile_points``（D層台帳の未検証合意リスト）は
    ``epistemic_ledger.course_id`` 基準の投影のため、コース外のセッションでは空になる。
    UCサイクルの ``intention``（course 配下の持ち越し）も同梱しない。
    """
    document_id, _source_path, title = _resolve_discuss_document(current_user["id"], document_ref)
    context_id = document_context_id(document_id)
    result = build_discussion_opening(context_id, [document_id], course_focus="")
    # フロントがこの後のチャット・履歴 API に使う会話キーと、画面に出す論文名。
    result["document_context"] = {
        "document_id": document_id,
        "title": title,
        "context_id": context_id,
        "topic_id": DISCUSSION_TOPIC_ID,
        "label": DOCUMENT_DISCUSSION_TOPIC_LABEL,
    }
    _record_document_discuss_event("document_discuss_opened", current_user["id"], context_id)
    return result


@router.post("/documents/{document_ref}/discuss/chat", response_model=LearningChatResponse)
def document_discuss_chat(
    document_ref: str,
    body: LearningChatRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningChatResponse:
    """コース無し論文議論のチャット（設計 §5.2/§5.3）。

    既存 ``learning_chat`` の discuss 経路の**ファサード**で、本体は同じ
    ``_learning_chat_core`` を通る（応答様式 DA1〜DA6・書き直し/削除の truncate・
    tension プレフィルタ・痕跡記録・観測タグ ``learning:chat_discuss`` は共通コア由来）。

    - ゲートは document 可視性のみ（CR1）。受講ゲートは一切通らない。
    - 会話キーは ``course_id=_doc:{document_id}`` / ``topic_id=_discussion``（§5.1）。
    - RAG は既定で当該 document のみ。``discuss_scope="all_visible"`` のときだけ
      本人可視集合まで広げる（該当チャンクゼロでの無断フォールバックは無し = DM1）。
    - コストは既存 ``LEARNING_CHAT_MAX_CALLS_PER_DAY`` に相乗り（新設しない・CR9）。

    コース前提のペイロード（``action``＝グラフ要素説明 / ``atlas_context``＝分野の地図の
    ↗ アクション / ``cycle_mode``＝理解サイクルの AI モード）は v1 では提供しないので
    サーバ側で落とす（§5.4 の縮退を黙って壊さず、明示的に無効化する）。
    """
    document_id, source_path, title = _resolve_discuss_document(current_user["id"], document_ref)
    context_id = document_context_id(document_id)

    # 常に discuss として扱う（このエンドポイントに他の intent_mode は無い）。
    body.intent_mode = "discuss"
    body.action = None
    body.atlas_context = None
    body.cycle_mode = None

    response = _learning_chat_core(
        context_id,
        DISCUSSION_TOPIC_ID,
        body,
        current_user,
        course_data=_document_discuss_course_data(document_id, source_path, title),
        scope_document_ids={document_id},
    )
    _record_document_discuss_event("document_discuss_turn", current_user["id"], context_id)
    return response


@router.get(
    "/documents/{document_ref}/discuss/history",
    response_model=LearningChatHistoryResponse,
)
def get_document_discussion_history(
    document_ref: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningChatHistoryResponse:
    """コース無し論文議論の履歴（センチネルキー）。形は既存 ``get_chat_history`` と同一。"""
    document_id, _source_path, _title = _resolve_discuss_document(current_user["id"], document_ref)
    return get_chat_history(
        document_context_id(document_id), DISCUSSION_TOPIC_ID, current_user
    )


@router.delete("/documents/{document_ref}/discuss/messages/{message_id}")
def delete_document_discussion_message_from(
    document_ref: str,
    message_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """機能3（削除）の document 直付け版: 指定メッセージ以降の往復を取り除く。

    既存のコース経路と同じ ``truncate_chat_and_supersede`` の truncate セマンティクス
    （当該 user メッセージ・その回答・以降の往復を履歴から除き、派生 interest_traces は
    削除せず ``status='superseded'`` に遷移させる = CR8/P4）。行削除 API ではない。
    """
    document_id, _source_path, _title = _resolve_discuss_document(current_user["id"], document_ref)
    try:
        result = truncate_chat_and_supersede(
            current_user["id"], document_context_id(document_id), DISCUSSION_TOPIC_ID, message_id
        )
    except Exception:
        logger.exception(
            "Failed to delete document discuss message for user=%s doc=%s msg=%s",
            current_user["id"], document_id, message_id,
        )
        raise HTTPException(status_code=500, detail="Failed to delete chat message")

    if result is None:
        raise HTTPException(status_code=404, detail="Message not found")

    return {"status": "deleted", "removed_count": result["removed_count"]}


@router.get("/courses/{course_id}/source-chunk/{chunk_id}")
def get_source_chunk_route(
    course_id: str,
    chunk_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """出典ポップアップ用: チャンク本文（数式プレースホルダ正規化済み）と数式を返す（L1）。

    スコープは **URL の course の sources に限定**する（P0 オブジェクトスコープ是正）。

    1. `get_accessible_course_data` — 本人がその course にアクセスできること（不可なら 404）。
    2. `list_course_source_document_ids(course_data)` — その course の source document 集合。
    3. `get_chunk_passage(..., allowed_document_ids=...)` の SQL 内
       `document_id = ANY(...)` でスコープを強制する（取得後の Python 判定にしない）。
       sources が空なら SQL を発行せず None → 404（fail-closed）。

    かつては `list_visible_document_ids`（本人の全域可視集合）で絞っていたため、
    course に紐づかない別コース・public 文書のチャンクも URL の course 経由で読めていた。
    course への正規アクセスが source 文書の開示根拠であるという設計はそのまま
    （`list_visible_document_ids` との積集合は取らない — 取ると教員 private のコース教材が
    受講者から読めなくなり、コース経由開示が壊れる）。
    """
    course_data = get_accessible_course_data(current_user["id"], course_id)
    if course_data is None:
        raise HTTPException(status_code=404, detail="Source chunk not found")
    allowed_document_ids = list_course_source_document_ids(course_data)
    passage = get_chunk_passage(chunk_id, allowed_document_ids=allowed_document_ids)
    if not passage:
        raise HTTPException(status_code=404, detail="Source chunk not found")
    return passage


@router.get("/courses/{course_id}/chunks/{chunk_id}/claim-refs")
def get_chunk_claim_refs_route(
    course_id: str,
    chunk_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """出典タブの台帳併記（D3-6）を claim にも拡張するための学習者向け読み取り API。

    チャンクが当該コースの sources 教材に属するかを検証したうえで、そのチャンクに
    紐づく claim の最小情報（id・claim_type・短い label）のみを返す。数値
    （confidence 等）は含めない。コース非アクセス・チャンクがコース教材に属さない
    場合は 404（fail-closed。既存 source-chunk API のゲート欠落は繰り返さない）。
    """
    course_data = get_course_data(current_user["id"], course_id)
    if course_data is None:
        raise HTTPException(status_code=404, detail="Course not found")
    claims = get_chunk_claim_refs(course_data, chunk_id, user_id=current_user["id"])
    if claims is None:
        raise HTTPException(status_code=404, detail="Chunk not found in this course")
    return {"claims": claims}


@router.get("/courses/{course_id}/interest-traces")
def get_interest_traces_route(
    course_id: str,
    topic_id: str | None = None,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """問いの軌跡（未解決の問い・寄り道・誤答）を実データで返す（L3 資産化）。

    interest_traces から本人の痕跡を status 主役で返す。個人特定情報は含めない。
    """
    view = get_interest_traces(current_user["id"], course_id, topic_id)
    # 個人知識ネットワーク（わたしの地図）への表示除外フラグを付与する（UX proposal §6:
    # 地図には反映しない/地図に戻す）。既存の get_interest_traces は変更せず、
    # ここで1フィールド足すだけの最小変更にする。
    exclusion_flags = get_trace_map_exclusion_flags(current_user["id"], course_id)
    for trace in view.get("traces") or []:
        trace["map_excluded"] = exclusion_flags.get(trace["id"], False)
    return view


@router.post("/courses/{course_id}/interest-traces/{trace_id}/resolve")
def resolve_interest_trace_route(
    course_id: str,
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """痕跡を「解決済み」にする（本人の痕跡のみ）。"""
    ok = resolve_interest_trace(current_user["id"], trace_id, status="resolved")
    if not ok:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"ok": True, "trace_id": trace_id, "status": "resolved"}


class InternalizationRequest(BaseModel):
    reason: str = ""


@router.post("/courses/{course_id}/interest-traces/{trace_id}/internalize")
def internalize_interest_trace_route(
    course_id: str,
    trace_id: str,
    body: InternalizationRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """「なぜ自分に重要か」(Internalization Prompt) を痕跡へ保存する（L3・内発的動機）。"""
    ok = record_internalization(current_user["id"], trace_id, body.reason)
    if not ok:
        raise HTTPException(status_code=400, detail="Could not save internalization")
    return {"ok": True, "trace_id": trace_id}


# ---------------------------------------------------------------------------
# 分野の地図 — 学習パス提案カードの三択記録 (Issue C-3)
# ---------------------------------------------------------------------------


class AtlasPathDecisionRequest(BaseModel):
    node_id: str = ""
    node_label: str = ""
    level: int = 1
    skeleton_version: str = ""
    decision: str = ""  # proceed | edit | dismiss | connect
    learner_text: str = ""
    steps: list[str] = []
    topic_id: str | None = None


# 三択 + 「自分で繋ぐ」→ interest_traces の status。
# 却下 (dismiss) も status='dismissed' で保持し、削除しない (情報を落とさない §1.2)。
# connect は本人の言葉での記録なので articulated。
_ATLAS_PATH_DECISIONS = {
    "proceed": ("resolved", "この糸で進む"),
    "edit": ("resolved", "編集する"),
    "dismiss": ("dismissed", "今はやめる"),
    "connect": ("articulated", "自分で繋ぐ"),
}


@router.post("/courses/{course_id}/atlas/path-decision")
def record_atlas_path_decision(
    course_id: str,
    body: AtlasPathDecisionRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """学習パス提案カードの選択を interest_traces に帰属つきで記録する (Issue C-3)。

    [この糸で進む] [編集する] [今はやめる] と「自分で繋ぐ」入力のすべてを記録する。
    却下 (今はやめる) も記録する — 情報を落とさない。
    """
    if body.decision not in _ATLAS_PATH_DECISIONS:
        raise HTTPException(status_code=400, detail="Unknown decision")
    status, decision_label = _ATLAS_PATH_DECISIONS[body.decision]
    text = f"学習パス提案（「{body.node_label or body.node_id}」から）: {decision_label}"
    if body.decision == "connect" and body.learner_text.strip():
        # 「自分で繋ぐ」は本人の言葉をそのまま主文に残す (§1.2-5)
        text = body.learner_text.strip()
    record_interest_trace(
        current_user["id"], course_id, body.topic_id,
        kind="raw",
        text=text,
        context_label=body.node_label,
        extra_payload={
            "atlas": {
                "node_id": body.node_id,
                "level": body.level,
                "skeleton_version": body.skeleton_version,
                "action": "path_decision",
                "node_label": body.node_label,
            },
            "decision": body.decision,
            "path_steps": body.steps[:12],
        },
        status=status,
    )
    return {"ok": True, "decision": body.decision, "status": status}


# ---------------------------------------------------------------------------
# 違和感（tension）— TensionMiningAgent Stage 2: ダイジェスト・本人確定
# ---------------------------------------------------------------------------
# 権限: すべて本人（user_id 一致）のみ。教員・管理者は個別行にアクセス不可（P3）。


class TensionConfirmRequest(BaseModel):
    learner_text: str = ""


class TensionConnectRequest(BaseModel):
    component_id: str = ""
    edge_id: str = ""


@router.get("/courses/{course_id}/tension/digest")
def get_tension_digest_route(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人の tension 候補ダイジェスト（candidate・confidence>=0.55・新しい順に最大3件）。

    confidence の数値は返さない（学習者に数値スコアを見せない原則の準用）。
    セッション終了（20分無活動）後の未解析ヒントがあれば、ここで遅延起動する
    （best-effort・非同期。今回のレスポンスには間に合わなくてよい）。
    """
    session = _pg_session()
    try:
        topic_rows = session.execute(
            sa_text("""
                SELECT DISTINCT topic_id FROM interest_traces
                WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
                  AND payload->>'tension_hint' = 'true' AND analyzed_at IS NULL
            """),
            {"uid": current_user["id"], "cid": course_id},
        ).fetchall()
    except Exception:
        topic_rows = []
    finally:
        session.close()
    for (tid,) in topic_rows:
        maybe_schedule_tension_mining(current_user["id"], course_id, tid, session_end_check=True)

    return get_tension_digest(current_user["id"], course_id)


@router.post("/tension/{trace_id}/confirm")
def confirm_tension_route(
    trace_id: str,
    body: TensionConfirmRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """候補を本人が引き受ける: candidate → open（learner_text ありなら articulated）。

    違和感を生成するのは人間であり、この操作だけが候補を tension として確定する（P1）。
    """
    result = confirm_tension_trace(current_user["id"], trace_id, body.learner_text)
    if result is None:
        raise HTTPException(status_code=404, detail="Tension candidate not found")
    return {"ok": True, **result}


@router.post("/tension/{trace_id}/dismiss")
def dismiss_tension_route(
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人が「違う」と判定: candidate → dismissed（行は残す。P4）。"""
    result = dismiss_tension_trace(current_user["id"], trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Tension candidate not found")
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# 構造帰属（structure_anchor）— StructureAnchorAgent Stage 2: ダイジェスト・本人確定
# ---------------------------------------------------------------------------
# 権限: すべて本人（user_id 一致）のみ。教員・管理者は個別行にアクセス不可（P3）。
# 行の status は変えない（問い自体は確定済み。候補なのは帰属だけ）。


class AnchorConfirmRequest(BaseModel):
    doubt_type: str = ""
    anchor_type: str = ""
    anchor_id: str = ""
    anchor_label: str = ""


@router.get("/courses/{course_id}/anchors/digest")
def get_anchor_digest_route(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人の帰属候補ダイジェスト（llm_candidate・confidence>=0.55・新しい順に最大3件）。

    「この疑問は◯◯についてでしたか？」の確認カード用。confidence の数値は返さない。
    セッション終了（20分無活動）後の未帰属の問いがあれば、ここで遅延起動する
    （best-effort・非同期。今回のレスポンスには間に合わなくてよい）。
    """
    session = _pg_session()
    try:
        topic_rows = session.execute(
            sa_text("""
                SELECT DISTINCT topic_id FROM interest_traces
                WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
                  AND kind = 'question'
                  -- 機能3: 差し替え済みの問いだけが残るトピックで帰属解析を起動しない
                  -- （_fetch_pending_questions と同じ supersede 意味論。設計書 §2.4）
                  AND status <> 'superseded'
                  AND payload->'structure_anchor' IS NULL
                  AND payload->>'anchor_analyzed_at' IS NULL
            """),
            {"uid": current_user["id"], "cid": course_id},
        ).fetchall()
    except Exception:
        topic_rows = []
    finally:
        session.close()
    for (tid,) in topic_rows:
        maybe_schedule_anchor_mining(current_user["id"], course_id, tid, session_end_check=True)

    return get_anchor_digest(current_user["id"], course_id)


@router.post("/anchors/{trace_id}/confirm")
def confirm_anchor_route(
    trace_id: str,
    body: AnchorConfirmRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """帰属を本人が確定/訂正する: → attribution_source='confirmed'。

    帰属を確定するのは人間であり、この操作だけが LLM 候補を帰属として確定する（P1）。
    doubt_type / anchor_type / anchor_id を与えればその値で訂正して確定する。
    帰属が未生成の痕跡（方法Cの1タップ申告）には segment 縮退の最小アンカーを作る。
    """
    result = confirm_anchor_trace(
        current_user["id"], trace_id,
        doubt_type=body.doubt_type,
        anchor_type=body.anchor_type,
        anchor_id=body.anchor_id,
        anchor_label=body.anchor_label,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Anchor trace not found")
    # D層 (D3-6): 確定した anchor が分野で明示化済みの前提に対応していれば、
    # 事後に静かに併記する（通知しない・押し付けない。best-effort、失敗は無視）。
    try:
        from core.doubt.open_assumptions import related_confirmed_assumption
        from core.postgres import get_session as _doubt_session

        anchor = result.get("structure_anchor") or {}
        anchor_id = str(anchor.get("anchor_id") or "")
        if anchor_id:
            _ds = _doubt_session()
            try:
                related = related_confirmed_assumption(_ds, anchor_id)
            finally:
                _ds.close()
            if related:
                result["related_assumption"] = related
    except Exception:
        logger.debug("related assumption lookup skipped", exc_info=True)
    return {"ok": True, **result}


@router.post("/anchors/{trace_id}/dismiss")
def dismiss_anchor_route(
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人が帰属候補を「違う」と判定: structure_anchor.status='dismissed'（保持する。P4）。

    問い自体（行）は有効なまま残る。
    """
    result = dismiss_anchor_trace(current_user["id"], trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Anchor candidate not found")
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# 個人知識ネットワーク（わたしの地図）— 表示除外/復帰 (UX proposal §6)
# ---------------------------------------------------------------------------
# 「地図には反映しない」「地図に戻す」操作。痕跡は削除されず（P4）、地図の導出
# （core/personal_graph/derive.py）から外れるだけ。tension/anchor の dismiss（候補の
# 当落判定）とは独立で、status には触れない。本人のみ（current_user 以外の
# user_id を受けない）。


@router.post("/traces/{trace_id}/map-exclude")
def map_exclude_trace_route(
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """個人知識ネットワークへの表示から本人の痕跡を除外する（削除ではない。P4）。"""
    result = set_trace_map_exclusion(current_user["id"], trace_id, True)
    if result is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"ok": True, **result}


@router.post("/traces/{trace_id}/map-restore")
def map_restore_trace_route(
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """表示除外していた痕跡を個人知識ネットワークの表示へ戻す。"""
    result = set_trace_map_exclusion(current_user["id"], trace_id, False)
    if result is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# ハンズフリー音声会話（カジュアル対話モード用）
# ---------------------------------------------------------------------------

# アップロード音声の上限（無音区切りの1発話分。長くても数十秒を想定）
_VOICE_MAX_AUDIO_BYTES = 10 * 1024 * 1024


class VoiceSpeakRequest(BaseModel):
    text: str


@router.post("/voice/transcribe")
async def voice_transcribe_route(
    audio: UploadFile = File(...),
    language: str = "ja",
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """音声（1発話分）を Whisper 系モデルでテキストに文字起こしする。

    フロントの無音検知が区切った短い音声チャンクを受ける。openai プロバイダ以外では 503。
    """
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio")
    if len(data) > _VOICE_MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio too large")
    try:
        with usage_context("learning:voice_stt", user_id=current_user["id"]):
            text = transcribe_audio(data, audio.filename or "audio.webm", language=language)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("voice transcribe failed")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc
    return {"text": text}


@router.post("/voice/speak")
def voice_speak_route(
    body: VoiceSpeakRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """回答テキストを TTS で MP3(base64) に変換する（ハンズフリー会話の読み上げ用）。

    読み上げ前に LaTeX・markdown 記号・出典マーカーを除去する。
    """
    spoken = strip_text_for_speech(body.text)
    if not spoken:
        raise HTTPException(status_code=400, detail="Nothing to speak")
    try:
        with usage_context("learning:voice_tts", user_id=current_user["id"]):
            audio_bytes = generate_tts_audio(spoken)
    except Exception as exc:
        logger.exception("voice speak failed")
        raise HTTPException(status_code=500, detail=f"TTS failed: {exc}") from exc
    if audio_bytes is None:
        raise HTTPException(status_code=503, detail="TTS provider is not available")
    return {"audio_base64": base64.b64encode(audio_bytes).decode("ascii"), "format": "mp3"}


# ---------------------------------------------------------------------------
# インスペクト・モード（設計 docs/features/learning_ui_inspect_hover_design.md
# §5.2/§5.4/§9）: UI 論理アンカーの配信 + 未整備アンカーへのホバー滞留（no_hit）記録。
# どちらもコース非依存（トップバー・サイドバー等、画面全体のUI部品が対象）。
# ---------------------------------------------------------------------------

# interest_traces.course_id は NOT NULL のため、コース文脈を伴わない UI 全体の
# no_hit 記録には予約疑似コースIDを使う（discuss の "_discussion" と同じパターン）。
_UI_ANCHOR_EVENT_COURSE_ID = "_ui"

_UI_ANCHOR_EVENT_KINDS = frozenset({"no_hit"})

# 同一ユーザー×同一アンカーの no_hit 記録を書きすぎない簡易スパム防止（IH10/§5.4）。
_UI_ANCHOR_EVENT_DEDUP_WINDOW_MINUTES = 30


@router.get("/help/ui-anchors")
def get_ui_anchors_route(current_user: dict = Depends(_get_current_user)) -> dict:
    """インスペクト・モードの UI 論理アンカー配信（設計 §5.2/§9-3）。

    ログイン必須・読み取り専用・痕跡を書かない。クライアントはログイン時に1回
    フェッチしてキャッシュする前提で、ホバーごとに呼ばれることは想定しない。
    student audience で解決済みの節のみを返す（audience 越境なし）。
    """
    del current_user
    if _resolve_ui_anchors is None:
        return {"anchors": {}}
    try:
        return {"anchors": _resolve_ui_anchors()}
    except Exception:
        logger.warning("resolve_ui_anchors failed for ui-anchors endpoint", exc_info=True)
        return {"anchors": {}}


class UiAnchorEventRequest(BaseModel):
    anchor_id: str
    kind: str = "no_hit"
    # インスペクトは画面全体のモードのため、送信時点でコース/トピックが定まらない
    # 場合がある（例: コース選択前のトップバー）。両方任意。
    course_id: str | None = None
    topic_id: str | None = None


def _recent_duplicate_ui_anchor_event(user_id: str, help_anchor: str) -> bool:
    """直近（既定 30 分）に同一ユーザー×同一アンカーの no_hit 記録が無いかを確認する。

    スパム防止（IH10/§5.4）の簡易実装。実体は管理画面インスペクト・モード
    （``routes/admin_assistant.py``）と共有する ``services.recent_duplicate_ui_anchor_event``
    に委譲（外部挙動不変・DB 障害時 False で fail-open）。
    """
    return _recent_duplicate_ui_anchor_event_shared(
        user_id, help_anchor, window_minutes=_UI_ANCHOR_EVENT_DEDUP_WINDOW_MINUTES,
    )


@router.post("/help/ui-anchor-events", status_code=201)
def record_ui_anchor_event_route(
    body: UiAnchorEventRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """未整備 UI アンカーへのホバー滞留を help_usage 痕跡として記録する（設計 §5.4/§8）。

    質問の逐語は積まない（anchor_id は固定の論理IDであり自由文ではない）。
    滞留閾値・生ホバーイベントの記録禁止（IH10）はフロント側の責務 — ここは
    「一定時間ツールチップが表示され続けた」という事実の記録のみを担う。
    記録した行は G層 ``manual.help_gaps_pending`` の需要側集計にそのまま乗る
    （``help_anchor`` が空文字列にならない限り、no_hit の汎用バケツではなく
    ``ui:<anchor_id>`` 単位のバケツに集計される）。
    """
    anchor_id = (body.anchor_id or "").strip()
    if not anchor_id or anchor_id not in _KNOWN_UI_ANCHOR_IDS:
        raise HTTPException(status_code=422, detail=f"未知の UI アンカー: {body.anchor_id!r}")
    if body.kind not in _UI_ANCHOR_EVENT_KINDS:
        raise HTTPException(status_code=422, detail=f"未知の kind: {body.kind!r}")

    user_id = current_user["id"]
    help_anchor = f"ui:{anchor_id}"

    if _recent_duplicate_ui_anchor_event(user_id, help_anchor):
        return {"ok": True, "recorded": False}

    course_id = (body.course_id or "").strip() or _UI_ANCHOR_EVENT_COURSE_ID
    record_interest_trace(
        user_id, course_id, body.topic_id or None,
        kind="help_usage",
        text="UI要素の使い方（未整備）",
        extra_payload={"help_anchor": help_anchor, "documented": False, "no_hit": True},
    )
    return {"ok": True, "recorded": True}


def _tension_connect_edge_viewable(user_id: str, edge_id: str) -> bool:
    """connect 先の graph edge が本人にとって閲覧可能な document に属するか検証する（N38）。

    component 側の検証（``services._tension_connect_component_viewable``）と同型の
    予防的 fail-closed ゲート。graph edge は独立テーブルを持たず
    ``theory_component_graphs.graph_json`` の ``edges[]`` 内に ``edge_id`` キーで
    存在するため、JSONB containment で所属 document を解決し、
    ``services.resolve_document_access`` で閲覧可否を判定する。

    edge が見つからない・document が特定できない・閲覧不可、のいずれも False
    （安全側）。既存の connected 行には触らない — connect 時の新規書き込みだけを
    堰き止める（設計書 §6 / PN-7。journey が閲覧不可 document の情報を漏らす経路を
    connect 時点で断つ、component 側と同じ理由の予防措置）。
    """
    from services import resolve_document_access  # 既存 services の権限判定正本を再利用

    session = _pg_session()
    try:
        try:
            rows = session.execute(
                sa_text("""
                    SELECT DISTINCT document_id FROM theory_component_graphs
                    WHERE graph_json->'edges' @> jsonb_build_array(
                        jsonb_build_object('edge_id', CAST(:eid AS text))
                    )
                """),
                {"eid": edge_id},
            ).fetchall()
        except Exception:
            return False
    finally:
        session.close()
    document_ids = sorted(str(r[0]) for r in rows if r and r[0])
    if not document_ids:
        return False
    return any(resolve_document_access(user_id, doc_id).can_view for doc_id in document_ids)


@router.post("/tension/{trace_id}/connect")
def connect_tension_route(
    trace_id: str,
    body: TensionConnectRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """確定済み tension をグラフ上の node/edge に接続する（後続フェーズ）。

    component_id の閲覧可否は ``services.connect_tension_trace`` 内で検証済み。
    edge_id は route 側で同型に検証する（N38。fail-closed・既存データ非改変）。
    """
    edge_id = (body.edge_id or "").strip()
    if edge_id and not _tension_connect_edge_viewable(current_user["id"], edge_id):
        raise HTTPException(status_code=400, detail="Could not connect tension trace")
    result = connect_tension_trace(
        current_user["id"], trace_id,
        component_id=body.component_id, edge_id=body.edge_id,
    )
    if result is None:
        raise HTTPException(status_code=400, detail="Could not connect tension trace")
    return {"ok": True, **result}


@router.get("/courses/{course_id}/components/{component_id}/explanations")
def get_component_explanations_for_learner(
    course_id: str,
    component_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """学習者向け: 1つの理論・概念の説明バージョン(標準/各教員の説明)を返す(C層 Phase 2)。

    承認済み(teacher_approved)の説明のみを返す。標準説明は kind='standard'。
    承認の厚みは段階ラベルで示し、数値スコアは学習者に提示しない(点数化を避ける)。
    """
    from routes.theory_components import _endorsement_label  # 遅延 import(循環回避)

    if not get_viewable_course_data(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT e.id, e.kind, COALESCE(u.display_name, ''), e.title, e.body,
                       COALESCE(s.endorser_count, 0), COALESCE(s.strong_count, 0),
                       COALESCE(s.provisional_count, 0), COALESCE(s.expertise_breadth, 0)
                FROM component_explanations e
                LEFT JOIN users u ON u.id = e.author_id
                LEFT JOIN component_explanation_endorsement_summary s ON s.explanation_id = e.id
                WHERE e.component_id = CAST(:cid AS uuid)
                  AND e.course_id = :course_id
                  AND e.review_status = 'teacher_approved'
                ORDER BY (e.kind = 'standard') DESC, COALESCE(s.endorser_count, 0) DESC, e.created_at ASC
            """),
            {"cid": component_id, "course_id": course_id},
        ).fetchall()
    finally:
        session.close()
    explanations = []
    for r in rows:
        summary = {
            "endorser_count": int(r[5] or 0),
            "strong_count": int(r[6] or 0),
            "provisional_count": int(r[7] or 0),
            "expertise_breadth": int(r[8] or 0),
        }
        explanations.append({
            "id": str(r[0]),
            "kind": str(r[1] or "personal"),
            "author_name": str(r[2] or ""),
            "title": str(r[3] or ""),
            "body": str(r[4] or ""),
            "endorsement_label": _endorsement_label(summary),
        })
    return {"component_id": component_id, "explanations": explanations}


def _first_approved_component_explanation(component_id: str, course_id: str) -> dict | None:
    """承認済み(teacher_approved)の component_explanations を1件返す(C層)。

    ``get_component_explanations_for_learner`` と同じ承認条件・course スコープ・
    並び順（標準優先 → 承認厚み → 作成順）で、先頭1件のみを
    ``get_course_component_context`` の ``instance.explanation`` に充填する。
    """
    from routes.theory_components import _endorsement_label  # 遅延 import(循環回避)

    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT e.kind, COALESCE(u.display_name, ''), e.title, e.body,
                       COALESCE(s.endorser_count, 0), COALESCE(s.strong_count, 0),
                       COALESCE(s.provisional_count, 0), COALESCE(s.expertise_breadth, 0)
                FROM component_explanations e
                LEFT JOIN users u ON u.id = e.author_id
                LEFT JOIN component_explanation_endorsement_summary s ON s.explanation_id = e.id
                WHERE e.component_id = CAST(:cid AS uuid)
                  AND e.course_id = :course_id
                  AND e.review_status = 'teacher_approved'
                ORDER BY (e.kind = 'standard') DESC, COALESCE(s.endorser_count, 0) DESC, e.created_at ASC
                LIMIT 1
            """),
            {"cid": component_id, "course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return None
    summary = {
        "endorser_count": int(row[4] or 0),
        "strong_count": int(row[5] or 0),
        "provisional_count": int(row[6] or 0),
        "expertise_breadth": int(row[7] or 0),
    }
    return {
        "kind": str(row[0] or "personal"),
        "title": str(row[2] or ""),
        "body": str(row[3] or ""),
        "author_name": str(row[1] or ""),
        "endorsement_label": _endorsement_label(summary),
    }


@router.get("/courses/{course_id}/components/{component_id}/context")
def get_course_component_context(
    course_id: str,
    component_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """コーススコープ component 文脈 API（component_evidence_redesign Phase 2/3）。

    3条件の fail-closed（学習者向け図配信 API
    ``get_course_figure_image`` の Phase 4 パターンを踏襲）:
    1. 受講ゲート（``get_accessible_course_data`` — 本人が当該コースを閲覧できる）
    2. component の document がコースの document 集合
       （``_course_document_ids``）に含まれる（``core.component_context`` 内の
       SQL 制約として実施 — コース外文書の component は解決自体が失敗する）
    3. component 自体が解決できる（DB UUID または agent 側 legacy ID の両方を受理）

    いずれかが欠ければ 404（fail-closed）。承認済み(teacher_approved)の説明が
    1件あれば ``instance.explanation`` に充填する(C層。承認・共有レイヤーの
    既存条件をそのまま踏襲し、A/C層のコードは変更しない)。
    """
    course_data = get_accessible_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    course_document_ids = set(_course_document_ids(course_data))
    context = build_component_context(component_id, course_id, course_document_ids)
    if context is None:
        raise HTTPException(status_code=404, detail="Component not found")

    explanation = _first_approved_component_explanation(context["component_id"], course_id)
    if explanation is not None:
        context["instance"]["explanation"] = explanation
    return context


@router.get("/courses/{course_id}/elements/{element_type}/{element_id}/context")
def get_course_element_context(
    course_id: str,
    element_type: str,
    element_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """学習者向け claim / equation 文脈 API（learner_element_context_design Phase 3）。

    ``element_type`` は ``claim`` / ``equation`` のみ（それ以外は 404 — 学習者 API の
    404 統一方針）。component 文脈 API（``get_course_component_context``）と同じ
    3条件の fail-closed:
    1. 受講ゲート（``get_accessible_course_data`` — 本人が当該コースを閲覧できる）
    2. 要素の document がコースの document 集合（``_course_document_ids``）に含まれる
       （``core.element_context`` 内で claim は SQL の
       ``document_id = ANY(:doc_ids)``、equation はコース document 集合のみを
       走査対象にすることで実施 — コース外文書の要素は解決自体が失敗する）
    3. 要素自体が解決できる（claim は DB UUID / agent 側 legacy ID の両方を受理）

    いずれかが欠ければ 404（fail-closed）。要素は解決できたが W層 context lens が
    投影を返せない場合のみ ``{"available": false, "note": ...}`` を 200 で返す
    （fail-soft。文脈が無いことは異常ではない）。``upper`` / ``lower`` から
    ``relation_status == "candidate"`` は除外され、``confidence`` 等の数値は
    再帰的に除去される（学習者に未確定の AI 候補と生数値を出さない）。
    """
    if element_type not in CONTEXT_ELEMENT_TYPES:
        raise HTTPException(status_code=404, detail="Element not found")

    course_data = get_accessible_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    course_document_ids = set(_course_document_ids(course_data))
    context = build_element_context(element_type, element_id, course_document_ids)
    if context is None:
        raise HTTPException(status_code=404, detail="Element not found")
    return context
