"""検証シミュレーション層（Simulation Bridge, SB層）の教員向け API。

実パス: ``/api/admin/simulation/...``（admin 系子ルーターとして main.py から
``prefix="/api/admin"`` で直接登録される — seminar_brief.admin_router と同型）。

正本: ``docs/features/simulation_bridge_design.md``（SB1〜SB10・§6 API 表）。

- 全エンドポイント ``_require_teacher``。読み取りは document の閲覧権
  （``_ensure_document_viewable``）、書き込み・実行・LLM 呼び出しは編集権
  （``_ensure_document_editable``）。不可視・不在は同じ 404（fail-closed）。
- 生成コードはこのプロセスで実行しない（SB3）。実行は ``sim-runner``（``SandboxClient``）に
  依頼し、その前段でサーバ側 AST 検査を毎回やり直す。
- 状態遷移は ``core/simulation_bridge/store.py``（DELETE なし = SB7）。store は commit
  しないので、ここで commit し、監査はコミット後にまとめて記帳する（entity_type は
  ``AUDIT_ENTITY_SIMULATION`` 固定）。
- LLM を呼ぶのは「コードを生成」と「改訂案を求める」の 2 本だけ（各 1 コール + 日次 CostGate = SB9）。
- 学習者向けの経路は無い（SB6）。confidence はどの DTO にも載せない。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import functools

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from dependencies import ROLE_SYSTEM_ADMIN, _require_teacher
from quota import consume_daily_quota
from routes.theory_components import (
    _ensure_document_editable,
    _ensure_document_viewable,
    build_theory_modules_for_document,
)
from services import record_review_event

from core import llm_policy
from core.config import get_settings
from core.document_refs import canonical_document_ids, is_document_uuid
from core.doubt.observation_targets import observation_claim_targets
from core.label_vocab import SIMULATION_AGREEMENT_LABELS, SIMULATION_SEPARATION_LABELS
from core.llm import generate_text, generate_text_with_structured_output
from core.llm_usage.context import usage_context
from core.llm_worker.cost_gate import CostGate
from core.postgres import get_session as _pg_session
from core.schema import AUDIT_ENTITY_SIMULATION
from core.simulation_bridge import ast_guard, catalog as parts_catalog, codegen, comparator
from core.simulation_bridge import instrument, revision, schema, store
from core.simulation_bridge.sandbox import sandbox_client_from_settings

logger = logging.getLogger(__name__)

# main.py で prefix="/api/admin" を付けて直接登録される admin 系子ルーター（prefix なし）。
#
# 表示投影について: ``TeacherDisplayRoute``（DP7）は使わない。あの射影は ``k`` / ``weight`` / ``count``
# のようなキーを一律に落とすが、本層の DTO は **物理記号をキーにした写像**（``theory_outputs`` /
# ``observed`` / ``metrics.by_symbol``）を持つので、記号 ``k`` がそのまま消える。代わりに本層専用の
# 射影 ``_project_simulation_payload`` を route_class で全エンドポイントに掛け、落とすのは
# ``schema.FORBIDDEN_DTO_KEYS``（confidence / score / weight / structure_fingerprint 等）だけに限る。
# 文字列は ``strip_control_sequences`` を通す（LLM 生成コード・事実文の衛生）。


def _project_simulation_payload(value):
    from core.text_hygiene import strip_control_sequences

    forbidden = schema.FORBIDDEN_DTO_KEYS

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if k not in forbidden}
        if isinstance(node, (list, tuple)):
            return [walk(v) for v in node]
        if isinstance(node, str):
            return strip_control_sequences(node)
        return node

    if value is None or not isinstance(value, (dict, list, tuple)):
        return value
    try:
        return walk(value)
    except Exception:  # noqa: BLE001 — 射影の失敗で応答を止めない（DP6 と同じ態度）
        logger.warning("simulation display projection failed", exc_info=True)
        return value


class SimulationDisplayRoute(APIRoute):
    """戻り値を ``_project_simulation_payload`` に通す APIRoute（本層専用・同期エンドポイントのみ）。"""

    def __init__(self, path, endpoint, **kwargs):
        if getattr(endpoint, "__simulation_display_projected__", False):
            wrapped = endpoint
        else:

            @functools.wraps(endpoint)
            def wrapped(*args, **kw):
                return _project_simulation_payload(endpoint(*args, **kw))

            wrapped.__simulation_display_projected__ = True  # type: ignore[attr-defined]
        super().__init__(path, wrapped, **kwargs)


admin_router = APIRouter(tags=["SimulationBridge"], route_class=SimulationDisplayRoute)

_codegen_cost_gate = CostGate()
_revision_cost_gate = CostGate()

_QUOTA_MESSAGE = "本日の AI 呼び出し回数の上限に達しました。明日以降にもう一度お試しください。"
_NOT_FOUND = "対象が見つかりません。"
_SYMBOL_LIMIT = 60


# ---------------------------------------------------------------------------
# リクエスト
# ---------------------------------------------------------------------------


class GenerateProgramRequest(BaseModel):
    module_key: str = ""
    model: str | None = None


class DecideRequest(BaseModel):
    action: str = ""
    note: str | None = None


class WiringPart(BaseModel):
    slot: str = ""
    entry_id: str = ""
    version_no: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class WiringEdge(BaseModel):
    from_slot: str = ""
    to_slot: str = ""


class CreateWiringRequest(BaseModel):
    name: str = ""
    parts: list[WiringPart] = Field(default_factory=list)
    edges: list[WiringEdge] = Field(default_factory=list)


class RunRequest(BaseModel):
    program_id: str = ""
    wiring_id: str | None = None
    observation_claim_ids: list[str] = Field(default_factory=list)
    values: dict[str, float] = Field(default_factory=dict)
    params: dict[str, float] = Field(default_factory=dict)
    instrument_params: dict[str, dict[str, float]] = Field(default_factory=dict)
    reference: dict[str, dict[str, Any]] | None = None
    seed: int | None = None


class LedgerCandidateRequest(BaseModel):
    claim_ids: list[str] | None = None


class ProposeRevisionRequest(BaseModel):
    model: str | None = None


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


class _AuditBuffer:
    """store が呼ぶ ``record_audit(**kw)`` を受け、コミット後にまとめて記帳するアダプタ。

    store の書き込みと同じトランザクションの中で別セッションの監査行を先に確定させない
    （本体がロールバックしたのに監査だけ残る食い違いを避ける）。entity_type は常に
    ``AUDIT_ENTITY_SIMULATION``（生の文字列リテラルを渡さない）。
    """

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, **kwargs: Any) -> None:
        self.events.append(dict(kwargs))

    def flush(self) -> None:
        for event in self.events:
            metadata = dict(event.get("metadata") or {})
            if event.get("action"):
                metadata.setdefault("action", event.get("action"))
            if event.get("reason"):
                metadata.setdefault("reason", event.get("reason"))
            try:
                record_review_event(
                    AUDIT_ENTITY_SIMULATION,
                    str(event.get("entity_id") or ""),
                    str(event.get("old_status") or ""),
                    str(event.get("new_status") or ""),
                    event.get("actor_id"),
                    metadata,
                )
            except Exception:  # noqa: BLE001 — 監査は best-effort（record_review_event と同じ）
                logger.warning("simulation audit failed", exc_info=True)
        self.events = []


def _sandbox_client():
    """sim-runner クライアント（テストはこの関数を差し替える）。"""
    return sandbox_client_from_settings(get_settings())


def _user_id(current_user: dict) -> str:
    return str(current_user.get("id") or "")


def _canonical_document_id(session: Any, ref: str) -> str:
    text = str(ref or "").strip()
    if is_document_uuid(text):
        return text
    ids = canonical_document_ids(session, [text])
    if not ids:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return ids[0]


def _state_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc) or "この操作は今の状態ではできません。")


def _validate_model(model: str | None) -> str | None:
    requested = (model or "").strip() or None
    if requested:
        reason = llm_policy.validate_model_for_scene(schema.SCENE_SIMULATION, requested)
        if reason:
            raise HTTPException(status_code=422, detail="このモデルは検証シミュレーションでは選べません。")
    return requested


def _strip_forbidden(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_forbidden(v) for k, v in value.items() if k not in schema.FORBIDDEN_DTO_KEYS}
    if isinstance(value, list):
        return [_strip_forbidden(v) for v in value]
    return value


def _program_dto(program: dict | None) -> dict | None:
    if not program:
        return None
    provenance = dict(program.get("provenance") or {})
    return _strip_forbidden({
        "id": program.get("id"),
        "document_id": program.get("document_id"),
        "module_key": program.get("module_key"),
        "module_stable_key": program.get("module_stable_key"),
        "review_status": program.get("review_status"),
        "source_code": program.get("source_code") or "",
        "io_contract": dict(program.get("io_contract") or {}),
        "provenance": provenance,
        "facts": list(provenance.get("facts") or []),
        "created_by": program.get("created_by"),
        "created_at": program.get("created_at"),
        "decided_at": program.get("decided_at"),
        "superseded_at": program.get("superseded_at"),
    })


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
            "params": dict(part.get("params") or {}),
            "name": entry.get("name") or "",
            "entry_type": entry.get("entry_type") or "",
        })
    return _strip_forbidden({
        "id": wiring.get("id"),
        "document_id": wiring.get("document_id"),
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
    runner = dict(run.get("runner") or {})
    return _strip_forbidden({
        "id": run.get("id"),
        "document_id": run.get("document_id"),
        "program_id": run.get("program_id"),
        "wiring_id": run.get("wiring_id"),
        "status": run.get("status"),
        "observation_claim_ids": list(run.get("observation_claim_ids") or []),
        "inputs": dict(run.get("inputs") or {}),
        "result": dict(run.get("result") or {}),
        "runner": {"status": runner.get("status") or run.get("status"), "error": runner.get("error") or ""},
        "ledger_candidate_id": run.get("ledger_candidate_id"),
        "created_at": run.get("created_at"),
    })


def _str_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v or "").strip()]
    return []


def _module_equation_ids(module: dict, record: dict | None) -> list[str]:
    ids: list[str] = []
    for key in ("inputs", "outputs", "foundation"):
        for item in module.get(key) or []:
            if isinstance(item, dict) and item.get("equation_id"):
                ids.append(str(item["equation_id"]))
    if record:
        for step in record.get("member_steps") or []:
            if isinstance(step, dict):
                ids.extend(_str_list(step.get("input_equation_ids")))
                ids.extend(_str_list(step.get("output_equation_ids")))
        for key in ("input_equation_ids", "output_equation_ids", "foundation_equation_ids", "produced_equation_ids"):
            ids.extend(_str_list(record.get(key)))
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return default


def _saved_module_records(session: Any, document_id: str) -> dict[str, dict]:
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT agent_module_key, stable_key, member_steps, input_equation_ids,
                       output_equation_ids, foundation_equation_ids, produced_equation_ids
                  FROM knowledge_theory_modules_live
                 WHERE document_id = CAST(:d AS uuid)
                """
            ),
            {"d": document_id},
        ).fetchall()
    except Exception:  # noqa: BLE001 — 保存行が引けなくても読み時 DTO だけで続ける
        logger.debug("simulation: saved theory modules unavailable", exc_info=True)
        return {}
    out: dict[str, dict] = {}
    for row in rows or []:
        values = list(row)
        if len(values) < 7 or not values[0]:
            continue
        out[str(values[0])] = {
            "stable_key": str(values[1] or ""),
            "member_steps": _loads(values[2], []),
            "input_equation_ids": _loads(values[3], []),
            "output_equation_ids": _loads(values[4], []),
            "foundation_equation_ids": _loads(values[5], []),
            "produced_equation_ids": _loads(values[6], []),
        }
    return out


