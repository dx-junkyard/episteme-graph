"""理論モジュール層 — 読み時導出の本体（純関数）。

設計: ``docs/features/theory_module_layer_design.md`` §5（導出規則）/ §8.1（DTO）。

``build_theory_modules`` は **DB にも LLM にも embedding にも触れない**。入力は
route（``routes/theory_components.py::build_theory_modules_for_document``）が読んだ採用 run の
artifact と、正規化済みの ``graph_json`` だけで、出力は §8.1 の dict である。

不変条項の実装上の要点:

- TM1: 入力 dict を**書き換えない**（全て新しい dict / list を組む）。
- TM2: 同じ入力からは同じモジュールが出る。併合の同点は §5.4 の決定論順で割る。
- TM3: 成員は equation_chain / mixed_chain の「入力式と出力式の両方を持つ step」だけ。
  claim_chain は成員にしない。対応する詳細ノードがすべて debug 層か ``inferred`` の step も
  成員にしない。
- TM4: 分割の入力は式の依存と接点の本数だけ。stage・章・部品は色分けと照合にだけ使う。
- TM6 / TM10: 閾値・本数・多重度・指紋を DTO に出さない。内部 ID を表示ラベルに使わない。
- TM8: 例外を外に出さない。欠落は ``facts`` に事実文を1行足す。
"""

from __future__ import annotations

import re

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from core.element_vocab import THEORY_STAGE_LABELS as _ELEMENT_STAGE_LABELS
from core.element_vocab import operation_label as _vocab_operation_label

from core.theory_modules.schema import (
    BACKING_INFERRED,
    BACKING_ORDER,
    CLAIM_CHAIN_TYPES,
    DETAIL_GRAPH_LAYERS,
    EQUATION_ID_KEY_PREFIX,
    EQUATION_ID_TOKEN_RE,
    FACT_BUILD_FAILED,
    FACT_CLAIM_CHAINS_ONLY,
    FACT_CYCLE_PREFIX,
    FACT_INFERRED_STEPS_EXCLUDED,
    FACT_INTERFACE_TOO_WIDE,
    FACT_NO_CLAIMS,
    FACT_NO_COMPONENTS,
    FACT_NO_DERIVATIONS,
    FACT_NO_EQUATION_STEPS,
    FACT_NO_EQUATIONS,
    FACT_NO_GRAPH,
    FACT_SINKS,
    GRAPH_LAYER_DEBUG,
    INNER_HUB_MIN_CONSUMERS,
    INNER_INTERFACE_LIMIT,
    ISOLATED_CYCLE,
    ISOLATED_INTERFACE_TOO_WIDE,
    LEVEL_INNER,
    LEVEL_OUTER,
    MEMBER_CHAIN_TYPES,
    MODULE_IDENTITY_MIN_MEMBERS,
    MODULE_IDENTITY_MIN_PROCESS_KINDS,
    OUTER_INTERFACE_LIMIT,
    RECORDS_SKIP_BUILD_FAILED,
    RECORDS_SKIP_NO_DERIVATIONS,
    RULE_VERSION,
    SHARED_FOUNDATION_MIN_CONSUMERS,
    SINK_CHAIN_TYPES,
    THEORY_OBJECT_LABELS_MAX,
    THEORY_OBJECT_MAX,
    UNCLASSIFIED_EDGE_TYPE,
    clean_text,
    contains_internal_id,
    equation_display_label,
    has_printed_label,
    truncate_snippet,
)

# ---------------------------------------------------------------------------
# A層語彙（関数内 import）
#
# classify_operation / stage_for_edge_type / THEORY_STAGES / derivation_step_ref の正本は
# ``src/episteme_graph/agents/component_graph/schema.py``。本モジュールは backend だけを
# sys.path に置いた環境からも import され得るため、A層は関数内で読み、読めない環境では
# stage を空にして縮退する（分割は stage に依存しない = TM4 なので、モジュール自体は組める）。
# ---------------------------------------------------------------------------


def _a_layer():
    try:
        from episteme_graph.agents.component_graph import schema as cg_schema
    except Exception:  # noqa: BLE001 — A層が読めない環境では stage を空にする
        return None
    return cg_schema


def _step_ref(derivation_id: str, step_id: str) -> str:
    cg = _a_layer()
    if cg is not None:
        return cg.derivation_step_ref(derivation_id, step_id)
    chain = str(derivation_id or "").strip()
    step = str(step_id or "").strip()
    if not chain:
        return step
    return f"{chain}:{step}" if step else chain


def _stage_for_operation(operation: str) -> str:
    cg = _a_layer()
    if cg is None:
        return ""
    try:
        _verb, edge_type, _generic = cg.classify_operation(operation)
        return str(cg.stage_for_edge_type(edge_type) or "")
    except Exception:  # noqa: BLE001
        return ""


def _classify_operation(operation: str) -> tuple[str, bool]:
    """``(edge_type, generic)``。A層が読めない環境では ``("", False)``（§13.4 の縮退）。

    ``edge_type`` は ``component_graph.schema.classify_operation`` の第 2 要素（汎用は
    ``requires_review``、未知は ``transforms``）。保存用出力の構造の指紋と同一性候補の対象判定に
    だけ使い、分割には使わない（TM4）。
    """
    cg = _a_layer()
    if cg is None:
        return "", False
    try:
        _verb, edge_type, generic = cg.classify_operation(operation)
        return str(edge_type or ""), bool(generic)
    except Exception:  # noqa: BLE001
        return "", False


def _stage_order() -> list[str]:
    cg = _a_layer()
    if cg is not None:
        return list(cg.THEORY_STAGES)
    return list(_ELEMENT_STAGE_LABELS.keys())


def _operation_label(operation: str) -> str:
    """操作動詞の訳（``element_vocab.OPERATION_LABELS``）。未知は ""（fail-closed）。"""
    op = str(operation or "").strip()
    if not op:
        return ""
    label = _vocab_operation_label(op)
    if label:
        return label
    head = op.lower().replace("-", "_").split("_", 1)[0]
    return _vocab_operation_label(head)


