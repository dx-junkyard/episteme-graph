"""Episteme Graph — 学習エンドポイント (/api/learning)。"""

from __future__ import annotations

import dataclasses
import base64
import json
import logging
import re
import threading
import uuid
from contextlib import nullcontext
from dataclasses import asdict
from functools import lru_cache
from typing import Callable

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import text as sa_text

from dependencies import _get_current_user
from quota import consume_daily_quota
from schemas import (
    ChunkContent,
    CourseCreateRequest,
    CourseUpdateRequest,
    LearningChatHistoryResponse,
    LearningChatRequest,
    LearningChatResponse,
    LearningCheckObservation,
    LearningCheckQuestionRequest,
    LearningCheckQuestionResponse,
    LearningCheckSelfCheckRequest,
    LearningCheckSelfCheckResponse,
    LearningCourseDetail,
    LearningCourseLayeredResponse,
    LearningCourseOut,
    LearningEnrollOut,
    LearningProgress,
    PersonalLayer,
    TopicMaterialResponse,
)
from services import (
    calculate_progress,
    check_prerequisites,
    _is_explicit_prerequisite_acknowledgement,
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
    load_stored_chat_history,
    CITATION_MAP_KEY as _SERVICES_CITATION_MAP_KEY,
    topic_source_document_ids,
    persist_chat_history,
    truncate_chat_and_supersede,
    recent_duplicate_ui_anchor_event as _recent_duplicate_ui_anchor_event_shared,
    record_internalization,
    record_interest_trace,
    record_learner_articulated_tension,
    record_review_event,
    record_topic_check_pass,
    review_personal_misconception,
    MISCONCEPTION_DECISIONS,
    resolve_document_access,
    resolve_interest_trace,
    save_course_data,
    set_trace_map_exclusion,
    delete_course_data,
    list_course_source_document_ids,
    list_visible_document_ids,
    get_chunks_for_prompt,
    search_chunks_with_metadata,
    user_can_access_group,
    user_can_view_course,
)
from pydantic import BaseModel
from core.course_data import (
    course_cartridge_id,
    course_content_is_preparing,
    course_content_state,
    course_focus,
    course_llm_models,
    course_source_material_ids,
    course_title as _course_title,
    course_topics,
    find_course_topic,
    is_symbol_concept_name,
    iter_all_topics,
    learner_topic_units_projection,
    topic_unit_keys,
)
from core.teaching_figures import store as teaching_figures_store
from core import decision_context
from core.course_prerequisites import resolve_prerequisite_topic_ids
from core.course_units import (
    candidate_keys,
    list_unit_candidates,
    resolve_unit_refs,
)
from core.schema import AUDIT_ENTITY_COURSE_TOPIC
from core.cartridges import load_cartridge
from core.config import get_settings
from core.lecture import (
    annotate_reconstructed_formulas,
    build_topic_slides,
    find_figure_embed_ids,
    resolve_figure_embeds,
    topic_material_delivery_segments,
)
from core import check_review
from core import element_explanations
from core import llm_policy
from core import label_vocab
from core.llm import (
    generate_text,
    generate_text_stream,
    get_llm_params,
    transcribe_audio,
)
from core.storage import get_storage_client
from core.llm_usage.context import usage_context
from core.llm_worker.client import resolve_model
from core.llm_worker.cost_gate import CostGate
from core.llm_worker.history import window_history
from core.llm_worker.single_shot import json_call
from core.text_hygiene import (
    UNTRUSTED_SOURCE_NOTICE,
    sanitize_source_text_for_prompt,
    scrub_internal_placeholders,
    strip_control_sequences,
)
from core.tts import generate_tts_audio, strip_text_for_speech
from core.learning_experience import (
    TIER_APPROVED,
    TIER_OUT_OF_SOURCE,
    TIER_SOURCE,
    aggregate_overall_tier,
    build_position_anchor,
    out_of_source_guard_instruction,
    out_of_source_notice,
    tier_floor,
)
from core.learning_stance.heuristic import prejudge as prejudge_stance_route
from core.learning_stance.schema import build_stance_dto, resolve_stance
# 画面文脈アダプター Phase 4（assistant_screen_adapter_design.md §11）: 画面が渡した
# 参照を正規化し、route が組んだ権限ゲート済み sources から事実文ブロックを描く。
# 解決器は core 側（決定論・非LLM・読み取り専用）。
from core.assistant_context import (
    BLOCK_HEADER_LEARNING,
    BLOCK_HEADER_RETRIEVED,
    MAX_BLOCK_CHARS_LEARNING,
    SCREEN_LEARNING,
    normalize_screen_context,
    render_block,
    render_selection_block,
    resolve as resolve_screen_context,
)
from core.assistant_context.schema import (
    LEARNING_ELEMENT_TYPES,
    LEARNING_VERIFICATION_OUTPUT_CONSTRAINT,
    MAX_LEARNING_RETRIEVED_CLAIMS_PER_SOURCE,
)
from core.learning_support_agent import (
    LearningSupportAgent,
    LearningSupportResult,
    extract_inline_actions,
)
from core.personas import course_persona_settings, persona_prompt
from core.postgres import get_session as _pg_session
from core.component_context import build_component_context
# 知識の転用層 P4-2: 理論操作グラフの読み出しは W層の既存関数を再利用する
# （main 層ノードの review_status 合成も含め、二重実装しない）。
from core.deliberation.graph_dialogue import load_latest_graph
from core.element_context import (
    SUPPORTED_ELEMENT_TYPES as CONTEXT_ELEMENT_TYPES,
    build_element_context,
)
# 概念レジストリ P3-5（concept_registry_design.md §7）: 記号の「直前の定義」。
# 読み取り専用・LLM 0 回・コース sources へ SQL 内でスコープ強制する。
from core.symbol_lookup import lookup_symbol_definition
from core.discuss.opening import build_opening as build_discussion_opening
from core.discuss.opening import document_thesis_fact_lines
from core.discuss.mirroring import MIRROR_MOVED_NOTE, MIRROR_MOVED_NOTE_EN, extract_mirror
# コーパス回遊 Phase B（docs/features/corpus_roaming_design.md §5.1）: コース無し論文議論の
# 会話コンテキスト・センチネル。**"_doc:" の組み立て・判定はこの正本関数以外に書かない**。
from core.discuss.context import document_context_id, parse_document_context
from core.discuss import observation as discuss_observation
from core.cycle.derive import build_intention_dto
from core.cycle.queries import fetch_active_carryover, fetch_intentions
from core.course_content_builder import (
    strip_generated_reference_appendix,
    build_course_content_background,
    build_topic_evidence_items,
    normalize_evidence_id,
    material_text_for_prompt,
)
from core.text_excerpt import excerpt
from core.atlas_path import build_learning_path_card
from core.tension.prefilter import judge_tension_hint
from core.tension.worker import maybe_schedule_tension_mining
from core.structure_anchor.schema import (
    ANCHOR_TYPE_LABELS,
    ANCHOR_TYPES,
    ATTRIBUTION_LEARNER_SELECTED,
    DOUBT_TYPE_LABELS,
    anchor_type_for_element,
    build_anchor_payload,
)
from core.structure_anchor.selection_segment import resolve_selection_segment
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
# 画面文脈アダプター Phase 4（§11.3「生テーブルを引かない」）: 台帳・配置の**学習者向け
# 射影**は各エンドポイントと同じ関数を通す（遮断を2箇所に書かない）。private helper の
# クロスルーター再利用は routes.lecture / routes.teaching_figures と同じ既存パターン。
from routes.doubt import learner_ledger_line
from routes.landscape import learner_landscape_for_documents
# agent 側 ID（DB 行を持たない集約ノード等）を台帳の照会に流さないための事前判定。
# 正本は routes/theory_components.py（定義を二重化しない）。
from routes.theory_components import _is_db_uuid
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

#: LLM 失敗時の degraded 固定文（設計書 I3「会話は死なせない」）。チャット本体と
#: グラフ要素説明の2経路が同じ文を返す（経路ごとに言い換えない）。
_CHAT_DEGRADED_MESSAGE = "AI 応答を生成できませんでした。しばらくしてからもう一度お試しください。"


def _source_location_meta(result: dict) -> str:
    """出典1件の ``meta``（論文の中の箇所の手がかり）。IK-0381。

    ``search_chunks_with_metadata`` が chunk 行から作った ``location_hint``（節の見出し +
    区画の冒頭）を優先し、無いとき（旧データ・テストのスタブ）だけファイル名に縮退する。
    learning_chat と前提説明の2つの組み立て箇所で同じ値にするための1関数。
    """
    return str(result.get("location_hint") or result.get("source_file") or "")


