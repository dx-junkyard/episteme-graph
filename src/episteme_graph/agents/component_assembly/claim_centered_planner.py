"""Claim-centered component planning for component assembly inputs."""
from __future__ import annotations

from dataclasses import dataclass, field


SUPPORT_ROLES = {
    "theory_base",
    "observable_bridge",
    "derivation_core",
    "result",
    "application",
    "limitation",
}

SUPPORT_KINDS = {
    "direct",
    "indirect",
    "prerequisite",
    "derivational",
    "application",
    "caveat",
}

SUPPORT_LAYERS = [
    {
        "layer": "theory_base",
        "role": "Explain the theoretical basis and where model differences enter.",
        "support_kind": "prerequisite",
        "support_distance_to_headline_claim": 2,
    },
    {
        "layer": "observable_bridge",
        "role": "Map theory quantities into observable quantities.",
        "support_kind": "indirect",
        "support_distance_to_headline_claim": 1,
    },
    {
        "layer": "derivation_core",
        "role": "Eliminate unknown bias parameters or derive the core relation.",
        "support_kind": "derivational",
        "support_distance_to_headline_claim": 0,
    },
    {
        "layer": "result_and_application",
        "role": "State bias-free consistency relations, constraints, or applications.",
        "support_kind": "direct",
        "support_distance_to_headline_claim": 0,
    },
]


@dataclass
class ClaimCenteredPlan:
    headline_claim: str = ""
    headline_claim_ids: list[str] = field(default_factory=list)
    support_layers: list[dict] = field(default_factory=lambda: [dict(layer) for layer in SUPPORT_LAYERS])

    def to_dict(self) -> dict:
        return {
            "headline_claim": self.headline_claim,
            "headline_claim_ids": list(self.headline_claim_ids),
            "support_layers": [dict(layer) for layer in self.support_layers],
        }


class ClaimCenteredComponentPlanner:
    """Build a deterministic claim-centered plan before component assembly."""

    def build(self, *, accepted_claims: list[dict], thesis_nodes: list[dict]) -> ClaimCenteredPlan:
        headline_node = _headline_thesis_node(thesis_nodes)
        headline_claim = str(headline_node.get("text") or "").strip()
        headline_claim_ids = _ordered_unique(headline_node.get("claim_ids") or [])

        if not headline_claim:
            claim = _best_headline_claim(accepted_claims)
            headline_claim = str(claim.get("text") or "").strip()
            headline_claim_ids = _ordered_unique([claim.get("claim_id")] if claim.get("claim_id") else [])

        return ClaimCenteredPlan(
            headline_claim=headline_claim,
            headline_claim_ids=headline_claim_ids,
            support_layers=[dict(layer) for layer in SUPPORT_LAYERS],
        )


def infer_support_metadata(component, plan: dict | None) -> dict:
    """Infer support fields from responsibility, operation, text, and linked claims."""
    plan = plan or {}
    headline_ids = _ordered_unique(plan.get("headline_claim_ids") or [])
    existing_claims = _ordered_unique(
        list(getattr(component, "supports_claim_ids", []) or [])
        + list(getattr(component, "linked_claim_ids", []) or [])
        + list((getattr(component, "evidence_refs", {}) or {}).get("claim_ids") or [])
    )
    role = _normalize_support_role(getattr(component, "support_role", "") or _infer_support_role(component))
    kind = _normalize_support_kind(getattr(component, "support_kind", "") or _support_kind_for_role(role, component))
    distance = getattr(component, "support_distance_to_headline_claim", 0)
    try:
        distance = int(distance)
    except (TypeError, ValueError):
        distance = _distance_for_role(role)
    if not getattr(component, "supports_claim_ids", []) and not existing_claims and headline_ids and role in {"derivation_core", "result"}:
        existing_claims = headline_ids
    if headline_ids and set(existing_claims) & set(headline_ids):
        distance = min(distance, 0 if kind in {"direct", "derivational"} else 1)
    return {
        "support_role": role,
        "support_kind": kind,
        "supports_claim_ids": existing_claims,
        "support_distance_to_headline_claim": max(distance, 0),
    }


def _headline_thesis_node(thesis_nodes: list[dict]) -> dict:
    for node in thesis_nodes or []:
        if str(node.get("kind") or "") == "central_thesis":
            return node
    return {}


def _best_headline_claim(accepted_claims: list[dict]) -> dict:
    if not accepted_claims:
        return {}
    def score(claim: dict) -> tuple[int, float]:
        tier = str(claim.get("claim_tier") or "").lower()
        role_labels = " ".join(str(v).lower() for v in claim.get("role_labels") or [])
        tier_score = 2 if "central" in tier or "main" in tier else 1 if "result" in tier else 0
        role_score = 1 if any(k in role_labels for k in ("main", "result", "conclusion")) else 0
        return (tier_score + role_score, float(claim.get("confidence") or 0.0))
    return sorted(accepted_claims, key=score, reverse=True)[0]


def _infer_support_role(component) -> str:
    text = _component_text(component)
    responsibility = str(getattr(component, "responsibility_type", "") or "").lower()
    operation = str(getattr(component, "operation", "") or getattr(component, "primary_operation", "") or "").lower()
    if responsibility == "limitation" or any(k in text for k in ("limitation", "caveat", "invalid")):
        return "limitation"
    if responsibility == "application" or any(k in operation + " " + text for k in ("forecast", "diagnos", "application")):
        return "application"
    if responsibility == "constraint" or any(k in text for k in ("consistency relation", "constraint", "final relation", "result")):
        return "result"
    if responsibility == "derivation" or any(k in operation + " " + text for k in ("eliminat", "derive", "solve")):
        return "derivation_core"
    if responsibility in {"observation_model", "observable_basis", "equation_system"}:
        return "observable_bridge"
    return "theory_base"


def _support_kind_for_role(role: str, component) -> str:
    if role == "derivation_core":
        return "derivational"
    if role == "result":
        return "direct"
    if role == "application":
        return "application"
    if role == "limitation":
        return "caveat"
    if role == "theory_base":
        return "prerequisite"
    return "indirect"


def _distance_for_role(role: str) -> int:
    return {
        "derivation_core": 0,
        "result": 0,
        "application": 1,
        "limitation": 1,
        "observable_bridge": 1,
        "theory_base": 2,
    }.get(role, 1)


def _normalize_support_role(value: object) -> str:
    raw = str(value or "").strip()
    if raw == "main_result":
        raw = "result"
    if raw == "background":
        raw = "theory_base"
    return raw if raw in SUPPORT_ROLES else "theory_base"


def _normalize_support_kind(value: object) -> str:
    raw = str(value or "").strip()
    return raw if raw in SUPPORT_KINDS else "indirect"


def _component_text(component) -> str:
    return " ".join(
        str(v or "")
        for v in (
            getattr(component, "label", ""),
            getattr(component, "summary", ""),
            getattr(component, "reason", ""),
            getattr(component, "operation", ""),
            getattr(component, "primary_operation", ""),
        )
    ).lower()


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text = str(value or "")
        if text and text not in result:
            result.append(text)
    return result