# ---------------------------------------------------------------------------
# 小さなユーティリティ
# ---------------------------------------------------------------------------


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip() if not isinstance(item, (dict, list)) else ""
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _dedup(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


# ---------------------------------------------------------------------------
# 成員 step
# ---------------------------------------------------------------------------


@dataclass
class _Step:
    order: int  # 出現順（チェーンの並び × step の並び。§5.4 の同点規則に使う）
    operation: str
    inputs: list[str]
    outputs: list[str]
    occurrences: list[tuple[str, str]] = field(default_factory=list)
    required_claim_ids: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    node_ids: list[str] = field(default_factory=list)
    backing: str = ""
    stage: str = ""
    edge_type: str = ""
    generic: bool = False


@dataclass
class _Sink:
    operation: str
    inputs: list[str]
    outputs: list[str]


class _GraphIndex:
    """graph_json の詳細ノードを step と対応付けるための索引（読むだけ）。"""

    def __init__(self, graph_json: dict):
        self.nodes: list[dict] = []
        for node in _dicts(_mapping(graph_json).get("nodes")):
            node_id = str(node.get("component_id") or node.get("id") or "").strip()
            if not node_id:
                continue
            layer = str(node.get("graph_layer") or "").strip()
            if layer not in DETAIL_GRAPH_LAYERS:
                continue
            self.nodes.append(node)
        self.by_ref: dict[str, list[dict]] = {}
        for node in self.nodes:
            for ref in _ids(node.get("linked_derivation_ids")):
                self.by_ref.setdefault(ref, []).append(node)

    def nodes_for(self, derivation_id: str, step_id: str, inputs: list[str], outputs: list[str]) -> list[dict]:
        """(derivation_id, step_id) の詳細ノード。合成 ID が主経路、裸の step_id は derivation_id 併記で照合。

        裸の ``step_001`` はチェーンごとに振り直されるので、derivation_id と step_id の
        両方を持つノードに限る。さらに、ノードが式を持つなら step の入出力式と一致するものに
        絞る（複数チェーンに現れる step のノードが複数の derivation_id と step_id を並べて
        持つときの取り違えを防ぐ）。
        """
        composite = _step_ref(derivation_id, step_id)
        candidates: list[dict] = list(self.by_ref.get(composite, []))
        if not candidates:
            for node in self.by_ref.get(step_id, []):
                refs = set(_ids(node.get("linked_derivation_ids")))
                if derivation_id in refs:
                    candidates.append(node)
        out: list[dict] = []
        want_in, want_out = set(inputs), set(outputs)
        for node in candidates:
            node_in = set(_ids(node.get("input_equation_ids")))
            node_out = set(_ids(node.get("output_equation_ids")))
            if (node_in or node_out) and (node_in != want_in or node_out != want_out):
                continue
            if node not in out:
                out.append(node)
        return out


def _node_id(node: dict) -> str:
    return str(node.get("component_id") or node.get("id") or "").strip()


def _weakest_backing(statuses: Iterable[str]) -> str:
    """成員の裏付けの最も弱いもの（BACKING_ORDER の範囲）。無ければ ""。"""
    rank = {status: index for index, status in enumerate(BACKING_ORDER)}
    worst = ""
    for status in statuses:
        if status not in rank:
            continue
        if not worst or rank[status] > rank[worst]:
            worst = status
    return worst


def _collect_steps(chains: list[dict], graph: _GraphIndex) -> tuple[list[_Step], list[_Sink], bool]:
    """§5.2 の成員 step（重複除去済み）と sink を集める。戻り値の bool は推定 step の除外有無。"""
    steps_by_key: dict[tuple, _Step] = {}
    ordered: list[_Step] = []
    sinks: list[_Sink] = []
    sink_keys: set[tuple] = set()
    order = 0
    for chain in chains:
        chain_type = str(chain.get("chain_type") or "").strip()
        derivation_id = str(chain.get("derivation_id") or "").strip()
        for step in _dicts(chain.get("steps")):
            inputs = _ids(step.get("input_equation_ids"))
            outputs = _ids(step.get("output_equation_ids"))
            operation = str(step.get("operation") or chain.get("operation") or "").strip()
            if chain_type in SINK_CHAIN_TYPES:
                if not outputs and not inputs:
                    continue
                key = (operation, frozenset(inputs), frozenset(outputs))
                if key not in sink_keys:
                    sink_keys.add(key)
                    sinks.append(_Sink(operation=operation, inputs=inputs, outputs=outputs))
                continue
            if chain_type not in MEMBER_CHAIN_TYPES:
                continue  # claim_chain ほか（TM3 / §5.2-2）
            if not inputs or not outputs:
                continue  # TM3: 入力式と出力式の両方を持つ step だけ
            key = (operation, frozenset(inputs), frozenset(outputs))
            step_id = str(step.get("step_id") or "").strip()
            entry = steps_by_key.get(key)
            if entry is None:
                entry = _Step(order=order, operation=operation, inputs=inputs, outputs=outputs)
                order += 1
                steps_by_key[key] = entry
                ordered.append(entry)
            if (derivation_id, step_id) not in entry.occurrences:
                entry.occurrences.append((derivation_id, step_id))
            entry.required_claim_ids = _dedup(entry.required_claim_ids + _ids(step.get("required_claim_ids")))
            entry.assumptions = _dedup(
                entry.assumptions + [truncate_snippet(a) for a in _ids(step.get("assumption_ids"))]
            )

    kept: list[_Step] = []
    excluded_inferred = False
    for entry in ordered:
        nodes: list[dict] = []
        for derivation_id, step_id in entry.occurrences:
            for node in graph.nodes_for(derivation_id, step_id, entry.inputs, entry.outputs):
                if node not in nodes:
                    nodes.append(node)
        usable = [
            n for n in nodes
            if str(n.get("graph_layer") or "") != GRAPH_LAYER_DEBUG
            and str(n.get("source_backing_status") or "") != BACKING_INFERRED
        ]
        if nodes and not usable:
            excluded_inferred = True  # TM3: debug 層・inferred の step は使わない
            continue
        entry.node_ids = _dedup(_node_id(n) for n in usable)
        entry.backing = _weakest_backing(str(n.get("source_backing_status") or "") for n in usable)
        entry.stage = _stage_for_operation(entry.operation)
        entry.edge_type, entry.generic = _classify_operation(entry.operation)
        kept.append(entry)
    for index, entry in enumerate(kept):
        entry.order = index
    return kept, sinks, excluded_inferred


# ---------------------------------------------------------------------------
# 式の依存・共有の基礎・循環
# ---------------------------------------------------------------------------


class _Flow:
    """成員 step 間の式の受け渡し（生産者・消費者）。"""

    def __init__(self, steps: list[_Step], sinks: list[_Sink]):
        self.steps = steps
        self.producers: dict[str, list[int]] = {}
        self.consumers: dict[str, list[int]] = {}
        for step in steps:
            for eq in step.outputs:
                self.producers.setdefault(eq, []).append(step.order)
            for eq in step.inputs:
                self.consumers.setdefault(eq, []).append(step.order)
        self.sink_consumed: set[str] = {eq for sink in sinks for eq in sink.inputs}
        #: sink が生む式（論文全体をまとめる手順の結果）。成員 step も同じ式を生むとき、
        #: その式は sink の結果として sink 側に記録し、モジュールの「外へ出す式」に数えない
        #: （実装記録 §12 の逸脱 2）。
        self.sink_produced: set[str] = {eq for sink in sinks for eq in sink.outputs}
        # §5.3: 入次数を問わず、消費する成員 step が閾値以上の式
        self.foundation: set[str] = {
            eq for eq, users in self.consumers.items()
            if len(set(users)) >= SHARED_FOUNDATION_MIN_CONSUMERS
        }

    def produced(self, members: Iterable[int]) -> list[str]:
        return _dedup(eq for index in sorted(members) for eq in self.steps[index].outputs)

    def consumed(self, members: Iterable[int]) -> list[str]:
        return _dedup(eq for index in sorted(members) for eq in self.steps[index].inputs)

    def interface(self, members: frozenset[int]) -> tuple[list[str], list[str], list[str]]:
        """(外から受ける式, 外へ出す式, sink へ渡す式)。共有の基礎は数えない（§5.4）。"""
        produced = self.produced(members)
        produced_set = set(produced)
        inputs = [
            eq for eq in self.consumed(members)
            if eq not in produced_set and eq not in self.foundation
        ]
        outputs: list[str] = []
        to_sink: list[str] = []
        for eq in produced:
            if eq in self.foundation:
                continue
            users = set(self.consumers.get(eq, []))
            if users - members:
                outputs.append(eq)
            elif not users and eq not in self.sink_consumed and eq not in self.sink_produced:
                outputs.append(eq)  # どの step にも消費されない = 論文の結果
            elif eq in self.sink_consumed or eq in self.sink_produced:
                to_sink.append(eq)
        return inputs, outputs, to_sink

    def width(self, members: frozenset[int]) -> int:
        inputs, outputs, _ = self.interface(members)
        return len(inputs) + len(outputs)


def _cyclic_equations(steps: list[_Step]) -> list[set[str]]:
    """式の依存グラフの強連結成分のうち、式を2つ以上含むもの（反復 Tarjan・決定論順）。"""
    adjacency: dict[str, list[str]] = {}
    for step in steps:
        for src in step.inputs:
            targets = adjacency.setdefault(src, [])
            for dst in step.outputs:
                if dst not in targets:
                    targets.append(dst)
            for dst in step.outputs:
                adjacency.setdefault(dst, [])
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[set[str]] = []
    counter = 0
    for root in adjacency:
        if root in index_of:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child_index = work.pop()
            if child_index == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            children = adjacency.get(node, [])
            if child_index < len(children):
                work.append((node, child_index + 1))
                child = children[child_index]
                if child not in index_of:
                    work.append((child, 0))
                elif child in on_stack:
                    low[node] = min(low[node], index_of[child])
                continue
            if low[node] == index_of[node]:
                component: set[str] = set()
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.add(member)
                    if member == node:
                        break
                if len(component) >= 2:
                    components.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return components


# ---------------------------------------------------------------------------
# 併合（§5.4）
# ---------------------------------------------------------------------------


def _depends(flow: _Flow, a: frozenset[int], b: frozenset[int]) -> bool:
    """モジュール a が生む式をモジュール b が消費するか（共有の基礎も依存として数える）。"""
    produced = set(flow.produced(a))
    return any(eq in produced for eq in flow.consumed(b))


def _reachable_via_third(
    graph: dict[int, set[int]], start: int, goal: int
) -> bool:
    """start から goal へ、start と goal 以外のモジュールを少なくとも1つ経由して到達できるか。"""
    frontier = [n for n in sorted(graph.get(start, set())) if n not in (start, goal)]
    seen: set[int] = set(frontier)
    while frontier:
        node = frontier.pop()
        for nxt in sorted(graph.get(node, set())):
            if nxt == goal:
                return True
            if nxt == start or nxt in seen:
                continue
            seen.add(nxt)
            frontier.append(nxt)
    return False


def _merge_modules(
    flow: _Flow,
    initial: list[frozenset[int]],
    limit: int,
) -> list[frozenset[int]]:
    """接点の本数が ``limit`` 以下を保つ限り、併合後の接点が最小の隣接対から貪欲に併合する。

    同点は ①併合後の成員数が少ない ②成員の出現順（最小値）が早い ③もう一方の出現順が早い
    の順で割る（TM2）。隣接は「一方が生む式を他方が消費する」で、共有の基礎も含めて数える
    （接点の本数には数えない）。併合するとモジュール間に循環ができる対は候補にしない。
    循環に関わる step は呼び出し側が1つの初期モジュールに縮約して渡す（§5.5 の読み替え）。
    """
    modules: list[frozenset[int]] = list(initial)
    while True:
        dependency: dict[int, set[int]] = {i: set() for i in range(len(modules))}
        for i, a in enumerate(modules):
            for j, b in enumerate(modules):
                if i != j and _depends(flow, a, b):
                    dependency[i].add(j)
        best: tuple | None = None
        best_pair: tuple[int, int] | None = None
        for i in range(len(modules)):
            for j in range(i + 1, len(modules)):
                if j not in dependency[i] and i not in dependency[j]:
                    continue  # 隣接していない
                merged = modules[i] | modules[j]
                width = flow.width(merged)
                if width > limit:
                    continue
                if _reachable_via_third(dependency, i, j) or _reachable_via_third(dependency, j, i):
                    continue  # 併合するとモジュール間に循環ができる
                first_i, first_j = min(modules[i]), min(modules[j])
                key = (width, len(merged), min(first_i, first_j), max(first_i, first_j))
                if best is None or key < best:
                    best = key
                    best_pair = (i, j)
        if best_pair is None:
            break
        i, j = best_pair
        merged = modules[i] | modules[j]
        modules = [m for k, m in enumerate(modules) if k not in (i, j)] + [merged]
    return sorted(modules, key=lambda m: min(m))


# ---------------------------------------------------------------------------
# 内側 2 段目（§5.4 / 実装記録 §12.3）
# ---------------------------------------------------------------------------


def _inner_handoffs(
    flow: _Flow, units: list[frozenset[int]], outer_members: frozenset[int]
) -> dict[tuple[int, int], list[str]]:
    """外枠の中の初期モジュール間の受け渡し ``(生む側, 使う側) -> 式``。共有の基礎は数えない。

    外枠そのものの入出力（外枠の外で生まれる式・外枠の外で使われる式・sink へ渡す式）は、
    内側から見れば所与なので受け渡しに含めない。
    """
    unit_of: dict[int, int] = {}
    for index, unit in enumerate(units):
        for order in unit:
            unit_of[order] = index
    handoffs: dict[tuple[int, int], list[str]] = {}
    for index, unit in enumerate(units):
        for eq in flow.produced(unit):
            if eq in flow.foundation:
                continue
            for consumer in sorted(set(flow.consumers.get(eq, []))):
                if consumer not in outer_members:
                    continue
                target = unit_of[consumer]
                if target == index:
                    continue
                passed = handoffs.setdefault((index, target), [])
                if eq not in passed:
                    passed.append(eq)
    return handoffs


def _components(size: int, links: Iterable[tuple[int, int]]) -> list[list[int]]:
    """無向の連結成分（決定論: 各成分は昇順、成分の並びは最小の添字の順）。"""
    parent = list(range(size))

    def _find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for a, b in links:
        root_a, root_b = _find(a), _find(b)
        if root_a != root_b:
            parent[max(root_a, root_b)] = min(root_a, root_b)
    groups: dict[int, list[int]] = {}
    for node in range(size):
        groups.setdefault(_find(node), []).append(node)
    return sorted(groups.values(), key=lambda group: group[0])


def _inner_width(handoffs: dict[tuple[int, int], list[str]], group: set[int]) -> int:
    """内側の接点の本数 = 外枠の中で、group の外の初期モジュールと受け渡す式の本数。"""
    received: set[str] = set()
    sent: set[str] = set()
    for (source, target), equations in handoffs.items():
        if source in group and target not in group:
            sent |= set(equations)
        elif target in group and source not in group:
            received |= set(equations)
    return len(received) + len(sent)


def _inner_modules(
    flow: _Flow,
    units: list[frozenset[int]],
    outer_members: frozenset[int],
    cycle_groups: list[frozenset[int]],
) -> list[frozenset[int]]:
    """外枠を内側モジュールに分ける（§5.4 / 実装記録 §12.3）。

    外枠の中は共有の基礎と外の入出力を除けば閉じていることが多く（2609.15375v1 の計算本体は
    接点 0）、「接点が上限以下なら併合する」外枠の手続を小さな上限で回し直しても、閉じた本体は
    丸ごと 1 つに戻るか、1〜2 手順の断片に割れるだけになる。内側は併合ではなく**境目を入れる**:

    1. 外枠の中の初期モジュール（循環は縮約済み）どうしを、共有の基礎を除く受け渡しで結ぶ。
    2. **先頭になる初期モジュール**を決める — 循環のまとまり（互いに定める式の組）と、
       生む式が外枠の中の ``INNER_HUB_MIN_CONSUMERS`` 個以上の他の初期モジュールに使われる
       もの（以降の手順が共通に土台にする式を置く手順）。
    3. 先頭へ入る受け渡しを切る。出現順に先頭を 1 つずつ試し、切ったあとの全ての内側モジュールの
       接点（外枠の中で他と受け渡す式の本数）が ``INNER_INTERFACE_LIMIT`` 以下のときだけ採る。
    4. 残った受け渡しの連結成分が内側モジュール。共有の基礎だけでつながる手順（基礎を導く手順・
       基礎だけから結果を出す手順）は単独の内側モジュールになる（基礎は接点にも結び付きにも
       数えない = §5.3 と同じ扱い）。

    stage・章・部品は使わない（TM4）。
    """
    ordered = sorted(units, key=lambda unit: min(unit))
    handoffs = _inner_handoffs(flow, ordered, outer_members)
    cycle_set = set(cycle_groups)
    users: dict[tuple[int, str], set[int]] = {}
    for (source, target), equations in handoffs.items():
        for eq in equations:
            users.setdefault((source, eq), set()).add(target)
    leaders = sorted(
        {index for index, unit in enumerate(ordered) if unit in cycle_set}
        | {source for (source, _eq), targets in users.items() if len(targets) >= INNER_HUB_MIN_CONSUMERS}
    )

    def _groups(cut: set[int]) -> list[list[int]]:
        links = [pair for pair in handoffs if pair[1] not in cut]
        return _components(len(ordered), links)

    accepted: set[int] = set()
    for leader in leaders:
        trial = accepted | {leader}
        if all(_inner_width(handoffs, set(group)) <= INNER_INTERFACE_LIMIT for group in _groups(trial)):
            accepted = trial
    modules = [frozenset().union(*(ordered[i] for i in group)) for group in _groups(accepted)]
    return sorted(modules, key=lambda members: min(members))


# ---------------------------------------------------------------------------
# 表示の組み立て
# ---------------------------------------------------------------------------


_SYNTHESIZED_CLAIM_TEXT_RE = re.compile(
    r"^\s*(?:In (?:an )?equation(?: of this paper| \()|Equation \(|An equation of this paper)",
    re.IGNORECASE,
)


def _is_synthesized_claim(claim: dict) -> bool:
    """式から機械合成された主張か（equation_claim_synthesis 由来）。

    出所は ``origin == "equation_synthesis"`` が正だが、旧 artifact は ``origin`` を持たず
    ``claim_id`` が ``synth_claim_*`` で、本文が定型文で始まる。3 つのどれかで判定する
    （分野語ではなく合成器の定型文なので domain-independent — TM5）。
    """
    if str(claim.get("origin") or "") == "equation_synthesis":
        return True
    if str(claim.get("claim_id") or "").startswith("synth_claim"):
        return True
    return bool(_SYNTHESIZED_CLAIM_TEXT_RE.match(str(claim.get("text") or "")))


_SYNTH_DEFINES_RE = re.compile(r"defines\s+(\$[^$]+\$)")
_SYNTH_DEPENDS_RE = re.compile(r",\s*(\$[^$]+\$)\s+depends on")
_PLAIN_WRAPPER_COMMANDS = (
    "mathrm|rm|bm|mathbf|boldsymbol|text|textrm|mathcal|mathbb|mathfrak|mathit|operatorname|"
    "tilde|hat|bar|vec|dot|ddot|overline|underline|widetilde|widehat"
)
_PLAIN_WRAPPERS_RE = re.compile(r"\\(?:" + _PLAIN_WRAPPER_COMMANDS + r")\s*\{([^{}]*)\}")
_PLAIN_WRAPPERS_BARE_RE = re.compile(r"\\(?:" + _PLAIN_WRAPPER_COMMANDS + r")\s+([A-Za-z0-9])")
_PLAIN_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω", "odot": "⊙", "infty": "∞", "partial": "∂",
    "nabla": "∇", "times": "×", "cdot": "·", "pm": "±", "ell": "ℓ", "hbar": "ℏ",
}


