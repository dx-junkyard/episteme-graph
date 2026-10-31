"""審判 D — 文書（マニュアルの節 ⇄ 観測）。

決定論の部分は「突き合わせの組」を作るだけ: friction の出たステップごとに、行為の affordance /
行為レジストリの manual / 経路の ``expects[].manual`` から ``docs/manual/**/*.md`` の節を引き、
「マニュアルはこう書いている / 観測はこうだった」の組にする（``document_pairs.jsonl``）。
組を LLM 審判（``judge_pair``）にかけると、実装の欠陥（doc_code_diff）か文書の欠陥
（doc_correction）かの仮説が付く。判定は人（PE3）。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from uxsim.actions import registry
from uxsim.config import REPO_ROOT
from uxsim.llm import PersonaLLM
from uxsim.oracles.findings import FindingFactory
from uxsim.schema import Finding, TranscriptStep

MANUAL_ROOT = REPO_ROOT / "docs" / "manual"
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*\{#([a-z0-9-]+)\}\s*$")
_ANY_HEADING = re.compile(r"^(#{1,6})\s")
SECTION_CHARS = 1200
FRICTIONS = ("confused", "misread", "blocked", "gave_up")


@lru_cache(maxsize=1)
def manual_sections(root: Path = MANUAL_ROOT) -> dict[str, str]:
    """``"<audience>/<file>#<anchor>"`` → 節の本文（見出しから同じか上位の次の見出しまで）。"""
    sections: dict[str, str] = {}
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = _HEADING.match(line)
            if not m:
                continue
            level = len(m.group(1))
            body = [m.group(2)]
            for nxt in lines[i + 1:]:
                h = _ANY_HEADING.match(nxt)
                if h and len(h.group(1)) <= level:
                    break
                body.append(nxt)
            sections[f"{rel}#{m.group(3)}"] = "\n".join(body).strip()
        sections.setdefault(rel, "\n".join(lines[:40]).strip())
    return sections


def manual_refs_for_step(step: TranscriptStep, expects: Optional[list[str]] = None) -> list[str]:
    refs: list[str] = []
    if "#" in step.affordance or step.affordance.endswith(".md"):
        refs.append(step.affordance)
    action = registry.get(step.action_id)
    if action and action.manual:
        refs.append(action.manual)
    refs.extend(expects or [])
    seen: list[str] = []
    for r in refs:
        if r and r not in seen:
            seen.append(r)
    return seen


def _expects_for(scenario_id: str) -> list[str]:
    if not scenario_id:
        return []
    try:
        from uxsim.runner.scenario import load_scenario

        return [str(e["manual"]) for e in load_scenario(scenario_id).expects if e.get("manual")]
    except Exception:  # noqa: BLE001 — 経路が読めなくても突き合わせは続ける
        return []


@dataclass
class DocPair:
    seq: int
    persona_id: str
    scenario_id: str
    action_id: str
    manual_ref: str
    manual_says: str
    observed: str
    reaction: str
    friction: str


def build_pairs(steps: list[TranscriptStep]) -> tuple[list[DocPair], list[str]]:
    """friction の出たステップの突き合わせの組と、引けなかった manual 参照の一覧。"""
    sections = manual_sections()
    pairs: list[DocPair] = []
    missing: set[str] = set()
    cache: dict[str, list[str]] = {}
    for s in steps:
        if s.think.friction not in FRICTIONS:
            continue
        expects = cache.setdefault(s.scenario_id, _expects_for(s.scenario_id))
        for ref in manual_refs_for_step(s, expects):
            text = sections.get(ref)
            if text is None:
                missing.add(ref)
                continue
            pairs.append(DocPair(seq=s.seq, persona_id=s.persona_id, scenario_id=s.scenario_id,
                                 action_id=s.action_id, manual_ref=ref, manual_says=text[:SECTION_CHARS],
                                 observed=s.observation[:1000], reaction=s.think.reaction, friction=s.think.friction))
    return pairs, sorted(missing)


JUDGE_SYSTEM = """あなたは利用者マニュアルと実際の画面を突き合わせる審判。
マニュアルの節と、利用者が実際に見た画面・反応を読み、食い違いがあれば仮説を述べる（断定しない）:
- doc_code_diff: マニュアルどおりに振る舞っていない（実装の欠陥の疑い）
- doc_correction: 実装は妥当だが、マニュアルの書き方が利用者を誤らせる（文書の欠陥の疑い）
- consistent: 食い違いは無い
点数・進捗率・督促を足すべきという提案はしない。"""
JUDGE_SCHEMA = json.dumps({"verdict": "doc_code_diff|doc_correction|consistent",
                           "hypothesis": "一文（マニュアルは X と書いているが観測は Y）"}, ensure_ascii=False)


def judge_pair(pair: DocPair, llm: PersonaLLM) -> Optional[dict]:
    try:
        out = llm.complete_json(JUDGE_SYSTEM, [{"role": "user", "content": json.dumps(asdict(pair), ensure_ascii=False)}],
                                JUDGE_SCHEMA)
    except Exception:  # noqa: BLE001
        return None
    return out if out.get("verdict") in ("doc_code_diff", "doc_correction", "consistent") else None


def check(steps: list[TranscriptStep], factory: FindingFactory, llm: Optional[PersonaLLM] = None,
          out_dir: Optional[Path] = None) -> tuple[list[Finding], dict]:
    pairs, missing = build_pairs(steps)
    if out_dir is not None:
        with (out_dir / "document_pairs.jsonl").open("w", encoding="utf-8") as fh:
            for p in pairs:
                fh.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
    findings: list[Finding] = []
    if llm is not None:
        by_seq = {s.seq: s for s in steps}
        for p in pairs:
            verdict = judge_pair(p, llm)
            if not verdict or verdict["verdict"] == "consistent":
                continue
            layers = ["docs"] if verdict["verdict"] == "doc_correction" else None
            findings.append(factory.make(
                oracle="D", severity="inconsistent", step=by_seq.get(p.seq), quote=p.manual_ref,
                hypothesis=f"仮説（{verdict['verdict']}）: {verdict.get('hypothesis', '')}", layers=layers))
    return findings, {"document_pairs": len(pairs), "manual_refs_not_found": missing}
