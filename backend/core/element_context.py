"""学習者向け claim / equation 文脈 API の core ロジック（非LLM・読み取り専用）。

設計書: ``docs/features/learner_element_context_design.md``。

``core/component_context.py`` が component について実現した「コーススコープ・
fail-closed のオンデマンド文脈 API」を、``claim`` と ``equation`` へ広げる。
component の文脈 DTO が instance / shared_part / graph の三層構造を持つのに対し、
本モジュールは **W層の要素中心コンテキストレンズ
（``core/deliberation/context_lens.py``）の投影を学習者向けにフィルタして返すだけ**
に責務を絞る（claim / equation には ComponentRecord のような固有の rich 投影が
無く、上位・下位の関係そのものが提示価値の中心だからである）。

不変条項（設計書 §2）:

- **fail-closed**: 要素の ``document_id`` がコースの document 集合
  （呼び出し側が渡す ``course_document_ids``、通常は
  ``routes/lecture.py::_course_document_ids`` の戻り値）に含まれない場合は
  SQL の時点で除外し ``None`` を返す（コース外文書の要素は解決自体が失敗する）。
- **candidate を学習者に出さない**: ``upper`` / ``lower`` のうち
  ``relation_status == "candidate"``（AI が提案した未確定の関係）は除外し、
  ``focus.contextual_role`` も source_backed / confirmed のときだけ残す
  （``component_context._build_graph`` の graph 射影と同じ原則）。
- **数値を見せない（W8）**: 最終レスポンスを再帰走査して ``"confidence"`` キーを
  除去する（``component_context.strip_confidence`` を共有）。
- **裸の内部 ID を出さない**: ITEM の ``evidence_refs``（evidence_id / step_id 等の
  内部参照）と ``focus.provenance``（``theory_claims:<uuid>`` 等）は落とす。
- **書き込みを行わない**: A層（``src/episteme_graph/agents/``）・W層のコードは
  読むだけで変更しない。本モジュールに書き込み経路は無い。
- 本モジュールは FastAPI を import しない（開発ルール2 / core/ 共通ルール）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core.component_context import strip_confidence
from core.deliberation import context_lens as context_lens_mod
from core.deliberation.refs import equation_records
from core.deliberation.schema import (
    CONTEXT_STATUS_CANDIDATE,
    CONTEXT_STATUS_CONFIRMED,
    CONTEXT_STATUS_SOURCE_BACKED,
    ELEMENT_EQUATION,
    ELEMENT_THEORY_CLAIM,
    SCOPE_DOCUMENT,
    ElementRef,
)

logger = logging.getLogger(__name__)

# ── 公開 element_type 語彙（API パスに現れる値）─────────────────────────────
# 学習者向け API は W層の内部語彙（``theory_claim``）ではなく短い ``claim`` を使う。
# それ以外の値は呼び出し側が 404 にする（学習者 API の 404 統一方針）。
ELEMENT_TYPE_CLAIM = "claim"
ELEMENT_TYPE_EQUATION = "equation"
SUPPORTED_ELEMENT_TYPES = (ELEMENT_TYPE_CLAIM, ELEMENT_TYPE_EQUATION)

# 公開語彙 → W層内部語彙。
_INTERNAL_ELEMENT_TYPES = {
    ELEMENT_TYPE_CLAIM: ELEMENT_THEORY_CLAIM,
    ELEMENT_TYPE_EQUATION: ELEMENT_EQUATION,
}

# 学習者に出してよい関係状態（candidate は教員確定前の AI 候補なので出さない）。
_LEARNER_VISIBLE_STATUSES = (CONTEXT_STATUS_SOURCE_BACKED, CONTEXT_STATUS_CONFIRMED)

# 各レーンの表示上限（``component_context._GRAPH_LANE_MAX`` と同じ値。candidate 除外
# 後の件数に対して適用する）。
_LANE_MAX = 20

# context_lens が投影を返せない / 例外だった場合の事実文（available:false 時の note）。
NOTE_NO_CONTEXT = "この要素の文脈情報は現在表示できません。"

PROVENANCE_COURSE_FREEZE = "course_freeze"


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _normalized_document_ids(course_document_ids: set[str] | None) -> list[str]:
    return sorted({str(d) for d in (course_document_ids or set()) if str(d or "").strip()})


def _json_list(value: Any) -> list:
    return value if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# 要素解決（コース document スコープを SQL / 走査対象で強制する）
# ---------------------------------------------------------------------------


def _resolve_claim(element_id: str, course_document_ids: set[str]) -> tuple[str, str] | None:
    """claim を ``(db_uuid, document_id)`` に解決する（コース document 集合内に限定）。

    ``document_id = ANY(:doc_ids)`` を WHERE 句に直接含めることで（後付けの Python
    フィルタではなく）、agent 側 ID（``claim_span_007`` 等、論文ごとに独立採番される
    ため文書間で衝突しうる）がコース外文書の同名 claim に誤って一致する余地を断つ
    （``component_context._resolve_component_row`` と同型の fail-closed）。
    """
    document_ids = _normalized_document_ids(course_document_ids)
    if not document_ids:
        return None

    conditions = ["source_scope->'legacy_ids' ? :raw_id"]
    params: dict[str, Any] = {"raw_id": str(element_id), "doc_ids": document_ids}
    if _is_uuid(element_id):
        conditions.append("id = CAST(:uuid_id AS uuid)")
        params["uuid_id"] = str(element_id)
    where_clause = " OR ".join(conditions)

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                SELECT id::text AS id, document_id
                FROM theory_claims
                WHERE document_id = ANY(:doc_ids) AND ({where_clause})
                ORDER BY (id::text = :raw_id) DESC
                LIMIT 1
                """
            ),
            params,
        ).mappings().fetchone()
    finally:
        session.close()
    if not row:
        return None
    return str(row["id"]), str(row["document_id"] or "")


