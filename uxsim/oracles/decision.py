"""審判 E6 — 一括確定の確定文脈（``decision_context``）が「何を見て何を選べたか」を再構成できるか（第 23 周）。

正本: ``docs/features/decision_context_design.md``（DC1〜DC4）・vision §6 改訂原則1。決定論・砂場 DB の**読み**だけ（PE2）。

- 砂場 DB があれば、run の時間窓に run の教員ペルソナが記帳した ``theory_review_events`` のうち
  ``metadata.action = 'accept_on_release'``（リリース前の確認の「次へ」）と ``'course_visibility'`` で
  ``new_status = 'public'``（公開）の行を読み、``metadata.decision_context`` を検査する。
- DB が無くても、transcript の ``admin.release_review.accept`` の応答に載る ``decision_context`` を同じ規則で検査する。

規則（どれも事実の検査。数値の閾値は置かない）:
1. 確定の行に ``decision_context`` がある（DC1）
2. ``presented`` / ``applied`` が ``{count, ids, truncated}`` の形で、``truncated`` は「件数 > 列挙した id の数」と一致する
3. ``presented_matches_applied`` が id の集合比較と一致する（切り詰めが無いときだけ判定できる — DC2）
4. ``alternatives_available`` が空でなく、``decline_possible`` が真（DC3）
5. ``reopen.path`` がある（再審の経路）
6. 行の対象（``entity_id``）が ``applied`` に含まれる（切り詰めが無いとき）
7. 来歴申告（``client_reported``）の「根拠を開いた行」は申告した提示集合の部分集合（DC4 — 申告の自己矛盾）
"""
from __future__ import annotations

from typing import Any, Optional

from uxsim.oracles.findings import FindingFactory, excerpt_json
from uxsim.schema import Finding, RunMeta, TranscriptStep

ACTION_ACCEPT = "accept_on_release"
ACTION_VISIBILITY = "course_visibility"
LAYERS = ["decision_context", "release_review"]


def _id_block_problems(name: str, block: Any) -> list[str]:
    if not isinstance(block, dict):
        return [f"{name} が無い"]
    ids, count, truncated = block.get("ids"), block.get("count"), block.get("truncated")
    if not isinstance(ids, list) or not isinstance(count, int) or not isinstance(truncated, bool):
        return [f"{name} が {{count, ids, truncated}} の形でない"]
    if truncated != (count > len(ids)):
        return [f"{name} の truncated が件数と列挙した id の数に合っていない"]
    if not truncated and count != len(set(map(str, ids))):
        return [f"{name} の件数と列挙した id の数が合っていない（切り詰めは無い）"]
    return []


def context_problems(ctx: Any, *, entity_id: str = "") -> list[str]:
    """``decision_context`` 1 ブロックの問題（事実文の列）。空なら再構成できる。"""
    if not isinstance(ctx, dict):
        return ["確定の記帳に decision_context が無い"]
    out: list[str] = []
    out += _id_block_problems("presented", ctx.get("presented"))
    out += _id_block_problems("applied", ctx.get("applied"))
    pres, appl = ctx.get("presented") or {}, ctx.get("applied") or {}
    if not out and not pres.get("truncated") and not appl.get("truncated"):
        same = set(map(str, pres.get("ids") or [])) == set(map(str, appl.get("ids") or []))
        if bool(ctx.get("presented_matches_applied")) != same:
            out.append("presented_matches_applied が提示と適用の id の集合比較と一致しない")
        if entity_id and entity_id not in set(map(str, appl.get("ids") or [])):
            out.append("記帳の対象が applied に含まれていない")
    skipped = ctx.get("skipped")
    if skipped is not None:
        # 第 23 周 F5: 「見たが確認しなかった」対象（編集権限が無い等）。提示 = 適用 ∪ 見送り、適用と見送りは交わらない
        out += _id_block_problems("skipped", skipped)
        if isinstance(skipped, dict) and not out and not (pres.get("truncated") or appl.get("truncated")
                                                          or skipped.get("truncated")):
            p_ids, a_ids = set(map(str, pres.get("ids") or [])), set(map(str, appl.get("ids") or []))
            k_ids = set(map(str, skipped.get("ids") or []))
            if a_ids & k_ids:
                out.append("同じ対象が applied と skipped の両方にある")
            if not k_ids <= p_ids:
                out.append("skipped に提示されていない対象がある")
            if k_ids and not str(skipped.get("reason") or "").strip():
                out.append("skipped に理由（reason）が無い")
    if not ctx.get("alternatives_available"):
        out.append("その場で選べた代替（alternatives_available）が空")
    if ctx.get("decline_possible") is not True:
        out.append("decline_possible が真でない")
    reopen = ctx.get("reopen") if isinstance(ctx.get("reopen"), dict) else {}
    if not str(reopen.get("path") or "").strip():
        out.append("再審の経路（reopen.path）が無い")
    reported = ctx.get("client_reported") if isinstance(ctx.get("client_reported"), dict) else {}
    opened = set(map(str, reported.get("evidence_expanded_placement_ids") or []))
    shown = reported.get("presented_placement_ids")
    if opened and isinstance(shown, list) and not opened <= set(map(str, shown)):
        out.append("来歴申告の「根拠を開いた行」が申告した提示集合の外にある")
    return out


