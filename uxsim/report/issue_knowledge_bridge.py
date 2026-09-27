"""発見 → 課題ナレッジの**候補**エントリ（``docs/issue_knowledge/TEMPLATE.md`` の形）。

``docs/issue_knowledge/`` には書かない。``runs/.../ik_candidates/IK-XXXX-<slug>.md`` に置き、番号は人が
``backend/scripts/issue_knowledge_index.py --new <slug>`` で振る（PE3 — 分類の確定は人）。

``python -m uxsim.report.issue_knowledge_bridge runs/<campaign>/<run_id> [--finding f-...]``
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from uxsim.actions import registry
from uxsim.config import REPO_ROOT, UXSIM_ROOT
from uxsim.oracles.findings import load_findings, load_meta
from uxsim.schema import Finding, RunMeta

DESIGN_DOC = "docs/architecture/persona_enactment_testing_design.md"
LAYERS_DOC = REPO_ROOT / "docs" / "issue_knowledge" / "layers.md"
AXES = ("processing", "structure", "connection", "governance")


def layer_vocabulary() -> set[str]:
    if not LAYERS_DOC.is_file():
        return set()
    return set(re.findall(r"^\| `([a-z_]+)`", LAYERS_DOC.read_text(encoding="utf-8"), re.MULTILINE))


def slug_for(finding: Finding) -> str:
    """英小文字とハイフンの slug（審判 + 指紋の先頭。仮題なので人が付け替えてよい）。"""
    return f"uxsim-{finding.oracle.lower()}-{finding.fingerprint[:8]}"


def _q(text: str) -> str:
    """YAML の単一行スカラー（二重引用符）。"""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _realizing(finding: Finding) -> str:
    aff = finding.affordance
    label = next((a.label for a in registry.REGISTRY.values() if a.affordance == aff and aff), "")
    return f"ペルソナの通し受講で「{label}」を行う" if label else "ペルソナの通し受講で画面を操作する"


def candidate_entry(finding: Finding, meta: Optional[RunMeta], run_dir: Path, today: Optional[str] = None) -> str:
    """候補エントリの本文（front-matter + 4 見出し）。"""
    today = today or date.today().isoformat()
    vocab = layer_vocabulary()
    layers = [l for l in finding.suspected_layer if not vocab or l in vocab] or ["frontend_learning_ui"]
    try:
        transcript = (run_dir / "transcript.jsonl").resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        transcript = str(run_dir / "transcript.jsonl")
    campaign = finding.campaign_id or (meta.campaign_id if meta else "")
    replay_cmd = (f"backend/.venv/bin/python -m uxsim.runner.api <campaign.yaml> --replay {finding.run_id}"
                  + (f" --persona {finding.persona_id.split(',')[0]}" if finding.persona_id else "")
                  + (f" --scenario {finding.scenario_id}" if finding.scenario_id else ""))
    fm = [
        "---",
        "id: IK-XXXX  # 番号は backend/scripts/issue_knowledge_index.py --new で振る",
        f"title: {_q(finding.hypothesis)}",
        "status: open",
        f"recorded_at: {today}",
        "resolved_at: null",
        "sources:",
        f"  - {DESIGN_DOC} §9",
        f"  # run の transcript（git 管理外）: {transcript}",
        f"  # campaign: {campaign} ／ finding: {finding.finding_id}",
        "feature_context:",
        f"  realizing: {_q(_realizing(finding))}",
        f"  layers: [{', '.join(layers)}]",
        "classification:",
        "  axes:  # 人が座標を起こす。全軸 unknown のままでは課題ナレッジの検査を通らない（意図して人に残す）",
        *[f"    {a}: [unknown]" for a in AXES],
        "  axis_confidence:",
        *[f"    {a}: low" for a in AXES],
        "  proposals: []",
        "  cause_status: hypothesis",
        "  review: candidate",
        "  reviewed_by: null",
        "  reviewed_at: null",
        "  basis: >-",
        f"    仮説: 審判 {finding.oracle}（{finding.severity_label}）の観測から起こした候補で、原因の性質はまだ見ていない。"
        " transcript の該当ステップを読み、4 軸のどの要素が原因に当たるかを確かめると確定できる。",
        "generalization:",
        "  level: instance",
        "  general_form: （要記入 — 機能名・層名を剥がした一文）",
        "pattern: unknown  # 人が dictionary.md の型（#### 見出し）から選ぶ。unknown のままでは検査を通らない",
        "discovery:",
        "  perspective: [reproduction, symptom_report]",
        "  note: ペルソナ通し受講で観測。",
        "resolution:",
        "  perspective: [pending]",
        "  note: 未着手。",
        "  landed_in: []",
        "related: []",
        "view_of: []",
        "history: []",
        "---",
    ]
    body = [
        "",
        "## 課題",
        "",
        f"症状（観測）: {finding.hypothesis}",
        "",
        f"- 審判: {finding.oracle} ／ 段階: {finding.severity_label} ／ 再現性: {finding.reproducibility}",
        f"- ペルソナ: {finding.persona_id or '—'} ／ 経路: {finding.scenario_id or '—'} ／ 画面: {finding.screen or '—'}"
        f" ／ 部品: {finding.affordance or '—'}",
        f"- transcript のステップ: {finding.evidence.transcript_steps}",
        f"- 引用: {finding.evidence.quote or '—'}",
        "",
        "原因は仮説のまま（症状から遡った原因はまだ書いていない）。",
        "",
        "## 発見の観点",
        "",
        "ペルソナ通し受講（uxsim）で観測した。再現手順:",
        "",
        f"```\n{replay_cmd}\n```",
        "",
        "## 解決の観点",
        "",
        "未解決。transcript を読み、製品の欠陥か・ペルソナの前提知識由来か・原則どおりの拒否かを人が判断する。",
        "",
        "## 一般化",
        "",
        "（要記入）",
        "",
    ]
    return "\n".join(fm + body)


def write_candidates(run_dir: Path, finding_ids: Optional[list[str]] = None) -> list[Path]:
    meta = load_meta(run_dir)
    out_dir = run_dir / "ik_candidates"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for f in load_findings(run_dir / "findings.jsonl"):
        if f.status != "open" or (finding_ids and f.finding_id not in finding_ids):
            continue
        path = out_dir / f"IK-XXXX-{slug_for(f)}.md"
        path.write_text(candidate_entry(f, meta, run_dir), encoding="utf-8")
        written.append(path)
    return written


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    run_dir = Path(argv[0])
    if not run_dir.is_dir():
        run_dir = UXSIM_ROOT / argv[0]
    ids = [a.split("=", 1)[1] for a in argv[1:] if a.startswith("--finding=")]
    paths = write_candidates(run_dir, ids or None)
    for p in paths:
        slug = p.stem.split("-", 2)[2]
        print(f"{p}\n  → 番号を振る: backend/.venv/bin/python backend/scripts/issue_knowledge_index.py --new {slug}"
              " で雛形を作り、この候補の内容を写す（返却されたファイル名を使う）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
