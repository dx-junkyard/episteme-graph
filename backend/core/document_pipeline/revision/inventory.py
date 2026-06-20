"""Baseline inventory builder for the iterative-improvement pipeline (#403).

Reads the adopted (base) run's ``stage_outputs._artifacts`` payload and produces a
normalized, cross-referenced inventory of every artifact entity (claims,
equations, evidence, derivations, components, graph nodes, thesis) plus the
relations between them, the existing validation/review findings, the
teacher-approved / manually-edited *protected* decisions, and any reference that
could not be resolved.

This module is intentionally deterministic and side-effect free: the same
``artifacts`` dict always yields the same inventory, so the deterministic portion
of checkpoint planning is reproducible (AC #403).
"""
from __future__ import annotations

import copy
from typing import Any

ARTIFACTS_KEY = "_artifacts"

# Entity buckets used throughout the revision subsystem.
ENTITY_TYPES = (
    "claims",
    "equations",
    "evidence",
    "derivations",
    "components",
    "graph_nodes",
    "graph_edges",
    "thesis",
)

# review_status / flag values that mark a decision as protected (teacher-approved
# or manually edited). These must never be silently overwritten by a candidate.
_PROTECTED_REVIEW_STATUSES = {
    "teacher_approved",
    "approved",
    "manually_edited",
    "manual_edit",
    "locked",
    "teacher_locked",
    "human_verified",
}
_PROTECTED_FLAGS = ("teacher_approved", "manual_edit", "manually_edited", "locked")


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _str_list(value: Any) -> list[str]:
    return [str(v) for v in _as_list(value) if v not in (None, "")]


def get_artifacts(stage_outputs: Any) -> dict:
    """Return the ``_artifacts`` sub-dict from a stage_outputs payload."""
    payload = _as_dict(stage_outputs)
    artifacts = payload.get(ARTIFACTS_KEY)
    return artifacts if isinstance(artifacts, dict) else {}


def _is_protected(record: dict) -> bool:
    if str(record.get("review_status") or "").strip().lower() in _PROTECTED_REVIEW_STATUSES:
        return True
    for flag in _PROTECTED_FLAGS:
        if record.get(flag):
            return True
    return False


def _source_locations(record: dict) -> list[dict]:
    """Collect source-position pointers from an artifact record, normalized."""
    locations: list[dict] = []
    scope = _as_dict(record.get("source_scope"))
    if scope.get("section_id") or scope.get("block_id") or scope.get("span_id"):
        locations.append({
            "section_id": scope.get("section_id") or record.get("section_id") or "",
            "block_id": scope.get("block_id") or "",
            "span_id": scope.get("span_id") or "",
            "page": scope.get("page"),
        })
    for key in ("source_location", "source_locations"):
        val = record.get(key)
        if isinstance(val, dict):
            locations.append(val)
        elif isinstance(val, list):
            locations.extend(v for v in val if isinstance(v, dict))
    if not locations and record.get("section_id"):
        locations.append({"section_id": record.get("section_id"), "block_id": "", "span_id": ""})
    return locations


# ---------------------------------------------------------------------------
# Per-type normalization
# ---------------------------------------------------------------------------

def _index_claims(artifacts: dict) -> dict[str, dict]:
    cb = _as_dict(artifacts.get("claim_object_builder"))
    out: dict[str, dict] = {}
    for r in _as_list(cb.get("claims")):
        if not isinstance(r, dict):
            continue
        cid = str(r.get("claim_id") or "").strip()
        if not cid:
            continue
        out[cid] = {
            "entity_id": cid,
            "entity_type": "claim",
            "text": r.get("text") or "",
            "normalized_text": r.get("normalized_text") or r.get("text") or "",
            "claim_type": r.get("claim_type") or "unknown",
            "is_atomic": bool(r.get("is_atomic", str(r.get("atomicity", "atomic")) == "atomic")),
            "atomicity": r.get("atomicity") or "atomic",
            "support_status": r.get("support_status") or "source_backed",
            "review_status": r.get("review_status") or "teacher_review_required",
            "review_reasons": _str_list(r.get("review_reasons")),
            "confidence": float(r.get("confidence") or 0.0),
            "equation_ids": _str_list(r.get("equation_ids")),
            "evidence_ids": _str_list(r.get("source_evidence_ids")),
            "component_ids": _str_list(r.get("linked_component_ids")),
            "derivation_ids": _str_list(r.get("derivation_ids")),
            "source_locations": _source_locations(r),
            "protected": _is_protected(r),
        }
    return out


