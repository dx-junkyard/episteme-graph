"""検証シミュレーション層（SB層）— programs / wirings / runs と台帳候補の DB プリミティブ。

正本: ``docs/features/simulation_bridge_design.md`` §4（データモデル）/ §2（SB1〜SB10）。
DDL の正本は ``backend/db/088_simulation_bridge.sql``。語彙は ``core/simulation_bridge/schema.py``。

``core/atlas_edges/store.py`` と同じ流儀:

- セッションは **呼び出し側が管理する**（``core/postgres.get_session()`` + try/finally）。
  本モジュールは commit / rollback / close を行わない。
- 第1引数は必ず ``session``、それ以外はキーワード引数。SQL は ``sqlalchemy.text`` で書き、
  ORM を使わない。FastAPI / services / LLM SDK に依存しない（開発ルール2）。
- 戻り値は素の dict（uuid 列は ``::text`` で文字列にする）。

不変条項:

- **SB7 情報を落とさない**: 本モジュールに DELETE 文は無い。programs / wirings は状態遷移
  （再生成は ``superseded``）、runs は append-only（``ledger_candidate_id`` の刻印だけが
  後から書かれる）。
- **SB3 / KN-3 確定は人間**: programs / wirings の confirm / dismiss は
  :class:`core.candidate_flow.CandidateFlow` を通す（遷移の可否・帰属の検査を再実装しない）。
  ``origin='llm_candidate'`` の配線を ``confirmed`` で作る経路は無い。
- **SB1 一致は検証ではない**: 台帳候補を確定しても ``verification_status`` は
  ``untested`` / ``unknown`` からだけ ``indirectly_supported`` に上がり、それ以上には
  上げない（``directly_verified`` へは決して上げない）。
- **SB6 confidence を持たない**: 候補 dict・行 dict のどこにも confidence を載せない。
- 監査は注入された ``record_audit`` callable だけを通す（entity_type は
  ``core.schema.AUDIT_ENTITY_SIMULATION`` 固定。action は ``schema.AUDIT_ACTIONS``）。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import text as sa_text

from core.candidate_flow import (
    ACTION_CONFIRM,
    ACTION_DISMISS,
    CandidateFlow,
    CandidateTransitionError,
    CandidateVocabulary,
)
from core.doubt.schema import VerificationScope
from core.schema import AUDIT_ENTITY_SIMULATION
from core.simulation_bridge import schema

logger = logging.getLogger(__name__)

RecordAudit = Callable[..., Any]

# ---------------------------------------------------------------------------
# 例外（API 層が 404 / 409・422 に写す。detail は日本語の事実文）
# ---------------------------------------------------------------------------


class SimulationNotFoundError(LookupError):
    """対象の行（program / wiring / run / 台帳行・候補）が無い。"""


class SimulationStateError(ValueError):
    """状態として許されない操作（判断済みへの再判断・楽観的競合・語彙外）。"""


# ---------------------------------------------------------------------------
# 語彙（CandidateFlow の宣言）
# ---------------------------------------------------------------------------

PROGRAM_VOCABULARY = CandidateVocabulary(
    candidate=schema.PROGRAM_STATUS_CANDIDATE,
    accepted=schema.PROGRAM_STATUS_CONFIRMED,
    dismissed=schema.PROGRAM_STATUS_DISMISSED,
    superseded=schema.PROGRAM_STATUS_SUPERSEDED,
)

WIRING_VOCABULARY = CandidateVocabulary(
    candidate=schema.WIRING_STATUS_CANDIDATE,
    accepted=schema.WIRING_STATUS_CONFIRMED,
    dismissed=schema.WIRING_STATUS_DISMISSED,
)

#: 教員が送ってよい判断（restore は v1 では提供しない — 設計書 §6）。
DECIDE_ACTIONS: tuple[str, ...] = (ACTION_CONFIRM, ACTION_DISMISS)

_PROGRAM_AUDIT_ACTIONS = {ACTION_CONFIRM: "program_confirmed", ACTION_DISMISS: "program_dismissed"}
_WIRING_AUDIT_ACTIONS = {ACTION_CONFIRM: "wiring_confirmed", ACTION_DISMISS: "wiring_dismissed"}

#: 台帳の target_type（migration 029 の CHECK と同じ）。
LEDGER_TARGET_TYPES: tuple[str, ...] = ("claim", "assumption", "equation", "component")

#: 配線なしで確定したときのスコープの ``system`` 軸。
NO_WIRING_SYSTEM_LABEL = "配線なし"
#: スコープの ``condition`` 軸の上限文字数。
_CONDITION_MAX_CHARS = 300

# ---------------------------------------------------------------------------
# 列と行変換
# ---------------------------------------------------------------------------

_PROGRAM_COLUMNS = (
    "id", "document_id", "module_stable_key", "module_key", "language", "source_code",
    "io_contract", "provenance", "review_status", "review_note", "decided_by", "decided_at",
    "created_by", "created_at", "updated_at", "superseded_at", "superseded_by_id",
)
_PROGRAM_SELECT = """
    id::text, document_id::text, module_stable_key, module_key, language, source_code,
    io_contract, provenance, review_status, review_note, decided_by, decided_at,
    created_by, created_at, updated_at, superseded_at, superseded_by_id::text
