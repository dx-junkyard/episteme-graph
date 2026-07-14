"""個人知識ネットワークの導出（設計書 §4）。非LLM・決定論的な純粋関数。

``build_network`` は sqlalchemy 等の DB 依存を一切持たない純粋関数であり、fake rows
（dict のリスト）だけでローカル検証できる。**モジュールトップで ``core.personal_graph.queries``
を import しない** — ローカル環境に sqlalchemy が無くても純粋部を検証できるようにするため
（``derive_personal_network`` 内で遅延 import する）。
"""

from __future__ import annotations

from core.personal_graph.schema import (
    ANCHOR_TYPE_COMPONENT,
    ANCHOR_TYPE_GRAPH_EDGE,
    ANCHOR_TYPE_TOPIC,
    EDGE_KIND_BRIDGE,
    NODE_KIND_QUESTION,
    NODE_KIND_RECONSTRUCTION,
    NODE_KIND_TENSION,
    PersonalAnchor,
    PersonalEdge,
    PersonalNetwork,
    PersonalNode,
)

# TENSION_OWNED_STATUSES の正本は core/tension/schema.py（再定義禁止・CLAUDE.md）。
from core.tension.schema import TENSION_OWNED_STATUSES

# structure_anchor の帰属語彙・status 語彙の正本も core/structure_anchor/schema.py
# （anchor_type の粒度語彙も同モジュールが正本。ここでは同意判定に使う値のみ import する）。
from core.structure_anchor.schema import (
    ANCHOR_STATUS_ACTIVE,
    ATTRIBUTION_CONFIRMED,
    ATTRIBUTION_LEARNER_SELECTED,
)

_TRACE_LABEL_LIMIT = 80

# N2 の「本人確定」とみなす attribution_source（§2「同意の汲み取り」層1: 明示 confirm 必須）
_ATTRIBUTION_OWNED = (ATTRIBUTION_LEARNER_SELECTED, ATTRIBUTION_CONFIRMED)

# N4 で「本人が異議を挟んだ」とみなす self_check（§2「採用しない痕跡」）
_SELF_CHECK_DISAGREE = ("disagreed", "verdict_wrong")

_BRIDGE_FACT = "この引っかかりを自分でつないだ"
_FACT_REVISED = "改訂を経てたどり着いた再構成"
_FACT_DESCENDED = "原因を絞るため記号まで降りた"


def _truncate(text: str | None, limit: int = _TRACE_LABEL_LIMIT) -> str:
    return (text or "").strip()[:limit]


def _sort_key(row: dict) -> tuple:
    """(created_at, id) の決定論順キー（§4）。両方とも文字列として辞書順比較する。"""
    return (str(row.get("created_at") or ""), str(row.get("id") or ""))


def _tension_label(payload: dict) -> str:
    """payload.learner_text（articulate 済み）優先、無ければ payload.text（evidence_quote 由来）。"""
    learner_text = (payload.get("learner_text") or "").strip()
    if learner_text:
        return _truncate(learner_text)
    return _truncate(payload.get("text"))


def _tension_anchor(trace: dict, topic_atlas: dict[str, str]) -> PersonalAnchor:
    """§2: target_refs.component_ids / edge_ids が非空ならそちら、無ければ topic 粒度。"""
    payload = trace.get("payload") or {}
    refs = payload.get("target_refs") or {}
    component_ids = [str(c) for c in (refs.get("component_ids") or []) if c]
    edge_ids = [str(e) for e in (refs.get("edge_ids") or []) if e]
    if component_ids:
        return PersonalAnchor(anchor_type=ANCHOR_TYPE_COMPONENT, anchor_id=component_ids[0])
    if edge_ids:
        return PersonalAnchor(anchor_type=ANCHOR_TYPE_GRAPH_EDGE, anchor_id=edge_ids[0])
    topic_id = str(trace.get("topic_id") or "")
    return PersonalAnchor(
        anchor_type=ANCHOR_TYPE_TOPIC,
        anchor_id=topic_id,
        atlas_node_id=topic_atlas.get(topic_id),
    )