def _resolve_equation(element_id: str, course_document_ids: set[str]) -> tuple[str, str] | None:
    """equation を ``(equation_id, document_id)`` に解決する。

    equation は独立テーブルを持たない（W層設計 §2）ため、コースの document 集合を
    順に走査し ``equation_semantics`` artifact の ``equations[].equation_id`` に一致する
    最初の document を採用する（``core/deliberation/refs.py::_resolve_equation`` と
    同じ存在確認）。走査対象がコース document 集合そのものなので、コース外文書の
    equation は原理的に解決されない（fail-closed）。1 document の artifact 読み取りが
    失敗しても他 document の走査は続ける（fail-soft）。
    """
    raw_id = str(element_id)
    for document_id in _normalized_document_ids(course_document_ids):
        try:
            records = equation_records(document_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "element_context: equation records read failed for document %s",
                document_id,
                exc_info=True,
            )
            continue
        for record in records:
            if isinstance(record, dict) and str(record.get("equation_id") or "") == raw_id:
                return raw_id, document_id
    return None


def _resolve_element(
    element_type: str, element_id: str, course_document_ids: set[str]
) -> tuple[str, str] | None:
    if element_type == ELEMENT_TYPE_CLAIM:
        return _resolve_claim(element_id, course_document_ids)
    if element_type == ELEMENT_TYPE_EQUATION:
        return _resolve_equation(element_id, course_document_ids)
    return None


# ---------------------------------------------------------------------------
# 学習者向けフィルタ（candidate 除外 / role 抑止 / 内部 ID 除去）
# ---------------------------------------------------------------------------


