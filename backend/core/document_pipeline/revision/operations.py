"""Explicit revision operations and deterministic candidate assembly (#405).

A revision is recorded as a list of explicit operations (add/update/split/merge/
remove entity, add/remove relation, relink evidence). ``apply_operations`` reads
the base artifacts as *immutable* input, applies the operations in a
deterministic order, follows id changes (split/merge mapping) across all
references, and returns a candidate set of artifacts plus per-operation
validation results.

Safety rules (AC #405):
- Base artifacts are never mutated (deep-copied).
- Operations apply in a canonical, reproducible order.
- split/merge id mappings are recorded and references are followed.
- A reference to an id that does not exist (and was not already unresolved in
  the base) is an error: the offending operation fails and the whole candidate
  is marked invalid (no partial adoption).
- Operations targeting protected entities are flagged for confirmation and are
  not treated as auto-adoptable.
- Entities not targeted by any operation are left byte-for-byte unchanged.
"""
from __future__ import annotations

import copy
from typing import Any

OPERATIONS = (
    "add_entity",
    "update_entity",
    "split_entity",
    "merge_entities",
    "remove_entity",
    "add_relation",
    "remove_relation",
    "relink_evidence",
)

# entity_type -> (artifact_key, list_key, id_field)
ENTITY_REGISTRY = {
    "claim": ("claim_object_builder", "claims", "claim_id"),
    "equation": ("equation_semantics", "equations", "equation_id"),
    "evidence": ("evidence_registry", "records", "evidence_id"),
    "derivation": ("derivation_chain", "chains", "derivation_id"),
    "component": ("component_assembly", "components", "component_id"),
    "graph_node": ("component_graph", "nodes", None),
    "graph_edge": ("component_graph", "edges", "edge_id"),
}

# List reference fields per entity type: (field_name, target_entity_type).
REFERENCE_FIELDS = {
    "claim": [
        ("equation_ids", "equation"),
        ("source_evidence_ids", "evidence"),
        ("linked_component_ids", "component"),
        ("derivation_ids", "derivation"),
    ],
    "equation": [
        ("source_evidence_ids", "evidence"),
    ],
    "component": [
        ("linked_claim_ids", "claim"),
        ("linked_equation_ids", "equation"),
        ("input_equation_ids", "equation"),
        ("output_equation_ids", "equation"),
        ("linked_evidence_ids", "evidence"),
        ("linked_derivation_ids", "derivation"),
    ],
    "graph_node": [
        ("linked_claim_ids", "claim"),
        ("linked_equation_ids", "equation"),
        ("linked_evidence_ids", "evidence"),
        ("linked_derivation_ids", "derivation"),
        ("member_component_ids", "component"),
    ],
}

# Canonical apply phases (lower = earlier). Structural changes first so adds and
# relation wiring see the post-structure id space.
_PHASE = {
    "remove_entity": 0,
    "split_entity": 1,
    "merge_entities": 1,
    "add_entity": 2,
    "update_entity": 3,
    "relink_evidence": 4,
    "add_relation": 5,
    "remove_relation": 5,
}


def make_operation(
    *,
    operation: str,
    target_type: str,
    target_id: str = "",
    before_json: Any = None,
    after_json: Any = None,
    reason: str = "",
    checkpoint_ids: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    source_locations: list | None = None,
    confidence: float = 0.0,
    protected_target: bool = False,
    operation_id: str | None = None,
    **extra: Any,
) -> dict:
    if operation not in OPERATIONS:
        raise ValueError(f"unknown revision operation: {operation}")
    op = {
        "operation_id": operation_id or "",
        "operation": operation,
        "target_type": target_type,
        "target_id": target_id,
        "before_json": before_json,
        "after_json": after_json,
        "reason": reason,
        "checkpoint_ids": list(checkpoint_ids or []),
        "evidence_refs": list(evidence_refs or []),
        "source_locations": list(source_locations or []),
        "confidence": float(confidence or 0.0),
        "protected_target": bool(protected_target),
        "validation_result": {"status": "pending"},
    }
    op.update(extra)
    return op


# ---------------------------------------------------------------------------
# Helpers over the candidate artifact tree
# ---------------------------------------------------------------------------

def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _record_id(record: dict, id_field: str | None) -> str:
    if id_field:
        return str(record.get(id_field) or "")
    # graph node fallback resolution
    return str(record.get("component_id") or record.get("node_id") or record.get("id") or "")


def _ensure_container(candidate: dict, entity_type: str) -> list:
    artifact_key, list_key, _ = ENTITY_REGISTRY[entity_type]
    artifact = candidate.setdefault(artifact_key, {})
    if not isinstance(artifact, dict):
        artifact = {}
        candidate[artifact_key] = artifact
    lst = artifact.get(list_key)
    if not isinstance(lst, list):
        lst = []
        artifact[list_key] = lst
    return lst


