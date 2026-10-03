"""検証シミュレーション層 v1.1「主語は学習者」— 学習者が作って試す API（動かして確かめる）。

正本: ``docs/features/simulation_bridge_design.md`` §14（SB11〜SB15・§14.3 学習者 API）。
実パスは ``/api/learning/...``（main.py が ``learning_router`` を直接 include する。
cycle / descent と同型。admin.router 経由の二段ネストにしない — Tier 3-17c）。

- **試行に承認ゲートを置かない（SB11）**。生成・編集・配線・実行は本人の操作だけで完結する。
  「採用」は本人の操作で、教員の承認ではない。安全の弁は AST 検査と sim-runner の隔離実行で、
  承認の弁とは混ぜない（実行前に AST 検査を毎回やり直す = SB3）。
- **所有は本人・可視は本人（SB12）**。programs / wirings / runs の読み書きはすべて
  ``owner_user_id == 本人`` を要求し、他者の行は存在を明かさず 404。受講ゲートは
  ``get_accessible_course_data``、教材はコースの sources に限る（``list_course_source_document_ids``。
  外は 404）。教材の既定は焦点論文（``services.learner_focus_document`` = FD1。「どの論文か」の
  優先を本ファイルで新しく書かない）。
- **共有は本人の 1 操作・台帳は教員の弁（SB13）**。共有すると ``shared_at`` を刻み、観測系の
  主張の台帳に候補を追記する（確定は教員・``indirectly_supported`` 止まり = SB1）。
- **予想は並置・採点しない（SB14）**。``prediction`` は数値だけを受けてそのまま保存・返却する。
- **痕跡は登録簿に宣言（SB15）**。成功した run ごとに ``interest_traces`` の kind
  ``verification_attempt`` を 1 行（``services.record_interest_trace`` 経由・数値を持たない）。
  本人の試行の記録は監査記帳しない（主権台帳と同じ判断 — 本人行動の記録を観察面にしない）。
- LLM を呼ぶのは「コードを生成」と「改訂案を求める」の 2 本だけ（各 1 コール + 本人キーの
  日次 CostGate = SB9）。モデル名は学習者に出さず、学習者はモデルを選ばない（M9）。
- 学習者向け DTO は ``LearnerDisplayRoute`` の射影を通るので、**記号をキーにした写像は
  ``[{symbol, ...}]`` のリスト**で返す（射影が ``k`` / ``n`` / ``count`` のようなキーを落とす —
  IK-0598 の学習者側）。所有者・作成者の user id と confidence はどの DTO にも載せない。
- 行を消さない（SB7）。DELETE メソッドは無い。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from display_route import LearnerDisplayRoute  # DP1: 学習者向けルートは表示投影を必ず通る
from dependencies import _get_current_user
from quota import consume_daily_quota
from routes.theory_components import build_theory_modules_for_document
from services import (
    get_accessible_course_data,
    learner_focus_document,
    list_course_source_document_ids,
    mark_verification_attempt_shared,
    record_interest_trace,
)

from core.config import get_settings
from core.document_refs import canonical_document_ids, is_document_uuid
from core.doubt.observation_targets import observation_claim_targets
from core.llm import generate_text, generate_text_with_structured_output
from core.llm_usage.context import usage_context
from core.llm_worker.cost_gate import CostGate
from core.postgres import get_session as _pg_session
from core.simulation_bridge import catalog as parts_catalog
from core.simulation_bridge import codegen, instrument, revision, schema, service, store
from core.simulation_bridge.sandbox import sandbox_client_from_settings

logger = logging.getLogger(__name__)

learning_router = APIRouter(prefix="/api/learning", tags=["SimulationLab"], route_class=LearnerDisplayRoute)

#: 生成と改訂提案で 1 つのカウンタを共有する（本人キー・``SIMULATION_LEARNER_MAX_CALLS_PER_DAY``）。
_learner_cost_gate = CostGate()

_QUOTA_MESSAGE = "本日の AI 呼び出し回数の上限に達しました。明日以降にもう一度お試しください。"
_NOT_FOUND = "対象が見つかりません。"
_PROGRAM_NOT_FOUND = "理論プログラムが見つかりません。"
_WIRING_NOT_FOUND = "配線が見つかりません。"
_RUN_NOT_FOUND = "実行記録が見つかりません。"
_SOURCE_MAX_CHARS = 40_000


def _noop_audit(**_kwargs: Any) -> None:
    """本人の試行は監査記帳しない（store の判断 API が要求する callable の受け口）。"""


# ---------------------------------------------------------------------------
# リクエスト
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    module_key: str = ""
    document_id: str | None = None
    topic_id: str | None = None


class EditRequest(BaseModel):
    source_code: str = ""


class LearnerWiringRequest(BaseModel):
    name: str = ""
    document_id: str | None = None
    topic_id: str | None = None
    # parts[].params は {name: value} か [{name, value}] のどちらでも受ける。
    parts: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class LearnerRunRequest(BaseModel):
    program_id: str = ""
    wiring_id: str | None = None
    topic_id: str | None = None
    observation_claim_ids: list[str] = Field(default_factory=list)
    # 記号の値は [{symbol, value}] / パラメータは [{name, value}]（{記号: 値} の写像も受ける）。
    prediction: Any = None
    values: Any = None
    params: Any = None
    instrument_params: Any = None
    reference: Any = None
    seed: int | None = None


class ShareRequest(BaseModel):
    claim_ids: list[str] | None = None


# ---------------------------------------------------------------------------
# 入力の正規化（数値だけ・文字列は 422）
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numeric_pairs(value: Any, *, key_name: str, label: str) -> dict[str, float]:
    """``[{symbol|name, value}]`` または ``{記号: 値}`` → ``{記号: 数値}``。数値以外は 422。"""
    if value is None:
        return {}
    items: list[tuple[Any, Any]] = []
    if isinstance(value, dict):
        items = list(value.items())
    elif isinstance(value, list):
        for entry in value:
            if not isinstance(entry, dict):
                raise HTTPException(status_code=422, detail=f"{label}の形が正しくありません。")
            items.append((entry.get(key_name, entry.get("symbol", entry.get("name"))), entry.get("value")))
    else:
        raise HTTPException(status_code=422, detail=f"{label}の形が正しくありません。")
    out: dict[str, float] = {}
    for name, number in items:
        key = str(name or "").strip()
        if not key:
            raise HTTPException(status_code=422, detail=f"{label}に名前の無い値があります。")
        if not _is_number(number):
            raise HTTPException(status_code=422, detail=f"{label}には数値だけを入れてください（{key}）。")
        out[key] = float(number)
    return out


#: ベクトル入力の上限（波数ベクトル等。長い列は実測データの取り込みであって v1 の対象外）。
_VECTOR_MAX_LEN = 64


def _value_pairs(value: Any, *, label: str) -> dict[str, float | list[float]]:
    """入力の値: 数値、または数値の列（ベクトル。カンマ区切りの画面入力が ``[..]`` で届く）。

    生成コードは波数ベクトルのような配列入力を期待することがある（実機 2026-10-03:
    ``TypeError: 'float' object is not iterable``）。スカラーだけに縛らない。
    """
    if value is None:
        return {}
    items: list[tuple[Any, Any]] = []
    if isinstance(value, dict):
        items = list(value.items())
    elif isinstance(value, list):
        for entry in value:
            if not isinstance(entry, dict):
                raise HTTPException(status_code=422, detail=f"{label}の形が正しくありません。")
            items.append((entry.get("symbol", entry.get("name")), entry.get("value")))
    else:
        raise HTTPException(status_code=422, detail=f"{label}の形が正しくありません。")
    out: dict[str, float | list[float]] = {}
    for name, number in items:
        key = str(name or "").strip()
        if not key:
            raise HTTPException(status_code=422, detail=f"{label}に名前の無い値があります。")
        if isinstance(number, list):
            if not number or len(number) > _VECTOR_MAX_LEN or not all(_is_number(x) for x in number):
                raise HTTPException(status_code=422, detail=f"{label}のベクトルは数値だけの短い列にしてください（{key}）。")
            out[key] = [float(x) for x in number]
            continue
        if not _is_number(number):
            raise HTTPException(status_code=422, detail=f"{label}には数値だけを入れてください（{key}）。")
        out[key] = float(number)
    return out


def _prediction_list(value: Any) -> list[dict]:
    """予想を ``[{symbol, value}]`` にする（数値だけ。そのまま保存して並置する = SB14）。"""
    pairs = _numeric_pairs(value, key_name="symbol", label="予想")
    return [{"symbol": k, "value": v} for k, v in pairs.items()]


def _instrument_params(value: Any) -> dict[str, dict[str, float]]:
    """``{slot: {name: value}}`` / ``[{slot, params: [{name, value}]}]`` → ``{slot: {name: 数値}}``。"""
    if value is None:
        return {}
    out: dict[str, dict[str, float]] = {}
    if isinstance(value, dict):
        for slot, params in value.items():
            out[str(slot)] = _numeric_pairs(params, key_name="name", label="部品のパラメータ")
        return out
    if isinstance(value, list):
        for entry in value:
            if not isinstance(entry, dict) or not str(entry.get("slot") or "").strip():
                raise HTTPException(status_code=422, detail="部品のパラメータの形が正しくありません。")
            out[str(entry["slot"])] = _numeric_pairs(entry.get("params"), key_name="name", label="部品のパラメータ")
        return out
    raise HTTPException(status_code=422, detail="部品のパラメータの形が正しくありません。")


def _reference(value: Any) -> dict[str, dict[str, Any]] | None:
    """論文の記載値 ``[{symbol, value, unit, source_note}]`` / ``{sym: {...}}`` → ``{sym: {...}}``。"""
    if value is None:
        return None
    entries: list[tuple[Any, Any]] = []
    if isinstance(value, dict):
        entries = list(value.items())
    elif isinstance(value, list):
        for entry in value:
            if not isinstance(entry, dict):
                raise HTTPException(status_code=422, detail="論文の記載値の形が正しくありません。")
            entries.append((entry.get("symbol"), entry))
    else:
        raise HTTPException(status_code=422, detail="論文の記載値の形が正しくありません。")
    out: dict[str, dict[str, Any]] = {}
    for symbol, entry in entries:
        key = str(symbol or "").strip()
        if not key:
            continue
        raw = entry.get("value") if isinstance(entry, dict) else entry
        if not _is_number(raw):
            raise HTTPException(status_code=422, detail=f"論文の記載値には数値だけを入れてください（{key}）。")
        out[key] = {
            "value": float(raw),
            "unit": str((entry.get("unit") if isinstance(entry, dict) else "") or ""),
            "source_note": str((entry.get("source_note") if isinstance(entry, dict) else "") or ""),
        }
    return out or None


def _wiring_parts(parts: list[dict[str, Any]]) -> list[dict]:
    out = []
    for part in parts or []:
        if not isinstance(part, dict):
            raise HTTPException(status_code=422, detail="部品の形が正しくありません。")
        version = part.get("version_no")
        out.append({
            "slot": str(part.get("slot") or ""),
            "entry_id": str(part.get("entry_id") or ""),
            "version_no": int(version) if _is_number(version) else None,
            "params": _numeric_pairs(part.get("params"), key_name="name", label="部品のパラメータ"),
        })
    return out


def _wiring_edges(edges: list[dict[str, Any]]) -> list[dict]:
    return [
        {"from_slot": str(e.get("from_slot") or ""), "to_slot": str(e.get("to_slot") or "")}
        for e in edges or [] if isinstance(e, dict)
    ]


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


def _sandbox_client():
    """sim-runner クライアント（テストはこの関数を差し替える）。"""
    return sandbox_client_from_settings(get_settings())


def _uid(current_user: dict) -> str:
    return str(current_user.get("id") or "")


def _require_course(user_id: str, course_id: str) -> dict:
    course_data = get_accessible_course_data(user_id, course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")
    return course_data


def _course_document_ids(course_data: dict) -> set[str]:
    try:
        return {str(d) for d in list_course_source_document_ids(course_data) if str(d or "").strip()}
    except Exception:  # noqa: BLE001 — 解決できなければ空（fail-closed）
        logger.debug("simulation lab: course documents unavailable", exc_info=True)
        return set()


def _canonical(session: Any, ref: str) -> str:
    text = str(ref or "").strip()
    if not text:
        return ""
    if is_document_uuid(text):
        return text
    try:
        ids = canonical_document_ids(session, [text])
    except Exception:  # noqa: BLE001
        service.rollback_quietly(session)
        ids = []
    return ids[0] if ids else ""


def _resolve_document(
    session: Any,
    current_user: dict,
    course_data: dict,
    course_id: str,
    *,
    document_id: str | None,
    topic_id: str | None,
    allowed: set[str],
    required: bool,
) -> str:
    """教材を 1 つに決める（指定があればそれ・無ければ焦点論文）。コースの sources の外は 404。"""
    explicit = (document_id or "").strip()
    if explicit:
        doc = _canonical(session, explicit)
        if not doc or doc not in allowed:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        return doc
    try:
        focus = learner_focus_document(
            current_user, course_data, course_id, (topic_id or "").strip() or None, allowed_document_ids=allowed
        )
        ids = [d for d in focus.document_ids if d in allowed]
    except Exception:  # noqa: BLE001 — 焦点は補助（未選択として返す）
        logger.debug("simulation lab: focus unavailable", exc_info=True)
        ids = []
    if ids:
        return ids[0]
    if required:
        raise HTTPException(status_code=422, detail=schema.FACT_SELECT_DOCUMENT)
    return ""


def _document_titles(session: Any, document_ids: list[str]) -> dict[str, str]:
    ids = [d for d in document_ids if is_document_uuid(d)]
    if not ids:
        return {}
    try:
        rows = session.execute(
            sa_text("SELECT id::text, title FROM documents WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": ids},
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.debug("simulation lab: document titles unavailable", exc_info=True)
        service.rollback_quietly(session)
        return {}
    return {str(r[0]): str(r[1] or "") for r in rows or [] if r and r[0]}


def _own_or_404(row: dict | None, user_id: str, detail: str) -> dict:
    if not row or not store.is_owner(row, user_id):
        raise HTTPException(status_code=404, detail=detail)
    return row


def _get_or_none(getter, session: Any, row_id: str) -> dict | None:
    try:
        return getter(session, row_id)
    except Exception:  # noqa: BLE001 — UUID でない id 等は不在と同じ扱い
        service.rollback_quietly(session)
        return None


def _require_scope(user_id: str, row: dict, detail: str) -> dict:
    """本人の行の学習文脈がまだ有効か（受講ゲート + 教材がコースの sources に残っている）。"""
    course_id = str(row.get("course_id") or "")
    if not course_id:
        raise HTTPException(status_code=404, detail=detail)
    course_data = _require_course(user_id, course_id)
    document_id = str(row.get("document_id") or "")
    if document_id and document_id not in _course_document_ids(course_data):
        raise HTTPException(status_code=404, detail=detail)
    return course_data


def _modules(document_id: str) -> tuple[dict, list[dict]]:
    try:
        dto = build_theory_modules_for_document(document_id) or {}
    except Exception:  # noqa: BLE001 — 導出不能は available:false で正直に縮退
        logger.debug("simulation lab: theory modules unavailable", exc_info=True)
        dto = {}
    return dto, [m for m in (dto.get("modules") or []) if isinstance(m, dict)]


def _consume_quota(user_id: str) -> None:
    settings = get_settings()
    consume_daily_quota(
        _learner_cost_gate,
        user_id=user_id,
        limit=int(getattr(settings, "simulation_learner_max_calls_per_day", 20) or 0),
        message=_QUOTA_MESSAGE,
    )


def _targets(session: Any, document_id: str) -> list[dict]:
    try:
        rows = observation_claim_targets(session, document_id=document_id)
    except Exception:  # noqa: BLE001
        logger.debug("simulation lab: observation targets unavailable", exc_info=True)
        service.rollback_quietly(session)
        rows = []
    return [
        {"claim_id": str(t.get("claim_id") or ""), "label": t.get("label") or ""}
        for t in rows or [] if isinstance(t, dict) and t.get("claim_id")
    ]


# ---------------------------------------------------------------------------
# 学習者向け DTO（記号をキーにした写像はリスト・所有者 / 作成者 / confidence なし）
# ---------------------------------------------------------------------------


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in schema.FORBIDDEN_DTO_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _program_dto(program: dict | None) -> dict | None:
    if not program:
        return None
    provenance = dict(program.get("provenance") or {})
    return _strip({
        "id": program.get("id"),
        "module_key": program.get("module_key"),
        "review_status": program.get("review_status"),
        "source_code": program.get("source_code") or "",
        "io_contract": dict(program.get("io_contract") or {}),
        "provenance": {
            "reconstructed_inputs": bool(provenance.get("reconstructed_inputs")),
            "facts": list(provenance.get("facts") or []),
            "ast_ok": bool(provenance.get("ast_ok")),
            "ast_reasons": list(provenance.get("ast_reasons") or []),
        },
        "facts": list(provenance.get("facts") or []),
        "created_at": program.get("created_at"),
    })


def _name_value_list(mapping: Any) -> list[dict]:
    return [{"name": item["symbol"], "value": item["value"]} for item in service.symbol_value_list(mapping)]


def _wiring_dto(wiring: dict | None, catalog: dict[str, dict]) -> dict | None:
    if not wiring:
        return None
    parts = []
    for part in wiring.get("parts") or []:
        if not isinstance(part, dict):
            continue
        entry = catalog.get(str(part.get("entry_id") or "")) or {}
        parts.append({
            "slot": part.get("slot"),
            "entry_id": part.get("entry_id"),
            "version_no": part.get("version_no"),
            "params": _name_value_list(part.get("params") or {}),
            "name": entry.get("name") or "",
            "entry_type": entry.get("entry_type") or "",
        })
    return _strip({
        "id": wiring.get("id"),
        "name": wiring.get("name") or "",
        "parts": parts,
        "edges": list(wiring.get("edges") or []),
        "origin": wiring.get("origin"),
        "review_status": wiring.get("review_status"),
        "parent_wiring_id": wiring.get("parent_wiring_id"),
        "revision_note": wiring.get("revision_note") or "",
        "created_at": wiring.get("created_at"),
    })


def _run_dto(run: dict | None) -> dict | None:
    if not run:
        return None
    inputs = dict(run.get("inputs") or {})
    result = dict(run.get("result") or {})
    comparison = dict(result.get("comparison") or {})
    metrics = dict(comparison.get("metrics") or {})
    learner_result: dict[str, Any] = {
        "theory_outputs": service.symbol_value_list(result.get("theory_outputs") or {}),
        "observed": service.observed_list(result.get("observed") or {}),
        "comparison": {
            "agreement_level": comparison.get("agreement_level") or schema.AGREEMENT_LEVEL_UNMEASURED,
            "separation_level": comparison.get("separation_level") or schema.SEPARATION_LEVEL_UNMEASURED,
            "reference_level": comparison.get("reference_level") or schema.AGREEMENT_LEVEL_UNMEASURED,
            "facts": list(comparison.get("facts") or []),
            # SB6 の読み替え（§14.1）: 本人の run の数値は本人に見せてよい。
            "metrics": {
                schema.METRIC_RELATIVE_DIFFERENCE: metrics.get(schema.METRIC_RELATIVE_DIFFERENCE),
                schema.METRIC_SEPARATION_RATIO: metrics.get(schema.METRIC_SEPARATION_RATIO),
                schema.METRIC_REFERENCE_DIFFERENCE: metrics.get(schema.METRIC_REFERENCE_DIFFERENCE),
                "by_symbol": service.metrics_by_symbol_list(metrics.get("by_symbol") or {}),
            },
        } if comparison else None,
        "labels": dict(result.get("labels") or {}),
        "statement": str(result.get("statement") or ""),
        "facts": list(result.get("facts") or []),
    }
    return _strip({
        "id": run.get("id"),
        "program_id": run.get("program_id"),
        "wiring_id": run.get("wiring_id"),
        "status": run.get("status"),
        "observation_claim_ids": list(run.get("observation_claim_ids") or []),
        "prediction": service.symbol_value_list(run.get("prediction") or []),
        "inputs": {
            "values": service.symbol_value_list(inputs.get("values") or {}),
            "params": _name_value_list(inputs.get("params") or {}),
            "reference": service.reference_list(inputs.get("reference") or {}),
            "seed": inputs.get("seed"),
        },
        "result": learner_result,
        "shared": bool(run.get("shared_at")),
        "created_at": run.get("created_at"),
    })


def _parts_catalog_dto(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows or []:
        body = dict(row.get("body") or {})
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        out.append({
            "entry_id": row.get("entry_id"),
            "version_no": row.get("version_no"),
            "entry_type": row.get("entry_type"),
            "entry_type_label": row.get("entry_type_label") or "",
            "name": row.get("name") or "",
            "summary": row.get("summary") or "",
            "params": [
                {
                    "name": str(name),
                    "default": (spec or {}).get("default") if isinstance(spec, dict) else spec,
                    "unit": str(((spec or {}).get("unit") if isinstance(spec, dict) else "") or ""),
                    "min": (spec or {}).get("min") if isinstance(spec, dict) else None,
                    "max": (spec or {}).get("max") if isinstance(spec, dict) else None,
                }
                for name, spec in sorted(params.items(), key=lambda kv: str(kv[0]))
            ],
            "noise_kind": str(body.get("noise_kind") or ""),
            "removes": [str(r) for r in body.get("removes") or [] if str(r or "").strip()]
            if isinstance(body.get("removes"), list)
            else ([str(body["removes"])] if body.get("removes") else []),
        })
    return out


def _load_catalog(session: Any) -> tuple[list[dict], dict[str, dict]]:
    rows = parts_catalog.load_parts_catalog(session)
    return rows, parts_catalog.catalog_by_id(rows)


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


@learning_router.get("/courses/{course_id}/simulation/overview")
def get_simulation_lab_overview(
    course_id: str,
    document_id: str = Query(""),
    topic_id: str = Query(""),
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """焦点論文の理論モジュール + 本人の programs / wirings / runs + 部品目録 + 観測系の主張。"""
    uid = _uid(current_user)
    course_data = _require_course(uid, course_id)
    allowed = _course_document_ids(course_data)
    session = _pg_session()
    try:
        doc = _resolve_document(
            session, current_user, course_data, course_id,
            document_id=document_id, topic_id=topic_id, allowed=allowed, required=False,
        )
        titles = _document_titles(session, sorted(allowed))
        documents = [
            {"document_id": d, "title": titles.get(d, "")} for d in sorted(allowed, key=lambda d: (titles.get(d, ""), d))
        ]
        client = _sandbox_client()
        catalog_rows, catalog = _load_catalog(session)
        base = {
            "course_id": course_id,
            "document_id": doc or None,
            "document_title": titles.get(doc, "") if doc else "",
            "documents": documents,
            "sandbox_configured": bool(getattr(client, "configured", False)),
            "parts_catalog": _parts_catalog_dto(catalog_rows),
        }
        if not doc:
            return {
                **base,
                "available": False,
                "facts": [schema.FACT_SELECT_DOCUMENT],
                "modules": [],
                "programs": [],
                "wirings": [],
                "runs": [],
                "observation_targets": [],
            }
        dto, modules = _modules(doc)
        facts = [str(f) for f in (dto.get("facts") or []) if str(f or "").strip()]
        records = service.saved_module_records(session, doc)
        equations = {str(e.get("agent_equation_id") or ""): e for e in service.equation_rows(session, doc, None)}
        programs = store.list_programs(session, document_id=doc, owner_user_id=uid)
        module_items = service.module_items(
            modules,
            records=records,
            equations_by_id=equations,
            latest_program_by_module=service.latest_by_module(programs),
            program_dto=_program_dto,
        )
        wirings = store.list_wirings(session, document_id=doc, owner_user_id=uid)
        runs = store.list_runs(session, document_id=doc, owner_user_id=uid)
        if not catalog_rows:
            facts.append(schema.FACT_NO_PARTS_CATALOG)
        return {
            **base,
            "available": bool(modules),
            "facts": facts,
            "modules": module_items,
            "programs": [_program_dto(p) for p in programs],
            "wirings": [_wiring_dto(w, catalog) for w in wirings],
            "runs": [_run_dto(r) for r in runs],
            "observation_targets": _targets(session, doc),
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 理論プログラム（生成・自分で書く・採用）
# ---------------------------------------------------------------------------


@learning_router.post("/courses/{course_id}/simulation/programs/generate")
def generate_simulation_lab_program(
    course_id: str,
    body: GenerateRequest = Body(...),
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """理論モジュール 1 つから本人所有のプログラム候補を作る（LLM 1 コール・本人キーの日次上限）。"""
    uid = _uid(current_user)
    course_data = _require_course(uid, course_id)
    allowed = _course_document_ids(course_data)
    module_key = (body.module_key or "").strip()
    if not module_key:
        raise HTTPException(status_code=422, detail="理論モジュールを指定してください。")
    session = _pg_session()
    try:
        doc = _resolve_document(
            session, current_user, course_data, course_id,
            document_id=body.document_id, topic_id=body.topic_id, allowed=allowed, required=True,
        )
        _dto, modules = _modules(doc)
        module = service.find_module(modules, module_key)
        if module is None:
            raise HTTPException(status_code=404, detail="この理論モジュールは見つかりません。")
        record, equations, symbols = service.fetch_module_inputs(session, doc, module)
        if not any(service.has_text(row) for row in equations):
            raise HTTPException(status_code=422, detail=schema.FACT_NO_MODULE_EQUATIONS)
        _consume_quota(uid)
        with usage_context(feature=schema.FEATURE_LEARNER_CODEGEN, user_id=uid or None, document_id=doc):
            draft, facts, provenance = service.generate_draft(
                module,
                equations=equations,
                symbols=symbols,
                structured_fn=generate_text_with_structured_output,
                text_fn=generate_text,
            )
        if not draft.source_code:
            raise HTTPException(status_code=422, detail=" ".join(facts) or codegen.FACT_GENERATION_FAILED)
        try:
            program = store.create_program(
                session,
                document_id=doc,
                module_key=module_key,
                module_stable_key=str((record or {}).get("stable_key") or ""),
                source_code=draft.source_code,
                io_contract=draft.io_contract,
                provenance=provenance,
                created_by=uid,
                review_status=schema.PROGRAM_STATUS_CANDIDATE,
                owner_user_id=uid,
                course_id=course_id,
            )
        except store.SimulationStateError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
    finally:
        session.close()
    return {"program": _program_dto(program), "facts": facts}


@learning_router.post("/simulation/programs/{program_id}/edit")
def edit_simulation_lab_program(
    program_id: str,
    body: EditRequest = Body(...),
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人が書いた・直したコードを本人の新しい版として保存する（旧版は superseded・SB7）。

    AST 検査を通らないコードも候補として保存する（``ast_ok=False``）。採用・実行はできない。
    """
    uid = _uid(current_user)
    source = str(body.source_code or "")
    if not source.strip():
        raise HTTPException(status_code=422, detail="コードを入れてください。")
    if len(source) > _SOURCE_MAX_CHARS:
        raise HTTPException(status_code=422, detail="コードが長すぎます。")
    session = _pg_session()
    try:
        previous = _own_or_404(_get_or_none(store.get_program, session, program_id), uid, _PROGRAM_NOT_FOUND)
        _require_scope(uid, previous, _PROGRAM_NOT_FOUND)
        ok, reasons = service.check_source(source)
        old_provenance = dict(previous.get("provenance") or {})
        facts = [schema.FACT_LEARNER_CODE_EDITED]
        if not ok:
            facts.append(schema.FACT_LEARNER_CODE_REJECTED)
        if old_provenance.get("reconstructed_inputs"):
            facts.append(schema.FACT_RECONSTRUCTED_INPUTS)
        provenance = {
            "origin": "learner_edit",
            "edited_from_id": previous.get("id"),
            "reconstructed_inputs": bool(old_provenance.get("reconstructed_inputs")),
            "facts": facts,
            "ast_ok": ok,
        }
        if not ok:
            provenance["ast_reasons"] = reasons
        try:
            program = store.create_program(
                session,
                document_id=str(previous.get("document_id") or ""),
                module_key=str(previous.get("module_key") or ""),
                module_stable_key=str(previous.get("module_stable_key") or ""),
                source_code=source,
                io_contract=dict(previous.get("io_contract") or {}),
                provenance=provenance,
                created_by=uid,
                review_status=schema.PROGRAM_STATUS_CANDIDATE,
                owner_user_id=uid,
                course_id=str(previous.get("course_id") or "") or None,
            )
        except store.SimulationStateError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
    finally:
        session.close()
    return {"program": _program_dto(program), "facts": facts}