def _index_equations(artifacts: dict) -> dict[str, dict]:
    eq = _as_dict(artifacts.get("equation_semantics"))
    out: dict[str, dict] = {}
    for r in _as_list(eq.get("equations")) or _as_list(eq.get("records")):
        if not isinstance(r, dict):
            continue
        eid = str(r.get("equation_id") or r.get("id") or "").strip()
        if not eid:
            continue
        reconstruction = _as_dict(r.get("reconstruction"))
        extraction = _as_dict(r.get("source_extraction"))
        policy = _as_dict(r.get("confidence_policy"))
        out[eid] = {
            "entity_id": eid,
            "entity_type": "equation",
            "label": r.get("label") or reconstruction.get("label") or "",
            "latex": reconstruction.get("latex") or r.get("latex") or "",
            "raw_text": extraction.get("raw_text") or r.get("raw_text") or "",
            "review_status": r.get("review_status") or policy.get("review_status") or "",
            "confidence": float(r.get("confidence") or policy.get("confidence") or 0.0),
            "confidence_gate": policy.get("gate") or r.get("confidence_gate") or "",
            "evidence_ids": _str_list(r.get("source_evidence_ids")),
            "source_locations": _source_locations(r) + (
                [extraction.get("source_location")]
                if isinstance(extraction.get("source_location"), dict) else []
            ),
            "protected": _is_protected(r),
        }
    return out


def _index_evidence(artifacts: dict) -> dict[str, dict]:
    ev = _as_dict(artifacts.get("evidence_registry"))
    out: dict[str, dict] = {}
    for r in _as_list(ev.get("records")):
        if not isinstance(r, dict):
            continue
        eid = str(r.get("evidence_id") or r.get("id") or "").strip()
        if not eid:
            continue
        out[eid] = {
            "entity_id": eid,
            "entity_type": "evidence",
            "evidence_text": r.get("evidence_text") or r.get("text") or "",
            "extraction_status": r.get("extraction_status") or "",
            "extraction_source": r.get("extraction_source") or "",
            "needs_review": bool(r.get("needs_review", False)),
            "source_locations": _source_locations(r),
            "protected": _is_protected(r),
        }
    return out


def _index_derivations(artifacts: dict) -> dict[str, dict]:
    dc = _as_dict(artifacts.get("derivation_chain"))
    out: dict[str, dict] = {}
    for chain in _as_list(dc.get("chains")) or _as_list(dc.get("derivation_chains")):
        if not isinstance(chain, dict):
            continue
        did = str(chain.get("derivation_id") or chain.get("chain_id") or chain.get("id") or "").strip()
        if not did:
            continue
        steps = _as_list(chain.get("steps"))
        equation_ids: list[str] = []
        step_ids: list[str] = []
        for s in steps:
            if isinstance(s, dict):
                equation_ids.extend(_str_list(s.get("input_equation_ids")) + _str_list(s.get("output_equation_ids")))
                equation_ids.extend(_str_list(s.get("inputs")) + _str_list(s.get("outputs")))
                sid = str(s.get("step_id") or s.get("id") or "").strip()
                if sid:
                    step_ids.append(sid)
        out[did] = {
            "entity_id": did,
            "entity_type": "derivation",
            "label": chain.get("label") or chain.get("name") or "",
            "step_count": len(steps),
            # Derivation *step* ids live alongside the chain id; refs may point at
            # either, so both are tracked and the resolver accepts both (#412 P1-7).
            "step_ids": list(dict.fromkeys(step_ids)),
            "review_status": chain.get("review_status") or "",
            "equation_ids": list(dict.fromkeys(equation_ids)),
            "source_locations": _source_locations(chain),
            "protected": _is_protected(chain),
        }
    return out