def _tension_node(trace: dict, topic_atlas: dict[str, str]) -> PersonalNode:
    payload = trace.get("payload") or {}
    return PersonalNode(
        id=str(trace["id"]),
        node_kind=NODE_KIND_TENSION,
        label=_tension_label(payload),
        anchor=_tension_anchor(trace, topic_atlas),
        topic_id=trace.get("topic_id"),
        created_at=str(trace.get("created_at") or ""),
        facts=[],
        source={"kind": "tension", "status": trace.get("status") or ""},
    )


def _tension_bridge_edges(trace: dict) -> list[PersonalEdge]:
    """§3: status='connected' の tension について component_ids / edge_ids の各要素へ橋を張る。"""
    payload = trace.get("payload") or {}
    refs = payload.get("target_refs") or {}
    trace_id = str(trace["id"])
    edges: list[PersonalEdge] = []
    for cid in refs.get("component_ids") or []:
        if not cid:
            continue
        edges.append(PersonalEdge(
            edge_kind=EDGE_KIND_BRIDGE,
            from_node_id=trace_id,
            to_ref={"ref_type": "component", "ref_id": str(cid)},
            fact=_BRIDGE_FACT,
        ))
    for eid in refs.get("edge_ids") or []:
        if not eid:
            continue
        edges.append(PersonalEdge(
            edge_kind=EDGE_KIND_BRIDGE,
            from_node_id=trace_id,
            to_ref={"ref_type": "graph_edge", "ref_id": str(eid)},
            fact=_BRIDGE_FACT,
        ))
    return edges


def _question_anchor(trace: dict, topic_atlas: dict[str, str]) -> tuple[PersonalAnchor, str]:
    """§2 N2/N3: structure_anchor が本人確定済み（learner_selected/confirmed・active）なら
    そのアンカーを使う（N2）。それ以外（無し・llm_candidate・dismissed）は topic 粒度へ
    縮退する（N3）。**llm_candidate の anchor_id は絶対に使わない**（PN-3）。

    戻り値は (anchor, attribution_source_for_source_meta)。
    """
    payload = trace.get("payload") or {}
    structure_anchor = payload.get("structure_anchor")
    if (
        isinstance(structure_anchor, dict)
        and structure_anchor.get("status") == ANCHOR_STATUS_ACTIVE
        and structure_anchor.get("attribution_source") in _ATTRIBUTION_OWNED
    ):
        anchor = PersonalAnchor(
            anchor_type=str(structure_anchor.get("anchor_type") or ANCHOR_TYPE_TOPIC),
            anchor_id=str(structure_anchor.get("anchor_id") or ""),
            anchor_label=str(structure_anchor.get("anchor_label") or ""),
        )
        return anchor, str(structure_anchor.get("attribution_source") or "")

    topic_id = str(trace.get("topic_id") or "")
    anchor = PersonalAnchor(
        anchor_type=ANCHOR_TYPE_TOPIC,
        anchor_id=topic_id,
        atlas_node_id=topic_atlas.get(topic_id),
    )
    attribution = ""
    if isinstance(structure_anchor, dict):
        attribution = str(structure_anchor.get("attribution_source") or "")
    return anchor, attribution


def _question_node(trace: dict, topic_atlas: dict[str, str]) -> PersonalNode:
    payload = trace.get("payload") or {}
    anchor, attribution_source = _question_anchor(trace, topic_atlas)
    return PersonalNode(
        id=str(trace["id"]),
        node_kind=NODE_KIND_QUESTION,
        label=_truncate(payload.get("text")),
        anchor=anchor,
        topic_id=trace.get("topic_id"),
        created_at=str(trace.get("created_at") or ""),
        facts=[],
        source={
            "kind": "question",
            "status": trace.get("status") or "",
            "attribution_source": attribution_source,
        },
    )


def _reconstruction_terminal_ids(reconstructions: list[dict]) -> set[str]:
    """revision_of チェーンの終端行（他行の revision_of に自分の id が現れない行）の id 集合。"""
    referenced = {str(r["revision_of"]) for r in reconstructions if r.get("revision_of")}
    return {str(r["id"]) for r in reconstructions if str(r["id"]) not in referenced}


