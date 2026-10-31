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


def campaign_first_student(campaign_dir: Path) -> str:
    """campaign の定義（``campaigns/**/<campaign_dir の名前>.yaml``）の ``personas.students[0]``（第 25 周 第 7 周 studio H34）。

    発行前の時点では教員の run しか無く、run から学生を決められない（全組 unavailable になった）。定義が無ければ空。"""
    import yaml

    name = Path(campaign_dir).name
    for path in sorted((UXSIM_ROOT / "campaigns").rglob(f"{name}.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 — 読めない定義は無いのと同じ
            continue
        students = ((data.get("personas") or {}).get("students") or []) if isinstance(data, dict) else []
        if students:
            return str(students[0])
    return ""


#: 第 25 周 第 7 周 studio H34: 発行の前と後の cross を別ファイルに書く（後の審判が前の結果を上書きしない）
CROSS_PHASES = ("before", "after")


def cross_file_name(phase: str = "") -> str:
    return f"campaign_studio_cross.{phase}.jsonl" if phase in CROSS_PHASES else "campaign_studio_cross.jsonl"


#: 第 25 周 第 9 波（studio H40）: runner が発行の直前に run の中へ書いた学生の読み（``studio_exec.PREPUBLISH_FILE``）を
#: campaign の単位に集めたもの
PREPUBLISH_COLLECTED = "campaign_studio_cross.prepublish.jsonl"
CROSS_BEFORE_EMPTY_NOTE = ("発行の前の cross に学生 API の組が無い — 比較が成り立たない（前提: この時点で教員の保存が無い）。"
                           "発行の直前の学生の読みは各 run の campaign_studio_cross.before.jsonl（runner が書く）を見る")


def collect_prepublish_rows(campaign_dir: Path) -> list[dict]:
    """各 run の ``campaign_studio_cross.before.jsonl``（発行の直前に runner が書いた学生の読み）を集めて
    ``campaign_studio_cross.prepublish.jsonl`` に書く（無ければ書かない）。"""
    rows: list[dict] = []
    for d in sorted(Path(campaign_dir).iterdir()):
        f = d / "campaign_studio_cross.before.jsonl"
        if not (d.is_dir() and f.is_file()):
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(dict(json.loads(line), source_run=d.name))
                except ValueError:
                    continue
    if rows:
        with (Path(campaign_dir) / PREPUBLISH_COLLECTED).open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


def _student_total_fetcher(campaign_dir: Path, student: str = ""):
    """学生の講義 API（``GET /api/learning/lecture/courses/{c}/topics/{t}/sequence``）の ``total_slides`` を引く関数。

    第 25 周 第 6 周 H32: 読み取りだけ・1 トピック 1 回。学生は ``--student`` か campaign に最初に現れた ``st-``。
    ログインできなければ全組 None（引けなかった事実として残す）。"""
    from uxsim.runner.api import persona_username
    from uxsim.runner.client import EpistemeClient

    if not student:
        student = campaign_first_student(campaign_dir)
    if not student:
        runs = {d.name: load_transcript(d) for d in sorted(campaign_dir.iterdir())
                if d.is_dir() and (d / "transcript.jsonl").is_file()}
        student = studio.student_persona_of(runs)
    s = get_settings()
    client = EpistemeClient(s.base_url)
    ok = bool(student) and client.login(persona_username(student), s.persona_password)[0]

    def fetch(course_id: str, topic_id: str):
        if not ok:
            return None
        status, body, _ = client.call("GET", f"/api/learning/lecture/courses/{course_id}/topics/{topic_id}/sequence")
        if status != 200 or not isinstance(body, dict):
            return None
        total = body.get("total_slides")
        return total if isinstance(total, int) else None

    return fetch


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="uxsim oracles")
    ap.add_argument("run_dir")
    ap.add_argument("--judge", action="store_true", help="C・D の LLM 審判を使う（外部 LLM を呼ぶ）")
    ap.add_argument("--campaign", action="store_true",
                    help="run_dir を campaign のディレクトリとして、周をまたぐ突き合わせ（面 D の X1）だけをかける")
    ap.add_argument("--student-api", action="store_true",
                    help="--campaign と一緒に: 教員が保存したトピックごとに学生の講義 API（total_slides）を直接引いて比べる"
                         "（読み取りだけ・第 25 周 第 6 周 H32）")
    ap.add_argument("--student", default="",
                    help="--student-api で使う学生ペルソナ（既定: campaign の定義の personas.students[0] → run に最初に現れた st-）")
    ap.add_argument("--phase", default="", choices=("", *CROSS_PHASES),
                    help="--campaign と一緒に: 発行の前（before）/ 後（after）の cross を別ファイルに書く（上書きしない — studio H34）")
    args = ap.parse_args(argv)
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        run_dir = UXSIM_ROOT / args.run_dir
    if args.campaign:
        fetch = _student_total_fetcher(run_dir, args.student) if args.student_api else None
        rows = studio.check_campaign_dir(run_dir, fetch_total=fetch, out_name=cross_file_name(args.phase))
        print(f"studio cross: {len(rows)} 組（うち枚数の食い違い {sum(1 for r in rows if r['mismatch'])}・"
              f"保存の案だけの組 {sum(1 for r in rows if r.get('draft_only'))}・"
              f"学生 API を引けなかった組 {sum(1 for r in rows if r.get('unavailable'))}）→ "
              f"{run_dir / cross_file_name(args.phase)}")
        if args.phase == "before" and args.student_api and not any(r.get("kind") == "student_api" for r in rows):
            print(CROSS_BEFORE_EMPTY_NOTE)
        pre = collect_prepublish_rows(run_dir)
        if pre:
            print(f"studio prepublish: 発行の直前の学生の読み {len(pre)} 組（うち枚数の食い違い "
                  f"{sum(1 for r in pre if r.get('mismatch'))}）→ {run_dir / PREPUBLISH_COLLECTED}")
        return 0
    llm = None
    if args.judge:
        s = get_settings()
        llm = make_live_llm(s.persona_llm_provider, s.persona_llm_model, s.persona_llm_api_key)
    findings = run_oracles(run_dir, judge_llm=llm)
    print(f"findings: {len(findings)} 件 → {run_dir / 'findings.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