"""

_WIRING_COLUMNS = (
    "id", "document_id", "name", "parts", "edges", "origin", "parent_wiring_id",
    "revision_note", "review_status", "review_note", "decided_by", "decided_at",
    "created_by", "created_at", "updated_at",
)
_WIRING_SELECT = """
    id::text, document_id::text, name, parts, edges, origin, parent_wiring_id::text,
    revision_note, review_status, review_note, decided_by, decided_at,
    created_by, created_at, updated_at
"""

_RUN_COLUMNS = (
    "id", "document_id", "program_id", "wiring_id", "observation_claim_ids", "inputs",
    "result", "runner", "status", "ledger_candidate_id", "created_by", "created_at",
)
_RUN_SELECT = """
    id::text, document_id::text, program_id::text, wiring_id::text, observation_claim_ids,
    inputs, result, runner, status, ledger_candidate_id, created_by, created_at
"""

_JSON_DICT_KEYS = frozenset({"io_contract", "provenance", "inputs", "result", "runner"})
_JSON_LIST_KEYS = frozenset({"parts", "edges", "observation_claim_ids"})
_TIME_KEYS = frozenset({"decided_at", "created_at", "updated_at", "superseded_at"})


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return default
        return parsed if isinstance(parsed, type(default)) else default
    return default


def _row_values(row: Any, columns: Sequence[str]) -> dict:
    if isinstance(row, Mapping):
        return {name: row.get(name) for name in columns}
    values = list(row)
    return {name: values[i] if i < len(values) else None for i, name in enumerate(columns)}


def _row_to_dict(row: Any, columns: Sequence[str]) -> dict:
    record = _row_values(row, columns)
    out: dict[str, Any] = {}
    for key, value in record.items():
        if key in _JSON_DICT_KEYS:
            out[key] = _loads(value, {})
        elif key in _JSON_LIST_KEYS:
            out[key] = _loads(value, [])
        elif key in _TIME_KEYS:
            out[key] = _iso(value)
        elif key in ("id", "document_id", "program_id", "wiring_id", "parent_wiring_id", "superseded_by_id"):
            out[key] = str(value) if value else None
        elif key == "decided_by":
            out[key] = str(value) if value else None
        else:
            out[key] = value if value is not None else ""
    return out


def _require_actor(actor_id: Any) -> str:
    actor = _clean(actor_id)
    if not actor:
        raise SimulationStateError("操作した人が特定できません（確定は人間の操作のみ）。")
    return actor


def _audit(
    record_audit: RecordAudit | None,
    *,
    entity_id: str,
    action: str,
    old_status: str | None = None,
    new_status: str | None = None,
    actor_id: str | None = None,
    reason: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """監査記帳（注入された callable のみ。例外は握らない = 監査必須）。"""
    if record_audit is None:
        return
    if action not in schema.AUDIT_ACTIONS:
        raise SimulationStateError(f"監査の操作語彙にない値です: {action}")
    record_audit(
        entity_type=AUDIT_ENTITY_SIMULATION,
        entity_id=entity_id,
        action=action,
        old_status=old_status,
        new_status=new_status,
        actor_id=actor_id,
        reason=reason,
        metadata=dict(metadata or {}),
    )


def _translated_audit(record_audit: RecordAudit, table: Mapping[str, str]) -> RecordAudit:
    """CandidateFlow の action（confirm / dismiss）を本層の監査語彙へ写す。"""

    def _record(**kwargs: Any) -> Any:
        action = table.get(kwargs.get("action"), kwargs.get("action"))
        kwargs["action"] = action
        kwargs["entity_type"] = AUDIT_ENTITY_SIMULATION
        return record_audit(**kwargs)

    return _record


# ---------------------------------------------------------------------------
# simulation_programs
# ---------------------------------------------------------------------------


def create_program(
    session: Any,
    *,
    document_id: str,
    module_key: str,
    module_stable_key: str = "",
    source_code: str,
    io_contract: Mapping[str, Any] | None,
    provenance: Mapping[str, Any] | None,
    created_by: str,
    review_status: str = schema.PROGRAM_STATUS_CANDIDATE,
    record_audit: RecordAudit | None = None,
) -> dict:
    """理論プログラムの新しい版を挿入し、同じ (document, module) の旧版を superseded にする。

    旧版（``candidate`` / ``confirmed``）は削除せず ``superseded`` + ``superseded_at`` +
    ``superseded_by_id``（新行）で残す（SB7）。新行を先に挿入するのは、``superseded_by_id``
    の FK が新行の実在を要求するため。戻り値の ``superseded_ids`` に置き換えた旧行を列挙する。

    ``record_audit`` を渡すと ``program_generated`` と旧行ごとの ``program_superseded`` を記帳する。
    """
    doc = _clean(document_id)
    key = _clean(module_key)
    if not doc:
        raise SimulationStateError("教材が指定されていません。")
    if not key:
        raise SimulationStateError("理論モジュールが指定されていません。")
    status = _clean(review_status) or schema.PROGRAM_STATUS_CANDIDATE
    if status not in (schema.PROGRAM_STATUS_CANDIDATE, schema.PROGRAM_STATUS_CONFIRMED):
        raise SimulationStateError(f"理論プログラムをこの状態では作れません: {status}")

    row = session.execute(
        sa_text(
            f"""
            INSERT INTO simulation_programs
                (document_id, module_stable_key, module_key, language, source_code,
                 io_contract, provenance, review_status, created_by)
            VALUES
                (CAST(:document_id AS uuid), :module_stable_key, :module_key, :language,
                 :source_code, CAST(:io_contract AS jsonb), CAST(:provenance AS jsonb),
                 :review_status, :created_by)
            RETURNING {_PROGRAM_SELECT}
            """
        ),
        {
            "document_id": doc,
            "module_stable_key": _clean(module_stable_key),
            "module_key": key,
            "language": schema.PROGRAM_LANGUAGE,
            "source_code": str(source_code or ""),
            "io_contract": _dumps(dict(io_contract or {})),
            "provenance": _dumps(dict(provenance or {})),
            "review_status": status,
            "created_by": _clean(created_by),
        },
    ).fetchone()
    if row is None:
        raise SimulationStateError("理論プログラムを保存できませんでした。")
    program = _row_to_dict(row, _PROGRAM_COLUMNS)
    new_id = program["id"]

    superseded_rows = session.execute(
        sa_text(
            """
            UPDATE simulation_programs
               SET review_status = :superseded,
                   superseded_at = now(),
                   superseded_by_id = CAST(:new_id AS uuid),
                   updated_at = now()
             WHERE document_id = CAST(:document_id AS uuid)
               AND module_key = :module_key
               AND review_status IN (:candidate, :confirmed)
               AND id <> CAST(:new_id AS uuid)
            RETURNING id::text, review_status
            """
        ),
        {
            "superseded": schema.PROGRAM_STATUS_SUPERSEDED,
            "new_id": new_id,
            "document_id": doc,
            "module_key": key,
            "candidate": schema.PROGRAM_STATUS_CANDIDATE,
            "confirmed": schema.PROGRAM_STATUS_CONFIRMED,
        },
    ).fetchall()
    superseded_ids = [str(r[0]) for r in (superseded_rows or []) if r and r[0]]
    program["superseded_ids"] = superseded_ids

    _audit(
        record_audit,
        entity_id=new_id,
        action="program_generated",
        new_status=status,
        actor_id=_clean(created_by) or None,
        metadata={"document_id": doc, "module_key": key},
    )
    for old_id in superseded_ids:
        _audit(
            record_audit,
            entity_id=old_id,
            action="program_superseded",
            new_status=schema.PROGRAM_STATUS_SUPERSEDED,
            actor_id=_clean(created_by) or None,
            metadata={"superseded_by_id": new_id},
        )
    return program


def list_programs(session: Any, *, document_id: str, include_superseded: bool = False) -> list[dict]:
    """教材の理論プログラム（新しい順）。既定では superseded を除く。"""
    doc = _clean(document_id)
    if not doc:
        return []
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_PROGRAM_SELECT} FROM simulation_programs
             WHERE document_id = CAST(:document_id AS uuid)
               AND (:include_superseded OR review_status <> :superseded)
             ORDER BY created_at DESC
            """
        ),
        {
            "document_id": doc,
            "include_superseded": bool(include_superseded),
            "superseded": schema.PROGRAM_STATUS_SUPERSEDED,
        },
    ).fetchall()
    return [_row_to_dict(r, _PROGRAM_COLUMNS) for r in (rows or [])]


