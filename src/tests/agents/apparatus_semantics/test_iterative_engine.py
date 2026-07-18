"""Tests for the iterative contextual-analysis state machine (#499).

Design doc: docs/features/contextual_figure_analysis_iterative_verification.md
(see the "実装記録（2026-07-18 実装決定事項）" section — the source of truth for
this module).

``FakeLLMClient`` dispatches by response_schema identity (the real llm_client
uses distinct schema constants per pipeline stage — see llm_client.py), which
lets each test control exactly what each stage returns regardless of how many
times a stage is called (e.g. across repair attempts).
"""
from __future__ import annotations

from collections import deque
from unittest.mock import patch

from episteme_graph.agents.apparatus_semantics.agent import ApparatusSemanticsAgent
from episteme_graph.agents.apparatus_semantics.iterative import (
    IterativeFigureAnalyzer,
    VisionBudget,
)
from episteme_graph.agents.apparatus_semantics.llm_client import (
    ALIGNMENT_RESPONSE_SCHEMA,
    HYPOTHESIS_RESPONSE_SCHEMA,
    OBSERVATION_RESPONSE_SCHEMA,
    VERIFICATION_RESPONSE_SCHEMA,
)
from episteme_graph.agents.apparatus_semantics.prompt import IterativePromptFactory
from episteme_graph.agents.apparatus_semantics.schema import (
    FigureImageInput,
    IterativeConfig,
)
from episteme_graph.agents.apparatus_semantics.validator import ApparatusSemanticsValidator

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"
_IMAGE_PAYLOADS = [{"mime_type": "image/png", "data_base64": "AAA="}]


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