def _reconstruction_chain_facts(root_id: str, by_id: dict[str, dict]) -> list[str]:
    """終端行から revision_of を遡り、改訂・記号降下の事実を集める（回数は出さない。PN-4）。"""
    revised = False
    descended = False
    seen: set[str] = set()
    node = by_id.get(root_id)
    while node is not None and node["id"] not in seen:
        seen.add(str(node["id"]))
        if node.get("descended_to_symbol"):
            descended = True
        prev_id = node.get("revision_of")
        if prev_id:
            revised = True
            node = by_id.get(str(prev_id))
        else:
            node = None

    facts: list[str] = []
    if revised:
        facts.append(_FACT_REVISED)
    if descended:
        facts.append(_FACT_DESCENDED)
    return facts


def _reconstruction_node(row: dict, by_id: dict[str, dict]) -> PersonalNode:
    row_id = str(row["id"])
    return PersonalNode(
        id=row_id,
        node_kind=NODE_KIND_RECONSTRUCTION,
        label="claim への再構成",
        anchor=PersonalAnchor(anchor_type="claim", anchor_id=str(row.get("claim_id") or "")),
        topic_id=None,
        created_at=str(row.get("created_at") or ""),
        facts=_reconstruction_chain_facts(row_id, by_id),
        source={
            "kind": "reconstruction",
            "item_id": str(row.get("item_id") or ""),
            "machine_verdict": row.get("machine_verdict") or "",
            "self_check": row.get("self_check"),
        },
    )


def build_network(
    traces: list[dict],
    reconstructions: list[dict],
    topic_atlas: dict[str, str],
) -> PersonalNetwork:
    """§4 の導出アルゴリズム本体（純粋関数・非LLM・決定論）。

    - N1 tension: ``kind='tension'`` かつ ``status in TENSION_OWNED_STATUSES`` のみ採用。
      ``status='connected'`` の行だけ bridge 辺を張る。
    - N2/N3 question: ``kind='question'`` かつ ``status != 'superseded'`` は必ずノード化する
      （structure_anchor の確定状況によって N2/N3 いずれかのアンカーへ解決するだけで、
      「帰属なし」は棄却理由にならない）。
    - N4 reconstruction: revision_of チェーンの終端行のみを対象に、
      ``machine_verdict='match'`` かつ ``self_check`` が異議シグナル
      （disagreed/verdict_wrong）でなければ採用する（NULL は異議なしとして含める）。
    - 出力順は nodes・edges とも (created_at, id) の決定論順。
    """
    nodes: list[PersonalNode] = []
    edges: list[PersonalEdge] = []

    traces_sorted = sorted(traces, key=_sort_key)
    for trace in traces_sorted:
        kind = trace.get("kind")
        status = trace.get("status")
        if kind == "tension":
            if status not in TENSION_OWNED_STATUSES:
                continue
            nodes.append(_tension_node(trace, topic_atlas))
            if status == "connected":
                edges.extend(_tension_bridge_edges(trace))
        elif kind == "question":
            if status == "superseded":
                continue
            nodes.append(_question_node(trace, topic_atlas))

    recon_sorted = sorted(reconstructions, key=_sort_key)
    by_id = {str(r["id"]): r for r in recon_sorted}
    terminal_ids = _reconstruction_terminal_ids(recon_sorted)
    for row in recon_sorted:
        row_id = str(row["id"])
        if row_id not in terminal_ids:
            continue
        if row.get("machine_verdict") != "match":
            continue
        if row.get("self_check") in _SELF_CHECK_DISAGREE:
            continue
        nodes.append(_reconstruction_node(row, by_id))

    nodes.sort(key=lambda n: (n.created_at, n.id))
    return PersonalNetwork(nodes=nodes, edges=edges)


def derive_personal_network(user_id: str, course_id: str) -> PersonalNetwork:
    """エントリポイント（設計書 §4）。DB 読み取りは queries.py に委譲する。

    ``queries`` はここで遅延 import する（モジュールトップで import すると sqlalchemy が
    ローカル環境に無い場合に ``build_network`` 単体の検証まで巻き込んで壊れるため）。
    """
    from core.personal_graph import queries  # 遅延 import（理由は docstring 参照）

    traces = queries.fetch_traces(user_id, course_id)
    reconstructions = queries.fetch_reconstructions(user_id, course_id)
    topic_atlas = queries.fetch_topic_atlas_binding(course_id)
    return build_network(traces, reconstructions, topic_atlas)
