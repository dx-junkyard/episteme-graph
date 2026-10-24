"""API runner — campaign を実行して run ディレクトリに全部残す（PE5・PE7）。

使い方（リポジトリルートで）::

    backend/.venv/bin/python -m uxsim.runner.api campaigns/astrophysics/c-first-course.yaml \\
        [--replay RUN_ID] [--persona st-01-...] [--scenario s-...] [--max-steps N] [--no-oracles]

run ディレクトリ: ``<runs_dir>/<campaign_id>/<run_id>/``
  meta.json / transcript.jsonl / cache/persona_llm.jsonl / logs/run.log / logs/setup.jsonl
"""
from __future__ import annotations

import os

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from uxsim.actions import registry
from uxsim.config import REPO_ROOT, UXSIM_ROOT, Settings, get_settings
from uxsim.llm import (CachingPersonaLLM, PersonaLLM, ReplayPersonaLLM, ReplayThenLivePersonaLLM,
                       load_cache_jsonl, make_live_llm)
from uxsim.persona.agent import PersonaAgent, StepDecision
from uxsim.persona.compose import PersonaSpec, compose_persona, course_brief
from uxsim.runner.actions_exec import PINNED_MISMATCH_MARK, execute, prefetch_courses, runner_note
from uxsim.runner.client import EpistemeClient
from uxsim.runner.scenario import (SKIP_ACTION_ID, Option, Scenario, ScenarioParams, StepSpec, evaluate_until,
                                   load_params, load_scenario)
from uxsim.runner.state import PersonaSession, persona_uses_voice, project_observation
from uxsim.schema import RunMeta, ThinkAloud, TranscriptStep, now_iso

BUDGET_KEYS = ("product_llm_calls", "persona_llm_calls", "wall_clock_minutes")


class BudgetExhausted(Exception):
    """campaign の予算に達した（PE4）。"""


# ----------------------------------------------------------------------------
# 補助
# ----------------------------------------------------------------------------

def read_git_commit(repo: Path = REPO_ROOT) -> str:
    """``.git/HEAD`` を読むだけで commit を得る（git コマンドを使わない）。"""
    head = repo / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
    except OSError:
        return ""
    if not ref.startswith("ref:"):
        return ref
    name = ref.split(" ", 1)[1].strip()
    loose = repo / ".git" / name
    if loose.is_file():
        return loose.read_text().strip()
    packed = repo / ".git" / "packed-refs"
    if packed.is_file():
        for line in packed.read_text().splitlines():
            if line.endswith(" " + name):
                return line.split(" ", 1)[0]
    return ""


