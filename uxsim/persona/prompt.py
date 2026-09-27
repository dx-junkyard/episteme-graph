"""ペルソナ LLM のプロンプト（1 ステップ = 1 コール — §7.3）。

system はペルソナの声・知識・目標・誤解（信念として持つ）と演じ方の規律（§6.3）。
user は「目標・このステップで取れる行為・直前に見た画面・短い経緯」。会話は毎回 1 ターンで組み、
replay cache のキーを決定論的に保つ。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from uxsim.actions.registry import Action
from uxsim.persona.compose import PersonaSpec

OUTPUT_SCHEMA_HINT = json.dumps({
    "reaction_prev": "直前に見た画面への反応（思ったことをそのまま。初回は空）",
    "friction_prev": "none | confused | misread | blocked | gave_up（直前の画面について）",
    "gave_up_reason": "諦めたときだけ、その理由（それ以外は空）",
    "intent": "次にやろうとしていること",
    "expectation": "その操作で画面に何が出ると思うか",
    "action_id": "許された行為 id のどれか 1 つ",
    "args": {"引数名": "値"},
    "goal_reached": "目標を果たせたと思ったら true",
    "stop": "この流れをここでやめたいと思ったら true（理由は reaction_prev に）",
}, ensure_ascii=False, indent=1)

_RULES_JA = """# 演じ方の規律
- あなたは画面に見えるものしか知らない。API・内部の仕組み・設計書は知らない。
- 知っている語は knows だけ。vague の語は曖昧に、unknown の語は知らないものとして振る舞う。
- 誤解（beliefs）は正しいと信じている。訂正されたら、納得できたときだけ考えを改める。
- 理解できないときに理解したふりをしない。分からなければ confused、先に進めなければ blocked。
- 諦める条件に当たったら friction_prev を gave_up にし、gave_up_reason に理由を書く。
- 行為は「このステップで取れる行為」の id からだけ選ぶ。無い操作は選べない。
- think-aloud（reaction_prev / intent / expectation）は {lang} で書く。
- 数値の点数・評価を自分でつけない。"""


def _lang(spec: PersonaSpec) -> str:
    return "English" if spec.language == "en" else "日本語"


def build_system_prompt(spec: PersonaSpec) -> str:
    """ペルソナの system プロンプト。"""
    k = spec.knowledge
    parts = [
        f"あなたは「{spec.display_name}」として、学習システムを利用者の立場で使う。",
        f"立場: {'教員' if spec.population == 'teachers' else '学生'}",
    ]
    if spec.summary:
        parts.append(f"行動の型: {spec.summary}")
    if spec.background:
        parts.append(f"背景:\n{spec.background.strip()}")
    if spec.voice:
        parts.append(f"話し方・考え方:\n{spec.voice.strip()}")
    if spec.habits:
        parts.append("癖: " + ", ".join(f"{a}={b}" for a, b in spec.habits.items()))
    parts.append("知っている語（knows）: " + ("、".join(k.get("knows") or []) or "（特になし）"))
    parts.append("曖昧な語（vague）: " + ("、".join(k.get("vague") or []) or "（特になし）"))
    parts.append("知らない語（unknown）: " + ("、".join(k.get("unknown") or []) or "（特になし）"))
    if spec.misconceptions:
        parts.append("信じていること（beliefs）:\n" + "\n".join(f"- {m['text']}" for m in spec.misconceptions))
    if spec.goals:
        parts.append("目標:\n" + "\n".join(f"- {g['text']}" for g in spec.goals))
    if spec.gives_up_when:
        parts.append(f"諦める条件:\n{spec.gives_up_when.strip()}")
    if spec.asks_out_of_principle:
        parts.append("ときどき口にする要求:\n" + "\n".join(f"- {x}" for x in spec.asks_out_of_principle))
    parts.append(_RULES_JA.format(lang=_lang(spec)))
    return "\n\n".join(parts)


SKIP_LINE = "- skip — 何も選ばずに先へ進む"


def describe_action(action: Action, note: str = "") -> str:
    args = ", ".join(f"{n}:{t}" for n, t in action.args.items()) or "引数なし"
    pre = f" ／ 前提: {action.precondition}" if action.precondition else ""
    hint = f" ／ つもり: {note}" if note else ""
    return f"- {action.id} — {action.label}（{args}）{pre}{hint}"


def build_step_message(
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
) -> str:
    """1 ステップ分の user メッセージ。"""
    parts = [f"# 目標\n{goal}"]
    if instruction:
        parts.append(f"# このステップ\n{instruction}")
    if history:
        parts.append("# ここまで\n" + "\n".join(history[-6:]))
    parts.append("# 直前に見た画面\n" + (observation or "（まだ何も見ていない）"))
    if finishing:
        parts.append("# 終了\nこの経路はここで終わる。直前の画面への反応だけを返し、action_id は \"none\" にする。")
    else:
        lines = [describe_action(a, (notes or {}).get(a.id, "")) for a in allowed]
        if optional:
            lines.append(SKIP_LINE)
        parts.append("# このステップで取れる行為\n" + "\n".join(lines))
        if exhausted:
            parts.append("# 材料切れ\n" + "、".join(exhausted)
                         + " の材料は尽きた。これまでと同じ調子で、自分で考えて引数に書く。")
        if suggested_args:
            parts.append("# 使ってよい材料（自分の言葉に直してよい）\n"
                         + json.dumps(suggested_args, ensure_ascii=False))
    return "\n\n".join(parts)