def _symbol_of_synthesized(text: str) -> str:
    """合成主張の定型文から対象の記号（``$…$``）を取り出す。定型に合わなければ ""。"""
    for pattern in (_SYNTH_DEFINES_RE, _SYNTH_DEPENDS_RE):
        match = pattern.search(text or "")
        if match:
            return match.group(1).strip()
    return ""


def plain_math(text: str) -> str:
    """``$…$`` の LaTeX を、ノードのラベル（プレーンテキスト）用に読める形へ落とす。

    数式描画の代替ではなく、``\\Delta\\Sigma(R)`` を「ΔΣ(R)」程度に整えるだけ。詳細ペインは
    ``theory_object`` の ``$…$`` を数式として描く。
    """
    out = str(text or "").replace("$", "")
    # 二重化した区切り（JSON 由来の ``\\Theta`` / TeX の改行 ``\\``）を先に畳む
    # （第 14 周: 「\Θ^2_GW\」の残骸）。関係記号は読める記号へ（``z\mid Λ`` →「z | Λ」）。
    out = re.sub(r"\\\\+(?=[A-Za-z])", r"\\", out)
    out = re.sub(r"\\\\+", " ", out)
    out = re.sub(r"\\(?:mid|vert)\b", " | ", out)
    # 記号名の置換は装飾コマンドを剥がす前に行う（剥がした後だと ``\\cdot\\vec{k}`` が
    # ``\\cdotk`` になって名前の境界が消える）。
    out = re.sub(r"\\([A-Za-z]+)", lambda m: _PLAIN_GREEK.get(m.group(1), m.group(0)), out)
    for _ in range(3):
        out = _PLAIN_WRAPPERS_RE.sub(r"\1", out)
        out = _PLAIN_WRAPPERS_BARE_RE.sub(r"\1", out)
    out = re.sub(r"\\(?:rm|bf|it|bm|displaystyle)\b\s*", "", out)
    out = re.sub(r"\\(?:left|right|,|;|!|quad|qquad)", "", out)
    out = re.sub(r"\\([A-Za-z]+)", r"\1", out)
    out = out.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", out).strip()