def _index_components(artifacts: dict) -> dict[str, dict]:
    ca = _as_dict(artifacts.get("component_assembly"))
    out: dict[str, dict] = {}
    for r in _as_list(ca.get("components")):
        if not isinstance(r, dict):
            continue
        cid = str(r.get("component_id") or "").strip()
        if not cid:
            continue
        out[cid] = {
            "entity_id": cid,
            "entity_type": "component",
            "label": r.get("label") or r.get("name") or "",
            "responsibility_type": r.get("responsibility_type") or "",
            "review_status": r.get("review_status") or "teacher_review_required",
            "split_recommendation": _as_dict(r.get("split_recommendation")),
            "component_quality": _as_dict(r.get("component_quality")),
            "confidence": float(r.get("confidence") or 0.0),
            "claim_ids": _str_list(r.get("linked_claim_ids")),
            "equation_ids": _str_list(r.get("linked_equation_ids"))
                            + _str_list(r.get("input_equation_ids"))
                            + _str_list(r.get("output_equation_ids")),
            "evidence_ids": _str_list(r.get("linked_evidence_ids")),
            "derivation_ids": _str_list(r.get("linked_derivation_ids")),
            "source_locations": _source_locations(r),
            "protected": _is_protected(r),
        }
        out[cid]["equation_ids"] = list(dict.fromkeys(out[cid]["equation_ids"]))
    return out


def _graph_node_id(n: dict) -> str:
    return str(n.get("component_id") or n.get("node_id") or n.get("id") or "").strip()


def _index_graph(artifacts: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    cg = _as_dict(artifacts.get("component_graph"))
    nodes: dict[str, dict] = {}
    for n in _as_list(cg.get("nodes")):
        if not isinstance(n, dict):
            continue
        nid = _graph_node_id(n)
        if not nid:
            continue
        nodes[nid] = {
            "entity_id": nid,
            "entity_type": "graph_node",
            "label": n.get("label") or n.get("name") or "",
            "graph_layer": n.get("graph_layer") or "main",
            "component_type": n.get("component_type") or "",
            "operation": n.get("operation") or "",
            "review_status": n.get("review_status") or "teacher_review_required",
            "source_backing_status": n.get("source_backing_status") or "",
            "review_reasons": _str_list(n.get("review_reasons")),
            "claim_ids": _str_list(n.get("linked_claim_ids")),
            "equation_ids": _str_list(n.get("linked_equation_ids")),
            "evidence_ids": _str_list(n.get("linked_evidence_ids")),
            "derivation_ids": _str_list(n.get("linked_derivation_ids")),
            # member_component_ids reference other graph nodes (aggregated detail
            # nodes) — falling back to component ids. Schema-defined so the same
            # resolver classifies them for base + candidate alike (#412 P1-7).
            "member_component_ids": _str_list(n.get("member_component_ids")),
            "protected": _is_protected(n),
        }
    edges: dict[str, dict] = {}
    for i, e in enumerate(_as_list(cg.get("edges"))):
        if not isinstance(e, dict):
            continue
        eid = str(e.get("edge_id") or f"edge_{i+1:04d}")
        source = str(e.get("source_component_id") or e.get("source") or e.get("from") or "")
        target = str(e.get("target_component_id") or e.get("target") or e.get("to") or "")
        edges[eid] = {
            "entity_id": eid,
            "entity_type": "graph_edge",
            "source": source,
            "target": target,
            "edge_type": e.get("relation") or e.get("edge_type") or e.get("type") or "RELATED_TO",
            "source_backing_status": e.get("source_backing_status") or e.get("support_status") or "",
            "review_status": e.get("review_status") or "teacher_review_required",
            "review_reasons": _str_list(e.get("review_reasons")),
            "evidence_equation_ids": _str_list(e.get("evidence_equation_ids")),
            "evidence_claim_ids": _str_list(e.get("evidence_claim_ids")),
            "protected": _is_protected(e),
        }
    return nodes, edges


def _index_thesis(artifacts: dict) -> dict[str, dict]:
    th = _as_dict(artifacts.get("thesis_reconstruction"))
    out: dict[str, dict] = {}
    points = _as_list(th.get("support_structure")) or _as_list(th.get("points"))
    central = th.get("central_thesis") or th.get("thesis")
    if central:
        out["thesis_central"] = {
            "entity_id": "thesis_central",
            "entity_type": "thesis",
            "text": central if isinstance(central, str) else _as_dict(central).get("text", ""),
            "role": "central_thesis",
            "component_ids": _str_list(_as_dict(central).get("component_ids")) if isinstance(central, dict) else [],
            "source_locations": [],
            "protected": _is_protected(_as_dict(central)),
        }
    for i, p in enumerate(points):
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or p.get("point_id") or f"thesis_support_{i+1}")
        out[pid] = {
            "entity_id": pid,
            "entity_type": "thesis",
            "text": p.get("text") or p.get("claim") or "",
            "role": "support",
            "component_ids": _str_list(p.get("component_ids")),
            "claim_ids": _str_list(p.get("claim_ids")),
            "source_locations": _source_locations(p),
            "protected": _is_protected(p),
        }
    return out


