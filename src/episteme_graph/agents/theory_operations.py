"""Generic theory-operation framework (issues #395 / #396 / #397).

The core pipeline must identify *generic* theoretical operations from
source-backed structure, never paper- or domain-specific patterns. A previous
implementation hard-coded branches such as ``linearize_skewness_bias_equation``
or ``eliminate_second_order_bias`` directly in the refinement / grouping logic,
which silently optimised the whole pipeline for one paper family.

This module provides a small, stable, domain-neutral two-layer model:

* ``operation_family`` — one of a fixed, broad set (CORE_OPERATION_FAMILIES).
  Always present; grouping / splitting / graph construction use only this.
* ``operation_subtype`` — optional, source-/cartridge-backed refinement. ``None``
  or ``unknown_specific_operation`` when not confidently identified, in which
  case ``review_required`` is set.

Domain-specific terminology is handled exclusively by cartridges via the
``operation_subtype_hints`` vocabulary adapter (see :func:`classify_operation`).
Core keyword matching uses only generic logical-operation verbs (define, assume,
derive, compare, …) — never observable names, parameter names, or method names.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# --- Core operation families (broad, stable, domain-neutral) ----------------
INTRODUCE_ENTITY = "introduce_entity"
DEFINE_RELATION = "define_relation"
IMPOSE_ASSUMPTION = "impose_assumption"
CONSTRUCT_MODEL = "construct_model"
TRANSFORM_REPRESENTATION = "transform_representation"
DERIVE_CONSEQUENCE = "derive_consequence"
EVALUATE_CONDITION = "evaluate_condition"
COMPARE_CASES = "compare_cases"
VALIDATE_OR_TEST = "validate_or_test"
APPLY_TO_CONTEXT = "apply_to_context"
STATE_LIMITATION = "state_limitation"
UNKNOWN_OPERATION = "unknown_specific_operation"

CORE_OPERATION_FAMILIES = [
    INTRODUCE_ENTITY,
    DEFINE_RELATION,
    IMPOSE_ASSUMPTION,
    CONSTRUCT_MODEL,
    TRANSFORM_REPRESENTATION,
    DERIVE_CONSEQUENCE,
    EVALUATE_CONDITION,
    COMPARE_CASES,
    VALIDATE_OR_TEST,
    APPLY_TO_CONTEXT,
    STATE_LIMITATION,
    UNKNOWN_OPERATION,
]

# Short, domain-neutral display labels (frontend may localise further).
FAMILY_LABELS = {
    INTRODUCE_ENTITY: "Introduce entity",
    DEFINE_RELATION: "Define relation",
    IMPOSE_ASSUMPTION: "Impose assumption",
    CONSTRUCT_MODEL: "Construct model",
    TRANSFORM_REPRESENTATION: "Transform representation",
    DERIVE_CONSEQUENCE: "Derive consequence",
    EVALUATE_CONDITION: "Evaluate condition",
    COMPARE_CASES: "Compare cases",
    VALIDATE_OR_TEST: "Validate / test",
    APPLY_TO_CONTEXT: "Apply to context",
    STATE_LIMITATION: "State limitation",
    UNKNOWN_OPERATION: "Unspecified operation",
}

# Generic logical-operation keywords ONLY. Ordered by specificity so a more
# specific verb wins over a broad one. No observable / parameter / method names.
_FAMILY_KEYWORDS: list[tuple[str, str]] = [
    # introduce
    ("introduce", INTRODUCE_ENTITY),
    ("denote", INTRODUCE_ENTITY),
    ("notation", INTRODUCE_ENTITY),
    # assumption
    ("assume", IMPOSE_ASSUMPTION),
    ("assumption", IMPOSE_ASSUMPTION),
    ("suppose", IMPOSE_ASSUMPTION),
    ("hypothes", IMPOSE_ASSUMPTION),
    ("posit", IMPOSE_ASSUMPTION),
    # model construction
    ("parameteriz", CONSTRUCT_MODEL),
    ("parametriz", CONSTRUCT_MODEL),
    ("construct", CONSTRUCT_MODEL),
    ("model", CONSTRUCT_MODEL),
    ("expansion", CONSTRUCT_MODEL),
    ("ansatz", CONSTRUCT_MODEL),
    # transform
    ("lineariz", TRANSFORM_REPRESENTATION),
    ("substitut", TRANSFORM_REPRESENTATION),
    ("transform", TRANSFORM_REPRESENTATION),
    ("change of variable", TRANSFORM_REPRESENTATION),
    ("normaliz", TRANSFORM_REPRESENTATION),
    ("rescal", TRANSFORM_REPRESENTATION),
    ("expand", TRANSFORM_REPRESENTATION),
    ("project", TRANSFORM_REPRESENTATION),
    # derive
    ("eliminat", DERIVE_CONSEQUENCE),
    ("marginaliz", DERIVE_CONSEQUENCE),
    ("solve", DERIVE_CONSEQUENCE),
    ("deriv", DERIVE_CONSEQUENCE),
    ("integrate", DERIVE_CONSEQUENCE),
    ("infer", DERIVE_CONSEQUENCE),
    ("deduce", DERIVE_CONSEQUENCE),
    ("conclude", DERIVE_CONSEQUENCE),
    ("result", DERIVE_CONSEQUENCE),
    # evaluate condition / constraint
    ("constrain", EVALUATE_CONDITION),
    ("constraint", EVALUATE_CONDITION),
    ("residual", EVALUATE_CONDITION),
    ("consistency", EVALUATE_CONDITION),
    ("criterion", EVALUATE_CONDITION),
    ("evaluate", EVALUATE_CONDITION),
    ("condition", EVALUATE_CONDITION),
    ("bound", EVALUATE_CONDITION),
    # validate / test
    ("validat", VALIDATE_OR_TEST),
    ("verif", VALIDATE_OR_TEST),
    ("test", VALIDATE_OR_TEST),
    ("benchmark", VALIDATE_OR_TEST),
    ("numerical", VALIDATE_OR_TEST),
    ("simulat", VALIDATE_OR_TEST),
    # apply
    ("forecast", APPLY_TO_CONTEXT),
    ("apply", APPLY_TO_CONTEXT),
    ("application", APPLY_TO_CONTEXT),
    ("predict", APPLY_TO_CONTEXT),
    ("diagnos", APPLY_TO_CONTEXT),
    # limitation (checked before "limit" so "limitation" is not read as a limit case)
    ("limitation", STATE_LIMITATION),
    ("caveat", STATE_LIMITATION),
    ("invalid", STATE_LIMITATION),
    ("breaks down", STATE_LIMITATION),
    # compare
    ("compar", COMPARE_CASES),
    ("contrast", COMPARE_CASES),
    ("limit", COMPARE_CASES),
    ("case", COMPARE_CASES),
    # definition (broad; checked late so "define a model" → construct, etc.)
    ("defin", DEFINE_RELATION),
    ("relation", DEFINE_RELATION),
    ("relate", DEFINE_RELATION),
    ("equation", DEFINE_RELATION),
]


@dataclass
class OperationClassification:
    operation_family: str
    operation_subtype: str | None = None
    subtype_source: str = "unknown"  # source_text | equation_semantics | cartridge | llm | unknown
    confidence: float = 0.0
    review_required: bool = False
    review_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "operation_family": self.operation_family,
            "operation_subtype": self.operation_subtype,
            "subtype_source": self.subtype_source,
            "confidence": round(self.confidence, 3),
            "review_required": self.review_required,
            "review_reasons": list(self.review_reasons),
        }


def _cartridge_subtype_hints(cartridge) -> list[dict]:
    """Pull ``operation_subtype_hints`` from a cartridge context, if present.

    The vocabulary adapter lives entirely in the cartridge (issue #397): a hint
    is ``{"when_terms_present": [...], "operation_family": "...",
    "operation_subtype": "..."}``. Core logic never hard-codes the terms.
    """
    if cartridge is None:
        return []
    ontology = getattr(cartridge, "ontology", None) or {}
    hints = ontology.get("operation_subtype_hints") if isinstance(ontology, dict) else None
    if not isinstance(hints, list):
        # Also accept a top-level attribute for adapter configs.
        hints = getattr(cartridge, "operation_subtype_hints", None)
    return [h for h in (hints or []) if isinstance(h, dict)]


def classify_operation(operation_text: str, *, cartridge=None) -> OperationClassification:
    """Classify a free-text operation into a generic family (+ optional subtype).

    Domain-specific subtypes come ONLY from the cartridge vocabulary adapter; the
    core keyword pass assigns a broad, domain-neutral family. When no generic
    keyword matches, the family is ``unknown_specific_operation`` and the result
    is flagged ``review_required`` rather than forced into a narrow pattern.
    """
    text = str(operation_text or "").lower()

    # Cartridge vocabulary adapter (issue #397): domain terms → family + subtype.
    for hint in _cartridge_subtype_hints(cartridge):
        terms = [str(t).lower() for t in (hint.get("when_terms_present") or []) if str(t)]
        if terms and all(t in text for t in terms):
            family = str(hint.get("operation_family") or "").strip()
            if family not in CORE_OPERATION_FAMILIES:
                family = UNKNOWN_OPERATION
            return OperationClassification(
                operation_family=family,
                operation_subtype=hint.get("operation_subtype"),
                subtype_source="cartridge",
                confidence=0.8,
                review_required=family == UNKNOWN_OPERATION,
                review_reasons=[] if family != UNKNOWN_OPERATION else ["operation_family_unrecognized"],
            )

    hits = 0
    family = ""
    for needle, fam in _FAMILY_KEYWORDS:
        if needle in text:
            family = fam
            hits += 1
            break
    if not family:
        return OperationClassification(
            operation_family=UNKNOWN_OPERATION,
            operation_subtype="unknown_specific_operation",
            subtype_source="unknown",
            confidence=0.0,
            review_required=True,
            review_reasons=["operation_family_unrecognized"],
        )
    return OperationClassification(
        operation_family=family,
        operation_subtype=None,
        subtype_source="source_text",
        confidence=0.6,
        review_required=False,
        review_reasons=[],
    )


def operation_family(operation_text: str, *, cartridge=None) -> str:
    """Convenience: just the broad family (used as a domain-neutral group key)."""
    return classify_operation(operation_text, cartridge=cartridge).operation_family