def _find_record(lst: list, id_field: str | None, target_id: str) -> dict | None:
    for r in lst:
        if isinstance(r, dict) and _record_id(r, id_field) == target_id:
            return r
    return None


def _collect_ids(candidate: dict) -> dict[str, set]:
    ids: dict[str, set] = {}
    for etype, (akey, lkey, idf) in ENTITY_REGISTRY.items():
        bucket = ids.setdefault(etype, set())
        for r in _as_list((candidate.get(akey) or {}).get(lkey)):
            if isinstance(r, dict):
                rid = _record_id(r, idf)
                if rid:
                    bucket.add(rid)
    return ids


# ---------------------------------------------------------------------------
# Reference rewriting after structural id changes
# ---------------------------------------------------------------------------

def _rewrite_list(values: Any, mapping: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for v in _as_list(values):
        sv = str(v)
        if sv in mapping:
            out.extend(mapping[sv])
        else:
            out.append(sv)
    # de-dup preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for v in out:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped


def _apply_id_mapping(candidate: dict, mapping: dict[str, list[str]]) -> list[str]:
    """Follow split/merge/removal id changes across all references.

    Returns the list of graph edge_ids dropped because an endpoint was removed.
    """
    if not mapping:
        return []
    for entity_type, fields in REFERENCE_FIELDS.items():
        akey, lkey, idf = ENTITY_REGISTRY[entity_type]
        for r in _as_list((candidate.get(akey) or {}).get(lkey)):
            if not isinstance(r, dict):
                continue
            for field, _target in fields:
                if field in r:
                    r[field] = _rewrite_list(r.get(field), mapping)

    # derivation steps: rewrite equation id lists.
    for chain in _as_list((candidate.get("derivation_chain") or {}).get("chains")):
        if not isinstance(chain, dict):
            continue
        for step in _as_list(chain.get("steps")):
            if not isinstance(step, dict):
                continue
            for field in ("input_equation_ids", "output_equation_ids", "inputs", "outputs"):
                if field in step:
                    step[field] = _rewrite_list(step.get(field), mapping)

    # graph edges: endpoints + evidence ids. Drop edges whose endpoint was removed.
    dropped: list[str] = []
    cg = candidate.get("component_graph") or {}
    edges = _as_list(cg.get("edges"))
    kept: list[dict] = []
    for e in edges:
        if not isinstance(e, dict):
            kept.append(e)
            continue
        drop = False
        for key in ("source", "target", "source_component_id", "target_component_id", "from", "to"):
            if key in e and e[key] is not None:
                sv = str(e[key])
                if sv in mapping:
                    repl = mapping[sv]
                    if not repl:
                        drop = True
                    else:
                        e[key] = repl[0]
        for key in ("evidence_equation_ids", "evidence_claim_ids", "evidence_derivation_ids"):
            if key in e:
                e[key] = _rewrite_list(e.get(key), mapping)
        if drop:
            dropped.append(str(e.get("edge_id") or ""))
        else:
            kept.append(e)
    if isinstance(cg, dict):
        cg["edges"] = kept
    return dropped


# ---------------------------------------------------------------------------
# Per-operation application
# ---------------------------------------------------------------------------

class _OpError(Exception):
    pass


def _op_add_entity(candidate, op, mapping):
    after = op.get("after_json")
    if not isinstance(after, dict):
        raise _OpError("add_entity requires an after_json object")
    _, _, idf = ENTITY_REGISTRY[op["target_type"]]
    new_id = _record_id(after, idf) or op.get("target_id")
    if not new_id:
        raise _OpError("add_entity requires an id in after_json/target_id")
    lst = _ensure_container(candidate, op["target_type"])
    if _find_record(lst, idf, new_id) is not None:
        raise _OpError(f"add_entity id already exists: {new_id}")
    if idf and idf not in after:
        after = {**after, idf: new_id}
    lst.append(after)


def _op_update_entity(candidate, op, mapping):
    _, _, idf = ENTITY_REGISTRY[op["target_type"]]
    lst = _ensure_container(candidate, op["target_type"])
    rec = _find_record(lst, idf, op["target_id"])
    if rec is None:
        raise _OpError(f"update_entity target not found: {op['target_id']}")
    after = op.get("after_json")
    if not isinstance(after, dict):
        raise _OpError("update_entity requires an after_json object")
    rec.update(after)


def _op_remove_entity(candidate, op, mapping):
    _, _, idf = ENTITY_REGISTRY[op["target_type"]]
    lst = _ensure_container(candidate, op["target_type"])
    rec = _find_record(lst, idf, op["target_id"])
    if rec is None:
        raise _OpError(f"remove_entity target not found: {op['target_id']}")
    lst.remove(rec)
    mapping[op["target_id"]] = []  # references to it are dropped


def _op_split_entity(candidate, op, mapping):
    _, _, idf = ENTITY_REGISTRY[op["target_type"]]
    lst = _ensure_container(candidate, op["target_type"])
    rec = _find_record(lst, idf, op["target_id"])
    if rec is None:
        raise _OpError(f"split_entity target not found: {op['target_id']}")
    parts = op.get("after_json")
    if not isinstance(parts, list) or not parts:
        raise _OpError("split_entity requires a non-empty after_json list")
    new_ids: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise _OpError("split_entity parts must be objects")
        nid = _record_id(part, idf)
        if not nid:
            raise _OpError("split_entity part missing id")
        new_ids.append(nid)
    lst.remove(rec)
    lst.extend(parts)
    mapping[op["target_id"]] = new_ids


def _op_merge_entities(candidate, op, mapping):
    _, _, idf = ENTITY_REGISTRY[op["target_type"]]
    lst = _ensure_container(candidate, op["target_type"])
    sources = op.get("source_ids") or op.get("target_ids") or []
    if op.get("target_id") and op["target_id"] not in sources:
        sources = [op["target_id"], *sources]
    sources = [str(s) for s in sources if s]
    if len(sources) < 2:
        raise _OpError("merge_entities requires >= 2 source ids")
    merged = op.get("after_json")
    if not isinstance(merged, dict):
        raise _OpError("merge_entities requires an after_json object")
    merged_id = _record_id(merged, idf)
    if not merged_id:
        raise _OpError("merge_entities after_json missing id")
    removed_any = False
    for sid in sources:
        rec = _find_record(lst, idf, sid)
        if rec is not None:
            lst.remove(rec)
            removed_any = True
    if not removed_any:
        raise _OpError("merge_entities found no source records")
    lst.append(merged)
    for sid in sources:
        mapping[sid] = [merged_id]


def _op_relink_evidence(candidate, op, mapping):
    _, _, idf = ENTITY_REGISTRY[op["target_type"]]
    lst = _ensure_container(candidate, op["target_type"])
    rec = _find_record(lst, idf, op["target_id"])
    if rec is None:
        raise _OpError(f"relink_evidence target not found: {op['target_id']}")
    new_refs = [str(v) for v in (op.get("evidence_refs") or op.get("after_json") or []) if v]
    field = "source_evidence_ids" if op["target_type"] in ("claim", "equation") else "linked_evidence_ids"
    rec[field] = new_refs


def _relation_field(op) -> tuple[str, str]:
    """Return (link_field, target_type) for a relation op on an entity."""
    field = op.get("relation_field")
    target_type = op.get("relation_target_type") or "evidence"
    if field:
        return field, target_type
    # default mapping by target type
    default = {
        "evidence": ("source_evidence_ids", "evidence"),
        "equation": ("equation_ids", "equation"),
        "component": ("linked_component_ids", "component"),
        "claim": ("linked_claim_ids", "claim"),
        "derivation": ("derivation_ids", "derivation"),
    }
    return default.get(target_type, ("source_evidence_ids", "evidence"))


def _op_add_relation(candidate, op, mapping):
    # Graph edge relation.
    if op["target_type"] == "graph_edge":
        edges = _ensure_container(candidate, "graph_edge")
        after = op.get("after_json")
        if not isinstance(after, dict):
            raise _OpError("add_relation (graph_edge) requires after_json object")
        edges.append(after)
        return
    # Entity link relation.
    _, _, idf = ENTITY_REGISTRY[op["target_type"]]
    lst = _ensure_container(candidate, op["target_type"])
    rec = _find_record(lst, idf, op["target_id"])
    if rec is None:
        raise _OpError(f"add_relation source not found: {op['target_id']}")
    field, _tt = _relation_field(op)
    rel_target = op.get("relation_target_id")
    if not rel_target:
        raise _OpError("add_relation requires relation_target_id")
    rec.setdefault(field, [])
    if rel_target not in rec[field]:
        rec[field].append(rel_target)


def _op_remove_relation(candidate, op, mapping):
    if op["target_type"] == "graph_edge":
        cg = candidate.setdefault("component_graph", {})
        edges = _as_list(cg.get("edges"))
        eid = op.get("target_id")
        cg["edges"] = [e for e in edges if not (isinstance(e, dict) and str(e.get("edge_id")) == str(eid))]
        return
    _, _, idf = ENTITY_REGISTRY[op["target_type"]]
    lst = _ensure_container(candidate, op["target_type"])
    rec = _find_record(lst, idf, op["target_id"])
    if rec is None:
        raise _OpError(f"remove_relation source not found: {op['target_id']}")
    field, _tt = _relation_field(op)
    rel_target = op.get("relation_target_id")
    if field in rec and rel_target in rec[field]:
        rec[field] = [v for v in rec[field] if v != rel_target]


_DISPATCH = {
    "add_entity": _op_add_entity,
    "update_entity": _op_update_entity,
    "remove_entity": _op_remove_entity,
    "split_entity": _op_split_entity,
    "merge_entities": _op_merge_entities,
    "relink_evidence": _op_relink_evidence,
    "add_relation": _op_add_relation,
    "remove_relation": _op_remove_relation,
}


# ---------------------------------------------------------------------------
# Integrity check
# ---------------------------------------------------------------------------

def _base_unresolved_keys(base_inventory: dict | None) -> set[tuple]:
    keys: set[tuple] = set()
    for u in (base_inventory or {}).get("unresolved_references", []) or []:
        keys.add((u.get("source_id"), u.get("ref_field"), u.get("target_id")))
    return keys


def _candidate_dangling(candidate: dict) -> list[dict]:
    ids = _collect_ids(candidate)
    dangling: list[dict] = []
    for entity_type, fields in REFERENCE_FIELDS.items():
        akey, lkey, idf = ENTITY_REGISTRY[entity_type]
        for r in _as_list((candidate.get(akey) or {}).get(lkey)):
            if not isinstance(r, dict):
                continue
            sid = _record_id(r, idf)
            for field, target_type in fields:
                for tid in _as_list(r.get(field)):
                    if str(tid) not in ids.get(target_type, set()):
                        dangling.append({"source_id": sid, "ref_field": field, "target_id": str(tid)})
    # graph edge endpoints
    node_ids = ids.get("graph_node", set())
    for e in _as_list((candidate.get("component_graph") or {}).get("edges")):
        if not isinstance(e, dict):
            continue
        eid = str(e.get("edge_id") or "")
        for role in ("source", "target"):
            v = e.get(role) or e.get(f"{role}_component_id")
            if v and str(v) not in node_ids:
                dangling.append({"source_id": eid, "ref_field": role, "target_id": str(v)})
    return dangling


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def _canonical_order(operations: list[dict]) -> list[tuple[int, dict]]:
    indexed = list(enumerate(operations))
    return sorted(
        indexed,
        key=lambda pair: (_PHASE.get(pair[1].get("operation"), 9),
                          str(pair[1].get("operation_id") or ""),
                          pair[0]),
    )


def apply_operations(
    base_artifacts: dict,
    operations: list[dict],
    *,
    base_inventory: dict | None = None,
) -> dict:
    """Apply revision operations to immutable base artifacts → candidate set."""
    candidate = copy.deepcopy(base_artifacts) if isinstance(base_artifacts, dict) else {}
    id_mapping: dict[str, list[str]] = {}
    applied: list[dict] = []
    errors: list[dict] = []
    protected_changes: list[dict] = []
    dropped_edges: list[str] = []

    for _idx, op in _canonical_order(operations or []):
        op = copy.deepcopy(op)
        handler = _DISPATCH.get(op.get("operation"))
        if handler is None:
            op["validation_result"] = {"status": "failed", "error": f"unknown operation {op.get('operation')}"}
            errors.append({"operation_id": op.get("operation_id"), "error": op["validation_result"]["error"]})
            applied.append(op)
            continue
        try:
            handler(candidate, op, id_mapping)
            op["validation_result"] = {"status": "applied"}
            if op.get("protected_target"):
                protected_changes.append({
                    "operation_id": op.get("operation_id"),
                    "operation": op.get("operation"),
                    "target_type": op.get("target_type"),
                    "target_id": op.get("target_id"),
                })
        except _OpError as exc:
            op["validation_result"] = {"status": "failed", "error": str(exc)}
            errors.append({"operation_id": op.get("operation_id"), "error": str(exc)})
        applied.append(op)

    # Follow id changes from split/merge/remove across all references.
    dropped_edges = _apply_id_mapping(candidate, id_mapping)

    # Integrity: any NEW dangling reference (not pre-existing in base) is fatal.
    base_keys = _base_unresolved_keys(base_inventory)
    candidate_dangling = _candidate_dangling(candidate)
    new_dangling = [
        d for d in candidate_dangling
        if (d["source_id"], d["ref_field"], d["target_id"]) not in base_keys
    ]
    if new_dangling:
        errors.append({"error": "new_unknown_references", "details": new_dangling})

    invalid = bool(errors)
    return {
        "candidate_artifacts": candidate,
        "id_mapping": {k: v for k, v in id_mapping.items()},
        "applied_operations": applied,
        "operation_errors": errors,
        "protected_changes": protected_changes,
        "dropped_edges": dropped_edges,
        "carried_unresolved_references": [
            d for d in candidate_dangling
            if (d["source_id"], d["ref_field"], d["target_id"]) in base_keys
        ],
        "invalid": invalid,
        "requires_confirmation": bool(protected_changes),
    }