# ---------------------------------------------------------------------------
# Relations + reference resolution
# ---------------------------------------------------------------------------

# Which entity-bucket each link field targets.
_LINK_TARGETS = {
    "equation_ids": "equations",
    "evidence_ids": "evidence",
    "derivation_ids": "derivations",
    "component_ids": "components",
    "claim_ids": "claims",
}


def _build_relations(entities: dict[str, dict[str, dict]]) -> tuple[list[dict], list[dict]]:
    relations: list[dict] = []
    unresolved: list[dict] = []

    # Derivation *step* ids resolve a ``derivation_ids`` reference just as a chain
    # id does, so step refs are not mis-flagged as dangling (#412 P1-7).
    derivation_step_ids: set[str] = set()
    for d in entities.get("derivations", {}).values():
        derivation_step_ids.update(d.get("step_ids") or [])
    graph_node_ids = set(entities.get("graph_nodes", {}))
    component_ids = set(entities.get("components", {}))

    def _bucket_has(bucket: str, _id: str) -> bool:
        if _id in entities.get(bucket, {}):
            return True
        if bucket == "derivations" and _id in derivation_step_ids:
            return True
        return False

    def _emit(source_type, source_id, rel_type, target_bucket, target_id, field):
        target_singular = target_bucket.rstrip("s") if target_bucket != "evidence" else "evidence"
        rel = {
            "type": rel_type,
            "source_type": source_type,
            "source_id": source_id,
            "target_type": target_singular,
            "target_id": target_id,
        }
        if _bucket_has(target_bucket, target_id):
            relations.append(rel)
        else:
            unresolved.append({
                "source_type": source_type,
                "source_id": source_id,
                "ref_field": field,
                "target_type": target_singular,
                "target_id": target_id,
                "reason": "missing_target",
            })

    for bucket_name in ("claims", "components", "graph_nodes", "derivations", "thesis", "equations"):
        for ent_id, ent in entities.get(bucket_name, {}).items():
            for field, target_bucket in _LINK_TARGETS.items():
                for target_id in ent.get(field, []) or []:
                    _emit(ent["entity_type"], ent_id, f"{ent['entity_type']}_{field}",
                          target_bucket, target_id, field)

    # Graph node membership: a node aggregates other graph nodes (detail nodes),
    # falling back to component ids. Resolved against both spaces (#412 P1-7).
    for node_id, node in entities.get("graph_nodes", {}).items():
        for target_id in node.get("member_component_ids", []) or []:
            if target_id in graph_node_ids or target_id in component_ids:
                relations.append({
                    "type": "graph_node_member_component_ids",
                    "source_type": "graph_node",
                    "source_id": node_id,
                    "target_type": "graph_node",
                    "target_id": target_id,
                })
            else:
                unresolved.append({
                    "source_type": "graph_node",
                    "source_id": node_id,
                    "ref_field": "member_component_ids",
                    "target_type": "graph_node",
                    "target_id": target_id,
                    "reason": "missing_member",
                })

    # Graph edges → node endpoints.
    for edge_id, edge in entities.get("graph_edges", {}).items():
        for endpoint, role in ((edge.get("source"), "source"), (edge.get("target"), "target")):
            if not endpoint:
                continue
            if _bucket_has("graph_nodes", endpoint):
                relations.append({
                    "type": f"graph_edge_{role}",
                    "source_type": "graph_edge",
                    "source_id": edge_id,
                    "target_type": "graph_node",
                    "target_id": endpoint,
                })
            else:
                unresolved.append({
                    "source_type": "graph_edge",
                    "source_id": edge_id,
                    "ref_field": role,
                    "target_type": "graph_node",
                    "target_id": endpoint,
                    "reason": "missing_endpoint",
                })

    return relations, unresolved


