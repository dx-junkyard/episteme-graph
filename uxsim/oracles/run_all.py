"""審判を全部かける: ``python -m uxsim.oracles.run_all runs/<campaign>/<run_id> [--judge]``。

A・B・E（E6 確定文脈・E7 差分・E8 撤回の保持を含む）と対話の往復をまたぐ検査（``dialogue``）・検証シミュレーション層の検査（``simulation``）は決定論（LLM 0 回）。C・D の LLM 審判は ``--judge`` のときだけ（ペルソナと同じ
プロバイダ・別プロンプト）。出力: ``findings.jsonl`` / ``oracle_notes.json`` / ``document_pairs.jsonl``。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from uxsim.config import UXSIM_ROOT, get_settings
from uxsim.llm import PersonaLLM, make_live_llm
from uxsim.oracles import (atlas, behavior, contract, decision, dialogue, differential, document, doubt, observation,
                           principle, retraction, share, simulation, studio)
from uxsim.oracles.findings import FindingFactory, dedupe, load_meta, load_transcript, write_findings
from uxsim.schema import Finding


def run_oracles(run_dir: Path, *, judge_llm: Optional[PersonaLLM] = None, database_url: Optional[str] = None,
                engine=None) -> list[Finding]:
    """run ディレクトリの transcript に審判をかけ、束ねた発見を書き出して返す。"""
    steps = load_transcript(run_dir)
    meta = load_meta(run_dir)
    factory = FindingFactory(meta)
    notes: dict = {}
    found: list[Finding] = []
    brain_backed = str(getattr(meta, "persona_llm_provider", "") or get_settings().persona_llm_provider) == "mailbox"
    found += contract.check(steps, factory, brain_backed=brain_backed)
    found += principle.check(steps, factory)
    found += dialogue.check(steps, factory)
    found += simulation.check(steps, factory)
    # 第 25 周（空白の 4 面）: 面ごとの決定論の審判
    found += atlas.check(steps, factory)  # r25:atlas
    found += share.check(steps, factory)  # r25:share
    found += doubt.check(steps, factory)  # r25:doubt
    found += studio.check(steps, factory)  # r25:studio
    notes["studio"] = studio.summarize(steps)  # r25:studio
    notes["dialogue_ui_contract"] = dialogue.ui_contract_notes(steps)
    c, notes["behavior"] = behavior.check(steps, factory, judge_llm)
    found += c
    d, notes["document"] = document.check(steps, factory, judge_llm, out_dir=run_dir)
    found += d
    url = get_settings().sandbox_database_url if database_url is None else database_url
    e, notes["observation"] = observation.check(meta, factory, url, engine, steps=steps)
    found += e
    found += simulation.check_observation(meta, factory, url, engine, steps=steps)
    found += share.check_observation(meta, factory, url, engine, steps=steps)  # r25:share
    found += doubt.check_observation(meta, factory, url, engine, steps=steps)  # r25:doubt
    # 第 23 周: E6 確定文脈 / E7 差分（沈黙適応の仮説）/ E8 撤回の保持
    e6, notes["decision_context"] = decision.check(steps, meta, factory, url, engine)
    found += e6
    found += differential.check(steps, factory)
    e8, notes["retraction"] = retraction.check(steps, meta, factory, url, engine)
    found += e8
    findings = dedupe(found)
    write_findings(run_dir / "findings.jsonl", findings)
    (run_dir / "oracle_notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=2, default=str),
                                               encoding="utf-8")
    return findings


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="uxsim oracles")
    ap.add_argument("run_dir")
    ap.add_argument("--judge", action="store_true", help="C・D の LLM 審判を使う（外部 LLM を呼ぶ）")
    args = ap.parse_args(argv)
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        run_dir = UXSIM_ROOT / args.run_dir
    llm = None
    if args.judge:
        s = get_settings()
        llm = make_live_llm(s.persona_llm_provider, s.persona_llm_model, s.persona_llm_api_key)
    findings = run_oracles(run_dir, judge_llm=llm)
    print(f"findings: {len(findings)} 件 → {run_dir / 'findings.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