def _consume_learning_chat_quota(user_id: str, quota_state: dict) -> None:
    """そのリクエストで最初に LLM を呼ぶ直前に1回だけコスト上限を消費するヘルパー。

    ``quota_state`` はリクエストスコープの mutable dict（``{"consumed": False}``）。
    同一リクエスト内で複数回 LLM を呼んでも消費は1回のみ（学習チャットは intent 分類〜
    本体まで含めて1、設計書 §1）。LLM を1度も呼ばないパス（承認済み説明があるグラフ
    要素タップ等）からはそもそも呼ばれないため消費されない。超過時は 429（事実文のみ・
    数値非表示, I2）。
    """
    settings = get_settings()
    consume_daily_quota(
        _learning_chat_cost_gate,
        user_id=user_id,
        limit=int(getattr(settings, "learning_chat_max_calls_per_day", 300) or 0),
        message="本日のAI呼び出し回数の上限に達しました。明日以降に再度お試しください。",
        quota_state=quota_state,
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


# ---------------------------------------------------------------------------
# 学ぶ単位の一級化 Phase 2（learning_units_design.md §6.2 / §6.4 / §7）:
# コース登録 = 「提示された unit 候補のうち topic に束ねたもの」の一括確定。
# ---------------------------------------------------------------------------

#: 登録後に unit の束ねを直す経路（原稿スタジオのトピック保存）。decision_context の reopen。
_COURSE_TOPIC_REOPEN_PATH = "PUT /api/admin/courses/{course_id}/lecture-studio/course-topics/{topic_id}"


def _ordered_source_document_ids(
    session, material_ids: list[str], *, user_id: str
) -> list[str]:
    """sources の material_id 順に document.id（テキスト）を返す。

    handle（U1..Un）は document 順に依存するため、コースビルダーの
    ``_build_material_context`` と同じ **material_ids の順**で並べる（設計書 §6.2）。
    解決できない material は落とす（推測しない）。

    **可視性ゲートは必須**（P2-R5・fail-closed）: ``user_id`` にとって見えない
    document は、たとえ sources に material_id が書かれていても候補表に出さない。
    ``user_id`` が空、または可視集合が空なら **SQL を発行せず空**を返す
    （``search_chunks_with_metadata`` の ``allowed_document_ids`` と同じ姿勢）。
    """
    ordered = [str(m).strip() for m in (material_ids or []) if str(m or "").strip()]
    if not ordered or not str(user_id or "").strip():
        return []
    visible = set(list_visible_document_ids(user_id) or [])
    if not visible:
        return []
    placeholders = ", ".join(f":mid_{i}" for i in range(len(ordered)))
    rows = session.execute(
        sa_text(
            f"SELECT source_path, id::text AS doc_id FROM documents "
            f"WHERE source_path IN ({placeholders})"
        ),
        {f"mid_{i}": mid for i, mid in enumerate(ordered)},
    ).fetchall()
    by_material = {
        str(r[0]): str(r[1])
        for r in rows
        if r and r[0] and r[1] and str(r[1]) in visible
    }
    return list(dict.fromkeys(by_material[m] for m in ordered if m in by_material))


def _preserve_topic_units(incoming_topics: list[dict], existing_topics: object) -> list[dict]:
    """``PUT /courses/{id}`` の topics 反映で ``units`` の参照キーを失わせない（P2-R4）。

    学習者・教員向け GET は ``units`` を ``kind`` / ``label`` だけへ射影する
    （``learner_topic_units_projection``）ので、GET した下書きをそのまま PUT すると
    参照キーの無い units で上書きされ、freeze が何も束ねられなくなる。

    規則（topic id キーの温存）:

    - incoming の units が**参照キーを持っていれば**（``topic_unit_keys`` が非空）
      incoming を優先する（明示的な束ね直しはそのまま通す）。
    - そうでなければ、同じ topic id の既存 units を**そのまま温存**する。
    - 既存に対応が無ければ incoming のまま（新規トピック）。

    参照キーの綴りは ``core.course_data`` の述語だけで扱う（KO10: 学習者向け経路の
    ソースに内部列名を書かない）。入力は mutate しない。
    """
    existing_units: dict[str, list] = {}
    if isinstance(existing_topics, list):
        for topic in existing_topics:
            if not isinstance(topic, dict):
                continue
            topic_id = str(topic.get("id") or "").strip()
            units = topic.get("units")
            if topic_id and isinstance(units, list) and units:
                existing_units[topic_id] = units

    out: list[dict] = []
    for topic in incoming_topics:
        if not isinstance(topic, dict):
            out.append(topic)
            continue
        topic_id = str(topic.get("id") or "").strip()
        has_keys = bool(topic_unit_keys(topic))
        if has_keys or topic_id not in existing_units:
            out.append(topic)
            continue
        merged = dict(topic)
        merged["units"] = existing_units[topic_id]
        out.append(merged)
    return out


def _bind_topic_units(session, data: dict, *, user_id: str) -> tuple[list[dict], dict]:
    """``topics[].units``（handle の配列）を候補表で解決し、決定文脈の材料を返す。

    戻り値の第2要素は ``{"presented": [参照キー...], "applied": [参照キー...],
    "unresolved": bool}``。候補に無い handle は捨てる（LU3）。候補がゼロ（解析済みの
    unit が無い）なら units は空のまま・presented も空で、呼び出し側は記帳しない（LU7 / DC3）。
    """
    topics = [dict(t) for t in (data.get("topics") or []) if isinstance(t, dict)]
    document_ids = _ordered_source_document_ids(
        session, course_source_material_ids(data), user_id=user_id
    )
    candidates = list_unit_candidates(session, document_ids) if document_ids else []
    applied: list[str] = []
    unresolved = False
    outside_sources = False
    for topic in topics:
        raw = topic.get("units")
        requested = [u for u in raw if isinstance(u, (str, dict))] if isinstance(raw, list) else []
        # IK-0371: 草案が参照キー付き（{handle, 参照キー}）なら参照キーで引く — handle は
        # 草案を出したターンの候補表の位置で、登録時の sources の候補表では別の単位を
        # 指し得る。参照キーが登録時の候補表に無い単位（その論文が sources から外れた等）は
        # 捨て、その事実を記帳する（別の単位を黙って付けない）。
        if candidates:
            resolved, stats = resolve_unit_refs(candidates, requested)
        else:
            resolved, stats = [], {"key_missing": any(isinstance(u, dict) for u in requested)}
        if len(resolved) < len(requested):
            unresolved = True
        if stats.get("key_missing"):
            outside_sources = True
        topic["units"] = resolved
        applied.extend(topic_unit_keys(topic))
    info = {
        "presented": candidate_keys(candidates),
        "applied": list(dict.fromkeys(k for k in applied if k)),
        "unresolved": unresolved,
        "outside_sources": outside_sources,
    }
    return topics, info


def _record_course_registration(course_id: str, user_id: str, info: dict) -> None:
    """コース登録を一括確定として記帳する（設計書 §7・O-3(a)）。

    候補が提示されていないときは呼ばない（代替の無い確定は記帳できない = DC3）。
    新 entity_type は作らず ``AUDIT_ENTITY_COURSE_TOPIC`` に course 単位で 1 行。best-effort。
    """
    try:
        ctx = decision_context.build_decision_context(
            basis=decision_context.BASIS_COURSE_REGISTER_UNITS,
            presented_ids=info.get("presented") or [],
            applied_ids=info.get("applied") or [],
            # コースビルダーでは下書きを編集してから登録でき、unit は topic から外せる。
            alternatives=(decision_context.ALT_EDIT, decision_context.ALT_DESELECT),
            reopen_path=_COURSE_TOPIC_REOPEN_PATH,
            # unit の確定状態は candidate 始まりで、登録後も候補のまま見直せる（LU2）。
            reopen_statuses=("candidate",),
            # 候補区画に根拠（逐語）が出ていたかはサーバから検証できない（DC4）。
            evidence_shown=None,
        )
        metadata = decision_context.attach_decision_context(
            {
                "action": "course_register",
                "object_type": "course",
                "course_id": course_id,
                # 選ばれた handle のうち候補に無かったものを捨てた事実（件数は載せない・LU5）。
                "unit_handles_unresolved": bool(info.get("unresolved")),
                # IK-0371: 草案のターンの候補表で選ばれた単位のうち、登録時の sources の
                # 候補表に無かったもの（論文が外された等）を捨てた事実（件数なし・LU5）。
                "unit_refs_outside_sources": bool(info.get("outside_sources")),
            },
            ctx,
        )
        record_review_event(
            AUDIT_ENTITY_COURSE_TOPIC, course_id, "draft", "registered", user_id, metadata,
        )
    except Exception:  # noqa: BLE001 — 記帳の失敗で登録を止めない
        logger.warning("course registration decision_context not recorded: course=%s", course_id, exc_info=True)


def _project_topics_for_learner(data: dict) -> dict:
    """学習者向け DTO 用に ``topics[].units`` を ``kind`` / ``label`` だけへ射影したコピーを返す
    （設計書 §6.1 / KO10: 参照キー・unit_id・source を学習者に出さない）。保存データは変更しない。"""
    if not isinstance(data, dict):
        return data
    out = dict(data)

    def _project(topic):
        if not isinstance(topic, dict) or "units" not in topic:
            return topic
        projected = dict(topic)
        projected["units"] = learner_topic_units_projection(topic)
        return projected

    if isinstance(out.get("topics"), list):
        out["topics"] = [_project(t) for t in out["topics"]]
    # IK-0384: ``course_content_status`` はコース生成の内部記録（件数・document_id・
    # draft_errors・uncovered_sections(_dropped) など）を任意キーで積む free-form dict で、
    # 学習者に出すものではない（KO10 / 数値非表示）。学習画面が読めるのは状態の語だけ
    # （``course_content_state`` = ``status`` の trim 済み文字列）。教員向けの経路
    # （原稿スタジオの course-structure・PUT の応答）は射影しない。
    if "course_content_status" in out:
        _state = course_content_state(data)
        out["course_content_status"] = {"status": _state} if _state else {}
    if isinstance(out.get("chapters"), list):
        chapters = []
        for ch in out["chapters"]:
            if isinstance(ch, dict) and isinstance(ch.get("topics"), list):
                ch = dict(ch)
                ch["topics"] = [_project(t) for t in ch["topics"]]
            chapters.append(ch)
        out["chapters"] = chapters
    return out


def _split_symbol_concepts(concepts: list) -> tuple[list, list[str]]:
    """概念マップから記号を除き ``(残した概念, 除いた名前)`` を返す（案 E）。

    正本: ``docs/features/claim_concept_grounding_design.md`` §8 / CG6「記号は概念に
    しない」。判定は A層 P0-3 に委譲する共通述語 ``course_data.is_symbol_concept_name``
    のみで行い、ここで第2の正規表現・分野語のリストを持たない。

    - ``name`` が記号なら、その概念（と配下の ``children``）を概念マップから外す。
    - ``name`` は概念でも ``children[]`` に記号が混じっていれば、その子だけを外して
      概念自体は残す（除去の粒度を概念単位に丸めない）。
    - 除いた名前は捨てずに出現順・重複除去で返す（CG5「情報を落とさない」）。呼び出し側が
      ``data.excluded_symbol_concepts`` に残す。**学習者には出さない**。

    入力は mutate せず、新しい list / dict を返す。
    """
    kept: list = []
    excluded: list[str] = []

    def _exclude(name: object) -> None:
        token = str(name or "").strip()
        if token and token not in excluded:
            excluded.append(token)

    for concept in concepts or []:
        if not isinstance(concept, dict):
            # 想定外の形（文字列など）は判定だけ掛けて素通しする（情報を落とさない）。
            if is_symbol_concept_name(concept):
                _exclude(concept)
            else:
                kept.append(concept)
            continue
        if is_symbol_concept_name(concept.get("name")):
            _exclude(concept.get("name"))
            for child in concept.get("children") or []:
                _exclude(child)
            continue
        children = concept.get("children")
        if isinstance(children, list):
            kept_children = []
            for child in children:
                if is_symbol_concept_name(child):
                    _exclude(child)
                else:
                    kept_children.append(child)
            if len(kept_children) != len(children):
                concept = dict(concept)
                concept["children"] = kept_children
        kept.append(concept)

    return kept, excluded


@router.post("/courses", response_model=LearningCourseOut, status_code=201)
def create_course(
    body: CourseCreateRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseOut:
    """新しいコースを作成する。

    学ぶ単位の一級化 Phase 2: ①前提を同コース topic の ID 参照に（`resolve_prerequisite_topic_ids`、
    正規化題名の完全一致のみ）②`topics[].units` の handle を候補表で解決（候補に無い handle は
    捨てる）③候補が提示されていたときだけ登録を一括確定として `decision_context` 付きで記帳する。
    いずれも非LLM・保存前の決定論処理で、失敗しても登録は止めない。

    主張の概念接地（案 E）: ④学習者の概念マップから記号を除き、除いた名前を
    ``data.excluded_symbol_concepts`` に残す（`claim_concept_grounding_design.md` §8）。
    """
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

    # 案 E（claim_concept_grounding_design.md §8 / CG6）: 学習者の概念マップに記号を
    # 出さない。除いた名前は捨てずに残す（CG5）。決定論・LLM 0 回。
    data["concepts"], excluded_symbol_names = _split_symbol_concepts(data["concepts"])
    if excluded_symbol_names:
        data["excluded_symbol_concepts"] = excluded_symbol_names

    # P2-4: 前提を ID 参照に（入力を mutate せず新しい list を返す）。
    try:
        data["topics"] = resolve_prerequisite_topic_ids(data["topics"])
    except Exception:  # noqa: BLE001 — 解決できなくても名前は残る（LU1）
        logger.warning("prerequisite topic_id resolution failed: course=%s", course_id, exc_info=True)

    # P2-3 / P2-5: unit handle の解決と一括確定の材料。
    units_info: dict | None = None
    session = _pg_session()
    try:
        data["topics"], units_info = _bind_topic_units(
            session, data, user_id=current_user["id"]
        )
    except Exception:  # noqa: BLE001 — 候補表が読めなくても登録は止めない（LU8）
        logger.warning("unit handle resolution failed: course=%s", course_id, exc_info=True)
        for topic in data["topics"]:
            if isinstance(topic, dict) and "units" in topic:
                # 解決できなかった handle は意味を持たないので保存しない（学習者へ内部 ID を出さない）
                topic["units"] = []
    finally:
        session.close()

    save_course_data(
        current_user["id"],
        course_id,
        data,
        is_template=body.is_template,
        visibility=body.visibility,
        group_id=body.group_id if body.visibility == "group" else None,
        description=body.description,
    )

    # 候補が提示されていたときだけ「一括確定」として記帳する（LU7 / DC3）。
    if units_info and units_info.get("presented"):
        _record_course_registration(course_id, current_user["id"], units_info)

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
    # IK-0398: トピック・章の状態（completed / in_progress / locked）と progress_pct は
    # マスターコースの既定値（作成時の値）のままで、確認問題で本人が完了したトピックが
    # 反映されていなかった。本人の完了記録（learning_states.progress_data.completed_topics）
    # から読み時に導出して重ねる（保存データは変えない・新しい表なし）。取得に失敗しても
    # コースは返す（fail-open = 従来どおりマスターの値）。
    try:
        completed_ids = list(
            get_course_completion(current_user["id"], course_id, data).get("completed_topic_ids") or []
        )
    except Exception:
        logger.warning("Failed to read course completion for course view", exc_info=True)
        completed_ids = []
    return LearningCourseLayeredResponse(
        master_course=LearningCourseDetail(
            **_project_topics_for_learner(
                _overlay_learner_progress(_with_resolved_source_titles(data), completed_ids)
            )
        ),
        personal_layer=PersonalLayer(**personal),
    )


def _overlay_learner_progress(data: dict, completed_topic_ids: list[str]) -> dict:
    """本人の完了記録からトピック・章の状態と progress_pct を導出して重ねたコピーを返す（IK-0398）。

    規則（決定論・保存データは変更しない）:
      - 完了記録のあるトピック → ``completed``
      - その直後のトピック（フラット ``topics[]`` の順）が ``locked`` なら ``in_progress``
        （確認を終えたら次が開く、の表示を揃える。ロックは表示であって API は
        ロック中のトピックも開ける — 学習者を閉め出さない既存挙動は変えない）
      - 章: 全トピック完了 → ``completed`` / どれかが完了か進行中 → ``in_progress``。
        ``progress_pct`` は章内の完了トピックの割合（既存の DTO 項目を実値に揃えるだけ）
    完了記録が1件も無ければ何も変えない（マスターの値のまま）。
    """
    completed = {str(t) for t in (completed_topic_ids or []) if t}
    if not isinstance(data, dict) or not completed:
        return data
    out = dict(data)
    flat = [t for t in (data.get("topics") or []) if isinstance(t, dict)]
    status_by_id: dict[str, str] = {}
    previous_completed = False
    for topic in flat:
        topic_id = str(topic.get("id") or "")
        status = str(topic.get("status") or "locked")
        if topic_id in completed:
            status = "completed"
        elif previous_completed and status == "locked":
            status = "in_progress"
        if topic_id:
            status_by_id[topic_id] = status
        previous_completed = topic_id in completed

    def _with_status(topic):
        if not isinstance(topic, dict):
            return topic
        topic_id = str(topic.get("id") or "")
        if topic_id not in status_by_id:
            return topic
        return {**topic, "status": status_by_id[topic_id]}

    out["topics"] = [_with_status(t) for t in (data.get("topics") or [])]
    if isinstance(data.get("chapters"), list):
        chapters = []
        for ci, chapter in enumerate(data["chapters"]):
            if not isinstance(chapter, dict):
                chapters.append(chapter)
                continue
            chapter = dict(chapter)
            if isinstance(chapter.get("topics"), list):
                chapter["topics"] = [_with_status(t) for t in chapter["topics"]]
            members = [t for t in flat if t.get("chapter_index") == ci]
            if not members and isinstance(chapter.get("topics"), list):
                members = [t for t in chapter["topics"] if isinstance(t, dict)]
            statuses = [status_by_id.get(str(t.get("id") or ""), str(t.get("status") or "locked")) for t in members]
            if statuses:
                done = sum(1 for st in statuses if st == "completed")
                if done == len(statuses):
                    chapter["status"] = "completed"
                elif done or any(st == "in_progress" for st in statuses):
                    chapter["status"] = "in_progress"
                chapter["progress_pct"] = int(round(100 * done / len(statuses)))
            chapters.append(chapter)
        out["chapters"] = chapters
    return out


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
        # P2-R4: GET 射影で参照キーが落ちた units を素通しすると、往復1回で
        # 「学ぶ単位」の参照が消える。topic id キーで既存の units を温存する。
        data["topics"] = _preserve_topic_units(
            [t.model_dump() for t in body.topics], data.get("topics")
        )
    if body.concepts is not None:
        # P3-R8: 概念マップの記号除去は登録時（register_course）と同じ弁を通す
        # （案 E / CG6。PUT 経由でだけ記号が復活するのを防ぐ）。除いた名前は残す（CG5）。
        _kept_concepts, _excluded_symbols = _split_symbol_concepts(
            [c.model_dump() for c in body.concepts]
        )
        data["concepts"] = _kept_concepts
        if _excluded_symbols:
            data["excluded_symbol_concepts"] = _excluded_symbols
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


@router.post("/courses/{course_id}/enroll", response_model=LearningEnrollOut, status_code=201)
def enroll_course(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningEnrollOut:
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
                       COALESCE(is_published, false), COALESCE(is_template, false),
                       COALESCE(description, '')
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

    title, visibility, group_id, is_published, is_template = tuple(record)[:5]
    description = tuple(record)[5] if len(tuple(record)) > 5 else ""
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
    # IK-0369: 応答は一覧（GET /courses）の「受講中」行と同じ投影で埋める。id / title だけを
    # 埋めて他の列を既定値（非公開・受講不可）のまま返すと、公開コースに登録した直後の
    # 画面がそれを事実として読む。受講登録した後なので is_enrollable は一覧と同じく False。
    # IK-0385: is_enrollable=False を「受講できなかった」と読まれないよう、成立した事実を
    # enrolled / notice で添える（既存フィールドは不変・追加のみ）。
    return LearningEnrollOut(
        enrolled=True,
        notice=label_vocab.COURSE_ENROLLED_NOTICE,
        id=course_id,
        title=title or "",
        is_template=bool(is_template),
        is_published=bool(is_published),
        is_enrollable=False,
        visibility=visibility or "private",
        group_id=str(group_id) if group_id else None,
        description=description or "",
    )


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
#   - 教材内容の質問は誤爆コストの方が大きいため、UI参照語と問い形が両方揃っていても、
#     学術教材の内容語らしき語（_CONTENT_QUESTION_TERMS）が共起していれば偽に倒す
#     （例: 「この式はどう使うの」「運動方程式の使い方」）。
#   - **この語彙に特定分野（物理など）の用語を書かない**（このシステムは分野を限定
#     しない）。分野固有の語は、コースのカートリッジ ontology から読み時に足す
#     （:func:`_cartridge_content_terms`）。カートリッジが無い／読めない分野でも
#     下の分野非依存語だけで判定が成立する（フェイルソフト）。
#   - メッセージが長い（雑談・複合質問らしい）場合も偽にする（保守的に絞る）。
_HELP_CONTEXT_TERMS = (
    "画面", "ボタン", "操作", "アプリ", "この機能", "音声モード", "音声入力",
    "マイク", "ヘルプ", "メニュー", "使い方",
)
_HELP_QUESTION_FORMS = (
    "使い方", "どう使", "どうやって", "方法", "どこ",
)
# 分野非依存の「教材内容らしさ」語彙（学問一般の語のみ。分野固有語は書かない）。
_CONTENT_QUESTION_TERMS = (
    "式", "方程式", "定理", "法則", "証明", "導出", "定義", "公式",
    "理論", "概念", "仮定", "前提条件", "モデル", "計算", "関数", "係数",
    "変数", "近似", "観測", "実験", "論文",
    # IK-0476: 英語で問う受講者の学問一般語（分野語は書かない）。
    "equation", "theorem", "derivation", "definition", "theory", "hypothesis",
    "assumption", "approximation", "parameter", "observation", "measurement",
    "constraint", "the paper", "this paper",
)

#: コースデータから内容語を引くときの区切り（トピック題名「主結果：CMB・BAO・SN・宇宙シア」等）。
_COURSE_TERM_SPLIT_RE = re.compile(r"[\s：:・、，,／/（）()「」『』\[\]【】—\-–~〜]+")
_COURSE_TERM_LIMIT = 200


def _course_content_terms(course_data: dict | None) -> tuple[str, ...]:
    """コースのトピック題名・概念名から「教材内容語」を集める（IK-0476・決定論・非LLM）。

    分野語をコードに書かずに、分野未指定（cartridge_id 空）のコースでも一次判定が教材の
    語を知るための供給口。題名は区切り記号で割り、3文字以上の断片だけを使う（"SN" の
    ような短い略語は部分一致の誤判定を招くので使わない — ``_CARTRIDGE_TERM_MIN_LEN`` と同じ）。
    """
    if not isinstance(course_data, dict):
        return ()
    raw: list[str] = []
    for topic in iter_all_topics(course_data):
        raw.extend(_COURSE_TERM_SPLIT_RE.split(str(topic.get("title") or "")))
        for concept in topic.get("concepts") or []:
            raw.append(str(concept.get("name") if isinstance(concept, dict) else concept or ""))
    for concept in course_data.get("concepts") or []:
        raw.append(str(concept.get("name") if isinstance(concept, dict) else concept or ""))
    terms: list[str] = []
    for term in raw:
        term = term.strip()
        if len(term) < _CARTRIDGE_TERM_MIN_LEN or term in terms:
            continue
        terms.append(term)
        if len(terms) >= _COURSE_TERM_LIMIT:
            break
    return tuple(terms)

# カートリッジ由来の内容語を引くときの下限文字数（"SM" のような短い別名を
# 部分一致に使うと、無関係な文が内容語ありと誤判定されるため）。
_CARTRIDGE_TERM_MIN_LEN = 3
_CARTRIDGE_TERM_LIMIT = 200


@lru_cache(maxsize=16)
def _cartridge_content_terms(cartridge_id: str) -> tuple[str, ...]:
    """カートリッジ ontology から分野固有の「教材内容語」を集める（フェイルソフト）。

    分野名・分野語彙をコードにハードコードしないための供給口（開発ルール7
    「domain-specific なロジックを埋め込まず cartridge から読む」と同じ方針）。
    読めない・存在しないカートリッジでは空タプルを返し、呼び出し側は分野非依存語
    （:data:`_CONTENT_QUESTION_TERMS`）だけで判定する。

    ``cartridge_id`` が空のときは呼び出さないこと（``load_cartridge(None)`` は
    既定カートリッジへ縮退するため、無関係な分野の語彙を引いてしまう）。
    """
    cid = (cartridge_id or "").strip()
    if not cid:
        return ()
    try:
        cartridge = load_cartridge(cid)
    except Exception:  # noqa: BLE001 — 分野語彙は補助。読めなければ無しで続行する
        logger.debug("cartridge content terms unavailable: %s", cid, exc_info=True)
        return ()
    terms: list[str] = []

    def _add(value: object) -> None:
        text = str(value or "").strip()
        if not text or len(text) < _CARTRIDGE_TERM_MIN_LEN:
            return
        if text not in terms:
            terms.append(text)

    for concept_type in cartridge.ontology.concept_types:
        if isinstance(concept_type, dict):
            _add(concept_type.get("label_ja"))
            for example in concept_type.get("examples") or []:
                _add(example)
    for alias_entry in cartridge.ontology.aliases:
        if isinstance(alias_entry, dict):
            _add(alias_entry.get("canonical"))
            for alias in alias_entry.get("aliases") or []:
                _add(alias)
    return tuple(terms[:_CARTRIDGE_TERM_LIMIT])


def _is_usage_question(message: str, *, cartridge_id: str | None = None) -> bool:
    """メッセージが画面・システムの使い方についての質問かどうかを保守的に判定する。

    非LLM・同期（casual/音声バイパスより手前で評価するための決定論判定）。
    ``cartridge_id`` を渡すと、その分野の語彙も「教材内容らしさ」の判定に足す
    （分野語のハードコードを避けるための供給口。省略時は分野非依存語のみ）。
    """
    msg = (message or "").strip()
    if not msg or len(msg) >= 50:
        return False
    if any(term in msg for term in _CONTENT_QUESTION_TERMS):
        return False
    lowered = msg.lower()
    if any(term.lower() in lowered for term in _cartridge_content_terms(cartridge_id or "")):
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
        # 分野名をハードコードしない（コースごとに分野が異なる）。コース名は
        # プロンプト冒頭で提示済みなので、ここでは「このコースが扱う専門分野」と書く。
        "- DOMAIN_RAG: このコースが扱う専門分野の知識・概念に関する質問\n\n"
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
    source_context: str | None = None,
    explain_prerequisite: str | None = None,
) -> str:
    """学習相談・メタ質問・学習開始への応答を生成する（ルート②: ナビゲーター）。

    コース全体の構造と現在のトピックをベースに、学習アドバイスや導入メッセージを提供する。

    ``source_context`` は前提知識の3段解決（是正 F4）で解決した資料の抜粋
    （``[出典N]`` 付きの context block）。渡された場合だけ、説明を抜粋に基づかせ、
    抜粋外の内容は一般的な学術知識であることを明示させる（原則8: 出所の正直さ）。
    LLM コール数は渡しても渡さなくても1回のまま。

    ``explain_prerequisite`` は IK-0383: 学習者がその前提そのものの説明を求めたとき
    （逆質問への「いいえ・教えて」、前提名を挙げた問い）に渡す前提名。渡したときは
    ナビゲーターの案内（全体像・構成要素の列挙・「解説はまだしない」）ではなく、
    その前提の説明を書かせる（問いに答えないまま案内を返さない）。
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

    source_block = ""
    if source_context:
        # IK-0430: 出典マーカーの指示は、抜粋に番号付き出典が実際にあるときだけ出す
        # （コース内トピックの教材だけの抜粋に「[出典1] を挿入せよ」と指示すると、
        # 番号の無い抜粋に対して捏造を誘う）。
        if _CITATION_MARKER_RE.search(source_context):
            citation_rule = (
                "  - 抜粋を参照したときは、付された番号付き出典マーカー `[出典N]` を"
                "本文に自然に挿入すること（番号は提示されたものに対応させ、独自の番号や"
                "『書籍名』形式は使わないこと）。\n"
            )
        else:
            citation_rule = "  - 抜粋には番号付き出典が無いので、出典マーカーは書かないこと。\n"
        source_block = (
            f"{source_context}\n\n"
            "■ 上の抜粋の扱い（出所の正直さ）:\n"
            "  - 前提知識の説明は、まず上の抜粋に基づいて書くこと。\n"
            + citation_rule
            + "  - 抜粋に無い内容を補ったときは、資料由来ではないことが読み手に分かるように書くこと。\n\n"
        )

    explain_target = (explain_prerequisite or "").strip()
    if explain_target:
        prompt = (
            f"あなたは「{course_title}」の学習をサポートする教授です。\n"
            f"学生は現在「{topic_title}」のトピックを学習しており、その前提知識"
            f"「{explain_target}」の説明を求めています。\n\n"
            f"{persona_block}"
            f"{source_block}"
            f"学生からのメッセージ: {message}\n\n"
            f"前提知識「{explain_target}」そのものを、次の構成で説明してください:\n"
            "1. 【ひとことで】何をする考え方・方法なのかを1〜2文で述べる。\n"
            "2. 【しくみ】考え方の要点を具体的に説明する（必要なら数式を $...$ で示してよい）。\n"
            f"3. 【このトピックとのつながり】「{topic_title}」でこの前提がどう使われるかを1〜2文で述べる。\n\n"
            "※注意: 学生のメッセージに具体的な問いが含まれていれば、それに必ず答えること。\n"
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
            logger.warning("Prerequisite explanation LLM call failed, returning fallback")
            return f"「{explain_target}」の説明を生成できませんでした。"

    prompt = (
        f"あなたは「{course_title}」の学習をサポートするナビゲーター教授です。\n"
        f"学生は現在「{topic_title}」のトピックを学習しています。\n\n"
        f"{persona_block}"
        f"{topics_block}"
        f"{concepts_block}"
        f"{prereqs_block}"
        f"{source_block}"
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


# ---------------------------------------------------------------------------
# 前提知識の3段解決（是正 F4 / 六つのレンズ レンズ6 提案6、2026-09-10）
# ---------------------------------------------------------------------------
#
# 従来、前提知識の説明（LEARNING_ADVICE の前提確認分岐）は RAG を通らない LLM 説明で
# `content_grounding` が None のまま返り、フロントは出所バッジを描かなかった
# （＝一番あやふやな回答が一番確からしく見える）。ここでは解決を段階化する:
#   ① 同コースの topic に一致する（コース内の教材）
#   ② 本人が閲覧できる document のチャンク（既存 RAG と同じ
#      `search_chunks_with_metadata(..., allowed_document_ids=...)` を使う。
#      コース sources 外のヒットは既存判定どおり `other_material` になる）
#   ③ どこにも無ければ LLM の説明を返すが `content_grounding="model_generated"` を必ず設定し、
#      閉世界の事実文（SL1 継承）を添える。
# LLM の追加コールは無い（②は検索1回=通常の RAG ターンと同じ、③は既存 advice 経路）。

#: 解決できなかった前提について学習者に告げる固定文（SL1 の閉世界語彙）。
#: 言えるのは「このコーパスの中には資料が無い」だけで、分野レベルの不在
#: （分野で扱われていない・誰も書いていない）は言わない — 台帳・コーパスの
#: 射影であって分野の射影ではない。
PREREQUISITE_CLOSED_WORLD_FACT = "このコーパスの中には、この前提を扱う資料がありません。"

#: 前提知識の解決で LLM に渡す抜粋の見出し（RAG 本経路の見出しとは別物）。
_PREREQUISITE_CONTEXT_HEADING = "## この前提知識に関連する資料の抜粋"


def _prerequisite_terms(message: str, topic_info: dict | None) -> list[str]:
    """解決対象の前提知識名を決定論的に取り出す（現在トピックの `prerequisites`）。

    発話に名前が含まれているものを優先して並べ替えるだけで、AI に推定させない。
    """
    terms: list[str] = []
    for prereq in ((topic_info or {}).get("prerequisites") or []):
        name = prereq.get("name", prereq) if isinstance(prereq, dict) else prereq
        name = str(name or "").strip()
        if name and name not in terms:
            terms.append(name)
    msg = message or ""
    mentioned = [t for t in terms if t and t in msg]
    rest = [t for t in terms if t not in mentioned]
    return mentioned + rest


#: 教材本文の埋め込みのうち、LLM に渡しても中身の無い参照（出典・部品・主張のチップ）。
#: 図・数式は ``scrub_internal_placeholders`` が「（図）」「（数式）」の事実語に置き換える。
_EXCERPT_REFERENCE_EMBED_RE = re.compile(
    r"!\[\[\s*(?:source|component|claim):[^\]\n]*\]\]", re.IGNORECASE
)


def _scrub_excerpt_embeds(text: str) -> str:
    """前提説明のプロンプトへ載せる抜粋から、表示用の埋め込み記法を外す（IK-0430）。

    ``![[figure:<uuid>]]`` → 「（図）」、``![[equation:…]]`` / ``[[FORMULA_N]]`` →
    「（数式）」（``core.text_hygiene`` の正本）、``![[source:…]]`` 等のチップ参照は取り除く。
    描画には使わない（表示は各画面の解決器が正本）。
    """
    # 生成時の決定論付録（「### この節で参照する図」+ 埋め込み行）は本文ではないので先に外す
    # （付録の形の判定は course_content_builder が正本。埋め込みを置き換える前に通す）。
    cleaned = strip_generated_reference_appendix(str(text or ""))
    cleaned = scrub_internal_placeholders(cleaned)
    return _EXCERPT_REFERENCE_EMBED_RE.sub("", cleaned)


def _resolve_prerequisite_context(
    user_id: str,
    course_data: dict,
    terms: list[str],
    *,
    max_search_terms: int = 3,
    citation_numbers: "_SessionCitationNumbers | None" = None,
) -> dict:
    """前提知識の①②を解決し、context block / 出典 / grounding / 未解決名を返す。

    ``citation_numbers`` は会話の出典採番器（IK-0432）。省略時はこの往復だけの新しい
    採番器（1 から）を使う。

    Returns
    -------
    dict
        ``{"context_block", "cited_sources", "overall_tier", "content_grounding",
        "resolved", "unresolved"}``。検索は1回だけ（前提名を連結したクエリ）。
    """
    resolved: list[str] = []
    blocks: list[str] = []
    cited_sources: list[dict] = []
    has_course_topic_material = False
    if citation_numbers is None:
        citation_numbers = _SessionCitationNumbers()

    if terms:
        course_material_ids = set(course_source_material_ids(course_data))

        # ① 同コースの topic（章ネストも走査する。走査は course_data アクセサに委譲）
        topics_by_title: dict[str, dict] = {}
        for topic in iter_all_topics(course_data):
            title = str(topic.get("title") or "").strip().casefold()
            if title and title not in topics_by_title:
                topics_by_title[title] = topic
        for term in terms:
            topic = topics_by_title.get(term.strip().casefold())
            material = _topic_student_material(topic) if topic else ""
            if material:
                blocks.append(
                    f"[コース内トピック『{topic.get('title') or term}』の教材]\n"
                    f"{_scrub_excerpt_embeds(material)[:3000]}"
                )
                has_course_topic_material = True
                if term not in resolved:
                    resolved.append(term)

        # ② 本人が閲覧できる document のチャンク（可視性は allowed_document_ids で fail-closed）
        pending = [t for t in terms if t not in resolved]
        if pending:
            allowed_document_ids = list_visible_document_ids(user_id)
            chunk_results = search_chunks_with_metadata(
                "、".join(pending[:max_search_terms]),
                top_k=6,
                allowed_document_ids=allowed_document_ids,
            )
            matched_text: list[str] = []
            for r in chunk_results:
                if float(r.get("score") or 0.0) < 0.30:
                    continue
                text = str(r.get("text") or "")
                # 出典番号は会話の中で固定（IK-0432。本体 RAG と同じ採番器・同じ組み立て）。
                _source = _adopted_source_entry(citation_numbers, r, course_material_ids)
                cited_sources.append(_source)
                blocks.append(
                    f"[出典{_source['index']}] 『{r.get('source_title', '')}』\n"
                    f"{sanitize_source_text_for_prompt(_scrub_excerpt_embeds(text))}"
                )
                matched_text.append(text)
            # 「その前提を扱っている」の判定は逐語一致だけ（決定論・追加コストなし）。
            # ベクトル近傍で引けただけの資料を「この前提を扱っている」とは言わない。
            haystack = " ".join(matched_text).casefold()
            if haystack:
                for term in pending:
                    if term.strip().casefold() in haystack and term not in resolved:
                        resolved.append(term)

    unresolved = [t for t in terms if t not in resolved]

    overall_tier = aggregate_overall_tier([s["tier"] for s in cited_sources])
    if has_course_topic_material:
        overall_tier = tier_floor(overall_tier, TIER_SOURCE)

    if has_course_topic_material or any(s["origin"] == "course_material" for s in cited_sources):
        content_grounding = "course_material"
    elif cited_sources:
        content_grounding = "other_material"
    else:
        content_grounding = "model_generated"

    context_block = None
    if blocks:
        # 信頼境界（docs/architecture/trust_boundary_pdf_input.md）: 抜粋は第三者が
        # 書いた untrusted 入力。区切り（ラベル + `---`）に加えて固定文を前置する。
        context_block = (
            _PREREQUISITE_CONTEXT_HEADING + "\n"
            + UNTRUSTED_SOURCE_NOTICE + "\n\n"
            + "\n---\n".join(blocks)
        )

    return {
        "context_block": context_block,
        "cited_sources": cited_sources,
        "overall_tier": overall_tier,
        "content_grounding": content_grounding,
        "resolved": resolved,
        "unresolved": unresolved,
    }


def _prerequisite_closed_world_note(unresolved: list[str]) -> str:
    """解決できなかった前提についての閉世界事実文（数値は出さない）。"""
    names = [n for n in (unresolved or []) if (n or "").strip()]
    if not names:
        return ""
    listed = "、".join(f"「{n}」" for n in names[:3])
    return f"{PREREQUISITE_CLOSED_WORLD_FACT}（対象: {listed}）"


# ---------------------------------------------------------------------------
# 前提確認の逆質問への答え・前提そのものへの問い（IK-0383）
# ---------------------------------------------------------------------------
#
# 前提確認の逆質問（`check_prerequisites` の介入文 + はい/いいえ）のあと、学習者が
# ボタンではなく「いいえ、DCF法から教えてください」と打つと、`check_prerequisites` の
# 言及判定（前提の**名前全体**が発話に含まれるか）に掛からず、同じ逆質問がそのまま
# 返っていた（3回続いた実例あり）。「DCF法とは何ですか」のような前提そのものへの問いも
# 同じ理由で逆質問に吸い込まれ、問いに答えないままになっていた。
# ここでは決定論・非LLMで、①直前の往復が逆質問だったか ②発話が前提そのものの説明を
# 求めているか を判定し、説明の3段解決（是正 F4）へ流す。**逆質問を2回続けて出さない**。
# 「理解している」の記帳は従来どおり `check_prerequisites` 側だけが行う（否定形は記帳しない）。

#: `services.check_prerequisites` が組み立てる逆質問の定型部分（直前の往復が逆質問だったかの判定用）。
#: 文言の一致は test_prerequisite_routing.py が固定する（片方だけ変えると判定が外れる）。
PREREQUISITE_GATE_MARKER = "を理解するには、まず以下の前提知識を押さえる必要があります"
_PREREQUISITE_GATE_FIRST_RE = re.compile(r"まず「(.+?)」から説明します")

#: 「理解していない・教えてほしい」の答え（逆質問の直後に効く）。
_PREREQ_NEGATIVE_MARKERS = (
    "いいえ", "いや、", "わからない", "分からない", "わかりません", "分かりません",
    "知らない", "知りません", "理解していません", "理解できていません", "自信がない",
    "自信がありません",
)
#: 前提そのものの説明を求める言い方（逆質問が無くても、前提名と併せて効く）。
_PREREQ_TEACH_MARKERS = (
    "教えて", "説明して", "から説明", "から学", "とは", "って何", "ってなに", "ってどういう",
    "わからない", "分からない", "わかりません", "分かりません",
)
_PREREQ_HEAD_SPLIT_RE = re.compile(r"による|における|について|の|と|を|が|は|で|\s")
#: IK-0424: 英語の「理解していない・説明してほしい」（逆質問の直後に効く）。小文字化して語で見る。
_PREREQ_NEGATIVE_EN_RE = re.compile(
    r"^\s*no\b|\bi\s+don'?t\b|\bi\s+do\s+not\b|\bnot\s+familiar\b|\bplease\s+explain\b"
)
#: IK-0424: 英語の説明要求（前提名・前提の内容語と併せて効く）。
_PREREQ_TEACH_EN_RE = re.compile(r"\bexplain\b|\bwhat\s+is\b|\bwhat's\b|\btell\s+me\b")
#: IK-0423: 前提の内容語が2つ以上重なる発話を「前提そのものへの問い」と読むときの、問いの形の目印。
_PREREQ_QUESTION_MARKERS = ("？", "?", "どう", "なぜ", "何", "違", "とは")
_PREREQ_QUESTION_EN_RE = re.compile(r"\b(?:why|how|what|which|explain)\b")
#: IK-0423: 前提名の内容語（漢字・カタカナの2字以上の連なり / 英字2字以上の語）。
_PREREQ_CONTENT_WORD_RE = re.compile(r"[\u3400-\u9fff々〆ヵヶ]{2,}|[\u30a1-\u30faー]{2,}|[A-Za-z][A-Za-z0-9]+")
#: 内容語が何個重なれば「前提そのものへの問い」と読むか（前提名の言い換えを拾う）。
_PREREQ_CONTENT_OVERLAP_MIN = 2


def _prerequisite_display_names(topic_info: dict | None, course_data: dict | None) -> list[str]:
    """現在トピックの前提の名前（と topic_id で結ばれた題名）を保存順で返す。"""
    topics_by_id = {
        str(t.get("id")): t for t in iter_all_topics(course_data or {}) if isinstance(t, dict) and t.get("id")
    }
    names: list[str] = []
    for prereq in ((topic_info or {}).get("prerequisites") or []):
        if isinstance(prereq, dict):
            candidates = [str(prereq.get("name") or "").strip()]
            linked = topics_by_id.get(str(prereq.get("topic_id") or "").strip())
            if linked:
                candidates.append(str(linked.get("title") or "").strip())
        else:
            candidates = [str(prereq or "").strip()]
        for name in candidates:
            if name and name not in names:
                names.append(name)
    return names


def _prerequisite_mentioned(message: str, names: list[str]) -> str | None:
    """発話が言及している前提の名前を返す（名前全体か、名前の頭の語 = 3文字以上）。"""
    msg = (message or "").casefold()
    if not msg:
        return None
    for name in names:
        if name.casefold() in msg:
            return name
    for name in names:
        head = _PREREQ_HEAD_SPLIT_RE.split(name, maxsplit=1)[0].strip()
        if len(head) >= 3 and head != name and head.casefold() in msg:
            return name
    return None


def _prerequisite_content_overlap(message: str, names: list[str]) -> str | None:
    """前提名の内容語が発話に2つ以上現れるなら、その前提名を返す（IK-0423・決定論）。

    内容語は漢字・カタカナの2字以上の連なりと英字2字以上の語。英字の語は語境界付きの照合
    （``alias_matching`` の正本。``GR`` が ``gravity`` の途中に当たらない）、漢字・カタカナは
    部分文字列で見る。略語の展開（MG ≈ 修正重力）は見ない — 重なりは原文の語だけで数える。
    """
    msg = message or ""
    if not msg:
        return None
    from episteme_graph.agents.alias_matching import text_mentions_alias

    for name in names:
        words: list[str] = []
        for word in _PREREQ_CONTENT_WORD_RE.findall(name):
            if word not in words:
                words.append(word)
        if len(words) < _PREREQ_CONTENT_OVERLAP_MIN:
            continue
        hits = 0
        for word in words:
            if word.isascii():
                if text_mentions_alias(msg, word):
                    hits += 1
            elif word in msg:
                hits += 1
        if hits >= _PREREQ_CONTENT_OVERLAP_MIN:
            return name
    return None


def _is_question_shaped(message: str) -> bool:
    """問いの形の発話か（？・どう・なぜ・何・違い・とは / why・how・what・which・explain）。"""
    msg = message or ""
    return any(m in msg for m in _PREREQ_QUESTION_MARKERS) or bool(
        _PREREQ_QUESTION_EN_RE.search(msg.casefold())
    )


def _prerequisite_gate_asked_in_history(history: list[dict] | None, topic_title: str) -> bool:
    """このトピックの逆質問が、履歴のどこかの assistant ターンに既にあるか（IK-0422）。

    逆質問は (トピック, セッション) につき1回だけにする。直前の往復だけを見ると、逆質問の
    あとに1往復挟んだ次の問いでまた同じ逆質問が返っていた。
    """
    for turn in history or []:
        if not isinstance(turn, dict) or turn.get("role") != "assistant":
            continue
        content = str(turn.get("content") or "")
        if PREREQUISITE_GATE_MARKER in content and f"「{topic_title}」" in content:
            return True
    return False


def _is_mostly_latin(text: str) -> bool:
    """ラテン文字が文字の大半を占める発話か（IK-0424。_answer_language_line と同じ基準）。"""
    body = str(text or "")
    latin = sum(1 for ch in body if ("a" <= ch <= "z") or ("A" <= ch <= "Z"))
    cjk = sum(1 for ch in body if "\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff")
    return latin >= 8 and latin > cjk * 4


def _is_kana_kanji_free(text: str) -> bool:
    """かな・漢字を1字も含まず、ラテン文字を含む発話か（IK-0450）。

    定型文の言語を選ぶ目印。短い定型句（"Thanks!" / "Can you tell me my score?"）は
    ``_is_mostly_latin`` の 8 文字基準では拾えないため、こちらを使う。
    """
    body = str(text or "")
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", body):
        return False
    return bool(re.search(r"[A-Za-z]", body))


def _history_has_gate_skipped_notice(history: list | None) -> bool:
    """履歴の assistant ターンに「前提の確認はまだ記録していません」の1行が既にあるか（IK-0446）。"""
    patterns = []
    for template in (
        label_vocab.PREREQUISITE_GATE_SKIPPED_NOTICE,
        label_vocab.PREREQUISITE_GATE_SKIPPED_NOTICE_EN,
    ):
        head, _, tail = template.partition("{prerequisite}")
        patterns.append(re.compile(re.escape(head) + r".{1,200}?" + re.escape(tail)))
    for turn in history or []:
        if not isinstance(turn, dict) or turn.get("role") != "assistant":
            continue
        content = str(turn.get("content") or "")
        if any(p.search(content) for p in patterns):
            return True
    return False


def _is_bare_yes(message: str) -> bool:
    """逆質問への素の「はい / yes」（IK-0424。逆質問の直後だけ理解の答えとして読む）。"""
    normalized = re.sub(r"[\s。．.!！、,]+", "", (message or "").casefold())
    return normalized in {"yes", "yeah", "yep", "はい"}


def _previous_turn_was_prerequisite_gate(history: list[dict] | None, topic_title: str) -> str | None:
    """直前の assistant ターンがこのトピックの逆質問なら、その逆質問が挙げた最初の前提名を返す。

    該当しなければ None。前提名が読めない逆質問は空文字を返す（「逆質問だった」事実は残す）。
    """
    for turn in reversed(history or []):
        if not isinstance(turn, dict) or turn.get("role") != "assistant":
            continue
        content = str(turn.get("content") or "")
        if PREREQUISITE_GATE_MARKER in content and f"「{topic_title}」" in content:
            match = _PREREQUISITE_GATE_FIRST_RE.search(content)
            return match.group(1).strip() if match else ""
        return None
    return None


def _prerequisite_followup(
    message: str,
    history: list[dict] | None,
    topic_info: dict | None,
    topic_title: str,
    course_data: dict | None,
) -> tuple[str | None, str | None]:
    """前提確認まわりの発話を分類する（決定論・非LLM）。

    Returns
    -------
    (kind, target)
        ``kind`` は ``"explain"``（前提そのものの説明へ = 3段解決）/ ``"after_gate"``
        （直前が逆質問だった。逆質問を繰り返さない）/ ``"gated_before"``（このトピックの
        逆質問が履歴のもっと前にある。出し直さず、事実文1行を添えて答える = IK-0422）/
        ``None``（従来どおり）。
        ``target`` は説明する前提の名前（``"explain"`` のときだけ）。
    """
    names = _prerequisite_display_names(topic_info, course_data)
    if not names:
        return None, None
    msg = message or ""
    lowered = msg.casefold()
    gate_first = _previous_turn_was_prerequisite_gate(history, topic_title)
    mentioned = _prerequisite_mentioned(msg, names)
    overlap = _prerequisite_content_overlap(msg, names)
    acknowledged = _is_explicit_prerequisite_acknowledgement(msg)
    negative = any(m in msg for m in _PREREQ_NEGATIVE_MARKERS) or bool(
        _PREREQ_NEGATIVE_EN_RE.search(lowered)
    )
    teach = any(m in msg for m in _PREREQ_TEACH_MARKERS) or bool(_PREREQ_TEACH_EN_RE.search(lowered))
    if gate_first is not None:
        if acknowledged or _is_bare_yes(msg):
            return "after_gate", None
        if negative or teach or mentioned or overlap:
            return "explain", mentioned or overlap or gate_first or names[0]
        return "after_gate", None
    if mentioned and teach and not acknowledged:
        return "explain", mentioned
    # IK-0423: 前提名の言い換え（内容語が2つ以上重なる問い）も前提そのものへの問いとして読む。
    if overlap and (teach or _is_question_shaped(msg)) and not acknowledged:
        return "explain", overlap
    # IK-0422: 逆質問は (トピック, セッション) につき1回。履歴のどこかに既にあれば出し直さない。
    if _prerequisite_gate_asked_in_history(history, topic_title):
        return "gated_before", None
    return None, None


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
   英語で回答するときは、この目印を `[Ask more about X]` の形式で書いてください。

**フォーマット要件:**
- 数式は必ず LaTeX 記法で記述（インラインは $...$、ディスプレイは $$...$$）
- 教材を参照した場合は、コンテキストに付された番号付き出典マーカー `[出典1]` `[出典2]` … を本文に自然に挿入して言及すること。番号は提示された出典に対応させ、独自の番号や『書籍名』形式は使わないこと。
- コンテキストに番号付き出典（`[出典1]` …）が1つも無い場合は、出典マーカーを一切書かないこと。{persona_block}"""


def _get_casual_teacher_system_prompt(
    domain: str,
    response_persona: str | None = None,
    *,
    spoken: bool = True,
) -> str:
    """カジュアル対話モード（気軽に話せる先生）のシステムプロンプトを生成する。

    入口統合 Phase 1（``docs/features/learning_chat_entry_unification_design.md``
    §4.4）で**様相（軽い調子）と伝達形式（読み上げ）を分離**した。畳まれていた
    2つのうち、様相（相づち・聞き返し・採点しない）は両方で共通で、伝達形式だけが
    ``spoken`` で変わる。

    - ``spoken=True``（既定・ハンズフリー音声会話。**本文は従来のまま**）:
      2〜4文の短い話し言葉・記号なし・LaTeX なし。
    - ``spoken=False``（テキストの casual_light）: 軽い調子は保ったまま
      **LaTeX と出典マーカー ``[出典N]`` を許可**する（数式を言葉に潰すのは
      テキストでは劣化になる）。``[ACTION_BUTTON: ...]`` 等のシステム記法は
      引き続き禁止（気軽な会話に UI 遷移を差し込まない）。

    根拠の一線（教材コンテキスト優先・断定回避）はどちらでもチューターモードと同じ。
    """
    domain_label = domain.strip() if domain.strip() else "このコースの専門分野"
    persona_instruction = persona_prompt(response_persona, target="response")
    persona_block = f"\n\n**口調設定:**\n{persona_instruction}" if persona_instruction else ""
    if spoken:
        _delivery_rule = """1. 【会話調】音声で読み上げられます。1回の応答は2〜4文の短い話し言葉にしてください。
   箇条書き・見出し・記号・絵文字は使わないでください。"""
        _format_rule = """5. 【出さないもの】数式の羅列・LaTeX・出典番号マーカー・`[ACTION_BUTTON: ...]` などの
   システム記法は一切出力しないでください。数式が必要なら言葉で言い換えてください。"""
    else:
        _delivery_rule = """1. 【会話調】文字で読まれます。1回の応答は短め（3〜6文程度）の話し言葉にしてください。
   見出しや長い箇条書きで講義調にせず、立ち話の雰囲気を保ってください。"""
        _format_rule = """5. 【書き方】数式が要るところは LaTeX（インラインは $...$、ディスプレイは $$...$$）で
   そのまま書いてかまいません。教材のコンテキストに番号付き出典（`[出典1]` …）があれば、
   言及したところに自然に添えてください（無い場合は出典マーカーを書かないこと）。
   `[ACTION_BUTTON: ...]` などのシステム記法は出力しないでください。"""
    return f"""あなたは{domain_label}が大好きで、学生と雑談するのが楽しみな「気軽に話せる先生」です。
研究室の廊下やゼミ後の立ち話のように、教材で扱っている題材について肩の力を抜いて一緒に面白がってください。

**会話のルール:**
{_delivery_rule}
2. 【一緒に面白がる】採点や訂正を急がず、学生の言葉をまず受け止めてください。
   「たしかにそう見えるよね」「いいところに気づいたね」のような相づちから入って構いません。
3. 【聞き返す】ときどき「きみはどう思う?」「どこが引っかかった?」と軽く聞き返し、
   学生が自分の言葉で話す余地を残してください。毎回はしつこいので2〜3往復に1回程度。
4. 【根拠は正直に】提供される「教材からのコンテキスト」があればそれに沿って話してください。
   教材に無い話題は、想像や一般論であることが伝わる言い方（「たぶん」「一般には」）で話してください。
{_format_rule}{persona_block}"""


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
   英語で返答するときは 〔鏡〕You read it as "＜学生の直前の発話からの逐語引用＞" — is that right?〔/鏡〕
   の形でもかまいません（引用符は「」でも "" でも、中身は学生の発話の逐語引用にしてください）。
   〔鏡〕の中には論文の内容・一般知識・教科書的な正解を持ち込まず、学生の発話の言い直しだけを
   書いてください。学生の能力・傾向・人物像について述べてはいけません。
   〔鏡〕…〔/鏡〕 は返答の先頭に置いてください。鏡の前に前置き文や予告文（「次に、あなたの
   言い直しについてです。」等）を書かず、鏡の後に本文を続けてください（鏡で返答を終えないで
   ください）。ルール4で先に質問へ答える場合だけ、その答えの直後に鏡を置いてください。
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


# ---------------------------------------------------------------------------
# 出典番号を会話の中で安定させる（IK-0432）
# ---------------------------------------------------------------------------
#
# 従来は毎ターン採用した出典を 1..k の連番で振り直していた。履歴に残った過去の回答の
# ``[出典2]`` と今回の ``[出典2]`` が別のチャンクを指し、プロンプトに再注入される履歴の
# 番号とも食い違っていた。番号は (course, topic) の会話ごとに固定する:
#   ①その会話で既に番号を持つチャンクは同じ番号を使う（最初に振った番号）
#   ②新しいチャンクはこれまでの最大番号の続きから振る
# 検索（top_k・閾値）は変えない。変えるのは番号の振り方だけ。

#: 保存済み履歴から assistant へ戻す描画メタのキー（``persist_chat_history`` の assistant_meta）。
_HISTORY_META_KEYS = ("sources", "overall_tier", "content_grounding", "manual_citations")


def _history_meta_match_key(content: object) -> str:
    """履歴の assistant 本文を突き合わせるキー（表示用の注意書きを剥がし、空白を詰める）。"""
    text = str(content or "").strip()
    notice = out_of_source_notice()
    if text.startswith(notice):
        text = text[len(notice):].strip()
    # IK-0444: 保存本文はドリルダウンのマーカー込み（``[〇〇について詳しく聞く]``）で、
    # 応答で返す本文はマーカーを抜いた形。送り返された本文と突き合うよう両方から抜く。
    text, _ = extract_inline_actions(text)
    return re.sub(r"\s+", " ", text)


def _rehydrate_history_sources(history: list | None, stored: list | None) -> list:
    """クライアントが描画メタを落として送り返した assistant ターンに、保存済みのメタを戻す。

    UI は ``sources`` 付きで履歴を送るが、``{role, content}`` だけを送るクライアントもある。
    その往復を保存すると ``sources`` が失われ、次の往復で番号の対応が辿れなくなる。
    本文（注意書きを剥がした形）が一致する保存済み assistant ターンのメタだけを戻す
    （推測で結び付けない。一致しなければそのまま）。入力は変更しない。
    """
    turns = list(history or [])
    if not turns or not stored:
        return turns
    meta_by_content: dict[str, dict] = {}
    for turn in stored:
        if not isinstance(turn, dict) or turn.get("role") != "assistant":
            continue
        meta = {k: turn[k] for k in _HISTORY_META_KEYS if turn.get(k) not in (None, "", [], {})}
        key = _history_meta_match_key(turn.get("content"))
        if meta and key:
            meta_by_content.setdefault(key, meta)
    if not meta_by_content:
        return turns
    out: list = []
    for turn in turns:
        if (
            isinstance(turn, dict)
            and turn.get("role") == "assistant"
            and not turn.get("sources")
        ):
            meta = meta_by_content.get(_history_meta_match_key(turn.get("content")))
            if meta:
                merged = dict(meta)
                merged.update({
                    k: v for k, v in turn.items()
                    if not (k in _HISTORY_META_KEYS and v in (None, "", [], {}))
                })
                turn = merged
        out.append(turn)
    return out


class _SessionCitationNumbers:
    """(course, topic) の会話で出典番号を固定する採番器（IK-0432・決定論・非LLM）。

    IK-0467: 範囲は (course, topic) の会話（会話履歴の保存単位・学習画面の会話区画と同じ）。
    トピックを跨いで番号を揃えないのは設計どおり（docs/backend/rag-chat.md §②）。

    IK-0466: 番号は単調で、一度どこかのチャンクに出した番号は別のチャンクへ振り直さない。
    読み込む対応表の番号の最大値を次の番号の起点にし、同じ番号を別のチャンクが持って
    いる入力（旧来の振り直しの名残）は、先に見た対応だけを採る。
    """

    def __init__(self, *histories: list | None) -> None:
        self._by_chunk: dict[str, int] = {}
        self._owner: dict[int, str] = {}
        self._max = 0
        self._used: set[int] = set()
        for history in histories:
            for turn in history or []:
                if not isinstance(turn, dict) or turn.get("role") != "assistant":
                    continue
                # IK-0444: 会話全体の「チャンク → 番号」の控え（サーバが書く累積の対応表）。
                # クライアントが履歴を窓で切ったり sources を落として送り返したりしても、
                # 保存済みの最新 assistant ターンにこの控えが残っていれば対応を失わない。
                citation_map = turn.get(CITATION_MAP_KEY)
                if isinstance(citation_map, dict):
                    for chunk_id, raw_index in citation_map.items():
                        self._reserve(chunk_id, raw_index)
                for source in turn.get("sources") or []:
                    if isinstance(source, dict):
                        # 最初に振った番号を正とする（旧来の振り直しで同じチャンクに複数の
                        # 番号が付いた履歴でも、最初の対応に揃える）。
                        self._reserve(source.get("chunk_id"), source.get("index"))

    def _reserve(self, chunk_id: object, raw_index: object) -> None:
        try:
            index = int(raw_index or 0)
        except (TypeError, ValueError):
            return
        if index <= 0:
            return
        # 番号そのものは、対応が採れなくても「出したことがある」として起点に数える。
        self._max = max(self._max, index)
        cid = str(chunk_id or "").strip()
        if not cid or cid in self._by_chunk or index in self._owner:
            return
        self._by_chunk[cid] = index
        self._owner[index] = cid

    def assign(self, chunk_id: object) -> int:
        """このターンで採用した出典に番号を振る（同じターン内で番号は重複しない）。"""
        cid = str(chunk_id or "").strip()
        index = self._by_chunk.get(cid) if cid else None
        if index is None or index in self._used:
            self._max += 1
            index = self._max
            if cid and cid not in self._by_chunk:
                self._by_chunk[cid] = index
                self._owner[index] = cid
        self._used.add(index)
        return index

    def mapping(self) -> dict[str, int]:
        """会話全体の「チャンク → 番号」（最初に振った番号）。履歴に控えとして焼き込む。"""
        return dict(self._by_chunk)


#: assistant ターンに焼き込む累積の対応表のキー（IK-0444）。正本は services（IK-0466）。
CITATION_MAP_KEY = _SERVICES_CITATION_MAP_KEY


def _carry_citation_map(history: list | None, numbers: "_SessionCitationNumbers") -> list:
    """送られてきた履歴の最後の assistant ターンに、会話全体の対応表の控えを載せる（IK-0444）。

    どの経路の ``persist_chat_history`` も ``body.history`` を丸ごと書き直すので、ここで
    控えを載せておけば、クライアントが sources を落とした・窓で切った履歴を送っても、
    保存後の行に対応表が残る。入力は変更しない（載せるターンだけ複製する）。
    """
    turns = list(history or [])
    mapping = numbers.mapping()
    if not mapping:
        return turns
    for i in range(len(turns) - 1, -1, -1):
        turn = turns[i]
        if isinstance(turn, dict) and turn.get("role") == "assistant":
            carried = dict(turn)
            carried[CITATION_MAP_KEY] = mapping
            turns[i] = carried
            break
    return turns


def _prefer_topic_documents(results: list[dict], topic_doc_ids: set[str]) -> list[dict]:
    """トピックの論文のチャンクを先に、その中は類似度の高い順に並べる（IK-0472・純関数）。

    ``(採用の下限未満, トピックの論文でない, -score)`` の安定ソート。採用の下限（0.30）を
    満たすチャンクを先に置くので、切り詰めで採用できる別論文のチャンクが採用できない
    トピック内のチャンクに押し出されることはない。``document_id`` を持たない結果は
    トピック外として扱う（推測で昇格させない）。採否と出所の分類は変えない。
    """
    return sorted(
        list(results or []),
        key=lambda r: (
            float(r.get("score") or 0.0) < 0.30,
            str(r.get("document_id") or "") not in topic_doc_ids,
            -float(r.get("score") or 0.0),
        ),
    )


_CITED_MARKER_RE = re.compile(r"\[出典(\d+)\]")


def _chunk_ids_cited_in_answer(answer: str, cited_sources: list[dict]) -> list[str]:
    """回答本文の ``[出典N]`` が指すチャンク id を引用順に返す（IK-0470・決定論）。

    ``cited_sources`` に無い番号は捨てる（本文照合済みの番号だけ）。本文が1つも引用して
    いなければ空（推測で検索上位を足さない）。
    """
    by_index = {
        int(s["index"]): str(s.get("chunk_id") or "")
        for s in cited_sources or []
        if isinstance(s, dict) and s.get("chunk_id") and str(s.get("index") or "").isdigit()
    }
    out: list[str] = []
    for match in _CITED_MARKER_RE.finditer(answer or ""):
        chunk_id = by_index.get(int(match.group(1)))
        if chunk_id and chunk_id not in out:
            out.append(chunk_id)
    return out


def _sources_cited_in_answer(answer: str, sources: list[dict]) -> list[dict]:
    """学習者に見せる出典を、回答本文が ``[出典N]`` で実際に引用したものだけに絞る（IK-0494）。

    文脈に採用した出典（``sources``）のうち本文が引いていないもの（別の論文の近いチャンク等）は
    出典の一覧に並べない。番号は振り直さない（会話の採番器の番号のまま = IK-0432/0444）。
    並びは ``sources`` の順。本文が1つも引用していなければ空。入力は変更しない。
    """
    cited = {int(m.group(1)) for m in _CITED_MARKER_RE.finditer(answer or "")}
    if not cited:
        return []
    out: list[dict] = []
    for s in sources or []:
        try:
            index = int(s.get("index"))
        except (TypeError, ValueError, AttributeError):
            continue
        if index in cited:
            out.append(s)
    return out


def _displayed_sources_for(answer: str, sources: list[dict], content_grounding: str | None) -> list[dict]:
    """学習者に見せる出典（IK-0494）。出所の帯（content_grounding）と食い違わないようにする。

    ①本文が引用した出典があればそれだけ。②本文が1つも引用していないのに出所が
    course_material / other_material のときは、その出所を決めた出典（origin が出所と同じもの）を
    並べる — 出所の判定（IK-0382）は「文脈に置いて問いに関わった資料」で決まり引用の有無では
    決まらないので、帯だけ「教材に基づく」で出典が空、という食い違いを作らない。
    トピック教材だけで course_material になった往復は番号付き出典が無いので空のまま（従来どおり）。
    """
    cited = _sources_cited_in_answer(answer, sources)
    if cited or content_grounding not in ("course_material", "other_material"):
        return cited
    return [s for s in sources or [] if isinstance(s, dict) and s.get("origin") == content_grounding]


#: IK-0475: 直前の回答の引用を次の往復の文脈へ戻す上限（件数・1件の字数）。
_CARRIED_CHUNK_MAX = 3
_CARRIED_CHUNK_MAX_CHARS = 1200


def _previous_turn_cited_refs(history: list | None) -> list[dict]:
    """直前の assistant ターンが本文で引用した出典 ``{index, chunk_id, tier}`` を引用順に返す。

    本文の ``[出典N]`` と、そのターンに焼き込まれた ``sources`` の両方にある番号だけ
    （推測で結ばない）。直前の assistant ターンが無い・出典が無ければ空。
    """
    for turn in reversed(list(history or [])):
        if not isinstance(turn, dict) or turn.get("role") != "assistant":
            continue
        by_index: dict[int, dict] = {}
        for source in turn.get("sources") or []:
            if not isinstance(source, dict):
                continue
            try:
                index = int(source.get("index"))
            except (TypeError, ValueError):
                continue
            chunk_id = str(source.get("chunk_id") or "").strip()
            if index > 0 and chunk_id and index not in by_index:
                by_index[index] = {"index": index, "chunk_id": chunk_id, "tier": source.get("tier")}
        out: list[dict] = []
        for match in _CITED_MARKER_RE.finditer(str(turn.get("content") or "")):
            ref = by_index.get(int(match.group(1)))
            if ref and ref not in out:
                out.append(ref)
        return out
    return []


def _carry_previous_cited_sources(
    history: list | None,
    numbers: "_SessionCitationNumbers",
    *,
    exclude_chunk_ids: set,
    allowed_document_ids,
) -> list[dict]:
    """直前の回答が引用したチャンクを、次の往復の文脈へ戻す検索結果の形で返す（IK-0475）。

    - 今回の検索に既にあるチャンク（``exclude_chunk_ids``）は戻さない（二重にしない）。
    - 会話の採番器の番号と履歴の番号が一致するものだけ（元の番号で戻せないものは戻さない）。
    - 可視性は ``get_chunks_for_prompt`` が ``allowed_document_ids`` を SQL 内で強制する。
    - 上限 :data:`_CARRIED_CHUNK_MAX` 件。tier は保存時の値を使う（類似度が無いので再判定しない）。
    """
    mapping = numbers.mapping()
    refs = [
        ref for ref in _previous_turn_cited_refs(history)
        if ref["chunk_id"] not in exclude_chunk_ids and mapping.get(ref["chunk_id"]) == ref["index"]
    ][:_CARRIED_CHUNK_MAX]
    if not refs:
        return []
    rows = get_chunks_for_prompt(
        [ref["chunk_id"] for ref in refs], allowed_document_ids=allowed_document_ids
    )
    tier_by_chunk = {ref["chunk_id"]: ref.get("tier") for ref in refs}
    out: list[dict] = []
    for row in rows or []:
        row = dict(row)
        stored_tier = tier_by_chunk.get(str(row.get("id") or ""))
        if stored_tier in (TIER_APPROVED, TIER_SOURCE, TIER_OUT_OF_SOURCE):
            row["tier"] = stored_tier
        out.append(row)
    return out


def _adopted_source_entry(
    numbers: _SessionCitationNumbers, result: dict, course_material_ids: set
) -> dict:
    """検索結果1件を採用した出典（``cited_sources`` の1要素）にする。2つの組み立て箇所の正本。

    類似度の生値（cosine）は載せない（IK-0433: 学習者に数値を返さない。tier の判定は
    ``search_chunks_with_metadata`` の内側で済んでいる）。
    """
    quote = sanitize_source_text_for_prompt(result.get("text") or "").strip().replace("\n", " ")
    return {
        "index": numbers.assign(result.get("id", "")),
        "chunk_id": result.get("id", ""),
        "source_title": result.get("source_title", "不明な教材"),
        "tier": result.get("tier", TIER_OUT_OF_SOURCE),
        # 数式区間（``$…$``）の途中では切らない（IK-0394。切り詰めは
        # ``core.text_excerpt.excerpt`` の1実装）。
        "quote": excerpt(quote, 80, keep_dollar_math=True),
        "meta": _source_location_meta(result),
        "origin": (
            "course_material" if result.get("material_id") in course_material_ids else "other_material"
        ),
    }


def _history_source_meta(cited_sources: list[dict]) -> list[dict]:
    """履歴に焼き込む出典メタ（チップ描画とポップアップ起動に要る最小フィールドのみ）。"""
    return [
        {
            "index": s["index"],
            "chunk_id": s["chunk_id"],
            "source_title": s["source_title"],
            "tier": s["tier"],
        }
        for s in cited_sources
    ]


def _screen_selection_element_type(selection: dict) -> str:
    """画面文脈 ``selection.element_type`` を学習側の語彙へ落とす（対象外なら ""）。

    ``_screen_selected_element_type``（正規化済み ctx を受ける版）と同じ規則。
    フロントは "formula"（教材埋め込みの語彙）を "equation" に写して送るが、旧
    クライアント・別経路からの素通しに備えて受け側でも吸収する。
    """
    raw = str(selection.get("element_type") or "").strip()
    if raw == "formula":
        raw = "equation"
    return raw if raw in LEARNING_ELEMENT_TYPES else ""


def _screen_selection_anchor_type(element_type: str) -> str:
    """画面文脈の要素型 → 構造帰属の粒度（``ANCHOR_TYPES``）。

    claim / equation はそのまま同名の粒度に当たる。component は概念ノード
    （``theory_components``）なので concept。figure は ``ANCHOR_TYPES`` に無いので
    設計 §5 の規律どおり**粗い粒度へ縮退**させる（chunk = 教材の箇所）。
    """
    if element_type in ANCHOR_TYPES:
        return element_type
    if element_type == "figure":
        return "chunk"
    return anchor_type_for_element(element_type)


def _screen_selection_for_anchor(
    body: LearningChatRequest, *, course_id: str
) -> dict | None:
    """痕跡帰属に使える画面文脈の ``selection``（無ければ None）。

    画面が別のコースを指しているなら丸ごと無視する（``_learning_screen_context_block``
    と同じ扱い）。正規化・語彙判定は core 側（SA1: 画面は参照しか渡さない）。
    """
    payload = getattr(body, "screen_context", None)
    if payload is None:
        return None
    try:
        ctx = normalize_screen_context(payload.model_dump())
    except Exception:  # pragma: no cover - 正規化は例外を出さない契約
        return None
    if ctx is None or ctx.screen != SCREEN_LEARNING:
        return None
    declared_course_id = str(ctx.selection.get("course_id") or "")
    if declared_course_id and declared_course_id != str(course_id):
        return None
    return dict(ctx.selection)


def _topic_material_segment_texts(
    topic: dict | None, figures_by_id: dict[str, dict] | None = None
) -> list[str]:
    """教材区画の本文を**表示順**で返す（P2-R3。配信と痕跡帰属の共通正本）。

    粒度の正本は ``core/lecture.py::build_topic_slides``（``===`` マーカーがあれば
    教員の明示分割、無く長ければ段落境界の自動ページ分割。決定論・非LLM）。
    受講表示・レクチャー・音声・readiness と同じ関数を通ることで、学習者が見る区画の
    並びと ``data-segment-index`` / :func:`resolve_selection_segment` の番号が一致する。

    ``figures_by_id`` を省略すると ``![[figure:id]]`` は原文のまま残るが、
    ``_display_length`` が未解決の図埋め込みも解決済み ``[[FIGURE_N]]`` と同じ長さに
    換算するため**ページ境界は変わらない**（``build_topic_slides`` の契約）。
    教材が無ければ空リスト。
    """
    if not isinstance(topic, dict) or not _topic_student_material(topic).strip():
        return []
    try:
        slides, _display, _spoken, _formulas = build_topic_slides(topic, figures_by_id)
    except Exception:  # noqa: BLE001 - 区画が決まらないだけ（配信・帰属は止めない）
        logger.warning("topic material segmentation failed", exc_info=True)
        return []
    return [str((slide or {}).get("display_text") or "") for slide in slides]


def _anchor_segment_texts(topic_info: dict | None) -> list[str] | None:
    """区画番号の解決材料（教材区画の本文・表示順）。無ければ None。

    ``get_topic_material`` が学習者へ配信する chunks と**同じ材料**（＝
    :func:`_topic_material_segment_texts` = ``build_topic_slides`` のページ）を使う
    （フロントの ``data-segment-index`` と同じ単位でなければ番号の意味が食い違う）。

    図埋め込みの解決（``figures_by_id``）はしない — ページ境界は解決の有無で
    変わらず、``[[FIGURE_N]]`` / ``![[figure:id]]`` のようなプレースホルダーは
    :func:`resolve_selection_segment` の照合キーから落ちるため、配信本文と同じ
    キーになる（P2-R13。同期パスにクエリを増やさない）。

    トピック本文が無い後方互換経路（PDF 復元チャンク）のために DB を引き直すことは
    しない — 材料が無ければ解決しない（推測しない）。
    """
    segments = _topic_material_segment_texts(topic_info or {})
    return segments or None


def _learner_selected_anchor(
    body: LearningChatRequest,
    *,
    screen_selection: dict | None = None,
    segment_texts: list[str] | None = None,
) -> dict | None:
    """発話時の明示アンカー（構造帰属・方法A）を非LLMで構築する。無ければ None。

    「どこ（anchor）」はこの操作で確定するが「どう（doubt_type）」までは分からないため
    unclassified のまま保持する（P4。方法B/C が後から補い得る）。

    優先順（学ぶ単位 P2-7・設計 §8）:

    1. 要素タップ（``element_id``）— 従来どおり ground truth。
    2. テキスト選択（``selection_text``）— 区画番号は ①クライアント申告
       （``selection_segment_id``）②``segment_texts`` との逐語一致 の順に解決し、
       どちらも決まらなければ ``anchor_id=""``（``seg_0`` を既定にしない = C-11 の是正）。
    3. 画面文脈の選択要素（``screen_selection``）— 学習者が要素チップを選んでいる状態での
       発話は明示アンカー。AI 候補（方法B）に回さず learner_selected で確定する。
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
        if seg is None and segment_texts:
            # 区画番号をクライアントが申告できなかったとき（レクチャー非再生など）は
            # 教材区画の本文との逐語一致で埋める。一意に決まらなければ None のまま。
            seg = resolve_selection_segment(segment_texts, sel)
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
    if screen_selection:
        element_type = _screen_selection_element_type(screen_selection)
        element_id = str(screen_selection.get("element_id") or "").strip()
        if element_type and element_id:
            label = str(screen_selection.get("element_label") or "").strip() or element_id
            return build_anchor_payload(
                anchor_type=_screen_selection_anchor_type(element_type),
                anchor_id=element_id,
                anchor_label=label,
                doubt_type="unclassified",
                attribution_source=ATTRIBUTION_LEARNER_SELECTED,
                evidence_quote="",
                reason="screen_selection",
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


# ---------------------------------------------------------------------------
# 画面文脈アダプター Phase 4（正本 docs/features/assistant_screen_adapter_design.md §11）
#
# 画面は**参照だけ**を渡し（SA1）、ここ（route）が権限3段
#   ①受講ゲート（呼び出し元が解決済みの course_data）
#   ②コース sources / scope_document_ids（grounding_document_ids）
#   ③各学習者射影の内部 SQL の ``ANY(:doc_ids)``
# を通した DTO を ``sources`` に組み、core の解決器（純関数・非LLM）が事実文にする。
#
# 規律:
# - **生テーブルを引かない**。学習者射影（component_context / element_context /
#   doubt の learner_ledger_line / landscape の learner_landscape_for_documents）が
#   持つ遮断（数値除去・内部 ID 遮断・scope 強制）を再実装しない（§11.3）。
# - **キャッシュはリクエスト内のみ**（プロセス跨ぎのキャッシュを作らない = §11.6）。
# - **fail-soft**。射影1本の例外はそのキーだけ None になり、全体が空なら
#   ``render_block`` が "" を返して従来と同一のプロンプトになる（SA2）。
# ---------------------------------------------------------------------------

#: 学習側で台帳を引ける要素型（``core.doubt.schema.TargetType`` に実在する型だけ）。
_SCREEN_LEDGER_TARGET_TYPES = {"component": "component", "claim": "claim"}


def _screen_selected_element_type(ctx) -> str:
    """``selection.element_type`` を学習側の語彙へ落とす（対象外なら ""）。"""
    raw = str(ctx.selection.get("element_type") or "").strip()
    # フロントは "formula"（教材埋め込みの語彙）を "equation" に写して送るが、
    # 旧クライアント・別経路からの素通しに備えて受け側でも吸収する。
    if raw == "formula":
        raw = "equation"
    return raw if raw in LEARNING_ELEMENT_TYPES else ""


def _screen_element_sources(
    ctx,
    *,
    course_id: str,
    topic_info: dict | None,
    grounding_document_ids: set[str],
) -> dict:
    """kind ``element`` の sources（選択チップ1件の射影 + evidence item）。"""
    element_type = _screen_selected_element_type(ctx)
    element_id = str(ctx.selection.get("element_id") or "").strip()
    if not element_type or not element_id:
        return {}

    context = None
    if element_type == "component":
        context = _component_context_with_explanation(
            element_id, course_id, set(grounding_document_ids)
        )
    elif element_type in CONTEXT_ELEMENT_TYPES:  # claim / equation
        context = build_element_context(
            element_type, element_id, set(grounding_document_ids)
        )

    # 題名だけの縮退材料。トピックに**公開済み**の参照からしか引かない
    # （build_topic_evidence_items の契約 — クライアント入力から任意 ID を解決しない）。
    item = None
    try:
        wanted = normalize_evidence_id(element_id)
        for candidate in build_topic_evidence_items(topic_info or {}):
            if str(candidate.get("kind") or "") != element_type:
                continue
            if normalize_evidence_id(candidate.get("id")) == wanted:
                item = candidate
                break
    except Exception:  # noqa: BLE001
        logger.debug("screen_context: evidence item lookup failed", exc_info=True)
        item = None

    return {"element_type": element_type, "context": context, "item": item}


def _screen_element_document_id(element: dict, ctx, grounding_document_ids: set[str]) -> str:
    """選択要素が由来する document_id（``grounding_document_ids`` 内のものだけ）。

    component は射影 DTO の ``instance.in_paper.document.id`` が正本。claim / equation /
    figure は学習者射影が document_id を返さないので、evidence item と画面の申告
    （参照 = SA1）を順に見て、**必ず grounding 集合への所属で検証**する（fail-closed）。
    """
    candidates: list[str] = []
    context = element.get("context")
    if isinstance(context, dict):
        document = ((context.get("instance") or {}).get("in_paper") or {}).get("document") or {}
        if isinstance(document, dict):
            candidates.append(str(document.get("id") or ""))
    item = element.get("item")
    if isinstance(item, dict):
        candidates.append(str(item.get("document_id") or ""))
    candidates.append(str(ctx.selection.get("document_id") or ""))
    for candidate in candidates:
        if candidate and candidate in grounding_document_ids:
            return candidate
    return ""


def _learning_screen_sources(
    ctx,
    *,
    course_id: str,
    course_data: dict,
    topic_info: dict | None,
    grounding_document_ids: set[str],
    kinds: tuple[str, ...] | None = None,
) -> dict:
    """画面文脈の解決に渡す ``sources``（§11.3 の契約）を権限ゲート内で組む。

    ``grounding_document_ids`` が空なら**何も引かない**（fail-closed）。
    ``selection.course_id`` が URL の course_id と一致しないときは呼び出し側で弾く。
    ``kinds`` で解決する種別が絞られているときは、**その種別が使う射影しか引かない**
    （``cycle_mode="elicit"`` は表示モードの事実だけなので DB を1本も引かない）。
    """
    if not grounding_document_ids:
        return {}
    wanted = None if kinds is None else set(kinds)
    if wanted is not None and not wanted & {"element", "verification", "placement"}:
        return {}

    sources: dict = {}
    try:
        element = _screen_element_sources(
            ctx,
            course_id=course_id,
            topic_info=topic_info,
            grounding_document_ids=grounding_document_ids,
        )
    except Exception:  # noqa: BLE001
        logger.debug("screen_context: element projection failed", exc_info=True)
        element = {}
    if element:
        sources["element"] = element

    if not element:
        return sources

    # 台帳（SL1 の閉世界語彙のまま）。対象型は台帳に実在する型だけ・ID は DB UUID のみ
    # （agent 側 ID では台帳行を引けないので引きにいかない）。
    ledger_target = _SCREEN_LEDGER_TARGET_TYPES.get(str(element.get("element_type") or ""))
    if ledger_target and (wanted is None or "verification" in wanted):
        context = element.get("context")
        target_id = ""
        if isinstance(context, dict):
            target_id = str(
                context.get("component_id") or context.get("element_id") or ""
            ).strip()
        if target_id and _is_db_uuid(target_id):
            session = _pg_session()
            try:
                line = learner_ledger_line(session, ledger_target, target_id)
                if line:
                    sources["ledger"] = line
            except Exception:  # noqa: BLE001
                logger.debug("screen_context: ledger projection failed", exc_info=True)
            finally:
                session.close()

    # 分野の地図での位置づけ（出所ラベルを剥がさない = §11.13-1）。
    document_id = (
        _screen_element_document_id(element, ctx, grounding_document_ids)
        if (wanted is None or "placement" in wanted)
        else ""
    )
    if document_id:
        try:
            landscape = learner_landscape_for_documents(course_data, [document_id])
            if (landscape or {}).get("documents"):
                sources["landscape"] = landscape
        except Exception:  # noqa: BLE001
            logger.debug("screen_context: landscape projection failed", exc_info=True)

    return sources


def _learning_screen_context_block(
    body: LearningChatRequest,
    *,
    course_id: str,
    course_data: dict,
    topic_info: dict | None,
    grounding_document_ids: set[str],
    kinds: tuple[str, ...] | None = None,
) -> tuple[str, bool]:
    """``(事実文ブロック, 台帳由来の事実を含むか)``。解決できなければ ``("", False)``。

    ``kinds`` は ``cycle_mode="elicit"`` のときに ``("view",)`` を渡す
    （問いの答えを手渡さない = §11.5）。どこで失敗しても空文字へ縮退する（SA2）。
    """
    payload = getattr(body, "screen_context", None)
    if payload is None:
        return "", False
    try:
        ctx = normalize_screen_context(payload.model_dump())
    except Exception:  # pragma: no cover - 正規化は例外を出さない契約
        return "", False
    if ctx is None or ctx.screen != SCREEN_LEARNING:
        return "", False
    # §11.2: 画面の ``selection.segment_id`` は ``selection_segment_id``（サーバが既に
    # 痕跡記録で信頼している明示アンカー）の写しであってよい。両方あって食い違えば
    # 後者を優先し、事実文に載る区画番号がクライアント申告だけで決まらないようにする。
    if body.selection_segment_id is not None:
        ctx = dataclasses.replace(
            ctx,
            selection={**ctx.selection, "segment_id": str(int(body.selection_segment_id))},
        )
    # 画面が別のコースを指しているなら丸ごと無視する（Phase 1 の document 不一致と同じ
    # 扱い。センチネル course_id の経路でも「一致しなければ無視」でよい = §11.3）。
    declared_course_id = str(ctx.selection.get("course_id") or "")
    if declared_course_id and declared_course_id != str(course_id):
        return "", False

    try:
        sources = _learning_screen_sources(
            ctx,
            course_id=course_id,
            course_data=course_data,
            topic_info=topic_info,
            grounding_document_ids=grounding_document_ids,
            kinds=kinds,
        )
    except Exception:  # noqa: BLE001
        logger.debug("screen_context: sources assembly failed", exc_info=True)
        sources = {}

    try:
        facts = resolve_screen_context(ctx, sources, kinds=kinds)
        block = render_block(
            facts,
            header=BLOCK_HEADER_LEARNING,
            max_chars=MAX_BLOCK_CHARS_LEARNING,
        )
    except Exception:  # pragma: no cover - resolve/render は例外を出さない契約
        logger.debug("screen_context: resolution failed", exc_info=True)
        return "", False
    if not block:
        return "", False
    # 台帳由来の事実が**実際にブロックへ載ったとき**だけ、出力側の拘束（SL1 の言い換え
    # 防止）を足す（§11.13-2 の2段構え。事実が無いのに拘束だけ足さない — 予算超過で
    # 落ちた場合・kinds で verification を外した場合も「載っていない」に含める）。
    has_ledger = False
    if kinds is None or "verification" in set(kinds):
        try:
            verification_facts = resolve_screen_context(ctx, sources, kinds=("verification",))
        except Exception:  # pragma: no cover - resolve は例外を出さない契約
            verification_facts = []
        has_ledger = any(fact and fact in block for fact in verification_facts)
    return block, has_ledger


# ---------------------------------------------------------------------------
# 知識の転用層 P4-2（knowledge_transfer_design.md §5）— RAG の構造 1 hop
#
# 「chunk 近傍 → その chunk に結ばれた主張 → 理論の骨格（main 層）のノード」を
# **決定論・LLM 0 回・embedding 0 回**（KT3）で解決し、SA層の kind
# ``retrieved_structure`` として当該ターンへ渡す。入口は画面の申告ではなく**回答に
# 採用した出典**（``cited_sources``）なので、``screen_context`` が無いターンでも働く。
#
# 権限（KT6）: 当該ターンの ``allowed_document_ids`` を ``ANY(:doc_ids)`` で SQL に
# 直接強制する（discuss の ``all_visible`` でも**検索範囲と同一**で、構造側で広げない）。
# 取得の失敗はすべて握って空へ縮退する（対話を止めない = SA2）。
# ---------------------------------------------------------------------------

#: 1回の解決で読む主張行の上限（出典は最大8件・出典あたり2主張なので十分な余裕）。
_RETRIEVED_STRUCTURE_ROW_LIMIT = 200

#: 1回の解決で読む理論操作グラフの document 数の上限（出典が散っても有界にする）。
_RETRIEVED_STRUCTURE_MAX_DOCUMENTS = 4


def _retrieved_structure_claims(
    chunk_ids: list[str], document_ids: list[str]
) -> list[dict]:
    """採用チャンクに結ばれた live の主張行を読む（DB 読み 1 本目）。

    ``origin='equation_synthesis'`` は本文が式そのもの（§5 で v1 対象外）なので除く。
    superseded の除外は live ビューが担う（KO5）。
    """
    if not chunk_ids or not document_ids:
        return []
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(
                f"""
                SELECT id::text AS id,
                       chunk_id::text AS chunk_id,
                       document_id::text AS document_id,
                       text,
                       claim_type,
                       COALESCE(agent_claim_id, '') AS agent_claim_id,
                       source_scope
                FROM theory_claims_live
                WHERE chunk_id = ANY(CAST(:chunk_ids AS uuid[]))
                  AND document_id = ANY(CAST(:doc_ids AS uuid[]))
                  AND COALESCE(origin, '') <> 'equation_synthesis'
                ORDER BY chunk_id, created_at, id
                LIMIT {_RETRIEVED_STRUCTURE_ROW_LIMIT}
                """
            ),
            {"chunk_ids": chunk_ids, "doc_ids": document_ids},
        ).mappings().fetchall()
    finally:
        session.close()
    return [dict(row) for row in (rows or [])]


def _retrieved_structure_node_index(document_id: str) -> dict[str, dict]:
    """``claim 参照 ID → main 層ノード``（DB 読み 2 本目・detail / debug は使わない）。

    グラフ側の ``linked_claim_ids`` は DB UUID / agent 側 claim ID のどちらでも
    入りうるので、キーは正規化せずそのまま引けるようにする。
    """
    graph = load_latest_graph(document_id) or {}
    index: dict[str, dict] = {}
    for node in (graph.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        if str(node.get("graph_layer") or "main") != "main":
            continue
        entry = {
            "label": str(node.get("label") or ""),
            "display_label": str(node.get("display_label") or ""),
        }
        for claim_id in (node.get("linked_claim_ids") or []):
            key = str(claim_id or "").strip()
            if key:
                index.setdefault(key, entry)
    return index


def _retrieved_structure_sources(
    cited_sources: list[dict], allowed_document_ids
) -> dict:
    """SA層 kind ``retrieved_structure`` の ``sources`` を組む（§5 の射影）。

    戻り値は ``{"sources": [{"index": "1", "claims": [{text, claim_type, node}]}]}``。
    数値（一致度・件数）は持たせない（KT7）。解決できなければ ``{}``。
    """
    document_ids = [str(d) for d in (allowed_document_ids or []) if str(d or "").strip()]
    if not cited_sources or not document_ids:
        return {}
    # 出典番号は cited_sources と 1 対 1（同じ chunk が複数回出ることはない）。
    index_by_chunk: dict[str, str] = {}
    for source in cited_sources:
        chunk_id = str((source or {}).get("chunk_id") or "").strip()
        index = str((source or {}).get("index") or "").strip()
        if chunk_id and index and _is_db_uuid(chunk_id):
            index_by_chunk.setdefault(chunk_id, index)
    if not index_by_chunk:
        return {}

    try:
        rows = _retrieved_structure_claims(list(index_by_chunk), document_ids)
    except Exception:  # noqa: BLE001
        logger.debug("retrieved_structure: claim projection failed", exc_info=True)
        return {}
    if not rows:
        return {}

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        index = index_by_chunk.get(str(row.get("chunk_id") or ""))
        if not index:
            continue
        bucket = grouped.setdefault(index, [])
        if len(bucket) >= MAX_LEARNING_RETRIEVED_CLAIMS_PER_SOURCE:
            continue
        bucket.append(row)
    if not grouped:
        return {}

    # 理論の骨格は document ごとに1回だけ読む（有界）。失敗した document は node なし。
    node_indexes: dict[str, dict[str, dict]] = {}
    for row in (claim for bucket in grouped.values() for claim in bucket):
        document_id = str(row.get("document_id") or "")
        if not document_id or document_id in node_indexes:
            continue
        if len(node_indexes) >= _RETRIEVED_STRUCTURE_MAX_DOCUMENTS:
            continue
        try:
            node_indexes[document_id] = _retrieved_structure_node_index(document_id)
        except Exception:  # noqa: BLE001
            logger.debug("retrieved_structure: graph projection failed", exc_info=True)
            node_indexes[document_id] = {}

    entries: list[dict] = []
    for index in sorted(grouped, key=lambda value: (len(value), value)):
        claims: list[dict] = []
        for row in grouped[index]:
            node_index = node_indexes.get(str(row.get("document_id") or ""), {})
            keys = [str(row.get("id") or ""), str(row.get("agent_claim_id") or "")]
            scope = row.get("source_scope")
            if isinstance(scope, dict):
                keys.extend(str(v or "") for v in (scope.get("legacy_ids") or []))
            node = None
            for key in keys:
                if key and key in node_index:
                    node = node_index[key]
                    break
            claims.append(
                {
                    "text": str(row.get("text") or ""),
                    "claim_type": str(row.get("claim_type") or ""),
                    "node": node,
                }
            )
        if claims:
            entries.append({"index": index, "claims": claims})
    return {"sources": entries} if entries else {}


def _learning_retrieved_structure_block(
    cited_sources: list[dict], allowed_document_ids
) -> str:
    """検索由来の構造ブロック（§5）。解決できなければ ``""``（従来と同一のプロンプト）。"""
    try:
        sources = _retrieved_structure_sources(cited_sources, allowed_document_ids)
    except Exception:  # noqa: BLE001
        logger.debug("retrieved_structure: sources assembly failed", exc_info=True)
        return ""
    if not sources:
        return ""
    ctx = normalize_screen_context({"screen": SCREEN_LEARNING})
    try:
        facts = resolve_screen_context(
            ctx, {"retrieved_structure": sources}, kinds=("retrieved_structure",)
        )
        return render_block(
            facts,
            header=BLOCK_HEADER_RETRIEVED,
            max_chars=MAX_BLOCK_CHARS_LEARNING,
        )
    except Exception:  # pragma: no cover - resolve/render は例外を出さない契約
        logger.debug("retrieved_structure: resolution failed", exc_info=True)
        return ""


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

    degraded = False
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
        try:
            answer = generate_text(
                messages=[{"role": "user", "content": prompt}],
                model=params["model"],
                reasoning_effort=params["reasoning_effort"],
                temperature=0.3,
            )
        except Exception:
            # 会話は死なせない（設計書 I3）: チャット本体と同じ degraded 規約に揃える。
            # 以前はここだけ try/except が無く、LLM 失敗が 500 になっていた（本体は
            # degraded 固定文 + 200）。履歴は保存し、本文依存の後処理（数式の差し込み）は
            # スキップする（I4）。
            logger.exception(
                "Graph element explanation LLM call failed for element %s", body.element_id
            )
            answer = _CHAT_DEGRADED_MESSAGE
            degraded = True
        if not degraded and target_formula_latex:
            formula_id = str(target_formula.get("id") or "").strip() if isinstance(target_formula, dict) else ""
            if formula_id:
                answer = answer.replace(formula_id, f"${target_formula_latex}$")
            if target_formula_latex not in answer:
                answer = f"対象の数式は次の式です。\n\n$${target_formula_latex}$$\n\n" + answer
    persist_chat_history(
        user_id, course_id, topic_id,
        body.history, user_message, answer,
    )
    return LearningChatResponse(answer=answer, course_update=None, degraded=degraded)


def _topic_student_material(topic: dict) -> str:
    material = topic.get("student_material")
    if isinstance(material, dict):
        text = str(material.get("source_text") or "").strip()
        if text:
            return text
    return str(topic.get("content") or topic.get("summary") or "").strip()


# ---------------------------------------------------------------------------
# トピック教材が「この問いの根拠」と言えるか（IK-0382）
# ---------------------------------------------------------------------------
#
# 表示中のトピック教材は毎ターン `[現在表示中の教材]` として文脈に注入される。従来は
# 注入しただけで `content_grounding="course_material"`（📘 教材から回答）・tier 下限
# `source` にしていたため、教材と無関係な問い（別トピックの概念・一般知識）に検索が
# 1件も当たらなくても「教材に基づく」と表示された（出所の正直さ・原則8 に反する）。
# ここでは**決定論・非LLM・追加コストなし**で、トピック教材がその問いに関わっているか
# だけを判定する。判定を落としても教材の注入自体は変えない（プロンプトは不変）。
#
# 関わっている、とみなすのは次のいずれか:
#   - 学習者が教材の箇所・要素を明示している（テキスト選択 / 要素タップ / チャンク指定）
#   - 問いに内容語が無い（「ここはどういう意味？」のような指示語だけの問い = 画面の教材を指す）
#   - 問いの内容語の過半が教材本文に逐語で現れる
# 内容語は「ひらがな・記号で区切った漢字・カタカナ・英数の連なり（2文字以上）」と
# 「」『』で括られた語。ASCII の語は略号（大文字2つ以上か数字を含む）だけを数える
# （英語の問いを日本語の教材と逐語比較しない — 判定できない問いは従来どおり扱う）。

_GROUNDING_QUOTED_RE = re.compile(r"[「『]([^」』]{2,40})[」』]")
_GROUNDING_COMPOUND_RE = re.compile(r"[\u30A1-\u30FA\u30FC\u4E00-\u9FFF\u3005A-Za-z0-9\-]+")
_GROUNDING_CJK_RE = re.compile(r"[\u30A1-\u30FA\u4E00-\u9FFF]")
#: 問いの主題を示さない汎用語（内容語から外す）。分野語は書かない。
_GROUNDING_GENERIC_TERMS = frozenset({
    "意味", "理解", "説明", "質問", "回答", "教材", "論文", "本文", "部分", "場合",
    "具体的", "初歩的", "確認", "問題", "答え", "出典", "箇所", "内容", "トピック",
    "何", "今", "今回", "方法", "理由", "関係", "結果",
})


def _grounding_content_terms(message: str) -> list[str]:
    """問いの内容語を決定論的に取り出す（重複除去・出現順）。"""
    msg = message or ""
    terms: list[str] = []

    def _add(term: str) -> None:
        term = term.strip()
        if len(term) < 2 or term in _GROUNDING_GENERIC_TERMS or term in terms:
            return
        terms.append(term)

    for quoted in _GROUNDING_QUOTED_RE.findall(msg):
        _add(quoted)
    for run in _GROUNDING_COMPOUND_RE.findall(msg):
        if _GROUNDING_CJK_RE.search(run):
            _add(run)
        elif sum(1 for ch in run if ch.isupper()) >= 2 or any(ch.isdigit() for ch in run):
            _add(run)
    return terms


def _topic_material_engages_message(
    body: "LearningChatRequest",
    topic_material: str,
    message: str | None = None,
) -> bool:
    """トピック教材がこの問いの根拠と言えるか（決定論・非LLM）。

    ``message`` を渡すとそれを問いとして判定する（前提確認の往復で元の質問に答える
    とき = IK-0396。既定は ``body.message``）。
    """
    if not topic_material:
        return False
    if (body.selection_text or "").strip() or body.element_id or body.chunk_id:
        return True
    terms = _grounding_content_terms(body.message if message is None else message)
    if not terms:
        return True
    haystack = topic_material.casefold()
    hits = sum(1 for t in terms if t.casefold() in haystack)
    return hits * 2 > len(terms)


def _retrieval_query_for_turn(body: "LearningChatRequest", message: str) -> str:
    """RAG 検索に渡す問い文を決める（IK-0395。決定論・非LLM・検索は1回のまま）。

    「はい、そう読みました。合っているんですか」のような内容語の無い相づち・追い質問は、
    その発話だけで検索すると 1 件も当たらず、直前まで出典のあった会話が
    「AI の一般知識」へ縮退する。内容語の判定は IK-0382 と同じ
    ``_grounding_content_terms`` を使い、内容語が無いときだけ、履歴のうち内容語のある
    直近の学習者発話を前に足す。テキスト選択・要素タップ・チャンク指定がある往復は
    画面の箇所を指しているので借りない（従来どおり）。
    """
    msg = message or ""
    if _grounding_content_terms(msg):
        return msg
    if (body.selection_text or "").strip() or body.element_id or body.chunk_id:
        return msg
    current = msg.strip()
    for turn in reversed(body.history or []):
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        content = str(turn.get("content") or "").strip()
        if not content or content == current:
            continue
        if _grounding_content_terms(content):
            return f"{content}\n{msg}" if msg else content
    return msg


def _history_without_out_of_source_notice(history: list | None) -> list:
    """LLM に再注入する履歴から、表示用の注意書き（out_of_source_notice）を剥がす（IK-0378）。

    注意書きは画面向けの表示で、回答本文ではない。クライアントが受け取った回答
    （注意書き付き）をそのまま履歴として送り返すため、剥がさないと次の往復で
    「教材の裏づけはない」という文が回答の一部としてモデルに戻る。
    """
    notice = out_of_source_notice()
    cleaned: list = []
    for turn in history or []:
        if (
            isinstance(turn, dict)
            and turn.get("role") == "assistant"
            and isinstance(turn.get("content"), str)
            and turn["content"].lstrip().startswith(notice)
        ):
            turn = {**turn, "content": turn["content"].lstrip()[len(notice):].lstrip()}
        cleaned.append(turn)
    return cleaned


#: 未踏ガードの優先順位（IK-0378）。ガードは採用した出典が1件も無いときだけ付くが、
#: 表示中の教材は注入されていることがあるので、即答の規則と衝突しない範囲を明示する。
_OUT_OF_SOURCE_GUARD_PRECEDENCE = (
    "（優先順位）このガードは、提示された出典や表示中の教材で裏づけられない主張にだけ"
    "適用すること。出典（[出典N]）や表示中の教材で裏づけられる内容については、回答を"
    "控えたり予想を先に求めたりせず、上の応答規則どおりに答えること。"
)


#: discuss 用の未踏ガード（IK-0473）。discuss の規則1（質問には即答・出し惜しみ禁止）と
#: 衝突する「予想を先に引き出す」段を持たない。断定しない・出所を分ける・推測を混ぜない、は残す。
_DISCUSS_OUT_OF_SOURCE_GUARD = (
    "【重要・未踏ガード（議論モード）】この質問は選択中の範囲の論文に十分な根拠が見つかっていません。"
    "次のとおり応答すること:\n"
    "1. 「この論文の抜粋では確認できない」と最初に一言明示する。\n"
    "2. そのうえで質問にはすぐに答える（予想を先に求めたり、答えを保留したりしない）。\n"
    "3. 一般知識で補う部分は『この論文に書かれている内容ではない参考情報』と区別して示す。\n"
    "4. 確実な事実と推測を混ぜない。誇張・断定表現を避ける。"
)


#: document 直付け議論の要旨ブロックの見出し（IK-0436）。番号付き出典ではないことを明示する。
_DOCUMENT_THESIS_HEADING = (
    "## この論文の要旨（論文から解析で再構成したもの。番号付き出典ではないので [出典N] を付けないこと）"
)
#: 要旨ブロックがあるときの未踏ガードの補足（IK-0436）。
_DOCUMENT_THESIS_GUARD_PRECEDENCE = (
    "（要旨の扱い）「この論文の要旨」に書かれた問い・目的・主張については、このガードの対象外として"
    "答えてよい。答えるときは、論文の解析結果の要旨に基づくことを一言添えること。"
)


# ---------------------------------------------------------------------------
# 理解度の点数・割合を求める発話（IK-0397。非LLM の pre-route）
# ---------------------------------------------------------------------------
#
# 数値非表示の原則（学習者に点数・正答率を見せない）を、LLM に言い換えさせず固定の
# 事実文で答える。誤爆を避けるため、点数の語と「自分・理解・確認問題」の語の両方が
# ある発話だけを拾う（「スコア関数」のような内容語だけでは拾わない）。

#: 常に「点数・割合を尋ねている」語（後ろに何が続いても点数の問い）。
_SCORE_REQUEST_ASK_JA = ("何点", "何割", "何パーセント", "何％", "何%")
#: 名詞の頭にもなる語（「スコア関数」「点数分布」）。直後が漢字・カタカナのときは数えない。
_SCORE_REQUEST_NOUN_JA = ("点数", "スコア")
_SCORE_REQUEST_SELF_JA = ("理解", "わた", "私", "自分", "僕", "俺", "確認問題", "成績")
_SCORE_NOUN_CONTINUES_RE = re.compile(r"[\u3400-\u9fff\u30a0-\u30ffー]")
#: 英語は「自分の点数」を指す形だけ（``my score`` / ``grade my …`` / ``how many points``）。
#: 「I don't get the main points」のような内容の発話は拾わない。
_SCORE_REQUEST_EN_RES = (
    re.compile(r"\bmy\s+(?:\w+\s+){0,2}?(?:score|grade|points?|percentage|marks?)\b", re.IGNORECASE),
    re.compile(r"\b(?:grade|score|rate|mark)\s+(?:me|my)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+many\s+points\s+(?:do|did|would)\s+i\b", re.IGNORECASE),
)


def _score_noun_asked(msg: str) -> bool:
    for term in _SCORE_REQUEST_NOUN_JA:
        start = 0
        while True:
            index = msg.find(term, start)
            if index < 0:
                break
            following = msg[index + len(term): index + len(term) + 1]
            if not following or not _SCORE_NOUN_CONTINUES_RE.match(following):
                return True
            start = index + len(term)
    return False


def _is_understanding_score_request(message: str) -> bool:
    msg = message or ""
    if not msg.strip():
        return False
    asked = any(t in msg for t in _SCORE_REQUEST_ASK_JA) or _score_noun_asked(msg)
    if asked and any(t in msg for t in _SCORE_REQUEST_SELF_JA):
        return True
    return any(pattern.search(msg) for pattern in _SCORE_REQUEST_EN_RES)


# ---------------------------------------------------------------------------
# お礼・締めくくりだけの発話（IK-0434。非LLM の pre-route）
# ---------------------------------------------------------------------------
#
# 「ありがとうございました、今日はここまでにします」「Thank you, that is very helpful!」に
# 検索・回答生成を走らせると、無関係な出典が並び、discuss は必須の問い返しで終える。
# 定型句と、定型句を強めるだけの語を取り除いて**何も残らない**発話だけを拾う
# （「ありがとう、でも µ はなぜ負？」のように内容が残る発話は拾わない — 狭く保つ）。

#: お礼・締めくくりの定型句（長いものから順に取り除く。casefold 後の形で書く）。
_CLOSING_PHRASES_JA = (
    "どうもありがとうございました", "どうもありがとうございます", "ありがとうございました",
    "ありがとうございます", "ありがとう", "助かりました", "助かります", "参考になりました",
    "勉強になりました", "今日はここまでにします", "今日はここまでにしておきます", "今日はここまで",
    "ここまでにします", "ここまでにしておきます", "終わりにします", "おしまいにします",
    "お疲れさまでした", "お疲れ様でした", "失礼します", "また来ます", "またよろしくお願いします",
)
_CLOSING_PHRASES_EN = (
    "thank you so much", "thank you very much", "thanks so much", "thanks a lot", "thank you",
    "thanks", "that is very helpful", "that's very helpful", "that was very helpful",
    "that is helpful", "that's helpful", "that was helpful", "this is very helpful",
    "this is helpful", "very helpful", "really helpful", "helpful", "that's all for today",
    "that is all for today", "i'm done for today", "i am done for today", "done for today",
    "see you next time", "see you", "goodbye", "bye", "have a nice day", "cheers",
)
#: 定型句を強めるだけで内容を持たない語（定型句を取り除いた残りに現れてよい）。
#: IK-0493: 「なるほど、ありがとうございます。」のような相づち＋お礼も定型句だけの発話として拾う
#: （相づちは内容を持たない。問いの形・内容が残る発話は従来どおり拾わない）。
_CLOSING_FILLER_JA = (
    "本当に", "ほんとうに", "とても", "大変", "すごく", "では", "じゃあ", "それでは",
    "なるほど", "分かりました", "わかりました", "了解しました", "了解です", "承知しました",
    "よくわかりました", "よく分かりました", "はい",
)
_CLOSING_FILLER_EN = (
    "really", "very", "so", "much", "ok", "okay", "great", "and", "for", "today",
    "i", "see", "got", "it", "understood", "ah", "oh", "alright", "right",
)
_CLOSING_PUNCT_RE = re.compile(r"[\s。．.!！?？、,，〜~…・'’\"「」😊🙏]+")


def _is_closing_utterance(message: str) -> bool:
    """お礼・締めくくりだけの発話か（決定論・非LLM）。"""
    text = (message or "").casefold().replace("’", "'")
    if not text.strip() or len(text) > 120:
        return False
    found = False
    for phrase in sorted(_CLOSING_PHRASES_JA + _CLOSING_PHRASES_EN, key=len, reverse=True):
        if phrase in text:
            if phrase.isascii():
                pattern = re.compile(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])")
                if not pattern.search(text):
                    continue
                text = pattern.sub(" ", text)
            else:
                text = text.replace(phrase, " ")
            found = True
    if not found:
        return False
    for filler in _CLOSING_FILLER_JA:
        text = text.replace(filler, " ")
    words = [w for w in _CLOSING_PUNCT_RE.split(text) if w]
    return all(w in _CLOSING_FILLER_EN for w in words)


def _is_closing_led_statement(message: str) -> bool:
    """お礼・締めくくりで始まり、問いを含まない発話か（IK-0471・決定論・非LLM）。

    「ありがとうございました。これでゼミで説明できそうです。」のように定型句の後に一言が
    続く発話は IK-0434 の締めくくり pre-route（定型句だけの発話）には当たらないが、教材で
    確かめる内容の問いではない。未踏ガードの注入・構造帰属・引っかかりのヒントの対象から
    外すための判定で、回答の生成そのものは止めない（狭く保つ: 問いの形なら偽）。
    """
    if _is_closing_utterance(message):
        return True
    text = (message or "").casefold().replace("’", "'")
    if not text.strip() or len(text) > 160 or _is_question_shaped(message):
        return False
    if any(phrase in text for phrase in _CLOSING_PHRASES_JA):
        return True
    return any(
        re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", text)
        for phrase in ("thank you", "thanks", "that is very helpful", "that's very helpful")
    )


def _question_before_prerequisite_gate(history: list | None, topic_title: str) -> str | None:
    """直前の assistant ターンがこのトピックの逆質問なら、その直前の学習者の質問を返す（IK-0396）。"""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        if turn.get("role") != "assistant":
            continue
        content = str(turn.get("content") or "")
        if PREREQUISITE_GATE_MARKER not in content or f"「{topic_title}」" not in content:
            return None
        for prev in reversed(turns[:index]):
            if prev.get("role") == "user":
                question = str(prev.get("content") or "").strip()
                return question or None
        return None
    return None


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
        # 教材区画の粒度は ``core/lecture.py::build_topic_slides`` のページ境界に揃える
        # （P2-R3）。表示・音声・readiness と**同じ決定論分割**なので、学習者が見る
        # 区画の並びとレクチャーのスライドの並びが一致し、テキスト選択の区画番号
        # （``data-segment-index`` / ``resolve_selection_segment``）が意味を持つ。
        # 分割できない（短い）教材は従来どおり1区画。
        # IK-0389 / IK-0455（配信側）: 区画の本文と、その本文が引く formulas を同じ関数から
        # 受け取り、引けない ``[[FORMULA_N]]`` / 根拠埋め込みを片付ける（正本は core/lecture.py）。
        segment_texts, formulas = topic_material_delivery_segments(topic or {}, figures_by_id, resolved_text, formulas, evidence_items)
        chunks = [
            ChunkContent(
                id=f"topic:{topic_id}",
                text=segment_text,
                chunk_index=topic_index,
                # formulas / figures / evidence_items は**区画ごとに間引かない**:
                # フロントの ``[[FORMULA_N]]`` / ``[[FIGURE_N]]`` 解決は配列の位置に
                # 依存するため、部分集合を渡すと番号がずれる（区画をまたぐ埋め込みも
                # 解決できなくなる）。同じ索引を各区画に渡す。
                formulas=formulas,
                figures=figures,
                evidence_items=evidence_items,
                chapter=None,
                # 章題は先頭区画にだけ出す（同じ題名を区画の数だけ繰り返さない）。
                section=(topic or {}).get("title") if seg_index == 0 else None,
                material_id=None,
                graph_mentions=[],
            )
            for seg_index, segment_text in enumerate(segment_texts)
        ]
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

    # IK-0375: 解説がまだ無く論文の本文をそのまま出すときは、その事実を1文添える
    # （何も言わずに論文の表紙が出ると、学習者はコースが壊れていると読む）。
    # 本文を返さないとき（chunks 空）は「そのまま表示しています」が偽になるので付けない。
    # 表示するチャンクの選び方はここでは変えない。
    return TopicMaterialResponse(
        topic_id=topic_id,
        chunks=chunks,
        preparation_notice=_topic_material_fallback_notice(course_data) if chunks else None,
    )


def _topic_material_fallback_notice(course_data: dict) -> str:
    """論文の本文をそのまま返すときの事実文を、コースの解説生成の記録から選ぶ。

    生成中と記録されていれば「準備中」、それ以外（記録なし・完了だがこのトピックの
    解説が無い・パイプライン待ち・失敗）は「生成されていません」。数字は入れない。
    """
    if course_content_is_preparing(course_data):
        return label_vocab.MATERIAL_PREPARING_NOTICE
    return label_vocab.MATERIAL_NOT_GENERATED_NOTICE


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
                SELECT id::text, document_id::text AS document_id, minio_key
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
    """確認問題の回答を出題の要件と並置する（是正 F1: 合否を出さない・確定しない）。

    AI の役割は Diff（並置）だけで、トピック完了の確定はここでは行わない。
    先へ進むかどうかは本人の自己確認（``POST .../check/self-check``）が決める。
    LLM 呼び出しは従来どおり1回（U層 feature ``learning:understanding_check`` /
    M層のコース単位モデル上書きも維持）。
    """
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

    material_text = material_text_for_prompt(topic, _topic_student_material(topic))
    requirements_text = "\n".join(f"- {r}" for r in answer_requirements) or "(未設定)"
    params = get_llm_params("fast")
    prompt = (
        "あなたは受講者の回答と出題の要件を並べて見せる補助役です。採点はしません。"
        "JSONのみを返してください。\n"
        "形式: {\"observations\": [{\"requirement\": \"回答に必要な要素（下のリストから"
        "そのまま転記）\", \"status\": \"covered\" または \"not_mentioned\", "
        "\"statement\": \"その要素について観察したことを推量形の1文で\"}], "
        "\"model_answer\": \"解答例\", \"explanation\": \"必要なら解説\"}\n\n"
        f"コース: {_course_title(course_data, default=course_id)}\n"
        f"セクション: {topic.get('title', topic_id)}\n"
        f"教材:\n{material_text[:5000]}\n\n"
        f"確認問題: {question}\n"
        f"解答例（設定済みの場合はこれを基準にする）:\n{expected_model_answer or '(未設定)'}\n\n"
        f"回答に必要な要素:\n{requirements_text}\n\n"
        f"解説（設定済みの場合は explanation に反映する）:\n{explanation or '(未設定)'}\n\n"
        f"受講者の回答: {body.answer}\n\n"
        f"{_answer_language_line(body.answer)}"
        "観点の書き方: 合否・正誤の判定は書かないでください。requirement は上の"
        "「回答に必要な要素」の文字列をそのまま使い、リストに無い要素を作らないでください"
        "（要素が未設定のときは observations を空にして構いません）。"
        # IK-0409: 要素が設定されているときは全要素に1件ずつ観点を付ける（要素5つのうち
        # 3つだけ見て残りを黙って落とさない）。
        "要素が設定されているときは、リストの**すべての要素について1件ずつ**、リストの順に"
        "observations を書いてください（要素を省かないでください）。statement は"
        "「…への言及は見当たらないようです」「…には触れているようです」のような推量形の"
        "1文にしてください。\n"
        # IK-0409: 英語で答えた受講者に日本語だけで返さない。
        "言語: statement・model_answer・explanation は**受講者の回答と同じ言語**で書いて"
        "ください（受講者が英語で答えたら英語で書く）。JSON のキー名と status の値"
        "（covered / not_mentioned）は変えないでください。requirement は上のリストの文字列を"
        "そのまま使ってください。\n"
        "禁止: 合格・不合格・正解・採点という語、点数・正解率・達成度のような数値、"
        "評価の言い切り、褒め言葉の羅列。次に進むかどうかを指示しないでください"
        "（それは受講者本人が決めます）。"
    )

    # M層 Phase 3（§6.4）: コース単位の学習チャットモデル上書きが設定されていれば
    # この並置にも適用する（live 設定、版ピンと独立）。未設定時は従来どおり fast tier 固定
    # （params）を使う — 挙動を変えない。
    _course_chat_model = get_course_live_llm_models(course_id).get(llm_policy.SCENE_LEARNING_CHAT)

    if _course_chat_model:
        # override 時は呼び出し引数として直接渡す（call_argument が最優先, §3-1）。
        # reasoning_effort は明示しない（カタログの既定 effort に委ねる）。
        _call_kwargs: dict = {"model": _course_chat_model}
    else:
        _call_kwargs = {
            "model": params["model"],
            "reasoning_effort": params["reasoning_effort"],
        }

    with usage_context("learning:understanding_check", user_id=current_user["id"], course_id=course_id):
        # 取り出しは共通実装へ委譲（``core/llm_worker/single_shot.py::json_call``）。
        _result = json_call(
            prompt,
            call=generate_text,
            temperature=0.1,
            degraded=None,
            log_label="check question juxtaposition",
            **_call_kwargs,
        )
    # 原則9 の degraded 規約: 判定を生まず、要件との見比べを本人に返す。
    # 旧実装の「40字以上なら合格」のような文字数フォールバックは作らない
    # （長く書けば通る、という演技を学ばせない）。
    degraded = _result is None
    parsed: dict = _result or {}
    if degraded:
        logger.warning("Check question juxtaposition failed; degrading to facts")

    # IK-0409: 要素が設定されていれば全要素ぶんの観点を受け取る（上限は要素数。
    # 要素が無いときの上限 MAX_OBSERVATIONS は従来どおり）。
    observations = check_review.parsed_observations(
        parsed, answer_requirements,
        limit=max(check_review.MAX_OBSERVATIONS, len(answer_requirements)),
    )
    # IK-0409: LLM が一部の要素にしか観点を付けなくても、残りを黙って落とさない
    # （「観点が得られなかった」事実を unclear で補う。degraded の往復は従来どおり空）。
    if not degraded:
        observations = check_review.fill_missing_requirements(observations, answer_requirements)
    covered, not_mentioned = check_review.split_observations(observations)
    statements = check_review.build_statements(body.answer, observations, degraded=degraded)
    model_answer = str(parsed.get("model_answer") or expected_model_answer or material_text[:800])
    response_explanation = str(parsed.get("explanation") or explanation or "")

    # 完了はここでは書かない（是正 F1）。返すのは現況だけで、確定は self-check 経路に移る。
    # 現況の取得に失敗しても並置レスポンス自体は落とさない（fail-open）。
    course_completed = False
    completed_topic_ids: list[str] = []
    try:
        completion = get_course_completion(current_user["id"], course_id, course_data)
        course_completed = bool(completion.get("course_completed"))
        completed_topic_ids = list(completion.get("completed_topic_ids") or [])
    except Exception:
        logger.warning(
            "Failed to read course completion for user=%s course=%s topic=%s",
            current_user["id"], course_id, topic_id, exc_info=True,
        )

    return LearningCheckQuestionResponse(
        advisory=True,
        degraded=degraded,
        statements=statements,
        observations=[LearningCheckObservation(**obs) for obs in observations],
        covered=covered,
        not_mentioned=not_mentioned,
        model_answer=model_answer,
        answer_requirements=answer_requirements,
        explanation=response_explanation,
        self_check_required=True,
        topic_completed=topic_id in completed_topic_ids,
        course_completed=course_completed,
        completed_topic_ids=completed_topic_ids,
    )


def _answer_language_line(answer: str) -> str:
    """受講者の回答の言語を決定論で見て、並置の言語を1行で指示する（IK-0409）。

    ラテン文字が文字の大半を占める回答だけ「英語で書く」と明示する（「受講者の回答と
    同じ言語で」だけでは日本語の指示文に引きずられて日本語で返ることがあった）。
    それ以外は空文字（従来どおり）。
    """
    text = str(answer or "")
    latin = sum(1 for ch in text if ("a" <= ch <= "z") or ("A" <= ch <= "Z"))
    cjk = sum(
        1 for ch in text
        if "\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff"
    )
    if latin >= 20 and latin > cjk * 4:
        return (
            "受講者の回答は英語で書かれています。statement・model_answer・explanation は"
            "英語で書いてください。\n\n"
        )
    return ""


def _self_check_disagreed_notice(user_id: str, course_id: str, topic_id: str) -> str:
    """「違っていた」の事実文。このトピックの直近の発話が英語なら英語で返す（IK-0450）。

    自己確認のリクエストは本文を持たないので、保存済みの会話の最後の発話で言語を選ぶ
    （読めなければ日本語 — fail-soft）。
    """
    last_user = next(
        (
            str(m.get("content") or "")
            for m in reversed(load_stored_chat_history(user_id, course_id, topic_id))
            if m.get("role") == "user" and str(m.get("content") or "").strip()
        ),
        "",
    )
    if last_user and _is_kana_kanji_free(last_user):
        return label_vocab.CHECK_SELF_CHECK_DISAGREED_NOTICE_EN
    return label_vocab.CHECK_SELF_CHECK_DISAGREED_NOTICE


@router.post(
    "/courses/{course_id}/topics/{topic_id}/check/self-check",
    response_model=LearningCheckSelfCheckResponse,
)
def self_check_topic_understanding(
    course_id: str,
    topic_id: str,
    body: LearningCheckSelfCheckRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningCheckSelfCheckResponse:
    """確認問題の並置を見たあとの自己確認（本人の 1 タップ・非LLM）。

    是正 F1: トピック完了を確定できるのはこの経路だけで、確定するのは本人が
    「合っていた」を押したときに限る（IK-0399: 「違っていた」は本人の見立てが要件と
    合わなかったという申告なので完了にしない — ``notice`` に事実文を返す）。
    ``disagreed`` / ``verdict_wrong``（AI の観点提示がおかしい）は申告として記録するが
    完了には使わない — かつ進行を止めない（トピックはロック表示でも開ける）。語彙は R層と共有
    （``core/reconstruction/schema.py::SELF_CHECK_VALUES``）。
    """
    self_check = str(body.self_check or "").strip()
    if self_check not in check_review.SELF_CHECK_VALUES:
        raise HTTPException(status_code=422, detail="invalid self-check value")

    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")
    topic = find_course_topic(course_data, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    topic_completed = False
    course_completed = False
    completed_topic_ids: list[str] = []
    try:
        if self_check in check_review.SELF_CHECK_ADVANCING:
            completion = record_topic_check_pass(
                current_user["id"], course_id, topic_id, course_data,
            )
            topic_completed = bool(completion.get("topic_completed"))
        else:
            # 申告の記録（本人の逐語・回答は残さない。「違っていた」の申告 / AI の観点提示への
            # 異議という事実だけ。IK-0399: disagreed も完了にしない）。
            logger.info(
                "check self-check %s course=%s topic=%s", self_check, course_id, topic_id,
            )
            completion = get_course_completion(current_user["id"], course_id, course_data)
        course_completed = bool(completion.get("course_completed"))
        completed_topic_ids = list(completion.get("completed_topic_ids") or [])
    except Exception:
        logger.warning(
            "Failed to record check self-check for user=%s course=%s topic=%s",
            current_user["id"], course_id, topic_id, exc_info=True,
        )

    return LearningCheckSelfCheckResponse(
        self_check=self_check,
        topic_completed=topic_completed,
        course_completed=course_completed,
        completed_topic_ids=completed_topic_ids,
        # IK-0399: 「違っていた」は完了にしない。その事実を1文で返す（数字なし）。
        notice=(
            _self_check_disagreed_notice(current_user["id"], course_id, topic_id)
            if self_check == "disagreed" else ""
        ),
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
    # IK-0444: 出典番号の対応表の控えはサーバの内部の記帳。学習者向けの履歴には載せない。
    history = [
        {k: v for k, v in m.items() if k != CITATION_MAP_KEY} if isinstance(m, dict) else m
        for m in history
    ]
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


def _run_learning_turn(gen) -> LearningChatResponse:
    """``_learning_chat_core`` の generator を同期に回し、最終 DTO だけを返すドライバ。

    ストリーミング Phase 3-a（設計書 §3.3）: 非ストリーム経路は本関数を通ることで、
    前処理・後処理を1つの関数に保ったまま従来と同じ ``LearningChatResponse`` を返す
    （途中のイベントは捨てる = ST7「非ストリーム API は不変」）。
    """
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value


def _stream_answer(messages: list[dict], *, model: str, usage_ctx: dict):
    """本文を逐次生成し ``("delta", text)`` を yield、全文を ``return`` する。

    ストリーミング Phase 3-a（設計書 §3.3）: **このモジュールで
    ``generate_text_stream`` を呼ぶのはここ1箇所**。U層の帰属は値渡し
    （``usage_ctx``）で、contextvar を yield を跨いで開かない（§3.2）。
    """
    chunks: list[str] = []
    for piece in generate_text_stream(messages=messages, temperature=0.3, model=model, usage_ctx=usage_ctx):
        if not piece:
            continue
        chunks.append(piece)
        yield ("delta", piece)
    return "".join(chunks)


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

    ストリーミング Phase 3-a（設計書 §3.3）でコアが generator になったため、
    ``_run_learning_turn`` で同期に回して従来と同じ DTO を返す（ST7）。
    """
    return _run_learning_turn(_learning_chat_core(course_id, topic_id, body, current_user))


def _learning_chat_core(
    course_id: str,
    topic_id: str,
    body: LearningChatRequest,
    current_user: dict,
    *,
    course_data: dict | None = None,
    scope_document_ids: set[str] | None = None,
    stream: bool = False,
):
    """学習チャット本体（コース経路 / document 直付け経路の共通コア）。

    **generator 関数**（ストリーミング Phase 3-a, 設計書 §3.3「生成器の継ぎ目」）。
    前処理・後処理を2つのエンドポイントが別々に持たないための構造で、値は
    ``return LearningChatResponse(...)``（＝ ``StopIteration.value``）で返る。
    同期に回すときは ``_run_learning_turn(...)`` を通す（イベントは捨てられる）。
    ``stream=True`` のときだけ本文が ``("delta", text)`` として流れる。

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
    else:
        _trunc = None

    # IK-0432: 出典番号を会話の中で固定する。保存済み履歴の assistant ターンが持つ
    # ``sources`` から「チャンク → 番号」を引き、描画メタを落として送り返された往復には
    # 保存済みのメタを戻す（その往復の保存で sources を失わない）。書き直しで切り詰めた
    # 場合は body.history がサーバ正本そのものなので追加の読み出しをしない。
    _stored_history = [] if _trunc is not None else load_stored_chat_history(
        current_user["id"], course_id, topic_id
    )
    body.history = _rehydrate_history_sources(body.history, _stored_history)
    # IK-0466: 書き直しで取り除いた往復が出した番号も予約する（その番号を別のチャンクへ
    # 振り直さない — 学習者の画面には直前まで出ていた番号）。
    _removed_turns = list((_trunc or {}).get("removed_history") or [])
    _citation_numbers = _SessionCitationNumbers(_stored_history, body.history, _removed_turns)
    body.history = _carry_citation_map(body.history, _citation_numbers)

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
            # 分野語彙（内容質問の誤爆ガード）はコースのカートリッジから読む
            # （分野名をコードに書かない。導出できないコースでは分野非依存語のみ）。
            or _is_usage_question(
                body.message,
                cartridge_id=course_cartridge_id(course_data) or None,
            )
        )
        if _is_usage_help:
            with usage_context("learning:help_usage", user_id=current_user["id"], course_id=course_id):
                return _usage_help_response(
                    current_user["id"], course_id, topic_id, body,
                    on_llm_call=_consume_quota,
                )

        # IK-0397: 理解度を点数・割合で求める発話は、意図分類（LLM）の前に固定の事実文で
        # 答える（非LLM・quota 非消費・数値非表示の原則を LLM に言い換えさせない）。
        # 痕跡は新しい kind を作らない（学習相談の一般アドバイスと同じく記録しない）。
        if _is_understanding_score_request(body.message):
            _score_reply = (
                label_vocab.UNDERSTANDING_SCORE_REQUEST_REPLY_EN
                if _is_kana_kanji_free(body.message)
                else label_vocab.UNDERSTANDING_SCORE_REQUEST_REPLY
            )
            persist_chat_history(
                current_user["id"], course_id, topic_id,
                body.history, body.message, _score_reply,
                user_message_id=body.message_id or None,
                assistant_meta={"content_grounding": "model_generated"},
            )
            return LearningChatResponse(
                answer=_score_reply,
                course_update=None,
                content_grounding="model_generated",
            )

        # IK-0434: お礼・締めくくりだけの発話は、検索も回答生成もせず固定の1文で応じる
        # （非LLM・quota 非消費・出典なし・問い返しなし。discuss も同じ）。typed action・
        # テキスト選択・理解サイクル・確認問題の壁打ちがある往復は対象にしない（狭く保つ）。
        if (
            not body.support_action
            and not (body.selection_text or "").strip()
            and not (body.cycle_mode or "").strip()
            and not body.check_scaffold
            and _is_closing_utterance(body.message)
        ):
            # 定型句だけの短い発話なので、「かな・漢字を含まない」を英語の目印にする
            # （``_is_mostly_latin`` の 8 文字基準では "Thanks!" を拾えない）。
            _closing_reply = (
                label_vocab.CLOSING_UTTERANCE_REPLY_EN
                if _is_kana_kanji_free(body.message)
                else label_vocab.CLOSING_UTTERANCE_REPLY
            )
            # IK-0447: 締めくくりの定型文は内容の説明ではないので出所の分類を付けない
            # （model_generated にすると「出典なしの AI の説明」の出所行が付く）。
            persist_chat_history(
                current_user["id"], course_id, topic_id,
                body.history, body.message, _closing_reply,
                user_message_id=body.message_id or None,
            )
            return LearningChatResponse(
                answer=_closing_reply,
                course_update=None,
                content_grounding=None,
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

    # IK-0383: 前提確認の逆質問への答え・前提そのものへの問い（決定論・非LLM）。
    #   - "explain": 前提そのものの説明（是正 F4 の3段解決）へ流す。typed action が無い往復だけ
    #     （UI ボタンの明示は従来どおり優先する）。下の一次判定の直後で LEARNING_ADVICE を確定し、
    #     意図分類の LLM コールを省く。
    #   - "after_gate": 直前が逆質問だった。逆質問を2回続けて出さない（下の前提ゲートで捨てる）。
    #   楽屋（backstage）は通常の RAG 回答のまま（記録面の私有化を崩さない）で、前提ゲート自体を
    #   通さない。casual / discuss / 地図アクション / 確認問題の壁打ちも従来どおり対象外。
    _prereq_followup: str | None = None
    _prereq_target: str | None = None
    if not (_is_casual or _is_discuss or _atlas_ctx or _is_backstage or body.check_scaffold):
        _prereq_followup, _prereq_target = _prerequisite_followup(
            body.message, body.history, topic_info, topic_title, course_data,
        )
        if _prereq_followup == "explain" and _route_for_typed_action(body.support_action):
            # 明示の typed action が勝つ（逆質問の繰り返しだけは after_gate として抑える）。
            _prereq_followup, _prereq_target = "after_gate", None
    # IK-0396: 逆質問に「理解している」と答えた往復は、記帳（check_prerequisites 側）のうえで
    # 逆質問を引き起こした**元の質問**に答える。元の質問は履歴の逆質問の直前の学習者発話。
    # 同じリクエストの中で通常の RAG 経路を通し（回答の LLM は1回・意図分類は追加しない）、
    # 回答の先頭に事実文を1行添える。見つからなければ従来どおり（この発話そのものに応じる）。
    _prereq_resume_question: str | None = None
    # IK-0424: 逆質問の直後の素の「はい / yes」も、逆質問への明示的な答えとして読む。
    _prereq_bare_yes = _prereq_followup == "after_gate" and _is_bare_yes(body.message)
    if _prereq_followup == "after_gate" and (
        _is_explicit_prerequisite_acknowledgement(body.message) or _prereq_bare_yes
    ):
        _prereq_resume_question = _question_before_prerequisite_gate(body.history, topic_title)
    # この往復で答える問い（検索・生成・教材の関わり・痕跡の本文）。保存する学習者発話は
    # body.message のまま（本人が打った文を書き換えない）。
    _turn_question = _prereq_resume_question or body.message
    # IK-0424: 「Yes, I understand. … which part …?」のように、理解の答えに新しい問いを続けた
    # 往復では、元の質問だけに差し替えず新しい問いも併せて答える（新しい問いを落とさない）。
    if _prereq_resume_question and not _prereq_bare_yes and _is_question_shaped(body.message):
        _turn_question = f"{_prereq_resume_question}\n\n{body.message}"

    # 入口統合 Phase 1（docs/features/learning_chat_entry_unification_design.md §4.2 の
    # [2] 段）: 非LLM の一次判定。「明らかに教材内容の問い」だけを DOMAIN_RAG として
    # 先に確定させ、意図分類の LLM コールを省く（LC5: どの経路でも現行を上回らない）。
    #   - 明示の様相（casual / discuss / 地図アクション）が立っている往復では推定器を
    #     走らせない（LC2: 明示は常に推定に勝つ。特に discuss 中の casual 推定は
    #     スコープ表示との食い違い・痕跡の帰属漏れを起こすので構造的に禁止）。
    #   - 挨拶・「はい…理解」の決定論ショートカット（_classify_intent 冒頭）は
    #     先取りしない — _is_greeting が偽のときだけ計算する。
    #   - 分野語はコードに書かない（分野非依存語 + コースのカートリッジ ontology 由来語を
    #     渡す。cartridge_id が空なら _cartridge_content_terms を呼ばない）。
    #   - 入力は**当該発話だけ**（履歴・過去の様相・学習者モデルを使わない = LC4）。
    _prejudged: str | None = None
    if not (_is_casual or _is_discuss or _atlas_ctx) and not _is_greeting(body.message):
        _stance_cartridge_id = course_cartridge_id(course_data) or ""
        _prejudged = prejudge_stance_route(
            body.message,
            content_terms=_CONTENT_QUESTION_TERMS
            + (
                _cartridge_content_terms(_stance_cartridge_id)
                if _stance_cartridge_id
                else ()
            )
            # IK-0476: コースのトピック題名・概念名（分野未指定のコースでも教材の語を知る）。
            + _course_content_terms(course_data),
        )
    if _prereq_followup == "explain":
        _prejudged = "LEARNING_ADVICE"  # IK-0383: 前提そのものの説明（上の判定）
    if _prereq_resume_question:
        _prejudged = "DOMAIN_RAG"  # IK-0396: 元の質問に答える（分類の LLM を追加しない）
    # 明示 casual（音声ループ等が intent_mode="casual" を送った往復）と、CHIT_CHAT 判定から
    # 合流する推定 casual_light を後段で区別するため、再代入より前の値を控える（LC6）。
    _explicit_casual = _is_casual

    # 2. 意図分類（Intent Routing）— UI ボタン由来の型付きアクションは分類を経由しない。
    #    discuss は casual と同様に意図分類（雑談拒否）をバイパスする（設計 §6.2）。
    with usage_context("learning:chat", user_id=current_user["id"], course_id=course_id):
        intent = None if (_is_casual or _is_discuss or _atlas_ctx) else (
            _route_for_typed_action(body.support_action)
            or _prejudged
            or _classify_intent(body.message, course_title, on_llm_call=_consume_quota)
        )

    # ルート①: 雑談まじりの発話 → **拒否しない**。軽い調子（casual_light）の様相として
    # そのまま通常の RAG フローへ合流させる（入口統合 Phase 1 設計 §4.3、オーナー判断 §12-1）。
    #
    # 旧実装はここで定型の拒否文（「…学習支援に特化したAIです」）を返して早期 return して
    # いた。これは casual が丸ごとバイパスしていた分岐そのもので、「casual のテキスト入口が
    # 1つも無い」ことの裏返しだった。拒否をやめても**根拠の一線は落ちない** — RAG 検索・
    # tier 集約・OutOfSourceGuard の system 注入・content_grounding はこの下流で全経路共通に
    # 効き、教材に無い話題は model_generated と正直に返る（原則8）。
    #
    # 実装は `_is_casual` の**再代入だけ**（LC8: 下流の条件式は無改変）。分類はこの行より
    # 手前で走り終えているので、前提知識ゲート・プロンプト選択・notice 抑制・誤解検出・
    # U層タグ・痕跡・detour 非化のすべてに自然に効く。使い方についての再誘導は HELP
    # pre-route と分類の USAGE_HELP 委譲が担い、ここでは扱わない（経路の一本化）。
    if intent == "CHIT_CHAT":
        _is_casual = True

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
        # 是正 F4（2026-09-10）: 前提知識の説明だけは3段解決を通す。①同コース topic /
        # ②本人が閲覧できる document のチャンク（検索1回）→ その抜粋を同じ1コールへ渡し、
        # ③どこにも無ければ model_generated として返す。どの分岐でも
        # `content_grounding` を None にしない（原則8: 出所の正直さ）。
        # IK-0383: 逆質問への「いいえ・教えて」と前提そのものへの問いは、その前提1つの説明にする。
        _explain_target = _prereq_target if _prereq_followup == "explain" else None
        is_prereq = (
            body.support_action in _PREREQUISITE_ACTIONS
            or LearningSupportAgent.is_prerequisite_request(body.message)
            or bool(_explain_target)
        )
        prereq_context: dict | None = None
        if is_prereq:
            prereq_context = _resolve_prerequisite_context(
                current_user["id"], course_data,
                [_explain_target] if _explain_target else _prerequisite_terms(body.message, topic_info),
                citation_numbers=_citation_numbers,
            )
        with usage_context("learning:chat", user_id=current_user["id"], course_id=course_id):
            advice_answer = _generate_learning_advice_response(
                course_title, topic_title, body.message,
                topic_info=topic_info, course_data=course_data,
                on_llm_call=_consume_quota,
                source_context=(prereq_context or {}).get("context_block"),
                explain_prerequisite=_explain_target,
            )
        advice_answer, inline_actions = extract_inline_actions(advice_answer)
        if is_prereq and prereq_context is not None:
            # IK-0430: 根拠の無い [出典N]（番号付き出典の無い抜粋への捏造）を本文から除く。
            advice_answer = _reconcile_citation_markers(
                advice_answer, {s["index"] for s in prereq_context["cited_sources"]}
            )
            # 解決できなかった前提は、閉世界の事実文でサーバ側から添える（LLM に
            # 言わせない・分野レベルの不在は言わない, SL1）。
            _closed_world = _prerequisite_closed_world_note(prereq_context["unresolved"])
            if _closed_world:
                advice_answer = f"{advice_answer}\n\n{_closed_world}"
            # 前提確認は detour（origin=現在アンカー）。復帰導線を必ず付ける。
            result = support_agent.with_learning_actions(
                answer=advice_answer,
                mode="prerequisite_review",
                origin=support_origin,
                extra_actions=inline_actions,
            )
            # IK-0494: 見せる出典は本文が引用したものだけ（番号は振り直さない）。
            _prereq_sources = _displayed_sources_for(
                result.answer, prereq_context["cited_sources"], prereq_context["content_grounding"]
            )
            _prereq_grounding = prereq_context["content_grounding"]
            _prereq_tier = prereq_context["overall_tier"]
            persist_chat_history(
                current_user["id"], course_id, topic_id,
                body.history, body.message, result.answer,
                assistant_meta={
                    "sources": _history_source_meta(_prereq_sources),
                    "overall_tier": _prereq_tier,
                    "content_grounding": _prereq_grounding,
                    CITATION_MAP_KEY: _citation_numbers.mapping(),
                },
            )
            return LearningChatResponse(
                **result.model_dump(),
                course_update=None,
                sources=_prereq_sources,
                overall_tier=_prereq_tier,
                content_grounding=_prereq_grounding,
            )
        # 学習開始・一般アドバイスはパス上（detour ではない）。前進アクションを型付きで提示。
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, advice_answer,
            assistant_meta={"content_grounding": "model_generated"},
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
            # 学習相談の一般アドバイスは資料に基づかない（コース構造とモデルの知識だけ）。
            # 出所を空欄のままにせず model_generated と正直に言う（是正 F4 / 原則8）。
            content_grounding="model_generated",
        )

    # 3. Adaptive Routing: 前提知識の自動判定 (ルート③/④の前に実行)
    # casual / discuss モードでは会話を止めない（前提確認の逆質問ゲートを挟まない）。
    prerequisite_intervention = None if (_is_casual or _is_discuss or _atlas_ctx) else check_prerequisites(
        current_user["id"], course_id, course_data, topic_title,
        # IK-0424: 逆質問の直後の素の「はい / yes」は、逆質問への明示的な答えとして記帳させる
        # （check_prerequisites の記帳判定が読む定型の答えに置き換えて渡す。保存する発話は不変）。
        "はい、理解しています" if _prereq_bare_yes else body.message,
    )
    # IK-0383: 楽屋は前提ゲートを通さない（楽屋の問いを逆質問に吸い込ませない）。直前が逆質問
    # だった往復でも同じ逆質問を繰り返さない。check_prerequisites は呼んだまま（「理解している」の
    # 明示的な答えの記帳はそちらの責務。否定形は記帳しない）で、介入だけを捨てる。
    # IK-0422: 逆質問は (トピック, セッション) につき1回。履歴のもっと前に同じトピックの逆質問が
    # ある往復（gated_before）と、書き直し（replace_message_id。切り詰めで消えたのは逆質問の
    # 往復であり得る）でも出し直さない。確認がまだ記録されていないことは事実文1行で添える。
    _prereq_gate_skipped_for: str | None = None
    if (
        prerequisite_intervention
        and not _is_backstage
        and not _prereq_resume_question
        and (_prereq_followup in ("after_gate", "gated_before") or body.replace_message_id)
    ):
        _prereq_gate_skipped_for = (
            str(prerequisite_intervention.get("first_prerequisite") or "").strip() or None
        )
    if _is_backstage or _prereq_followup in ("after_gate", "gated_before") or body.replace_message_id:
        prerequisite_intervention = None
    if prerequisite_intervention:
        choice_actions = support_agent.prerequisite_choice_actions(
            prerequisite_intervention.get("first_prerequisite", "")
        )
        _gate_answer = prerequisite_intervention["message"]
        if _is_mostly_latin(body.message):
            # IK-0424: 英語で問うた受講者に日本語だけの逆質問を返さない（英語の1文を添える）。
            _gate_answer = _gate_answer + "\n\n" + label_vocab.PREREQUISITE_GATE_EN.format(
                prerequisite=prerequisite_intervention.get("first_prerequisite", "")
            )
        result = support_agent.with_learning_actions(
            answer=_gate_answer,
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
    # IK-0364: 質問文の埋め込み（RAG 検索）も当該ターンの feature に帰属させる。分岐の正本は後段の
    # `_chat_feature` の決定（cycle > discuss > casual > chat）で、ここはそれを同じ順序で先取りする
    # （test_llm_usage_attribution が両者の一致を固定する）。generator の yield はこの with の外にある。
    _retrieval_feature = _cycle_chat_feature or (
        "learning:chat_discuss" if _is_discuss else ("learning:chat_casual" if _is_casual else "learning:chat")
    )
    # IK-0472: トピック内の問い（discuss 以外）では、トピックが束ねる論文のチャンクを先に並べる
    # （コースの別の論文のチャンクが類似度だけで上位を占めない）。検索は1回のまま、候補を
    # 少し多めに取って並べ替えてから 8 件に切る（決定論・安定ソート）。
    _topic_doc_ids = (
        topic_source_document_ids(topic_info)
        if topic_info and not _is_discuss and scope_document_ids is None
        else set()
    )
    with usage_context(_retrieval_feature, user_id=current_user["id"], course_id=course_id):
        chunk_results = search_chunks_with_metadata(
            _retrieval_query_for_turn(body, _turn_question),
            top_k=12 if _topic_doc_ids else 8, allowed_document_ids=allowed_document_ids,
        )
    if _topic_doc_ids:
        chunk_results = _prefer_topic_documents(chunk_results, _topic_doc_ids)[:8]
    cited_chunks = []
    cited_sources: list[dict] = []  # L1: 文脈に採用した根拠の tier 一覧
    has_topic_material = False
    # IK-0382: 注入したトピック教材を「この回答の根拠」と数えてよいか。注入（プロンプト）は
    # 従来どおりで、出所分類と tier 下限だけがこの判定に従う。
    topic_material_grounds = False
    if topic_info:
        topic_material = material_text_for_prompt(topic_info, _topic_student_material(topic_info))
        if topic_material:
            has_topic_material = True
            topic_material_grounds = _topic_material_engages_message(body, topic_material, message=_turn_question)
            cited_chunks.append(
                f"[現在表示中の教材]\n{sanitize_source_text_for_prompt(topic_material[:5000])}"
            )
    for r in chunk_results:
        if r["score"] >= 0.30:
            # 出典番号は会話の中で固定（IK-0432）。cited_sources と 1 対 1 のまま。
            _source = _adopted_source_entry(_citation_numbers, r, course_material_ids)
            cited_sources.append(_source)
            # IK-0492: 文脈に置く写しだけを整える（U+FFFD・arXiv の版の刻印。保存データは不変）。
            cited_chunks.append(
                f"[出典{_source['index']}] 『{r['source_title']}』\n"
                f"{sanitize_source_text_for_prompt(r['text'])}"
            )
    # IK-0475: 直前の回答が本文で引用したチャンクのうち今回の検索に無いものを、元の番号の
    # まま文脈へ戻す（最大3件・1件1200字）。番号の対応は会話の採番器（IK-0432/0444）と一致する
    # ものだけ、可視性は今回の allowed_document_ids を SQL 内で強制する（範囲を広げない）。
    # 予想を引き出す往復（elicit）では前の回答の根拠を手渡さない。
    if _cycle_mode != "elicit":
        _carried = _carry_previous_cited_sources(
            body.history,
            _citation_numbers,
            exclude_chunk_ids={s["chunk_id"] for s in cited_sources},
            allowed_document_ids=allowed_document_ids,
        )
        for r in _carried:
            _source = _adopted_source_entry(_citation_numbers, r, course_material_ids)
            cited_sources.append(_source)
            cited_chunks.append(
                f"[出典{_source['index']}] 『{r['source_title']}』（前の回答で引用した箇所）\n"
                f"{sanitize_source_text_for_prompt(r['text'])[:_CARRIED_CHUNK_MAX_CHARS]}"
            )

    # L1: 回答全体の格を最弱根拠へ安全側集約。採用根拠が無ければ未踏(out_of_source)。
    overall_tier = aggregate_overall_tier([s["tier"] for s in cited_sources])
    # トピック教材が問いに関わっているなら回答には実根拠があり、out_of_source
    # （「教材の裏づけなし」バナー + 未踏ガード）は事実と矛盾する。承認チェーン由来
    # ではないため approved には昇格させず、source を下限に引き上げる。
    # IK-0382: 「注入した」だけでは根拠にしない（教材と無関係な問いで 📘 教材から回答 と
    # 表示しない）。関わりの判定は `_topic_material_engages_message`（決定論）。
    if topic_material_grounds:
        overall_tier = tier_floor(overall_tier, TIER_SOURCE)
    # 回答内容の出所分類（tier=教員承認状況とは別軸）:
    #   教材(このコース) > 別の資料 > 出典を追えないモデル生成、の優先度で決める。
    if topic_material_grounds or any(s["origin"] == "course_material" for s in cited_sources):
        content_grounding = "course_material"
    elif cited_sources:
        content_grounding = "other_material"
    else:
        content_grounding = "model_generated"

    if cited_chunks:
        # 信頼境界（正本: docs/architecture/trust_boundary_pdf_input.md）: cited_chunks は
        # PDF / URL 取得 / arXiv 由来の本文（第三者が書いた untrusted 入力）。区切り
        # （`[出典N]` ラベル + `---`）に加えて、指示として解釈しない旨をここで明示する。
        context_block = (
            "## 関連する教材のコンテキスト\n"
            + UNTRUSTED_SOURCE_NOTICE + "\n\n"
            + "\n---\n".join(cited_chunks)
        )
    elif _is_discuss:
        # DM1（出所の正直さ）: discuss は該当チャンクが無くても他スコープへ無断で
        # 広げない。範囲を広げていない事実と、範囲外知識を使う場合の出所明示を指示する。
        context_block = (
            "※選択中の検索範囲には、この質問に直接関連する箇所は見当たりませんでした。"
            "範囲は広げていません。一般的な学術知識で回答する場合は、この論文由来ではないことを明示してください。"
        )
        # 楽屋（backstage）の質問は本人専用（SD4 / 原則5）。unanswered_query_logs は
        # 教員の「未回答の質問」表に氏名付きで出る経路なので、楽屋では記録しない。
        if not _is_backstage:
            log_unanswered_query(current_user["id"], course_id, topic_id, body.message)
    else:
        context_block = "※この質問に直接関連する教材セクションは見つかりませんでした。一般的な学術知識を用いて回答してください。"
        if not _is_backstage:
            log_unanswered_query(current_user["id"], course_id, topic_id, body.message)

    # IK-0436: document 直付けの議論（論文の海）では、開幕画面と同じ論文の問い・目的・主張を
    # 事実行として文脈の先頭に置く（要約の問いはチャンク検索で当たらないことがある）。
    # 番号付き出典ではない（cited_sources・content_grounding に数えない）。スコープは広げない（DM1）。
    _document_thesis_lines: list[str] = []
    if _document_context_id and _is_discuss:
        _document_thesis_lines = document_thesis_fact_lines(_document_context_id)
    if _document_thesis_lines:
        context_block = (
            _DOCUMENT_THESIS_HEADING + "\n"
            + UNTRUSTED_SOURCE_NOTICE + "\n"
            + "\n".join(_document_thesis_lines)
            + "\n\n" + context_block
        )

    # 入口統合 Phase 1（設計 §4.1 / §4.4）: casual に畳まれていた「様相（軽い調子）」と
    # 「伝達形式（読み上げ向き）」を分離する。読み上げ向きに倒すのは
    #   ① 画面が音声モード（body.screen_mode == "voice"。app.js が全送信経路で付与）
    #   ② 明示 casual かつ screen_mode 未指定（後方互換 — 既存 API クライアント・
    #      既存テストは intent_mode="casual" 単独で音声想定の応答を期待している）
    # の2つだけで、テキストから推定された casual_light は spoken=False になる。
    _casual_spoken = ((body.screen_mode or "").strip() == "voice") or (
        _explicit_casual and not (body.screen_mode or "").strip()
    )

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
        _system_prompt = _get_casual_teacher_system_prompt(
            domain, response_persona, spoken=_casual_spoken,
        )
    else:
        _system_prompt = _get_integrated_tutor_system_prompt(domain, response_persona)
    # 確認問題の壁打ちモード: どのモードの system プロンプトに対しても、解答の直接提示を
    # 禁じ・要素の説明は許し・組み立ては学習者に委ねる拘束を末尾へ追記する。
    if body.check_scaffold:
        _system_prompt += "\n\n" + _CHECK_SCAFFOLD_INSTRUCTION
    # IK-0378: 未踏ガードは**採用した根拠が1つも無いとき**（出所が model_generated）だけ付ける。
    # 旧条件の ``overall_tier == out_of_source`` は最弱集約なので、類似度が 0.30〜0.45 の
    # 出典が1件混じるだけで 8〜14 出典の回答にもガード（先に「確認できない」と言い予想を
    # 求める）が付き、tutor の即答・discuss の〔鏡〕と衝突していた。tier の値（UI の格表示）
    # は変えない。
    _no_adopted_grounding = content_grounding == "model_generated"
    # IK-0471: ガードは教材で確かめる**内容の問い**にだけ付ける。雑談（CHIT_CHAT → casual_light）
    # とお礼・締めくくりの発話に付けると、挨拶への返答に「教材では確認できない」「教材で
    # 確かめる対象はありません」が混ざる（第 11 周 00040 / 00072）。
    _guard_applies = (
        _no_adopted_grounding
        and intent != "CHIT_CHAT"
        and not _is_closing_led_statement(body.message)
    )
    if _guard_applies and _is_discuss:
        # IK-0473: discuss は「質問には即答」（規則1）なので、ガードの「予想を先に引き出す」を
        # 外した形にする（出所の正直さ = DM1 の事実行と「教材では確認できない」の明示は残す）。
        _system_prompt += "\n\n" + _DISCUSS_OUT_OF_SOURCE_GUARD
        if _document_thesis_lines:
            _system_prompt += "\n" + _DOCUMENT_THESIS_GUARD_PRECEDENCE
    elif _guard_applies:
        _system_prompt += "\n\n" + out_of_source_guard_instruction() + "\n" + _OUT_OF_SOURCE_GUARD_PRECEDENCE
        if _document_thesis_lines:
            _system_prompt += "\n" + _DOCUMENT_THESIS_GUARD_PRECEDENCE
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
    # IK-0495: 以前は足場の user ターンの直後に「はい、（トピック名）について…答えます」
    # のような assistant ターンを**作り話で**置いていた（モデルが言っていない発話を履歴に混ぜる・
    # 日本語固定で英語の会話にも入る）。応じ方の指示は足場の user ターンの指示文に含め、
    # assistant ターンは作らない（足場の直後は実際の会話履歴か、今回の発話が続く）。
    if body.check_scaffold:
        _scaffold_user_instruction = (
            "上記のコンテキストを踏まえ（不足している場合は補完して）、"
            "壁打ちモードの規則に従って学習者の発話に応じてください。"
            "答えの組み立ては学習者に委ね、構成要素の説明と問いかけで支援してください。"
        )
    elif _is_discuss:
        _scaffold_user_instruction = (
            "上記のコンテキストを踏まえ（不足している場合は補完して）、"
            "発話タイプ別の応答ルールに従って、以下の学生の発話に応じてください。"
            "学生の発話のタイプ（質問 / 解釈・立場の表明 / 詰まり）を見きわめて応じてください。"
        )
    else:
        _scaffold_user_instruction = (
            "上記のコンテキストを踏まえ（不足している場合は補完して）、以下の質問に答えてください。"
        )
    messages: list[dict] = [
        {"role": "system", "content": _system_prompt},
        {"role": "user", "content": (
            f"コース: {course_title}\n"
            f"現在のトピック: {topic_title}\n\n"
            f"{context_block}\n\n"
            f"{_scaffold_user_instruction}"
        )},
    ]
    # チャット型AI支援の共通基盤整理 §2-2: 直近20メッセージへウィンドウ化
    # （教材・RAGコンテキストは上の messages で毎回別途注入されるため先頭保護は不要, head_keep=0）。
    # IK-0394: 1件の上限は 4000 字（tutor の回答は 2000 字超が常態で、途中で切れた回答が
    # 再注入されていた）。超えるときは段落・文の境界で切って「…」を付ける（数式は割らない）。
    # IK-0378: 表示用の注意書きは回答本文ではないので剥がしてから渡す。
    _prompt_history = _history_without_out_of_source_notice(body.history)
    for turn in window_history(_prompt_history, max_messages=20, max_chars=4000, trim_at_boundary=True):
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": _turn_question})

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

    # -----------------------------------------------------------------------
    # 画面文脈アダプター Phase 4（assistant_screen_adapter_design.md §11.4 / §11.5）
    #
    # 位置: **CostGate の直後・generate_text の前**（429 で返るリクエストでは射影を
    # 走らせない = §11.6）。当該ターンの user メッセージだけを
    # 「画面文脈ブロック → 選択箇所ブロック → 発話」に組み替える。足場ターン
    # （messages[1]）には混ぜない — 足場は毎回同一に組み直す土台で、画面はターンごとに
    # 変わるため（§11.5）。**保存（persist_chat_history）と痕跡は body.message の
    # ままで不変**（SA6）。
    #
    # モード別（§11.5 の表）:
    #   casual            → 画面文脈ブロックなし・選択ブロックあり（短い会話調と衝突する）
    #   cycle_mode=elicit → 表示モードの事実1行だけ（主張本文・検証事実は問いの答えの
    #                       手渡しになる）・選択ブロックあり
    #   それ以外          → 両方（tutor / discuss / diff / 楽屋 / 確認問題の壁打ち）
    # -----------------------------------------------------------------------
    _screen_block = ""
    _screen_has_ledger = False
    if body.screen_context is not None and not _is_casual:
        # 解決に使う document 集合は**明示スコープ**だけ（discuss の all_visible でも
        # 広げない = 画面文脈が範囲を広げてはならない・DM1 / §11.5）。画面文脈を
        # 送ってこないリクエストでは1クエリも増やさない（従来と完全に同じ経路）。
        _screen_grounding_document_ids = (
            set(scope_document_ids)
            if scope_document_ids is not None
            else set(list_course_source_document_ids(course_data))
        )
        _screen_block, _screen_has_ledger = _learning_screen_context_block(
            body,
            course_id=course_id,
            course_data=course_data,
            topic_info=topic_info,
            grounding_document_ids=_screen_grounding_document_ids,
            kinds=("view",) if _cycle_mode == "elicit" else None,
        )
    # -----------------------------------------------------------------------
    # 知識の転用層 P4-2（knowledge_transfer_design.md §5）— RAG の構造 1 hop
    #
    # 入口は**回答に採用した出典**なので ``screen_context`` が無いターンでも働く。
    # モード別は画面文脈ブロックと同じ考え方（casual は短い会話調と衝突する /
    # elicit は主張本文が問いの答えの手渡しになる）。スコープは当該ターンの
    # ``allowed_document_ids`` そのままで、構造側で広げない（KT6）。
    # -----------------------------------------------------------------------
    _retrieved_block = ""
    if not _is_casual and _cycle_mode != "elicit":
        _retrieved_block = _learning_retrieved_structure_block(
            cited_sources, allowed_document_ids
        )
    _selection_block = render_selection_block(
        body.selection_text,
        _topic_student_material(topic_info) if topic_info else "",
    )
    if _screen_block or _retrieved_block or _selection_block:
        # 信頼境界（TB1〜TB4）: 画面文脈ブロック（claim 抜粋・逐語引用を含む）・検索由来の
        # 構造ブロック（claim 本文）・選択箇所ブロックは PDF 由来の untrusted 入力を運ぶので、
        # ``UNTRUSTED_SOURCE_NOTICE`` を添える。足場ターン（messages[1]）に既に同じ文が
        # あるときは重複させない（cited_chunks が空のターンでは足場に注意書きが無い —
        # そのときだけここで補う）。
        _turn_parts = [_screen_block, _retrieved_block, _selection_block, _turn_question]
        if UNTRUSTED_SOURCE_NOTICE not in str(messages[1].get("content") or ""):
            _turn_parts.insert(0, UNTRUSTED_SOURCE_NOTICE)
        messages[-1] = {
            "role": "user",
            "content": "\n\n".join([part for part in _turn_parts if part]),
        }
    if _screen_has_ledger:
        # §11.13-2 の2段構え: 台帳の事実を渡すときだけ、出力側にも閉世界の拘束を掛ける
        # （SL1 の denylist はサーバが書く文字列にしか効かないため）。
        messages[0] = {
            "role": "system",
            "content": messages[0]["content"] + "\n\n" + LEARNING_VERIFICATION_OUTPUT_CONSTRAINT,
        }
    if _screen_block or _retrieved_block:
        # §11.7: 構造 grounding が載ったターンの**種別だけ**を1ビット記録する
        # （payload は常に空・痕跡には焼き込まない・学習者には見せない）。
        # P4-2（knowledge_transfer_design.md §5）で検索由来の構造ブロックも同じ1ビットに
        # 相乗りする — **どちらの由来かは payload に入れない**（出したか出さなかったかだけ）。
        _record_document_discuss_event(
            "structured_grounding_present", current_user["id"], course_id
        )

    # 入口統合 Phase 1（設計 §4.2 の [4] / §7）: この往復の様相と、その出所を確定する。
    # 語彙・優先順位の正本は core/learning_stance/schema.py（純関数）。**様相は
    # discuss_scope / cycle_mode / backstage / check_scaffold を切り替えない**（LC1）。
    # ストリーミング Phase 3-a（§2.2 / §3.3）: 様相は回答本文に依存しないので生成の
    # **前**に解決し、`start` イベントと最終 DTO の両方で同じ値を使う。
    _stance, _stance_source = resolve_stance(
        cycle_mode=_cycle_mode,
        is_discuss=_is_discuss,
        is_casual=_is_casual,
        explicit_casual=_explicit_casual,
        has_typed_action=bool(_route_for_typed_action(body.support_action)),
        has_atlas_context=bool(_atlas_ctx),
    )
    # ストリーミング Phase 3-a: ここが本関数で唯一の「前処理の終わり」の目印。
    # 権限・可視性・値検証・CostGate（ST2）・画面文脈の注入まで済んでいるので、
    # ここで初めて 200 を返し始めてよい（route 側は最初の next() を
    # StreamingResponse の前で同期に呼ぶ）。
    yield ("start", build_stance_dto(_stance, _stance_source))

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
            # M1: 実効モデルは contextvar（コース上書き）の内側で1回だけ確定する。
            _effective_model = resolve_model("learning_chat_llm_model", fallback="analysis")
            if not stream:
                answer = generate_text(
                    messages=messages,
                    temperature=0.3,
                    model=_effective_model,
                )
        if stream:
            # contextvar の外で yield する（ストリーミング設計 §3.2。Starlette は
            # next() ごとに copy_context() した別スレッドでジェネレータを再開するため、
            # with を跨いだ yield は ContextVar.reset() の token 不一致で落ちる）。
            answer = yield from _stream_answer(
                messages,
                model=_effective_model,
                usage_ctx={
                    "feature": _chat_feature,
                    "user_id": current_user["id"],
                    "course_id": course_id,
                },
            )
    except Exception:
        # 会話は死なせない（設計書 I3）: 500 即死をやめ、degraded 固定文 + 200 へ縮退する。
        # 履歴は保存し、回答本文に依存する後処理（誤解検出・ドリルダウン抽出）はスキップする（I4）。
        logger.exception("Learning chat LLM call failed for topic %s", topic_id)
        answer = _CHAT_DEGRADED_MESSAGE
        degraded = True

    # 出典マーカーの突き合わせ: 根拠の無い [出典N]（捏造・番号超過）を本文から取り除き、
    # 根拠のある番号は表記ゆれを半角 [出典N] へ正規化する。ここで整えることで、
    # レスポンス・履歴焼き込み・関心痕跡のすべてに同じ本文が流れる。
    if not degraded:
        answer = _reconcile_citation_markers(answer, {s["index"] for s in cited_sources})
    # IK-0494: 学習者に見せる出典（応答の sources・履歴の焼き込み）は、本文が実際に引用した
    # ものだけ。文脈に採用しただけの別論文のチャンクを一覧に並べない。番号は振り直さない
    # （採番器の対応表 CITATION_MAP_KEY は採用した全チャンクの番号を控え続ける）。
    # tier・出所の分類は文脈に採用した出典から決めたまま（ガードの判定と揃える）。
    # IK-0493: お礼・締めくくりで始まる発話への返答が何も引用していなければ、内容の説明
    # ではないので出所の分類を付けない（IK-0447 の定型文と同じ扱い。model_generated にすると
    # 「出典を追えない AI の説明」の出所行が付く）。
    if (
        not degraded
        and not _sources_cited_in_answer(answer, cited_sources)
        and _is_closing_led_statement(body.message)
    ):
        content_grounding = None
    # 引用が無いのに出所が教材・別の資料のときは、その出所を決めた出典を並べる（帯と一覧を揃える）。
    _displayed_sources = _displayed_sources_for(answer, cited_sources, content_grounding)

    # L1 OutOfSourceGuard: 未踏なら断定せず、根拠が弱い旨を先頭に明示する。
    # casual では可視プレフィックスのみ省略（音声で毎回読み上げると会話が壊れるため）。
    # tier 自体はレスポンスで返し、UI のバッジ表示で担保する。degraded な固定文には
    # 付与しない（回答本文に依存する装飾のため、設計書 §4）。
    # discuss では意図的にこの明示を維持する（DM1: 出所の正直さを弱めない）。
    # IK-0378: 付けるのはガードと同じ条件（採用した根拠が1つも無い = model_generated）
    # だけ。注意書きは表示であって回答本文ではないので、**履歴には保存しない**
    # （保存・再注入すると次の往復の回答の一部としてモデルに戻る）。応答の answer にだけ
    # 前置する（下の persist_chat_history の後）。
    _visible_out_of_source_notice = (
        out_of_source_notice()
        if _no_adopted_grounding and not _is_casual and not degraded
        # IK-0471: お礼・締めくくりの往復には注意書きも付けない（ガードと同じ条件）。
        and _guard_applies
        else ""
    )
    # IK-0396: 前提確認の往復で元の質問に答えたときは、その事実を1行添える（保存する）。
    if _prereq_resume_question and not degraded:
        _resume_notice = (
            label_vocab.PREREQUISITE_ACK_RESUME_NOTICE_EN
            if _is_kana_kanji_free(body.message) or _is_kana_kanji_free(_prereq_resume_question)
            else label_vocab.PREREQUISITE_ACK_RESUME_NOTICE
        )
        answer = _resume_notice + "\n\n" + answer
    # IK-0422: 逆質問を出し直さずに答えた往復は、確認がまだ記録されていない事実を1行添える（保存する）。
    # IK-0446: 添えるのはこのトピックで最初に出し直さなかった往復だけ（履歴に既にこの1行が
    # あれば添えない。同じ事実を毎回の回答の先頭に積まない）。
    if (
        _prereq_gate_skipped_for
        and not degraded
        and not _history_has_gate_skipped_notice(body.history)
    ):
        _skipped_template = (
            label_vocab.PREREQUISITE_GATE_SKIPPED_NOTICE_EN
            if _is_mostly_latin(body.message)
            else label_vocab.PREREQUISITE_GATE_SKIPPED_NOTICE
        )
        answer = _skipped_template.format(prerequisite=_prereq_gate_skipped_for) + "\n\n" + answer

    # 誤解検出（マイルドな表現にも対応）。casual では採点・訂正の圧を掛けない。
    # degraded ターンは回答本文が根拠を伴わない固定文のため、本文依存の後処理はスキップする
    # （設計書 §4・I3/I4: 会話は死なせない・履歴保存はそのまま行う）。
    # 是正 F5: 記録されるのは **candidate**（AI が訂正を提案した箇所）だけで、「誤解」として
    # 確定するのは本人の3択（POST .../misconceptions/{id}/review）に限る。
    course_update = None
    if not degraded:
        if not _is_casual and topic_info and any(
            kw in answer for kw in ["訂正", "より正確です", "誤解"]
        ):
            course_update = detect_and_record_misconception(
                current_user["id"], course_id, course_data, topic_id, body.message, answer,
                # 機能3（書き直し・削除）で派生痕跡を supersede する将来の連携のため、
                # 当該ターンの user メッセージ id を候補に持たせる（無い経路では None）。
                message_id=body.message_id or None,
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
            "sources": _history_source_meta(_displayed_sources),
            "overall_tier": overall_tier,
            "content_grounding": content_grounding,
            # IK-0444: 会話全体の対応表の控え（このターンで振った番号を含む）。
            CITATION_MAP_KEY: _citation_numbers.mapping(),
        },
    )
    if _visible_out_of_source_notice:
        answer = _visible_out_of_source_notice + "\n\n" + answer
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
    # IK-0470: 教材内容の問いでない往復（雑談・お礼や締めくくり・理解サイクルの予想の表明）は
    # 構造帰属（方法B）と引っかかりのヒントの対象にしない。問いの痕跡そのものは残す（P4）。
    _anchor_skip_reason = (
        "not_a_content_question"
        if intent == "CHIT_CHAT" or _is_closing_led_statement(body.message) or _cycle_mode == "elicit"
        else None
    )
    if _anchor_skip_reason:
        _tension_hint = False
    # 構造帰属（方法A・同期・非LLM）: テキスト選択・要素タップの明示アンカーがあれば
    # learner_selected で確定記録する。無ければ方法B（非同期LLM）の帰属対象になる。
    # 学ぶ単位 P2-7（設計 §8）: ①区画番号が申告されていないテキスト選択は教材区画本文
    # との逐語一致で埋める（決まらなければ場所は空のまま）②画面で要素チップを選んだ
    # 状態の発話も明示アンカーとして確定する（course 不一致の画面文脈は無視される）。
    _sel_anchor = _learner_selected_anchor(
        body,
        screen_selection=_screen_selection_for_anchor(body, course_id=course_id),
        segment_texts=_anchor_segment_texts(topic_info),
    )
    # 様相（_stance / _stance_source）は生成の前に解決済み（ストリーミング §3.3 で
    # `start` イベントへ載せるため前倒しした。入力6つはいずれも回答本文に依存しない）。
    _trace_payload = {
        "overall_tier": overall_tier,
        "position_anchor": position_anchor,
        "tension_hint": _tension_hint,
        "casual": _is_casual,
        # 「この問いに戻る」で元の往復へジャンプするための逆引き（この問いを発した user メッセージ id）。
        "message_id": _persisted.get("user_message_id"),
        # 方法Bの帰属コンテキスト用: この回答が**本文で実際に引用した**チャンク（引用順・3件まで。
        # IK-0470: 以前は検索上位3件で、回答が引いていない箇所が帰属の手がかりになっていた）。
        "cited_chunk_ids": _chunk_ids_cited_in_answer(answer, cited_sources)[:3],
        **({"anchor_skip_reason": _anchor_skip_reason} if _anchor_skip_reason else {}),
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
        # 入口統合 Phase 1（設計 §7）: どの様相で答えたか・それが明示か推定かを
        # enum 2つだけ焼き込む（本文・逐語は入れない = DO1。confidence も入れない = LC7）。
        # 楽屋には焼き込まない（entry_mode と同じ SD4 のガード — 「集計に入りません」と
        # 宣言した枠に観測用のキーを足さない）。
        **(
            {"stance": _stance, "stance_source": _stance_source}
            if not _is_backstage
            else {}
        ),
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
        # IK-0396: 前提確認の往復で元の質問に答えたときは、その質問を問いとして残す。
        text=_turn_question,
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
    if _trace_kind == "question" and not _sel_anchor and not _anchor_skip_reason:
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
        # IK-0468: 英語の会話では在処の事実文も英語にする（IK-0450 と同じ規則）。
        if _mirror is not None and _is_kana_kanji_free(body.message):
            clean_answer = clean_answer.replace(MIRROR_MOVED_NOTE, MIRROR_MOVED_NOTE_EN)

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
        sources=_displayed_sources,
        overall_tier=overall_tier,
        content_grounding=content_grounding,
        position_anchor=position_anchor,
        structure_anchor=_sel_anchor,
        anchor_confirm=_anchor_confirm,
        mirror=_mirror,
        # 入口統合 Phase 1（設計 §5、LC6）: 推定したことを隠さず事実として返す。
        # RAG 応答（この最終 return）だけが設定し、HELP / 学習相談 / 地図 / 要素説明の
        # 早期 return は None のまま。数値キーは持たない（LC7）。
        stance=build_stance_dto(_stance, _stance_source),
        mock=False,
        degraded=degraded,
    )


# ---------------------------------------------------------------------------
# 学習チャットのストリーミング（Phase 3-a）
#
# 正本: docs/features/llm_response_streaming_design.md（ST1〜ST9 / §2.2 / §3.4）。
# 前処理・後処理は `_learning_chat_core` の1本のまま（分岐は転送方式だけ）。
# ---------------------------------------------------------------------------

#: chunk 境界で ANSI エスケープ列が割れても衛生を掛け損ねないための保留幅（§4.2）。
_SSE_HYGIENE_TAIL = 16


def _sse_frame(event: str, payload: dict) -> str:
    """SSE の1フレーム。``data:`` は必ず JSON 1行（本文の改行で枠が壊れない）。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_frames(gen, first):
    """``_learning_chat_core`` のイベント列を SSE フレーム列に変換する（§3.4 手順5）。

    - ``start`` → delta（衛生済み）→ ``final``（``LearningChatResponse.model_dump()`` を
      そのまま。キーを間引かない = ST7）。
    - ``final`` すら組み立てられない例外だけ ``error`` フレーム（§2.2）。
    - クライアント切断（``GeneratorExit``）は ``gen.close()`` へ伝える。コアの
      ``except Exception`` は ``GeneratorExit`` を捕まえないので、保存・痕跡へは
      進まない（ST1/ST5: 中断した往復は記録しない）。
    """
    _, stance_dto = first
    try:
        yield _sse_frame("start", {"stance": stance_dto})
        pending = ""
        try:
            while True:
                kind, value = next(gen)
                if kind != "delta":
                    continue
                pending += value
                if len(pending) > _SSE_HYGIENE_TAIL:
                    emit, pending = pending[:-_SSE_HYGIENE_TAIL], pending[-_SSE_HYGIENE_TAIL:]
                    cleaned = strip_control_sequences(emit)
                    if cleaned:
                        yield _sse_frame("delta", {"t": cleaned})
        except StopIteration as stop:
            response = stop.value
        cleaned_tail = strip_control_sequences(pending)
        if cleaned_tail:
            yield _sse_frame("delta", {"t": cleaned_tail})
        yield _sse_frame("final", response.model_dump())
    except GeneratorExit:
        gen.close()
        raise
    except Exception:
        logger.exception("Learning chat stream failed")
        gen.close()
        yield _sse_frame("error", {"reason": "upstream"})


@router.post("/courses/{course_id}/topics/{topic_id}/chat/stream")
def learning_chat_stream(
    course_id: str,
    topic_id: str,
    body: LearningChatRequest,
    current_user: dict = Depends(_get_current_user),
) -> StreamingResponse:
    """学習チャットの逐次配信（SSE, ストリーミング Phase 3-a §3.4）。

    非ストリーム版 ``learning_chat`` と**同じコア**（``_learning_chat_core``）を通し、
    最後の ``final`` イベントは同一入力に対する JSON レスポンスと同値（ST7）。

    ``settings.learning_chat_streaming_enabled`` が false のときは 404
    （機能が存在しない状態を正直に返す。フロントは従来の JSON 経路へ = ST9）。
    最初の ``next()`` は ``StreamingResponse`` を返す**前**に同期で呼ぶので、権限・
    可視性・値検証・CostGate（429）は 200 を返す前に通常の HTTP ステータスで出る
    （ST2 / 原則11）。
    """
    if not get_settings().learning_chat_streaming_enabled:
        raise HTTPException(status_code=404, detail="Not Found")

    gen = _learning_chat_core(course_id, topic_id, body, current_user, stream=True)
    try:
        first = next(gen)
    except StopIteration as stop:
        # LLM 非経由の確定応答（学習相談・HELP・地図・要素説明など）。delta を1つも
        # 出さずに start + final だけを流し、クライアントのコードパスを1本に保つ。
        response = stop.value

        def _immediate():
            yield _sse_frame("start", {"stance": response.stance})
            yield _sse_frame("final", response.model_dump())

        return StreamingResponse(
            _immediate(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    return StreamingResponse(
        _sse_frames(gen, first),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.get("/client-features")
def learning_client_features(
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """クライアントが使ってよい経路の配布（ストリーミング §3.4）。

    設定値の鏡で、bool 1キーのみ。数値・上限・モデル名は載せない（ST8 / M9）。
    取得できないときフロントは false 扱い（fail-to-current）。
    """
    return {"chat_streaming": bool(get_settings().learning_chat_streaming_enabled)}


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

    response = _run_learning_turn(_learning_chat_core(
        context_id,
        DISCUSSION_TOPIC_ID,
        body,
        current_user,
        course_data=_document_discuss_course_data(document_id, source_path, title),
        scope_document_ids={document_id},
    ))
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
    return _learner_source_passage(passage)


#: 出典ポップアップ（app.js ``openSourcePopup`` → ``renderMaterialChunk``）が読むキー（IK-0448）。
_LEARNER_PASSAGE_KEYS = ("chunk_id", "text", "section", "source_title", "formulas", "figures")
#: 式1件のうち描画に使うキー（``renderMaterialEquationBody`` と置換の索引）。
_LEARNER_FORMULA_KEYS = (
    "id", "latex", "summary", "plain_text", "raw_text", "label",
    "reconstructed", "reconstructed_mark", "reconstructed_note", "latex_note", "label_note",
)


def _learner_source_passage(passage: dict) -> dict:
    """出典ポップアップ用のチャンクを学習者向けに射影する（IK-0448）。

    ``chunks.formulas`` の要素は解析の内部情報（``review_reason`` / ``source_location`` の bbox /
    切り出し画像の base64 / ``block_id`` / ``section_id`` など）を丸ごと持っている。学習者に
    返すのは、ポップアップが描画に使うキーだけ（数値・内部 ID・画像データを載せない）。
    復元由来の式には、教材表示と同じ印と事実文を載せる。
    """
    out = {k: passage[k] for k in _LEARNER_PASSAGE_KEYS if k in passage}
    formulas = []
    for formula in passage.get("formulas") or []:
        if not isinstance(formula, dict):
            continue
        formulas.append({k: formula[k] for k in _LEARNER_FORMULA_KEYS if k in formula})
    out["formulas"] = annotate_reconstructed_formulas(formulas)
    return out


@router.get("/courses/{course_id}/symbols/lookup")
def get_symbol_lookup_route(
    course_id: str,
    symbol: str = "",
    equation_id: str = "",
    chunk_id: str = "",
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """記号の「直前の定義」（概念レジストリ P3-5 / ``concept_registry_design.md`` §7）。

    教材の数式の中の記号をタップしたときに、**その位置より前で最も近い定義**を
    論文の逐語で返す。**LLM を 1 度も呼ばず**（既存データの読みだけ）、quota も
    消費しない。

    fail-closed は既存の学習者向け文脈 API（``get_course_component_context`` /
    ``get_source_chunk_route``）と同じ3段:

    1. ``get_accessible_course_data`` — 本人が当該コースを閲覧できる（不可なら 404）
    2. ``list_course_source_document_ids(course_data)`` — そのコースの source 集合
       （**全域可視集合へ広げない** — P0 オブジェクトスコープ是正と同じ規律）
    3. ``core.symbol_lookup`` の SQL 内 ``document_id = ANY(...)`` で強制
       （sources が空なら SQL を発行せず ``available=false``）

    記号が空文字のときは 422（何を引くのか決まっていない照会は受けない）。記号は
    見つかったが定義が無い場合は 404 ではなく 200 + 事実文（「この論文には定義の
    記述が見つかりませんでした」）で返す — 定義の不在は異常ではなく事実である（KR8）。
    """
    if not str(symbol or "").strip():
        raise HTTPException(status_code=422, detail="記号が指定されていません。")

    course_data = get_accessible_course_data(current_user["id"], course_id)
    if course_data is None:
        raise HTTPException(status_code=404, detail="Course not found")

    allowed_document_ids = list_course_source_document_ids(course_data)
    session = _pg_session()
    try:
        return lookup_symbol_definition(
            session,
            symbol=symbol,
            document_ids=sorted(allowed_document_ids),
            equation_id=equation_id,
            chunk_id=chunk_id,
        )
    finally:
        session.close()


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

    digest = get_tension_digest(current_user["id"], course_id)
    # IK-0437: 候補ゼロを空配列だけで返すと「無い」のか「まだ作られていない」のか読めない。
    if isinstance(digest, dict) and not digest.get("items"):
        digest = {**digest, "facts": [label_vocab.TENSION_DIGEST_EMPTY_FACT]}
    return digest


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
    return _learner_tension_decision_dto(result)


@router.post("/tension/{trace_id}/dismiss")
def dismiss_tension_route(
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人が「違う」と判定: candidate → dismissed（行は残す。P4）。"""
    result = dismiss_tension_trace(current_user["id"], trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Tension candidate not found")
    return _learner_tension_decision_dto(result)


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

    digest = get_anchor_digest(current_user["id"], course_id)
    # IK-0437: 候補ゼロのときは事実文を添える（tension digest と同じ形）。
    if isinstance(digest, dict) and not digest.get("items"):
        digest = {**digest, "facts": [label_vocab.ANCHOR_DIGEST_EMPTY_FACT]}
    return digest


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
    return _learner_anchor_decision_dto(
        result, status="confirmed", notice=label_vocab.ANCHOR_CONFIRMED_NOTICE,
    )


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
    return _learner_anchor_decision_dto(
        result, status="dismissed", notice=label_vocab.ANCHOR_DISMISSED_NOTICE,
    )


def _learner_anchor_decision_dto(result: dict, *, status: str, notice: str) -> dict:
    """帰属の確定・却下の応答を学習者向けに射影する（IK-0411）。

    ``services.confirm_anchor_trace`` は保存した ``structure_anchor`` を丸ごと返す。そこには
    LLM 候補の ``confidence``（生の数値）・英語の ``reason``（内部の claim ID を含み得る）・
    ``anchor_id``（内部 ID）・``detector_version`` が載っていて、学習者に出すものではない
    （P3 / P7 / KO10）。ダイジェスト（``get_anchor_digest``）と同じラベルの投影だけを返す
    — 許可リスト方式で、新しいキーが payload に増えても応答へは漏れない。
    関連する前提（D3-6）は本文 ``statement`` だけを通す（前提の内部 ID は出さない）。
    """
    from core.structure_anchor.schema import ANCHOR_TYPE_LABELS, DOUBT_TYPE_LABELS

    result = result if isinstance(result, dict) else {}
    anchor = result.get("structure_anchor") if isinstance(result.get("structure_anchor"), dict) else {}
    dto: dict = {
        "ok": True,
        "trace_id": str(result.get("trace_id") or ""),
        "status": status,
        "notice": notice,
    }
    if anchor:
        atype = str(anchor.get("anchor_type") or "segment")
        dtype = str(anchor.get("doubt_type") or "unclassified")
        dto["anchor_label"] = str(anchor.get("anchor_label") or "")
        dto["anchor_type_label"] = ANCHOR_TYPE_LABELS.get(atype, "")
        dto["doubt_type"] = dtype if dtype in DOUBT_TYPE_LABELS else "unclassified"
        dto["doubt_type_label"] = DOUBT_TYPE_LABELS.get(dtype, DOUBT_TYPE_LABELS["unclassified"])
    related = result.get("related_assumption")
    if isinstance(related, dict) and str(related.get("statement") or "").strip():
        dto["related_assumption"] = {"statement": str(related.get("statement") or "")}
    return dto


def _learner_tension_decision_dto(result: dict) -> dict:
    """tension の確定・却下・接続の応答を許可リストで射影する（IK-0411 と同じ規律）。"""
    result = result if isinstance(result, dict) else {}
    return {
        "ok": True,
        "trace_id": str(result.get("trace_id") or ""),
        "status": str(result.get("status") or ""),
    }


# ---------------------------------------------------------------------------
# 誤解メモ（AI 候補 → 本人の3択）— 是正 F5 / 六つのレンズ 提案3
# ---------------------------------------------------------------------------
# 誤解メモは非LLM の文字列一致で検出した **AI の候補** にすぎない。「誤解」として
# 確定するのはこの経路の本人の3択だけで、却下も行を消さず status 遷移で保持する（P4）。
# 語彙は R層の自己確認と共有（core/reconstruction/schema.py::SELF_CHECK_VALUES）。


class MisconceptionReviewRequest(BaseModel):
    """誤解メモ候補への本人の判断（agreed / disagreed / verdict_wrong）。"""

    decision: str


@router.post("/courses/{course_id}/topics/{topic_id}/misconceptions/{entry_id}/review")
def review_misconception_route(
    course_id: str,
    topic_id: str,
    entry_id: str,
    body: MisconceptionReviewRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """誤解メモ候補を本人が確定 / 却下する（本人のみ・非LLM・migration 不要）。

    - ``agreed``: そう、これは私の誤解だった → ``confirmed``
    - ``disagreed``: これは誤解ではない → ``dismissed``（行は残す）
    - ``verdict_wrong``: AI の訂正のほうが違う → ``dismissed``（理由を分けて記帳する）

    語彙外は 422、候補が見つからない / すでに確定・却下済みは 404（他人の学習状態には
    そもそも到達できない — 更新は user_id 一致の行だけを対象にする）。
    """
    decision = str(body.decision or "").strip()
    if decision not in MISCONCEPTION_DECISIONS:
        raise HTTPException(status_code=422, detail="invalid misconception decision")

    course_data = get_accessible_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")
    if not find_course_topic(course_data, topic_id):
        raise HTTPException(status_code=404, detail="Topic not found")

    result = review_personal_misconception(
        current_user["id"], course_id, topic_id, entry_id, decision,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Misconception candidate not found")

    return {
        "ok": True,
        **result,
        # 更新後の個人レイヤーをそのまま返す（フロントは chat 応答と同じ経路でマージする）。
        "personal_layer": get_personal_layer(current_user["id"], course_id),
    }


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
                    SELECT DISTINCT document_id::text AS document_id FROM theory_component_graphs
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
    return _learner_tension_decision_dto(result)


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


def _component_context_with_explanation(
    component_id: str, course_id: str, course_document_ids: set[str]
) -> dict | None:
    """コーススコープの component 文脈 DTO（C層の承認済み説明を充填済み）。解決不能なら ``None``。

    ``get_course_component_context``（エンドポイント）と、画面文脈アダプター Phase 4
    （``assistant_screen_adapter_design.md`` §11.3 kind ``element``）の共通正本。
    document スコープの強制は ``core.component_context`` の SQL 内
    （``ANY(:doc_ids)``）が持つ — ここで再実装しない。
    """
    context = build_component_context(component_id, course_id, course_document_ids)
    if context is None:
        return None
    explanation = _first_approved_component_explanation(context["component_id"], course_id)
    if explanation is not None:
        context["instance"]["explanation"] = explanation
    return context


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
    context = _component_context_with_explanation(
        component_id, course_id, course_document_ids
    )
    if context is None:
        raise HTTPException(status_code=404, detail="Component not found")
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