# ---------------------------------------------------------------------------
# Existing issues + protected decisions
# ---------------------------------------------------------------------------

def _collect_existing_issues(artifacts: dict) -> list[dict]:
    issues: list[dict] = []
    gate = _as_dict(artifacts.get("export_validation"))
    for severity in ("errors", "warnings", "review_items"):
        for entry in _as_list(gate.get(severity)):
            if not isinstance(entry, dict):
                continue
            issues.append({
                "source": "export_validation",
                "severity": severity.rstrip("s") if severity != "review_items" else "review_required",
                "code": entry.get("code") or "",
                "message": entry.get("message") or "",
                "artifact": entry.get("artifact") or "",
                "path": entry.get("path") or "",
                "target_id": entry.get("target_id") or _target_from_path(entry.get("path")),
            })
    # Per-artifact validation_issues.
    for stage, payload in artifacts.items():
        if stage in ("export_validation",):
            continue
        for vi in _as_list(_as_dict(payload).get("validation_issues")):
            if not isinstance(vi, dict):
                if isinstance(vi, str):
                    issues.append({"source": stage, "severity": "warning", "code": "", "message": vi,
                                   "artifact": stage, "path": "", "target_id": ""})
                continue
            issues.append({
                "source": stage,
                "severity": vi.get("severity") or "warning",
                "code": vi.get("code") or "",
                "message": vi.get("message") or vi.get("reason") or "",
                "artifact": stage,
                "path": vi.get("path") or "",
                "target_id": vi.get("target_id") or vi.get("id") or "",
            })
    return issues


def _target_from_path(path: Any) -> str:
    if not isinstance(path, str):
        return ""
    # paths like "claims[clm_3].equation" -> clm_3
    if "[" in path and "]" in path:
        return path[path.index("[") + 1: path.index("]")]
    return ""


def _collect_protected(entities: dict[str, dict[str, dict]]) -> list[dict]:
    protected: list[dict] = []
    for bucket, ents in entities.items():
        for ent_id, ent in ents.items():
            if ent.get("protected"):
                protected.append({
                    "entity_type": ent.get("entity_type"),
                    "entity_id": ent_id,
                    "review_status": ent.get("review_status"),
                    "reason": "teacher_approved_or_manual_edit",
                })
    return protected


def _legacy_ids(row: dict) -> list[str]:
    scope = _as_dict(row.get("source_scope"))
    return _str_list(scope.get("legacy_ids"))


def _match_projection_entity(
    rows: list[dict],
    entities: dict[str, dict],
    *,
    text_field: str,
    row_text_field: str,
) -> list[tuple[dict, str]]:
    """Resolve projection rows to artifact ids without replacing stable agent ids."""
    matched: list[tuple[dict, str]] = []
    text_index: dict[str, list[str]] = {}
    location_index: dict[tuple[str, str, str], list[str]] = {}
    for entity_id, entity in entities.items():
        text = str(entity.get(text_field) or "").strip()
        if text:
            text_index.setdefault(text, []).append(entity_id)
        for location in entity.get("source_locations") or []:
            if not isinstance(location, dict):
                continue
            key = (
                str(location.get("section_id") or ""),
                str(location.get("block_id") or ""),
                str(location.get("span_id") or ""),
            )
            if any(key):
                location_index.setdefault(key, []).append(entity_id)
    for row in rows or []:
        entity_id = next((value for value in _legacy_ids(row) if value in entities), "")
        if not entity_id:
            scope = _as_dict(row.get("source_scope"))
            location_key = (
                str(scope.get("section_id") or ""),
                str(scope.get("block_id") or ""),
                str(scope.get("span_id") or ""),
            )
            candidates = location_index.get(location_key) or []
            if any(location_key) and len(candidates) == 1:
                entity_id = candidates[0]
        if not entity_id:
            text = str(row.get(row_text_field) or "").strip()
            candidates = text_index.get(text) or []
            if len(candidates) == 1:
                entity_id = candidates[0]
        if entity_id:
            matched.append((row, entity_id))
    return matched


