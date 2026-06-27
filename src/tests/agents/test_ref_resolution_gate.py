"""Issue #443 at the ExportValidationGate: set-membership claim-ref resolution
(form-independent) and FIELD_ALIAS_DUPLICATE detection.
"""
from __future__ import annotations

import importlib.util
import os
import sys

_GATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../../backend/core/document_pipeline/export_validation_gate.py",
)
_spec = importlib.util.spec_from_file_location("export_validation_gate_refres", _GATE_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["export_validation_gate_refres"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

ExportValidationGate = _mod.ExportValidationGate


class _Claim:
    def __init__(self, claim_id):
        self.claim_id = claim_id


class _ClaimObjects:
    def __init__(self, claims):
        self.claims = claims


def _make_artifacts(**kwargs):
    base = {
        "document_structure": {"blocks": [], "sections": []},
        "source_chunking": [{"text": "c"}],
        "claim_qualification": {"qualified_spans": []},
        "component_assembly": {"components": [], "validation_issues": []},
        "equation_semantics": {"equations": []},
    }
    base.update(kwargs)
    return base


def _run(artifacts, claim_objects=None):
    return ExportValidationGate().run(
        artifacts=artifacts, component_result=None, course_mapping=None,
        claim_objects=claim_objects, evidence=None, dsl=None,
    )


# --------------------------------------------------------------------------- #
# Set-membership claim-ref resolution (no name patterns)
# --------------------------------------------------------------------------- #

def test_scope_prefixed_ref_resolves_to_bare_claim_id():
    # A scope-prefixed referrer must resolve to the bare canonical claim id and
    # therefore NOT be flagged (issue #443: zero false positives).
    artifacts = _make_artifacts(
        equation_semantics={
            "equations": [{"equation_id": "eq_1", "linked_claim_ids": ["docA:claim_x"]}],
            "validation_issues": [],
        },
    )
    result = _run(artifacts, claim_objects=_ClaimObjects([_Claim("claim_x")]))
    assert all(
        w.code != "UNRESOLVED_CLAIM_REF_IN_ARTIFACT" for w in result.warnings
    )


def test_atypical_but_legitimate_id_not_flagged():
    # ``claim_span_5`` historically matched a "legacy" name pattern, but it is a
    # real claim here → must not be flagged.
    artifacts = _make_artifacts(
        equation_semantics={
            "equations": [{"equation_id": "eq_1", "linked_claim_ids": ["claim_span_5"]}],
            "validation_issues": [],
        },
    )
    result = _run(artifacts, claim_objects=_ClaimObjects([_Claim("claim_span_5")]))
    assert all(
        w.code != "UNRESOLVED_CLAIM_REF_IN_ARTIFACT" for w in result.warnings
    )
    # And the removed name-pattern code never appears.
    assert all(w.code != "PROVISIONAL_CLAIM_REF_IN_ARTIFACT" for w in result.warnings)


def test_genuinely_missing_ref_is_flagged_by_membership():
    artifacts = _make_artifacts(
        equation_semantics={
            "equations": [{"equation_id": "eq_1", "linked_claim_ids": ["claim_ghost"]}],
            "validation_issues": [],
        },
    )
    result = _run(artifacts, claim_objects=_ClaimObjects([_Claim("claim_real")]))
    assert any(
        w.code == "UNRESOLVED_CLAIM_REF_IN_ARTIFACT" for w in result.warnings
    )


# --------------------------------------------------------------------------- #
# FIELD_ALIAS_DUPLICATE
# --------------------------------------------------------------------------- #

def test_conflicting_alias_fields_flagged():
    artifacts = _make_artifacts(
        component_assembly={
            "components": [{
                "component_id": "comp_1",
                "responsibility_type": "derivation",
                "type": "definition",  # conflicts with responsibility_type
            }],
            "validation_issues": [],
        },
    )
    result = _run(artifacts)
    assert any(w.code == "FIELD_ALIAS_DUPLICATE" for w in result.warnings)


def test_mirrored_alias_fields_not_flagged():
    artifacts = _make_artifacts(
        component_assembly={
            "components": [{
                "component_id": "comp_1",
                "responsibility_type": "derivation",
                "type": "derivation",
                "name": "Closure relation",
                "label": "Closure relation",
            }],
            "validation_issues": [],
        },
    )
    result = _run(artifacts)
    assert all(w.code != "FIELD_ALIAS_DUPLICATE" for w in result.warnings)
