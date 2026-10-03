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
- v1.1（§14）: 学習者が作って試す経路は ``routes/simulation_lab.py``（学習者向けルーター）。
  本ルーターが一覧するのは**教員本人の行 + 所有者の無い v1 の旧行 + 学習者が本人の操作で
  共有した run**（``shared: true``）だけで、学習者の program / wiring / 共有していない run は
  見えない（SB12）。共有した学習者は表示名だけで示し、user id は出さない。
- 手順（式と記号の引き出し・AST 検査 → sim-runner → 比較・台帳候補）は
  ``core/simulation_bridge/service.py`` が正本で、学習者向けルーターと共有する。
- confidence はどの DTO にも載せない。
"""

from __future__ import annotations

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
from core.llm import generate_text, generate_text_with_structured_output
from core.llm_usage.context import usage_context
from core.llm_worker.cost_gate import CostGate
from core.postgres import get_session as _pg_session
from core.schema import AUDIT_ENTITY_SIMULATION
from core.simulation_bridge import catalog as parts_catalog, codegen
from core.simulation_bridge import instrument, revision, schema, service, store
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
    values: dict[str, float | list[float]] = Field(default_factory=dict)  # ベクトル入力可（波数など）
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


def _run_dto(run: dict | None, *, viewer_id: str = "", shared_by: dict[str, str] | None = None) -> dict | None:
    if not run:
        return None
    runner = dict(run.get("runner") or {})
    owner = str(run.get("owner_user_id") or "")
    shared = bool(run.get("shared_at"))
    extra: dict[str, Any] = {"shared": shared}
    if shared and owner and owner != viewer_id:
        # 学習者が共有した run（SB13）。表示名だけを出し、user id は出さない。
        extra["shared_by_label"] = (shared_by or {}).get(owner) or _SHARED_BY_UNKNOWN
    return _strip_forbidden({
        **extra,
        "id": run.get("id"),
        "document_id": run.get("document_id"),
        "program_id": run.get("program_id"),
        "wiring_id": run.get("wiring_id"),
        "status": run.get("status"),
        "observation_claim_ids": list(run.get("observation_claim_ids") or []),
        # 学習者が実行前に書いた予想（SB14: 並べて見せるだけ・当否は付けない。第 21 周 (g) の教員画面の列）
        "prediction": [dict(x) for x in (run.get("prediction") or []) if isinstance(x, dict)],
        "inputs": dict(run.get("inputs") or {}),
        "result": dict(run.get("result") or {}),
        "runner": {"status": runner.get("status") or run.get("status"), "error": runner.get("error") or ""},
        "ledger_candidate_id": run.get("ledger_candidate_id"),
        "created_at": run.get("created_at"),
    })


def _saved_module_records(session: Any, document_id: str) -> dict[str, dict]:
    """``service.saved_module_records`` の薄い委譲（テストの差し替え口）。"""
    return service.saved_module_records(session, document_id)


def _equation_rows(session: Any, document_id: str, ids: list[str] | None) -> list[dict]:
    return service.equation_rows(session, document_id, ids)


def _symbol_rows(session: Any, document_id: str, equations: list[dict]) -> list[dict]:
    return service.symbol_rows(session, document_id, equations)


def _user_display_names(session: Any, user_ids: list[str]) -> dict[str, str]:
    """共有した人の表示名（user id は DTO に出さない）。引けなければ空。"""
    ids = sorted({str(u) for u in user_ids if str(u or "").strip()})
    if not ids:
        return {}
    try:
        rows = session.execute(
            sa_text("SELECT id::text, display_name FROM users WHERE id::text = ANY(:ids)"),
            {"ids": ids},
        ).fetchall()
    except Exception:  # noqa: BLE001 — 表示名が引けなくても一覧は返す
        logger.debug("simulation: user display names unavailable", exc_info=True)
        service.rollback_quietly(session)
        return {}
    return {str(r[0]): str(r[1] or "") for r in rows or [] if r and r[0]}


#: 共有した人の表示名が引けないときのラベル。
_SHARED_BY_UNKNOWN = "学習者"


def _visible_to_teacher(row: dict | None, user_id: str, *, allow_shared: bool = False) -> bool:
    """教員の画面で扱える行か（本人の行・所有者の無い v1 の旧行・共有された run = SB12 / SB13）。"""
    if not row:
        return False
    owner = str(row.get("owner_user_id") or "")
    if not owner or owner == user_id:
        return True
    return bool(allow_shared and row.get("shared_at"))


def _modules_payload(document_id: str) -> tuple[dict, list[dict]]:
    try:
        dto = build_theory_modules_for_document(document_id) or {}
    except Exception:  # noqa: BLE001 — 導出不能は available:false で正直に縮退
        logger.debug("simulation: theory modules unavailable", exc_info=True)
        dto = {}
    modules = [m for m in (dto.get("modules") or []) if isinstance(m, dict)]
    return dto, modules


def _find_module(modules: list[dict], module_key: str) -> dict | None:
    return service.find_module(modules, module_key)


def _load_catalog(session: Any) -> tuple[list[dict], dict[str, dict]]:
    rows = parts_catalog.load_parts_catalog(session)
    return rows, parts_catalog.catalog_by_id(rows)


def _require_program(session: Any, program_id: str) -> dict:
    try:
        program = store.get_program(session, program_id)
    except Exception:  # noqa: BLE001 — UUID でない id 等は不在と同じ扱い
        service.rollback_quietly(session)
        program = None
    if not program:
        raise HTTPException(status_code=404, detail="理論プログラムが見つかりません。")
    return program


def _require_wiring(session: Any, wiring_id: str) -> dict:
    try:
        wiring = store.get_wiring(session, wiring_id)
    except Exception:  # noqa: BLE001
        service.rollback_quietly(session)
        wiring = None
    if not wiring:
        raise HTTPException(status_code=404, detail="配線が見つかりません。")
    return wiring


def _require_run(session: Any, run_id: str) -> dict:
    try:
        run = store.get_run(session, run_id)
    except Exception:  # noqa: BLE001
        service.rollback_quietly(session)
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
        uid = _user_id(current_user)
        programs = store.list_programs(session, document_id=doc, owner_user_id=uid, include_unowned=True)
        module_items = service.module_items(
            modules,
            records=records,
            equations_by_id=equations,
            latest_program_by_module=service.latest_by_module(programs),
            program_dto=_program_dto,
        )
        catalog_rows, catalog = _load_catalog(session)
        wirings = store.list_wirings(session, document_id=doc, owner_user_id=uid, include_unowned=True)
        runs = store.list_runs(
            session, document_id=doc, owner_user_id=uid, include_unowned=True, include_shared=True
        )
        shared_by = _user_display_names(
            session, [str(r.get("owner_user_id") or "") for r in runs if r.get("shared_at")]
        )
        try:
            targets = observation_claim_targets(session, document_id=doc)
        except Exception:  # noqa: BLE001
            logger.debug("simulation: observation targets unavailable", exc_info=True)
            service.rollback_quietly(session)
            targets = []
        # 第 21 周 R16: 本文の読めない・内部 ID 形のラベルは候補から外す（学習者側と同じ 1 関数）。
        targets = service.readable_observation_targets(targets)
        if not catalog_rows:
            facts.append(schema.FACT_NO_PARTS_CATALOG)
        if not targets:
            facts.append(schema.FACT_NO_OBSERVATION_TARGETS)
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
            "runs": [_run_dto(r, viewer_id=uid, shared_by=shared_by) for r in runs],
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
        record, equations, symbols = service.fetch_module_inputs(
            session,
            doc,
            module,
            saved_records=_saved_module_records,
            equations_fn=_equation_rows,
            symbols_fn=_symbol_rows,
        )
        if not any(service.has_text(row) for row in equations):
            raise HTTPException(status_code=422, detail=schema.FACT_NO_MODULE_EQUATIONS)

        settings = get_settings()
        consume_daily_quota(
            _codegen_cost_gate,
            user_id=uid,
            limit=int(getattr(settings, "simulation_codegen_max_calls_per_day", 20) or 0),
            message=_QUOTA_MESSAGE,
        )
        with usage_context(feature=schema.FEATURE_CODEGEN, user_id=uid or None, document_id=doc):
            draft, facts, provenance = service.generate_draft(
                module,
                equations=equations,
                symbols=symbols,
                structured_fn=generate_text_with_structured_output,
                text_fn=generate_text,
                model=requested_model,
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
                record_audit=audit,
                owner_user_id=uid or None,
                supersede_unowned=True,
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
        if not _visible_to_teacher(program, _user_id(current_user)):
            raise HTTPException(status_code=404, detail="理論プログラムが見つかりません。")
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
        wirings = store.list_wirings(
            session, document_id=doc, owner_user_id=_user_id(current_user), include_unowned=True
        )
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
                owner_user_id=_user_id(current_user) or None,
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
        if not _visible_to_teacher(wiring, _user_id(current_user)):
            raise HTTPException(status_code=404, detail="配線が見つかりません。")
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
        if str(program.get("document_id") or "") != doc or not _visible_to_teacher(program, uid):
            raise HTTPException(status_code=404, detail="理論プログラムが見つかりません。")
        if program.get("review_status") != schema.PROGRAM_STATUS_CONFIRMED:
            raise HTTPException(status_code=422, detail="確定した理論プログラムだけを実行できます。")
        wiring = None
        catalog: dict[str, dict] = {}
        wiring_id = (body.wiring_id or "").strip()
        if wiring_id:
            wiring = _require_wiring(session, wiring_id)
            wiring_document = str(wiring.get("document_id") or "")
            if (wiring_document and wiring_document != doc) or not _visible_to_teacher(wiring, uid):
                raise HTTPException(status_code=404, detail="配線が見つかりません。")
            if wiring.get("review_status") != schema.WIRING_STATUS_CONFIRMED:
                raise HTTPException(status_code=422, detail="確定した配線だけを実行に使えます。")
            _rows, catalog = _load_catalog(session)
        inputs = {
            "values": dict(body.values),
            "params": dict(body.params),
            "instrument_params": {k: dict(v) for k, v in body.instrument_params.items()},
            "reference": body.reference,
            "seed": body.seed,
        }
        claim_ids = [str(c).strip() for c in body.observation_claim_ids if str(c or "").strip()]
        try:
            status, result, runner = service.run_pipeline(
                program=program,
                wiring=wiring,
                catalog=catalog,
                values=body.values,
                params=body.params,
                instrument_params=body.instrument_params,
                reference=body.reference,
                seed=body.seed,
                sandbox_client=_sandbox_client(),
                module_label=_module_label(doc, str(program.get("module_key") or "")),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        run = store.record_run(
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
            owner_user_id=uid or None,
        )
        session.commit()
    finally:
        session.close()
    audit.flush()
    return {"run": _run_dto(run, viewer_id=uid)}


@admin_router.get("/simulation/documents/{document_id}/runs")
def list_simulation_runs(document_id: str, current_user: dict = Depends(_require_teacher)) -> dict:
    """実行記録の一覧（教員向け計器 ``simulation-agreement`` の数値を含む）。"""
    _ensure_document_viewable(document_id, current_user)
    session = _pg_session()
    try:
        doc = _canonical_document_id(session, document_id)
        uid = _user_id(current_user)
        runs = store.list_runs(
            session, document_id=doc, owner_user_id=uid, include_unowned=True, include_shared=True
        )
        shared_by = _user_display_names(
            session, [str(r.get("owner_user_id") or "") for r in runs if r.get("shared_at")]
        )
    finally:
        session.close()
    return {
        "indicator_id": "simulation-agreement",
        "runs": [_run_dto(r, viewer_id=uid, shared_by=shared_by) for r in runs],
    }


@admin_router.get("/simulation/runs/{run_id}")
def get_simulation_run(run_id: str, current_user: dict = Depends(_require_teacher)) -> dict:
    uid = _user_id(current_user)
    session = _pg_session()
    try:
        run = _require_run(session, run_id)
        if not _visible_to_teacher(run, uid, allow_shared=True):
            raise HTTPException(status_code=404, detail="実行記録が見つかりません。")
        shared_by = _user_display_names(session, [str(run.get("owner_user_id") or "")]) if run.get("shared_at") else {}
    finally:
        session.close()
    _ensure_document_viewable(str(run.get("document_id") or ""), current_user)
    return {"indicator_id": "simulation-agreement", "run": _run_dto(run, viewer_id=uid, shared_by=shared_by)}


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
        if not _visible_to_teacher(run, uid, allow_shared=True):
            raise HTTPException(status_code=404, detail="実行記録が見つかりません。")
        _ensure_document_editable(str(run.get("document_id") or ""), current_user)
        if run.get("status") != schema.RUN_STATUS_SUCCEEDED:
            raise HTTPException(status_code=422, detail="成功した実行だけを台帳の候補にできます。")
        requested = (body.claim_ids if body is not None and body.claim_ids is not None else None)
        claim_ids = [str(c).strip() for c in (requested if requested is not None else run.get("observation_claim_ids") or []) if str(c or "").strip()]
        if not claim_ids:
            raise HTTPException(status_code=422, detail="台帳に記帳する観測系の主張を選んでください。")
        try:
            candidates = service.ledger_candidates_for_run(
                session,
                run=run,
                claim_ids=claim_ids,
                created_by=uid,
                store_module=store,
                record_audit=audit,
            )
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
        if not _visible_to_teacher(run, uid):
            raise HTTPException(status_code=404, detail="実行記録が見つかりません。")
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
                owner_user_id=uid or None,
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