_EQUATION_COLUMNS = ("agent_equation_id", "label", "latex", "plain_text", "defined_symbols", "used_symbols", "agent_payload")


def _equation_rows(session: Any, document_id: str, ids: list[str] | None) -> list[dict]:
    sql = (
        "SELECT agent_equation_id, label, latex, plain_text, defined_symbols, used_symbols, agent_payload "
        "FROM knowledge_equations_live WHERE document_id = CAST(:d AS uuid)"
    )
    params: dict[str, Any] = {"d": document_id}
    if ids is not None:
        if not ids:
            return []
        sql += " AND agent_equation_id = ANY(:ids)"
        params["ids"] = list(ids)
    try:
        rows = session.execute(sa_text(sql), params).fetchall()
    except Exception:  # noqa: BLE001
        logger.debug("simulation: equations unavailable", exc_info=True)
        return []
    out = []
    for row in rows or []:
        values = list(row)
        if len(values) < len(_EQUATION_COLUMNS):
            continue
        item = dict(zip(_EQUATION_COLUMNS, values))
        item["defined_symbols"] = _loads(item.get("defined_symbols"), [])
        item["used_symbols"] = _loads(item.get("used_symbols"), [])
        item["agent_payload"] = _loads(item.get("agent_payload"), {})
        out.append(item)
    return out


