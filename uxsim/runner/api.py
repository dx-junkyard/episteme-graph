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
from uxsim.runner.actions_exec import PINNED_MISMATCH_MARK, execute, prefetch_courses
from uxsim.runner.client import EpistemeClient
from uxsim.runner.scenario import (SKIP_ACTION_ID, Scenario, ScenarioParams, StepSpec, evaluate_until, load_params,
                                   load_scenario)
from uxsim.runner.state import PersonaSession, project_observation
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


class _UntilMet(Exception):
    """repeat の until が成り立った（判断を次の step へ持ち越す）。"""

    def __init__(self, decision: StepDecision) -> None:
        super().__init__("until")
        self.decision = decision


RUNNER_DRIVEN_ACTIONS = ("auth.login",)  # runner がセッション開始時に行うので経路の中では飛ばす


class ScenarioRunner:
    """1 ペルソナ × 1 経路を実行する。"""

    def __init__(self, agent: PersonaAgent, scenario: Scenario, params: ScenarioParams, session: PersonaSession,
                 client: EpistemeClient, writer: RunWriter, budget: Budget, *, max_steps: int = 0,
                 divergence: Optional[ReplayThenLivePersonaLLM] = None) -> None:
        self.agent, self.scenario, self.params = agent, scenario, params
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
        return str(self.params.render(self.scenario.goal))

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
        if d.goal_reached and "goal_reached" in stop_when:
            raise _Stop("goal_reached")
        if d.stop and self.session.last_status == 429 and "quota_exhausted" in stop_when:
            raise _Stop("quota_exhausted")
        # 繰り返しの外で「この流れをやめたい」と言ったら経路を終える（第 8 周: stop=true の後も台本が続き、
        # ペルソナが去った後の操作を演じさせていた）。繰り返しの中では until の材料として扱う。
        if d.stop and self._until is None and "persona_stop" in stop_when:
            raise _Stop("persona_stop")

    def _decide(self, spec: StepSpec, allowed, suggested, exhausted) -> StepDecision:
        notes = {o.action: str(self.params.render(o.note)) for o in spec.options if o.note}
        decision = self.agent.step(goal=self._goal(), instruction=str(self.params.render(spec.note)),
                                   allowed=allowed, observation=self.observation, history=self.history,
                                   suggested_args=suggested, notes=notes, optional=spec.optional,
                                   exhausted=exhausted)
        self._finalize(decision)
        return decision

    def _one(self, spec: StepSpec) -> None:
        """do / choose の 1 回。"""
        if self.budget.exhausted():
            raise BudgetExhausted(self.budget.exhausted())
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
            self._check_stops(decision)
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

    def run(self) -> ScenarioOutcome:
        try:
            self._run_steps(self.scenario.steps)
        except _Stop as stop:
            self.outcome.stopped = str(stop)
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
                                 course_id=carried.get(pid, ""))
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
