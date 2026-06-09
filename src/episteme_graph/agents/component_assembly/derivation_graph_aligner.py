"""Step 4: DerivationGraphAligner (issue #325).

Aligns refined components (Step 3 output) with derivation chains, equation
operations, the theory component graph, and a headline-claim support map.

This stage is deterministic (non-LLM). It separates -- but connects -- five
artifacts that previously tended to leak into one another:

1. ``theory_component_graph``     -- high-level refined-component dependency graph
2. ``equation_operation_graph``   -- low-level equation manipulation graph
3. ``component_operation_links``  -- refined component <-> equation operation map
4. ``aligned_derivation_chains``  -- component-attributed derivation steps
5. ``support_map``                -- headline-claim-centered support layers

It also emits ``graph_alignment_validation`` (structural self-check) and an
``export_validation`` aggregation block (``derivation_graph_alignment``) so the
ExportValidationGate can surface results without re-implementing alignment.

Design principles (see issue #325 and CLAUDE.md):
- The component graph never contains raw equation-operation nodes.
- The equation-operation graph never contains theory-component nodes.
- Low-confidence / blocked equations downgrade the derivation paths that use
  them; final-result components that render blocked equations become
  ``review_required``.
- Old component IDs (pre Step-3 split) are resolved through the Step 3
  ``component_id_mapping``; unresolved references are reported, never dropped.
- Canonical edge types are preferred; generic ``RELATED_TO`` edges are kept but
  reported as warnings.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .claim_centered_planner import infer_support_metadata
from .responsibility import canonical_responsibility_type

# ---------------------------------------------------------------------------
# Canonical vocabularies
# ---------------------------------------------------------------------------

CANONICAL_EDGE_TYPES = {
    "defines",
    "feeds",
    "enables",
    "derives",
    "constrains",
    "applies",
    "limits",
    "depends_on",
    "contrasts",
    "refines",
}

GENERIC_EDGE_TYPE = "RELATED_TO"

# refined component dependency_type -> canonical theory-graph edge type.
DEPENDENCY_TO_EDGE = {
    "requires": "depends_on",
    "depends_on": "depends_on",
    "refines": "refines",
    "qualifies": "constrains",
    "supports": "feeds",
    "diagnoses": "applies",
    "propagates_uncertainty_to": "limits",
    "contrasts": "contrasts",
    "contradicts": "contrasts",
    "enables": "enables",
    "feeds": "feeds",
    "derives": "derives",
    "constrains": "constrains",
    "applies": "applies",
    "limits": "limits",
    "defines": "defines",
}

# equation-operation node vocabulary.
OPERATION_TYPES = {
    "define",
    "transform",
    "substitute",
    "solve",
    "eliminate",
    "linearize",
    "approximate",
    "derive",
}

# support_role -> support-map layer id.
SUPPORT_ROLE_TO_LAYER = {
    "result": "result_layer",
    "derivation_core": "derivation_core",
    "observable_bridge": "observable_bridge",
    "theory_base": "theory_base",
    "application": "application_layer",
    "limitation": "limitation_layer",
}

# Ordered standard layers (result first, down to prerequisites / caveats).
STANDARD_SUPPORT_LAYERS = [
    "result_layer",
    "derivation_core",
    "observable_bridge",
    "theory_base",
    "application_layer",
    "limitation_layer",
]

SOURCE_BACKED = "source_backed"
REVIEW_REQUIRED = "review_required"

# responsibility types that act as "final" theory results.
RESULT_RESPONSIBILITIES = {"constraint", "result"}
DERIVATION_RESPONSIBILITIES = {"derivation"}


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class DerivationGraphAlignmentResult:
    theory_component_graph: dict = field(default_factory=dict)
    equation_operation_graph: dict = field(default_factory=dict)
    component_operation_links: list = field(default_factory=list)
    aligned_derivation_chains: list = field(default_factory=list)
    support_map: dict = field(default_factory=dict)
    graph_alignment_validation: dict = field(default_factory=dict)
    # Aggregated block for export_validation.derivation_graph_alignment.
    export_validation: dict = field(default_factory=dict)
    unresolved_component_refs: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Aligner
# ---------------------------------------------------------------------------


class DerivationGraphAligner:
    """Build aligned theory / operation graphs from refined components."""

    def align(
        self,
        result,
        llm_input=None,
        derivations=None,
        headline_claim_id: str | None = None,
    ) -> DerivationGraphAlignmentResult:
        components = list(getattr(result, "components", []) or [])
        component_by_id = {c.component_id: c for c in components}
        component_ids = set(component_by_id)

        remap = _build_remap(result)
        eq_index = _equation_index(llm_input)
        eq_ids_known = set(eq_index)
        chains = list(getattr(derivations, "chains", []) or [])
        headline_claim_id = headline_claim_id or _headline_claim_id(llm_input)

        errors: list[dict] = []
        warnings: list[dict] = []
        review_items: list[dict] = []
        unresolved_component_refs: list[dict] = []

        # 1. Theory component graph (refined components only).
        theory_graph, generic_edges, dangling_component_refs = self._theory_component_graph(
            components,
            component_ids,
            remap,
            eq_index,
            unresolved_component_refs,
        )

        # 2. Equation operation graph (derivation-step operations only).
        operation_graph, operation_nodes, dangling_equation_refs = self._equation_operation_graph(
            chains, eq_index, eq_ids_known
        )

        # 3 + 4. Component-attributed derivation chains and operation links.
        aligned_chains, step_owner, review_required_paths = self._aligned_chains(
            chains, components, component_by_id, remap, eq_index, unresolved_component_refs
        )
        component_links = self._component_operation_links(
            components, aligned_chains, eq_index
        )

        linked_operation_ids = {
            op_id for link in component_links for op_id in link["operation_ids"]
        }
        unmapped_operations = [
            node["operation_id"]
            for node in operation_nodes
            if node["operation_id"] not in linked_operation_ids
        ]
        unmapped_steps = [
            step["step_id"]
            for chain in aligned_chains
            for step in chain["steps"]
            if not step.get("component_id")
        ]

        # 5. Support map.
        support_map, missing_support = self._support_map(
            components, headline_claim_id
        )

        # ---- Alignment rule checks (warnings / review items) ----
        self._check_derivation_components(components, component_links, warnings)
        self._check_final_result_components(
            components, component_by_id, remap, warnings
        )
        self._collect_review_paths(review_required_paths, review_items)

        for entry in generic_edges:
            warnings.append({
                "code": "generic_edge",
                "message": (
                    f"generic '{GENERIC_EDGE_TYPE}' edge {entry['source']} -> "
                    f"{entry['target']} (no canonical edge type)"
                ),
                "source": entry["source"],
                "target": entry["target"],
            })
        for cid in missing_support:
            warnings.append({
                "code": "component_missing_from_support_map",
                "message": f"component '{cid}' is not assigned to any support layer",
                "component_id": cid,
            })

        # ---- Hard errors ----
        for ref in unresolved_component_refs:
            errors.append({
                "code": "dangling_component_ref",
                "message": (
                    f"component reference '{ref['missing_ref']}' could not be "
                    f"resolved to a refined component"
                ),
                **ref,
            })
        for ref in dangling_component_refs:
            errors.append({
                "code": "dangling_component_ref",
                "message": (
                    f"theory graph edge references missing component "
                    f"'{ref['missing_ref']}'"
                ),
                **ref,
            })
        for ref in dangling_equation_refs:
            errors.append({
                "code": "dangling_equation_ref",
                "message": (
                    f"equation operation '{ref['operation_id']}' references "
                    f"missing equation '{ref['missing_ref']}'"
                ),
                **ref,
            })
        operation_node_in_component_graph = _operation_nodes_in_component_graph(
            theory_graph
        )
        for node_id in operation_node_in_component_graph:
            errors.append({
                "code": "component_graph_contains_operation_node",
                "message": f"theory component graph contains operation node '{node_id}'",
                "node_id": node_id,
            })
        support_missing_refs = _support_map_missing_components(support_map, component_ids)
        for cid in support_missing_refs:
            errors.append({
                "code": "support_map_missing_component",
                "message": f"support map references non-existent component '{cid}'",
                "component_id": cid,
            })

        component_graph_clean = not operation_node_in_component_graph
        operation_graph_clean = not dangling_equation_refs
        support_map_renderable = not support_missing_refs and not missing_support
        derivation_order_valid = self._derivation_order_valid(aligned_chains, eq_ids_known)

        graph_alignment_validation = {
            "component_graph_clean": component_graph_clean,
            "operation_graph_clean": operation_graph_clean,
            "derivation_order_valid": derivation_order_valid,
            "support_map_renderable": support_map_renderable,
            "dangling_component_refs": [r["missing_ref"] for r in dangling_component_refs]
            + [r["missing_ref"] for r in unresolved_component_refs],
            "dangling_equation_refs": [r["missing_ref"] for r in dangling_equation_refs],
            "unmapped_operations": unmapped_operations,
            "unmapped_derivation_steps": unmapped_steps,
            "review_required_paths": [p["derivation_id"] for p in review_required_paths],
            "generic_edges": generic_edges,
            "missing_support_layers": missing_support,
        }

        export_validation = {
            "errors": errors,
            "warnings": warnings,
            "review_items": review_items,
            "component_graph_clean": component_graph_clean,
            "operation_graph_clean": operation_graph_clean,
            "support_map_renderable": support_map_renderable,
        }

        return DerivationGraphAlignmentResult(
            theory_component_graph=theory_graph,
            equation_operation_graph=operation_graph,
            component_operation_links=component_links,
            aligned_derivation_chains=aligned_chains,
            support_map=support_map,
            graph_alignment_validation=graph_alignment_validation,
            export_validation=export_validation,
            unresolved_component_refs=unresolved_component_refs,
        )

    # ------------------------------------------------------------------
    # 1. Theory component graph
    # ------------------------------------------------------------------

    def _theory_component_graph(
        self,
        components,
        component_ids,
        remap,
        eq_index,
        unresolved_component_refs,
    ):
        nodes: list[dict] = []
        for component in components:
            nodes.append({
                "component_id": component.component_id,
                "component_type": _node_component_type(component),
                "support_role": _support_role(component),
                "concepts": list(component.concepts or []),
                "review_status": _component_review_status(component, eq_index),
            })

        edges: list[dict] = []
        generic_edges: list[dict] = []
        dangling: list[dict] = []
        seen: set[tuple] = set()
        for component in components:
            source = component.component_id
            for dep in component.dependencies or []:
                if not isinstance(dep, dict):
                    continue
                dep_type = str(dep.get("dependency_type") or "")
                edge_type, is_generic = _edge_type_for_dependency(dep_type)
                for raw_ref in dep.get("component_refs") or []:
                    resolved = _resolve_ref(
                        raw_ref, component_ids, remap, unresolved_component_refs, source
                    )
                    for target in resolved:
                        if target == source:
                            continue
                        if target not in component_ids:
                            dangling.append({
                                "component_id": source,
                                "missing_ref": target,
                            })
                            continue
                        key = (source, target, edge_type)
                        if key in seen:
                            continue
                        seen.add(key)
                        edge = {
                            "source": source,
                            "target": target,
                            "edge_type": edge_type,
                            "evidence_refs": _shared_refs(
                                component, _component_by_id(components, target),
                                "linked_evidence_ids",
                            ),
                            "claim_refs": _shared_refs(
                                component, _component_by_id(components, target),
                                "linked_claim_ids",
                            ),
                            "derivation_refs": _shared_refs(
                                component, _component_by_id(components, target),
                                "linked_derivation_ids",
                            ),
                        }
                        edges.append(edge)
                        if is_generic:
                            generic_edges.append({
                                "source": source,
                                "target": target,
                            })

        graph = {
            "graph_type": "theory_component_graph",
            "nodes": nodes,
            "edges": edges,
        }
        return graph, generic_edges, dangling

    # ------------------------------------------------------------------
    # 2. Equation operation graph
    # ------------------------------------------------------------------

    def _equation_operation_graph(self, chains, eq_index, eq_ids_known):
        nodes: list[dict] = []
        dangling: list[dict] = []
        op_by_id: dict[str, dict] = {}
        producers: dict[str, list[str]] = {}
        for chain in chains:
            for step in getattr(chain, "steps", []) or []:
                op_id = _operation_id(chain, step)
                inputs = list(step.input_equation_ids or [])
                outputs = list(step.output_equation_ids or [])
                node = {
                    "operation_id": op_id,
                    "operation_type": _operation_type(step.operation),
                    "input_equation_ids": inputs,
                    "output_equation_ids": outputs,
                    "source_evidence_ids": list(step.source_evidence_ids or []),
                    "review_status": _step_review_status(step, eq_index),
                }
                nodes.append(node)
                op_by_id[op_id] = node
                for eq_id in outputs:
                    producers.setdefault(eq_id, []).append(op_id)
                if eq_ids_known:
                    for eq_id in inputs + outputs:
                        if eq_id and eq_id not in eq_ids_known:
                            dangling.append({
                                "operation_id": op_id,
                                "missing_ref": eq_id,
                            })

        edges: list[dict] = []
        seen: set[tuple] = set()
        # Equation flow: producer output -> consumer input.
        for node in nodes:
            for eq_id in node["input_equation_ids"]:
                for producer_id in producers.get(eq_id, []):
                    if producer_id == node["operation_id"]:
                        continue
                    key = (producer_id, node["operation_id"], "produces_input_for")
                    if key in seen:
                        continue
                    seen.add(key)
                    edges.append({
                        "source": producer_id,
                        "target": node["operation_id"],
                        "edge_type": "produces_input_for",
                    })
        # Sequential dependency within a chain.
        for chain in chains:
            steps = getattr(chain, "steps", []) or []
            for prev, curr in zip(steps, steps[1:]):
                src = _operation_id(chain, prev)
                dst = _operation_id(chain, curr)
                key = (dst, src, "operation_depends_on")
                if key in seen:
                    continue
                seen.add(key)
                edges.append({
                    "source": dst,
                    "target": src,
                    "edge_type": "operation_depends_on",
                })

        graph = {
            "graph_type": "equation_operation_graph",
            "nodes": nodes,
            "edges": edges,
        }
        return graph, nodes, dangling

    # ------------------------------------------------------------------
    # 4. Aligned derivation chains
    # ------------------------------------------------------------------

    def _aligned_chains(
        self,
        chains,
        components,
        component_by_id,
        remap,
        eq_index,
        unresolved_component_refs,
    ):
        aligned: list[dict] = []
        step_owner: dict[str, str] = {}
        review_paths: list[dict] = []
        for chain in chains:
            candidates = self._chain_component_candidates(
                chain, components, component_by_id, remap, unresolved_component_refs
            )
            steps_out: list[dict] = []
            chain_review = _norm_status(getattr(chain, "review_status", ""))
            for step in getattr(chain, "steps", []) or []:
                owner = _assign_step_owner(step, candidates, component_by_id)
                op_id = _operation_id(chain, step)
                status = _step_review_status(step, eq_index)
                if status == REVIEW_REQUIRED:
                    chain_review = REVIEW_REQUIRED
                if owner:
                    step_owner[op_id] = owner
                steps_out.append({
                    "step_id": step.step_id,
                    "component_id": owner,
                    "operation_id": op_id,
                    "operation": _operation_type(step.operation),
                    "input_equation_ids": list(step.input_equation_ids or []),
                    "output_equation_ids": list(step.output_equation_ids or []),
                    "source_evidence_ids": list(step.source_evidence_ids or []),
                    "linked_claim_ids": _ordered_unique(
                        list(step.required_claim_ids or [])
                        + list(step.input_claim_ids or [])
                        + list(step.output_claim_ids or [])
                    ),
                    "review_status": status,
                })
            entry = {
                "derivation_id": chain.derivation_id,
                "name": getattr(chain, "teaching_takeaway", "") or chain.derivation_id,
                "linked_component_ids": candidates,
                "steps": steps_out,
                "review_status": chain_review,
            }
            aligned.append(entry)
            if chain_review == REVIEW_REQUIRED:
                review_paths.append({
                    "derivation_id": chain.derivation_id,
                    "review_required_step_ids": [
                        s["step_id"] for s in steps_out
                        if s["review_status"] == REVIEW_REQUIRED
                    ],
                })
        return aligned, step_owner, review_paths

    def _chain_component_candidates(
        self, chain, components, component_by_id, remap, unresolved_component_refs
    ):
        candidates: list[str] = []
        for raw in getattr(chain, "linked_component_ids", []) or []:
            for resolved in _resolve_ref(
                raw, set(component_by_id), remap, unresolved_component_refs,
                f"derivation:{chain.derivation_id}",
            ):
                if resolved in component_by_id and resolved not in candidates:
                    candidates.append(resolved)
        # Components that explicitly link this derivation.
        for component in components:
            if chain.derivation_id in (component.linked_derivation_ids or []):
                if component.component_id not in candidates:
                    candidates.append(component.component_id)
        return candidates

    # ------------------------------------------------------------------
    # 3. Component operation links
    # ------------------------------------------------------------------

    def _component_operation_links(self, components, aligned_chains, eq_index):
        ops_by_component: dict[str, list[str]] = {}
        for chain in aligned_chains:
            for step in chain["steps"]:
                cid = step.get("component_id")
                if not cid:
                    continue
                ops_by_component.setdefault(cid, [])
                if step["operation_id"] not in ops_by_component[cid]:
                    ops_by_component[cid].append(step["operation_id"])

        links: list[dict] = []
        for component in components:
            operation_ids = ops_by_component.get(component.component_id, [])
            if not operation_ids:
                continue
            inputs = list(component.input_equation_ids or [])
            outputs = list(component.output_equation_ids or [])
            if not inputs or not outputs:
                # Aggregate from owned operations when the component fields are bare.
                op_inputs, op_outputs = _operation_io(
                    component.component_id, aligned_chains
                )
                inputs = inputs or op_inputs
                outputs = outputs or op_outputs
            links.append({
                "component_id": component.component_id,
                "operation_ids": operation_ids,
                "role": _link_role(component),
                "input_equation_ids": _ordered_unique(inputs),
                "output_equation_ids": _ordered_unique(outputs),
                "review_status": _component_review_status(component, eq_index),
            })
        return links

    # ------------------------------------------------------------------
    # 5. Support map
    # ------------------------------------------------------------------

    def _support_map(self, components, headline_claim_id):
        layer_members: dict[str, list[str]] = {
            layer: [] for layer in STANDARD_SUPPORT_LAYERS
        }
        assigned: set[str] = set()
        primary: list[str] = []
        secondary: list[str] = []
        review_required: list[str] = []
        edges: list[dict] = []
        for component in components:
            role = _support_role(component)
            layer = SUPPORT_ROLE_TO_LAYER.get(role)
            if not layer:
                # Generate a layer id from the (non-standard) support role.
                layer = f"{role}_layer"
                layer_members.setdefault(layer, [])
            layer_members[layer].append(component.component_id)
            assigned.add(component.component_id)

            status = _component_review_status(component, None)
            if status == REVIEW_REQUIRED:
                review_required.append(component.component_id)
            elif role == "result":
                primary.append(component.component_id)
            else:
                secondary.append(component.component_id)

            if headline_claim_id:
                edges.append({
                    "source": component.component_id,
                    "target": headline_claim_id,
                    "support_kind": _support_kind(component),
                })

        layers = [
            {"layer_id": layer_id, "component_ids": layer_members[layer_id]}
            for layer_id in _layer_order(layer_members)
        ]
        support_map = {
            "headline_claim_id": headline_claim_id or "",
            "layers": layers,
            "edges": edges,
            "emphasis": {
                "primary_novelty_component_ids": primary,
                "secondary_support_component_ids": secondary,
                "review_required_component_ids": review_required,
            },
        }
        missing = [c.component_id for c in components if c.component_id not in assigned]
        return support_map, missing

    # ------------------------------------------------------------------
    # Alignment rule checks
    # ------------------------------------------------------------------

    def _check_derivation_components(self, components, component_links, warnings):
        link_by_component = {link["component_id"]: link for link in component_links}
        for component in components:
            if _responsibility(component) not in DERIVATION_RESPONSIBILITIES:
                continue
            link = link_by_component.get(component.component_id)
            inputs = list(component.input_equation_ids or [])
            outputs = list(component.output_equation_ids or [])
            if link:
                inputs = inputs or link["input_equation_ids"]
                outputs = outputs or link["output_equation_ids"]
            if not inputs or not outputs:
                warnings.append({
                    "code": "derivation_component_missing_io_equations",
                    "message": (
                        f"derivation component '{component.component_id}' is missing "
                        f"{'input' if not inputs else 'output'} equations"
                    ),
                    "component_id": component.component_id,
                })

    def _check_final_result_components(
        self, components, component_by_id, remap, warnings
    ):
        for component in components:
            if not _is_final_result(component):
                continue
            if not _has_upstream_derivation(
                component, component_by_id, remap
            ):
                warnings.append({
                    "code": "final_result_missing_upstream_derivation",
                    "message": (
                        f"final result/constraint component "
                        f"'{component.component_id}' has no upstream derivation "
                        f"component"
                    ),
                    "component_id": component.component_id,
                })

    def _collect_review_paths(self, review_required_paths, review_items):
        for path in review_required_paths:
            review_items.append({
                "code": "review_required_derivation_path",
                "message": (
                    f"derivation '{path['derivation_id']}' contains review-required "
                    f"steps due to low-confidence equations"
                ),
                "derivation_id": path["derivation_id"],
                "step_ids": path["review_required_step_ids"],
            })

    def _derivation_order_valid(self, aligned_chains, eq_ids_known) -> bool:
        """Order is invalid when a step consumes an equation that is only
        produced by a *later* step in the same chain (a backward dependency)."""
        for chain in aligned_chains:
            produced_anywhere: set[str] = set()
            for step in chain["steps"]:
                produced_anywhere.update(step["output_equation_ids"])
            produced_so_far: set[str] = set()
            for step in chain["steps"]:
                for eq_id in step["input_equation_ids"]:
                    if eq_id in produced_anywhere and eq_id not in produced_so_far:
                        return False
                produced_so_far.update(step["output_equation_ids"])
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_remap(result) -> dict[str, list[str]]:
    """old component id -> [new component ids] from Step 3 id mapping."""
    refinement = getattr(result, "component_refinement", {}) or {}
    remap: dict[str, list[str]] = {}
    for entry in refinement.get("component_id_mapping") or []:
        if not isinstance(entry, dict):
            continue
        old_id = str(entry.get("original_component_id") or "")
        new_ids = [str(v) for v in entry.get("new_component_ids") or [] if str(v)]
        if old_id:
            remap[old_id] = new_ids
    return remap


def _resolve_ref(raw_ref, component_ids, remap, unresolved, source) -> list[str]:
    ref = str(raw_ref or "")
    if not ref:
        return []
    if ref in component_ids:
        return [ref]
    if ref in remap:
        resolved = [r for r in remap[ref] if r in component_ids]
        if resolved:
            return resolved
    unresolved.append({"component_id": source, "missing_ref": ref})
    return []


def _equation_index(llm_input) -> dict[str, dict]:
    index: dict[str, dict] = {}
    if not llm_input:
        return index
    pools = list(getattr(llm_input, "equations", []) or []) + list(
        getattr(llm_input, "available_equations", []) or []
    )
    for eq in pools:
        eq_id = str(eq.get("equation_id") or "")
        if not eq_id:
            continue
        merged = dict(index.get(eq_id, {}))
        merged.update(eq)
        index[eq_id] = merged
    return index


def _headline_claim_id(llm_input) -> str:
    if not llm_input:
        return ""
    plan = getattr(llm_input, "claim_centered_plan", {}) or {}
    ids = plan.get("headline_claim_ids") or []
    if ids:
        return str(ids[0])
    return ""


def _node_component_type(component) -> str:
    responsibility = _responsibility(component)
    if responsibility:
        return responsibility
    return str(getattr(component, "component_type", "") or "")


def _responsibility(component) -> str:
    raw = getattr(component, "responsibility_type", "") or getattr(
        component, "operation", ""
    )
    return canonical_responsibility_type(raw)


def _support_role(component) -> str:
    role = str(getattr(component, "support_role", "") or "")
    if role:
        return role
    metadata = infer_support_metadata(component, None)
    return metadata["support_role"]


def _support_kind(component) -> str:
    kind = str(getattr(component, "support_kind", "") or "")
    if kind:
        return kind
    metadata = infer_support_metadata(component, None)
    return metadata["support_kind"]


def _norm_status(value) -> str:
    raw = str(value or "")
    if raw in {SOURCE_BACKED, "auto_accepted"}:
        return SOURCE_BACKED
    return REVIEW_REQUIRED


def _component_review_status(component, eq_index) -> str:
    if _norm_status(getattr(component, "review_status", "")) == REVIEW_REQUIRED:
        return REVIEW_REQUIRED
    if getattr(component, "review_required_equation_ids", None):
        return REVIEW_REQUIRED
    gate = getattr(component, "confidence_gate", {}) or {}
    if gate.get("blocked_by_equation_ids"):
        return REVIEW_REQUIRED
    # Final-result components rendering blocked equations are review_required.
    if eq_index and _is_final_result(component):
        for eq_id in _final_equation_ids(component):
            if _cannot_render_final(eq_index.get(eq_id)):
                return REVIEW_REQUIRED
    return SOURCE_BACKED


def _step_review_status(step, eq_index) -> str:
    if _norm_status(getattr(step, "review_status", "")) == REVIEW_REQUIRED:
        return REVIEW_REQUIRED
    gate = getattr(step, "confidence_gate", {}) or {}
    if gate.get("blocked_by_equation_ids"):
        return REVIEW_REQUIRED
    if eq_index:
        for eq_id in list(step.input_equation_ids or []) + list(
            step.output_equation_ids or []
        ):
            if _blocked_in_derivation(eq_index.get(eq_id)):
                return REVIEW_REQUIRED
    return SOURCE_BACKED


def _eq_policy(eq) -> dict:
    if not isinstance(eq, dict):
        return {}
    policy = eq.get("confidence_policy")
    return policy if isinstance(policy, dict) else {}


def _blocked_in_derivation(eq) -> bool:
    policy = _eq_policy(eq)
    if not policy:
        return False
    if str(policy.get("allowed_downstream_use") or "") == "blocked":
        return True
    return policy.get("can_be_used_in_derivation") is False


def _cannot_render_final(eq) -> bool:
    policy = _eq_policy(eq)
    if not policy:
        return False
    if str(policy.get("allowed_downstream_use") or "") == "blocked":
        return True
    return policy.get("can_be_rendered_as_final_formula") is False


def _edge_type_for_dependency(dep_type: str) -> tuple[str, bool]:
    mapped = DEPENDENCY_TO_EDGE.get(dep_type)
    if mapped in CANONICAL_EDGE_TYPES:
        return mapped, False
    return GENERIC_EDGE_TYPE, True


def _operation_type(operation: str) -> str:
    op = str(operation or "").lower()
    if "substitut" in op:
        return "substitute"
    if "eliminat" in op:
        return "eliminate"
    if "lineariz" in op:
        return "linearize"
    if "approxim" in op:
        return "approximate"
    if "solve" in op:
        return "solve"
    if "deriv" in op:
        return "derive"
    if "define" in op or "definition" in op:
        return "define"
    if "transform" in op:
        return "transform"
    return "transform"


def _operation_id(chain, step) -> str:
    step_id = str(getattr(step, "step_id", "") or "")
    if step_id:
        return step_id
    return f"{chain.derivation_id}:op"


def _link_role(component) -> str:
    responsibility = _responsibility(component)
    if responsibility in {"definition", "model", "observation_model", "observable_basis"}:
        return "defines"
    if responsibility == "derivation":
        return "derives"
    if responsibility == "constraint":
        return "constrains"
    if responsibility in {"application", "limitation"}:
        return "validates"
    return "uses"


def _is_final_result(component) -> bool:
    if _responsibility(component) in RESULT_RESPONSIBILITIES:
        return True
    return _support_role(component) == "result"


def _final_equation_ids(component) -> list[str]:
    ids = list(component.output_equation_ids or []) + list(
        component.constraint_equation_ids or []
    )
    return _ordered_unique(ids)


def _has_upstream_derivation(component, component_by_id, remap) -> bool:
    for dep in component.dependencies or []:
        if not isinstance(dep, dict):
            continue
        for raw_ref in dep.get("component_refs") or []:
            for target in _resolve_to_existing(raw_ref, component_by_id, remap):
                upstream = component_by_id.get(target)
                if upstream and _responsibility(upstream) in DERIVATION_RESPONSIBILITIES:
                    return True
    return False


def _resolve_to_existing(raw_ref, component_by_id, remap) -> list[str]:
    ref = str(raw_ref or "")
    if ref in component_by_id:
        return [ref]
    if ref in remap:
        return [r for r in remap[ref] if r in component_by_id]
    return []


def _assign_step_owner(step, candidates, component_by_id) -> str:
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]
    step_eqs = set(step.input_equation_ids or []) | set(step.output_equation_ids or [])
    best = ""
    best_overlap = 0
    for cid in candidates:
        component = component_by_id.get(cid)
        if not component:
            continue
        overlap = len(step_eqs & set(_component_equation_ids(component)))
        if overlap > best_overlap:
            best_overlap = overlap
            best = cid
    return best or candidates[0]


def _component_equation_ids(component) -> list[str]:
    ids: list[str] = []
    for field_name in (
        "linked_equation_ids",
        "input_equation_ids",
        "intermediate_equation_ids",
        "output_equation_ids",
        "constraint_equation_ids",
        "definition_equation_ids",
        "review_required_equation_ids",
    ):
        ids.extend(getattr(component, field_name, []) or [])
    refs = getattr(component, "evidence_refs", {}) or {}
    ids.extend(refs.get("equation_ids") or [])
    return _ordered_unique(ids)


def _operation_io(component_id, aligned_chains) -> tuple[list[str], list[str]]:
    inputs: list[str] = []
    outputs: list[str] = []
    for chain in aligned_chains:
        for step in chain["steps"]:
            if step.get("component_id") == component_id:
                inputs.extend(step["input_equation_ids"])
                outputs.extend(step["output_equation_ids"])
    return _ordered_unique(inputs), _ordered_unique(outputs)


def _shared_refs(source_component, target_component, field_name) -> list[str]:
    source_refs = set(getattr(source_component, field_name, []) or [])
    if target_component is None:
        return _ordered_unique(getattr(source_component, field_name, []) or [])
    target_refs = set(getattr(target_component, field_name, []) or [])
    return _ordered_unique(source_refs & target_refs)


def _component_by_id(components, component_id):
    for component in components:
        if component.component_id == component_id:
            return component
    return None


def _layer_order(layer_members) -> list[str]:
    order = [layer for layer in STANDARD_SUPPORT_LAYERS]
    for layer in layer_members:
        if layer not in order:
            order.append(layer)
    return order


def _operation_nodes_in_component_graph(theory_graph) -> list[str]:
    bad: list[str] = []
    for node in theory_graph.get("nodes", []):
        if "operation_id" in node or "operation_type" in node:
            bad.append(node.get("operation_id") or node.get("component_id") or "")
    return bad


def _support_map_missing_components(support_map, component_ids) -> list[str]:
    missing: list[str] = []
    for layer in support_map.get("layers", []):
        for cid in layer.get("component_ids", []):
            if cid not in component_ids and cid not in missing:
                missing.append(cid)
    emphasis = support_map.get("emphasis", {}) or {}
    for key in (
        "primary_novelty_component_ids",
        "secondary_support_component_ids",
        "review_required_component_ids",
    ):
        for cid in emphasis.get(key, []) or []:
            if cid not in component_ids and cid not in missing:
                missing.append(cid)
    return missing


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text = str(value or "")
        if text and text not in result:
            result.append(text)
    return result
