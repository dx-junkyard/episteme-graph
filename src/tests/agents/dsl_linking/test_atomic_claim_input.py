"""IK-0807: accepted_claims は claim object の atomic な子を優先し、ID を一系統に揃える。"""
from episteme_graph.agents.claim_object_builder.schema import (
    ClaimObjectBuildResult,
    ClaimObjectRecord,
)
from episteme_graph.agents.claim_qualification.schema import (
    ClaimQualificationResult,
    QualifiedSpanRecord,
)
from episteme_graph.agents.dsl_linking.input_builder import DSLLinkingInputBuilder
from episteme_graph.agents.thesis_reconstruction.schema import (
    THESIS_VERSION,
    ThesisReconstructionResult,
)

BUILDER = DSLLinkingInputBuilder()


def _span(span_id, block_id, text, atomic_claims=()):
    return QualifiedSpanRecord(
        span_id=span_id,
        block_id=block_id,
        section_id="sec_3",
        text=text,
        role_labels=["derivation_step"],
        qualification={"status": "accepted", "claim_tier": "paper_supporting"},
        edit_suggestions={},
        reason="r",
        confidence=0.8,
        atomic_claims=list(atomic_claims),
    )


def _obj(claim_id, text, *, span_id, atomic=True, parent=None, subs=()):
    return ClaimObjectRecord(
        claim_id=claim_id,
        document_id="doc",
        claim_type="derivation_step",
        text=text,
        source_evidence_ids=["ev"],
        source_span_ids=[span_id],
        concepts=[],
        section_id="sec_3",
        atomicity="atomic" if atomic else "composite",
        is_atomic=atomic,
        parent_claim_id=parent,
        subclaim_ids=list(subs),
    )


def _fixture():
    # 同じ節に span_002 が 2 ブロック（tex_b44 / tex_b50）ある — span_id はブロックごと。
    spans = [
        _span(
            "span_002", "tex_b44", "Prior factorises and is separable.",
            atomic_claims=[
                {"text": "The prior factorises.", "atomicity": "atomic", "status": "accepted"},
                {"text": "The prior is separable.", "atomicity": "atomic", "status": "accepted"},
            ],
        ),
        _span(
            "span_002", "tex_b50", "The likelihood is Gaussian.",
            atomic_claims=[
                {"text": "The likelihood is Gaussian.", "atomicity": "atomic", "status": "accepted"},
            ],
        ),
    ]
    objs = [
        _obj("claim_span_002_sub01", "The prior factorises.", span_id="span_002", parent="claim_span_002"),
        _obj("claim_span_002_sub02", "The prior is separable.", span_id="span_002", parent="claim_span_002"),
        _obj(
            "claim_span_002", "Prior factorises and is separable.", span_id="span_002",
            atomic=False, subs=("claim_span_002_sub01", "claim_span_002_sub02"),
        ),
        _obj("claim_span_002_7", "The likelihood is Gaussian.", span_id="span_002"),
    ]
    qualified = ClaimQualificationResult("doc", None, spans, [], [], {})
    return qualified, ClaimObjectBuildResult("doc", None, claims=objs)


def _thesis(claim_ids):
    return ThesisReconstructionResult(
        document_id="doc",
        thesis_version=THESIS_VERSION,
        cartridge_id=None,
        central_thesis={"text": "t", "claim_ids": claim_ids, "equation_ids": [], "evidence_block_ids": [], "reason": "", "confidence": 0.9},
        alternative_theses=[],
        support_structure={},
        excluded_from_core=[],
        thesis_graph_hints=[],
        review_notes=[],
        confidence=0.9,
    )


def test_atomic_children_replace_parent_and_ids_are_claim_object_ids():
    qualified, objs = _fixture()
    rows = BUILDER.build(qualified, claim_objects=objs).accepted_claims
    ids = [r["claim_id"] for r in rows]
    assert ids == ["claim_span_002_sub01", "claim_span_002_sub02", "claim_span_002_7"]
    assert all(r["atomicity"] == "atomic" and r["is_atomic"] for r in rows)
    # span_id がブロック間で衝突しても、本文で突合して block_id を補う。
    assert [r["block_id"] for r in rows] == ["tex_b44", "tex_b44", "tex_b50"]
    assert rows[0]["parent_claim_id"] == "claim_span_002"
    assert not any(r["claim_id"].startswith("claim:") for r in rows)


def test_parent_without_atomic_children_is_kept_with_explicit_atomicity():
    spans = [_span("span_001", "tex_b9", "A and B.")]
    objs = ClaimObjectBuildResult("doc", None, claims=[
        _obj("claim_span_001", "A and B.", span_id="span_001", atomic=False),
    ])
    rows = BUILDER.build(ClaimQualificationResult("doc", None, spans, [], [], {}), claim_objects=objs).accepted_claims
    assert [r["claim_id"] for r in rows] == ["claim_span_001"]
    assert rows[0]["atomicity"] == "composite" and rows[0]["is_atomic"] is False


def test_thesis_refs_use_same_namespace_and_expand_parents():
    qualified, objs = _fixture()
    llm_input = BUILDER.build(
        qualified, claim_objects=objs,
        thesis=_thesis(["claim_span_002", "claim:tex_b50:span_002"]),
    )
    accepted = {r["claim_id"] for r in llm_input.accepted_claims}
    central = llm_input.thesis_nodes[0]["claim_ids"]
    assert central == ["claim_span_002_sub01", "claim_span_002_sub02", "claim_span_002_7"]
    assert set(central) <= accepted


def test_every_row_declares_atomicity_without_claim_objects():
    qualified, _ = _fixture()
    rows = BUILDER.build(qualified).accepted_claims
    assert [r["atomicity"] for r in rows] == ["composite", "atomic"]
