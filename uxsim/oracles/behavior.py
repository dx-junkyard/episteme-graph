"""審判 C — 行動（決定論の前処理 + 任意の LLM 審判フック）。

前処理（決定論）で候補を拾う:
- think-aloud の friction が confused / blocked / gave_up / misread
- 同じ行為の 3 回連続失敗（HTTP 4xx/5xx・接続断。precondition だけで終わった手は数えない — runner の
  状態の穴なので ``dialogue.py`` の e がハーネスの発見として 1 件にまとめる）

指紋は原因で取る（``cause_key`` = 詰まり方 × 行為）。同じ行為で同じ詰まり方をしたペルソナは 1 件に束ね、
``evidence.server_rows["occurrences"]`` に全員のペルソナ・ステップ・反応を並べる（仮説の言い回しは
``hypotheses`` に残す）。
- 行為レジストリに無い操作を選ぼうとした（unsupported）
- 使い方の検索が空振りした（help no_hit — マニュアルの穴）

``judge(step_group, llm)`` は候補に「製品の欠陥 / ペルソナの前提知識由来 / 原則どおりの拒否」の
3 択で仮説を付ける（欠陥と断定しない — PE3）。LLM を渡さなければ呼ばない。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from uxsim.llm import PersonaLLM
from uxsim.oracles.findings import FindingFactory
from uxsim.schema import Finding, TranscriptStep

FRICTION_SEVERITY = {"blocked": "blocked", "gave_up": "blocked", "confused": "confused", "misread": "confused"}
CONSECUTIVE_FAILURES = 3
VERDICTS = ("product_defect", "persona_knowledge", "principled_refusal")

JUDGE_SYSTEM = """あなたは学習システムの UX 審判。ペルソナの通し受講の記録から、利用者が詰まった箇所を読む。
次の 3 つのどれに当たるかを仮説として述べる（断定しない）:
- product_defect: 製品の欠陥（表示・導線・応答の不備）
- persona_knowledge: ペルソナの前提知識の不足が主因
- principled_refusal: 製品の原則どおりの拒否・縮退（点数を出さない、督促しない等）で、欠陥ではない
「もっと点数を出すべき」「進捗率を出すべき」「督促すべき」という改善案は原則違反なので出さない。"""
JUDGE_SCHEMA = json.dumps({"verdict": "|".join(VERDICTS), "hypothesis": "一文の仮説（原因を主語に）",
                           "evidence_steps": [0]}, ensure_ascii=False)


@dataclass
class StepGroup:
    """審判にかける候補（連続したステップのまとまり）。"""

    reason: str
    steps: list[TranscriptStep] = field(default_factory=list)


def _precondition_only(step: TranscriptStep) -> bool:
    """HTTP を出さず precondition で終わっただけのステップ（runner の状態の穴。審判 dialogue の e が見る）。"""
    return bool(step.http) and all((t.error or "").startswith("precondition:") for t in step.http)


def _failed(step: TranscriptStep) -> bool:
    if step.action_id.startswith("unsupported"):
        return True
    if _precondition_only(step):
        return False  # 製品の失敗ではない（連鎖はハーネスの発見として dialogue.check が 1 件にまとめる）
    return any(t.status is None or (t.status or 0) >= 400 for t in step.http)


TRACE_CHAIN = ("learning.element.context", "learning.component.context")


def _trace_break(step: TranscriptStep) -> str:
    """辿りの連鎖の途切れ方（``404`` / ``missing`` / 空文字 = 途切れていない）。"""
    for t in step.http:
        if (t.error or "").startswith("precondition:"):
            return "missing"
        if t.status == 404:
            return "404"
        if t.status == 200 and '"missing"' in (t.response_excerpt or ""):
            return "missing"
    return ""


def prefilter(steps: list[TranscriptStep]) -> tuple[list[StepGroup], list[str], list[str]]:
    """候補グループ・unsupported の行為一覧・help no_hit の一覧を返す。"""
    groups: list[StepGroup] = []
    unsupported: list[str] = []
    no_hits: list[str] = []
    by_persona: dict[str, list[TranscriptStep]] = {}
    for s in steps:
        by_persona.setdefault(f"{s.persona_id}#{s.session}", []).append(s)
        if s.think.friction in FRICTION_SEVERITY:
            groups.append(StepGroup(reason=f"friction:{s.think.friction}", steps=[s]))
        if s.action_id.startswith("unsupported:"):
            unsupported.append(s.action_id.split(":", 1)[1])
        if s.action_id.endswith("help.inspect") and any(t.path.endswith("ui-anchor-events") for t in s.http):
            no_hits.append(str(s.args.get("anchor_id") or ""))
    for seq in by_persona.values():
        started = False
        for s in seq:
            if not s.action_id.startswith(TRACE_CHAIN):
                continue
            how = _trace_break(s)
            if how and (started or s.action_id.startswith(TRACE_CHAIN[1])):
                groups.append(StepGroup(reason=f"trace_break:{how}", steps=[s]))
            started = started or s.action_id.startswith(TRACE_CHAIN[0])
    for seq in by_persona.values():
        run: list[TranscriptStep] = []
        for s in seq:
            if run and s.action_id == run[-1].action_id and _failed(s):
                run.append(s)
            else:
                if len(run) >= CONSECUTIVE_FAILURES:
                    groups.append(StepGroup(reason="consecutive_failures", steps=list(run)))
                run = [s] if _failed(s) else []
        if len(run) >= CONSECUTIVE_FAILURES:
            groups.append(StepGroup(reason="consecutive_failures", steps=list(run)))
    return groups, sorted(set(unsupported)), sorted(set(n for n in no_hits if n))


def _deterministic_hypothesis(group: StepGroup) -> str:
    s = group.steps[0]
    if group.reason == "consecutive_failures":
        return f"{s.action_id} が続けて失敗し、利用者が先へ進めない"
    if group.reason.startswith("trace_break:"):
        return f"要素から部品への辿りが {s.action_id} で途切れた（{group.reason.split(':', 1)[1]}）"
    what = {"friction:blocked": "先へ進めなくなった", "friction:gave_up": "諦めた",
            "friction:confused": "画面の意味が分からなくなった", "friction:misread": "画面を読み違えた"}
    return f"{s.action_id} の後、利用者が{what.get(group.reason, '詰まった')}"


def cause_key(group: StepGroup) -> str:
    """原因の鍵（指紋の材料）。同じ行為 × 同じ詰まり方はペルソナをまたいで 1 件に束ねる。

    仮説の文（LLM 審判の言い回しはペルソナごとに揺れる）ではなく、行為と詰まり方で束ねる。
    """
    return f"cause|{group.reason}|{group.steps[0].action_id}"


def judge(group: StepGroup, llm: PersonaLLM) -> Optional[dict]:
    """LLM 審判で 3 択の仮説を付ける（失敗したら None）。"""
    lines = []
    for s in group.steps:
        lines.append(json.dumps({"seq": s.seq, "action": s.action_id, "intent": s.think.intent,
                                 "expectation": s.think.expectation, "observation": s.observation[:800],
                                 "reaction": s.think.reaction, "friction": s.think.friction,
                                 "gave_up_reason": s.think.gave_up_reason}, ensure_ascii=False))
    try:
        out = llm.complete_json(JUDGE_SYSTEM, [{"role": "user", "content": "\n".join(lines)}], JUDGE_SCHEMA)
    except Exception:  # noqa: BLE001 — 審判の失敗は決定論の仮説で代える
        return None
    if out.get("verdict") not in VERDICTS:
        return None
    return out


def check(steps: list[TranscriptStep], factory: FindingFactory, llm: Optional[PersonaLLM] = None
          ) -> tuple[list[Finding], dict]:
    """行動の発見と、レポート用の一覧（unsupported / no_hit）を返す。"""
    groups, unsupported, no_hits = prefilter(steps)
    out: list[Finding] = []
    for g in groups:
        s = g.steps[0]
        severity = ("blocked" if g.reason == "consecutive_failures" or g.reason.startswith("trace_break:")
                    else FRICTION_SEVERITY[g.reason.split(":", 1)[1]])
        hypothesis = _deterministic_hypothesis(g)
        verdict = judge(g, llm) if llm is not None else None
        if verdict is not None:
            if verdict["verdict"] == "principled_refusal":
                continue  # 原則どおりの振る舞いは欠陥にしない（PE10）
            hypothesis = f"仮説（{verdict['verdict']}）: {verdict.get('hypothesis', '')}".strip()
        out.append(factory.make(oracle="C", severity=severity, step=s, hypothesis=hypothesis,
                                quote=s.think.gave_up_reason or s.think.reaction,
                                steps=[x.seq for x in g.steps], fingerprint_key=cause_key(g),
                                server_rows={"occurrences": [{"persona_id": s.persona_id, "session": s.session,
                                                              "steps": [x.seq for x in g.steps],
                                                              "reaction": (s.think.gave_up_reason
                                                                           or s.think.reaction)[:200]}],
                                             "hypotheses": [hypothesis]}))
    for aid in unsupported:
        out.append(factory.make(oracle="C", severity="confused", screen="", affordance="",
                                hypothesis=f"利用者が画面に無い操作（{aid}）をしようとした", layers=[]))
    for anchor in no_hits:
        out.append(factory.make(oracle="C", severity="confused", affordance=anchor,
                                hypothesis=f"使い方の説明が見つからない部品がある（{anchor}）", layers=["help_kb", "docs"]))
    return out, {"unsupported_actions": unsupported, "help_no_hit": no_hits}
