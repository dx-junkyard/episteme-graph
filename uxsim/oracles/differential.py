"""審判 E7 — 差分審判（沈黙適応の仮説。第 23 周）。

vision §3.6 / UC5「能力・理解度の推定で提示内容・提示順を暗黙に変えない」。同じコースの**非 LLM** の応答
（コース / トピックの教材 / 議論の開幕 / 再構成の出題 / 論文の位置づけ）の構造を、学習者ごとに比べる。
比べる材料は runner が応答全体から作る ``HttpTrace.digest["structure"]``（欄の道 → 値の指紋。本人固有の欄・
宛先・時刻は落としてある — ``runner/digest.py``）。値そのものは残っていない（PE7）。

差は**仮説**として出す（severity は principle）。差の原因には正当なもの（教員がその間に確定した・解析し直した・
再構成の出題が本人の答えた履歴に依存する）もあるので、各 trace の時刻を並べて根拠に残す。人が読む（PE3）。

使い方:
- ``run_all`` が 1 run の中の学生同士を比べる。
- 別々の run（1 場面ずつ運転した周）を比べるには
  ``python -m uxsim.oracles.differential uxsim/runs/<c>/<run1> uxsim/runs/<c>/<run2> ...``
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from uxsim.oracles.findings import FindingFactory, load_meta, load_transcript
from uxsim.schema import Finding, TranscriptStep

COMPARE_ACTIONS = ("learning.course.open", "learning.topic.open", "learning.discuss.opening",
                   "learning.reconstruction.next", "learning.landscape.view")
# 本人の履歴で変わって当然かもしれない面（仮説文に添える）
HISTORY_DEPENDENT = {"learning.reconstruction.next": "再構成の出題は本人が答えた履歴で変わりうる",
                     "learning.landscape.view": "教員がその間に配置を確定・却下すると変わる",
                     "learning.discuss.opening": "教員がその間に議論のきっかけを承認すると変わる"}
MAX_PATHS_IN_EVIDENCE = 20


def observations(steps: list[TranscriptStep]) -> list[dict]:
    """(行為, path, ペルソナ, 時刻, 構造) の列。"""
    out: list[dict] = []
    for s in steps:
        if s.screen != "learning" or s.action_id not in COMPARE_ACTIONS:
            continue
        for t in s.http:
            struct = (t.digest or {}).get("structure")
            if t.method.upper() == "GET" and t.status == 200 and isinstance(struct, dict) and struct:
                out.append({"action_id": s.action_id, "path": t.path, "persona_id": s.persona_id, "at": s.at,
                            "seq": s.seq, "structure": struct, "run_id": ""})
    return out


def diff_paths(a: dict, b: dict) -> list[str]:
    keys = sorted(set(a) | set(b))
    return [k for k in keys if a.get(k) != b.get(k)]


def compare(obs: list[dict], factory: FindingFactory) -> list[Finding]:
    """同じ (行為, path) を別のペルソナが見たときの構造の差。ペルソナごとに最初の観測を代表にする。"""
    out: list[Finding] = []
    groups: dict[tuple[str, str], dict[str, dict]] = {}
    for o in obs:
        groups.setdefault((o["action_id"], o["path"]), {}).setdefault(o["persona_id"], o)
    for (action_id, path), by_persona in sorted(groups.items()):
        personas = sorted(by_persona)
        if len(personas) < 2:
            continue
        base = by_persona[personas[0]]
        for other in personas[1:]:
            o = by_persona[other]
            diff = diff_paths(base["structure"], o["structure"])
            if not diff:
                continue
            caveat = HISTORY_DEPENDENT.get(action_id, "")
            hyp = (f"同じコースの非 LLM の応答の構造が学習者で違う（{action_id}）— ペルソナ依存の出力（沈黙適応）の仮説"
                   + (f"。ただし{caveat}" if caveat else ""))
            out.append(factory.make(
                oracle="E", severity="principle", screen="learning", affordance="",
                hypothesis=hyp,
                server_rows={"path": path, "personas": [base["persona_id"], other],
                             "at": [base["at"], o["at"]], "runs": [base.get("run_id"), o.get("run_id")],
                             "differing_fields": diff[:MAX_PATHS_IN_EVIDENCE],
                             "differing_field_count_capped": len(diff) > MAX_PATHS_IN_EVIDENCE},
                steps=[s for s in (base.get("seq"), o.get("seq")) if isinstance(s, int)],
                layers=["learner_experience_b", "understanding_cycle"],
                fingerprint_key=f"e7:{action_id}:{path}:{base['persona_id']}:{other}"))
    return out


def check(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    return compare(observations(steps), factory)


def check_runs(run_dirs: list[Path], factory: Optional[FindingFactory] = None) -> list[Finding]:
    obs: list[dict] = []
    meta = None
    for d in run_dirs:
        m = load_meta(d)
        meta = meta or m
        for o in observations(load_transcript(d)):
            o["run_id"] = m.run_id if m else d.name
            obs.append(o)
    return compare(obs, factory or FindingFactory(meta))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="uxsim E7 差分審判（複数 run の学生を比べる）")
    ap.add_argument("run_dirs", nargs="+")
    ap.add_argument("--out", default="", help="findings を書き出す jsonl（省略時は標準出力）")
    a = ap.parse_args(argv)
    findings = check_runs([Path(x) for x in a.run_dirs])
    lines = [f.model_dump_json() for f in findings]
    if a.out:
        Path(a.out).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    else:
        for ln in lines:
            print(json.loads(ln)["hypothesis"])
    print(f"E7: {len(findings)} 件", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
