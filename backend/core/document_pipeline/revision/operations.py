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
    source_chunk_ids: list[str] | None = None,
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
        # Registry evidence ids and source chunk ids are kept in separate fields
        # because they live in different ID spaces (#412 P0-6).
        "evidence_refs": list(evidence_refs or []),
        "source_chunk_ids": list(source_chunk_ids or []),
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

def _ref_key(ref: dict) -> tuple:
    return (ref.get("source_id"), ref.get("ref_field"), ref.get("target_id"))


def _base_unresolved_keys(base_inventory: dict | None) -> set[tuple]:
    keys: set[tuple] = set()
    for u in (base_inventory or {}).get("unresolved_references", []) or []:
        keys.add(_ref_key(u))
    return keys


def _candidate_unresolved_references(candidate: dict) -> list[dict]:
    """Unresolved references in the candidate, via the *same* resolver as the base.

    Using ``build_baseline_inventory`` (rather than a parallel raw-field walk)
    guarantees base and candidate dangling checks share one schema / ID semantics,
    so a reference that already dangled in the base is classified as *carried*, not
    a brand-new error (#412 P1-7).
    """
    from .inventory import build_baseline_inventory
    inv = build_baseline_inventory(candidate or {}, base_run_id="")
    return list(inv.get("unresolved_references") or [])


# ---------------------------------------------------------------------------
# Protected-decision detection (server-side, #410 P1-3)
# ---------------------------------------------------------------------------

# Operations that modify an *existing* entity (everything except add_entity).
_MODIFYING_OPERATIONS = {
    "update_entity", "split_entity", "merge_entities", "remove_entity",
    "add_relation", "remove_relation", "relink_evidence",
}


def _protected_ids(base_inventory: dict | None, base_artifacts: dict) -> set[str]:
    """Build the protected-entity id set from the *server-side* source of truth.

    Uses ``baseline_inventory.protected_decisions`` when available; otherwise
    derives it from the base artifacts directly. The client-supplied
    ``protected_target`` flag is never trusted (#410 P1-3) — a client could omit
    it to slip a change past confirmation.
    """
    decisions = (base_inventory or {}).get("protected_decisions")
    if decisions is None:
        # No inventory passed — derive protection straight from base artifacts.
        from .inventory import build_baseline_inventory
        decisions = build_baseline_inventory(base_artifacts or {}, base_run_id="").get(
            "protected_decisions", []
        )
    ids: set[str] = set()
    for d in decisions or []:
        eid = d.get("entity_id") if isinstance(d, dict) else None
        if eid:
            ids.add(str(eid))
    return ids


def _op_protected_targets(op: dict, protected_ids: set[str]) -> list[str]:
    """Return the protected entity ids an operation modifies (server-determined)."""
    if op.get("operation") not in _MODIFYING_OPERATIONS:
        return []
    candidates: list[str] = []
    if op.get("target_id"):
        candidates.append(str(op["target_id"]))
    # merge: every source target must be inspected
    for sid in (op.get("source_ids") or op.get("target_ids") or []):
        if sid:
            candidates.append(str(sid))
    return [cid for cid in dict.fromkeys(candidates) if cid in protected_ids]


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


# ---------------------------------------------------------------------------
# Partial-adoption support (#415): per-operation dependency analysis so a single
# bad operation excludes only itself + its dependents, not the whole candidate.
# ---------------------------------------------------------------------------

# Operation status taxonomy (#415).
OP_APPLIED = "applied"
OP_EXCLUDED_INVALID = "excluded_invalid"
OP_EXCLUDED_DEPENDENCY = "excluded_dependency"
OP_REQUIRES_CONFIRMATION = "requires_confirmation"

# Candidate status taxonomy (#415).
CAND_ADOPTABLE = "adoptable"
CAND_PARTIALLY_ADOPTABLE = "partially_adoptable"
CAND_BLOCKED = "blocked"


def _all_base_ids(base_inventory: dict | None, base_artifacts: dict) -> set[str]:
    """Every entity id present in the base (the ids an operation may safely consume)."""
    inv = base_inventory
    if not inv or "entities" not in inv:
        from .inventory import build_baseline_inventory
        inv = build_baseline_inventory(base_artifacts or {}, base_run_id="")
    ids: set[str] = set()
    for bucket in (inv.get("entities") or {}).values():
        ids.update(str(k) for k in (bucket or {}).keys())
    return ids