@learning_router.post("/simulation/programs/{program_id}/adopt")
def adopt_simulation_lab_program(program_id: str, current_user: dict = Depends(_get_current_user)) -> dict:
    """本人の採用（この版を使う = ``confirmed``・SB11）。AST 検査を通らないコードは 422。"""
    uid = _uid(current_user)
    session = _pg_session()
    try:
        program = _own_or_404(_get_or_none(store.get_program, session, program_id), uid, _PROGRAM_NOT_FOUND)
        _require_scope(uid, program, _PROGRAM_NOT_FOUND)
        ok, _reasons = service.check_source(str(program.get("source_code") or ""))
        if not ok or not (program.get("provenance") or {}).get("ast_ok"):
            raise HTTPException(status_code=422, detail=schema.FACT_LEARNER_CODE_REJECTED)
        try:
            store.decide_program(
                session,
                program_id=str(program.get("id")),
                action="confirm",
                actor_id=uid,
                note="",
                record_audit=_noop_audit,
            )
        except store.SimulationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=_PROGRAM_NOT_FOUND) from exc
        except store.SimulationStateError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.commit()
        refreshed = store.get_program(session, str(program.get("id"))) or program
    finally:
        session.close()
    return {"program": _program_dto(refreshed)}


# ---------------------------------------------------------------------------
# 部品目録・配線
# ---------------------------------------------------------------------------