def _project_item(item: dict) -> dict:
    """ITEM を学習者向けに射影する（``component_context._project_context_item`` と同形）。

    ``evidence_refs``（evidence_id / step_id 等の内部参照）と ``relation``
    （内部語彙キー）は落とし、読み手向けの ``relation_label`` のみ残す。
    """
    return {
        "id": item.get("element_id"),
        "element_type": item.get("element_type"),
        "label": item.get("label"),
        "relation_label": item.get("relation_label"),
        "relation_status": item.get("relation_status"),
        "navigable": bool(item.get("navigable")),
    }


def _visible_items(items: Any) -> list[dict]:
    """candidate を除外して射影し、レーン上限で切る。"""
    projected = [
        _project_item(item)
        for item in _json_list(items)
        if isinstance(item, dict) and item.get("relation_status") != CONTEXT_STATUS_CANDIDATE
    ]
    return projected[:_LANE_MAX]


def _project_focus(focus: Any, element_type: str, element_id: str) -> dict:
    """focus を学習者向けに射影する。

    ``contextual_role`` は ``contextual_role_status`` が source_backed / confirmed の
    ときだけ残す（candidate = AI 候補 / unidentified = 未同定は、役割を語らずキー自体を
    落とす — 推測で穴埋めしない）。``provenance``（内部 ID 列）は落とす。
    """
    focus = focus if isinstance(focus, dict) else {}
    result: dict[str, Any] = {
        "element_type": element_type,
        "element_id": element_id,
        "document_id": focus.get("document_id"),
        "label": str(focus.get("label") or ""),
        "intrinsic_summary": str(focus.get("intrinsic_summary") or ""),
    }
    role = str(focus.get("contextual_role") or "").strip()
    role_status = str(focus.get("contextual_role_status") or "")
    if role and role_status in _LEARNER_VISIBLE_STATUSES:
        result["contextual_role"] = role
        result["contextual_role_status"] = role_status
    generic = focus.get("generic")
    if isinstance(generic, dict):
        result["generic"] = generic
    return result


def _notes(value: Any) -> list[str]:
    return [str(n) for n in _json_list(value) if str(n or "").strip()]


# ---------------------------------------------------------------------------
# 公開インターフェース
# ---------------------------------------------------------------------------


def build_element_context(
    element_type: str, element_id: str, course_document_ids: set[str]
) -> dict | None:
    """学習者向け claim / equation 文脈 DTO を組み立てる。

    - 未対応の ``element_type``、またはコースの document 集合内で要素が解決できない
      場合は ``None``（呼び出し側は 404 にマッピングする）。
    - 要素は解決できたが W層 context lens が投影を返せない / 例外の場合は
      ``{"available": False, "note": <事実文>}``（fail-soft。呼び出し側は 200 で返す）。
    - 成功時は ``{"available": True, "element_type", "element_id", "focus",
      "upper", "lower", "notes", "provenance"}``。
    """
    if element_type not in SUPPORTED_ELEMENT_TYPES:
        return None

    resolved = _resolve_element(element_type, element_id, course_document_ids)
    if resolved is None:
        return None
    resolved_id, document_id = resolved

    try:
        ref = ElementRef(
            scope=SCOPE_DOCUMENT,
            element_type=_INTERNAL_ELEMENT_TYPES[element_type],
            element_id=resolved_id,
            document_id=document_id,
        )
        ref.validate()
        lens = context_lens_mod.build(ref)
    except Exception:  # noqa: BLE001
        logger.warning(
            "element_context: context_lens build failed for %s:%s",
            element_type,
            resolved_id,
            exc_info=True,
        )
        lens = None
    if not isinstance(lens, dict):
        return {"available": False, "note": NOTE_NO_CONTEXT}

    result = {
        "available": True,
        "element_type": element_type,
        "element_id": resolved_id,
        "focus": _project_focus(lens.get("focus"), element_type, resolved_id),
        "upper": _visible_items(lens.get("upper")),
        "lower": _visible_items(lens.get("lower")),
        "notes": _notes(lens.get("notes")),
        "provenance": PROVENANCE_COURSE_FREEZE,
    }
    return strip_confidence(result)