def _produced_ids(op: dict) -> list[str]:
    """Entity ids an operation *creates* (so dependents can be tracked)."""
    operation = op.get("operation")
    target_type = op.get("target_type")
    _, _, idf = ENTITY_REGISTRY.get(target_type, (None, None, None))
    after = op.get("after_json")
    if operation == "add_entity" and isinstance(after, dict):
        nid = _record_id(after, idf) or str(op.get("target_id") or "")
        return [nid] if nid else []
    if operation == "split_entity" and isinstance(after, list):
        return [_record_id(p, idf) for p in after
                if isinstance(p, dict) and _record_id(p, idf)]
    if operation == "merge_entities" and isinstance(after, dict):
        mid = _record_id(after, idf)
        return [mid] if mid else []
    return []


def _after_json_ref_ids(op: dict) -> list[str]:
    """Reference-list ids embedded in an add/update after_json (e.g. equation_ids)."""
    fields = REFERENCE_FIELDS.get(op.get("target_type"), [])
    after = op.get("after_json")
    records = after if isinstance(after, list) else [after]
    out: list[str] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        for field, _t in fields:
            out.extend(str(v) for v in _as_list(rec.get(field)) if v)
    return out


def _consumed_ids(op: dict) -> list[str]:
    """Entity ids an operation *references* and therefore depends upon (#415)."""
    operation = op.get("operation")
    target_type = op.get("target_type")
    out: list[str] = []
    if operation == "merge_entities":
        for sid in (op.get("source_ids") or op.get("target_ids") or []):
            if sid:
                out.append(str(sid))
        if op.get("target_id"):
            out.append(str(op["target_id"]))
    elif operation in ("update_entity", "split_entity", "remove_entity",
                       "relink_evidence", "remove_relation", "add_relation"):
        if target_type == "graph_edge":
            after = op.get("after_json") if operation == "add_relation" else None
            if isinstance(after, dict):
                for key in ("source", "target", "source_component_id",
                            "target_component_id", "from", "to"):
                    if after.get(key):
                        out.append(str(after[key]))
            if operation == "remove_relation" and op.get("target_id"):
                out.append(str(op["target_id"]))
        else:
            if op.get("target_id"):
                out.append(str(op["target_id"]))
            if op.get("relation_target_id"):
                out.append(str(op["relation_target_id"]))
    if operation in ("add_entity", "update_entity", "split_entity"):
        out.extend(_after_json_ref_ids(op))
    return list(dict.fromkeys(out))


def _op_introduces(op: dict, dangling: dict) -> bool:
    """Whether ``op`` is responsible for a specific new dangling reference."""
    source_id = str(dangling.get("source_id") or "")
    target_id = str(dangling.get("target_id") or "")
    if source_id and source_id == str(op.get("target_id") or ""):
        return True
    if source_id and source_id in _produced_ids(op):
        return True
    if op.get("operation") in ("add_relation", "relink_evidence"):
        if target_id and (target_id == str(op.get("relation_target_id") or "")
                          or target_id in [str(v) for v in (op.get("evidence_refs") or [])]):
            return True
    if op.get("operation") == "add_relation" and op.get("target_type") == "graph_edge":
        after = op.get("after_json") or {}
        if isinstance(after, dict) and source_id == str(after.get("edge_id") or ""):
            return True
    return False