class _Labels:
    """式の表示ラベル・主張本文・部品名の索引（読むだけ）。"""

    def __init__(self, artifacts: dict):
        equation_payload = _mapping(artifacts.get("equation_semantics"))
        records = _dicts(equation_payload.get("records")) or _dicts(equation_payload.get("equations"))
        self.equation_records: dict[str, dict] = {}
        for record in records:
            eq_id = str(record.get("equation_id") or "").strip()
            if eq_id and eq_id not in self.equation_records:
                self.equation_records[eq_id] = record
        self.claims: list[dict] = _dicts(_mapping(artifacts.get("claim_object_builder")).get("claims"))
        self.claims_by_id: dict[str, dict] = {}
        for claim in self.claims:
            claim_id = str(claim.get("claim_id") or "").strip()
            if claim_id and claim_id not in self.claims_by_id:
                self.claims_by_id[claim_id] = claim
        self.components: list[dict] = _dicts(_mapping(artifacts.get("component_assembly")).get("components"))

    def equation_label(self, eq_id: str) -> str:
        return equation_display_label(self.equation_records.get(eq_id))

    def equation_item(self, eq_id: str) -> dict:
        return {"equation_id": eq_id, "display_label": self.equation_label(eq_id)}

    def mask_equation_ids(self, text: str, *, printed_only: bool) -> str:
        """本文中の式 ID を印字番号に置き換える。``printed_only`` なら印字番号の無い式で ""。"""
        failed = False

        def _replace(match) -> str:
            nonlocal failed
            record = self.equation_records.get(match.group(0))
            if printed_only and not has_printed_label(record):
                failed = True
                return ""
            label = self.equation_label(match.group(0))
            return label if has_printed_label(record) else "番号なし"

        replaced = EQUATION_ID_TOKEN_RE.sub(_replace, text)
        if failed or contains_internal_id(replaced):
            return ""
        return replaced

    def claim_origin_label(self, claim_id: str) -> str:
        claim = self.claims_by_id.get(claim_id)
        if claim is None:
            return ""
        return CLAIM_ORIGIN_SYNTHESIZED_LABEL if _is_synthesized_claim(claim) else CLAIM_ORIGIN_PAPER_LABEL

    def claim_text(self, claim_id: str) -> str:
        claim = self.claims_by_id.get(claim_id) or {}
        text = clean_text(claim.get("text") or claim.get("normalized_text"))
        if not text:
            return ""
        if contains_internal_id(text):
            text = self.mask_equation_ids(text, printed_only=False)
        return truncate_snippet(text)

    def theory_object(self, equations: list[str], scope: set[str]) -> str:
        """外へ出す式に結び付く atomic claim の本文 → 無ければ式の印字番号（§5.6）。

        ``scope`` はモジュールが扱う式（生む式 ∪ 消費する式）。主張の ``equation_ids`` が
        ``scope`` からはみ出す主張（論文全体の式系をまとめる系レベルの主張など）は、この
        モジュールの対象を言わないので使わない。
        """
        position = {eq: index for index, eq in enumerate(equations)}
        # 並び: ⓪本文由来の主張を、式から機械合成した主張（「Equation (…) defines …」
        # 「In an equation of this paper, … depends on …」の定型文）より先 ①結び付く式が
        # 少ない主張ほど先（多数の式にまたがる系レベルの主張は、個々のモジュールの対象を
        # 言わない）②外へ出す式の並びで早い ③artifact の出現順。合成主張は本文由来が無い
        # ときの補欠で、式の印字番号よりは先に使う。
        candidates: list[tuple[tuple[int, int, int, int], dict]] = []
        for order, claim in enumerate(self.claims):
            atomic = claim.get("is_atomic") is True or str(claim.get("atomicity") or "") == "atomic"
            if not atomic:
                continue
            claim_equations = _ids(claim.get("equation_ids"))
            hits = [position[eq] for eq in claim_equations if eq in position]
            if not hits or not set(claim_equations) <= scope:
                continue
            synthesized = 1 if _is_synthesized_claim(claim) else 0
            candidates.append(((synthesized, len(claim_equations), min(hits), order), claim))
        candidates.sort(key=lambda item: item[0])
        for rank, claim in candidates:  # 1) 本文由来の主張で、内部 ID を含まない本文
            if rank[0]:
                break
            text = clean_text(claim.get("text"))
            if text and not contains_internal_id(text):
                return truncate_snippet(text, THEORY_OBJECT_MAX)
        # 2) 合成主張は定型文（「… defines $S$.」「…, $T$ depends on …」）なので本文を
        #    そのまま並べず、対象の記号だけを取り出して列挙する（最大 THEORY_OBJECT_LABELS_MAX）。
        symbols = _dedup(
            sym for _rank, claim in candidates
            for sym in [_symbol_of_synthesized(clean_text(claim.get("text")))] if sym
        )
        if symbols:
            return "、".join(symbols[:THEORY_OBJECT_LABELS_MAX])
        for _rank, claim in candidates:  # 2) 式 ID を印字番号に置き換えられる本文
            text = self.mask_equation_ids(clean_text(claim.get("text")), printed_only=True)
            if text:
                return truncate_snippet(text, THEORY_OBJECT_MAX)
        labels = _dedup(self.equation_label(eq) for eq in equations[:THEORY_OBJECT_LABELS_MAX])
        return truncate_snippet("、".join(labels), THEORY_OBJECT_MAX)

    def components_for(self, equations: list[str]) -> list[dict]:
        wanted = set(equations)
        out: list[dict] = []
        seen: set[str] = set()
        if not wanted:
            return out
        for component in self.components:
            eqs = set(_ids(component.get("output_equation_ids"))) | set(_ids(component.get("linked_equation_ids")))
            if not eqs & wanted:
                continue
            name = clean_text(component.get("name") or component.get("label"))
            if not name or contains_internal_id(name) or name in seen:
                continue
            seen.add(name)
            out.append({"name": truncate_snippet(name)})
        return out