def collect(database_url: str, meta: RunMeta, usernames: list[str], engine: Any = None) -> list[dict]:
    """run の窓・run の教員ペルソナの確定の記帳を読む（SELECT のみ）。"""
    if not usernames:
        return []
    from sqlalchemy import bindparam, create_engine, text  # 遅延 import

    eng = engine or create_engine(database_url)
    stmt = text(
        "SELECT e.entity_type, e.entity_id, e.old_status, e.new_status, e.metadata, e.created_at::text AS at, "
        "u.display_name AS username FROM theory_review_events e JOIN users u ON u.id = e.changed_by "
        "WHERE u.display_name IN :names AND e.metadata->>'action' IN (:a1, :a2) "
        "AND e.created_at BETWEEN CAST(:start AS timestamptz) AND CAST(:end AS timestamptz) ORDER BY e.created_at"
    ).bindparams(bindparam("names", expanding=True))
    with eng.connect() as conn:
        rows = conn.execute(stmt, {"names": list(usernames), "a1": ACTION_ACCEPT, "a2": ACTION_VISIBILITY,
                                   "start": meta.started_at, "end": meta.finished_at or meta.started_at}).mappings().all()
    return [dict(r) for r in rows]


def check_rows(rows: list[dict], factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    for r in rows:
        md = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        action = str(md.get("action") or "")
        if action == ACTION_VISIBILITY and str(r.get("new_status") or "") != "public":
            continue  # 公開以外の開示範囲の変更は一括確定ではない（設計書 §4）
        problems = context_problems(md.get("decision_context"), entity_id=str(r.get("entity_id") or ""))
        if problems:
            out.append(factory.make(
                oracle="E", severity="inconsistent", screen="admin", affordance="release-review.next"
                if action == ACTION_ACCEPT else "release-review.publish",
                hypothesis=f"確定の記帳から「何を見て何を選べたか」が再構成できない（{action}）: " + "; ".join(problems),
                server_rows={"theory_review_events": [{k: r.get(k) for k in ("entity_type", "entity_id", "new_status",
                                                                             "at", "username")}]},
                layers=LAYERS, fingerprint_key=f"e6:{action}:{'|'.join(problems)}"))
    return out


def check_transcript(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """「次へ」の応答に載る decision_context（画面が一致の事実文に使う dict）を同じ規則で見る。"""
    out: list[Finding] = []
    for step in steps:
        if step.action_id != "admin.release_review.accept":
            continue
        for t in step.http:
            if t.method.upper() != "POST" or t.status != 200:
                continue
            body = excerpt_json(t.response_excerpt)
            if not isinstance(body, dict):
                continue
            confirmed = body.get("confirmed")
            ctx = body.get("decision_context")
            if ctx is None and not confirmed:
                continue
            problems = context_problems(ctx)
            if problems:
                out.append(factory.make(oracle="E", severity="inconsistent", step=step, quote="; ".join(problems),
                                        hypothesis="「次へ」の応答の確定文脈から、提示と適用・選べた代替が再構成できない",
                                        layers=LAYERS, fingerprint_key="e6:transcript:" + "|".join(problems)))
    return out


def teacher_usernames(steps: list[TranscriptStep]) -> list[str]:
    try:
        from uxsim.runner.api import persona_username  # 遅延 import
    except Exception:  # noqa: BLE001
        return []
    return sorted({persona_username(s.persona_id) for s in steps if s.screen == "admin" and s.persona_id})


def check(steps: list[TranscriptStep], meta: Optional[RunMeta], factory: FindingFactory, database_url: str = "",
          engine: Any = None) -> tuple[list[Finding], dict]:
    found = check_transcript(steps, factory)
    notes: dict = {}
    if meta is None or not (database_url or engine is not None):
        notes["note"] = "審判 E6 の DB 側は UXSIM_SANDBOX_DATABASE_URL が未設定のため未実施（transcript 側だけ見た）。"
        return found, notes
    try:
        rows = collect(database_url, meta, teacher_usernames(steps), engine)
    except Exception as exc:  # noqa: BLE001 — 読めなかった事実を残す
        notes["note"] = f"審判 E6 の DB 側は砂場 DB を読めなかったため未実施: {type(exc).__name__}"
        return found, notes
    notes["rows"] = len(rows)
    if not rows and any(s.action_id in ("admin.release_review.accept", "admin.course.visibility") for s in steps):
        notes["fact"] = "run の窓に教員ペルソナの確定の記帳が無かった（「次へ」で確定した配置が無い・公開しなかった等）"
    return found + check_rows(rows, factory), notes