def _normalize_excluded(raw: dict) -> dict:
    """Minimal normalization of a pre-rejected op so it can still be analysed."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "operation_id": raw.get("operation_id") or "",
        "operation": raw.get("operation"),
        "target_type": raw.get("target_type"),
        "target_id": raw.get("target_id"),
        "after_json": raw.get("after_json"),
        "source_ids": raw.get("source_ids") or raw.get("target_ids") or [],
        "relation_target_id": raw.get("relation_target_id"),
        "evidence_refs": raw.get("evidence_refs") or [],
    }


def _excluded_record(op: dict, status: str, reason: str) -> dict:
    return {
        "operation_id": op.get("operation_id") or "",
        "status": status,
        "reason": reason,
        "operation": op.get("operation"),
        "target_type": op.get("target_type"),
        "target_id": op.get("target_id"),
    }


def _apply_included(base_artifacts: dict, included: list[dict]):
    """Apply the included ops on a fresh copy; return (candidate, id_mapping, failure).

    ``failure`` is ``(op_index, reason)`` for the first op whose handler raised, or
    None when all applied cleanly.
    """
    candidate = copy.deepcopy(base_artifacts) if isinstance(base_artifacts, dict) else {}
    id_mapping: dict[str, list[str]] = {}
    for _idx, op in _canonical_order(included):
        handler = _DISPATCH.get(op.get("operation"))
        if handler is None:
            return candidate, id_mapping, (op["_idx"], f"unknown operation {op.get('operation')}")
        try:
            handler(candidate, op, id_mapping)
        except _OpError as exc:
            return candidate, id_mapping, (op["_idx"], str(exc))
    return candidate, id_mapping, None


def apply_operations(
    base_artifacts: dict,
    operations: list[dict],
    *,
    base_inventory: dict | None = None,
    pre_excluded: list[dict] | None = None,
) -> dict:
    """Apply revision operations, excluding only the bad ones (#415, partial adoption).

    Invalid operations (failed pre-validation, missing target, or one that would
    introduce a new dangling reference) are excluded as ``excluded_invalid``;
    operations that depend on an excluded operation's output are excluded as
    ``excluded_dependency``. The candidate is rebuilt from the surviving ops, so
    independent valid changes remain adoptable even when a sibling op is bad.

    ``pre_excluded`` carries operations already rejected upstream (typed evidence /
    checkpoint validation, #414) as ``{"op"|"raw": <op>, "reason": str}`` so their
    dependents are excluded too.
    """
    base_artifacts = base_artifacts or {}
    ops = [copy.deepcopy(o) for o in (operations or [])]
    for i, o in enumerate(ops):
        o["_idx"] = i

    # Pre-rejected ops keep their reason and still register produced ids so their
    # dependents are detected, but are never applied.
    extra_excluded: list[dict] = []
    for j, rej in enumerate(pre_excluded or []):
        xo = _normalize_excluded(rej.get("op") or rej.get("raw") or rej)
        xo["_idx"] = len(ops) + j
        xo["_pre_reason"] = rej.get("reason") or "invalid_operation"
        extra_excluded.append(xo)

    all_ops = {o["_idx"]: o for o in ops}
    all_ops.update({xo["_idx"]: xo for xo in extra_excluded})

    base_ids = _all_base_ids(base_inventory, base_artifacts)
    produced_by: dict[str, list[int]] = {}
    for o in all_ops.values():
        for pid in _produced_ids(o):
            produced_by.setdefault(pid, []).append(o["_idx"])

    # excluded: _idx -> {"status", "reason"}
    excluded: dict[int, dict] = {
        xo["_idx"]: {"status": OP_EXCLUDED_INVALID, "reason": xo["_pre_reason"]}
        for xo in extra_excluded
    }
    protected_ids = _protected_ids(base_inventory, base_artifacts)

    def _propagate() -> None:
        again = True
        while again:
            again = False
            for o in ops:
                idx = o["_idx"]
                if idx in excluded:
                    continue
                for cid in _consumed_ids(o):
                    if cid in base_ids:
                        continue
                    producers = produced_by.get(cid, [])
                    if not producers:
                        excluded[idx] = {"status": OP_EXCLUDED_INVALID,
                                         "reason": f"unknown_reference:{cid}"}
                        again = True
                        break
                    if all(p in excluded for p in producers):
                        excluded[idx] = {"status": OP_EXCLUDED_DEPENDENCY,
                                         "reason": f"depends_on_excluded:{cid}"}
                        again = True
                        break

    blocked_dangling: list[dict] = []
    candidate: dict = {}
    id_mapping: dict[str, list[str]] = {}
    dropped_edges: list[str] = []
    carried: list[dict] = []
    # Iterate: exclude dependents, apply, exclude handler failures / new danglers,
    # repeat until the surviving set applies cleanly. Bounded — each pass excludes
    # at least one op.
    for _guard in range(len(ops) + 2):
        _propagate()
        included = [o for o in ops if o["_idx"] not in excluded]
        candidate, id_mapping, failure = _apply_included(base_artifacts, included)
        if failure is not None:
            excluded[failure[0]] = {"status": OP_EXCLUDED_INVALID, "reason": failure[1]}
            continue
        dropped_edges = _apply_id_mapping(candidate, id_mapping)
        if base_inventory is not None:
            base_keys = _base_unresolved_keys(base_inventory)
        else:
            from .inventory import build_baseline_inventory
            base_keys = _base_unresolved_keys(
                build_baseline_inventory(base_artifacts or {}, base_run_id="")
            )
        candidate_unresolved = _candidate_unresolved_references(candidate)
        new_dangling = [d for d in candidate_unresolved if _ref_key(d) not in base_keys]
        carried = [d for d in candidate_unresolved if _ref_key(d) in base_keys]
        if new_dangling:
            culprits = {o["_idx"] for o in included
                        for d in new_dangling if _op_introduces(o, d)}
            if culprits:
                for idx in culprits:
                    excluded[idx] = {"status": OP_EXCLUDED_INVALID,
                                     "reason": "introduces_unknown_reference"}
                continue
            blocked_dangling = new_dangling  # unattributable → candidate is blocked
        break

    # Build the final per-operation records, protected-change flags, and summary.
    included_idx = {o["_idx"] for o in ops if o["_idx"] not in excluded}
    protected_changes: list[dict] = []
    applied_operations: list[dict] = []
    excluded_operations: list[dict] = []
    counts = {OP_APPLIED: 0, OP_EXCLUDED_INVALID: 0,
              OP_EXCLUDED_DEPENDENCY: 0, OP_REQUIRES_CONFIRMATION: 0}

    for o in sorted(all_ops.values(), key=lambda x: x["_idx"]):
        idx = o["_idx"]
        record = {k: v for k, v in o.items() if not k.startswith("_")}
        if idx in excluded:
            status = excluded[idx]["status"]
            reason = excluded[idx]["reason"]
            record["validation_result"] = {"status": "failed", "error": reason}
            record["operation_status"] = status
            excluded_operations.append(_excluded_record(record, status, reason))
            counts[status] = counts.get(status, 0) + 1
        else:
            record["validation_result"] = {"status": "applied"}
            server_protected = _op_protected_targets(o, protected_ids)
            record["protected_target"] = bool(server_protected)
            record["protected_target_source"] = "server"
            if server_protected:
                record["operation_status"] = OP_REQUIRES_CONFIRMATION
                counts[OP_REQUIRES_CONFIRMATION] += 1
                protected_changes.append({
                    "operation_id": o.get("operation_id"),
                    "operation": o.get("operation"),
                    "target_type": o.get("target_type"),
                    "target_id": o.get("target_id"),
                    "protected_entity_ids": server_protected,
                })
            else:
                record["operation_status"] = OP_APPLIED
            counts[OP_APPLIED] += 1
        applied_operations.append(record)

    operation_summary = {
        "applied_count": counts[OP_APPLIED],
        "excluded_invalid_count": counts[OP_EXCLUDED_INVALID],
        "excluded_dependency_count": counts[OP_EXCLUDED_DEPENDENCY],
        "requires_confirmation_count": counts[OP_REQUIRES_CONFIRMATION],
    }
    total_excluded = counts[OP_EXCLUDED_INVALID] + counts[OP_EXCLUDED_DEPENDENCY]
    if blocked_dangling or (counts[OP_APPLIED] == 0 and total_excluded > 0):
        candidate_status = CAND_BLOCKED
    elif total_excluded > 0:
        candidate_status = CAND_PARTIALLY_ADOPTABLE
    else:
        candidate_status = CAND_ADOPTABLE

    operation_errors = [
        {"operation_id": rec["operation_id"], "error": rec["reason"]}
        for rec in excluded_operations
    ]
    if blocked_dangling:
        operation_errors.append({"error": "new_unknown_references",
                                 "details": blocked_dangling})

    return {
        "candidate_artifacts": candidate,
        "id_mapping": {k: v for k, v in id_mapping.items()},
        "applied_operations": applied_operations,
        "operation_errors": operation_errors,
        "excluded_operations": excluded_operations,
        "operation_summary": operation_summary,
        "candidate_status": candidate_status,
        "protected_changes": protected_changes,
        "dropped_edges": dropped_edges,
        "carried_unresolved_references": carried,
        # ``invalid`` retains its meaning of "cannot be adopted as-is"; with partial
        # adoption that is only the fully-blocked case (#415).
        "invalid": candidate_status == CAND_BLOCKED,
        "requires_confirmation": bool(protected_changes),
    }

