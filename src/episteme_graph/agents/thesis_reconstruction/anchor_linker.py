"""Deterministic thesis ↔ DSL anchor linking (issue #442).

After DSL linking, the reconstructed thesis must point at the graph nodes that
represent it so traversal has an explicit entry/exit. This non-LLM step maps the
thesis's headline / supporting claim ids and central-thesis equation ids to DSL
nodes via each node's ``source_refs`` and records the match on both sides:

  * ``ThesisReconstructionResult.anchor_node_ids`` ← matched DSL node ids
  * ``DSLNode.is_thesis_anchor``                   ← True for matched nodes

Domain-agnostic: only id overlap and node provenance are used; no paper-specific
terms or LLM judgement.
"""
from __future__ import annotations


def link_thesis_anchors(thesis, dsl) -> list[str]:
    """Cross-link the thesis artifact and the DSL graph in place (issue #442).

    Returns the list of anchor node ids set on the thesis. Tolerates None inputs.
    """
    if thesis is None or dsl is None:
        return []

    central = getattr(thesis, "central_thesis", {}) or {}
    if not isinstance(central, dict):
        central = {}

    thesis_claim_ids: set[str] = set()
    for cid in central.get("claim_ids") or []:
        if cid:
            thesis_claim_ids.add(str(cid))
    for cid in getattr(thesis, "supporting_subclaim_ids", []) or []:
        if cid:
            thesis_claim_ids.add(str(cid))
    thesis_equation_ids = {
        str(eid) for eid in (central.get("equation_ids") or []) if eid
    }

    anchor_ids: list[str] = []
    seen: set[str] = set()
    for node in getattr(dsl, "nodes", []) or []:
        refs = getattr(node, "source_refs", {}) or {}
        node_claims = {str(v) for v in (refs.get("claim_ids") or []) if v}
        node_equations = {str(v) for v in (refs.get("equation_ids") or []) if v}
        is_anchor = bool(
            (thesis_claim_ids & node_claims) or (thesis_equation_ids & node_equations)
        )
        if is_anchor:
            node.is_thesis_anchor = True
            node_id = str(getattr(node, "node_id", "") or "")
            if node_id and node_id not in seen:
                seen.add(node_id)
                anchor_ids.append(node_id)

    # Fallback: if no claim/equation overlap resolved an anchor, use nodes whose
    # provenance is the thesis itself (source_kind=="thesis" / ThesisProxy) so the
    # traversal target is never left undefined when the graph carries thesis nodes.
    if not anchor_ids:
        for node in getattr(dsl, "nodes", []) or []:
            source_kind = str(getattr(node, "source_kind", "") or "")
            node_type = str(getattr(node, "node_type", "") or "")
            if source_kind == "thesis" or node_type == "ThesisProxy":
                node.is_thesis_anchor = True
                node_id = str(getattr(node, "node_id", "") or "")
                if node_id and node_id not in seen:
                    seen.add(node_id)
                    anchor_ids.append(node_id)

    existing = [str(v) for v in (getattr(thesis, "anchor_node_ids", []) or [])]
    merged = existing + [a for a in anchor_ids if a not in set(existing)]
    thesis.anchor_node_ids = merged
    return merged