def _equation_key_material(produced: list[str], equation_stable_keys: Mapping[str, str]) -> list[str]:
    """生む式ごとのキーの材料（§13.3）。写像に無い式は ``eqid:`` + equation_id。"""
    out: list[str] = []
    for eq in produced:
        key = str(equation_stable_keys.get(eq) or "").strip()
        out.append(key if key else EQUATION_ID_KEY_PREFIX + eq)
    return out


def _module_key(
    document_id: str,
    produced: list[str],
    level: str,
    taken: set[str],
    equation_stable_keys: Mapping[str, str],
) -> str:
    """§5.7 / §13.3 の読み時キー（材料 = 生む式の equation stable_key の昇順列 + level）。"""
    material_keys = _equation_key_material(produced, equation_stable_keys)
    material = "\x1f".join([document_id, ",".join(sorted(material_keys)), level])
    base = RULE_VERSION + ":" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    key = base
    suffix = 2
    while key in taken:
        key = f"{base}#{suffix}"
        suffix += 1
    taken.add(key)
    return key


def _module_dto(
    *,
    flow: _Flow,
    labels: _Labels,
    members: frozenset[int],
    level: str,
    key: str,
    parent_key: str | None,
    isolated_reason: str | None,
    stage_order: list[str],
) -> dict:
    ordered = [flow.steps[index] for index in sorted(members)]
    inputs, outputs, to_sink = flow.interface(members)
    used_foundation = [eq for eq in flow.consumed(members) if eq in flow.foundation]
    representative = outputs or to_sink or [eq for eq in flow.produced(members) if eq not in flow.foundation] \
        or flow.produced(members)

    verbs = _dedup(_operation_label(step.operation) for step in ordered)
    theory_object = labels.theory_object(
        representative, set(flow.produced(members)) | set(flow.consumed(members))
    )
    verb_text = "・".join(verbs)
    if verb_text and theory_object:
        label = f"{verb_text}: {theory_object}"
        visual_label = f"{verb_text}: {plain_math(theory_object)}"
    else:
        label = verb_text or theory_object
        visual_label = verb_text or plain_math(theory_object)

    stage_counts: dict[str, int] = {}
    for step in ordered:
        if step.stage:
            stage_counts[step.stage] = stage_counts.get(step.stage, 0) + 1
    stage_keys = [stage for stage in stage_order if stage in stage_counts]
    stage_keys += [stage for stage in stage_counts if stage not in stage_keys]
    dominant = ""
    for stage in stage_keys:  # 最多・同数は THEORY_STAGES の順で早い方
        if not dominant or stage_counts[stage] > stage_counts[dominant]:
            dominant = stage

    backing = _weakest_backing(step.backing for step in ordered) or BACKING_ORDER[-1]

    required_ids = _dedup(cid for step in ordered for cid in step.required_claim_ids)
    assumptions = _dedup(a for step in ordered for a in step.assumptions)

    member_rows = []
    for step in ordered:
        member_rows.append({
            "step_refs": [_step_ref(did, sid) for did, sid in step.occurrences],
            "node_ids": list(step.node_ids),
            "operation_label": _operation_label(step.operation),
            "stage_key": step.stage,
            "input_labels": [labels.equation_label(eq) for eq in step.inputs],
            "output_labels": [labels.equation_label(eq) for eq in step.outputs],
        })

    return {
        "module_key": key,
        "level": level,
        "parent_module_key": parent_key,
        "label": label,
        "visual_label": visual_label,
        "process_verbs": verbs,
        "theory_object": theory_object,
        "stage_keys": stage_keys,
        "dominant_stage": dominant,
        "source_backing_status": backing,
        "inputs": [labels.equation_item(eq) for eq in inputs],
        "outputs": [labels.equation_item(eq) for eq in outputs],
        "foundation": [labels.equation_item(eq) for eq in used_foundation],
        "required_claims": [
            {
                "claim_id": cid,
                "text": labels.claim_text(cid),
                "origin_label": labels.claim_origin_label(cid),
            }
            for cid in required_ids
        ],
        "assumption_ids": assumptions,
        # 前提の本文に出所の種別を添える（第 14 周 brain-te03 seq4/seq8: 図の注釈や「Likely…」の
        # 推測文が論文の前提と区別なく並んでいた）。assumption_ids は互換のため残す。
        "assumptions": [
            {"text": text, "origin_label": assumption_origin_label(text)} for text in assumptions
        ],
        # 同名のモジュールが並んでも区別できる副題（生む式の印字番号・内部 ID なし）。
        "subtitle": _module_subtitle(labels, outputs or representative),
        "members": member_rows,
        "components_for_comparison": labels.components_for(representative),
        "isolated_reason": isolated_reason,
    }