class FakeLLMClient:
    """Dispatches by response_schema identity, queue-per-stage.

    When a stage's queue is empty, a stage-appropriate default (well-formed,
    validation-clean) response is returned so a test only needs to override
    the stages it cares about.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.hypothesis_queue: deque = deque()
        self.observation_queue: deque = deque()
        self.alignment_queue: deque = deque()
        self.verification_queue: deque = deque()
        self.hypothesis_exception: Exception | None = None
        self.observation_exception: Exception | None = None
        self.alignment_exception: Exception | None = None
        self.verification_exception: Exception | None = None

    def generate(self, messages, response_schema=None, images=None):
        self.calls.append({"schema": response_schema, "images": images})
        if response_schema is HYPOTHESIS_RESPONSE_SCHEMA:
            if self.hypothesis_exception:
                raise self.hypothesis_exception
            return self.hypothesis_queue.popleft() if self.hypothesis_queue else _hypothesis_raw()
        if response_schema is OBSERVATION_RESPONSE_SCHEMA:
            if self.observation_exception:
                raise self.observation_exception
            return self.observation_queue.popleft() if self.observation_queue else _observation_raw()
        if response_schema is ALIGNMENT_RESPONSE_SCHEMA:
            if self.alignment_exception:
                raise self.alignment_exception
            return self.alignment_queue.popleft() if self.alignment_queue else _alignment_raw()
        if response_schema is VERIFICATION_RESPONSE_SCHEMA:
            if self.verification_exception:
                raise self.verification_exception
            return self.verification_queue.popleft() if self.verification_queue else _verification_raw()
        raise AssertionError(f"unexpected response_schema {response_schema!r}")

    def vision_call_count(self) -> int:
        return sum(1 for c in self.calls if c["images"])

    def schema_call_count(self, schema) -> int:
        return sum(1 for c in self.calls if c["schema"] is schema)


def _figure(
    figure_id: str = "fig_1",
    inner_labels: list | None = None,
    image_bytes=_PNG_BYTES,
) -> FigureImageInput:
    return FigureImageInput(
        figure_id=figure_id,
        figure_key=figure_id,
        figure_label="Figure 1",
        caption_text="Schematic of the ion source assembly.",
        image_bytes=image_bytes,
        nearby_text=["The ion source injects particles into the drift tube."],
        inner_labels=(
            inner_labels if inner_labels is not None
            else [{"text": "ION", "bbox": [0, 0, 10, 10]}]
        ),
    )


def _analyzer(fake_llm_client: FakeLLMClient, max_iterations: int = 3) -> IterativeFigureAnalyzer:
    config = IterativeConfig(enabled=True, max_iterations=max_iterations, model_name="test-model")
    return IterativeFigureAnalyzer(
        fake_llm_client, IterativePromptFactory(), ApparatusSemanticsValidator(), config,
    )


def _run(analyzer: IterativeFigureAnalyzer, figure: FigureImageInput, budget: VisionBudget, rescan_allowance: int = 3):
    return analyzer.run_figure(
        figure=figure,
        candidates=[],
        candidate_briefs=[],
        cartridge_hints={},
        guidance=None,
        image_payloads=_IMAGE_PAYLOADS,
        budget=budget,
        rescan_allowance=rescan_allowance,
    )


def _hypothesis_raw(**overrides) -> dict:
    base = {
        "role_in_paper": "shows the apparatus",
        "overall_subject": "ion source assembly",
        "expected_elements": [{
            "element_id": "exp_1",
            "name": "ion source",
            "evidence_quote": "ion source injects particles into the drift tube",
            "expected_labels": ["ION"],
            "importance": "primary",
            "confidence": 0.6,
        }],
        "expected_relations": [],
        "expected_visual_cues": ["a labeled box"],
        "unstated_points": [],
        "falsification_conditions": [],
        "evidence_quotes": [],
        "reason": "text mentions the ion source",
        "confidence": 0.6,
    }
    base.update(overrides)
    return base


def _observation_raw(**overrides) -> dict:
    base = {
        "panels": [],
        "elements": [{
            "observation_id": "obs_1",
            "kind": "box",
            "description": "a labeled box on the left",
            "label_text": "ION",
            "region_hint": "left",
        }],
        "connections": [],
        "ocr_labels": ["ION"],
        "repeated_motifs": [],
        "unreadable_regions": [],
        "undecidable_elements": [],
        "visual_mode_guess": "functional_diagram",
        "reason": "a labeled box is visible",
        "confidence": 0.7,
    }
    base.update(overrides)
    return base


def _alignment_raw(**overrides) -> dict:
    base = {
        "suggested_mode": "functional_diagram",
        "apparatus_name_candidate": "Ion source assembly",
        "matched_library_entry_id": None,
        "match_status": "novel",
        "parts": [{
            "name": "ion source",
            "role": "injects particles",
            "label_ref": "ION",
            "evidence_quote": "ion source injects particles into the drift tube",
            "reason": "named in text and seen in image",
            "confidence": 0.8,
        }],
        "connections": [],
        "evidence_quote": "ion source injects particles into the drift tube",
        "reason": "identified from both text and image",
        "confidence": 0.75,
        "alignment_items": [{
            "item_id": "align_1",
            "item_kind": "element",
            "label": "ion source",
            "status": "supported_by_both",
            "expected_ref": "exp_1",
            "observation_refs": ["obs_1"],
            "label_ref": "ION",
            "text_evidence": "ion source injects particles into the drift tube",
            "visual_evidence": "a labeled box on the left",
            "severity": "low",
            "reason": "confirmed by both",
            "confidence": 0.8,
        }],
        "alternative_hypotheses": [],
        "verification_tasks": [],
        "review_questions": [],
    }
    base.update(overrides)
    return base


def _bad_alignment_raw_with_unsupported_part(**overrides) -> dict:
    """An alignment response whose part 'ghost part' has no visual backing —
    only a text_only alignment item. validate_alignment must flag this."""
    base = _alignment_raw(
        parts=[{
            "name": "ghost part",
            "role": "invented from text only",
            "label_ref": None,
            "evidence_quote": "",
            "reason": "mentioned in text",
            "confidence": 0.5,
        }],
        alignment_items=[{
            "item_id": "align_1",
            "item_kind": "element",
            "label": "ghost part",
            "status": "text_only",
            "text_evidence": "ion source injects particles into the drift tube",
            "severity": "medium",
            "reason": "only mentioned in text",
            "confidence": 0.5,
        }],
    )
    base.update(overrides)
    return base


def _alignment_raw_with_single_task(
    task_id: str = "task_1", question: str = "Is mystery box A visible?", align_id: str = "align_2",
) -> dict:
    return _alignment_raw(
        parts=[],
        alignment_items=[{
            "item_id": align_id,
            "item_kind": "element",
            "label": "mystery box",
            "status": "unresolved",
            "severity": "medium",
            "text_evidence": "",
            "visual_evidence": "",
            "reason": "ambiguous",
            "confidence": 0.3,
        }],
        verification_tasks=[{
            "task_id": task_id,
            "question": question,
            "target_item_ids": [align_id],
            "region_hint": "center",
            "focus_bbox_rel": None,
            "success_condition": "clarified",
            "refutation_condition": "still ambiguous",
        }],
    )


def _verification_raw(**overrides) -> dict:
    base = {
        "task_findings": [],
        "updated_alignment_items": [],
        "new_alignment_items": [],
        "new_verification_tasks": [],
        "hypothesis_updates": [],
        "record_deltas": {},
        "notes": "",
    }
    base.update(overrides)
    return base


def _verification_raw_resolves_task(
    task_id: str = "task_1", align_id: str = "align_2",
) -> dict:
    return {
        "task_findings": [{
            "task_id": task_id,
            "outcome": "resolved",
            "observation": "the detector is connected to the ion source",
            "evidence": "obs_1",
        }],
        "updated_alignment_items": [{
            "item_id": align_id,
            "item_kind": "element",
            "label": "detector",
            "status": "supported_by_both",
            "observation_refs": ["obs_1"],
            "label_ref": "ION",
            "text_evidence": "ion source injects particles into the drift tube",
            "visual_evidence": "a labeled box on the left, connected to a detector",
            "severity": "low",
            "reason": "confirmed after rescan",
            "confidence": 0.7,
        }],
        "new_alignment_items": [],
        "new_verification_tasks": [],
        "hypothesis_updates": [],
        "record_deltas": {
            "parts_to_add": [{
                "name": "detector",
                "role": "detects particles",
                "label_ref": "ION",
                "evidence_quote": "",
                "reason": "confirmed via rescan",
                "confidence": 0.7,
                "observation_refs": ["obs_1"],
            }],
            "parts_to_remove": [],
            "connections_to_add": [],
            "connections_to_remove": [],
        },
        "notes": "resolved after rescan",
    }


def _verification_raw_unresolved_with_new_task(
    resolved_task_id: str, new_task_id: str, new_question: str, align_id: str = "align_2",
) -> dict:
    return {
        "task_findings": [{
            "task_id": resolved_task_id,
            "outcome": "unresolved",
            "observation": "still unclear",
            "evidence": "",
        }],
        "updated_alignment_items": [],
        "new_alignment_items": [],
        "new_verification_tasks": [{
            "task_id": new_task_id,
            "question": new_question,
            "target_item_ids": [align_id],
            "region_hint": "center",
            "focus_bbox_rel": None,
            "success_condition": "clarified",
            "refutation_condition": "still ambiguous",
        }],
        "hypothesis_updates": [],
        "record_deltas": {},
        "notes": "",
    }


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_happy_path_converges_with_expected_call_counts():
    fake = FakeLLMClient()
    analyzer = _analyzer(fake)
    record = _run(analyzer, _figure(), VisionBudget(None))

    ia = record.iterative_analysis
    assert ia is not None
    assert ia.convergence_status == "converged"
    assert ia.llm_calls == 3
    assert ia.vision_calls == 1
    assert ia.context_hypothesis is not None
    assert ia.visual_observations is not None
    assert len(ia.alignment_items) == 1
    assert record.parts and record.parts[0].name == "ion source"


# ---------------------------------------------------------------------------
# 2. text_only elements never leak into parts (deterministic guard)
# ---------------------------------------------------------------------------


def test_text_only_part_is_stripped_by_deterministic_guard():
    fake = FakeLLMClient()
    bad = _bad_alignment_raw_with_unsupported_part()
    fake.alignment_queue.extend([bad, bad, bad])  # initial + 2 repair attempts, all invalid

    analyzer = _analyzer(fake)
    record = _run(analyzer, _figure(), VisionBudget(None))

    assert record.parts == []
    ia = record.iterative_analysis
    assert any(
        item.status == "text_only" and item.label == "ghost part" for item in ia.alignment_items
    )
    assert any("removed parts without visual support" in f for f in ia.stage_failures)


# ---------------------------------------------------------------------------
# 3. Gap-driven rescan resolves a task and converges
# ---------------------------------------------------------------------------


def test_gap_driven_rescan_resolves_task_and_converges():
    fake = FakeLLMClient()
    fake.alignment_queue.append(_alignment_raw_with_single_task())
    fake.verification_queue.append(_verification_raw_resolves_task())

    analyzer = _analyzer(fake)
    record = _run(analyzer, _figure(), VisionBudget(None))

    ia = record.iterative_analysis
    assert ia.convergence_status == "converged"
    assert ia.vision_calls == 2
    assert len(ia.verification_iterations) == 1
    assert ia.verification_iterations[0].changes
    assert any(p.name == "detector" for p in record.parts)


# ---------------------------------------------------------------------------
# 4. Duplicate verification task is never re-executed (no infinite loop)
# ---------------------------------------------------------------------------


def test_duplicate_verification_task_is_not_reexecuted():
    fake = FakeLLMClient()
    fake.alignment_queue.append(_alignment_raw_with_single_task())
    fake.verification_queue.append({
        "task_findings": [{
            "task_id": "task_1", "outcome": "unresolved",
            "observation": "still ambiguous", "evidence": "",
        }],
        "updated_alignment_items": [],
        "new_alignment_items": [],
        "new_verification_tasks": [{
            # Same target + same (normalized) question as task_1 -> must be deduped.
            "task_id": "task_2",
            "question": "Is mystery box A visible?",
            "target_item_ids": ["align_2"],
            "region_hint": "center",
            "focus_bbox_rel": None,
            "success_condition": "clarified",
            "refutation_condition": "still ambiguous",
        }],
        "hypothesis_updates": [],
        "record_deltas": {},
        "notes": "",
    })

    analyzer = _analyzer(fake, max_iterations=5)
    record = _run(analyzer, _figure(), VisionBudget(None), rescan_allowance=5)

    ia = record.iterative_analysis
    assert ia.convergence_status in ("converged", "no_progress")
    assert len(ia.verification_iterations) == 1
    assert ia.vision_calls == 2  # observation + exactly one verification round
    assert not any(t.task_id == "task_2" for t in ia.open_verification_tasks)


# ---------------------------------------------------------------------------
# 5. max_iterations reached: fresh tasks every round
# ---------------------------------------------------------------------------


def test_max_iterations_reached_keeps_open_tasks_and_review_questions():
    fake = FakeLLMClient()
    fake.alignment_queue.append(_alignment_raw_with_single_task())
    fake.verification_queue.append(
        _verification_raw_unresolved_with_new_task("task_1", "task_2", "Is mystery box B visible?")
    )
    fake.verification_queue.append(
        _verification_raw_unresolved_with_new_task("task_2", "task_3", "Is mystery box C visible?")
    )

    analyzer = _analyzer(fake, max_iterations=2)
    record = _run(analyzer, _figure(), VisionBudget(None), rescan_allowance=2)

    ia = record.iterative_analysis
    assert ia.convergence_status == "max_iterations_reached"
    assert len(ia.verification_iterations) == 2
    assert ia.review_questions
    assert any(t.task_id == "task_3" for t in ia.open_verification_tasks)


# ---------------------------------------------------------------------------
# 6. hypothesis exception -> stage_failures, pipeline continues
# ---------------------------------------------------------------------------


def test_hypothesis_exception_is_recorded_and_pipeline_continues():
    fake = FakeLLMClient()
    fake.hypothesis_exception = RuntimeError("boom")

    analyzer = _analyzer(fake)
    record = _run(analyzer, _figure(), VisionBudget(None))

    ia = record.iterative_analysis
    assert any("hypothesis: boom" in f for f in ia.stage_failures)
    assert ia.context_hypothesis is None
    assert ia.visual_observations is not None
    assert ia.convergence_status == "converged"


# ---------------------------------------------------------------------------
# 7. observation exception -> text_only-only alignment, aborted_error
# ---------------------------------------------------------------------------


def test_observation_exception_degrades_to_text_only_alignment():
    fake = FakeLLMClient()
    fake.observation_exception = RuntimeError("vision down")

    analyzer = _analyzer(fake)
    record = _run(analyzer, _figure(), VisionBudget(None))

    ia = record.iterative_analysis
    assert ia.convergence_status == "aborted_error"
    assert record.parts == []
    assert record.connections == []
    assert ia.context_hypothesis is not None
    assert ia.alignment_items  # non-empty: derived from hypothesis.expected_elements
    assert all(item.status == "text_only" for item in ia.alignment_items)
    assert any("observation: vision down" in f for f in ia.stage_failures)
    assert record.source_backing_status != "source_backed"


# ---------------------------------------------------------------------------
# 8. budget exhausted -> no rescan, partial results kept
# ---------------------------------------------------------------------------


def test_budget_exhausted_skips_rescan_and_keeps_partial_results():
    fake = FakeLLMClient()
    fake.alignment_queue.append(_alignment_raw_with_single_task())

    analyzer = _analyzer(fake, max_iterations=3)
    record = _run(analyzer, _figure(), VisionBudget(1), rescan_allowance=3)

    ia = record.iterative_analysis
    assert ia.convergence_status == "aborted_cost_limit"
    assert ia.vision_calls == 1
    assert not ia.verification_iterations
    assert any(t.task_id == "task_1" for t in ia.open_verification_tasks)


# ---------------------------------------------------------------------------
# 9. alignment repair exhausted after exactly the configured attempt count
# ---------------------------------------------------------------------------


def test_alignment_repair_exhausted_after_exactly_max_attempts():
    fake = FakeLLMClient()
    fake.alignment_queue.extend([
        _bad_alignment_raw_with_unsupported_part(reason="first bad attempt"),
        _bad_alignment_raw_with_unsupported_part(reason="second bad attempt"),
        _bad_alignment_raw_with_unsupported_part(reason="third bad attempt"),
    ])

    analyzer = _analyzer(fake)
    record = _run(analyzer, _figure(), VisionBudget(None))

    assert fake.schema_call_count(ALIGNMENT_RESPONSE_SCHEMA) == 3  # initial + 2 repairs
    assert record.parts == []
    ia = record.iterative_analysis
    assert any("removed parts without visual support" in f for f in ia.stage_failures)


# ---------------------------------------------------------------------------
# 10. Reservation policy: no figure is starved of its mandatory observation
# ---------------------------------------------------------------------------


def test_reservation_policy_guarantees_observation_for_every_figure():
    agent = ApparatusSemanticsAgent(iterative_config=IterativeConfig(
        enabled=True, max_iterations=3, vision_call_budget=3, model_name="test-model",
    ))
    fig1 = _figure(figure_id="fig_1")
    fig2 = _figure(figure_id="fig_2")

    fig1_alignment = _alignment_raw_with_single_task(task_id="f1_task_1", question="Is A visible?")
    fig1_verification_calls = [
        _verification_raw_unresolved_with_new_task("f1_task_1", "f1_task_2", "Is B visible?"),
        _verification_raw_unresolved_with_new_task("f1_task_2", "f1_task_3", "Is C visible?"),
        _verification_raw_unresolved_with_new_task("f1_task_3", "f1_task_4", "Is D visible?"),
    ]
    fig2_alignment = _alignment_raw()  # trivial convergence, no verification tasks
    state = {"fig1_verify_calls": 0}

    def fake_generate(messages, response_schema=None, images=None):
        content = "\n".join(str(m.get("content", "")) for m in messages)
        figure_id = "fig_1" if "figure_id: fig_1" in content else "fig_2"
        if response_schema is HYPOTHESIS_RESPONSE_SCHEMA:
            return _hypothesis_raw()
        if response_schema is OBSERVATION_RESPONSE_SCHEMA:
            return _observation_raw()
        if response_schema is ALIGNMENT_RESPONSE_SCHEMA:
            return fig1_alignment if figure_id == "fig_1" else fig2_alignment
        if response_schema is VERIFICATION_RESPONSE_SCHEMA:
            assert figure_id == "fig_1", "fig_2 should never need a rescan in this scenario"
            idx = state["fig1_verify_calls"]
            state["fig1_verify_calls"] += 1
            if idx < len(fig1_verification_calls):
                return fig1_verification_calls[idx]
            return _verification_raw()
        raise AssertionError(f"unexpected schema {response_schema!r}")

    with patch.object(agent._llm_client, "generate", side_effect=fake_generate):
        result = agent.run(document_id="doc_test", figures=[fig1, fig2], library_candidates={})

    by_id = {r.figure_id: r for r in result.apparatus_records}
    ia1 = by_id["fig_1"].iterative_analysis
    ia2 = by_id["fig_2"].iterative_analysis

    assert ia1.vision_calls >= 1  # fig1 got its own mandatory observation
    assert ia2.vision_calls >= 1  # fig2 was not starved by fig1's rescans
    assert ia1.vision_calls + ia2.vision_calls <= 3  # never exceeds the shared budget
    assert ia2.convergence_status == "converged"


# ---------------------------------------------------------------------------
# 11. one-shot backward compatibility lives in test_agent.py (append-only)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 12 / 13. iterative path still applies deterministic grounding + never
# assigns source_backed (exercised end-to-end through ApparatusSemanticsAgent)
# ---------------------------------------------------------------------------


def _iterative_fake_generate(messages, response_schema=None, images=None):
    if response_schema is HYPOTHESIS_RESPONSE_SCHEMA:
        return _hypothesis_raw()
    if response_schema is OBSERVATION_RESPONSE_SCHEMA:
        return _observation_raw()
    if response_schema is ALIGNMENT_RESPONSE_SCHEMA:
        return _alignment_raw()
    raise AssertionError(f"unexpected schema {response_schema!r}")


def test_iterative_path_still_attaches_label_grounding():
    agent = ApparatusSemanticsAgent(
        iterative_config=IterativeConfig(enabled=True, max_iterations=1, model_name="test-model"),
    )
    figure = _figure(inner_labels=[{"text": "ION", "bbox": [1, 2, 3, 4]}])

    with patch.object(agent._llm_client, "generate", side_effect=_iterative_fake_generate):
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})

    record = result.apparatus_records[0]
    assert record.parts and record.parts[0].label_ref == "ION"
    assert record.parts[0].bbox == [1, 2, 3, 4]


def test_iterative_path_never_assigns_source_backed():
    agent = ApparatusSemanticsAgent(
        iterative_config=IterativeConfig(enabled=True, max_iterations=1, model_name="test-model"),
    )
    figure = _figure()

    with patch.object(agent._llm_client, "generate", side_effect=_iterative_fake_generate):
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})

    record = result.apparatus_records[0]
    assert record.source_backing_status != "source_backed"
    assert record.review_status == "review_required"
