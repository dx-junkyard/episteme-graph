"""campaign の報告書（markdown）: ``python -m uxsim.report.campaign_report runs/<campaign>/<run_id>``。

数値は run の報告書にだけ置く（PE7）。発見は段階語（severity_label）で並べ、点数にしない。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

from uxsim.config import UXSIM_ROOT
from uxsim.oracles.findings import load_findings, load_meta, load_transcript

SEVERITY_ORDER = ("blocked", "confused", "inconsistent", "principle")
ORACLE_NAMES = {"A": "契約", "B": "原則", "C": "行動", "D": "文書", "E": "観測差分"}


def build_report(run_dir: Path) -> str:
    meta = load_meta(run_dir)
    steps = load_transcript(run_dir)
    findings = load_findings(run_dir / "findings.jsonl")
    notes_path = run_dir / "oracle_notes.json"
    oracle_notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.is_file() else {}
    lines: list[str] = []
    title = meta.campaign_id if meta else run_dir.parent.name
    lines.append(f"# ペルソナ通し受講の報告 — {title}")
    lines.append("")
    if meta:
        lines += [f"- run: `{meta.run_id}`（{meta.started_at} 〜 {meta.finished_at or '未完了'}）",
                  f"- 分野: {meta.domain} ／ snapshot: {meta.snapshot or '（未指定）'}",
                  f"- commit: `{meta.git_commit or '（不明）'}` ／ base_url: {meta.base_url}",
                  f"- replay 元: {meta.replay_of or 'なし'}",
                  f"- 予算: {json.dumps(meta.budget, ensure_ascii=False)} ／ 使用: {json.dumps(meta.spent, ensure_ascii=False)}"]
        if meta.flags:
            lines.append(f"- flags（宣言）: {json.dumps(meta.flags, ensure_ascii=False)}")
        if meta.pinned_course_id:
            lines.append(f"- 固定したコース: {meta.pinned_course_id}")
        if meta.pinned_course_mismatch:
            who = "、".join(f"{m.get('persona_id', '')}→{m.get('actual', '')}" for m in meta.pinned_course_mismatch)
            lines.append(f"- **注意: 固定したコースとは別のコースを開いたペルソナがいる（{who}）。その内容依存の検証は"
                         "固定したコースを見ていない**（IK-0506・砂場準備 `uxsim/sandbox/pin_course.py`）")
    lines.append("")
    lines.append("## ペルソナごとの歩み")
    by_persona: dict[str, list] = defaultdict(list)
    for s in steps:
        by_persona[s.persona_id].append(s)
    for pid, seq in by_persona.items():
        frictions = [s for s in seq if s.think.friction != "none"]
        gave_up = [s for s in seq if s.think.friction == "gave_up" or s.think.gave_up_reason]
        lines.append(f"### {pid}")
        lines.append(f"- ステップ: {len(seq)} ／ 経路: {', '.join(sorted({s.scenario_id for s in seq if s.scenario_id})) or '—'}")
        for s in frictions:
            lines.append(f"  - #{s.seq} `{s.action_id}` — {s.think.friction}: {s.think.reaction[:120]}")
        for s in gave_up:
            lines.append(f"  - 諦めた理由（#{s.seq}）: {s.think.gave_up_reason or '（理由なし）'}")
    lines.append("")
    lines.append("## 発見（審判・段階ごと）")
    if not findings:
        lines.append("発見はありません（見つからなかったことは、問題が無いことを意味しません）。")
    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for f in findings:
        grouped[(f.oracle, f.severity_label)].append(f)
    for oracle in "ABCDE":
        for sev in SEVERITY_ORDER:
            items = grouped.get((oracle, sev))
            if not items:
                continue
            lines.append(f"### {oracle}（{ORACLE_NAMES[oracle]}）— {sev}")
            for f in items:
                where = f" ／ `{f.affordance}`" if f.affordance else ""
                lines.append(f"- `{f.finding_id}` {f.hypothesis}{where} ／ ステップ {f.evidence.transcript_steps}"
                             f" ／ 層候補 {f.suspected_layer} ／ {f.reproducibility}")
    lines.append("")
    lines.append("## 記録（事実文）")
    for n in (meta.notes if meta else []):
        lines.append(f"- {n}")
    beh = oracle_notes.get("behavior") or {}
    if beh.get("unsupported_actions"):
        lines.append(f"- 画面に無い操作を選ぼうとした: {', '.join(beh['unsupported_actions'])}")
    if beh.get("help_no_hit"):
        lines.append(f"- 使い方の説明が見つからなかった部品: {', '.join(beh['help_no_hit'])}")
    doc = oracle_notes.get("document") or {}
    if doc.get("manual_refs_not_found"):
        lines.append(f"- マニュアルに見つからなかった節: {', '.join(doc['manual_refs_not_found'])}")
    obs = oracle_notes.get("observation") or {}
    if obs.get("note"):
        lines.append(f"- {obs['note']}")
    if obs.get("misconception_traces"):
        lines.append("- 砂場に記録された誤解（仕込んだ誤解との照合は人が行う）:")
        for r in obs["misconception_traces"]:
            lines.append(f"  - {r.get('username')}: {r.get('text')}")
    lines.append("- browser runner は未実施（API runner のみ）。")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    run_dir = Path(argv[0])
    if not run_dir.is_dir():
        run_dir = UXSIM_ROOT / argv[0]
    out = run_dir / "report.md"
    out.write_text(build_report(run_dir), encoding="utf-8")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