CLAIM_ORIGIN_PAPER_LABEL = "本文の主張"
CLAIM_ORIGIN_SYNTHESIZED_LABEL = "式から機械的に組んだ文（本文の引用ではありません）"
ASSUMPTION_ORIGIN_PAPER_LABEL = "導出の記録にある前提"
ASSUMPTION_ORIGIN_FIGURE_LABEL = "図の注記に由来する前提"
ASSUMPTION_ORIGIN_INFERRED_LABEL = "解析が推測で補った前提（未確認）"

_FIGURE_NOTE_RE = re.compile(r"\b(?:panel|fig\.?|figure|subplot)\b|図", re.IGNORECASE)
_HEDGE_RE = re.compile(r"^\s*(?:likely|probably|possibly|presumably|perhaps|may|might|assum(?:e|ed|ing))\b", re.IGNORECASE)


def assumption_origin_label(text: str) -> str:
    """前提の本文の出所の種別（決定論・分野中立。推測文と図の注記を論文の前提と分ける）。"""
    value = str(text or "")
    if _HEDGE_RE.search(value):
        return ASSUMPTION_ORIGIN_INFERRED_LABEL
    if _FIGURE_NOTE_RE.search(value):
        return ASSUMPTION_ORIGIN_FIGURE_LABEL
    return ASSUMPTION_ORIGIN_PAPER_LABEL


def _module_subtitle(labels: "_Labels", equations: list[str]) -> str:
    printed = _dedup(
        labels.equation_label(eq) for eq in equations
        if has_printed_label(labels.equation_records.get(eq))
    )
    if printed:
        return "生む式: " + "、".join(printed[:THEORY_OBJECT_LABELS_MAX])
    # 番号なしの式は左辺の記号を添えて区別する（第 15 周 te-03 seq4: 同名の内側モジュール）。
    from core.theory_modules.schema import equation_display_name

    unnumbered = _dedup(
        equation_display_name(labels.equation_records.get(eq)) for eq in equations
    )
    if unnumbered:
        return "生む式: " + "、".join(unnumbered[:THEORY_OBJECT_LABELS_MAX])
    return ""


def _structure_fingerprint(level: str, steps: list[_Step], inputs: list[str], outputs: list[str], premise: bool) -> str:
    """構造の指紋（§13.4）。**内部表現**で DTO・API・監査・候補の文面に出さない（TM12）。

    ``{rule_version}|{level}|ops={edge_type}:{多重度},…|in={本数}|out={本数}|premise={0|1}``。
    共有の基礎は入れない（§6）。A層が読めず分類できない手順は ``UNCLASSIFIED_EDGE_TYPE``。
    """
    multiplicity: dict[str, int] = {}
    for step in steps:
        edge_type = step.edge_type or UNCLASSIFIED_EDGE_TYPE
        multiplicity[edge_type] = multiplicity.get(edge_type, 0) + 1
    ops = ",".join(f"{edge_type}:{multiplicity[edge_type]}" for edge_type in sorted(multiplicity))
    return (
        f"{RULE_VERSION}|{level}|ops={ops}|in={len(inputs)}|out={len(outputs)}"
        f"|premise={1 if premise else 0}"
    )


def _identity_eligible(level: str, steps: list[_Step]) -> bool:
    """規則 ⑤ の対象か（§13.4）: 外枠・成員の下限・汎用でない工程の型の種類の下限・全手順が分類済み。"""
    if level != LEVEL_OUTER or len(steps) < MODULE_IDENTITY_MIN_MEMBERS:
        return False
    if any(not step.edge_type for step in steps):
        return False  # 分類できない工程で一致を言わない
    kinds = {step.edge_type for step in steps if not step.generic}
    return len(kinds) >= MODULE_IDENTITY_MIN_PROCESS_KINDS


def _module_record(
    *,
    flow: _Flow,
    members: frozenset[int],
    dto: dict,
    equation_stable_keys: Mapping[str, str],
) -> dict:
    """保存用の 1 行（§13.4）。DTO とは別の dict で、指紋・式の stable_key はここにだけ載る。"""
    ordered = [flow.steps[index] for index in sorted(members)]
    inputs, outputs, to_sink = flow.interface(members)
    produced = flow.produced(members)
    used_foundation = [eq for eq in flow.consumed(members) if eq in flow.foundation]
    required_ids = _dedup(cid for step in ordered for cid in step.required_claim_ids)
    assumptions = _dedup(a for step in ordered for a in step.assumptions)
    level = str(dto.get("level") or "")
    return {
        "module_key": dto.get("module_key"),
        "level": level,
        "parent_module_key": dto.get("parent_module_key"),
        "produced_equation_ids": list(produced),
        "produced_equation_keys": _equation_key_material(produced, equation_stable_keys),
        "label": dto.get("label") or "",
        "visual_label": dto.get("visual_label") or "",
        "theory_object": dto.get("theory_object") or "",
        "process_verbs": list(dto.get("process_verbs") or []),
        "stage_keys": list(dto.get("stage_keys") or []),
        "dominant_stage": dto.get("dominant_stage") or "",
        "source_backing_status": dto.get("source_backing_status") or "",
        "isolated_reason": dto.get("isolated_reason"),
        "input_equation_ids": list(inputs),
        "output_equation_ids": list(outputs),
        "foundation_equation_ids": list(used_foundation),
        "sink_equation_ids": list(to_sink),
        "required_claim_ids": list(required_ids),
        "assumptions": list(assumptions),
        "members": [
            {
                "step_refs": [_step_ref(did, sid) for did, sid in step.occurrences],
                "node_ids": list(step.node_ids),
                "operation": step.operation,
                "edge_type": step.edge_type,
                "generic": bool(step.generic),
                "stage_key": step.stage,
                "input_equation_ids": list(step.inputs),
                "output_equation_ids": list(step.outputs),
            }
            for step in ordered
        ],
        "structure_fingerprint": _structure_fingerprint(
            level, ordered, inputs, outputs, bool(required_ids or assumptions)
        ),
        "identity_eligible": _identity_eligible(level, ordered),
        "components_for_comparison": [
            str(item.get("name") or "") for item in (dto.get("components_for_comparison") or [])
            if isinstance(item, dict) and item.get("name")
        ],
    }