def load_campaign(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    missing = [k for k in ("id", "domain") if not data.get(k)]
    if missing:
        raise ValueError(f"campaign に必須キーがありません: {missing}")
    budget = data.get("budget") or {}
    lacking = [k for k in BUDGET_KEYS if k not in budget]
    if lacking:
        raise ValueError(f"campaign の budget に必須キーがありません: {lacking}")
    return data


def resolve_campaign_path(arg: str) -> Path:
    p = Path(arg)
    for cand in (p, UXSIM_ROOT / p, REPO_ROOT / p):
        if cand.is_file():
            return cand.resolve()
    raise FileNotFoundError(arg)


def persona_fields(spec: PersonaSpec, root: Path = UXSIM_ROOT) -> dict[str, Any]:
    """``{{persona.<欄>}}`` が引く値（ペルソナファイルの欄 + 合成値。course_brief は依頼文の本文）。"""
    fields: dict[str, Any] = dict(spec.raw)
    fields.update({"display_name": spec.display_name, "language": spec.language, "archetype": spec.archetype_id,
                   "goal_text": spec.goals[0]["text"] if spec.goals else ""})
    brief = spec.raw.get("course_brief")
    if isinstance(brief, str) and brief:
        fields["course_brief"] = (course_brief(spec.domain, brief, root) or brief).strip()
    return fields


def persona_username(persona_id: str) -> str:
    return "uxsim_" + persona_id.replace("-", "_")


@dataclass
class Budget:
    """予算の監視。製品側の LLM 呼び出しは llm_cost=product の行為の実行回数で近似する。"""

    limits: dict[str, int]
    started: float = field(default_factory=time.monotonic)
    product_calls: int = 0
    llm: Optional[PersonaLLM] = None

    def spent(self) -> dict[str, int]:
        return {"product_llm_calls": self.product_calls,
                "persona_llm_calls": int(getattr(self.llm, "calls", 0) or 0),
                "wall_clock_minutes": int((time.monotonic() - self.started) // 60)}

    def exhausted(self) -> str:
        spent = self.spent()
        for k in BUDGET_KEYS:
            limit = self.limits.get(k)
            if limit is not None and spent[k] >= int(limit):
                return k
        return ""


class RunWriter:
    """run ディレクトリへの書き出し。"""

    def __init__(self, run_dir: Path) -> None:
        self.dir = run_dir
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)
        (run_dir / "cache").mkdir(parents=True, exist_ok=True)
        self.transcript = run_dir / "transcript.jsonl"
        self.seq = 0

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def write_step(self, step: TranscriptStep) -> None:
        with self.transcript.open("a", encoding="utf-8") as fh:
            fh.write(step.model_dump_json() + "\n")

    def log(self, message: str) -> None:
        with (self.dir / "logs" / "run.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {message}\n")

    def setup_log(self, row: dict) -> None:
        with (self.dir / "logs" / "setup.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def write_meta(self, meta: RunMeta) -> None:
        (self.dir / "meta.json").write_text(meta.model_dump_json(indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------
# 1 経路の実行
# ----------------------------------------------------------------------------

@dataclass
class ScenarioOutcome:
    steps: int = 0
    gave_up: bool = False
    gave_up_reason: str = ""
    stopped: str = ""


class _Stop(Exception):
    """経路の stop_when に当たった。"""


STOP_NOTE_MARK = "scene_stopped"


def stop_note(reason: str, chosen: str = "") -> str:
    """場面が止まったことを transcript に残す runner の注記（第 25 周 H7）。

    頭脳ノートが、実行されなかった行為の画面について所見を書いていた（te-04 / st-05）。止まった理由と、
    頭脳が選んだが実行していない行為を事実として残す。
    """
    if chosen and chosen not in ("none", SKIP_ACTION_ID):
        tail = f"頭脳が選んだ次の行為 {chosen} は実行していない（この後の画面は存在しない）"
    else:
        tail = "頭脳には次の行為を選ばせていない"
    return f"runner: {STOP_NOTE_MARK}: 場面はこの操作で終わった（停止条件: {reason}）— {tail}"


class _UntilMet(Exception):
    """repeat の until が成り立った（判断を次の step へ持ち越す）。"""

    def __init__(self, decision: StepDecision) -> None:
        super().__init__("until")
        self.decision = decision


RUNNER_DRIVEN_ACTIONS = ("auth.login",)  # runner がセッション開始時に行うので経路の中では飛ばす

# 前提の部品（scenario.RETURN_DOOR_PARTS）→ それを残す経路の手順の見分け方（第 18 周）。
# 種まき経路をペルソナが途中で終えても、部品を残す手順（議論の着地の「書き置き」「持ち越す問い」）には進める
SEED_PART_ROLES = {"leave_note": "leave_note", "carryover": "carryover_question"}
SEED_PART_ACTIONS = {"last_tension": "learning.tension.confirm"}
SEED_PART_LABELS = {"leave_note": "次の自分への書き置き", "carryover": "次に考えたい問い（持ち越す問い）",
                    "last_tension": "引っかかりの確定"}
SEED_LANDING_STOPS = ("persona_stop", "goal_reached", "blocked")  # この止まり方なら着地の手順へ進める
SEED_LANDING_MARK = "seed_landing"


def _option_leaves_part(option: Option, part: str) -> bool:
    role = SEED_PART_ROLES.get(part)
    if role and option.action == "learning.cycle.intention" and str((option.with_ or {}).get("role") or "") == role:
        return True
    return bool(SEED_PART_ACTIONS.get(part) and option.action == SEED_PART_ACTIONS[part])


def seed_landing_steps(scenario: Scenario, parts: list[str]) -> list[tuple[str, StepSpec]]:
    """種まき経路の中で、前提の部品 ``parts`` を残す手順（最上位の do / choose）を経路の順に返す。"""
    found: list[tuple[str, StepSpec]] = []
    for spec in scenario.steps:
        if spec.kind == "repeat":
            continue
        for part in parts:
            if any(_option_leaves_part(o, part) for o in spec.options):
                found.append((part, spec))
                break
    return found


class ScenarioRunner:
    """1 ペルソナ × 1 経路を実行する。"""

    def __init__(self, agent: PersonaAgent, scenario: Scenario, params: ScenarioParams, session: PersonaSession,
                 client: EpistemeClient, writer: RunWriter, budget: Budget, *, max_steps: int = 0,
                 divergence: Optional[ReplayThenLivePersonaLLM] = None,
                 seed_requires: Optional[list[str]] = None) -> None:
        self.agent, self.scenario, self.params = agent, scenario, params
        # 種まきとして歩くときの前提の部品（空なら通常の経路）
        self.seed_requires = list(seed_requires or [])
        self._landing = False
        self._done: set[int] = set()
        self.landing_parts: list[str] = []
        self.session, self.client, self.writer, self.budget = session, client, writer, budget
        self.max_steps = max_steps
        self.divergence = divergence
        self.history: list[str] = []
        # 同じセッションの前の経路で最後に見た画面を引き継ぐ（第 9 周: 経路の最後の操作の結果をペルソナが
        # 一度も見ずに次の経路へ移り、「結果が出なかった」と記録していた）
        self.observation = str(session.scratch.get("last_observation") or "")
        self.pending: Optional[TranscriptStep] = None
        self.outcome = ScenarioOutcome()
        self._carry: Optional[StepDecision] = None
        self._until: Optional[tuple[str, int]] = None

    # -- 記録 ------------------------------------------------------------------
    def _finalize(self, decision: StepDecision) -> None:
        if self.pending is None:
            return
        self.pending.think.reaction = decision.prev_reaction
        self.pending.think.friction = decision.prev_friction  # type: ignore[assignment]
        self.pending.think.gave_up_reason = decision.gave_up_reason
        self.writer.write_step(self.pending)
        self.history.append(f"{self.pending.seq}. {self.pending.action_id} → {decision.prev_reaction[:80]}")
        self.pending = None

    def _goal(self) -> str:
        goal = str(self.params.render(self.scenario.goal))
        if self.seed_requires:
            parts = "と".join(SEED_PART_LABELS.get(p, p) for p in self.seed_requires)
            goal += f"。議論を終える前に、{parts}を必ず残してから終える"
        return goal

    # -- 判断 ------------------------------------------------------------------
    def _check_stops(self, d: StepDecision) -> None:
        stop_when = self.scenario.stop_when
        if d.prev_friction == "gave_up" or d.gave_up_reason:
            self.outcome.gave_up = True
            self.outcome.gave_up_reason = d.gave_up_reason
            if "gave_up" in stop_when:
                raise _Stop("gave_up")
        # blocked 1 回で経路を終えない（第 4 周: ⚓ が無い 1 回の blocked でチャットの手前で経路が終わり、
        # トピック内チャットが 4 周とも未検証だった）。HTTP エラーが伴うか、連続 2 回の blocked で止める。
        if d.prev_friction == "blocked":
            self._blocked_streak = getattr(self, "_blocked_streak", 0) + 1
        else:
            self._blocked_streak = 0
        http_error = (self.session.last_status or 0) >= 400
        if "blocked" in stop_when and (self._blocked_streak >= 2 or (d.prev_friction == "blocked" and http_error)):
            raise _Stop("blocked")
        if self._landing:
            return  # 種まきの着地（部品を残す手順）の最中は、やめたい・目標に届いた、では止めない
        if d.goal_reached and "goal_reached" in stop_when:
            raise _Stop("goal_reached")
        if d.stop and self.session.last_status == 429 and "quota_exhausted" in stop_when:
            raise _Stop("quota_exhausted")
        # 繰り返しの外で「この流れをやめたい」と言ったら経路を終える（第 8 周: stop=true の後も台本が続き、
        # ペルソナが去った後の操作を演じさせていた）。繰り返しの中では until の材料として扱う。
        if d.stop and self._until is None and "persona_stop" in stop_when:
            raise _Stop("persona_stop")

    def _stop_may_follow(self) -> bool:
        """直前の操作への反応しだいで場面が止まりうるか（第 25 周 H7）。

        blocked の停止は「HTTP エラーを伴う blocked」か「2 回続いた blocked」で成立する。どちらかが成り立ちうる
        ときは、頭脳に次の行為を選ばせる前に反応だけを聞く（止まるなら行為を選ばせない）。
        """
        if self.pending is None or "blocked" not in self.scenario.stop_when:
            return False
        http_error = (self.session.last_status or 0) >= 400
        return http_error or getattr(self, "_blocked_streak", 0) >= 1

    def _stop_or_finalize(self, d: StepDecision, chosen: str = "") -> None:
        """停止条件を見てから直前のステップを記録する。止まるときは transcript に止まった理由を残す（H7）。"""
        try:
            self._check_stops(d)
        except _Stop as stop:
            if self.pending is not None:
                self.pending.runner_notes.append(stop_note(str(stop), chosen))
            self._finalize(d)
            raise
        self._finalize(d)

    def _decide(self, spec: StepSpec, allowed, suggested, exhausted) -> StepDecision:
        notes = {o.action: str(self.params.render(o.note)) for o in spec.options if o.note}
        instruction = str(self.params.render(spec.note))
        probe: Optional[StepDecision] = None
        if self._stop_may_follow():
            # 反応だけを先に聞く。止まるならここで _Stop が上がり、頭脳は次の行為を選ばない
            probe = self.agent.step(goal=self._goal(), instruction=instruction, allowed=allowed,
                                    observation=self.observation, history=self.history, reaction_only=True)
            self._stop_or_finalize(probe)
        decision = self.agent.step(goal=self._goal(), instruction=instruction,
                                   allowed=allowed, observation=self.observation, history=self.history,
                                   suggested_args=suggested, notes=notes, optional=spec.optional,
                                   exhausted=exhausted)
        if probe is not None:
            # 同じ画面への反応は先に聞いたものが正本（二重に数えない）
            decision.prev_reaction, decision.prev_friction = probe.prev_reaction, probe.prev_friction
            decision.gave_up_reason = ""
        self._stop_or_finalize(decision, chosen=decision.action_id)
        return decision

    def _one(self, spec: StepSpec) -> None:
        """do / choose の 1 回。"""
        if self.budget.exhausted():
            raise BudgetExhausted(self.budget.exhausted())
        self._done.add(id(spec))
        allowed = [a for a in spec.allowed(self.scenario.screen) if a.id not in RUNNER_DRIVEN_ACTIONS]
        if not allowed:
            return
        single = spec.kind != "choose" and len(spec.options) == 1 and spec.options[0].action != "*"
        suggested, exhausted = self.params.render_args(spec.options[0].with_) if single else ({}, [])
        carry, self._carry = self._carry, None
        if carry is not None and carry.action_id in {a.id for a in allowed}:
            decision = carry
        else:
            decision = self._decide(spec, allowed, suggested, exhausted)
            if self._until is not None:
                expr, iterations = self._until
                self._until = None
                if evaluate_until(expr, {"friction": decision.prev_friction, "steps": iterations,
                                         "status": self.session.last_status, "goal_reached": decision.goal_reached,
                                         "persona_decides_to_stop": decision.stop}):
                    raise _UntilMet(decision)
        if decision.action_id == SKIP_ACTION_ID:
            return
        if not single:
            option = spec.option_for(decision.action_id)
            if option is not None and option.with_:
                rendered, _ = self.params.render_args(option.with_)
                # 台本の値は既定（seed）。ペルソナが自分で書いた非空の値（message / text 等）が勝つ
                # （第 1 周で Copilot と discuss に台本文が送られ、ペルソナの問いが届かなかった）。
                decision.args = {**rendered, **{k: v for k, v in decision.args.items() if v not in (None, "")}}
        self._execute(decision)

    def _run_steps(self, steps: list[StepSpec]) -> None:
        for spec in steps:
            if spec.kind == "repeat":
                self._repeat(spec)
            else:
                self._one(spec)

    def _repeat(self, spec: StepSpec) -> None:
        iterations = 0
        while iterations < spec.max:
            self._until = (spec.until, iterations) if iterations > 0 else None
            try:
                if spec.children:
                    self._run_steps(spec.children)
                else:
                    self._one(spec)
            except _UntilMet as met:
                self._carry = met.decision
                break
            finally:
                self._until = None
            iterations += 1

    # -- 実行 ------------------------------------------------------------------
    def _execute(self, decision: StepDecision) -> None:
        action = registry.get(decision.action_id)
        valid = action is not None and not decision.invalid_choice
        action_id = decision.action_id if valid else f"unsupported:{decision.invalid_choice or decision.action_id}"
        http = execute(action_id, decision.args, self.session, self.client)
        if valid and action.llm_cost == "product":
            self.budget.product_calls += 1
        observation = project_observation(action_id, self.session.last_status, self.session.last_body)
        for note in self.session.scratch.pop("observation_notes", None) or []:
            # runner が画面の前提として添える事実（例: 音声モードは議論では使えない）。審判・ペルソナが割り引く材料
            observation += f"\n〔{note}〕"
        if action_id == "learning.topic.open" and self.session.last_status == 200:
            # 画面はトピックの末尾に確認問題を出す。投影に無いと、ペルソナは問題文を見ずに答える（第 8 周）
            topic = self.session.topics.get(self.session.topic_id) or {}
            qs = [q.get("question", "") if isinstance(q, dict) else str(q) for q in (topic.get("check_questions") or [])]
            qs = [q for q in qs if q]
            if qs:
                observation += "\n確認問題: " + " ／ ".join(qs)
        self.pending = TranscriptStep(
            seq=self.writer.next_seq(), persona_id=self.session.persona_id, session=self.session.session_no,
            scenario_id=self.scenario.id, action_id=action_id, args=decision.args,
            think=ThinkAloud(intent=decision.intent, expectation=decision.expectation),
            screen=action.screen if action else self.scenario.screen,
            affordance=action.affordance if action else "", http=http, observation=observation,
            replay_divergence=bool(self.divergence and self.divergence.diverged),
            runner_notes=list(self.session.scratch.pop("runner_notes", None) or []),
        )
        self.observation = observation
        self.session.scratch["last_observation"] = observation
        self.outcome.steps += 1
        if self.max_steps and self.outcome.steps >= self.max_steps:
            raise _Stop("max_steps")

    def _land_seed(self) -> None:
        """種まき経路が途中で止まったとき、まだ歩いていない「部品を残す手順」へ進める（runner の手）。"""
        pending = [(part, spec) for part, spec in seed_landing_steps(self.scenario, self.seed_requires)
                   if id(spec) not in self._done]
        if not pending:
            return
        self._landing = True
        parts = [p for p, _ in pending]
        self.landing_parts = parts
        runner_note(self.session, f"{SEED_LANDING_MARK}: 種まきの経路 {self.scenario.id} が"
                                  f"「{self.outcome.stopped}」で止まったため、前提の部品"
                                  f"（{', '.join(parts)}）を残す着地の手順へ進めた（runner の手）")
        try:
            for _, spec in pending:
                self._one(spec)
        except _Stop:
            pass
        finally:
            self._landing = False

    def run(self) -> ScenarioOutcome:
        # 第 25 周 H6: 経路が ``pin_course: true`` を宣言したら、学生のコースの選択を campaign の course_id に固定する
        # （actions_exec ``pinned_course_override``）。経路ごとに立て直す（種まきの経路に持ち越さない）
        self.session.scratch["pin_course_strict"] = bool(self.scenario.raw.get("pin_course"))
        try:
            try:
                self._run_steps(self.scenario.steps)
            except _Stop as stop:
                self.outcome.stopped = str(stop)
            if self.seed_requires and self.outcome.stopped in SEED_LANDING_STOPS:
                self._land_seed()
        except BudgetExhausted as exc:
            self.outcome.stopped = f"budget:{exc}"
        finally:
            if self.pending is not None:
                try:
                    final = self.agent.step(goal=self._goal(), instruction="", allowed=[],
                                            observation=self.observation, history=self.history, finishing=True)
                    self._finalize(final)
                    if final.prev_friction == "gave_up" or final.gave_up_reason:
                        self.outcome.gave_up = True
                        self.outcome.gave_up_reason = final.gave_up_reason
                except Exception as exc:  # noqa: BLE001 — 最後の反応が取れなくても記録は残す
                    self.writer.log(f"最後の反応を取得できませんでした: {exc}")
                    self.writer.write_step(self.pending)
                    self.pending = None
        return self.outcome


# ----------------------------------------------------------------------------
# campaign
# ----------------------------------------------------------------------------

def _scenario_entries(entries: Any) -> list[tuple[str, Optional[list[int]]]]:
    out: list[tuple[str, Optional[list[int]]]] = []
    for e in entries or []:
        if isinstance(e, dict):
            sessions = e.get("sessions", e.get("session"))
            out.append((str(e.get("id")), [sessions] if isinstance(sessions, int) else sessions))
        else:
            out.append((str(e), None))
    return out


def _provision(settings: Settings, specs: list[PersonaSpec], writer: RunWriter) -> list[str]:
    """ペルソナのアカウントを製品の正規経路（管理者の作成 API）で用意する。"""
    notes: list[str] = []
    admin = EpistemeClient(settings.base_url, settings.http_timeout_s)
    try:
        ok, trace = admin.login(settings.admin_username, settings.admin_password)
        writer.setup_log({"step": "admin_login", "status": trace.status})
        if not ok:
            notes.append("管理者でログインできなかったため、アカウントの作成を行っていません（既存のアカウントでログインを試みます）。")
            return notes
        for spec in specs:
            username = persona_username(spec.id)
            path = "/api/admin/users/teacher" if spec.population == "teachers" else "/api/admin/users/student"
            status, _, _ = admin.call("POST", path, json={"username": username, "email": f"{username}@uxsim.invalid",
                                                         "password": settings.persona_password})
            writer.setup_log({"step": "create_account", "persona": spec.id, "status": status})
    finally:
        admin.close()
    return notes


def _prefetch_courses_step(session: PersonaSession, client: EpistemeClient, writer: RunWriter) -> None:
    """学生のセッション開始時のコース一覧（runner の手・ペルソナの判断ではない）を transcript に残す。

    経路が ``learning.course.list`` を経ずに ``learning.course.open`` から始まっても
    ``precondition:course_id`` で止まらないようにする（第 7 周）。ペルソナへの観測（最初のプロンプト）には
    流さない（replay のキャッシュ鍵を変えない）。
    """
    http, notes = prefetch_courses(session, client)
    action = registry.REGISTRY["learning.course.list"]
    writer.write_step(TranscriptStep(
        seq=writer.next_seq(), persona_id=session.persona_id, session=session.session_no,
        action_id="learning.course.list", screen=action.screen, affordance=action.affordance, http=http,
        observation=project_observation("learning.course.list", session.last_status, session.last_body),
        runner_notes=notes + list(session.scratch.pop("runner_notes", None) or [])))


def record_pinned_mismatch(meta: RunMeta, session: PersonaSession) -> bool:
    """セッションで記録した固定コースの取り違え（IK-0506）を meta に移す。記録があれば True。"""
    found = list(session.scratch.pop("pinned_course_mismatch", None) or [])
    for m in found:
        meta.pinned_course_mismatch.append({**m, "persona_id": session.persona_id, "session": session.session_no})
        meta.notes.append(
            f"{PINNED_MISMATCH_MARK}: {session.persona_id}（セッション {session.session_no}）は campaign が固定した"
            f"コース {m.get('pinned')} ではなく {m.get('actual')} を開いた（{m.get('action_id')}）。この週の"
            "内容依存の検証（是正の再現）は固定したコースを見ていないため、結果を是正の成否として読まない"
            "（砂場準備: uxsim/sandbox/pin_course.py）。")
    return bool(found)


BLOCKED_PRECONDITION_MARK = "blocked_precondition"
SEEDED_PRECONDITION_MARK = "seeded_precondition"


def _precondition_course_id(session: PersonaSession) -> str:
    return str(session.course_id or session.scratch.get("pinned_course_id") or session.scratch.get("default_course_id") or "")


def check_preconditions(scenario: Scenario, session: PersonaSession, client: EpistemeClient,
                        writer: Optional["RunWriter"] = None) -> list[dict[str, Any]]:
    """経路の機械で確かめられる前提（``check`` を持つ要素）を学習者の GET で確かめ、満たされないものを返す。

    runner の手（ペルソナの判断ではない）なので、確かめた GET は ``runner_driven`` の transcript 行に残す。
    session の ``last_status`` / ``last_body`` は確かめる前の値に戻す（ペルソナの次の判断・stop 判定を変えない）。
    """
    unmet: list[dict[str, Any]] = []
    saved = (session.last_status, session.last_body, session.last_action)
    for pre in scenario.precondition_checks:
        check, requires, text = pre.get("check"), list(pre.get("requires") or []), str(pre.get("text") or "")
        if check == "owns_course":
            miss = _check_owns_course(scenario, session, client, writer, requires, text)
            if miss:
                unmet.append(miss)
            continue
        if check != "return_door":
            continue
        cid = _precondition_course_id(session)
        if not cid:
            unmet.append({"text": text, "check": check, "missing": requires, "reason": "受講中のコースが無い"})
            continue
        http = execute("learning.cycle.return_door", {"course_id": cid}, session, client)
        status, body = session.last_status, session.last_body
        if status != 200 or not isinstance(body, dict):
            missing, reason = requires, f"扉を読めなかった（HTTP {status}）"
        else:
            missing = [r for r in requires if not body.get(r)] if requires else (["any"] if body.get("empty") else [])
            reason = "扉に残っていない" if missing else ""
        if missing:
            unmet.append({"text": text, "check": check, "missing": missing, "reason": reason})
        if writer is not None:
            action = registry.REGISTRY["learning.cycle.return_door"]
            verdict = "満たされている" if not missing else f"満たされていない（{reason}: {', '.join(missing)}）"
            writer.write_step(TranscriptStep(
                seq=writer.next_seq(), persona_id=session.persona_id, session=session.session_no,
                scenario_id=scenario.id, action_id="learning.cycle.return_door", screen=action.screen,
                affordance=action.affordance, http=http,
                observation=project_observation("learning.cycle.return_door", status, body),
                runner_notes=[f"runner_driven: 経路 {scenario.id} の前提「{text}」を確かめた — {verdict}"]))
    session.last_status, session.last_body, session.last_action = saved
    return unmet


def _check_owns_course(scenario: Scenario, session: PersonaSession, client: EpistemeClient,
                       writer: Optional["RunWriter"], requires: list[str], text: str) -> Optional[dict[str, Any]]:
    """教員が所有するコースがあるかを GET /api/admin/courses で確かめる（第 23 周）。

    満たされていれば ``admin.courses.list`` の手と同じく ``session.course_id`` をそのコースにする
    （以降の「確認して公開」の対象）。満たされていなければ不足の dict を返す。
    """
    args = {"prefer": "unpublished"} if "unpublished" in requires else {}
    http = execute("admin.courses.list", args, session, client)
    status, body = session.last_status, session.last_body
    rows = [c for c in body if isinstance(c, dict)] if isinstance(body, list) else []
    owned = [c for c in rows if c.get("role") == "owner"]
    if "unpublished" in requires:
        owned = [c for c in owned if not c.get("is_published")]
    if status != 200:
        miss = {"text": text, "check": "owns_course", "missing": ["owned_course"],
                "reason": f"コース一覧を読めなかった（HTTP {status}）"}
    elif not owned:
        miss = {"text": text, "check": "owns_course", "missing": ["owned_course"],
                "reason": "自分が所有する" + ("未公開の" if "unpublished" in requires else "") + "コースが無い"}
    else:
        miss = None
    if writer is not None:
        action = registry.REGISTRY["admin.courses.list"]
        verdict = "満たされている" if miss is None else f"満たされていない（{miss['reason']}）"
        writer.write_step(TranscriptStep(
            seq=writer.next_seq(), persona_id=session.persona_id, session=session.session_no,
            scenario_id=scenario.id, action_id="admin.courses.list", args=args, screen=action.screen,
            affordance=action.affordance, http=http,
            observation=project_observation("admin.courses.list", status, body),
            runner_notes=[f"runner_driven: 経路 {scenario.id} の前提「{text}」を確かめた — {verdict}"]
            + list(session.scratch.pop("runner_notes", None) or [])))
    return miss


def bootstrap_seed_course(seed: Scenario, session: PersonaSession, client: EpistemeClient,
                          writer: Optional["RunWriter"] = None) -> tuple[bool, list[str]]:
    """種まき経路を歩く前に、通常の経路と同じコースの文脈（受講 → コースを開く）を用意する（runner の手）。

    通常の経路は ``learning.course.list`` → ``learning.course.enroll`` → ``learning.course.open`` で campaign の
    固定コースに入るが、種まき経路（例: s-discuss-one-paper）は ``course.open`` から始まる。前のセッションで
    受講していないペルソナが種をまくと、受講可能なだけのコースを開いて 404 になり、以降の手順が
    ``course_id`` 無しで空振りした（第 18 周 st-07）。ここで受講と表示を済ませ、種まき経路の ``course.open``
    が同じコースを開けるようにする。戻り値は (用意できたか, 事実文の列)。
    """
    notes: list[str] = []
    if session.role == "TEACHER":
        # 教員の種まき（例: 自分のコースを登録する経路）は受講の文脈を要しない（第 23 周）
        return True, ["教員の種まきなので受講・コースを開く手順は要らない"]
    saved = (session.last_status, session.last_body, session.last_action)
    cid = _precondition_course_id(session)
    if not cid:
        return False, ["受講中のコースも受講可能なコースも無い"]
    plan: list[tuple[str, dict[str, Any]]] = []
    if not session.course_id:
        plan.append(("learning.course.enroll", {"course_id": cid}))
    plan.append(("learning.course.open", {"course_id": cid}))
    ok = True
    for action_id, args in plan:
        http = execute(action_id, args, session, client)
        status, body = session.last_status, session.last_body
        good = bool(status) and int(status) < 300
        enroll = action_id == "learning.course.enroll"
        fact = (f"{'受講を始めた' if enroll else 'コースを開いた'}（course_id={cid}）" if good else
                f"{'受講を始められなかった' if enroll else 'コースを開けなかった'}（HTTP {status}・course_id={cid}）")
        notes.append(fact)
        if writer is not None:
            action = registry.REGISTRY[action_id]
            writer.write_step(TranscriptStep(
                seq=writer.next_seq(), persona_id=session.persona_id, session=session.session_no,
                scenario_id=seed.id, action_id=action_id, args=args, screen=action.screen,
                affordance=action.affordance, http=http, observation=project_observation(action_id, status, body),
                runner_notes=[f"runner_driven: 種まき経路 {seed.id} の前に、通常の経路と同じくコースの文脈を"
                              f"用意した — {fact}"] + list(session.scratch.pop("runner_notes", None) or [])))
        if not good:
            ok = False
            break
    session.last_status, session.last_body, session.last_action = saved
    if ok and session.course_id != cid:
        ok = False
        notes.append(f"コースを開いた後も course_id={cid} が画面の文脈に残っていない")
    return ok, notes


def _make_llm(settings: Settings, run_dir: Path, replay_dir: Optional[Path]):
    live: Optional[PersonaLLM] = None
    if settings.persona_llm_provider != "replay":
        live = make_live_llm(settings.persona_llm_provider, settings.persona_llm_model, settings.persona_llm_api_key)
    divergence = None
    inner: PersonaLLM
    if replay_dir is not None:
        replay = ReplayPersonaLLM(load_cache_jsonl(replay_dir / "cache" / "persona_llm.jsonl"))
        divergence = ReplayThenLivePersonaLLM(replay, live)
        inner = divergence
    else:
        if live is None:
            raise ValueError("provider=replay のときは --replay RUN_ID が必要です")
        inner = live
    return CachingPersonaLLM(inner, run_dir / "cache" / "persona_llm.jsonl"), divergence


def run_campaign(
    campaign_path: Path,
    *,
    replay_of: Optional[str] = None,
    persona_filter: Optional[list[str]] = None,
    scenario_filter: Optional[list[str]] = None,
    max_steps: int = 0,
    settings: Optional[Settings] = None,
    llm: Optional[PersonaLLM] = None,
    client_factory=None,
    provision: bool = True,
    data_root: Path = UXSIM_ROOT,
) -> Path:
    """campaign を 1 回実行し、run ディレクトリを返す。"""
    settings = settings or get_settings()
    campaign = load_campaign(campaign_path)
    cid, domain = str(campaign["id"]), str(campaign["domain"])
    # 同じ秒に複数プロセスが起動すると run ディレクトリが衝突する（第 5 周: 学生 4 名を並列起動）。pid を足す
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    run_dir = settings.runs_dir / cid / run_id
    writer = RunWriter(run_dir)
    replay_dir = (settings.runs_dir / cid / replay_of) if replay_of else None
    divergence = None
    if llm is None:
        llm, divergence = _make_llm(settings, run_dir, replay_dir)
    budget = Budget(limits={k: int(v) for k, v in (campaign.get("budget") or {}).items()}, llm=llm)
    meta = RunMeta(run_id=run_id, campaign_id=cid, domain=domain, snapshot=str(campaign.get("snapshot") or ""),
                   git_commit=read_git_commit(), base_url=settings.base_url,
                   flags={str(k): str(v) for k, v in (campaign.get("flags") or {}).items()},
                   budget={k: int(v) for k, v in (campaign.get("budget") or {}).items()}, replay_of=replay_of or "")
    meta.pinned_course_id = str(campaign.get("course_id") or "")
    if meta.flags:
        meta.notes.append("flags は砂場の api-server の環境変数で設定する。runner は設定していない（宣言の記録のみ）。")
    writer.write_meta(meta)

    if domain == "*" or "regression" in (campaign.get("scenarios") or {}):
        files = sorted((data_root / "scenarios" / "regression").glob("IK-*.yaml"))
        meta.notes.append("回帰経路がまだ 1 本も無いため対象なし。" if not files else
                          "回帰経路の一括再演は v1 未実装（各回帰経路を元の campaign で --replay して確かめる）。")
        meta.finished_at = now_iso()
        writer.write_meta(meta)
        return run_dir

    personas = campaign.get("personas") or {}
    teacher_ids = [str(x) for x in personas.get("teachers") or []]
    student_ids = [str(x) for x in personas.get("students") or []]
    if persona_filter:
        teacher_ids = [p for p in teacher_ids if p in persona_filter]
        student_ids = [p for p in student_ids if p in persona_filter]
    specs: dict[str, PersonaSpec] = {}
    for pid in teacher_ids + student_ids:
        spec = compose_persona(domain, pid, data_root)
        if spec.status == "retired":
            meta.notes.append(f"{pid} は retired のため走らせていません。")
            continue
        specs[pid] = spec
    if provision:
        meta.notes.extend(_provision(settings, list(specs.values()), writer))
    sessions_per_student = int(campaign.get("sessions_per_student") or 1)
    scen = campaign.get("scenarios") or {}
    make_client = client_factory or (lambda: EpistemeClient(settings.base_url, settings.http_timeout_s))

    stop_all = ""
    plan = [(pid, "teacher", 1) for pid in teacher_ids if pid in specs]
    plan += [(pid, "student", n) for n in range(1, sessions_per_student + 1) for pid in student_ids if pid in specs]
    carried: dict[str, str] = {}
    for pid, role_key, session_no in plan:
        if stop_all:
            break
        spec = specs[pid]
        entries = _scenario_entries(scen.get(role_key))
        runnable = []
        for sid, sessions in entries:
            if scenario_filter and sid not in scenario_filter:
                continue
            scenario = load_scenario(sid, data_root)
            allowed_sessions = sessions or scenario.sessions or [1]
            if session_no in allowed_sessions:
                runnable.append(scenario)
        if not runnable:
            continue
        client = make_client()
        session = PersonaSession(persona_id=pid, username=persona_username(pid), password=settings.persona_password,
                                 role="TEACHER" if role_key == "teacher" else "STUDENT", session_no=session_no,
                                 course_id=carried.get(pid, ""), uses_voice=persona_uses_voice(spec.habits))
        # campaign の course_id（検証したいコースの固定。第 9 周: 同名コース 2 本のうち再生成していない方を
        # 「受講中の先頭」で拾い、内容依存の是正が確認できなかった）
        if campaign.get("course_id"):
            session.scratch["pinned_course_id"] = str(campaign["course_id"])
        ok, trace = client.login(session.username, session.password)
        writer.write_step(TranscriptStep(
            seq=writer.next_seq(), persona_id=pid, session=session_no, action_id="auth.login",
            screen=spec.screen, affordance=registry.REGISTRY["auth.login"].affordance, http=[trace],
            observation="ログインした。" if ok else project_observation("auth.login", trace.status, trace.response_excerpt)))
        client.take_traces()
        if not ok:
            meta.notes.append(f"{pid}（セッション {session_no}）はログインできなかったため経路を実行していません。")
            client.close()
            continue
        session.token = client.token
        if role_key == "student":
            _prefetch_courses_step(session, client, writer)
        agent = PersonaAgent(spec, llm)
        try:
            for scenario in runnable:
                if scenario.status == "retired":
                    meta.notes.append(f"{scenario.id} は retired のため走らせていません。")
                    continue
                params = ScenarioParams(load_params(domain, scenario.id, pid, data_root, spec.archetype_id),
                                        persona=persona_fields(spec, data_root))
                missing = scenario.missing_params(params.values)
                if missing:
                    meta.notes.append(f"{scenario.id}（{pid}）は分野パラメータが埋まっていないため未実施: {', '.join(missing)}")
                    continue
                if scenario.precondition_checks:
                    unmet = check_preconditions(scenario, session, client, writer)
                    seeded: list[str] = []
                    if unmet and scenario.seed_scenarios:
                        # (a) 種をまく経路があれば先に歩く（同じペルソナ・同じセッション）。歩いた後にもう一度確かめる
                        for seed_id in scenario.seed_scenarios:
                            seed = load_scenario(seed_id, data_root)
                            seed_params = ScenarioParams(load_params(domain, seed.id, pid, data_root, spec.archetype_id),
                                                         persona=persona_fields(spec, data_root))
                            seed_missing = seed.missing_params(seed_params.values)
                            if seed_missing:
                                meta.notes.append(f"{seed.id}（{pid}・{scenario.id} の種まき）は分野パラメータが埋まっていないため未実施: "
                                                  f"{', '.join(seed_missing)}")
                                continue
                            parts = sorted({m for u in unmet for m in u.get("missing") or []})
                            record: dict[str, Any] = {"persona_id": pid, "session": session_no,
                                                      "scenario_id": scenario.id, "seed_id": seed.id,
                                                      "unmet_before": parts, "bootstrap": [], "stopped": "",
                                                      "steps": 0, "landing": [], "satisfied": False}
                            meta.seeded_precondition.append(record)
                            lacking = [p for p in parts if p in SEED_PART_ROLES or p in SEED_PART_ACTIONS]
                            unreachable = [p for p in lacking
                                           if p not in {q for q, _ in seed_landing_steps(seed, lacking)}]
                            if unreachable:
                                meta.notes.append(f"{seed.id}（{pid}・{scenario.id} の種まき）には前提の部品"
                                                  f"（{', '.join(unreachable)}）を残す手順が無い")
                            ok_boot, boot_notes = bootstrap_seed_course(seed, session, client, writer)
                            record["bootstrap"] = boot_notes
                            if not ok_boot:
                                record["stopped"] = "bootstrap_failed"
                                meta.notes.append(f"{seed.id}（{pid}・{scenario.id} の種まき）はコースの文脈を"
                                                  f"用意できなかったため歩いていません: {'; '.join(boot_notes)}")
                                continue
                            runner_note(session, f"{SEEDED_PRECONDITION_MARK}: 経路 {scenario.id} の前提"
                                                 f"（{'; '.join(u['text'] for u in unmet)}）が満たされていないため、"
                                                 f"先に {seed.id} を歩いて種をまく（runner の手）")
                            writer.log(f"seed {pid} s{session_no} {seed.id} for {scenario.id}")
                            seed_runner = ScenarioRunner(agent, seed, seed_params, session, client, writer, budget,
                                                         max_steps=max_steps, divergence=divergence,
                                                         seed_requires=parts)
                            seed_outcome = seed_runner.run()
                            record.update(stopped=seed_outcome.stopped, steps=seed_outcome.steps,
                                          landing=list(seed_runner.landing_parts))
                            seeded.append(seed.id)
                            writer.write_meta(meta)
                            if seed_outcome.stopped.startswith("budget:"):
                                stop_all = seed_outcome.stopped
                                break
                        if stop_all:
                            meta.notes.append(f"予算（{stop_all.split(':', 1)[1]}）に達したため、ここで campaign を止めました。")
                            break
                        unmet = check_preconditions(scenario, session, client, writer)
                        for rec in meta.seeded_precondition:
                            if rec["persona_id"] == pid and rec["session"] == session_no and \
                                    rec["scenario_id"] == scenario.id:
                                rec["satisfied"] = not unmet
                        if seeded:
                            meta.notes.append(f"{SEEDED_PRECONDITION_MARK}: {scenario.id}（{pid}・セッション {session_no}）の"
                                              f"前提を満たすために {', '.join(seeded)} を先に歩いた — "
                                              + ("前提は満たされた" if not unmet else "それでも満たされていない"))
                    if unmet:
                        # (b) 種をまけない（または撒いても足りない）なら経路を歩かない — 空のセッションを歩くと
                        # 扉・持ち越しが空であることを製品の欠陥と読む偽所見になる（第 17 周 st-07）
                        detail = "; ".join(f"{u['text']}（{u['reason']}: {', '.join(u['missing'])}）" for u in unmet)
                        meta.blocked_precondition.append({"persona_id": pid, "session": session_no,
                                                          "scenario_id": scenario.id, "unmet": unmet, "seeded": seeded})
                        meta.notes.append(f"{BLOCKED_PRECONDITION_MARK}: {scenario.id}（{pid}・セッション {session_no}）は"
                                          f"前提が満たされていないため歩いていません: {detail}"
                                          + (f"（種まき {', '.join(seeded)} の後も不足）" if seeded else ""))
                        runner_note(session, f"{BLOCKED_PRECONDITION_MARK}: 経路 {scenario.id} を飛ばした — {detail}")
                        writer.write_meta(meta)
                        continue
                writer.log(f"start {pid} s{session_no} {scenario.id}")
                outcome = ScenarioRunner(agent, scenario, params, session, client, writer, budget,
                                         max_steps=max_steps, divergence=divergence).run()
                writer.log(f"end {pid} s{session_no} {scenario.id} steps={outcome.steps} stopped={outcome.stopped}")
                if outcome.gave_up:
                    meta.notes.append(f"{pid} は {scenario.id} で諦めた: {outcome.gave_up_reason or '（理由なし）'}")
                if outcome.stopped.startswith("budget:"):
                    stop_all = outcome.stopped
                    meta.notes.append(f"予算（{outcome.stopped.split(':', 1)[1]}）に達したため、ここで campaign を止めました。")
                    break
        finally:
            carried[pid] = session.course_id
            if record_pinned_mismatch(meta, session):
                writer.write_meta(meta)  # 途中で止まった run でも meta に残す（第 12 周の meta は finished_at が空だった）
            meta.spent["http_429"] = meta.spent.get("http_429", 0) + client.count_429
            client.close()
    meta.spent.update(budget.spent())
    if divergence is not None and divergence.diverged:
        meta.notes.append(f"replay は {divergence.diverged_at_call} 回目のペルソナ呼び出しで分岐し、以降は live で続けました。")
    meta.finished_at = now_iso()
    writer.write_meta(meta)
    return run_dir


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="uxsim API runner")
    ap.add_argument("campaign")
    ap.add_argument("--replay", default=None, help="同じ campaign の run_id を指定して再演する")
    ap.add_argument("--persona", action="append", default=None)
    ap.add_argument("--scenario", action="append", default=None)
    ap.add_argument("--max-steps", type=int, default=0)
    ap.add_argument("--no-oracles", action="store_true")
    args = ap.parse_args(argv)
    run_dir = run_campaign(resolve_campaign_path(args.campaign), replay_of=args.replay,
                           persona_filter=args.persona, scenario_filter=args.scenario, max_steps=args.max_steps)
    print(f"run: {run_dir}")
    if not args.no_oracles:
        from uxsim.oracles.run_all import run_oracles

        findings = run_oracles(run_dir)
        print(f"findings: {len(findings)} 件 → {run_dir / 'findings.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
