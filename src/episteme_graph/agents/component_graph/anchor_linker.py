"""Deterministic thesis → component-graph anchor propagation (issue #449).

The rendered TheoryOperationGraph (component graph) must mark the nodes that
represent the paper's central thesis so the UI can always highlight "where the
goal is". This non-LLM step mirrors ``thesis_reconstruction.anchor_linker`` but
operates on the component graph: a node is a thesis anchor when its source-backing
claim/equation ids overlap the thesis's headline / supporting claim ids or the
central-thesis equation ids.

Domain-agnostic: only stable claim/equation id overlap is used — no paper-specific
terms, no LLM judgement, and crucially no cross-artifact prefix matching (that
mismatch is issue #443; claim/equation ids are shared, stable ids within the
pipeline so this is independent of it).
"""
from __future__ import annotations


def _str_set(values) -> set[str]:
    return {str(v) for v in (values or []) if v}


def link_component_thesis_anchors(thesis, graph_result) -> list[str]:
    """Set ``is_thesis_anchor`` on component-graph nodes in place (issue #449).

    Returns the list of component ids flagged as anchors. Tolerates None inputs.
    """
    if thesis is None or graph_result is None:
        return []

    central = getattr(thesis, "central_thesis", {}) or {}
    if not isinstance(central, dict):
        central = {}

    thesis_claim_ids = _str_set(central.get("claim_ids"))
    thesis_claim_ids |= _str_set(getattr(thesis, "supporting_subclaim_ids", []))
    thesis_equation_ids = _str_set(central.get("equation_ids"))

    if not thesis_claim_ids and not thesis_equation_ids:
        return []

    nodes = getattr(graph_result, "nodes", []) or []
    by_id: dict[str, object] = {}
    for node in nodes:
        nid = str(getattr(node, "component_id", "") or "")
        if nid:
            by_id[nid] = node

    def node_claims(node) -> set[str]:
        ids: set[str] = set()
        for attr in ("linked_claim_ids", "output_claim_ids"):
            ids |= _str_set(getattr(node, attr, []))
        return ids

    def node_equations(node) -> set[str]:
        ids: set[str] = set()
        for attr in ("linked_equation_ids", "output_equation_ids"):
            ids |= _str_set(getattr(node, attr, []))
        return ids

    anchor_ids: list[str] = []
    seen: set[str] = set()

    def _flag(node) -> None:
        node.is_thesis_anchor = True
        nid = str(getattr(node, "component_id", "") or "")
        if nid and nid not in seen:
            seen.add(nid)
            anchor_ids.append(nid)

    for node in nodes:
        is_anchor = bool(
            (thesis_claim_ids & node_claims(node))
            or (thesis_equation_ids & node_equations(node))
        )
        if not is_anchor:
            continue
        _flag(node)
        # Propagate to the aggregating main node so the default (main-layer) view
        # also surfaces the anchor, not only the equation_detail node.
        parent_id = str(getattr(node, "parent_component_id", "") or "")
        if parent_id and parent_id in by_id:
            _flag(by_id[parent_id])

    return anchor_ids