def get_program(session: Any, program_id: str) -> dict | None:
    pid = _clean(program_id)
    if not pid:
        return None
    row = session.execute(
        sa_text(f"SELECT {_PROGRAM_SELECT} FROM simulation_programs WHERE id = CAST(:id AS uuid)"),
        {"id": pid},
    ).fetchone()
    return _row_to_dict(row, _PROGRAM_COLUMNS) if row else None


def _make_status_apply(session: Any, *, table: str, select_sql: str, columns: Sequence[str], label: str):
    """CandidateFlow に渡す書き込み callable（楽観的な ``WHERE review_status = :old``）。"""

    def _apply(
        *,
        entity_id: str,
        old_status: str,
        new_status: str,
        actor_id: str | None,
        reason: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict:
        row = session.execute(
            sa_text(
                f"""
                UPDATE {table}
                   SET review_status = :new_status,
                       review_note = CASE WHEN :review_note <> '' THEN :review_note
                                          ELSE {table}.review_note END,
                       decided_by = :actor_id,
                       decided_at = now(),
                       updated_at = now()
                 WHERE id = CAST(:id AS uuid) AND review_status = :old_status
                RETURNING {select_sql}
                """
            ),
            {
                "id": entity_id,
                "old_status": old_status,
                "new_status": new_status,
                "review_note": _clean(reason),
                "actor_id": _clean(actor_id),
            },
        ).fetchone()
        if row is None:
            raise SimulationStateError(f"この{label}の状態は、ほかの操作によって変更されています。")
        return _row_to_dict(row, columns)

    return _apply


def _decide(
    session: Any,
    *,
    entity_id: str,
    current: dict | None,
    action: str,
    actor_id: str,
    note: str,
    record_audit: RecordAudit,
    vocab: CandidateVocabulary,
    table: str,
    select_sql: str,
    columns: Sequence[str],
    audit_table: Mapping[str, str],
    label: str,
) -> dict:
    if current is None:
        raise SimulationNotFoundError(f"{label}が見つかりません。")
    act = _clean(action)
    if act not in DECIDE_ACTIONS:
        raise SimulationStateError(f"判断の操作は confirm か dismiss です（受け取った値: {action}）。")
    actor = _require_actor(actor_id)
    if record_audit is None or not callable(record_audit):
        raise SimulationStateError("監査の記帳先が指定されていません。")
    current_status = _clean(current.get("review_status"))
    if current_status != vocab.candidate:
        raise SimulationStateError(f"この{label}は既に判断済みです。")
    flow = CandidateFlow(
        vocab=vocab,
        audit_entity_type=AUDIT_ENTITY_SIMULATION,
        apply_status=_make_status_apply(
            session, table=table, select_sql=select_sql, columns=columns, label=label
        ),
        record_audit=_translated_audit(record_audit, audit_table),
        require_dismiss_reason=False,
    )
    method = getattr(flow, act)
    try:
        result = method(entity_id, current_status=current_status, actor_id=actor, reason=_clean(note))
    except CandidateTransitionError as exc:
        raise SimulationStateError(f"この{label}は既に判断済みです。") from exc
    return result["applied"]


def decide_program(
    session: Any,
    *,
    program_id: str,
    action: str,
    actor_id: str,
    note: str = "",
    record_audit: RecordAudit,
) -> dict:
    """理論プログラム候補の確定 / 見送り（``candidate`` からのみ・理由は任意）。

    Raises:
        SimulationNotFoundError: 行が無い。
        SimulationStateError: 判断済み・語彙外の action・楽観的競合・帰属の欠落。
    """
    pid = _clean(program_id)
    return _decide(
        session,
        entity_id=pid,
        current=get_program(session, pid),
        action=action,
        actor_id=actor_id,
        note=note,
        record_audit=record_audit,
        vocab=PROGRAM_VOCABULARY,
        table="simulation_programs",
        select_sql=_PROGRAM_SELECT,
        columns=_PROGRAM_COLUMNS,
        audit_table=_PROGRAM_AUDIT_ACTIONS,
        label="理論プログラム",
    )


# ---------------------------------------------------------------------------
# instrument_wirings
# ---------------------------------------------------------------------------


def create_wiring(
    session: Any,
    *,
    document_id: str | None,
    name: str,
    parts: Sequence[Mapping[str, Any]] | None,
    edges: Sequence[Mapping[str, Any]] | None,
    origin: str,
    created_by: str,
    parent_wiring_id: str | None = None,
    revision_note: str = "",
    review_status: str | None = None,
    record_audit: RecordAudit | None = None,
) -> dict:
    """配線行を作る。

    ``review_status`` 未指定なら ``origin='teacher'`` → ``confirmed`` /
    ``origin='llm_candidate'`` → ``candidate``。**LLM の改訂提案を confirmed で作ることは
    できない**（SB5: 教員が確定して初めて run に使える）。``document_id`` が空なら
    論文に紐づかない汎用配線。部品・配線の妥当性検査（``instrument.validate_wiring``）は
    呼び出し側の責務。
    """
    org = _clean(origin)
    if org not in schema.WIRING_ORIGINS:
        raise SimulationStateError(f"配線の出所の語彙にない値です: {origin}")
    if review_status is None or not _clean(review_status):
        status = (
            schema.WIRING_STATUS_CONFIRMED
            if org == schema.WIRING_ORIGIN_TEACHER
            else schema.WIRING_STATUS_CANDIDATE
        )
    else:
        status = _clean(review_status)
    if status not in (schema.WIRING_STATUS_CANDIDATE, schema.WIRING_STATUS_CONFIRMED):
        raise SimulationStateError(f"配線をこの状態では作れません: {status}")
    if org == schema.WIRING_ORIGIN_LLM and status == schema.WIRING_STATUS_CONFIRMED:
        raise SimulationStateError("AI の改訂提案は候補として作ります。教員が確定するまで使えません。")
    creator = _clean(created_by)
    if status == schema.WIRING_STATUS_CONFIRMED:
        _require_actor(creator)

    row = session.execute(
        sa_text(
            f"""
            INSERT INTO instrument_wirings
                (document_id, name, parts, edges, origin, parent_wiring_id, revision_note,
                 review_status, decided_by, decided_at, created_by)
            VALUES
                (CAST(NULLIF(:document_id, '') AS uuid), :name, CAST(:parts AS jsonb),
                 CAST(:edges AS jsonb), :origin, CAST(NULLIF(:parent_wiring_id, '') AS uuid),
                 :revision_note, :review_status,
                 CASE WHEN :review_status = :confirmed THEN :created_by ELSE NULL END,
                 CASE WHEN :review_status = :confirmed THEN now() ELSE NULL END,
                 :created_by)
            RETURNING {_WIRING_SELECT}
            """
        ),
        {
            "document_id": _clean(document_id),
            "name": _clean(name),
            "parts": _dumps([dict(p) for p in (parts or [])]),
            "edges": _dumps([dict(e) for e in (edges or [])]),
            "origin": org,
            "parent_wiring_id": _clean(parent_wiring_id),
            "revision_note": str(revision_note or "").strip(),
            "review_status": status,
            "confirmed": schema.WIRING_STATUS_CONFIRMED,
            "created_by": creator,
        },
    ).fetchone()
    if row is None:
        raise SimulationStateError("配線を保存できませんでした。")
    wiring = _row_to_dict(row, _WIRING_COLUMNS)
    _audit(
        record_audit,
        entity_id=wiring["id"],
        action="wiring_created" if org == schema.WIRING_ORIGIN_TEACHER else "wiring_proposed",
        new_status=status,
        actor_id=creator or None,
        metadata={
            "document_id": _clean(document_id) or None,
            "origin": org,
            "parent_wiring_id": _clean(parent_wiring_id) or None,
        },
    )
    return wiring


def list_wirings(session: Any, *, document_id: str | None, include_generic: bool = True) -> list[dict]:
    """教材の配線（+ 既定で汎用配線 = ``document_id IS NULL``）を新しい順に返す。"""
    doc = _clean(document_id)
    if not doc and not include_generic:
        return []
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_WIRING_SELECT} FROM instrument_wirings
             WHERE (:has_document AND document_id = CAST(:document_id AS uuid))
                OR (:include_generic AND document_id IS NULL)
             ORDER BY created_at DESC
            """
        ),
        {"document_id": doc or None, "has_document": bool(doc), "include_generic": bool(include_generic)},
    ).fetchall()
    return [_row_to_dict(r, _WIRING_COLUMNS) for r in (rows or [])]


def get_wiring(session: Any, wiring_id: str) -> dict | None:
    wid = _clean(wiring_id)
    if not wid:
        return None
    row = session.execute(
        sa_text(f"SELECT {_WIRING_SELECT} FROM instrument_wirings WHERE id = CAST(:id AS uuid)"),
        {"id": wid},
    ).fetchone()
    return _row_to_dict(row, _WIRING_COLUMNS) if row else None


def decide_wiring(
    session: Any,
    *,
    wiring_id: str,
    action: str,
    actor_id: str,
    note: str = "",
    record_audit: RecordAudit,
) -> dict:
    """配線の改訂候補の確定 / 見送り（``candidate`` からのみ）。"""
    wid = _clean(wiring_id)
    return _decide(
        session,
        entity_id=wid,
        current=get_wiring(session, wid),
        action=action,
        actor_id=actor_id,
        note=note,
        record_audit=record_audit,
        vocab=WIRING_VOCABULARY,
        table="instrument_wirings",
        select_sql=_WIRING_SELECT,
        columns=_WIRING_COLUMNS,
        audit_table=_WIRING_AUDIT_ACTIONS,
        label="配線",
    )


# ---------------------------------------------------------------------------
# simulation_runs（append-only）
# ---------------------------------------------------------------------------


def record_run(
    session: Any,
    *,
    document_id: str,
    program_id: str,
    wiring_id: str | None,
    observation_claim_ids: Sequence[str] | None,
    inputs: Mapping[str, Any] | None,
    result: Mapping[str, Any] | None,
    runner: Mapping[str, Any] | None,
    status: str,
    created_by: str,
    record_audit: RecordAudit | None = None,
) -> dict:
    """実行記録を 1 行追記する（失敗・rejected も残す = SB7）。"""
    st = _clean(status)
    if st not in schema.RUN_STATUSES:
        raise SimulationStateError(f"実行結果の語彙にない値です: {status}")
    doc = _clean(document_id)
    pid = _clean(program_id)
    if not doc or not pid:
        raise SimulationStateError("教材と理論プログラムが指定されていません。")
    row = session.execute(
        sa_text(
            f"""
            INSERT INTO simulation_runs
                (document_id, program_id, wiring_id, observation_claim_ids, inputs, result,
                 runner, status, created_by)
            VALUES
                (CAST(:document_id AS uuid), CAST(:program_id AS uuid),
                 CAST(NULLIF(:wiring_id, '') AS uuid), CAST(:observation_claim_ids AS jsonb),
                 CAST(:inputs AS jsonb), CAST(:result AS jsonb), CAST(:runner AS jsonb),
                 :status, :created_by)
            RETURNING {_RUN_SELECT}
            """
        ),
        {
            "document_id": doc,
            "program_id": pid,
            "wiring_id": _clean(wiring_id),
            "observation_claim_ids": _dumps([_clean(c) for c in (observation_claim_ids or []) if _clean(c)]),
            "inputs": _dumps(dict(inputs or {})),
            "result": _dumps(dict(result or {})),
            "runner": _dumps(dict(runner or {})),
            "status": st,
            "created_by": _clean(created_by),
        },
    ).fetchone()
    if row is None:
        raise SimulationStateError("実行記録を保存できませんでした。")
    run = _row_to_dict(row, _RUN_COLUMNS)
    _audit(
        record_audit,
        entity_id=run["id"],
        action="run_recorded",
        new_status=st,
        actor_id=_clean(created_by) or None,
        metadata={"document_id": doc, "program_id": pid, "wiring_id": _clean(wiring_id) or None},
    )
    return run


def list_runs(session: Any, *, document_id: str, limit: int = 50) -> list[dict]:
    doc = _clean(document_id)
    if not doc:
        return []
    try:
        lim = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        lim = 50
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_RUN_SELECT} FROM simulation_runs
             WHERE document_id = CAST(:document_id AS uuid)
             ORDER BY created_at DESC
             LIMIT :limit
            """
        ),
        {"document_id": doc, "limit": lim},
    ).fetchall()
    return [_row_to_dict(r, _RUN_COLUMNS) for r in (rows or [])]


