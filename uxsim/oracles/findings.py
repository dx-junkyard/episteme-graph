"""発見レコード（Finding）の生成・指紋・重複束ね・書き出し。

指紋 = sha256(oracle | screen | affordance | 正規化した仮説)。数値は根拠（evidence）にだけ置き、
点数にはしない（PE7・PE10）。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable, Optional

from uxsim.schema import Evidence, Finding, RunMeta, TranscriptStep

# 行為 id の接頭辞 → docs/issue_knowledge/layers.md の語彙（最長一致）
_LAYER_BY_PREFIX: dict[str, list[str]] = {
    "auth.": ["auth_visibility"],
    "learning.course.": ["auth_visibility"],
    "learning.topic.": ["learner_experience_b"],
    "learning.chat.usage_help": ["help_kb"],
    "learning.chat.backstage": ["structure_descent"],
    "learning.chat.": ["rag_chat"],
    "learning.check.": ["learner_experience_b"],
    "learning.discuss.": ["discuss"],
    "learning.cycle.": ["understanding_cycle"],
    "learning.tension.": ["learner_experience_b"],
    "learning.anchors.": ["learner_experience_b"],
    "learning.reconstruction.": ["reconstruction_r"],
    "learning.symbol.": ["concept_registry"],
    "learning.descent.": ["structure_descent"],
    "learning.element.": ["learner_experience_b"],
    "learning.source_chunk.": ["rag_chat"],
    "learning.records.": ["trace_registry"],
    "learning.personal_network.": ["personal_network"],
    "learning.atlas.": ["field_atlas_s"],
    "learning.landscape.": ["knowledge_landscape"],
    "learning.corpus.": ["corpus_roaming"],
    "learning.lecture.": ["lecture_player"],
    "learning.help.": ["help_kb"],
    "learning.progress": ["learner_experience_b"],
    "learning.voice.": ["tts_voice"],
    "admin.next_steps.": ["guidance_g"],
    "admin.materials.upload_url": ["url_material_fetch"],
    "admin.materials.": ["pipeline_a"],
    "admin.graph_review.": ["graph_review"],
    "admin.course_builder.": ["course_builder"],
    "admin.course.": ["auth_visibility"],
    "admin.release_review.": ["release_review"],
    "admin.atlas_binding.": ["field_atlas_s"],
    "admin.lecture_studio.": ["lecture_studio"],
    "admin.users.": ["account_lifecycle"],
    "admin.groups.": ["auth_visibility"],
    "admin.copilot.": ["admin_copilot"],
    "admin.help.": ["help_kb"],
    "admin.discuss_opening_review.": ["discuss"],
}


def layers_for_action(action_id: str, screen: str = "") -> list[str]:
    """行為から疑わしい層の候補を返す（場所の記録・仮説）。"""
    aid = action_id.split(":", 1)[-1]
    best = max((p for p in _LAYER_BY_PREFIX if aid.startswith(p)), key=len, default="")
    layers = list(_LAYER_BY_PREFIX.get(best, []))
    ui = "frontend_admin_ui" if (screen == "admin" or aid.startswith("admin.")) else "frontend_learning_ui"
    if ui not in layers:
        layers.append(ui)
    return layers[:2]


_WS = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")
_IDS = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{16,}\b", re.IGNORECASE)
_QUOTED = re.compile(r"「[^」]*」|\"[^\"]*\"")


def normalize_hypothesis(text: str) -> str:
    """指紋用の正規化（引用・数字・空白の揺れを畳む）。"""
    s = _QUOTED.sub("「…」", text or "")
    s = _IDS.sub("<id>", s)
    s = _DIGITS.sub("#", s)
    return _WS.sub(" ", s).strip().lower()


def fingerprint(oracle: str, screen: str, affordance: str, hypothesis: str) -> str:
    raw = "|".join([oracle, screen or "", affordance or "", normalize_hypothesis(hypothesis)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class FindingFactory:
    """1 run 内の Finding を作る（連番の finding_id を振る）。"""

    def __init__(self, meta: Optional[RunMeta] = None) -> None:
        self.meta = meta
        self._seq = 0

    def make(self, *, oracle: str, severity: str, hypothesis: str, step: Optional[TranscriptStep] = None,
             screen: str = "", affordance: str = "", quote: str = "", server_rows: Optional[dict] = None,
             layers: Optional[list[str]] = None, steps: Optional[list[int]] = None) -> Finding:
        self._seq += 1
        run_id = self.meta.run_id if self.meta else ""
        scr = screen or (step.screen if step else "")
        aff = affordance or (step.affordance if step else "")
        return Finding(
            finding_id=f"f-{run_id}-{self._seq:04d}",
            fingerprint=fingerprint(oracle, scr, aff, hypothesis),
            oracle=oracle,  # type: ignore[arg-type]
            severity_label=severity,  # type: ignore[arg-type]
            reproducibility="reproduced_in_replay" if (self.meta and self.meta.replay_of) else "observed_once",
            persona_id=step.persona_id if step else "",
            scenario_id=step.scenario_id if step else "",
            screen=scr, affordance=aff, hypothesis=hypothesis,
            evidence=Evidence(transcript_steps=steps if steps is not None else ([step.seq] if step else []),
                              quote=quote[:400], server_rows=server_rows or {}),
            suspected_layer=layers if layers is not None else (layers_for_action(step.action_id, scr) if step else []),
            run_id=run_id, campaign_id=self.meta.campaign_id if self.meta else "",
        )


def dedupe(findings: Iterable[Finding]) -> list[Finding]:
    """同じ指紋を 1 件に束ねる（根拠のステップと引用を合わせる）。"""
    merged: dict[str, Finding] = {}
    for f in findings:
        if f.fingerprint not in merged:
            merged[f.fingerprint] = f.model_copy(deep=True)
            continue
        keep = merged[f.fingerprint]
        steps = sorted(set(keep.evidence.transcript_steps) | set(f.evidence.transcript_steps))
        keep.evidence.transcript_steps = steps
        for k, v in f.evidence.server_rows.items():
            keep.evidence.server_rows.setdefault(k, v)
        if f.persona_id and f.persona_id not in keep.persona_id.split(","):
            keep.persona_id = ",".join(x for x in (keep.persona_id, f.persona_id) if x)
    return list(merged.values())


def load_transcript(run_dir: Path) -> list[TranscriptStep]:
    path = run_dir / "transcript.jsonl"
    if not path.is_file():
        return []
    steps = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            steps.append(TranscriptStep.model_validate_json(line))
    return sorted(steps, key=lambda s: s.seq)


def load_meta(run_dir: Path) -> Optional[RunMeta]:
    path = run_dir / "meta.json"
    return RunMeta.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None


def write_findings(path: Path, findings: Iterable[Finding]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for f in findings:
            fh.write(f.model_dump_json() + "\n")


def load_findings(path: Path) -> list[Finding]:
    if not path.is_file():
        return []
    return [Finding.model_validate_json(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def excerpt_json(excerpt: str):
    """``response_excerpt``（先頭 2000 字）を JSON として読む。切れていれば None。"""
    try:
        return json.loads(excerpt)
    except (json.JSONDecodeError, TypeError):
        return None
