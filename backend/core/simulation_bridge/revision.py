"""配線の改訂提案（SB層 §5.4・LLM 1 コール + 決定論の捕捉ガード・DB 非接触）。

比較結果と現在の配線を見せ、**凍結版の部品目録の中から**組み替え（追加 / 除去 / パラメータ変更）を
最大 3 件だけ提案させる。validator が目録外の部品・宣言の無いパラメータ・範囲外の値・語彙外の
操作を落とす（SB5 捏造ガード）。新しいコードは書かせない。

U層の帰属（``usage_context``）と日次 CostGate は呼び出し側（route）の責務。
"""

from __future__ import annotations

import copy
import json
import math
import re
from typing import Any, Callable

from pydantic import BaseModel, Field

from core.llm_worker.single_shot import structured_call
from core.text_hygiene import UNTRUSTED_SOURCE_NOTICE, strip_control_sequences

from . import instrument, schema

__all__ = [
    "MAX_CHANGES",
    "FACT_REVISION_DEGRADED",
    "propose_revision",
    "build_revision_prompt",
]

#: 1 回の提案で受け付ける変更の上限。
MAX_CHANGES = 3
FACT_REVISION_DEGRADED = "改訂案の生成に失敗しました。時間をおいてもう一度試してください。"

_REASON_MAX_CHARS = 200
_SLOT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class _Change(BaseModel):
    op: str
    slot: str = ""
    entry_id: str = ""
    param: str = ""
    value: float | None = None
    reason: str = ""


class _RevisionResponse(BaseModel):
    changes: list[_Change] = Field(default_factory=list)


_PROMPT = """あなたは実験機シミュレータの配線を見直す補助者です。
理論の出力と模擬観測の比較結果を読み、配線の組み替えを最大 {max_changes} 件だけ提案してください。

規則:
- 使ってよい操作は add（部品を信号経路の末尾に足す）/ remove（slot を外す）/ set_param（slot のパラメータを変える）の 3 つだけです。
- add で使える部品は、下の「部品の目録」にある entry_id だけです。目録に無い部品を挙げないでください。
- set_param のパラメータ名と値は、その部品の params に宣言された名前と min〜max の範囲に限ります。
- 新しいコードや式を書かないでください。
- 各変更の reason は日本語 1 文で、なぜその組み替えが比較結果に効きそうかを書いてください。
- 組み替えで改善できないと考えるなら changes を空にしてください。

{notice}

[比較結果]
{comparison_json}

[現在の配線（上流から順）]
{wiring_json}

[部品の目録（凍結版）]
{catalog_json}
"""


def _clean(text: Any, limit: int = _REASON_MAX_CHARS) -> str:
    return strip_control_sequences(str(text or "")).strip()[:limit]


def _part_params_decl(entry: dict) -> dict:
    body = entry.get("body") if isinstance(entry.get("body"), dict) else {}
    params = body.get("params")
    return params if isinstance(params, dict) else {}


def _catalog_view(catalog: dict[str, dict]) -> list[dict]:
    view = []
    for entry_id in sorted(catalog):
        entry = catalog[entry_id] or {}
        body = entry.get("body") if isinstance(entry.get("body"), dict) else {}
        params = {}
        for name, spec in _part_params_decl(entry).items():
            if isinstance(spec, dict):
                params[str(name)] = {k: spec.get(k) for k in schema.PART_PARAM_KEYS if k in spec}
        view.append(
            {
                "entry_id": entry_id,
                "name": _clean(entry.get("name"), 120),
                "entry_type": entry.get("entry_type"),
                "summary": _clean(entry.get("summary") or body.get("summary") or body.get("depends_on"), 300),
                "removes": [str(r) for r in (body.get("removes") or []) if isinstance(r, str)],
                "noise_kind": body.get("noise_kind"),
                "params": params,
            }
        )
    return view


def _wiring_view(parts: list[dict], edges: list[dict], catalog: dict[str, dict]) -> list[dict]:
    order = instrument.chain_order(parts, edges) or [str(p.get("slot") or "") for p in parts]
    by_slot = {str(p.get("slot") or ""): p for p in parts}
    resolved = instrument.default_params(parts, catalog)
    view = []
    for slot in order:
        part = by_slot.get(slot) or {}
        entry = catalog.get(str(part.get("entry_id") or "")) or {}
        view.append(
            {
                "slot": slot,
                "entry_id": part.get("entry_id"),
                "name": _clean(entry.get("name"), 120),
                "entry_type": entry.get("entry_type"),
                "params": resolved.get(slot, {}),
            }
        )
    return view