_SYMBOL_COLUMNS = ("id", "agent_symbol_id", "canonical_symbol", "kind", "unit", "scope", "definition_evidence_texts", "agent_payload")


def _symbol_rows(session: Any, document_id: str, equations: list[dict]) -> list[dict]:
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id::text, agent_symbol_id, canonical_symbol, kind, unit, scope,
                       definition_evidence_texts, agent_payload
                  FROM knowledge_symbols_live
                 WHERE document_id = CAST(:d AS uuid)
                 ORDER BY canonical_symbol
                """
            ),
            {"d": document_id},
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.debug("simulation: symbols unavailable", exc_info=True)
        return []
    symbols = []
    for row in rows or []:
        values = list(row)
        if len(values) < len(_SYMBOL_COLUMNS):
            continue
        item = dict(zip(_SYMBOL_COLUMNS, values))
        item["definition_evidence_texts"] = _loads(item.get("definition_evidence_texts"), [])
        item["agent_payload"] = _loads(item.get("agent_payload"), {})
        symbols.append(item)
    wanted: set[str] = set()
    for eq in equations:
        wanted.update(_str_list(eq.get("defined_symbols")))
        wanted.update(_str_list(eq.get("used_symbols")))
    matched = [s for s in symbols if str(s.get("canonical_symbol") or "") in wanted]
    return (matched or symbols)[:_SYMBOL_LIMIT]


def _has_text(row: dict) -> bool:
    return bool(str(row.get("latex") or "").strip() or str(row.get("plain_text") or "").strip())


def _is_reconstructed(row: dict) -> bool:
    policy = (row.get("agent_payload") or {}).get("confidence_policy") if isinstance(row.get("agent_payload"), dict) else None
    return bool(isinstance(policy, dict) and policy.get("must_not_treat_as_source_extracted"))


def _modules_payload(document_id: str) -> tuple[dict, list[dict]]:
    try:
        dto = build_theory_modules_for_document(document_id) or {}
    except Exception:  # noqa: BLE001 — 導出不能は available:false で正直に縮退
        logger.debug("simulation: theory modules unavailable", exc_info=True)
        dto = {}
    modules = [m for m in (dto.get("modules") or []) if isinstance(m, dict)]
    return dto, modules


def _find_module(modules: list[dict], module_key: str) -> dict | None:
    return next((m for m in modules if str(m.get("module_key") or "") == module_key), None)


def _load_catalog(session: Any) -> tuple[list[dict], dict[str, dict]]:
    rows = parts_catalog.load_parts_catalog(session)
    return rows, parts_catalog.catalog_by_id(rows)


def _require_program(session: Any, program_id: str) -> dict:
    try:
        program = store.get_program(session, program_id)
    except Exception:  # noqa: BLE001 — UUID でない id 等は不在と同じ扱い
        program = None
    if not program:
        raise HTTPException(status_code=404, detail="理論プログラムが見つかりません。")
    return program


def _require_wiring(session: Any, wiring_id: str) -> dict:
    try:
        wiring = store.get_wiring(session, wiring_id)
    except Exception:  # noqa: BLE001
        wiring = None
    if not wiring:
        raise HTTPException(status_code=404, detail="配線が見つかりません。")
    return wiring


def _require_run(session: Any, run_id: str) -> dict:
    try:
        run = store.get_run(session, run_id)
    except Exception:  # noqa: BLE001
        run = None
    if not run:
        raise HTTPException(status_code=404, detail="実行記録が見つかりません。")
    return run


# ---------------------------------------------------------------------------
# 一覧（overview）
# ---------------------------------------------------------------------------


@admin_router.get("/simulation/documents/{document_id}/overview")
def get_simulation_overview(document_id: str, current_user: dict = Depends(_require_teacher)) -> dict:
    """1 画面分（理論モジュール・programs・wirings・runs・観測系 claim・部品目録）。"""
    _ensure_document_viewable(document_id, current_user)
    session = _pg_session()
    try:
        doc = _canonical_document_id(session, document_id)
        dto, modules = _modules_payload(doc)
        facts = [str(f) for f in (dto.get("facts") or []) if str(f or "").strip()]
        records = _saved_module_records(session, doc)
        equations = {str(e.get("agent_equation_id") or ""): e for e in _equation_rows(session, doc, None)}
        programs = store.list_programs(session, document_id=doc)
        latest_by_module: dict[str, dict] = {}
        for program in programs:
            key = str(program.get("module_key") or "")
            if key and key not in latest_by_module:
                latest_by_module[key] = program
        module_items = []
        for module in modules:
            key = str(module.get("module_key") or "")
            ids = _module_equation_ids(module, records.get(key))
            rows = [equations[i] for i in ids if i in equations]
            texted = [r for r in rows if _has_text(r)]
            module_items.append({
                "module_key": key,
                "level": module.get("level"),
                "label": module.get("label") or "",
                "visual_label": module.get("visual_label") or "",
                "theory_object": module.get("theory_object") or "",
                "inputs": [
                    {"equation_id": i.get("equation_id"), "display_label": i.get("display_label")}
                    for i in module.get("inputs") or [] if isinstance(i, dict)
                ],
                "outputs": [
                    {"equation_id": i.get("equation_id"), "display_label": i.get("display_label")}
                    for i in module.get("outputs") or [] if isinstance(i, dict)
                ],
                "assumptions": [
                    {"text": a.get("text") or "", "origin_label": a.get("origin_label") or ""}
                    for a in module.get("assumptions") or [] if isinstance(a, dict)
                ],
                "has_equation_text": bool(texted),
                "reconstructed_inputs": any(_is_reconstructed(r) for r in texted),
                "program": _program_dto(latest_by_module.get(key)),
            })
        catalog_rows, catalog = _load_catalog(session)
        wirings = store.list_wirings(session, document_id=doc)
        runs = store.list_runs(session, document_id=doc)
        try:
            targets = observation_claim_targets(session, document_id=doc)
        except Exception:  # noqa: BLE001
            logger.debug("simulation: observation targets unavailable", exc_info=True)
            targets = []
        if not catalog_rows:
            facts.append(schema.FACT_NO_PARTS_CATALOG)
        client = _sandbox_client()
        return {
            "document_id": doc,
            "available": bool(modules),
            "facts": facts,
            "sandbox_configured": bool(getattr(client, "configured", False)),
            "indicator_id": "simulation-agreement",
            "modules": module_items,
            "programs": [_program_dto(p) for p in programs],
            "wirings": [_wiring_dto(w, catalog) for w in wirings],
            "runs": [_run_dto(r) for r in runs],
            "observation_targets": [
                {
                    "claim_id": t.get("claim_id"),
                    "label": t.get("label") or "",
                    "identified_via": t.get("identified_via") or "",
                }
                for t in targets if isinstance(t, dict)
            ],
            "parts_catalog": catalog_rows,
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 理論プログラム
# ---------------------------------------------------------------------------


@admin_router.post("/simulation/documents/{document_id}/programs/generate")
def generate_simulation_program(
    document_id: str,
    body: GenerateProgramRequest = Body(...),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """理論モジュール 1 つから理論プログラムの候補を作る（LLM 1 コール・候補・SB3）。"""
    _ensure_document_editable(document_id, current_user)
    module_key = (body.module_key or "").strip()
    if not module_key:
        raise HTTPException(status_code=422, detail="理論モジュールを指定してください。")
    requested_model = _validate_model(body.model)
    uid = _user_id(current_user)
    session = _pg_session()
    audit = _AuditBuffer()
    try:
        doc = _canonical_document_id(session, document_id)
        _dto, modules = _modules_payload(doc)
        module = _find_module(modules, module_key)
        if module is None:
            raise HTTPException(status_code=404, detail="この理論モジュールは見つかりません。")
        records = _saved_module_records(session, doc)
        record = records.get(module_key)
        ids = _module_equation_ids(module, record)
        equations = _equation_rows(session, doc, ids)
        if not any(_has_text(row) for row in equations):
            raise HTTPException(status_code=422, detail=schema.FACT_NO_MODULE_EQUATIONS)
        symbols = _symbol_rows(session, doc, equations)

        settings = get_settings()
        consume_daily_quota(
            _codegen_cost_gate,
            user_id=uid,
            limit=int(getattr(settings, "simulation_codegen_max_calls_per_day", 20) or 0),
            message=_QUOTA_MESSAGE,
        )
        with usage_context(feature=schema.FEATURE_CODEGEN, user_id=uid or None, document_id=doc):
            draft = codegen.generate_program(
                module,
                equations=equations,
                symbols=symbols,
                structured_fn=generate_text_with_structured_output,
                text_fn=generate_text,
                model=requested_model,
            )
        if not draft.source_code:
            raise HTTPException(
                status_code=422,
                detail=" ".join(codegen.program_facts_for_display(draft)) or codegen.FACT_GENERATION_FAILED,
            )
        facts = codegen.program_facts_for_display(draft)
        provenance = dict(draft.provenance or {})
        provenance["facts"] = facts
        provenance["ast_ok"] = bool(draft.accepted)
        if draft.ast_check is not None and not draft.accepted:
            provenance["ast_reasons"] = list(draft.ast_check.reasons)
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
                record_audit=audit,
            )
        except store.SimulationStateError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
    finally:
        session.close()
    audit.flush()
    return {"program": _program_dto(program), "facts": facts}


@admin_router.post("/simulation/programs/{program_id}/decide")
def decide_simulation_program(
    program_id: str,
    body: DecideRequest = Body(...),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """理論プログラム候補の確定 / 見送り（人の操作のみ）。AST 検査を通らない候補は確定できない。"""
    session = _pg_session()
    audit = _AuditBuffer()
    try:
        program = _require_program(session, program_id)
        _ensure_document_editable(str(program.get("document_id") or ""), current_user)
        action = (body.action or "").strip()
        if action not in store.DECIDE_ACTIONS:
            raise HTTPException(status_code=422, detail="操作は confirm か dismiss です。")
        if action == "confirm" and not (program.get("provenance") or {}).get("ast_ok"):
            raise HTTPException(
                status_code=422,
                detail="このコードは許可されていない操作を含むため確定できません。コードを生成し直してください。",
            )
        try:
            decided = store.decide_program(
                session,
                program_id=program_id,
                action=action,
                actor_id=_user_id(current_user),
                note=(body.note or "").strip(),
                record_audit=audit,
            )
        except store.SimulationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except store.SimulationStateError as exc:
            session.rollback()
            raise _state_error(exc) from exc
        session.commit()
        refreshed = store.get_program(session, program_id) or decided
    finally:
        session.close()
    audit.flush()
    return {"program": _program_dto(refreshed)}


# ---------------------------------------------------------------------------
# 部品目録・配線
# ---------------------------------------------------------------------------


@admin_router.get("/simulation/parts")
def list_simulation_parts(current_user: dict = Depends(_require_teacher)) -> dict:
    """凍結版の部品目録（装置モデル / ノイズ源 / 信号処理の工夫）。"""
    session = _pg_session()
    try:
        rows, _catalog = _load_catalog(session)
    finally:
        session.close()
    return {"parts": rows, "facts": [] if rows else [schema.FACT_NO_PARTS_CATALOG]}


@admin_router.get("/simulation/documents/{document_id}/wirings")
def list_simulation_wirings(document_id: str, current_user: dict = Depends(_require_teacher)) -> dict:
    _ensure_document_viewable(document_id, current_user)
    session = _pg_session()
    try:
        doc = _canonical_document_id(session, document_id)
        _rows, catalog = _load_catalog(session)
        wirings = store.list_wirings(session, document_id=doc)
    finally:
        session.close()
    return {"wirings": [_wiring_dto(w, catalog) for w in wirings]}


@admin_router.post("/simulation/documents/{document_id}/wirings")
def create_simulation_wiring(
    document_id: str,
    body: CreateWiringRequest = Body(...),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教員が部品目録から配線を組む（``confirmed`` で作られる）。"""
    _ensure_document_editable(document_id, current_user)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="配線の名前を入れてください。")
    parts = [p.model_dump() for p in body.parts]
    edges = [e.model_dump() for e in body.edges]
    session = _pg_session()
    audit = _AuditBuffer()
    try:
        doc = _canonical_document_id(session, document_id)
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
                origin=schema.WIRING_ORIGIN_TEACHER,
                created_by=_user_id(current_user),
                record_audit=audit,
            )
        except store.SimulationStateError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
    finally:
        session.close()
    audit.flush()
    return {"wiring": _wiring_dto(wiring, catalog)}