def get_run(session: Any, run_id: str) -> dict | None:
    rid = _clean(run_id)
    if not rid:
        return None
    row = session.execute(
        sa_text(f"SELECT {_RUN_SELECT} FROM simulation_runs WHERE id = CAST(:id AS uuid)"),
        {"id": rid},
    ).fetchone()
    return _row_to_dict(row, _RUN_COLUMNS) if row else None


def set_run_ledger_candidate(session: Any, *, run_id: str, candidate_id: str) -> dict | None:
    """run に台帳候補 ID を刻印する（run の唯一の後書き列）。行が無ければ ``None``。"""
    row = session.execute(
        sa_text(
            f"""
            UPDATE simulation_runs SET ledger_candidate_id = :candidate_id
             WHERE id = CAST(:id AS uuid)
            RETURNING {_RUN_SELECT}
            """
        ),
        {"id": _clean(run_id), "candidate_id": _clean(candidate_id)},
    ).fetchone()
    return _row_to_dict(row, _RUN_COLUMNS) if row else None


# ---------------------------------------------------------------------------
# 台帳 epistemic_ledger.simulation_candidates（§4.5）
# ---------------------------------------------------------------------------


def summarize_run_inputs(inputs: Mapping[str, Any] | None) -> str:
    """run の入力条件をスコープの ``condition`` 軸 1 行にする（決定論・上限付き）。"""
    payload = dict(inputs or {})
    parts: list[str] = []
    values = payload.get("values")
    if isinstance(values, Mapping) and values:
        parts.append("入力 " + ", ".join(f"{k}={values[k]}" for k in sorted(values, key=str)))
    params = payload.get("params")
    if isinstance(params, Mapping) and params:
        parts.append("パラメータ " + ", ".join(f"{k}={params[k]}" for k in sorted(params, key=str)))
    if payload.get("seed") is not None:
        parts.append(f"seed={payload.get('seed')}")
    text = "・".join(parts) if parts else "入力条件の記録なし"
    if len(text) > _CONDITION_MAX_CHARS:
        text = text[: _CONDITION_MAX_CHARS - 1] + "…"
    return text