def _comparison_view(comparison: Any) -> dict:
    if isinstance(comparison, schema.Comparison):
        comparison = comparison.to_dict()
    comparison = comparison if isinstance(comparison, dict) else {}
    metrics = comparison.get("metrics") if isinstance(comparison.get("metrics"), dict) else {}
    return {
        "agreement_level": comparison.get("agreement_level"),
        "separation_level": comparison.get("separation_level"),
        "reference_level": comparison.get("reference_level"),
        "facts": [_clean(f, 300) for f in (comparison.get("facts") or [])],
        "metrics": {
            k: metrics.get(k)
            for k in (
                schema.METRIC_RELATIVE_DIFFERENCE,
                schema.METRIC_SEPARATION_RATIO,
                schema.METRIC_REFERENCE_DIFFERENCE,
            )
        },
    }


def build_revision_prompt(wiring: dict, comparison: Any, catalog: dict[str, dict]) -> str:
    parts = list((wiring or {}).get("parts") or [])
    edges = list((wiring or {}).get("edges") or [])
    return _PROMPT.format(
        max_changes=MAX_CHANGES,
        notice=UNTRUSTED_SOURCE_NOTICE,
        comparison_json=json.dumps(_comparison_view(comparison), ensure_ascii=False),
        wiring_json=json.dumps(_wiring_view(parts, edges, catalog), ensure_ascii=False),
        catalog_json=json.dumps(_catalog_view(catalog), ensure_ascii=False),
    )


def _normalize_changes(parsed: Any) -> list[dict] | None:
    if parsed is None:
        return None
    if isinstance(parsed, BaseModel):
        parsed = parsed.model_dump()
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("changes")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, BaseModel):
            item = item.model_dump()
        if isinstance(item, dict):
            out.append(item)
    return out


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _new_slot(entry_type: str, existing: set[str]) -> str:
    n = 1
    while f"{entry_type}_{n}" in existing:
        n += 1
    return f"{entry_type}_{n}"


def _apply(parts: list[dict], edges: list[dict], change: schema.RevisionChange) -> tuple[list[dict], list[dict]]:
    parts = copy.deepcopy(parts)
    edges = copy.deepcopy(edges)
    if change.op == "add":
        order = instrument.chain_order(parts, edges)
        tail = order[-1] if order else (str(parts[-1].get("slot")) if parts else None)
        parts.append({"slot": change.slot, "entry_id": change.entry_id, "version_no": change.value, "params": {}})
        if tail:
            edges.append({"from_slot": tail, "to_slot": change.slot})
    elif change.op == "remove":
        incoming = [e for e in edges if e.get("to_slot") == change.slot]
        outgoing = [e for e in edges if e.get("from_slot") == change.slot]
        edges = [e for e in edges if change.slot not in (e.get("from_slot"), e.get("to_slot"))]
        if len(incoming) == 1 and len(outgoing) == 1:
            edges.append({"from_slot": incoming[0].get("from_slot"), "to_slot": outgoing[0].get("to_slot")})
        parts = [p for p in parts if p.get("slot") != change.slot]
    elif change.op == "set_param":
        for part in parts:
            if part.get("slot") == change.slot:
                params = dict(part.get("params") or {})
                params[change.param] = change.value
                part["params"] = params
    return parts, edges


