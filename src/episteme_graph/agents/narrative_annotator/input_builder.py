"""Build NarrativeAnnotator LLM input (issue #360).

Packages the main-layer graph (nodes / edges), the reconstructed thesis text,
and human-readable material for each main node so the LLM can mostly quote and
arrange existing material instead of inventing new content.

IK-0753: the material must be human-readable. Main nodes aggregate many
derivation steps, and what the aggregation keeps by itself is internal IDs and
counts (``Derive eq_… from eq_… via define -> derive_result`` / ``Logical
progression through 27 claims in section sec_1``). Those deterministic
sentences are not material: each node is described by its label, its
description, the atomic-claim text of the steps it aggregates and the printed
numbers of its equations. Internal-ID tokens (``internal_id_text.INTERNAL_ID_RE``
— the same pattern as the display projection) are stripped from every text
field, sentences built from counts / operation arrows are dropped, and the
LLM addresses nodes and edges through short handles (``N1`` / ``E1``) instead
of internal component / edge IDs.
"""
from __future__ import annotations

import re

from ..internal_id_text import contains_internal_id, strip_internal_ids
from .schema import NarrativeLLMInput

_MAX_MAIN_NODES = 16
_MAX_MAIN_EDGES = 24
#: Key points / equations listed per main node (an enumeration, never a count).
_MAX_KEY_POINTS = 6
_MAX_EQUATIONS = 8
_MAX_TEXT_CHARS = 400

#: A paper's printed equation number carried as an equation id (``eq_12`` /
#: ``eq_2_7``). Shown as ``(12)`` / ``(2.7)``; any other equation id is internal.
_PRINTED_EQUATION_ID_RE = re.compile(r"^eq_([0-9]+(?:_[0-9]+)*)$")

#: Deterministic machine sentences that are not reader material: operation
#: arrows (``define -> derive_result``) and counts of claims / equations / steps.
_MACHINE_SENTENCE_RE = re.compile(
    r"->|\b[0-9]+\s+(?:claims?|equations?|equation\s+steps?|steps?)\b|step\(s\)|\(#[0-9]+\)",
    re.IGNORECASE,
)


def human_text(text) -> str:
    """Return ``text`` as reader material, or "" when it is machine text (IK-0753)."""
    raw = " ".join(str(text or "").split())
    if not raw or _MACHINE_SENTENCE_RE.search(raw):
        return ""
    cleaned = strip_internal_ids(raw)
    if not cleaned or contains_internal_id(cleaned):
        return ""
    return cleaned[:_MAX_TEXT_CHARS]


def printed_equation_label(equation_id: str) -> str:
    """``eq_12`` → ``(12)`` / ``eq_2_7`` → ``(2.7)``; internal equation IDs → ""."""
    match = _PRINTED_EQUATION_ID_RE.match(str(equation_id or "").strip())
    if not match:
        return ""
    return "(" + match.group(1).replace("_", ".") + ")"


