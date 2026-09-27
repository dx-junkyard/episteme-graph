"""ペルソナ 1 人の行為選択（``PersonaLLM`` を 1 ステップ 1 回呼ぶ）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from uxsim.actions.registry import Action
from uxsim.llm import PersonaLLM
from uxsim.persona.compose import PersonaSpec
from uxsim.persona.prompt import OUTPUT_SCHEMA_HINT, build_step_message, build_system_prompt
from uxsim.schema import ThinkAloud

FRICTIONS = ("none", "confused", "misread", "blocked", "gave_up")
NO_ACTION = "none"
SKIP_ACTION_ID = "skip"


@dataclass
class StepDecision:
    """1 回の呼び出しの結果。``prev_*`` は直前ステップへの反応、残りは次の行為。"""

    action_id: str
    args: dict[str, Any] = field(default_factory=dict)
    intent: str = ""
    expectation: str = ""
    prev_reaction: str = ""
    prev_friction: str = "none"
    gave_up_reason: str = ""
    invalid_choice: str = ""  # 許されない行為を選んだとき、その id
    goal_reached: bool = False
    stop: bool = False

    def think(self) -> ThinkAloud:
        """次の行為の think-aloud（反応は次の呼び出しで埋まる）。"""
        return ThinkAloud(intent=self.intent, expectation=self.expectation)


def _is_scripted(llm: Any) -> bool:
    """ラッパ（Caching / ReplayThenLive）越しでも scripted 実装かを見る。"""
    seen = 0
    while llm is not None and seen < 5:
        if getattr(llm, "scripted", False):
            return True
        llm = getattr(llm, "inner", None) or getattr(llm, "live", None)
        seen += 1
    return False


def _truthy(v: Any) -> bool:
    return v is True or str(v).strip().lower() in ("true", "yes", "1")


def _normalize_friction(v: Any) -> str:
    s = str(v or "none").strip().lower()
    return s if s in FRICTIONS else "none"


class PersonaAgent:
    """ペルソナ定義 + LLM。"""

    def __init__(self, spec: PersonaSpec, llm: PersonaLLM) -> None:
        self.spec = spec
        self.llm = llm
        self.system = build_system_prompt(spec)

    def _ask(self, message: str) -> dict:
        return self.llm.complete_json(self.system, [{"role": "user", "content": message}], OUTPUT_SCHEMA_HINT)

    def step(
        self,
        *,
        goal: str,
        instruction: str,
        allowed: list[Action],
        observation: str,
        history: list[str],
        suggested_args: Optional[dict[str, Any]] = None,
        finishing: bool = False,
        notes: Optional[dict[str, str]] = None,
        optional: bool = False,
        exhausted: Optional[list[str]] = None,
    ) -> StepDecision:
        """次の行為を選ぶ。許されない行為を選んだら 1 回だけ言い直させ、それでも駄目なら記録する。"""
        if _is_scripted(self.llm):
            first = allowed[0].id if allowed else NO_ACTION
            return StepDecision(
                action_id=NO_ACTION if finishing else first,
                args={} if finishing else dict(suggested_args or {}),
                intent="台本どおりに進む（scripted）",
                expectation="",
                prev_reaction="（scripted: 反応は記録しない）",
                prev_friction="none",
            )
        message = build_step_message(goal=goal, instruction=instruction, allowed=allowed, observation=observation,
                                     history=history, suggested_args=suggested_args, finishing=finishing,
                                     notes=notes, optional=optional, exhausted=exhausted)
        out = self._ask(message)
        allowed_ids = {a.id for a in allowed} | ({SKIP_ACTION_ID} if optional else set())
        chosen = str(out.get("action_id") or "").strip()
        invalid = ""
        if not finishing and chosen not in allowed_ids:
            invalid = chosen
            retry = message + ("\n\n# 訂正\n"
                               f"\"{chosen}\" はこのステップで取れない。許された id から 1 つ選び直す。")
            out2 = self._ask(retry)
            chosen2 = str(out2.get("action_id") or "").strip()
            if chosen2 in allowed_ids:
                # 反応は最初の答えを残す（同じ画面への反応なので）
                out = {**out2, "reaction_prev": out.get("reaction_prev", ""),
                       "friction_prev": out.get("friction_prev", "none"),
                       "gave_up_reason": out.get("gave_up_reason", "")}
                chosen, invalid = chosen2, ""
        args = out.get("args") if isinstance(out.get("args"), dict) else {}
        merged_args = {**(suggested_args or {}), **{k: v for k, v in args.items() if v not in (None, "")}}
        return StepDecision(
            action_id=NO_ACTION if finishing else chosen,
            args={} if finishing else merged_args,
            intent=str(out.get("intent") or ""),
            expectation=str(out.get("expectation") or ""),
            prev_reaction=str(out.get("reaction_prev") or ""),
            prev_friction=_normalize_friction(out.get("friction_prev")),
            gave_up_reason=str(out.get("gave_up_reason") or ""),
            invalid_choice=invalid,
            goal_reached=_truthy(out.get("goal_reached")),
            stop=_truthy(out.get("stop")),
        )
