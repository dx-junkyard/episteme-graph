"""経路（scenario）の読み込みと、分野の穴（``{{param}}``）の解決。

記法の正本は ``uxsim/scenarios/README.md``。経路ファイルは ``uxsim/scenarios/**/<id>.yaml``、
分野固有の値は ``uxsim/domains/<domain>/scenario_params/<scenario_id>.yaml`` が埋める。

穴:
- ``{{name}}`` — 値に置き換える（文字列の一部ならリストは「、」で連結）
- ``{{name | next}}`` — リストの次の要素。尽きたら「材料切れ」（その引数は渡さず、ペルソナが同じ調子で作る）
- ``{{name | current}}`` — 直前に ``next`` で取り出した要素
- ``{{persona.<欄>}}`` — ペルソナの欄（``persona.teaching.course_subject`` のように入れ子も可。
  ``persona.course_brief`` は ``corpus/course_briefs/<名>.md`` の本文）

steps の要素:
- ``{do: <id>, with: {...}, note: "..."}`` — 決まった行為
- ``{choose: [<id> | {do, with, note}, ...], optional: true}`` — ペルソナが 1 つ選ぶ（optional なら選ばずに進める）。
  ``choose: all`` は画面の全行為
- ``{repeat: {until, max, do | choose | steps: [...], with, note}}`` — 繰り返し（steps は塊の繰り返し）

until の式（``or`` / ``and`` でつなぐ。and が先に結合）:
``friction in [a, b]`` / ``friction == a`` / ``steps > N``（>= < <= == != も可）/ ``status >= 400`` /
``response is 429`` / ``goal_reached`` / ``persona_decides_to_stop`` / ``gave_up`` / ``blocked`` / ``budget_exhausted``
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from uxsim.actions import registry
from uxsim.config import UXSIM_ROOT

_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*(?:\|\s*(\w+)\s*)?\}\}")
_FLAGS = ("gave_up", "blocked", "budget_exhausted", "goal_reached", "persona_decides_to_stop")
_CLAUSE_RE = re.compile(
    r"^\s*(?:friction\s+in\s+\[(?P<set>[^\]]*)\]"
    r"|response\s+is\s+(?P<resp>\d{3})"
    r"|(?P<lhs>friction|steps|status)\s*(?P<op>==|!=|>=|<=|>|<)\s*(?P<rhs>[\w-]+)"
    r"|(?P<flag>" + "|".join(_FLAGS) + r"))\s*$")
DEFAULT_REPEAT_MAX = 12
SKIP_ACTION_ID = "skip"  # optional な choose で「何も選ばずに進む」


class ScenarioError(ValueError):
    """経路ファイル・分野パラメータの不備。"""


class Exhausted:
    """``| next`` の材料切れ（値ではない印）。"""

    def __repr__(self) -> str:
        return "<exhausted>"


EXHAUSTED = Exhausted()


# ----------------------------------------------------------------------------
# until 式
# ----------------------------------------------------------------------------

def _eval_clause(clause: str, ctx: dict[str, Any]) -> bool:
    m = _CLAUSE_RE.match(clause)
    if not m:
        raise ScenarioError(f"until の式を解釈できません: {clause!r}")
    if m.group("set") is not None:
        values = {v.strip().strip("'\"") for v in m.group("set").split(",") if v.strip()}
        return str(ctx.get("friction", "none")) in values
    if m.group("resp"):
        return ctx.get("status") == int(m.group("resp"))
    if m.group("flag"):
        flag = m.group("flag")
        return bool(ctx.get(flag)) or (flag in ("gave_up", "blocked") and ctx.get("friction") == flag)
    lhs, op, rhs = m.group("lhs"), m.group("op"), m.group("rhs")
    left = ctx.get(lhs)
    if lhs == "friction":
        left = str(left or "none")
        return (left == rhs) if op == "==" else (left != rhs) if op == "!=" else False
    if left is None:
        return False
    a, b = int(left), int(rhs)
    return {"==": a == b, "!=": a != b, ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[op]


def _split(expr: str) -> list[list[str]]:
    return [re.split(r"\s+and\s+", part) for part in re.split(r"\s+or\s+", str(expr or "").strip())]


def evaluate_until(expr: str, ctx: dict[str, Any]) -> bool:
    """until 式を評価する。ctx のキー: friction / steps / status / goal_reached / persona_decides_to_stop …"""
    if not expr or not str(expr).strip():
        return False
    return any(all(_eval_clause(c, ctx) for c in part) for part in _split(expr))


def validate_until(expr: str) -> None:
    for part in _split(expr):
        for c in part:
            if c.strip() and not _CLAUSE_RE.match(c):
                raise ScenarioError(f"until の式を解釈できません: {c!r}")


# ----------------------------------------------------------------------------
# パラメータ
# ----------------------------------------------------------------------------

def _dig(data: Any, dotted: str) -> Any:
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return ""
    return cur


class ScenarioParams:
    """分野パラメータ + ``| next`` / ``| current`` のカーソル。"""

    def __init__(self, values: dict[str, Any], persona: Optional[dict[str, Any]] = None) -> None:
        self.values = dict(values)
        self.persona = dict(persona or {})
        self._cursor: dict[str, int] = {}
        self._current: dict[str, Any] = {}

    def lookup(self, name: str, op: Optional[str] = None) -> Any:
        if name.startswith("persona."):
            return _dig(self.persona, name.split(".", 1)[1])
        if name not in self.values:
            raise ScenarioError(f"パラメータが埋まっていません: {name}")
        value = self.values[name]
        if op == "next":
            items = value if isinstance(value, list) else [value]
            i = self._cursor.get(name, 0)
            self._cursor[name] = i + 1
            if i >= len(items):
                self._current[name] = EXHAUSTED
                return EXHAUSTED
            self._current[name] = items[i]
            return items[i]
        if op == "current":
            return self._current.get(name, EXHAUSTED)
        if op not in (None, ""):
            raise ScenarioError(f"未知のフィルタ: {op}")
        return value

    def render(self, template: Any) -> Any:
        """文字列・dict・list の中の穴を解決する（全体が 1 つの穴なら値の型を保つ）。"""
        if isinstance(template, dict):
            return {k: self.render(v) for k, v in template.items()}
        if isinstance(template, list):
            return [self.render(v) for v in template]
        if not isinstance(template, str):
            return template
        whole = _TEMPLATE_RE.fullmatch(template.strip())
        if whole:
            return self.lookup(whole.group(1), whole.group(2))

        def repl(m: re.Match) -> str:
            v = self.lookup(m.group(1), m.group(2))
            if v is EXHAUSTED:
                return ""
            return "、".join(map(str, v)) if isinstance(v, list) else str(v)

        return _TEMPLATE_RE.sub(repl, template)

    def render_args(self, template: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """引数の材料を解決する。材料切れのキーは落とし、その名前を返す。"""
        rendered: dict[str, Any] = {}
        exhausted: list[str] = []
        for k, v in (template or {}).items():
            try:
                rendered[k] = self.render(v)
            except ScenarioError:
                # 分野側に無い材料（任意の穴）はその引数を渡さない（第 8 周: topic_hint_terms のような
                # 分野依存の任意材料を経路に書けるようにする。必須材料は params_required で守る）
                exhausted.append(k)
        exhausted += [k for k, v in rendered.items() if v is EXHAUSTED]
        return {k: v for k, v in rendered.items() if v is not EXHAUSTED}, exhausted


def template_names(value: Any) -> set[str]:
    """値に現れる ``{{name}}`` の名前（``persona.`` は除く）。"""
    found: set[str] = set()
    if isinstance(value, dict):
        for v in value.values():
            found |= template_names(v)
    elif isinstance(value, list):
        for v in value:
            found |= template_names(v)
    elif isinstance(value, str):
        found |= {m.group(1) for m in _TEMPLATE_RE.finditer(value) if not m.group(1).startswith("persona.")}
    return found


def load_params(domain: str, scenario_id: str, persona_id: str = "", root: Path = UXSIM_ROOT,
                archetype_id: str = "") -> dict[str, Any]:
    """分野パラメータ。``params:`` の下を正とし、``per_archetype:`` → ``personas:`` の順に上書きする。"""
    p = root / "domains" / domain / "scenario_params" / f"{scenario_id}.yaml"
    if not p.is_file():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    base = dict(data.get("params") or {}) if isinstance(data.get("params"), dict) else {
        k: v for k, v in data.items() if k not in ("id", "scenario", "domain", "personas", "per_archetype")}
    short = archetype_id.split("/")[-1] if archetype_id else ""
    for layer, key in ((data.get("per_archetype") or {}, short), (data.get("personas") or {}, persona_id)):
        if key and isinstance(layer.get(key), dict):
            base.update(layer[key])
    return base


# ----------------------------------------------------------------------------
# 経路
# ----------------------------------------------------------------------------

@dataclass
class Option:
    action: str
    with_: dict[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass
class StepSpec:
    kind: str  # do | choose | repeat
    options: list[Option] = field(default_factory=list)  # do / choose / repeat(do|choose) の候補
    children: list["StepSpec"] = field(default_factory=list)  # repeat(steps) の塊
    until: str = ""
    max: int = 1
    note: str = ""
    optional: bool = False

    @property
    def actions(self) -> list[str]:
        return [o.action for o in self.options]

    def allowed(self, screen: str) -> list[registry.Action]:
        if self.actions == ["*"]:
            return registry.actions_for(screen)  # type: ignore[arg-type]
        return [registry.REGISTRY[a] for a in self.actions if a in registry.REGISTRY]

    def option_for(self, action_id: str) -> Optional[Option]:
        return next((o for o in self.options if o.action == action_id), None)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class Scenario:
    id: str
    population: str
    goal: str
    steps: list[StepSpec]
    params_required: list[str] = field(default_factory=list)
    stop_when: list[str] = field(default_factory=lambda: ["gave_up", "budget_exhausted"])
    expects: list[dict[str, Any]] = field(default_factory=list)
    runner: list[str] = field(default_factory=lambda: ["api"])
    sessions: Optional[list[int]] = None
    status: str = "active"
    path: Optional[Path] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def screen(self) -> str:
        return "admin" if self.population == "teachers" else "learning"

    def all_steps(self):
        for s in self.steps:
            yield from s.walk()

    def referenced_actions(self) -> set[str]:
        return {a for s in self.all_steps() for a in s.actions if a != "*"}

    def missing_params(self, params: dict[str, Any]) -> list[str]:
        needed = set(self.params_required) | template_names(self.goal)
        for s in self.all_steps():
            needed |= template_names(s.note)
            for o in s.options:
                needed |= template_names(o.with_) | template_names(o.note)
        return sorted(n for n in needed if n not in params)


def _option(raw: Any, with_: Optional[dict] = None, note: str = "") -> Option:
    if isinstance(raw, dict):
        return Option(str(raw.get("do")), dict(raw.get("with") or {}), str(raw.get("note") or ""))
    return Option(str(raw), dict(with_ or {}), note)


def _options(value: Any, with_: Optional[dict] = None, note: str = "") -> list[Option]:
    if value in ("all", "*"):
        return [Option("*", dict(with_ or {}), note)]
    if isinstance(value, (list, tuple)):
        return [_option(v, with_, note) for v in value]
    return [_option(value, with_, note)]


def _parse_step(raw: Any) -> StepSpec:
    if not isinstance(raw, dict):
        raise ScenarioError(f"step は dict で書く: {raw!r}")
    note = str(raw.get("note") or raw.get("say") or "")
    if "repeat" in raw:
        r = raw["repeat"] or {}
        until = str(r.get("until") or "")
        validate_until(until)
        spec = StepSpec("repeat", until=until, max=int(r.get("max") or DEFAULT_REPEAT_MAX),
                        note=str(r.get("note") or note))
        if "steps" in r:
            spec.children = [_parse_step(s) for s in r.get("steps") or []]
        elif "do" in r:
            spec.options = _options(r["do"], r.get("with"), spec.note)
        elif "choose" in r:
            spec.options = _options(r["choose"], r.get("with"), spec.note)
        else:
            raise ScenarioError(f"repeat に do / choose / steps がありません: {raw!r}")
        return spec
    if "do" in raw:
        return StepSpec("do", _options(raw["do"], raw.get("with"), note), note=note)
    if "choose" in raw:
        return StepSpec("choose", _options(raw["choose"], raw.get("with"), ""), note=note,
                        optional=bool(raw.get("optional")))
    raise ScenarioError(f"step に do / choose / repeat がありません: {raw!r}")


def parse_scenario(data: dict[str, Any], path: Optional[Path] = None) -> Scenario:
    """経路の dict を ``Scenario`` にする。未登録の行為 id は ``ScenarioError``（PE9）。"""
    if not data.get("id"):
        raise ScenarioError(f"id がありません: {path}")
    steps = [_parse_step(s) for s in data.get("steps") or []]
    sc = Scenario(id=str(data["id"]), population=str(data.get("population") or "students"),
                  goal=str(data.get("goal") or ""), steps=steps,
                  params_required=[str(x) for x in data.get("params_required") or []],
                  stop_when=[str(x) for x in data.get("stop_when") or ["gave_up", "budget_exhausted"]],
                  expects=[e for e in data.get("expects") or [] if isinstance(e, dict)],
                  status=str(data.get("status") or "active"), path=path, raw=data)
    unknown = sorted(a for a in sc.referenced_actions() if a not in registry.REGISTRY)
    if unknown:
        raise ScenarioError(f"{sc.id}: 行為レジストリに無い行為: {unknown}")
    sessions = data.get("sessions", data.get("session"))
    sc.sessions = [int(sessions)] if isinstance(sessions, int) else (
        [int(x) for x in sessions] if isinstance(sessions, list) else None)
    runner = data.get("runner") or ["api"]
    sc.runner = [runner] if isinstance(runner, str) else list(runner)
    return sc


def find_scenario_file(scenario_id: str, root: Path = UXSIM_ROOT) -> Path:
    hits = sorted((root / "scenarios").rglob(f"{scenario_id}.yaml"))
    if not hits:
        raise ScenarioError(f"経路が見つかりません: {scenario_id}")
    return hits[0]


def load_scenario(scenario_id: str, root: Path = UXSIM_ROOT) -> Scenario:
    path = find_scenario_file(scenario_id, root)
    return parse_scenario(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, path)
