"""審判 E — 観測差分（決定論・砂場 DB の**読み**だけ — PE2）。

``UXSIM_SANDBOX_DATABASE_URL`` が設定されているときだけ動く。無ければ「未実施」と記録する。
- ``llm_usage_events``: run の時間窓で ``success = false`` の行、``feature = 'unattributed'`` の行
- ``interest_traces``: uxsim ペルソナの ``kind = 'misconception'`` を一覧（仕込んだ誤解との照合は人）
- ``discuss_metric_events``: ``stance_corrected`` の回（様相の誤推定の追跡）
"""
from __future__ import annotations

from typing import Any, Optional

from uxsim.oracles.findings import FindingFactory
from uxsim.schema import Finding, RunMeta, TranscriptStep

SKIP_NOTE = "審判 E は UXSIM_SANDBOX_DATABASE_URL が未設定のため未実施。"


def _window(meta: RunMeta) -> dict[str, str]:
    return {"start": meta.started_at, "end": meta.finished_at or meta.started_at}


def run_usernames(steps: Optional[list[TranscriptStep]]) -> list[str]:
    """run に現れたペルソナのログイン名（``users.display_name``）。審判 E の LLM 計上を run の利用者に絞る
    （第 25 周 doubt H27 / atlas H24: 時刻窓だけで絞ると、並走した他の面のレーンの embedding を拾った）。"""
    pids = sorted({s.persona_id for s in (steps or []) if getattr(s, "persona_id", "")})
    if not pids:
        return []
    try:
        from uxsim.runner.api import persona_username  # 遅延 import（runner → oracles の向きを増やさない）
    except Exception:  # noqa: BLE001
        return []
    return [persona_username(p) for p in pids]


def collect(database_url: str, meta: RunMeta, engine: Any = None,
            usernames: Optional[list[str]] = None) -> dict[str, Any]:
    """砂場 DB から観測を読む（書き込まない）。``usernames`` があれば llm_usage_events をその利用者の行に限る。"""
    from sqlalchemy import bindparam, create_engine, text  # 遅延 import

    eng = engine or create_engine(database_url)
    w = _window(meta)
    rows: dict[str, Any] = {}
    by_user = ""
    if usernames:
        by_user = " AND user_id IN (SELECT id FROM users WHERE display_name IN :names)"
        w = dict(w, names=list(usernames))

    def _q(sql: str):
        stmt = text(sql)
        return stmt.bindparams(bindparam("names", expanding=True)) if usernames else stmt

    with eng.connect() as conn:
        failed = conn.execute(_q(
            "SELECT feature, operation, error_type, count(*) AS n FROM llm_usage_events "
            "WHERE success = false AND occurred_at BETWEEN CAST(:start AS timestamptz) AND CAST(:end AS timestamptz)"
            + by_user + " GROUP BY feature, operation, error_type ORDER BY n DESC"), w).mappings().all()
        rows["llm_failed"] = [dict(r) for r in failed]
        unattributed = conn.execute(_q(
            "SELECT operation, count(*) AS n FROM llm_usage_events WHERE feature = 'unattributed' "
            "AND occurred_at BETWEEN CAST(:start AS timestamptz) AND CAST(:end AS timestamptz)"
            + by_user + " GROUP BY operation"), w).mappings().all()
        rows["llm_unattributed"] = [dict(r) for r in unattributed]
        mis = conn.execute(text(
            "SELECT u.display_name AS username, t.topic_id, t.payload->>'text' AS text, t.status "
            "FROM interest_traces t JOIN users u ON u.id = t.user_id "
            "WHERE t.kind = 'misconception' AND u.display_name LIKE 'uxsim\\_%' "
            "AND t.created_at BETWEEN CAST(:start AS timestamptz) AND CAST(:end AS timestamptz) "
            "ORDER BY t.created_at"), w).mappings().all()
        rows["misconception_traces"] = [dict(r) for r in mis]
        stance = conn.execute(text(
            "SELECT u.display_name AS username, e.created_at::text AS at FROM discuss_metric_events e "
            "JOIN users u ON u.id = e.user_id WHERE e.event = 'stance_corrected' AND u.display_name LIKE 'uxsim\\_%' "
            "AND e.created_at BETWEEN CAST(:start AS timestamptz) AND CAST(:end AS timestamptz)"), w).mappings().all()
        rows["stance_corrected"] = [dict(r) for r in stance]
    return rows


def check(meta: Optional[RunMeta], factory: FindingFactory, database_url: str = "",
          engine: Any = None, *, steps: Optional[list[TranscriptStep]] = None) -> tuple[list[Finding], dict]:
    if meta is None or not (database_url or engine is not None):
        return [], {"note": SKIP_NOTE}
    names = run_usernames(steps)
    window_note = "" if names else ("（run の利用者で絞れなかった — 同じ時間窓に並走した他のレーンの行の可能性がある）")
    try:
        rows = collect(database_url, meta, engine, usernames=names or None)
    except Exception as exc:  # noqa: BLE001 — 読めなかった事実を残す
        return [], {"note": f"審判 E は砂場 DB を読めなかったため未実施: {type(exc).__name__}"}
    findings: list[Finding] = []
    # 同じ原因（error_type）の失敗は 1 件に束ねる（第 1 周: 課金切れ 1 原因で findings 5 件が量産された）。
    by_error: dict[str, list] = {}
    for r in rows["llm_failed"]:
        by_error.setdefault(str(r.get("error_type") or "種別なし"), []).append(r)
    for error_type, group in by_error.items():
        features = sorted({str(r.get("feature") or "") for r in group})
        findings.append(factory.make(
            oracle="E", severity="inconsistent", screen="", affordance="",
            hypothesis=f"製品側の LLM 呼び出しが失敗している（原因: {error_type}。影響した機能: {', '.join(features)}）"
                       + window_note,
            server_rows={"llm_usage_events": group}, layers=["usage_metering_u"]))
    if rows["llm_unattributed"]:
        findings.append(factory.make(
            oracle="E", severity="inconsistent", screen="", affordance="",
            hypothesis="LLM 呼び出しの一部が機能に帰属されていない（feature = unattributed）" + window_note,
            server_rows={"llm_usage_events": rows["llm_unattributed"]}, layers=["usage_metering_u"]))
    return findings, {"misconception_traces": rows["misconception_traces"],
                      "stance_corrected": rows["stance_corrected"]}
