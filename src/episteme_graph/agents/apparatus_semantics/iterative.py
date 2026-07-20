"""Iterative contextual-analysis state machine for ApparatusSemanticsAgent (#499).

Design doc: docs/features/contextual_figure_analysis_iterative_verification.md
(see the "実装記録（2026-07-18 実装決定事項）" section at the end — that section
is the source of truth for this module).

``IterativeFigureAnalyzer`` drives one figure through the five-step pipeline:

    ① hypothesis (text-only)  -> ContextHypothesis
    ② observation (image-only) -> VisualObservationSet
    ③ alignment (text-only, sees ①+②) -> ApparatusRecord + alignment_items/
      alternative_hypotheses/verification_tasks/review_questions
    ④ verification (image, gap-driven, up to N rounds) -> record deltas +
      alignment updates
    ⑤ convergence (deterministic, Python-only)

It is additive: ``ApparatusSemanticsAgent`` only calls into this module when
constructed with ``IterativeConfig(enabled=True)`` (see agent.py). The
existing one-shot path is untouched.

Every stage failure is caught and recorded in
``IterativeAnalysisRecord.stage_failures`` rather than raising — a figure is
never dropped (P4, "情報を落とさない"). ``run_figure`` never raises.
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from .llm_client import (
    ALIGNMENT_RESPONSE_SCHEMA,
    HYPOTHESIS_RESPONSE_SCHEMA,
    OBSERVATION_RESPONSE_SCHEMA,
    VERIFICATION_RESPONSE_SCHEMA,
    ApparatusSemanticsLLMClient,
)
from .prompt import IterativePromptFactory
from .repair import _fallback_record, _normalize, _safe_float
from .repair import (
    parse_alignment_output,
    parse_hypothesis,
    parse_observations,
    parse_verification_output,
)
from .schema import (
    AlignmentItem,
    ApparatusConnection,
    ApparatusPart,
    ApparatusRecord,
    ContextHypothesis,
    FigureImageInput,
    IterativeAnalysisRecord,
    IterativeConfig,
    LibraryCandidate,
    VerificationIterationRecord,
    VerificationTask,
)
from .validator import ApparatusSemanticsValidator

logger = logging.getLogger(__name__)

# Per-stage bounded repair attempts (mirrors the one-shot repair pattern in
# repair.py, but scoped per pipeline stage — see design doc "エンジンと後方互換").
_OBSERVATION_MAX_REPAIR_ATTEMPTS = 1
_ALIGNMENT_MAX_REPAIR_ATTEMPTS = 2
_VERIFICATION_MAX_REPAIR_ATTEMPTS = 1

class VisionBudget:
    """Mutable vision-call budget shared across every figure in one
    ``ApparatusSemanticsAgent.run()`` invocation.

    ``total=None`` means unlimited: ``remaining()`` stays ``None`` and
    ``try_consume()`` always succeeds. Otherwise each successful
    ``try_consume()`` call decrements the counter by one; once it reaches
    zero, further calls return ``False`` (the caller must degrade rather than
    spend more vision calls — design doc "コスト制御").
    """

    def __init__(self, total: int | None) -> None:
        self._remaining = total

    def remaining(self) -> int | None:
        return self._remaining

    def try_consume(self) -> bool:
        if self._remaining is None:
            return True
        if self._remaining <= 0:
            return False
        self._remaining -= 1
        return True


def _task_key(task: VerificationTask) -> tuple[tuple[str, ...], str]:
    """Dedupe key for a verification task: normalized target ids + question.

    Used to reject re-executing an already-answered question (design doc
    "同じ入力・同じ問いを繰り返すだけの iteration は行わない") and to reject a
    freshly-proposed task that repeats one already asked.
    """
    return (tuple(sorted(str(t) for t in task.target_item_ids)), _normalize(task.question))


class IterativeFigureAnalyzer:
    """State machine driving one figure through the hypothesis -> observation
    -> alignment -> gap-driven verification -> convergence pipeline."""

    def __init__(
        self,
        llm_client: ApparatusSemanticsLLMClient,
        prompt_factory: IterativePromptFactory,
        validator: ApparatusSemanticsValidator,
        config: IterativeConfig,
    ) -> None:
        self._llm_client = llm_client
        self._prompt_factory = prompt_factory
        self._validator = validator
        self._config = config

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run_figure(
        self,
        *,
        figure: FigureImageInput,
        candidates: list[LibraryCandidate],
        candidate_briefs: list[dict],
        cartridge_hints: dict | None,
        guidance: dict | None,
        image_payloads: list[dict],
        budget: VisionBudget,
        rescan_allowance: int,
    ) -> ApparatusRecord:
        cartridge_hints = cartridge_hints or {}
        ia = IterativeAnalysisRecord(enabled=True, model=self._config.model_name)
        candidate_ids = {c.entry_id for c in candidates}

        hypothesis, hypothesis_dict = self._run_hypothesis(figure, cartridge_hints, ia)
        observations, observations_dict = self._run_observation(
            figure, image_payloads, budget, ia,
        )

        if observations is None:
            # ②失敗 or 予算枯渇 -> 観察なし縮退 (design doc "観察なし縮退").
            record = self._degrade_without_observation(figure, hypothesis, ia)
            record.iterative_analysis = ia
            return record

        record, iterative_parts = self._run_alignment(
            figure=figure,
            candidates=candidates,
            candidate_ids=candidate_ids,
            candidate_briefs=candidate_briefs,
            cartridge_hints=cartridge_hints,
            guidance=guidance,
            hypothesis_dict=hypothesis_dict,
            observations_dict=observations_dict,
            ia=ia,
        )
        if record is None:
            record = _fallback_record(
                figure, "iterative_alignment_failed", repair_failed=False,
            )
            ia.convergence_status = "aborted_error"
            self._finalize_ia(
                ia, alignment_items=[], review_questions_raw=[], open_tasks=[],
                unresolved_tasks=[], figure=figure,
            )
            record.iterative_analysis = ia
            return record

        alignment_items: list[AlignmentItem] = list(iterative_parts.get("alignment_items") or [])
        alternative_hypotheses = list(iterative_parts.get("alternative_hypotheses") or [])
        tasks: list[VerificationTask] = list(iterative_parts.get("verification_tasks") or [])
        review_questions_raw = list(iterative_parts.get("review_questions") or [])

        self._run_verification_loop(
            figure=figure,
            record=record,
            alignment_items=alignment_items,
            alternative_hypotheses=alternative_hypotheses,
            tasks=tasks,
            observations=observations,
            guidance=guidance,
            image_payloads=image_payloads,
            budget=budget,
            rescan_allowance=rescan_allowance,
            ia=ia,
        )

        ia.alignment_items = alignment_items
        ia.alternative_hypotheses = alternative_hypotheses
        open_tasks = [t for t in tasks if t.status == "open"]
        unresolved_tasks = [t for t in tasks if t.status == "unresolved"]
        self._finalize_ia(
            ia, alignment_items=alignment_items, review_questions_raw=review_questions_raw,
            open_tasks=open_tasks, unresolved_tasks=unresolved_tasks, figure=figure,
        )
        ia.open_verification_tasks = open_tasks

        # Fold any residual structural issues into stage_failures rather than
        # raising or dropping the record (P4) — run_figure's contract is to
        # always return a reviewable ApparatusRecord.
        residual = self._validator.validate_iterative_record(ia)
        if residual:
            ia.stage_failures.append(
                "iterative_record: " + "; ".join(i.message for i in residual)
            )

        record.iterative_analysis = ia
        return record

    # ------------------------------------------------------------------
    # Step 1: hypothesis (text-only, no vision spend)
    # ------------------------------------------------------------------

    def _run_hypothesis(
        self, figure: FigureImageInput, cartridge_hints: dict, ia: IterativeAnalysisRecord,
    ) -> tuple[ContextHypothesis | None, dict]:
        try:
            messages = self._prompt_factory.build_hypothesis_messages(figure, cartridge_hints)
            ia.llm_calls += 1
            raw = self._llm_client.generate(messages, response_schema=HYPOTHESIS_RESPONSE_SCHEMA)
        except Exception as exc:  # noqa: BLE001 - stage must never propagate
            ia.stage_failures.append(f"hypothesis: {exc}")
            return None, {}

        hypothesis = parse_hypothesis(raw)
        # No dedicated hypothesis-repair prompt exists in prompt.py for this
        # wave; validation issues are logged but do not block the pipeline —
        # the hypothesis is still usable as a best-effort expectation model.
        issues = self._validator.validate_hypothesis(hypothesis, figure=figure)
        if [i for i in issues if i.severity == "error"]:
            logger.info(
                "apparatus_semantics iterative hypothesis validation issues figure=%s: %s",
                figure.figure_id, [i.message for i in issues if i.severity == "error"],
            )
        ia.context_hypothesis = hypothesis
        return hypothesis, raw

    # ------------------------------------------------------------------
    # Step 2: observation (image-only)
    # ------------------------------------------------------------------

    def _run_observation(
        self,
        figure: FigureImageInput,
        image_payloads: list[dict],
        budget: VisionBudget,
        ia: IterativeAnalysisRecord,
    ):
        if not budget.try_consume():
            ia.convergence_status = "aborted_cost_limit"
            return None, {}

        messages = self._prompt_factory.build_observation_messages(figure)
        try:
            ia.llm_calls += 1
            ia.vision_calls += 1
            raw = self._llm_client.generate(
                messages, response_schema=OBSERVATION_RESPONSE_SCHEMA, images=image_payloads,
            )
        except Exception as exc:  # noqa: BLE001
            ia.stage_failures.append(f"observation: {exc}")
            ia.convergence_status = "aborted_error"
            return None, {}

        observations = parse_observations(raw)
        issues = self._validator.validate_observations(observations)
        error_issues = [i for i in issues if i.severity == "error"]

        if error_issues and budget.try_consume():
            try:
                ia.llm_calls += 1
                ia.vision_calls += 1
                raw_retry = self._llm_client.generate(
                    messages, response_schema=OBSERVATION_RESPONSE_SCHEMA, images=image_payloads,
                )
                observations_retry = parse_observations(raw_retry)
                issues_retry = self._validator.validate_observations(observations_retry)
                if not [i for i in issues_retry if i.severity == "error"]:
                    observations, raw = observations_retry, raw_retry
                    error_issues = []
                else:
                    error_issues = [i for i in issues_retry if i.severity == "error"]
            except Exception as exc:  # noqa: BLE001
                ia.stage_failures.append(f"observation: repair_failed {exc}")

        if error_issues:
            ia.stage_failures.append(
                "observation: unresolved_validation_errors: "
                + "; ".join(i.message for i in error_issues)
            )

        ia.visual_observations = observations
        return observations, raw

    def _degrade_without_observation(
        self,
        figure: FigureImageInput,
        hypothesis: ContextHypothesis | None,
        ia: IterativeAnalysisRecord,
    ) -> ApparatusRecord:
        """Observation could not be produced (budget exhausted or LLM
        exception). parts/connections stay empty — never fabricate detection
        from text alone (design doc "観察なし縮退")."""
        record = _fallback_record(
            figure, f"iterative_no_observation:{ia.convergence_status}",
        )
        alignment_items: list[AlignmentItem] = []
        if hypothesis is not None:
            for idx, element in enumerate(hypothesis.expected_elements, start=1):
                alignment_items.append(AlignmentItem(
                    item_id=element.element_id or f"align_{idx}",
                    item_kind="element",
                    label=element.name,
                    status="text_only",
                    expected_ref=element.element_id,
                    text_evidence=element.evidence_quote,
                    severity="medium",
                    reason="no visual observation was available for this run",
                    confidence=element.confidence,
                ))
        ia.alignment_items = alignment_items
        self._finalize_ia(
            ia, alignment_items=alignment_items, review_questions_raw=[], open_tasks=[],
            unresolved_tasks=[], figure=figure,
        )
        return record

    # ------------------------------------------------------------------
    # Step 3: alignment (text-only, sees ①+②)
    # ------------------------------------------------------------------

    def _run_alignment(
        self,
        *,
        figure: FigureImageInput,
        candidates: list[LibraryCandidate],
        candidate_ids: set[str],
        candidate_briefs: list[dict],
        cartridge_hints: dict,
        guidance: dict | None,
        hypothesis_dict: dict,
        observations_dict: dict,
        ia: IterativeAnalysisRecord,
    ):
        try:
            messages = self._prompt_factory.build_alignment_messages(
                figure, hypothesis_dict, observations_dict, candidate_briefs,
                cartridge_hints, guidance,
            )
            ia.llm_calls += 1
            raw = self._llm_client.generate(messages, response_schema=ALIGNMENT_RESPONSE_SCHEMA)
        except Exception as exc:  # noqa: BLE001
            ia.stage_failures.append(f"alignment: {exc}")
            return None, None

        record, iterative_parts = parse_alignment_output(raw, figure, candidates)
        observations_obj = ia.visual_observations
        issues = self._validator.validate_alignment(
            record, iterative_parts, figure=figure, candidate_ids=candidate_ids,
            observations=observations_obj,
        )
        error_issues = [i for i in issues if i.severity == "error"]

        attempt = 1
        while error_issues and attempt <= _ALIGNMENT_MAX_REPAIR_ATTEMPTS:
            try:
                messages = self._prompt_factory.build_alignment_repair_messages(
                    figure, hypothesis_dict, observations_dict, candidate_briefs,
                    cartridge_hints, raw, error_issues, guidance,
                )
                ia.llm_calls += 1
                raw = self._llm_client.generate(messages, response_schema=ALIGNMENT_RESPONSE_SCHEMA)
            except Exception as exc:  # noqa: BLE001
                ia.stage_failures.append(f"alignment: repair_failed {exc}")
                break
            record, iterative_parts = parse_alignment_output(raw, figure, candidates)
            issues = self._validator.validate_alignment(
                record, iterative_parts, figure=figure, candidate_ids=candidate_ids,
                observations=observations_obj,
            )
            error_issues = [i for i in issues if i.severity == "error"]
            attempt += 1

        inner_label_ci_set, observation_ids = self._support_sets(figure, observations_obj)
        alignment_items_seen = list((iterative_parts or {}).get("alignment_items") or [])

        if error_issues:
            # Repair exhausted without a clean result — never fail the whole
            # figure over this (P4). Structurally guarantee two invariants via
            # deterministic Python-only recovery instead (design doc "決定論的
            # ガード"): (1) an item claiming supported_by_both/visual_only but
            # whose visual backing cannot be traced to a known observation or
            # in-figure label gets downgraded rather than trusted as-is (gap
            # #3 — a free-text description alone is not visual evidence), then
            # (2) any part left without a *supporting and traceable* alignment
            # item is stripped (gap #1).
            downgraded = self._downgrade_untraceable_alignment_items(
                alignment_items_seen,
                inner_label_ci_set=inner_label_ci_set,
                observation_ids=observation_ids,
            )
            if downgraded:
                ia.stage_failures.append(
                    "alignment: downgraded untraceable items after repair "
                    "exhaustion: " + "; ".join(downgraded)
                )

        removed = self._enforce_part_support(
            record, alignment_items_seen,
            inner_label_ci_set=inner_label_ci_set, observation_ids=observation_ids,
        )
        if removed:
            ia.stage_failures.append(
                "alignment: removed parts without visual support: " + "; ".join(removed)
            )

        if error_issues:
            remaining_fatal = [
                i for i in self._validator.validate_alignment(
                    record, iterative_parts, figure=figure, candidate_ids=candidate_ids,
                    observations=observations_obj,
                )
                if i.severity == "error" and i.rule_id not in (
                    "part_without_visual_support", "alignment_visual_support_untraceable",
                )
            ]
            if remaining_fatal:
                ia.stage_failures.append(
                    "alignment: unresolved_after_repair: "
                    + "; ".join(i.message for i in remaining_fatal)
                )

        return record, iterative_parts

    @staticmethod
    def _support_sets(
        figure: FigureImageInput, observations,
    ) -> tuple[set[str], set[str]]:
        """(inner_label_ci_set, observation_id_set) shared by every
        deterministic part-support guard (``_enforce_part_support`` /
        ``_downgrade_untraceable_alignment_items``)."""
        inner_label_ci_set = {
            str(label.get("text", "") or "").strip().casefold()
            for label in (figure.inner_labels or [])
            if isinstance(label, dict) and str(label.get("text", "") or "").strip()
        }
        observation_ids = {
            e.observation_id for e in (observations.elements if observations else []) if e.observation_id
        }
        return inner_label_ci_set, observation_ids

    @staticmethod
    def _item_visual_support_traceable(
        item: AlignmentItem, *, inner_label_ci_set: set[str], observation_ids: set[str],
    ) -> bool:
        """Whether ``item``'s observation_refs/label_ref actually resolve to a
        known observation or a real in-figure label (gap #3) — mirrors
        validator.py's ``_alignment_visual_support_is_traceable`` (the
        observations-present branch), expressed over the engine's precomputed
        sets rather than the raw ``VisualObservationSet``/``FigureImageInput``.
        """
        obs_refs = [str(r) for r in (item.observation_refs or []) if str(r).strip()]
        if any(r in observation_ids for r in obs_refs):
            return True
        label_ref = (item.label_ref or "").strip().casefold()
        if label_ref and (not inner_label_ci_set or label_ref in inner_label_ci_set):
            return True
        return False

    @staticmethod
    def _downgrade_untraceable_alignment_items(
        alignment_items: list[AlignmentItem],
        *,
        inner_label_ci_set: set[str],
        observation_ids: set[str],
    ) -> list[str]:
        """Deterministic recovery once the alignment repair budget is
        exhausted (gap #3): an item claiming supported_by_both/visual_only
        whose visual backing cannot be traced to a known observation or
        in-figure label is downgraded rather than left to silently fabricate
        visual support. supported_by_both downgrades to text_only when it
        still has text backing (and its now-untrustworthy visual refs are
        cleared); everything else downgrades to unresolved.
        """
        changes: list[str] = []
        for item in alignment_items:
            if item.status not in ("supported_by_both", "visual_only"):
                continue
            if IterativeFigureAnalyzer._item_visual_support_traceable(
                item, inner_label_ci_set=inner_label_ci_set, observation_ids=observation_ids,
            ):
                continue
            old_status = item.status
            if old_status == "supported_by_both" and item.text_evidence.strip():
                item.status = "text_only"
                item.observation_refs = []
                item.label_ref = ""
            else:
                item.status = "unresolved"
            changes.append(
                f"alignment item {item.item_id or '<unknown>'}: downgraded "
                f"{old_status} -> {item.status} (visual evidence not traceable "
                "to a known observation or in-figure label)"
            )
        return changes

    @staticmethod
    def _enforce_part_support(
        record: ApparatusRecord,
        alignment_items: list[AlignmentItem],
        *,
        inner_label_ci_set: set[str],
        observation_ids: set[str],
    ) -> list[str]:
        """Deterministically strip any part whose backing alignment item is
        missing, no longer a supporting status, or not traceable to a known
        visual observation (design doc gap #1: a verification round can
        downgrade an item to text_only/unresolved without also returning
        parts_to_remove, letting the negated part survive into the final
        record).

        Mirrors the part<->item matching rule used by validator.py's
        ``part_without_visual_support`` (label_ref equality, else name<->label
        case-insensitive equality; an OR across the two, not "pick one and
        stop") plus the traceability requirement from gap #3. Safe to call
        repeatedly / on an already-consistent record (no-op — returns ``[]``).
        """
        supporting_by_label: set[str] = set()
        supporting_by_name: set[str] = set()
        any_item_by_label: dict[str, AlignmentItem] = {}
        any_item_by_name: dict[str, AlignmentItem] = {}

        for item in alignment_items:
            label_ref = (item.label_ref or "").strip().casefold()
            name = (item.label or "").strip().casefold()
            if label_ref:
                any_item_by_label.setdefault(label_ref, item)
            if name:
                any_item_by_name.setdefault(name, item)
            if item.status not in ("supported_by_both", "visual_only"):
                continue
            if not IterativeFigureAnalyzer._item_visual_support_traceable(
                item, inner_label_ci_set=inner_label_ci_set, observation_ids=observation_ids,
            ):
                continue
            if label_ref:
                supporting_by_label.add(label_ref)
            if name:
                supporting_by_name.add(name)

        changes: list[str] = []
        kept: list[ApparatusPart] = []
        for part in record.parts:
            part_label_ref = (part.label_ref or "").strip().casefold()
            part_name = (part.name or "").strip().casefold()
            supported = (
                bool(part_label_ref) and part_label_ref in supporting_by_label
            ) or (
                bool(part_name) and part_name in supporting_by_name
            )
            if supported:
                kept.append(part)
                continue
            matching_item = None
            if part_label_ref:
                matching_item = any_item_by_label.get(part_label_ref)
            if matching_item is None and part_name:
                matching_item = any_item_by_name.get(part_name)
            status = matching_item.status if matching_item is not None else "missing"
            changes.append(f"part removed (alignment status={status}): {part.name}")

        if len(kept) == len(record.parts):
            return []

        record.parts = kept
        kept_names = {p.name for p in kept}
        surviving_connections: list[ApparatusConnection] = []
        for conn in record.connections:
            if conn.from_part in kept_names and conn.to_part in kept_names:
                surviving_connections.append(conn)
            else:
                changes.append(
                    "connection removed (part no longer supported): "
                    f"{conn.from_part}->{conn.to_part}"
                )
        record.connections = surviving_connections

        return changes

    # ------------------------------------------------------------------
    # Step 4: gap-driven verification loop
    # ------------------------------------------------------------------

    def _run_verification_loop(
        self,
        *,
        figure: FigureImageInput,
        record: ApparatusRecord,
        alignment_items: list[AlignmentItem],
        alternative_hypotheses: list,
        tasks: list[VerificationTask],
        observations,
        guidance: dict | None,
        image_payloads: list[dict],
        budget: VisionBudget,
        rescan_allowance: int,
        ia: IterativeAnalysisRecord,
    ) -> None:
        inner_label_ci_set, observation_ids = self._support_sets(figure, observations)

        executed_keys: set[tuple] = set()
        max_rounds = max(0, min(self._config.max_iterations, rescan_allowance))
        iteration_index = 0

        while True:
            # Convergence is judged over the *true* status of every task ever
            # seen, never filtered by ``executed_keys`` (design doc gap #2:
            # "未回答の検証課題があっても converged になる" — a task that was
            # executed but got no finding back must not quietly vanish from
            # this check just because it is in ``executed_keys``).
            # ``executed_keys`` dedupes only which tasks get *re-sent* to the
            # LLM this round, never whether the loop considers itself done.
            has_high_contradiction = any(
                item.status == "contradicted" and item.severity == "high"
                for item in alignment_items
            )
            any_open_task = any(t.status == "open" for t in tasks)

            if not has_high_contradiction and not any_open_task:
                ia.convergence_status = "converged"
                return

            open_tasks = [
                t for t in tasks if t.status == "open" and _task_key(t) not in executed_keys
            ]
            if not open_tasks:
                ia.convergence_status = "no_progress"
                return
            if iteration_index >= max_rounds:
                ia.convergence_status = "max_iterations_reached"
                return
            if not budget.try_consume():
                ia.convergence_status = "aborted_cost_limit"
                return

            iteration_index += 1
            executed_task_ids_this_round = {t.task_id for t in open_tasks}
            for task in open_tasks:
                executed_keys.add(_task_key(task))

            task_dicts = [asdict(t) for t in open_tasks]
            alignment_snapshot = [asdict(i) for i in alignment_items]
            messages = self._prompt_factory.build_verification_messages(
                figure, task_dicts, alignment_snapshot, iteration_index, guidance=guidance,
            )

            try:
                ia.llm_calls += 1
                ia.vision_calls += 1
                raw = self._llm_client.generate(
                    messages, response_schema=VERIFICATION_RESPONSE_SCHEMA, images=image_payloads,
                )
            except Exception as exc:  # noqa: BLE001
                ia.stage_failures.append(f"verification[{iteration_index}]: {exc}")
                ia.verification_iterations.append(VerificationIterationRecord(
                    iteration_index=iteration_index,
                    executed_task_ids=[t.task_id for t in open_tasks],
                    findings=[],
                    changes=[],
                    vision_call_used=True,
                    notes=f"exception: {exc}",
                ))
                ia.convergence_status = "no_progress"
                return

            known_task_ids = {t.task_id for t in tasks}
            parsed = parse_verification_output(raw)
            issues = self._validator.validate_verification_output(
                parsed, known_task_ids=known_task_ids, observations=observations, figure=figure,
            )
            error_issues = [i for i in issues if i.severity == "error"]

            if error_issues and budget.try_consume():
                try:
                    ia.llm_calls += 1
                    ia.vision_calls += 1
                    raw_retry = self._llm_client.generate(
                        messages, response_schema=VERIFICATION_RESPONSE_SCHEMA,
                        images=image_payloads,
                    )
                    parsed_retry = parse_verification_output(raw_retry)
                    issues_retry = self._validator.validate_verification_output(
                        parsed_retry, known_task_ids=known_task_ids,
                        observations=observations, figure=figure,
                    )
                    if not [i for i in issues_retry if i.severity == "error"]:
                        parsed, error_issues = parsed_retry, []
                    else:
                        error_issues = [i for i in issues_retry if i.severity == "error"]
                except Exception as exc:  # noqa: BLE001
                    ia.stage_failures.append(
                        f"verification[{iteration_index}]: repair_failed {exc}"
                    )

            if error_issues:
                ia.verification_iterations.append(VerificationIterationRecord(
                    iteration_index=iteration_index,
                    executed_task_ids=[t.task_id for t in open_tasks],
                    findings=[],
                    changes=[],
                    vision_call_used=True,
                    notes="discarded_invalid_output: " + "; ".join(
                        i.message for i in error_issues
                    ),
                ))
                ia.convergence_status = "no_progress"
                return

            changes = self._merge_verification_output(
                parsed=parsed,
                tasks=tasks,
                alignment_items=alignment_items,
                alternative_hypotheses=alternative_hypotheses,
                record=record,
                observation_ids=observation_ids,
                inner_label_ci_set=inner_label_ci_set,
                iteration_index=iteration_index,
                executed_keys=executed_keys,
                executed_task_ids=executed_task_ids_this_round,
            )

            # Gap #1: a verification round can downgrade an item's status
            # (e.g. to text_only/unresolved) without also returning a matching
            # parts_to_remove delta. Re-derive part support from the
            # (possibly just-updated) alignment items after every merge so a
            # negated part can never survive into the final record.
            changes.extend(self._enforce_part_support(
                record, alignment_items,
                inner_label_ci_set=inner_label_ci_set, observation_ids=observation_ids,
            ))

            ia.verification_iterations.append(VerificationIterationRecord(
                iteration_index=iteration_index,
                executed_task_ids=[t.task_id for t in open_tasks],
                findings=[dict(f) for f in (parsed.get("task_findings") or []) if isinstance(f, dict)],
                changes=changes,
                vision_call_used=True,
                notes=str(parsed.get("notes") or ""),
            ))

            if not changes:
                has_high = any(
                    item.status == "contradicted" and item.severity == "high"
                    for item in alignment_items
                )
                any_open_task_now = any(t.status == "open" for t in tasks)
                ia.convergence_status = (
                    "converged" if not has_high and not any_open_task_now else "no_progress"
                )
                return
            # else: loop back to top and re-evaluate convergence conditions.

    @staticmethod
    def _merge_verification_output(
        *,
        parsed: dict,
        tasks: list[VerificationTask],
        alignment_items: list[AlignmentItem],
        alternative_hypotheses: list,
        record: ApparatusRecord,
        observation_ids: set[str],
        inner_label_ci_set: set[str],
        iteration_index: int,
        executed_keys: set[tuple],
        executed_task_ids: set[str],
    ) -> list[str]:
        changes: list[str] = []

        # --- task status updates ---
        outcome_by_task = {
            str(f.get("task_id", "")): str(f.get("outcome", ""))
            for f in (parsed.get("task_findings") or []) if isinstance(f, dict)
        }
        _OUTCOME_TO_STATUS = {"resolved": "resolved", "refuted": "refuted", "unresolved": "unresolved"}
        for task in tasks:
            outcome = outcome_by_task.get(task.task_id)
            if outcome is None:
                continue
            new_status = _OUTCOME_TO_STATUS.get(outcome)
            if new_status and new_status != task.status:
                changes.append(f"task {task.task_id}: {task.status} -> {new_status}")
                task.status = new_status

        # --- tasks executed this round but with no finding returned at all:
        # force them to "unresolved" rather than silently leaving them "open"
        # forever (design doc gap #2: "未回答の検証課題があっても converged
        # になる" — an "open" task whose key is already in executed_keys used
        # to be invisible to the next round's convergence check).
        for task in tasks:
            if task.task_id in executed_task_ids and task.status == "open":
                changes.append(f"task {task.task_id}: no finding returned -> unresolved")
                task.status = "unresolved"

        # --- alignment item updates (by item_id) ---
        by_id = {item.item_id: item for item in alignment_items if item.item_id}
        for updated in parsed.get("updated_alignment_items") or []:
            if not updated.item_id:
                continue
            existing = by_id.get(updated.item_id)
            if existing is not None:
                if existing.status != updated.status:
                    changes.append(
                        f"{updated.item_id}: {existing.status} -> {updated.status}"
                    )
                idx = alignment_items.index(existing)
                alignment_items[idx] = updated
                by_id[updated.item_id] = updated
            else:
                alignment_items.append(updated)
                by_id[updated.item_id] = updated
                changes.append(f"align item added: {updated.item_id}")

        existing_align_ids = {item.item_id for item in alignment_items if item.item_id}
        for new_item in parsed.get("new_alignment_items") or []:
            item_id = new_item.item_id or f"align_iter{iteration_index}"
            candidate_id = item_id
            suffix = 1
            while candidate_id in existing_align_ids:
                suffix += 1
                candidate_id = f"{item_id}_{suffix}"
            new_item.item_id = candidate_id
            alignment_items.append(new_item)
            existing_align_ids.add(candidate_id)
            changes.append(f"align item added: {candidate_id}")

        # --- record deltas (parts/connections) ---
        deltas = parsed.get("record_deltas") or {}

        for part_delta in deltas.get("parts_to_add") or []:
            if not isinstance(part_delta, dict):
                continue
            obs_refs = [
                str(r) for r in (part_delta.get("observation_refs") or []) if str(r).strip()
            ]
            label_ref = str(part_delta.get("label_ref") or "").strip()
            has_obs_backing = bool(obs_refs) and any(r in observation_ids for r in obs_refs)
            has_label_backing = bool(label_ref) and (
                not inner_label_ci_set or label_ref.casefold() in inner_label_ci_set
            )
            name = str(part_delta.get("name") or "").strip()
            if not (has_obs_backing or has_label_backing):
                changes.append(f"part not added (no visual support): {name or '<unnamed>'}")
                continue
            if not name or any(p.name == name for p in record.parts):
                continue
            record.parts.append(ApparatusPart(
                name=name,
                role=str(part_delta.get("role") or ""),
                label_ref=label_ref or None,
                evidence_quote=str(part_delta.get("evidence_quote") or ""),
                reason=str(part_delta.get("reason") or ""),
                confidence=_safe_float(part_delta.get("confidence", 0.0)),
            ))
            changes.append(f"part added: {name}")

        for name in deltas.get("parts_to_remove") or []:
            name = str(name)
            before = len(record.parts)
            record.parts = [p for p in record.parts if p.name != name]
            if len(record.parts) != before:
                changes.append(f"part removed: {name}")

        part_names = {p.name for p in record.parts}
        for conn_delta in deltas.get("connections_to_add") or []:
            if not isinstance(conn_delta, dict):
                continue
            from_part = str(conn_delta.get("from_part") or "")
            to_part = str(conn_delta.get("to_part") or "")
            if from_part not in part_names or to_part not in part_names:
                changes.append(f"connection not added (unknown part): {from_part}->{to_part}")
                continue
            record.connections.append(ApparatusConnection(
                from_part=from_part,
                to_part=to_part,
                relation=str(conn_delta.get("relation") or ""),
                reason=str(conn_delta.get("reason") or ""),
                confidence=_safe_float(conn_delta.get("confidence", 0.0)),
            ))
            changes.append(f"connection added: {from_part}->{to_part}")

        for conn_delta in deltas.get("connections_to_remove") or []:
            if not isinstance(conn_delta, dict):
                continue
            from_part = str(conn_delta.get("from_part") or "")
            to_part = str(conn_delta.get("to_part") or "")
            before = len(record.connections)
            record.connections = [
                c for c in record.connections
                if not (c.from_part == from_part and c.to_part == to_part)
            ]
            if len(record.connections) != before:
                changes.append(f"connection removed: {from_part}->{to_part}")

        # --- alternative hypothesis updates ---
        hyp_by_id = {h.hypothesis_id: h for h in alternative_hypotheses if h.hypothesis_id}
        for update in parsed.get("hypothesis_updates") or []:
            if not isinstance(update, dict):
                continue
            hid = str(update.get("hypothesis_id") or "")
            hyp = hyp_by_id.get(hid)
            if hyp is None:
                continue
            new_status = str(update.get("status") or hyp.status)
            if new_status != hyp.status:
                changes.append(f"hypothesis {hid}: {hyp.status} -> {new_status}")
            hyp.status = new_status
            hyp.confidence = _safe_float(update.get("confidence", hyp.confidence))

        # --- new verification tasks (deduped against every task ever seen) ---
        existing_task_keys = {_task_key(t) for t in tasks} | executed_keys
        existing_task_ids = {t.task_id for t in tasks}
        for new_task in parsed.get("new_verification_tasks") or []:
            key = _task_key(new_task)
            if key in existing_task_keys:
                continue
            task_id = new_task.task_id or f"task_iter{iteration_index}"
            candidate_id = task_id
            suffix = 1
            while candidate_id in existing_task_ids:
                suffix += 1
                candidate_id = f"{task_id}_{suffix}"
            new_task.task_id = candidate_id
            tasks.append(new_task)
            existing_task_keys.add(key)
            existing_task_ids.add(candidate_id)
            changes.append(f"new task: {candidate_id}")

        return changes

    # ------------------------------------------------------------------
    # Step 5: finalization (deterministic — never LLM)
    # ------------------------------------------------------------------

    def _finalize_ia(
        self,
        ia: IterativeAnalysisRecord,
        *,
        alignment_items: list[AlignmentItem],
        review_questions_raw: list[dict],
        open_tasks: list[VerificationTask],
        unresolved_tasks: list[VerificationTask],
        figure: FigureImageInput,
    ) -> None:
        ia.unresolved_conflicts = [
            asdict(item) for item in alignment_items
            if item.status in ("contradicted", "unresolved")
        ]

        review_questions = [dict(q) for q in review_questions_raw if isinstance(q, dict)]
        seen_question_ids = {q.get("question_id") for q in review_questions}

        def _add_question(question_id: str, question: str, related_item_ids: list, region_hint: str = "") -> None:
            if question_id in seen_question_ids:
                return
            review_questions.append({
                "question_id": question_id,
                "question": question,
                "related_item_ids": related_item_ids,
                "region_hint": region_hint,
            })
            seen_question_ids.add(question_id)

        non_converged = ia.convergence_status != "converged"

        if non_converged and not review_questions:
            for task in open_tasks:
                _add_question(
                    f"open_task_{task.task_id}", task.question or self._generic_question(figure),
                    list(task.target_item_ids), task.region_hint,
                )

        # Design doc: "未解決点が明示されていることが収束条件の一部" — an
        # "unresolved" verification task or an unresolved/contradicted
        # alignment item must surface as a review question regardless of
        # convergence_status. A task/item can end up "unresolved" (rather
        # than "open") on a run that otherwise converged (e.g. only a
        # low/medium-severity contradiction remained), so this must not be
        # gated by ``non_converged`` (gap #2).
        for task in unresolved_tasks:
            _add_question(
                f"unresolved_task_{task.task_id}", task.question or self._generic_question(figure),
                list(task.target_item_ids), task.region_hint,
            )

        for idx, item in enumerate(alignment_items, start=1):
            if item.status in ("contradicted", "unresolved"):
                _add_question(
                    f"synthesized_{item.item_id or idx}", self._synthesize_question(item),
                    [item.item_id] if item.item_id else [],
                )

        if non_converged and not review_questions:
            for idx, item in enumerate(alignment_items, start=1):
                if item.status == "text_only":
                    _add_question(
                        f"synthesized_{item.item_id or idx}", self._synthesize_question(item),
                        [item.item_id] if item.item_id else [],
                    )

        if non_converged and not review_questions:
            _add_question("synthesized_generic", self._generic_question(figure), [])

        ia.review_questions = review_questions

    @staticmethod
    def _synthesize_question(item: AlignmentItem) -> str:
        if item.status == "text_only":
            return (
                f"「{item.label}」は本文で言及されていますが、画像内で確認できませんでした。"
                "図中のどこに対応するか教えてください。"
            )
        if item.status == "contradicted":
            return (
                f"「{item.label}」について、本文と画像の内容が一致しないようです。"
                "どちらが正しいか確認をお願いします。"
            )
        return f"「{item.label}」について十分な情報がなく判断できません。確認をお願いします。"

    @staticmethod
    def _generic_question(figure: FigureImageInput) -> str:
        return (
            f"「{figure.figure_label or figure.figure_id}」の解析は自動収束しませんでした。"
            "内容を確認してください。"
        )
