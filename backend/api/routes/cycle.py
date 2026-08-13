"""理解サイクル（Understanding Cycle, UCサイクル）Phase 1 API。

正本: ``docs/features/understanding_cycle_design.md``（UC1〜UC10）。実パスはすべて
``/api/learning/...``（本人のみ・受講ゲートは ``get_accessible_course_data``、tension /
structure_anchor と同型）。骨格は非LLM・同期（UC8）。intention / 軽量アンカーは
削除せず状態遷移のみで保持し（UC6）、行削除 API は作らない。監査記帳（
``theory_review_events``）は行わない（本人専用メモという指揮官裁定）。

- ``POST .../cycle/intention``: OPEN（初回動機・持ち越し問い再回答）と LEAVE
  （持ち越す問いの選択）を1つの API で扱う（role で分岐）。
- ``POST .../cycle/intention/{trace_id}/dismiss``: status 遷移のみ（UC6）。
- ``POST .../cycle/anchor``: 軽量アンカー4ボタン（設計書 §4.2/§5.4）。既存
  structure_anchor 経路A（``attribution_source='learner_selected'``）へ相乗りし、
  ``core.structure_anchor.schema.build_anchor_payload`` を必ず使う。
- ``GET .../cycle/landing-candidates``: LEAVE の選択リスト（設計書 §5.5）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import _get_current_user
from services import (
    dismiss_cycle_intention,
    get_accessible_course_data,
    record_cycle_anchor_mark,
    record_cycle_intention,
)
from core.cycle.derive import build_landing_candidates, build_revisit_facts
from core.cycle.map_diff import build_map_diff_facts, build_network_as_of
from core.cycle.queries import (
    fetch_active_carryover,
    fetch_landing_candidates,
    fetch_recent_traces_since,
)
from core.cycle.schema import INTENTION_ROLES, QUICK_LABELS
from core.personal_graph.derive import derive_personal_network
from core.structure_anchor.schema import (
    ATTRIBUTION_LEARNER_SELECTED,
    anchor_type_for_element,
    build_anchor_payload,
)

logger = logging.getLogger(__name__)

# main.py で直接 include される学習者向け router（reconstruction.py / discuss_observation.py
# と同型。admin.router 経由の二段ネストにしない — Tier 3-17c）。
learning_router = APIRouter(prefix="/api/learning", tags=["Learning"])


class CycleIntentionRequest(BaseModel):
    role: str
    text: str = ""
    source_trace_id: str | None = None
    prediction: dict | None = None


@learning_router.post("/courses/{course_id}/cycle/intention", status_code=201)
def record_cycle_intention_route(
    course_id: str,
    body: CycleIntentionRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """OPEN（初回動機/持ち越し再回答）・LEAVE（持ち越し選択）の記録。

    revisit_answer のときだけ、REVISIT 直後の差分事実文（数値なし・最大3件）を
    ``facts`` に同梱する（設計書 §5.2）。事実文の導出に失敗しても記録自体は
    成功させる（fail-open。骨格である記録は非LLM・同期のまま完結させる, UC8）。
    """
    course_data = get_accessible_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    role = (body.role or "").strip()
    if role not in INTENTION_ROLES:
        raise HTTPException(status_code=422, detail="Invalid role")
    text = (body.text or "").strip()
    prediction_text = ""
    if isinstance(body.prediction, dict):
        prediction_text = str(body.prediction.get("text", "") or "").strip()
    # 「予想してから開く」（§5.3）は動機が空のまま予想だけを記録できる —
    # opening_motive に限り、prediction.text があれば text 空を許容する。
    if not text and not (role == "opening_motive" and prediction_text):
        raise HTTPException(status_code=422, detail="text is required")
    if role == "revisit_answer" and not (body.source_trace_id or "").strip():
        raise HTTPException(
            status_code=422, detail="source_trace_id is required for revisit_answer"
        )

    result = record_cycle_intention(
        current_user["id"], course_id, role, text,
        source_trace_id=body.source_trace_id, prediction=body.prediction,
    )
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to record intention")

    facts: list[str] = []
    if role == "revisit_answer":
        try:
            carryover = fetch_active_carryover(current_user["id"], course_id)
            since = carryover.get("created_at") if carryover else None
            if since:
                rows = fetch_recent_traces_since(current_user["id"], course_id, since)
                # 帰り道の景色（設計書 §6）: carryover を残した時点までの個人知識
                # ネットワークと現在の導出結果を比較し、構造差分を肯定形の事実文に
                # 昇格する（personal_graph は読むだけ・非改変。core.cycle.map_diff 参照）。
                # 失敗しても記録・facts の骨格は成立させる（この try 全体が fail-open）。
                before_network = build_network_as_of(current_user["id"], course_id, since)
                after_network = derive_personal_network(current_user["id"], course_id)
                map_diff_facts = build_map_diff_facts(before_network, after_network)
                facts = build_revisit_facts(carryover, rows, map_diff_facts=map_diff_facts)
        except Exception:
            logger.warning("Failed to build revisit facts", exc_info=True)
            facts = []

    return {"ok": True, "trace_id": result["trace_id"], "facts": facts}


@learning_router.post("/cycle/intention/{trace_id}/dismiss")
def dismiss_cycle_intention_route(
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """intention 痕跡の dismiss（status 遷移のみ。UC6 — 行削除しない）。"""
    ok = dismiss_cycle_intention(current_user["id"], trace_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"ok": True}


class CycleAnchorRequest(BaseModel):
    quick_label: str
    topic_id: str = ""
    selection_text: str | None = None
    selection_segment_id: int | None = None
    chunk_id: str | None = None
    element_id: str | None = None
    element_type: str | None = None
    element_label: str | None = None


def _cycle_anchor_payload(body: "CycleAnchorRequest", doubt_type: str) -> dict:
    """§4.2 の軽量アンカー: 既存 ``_learner_selected_anchor``（routes/learning.py）と

    同じ縮退規則（element_id → selection_text → chunk_id → segment 空縮退）で
    anchor_type/anchor_id を決め、doubt_type だけ quick_label のマッピング値で
    上書きする。routes/learning.py の既存関数は非改変のまま、ここで独立に組み立てる
    （build_anchor_payload を必ず使う）。
    """
    if body.element_id:
        atype = anchor_type_for_element(body.element_type)
        anchor_id = body.chunk_id if (atype == "chunk" and body.chunk_id) else body.element_id
        return build_anchor_payload(
            anchor_type=atype,
            anchor_id=anchor_id,
            anchor_label=body.element_label or body.element_id,
            doubt_type=doubt_type,
            attribution_source=ATTRIBUTION_LEARNER_SELECTED,
            evidence_quote="",
            reason="cycle_anchor_element",
            confidence=1.0,
        )
    sel = (body.selection_text or "").strip()
    if sel:
        seg = body.selection_segment_id
        return build_anchor_payload(
            anchor_type="segment",
            anchor_id=f"seg_{int(seg)}" if seg is not None else "",
            anchor_label=(sel[:40] + "…") if len(sel) > 40 else sel,
            doubt_type=doubt_type,
            attribution_source=ATTRIBUTION_LEARNER_SELECTED,
            evidence_quote=sel,
            reason="cycle_anchor_selection",
            confidence=1.0,
        )
    if (body.chunk_id or "").strip():
        return build_anchor_payload(
            anchor_type="chunk",
            anchor_id=body.chunk_id,
            anchor_label="",
            doubt_type=doubt_type,
            attribution_source=ATTRIBUTION_LEARNER_SELECTED,
            evidence_quote="",
            reason="cycle_anchor_chunk",
            confidence=1.0,
        )
    return build_anchor_payload(
        anchor_type="segment",
        anchor_id="",
        anchor_label="",
        doubt_type=doubt_type,
        attribution_source=ATTRIBUTION_LEARNER_SELECTED,
        evidence_quote="",
        reason="cycle_anchor_fallback",
        confidence=1.0,
    )


@learning_router.post("/courses/{course_id}/cycle/anchor", status_code=201)
def record_cycle_anchor_route(
    course_id: str,
    body: CycleAnchorRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """軽量アンカー4ボタンの1タップ確定（設計書 §4.2/§5.4）。確認ダイアログを出さない。"""
    course_data = get_accessible_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    key = (body.quick_label or "").strip()
    if key not in QUICK_LABELS:
        raise HTTPException(status_code=422, detail="Invalid quick_label")

    doubt_type = str(QUICK_LABELS[key]["doubt_type"])
    anchor_payload = _cycle_anchor_payload(body, doubt_type)
    text = (
        (body.selection_text or "")[:120].strip()
        or (body.element_label or "").strip()
        or str(QUICK_LABELS[key]["label"])
    )

    trace_id = record_cycle_anchor_mark(
        current_user["id"], course_id, body.topic_id or None, key, anchor_payload, text,
    )
    if trace_id is None:
        raise HTTPException(status_code=500, detail="Failed to record anchor")
    return {"ok": True, "trace_id": trace_id}


@learning_router.get("/courses/{course_id}/cycle/landing-candidates")
def get_cycle_landing_candidates_route(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """LEAVE（持ち越す問いの選択リスト）候補一覧（設計書 §5.5）。数値・件数は含めない。"""
    course_data = get_accessible_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    try:
        rows = fetch_landing_candidates(current_user["id"], course_id)
        candidates = build_landing_candidates(rows)
    except Exception:
        logger.warning("Failed to build landing candidates", exc_info=True)
        candidates = []
    return {"candidates": candidates}