@admin_router.post("/simulation/wirings/{wiring_id}/decide")
def decide_simulation_wiring(
    wiring_id: str,
    body: DecideRequest = Body(...),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """配線の改訂候補の確定 / 見送り。"""
    session = _pg_session()
    audit = _AuditBuffer()
    try:
        wiring = _require_wiring(session, wiring_id)
        document_id = str(wiring.get("document_id") or "")
        if document_id:
            _ensure_document_editable(document_id, current_user)
        elif not (
            current_user.get("role") == ROLE_SYSTEM_ADMIN
            or str(wiring.get("created_by") or "") == _user_id(current_user)
        ):
            raise HTTPException(status_code=404, detail="配線が見つかりません。")
        action = (body.action or "").strip()
        if action not in store.DECIDE_ACTIONS:
            raise HTTPException(status_code=422, detail="操作は confirm か dismiss です。")
        try:
            store.decide_wiring(
                session,
                wiring_id=wiring_id,
                action=action,
                actor_id=_user_id(current_user),
                note=(body.note or "").strip(),
                record_audit=audit,
            )
        except store.SimulationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except store.SimulationStateError as exc:
            session.rollback()
            raise _state_error(exc) from exc
        session.commit()
        refreshed = store.get_wiring(session, wiring_id)
        _rows, catalog = _load_catalog(session)
    finally:
        session.close()
    audit.flush()
    return {"wiring": _wiring_dto(refreshed, catalog)}


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


def _module_label(document_id: str, module_key: str) -> str:
    _dto, modules = _modules_payload(document_id)
    module = _find_module(modules, module_key)
    return str((module or {}).get("label") or "")


@admin_router.post("/simulation/documents/{document_id}/runs")
def create_simulation_run(
    document_id: str,
    body: RunRequest = Body(...),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """理論プログラム（と任意の配線）を sim-runner で同期実行し、比較して 1 行記録する。"""
    _ensure_document_editable(document_id, current_user)
    uid = _user_id(current_user)
    session = _pg_session()
    audit = _AuditBuffer()
    try:
        doc = _canonical_document_id(session, document_id)
        program = _require_program(session, (body.program_id or "").strip())
        if str(program.get("document_id") or "") != doc:
            raise HTTPException(status_code=404, detail="理論プログラムが見つかりません。")
        if program.get("review_status") != schema.PROGRAM_STATUS_CONFIRMED:
            raise HTTPException(status_code=422, detail="確定した理論プログラムだけを実行できます。")
        wiring = None
        catalog: dict[str, dict] = {}
        wiring_id = (body.wiring_id or "").strip()
        if wiring_id:
            wiring = _require_wiring(session, wiring_id)
            wiring_document = str(wiring.get("document_id") or "")
            if wiring_document and wiring_document != doc:
                raise HTTPException(status_code=404, detail="配線が見つかりません。")
            if wiring.get("review_status") != schema.WIRING_STATUS_CONFIRMED:
                raise HTTPException(status_code=422, detail="確定した配線だけを実行に使えます。")
            _rows, catalog = _load_catalog(session)
        wiring_name = str((wiring or {}).get("name") or "")
        provenance = dict(program.get("provenance") or {})
        reconstructed = bool(provenance.get("reconstructed_inputs"))
        inputs = {
            "values": dict(body.values),
            "params": dict(body.params),
            "instrument_params": {k: dict(v) for k, v in body.instrument_params.items()},
            "reference": body.reference,
            "seed": body.seed,
        }
        claim_ids = [str(c).strip() for c in body.observation_claim_ids if str(c or "").strip()]

        def _record(status: str, result: dict, runner: dict) -> dict:
            return store.record_run(
                session,
                document_id=doc,
                program_id=str(program.get("id")),
                wiring_id=wiring_id or None,
                observation_claim_ids=claim_ids,
                inputs=inputs,
                result=result,
                runner=runner,
                status=status,
                created_by=uid,
                record_audit=audit,
            )

        source = str(program.get("source_code") or "")
        check = ast_guard.check_program(source)
        if not (check.ok and check.entrypoint_found):
            run = _record(
                schema.RUN_STATUS_REJECTED,
                {"facts": [schema.FACT_AST_REJECTED], "reconstructed_inputs": reconstructed},
                {"status": schema.RUN_STATUS_REJECTED, "ast_check": list(check.reasons), "error": ""},
            )
            session.commit()
        else:
            instrument_source = ""
            instrument_param_values: dict[str, Any] = {}
            if wiring is not None:
                parts = list(wiring.get("parts") or [])
                edges = list(wiring.get("edges") or [])
                try:
                    instrument_source = instrument.compose_instrument(parts, edges, catalog)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                merged = instrument.default_params(parts, catalog)
                for slot, values in (body.instrument_params or {}).items():
                    merged.setdefault(str(slot), {}).update({str(k): float(v) for k, v in values.items()})
                instrument_param_values = merged

            client = _sandbox_client()
            theory = client.run(
                source,
                entrypoint=schema.PROGRAM_ENTRYPOINT,
                inputs=dict(body.values),
                params=dict(body.params),
                seed=body.seed,
            )
            if theory.status != schema.RUN_STATUS_SUCCEEDED:
                run = _record(
                    schema.RUN_STATUS_FAILED,
                    {"facts": list(theory.facts or []), "reconstructed_inputs": reconstructed},
                    {"status": schema.RUN_STATUS_FAILED, "error": theory.error, "stage": "theory"},
                )
            else:
                theory_outputs = dict(theory.outputs or {})
                observed: dict | None = None
                failed = None
                if wiring is not None:
                    observed_run = client.run(
                        instrument_source,
                        entrypoint=schema.INSTRUMENT_ENTRYPOINT,
                        inputs=theory_outputs,
                        params=instrument_param_values,
                        seed=body.seed,
                    )
                    if observed_run.status != schema.RUN_STATUS_SUCCEEDED:
                        failed = observed_run
                    else:
                        raw = observed_run.outputs or {}
                        observed = raw.get("observed") if isinstance(raw.get("observed"), dict) else {}
                if failed is not None:
                    run = _record(
                        schema.RUN_STATUS_FAILED,
                        {
                            "theory_outputs": theory_outputs,
                            "facts": list(failed.facts or []),
                            "reconstructed_inputs": reconstructed,
                        },
                        {"status": schema.RUN_STATUS_FAILED, "error": failed.error, "stage": "instrument"},
                    )
                else:
                    comparison = comparator.compare(
                        theory_outputs, observed, reference=body.reference, wiring_name=wiring_name
                    )
                    statement = comparator.comparison_statement(
                        comparison,
                        module_label=_module_label(doc, str(program.get("module_key") or "")),
                        wiring_name=wiring_name,
                    )
                    facts = list(comparison.facts)
                    if reconstructed:
                        facts.append(schema.FACT_RECONSTRUCTED_INPUTS)
                    result = {
                        "theory_outputs": theory_outputs,
                        "observed": observed,
                        "comparison": comparison.to_dict(),
                        "labels": {
                            "agreement": SIMULATION_AGREEMENT_LABELS[comparison.agreement_level],
                            "separation": SIMULATION_SEPARATION_LABELS[comparison.separation_level],
                            "reference": SIMULATION_AGREEMENT_LABELS[comparison.reference_level],
                        },
                        "statement": statement,
                        "facts": facts,
                        "reconstructed_inputs": reconstructed,
                        "wiring_name": wiring_name,
                    }
                    run = _record(
                        schema.RUN_STATUS_SUCCEEDED,
                        result,
                        {"status": schema.RUN_STATUS_SUCCEEDED, "error": ""},
                    )
            session.commit()
    finally:
        session.close()
    audit.flush()
    return {"run": _run_dto(run)}


@admin_router.get("/simulation/documents/{document_id}/runs")
def list_simulation_runs(document_id: str, current_user: dict = Depends(_require_teacher)) -> dict:
    """実行記録の一覧（教員向け計器 ``simulation-agreement`` の数値を含む）。"""
    _ensure_document_viewable(document_id, current_user)
    session = _pg_session()
    try:
        doc = _canonical_document_id(session, document_id)
        runs = store.list_runs(session, document_id=doc)
    finally:
        session.close()
    return {"indicator_id": "simulation-agreement", "runs": [_run_dto(r) for r in runs]}


@admin_router.get("/simulation/runs/{run_id}")
def get_simulation_run(run_id: str, current_user: dict = Depends(_require_teacher)) -> dict:
    session = _pg_session()
    try:
        run = _require_run(session, run_id)
    finally:
        session.close()
    _ensure_document_viewable(str(run.get("document_id") or ""), current_user)
    return {"indicator_id": "simulation-agreement", "run": _run_dto(run)}


# ---------------------------------------------------------------------------
# 台帳への候補・改訂案
# ---------------------------------------------------------------------------


@admin_router.post("/simulation/runs/{run_id}/ledger-candidate")
def add_simulation_ledger_candidate(
    run_id: str,
    body: LedgerCandidateRequest = Body(default=None),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """成功した run の比較結果を、観測系 claim ごとに台帳の候補として 1 件ずつ追記する（SB1）。"""
    uid = _user_id(current_user)
    session = _pg_session()
    audit = _AuditBuffer()
    try:
        run = _require_run(session, run_id)
        _ensure_document_editable(str(run.get("document_id") or ""), current_user)
        if run.get("status") != schema.RUN_STATUS_SUCCEEDED:
            raise HTTPException(status_code=422, detail="成功した実行だけを台帳の候補にできます。")
        requested = (body.claim_ids if body is not None and body.claim_ids is not None else None)
        claim_ids = [str(c).strip() for c in (requested if requested is not None else run.get("observation_claim_ids") or []) if str(c or "").strip()]
        if not claim_ids:
            raise HTTPException(status_code=422, detail="台帳に記帳する観測系の主張を選んでください。")
        result = dict(run.get("result") or {})
        comparison = dict(result.get("comparison") or {})
        statement = str(result.get("statement") or "")
        level = str(comparison.get("agreement_level") or schema.AGREEMENT_LEVEL_UNMEASURED)
        candidates = []
        try:
            for claim_id in claim_ids:
                candidate = store.build_ledger_candidate(
                    run=run,
                    statement=statement,
                    level=level,
                    facts=list(comparison.get("facts") or []),
                    reconstructed_inputs=bool(result.get("reconstructed_inputs")),
                    created_by=uid,
                    wiring_name=str(result.get("wiring_name") or ""),
                )
                candidates.append(
                    store.append_ledger_candidate(
                        session,
                        target_type="claim",
                        target_id=claim_id,
                        candidate=candidate,
                        record_audit=audit,
                        actor_id=uid,
                    )
                )
            store.set_run_ledger_candidate(session, run_id=run_id, candidate_id=str(candidates[0]["candidate_id"]))
        except store.SimulationStateError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
    finally:
        session.close()
    audit.flush()
    return {"candidates": [_strip_forbidden(c) for c in candidates]}


@admin_router.post("/simulation/runs/{run_id}/propose-revision")
def propose_simulation_revision(
    run_id: str,
    body: ProposeRevisionRequest = Body(default=None),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """配線の改訂案を 1 回だけ求める（部品の組み替えのみ・候補・SB5）。"""
    uid = _user_id(current_user)
    requested_model = _validate_model(body.model if body is not None else None)
    session = _pg_session()
    audit = _AuditBuffer()
    try:
        run = _require_run(session, run_id)
        _ensure_document_editable(str(run.get("document_id") or ""), current_user)
        wiring_id = str(run.get("wiring_id") or "")
        comparison = dict((run.get("result") or {}).get("comparison") or {})
        if not wiring_id or not comparison:
            raise HTTPException(status_code=422, detail="配線を使って比較まで終わった実行だけに改訂案を求められます。")
        parent = _require_wiring(session, wiring_id)
        _rows, catalog = _load_catalog(session)
        settings = get_settings()
        consume_daily_quota(
            _revision_cost_gate,
            user_id=uid,
            limit=int(getattr(settings, "simulation_revision_max_calls_per_day", 20) or 0),
            message=_QUOTA_MESSAGE,
        )
        with usage_context(
            feature=schema.FEATURE_REVISION, user_id=uid or None, document_id=str(run.get("document_id") or "") or None
        ):
            proposal = revision.propose_revision(
                parent,
                comparison,
                catalog,
                structured_fn=generate_text_with_structured_output,
                text_fn=generate_text,
                model=requested_model,
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
                record_audit=audit,
            )
        except store.SimulationStateError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
    finally:
        session.close()
    audit.flush()
    return {"wiring": _wiring_dto(wiring, catalog), "proposal": proposal_dto}


__all__ = ["admin_router"]