def propose_revision(
    wiring: dict,
    comparison: Any,
    catalog: dict[str, dict],
    *,
    structured_fn: Callable[..., Any],
    text_fn: Callable[..., str] | None = None,
    model: Any = None,
) -> schema.RevisionProposal:
    """配線の改訂案を 1 回だけ LLM に求め、目録と規約に照らして通る変更だけを返す。"""
    base_parts = copy.deepcopy(list((wiring or {}).get("parts") or []))
    base_edges = copy.deepcopy(list((wiring or {}).get("edges") or []))
    if not catalog:
        return schema.RevisionProposal(
            changes=[], dropped=[], facts=[schema.FACT_NO_PARTS_CATALOG], parts=base_parts, edges=base_edges
        )

    prompt = build_revision_prompt(wiring, comparison, catalog)
    parsed = structured_call(
        prompt,
        _RevisionResponse,
        structured_fn=structured_fn,
        text_fn=text_fn,
        model=model,
        text_fallback=text_fn is not None,
        degraded=None,
        log_label="simulation revision",
    )
    raw_changes = _normalize_changes(parsed)
    if raw_changes is None:
        return schema.RevisionProposal(
            changes=[], dropped=[], facts=[FACT_REVISION_DEGRADED], parts=base_parts, edges=base_edges
        )

    dropped: list[str] = []
    if len(raw_changes) > MAX_CHANGES:
        dropped.append(f"提案は {MAX_CHANGES} 件までのため、それを超える変更を外しました。")
        raw_changes = raw_changes[:MAX_CHANGES]

    parts, edges = base_parts, base_edges
    baseline_problems = set(instrument.validate_wiring(parts, edges, catalog))
    accepted: list[schema.RevisionChange] = []

    for index, item in enumerate(raw_changes, start=1):
        op = str(item.get("op") or "").strip()
        slot = str(item.get("slot") or "").strip()
        entry_id = str(item.get("entry_id") or "").strip()
        param = str(item.get("param") or "").strip()
        value = item.get("value")
        reason = _clean(item.get("reason"))
        where = f"{index} 件目の提案"
        slots = {str(p.get("slot") or "") for p in parts}
        if op not in schema.REVISION_OPS:
            dropped.append(f"{where}は使えない操作だったため外しました。")
            continue
        if op == "add":
            entry = catalog.get(entry_id)
            if entry is None:
                dropped.append(f"{where}は目録に無い部品を挙げていたため外しました。")
                continue
            if entry.get("entry_type") not in schema.PART_TYPES:
                dropped.append(f"{where}は実験機の部品ではないものを挙げていたため外しました。")
                continue
            if not slot or slot in slots or not _SLOT_RE.match(slot):
                slot = _new_slot(str(entry.get("entry_type")), slots)
            change = schema.RevisionChange(op="add", slot=slot, entry_id=entry_id, reason=reason)
            change_for_apply = schema.RevisionChange(
                op="add", slot=slot, entry_id=entry_id, value=entry.get("version_no"), reason=reason
            )
        else:
            if slot not in slots:
                dropped.append(f"{where}は配線に無い slot を指していたため外しました。")
                continue
            if op == "remove":
                change = schema.RevisionChange(op="remove", slot=slot, reason=reason)
                change_for_apply = change
            else:
                target = next(p for p in parts if str(p.get("slot") or "") == slot)
                entry = catalog.get(str(target.get("entry_id") or "")) or {}
                spec = _part_params_decl(entry).get(param)
                if not param or not isinstance(spec, dict):
                    dropped.append(f"{where}は部品に宣言の無いパラメータを挙げていたため外しました。")
                    continue
                if not _is_number(value):
                    dropped.append(f"{where}のパラメータ『{param}』の値が数値ではなかったため外しました。")
                    continue
                numeric = float(value)
                if (_is_number(spec.get("min")) and numeric < float(spec["min"])) or (
                    _is_number(spec.get("max")) and numeric > float(spec["max"])
                ):
                    dropped.append(f"{where}のパラメータ『{param}』の値が部品の範囲の外だったため外しました。")
                    continue
                change = schema.RevisionChange(op="set_param", slot=slot, param=param, value=numeric, reason=reason)
                change_for_apply = change

        new_parts, new_edges = _apply(parts, edges, change_for_apply)
        problems = set(instrument.validate_wiring(new_parts, new_edges, catalog))
        if problems - baseline_problems:
            dropped.append(f"{where}を適用すると配線が規約を満たさなくなるため外しました。")
            continue
        parts, edges = new_parts, new_edges
        accepted.append(change)

    facts: list[str] = []
    if not accepted:
        # 第 22 周 (d'): 「作れなかった」だけでは理由が読めない。AI が変更を挙げなかったのか、挙げたが全て外れたのかを分ける。
        facts = [schema.FACT_NO_PROPOSAL, schema.FACT_NO_PROPOSAL_ALL_DROPPED if dropped else schema.FACT_NO_PROPOSAL_NONE]
    return schema.RevisionProposal(changes=accepted, dropped=dropped, facts=facts, parts=parts, edges=edges)