def build_ledger_candidate(
    *,
    run: Mapping[str, Any],
    statement: str,
    level: str,
    facts: Sequence[str] | None,
    reconstructed_inputs: bool,
    created_by: str,
    wiring_name: str = "",
) -> dict:
    """台帳候補 1 件（設計書 §4.5）。confidence は持たない（SB6）。

    ``wiring_name`` / ``condition`` は確定時にスコープの ``system`` / ``condition`` 軸へ
    写すための控え（run の入力から決定論で作る）。
    """
    statement_text = str(statement or "").strip()
    if not statement_text:
        raise SimulationStateError("台帳候補の文が空です。")
    lvl = _clean(level)
    if lvl not in schema.AGREEMENT_LEVELS:
        raise SimulationStateError(f"一致の段階の語彙にない値です: {level}")
    return {
        "candidate_id": str(uuid.uuid4()),
        "run_id": _clean(run.get("id")),
        "program_id": _clean(run.get("program_id")),
        "wiring_id": _clean(run.get("wiring_id")),
        "wiring_name": _clean(wiring_name),
        "condition": summarize_run_inputs(run.get("inputs") if isinstance(run.get("inputs"), Mapping) else {}),
        "statement": statement_text,
        "level": lvl,
        "facts": [str(f) for f in (facts or []) if str(f or "").strip()],
        "reconstructed_inputs": bool(reconstructed_inputs),
        "status": schema.LEDGER_CANDIDATE_STATUS_CANDIDATE,
        "detector_version": schema.DETECTOR_VERSION,
        "created_at": _now_iso(),
        "created_by": _clean(created_by),
    }