def overlay_projection_state(inventory: dict, projection: dict | None) -> dict:
    """Overlay current DB edits/review decisions onto an artifact inventory.

    Artifact entity ids remain the canonical operation ids. Projection UUIDs are
    attached as metadata and used to associate review events.
    """
    result = copy.deepcopy(inventory or {})
    projection = projection or {}
    entities = result.setdefault("entities", {})
    claims = entities.setdefault("claims", {})
    components = entities.setdefault("components", {})
    db_to_entity: dict[tuple[str, str], str] = {}

    for row, entity_id in _match_projection_entity(
        projection.get("claims") or [],
        claims,
        text_field="normalized_text",
        row_text_field="normalized_text",
    ):
        entity = claims[entity_id]
        original = {
            "text": entity.get("text") or "",
            "normalized_text": entity.get("normalized_text") or "",
            "claim_type": entity.get("claim_type") or "",
            "support_status": entity.get("support_status") or "",
            "review_status": entity.get("review_status") or "",
        }
        current = {
            "text": row.get("text") or "",
            "normalized_text": row.get("normalized_text") or row.get("text") or "",
            "claim_type": row.get("claim_type") or "",
            "support_status": row.get("support_status") or "",
            "review_status": row.get("review_status") or "",
        }
        db_id = str(row.get("id") or "")
        if db_id:
            db_to_entity[("claim", db_id)] = entity_id
            entity["projection_id"] = db_id
        entity.update(current)
        if current != original:
            entity["manual_edit"] = True
            entity["protected"] = True
            entity["projection_diverged_from_artifact"] = True

    for row, entity_id in _match_projection_entity(
        projection.get("components") or [],
        components,
        text_field="label",
        row_text_field="name",
    ):
        entity = components[entity_id]
        original = {
            "label": entity.get("label") or "",
            "summary": entity.get("summary") or "",
            "review_status": entity.get("review_status") or "",
        }
        current = {
            "label": row.get("name") or "",
            "summary": row.get("summary") or "",
            "review_status": row.get("review_status") or "",
        }
        db_id = str(row.get("id") or "")
        if db_id:
            db_to_entity[("component", db_id)] = entity_id
            entity["projection_id"] = db_id
        entity.update(current)
        if current != original:
            entity["manual_edit"] = True
            entity["protected"] = True
            entity["projection_diverged_from_artifact"] = True

    latest_events: dict[tuple[str, str], dict] = {}
    for event in projection.get("review_events") or []:
        if not isinstance(event, dict):
            continue
        key = (str(event.get("entity_type") or ""), str(event.get("entity_id") or ""))
        if key in db_to_entity:
            latest_events[key] = event
    for key, event in latest_events.items():
        entity_id = db_to_entity[key]
        bucket = claims if key[0] == "claim" else components
        entity = bucket[entity_id]
        entity["latest_review_event"] = {
            "old_status": event.get("old_status") or "",
            "new_status": event.get("new_status") or "",
            "metadata": _as_dict(event.get("metadata")),
        }
        new_status = str(event.get("new_status") or "").strip().lower()
        metadata = _as_dict(event.get("metadata"))
        if new_status in _PROTECTED_REVIEW_STATUSES or any(
            metadata.get(flag) for flag in _PROTECTED_FLAGS
        ):
            entity["protected"] = True

    result["protected_decisions"] = _collect_protected(entities)
    result["projection_overlay"] = {
        "claim_count": len(projection.get("claims") or []),
        "component_count": len(projection.get("components") or []),
        "review_event_count": len(projection.get("review_events") or []),
    }
    return result


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def build_baseline_inventory(
    artifacts: dict,
    *,
    base_run_id: str,
    document_id: str = "",
) -> dict:
    """Build a normalized, cross-referenced inventory from base-run artifacts."""
    artifacts = _as_dict(artifacts)
    graph_nodes, graph_edges = _index_graph(artifacts)
    entities = {
        "claims": _index_claims(artifacts),
        "equations": _index_equations(artifacts),
        "evidence": _index_evidence(artifacts),
        "derivations": _index_derivations(artifacts),
        "components": _index_components(artifacts),
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
        "thesis": _index_thesis(artifacts),
    }
    relations, unresolved = _build_relations(entities)
    counts = {bucket: len(ents) for bucket, ents in entities.items()}
    return {
        "base_run_id": base_run_id,
        "document_id": document_id,
        "entities": entities,
        "entity_counts": counts,
        "relations": relations,
        "existing_issues": _collect_existing_issues(artifacts),
        "protected_decisions": _collect_protected(entities),
        "unresolved_references": unresolved,
    }
