"""Deterministic DSL → component-graph edge polarity propagation (issue #451).

Component-graph edges describe theory-operation flow and do not natively carry a
polarity. The DSL graph, however, records ``polarity`` ("+" / "-") on its causal /
relational edges. This non-LLM step propagates that polarity onto component edges
so the UI can visualise promotion vs. inhibition from a real field (never guessed
from the relation label).

Domain-agnostic: only stable claim-id overlap and the DSL ``polarity`` field are
used — no paper-specific vocabulary, no cross-artifact prefix matching (#443).
A component edge src→tgt is assigned the polarity of a DSL edge that runs from a
claim referenced on the source side to a claim referenced on the target side.
Negative polarity dominates (the inhibitory reading is the safer thing to surface).
"""
from __future__ import annotations


def _str_set(values) -> set[str]:
    return {str(v) for v in (values or []) if v}


def link_component_edge_polarity(dsl, graph_result) -> int:
    """Set ``polarity`` on component-graph edges in place (issue #451).

    Returns the number of edges that received a polarity. Tolerates None inputs.
    """
    if dsl is None or graph_result is None:
        return 0

    # DSL node id -> the claim ids it represents.
    node_claims: dict[str, set[str]] = {}
    for node in getattr(dsl, "nodes", []) or []:
        refs = getattr(node, "source_refs", {}) or {}
        node_claims[str(getattr(node, "node_id", "") or "")] = _str_set(refs.get("claim_ids"))

    # Directed claim pair -> polarity, from DSL edges with an explicit +/- polarity.
    # Negative dominates so an inhibitory relation is never masked by a positive one.
    pair_polarity: dict[tuple[str, str], str] = {}
    for edge in getattr(dsl, "edges", []) or []:
        pol = str(getattr(edge, "polarity", "") or "")
        if pol not in ("+", "-"):
            continue
        src_claims = node_claims.get(str(getattr(edge, "from_node_id", "") or ""), set())
        tgt_claims = node_claims.get(str(getattr(edge, "to_node_id", "") or ""), set())
        for a in src_claims:
            for b in tgt_claims:
                key = (a, b)
                if pair_polarity.get(key) == "-":
                    continue
                if pol == "-" or key not in pair_polarity:
                    pair_polarity[key] = pol
    if not pair_polarity:
        return 0

    cnodes: dict[str, object] = {}
    for node in getattr(graph_result, "nodes", []) or []:
        cid = str(getattr(node, "component_id", "") or "")
        if cid:
            cnodes[cid] = node

    def _out_claims(node) -> set[str]:
        ids: set[str] = set()
        for attr in ("output_claim_ids", "linked_claim_ids"):
            ids |= _str_set(getattr(node, attr, []))
        return ids

    def _in_claims(node) -> set[str]:
        ids: set[str] = set()
        for attr in ("input_claim_ids", "required_claim_ids", "linked_claim_ids"):
            ids |= _str_set(getattr(node, attr, []))
        return ids

    count = 0
    for edge in getattr(graph_result, "edges", []) or []:
        if str(getattr(edge, "polarity", "") or ""):
            continue
        src = cnodes.get(str(getattr(edge, "source", "") or ""))
        tgt = cnodes.get(str(getattr(edge, "target", "") or ""))
        if not src or not tgt:
            continue
        src_claims = _out_claims(src)
        tgt_claims = _in_claims(tgt)
        polarity = ""
        for a in src_claims:
            for b in tgt_claims:
                p = pair_polarity.get((a, b))
                if p == "-":
                    polarity = "-"
                    break
                if p == "+" and polarity != "-":
                    polarity = "+"
            if polarity == "-":
                break
        if polarity:
            edge.polarity = polarity
            count += 1
    return count