class NarrativeInputBuilder:
    def build(
        self,
        graph,
        thesis=None,
        derivations=None,
        cartridge=None,
        config: dict | None = None,
    ) -> NarrativeLLMInput:
        cfg = config or {}
        takeaways = self._takeaways_by_derivation(derivations)
        main_nodes = self._main_nodes(
            graph, takeaways, int(cfg.get("max_main_nodes", _MAX_MAIN_NODES))
        )
        refs = {n["component_id"]: n["ref"] for n in main_nodes}
        main_edges = self._main_edges(
            graph, refs, int(cfg.get("max_main_edges", _MAX_MAIN_EDGES))
        )
        return NarrativeLLMInput(
            document_id=str(getattr(graph, "document_id", "") or ""),
            cartridge_id=cartridge.cartridge_id if cartridge else getattr(graph, "cartridge_id", None),
            central_thesis_text=human_text(self._thesis_text(thesis)),
            main_nodes=main_nodes,
            main_edges=main_edges,
            normalized_terms=self._normalized_terms(cartridge) if cartridge else None,
        )

    @staticmethod
    def _thesis_text(thesis) -> str:
        central = getattr(thesis, "central_thesis", None)
        if isinstance(central, dict):
            return str(central.get("text") or "")
        return ""

    @staticmethod
    def _takeaways_by_derivation(derivations) -> dict[str, str]:
        """Derivation takeaways that are reader prose (machine sentences dropped)."""
        takeaways: dict[str, str] = {}
        for chain in getattr(derivations, "chains", []) or []:
            derivation_id = str(getattr(chain, "derivation_id", "") or "")
            takeaway = human_text(getattr(chain, "teaching_takeaway", ""))
            if derivation_id and takeaway:
                takeaways[derivation_id] = takeaway
        return takeaways

    @staticmethod
    def _main_nodes(graph, takeaways: dict[str, str], limit: int) -> list[dict]:
        all_nodes = list(getattr(graph, "nodes", []) or [])
        by_id = {str(getattr(n, "component_id", "") or ""): n for n in all_nodes}
        children: dict[str, list] = {}
        for node in all_nodes:
            parent = str(getattr(node, "parent_component_id", "") or "")
            if parent:
                children.setdefault(parent, []).append(node)

        nodes = []
        for node in all_nodes:
            if str(getattr(node, "graph_layer", "main") or "main") != "main":
                continue
            component_id = str(node.component_id)
            members = list(children.get(component_id, []))
            for member_id in (
                list(getattr(node, "member_component_ids", []) or [])
                + list(getattr(node, "detail_node_ids", []) or [])
            ):
                member = by_id.get(str(member_id))
                if member is not None and member not in members:
                    members.append(member)

            description = human_text(getattr(node, "description", ""))
            key_points: list[str] = []
            for text in (
                [takeaways[d] for d in (getattr(node, "linked_derivation_ids", []) or []) if d in takeaways]
                + [human_text(getattr(m, "description", "")) for m in members]
            ):
                if text and text != description and text not in key_points:
                    key_points.append(text)
                if len(key_points) >= _MAX_KEY_POINTS:
                    break

            equations: list[str] = []
            for eq_id in getattr(node, "linked_equation_ids", []) or []:
                label = printed_equation_label(eq_id)
                if label and label not in equations:
                    equations.append(label)
                if len(equations) >= _MAX_EQUATIONS:
                    break

            nodes.append({
                "component_id": component_id,
                "label": strip_internal_ids(str(node.label or "")),
                "theory_object": human_text(getattr(node, "theory_object", "")),
                "description": description,
                "operation": str(getattr(node, "operation", "") or ""),
                "source_backing_status": getattr(node, "source_backing_status", ""),
                "teaching_takeaways": key_points,
                "equations": equations,
                "concepts": [
                    c for c in (human_text(c) for c in (getattr(node, "concepts", []) or []))
                    if c
                ][:_MAX_KEY_POINTS],
                "display_order": int(getattr(node, "display_order", 0) or 0),
            })
            if len(nodes) >= limit:
                break
        nodes.sort(key=lambda n: (n["display_order"], n["component_id"]))
        for index, node in enumerate(nodes, start=1):
            node["ref"] = f"N{index}"
        return nodes

    @staticmethod
    def _main_edges(graph, refs: dict[str, str], limit: int) -> list[dict]:
        edges = []
        for edge in getattr(graph, "edges", []) or []:
            source = str(getattr(edge, "source", "") or "")
            target = str(getattr(edge, "target", "") or "")
            if source not in refs or target not in refs:
                continue
            edges.append({
                "edge_id": edge.edge_id,
                "ref": f"E{len(edges) + 1}",
                "source": source,
                "target": target,
                "source_ref": refs[source],
                "target_ref": refs[target],
                "edge_type": getattr(edge, "edge_type", ""),
                "reasoning": human_text(getattr(edge, "reasoning", "")),
            })
            if len(edges) >= limit:
                break
        return edges

    @staticmethod
    def _normalized_terms(cartridge) -> list[dict]:
        terms: list[dict] = []
        if getattr(cartridge, "aliases", None):
            for canonical, aliases in cartridge.aliases.items():
                terms.append({"canonical": canonical, "aliases": aliases})
        return terms


#: Fields of a main node / edge shown to the LLM (internal IDs stay out).
PROMPT_NODE_FIELDS = (
    "ref", "label", "theory_object", "description", "operation",
    "source_backing_status", "teaching_takeaways", "equations", "concepts",
)
PROMPT_EDGE_FIELDS = ("ref", "source_ref", "target_ref", "edge_type", "reasoning")


def prompt_nodes(llm_input: NarrativeLLMInput) -> list[dict]:
    return [
        {k: n[k] for k in PROMPT_NODE_FIELDS if n.get(k) not in (None, "", [])}
        for n in llm_input.main_nodes
    ]


def prompt_edges(llm_input: NarrativeLLMInput) -> list[dict]:
    return [
        {k: e[k] for k in PROMPT_EDGE_FIELDS if e.get(k) not in (None, "", [])}
        for e in llm_input.main_edges
    ]