def _records_payload(
    *,
    persistable: bool,
    skip_reason: str = "",
    records: list[dict] | None = None,
    equations_without_stable_key: list[str] | None = None,
) -> dict:
    return {
        "persistable": persistable,
        "skip_reason": skip_reason if not persistable else "",
        "rule_version": RULE_VERSION,
        "records": list(records or []),
        # 内部の報告: equation stable_key が引けず ``eqid:`` を材料にした式（facts には出さない）。
        "equations_without_stable_key": list(equations_without_stable_key or []),
    }


def _unavailable(document_id: str, facts: list[str], claim_sequence: list[str]) -> dict:
    return {
        "document_id": document_id,
        "available": False,
        "facts": facts,
        "rule_version": RULE_VERSION,
        "modules": [],
        "edges": [],
        "sinks": [],
        "foundations": [],
        "claim_sequence_node_ids": claim_sequence,
    }


def _claim_sequence_node_ids(graph_json: dict, chains: list[dict]) -> list[str]:
    """(b-読) の目印: ``linked_derivation_ids`` がすべて claim_chain に属する詳細ノード。

    裸の ``step_001`` はどのチェーンのものか決まらないので判定に使わない。derivation_id
    （または合成 ID の先頭）で引けたチェーン種別が1つ以上あり、そのすべてが claim_chain の
    ノードだけを返す。
    """
    chain_types: dict[str, str] = {}
    for chain in chains:
        derivation_id = str(chain.get("derivation_id") or "").strip()
        if derivation_id:
            chain_types[derivation_id] = str(chain.get("chain_type") or "").strip()
    out: list[str] = []
    for node in _dicts(_mapping(graph_json).get("nodes")):
        if str(node.get("graph_layer") or "") not in DETAIL_GRAPH_LAYERS:
            continue
        node_id = _node_id(node)
        if not node_id:
            continue
        types: list[str] = []
        for ref in _ids(node.get("linked_derivation_ids")):
            if ref in chain_types:
                types.append(chain_types[ref])
            elif ":" in ref and ref.split(":", 1)[0] in chain_types:
                types.append(chain_types[ref.split(":", 1)[0]])
        if types and all(t in CLAIM_CHAIN_TYPES for t in types) and node_id not in out:
            out.append(node_id)
    return out


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_theory_modules(
    *,
    document_id: str,
    artifacts: dict,
    graph_json: dict,
    equation_stable_keys: Mapping[str, str] | None = None,
) -> dict:
    """理論モジュール DTO（設計書 §8.1）を組み立てる。入力は一切 mutate しない。

    ``equation_stable_keys``（agent equation ID → equation stable_key。§13.3）は
    ``module_key`` の材料にだけ使う。呼び出し側は ``persistence.equation_stable_key_map`` で
    作る（写像の計算を二重実装しない）。例外を外に出さない（TM8）。導出に失敗したら
    ``available: false`` + 事実文を返す。
    """
    doc_id = str(document_id or "")
    try:
        dto, _records = _build(doc_id, _mapping(artifacts), _mapping(graph_json), _key_map(equation_stable_keys))
        return dto
    except Exception:  # noqa: BLE001 — 読み時射影の失敗で画面を壊さない
        return _unavailable(doc_id, [FACT_BUILD_FAILED], [])


def build_theory_module_records(
    *,
    document_id: str,
    artifacts: dict,
    graph_json: dict,
    equation_stable_keys: Mapping[str, str] | None = None,
) -> dict:
    """保存用の出力（設計書 §13.4）。DTO（:func:`build_theory_modules`）とは別の戻り値。

    構造の指紋・式の stable_key・工程の型はここにだけ載り、DTO には混ぜない（TM12）。
    呼ぶのはパイプラインステージ ``theory_modules`` だけで、route は import しない。

    戻り値: ``{"persistable", "skip_reason", "rule_version", "records",
    "equations_without_stable_key"}``。``persistable`` が False のとき（builder の例外 =
    ``build_failed`` / 導出の解析結果が無い・導出に失敗した = ``no_derivations``）は呼び出し側が
    SQL を発行しない（TM14）。式の手順が 0（主張の並びだけ）のときは ``persistable: True`` かつ
    ``records: []``（それが今回の解析の結果なので、旧行を superseded にする）。
    """
    doc_id = str(document_id or "")
    try:
        _dto, records = _build(doc_id, _mapping(artifacts), _mapping(graph_json), _key_map(equation_stable_keys))
        return records
    except Exception:  # noqa: BLE001 — 保存用出力の失敗は「保存しない」に倒す
        return _records_payload(persistable=False, skip_reason=RECORDS_SKIP_BUILD_FAILED)


def _key_map(value: Mapping[str, str] | None) -> dict[str, str]:
    """写像を複製して正規化する（入力を mutate しない。空キー・空値は落とす）。"""
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, str] = {}
    for key, item in value.items():
        k, v = str(key or "").strip(), str(item or "").strip()
        if k and v:
            out[k] = v
    return out