def _require_target_type(target_type: str) -> str:
    tt = _clean(target_type)
    if tt not in LEDGER_TARGET_TYPES:
        raise SimulationStateError(f"台帳の対象の種類にない値です: {target_type}")
    return tt


def _fetch_ledger(session: Any, target_type: str, target_id: str, *, for_update: bool = False) -> dict | None:
    row = session.execute(
        sa_text(
            """
            SELECT verification_status, verification_scopes, simulation_candidates
              FROM epistemic_ledger
             WHERE target_id = :tid AND target_type = :ttype
            """
            + (" FOR UPDATE" if for_update else "")
        ),
        {"tid": target_id, "ttype": target_type},
    ).fetchone()
    if row is None:
        return None
    values = _row_values(row, ("verification_status", "verification_scopes", "simulation_candidates"))
    return {
        "verification_status": _clean(values["verification_status"]) or "unknown",
        "verification_scopes": [s for s in _loads(values["verification_scopes"], []) if isinstance(s, dict)],
        "simulation_candidates": [c for c in _loads(values["simulation_candidates"], []) if isinstance(c, dict)],
    }


def append_ledger_candidate(
    session: Any,
    *,
    target_type: str,
    target_id: str,
    candidate: Mapping[str, Any],
    record_audit: RecordAudit | None = None,
    actor_id: str | None = None,
) -> dict:
    """台帳行を（無ければ）作り、``simulation_candidates`` に候補を 1 件追記する。

    台帳行の作成は ``routes/doubt.py::_get_or_create_ledger_row`` と同じ
    ``INSERT ... ON CONFLICT (target_id, target_type) DO NOTHING``。
    """
    tt = _require_target_type(target_type)
    tid = _clean(target_id)
    if not tid:
        raise SimulationStateError("台帳の対象が指定されていません。")
    item = dict(candidate or {})
    for key in schema.FORBIDDEN_DTO_KEYS:
        item.pop(key, None)
    if not _clean(item.get("candidate_id")):
        raise SimulationStateError("台帳候補の ID がありません。")
    session.execute(
        sa_text(
            """
            INSERT INTO epistemic_ledger (target_id, target_type)
            VALUES (:tid, :ttype)
            ON CONFLICT (target_id, target_type) DO NOTHING
            """
        ),
        {"tid": tid, "ttype": tt},
    )
    session.execute(
        sa_text(
            """
            UPDATE epistemic_ledger
               SET simulation_candidates = simulation_candidates || CAST(:new AS jsonb),
                   updated_at = now()
             WHERE target_id = :tid AND target_type = :ttype
            """
        ),
        {"new": _dumps([item]), "tid": tid, "ttype": tt},
    )
    _audit(
        record_audit,
        entity_id=f"{tt}:{tid}",
        action="ledger_candidate_added",
        new_status=schema.LEDGER_CANDIDATE_STATUS_CANDIDATE,
        actor_id=_clean(actor_id) or _clean(item.get("created_by")) or None,
        metadata={"candidate_id": item.get("candidate_id"), "run_id": item.get("run_id")},
    )
    return item


