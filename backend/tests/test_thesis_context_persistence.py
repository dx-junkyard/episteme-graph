"""Tests for Phase 1 of the hierarchical context explanation design.

docs/features/hierarchical_context_explanation_design.md §4:
  - ``theory_components.thesis_context`` transcribes ComponentRecord's
    role_in_thesis / supports_thesis_node_ids / support_role /
    support_distance_to_headline_claim (previously artifact-only).
  - ``theory_claims.thesis_refs`` is a deterministic, non-LLM reverse lookup
    (thesis_reconstruction's central_thesis.claim_ids / support_structure[]
    .claim_ids -> the persisted claim row that originated the referenced
    span), so a claim can be traced back up to the thesis nodes it backs.

No LLM, no agent (src/episteme_graph/agents/) touched. All coverage here is
pure-logic (reverse-lookup helpers) or DB-shape (captured INSERT params via a
fake session), mirroring the existing ``persist_components`` test style in
``backend/tests/test_document_pipeline.py``.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline import persistence  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _thesis_result(central_claim_ids=None, support_structure=None, central_text="The central result."):
    return types.SimpleNamespace(
        central_thesis={
            "text": central_text,
            "claim_ids": list(central_claim_ids or []),
        },
        support_structure=dict(support_structure or {}),
    )


def _qualified_span(
    span_id="s1",
    block_id="b1",
    section_id="sec1",
    text="claim text",
    decision="accepted",
):
    return types.SimpleNamespace(
        span_id=span_id,
        block_id=block_id,
        section_id=section_id,
        text=text,
        reason="",
        confidence=0.9,
        qualification={"decision": decision, "claim_type_candidate": "result"},
    )


class _FakeRow:
    def __init__(self, value):
        self._value = value

    def __getitem__(self, idx):
        return self._value


class _FakeExec:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeClaimsSession:
    """Captures every INSERT's params, assigning incrementing fake UUIDs.

    The DELETE-by-document_id statement (params == {"doc_id": ...}) is not an
    INSERT so it is not captured and returns a row-less exec.
    """

    def __init__(self):
        self.inserted: list[dict] = []
        self._counter = 0

    def execute(self, _stmt, params=None):
        params = params or {}
        if "claim_type" in params:
            self._counter += 1
            uid = f"claim-uuid-{self._counter}"
            self.inserted.append(dict(params))
            return _FakeExec(_FakeRow(uid))
        return _FakeExec(_FakeRow(None))

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _FakeComponentsSession:
    def __init__(self):
        self.inserted: list[dict] = []
        self._counter = 0

    def execute(self, _stmt, params=None):
        params = params or {}
        if "maturity_source" in params:
            self._counter += 1
            uid = f"component-uuid-{self._counter}"
            self.inserted.append(dict(params))
            return _FakeExec(_FakeRow(uid))
        return _FakeExec(_FakeRow(None))

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _apparatus_like_component(component_id="comp_1", label="widget", **overrides):
    base = dict(
        component_id=component_id,
        component_type="theory",
        label=label,
        summary="a component",
        evidence_refs={"claim_ids": []},
        inputs=[], outputs=[], preconditions=[], cautions=[],
        dependencies=[], internal_flow=[],
        maturity_source="llm_proposed",
        fallback_reason="",
        review_status="teacher_review_required",
        source_scope={},
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# pure-logic: _thesis_ref_nodes / _claim_thesis_ref_index / _truncate_excerpt
# ---------------------------------------------------------------------------


def test_thesis_ref_nodes_none_result_returns_empty():
    assert persistence._thesis_ref_nodes(None) == []


def test_thesis_ref_nodes_builds_central_and_support_entries():
    thesis = _thesis_result(
        central_claim_ids=["claim_abc"],
        support_structure={
            "assumptions": [
                {"text": "an assumption", "claim_ids": ["claim_xyz"]},
            ],
            "direct_supports": [],
        },
    )
    nodes = persistence._thesis_ref_nodes(thesis)

    central = next(n for n in nodes if n["thesis_ref"] == "central_thesis")
    assert central["kind"] == "central_thesis"
    assert central["claim_ids"] == ["claim_abc"]
    assert central["text"] == "The central result."

    support = next(n for n in nodes if n["thesis_ref"] == "support:assumptions:0")
    assert support["kind"] == "assumptions"
    assert support["claim_ids"] == ["claim_xyz"]
    assert support["text"] == "an assumption"

    # empty support_structure sections contribute no nodes
    assert not any(n["thesis_ref"].startswith("support:direct_supports") for n in nodes)


def test_truncate_excerpt_keeps_short_text_unchanged():
    assert persistence._truncate_excerpt("short text") == "short text"


def test_truncate_excerpt_truncates_long_text_with_ellipsis():
    long_text = "x" * 300
    excerpt = persistence._truncate_excerpt(long_text, limit=240)
    assert len(excerpt) <= 241  # 240 chars + the ellipsis char
    assert excerpt.endswith("…")


def test_claim_thesis_ref_index_dedupes_same_thesis_ref_per_claim():
    thesis = _thesis_result(
        central_claim_ids=["claim_dup", "claim_dup"],
    )
    index = persistence._claim_thesis_ref_index(thesis)
    assert index["claim_dup"] == [{
        "thesis_ref": "central_thesis",
        "kind": "central_thesis",
        "text_excerpt": "The central result.",
    }]


def test_claim_thesis_ref_index_empty_for_no_thesis_result():
    assert persistence._claim_thesis_ref_index(None) == {}


# ---------------------------------------------------------------------------
# pure-logic: _claim_thesis_refs_for_span (reverse lookup by span_id)
# ---------------------------------------------------------------------------


def test_claim_thesis_refs_for_span_matches_via_canonical_claim_id_form():
    """thesis claim_ids use ClaimObjectBuilder's ``claim_{safe(span_id)}`` form.

    ``_claim_legacy_keys({"span_id": "s-1"})`` produces {"s-1", "claim_s_1"};
    the thesis artifact references the claim as "claim_s_1" (canonical form),
    so a span with span_id "s-1" must resolve it.
    """
    thesis = _thesis_result(central_claim_ids=["claim_s_1"])
    index = persistence._claim_thesis_ref_index(thesis)

    refs = persistence._claim_thesis_refs_for_span("s-1", index)
    assert refs == [{
        "thesis_ref": "central_thesis",
        "kind": "central_thesis",
        "text_excerpt": "The central result.",
    }]


def test_claim_thesis_refs_for_span_matches_via_raw_span_id():
    thesis = _thesis_result(central_claim_ids=["s-1"])
    index = persistence._claim_thesis_ref_index(thesis)
    refs = persistence._claim_thesis_refs_for_span("s-1", index)
    assert [r["thesis_ref"] for r in refs] == ["central_thesis"]


def test_claim_thesis_refs_for_span_no_match_returns_empty_no_guessing():
    """A thesis claim_id that never persists (e.g. equation-claim-synthesis'
    ``synth_claim_0001``) must not be guessed onto some unrelated span."""
    thesis = _thesis_result(central_claim_ids=["synth_claim_0001"])
    index = persistence._claim_thesis_ref_index(thesis)
    assert persistence._claim_thesis_refs_for_span("s-1", index) == []


def test_claim_thesis_refs_for_span_none_span_id_returns_empty():
    thesis = _thesis_result(central_claim_ids=["claim_s_1"])
    index = persistence._claim_thesis_ref_index(thesis)
    assert persistence._claim_thesis_refs_for_span(None, index) == []


def test_claim_thesis_refs_for_span_aggregates_multiple_sections_sorted():
    thesis = _thesis_result(
        central_claim_ids=["claim_s_1"],
        support_structure={
            "assumptions": [{"text": "assume", "claim_ids": ["claim_s_1"]}],
        },
    )
    index = persistence._claim_thesis_ref_index(thesis)
    refs = persistence._claim_thesis_refs_for_span("s-1", index)
    thesis_refs = sorted(r["thesis_ref"] for r in refs)
    assert thesis_refs == ["central_thesis", "support:assumptions:0"]


# ---------------------------------------------------------------------------
# pure-logic: _component_thesis_context
# ---------------------------------------------------------------------------


def test_component_thesis_context_none_when_all_fields_empty():
    comp = _apparatus_like_component()
    assert persistence._component_thesis_context(comp) is None


def test_component_thesis_context_dict_when_role_in_thesis_present():
    comp = _apparatus_like_component(
        role_in_thesis="Directly supports the central thesis.",
        supports_thesis_node_ids=["central_thesis"],
        support_role="result",
        support_distance_to_headline_claim=0,
    )
    ctx = persistence._component_thesis_context(comp)
    assert ctx == {
        "role_in_thesis": "Directly supports the central thesis.",
        "supports_thesis_node_ids": ["central_thesis"],
        "support_role": "result",
        "support_distance_to_headline_claim": 0,
    }


def test_component_thesis_context_dict_when_only_distance_nonzero():
    comp = _apparatus_like_component(support_distance_to_headline_claim=2)
    ctx = persistence._component_thesis_context(comp)
    assert ctx == {
        "role_in_thesis": "",
        "supports_thesis_node_ids": [],
        "support_role": "",
        "support_distance_to_headline_claim": 2,
    }


# ---------------------------------------------------------------------------
# persist_qualified_claims: thesis_refs reaches the INSERT
# ---------------------------------------------------------------------------


def _run_persist_qualified_claims(spans, thesis_result=None):
    qualified_result = types.SimpleNamespace(qualified_spans=spans)
    chunk_index = [{"chunk_id": "chunk-1", "block_ids": ["b1"]}]
    session = _FakeClaimsSession()
    with patch.object(persistence, "_pg_session", return_value=session):
        saved = persistence.persist_qualified_claims(
            document_id="doc-1",
            qualified_result=qualified_result,
            chunk_index=chunk_index,
            thesis_result=thesis_result,
        )
    return saved, session


def test_persist_qualified_claims_writes_thesis_refs_for_matching_span():
    thesis = _thesis_result(central_claim_ids=["claim_s1"], central_text="Headline claim text.")
    saved, session = _run_persist_qualified_claims(
        [_qualified_span(span_id="s1")], thesis_result=thesis
    )
    assert len(session.inserted) == 1
    params = session.inserted[0]
    assert params["thesis_refs"] is not None
    thesis_refs = json.loads(params["thesis_refs"])
    assert thesis_refs == [{
        "thesis_ref": "central_thesis",
        "kind": "central_thesis",
        "text_excerpt": "Headline claim text.",
    }]
    assert saved[0]["claim_id"] == "claim-uuid-1"


def test_persist_qualified_claims_thesis_refs_null_when_no_thesis_result():
    saved, session = _run_persist_qualified_claims(
        [_qualified_span(span_id="s1")], thesis_result=None
    )
    assert session.inserted[0]["thesis_refs"] is None


def test_persist_qualified_claims_thesis_refs_null_when_no_matching_claim():
    thesis = _thesis_result(central_claim_ids=["claim_unrelated_span"])
    _saved, session = _run_persist_qualified_claims(
        [_qualified_span(span_id="s1")], thesis_result=thesis
    )
    assert session.inserted[0]["thesis_refs"] is None


def test_persist_qualified_claims_rejected_span_is_skipped_and_not_indexed():
    thesis = _thesis_result(central_claim_ids=["claim_s1"])
    saved, session = _run_persist_qualified_claims(
        [_qualified_span(span_id="s1", decision="rejected")], thesis_result=thesis
    )
    assert saved == []
    assert session.inserted == []


def test_persist_qualified_claims_multiple_spans_only_matching_one_gets_refs():
    thesis = _thesis_result(central_claim_ids=["claim_s1"])
    saved, session = _run_persist_qualified_claims(
        [_qualified_span(span_id="s1"), _qualified_span(span_id="s2", block_id="b1")],
        thesis_result=thesis,
    )
    assert len(saved) == 2
    by_span_id = {
        json.loads(p["source_scope"])["span_id"]: p["thesis_refs"]
        for p in session.inserted
    }
    assert by_span_id["s1"] is not None
    assert by_span_id["s2"] is None


# ---------------------------------------------------------------------------
# persist_components: thesis_context reaches the INSERT
# ---------------------------------------------------------------------------


def _run_persist_components(components):
    component_result = types.SimpleNamespace(components=components)
    session = _FakeComponentsSession()
    with patch.object(persistence, "_pg_session", return_value=session):
        id_map = persistence.persist_components(
            document_id="doc-1", component_result=component_result
        )
    return id_map, session


def test_persist_components_thesis_context_included_when_present():
    comp = _apparatus_like_component(
        component_id="comp_1",
        label="derivation-core",
        role_in_thesis="Derives the core relation the thesis depends on.",
        supports_thesis_node_ids=["central_thesis", "support:derivation_core:0"],
        support_role="derivation_core",
        support_distance_to_headline_claim=1,
    )
    _id_map, session = _run_persist_components([comp])
    assert len(session.inserted) == 1
    params = session.inserted[0]
    assert params["thesis_context"] is not None
    ctx = json.loads(params["thesis_context"])
    assert ctx == {
        "role_in_thesis": "Derives the core relation the thesis depends on.",
        "supports_thesis_node_ids": ["central_thesis", "support:derivation_core:0"],
        "support_role": "derivation_core",
        "support_distance_to_headline_claim": 1,
    }


def test_persist_components_thesis_context_null_when_absent():
    comp = _apparatus_like_component(component_id="comp_2", label="plain")
    _id_map, session = _run_persist_components([comp])
    params = session.inserted[0]
    assert params["thesis_context"] is None


def test_persist_components_thesis_context_does_not_disturb_f2_source_scope_merge():
    """Adding thesis_context must not regress the F2 source_scope merge
    (figure_concept_linking_design.md): agent-side source_scope keys survive,
    only document_id/legacy_ids are overwritten.
    """
    comp = _apparatus_like_component(
        component_id="comp_apparatus_1",
        label="spectrometer",
        component_type="apparatus",
        source_scope={
            "source": "apparatus_semantics",
            "document_id": "stale-from-agent",
            "figure_id": "fig-uuid-1",
            "figure_key": "fig_5.2",
        },
        role_in_thesis="Directly supports the central thesis. Applies the thesis to a concrete case.",
        supports_thesis_node_ids=["central_thesis"],
        support_role="application",
    )
    _id_map, session = _run_persist_components([comp])
    params = session.inserted[0]
    scope = json.loads(params["source_scope"])
    assert scope["source"] == "apparatus_semantics"
    assert scope["figure_id"] == "fig-uuid-1"
    assert scope["figure_key"] == "fig_5.2"
    assert scope["document_id"] == "doc-1"
    assert scope["legacy_ids"] == ["comp_apparatus_1"]

    ctx = json.loads(params["thesis_context"])
    assert ctx["role_in_thesis"].startswith("Directly supports the central thesis.")
    assert ctx["supports_thesis_node_ids"] == ["central_thesis"]


# ---------------------------------------------------------------------------
# orchestrator wiring: persist_qualified_claims must receive ctx.thesis
# ---------------------------------------------------------------------------


def test_orchestrator_passes_thesis_result_to_persist_qualified_claims():
    orchestrator_src = (
        ROOT / "backend" / "core" / "document_pipeline" / "orchestrator.py"
    ).read_text(encoding="utf-8")
    call_start = orchestrator_src.index("saved_claims = persist_qualified_claims(")
    call_snippet = orchestrator_src[call_start:call_start + 400]
    assert "thesis_result=ctx.thesis" in call_snippet


# ---------------------------------------------------------------------------
# migration file sanity (idempotency itself is covered by
# test_migrations_runner.py's TestIdempotencyLint over all backend/db/*.sql)
# ---------------------------------------------------------------------------


def test_migration_055_adds_both_columns_idempotently():
    sql = (ROOT / "backend" / "db" / "055_thesis_context_persistence.sql").read_text(
        encoding="utf-8"
    )
    assert "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS thesis_context JSONB" in sql
    assert "ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS thesis_refs JSONB" in sql
