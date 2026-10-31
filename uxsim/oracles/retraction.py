"""審判 E8 — 撤回・訂正の保持（vision §3.7 可逆・§5.2 主権・P4。第 23 周）。

学習者が取り消した・外したものは**消えずに状態で残る**ことを確かめる。決定論。

transcript から取り消しの操作を集める:
- 会話の削除（``DELETE …/chat/messages/{message_id}`` 200）と書き直し（runner_note ``rewrite_target: <id>``）
  → その発言の派生痕跡（``interest_traces.payload.message_id``）は ``status='superseded'`` で残る
- 「地図には反映しない」「地図に戻す」（``POST /api/learning/traces/{id}/map-exclude|map-restore`` 200）
  → 行は残り、``payload.map_excluded`` が最後の操作と一致する
- 引っかかり・帰属の候補を外す（``POST /api/learning/{tension|anchors}/{id}/dismiss`` 200）
  → 行は残り、引っかかりは ``status='dismissed'``、帰属は ``payload.structure_anchor.status='dismissed'``

砂場 DB（読みだけ — PE2）があれば行を読む。無くても、操作の後に開いた「わたしの記録」
（``HttpTrace.digest["records"]``）に同じ記録が残っているかを見る（地図の除外・候補の却下）。
"""
from __future__ import annotations

import re
from typing import Any, Optional

from uxsim.oracles.findings import FindingFactory
from uxsim.schema import Finding, RunMeta, TranscriptStep

_DELETE_RE = re.compile(r"/chat/messages/(?P<mid>[^/]+)$")
_MAP_RE = re.compile(r"^/api/learning/traces/(?P<tid>[^/]+)/map-(?P<op>exclude|restore)$")
_DISMISS_RE = re.compile(r"^/api/learning/(?P<kind>tension|anchors)/(?P<tid>[^/]+)/dismiss$")
REWRITE_NOTE = "rewrite_target:"
LAYERS = ["trace_registry", "personal_network"]


def collect_ops(steps: list[TranscriptStep]) -> dict[str, Any]:
    """取り消しの操作（宛先と、操作した seq・ペルソナ）。"""
    ops: dict[str, Any] = {"messages": [], "map": {}, "dismiss": []}
    for s in steps:
        for note in s.runner_notes or []:
            if note.startswith(REWRITE_NOTE):
                ops["messages"].append({"message_id": note[len(REWRITE_NOTE):].strip(), "seq": s.seq,
                                        "persona_id": s.persona_id, "op": "rewrite"})
        for t in s.http:
            if t.status != 200:
                continue
            path = t.path or ""
            m = _DELETE_RE.search(path)
            if m and t.method.upper() == "DELETE":
                ops["messages"].append({"message_id": m.group("mid"), "seq": s.seq, "persona_id": s.persona_id,
                                        "op": "delete"})
                continue
            m = _MAP_RE.match(path)
            if m and t.method.upper() == "POST":
                ops["map"][m.group("tid")] = {"op": m.group("op"), "seq": s.seq, "persona_id": s.persona_id}
                continue
            m = _DISMISS_RE.match(path)
            if m and t.method.upper() == "POST":
                ops["dismiss"].append({"trace_id": m.group("tid"), "kind": m.group("kind"), "seq": s.seq,
                                       "persona_id": s.persona_id})
    return ops


def _records_after(steps: list[TranscriptStep], persona_id: str, seq: int) -> Optional[list[dict]]:
    """操作の後で同じペルソナが最初に開いた「わたしの記録」の行（無ければ None）。"""
    for s in steps:
        if s.persona_id != persona_id or s.seq <= seq or s.action_id != "learning.records.mine":
            continue
        for t in s.http:
            if t.status == 200 and t.path == "/api/me/records":
                return list((t.digest or {}).get("records") or [])
    return None