def decide_ledger_candidate(
    session: Any,
    *,
    target_type: str,
    target_id: str,
    candidate_id: str,
    action: str,
    actor_id: str,
    record_audit: RecordAudit | None = None,
) -> dict:
    """台帳候補の確定 / 見送り（配列内の status 遷移。行・要素は削除しない）。

    確定時は ``verification_scopes`` に ``VerificationScope`` 形のスコープを 1 件追加し
    （``system`` = 配線名 or 「配線なし」・``condition`` = 入力条件・``evidence_ids`` は空）、
    ``verification_status`` が ``untested`` / ``unknown`` のときだけ
    ``indirectly_supported`` に上げる（SB1。``directly_verified`` には決して上げない）。

    Returns:
        ``{"candidate", "scope", "verification_status", "previous_status"}``
        （見送りでは ``scope`` は ``None``・status は不変）。

    Raises:
        SimulationNotFoundError: 台帳行・候補が無い。
        SimulationStateError: 判断済み・語彙外の action・帰属の欠落。
    """
    tt = _require_target_type(target_type)
    tid = _clean(target_id)
    cid = _clean(candidate_id)
    act = _clean(action)
    if act not in DECIDE_ACTIONS:
        raise SimulationStateError(f"判断の操作は confirm か dismiss です（受け取った値: {action}）。")
    actor = _require_actor(actor_id)

    ledger = _fetch_ledger(session, tt, tid, for_update=True)
    if ledger is None:
        raise SimulationNotFoundError("この対象の台帳がありません。")
    candidates = ledger["simulation_candidates"]
    target = next((c for c in candidates if _clean(c.get("candidate_id")) == cid), None)
    if target is None:
        raise SimulationNotFoundError("台帳候補が見つかりません。")
    if _clean(target.get("status")) != schema.LEDGER_CANDIDATE_STATUS_CANDIDATE:
        raise SimulationStateError("この台帳候補は既に処理済みです。")

    previous_status = ledger["verification_status"]
    now = _now_iso()
    scope: dict | None = None
    new_status = previous_status
    if act == ACTION_CONFIRM:
        target["status"] = schema.LEDGER_CANDIDATE_STATUS_CONFIRMED
        target["confirmed_by"] = actor
        target["confirmed_at"] = now
        scope = VerificationScope(
            condition=str(target.get("condition") or "").strip() or "入力条件の記録なし",
            system=_clean(target.get("wiring_name")) or NO_WIRING_SYSTEM_LABEL,
            evidence_ids=[],
            recorded_by=actor,
            reason=f"検証シミュレーション run {_clean(target.get('run_id'))} の候補を教員が確定",
            recorded_at=now,
        ).model_dump()
        if previous_status in schema.LEDGER_RAISABLE_FROM:
            new_status = schema.LEDGER_MAX_STATUS
        session.execute(
            sa_text(
                """
                UPDATE epistemic_ledger
                   SET simulation_candidates = CAST(:candidates AS jsonb),
                       verification_scopes = verification_scopes || CAST(:scope AS jsonb),
                       verification_status = :verification_status,
                       updated_at = now()
                 WHERE target_id = :tid AND target_type = :ttype
                """
            ),
            {
                "candidates": _dumps(candidates),
                "scope": _dumps([scope]),
                "verification_status": new_status,
                "tid": tid,
                "ttype": tt,
            },
        )
        audit_action = "ledger_candidate_confirmed"
    else:
        target["status"] = schema.LEDGER_CANDIDATE_STATUS_DISMISSED
        target["dismissed_by"] = actor
        target["dismissed_at"] = now
        session.execute(
            sa_text(
                """
                UPDATE epistemic_ledger
                   SET simulation_candidates = CAST(:candidates AS jsonb),
                       updated_at = now()
                 WHERE target_id = :tid AND target_type = :ttype
                """
            ),
            {"candidates": _dumps(candidates), "tid": tid, "ttype": tt},
        )
        audit_action = "ledger_candidate_dismissed"

    _audit(
        record_audit,
        entity_id=f"{tt}:{tid}",
        action=audit_action,
        old_status=schema.LEDGER_CANDIDATE_STATUS_CANDIDATE,
        new_status=target["status"],
        actor_id=actor,
        metadata={
            "candidate_id": cid,
            "run_id": target.get("run_id"),
            "verification_status_before": previous_status,
            "verification_status_after": new_status,
        },
    )
    return {
        "candidate": dict(target),
        "scope": scope,
        "verification_status": new_status,
        "previous_status": previous_status,
    }


__all__ = [
    "DECIDE_ACTIONS",
    "LEDGER_TARGET_TYPES",
    "NO_WIRING_SYSTEM_LABEL",
    "PROGRAM_VOCABULARY",
    "SimulationNotFoundError",
    "SimulationStateError",
    "WIRING_VOCABULARY",
    "append_ledger_candidate",
    "build_ledger_candidate",
    "create_program",
    "create_wiring",
    "decide_ledger_candidate",
    "decide_program",
    "decide_wiring",
    "get_program",
    "get_run",
    "get_wiring",
    "list_programs",
    "list_runs",
    "list_wirings",
    "record_run",
    "set_run_ledger_candidate",
    "summarize_run_inputs",
]
