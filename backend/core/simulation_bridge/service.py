"""検証シミュレーション層（SB層）— 教員向け・学習者向けの 2 つのルーターが共有する手順。

正本: ``docs/features/simulation_bridge_design.md`` §5（各部品の規則）/ §14（v1.1 主語は学習者）。

教員向け ``api/routes/simulation_bridge.py`` と学習者向け ``api/routes/simulation_lab.py`` は
権限ゲート・所有の規則・DTO の形だけが違い、「理論モジュールから式と記号を引く」
「生成コードを AST 検査して sim-runner で走らせ比較する」「成功した run を台帳候補にする」
の手順は同じ。その手順をここに 1 つだけ置く（二重実装しない）。

- **FastAPI を import しない**（開発ルール2）。HTTP への写像（404 / 422）は呼び出し側の責務で、
  本モジュールは ``ValueError`` / 素の戻り値で事実を返す。
- LLM 関数・sandbox クライアント・store モジュールは**引数で受け取る**（ルーター側の patch
  seam ``generate_text_with_structured_output`` / ``_sandbox_client`` / ``store`` を壊さない）。
- 生成コードはこのプロセスで実行しない（SB3）。実行は ``sandbox_client.run`` に依頼し、その前段で
  AST 検査を毎回やり直す。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import text as sa_text

from core import display_projection
from core.label_vocab import SIMULATION_AGREEMENT_LABELS, SIMULATION_AXIS_LABELS, SIMULATION_SEPARATION_LABELS
from core.simulation_bridge import ast_guard, codegen, comparator, instrument, schema

logger = logging.getLogger(__name__)

#: 1 モジュールの生成に渡す記号の上限。
SYMBOL_LIMIT = 60


# ---------------------------------------------------------------------------
# 小道具
# ---------------------------------------------------------------------------


def loads(value: Any, default: Any) -> Any:
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


def rollback_quietly(session: Any) -> None:
    """読みの失敗を握りつぶすときに、セッションを使える状態へ戻す。

    PostgreSQL は文が 1 つ失敗するとトランザクション全体を abort にし、後続の文はすべて
    ``InFailedSqlTransaction`` になる。縮退値を返して続ける経路は、必ずここを通してから返す
    （握りつぶした SQL エラーでセッションを汚さない）。
    """
    rollback = getattr(session, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001 — rollback の失敗で応答を止めない
            logger.debug("simulation: rollback failed", exc_info=True)


def str_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v or "").strip()]
    return []


def has_text(row: Mapping[str, Any]) -> bool:
    return bool(str(row.get("latex") or "").strip() or str(row.get("plain_text") or "").strip())


def is_reconstructed(row: Mapping[str, Any]) -> bool:
    payload = row.get("agent_payload")
    policy = payload.get("confidence_policy") if isinstance(payload, dict) else None
    return bool(isinstance(policy, dict) and policy.get("must_not_treat_as_source_extracted"))


def find_module(modules: Sequence[Mapping[str, Any]], module_key: str) -> dict | None:
    return next((dict(m) for m in modules if str(m.get("module_key") or "") == module_key), None)


def module_equation_ids(module: Mapping[str, Any], record: Mapping[str, Any] | None) -> list[str]:
    """理論モジュールが受ける・生む式の ID（読み時 DTO + 保存行の成員 step）。重複なし・順序保持。"""
    ids: list[str] = []
    for key in ("inputs", "outputs", "foundation"):
        for item in module.get(key) or []:
            if isinstance(item, dict) and item.get("equation_id"):
                ids.append(str(item["equation_id"]))
    if record:
        for step in record.get("member_steps") or []:
            if isinstance(step, dict):
                ids.extend(str_list(step.get("input_equation_ids")))
                ids.extend(str_list(step.get("output_equation_ids")))
        for key in ("input_equation_ids", "output_equation_ids", "foundation_equation_ids", "produced_equation_ids"):
            ids.extend(str_list(record.get(key)))
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


# ---------------------------------------------------------------------------
# DB 読み（live ビューのみ — KO5）
# ---------------------------------------------------------------------------


def saved_module_records(session: Any, document_id: str) -> dict[str, dict]:
    """``knowledge_theory_modules_live`` の保存行（module_key → 成員 step と式 ID）。"""
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
        rollback_quietly(session)
        return {}
    out: dict[str, dict] = {}
    for row in rows or []:
        values = list(row)
        if len(values) < 7 or not values[0]:
            continue
        out[str(values[0])] = {
            "stable_key": str(values[1] or ""),
            "member_steps": loads(values[2], []),
            "input_equation_ids": loads(values[3], []),
            "output_equation_ids": loads(values[4], []),
            "foundation_equation_ids": loads(values[5], []),
            "produced_equation_ids": loads(values[6], []),
        }
    return out


_EQUATION_COLUMNS = ("agent_equation_id", "label", "latex", "plain_text", "defined_symbols", "used_symbols", "agent_payload")


def equation_rows(session: Any, document_id: str, ids: list[str] | None) -> list[dict]:
    """``knowledge_equations_live`` の式（``ids`` が None なら教材の全件）。"""
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
        rollback_quietly(session)
        return []
    out = []
    for row in rows or []:
        values = list(row)
        if len(values) < len(_EQUATION_COLUMNS):
            continue
        item = dict(zip(_EQUATION_COLUMNS, values))
        item["defined_symbols"] = loads(item.get("defined_symbols"), [])
        item["used_symbols"] = loads(item.get("used_symbols"), [])
        item["agent_payload"] = loads(item.get("agent_payload"), {})
        out.append(item)
    return out


_SYMBOL_COLUMNS = ("id", "agent_symbol_id", "canonical_symbol", "kind", "unit", "scope", "definition_evidence_texts", "agent_payload")


def symbol_rows(session: Any, document_id: str, equations: list[dict]) -> list[dict]:
    """``knowledge_symbols_live`` の記号（式に現れるものを優先・上限 :data:`SYMBOL_LIMIT`）。"""
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
        rollback_quietly(session)
        return []
    symbols = []
    for row in rows or []:
        values = list(row)
        if len(values) < len(_SYMBOL_COLUMNS):
            continue
        item = dict(zip(_SYMBOL_COLUMNS, values))
        item["definition_evidence_texts"] = loads(item.get("definition_evidence_texts"), [])
        item["agent_payload"] = loads(item.get("agent_payload"), {})
        symbols.append(item)
    wanted: set[str] = set()
    for eq in equations:
        wanted.update(str_list(eq.get("defined_symbols")))
        wanted.update(str_list(eq.get("used_symbols")))
    matched = [s for s in symbols if str(s.get("canonical_symbol") or "") in wanted]
    return (matched or symbols)[:SYMBOL_LIMIT]


def fetch_module_inputs(
    session: Any,
    document_id: str,
    module: Mapping[str, Any],
    *,
    saved_records: Callable[[Any, str], dict] | None = None,
    equations_fn: Callable[[Any, str, list[str] | None], list[dict]] | None = None,
    symbols_fn: Callable[[Any, str, list[dict]], list[dict]] | None = None,
) -> tuple[dict | None, list[dict], list[dict]]:
    """理論モジュール 1 つの生成に渡す (保存行, 式, 記号)。

    関数を差し替えられるのは、教員向けルーターの既存の patch seam を保つため。
    """
    records = (saved_records or saved_module_records)(session, document_id)
    record = records.get(str(module.get("module_key") or ""))
    ids = module_equation_ids(module, record)
    equations = (equations_fn or equation_rows)(session, document_id, ids)
    symbols = (symbols_fn or symbol_rows)(session, document_id, equations)
    return record, equations, symbols


def module_items(
    modules: Sequence[Mapping[str, Any]],
    *,
    records: Mapping[str, Mapping[str, Any]],
    equations_by_id: Mapping[str, Mapping[str, Any]],
    latest_program_by_module: Mapping[str, Any],
    program_dto: Callable[[Any], Any],
) -> list[dict]:
    """overview の ``modules[]``（指紋・内部の数値は載せない = TM6 / SB6）。"""
    items = []
    for module in modules:
        key = str(module.get("module_key") or "")
        ids = module_equation_ids(module, records.get(key))
        rows = [equations_by_id[i] for i in ids if i in equations_by_id]
        texted = [r for r in rows if has_text(r)]
        items.append({
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
            # 第 21 周 R1: 外枠の境界をまたがないモジュールは inputs / outputs が空になる。成員の式と
            # 論文全体のまとめ（sink）へ渡す式を additive に出す（theory_modules builder の
            # member_equations / to_sink。無ければ空 — 表示の正本は builder）。
            "equations": [
                {
                    "display_label": e.get("display_label") or "",
                    "latex_snippet": e.get("latex_snippet") or "",
                    "defined_symbols": str_list(e.get("defined_symbols")),
                }
                for e in module.get("member_equations") or [] if isinstance(e, dict)
            ],
            "to_sink": [
                {"equation_id": e.get("equation_id"), "display_label": e.get("display_label") or ""}
                for e in module.get("to_sink") or [] if isinstance(e, dict)
            ],
            "has_equation_text": bool(texted),
            "reconstructed_inputs": any(is_reconstructed(r) for r in texted),
            "program": program_dto(latest_program_by_module.get(key)),
        })
    return items


def latest_by_module(programs: Sequence[Mapping[str, Any]]) -> dict[str, dict]:
    """新しい順の programs から module_key ごとの最新 1 件。"""
    out: dict[str, dict] = {}
    for program in programs:
        key = str(program.get("module_key") or "")
        if key and key not in out:
            out[key] = dict(program)
    return out


# ---------------------------------------------------------------------------
# 生成（LLM 1 コール・候補 = SB3）
# ---------------------------------------------------------------------------


def generate_draft(
    module: Mapping[str, Any],
    *,
    equations: list[dict],
    symbols: list[dict],
    structured_fn: Callable[..., Any],
    text_fn: Callable[..., Any],
    model: str | None = None,
) -> tuple[Any, list[str], dict]:
    """理論プログラムの下書きを作り、(draft, 事実文, provenance) を返す。

    ``draft.source_code`` が空なら生成に失敗した（呼び出し側が 422 に写す）。AST 検査を
    通らない下書きも ``provenance.ast_ok=False`` で返す（候補として保存し、確定・採用を拒む）。
    """
    draft = codegen.generate_program(
        module,
        equations=equations,
        symbols=symbols,
        structured_fn=structured_fn,
        text_fn=text_fn,
        model=model,
    )
    facts = codegen.program_facts_for_display(draft)
    provenance = dict(draft.provenance or {})
    provenance["facts"] = facts
    provenance["ast_ok"] = bool(draft.accepted)
    if draft.ast_check is not None and not draft.accepted:
        provenance["ast_reasons"] = list(draft.ast_check.reasons)
    return draft, facts, provenance


def check_source(source_code: str) -> tuple[bool, list[str]]:
    """本人が書いた・直したコードの AST 検査（実行しない）。(合格, 理由の列)。"""
    check = ast_guard.check_program(str(source_code or ""))
    ok = bool(check.ok and check.entrypoint_found)
    reasons = list(check.reasons)
    if check.ok and not check.entrypoint_found and not reasons:
        reasons = [f"関数 {schema.PROGRAM_ENTRYPOINT}(inputs, params) が定義されていません。"]
    return ok, reasons


# ---------------------------------------------------------------------------
# 実行（AST 検査 → sim-runner → 比較 = SB3 / SB4）
# ---------------------------------------------------------------------------


#: 実行前検査で入力が足りないときの事実文（学習者が自分で直せるよう名指しする）。
FACT_MISSING_INPUT = "入力『{name}』の値が必要です。値を入れてから実行してください。"
#: 実行時エラーの事実文（本人のコードのエラーなので型とメッセージの先頭を見せる。制御文字は除く）。
FACT_RUNTIME_ERROR = "実行時のエラー: {head}"
_ERROR_HEAD_CHARS = 200


def missing_required_inputs(source: str, *, values: Mapping[str, Any], params: Mapping[str, Any]) -> list[str]:
    """コードが必ず読むキーのうち、入力にも定数にも無いものを名前順で返す（決定論・非実行）。"""
    keys = ast_guard.required_keys(source)
    given = {str(k) for k, v in dict(values or {}).items() if v is not None} | {
        str(k) for k, v in dict(params or {}).items() if v is not None
    }
    missing = [k for k, required in keys["inputs"].items() if required and k not in given]
    missing += [k for k, required in keys["params"].items() if required and k not in given]
    return sorted(set(missing))


def error_facts(error: str | None) -> list[str]:
    """sandbox のエラー文字列を 1 行の事実文にする（空なら何も足さない）。"""
    from core.text_hygiene import strip_control_sequences

    head = strip_control_sequences(str(error or "")).strip().splitlines()
    if not head or not head[0]:
        return []
    text = head[0][:_ERROR_HEAD_CHARS]
    if text.startswith("not_configured") or text.startswith("missing_inputs"):
        return []
    return [FACT_RUNTIME_ERROR.format(head=text)]


def run_pipeline(
    *,
    program: Mapping[str, Any],
    wiring: Mapping[str, Any] | None,
    catalog: Mapping[str, Mapping[str, Any]],
    values: Mapping[str, Any],
    params: Mapping[str, Any],
    instrument_params: Mapping[str, Mapping[str, Any]] | None,
    reference: Mapping[str, Any] | None,
    seed: int | None,
    sandbox_client: Any,
    module_label: str = "",
) -> tuple[str, dict, dict]:
    """1 run 分の手順を通し、(status, result, runner) を返す（記録は呼び出し側）。

    Raises:
        ValueError: 配線を実験機コードに組み立てられない（呼び出し側が 422 に写す）。
    """
    wiring_name = str((wiring or {}).get("name") or "")
    provenance = dict(program.get("provenance") or {})
    reconstructed = bool(provenance.get("reconstructed_inputs"))
    source = str(program.get("source_code") or "")
    check = ast_guard.check_program(source)
    if not (check.ok and check.entrypoint_found):
        return (
            schema.RUN_STATUS_REJECTED,
            {"facts": [schema.FACT_AST_REJECTED], "reconstructed_inputs": reconstructed},
            {"status": schema.RUN_STATUS_REJECTED, "ast_check": list(check.reasons), "error": ""},
        )

    instrument_source = ""
    instrument_param_values: dict[str, Any] = {}
    if wiring is not None:
        parts = list(wiring.get("parts") or [])
        edges = list(wiring.get("edges") or [])
        instrument_source = instrument.compose_instrument(parts, edges, catalog)
        merged = instrument.default_params(parts, catalog)
        for slot, slot_values in (instrument_params or {}).items():
            merged.setdefault(str(slot), {}).update({str(k): float(v) for k, v in (slot_values or {}).items()})
        instrument_param_values = merged

    # 実行前の検査: コードが必ず読むキーが入力に無ければ、KeyError を待たずに名指しで返す（学習者が直せる事実文）。
    missing = missing_required_inputs(source, values=values, params=params)
    if missing:
        facts = [FACT_MISSING_INPUT.format(name=name) for name in missing]
        return (
            schema.RUN_STATUS_FAILED,
            {"facts": facts, "reconstructed_inputs": reconstructed},
            {"status": schema.RUN_STATUS_FAILED, "error": "missing_inputs: " + ", ".join(missing), "stage": "theory"},
        )

    theory = sandbox_client.run(
        source,
        entrypoint=schema.PROGRAM_ENTRYPOINT,
        inputs=dict(values),
        params=dict(params),
        seed=seed,
    )
    if theory.status != schema.RUN_STATUS_SUCCEEDED:
        return (
            schema.RUN_STATUS_FAILED,
            {"facts": list(theory.facts or []) + error_facts(theory.error), "reconstructed_inputs": reconstructed},
            {"status": schema.RUN_STATUS_FAILED, "error": theory.error, "stage": "theory"},
        )

    theory_outputs = dict(theory.outputs or {})
    observed: dict | None = None
    if wiring is not None:
        observed_run = sandbox_client.run(
            instrument_source,
            entrypoint=schema.INSTRUMENT_ENTRYPOINT,
            inputs=theory_outputs,
            params=instrument_param_values,
            seed=seed,
        )
        if observed_run.status != schema.RUN_STATUS_SUCCEEDED:
            return (
                schema.RUN_STATUS_FAILED,
                {
                    "theory_outputs": theory_outputs,
                    "facts": list(observed_run.facts or []) + error_facts(observed_run.error),
                    "reconstructed_inputs": reconstructed,
                },
                {"status": schema.RUN_STATUS_FAILED, "error": observed_run.error, "stage": "instrument"},
            )
        raw = observed_run.outputs or {}
        observed = raw.get("observed") if isinstance(raw.get("observed"), dict) else {}

    comparison = comparator.compare(
        theory_outputs, observed, reference=dict(reference) if reference else None, wiring_name=wiring_name
    )
    statement = comparator.comparison_statement(comparison, module_label=module_label, wiring_name=wiring_name)
    # 第 21 周 R5: statement は一致の文を含むので、facts 側の同じ一致の文を 1 つに畳む。
    agreement_fact = schema.closed_world(
        schema.AGREEMENT_FACTS[comparison.agreement_level], wiring_name=wiring_name
    )
    comparison_dict = comparison.to_dict()
    comparison_dict["facts"] = without_statement_duplicate(comparison_dict["facts"], statement, agreement_fact)
    facts = list(comparison_dict["facts"])
    if reconstructed:
        facts.append(schema.FACT_RECONSTRUCTED_INPUTS)
    result = {
        "theory_outputs": theory_outputs,
        # 第 21 周 R7: 配線なしは「模擬観測は理論の出力そのもの」（FACT_NO_WIRING）なので、表もそうする。
        # 比較の意味（一致は未測定）は comparator のまま変えない。
        "observed": observed if observed is not None else observed_from_theory(theory_outputs),
        "comparison": comparison_dict,
        "labels": axis_labels(
            comparison.agreement_level, comparison.separation_level, comparison.reference_level
        ),
        "statement": statement,
        "facts": facts,
        "reconstructed_inputs": reconstructed,
        "wiring_name": wiring_name,
    }
    return schema.RUN_STATUS_SUCCEEDED, result, {"status": schema.RUN_STATUS_SUCCEEDED, "error": ""}


def axis_labels(agreement_level: str, separation_level: str, reference_level: str) -> dict[str, str]:
    """``result.labels``（第 21 周 R5: 値は ``"{軸名}: {段階}"`` — 3 つが何の段階か読めるように）。"""
    return {
        "agreement": f"{SIMULATION_AXIS_LABELS['agreement']}: {SIMULATION_AGREEMENT_LABELS[agreement_level]}",
        "separation": f"{SIMULATION_AXIS_LABELS['separation']}: {SIMULATION_SEPARATION_LABELS[separation_level]}",
        "reference": f"{SIMULATION_AXIS_LABELS['reference']}: {SIMULATION_AGREEMENT_LABELS[reference_level]}",
    }


def without_statement_duplicate(facts: Sequence[str], statement: str, agreement_fact: str) -> list[str]:
    """statement と同文・statement が既に言っている一致の文を facts から落とす（R5）。"""
    statement = str(statement or "")
    out = []
    for fact in facts or []:
        text = str(fact)
        if statement and (text == statement or text == agreement_fact):
            continue
        out.append(text)
    return out


def observed_from_theory(theory_outputs: Mapping[str, Any]) -> dict[str, dict]:
    """配線なしの模擬観測（= 理論の出力そのもの・ばらつきなし）を観測の表の形にする（R7）。"""
    out: dict[str, dict] = {}
    for symbol, value in (theory_outputs or {}).items():
        base = comparator.scalar(value)
        if base is not None:
            out[str(symbol)] = {"mean": base, "std": None}
    return out


def is_unmeasured_run(run: Mapping[str, Any]) -> bool:
    """比較の 3 軸（一致・分離・記載値）がすべて未測定の run か（共有で台帳候補を作らない判定）。"""
    comparison = dict((run.get("result") or {}).get("comparison") or {})
    unmeasured = schema.AGREEMENT_LEVEL_UNMEASURED
    return all(
        str(comparison.get(key) or unmeasured) == unmeasured
        for key in ("agreement_level", "separation_level", "reference_level")
    )


def is_readable_target_label(label: Any) -> bool:
    """観測系の主張の候補として学習者・教員に出してよいラベルか（R16・P-0008）。

    本文が空・``（本文を特定できない要素）``・内部 ID 形（``claim:`` / ``span_`` / ``tex_b`` 等）は出さない。
    英語の原文は教材の言語なので残す。
    """
    text = str(label or "").strip()
    if not text or text == display_projection.UNIDENTIFIED_ELEMENT_TEXT:
        return False
    lowered = text.lower()
    if lowered.startswith("claim:") or "span_" in lowered or "tex_b" in lowered:
        return False
    return not display_projection.contains_internal_id(text)


def readable_observation_targets(targets: Sequence[Any]) -> list[dict]:
    """``observation_claim_targets`` の戻りから、本文の読める候補だけを残す（両 route の唯一の判定）。"""
    return [
        dict(t) for t in targets or []
        if isinstance(t, Mapping) and t.get("claim_id") and is_readable_target_label(t.get("label"))
    ]


# ---------------------------------------------------------------------------
# 台帳候補（SB1 / SB13）
# ---------------------------------------------------------------------------


def ledger_candidates_for_run(
    session: Any,
    *,
    run: Mapping[str, Any],
    claim_ids: Sequence[str],
    created_by: str,
    store_module: Any,
    record_audit: Callable[..., Any] | None = None,
) -> list[dict]:
    """成功した run の比較結果を、観測系 claim ごとに台帳の候補として 1 件ずつ追記する。

    確定は教員（SB1・SB13）。``created_by`` は候補を出した人（v1.1 では共有した学習者もありうる）。
    run に最初の候補 ID を刻印する。``claim_ids`` が空なら何もしない（呼び出し側が事実文を返す）。

    Raises:
        store_module.SimulationStateError: 候補の組み立て・追記ができない。
    """
    ids = [str(c).strip() for c in claim_ids if str(c or "").strip()]
    if not ids:
        return []
    result = dict(run.get("result") or {})
    comparison = dict(result.get("comparison") or {})
    statement = str(result.get("statement") or "")
    level = str(comparison.get("agreement_level") or schema.AGREEMENT_LEVEL_UNMEASURED)
    candidates = []
    for claim_id in ids:
        candidate = store_module.build_ledger_candidate(
            run=run,
            statement=statement,
            level=level,
            facts=list(comparison.get("facts") or []),
            reconstructed_inputs=bool(result.get("reconstructed_inputs")),
            created_by=created_by,
            wiring_name=str(result.get("wiring_name") or ""),
        )
        candidates.append(
            store_module.append_ledger_candidate(
                session,
                target_type="claim",
                target_id=claim_id,
                candidate=candidate,
                record_audit=record_audit,
                actor_id=created_by,
                document_id=str(run.get("document_id") or "") or None,
                course_id=str(run.get("course_id") or "") or None,
            )
        )
    store_module.set_run_ledger_candidate(
        session, run_id=str(run.get("id") or ""), candidate_id=str(candidates[0]["candidate_id"])
    )
    return candidates


# ---------------------------------------------------------------------------
# 学習者向けの形（記号をキーにした写像をリストにする — §14.1 / IK-0598）
# ---------------------------------------------------------------------------


def symbol_value_list(mapping: Any, *, key_name: str = "symbol") -> list[dict]:
    """``{sym: value}`` → ``[{symbol, value}]``（記号の昇順・決定論）。"""
    if isinstance(mapping, list):
        return [dict(item) for item in mapping if isinstance(item, dict)]
    if not isinstance(mapping, Mapping):
        return []
    return [{key_name: str(k), "value": mapping[k]} for k in sorted(mapping, key=str)]


def observed_list(mapping: Any) -> list[dict]:
    """``{sym: {mean, std, samples}}`` → ``[{symbol, mean, std}]``（標本列は載せない）。"""
    if not isinstance(mapping, Mapping):
        return []
    out = []
    for symbol in sorted(mapping, key=str):
        entry = mapping[symbol]
        if isinstance(entry, Mapping):
            out.append({"symbol": str(symbol), "mean": entry.get("mean"), "std": entry.get("std")})
        else:
            out.append({"symbol": str(symbol), "mean": entry, "std": None})
    return out


def reference_list(mapping: Any) -> list[dict]:
    """``{sym: {value, unit, source_note}}`` → ``[{symbol, value, unit, source_note}]``。"""
    if not isinstance(mapping, Mapping):
        return []
    out = []
    for symbol in sorted(mapping, key=str):
        entry = mapping[symbol]
        if isinstance(entry, Mapping):
            out.append({
                "symbol": str(symbol),
                "value": entry.get("value"),
                "unit": str(entry.get("unit") or ""),
                "source_note": str(entry.get("source_note") or ""),
            })
        else:
            out.append({"symbol": str(symbol), "value": entry, "unit": "", "source_note": ""})
    return out


def metrics_by_symbol_list(mapping: Any) -> list[dict]:
    """``{sym: {relative_difference, separation_ratio, ...}}`` → ``[{symbol, relative_difference, separation_ratio}]``。"""
    if not isinstance(mapping, Mapping):
        return []
    out = []
    for symbol in sorted(mapping, key=str):
        entry = mapping[symbol] if isinstance(mapping[symbol], Mapping) else {}
        out.append({
            "symbol": str(symbol),
            schema.METRIC_RELATIVE_DIFFERENCE: entry.get(schema.METRIC_RELATIVE_DIFFERENCE),
            schema.METRIC_SEPARATION_RATIO: entry.get(schema.METRIC_SEPARATION_RATIO),
            schema.METRIC_REFERENCE_DIFFERENCE: entry.get(schema.METRIC_REFERENCE_DIFFERENCE),
        })
    return out


__all__ = [
    "SYMBOL_LIMIT",
    "axis_labels",
    "is_readable_target_label",
    "is_unmeasured_run",
    "observed_from_theory",
    "readable_observation_targets",
    "without_statement_duplicate",
    "check_source",
    "rollback_quietly",
    "equation_rows",
    "fetch_module_inputs",
    "find_module",
    "generate_draft",
    "has_text",
    "is_reconstructed",
    "latest_by_module",
    "ledger_candidates_for_run",
    "loads",
    "metrics_by_symbol_list",
    "module_equation_ids",
    "module_items",
    "observed_list",
    "reference_list",
    "run_pipeline",
    "saved_module_records",
    "str_list",
    "symbol_rows",
    "symbol_value_list",
]
