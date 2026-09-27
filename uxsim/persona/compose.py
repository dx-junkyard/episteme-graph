"""ペルソナの合成（行動型 archetype × 分野パック domain pack — §11.1 / §11.3）。

- 行動型: ``uxsim/archetypes/<population>/<id>.yaml``
- ペルソナ: ``uxsim/domains/<domain>/personas/<population>/<persona_id>.yaml``（``archetype: students/<id>``）
- 誤解・目標の本文: ``uxsim/domains/<domain>/knowledge/{misconceptions,goals}.yaml`` のキーから引く
- ``overrides`` は行動型の値を局所的に上書きする（dict は 1 段深くマージ）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from uxsim.config import UXSIM_ROOT

POPULATIONS = ("students", "teachers")


class CompositionError(ValueError):
    """参照が解決できない（archetype・誤解・目標のキーが実在しない等）。"""


@dataclass
class PersonaSpec:
    """合成済みのペルソナ（ペルソナ LLM の system プロンプトの材料）。"""

    id: str
    population: str
    domain: str
    archetype_id: str
    display_name: str = ""
    language: str = "ja"
    background: str = ""
    summary: str = ""
    habits: dict[str, Any] = field(default_factory=dict)
    voice: str = ""
    gives_up_when: str = ""
    asks_out_of_principle: list[str] = field(default_factory=list)
    knowledge: dict[str, list[str]] = field(default_factory=dict)
    misconceptions: list[dict[str, str]] = field(default_factory=list)  # [{key, text}]
    goals: list[dict[str, str]] = field(default_factory=list)  # [{key, text}]
    status: str = "active"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def screen(self) -> str:
        return "admin" if self.population == "teachers" else "learning"


def load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise CompositionError(f"ファイルがありません: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _text_of(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for k in ("text", "statement", "belief", "description", "label", "title", "summary"):
            if isinstance(value.get(k), str) and value[k].strip():
                return value[k].strip()
    return ""


def index_by_key(data: Any, wrapper: str = "") -> dict[str, str]:
    """``{key: 本文}`` / ``[{id|key, text}]`` / ``{wrapper: ...}`` のどれでも ``{key: 本文}`` に揃える。"""
    if isinstance(data, dict) and wrapper and wrapper in data and len(data) <= 2:
        data = data[wrapper]
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if k in ("id", "domain", "version"):
                continue
            out[str(k)] = _text_of(v) or str(k)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                key = item.get("id") or item.get("key")
                if key:
                    out[str(key)] = _text_of(item) or str(key)
    return out


def _merge(base: dict, over: dict) -> dict:
    merged = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def find_persona_file(domain: str, persona_id: str, root: Path = UXSIM_ROOT) -> Path:
    for pop in POPULATIONS:
        p = root / "domains" / domain / "personas" / pop / f"{persona_id}.yaml"
        if p.is_file():
            return p
    raise CompositionError(f"ペルソナが見つかりません: {domain}/{persona_id}")


def compose_persona(domain: str, persona_id: str, root: Path = UXSIM_ROOT) -> PersonaSpec:
    """ペルソナ 1 人を合成する。参照が解決できなければ ``CompositionError``。"""
    ppath = find_persona_file(domain, persona_id, root)
    population = ppath.parent.name
    persona = load_yaml(ppath)
    arche_ref = str(persona.get("archetype") or "")
    if "/" not in arche_ref:
        arche_ref = f"{population}/{arche_ref}"
    archetype = load_yaml(root / "archetypes" / f"{arche_ref}.yaml")
    merged = _merge(archetype, persona.get("overrides") or {})

    kdir = root / "domains" / domain / "knowledge"
    mis_index = index_by_key(load_yaml(kdir / "misconceptions.yaml"), "misconceptions") \
        if (kdir / "misconceptions.yaml").is_file() else {}
    goal_index = index_by_key(load_yaml(kdir / "goals.yaml"), "goals") if (kdir / "goals.yaml").is_file() else {}

    def resolve(keys: Any, index: dict[str, str], kind: str) -> list[dict[str, str]]:
        out = []
        for k in keys or []:
            if isinstance(k, dict):  # 本文を直接書いた形も許す
                out.append({"key": str(k.get("id") or k.get("key") or ""), "text": _text_of(k)})
                continue
            if str(k) not in index:
                raise CompositionError(f"{persona_id}: {kind} のキーが分野パックにありません: {k}")
            out.append({"key": str(k), "text": index[str(k)]})
        return out

    knowledge = persona.get("knowledge") or {}
    return PersonaSpec(
        id=str(persona.get("id") or persona_id),
        population=population,
        domain=domain,
        archetype_id=arche_ref,
        display_name=str(persona.get("display_name") or persona_id),
        language=str(persona.get("language") or "ja"),
        background=str(persona.get("background") or ""),
        summary=str(merged.get("summary") or ""),
        habits=dict(merged.get("habits") or {}),
        voice=str(merged.get("voice") or ""),
        gives_up_when=str(merged.get("gives_up_when") or ""),
        asks_out_of_principle=[str(x) for x in (merged.get("asks_out_of_principle") or [])
                               if not isinstance(x, dict)],
        knowledge={k: [str(x) for x in (knowledge.get(k) or [])] for k in ("knows", "vague", "unknown")},
        misconceptions=resolve(persona.get("misconceptions"), mis_index, "misconceptions"),
        goals=resolve(persona.get("goals"), goal_index, "goals"),
        status=str(persona.get("status") or "active"),
        raw=persona,
    )


def load_domain(domain: str, root: Path = UXSIM_ROOT) -> dict[str, Any]:
    """``domains/<domain>/domain.yaml``（無ければ空 dict）。"""
    p = root / "domains" / domain / "domain.yaml"
    return load_yaml(p) if p.is_file() else {}


def course_brief(domain: str, name: str, root: Path = UXSIM_ROOT) -> Optional[str]:
    p = root / "domains" / domain / "corpus" / "course_briefs" / (name if name.endswith(".md") else f"{name}.md")
    return p.read_text(encoding="utf-8") if p.is_file() else None
