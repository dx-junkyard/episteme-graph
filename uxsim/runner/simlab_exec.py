"""検証シミュレーション層（SB層・学習画面「🧪 動かして確かめる」/ 教員「検証シミュレーション…」）の行為と投影。

契約の正本は ``docs/features/simulation_bridge_design.md`` §14.3（学習者 API）/ §6（教員 API）。
行為 id と引数は ``uxsim/actions/registry.py`` の ``_SIMLAB`` / ``_ADMIN_SIM``。

- 画面が覚えるもの（``PersonaSession.scratch``）: ``sim_document_id`` / ``sim_modules``（overview の modules）/
  ``sim_module_key``（選んだモジュール）/ ``sim_targets``（観測系の主張）/ ``sim_parts``（部品目録）/
  ``sim_programs_dto`` / ``sim_wirings_dto`` / ``sim_runs_dto``（新しい順）/ ``sim_prediction``（予想）。
  path の穴 ``{program_id}`` ``{wiring_id}`` ``{run_id}`` は ``session.known`` の ``sim_programs`` /
  ``sim_wirings`` / ``sim_runs`` から埋まる（``state.PersonaSession.resolve``）。
- 投影は画面（``frontend/public/js/simulation-lab.js`` / ``admin-simulation-bridge.js``）が描く欄だけを文にする。
  **内部 ID・英語・数値は隠さない**（§7.3 — 隠すと審判 B から欠陥が見えなくなる）。
- このモジュールは ``actions_exec`` を import しない（``actions_exec`` が末尾で本モジュールの ``HANDLERS`` を取り込む。
  ``state.project_observation`` も遅延 import で ``project`` を呼ぶ）。
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from uxsim.runner.state import PersonaSession, _clip, _error, _generic

# 画面（simulation-lab.js）の固定文 — 投影に添える（学習者が常に見る欄）
PANEL_OWN_RECORDS = "この記録はあなたにだけ表示されます。成績評価には使用されません。"
PANEL_NOT_VERIFICATION = "シミュレーションの一致は検証ではなく、分析の妥当性の候補にとどまります。"
PANEL_SANDBOX_MISSING = "この環境ではコードを実行する場所が用意されていません。コードの生成と編集はできますが、実行はできません。"
PANEL_SHARE_CONFIRM = "この実行を教員に共有します。比較が測れている実行なら、関係する主張の台帳に候補として記帳します（確定は教員が行います）。比較が測れていない実行は共有だけで、台帳の候補にはなりません。"
PANEL_PROPOSAL_HEAD = "AI の提案（未確認）"
PANEL_PROPOSAL_NOTE = "この提案は参考表示です"
METRIC_LABELS = {
    "relative_difference": "理論の出力と模擬観測の相対差",
    "separation_ratio": "差と模擬観測のばらつきの比",
    "reference_relative_difference": "理論の出力と論文の記載値の相対差",
}
STATUS_LABELS = {"succeeded": "実行できました", "failed": "実行は完了しませんでした"}
LEVEL_LABELS = {"outer": "外枠", "inner": "内側"}
PROGRAM_STATUS_LABELS = {"candidate": "候補", "confirmed": "採用済み", "dismissed": "見送り", "superseded": "旧版"}

_NUM_RE = re.compile(r"^\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\s*$")


# ----------------------------------------------------------------------------
# 値の整形
# ----------------------------------------------------------------------------

def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and _NUM_RE.match(v):
        return float(v)
    return None


def parse_value(v: Any) -> Any:
    """入力の値: 数値 / 数値の列 / "0.1, 0.2, 0.3" のカンマ区切り → 数値 or 数値の列（それ以外はそのまま返す）。"""
    if isinstance(v, list):
        nums = [_num(x) for x in v]
        return [n for n in nums if n is not None] if nums and all(n is not None for n in nums) else v
    if isinstance(v, str) and "," in v:
        parts = [p for p in (x.strip() for x in v.split(",")) if p]
        nums = [_num(p) for p in parts]
        if parts and all(n is not None for n in nums):
            return nums
        return v
    n = _num(v)
    return v if n is None else n


def _numeric_map(m: Any) -> dict[str, Any]:
    """{記号: 値} か [{symbol|name, value}] → {記号: 整形した値}（数値にならない値はそのまま — 製品側の 422 を見る）。"""
    out: dict[str, Any] = {}
    if isinstance(m, dict):
        for k, v in m.items():
            if str(k).strip():
                out[str(k).strip()] = parse_value(v)
    elif isinstance(m, list):
        for e in m:
            if isinstance(e, dict):
                k = e.get("symbol", e.get("name"))
                if str(k or "").strip():
                    out[str(k).strip()] = parse_value(e.get("value"))
    return out


def _pairs(v: Any) -> list[tuple[str, Any]]:
    """学習者 DTO（[{symbol, value}]）と教員 DTO（{記号: 値}）の両方を (記号, 値) の列に。"""
    if isinstance(v, list):
        return [(str(e.get("symbol", e.get("name", ""))), e.get("value")) for e in v if isinstance(e, dict)]
    if isinstance(v, dict):
        return [(str(k), val) for k, val in v.items()]
    return []


def _observed_map(v: Any) -> dict[str, tuple[Any, Any]]:
    """学習者 DTO ``[{symbol, mean, std}]`` / 教員 DTO ``{sym: {mean, std}}`` → {記号: (平均, ばらつき)}。"""
    out: dict[str, tuple[Any, Any]] = {}
    items = v if isinstance(v, list) else ([{"symbol": k, **(val if isinstance(val, dict) else {"mean": val})}
                                             for k, val in v.items()] if isinstance(v, dict) else [])
    for e in items:
        if isinstance(e, dict) and str(e.get("symbol") or ""):
            if e.get("mean") is None and e.get("std") is None and "value" in e:
                out[str(e["symbol"])] = (e.get("value"), None)
            else:
                out[str(e["symbol"])] = (e.get("mean"), e.get("std"))
    return out


def _fmt(v: Any) -> str:
    """画面（simulation-lab.js ``fmt``）と同じ: 1e-3 未満・1e6 以上は指数表記、それ以外は小数 6 桁まで。"""
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        a = abs(float(v))
        if a != 0 and (a < 1e-3 or a >= 1e6):
            return f"{float(v):.4e}"
        return f"{round(float(v) * 1e6) / 1e6:g}"
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v[:8]) + ("…" if len(v) > 8 else "") + "]"
    return str(v)


def _facts(v: Any) -> list[str]:
    return [str(f) for f in (v or []) if str(f or "").strip()] if isinstance(v, list) else []


# ----------------------------------------------------------------------------
# 画面の記憶
# ----------------------------------------------------------------------------

def _dto_list(session: PersonaSession, key: str) -> list[dict]:
    v = session.scratch.get(key)
    return v if isinstance(v, list) else []


def _put_front(session: PersonaSession, key: str, kind: str, dto: Any) -> None:
    """新しい DTO を先頭に置き、id を既知 ID として覚える（``latest()`` が最新を返す）。"""
    if not isinstance(dto, dict) or not dto.get("id"):
        return
    rows = [d for d in _dto_list(session, key) if d.get("id") != dto.get("id")]
    session.scratch[key] = [dto] + rows
    session.remember(kind, dto.get("id"))


def _remember_overview(session: PersonaSession, body: dict) -> None:
    if body.get("document_id"):
        session.scratch["sim_document_id"] = str(body["document_id"])
    session.scratch["sim_modules"] = [m for m in (body.get("modules") or []) if isinstance(m, dict)]
    session.scratch["sim_targets"] = [t for t in (body.get("observation_targets") or []) if isinstance(t, dict)]
    session.scratch["sim_parts"] = [p for p in (body.get("parts_catalog") or []) if isinstance(p, dict)]
    session.scratch["sim_documents"] = [d for d in (body.get("documents") or []) if isinstance(d, dict)]
    for key, kind in (("programs", "sim_programs"), ("wirings", "sim_wirings"), ("runs", "sim_runs")):
        rows = [r for r in (body.get(key) or []) if isinstance(r, dict) and r.get("id")]
        session.scratch[f"sim_{key}_dto"] = rows  # 製品は新しい順で返す
        for r in reversed(rows):
            session.remember(kind, r.get("id"))


_TEX_CMD_RE = re.compile(r"\\(?:mathrm|mathcal|mathbf|mathit|rm|text|mathsf|boldsymbol|bm)\s*\{([^{}]*)\}")


_GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta", "η": "eta", "θ": "theta",
          "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "µ": "mu", "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho",
          "σ": "sigma", "τ": "tau", "υ": "upsilon", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
          "γ": "gamma", "δ": "delta", "θ": "theta", "λ": "lambda", "ξ": "xi", "π": "pi", "σ": "sigma",
          "φ": "phi", "ψ": "psi", "ω": "omega", "±": "pm", "∞": "infty"}


def norm_tex(text: Any) -> str:
    """ラベル照合用に TeX を平らにする（$ / 修飾コマンド / 波括弧 / 空白を落とし、ギリシャ文字は TeX 名に、小文字に）。"""
    s = str(text or "").replace("$", "")
    s = "".join(_GREEK.get(ch, ch) for ch in s).lower()
    for _ in range(3):
        s = _TEX_CMD_RE.sub(r"\1", s)
    s = re.sub(r"\\[a-z]+", lambda m: m.group(0)[1:], s)  # \alpha → alpha
    return re.sub(r"[{}\\\s]", "", s)


def _module_text(m: dict) -> str:
    return norm_tex(" ".join(str(m.get(k) or "") for k in ("label", "visual_label", "theory_object")))


def find_module(modules: list[dict], hint: Any) -> Optional[dict]:
    """module_hint（一覧の番号・ラベル / 対象の部分一致）→ モジュール。"""
    text = str(hint or "").strip()
    if not text or not modules:
        return None
    if text.isdigit():
        i = int(text)
        return modules[i - 1] if 1 <= i <= len(modules) else None
    needle = norm_tex(text)
    if not needle:
        return None
    # 理論対象そのもの（例: M_J）が一致するモジュールを、ラベルに含むだけのもの（λ_J、M_J）より先に
    for m in modules:
        if norm_tex(m.get("theory_object")) == needle:
            return m
    for m in modules:
        if needle in _module_text(m):
            return m
    # 空白で割った語の全部が入る（「P_B 制約」のような指定）
    words = [norm_tex(w) for w in text.lower().split() if w]
    for m in modules:
        if words and all(w in _module_text(m) for w in words):
            return m
    return None


def _latest_program(session: PersonaSession, *, confirmed_first: bool) -> Optional[dict]:
    rows = _dto_list(session, "sim_programs_dto")
    if confirmed_first:
        for r in rows:
            if r.get("review_status") == "confirmed":
                return r
    return rows[0] if rows else None


def _overview_query(ctx) -> dict:
    q: dict[str, Any] = {}
    doc = ctx.args.get("document_id") or ctx.session.scratch.get("sim_document_id")
    if doc:
        q["document_id"] = str(doc)
    topic = ctx.args.get("topic_id") or ctx.session.topic_id
    if topic and "document_id" not in q:
        q["topic_id"] = str(topic)
    return q


def _get_overview(ctx) -> tuple[Optional[int], Any]:
    status, body = ctx.call("GET", "/api/learning/courses/{course_id}/simulation/overview", params=_overview_query(ctx))
    if status == 200 and isinstance(body, dict):
        _remember_overview(ctx.session, body)
    return status, body


# ----------------------------------------------------------------------------
# 学習側の行為
# ----------------------------------------------------------------------------

def _open(ctx) -> None:
    _get_overview(ctx)


def _select_module(ctx) -> None:
    status, body = _get_overview(ctx)
    if status != 200 or not isinstance(body, dict):
        return
    modules = _dto_list(ctx.session, "sim_modules")
    hint = ctx.args.get("module_hint")
    module = find_module(modules, hint)
    if module is None:
        ctx.session.last_body = {"_sim_select_miss": str(hint or ""), "modules": modules,
                                 "document_title": body.get("document_title", "")}
        return
    ctx.session.scratch["sim_module_key"] = str(module.get("module_key") or "")
    ctx.session.scratch["sim_module"] = module
    ctx.session.last_body = {"_sim_selected": module, "document_title": body.get("document_title", ""),
                             "parts_catalog": body.get("parts_catalog") or [],
                             "observation_targets": body.get("observation_targets") or []}


def _generate(ctx) -> None:
    modules = _dto_list(ctx.session, "sim_modules")
    hint = ctx.args.get("module_hint")
    module = find_module(modules, hint) if hint else None
    key = str((module or {}).get("module_key") or ctx.session.scratch.get("sim_module_key") or "")
    if not key:
        ctx.missing("module_key")
        return
    if module is not None:
        ctx.session.scratch["sim_module_key"] = key
        ctx.session.scratch["sim_module"] = module
    payload = {"module_key": key, "document_id": ctx.session.scratch.get("sim_document_id") or None,
               "topic_id": ctx.session.topic_id or None}
    status, body = ctx.main(json={k: v for k, v in payload.items() if v})
    if status == 200 and isinstance(body, dict):
        _put_front(ctx.session, "sim_programs_dto", "sim_programs", body.get("program"))


def _edit(ctx) -> None:
    program = _latest_program(ctx.session, confirmed_first=False)
    if program is None:
        ctx.missing("program_id")
        return
    source = ctx.args.get("source_code")
    if not str(source or "").strip():
        ctx.missing("source_code")
        return
    status, body = ctx.main(json={"source_code": str(source)}, overrides={"program_id": str(program["id"])})
    if status == 200 and isinstance(body, dict):
        _put_front(ctx.session, "sim_programs_dto", "sim_programs", body.get("program"))


def _adopt(ctx) -> None:
    program = _latest_program(ctx.session, confirmed_first=False)
    if program is None:
        ctx.missing("program_id")
        return
    status, body = ctx.main(overrides={"program_id": str(program["id"])})
    if status == 200 and isinstance(body, dict):
        _put_front(ctx.session, "sim_programs_dto", "sim_programs", body.get("program"))


def _find_part(parts: list[dict], name: Any) -> Optional[dict]:
    needle = str(name or "").strip().lower()
    if not needle:
        return None
    for p in parts:
        if str(p.get("name") or "").lower() == needle:
            return p
    for p in parts:
        if needle in str(p.get("name") or "").lower() or needle in str(p.get("summary") or "").lower():
            return p
    return None


def _wiring_save(ctx) -> None:
    parts_catalog = _dto_list(ctx.session, "sim_parts")
    if not parts_catalog:
        ctx.missing("parts_catalog")
        return
    names = ctx.args.get("parts")
    if isinstance(names, str):
        names = [x.strip() for x in re.split(r"[→,、>]+", names) if x.strip()]
    if not isinstance(names, list) or not names:
        ctx.missing("parts")
        return
    params_by_name = ctx.args.get("params") if isinstance(ctx.args.get("params"), dict) else {}
    parts, edges, unknown = [], [], []
    for i, nm in enumerate(names, start=1):
        entry = _find_part(parts_catalog, nm)
        if entry is None:
            unknown.append(str(nm))
            continue
        slot = f"s{i}"
        params = {}
        for key, val in (params_by_name.get(str(nm)) or params_by_name.get(str(entry.get("name"))) or {}).items():
            n = _num(val)
            params[str(key)] = n if n is not None else val
        parts.append({"slot": slot, "entry_id": entry.get("entry_id"), "version_no": entry.get("version_no"),
                      "params": params})
    if unknown:
        ctx.session.last_status, ctx.session.last_body = None, {"_sim_parts_unknown": unknown,
                                                                "catalog": parts_catalog}
        ctx.client.traces.append(_precondition_trace(ctx, "part_name"))
        return
    for a, b in zip(parts, parts[1:]):
        edges.append({"from_slot": a["slot"], "to_slot": b["slot"]})
    payload = {"name": str(ctx.args.get("name") or "わたしの配線"), "parts": parts, "edges": edges,
               "document_id": ctx.session.scratch.get("sim_document_id") or None,
               "topic_id": ctx.session.topic_id or None}
    status, body = ctx.main(json={k: v for k, v in payload.items() if v not in (None, "")})
    if status == 200 and isinstance(body, dict):
        _put_front(ctx.session, "sim_wirings_dto", "sim_wirings", body.get("wiring"))


def _precondition_trace(ctx, name: str):
    from uxsim.schema import HttpTrace  # 遅延 import（schema は軽いが、循環を避けて関数内に置く）

    return HttpTrace(method=ctx.action.api[0], path=ctx.action.api[1], status=None, error=f"precondition:{name}")


def _predict(ctx) -> None:
    pred = _numeric_map(ctx.args.get("prediction"))
    if not pred:
        ctx.missing("prediction")
        return
    ctx.session.scratch["sim_prediction"] = pred
    status, body = _get_overview(ctx)
    if status == 200 and isinstance(body, dict):
        ctx.session.last_body = {"_sim_prediction": pred, "document_title": body.get("document_title", "")}


def _pick_wiring(ctx) -> Optional[str]:
    """引数 wiring（名前の部分一致 / "none"）→ wiring_id。未指定なら最新の自分の配線。"""
    rows = _dto_list(ctx.session, "sim_wirings_dto")
    want = str(ctx.args.get("wiring") or "").strip()
    if want.lower() in ("none", "なし", "配線なし"):
        return None
    if want:
        for r in rows:
            if want.lower() in str(r.get("name") or "").lower():
                return str(r.get("id"))
        return None
    return str(rows[0]["id"]) if rows else None


def _claim_ids_for(ctx, hint: Any) -> list[str]:
    targets = _dto_list(ctx.session, "sim_targets")
    needle = str(hint or "").strip().lower()
    if not needle:
        return []
    ids = [str(t.get("claim_id")) for t in targets if needle in str(t.get("label") or "").lower()]
    return ids[:1]


def _run(ctx) -> None:
    program = _latest_program(ctx.session, confirmed_first=True)
    if program is None:
        ctx.missing("program_id")
        return
    values = _numeric_map(ctx.args.get("values"))
    params = _numeric_map(ctx.args.get("params"))
    prediction = _numeric_map(ctx.args.get("prediction")) or (
        ctx.session.scratch.get("sim_prediction") if isinstance(ctx.session.scratch.get("sim_prediction"), dict)
        else {})
    payload: dict[str, Any] = {"program_id": str(program["id"]), "values": values}
    wiring_id = _pick_wiring(ctx)
    if wiring_id:
        payload["wiring_id"] = wiring_id
    if params:
        payload["params"] = params
    if prediction:
        payload["prediction"] = [{"symbol": k, "value": v} for k, v in prediction.items()]
    claim_ids = _claim_ids_for(ctx, ctx.args.get("claim_hint"))
    if claim_ids:
        payload["observation_claim_ids"] = claim_ids
    if ctx.session.topic_id:
        payload["topic_id"] = ctx.session.topic_id
    seed = _num(ctx.args.get("seed"))
    if seed is not None:
        payload["seed"] = int(seed)
    status, body = ctx.main(json=payload)
    if status == 200 and isinstance(body, dict):
        _put_front(ctx.session, "sim_runs_dto", "sim_runs", body.get("run"))


def _share(ctx) -> None:
    rows = _dto_list(ctx.session, "sim_runs_dto")
    if not rows:
        ctx.missing("run_id")
        return
    claim_ids = _claim_ids_for(ctx, ctx.args.get("claim_hint"))
    payload = {"claim_ids": claim_ids} if claim_ids else {}
    status, body = ctx.main(json=payload, overrides={"run_id": str(rows[0]["id"])})
    if status == 200 and isinstance(body, dict):
        _put_front(ctx.session, "sim_runs_dto", "sim_runs", body.get("run"))


def _propose_revision(ctx) -> None:
    rows = _dto_list(ctx.session, "sim_runs_dto")
    with_wiring = [r for r in rows if r.get("wiring_id")]
    if not with_wiring:
        ctx.missing("run_id_with_wiring")
        return
    status, body = ctx.main(overrides={"run_id": str(with_wiring[0]["id"])})
    if status == 200 and isinstance(body, dict) and isinstance(body.get("wiring"), dict):
        _put_front(ctx.session, "sim_wirings_dto", "sim_wirings", body.get("wiring"))


# ----------------------------------------------------------------------------
# 教員側の行為
# ----------------------------------------------------------------------------

def _admin_open(ctx) -> None:
    doc = ctx.args.get("document_id")
    hint = str(ctx.args.get("material_hint") or "").strip().lower()
    if not doc and hint:
        # 教材一覧（題名・ファイル名の部分一致）から document_id を引く（画面の行を選ぶのと同じ）
        status, body = ctx.call("GET", "/api/admin/materials")
        rows = body if isinstance(body, list) else (body.get("materials") or body.get("items") or []) if isinstance(body, dict) else []
        for m in rows:
            if isinstance(m, dict) and any(hint in str(m.get(k) or "").lower() for k in ("title", "filename")):
                doc = m.get("document_id")
                break
        if not doc:
            ctx.missing("material_hint")
            return
    doc = doc or ctx.session.latest("documents")
    if not doc:
        ctx.missing("document_id")
        return
    status, body = ctx.main(overrides={"document_id": str(doc)})
    if status == 200 and isinstance(body, dict):
        ctx.session.scratch["sim_document_id"] = str(doc)
        runs = [r for r in (body.get("runs") or []) if isinstance(r, dict) and r.get("id")]
        ctx.session.scratch["sim_runs_dto"] = runs
        shared = [r for r in runs if r.get("shared")]
        ctx.session.scratch["sim_shared_runs_dto"] = shared
        for r in reversed(shared):
            ctx.session.remember("sim_runs", r.get("id"))
        ctx.session.scratch["sim_parts"] = [p for p in (body.get("parts_catalog") or []) if isinstance(p, dict)]


def _shared_run(ctx) -> Optional[dict]:
    rows = _dto_list(ctx.session, "sim_shared_runs_dto")
    want = str(ctx.args.get("run_id") or "").strip()
    if want:
        for r in rows:
            if str(r.get("id")) == want:
                return r
        return {"id": want}
    return rows[0] if rows else None


def _admin_review_shared(ctx) -> None:
    run = _shared_run(ctx)
    if run is None:
        ctx.missing("run_id")
        return
    status, body = ctx.main(overrides={"run_id": str(run["id"])})
    if status == 200 and isinstance(body, dict) and isinstance(body.get("run"), dict):
        rows = [r for r in _dto_list(ctx.session, "sim_shared_runs_dto") if r.get("id") != body["run"].get("id")]
        ctx.session.scratch["sim_shared_runs_dto"] = [body["run"]] + rows
        # 画面（admin-simulation-bridge.js 区画 4）は共有 run を選ぶと主張の台帳を 1 回読み、候補を並べる
        candidates = []
        for cid in [str(c) for c in (body["run"].get("observation_claim_ids") or []) if str(c or "").strip()][:3]:
            st2, ledger = ctx.call("GET", "/api/admin/doubt/ledger/{target_type}/{target_id}",
                                   overrides={"target_type": "claim", "target_id": cid})
            if st2 == 200 and isinstance(ledger, dict):
                for c in ledger.get("simulation_candidates") or []:
                    if isinstance(c, dict) and str(c.get("run_id") or "") == str(body["run"].get("id")):
                        candidates.append({"claim_id": cid, **c})
        ctx.session.last_status, ctx.session.last_body = 200, {**body, "_ledger_candidates": candidates,
                                                                "_claims_checked": bool(body["run"].get("observation_claim_ids"))}


def _admin_ledger_confirm(ctx) -> None:
    run = _shared_run(ctx)
    if run is None:
        ctx.missing("run_id")
        return
    claim_ids = [str(c) for c in (run.get("observation_claim_ids") or []) if str(c or "").strip()]
    if not claim_ids:
        ctx.missing("observation_claim_id")
        return
    target_type, target_id = "claim", claim_ids[0]
    status, body = ctx.call("GET", "/api/admin/doubt/ledger/{target_type}/{target_id}",
                            overrides={"target_type": target_type, "target_id": target_id})
    if status != 200 or not isinstance(body, dict):
        return
    candidate_id = ""
    for c in body.get("simulation_candidates") or []:
        if isinstance(c, dict) and str(c.get("run_id") or "") == str(run.get("id")):
            candidate_id = str(c.get("candidate_id") or c.get("id") or "")
            break
    if not candidate_id:
        ctx.session.last_body = {"_sim_no_candidate": True, "ledger": body}
        ctx.client.traces.append(_precondition_trace(ctx, "candidate_id"))
        ctx.session.last_status = None
        return
    ctx.main(json={}, overrides={"target_type": target_type, "target_id": target_id, "candidate_id": candidate_id})


HANDLERS: dict[str, Callable[[Any], None]] = {
    "learning.simlab.open": _open,
    "learning.simlab.select_module": _select_module,
    "learning.simlab.generate": _generate,
    "learning.simlab.edit": _edit,
    "learning.simlab.adopt": _adopt,
    "learning.simlab.wiring_save": _wiring_save,
    "learning.simlab.predict": _predict,
    "learning.simlab.run": _run,
    "learning.simlab.share": _share,
    "learning.simlab.propose_revision": _propose_revision,
    "admin.simulation.open": _admin_open,
    "admin.simulation.review_shared_run": _admin_review_shared,
    "admin.simulation.ledger_candidate_confirm": _admin_ledger_confirm,
}


# ----------------------------------------------------------------------------
# 投影（画面が描く欄だけを文にする）
# ----------------------------------------------------------------------------

def _eq_labels(items: Any) -> str:
    labels = [str(i.get("display_label") or i.get("equation_id") or "") for i in (items or []) if isinstance(i, dict)]
    return "、".join(x for x in labels if x) or "—"


def _module_lines(m: dict, *, detail: bool, index: Optional[int] = None, compact: bool = False) -> list[str]:
    """compact = 一覧（カードの見出し・入力→出力・復元の印だけ）。detail = 選んだときの詳細（前提を全部）。"""
    head = f"{index}. " if index is not None else ""
    level = LEVEL_LABELS.get(str(m.get("level") or ""), str(m.get("level") or ""))
    lines = [f"{head}[{level}] {m.get('label') or m.get('visual_label') or ''}"]
    if m.get("theory_object") and not compact:
        lines.append(f"   対象: {m['theory_object']}")
    io_line = f"   {_eq_labels(m.get('inputs'))} → {_eq_labels(m.get('outputs'))}"
    if m.get("to_sink"):
        io_line += "（→ 論文全体のまとめへ）"
    lines.append(io_line)
    # 第 21 波 R1: 入力→出力が空でも、成員の式（equations）を画面が出す
    eqs = [e for e in (m.get("equations") or []) if isinstance(e, dict)]
    if eqs and (not m.get("inputs") or not m.get("outputs") or detail):
        shown = [str(e.get("display_label") or e.get("latex_snippet") or "") for e in eqs[:6]]
        lines.append("   この手順の式: " + "、".join(x for x in shown if x) + ("、…" if len(eqs) > 6 else ""))
    assumptions = [str(a.get("text") or "") for a in (m.get("assumptions") or []) if isinstance(a, dict)]
    assumptions = [a for a in assumptions if a]
    if compact:
        if assumptions:
            lines.append(f"   前提: {assumptions[0]}" + (" / …" if len(assumptions) > 1 else ""))
        if not m.get("has_equation_text"):
            lines.append("   （この理論モジュールには式の本文が引けないため、コードを生成できません）")
        if m.get("reconstructed_inputs"):
            lines.append("   入力の式に PDF から復元された式が含まれています。")
        return lines
    if assumptions:
        shown = assumptions if detail else assumptions[:3]
        lines.append("   前提: " + " / ".join(shown) + ("" if len(shown) == len(assumptions) else " / …"))
    if not m.get("has_equation_text"):
        lines.append("   （この理論モジュールには式の本文が引けないため、コードを生成できません）")
    if m.get("reconstructed_inputs"):
        lines.append("   入力の式に PDF から復元された式が含まれています。")
    prog = m.get("program") if isinstance(m.get("program"), dict) else None
    if prog:
        lines.append(f"   コード: {PROGRAM_STATUS_LABELS.get(str(prog.get('review_status') or ''), str(prog.get('review_status') or ''))}")
    return lines


def _parts_lines(parts: Any) -> list[str]:
    groups: dict[str, list[str]] = {}
    for p in parts or []:
        if isinstance(p, dict):
            label = str(p.get("entry_type_label") or p.get("entry_type") or "部品")
            params = "、".join(f"{q.get('name')}={_fmt(q.get('default'))}{q.get('unit') or ''}" for q in (p.get("params") or [])
                               if isinstance(q, dict))
            groups.setdefault(label, []).append(f"{p.get('name') or ''}" + (f"（{params}）" if params else ""))
    return [f"部品 — {k}: " + " / ".join(v) for k, v in groups.items()]


def _program_lines(program: dict, facts: list[str]) -> list[str]:
    io = program.get("io_contract") if isinstance(program.get("io_contract"), dict) else {}
    status = PROGRAM_STATUS_LABELS.get(str(program.get("review_status") or ""), str(program.get("review_status") or ""))
    lines = [f"コード（{status}）",
             f"入力: {', '.join(str(x if not isinstance(x, dict) else x.get('symbol') or x.get('name') or '') for x in (io.get('inputs') or [])) or '—'}"
             f" → 出力: {', '.join(str(x if not isinstance(x, dict) else x.get('symbol') or x.get('name') or '') for x in (io.get('outputs') or [])) or '—'}"]
    if io.get("params"):
        lines.append("パラメータ: " + ", ".join(str(x if not isinstance(x, dict) else x.get("name") or "") for x in io["params"]))
    prov = program.get("provenance") if isinstance(program.get("provenance"), dict) else {}
    for f in facts or _facts(program.get("facts")) or _facts(prov.get("facts")):
        lines.append(f)
    if prov.get("ast_ok") is False:
        lines.append("採用・実行できません: " + " / ".join(_facts(prov.get("ast_reasons"))))
    lines.append("```python")
    lines.append(str(program.get("source_code") or ""))
    lines.append("```")
    return lines


def _wiring_lines(w: dict) -> list[str]:
    chain = " → ".join(f"{p.get('name') or p.get('entry_id') or ''}" +
                       ("（" + ", ".join(f"{q.get('name')}={_fmt(q.get('value'))}" for q in _pairs_named(p.get('params'))) + "）"
                        if _pairs_named(p.get('params')) else "")
                       for p in (w.get("parts") or []) if isinstance(p, dict))
    lines = [f"配線「{w.get('name') or ''}」: {chain or '（部品なし）'}"]
    if w.get("origin") == "llm_candidate":
        lines.append(f"{PANEL_PROPOSAL_HEAD} — {PANEL_PROPOSAL_NOTE}")
    if w.get("revision_note"):
        lines.append(str(w["revision_note"]))
    return lines


def _pairs_named(v: Any) -> list[dict]:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    if isinstance(v, dict):
        return [{"name": k, "value": val} for k, val in v.items()]
    return []


def _run_lines(run: dict, facts: list[str]) -> list[str]:
    status = str(run.get("status") or "")
    lines = [f"実行の記録: {STATUS_LABELS.get(status, status)}" + ("（共有済み）" if run.get("shared") else "")]
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    pred = dict(_pairs(run.get("prediction")))
    theory = dict(_pairs(result.get("theory_outputs")))
    observed = _observed_map(result.get("observed"))
    symbols: list[str] = []
    for m in (pred, theory, observed):
        for k in m:
            if k not in symbols:
                symbols.append(k)
    if symbols:
        # 画面（simulation-lab.js renderRunCard）と同じ列: あなたの予想 / 理論の出力 / 模擬観測（平均 ± ばらつき）
        # 教員が見る共有 run（shared_by_label あり）の列名は学習者の予想（画面の主語のずれを投影で作らない）
        who = "学習者の予想" if run.get("shared_by_label") else "あなたの予想"
        lines.append(f"記号 | {who} | 理論の出力 | 模擬観測（平均 ± ばらつき）")
        for s in symbols:
            o = observed.get(s)
            obs_text = "—" if o is None else f"{_fmt(o[0])} ± {_fmt(o[1])}"
            lines.append(f"{s} | {_fmt(pred.get(s))} | {_fmt(theory.get(s))} | {obs_text}")
    labels = result.get("labels") if isinstance(result.get("labels"), dict) else {}
    chips = [str(labels.get(k)) for k in ("agreement", "separation", "reference") if labels.get(k)]
    if chips:
        lines.append("理論の出力と模擬観測: " + " / ".join(chips))
    if result.get("statement"):
        lines.append(str(result["statement"]))
    comparison = result.get("comparison") if isinstance(result.get("comparison"), dict) else {}
    for f in _facts(result.get("facts")) + _facts(comparison.get("facts")) + list(facts or []):
        if f not in lines:
            lines.append(f)
    metrics = comparison.get("metrics") if isinstance(comparison.get("metrics"), dict) else {}
    for k, label in METRIC_LABELS.items():
        if metrics.get(k) is not None:
            lines.append(f"{label}: {_fmt(metrics[k])}")
    if run.get("shared_by_label"):
        lines.append(f"学習者が共有 {run['shared_by_label']}")
    elif status == "succeeded" and not run.get("shared"):
        # 画面（simulation-lab.js）の「共有する」は押すと確認の文を出してから送る。学習者はその文を読んで決められる
        lines.append(f"［共有する］（押すと確認: 「{PANEL_SHARE_CONFIRM}」）")
    return lines


def _overview_lines(body: dict) -> list[str]:
    lines = ["🧪 動かして確かめる" + (f" — {body.get('document_title')}" if body.get("document_title") else "")]
    if body.get("sandbox_configured") is False:
        lines.append(PANEL_SANDBOX_MISSING)
    for f in _facts(body.get("facts")):
        lines.append(f)
    docs = [d for d in (body.get("documents") or []) if isinstance(d, dict)]
    if not body.get("document_id") and docs:
        lines.append("論文: " + " / ".join(str(d.get("title") or d.get("document_id") or "") for d in docs))
    modules = [m for m in (body.get("modules") or []) if isinstance(m, dict)]
    if modules:
        lines.append("理論モジュールを選ぶ:")
        for i, m in enumerate(modules, start=1):
            lines.extend(_module_lines(m, detail=False, index=i, compact=True))
    elif body.get("document_id"):
        lines.append("理論モジュールが表示されていない。")
    lines.extend(_parts_lines(body.get("parts_catalog")))
    targets = [t for t in (body.get("observation_targets") or []) if isinstance(t, dict)]
    if targets:
        shown = [str(t.get("label") or "") for t in targets[:8]]
        lines.append("観測系の主張: " + " / ".join(shown) + (" / …" if len(targets) > 8 else ""))
    for key, label in (("programs", "わたしのコード"), ("wirings", "わたしの配線"), ("runs", "実行の記録")):
        rows = [r for r in (body.get(key) or []) if isinstance(r, dict)]
        if not rows:
            continue
        if key == "programs":
            lines.append(f"{label}: " + " / ".join(PROGRAM_STATUS_LABELS.get(str(r.get("review_status") or ""), str(r.get("review_status") or "")) for r in rows))
        elif key == "wirings":
            lines.append(f"{label}: " + " / ".join(str(r.get("name") or "") for r in rows))
        else:
            lines.append(f"{label}: " + " / ".join(
                STATUS_LABELS.get(str(r.get("status") or ""), str(r.get("status") or "")) +
                (" / " + str((r.get("result") or {}).get("labels", {}).get("agreement")) if isinstance(r.get("result"), dict) and (r.get("result") or {}).get("labels", {}).get("agreement") else "") +
                (" / 共有済み" if r.get("shared") else "") for r in rows))
    lines.append(PANEL_NOT_VERIFICATION)
    lines.append(PANEL_OWN_RECORDS)
    return lines


def project(action_id: str, status: Optional[int], body: Any) -> str:
    """SB層の応答をペルソナが読む画面の文に投影する。"""
    if isinstance(body, dict) and body.get("_precondition"):
        return f"この操作に必要なものがまだ画面に無い（{body['_precondition']}）。"
    if isinstance(body, dict) and body.get("_sim_parts_unknown"):
        names = "、".join(str(x) for x in body["_sim_parts_unknown"])
        return _clip("\n".join([f"部品目録に「{names}」という名前の部品は無い。"] + _parts_lines(body.get("catalog"))))
    if isinstance(body, dict) and body.get("_sim_no_candidate"):
        return _clip("\n".join(["この主張の台帳に、この実行の候補は記帳されていない。"] + _generic(body.get("ledger"), max_lines=40)))
    if status is None:
        return "画面が応答しなかった（接続できない / 時間切れ）。"
    if status >= 400:
        return _clip(_error(status, body))
    if not isinstance(body, dict):
        return _clip("\n".join(_generic(body)))
    if body.get("_sim_select_miss") is not None:
        lines = [f"「{body['_sim_select_miss']}」に合う理論モジュールが一覧に無い。一覧:"]
        for i, m in enumerate(body.get("modules") or [], start=1):
            lines.extend(_module_lines(m, detail=False, index=i))
        return _clip("\n".join(lines))
    if isinstance(body.get("_sim_selected"), dict):
        lines = ["選んだ理論モジュール:"] + _module_lines(body["_sim_selected"], detail=True)
        prog = body["_sim_selected"].get("program")
        if isinstance(prog, dict):
            lines.extend(_program_lines(prog, []))
        else:
            lines.append("コード（生成する・自分で書く・採用する）: まだ無い")
        lines.extend(_parts_lines(body.get("parts_catalog")))
        return _clip("\n".join(lines))
    if isinstance(body.get("_sim_prediction"), dict):
        pred = body["_sim_prediction"]
        return _clip("\n".join(["予想を書いてから条件を決める: " + ", ".join(f"{k}={_fmt(v)}" for k, v in pred.items()),
                                "（予想は実行の後に理論の出力・模擬観測と並べて表示される）"]))
    if action_id.startswith("admin.simulation."):
        return _clip("\n".join(_admin_lines(action_id, body)))
    if "program" in body and isinstance(body.get("program"), dict):
        return _clip("\n".join(_program_lines(body["program"], _facts(body.get("facts")))))
    if "wiring" in body and isinstance(body.get("wiring"), dict) and "proposal" not in body:
        return _clip("\n".join(_wiring_lines(body["wiring"])))
    if "proposal" in body:
        proposal = body.get("proposal") if isinstance(body.get("proposal"), dict) else {}
        lines = [PANEL_PROPOSAL_HEAD, PANEL_PROPOSAL_NOTE]
        # 画面（simulation-lab.js renderProposal）は各提案の理由の文だけを描く（op / slot / entry_id は出さない）
        for c in proposal.get("changes") or []:
            if isinstance(c, dict):
                lines.append(f"- {c.get('fact') or c.get('description') or c.get('reason') or c.get('kind') or ''}")
            elif isinstance(c, str):
                lines.append(f"- {c}")
        for d in proposal.get("dropped") or []:
            lines.append(f"（目録に無い提案を外した: {d}）")
        lines.extend(_facts(proposal.get("facts")))
        if isinstance(body.get("wiring"), dict):
            lines.extend(_wiring_lines(body["wiring"]))
        else:
            lines.append("新しい配線は作られなかった。")
        return _clip("\n".join(lines))
    if "run" in body and isinstance(body.get("run"), dict):
        return _clip("\n".join(_run_lines(body["run"], _facts(body.get("facts")))))
    if "modules" in body or "documents" in body:
        return _clip("\n".join(_overview_lines(body)))
    return _clip("\n".join(_generic(body)))


def _admin_lines(action_id: str, body: dict) -> list[str]:
    if action_id == "admin.simulation.open":
        lines = ["検証シミュレーション" + (f" — {body.get('document_title')}" if body.get("document_title") else "")]
        lines.extend(_facts(body.get("facts")))
        modules = [m for m in (body.get("modules") or []) if isinstance(m, dict)]
        if modules:
            lines.append("理論モジュール: " + " / ".join(str(m.get("label") or "") for m in modules[:12]) +
                         (" / …" if len(modules) > 12 else ""))
        programs = [p for p in (body.get("programs") or []) if isinstance(p, dict)]
        if programs:
            lines.append("コード: " + " / ".join(PROGRAM_STATUS_LABELS.get(str(p.get("review_status") or ""), str(p.get("review_status") or "")) for p in programs))
        wirings = [w for w in (body.get("wirings") or []) if isinstance(w, dict)]
        if wirings:
            lines.append("配線: " + " / ".join(str(w.get("name") or "") for w in wirings))
        runs = [r for r in (body.get("runs") or []) if isinstance(r, dict)]
        lines.append("実行の記録:" if runs else "実行の記録: なし")
        for r in runs:
            labels = (r.get("result") or {}).get("labels") if isinstance(r.get("result"), dict) else {}
            chip = ("学習者が共有" + (f" {r['shared_by_label']}" if r.get("shared_by_label") else "")) if r.get("shared") else ""
            lines.append(f"- {STATUS_LABELS.get(str(r.get('status') or ''), str(r.get('status') or ''))}"
                         + (f" / {labels.get('agreement')}" if isinstance(labels, dict) and labels.get("agreement") else "")
                         + (f" / {chip}" if chip else "") + f" id={r.get('id')}")
        return lines
    if action_id == "admin.simulation.review_shared_run" and isinstance(body.get("run"), dict):
        lines = _run_lines(body["run"], _facts(body.get("facts")))
        if "_ledger_candidates" in body:
            cands = body.get("_ledger_candidates") or []
            lines.append("台帳の候補（学習者が共有した実行）:")
            if not body.get("_claims_checked"):
                lines.append("  この実行には観測系の主張が選ばれていないため、台帳の候補はありません。")
            elif not cands:
                lines.append("  この実行の台帳の候補はありません（比較が測れていない実行は候補になりません）。")
            for c in cands:
                lines.append(f"  - 主張 {c.get('claim_id')}: {c.get('statement') or ''}（状態: {c.get('status')}）"
                             f" ［確定する］［見送る（理由必須）］")
        return lines + _generic(body["run"], max_lines=60)
    return _generic(body, max_lines=80)