def check_transcript(steps: list[TranscriptStep], ops: dict[str, Any], factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []

    def make(hyp: str, rows: dict, key: str, seq: int) -> Finding:
        return factory.make(oracle="E", severity="inconsistent", screen="learning", affordance="topbar.my-records",
                            hypothesis=hyp, server_rows=rows, steps=[seq], layers=LAYERS, fingerprint_key=f"e8:{key}")

    for tid, op in ops["map"].items():
        rows = _records_after(steps, op["persona_id"], op["seq"])
        if rows is None:
            continue
        hit = next((r for r in rows if r.get("id") == tid), None)
        if hit is None:
            out.append(make("「地図には反映しない／地図に戻す」をした記録が、わたしの記録から消えている",
                            {"trace_id": tid, "op": op["op"]}, f"map_missing:{op['op']}", op["seq"]))
        elif bool(hit.get("map_excluded")) != (op["op"] == "exclude"):
            out.append(make("わたしの記録の「地図には出していません」が、最後の操作（地図に反映しない／戻す）と合っていない",
                            {"trace_id": tid, "op": op["op"], "map_excluded": hit.get("map_excluded")},
                            f"map_flag:{op['op']}", op["seq"]))
    for d in ops["dismiss"]:
        rows = _records_after(steps, d["persona_id"], d["seq"])
        if rows is None:
            continue
        hit = next((r for r in rows if r.get("id") == d["trace_id"]), None)
        if hit is None:
            out.append(make("外した候補が、わたしの記録から消えている（外しても状態で残る — P4）",
                            {"trace_id": d["trace_id"], "kind": d["kind"]}, f"dismiss_missing:{d['kind']}", d["seq"]))
        elif d["kind"] == "tension" and hit.get("status") != "dismissed":
            out.append(make("外した引っかかりの候補が、わたしの記録で「外した」状態になっていない",
                            {"trace_id": d["trace_id"], "status": hit.get("status")}, "dismiss_status", d["seq"]))
    return out


def collect_db(database_url: str, ops: dict[str, Any], engine: Any = None) -> dict[str, list[dict]]:
    """取り消しの宛先の行を読む（SELECT のみ）。"""
    from sqlalchemy import bindparam, create_engine, text  # 遅延 import

    eng = engine or create_engine(database_url)
    mids = sorted({m["message_id"] for m in ops["messages"] if m.get("message_id")})
    tids = sorted(set(ops["map"]) | {d["trace_id"] for d in ops["dismiss"]})
    rows: dict[str, list[dict]] = {"messages": [], "traces": []}
    with eng.connect() as conn:
        if mids:
            rows["messages"] = [dict(r) for r in conn.execute(text(
                "SELECT id::text AS id, kind, status, payload->>'message_id' AS message_id, "
                "payload->>'superseded_reason' AS superseded_reason FROM interest_traces "
                "WHERE payload->>'message_id' IN :mids").bindparams(bindparam("mids", expanding=True)),
                {"mids": mids}).mappings().all()]
        if tids:
            rows["traces"] = [dict(r) for r in conn.execute(text(
                "SELECT id::text AS id, kind, status, payload FROM interest_traces WHERE id::text IN :tids"
            ).bindparams(bindparam("tids", expanding=True)), {"tids": tids}).mappings().all()]
    return rows


def check_db(ops: dict[str, Any], rows: dict[str, list[dict]], factory: FindingFactory) -> tuple[list[Finding], dict]:
    out: list[Finding] = []
    notes: dict = {}

    def make(hyp: str, server_rows: dict, key: str) -> Finding:
        return factory.make(oracle="E", severity="inconsistent", screen="learning", affordance="", hypothesis=hyp,
                            server_rows=server_rows, layers=LAYERS, fingerprint_key=f"e8:db:{key}")

    by_mid: dict[str, list[dict]] = {}
    for r in rows.get("messages") or []:
        by_mid.setdefault(str(r.get("message_id") or ""), []).append(r)
    no_rows = []
    for m in ops["messages"]:
        traces = by_mid.get(m["message_id"], [])
        if not traces:
            no_rows.append(m["message_id"])
            continue
        live = [t for t in traces if t.get("status") != "superseded"]
        # 理由（payload.superseded_reason = rewrite | delete）が記帳されているなら操作と一致すること。
        # 記帳の無い行（旧行・理由の無い経路）は記録が無い事実として notes にだけ残す
        wrong = [t for t in traces if t.get("status") == "superseded" and t.get("superseded_reason")
                 and t.get("superseded_reason") != m["op"]]
        if wrong:
            out.append(make("取り消した痕跡の理由（書き直し / 取り消し）が、実際の操作と合っていない",
                            {"message_id": m["message_id"], "op": m["op"],
                             "reasons": sorted({str(t.get("superseded_reason")) for t in wrong})}, f"reason:{m['op']}"))
        if any(t.get("status") == "superseded" and not t.get("superseded_reason") for t in traces):
            notes.setdefault("superseded_without_reason", []).append(m["message_id"])
        if live:
            out.append(make(f"{'削除' if m['op'] == 'delete' else '書き直し'}した発言の派生痕跡が superseded になっていない",
                            {"message_id": m["message_id"], "traces": [{k: t.get(k) for k in ("id", "kind", "status")}
                                                                       for t in live]}, f"msg:{m['op']}"))
    if no_rows:
        notes["messages_without_traces"] = no_rows  # 痕跡を作らない発言もある（事実として残すだけ）
    by_id = {str(r.get("id")): r for r in rows.get("traces") or []}
    for tid, op in ops["map"].items():
        r = by_id.get(tid)
        if r is None:
            out.append(make("「地図には反映しない／地図に戻す」をした痕跡の行が無い（削除された）",
                            {"trace_id": tid, "op": op["op"]}, "map_row_missing"))
            continue
        payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        if bool(payload.get("map_excluded")) != (op["op"] == "exclude"):
            out.append(make("痕跡の map_excluded が最後の操作（地図に反映しない／戻す）と合っていない",
                            {"trace_id": tid, "op": op["op"], "map_excluded": payload.get("map_excluded")}, "map_flag"))
    for d in ops["dismiss"]:
        r = by_id.get(d["trace_id"])
        if r is None:
            out.append(make("外した候補の痕跡の行が無い（削除された）", {"trace_id": d["trace_id"], "kind": d["kind"]},
                            "dismiss_row_missing"))
            continue
        payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        if d["kind"] == "tension":
            ok = r.get("status") == "dismissed"
        else:
            sa = payload.get("structure_anchor") if isinstance(payload.get("structure_anchor"), dict) else {}
            ok = sa.get("status") == "dismissed"
        if not ok:
            out.append(make("外した候補が「外した」状態で残っていない", {"trace_id": d["trace_id"], "kind": d["kind"],
                                                                       "status": r.get("status")}, f"dismiss:{d['kind']}"))
    return out, notes


def check(steps: list[TranscriptStep], meta: Optional[RunMeta], factory: FindingFactory, database_url: str = "",
          engine: Any = None) -> tuple[list[Finding], dict]:
    ops = collect_ops(steps)
    found = check_transcript(steps, ops, factory)
    if not (ops["messages"] or ops["map"] or ops["dismiss"]):
        return found, {"note": "取り消しの操作が無い run（E8 の対象なし）"}
    if meta is None or not (database_url or engine is not None):
        return found, {"note": "審判 E8 の DB 側は UXSIM_SANDBOX_DATABASE_URL が未設定のため未実施（記録の画面側だけ見た）。"}
    try:
        rows = collect_db(database_url, ops, engine)
    except Exception as exc:  # noqa: BLE001
        return found, {"note": f"審判 E8 の DB 側は砂場 DB を読めなかったため未実施: {type(exc).__name__}"}
    more, notes = check_db(ops, rows, factory)
    return found + more, notes
