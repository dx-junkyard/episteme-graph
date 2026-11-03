"""着手の地図の**下書き**（改善サイクル §2.2 の形。点数・順位にしない）。

``python -m uxsim.report.map_draft runs/<campaign>/<run_id>`` → ``map_draft.md``。
6 観点は事実文で書き、ハーネスに分からない欄は「（要記入）」の穴のまま残す（選ぶのは人）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from uxsim.config import UXSIM_ROOT
from uxsim.oracles.findings import load_findings, load_meta

VIEWPOINTS = ("目的への寄与", "利用状況での必要性", "放置時に起きること", "負担", "前提関係", "まだ分かっていないこと")


def build_map_draft(run_dir: Path) -> str:
    meta = load_meta(run_dir)
    findings = [f for f in load_findings(run_dir / "findings.jsonl") if f.status == "open"]
    campaign = meta.campaign_id if meta else run_dir.parent.name
    lines = ["# 着手の地図（下書き）", "",
             f"この波が前提とする利用状況: （要記入 — 観測の範囲は砂場で campaign {campaign} を走らせた"
             f"{'（snapshot ' + meta.snapshot + '）' if meta and meta.snapshot else ''}ペルソナの通し受講）", ""]
    if not findings:
        lines.append("未処理の発見はありません。")
    for f in findings:
        facts = {
            "目的への寄与": "（要記入）",
            "利用状況での必要性": f"ペルソナ {f.persona_id or '—'} が経路 {f.scenario_id or '—'} で当たった。",
            "放置時に起きること": f"観測: {f.hypothesis}（段階: {f.severity_label}）。",
            "負担": "（要記入）",
            "前提関係": f"層の候補: {', '.join(f.suspected_layer) or '—'}（場所の記録であって原因ではない）。",
            "まだ分かっていないこと": f"原因は仮説。再現性は {f.reproducibility}。transcript のステップ "
                                   f"{f.evidence.transcript_steps} を読んでいない。",
        }
        lines.append(f"## {f.finding_id} — {f.hypothesis}")
        for v in VIEWPOINTS:
            lines.append(f"- {v}: {facts[v]}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    run_dir = Path(argv[0])
    if not run_dir.is_dir():
        run_dir = UXSIM_ROOT / argv[0]
    out = run_dir / "map_draft.md"
    out.write_text(build_map_draft(run_dir), encoding="utf-8")
    print(f"map draft: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
