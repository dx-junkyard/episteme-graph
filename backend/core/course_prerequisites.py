"""コース前提知識の ID 参照化と半順序検査（学ぶ単位の一級化 Phase 2 / P2-4）。

正本: ``docs/features/learning_units_design.md`` §6.4（不変条項 LU1 / LU3 / LU5 / LU6）。

このモジュールが引き受けるのは2つだけ:

1. ``resolve_prerequisite_topic_ids(topics)`` — ``topics[].prerequisites[]`` の各要素を
   ``{"name", "status", "topic_id"}`` の dict に正規化し、**正規化題名の完全一致**で
   同コース topic の ``id`` を ``topic_id`` に埋める（additive。一致しなければ ``None`` の
   まま = 推測しない, LU1/LU3）。
2. ``analyze_prerequisite_order(topics)`` — 前提の参照グラフを**コース構造だけ**から
   検査し、循環・冗長・未解決・前方参照を事実として返す（LU6: 学習者の痕跡・回答・
   履歴・習得状態を一切入力にしない。この関数の引数は topics 1 つだけで、
   ``test_course_prerequisites.py`` が inspect で固定する）。

制約:

- FastAPI / SQLAlchemy / LLM を import しない純関数モジュール（決定論・非LLM, LU3）。
- 入力を mutate しない（呼び出し側の course_data を書き換えない）。
- 事実文に**件数・割合・スコアを書かない**（LU5）。督促・命令口調も書かない（G6 と同型）。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

__all__ = [
    "PrerequisiteOrderReport",
    "analyze_prerequisite_order",
    "normalize_topic_title",
    "resolve_prerequisite_topic_ids",
]

#: prerequisites[] 要素の既定 status（course_data.CoursePrerequisite と同じ）。
DEFAULT_PREREQUISITE_STATUS = "not_started"


def normalize_topic_title(title: object) -> str:
    """題名の突合キー（NFKC 正規化 → casefold → 全空白除去）。

    表記の正本は元の文字列で、これは**完全一致の突合にだけ**使う（部分一致・
    類似度は使わない — 推測で結び直さないため）。
    """
    text = "" if title is None else str(title)
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in normalized if not ch.isspace())


def _topic_id(topic: dict) -> str:
    return str(topic.get("id") or "").strip()


def _topic_title(topic: dict) -> str:
    return str(topic.get("title") or "").strip()


def _prerequisite_fields(prereq: object) -> tuple[str, str, dict]:
    """前提要素から ``(name, topic_id, 元 dict)`` を取り出す（素の文字列にも対応）。"""
    if isinstance(prereq, dict):
        name = str(prereq.get("name") or "").strip()
        topic_id = str(prereq.get("topic_id") or "").strip()
        return name, topic_id, prereq
    return str(prereq or "").strip(), "", {}


def _title_index(topics: list) -> dict[str, str]:
    """正規化題名 → topic_id。**同じ題名が複数ある場合は引かない**（曖昧なら推測しない）。"""
    index: dict[str, str] = {}
    ambiguous: set[str] = set()
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        tid = _topic_id(topic)
        key = normalize_topic_title(_topic_title(topic))
        if not tid or not key:
            continue
        if key in index and index[key] != tid:
            ambiguous.add(key)
        index.setdefault(key, tid)
    for key in ambiguous:
        index.pop(key, None)
    return index


def resolve_prerequisite_topic_ids(topics: list) -> list[dict]:
    """``topics`` の前提要素を dict 化し、解決できた ``topic_id`` を埋めた**新しい**リストを返す。

    - 入力（topics とその要素）を mutate しない。
    - 素の文字列要素も ``{"name", "status", "topic_id"}`` の dict にする（既存フォーマットの
      救済であって、名前は落とさない = 情報を落とさない）。
    - 既に ``topic_id`` が入っている要素は尊重する（教員が直した値を上書きしない）。
    - 自分自身を指す解決はしない（題名が同じでも自己参照は作らない）。
    - 一致しなければ ``topic_id`` は ``None`` のまま（推測しない, LU3）。
    """
    if not isinstance(topics, list):
        return []
    index = _title_index(topics)

    resolved: list[dict] = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        new_topic = dict(topic)
        own_id = _topic_id(topic)
        prereqs = topic.get("prerequisites")
        new_prereqs: list[dict] = []
        for prereq in prereqs if isinstance(prereqs, list) else []:
            name, existing_id, raw = _prerequisite_fields(prereq)
            if not name and not existing_id:
                continue
            item = dict(raw)
            item["name"] = name
            item.setdefault("status", DEFAULT_PREREQUISITE_STATUS)
            topic_id: str | None = existing_id or None
            if topic_id is None:
                candidate = index.get(normalize_topic_title(name))
                if candidate and candidate != own_id:
                    topic_id = candidate
            item["topic_id"] = topic_id
            new_prereqs.append(item)
        new_topic["prerequisites"] = new_prereqs
        resolved.append(new_topic)
    return resolved


# ---------------------------------------------------------------------------
# 半順序検査（コース構造だけを入力にする。学習者の痕跡は読まない, LU6）
# ---------------------------------------------------------------------------


@dataclass
class PrerequisiteOrderReport:
    """前提の参照グラフの検査結果（数値を持たない — 事実の列挙だけ, LU5）。

    - ``cycles``: 循環の閉路（topic_id の並び。決定論順）
    - ``redundant``: ``(topic_id, 前提 topic_id, 経由 topic_id)`` — 推移的に導ける直接辺
    - ``unresolved``: ``(topic_id, 前提名)`` — 対応するトピックが引けない前提
    - ``forward_references``: ``(topic_id, 前提 topic_id, 別の章か)`` — 後ろに置かれた前提
    - ``labels``: topic_id → 表示題名（事実文の組み立て用。題名が無ければ id）
    """

    cycles: list[list[str]] = field(default_factory=list)
    redundant: list[tuple[str, str, str]] = field(default_factory=list)
    unresolved: list[tuple[str, str]] = field(default_factory=list)
    forward_references: list[tuple[str, str, bool]] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (
            self.cycles or self.redundant or self.unresolved or self.forward_references
        )

    def label(self, topic_id: str) -> str:
        return self.labels.get(topic_id) or topic_id

    def to_facts(self) -> list[str]:
        """日本語の事実文（件数・割合を書かない・督促しない, LU5 / G6）。"""
        facts: list[str] = []
        for cycle in self.cycles:
            facts.append(self._cycle_fact(cycle))
        for topic_id, prereq_id, via_id in self.redundant:
            facts.append(
                f"「{self.label(topic_id)}」の前提「{self.label(prereq_id)}」は、"
                f"「{self.label(via_id)}」を経て既に含まれています。"
            )
        for topic_id, name in self.unresolved:
            facts.append(
                f"「{self.label(topic_id)}」の前提「{name}」に対応するトピックが"
                "このコースにありません。"
            )
        for topic_id, prereq_id, other_chapter in self.forward_references:
            where = "後の章にある" if other_chapter else "同じ章の後ろにある"
            facts.append(
                f"「{self.label(topic_id)}」は、{where}「{self.label(prereq_id)}」を"
                "前提にしています。"
            )
        return facts

    def _cycle_fact(self, cycle: list[str]) -> str:
        if len(cycle) == 1:
            return f"「{self.label(cycle[0])}」は、自分自身を前提としています。"
        parts = []
        for i, topic_id in enumerate(cycle):
            nxt = cycle[(i + 1) % len(cycle)]
            parts.append(f"「{self.label(topic_id)}」は「{self.label(nxt)}」を前提")
        return "とし、".join(parts) + "としています。"


def _strongly_connected_components(
    order: list[str], deps: dict[str, list[str]]
) -> list[list[str]]:
    """Tarjan の SCC（反復版・決定論）。返りは ``order`` の並びに整えた各成分。"""
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    components: list[list[str]] = []
    position = {tid: i for i, tid in enumerate(order)}

    for root in order:
        if root in index_of:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child_i = work[-1]
            if child_i == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            recursed = False
            children = deps.get(node, [])
            while child_i < len(children):
                child = children[child_i]
                child_i += 1
                if child not in index_of:
                    work[-1] = (node, child_i)
                    work.append((child, 0))
                    recursed = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index_of[child])
            if recursed:
                continue
            work[-1] = (node, child_i)
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(sorted(component, key=lambda t: position.get(t, 0)))
    return sorted(components, key=lambda c: position.get(c[0], 0))


def _cycle_path(members: list[str], deps: dict[str, list[str]]) -> list[str]:
    """SCC の中から閉路を1本取り出す（先頭ノードから戻ってくる経路。決定論）。"""
    member_set = set(members)
    start = members[0]
    path: list[str] = []
    visited: set[str] = set()

    def walk(node: str) -> bool:
        path.append(node)
        for child in deps.get(node, []):
            if child == start:
                return True
            if child in member_set and child not in visited:
                visited.add(child)
                if walk(child):
                    return True
        path.pop()
        return False

    visited.add(start)
    if walk(start):
        return path
    return members


def _reachable(node: str, deps: dict[str, list[str]]) -> set[str]:
    """``node`` から辿れるノード集合（閉路があっても止まる）。"""
    seen: set[str] = set()
    stack = list(deps.get(node, []))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(deps.get(current, []))
    return seen


def analyze_prerequisite_order(topics: list) -> PrerequisiteOrderReport:
    """コース構造（topics とその prerequisites）だけから前提の半順序を検査する。

    **学習者の入力を受け取らない**（引数は topics ひとつだけ。LU6 / UC5 / UC7 の恒久排除を
    シグネチャで固定する）。DB も LLM も使わない。
    """
    resolved = resolve_prerequisite_topic_ids(topics)

    order: list[str] = []
    labels: dict[str, str] = {}
    position: dict[str, tuple[int, int]] = {}
    for seq, topic in enumerate(resolved):
        tid = _topic_id(topic)
        if not tid or tid in labels:
            continue
        order.append(tid)
        labels[tid] = _topic_title(topic) or tid
        chapter_index = topic.get("chapter_index")
        chapter = chapter_index if isinstance(chapter_index, int) else 0
        position[tid] = (chapter, seq)

    deps: dict[str, list[str]] = {tid: [] for tid in order}
    unresolved: list[tuple[str, str]] = []
    for topic in resolved:
        tid = _topic_id(topic)
        if tid not in deps:
            continue
        for prereq in topic.get("prerequisites") or []:
            prereq_id = prereq.get("topic_id")
            name = str(prereq.get("name") or "").strip()
            if prereq_id and prereq_id in deps:
                if prereq_id not in deps[tid]:
                    deps[tid].append(prereq_id)
            elif name:
                unresolved.append((tid, name))

    cycles: list[list[str]] = []
    cyclic_nodes: set[str] = set()
    for component in _strongly_connected_components(order, deps):
        if len(component) > 1:
            cycles.append(_cycle_path(component, deps))
            cyclic_nodes.update(component)
        elif component and component[0] in deps.get(component[0], []):
            cycles.append([component[0]])
            cyclic_nodes.add(component[0])

    redundant: list[tuple[str, str, str]] = []
    reach_cache: dict[str, set[str]] = {}
    for tid in order:
        if tid in cyclic_nodes:
            # 循環の中では「推移的に導ける」が意味を成さない（循環の事実文を先に出す）。
            continue
        for prereq_id in deps[tid]:
            via = None
            for other in deps[tid]:
                if other == prereq_id:
                    continue
                if other not in reach_cache:
                    reach_cache[other] = _reachable(other, deps)
                if prereq_id in reach_cache[other]:
                    via = other
                    break
            if via is not None:
                redundant.append((tid, prereq_id, via))

    forward_references: list[tuple[str, str, bool]] = []
    for tid in order:
        for prereq_id in deps[tid]:
            if tid in cyclic_nodes and prereq_id in cyclic_nodes:
                continue  # 循環として既に報告している
            here = position.get(tid)
            there = position.get(prereq_id)
            if here is None or there is None:
                continue
            if there > here:
                forward_references.append((tid, prereq_id, there[0] != here[0]))

    return PrerequisiteOrderReport(
        cycles=cycles,
        redundant=redundant,
        unresolved=unresolved,
        forward_references=forward_references,
        labels=labels,
    )