def _build(
    document_id: str,
    artifacts: dict,
    graph_json: dict,
    equation_stable_keys: Mapping[str, str],
) -> tuple[dict, dict]:
    """(DTO, 保存用出力) を組み立てる。両者は同じ内部結果から作り、``module_key`` が一致する。"""
    facts: list[str] = []

    def _add_fact(text: str) -> None:
        if text and text not in facts:
            facts.append(text)

    chains = _dicts(_mapping(artifacts.get("derivation_chain")).get("chains"))
    claim_sequence = _claim_sequence_node_ids(graph_json, chains)
    if not chains:
        # 導出の解析結果が無い・導出に失敗した（失敗時もステージは空の chains を残す）。
        # 素材が無いことを「モジュールが全部消えた」と読まない（TM14）。
        return (
            _unavailable(document_id, [FACT_NO_DERIVATIONS], claim_sequence),
            _records_payload(persistable=False, skip_reason=RECORDS_SKIP_NO_DERIVATIONS),
        )

    graph = _GraphIndex(graph_json)
    steps, sinks, excluded_inferred = _collect_steps(chains, graph)
    if not steps:
        facts = [FACT_NO_EQUATION_STEPS]
        if any(str(c.get("chain_type") or "") in CLAIM_CHAIN_TYPES for c in chains):
            facts.append(FACT_CLAIM_CHAINS_ONLY)
        if excluded_inferred:
            facts.append(FACT_INFERRED_STEPS_EXCLUDED)
        # 式の手順が 0 = 今回の解析の結果。保存側は旧行を superseded にする（TM14 の後半）。
        return _unavailable(document_id, facts, claim_sequence), _records_payload(persistable=True)

    labels = _Labels(artifacts)
    if not labels.equation_records:
        _add_fact(FACT_NO_EQUATIONS)
    if not labels.claims:
        _add_fact(FACT_NO_CLAIMS)
    if not labels.components:
        _add_fact(FACT_NO_COMPONENTS)
    if not graph.nodes:
        _add_fact(FACT_NO_GRAPH)
    if excluded_inferred:
        _add_fact(FACT_INFERRED_STEPS_EXCLUDED)

    flow = _Flow(steps, sinks)

    # §5.5（実装記録 §12 の逸脱 1）: 循環に関わる step（循環する式の成分の中で入力と出力を
    # 結ぶ step）は、成分ごとに1つの初期モジュールへ縮約してから併合する。単独に切り離すと
    # 計算本体が循環の前後で割れ、試作（§3.2 / §3.3）の外枠と一致しなくなる。
    cycle_groups: list[frozenset[int]] = []
    cycle_equations: list[str] = []
    for component in _cyclic_equations(steps):
        group = frozenset(
            step.order for step in steps
            if set(step.inputs) & component and set(step.outputs) & component
        )
        if not group:
            continue
        overlapping = [g for g in cycle_groups if g & group]
        for g in overlapping:
            cycle_groups.remove(g)
            group = group | g
        cycle_groups.append(group)
        cycle_equations.extend(sorted(component))
    if cycle_groups:
        cycle_labels = _dedup(labels.equation_label(eq) for eq in cycle_equations)
        _add_fact(FACT_CYCLE_PREFIX + "、".join(cycle_labels))

    def _initial_units(orders: Iterable[int]) -> list[frozenset[int]]:
        pool = set(orders)
        units: list[frozenset[int]] = []
        for group in cycle_groups:
            inside = frozenset(group & pool)
            if inside:
                units.append(inside)
                pool -= inside
        units.extend(frozenset({order}) for order in pool)
        return sorted(units, key=lambda unit: min(unit))

    stage_order = _stage_order()
    outer = _merge_modules(flow, _initial_units(step.order for step in steps), OUTER_INTERFACE_LIMIT)

    taken_keys: set[str] = set()
    modules: list[dict] = []
    records: list[dict] = []
    outer_keys: list[tuple[frozenset[int], str]] = []
    inner_keys: list[tuple[frozenset[int], str]] = []
    too_wide = False

    def _isolated_reason(members: frozenset[int], limit: int, width: int | None = None) -> str | None:
        if any(members == group for group in cycle_groups):
            return ISOLATED_CYCLE  # 循環の縮約だけで閉じ、隣とまとまらなかった
        if len(members) != 1:
            return None
        if (flow.width(members) if width is None else width) > limit:
            return ISOLATED_INTERFACE_TOO_WIDE
        return None

    for members in outer:
        key = _module_key(document_id, flow.produced(members), LEVEL_OUTER, taken_keys, equation_stable_keys)
        reason = _isolated_reason(members, OUTER_INTERFACE_LIMIT)
        if reason == ISOLATED_INTERFACE_TOO_WIDE:
            too_wide = True
        outer_keys.append((members, key))
        outer_dto = _module_dto(
            flow=flow, labels=labels, members=members, level=LEVEL_OUTER, key=key,
            parent_key=None, isolated_reason=reason, stage_order=stage_order,
        )
        modules.append(outer_dto)
        records.append(_module_record(
            flow=flow, members=members, dto=outer_dto, equation_stable_keys=equation_stable_keys,
        ))
        if len(members) < 2:
            continue
        outer_units = _initial_units(members)
        inner = _inner_modules(flow, outer_units, members, cycle_groups)
        if len(inner) < 2:
            continue  # 内側が割れない外枠には内側を持たせない（§5.4）
        inner_handoffs = _inner_handoffs(flow, inner, members)
        for index, inner_members in enumerate(inner):
            inner_key = _module_key(
                document_id, flow.produced(inner_members), LEVEL_INNER, taken_keys, equation_stable_keys,
            )
            inner_keys.append((inner_members, inner_key))
            inner_dto = _module_dto(
                flow=flow, labels=labels, members=inner_members, level=LEVEL_INNER, key=inner_key,
                parent_key=key,
                isolated_reason=_isolated_reason(
                    inner_members, INNER_INTERFACE_LIMIT, _inner_width(inner_handoffs, {index}),
                ),
                stage_order=stage_order,
            )
            modules.append(inner_dto)
            records.append(_module_record(
                flow=flow, members=inner_members, dto=inner_dto, equation_stable_keys=equation_stable_keys,
            ))
    if too_wide:
        _add_fact(FACT_INTERFACE_TOO_WIDE)

    edges: list[dict] = []

    def _edges_for(group: list[tuple[frozenset[int], str]], level: str, parent_of: dict[str, str]) -> None:
        for source_members, source_key in group:
            produced = flow.produced(source_members)
            for target_members, target_key in group:
                if source_key == target_key or parent_of.get(source_key) != parent_of.get(target_key):
                    continue
                consumed = set(flow.consumed(target_members))
                # 共有の基礎も受け渡しの事実として辺に載せる（接点の本数に数えないだけ = §5.3）
                passed = [eq for eq in produced if eq in consumed]
                if passed:
                    edges.append({
                        "source": source_key,
                        "target": target_key,
                        "level": level,
                        "equation_labels": [labels.equation_label(eq) for eq in passed],
                    })

    _edges_for(outer_keys, LEVEL_OUTER, {})
    inner_parent = {m["module_key"]: m["parent_module_key"] for m in modules if m["level"] == LEVEL_INNER}
    _edges_for(inner_keys, LEVEL_INNER, inner_parent)

    sink_rows: list[dict] = []
    for sink in sinks:
        from_keys = _dedup(
            key for members, key in outer_keys
            if set(flow.produced(members)) & set(sink.inputs)
        )
        sink_rows.append({
            "operation_label": _operation_label(sink.operation),
            "input_labels": [labels.equation_label(eq) for eq in sink.inputs],
            "output_labels": [labels.equation_label(eq) for eq in sink.outputs],
            "source_module_keys": from_keys,
        })
    if sink_rows:
        _add_fact(FACT_SINKS)

    foundation_rows: list[dict] = []
    for eq in sorted(flow.foundation, key=lambda e: (min(flow.consumers.get(e, [0])), e)):
        foundation_rows.append({
            "equation_id": eq,
            "display_label": labels.equation_label(eq),
            "producer_module_keys": _dedup(
                key for members, key in outer_keys if eq in set(flow.produced(members))
            ),
            "consumer_module_keys": _dedup(
                key for members, key in outer_keys if eq in set(flow.consumed(members))
            ),
        })

    without_key = sorted({
        eq for step in steps for eq in step.outputs if not equation_stable_keys.get(eq)
    })
    dto = {
        "document_id": document_id,
        "available": True,
        "facts": facts,
        "rule_version": RULE_VERSION,
        "modules": modules,
        "edges": edges,
        "sinks": sink_rows,
        "foundations": foundation_rows,
        "claim_sequence_node_ids": claim_sequence,
    }
    return dto, _records_payload(
        persistable=True, records=records, equations_without_stable_key=without_key,
    )