@learning_router.get("/simulation/parts")
def list_simulation_lab_parts(current_user: dict = Depends(_get_current_user)) -> dict:
    """凍結版の部品目録（装置モデル / ノイズ源 / 信号処理の工夫）。"""
    session = _pg_session()
    try:
        rows, _catalog = _load_catalog(session)
    finally:
        session.close()
    return {"parts": _parts_catalog_dto(rows), "facts": [] if rows else [schema.FACT_NO_PARTS_CATALOG]}


@learning_router.get("/courses/{course_id}/simulation/wirings")
def list_simulation_lab_wirings(
    course_id: str,
    document_id: str = Query(""),
    topic_id: str = Query(""),
    current_user: dict = Depends(_get_current_user),
) -> dict:
    uid = _uid(current_user)
    course_data = _require_course(uid, course_id)
    allowed = _course_document_ids(course_data)
    session = _pg_session()
    try:
        doc = _resolve_document(
            session, current_user, course_data, course_id,
            document_id=document_id, topic_id=topic_id, allowed=allowed, required=True,
        )
        _rows, catalog = _load_catalog(session)
        wirings = store.list_wirings(session, document_id=doc, owner_user_id=uid)
    finally:
        session.close()
    return {"wirings": [_wiring_dto(w, catalog) for w in wirings]}


@learning_router.post("/courses/{course_id}/simulation/wirings")
def create_simulation_lab_wiring(
    course_id: str,
    body: LearnerWiringRequest = Body(...),
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人が部品目録から配線を組む（``origin='learner'``・本人の操作なので ``confirmed`` で作る）。"""
    uid = _uid(current_user)
    course_data = _require_course(uid, course_id)
    allowed = _course_document_ids(course_data)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="配線の名前を入れてください。")
    parts = _wiring_parts(body.parts)
    edges = _wiring_edges(body.edges)
    session = _pg_session()
    try:
        doc = _resolve_document(
            session, current_user, course_data, course_id,
            document_id=body.document_id, topic_id=body.topic_id, allowed=allowed, required=True,
        )
        _rows, catalog = _load_catalog(session)
        problems = instrument.validate_wiring(parts, edges, catalog)
        if problems:
            raise HTTPException(status_code=422, detail=" ".join(problems))
        try:
            wiring = store.create_wiring(
                session,
                document_id=doc,
                name=name,
                parts=parts,
                edges=edges,
                origin=schema.WIRING_ORIGIN_LEARNER,
                created_by=uid,
                owner_user_id=uid,
                course_id=course_id,
            )
        except store.SimulationStateError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
    finally:
        session.close()
    return {"wiring": _wiring_dto(wiring, catalog)}


# ---------------------------------------------------------------------------
# 実行・一覧・共有・改訂案
# ---------------------------------------------------------------------------


def _module_label(document_id: str, module_key: str) -> str:
    _dto, modules = _modules(document_id)
    return str((service.find_module(modules, module_key) or {}).get("label") or "")


@learning_router.post("/courses/{course_id}/simulation/runs")
def create_simulation_lab_run(
    course_id: str,
    body: LearnerRunRequest = Body(...),
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人が採用したプログラム（と本人の配線）を sim-runner で走らせ、比較して 1 行記録する。

    成功した run ごとに痕跡 ``verification_attempt`` を 1 行残す（数値を持たない = SB15）。
    """
    uid = _uid(current_user)
    course_data = _require_course(uid, course_id)
    allowed = _course_document_ids(course_data)
    prediction = _prediction_list(body.prediction)
    values = _value_pairs(body.values, label="入力の値")
    params = _numeric_pairs(body.params, key_name="name", label="パラメータ")
    instrument_params = _instrument_params(body.instrument_params)
    reference = _reference(body.reference)
    session = _pg_session()
    try:
        program = _own_or_404(
            _get_or_none(store.get_program, session, (body.program_id or "").strip()), uid, _PROGRAM_NOT_FOUND
        )
        doc = str(program.get("document_id") or "")
        if doc not in allowed:
            raise HTTPException(status_code=404, detail=_PROGRAM_NOT_FOUND)
        if program.get("review_status") != schema.PROGRAM_STATUS_CONFIRMED:
            raise HTTPException(status_code=422, detail="採用したプログラムだけを実行できます。")
        wiring = None
        catalog: dict[str, dict] = {}
        wiring_id = (body.wiring_id or "").strip()
        if wiring_id:
            wiring = _own_or_404(_get_or_none(store.get_wiring, session, wiring_id), uid, _WIRING_NOT_FOUND)
            wiring_document = str(wiring.get("document_id") or "")
            if wiring_document and wiring_document != doc:
                raise HTTPException(status_code=404, detail=_WIRING_NOT_FOUND)
            if wiring.get("review_status") != schema.WIRING_STATUS_CONFIRMED:
                raise HTTPException(status_code=422, detail="採用した配線だけを実行に使えます。")
            _rows, catalog = _load_catalog(session)
        known_claims = {t["claim_id"] for t in _targets(session, doc)}
        claim_ids = [str(c).strip() for c in body.observation_claim_ids if str(c or "").strip()]
        unknown = [c for c in claim_ids if c not in known_claims]
        if unknown:
            raise HTTPException(status_code=422, detail="この論文の観測系の主張から選んでください。")
        module_key = str(program.get("module_key") or "")
        module_label = _module_label(doc, module_key)
        try:
            status, result, runner = service.run_pipeline(
                program=program,
                wiring=wiring,
                catalog=catalog,
                values=values,
                params=params,
                instrument_params=instrument_params,
                reference=reference,
                seed=body.seed,
                sandbox_client=_sandbox_client(),
                module_label=module_label,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        run = store.record_run(
            session,
            document_id=doc,
            program_id=str(program.get("id")),
            wiring_id=wiring_id or None,
            observation_claim_ids=claim_ids,
            inputs={
                "values": values,
                "params": params,
                "instrument_params": instrument_params,
                "reference": reference,
                "seed": body.seed,
            },
            result=result,
            runner=runner,
            status=status,
            created_by=uid,
            owner_user_id=uid,
            course_id=course_id,
            prediction=prediction,
        )
        session.commit()
    finally:
        session.close()
    if status == schema.RUN_STATUS_SUCCEEDED:
        comparison = dict(result.get("comparison") or {})
        label = module_label or "理論モジュール"
        record_interest_trace(
            uid,
            course_id,
            (body.topic_id or "").strip() or None,
            kind=schema.TRACE_KIND_VERIFICATION_ATTEMPT,
            text=f"『{label}』を動かして確かめました。",
            context_label="",
            extra_payload={
                "run_id": str(run.get("id") or ""),
                "document_id": doc,
                "module_key": module_key,
                "agreement_level": str(comparison.get("agreement_level") or schema.AGREEMENT_LEVEL_UNMEASURED),
                "separation_level": str(comparison.get("separation_level") or schema.SEPARATION_LEVEL_UNMEASURED),
                "shared": False,
            },
            status="open",
        )
    return {"run": _run_dto(run)}


@learning_router.get("/courses/{course_id}/simulation/runs")
def list_simulation_lab_runs(
    course_id: str,
    document_id: str = Query(""),
    topic_id: str = Query(""),
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人の run だけ（新しい順）。"""
    uid = _uid(current_user)
    course_data = _require_course(uid, course_id)
    allowed = _course_document_ids(course_data)
    session = _pg_session()
    try:
        doc = _resolve_document(
            session, current_user, course_data, course_id,
            document_id=document_id, topic_id=topic_id, allowed=allowed, required=True,
        )
        runs = store.list_runs(session, document_id=doc, owner_user_id=uid)
    finally:
        session.close()
    return {"runs": [_run_dto(r) for r in runs]}


@learning_router.get("/simulation/runs/{run_id}")
def get_simulation_lab_run(run_id: str, current_user: dict = Depends(_get_current_user)) -> dict:
    uid = _uid(current_user)
    session = _pg_session()
    try:
        run = _own_or_404(_get_or_none(store.get_run, session, run_id), uid, _RUN_NOT_FOUND)
    finally:
        session.close()
    _require_scope(uid, run, _RUN_NOT_FOUND)
    return {"run": _run_dto(run)}


@learning_router.post("/simulation/runs/{run_id}/share")
def share_simulation_lab_run(
    run_id: str,
    body: ShareRequest = Body(default=None),
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人の run を担当教員と共有する（SB13・冪等）。

    初めて共有したときだけ、成功した run の観測系の主張の台帳に候補を追記する（確定は教員）。
    """
    uid = _uid(current_user)
    session = _pg_session()
    facts: list[str] = []
    try:
        run = _own_or_404(_get_or_none(store.get_run, session, run_id), uid, _RUN_NOT_FOUND)
        _require_scope(uid, run, _RUN_NOT_FOUND)
        already = bool(run.get("shared_at"))
        try:
            shared = store.set_run_shared(session, run_id=str(run.get("id")), owner_user_id=uid)
        except store.SimulationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=_RUN_NOT_FOUND) from exc
        except store.SimulationStateError as exc:
            session.rollback()
            raise HTTPException(status_code=404, detail=_RUN_NOT_FOUND) from exc
        facts.append(schema.FACT_SHARED)
        if not already:
            if run.get("status") != schema.RUN_STATUS_SUCCEEDED:
                facts.append(schema.FACT_SHARED_NOT_SUCCEEDED)
            else:
                requested = body.claim_ids if body is not None and body.claim_ids is not None else None
                known = {t["claim_id"] for t in _targets(session, str(run.get("document_id") or ""))}
                source = requested if requested is not None else run.get("observation_claim_ids") or []
                claim_ids = [str(c).strip() for c in source if str(c or "").strip() in known]
                if claim_ids:
                    try:
                        service.ledger_candidates_for_run(
                            session,
                            run=shared,
                            claim_ids=claim_ids,
                            created_by=uid,
                            store_module=store,
                            record_audit=None,
                        )
                    except store.SimulationStateError as exc:
                        session.rollback()
                        raise HTTPException(status_code=422, detail=str(exc)) from exc
                    facts.append(schema.FACT_SHARED_LEDGER)
                else:
                    facts.append(schema.FACT_SHARED_NO_LEDGER)
        session.commit()
        refreshed = store.get_run(session, str(run.get("id"))) or shared
    finally:
        session.close()
    if not already:
        mark_verification_attempt_shared(uid, str(refreshed.get("id") or ""))
    return {"run": _run_dto(refreshed), "facts": facts}


@learning_router.post("/simulation/runs/{run_id}/propose-revision")
def propose_simulation_lab_revision(run_id: str, current_user: dict = Depends(_get_current_user)) -> dict:
    """本人の配線の改訂案を 1 回だけ求める（部品の組み替えのみ・候補 = SB5）。"""
    uid = _uid(current_user)
    session = _pg_session()
    try:
        run = _own_or_404(_get_or_none(store.get_run, session, run_id), uid, _RUN_NOT_FOUND)
        _require_scope(uid, run, _RUN_NOT_FOUND)
        wiring_id = str(run.get("wiring_id") or "")
        comparison = dict((run.get("result") or {}).get("comparison") or {})
        if not wiring_id or not comparison:
            raise HTTPException(status_code=422, detail="配線を使って比較まで終わった実行だけに改訂案を求められます。")
        parent = _own_or_404(_get_or_none(store.get_wiring, session, wiring_id), uid, _WIRING_NOT_FOUND)
        _rows, catalog = _load_catalog(session)
        _consume_quota(uid)
        with usage_context(
            feature=schema.FEATURE_LEARNER_REVISION,
            user_id=uid or None,
            document_id=str(run.get("document_id") or "") or None,
        ):
            proposal = revision.propose_revision(
                parent,
                comparison,
                catalog,
                structured_fn=generate_text_with_structured_output,
                text_fn=generate_text,
            )
        proposal_dto = {
            "changes": [
                {
                    "op": c.op,
                    "slot": c.slot,
                    "entry_id": c.entry_id,
                    "param": c.param,
                    "value": c.value,
                    "reason": c.reason,
                }
                for c in proposal.changes
            ],
            "dropped": list(proposal.dropped),
            "facts": list(proposal.facts),
        }
        if proposal.empty:
            return {"wiring": None, "proposal": {"changes": [], "dropped": proposal_dto["dropped"], "facts": proposal_dto["facts"]}}
        note = " / ".join(c.reason for c in proposal.changes if c.reason)
        try:
            wiring = store.create_wiring(
                session,
                document_id=str(parent.get("document_id") or "") or None,
                name=f"{parent.get('name') or ''}（改訂案）",
                parts=proposal.parts,
                edges=proposal.edges,
                origin=schema.WIRING_ORIGIN_LLM,
                created_by=uid,
                parent_wiring_id=wiring_id,
                revision_note=note,
                owner_user_id=uid,
                course_id=str(run.get("course_id") or "") or None,
            )
        except store.SimulationStateError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
    finally:
        session.close()
    return {"wiring": _wiring_dto(wiring, catalog), "proposal": proposal_dto}


__all__ = ["learning_router"]
