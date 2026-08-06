"""Validation for LandscapePlacementAgent output.

Design: ``docs/features/knowledge_landscape_design.md`` §7.3. The validate
signature matches ``core/llm_worker/repair.run_with_repair``'s contract:
``validate(raw) -> (result | None, errors, warnings)``.

Hard errors (they force a repair attempt; 2 failures ⇒ nothing is placed — an
invented placement is worse than no placement):

- ``placements`` missing / not a list (output shape).
- ``domain_key`` not among the supplied domains, or ``node_id`` not among that
  domain's nodes. The map is the authority: the LLM may not invent anchors (LS7).
- ``perspective`` outside :data:`PERSPECTIVES`.
- ``weight`` missing / not a number / outside ``[0, 1]`` — it is the source of
  the admin UI's coarse label, so it is never silently defaulted (LS5).
- ``reason`` empty (LS4: every placement says why).
- ``evidence_quote`` empty, too short/long, or **not verbatim** in the supplied
  material (whitespace-normalized containment) — LS4 の捏造ガード.
- ``claim_id``, when present, not among the supplied claim ids.
- Zero usable placements **and** no ``unplaced_domains`` declared while the
  document has material: "cannot place" is allowed, but it must be *said*
  (LS10). Silence is a repair-worthy failure.

Warnings (kept, never block):

- more placements than ``max_placements`` → deterministic truncation
  (descending ``weight``, then input order) with ``truncated=True``.
- duplicate ``(domain_key, node_id, perspective)`` → first-wins dedupe.
- fewer placements than the design's 2〜6 target, or an ``unplaced_domains``
  entry naming a domain that was not offered.
"""
from __future__ import annotations

from .input_builder import LandscapePlacementInputBuilder, normalize_for_quote_match
from .schema import (
    LANDSCAPE_PLACEMENT_VERSION,
    LandscapePlacementInput,
    LandscapePlacementResult,
    MAX_EVIDENCE_QUOTE_CHARS,
    MAX_REASON_CHARS,
    MIN_EVIDENCE_QUOTE_CHARS,
    PERSPECTIVES,
    PlacementCandidate,
    TARGET_MIN_PLACEMENTS,
    UnplacedDomain,
    UNPARSED_WEIGHT,
    ValidationIssue,
)


class LandscapePlacementValidator:
    def __init__(self, input_builder: LandscapePlacementInputBuilder | None = None) -> None:
        self._input_builder = input_builder or LandscapePlacementInputBuilder()

    def validate(
        self,
        raw: dict,
        item: LandscapePlacementInput,
        *,
        cartridge_id: str | None = None,
    ) -> tuple[LandscapePlacementResult | None, list[str], list[str]]:
        issues: list[ValidationIssue] = []
        errors: list[str] = []
        warnings: list[str] = []

        def error(rule_id: str, message: str, field: str | None = None) -> None:
            issues.append(ValidationIssue(rule_id, "error", message, field))
            errors.append(f"{rule_id}: {message}")

        def warn(rule_id: str, message: str, field: str | None = None) -> None:
            issues.append(ValidationIssue(rule_id, "warning", message, field))
            warnings.append(f"{rule_id}: {message}")

        if not isinstance(raw, dict):
            error("output_not_object", "LLM output is not a JSON object")
            return None, errors, warnings

        raw_placements = raw.get("placements")
        if raw_placements is None or not isinstance(raw_placements, list):
            error("placements_missing", "output has no 'placements' array")
            return None, errors, warnings

        unplaced = self._collect_unplaced(raw.get("unplaced_domains"), item, warn)

        node_index = item.node_index()
        known_domains = set(item.domain_keys())
        known_claim_ids = item.claim_ids()
        haystack = self._input_builder.quote_haystack(item)

        accepted: list[PlacementCandidate] = []
        seen_keys: set[tuple[str, str, str]] = set()

        for index, entry in enumerate(raw_placements):
            field = f"placements[{index}]"
            if not isinstance(entry, dict):
                error("placement_not_object", f"{field} is not an object", field)
                continue
            placement = PlacementCandidate.from_dict(entry)

            if not placement.domain_key:
                error("placement_domain_missing", f"{field} has no domain_key", field)
                continue
            if placement.domain_key not in known_domains:
                error(
                    "placement_unknown_domain",
                    f"{field} domain_key {placement.domain_key!r} was not offered "
                    f"(choose one of: {', '.join(sorted(known_domains)) or 'none'})",
                    field,
                )
                continue
            if not placement.node_id:
                error("placement_node_missing", f"{field} has no node_id", field)
                continue
            if (placement.domain_key, placement.node_id) not in node_index:
                error(
                    "placement_unknown_node",
                    f"{field} node_id {placement.node_id!r} does not exist in domain "
                    f"{placement.domain_key!r} (use only the node_id values supplied; "
                    "do not invent anchors)",
                    field,
                )
                continue

            if placement.perspective not in PERSPECTIVES:
                error(
                    "placement_unknown_perspective",
                    f"{field} perspective {placement.perspective!r} is outside the "
                    f"vocabulary ({', '.join(PERSPECTIVES)})",
                    field,
                )

            if placement.weight == UNPARSED_WEIGHT or not (
                0.0 <= float(placement.weight) <= 1.0
            ):
                error(
                    "placement_weight_invalid",
                    f"{field} weight {entry.get('weight')!r} must be a number in [0,1]",
                    field,
                )

            if not placement.reason:
                error(
                    "placement_reason_missing",
                    f"{field} has no reason (every placement must say why the paper "
                    "relates to the anchor)",
                    field,
                )
            elif len(placement.reason) > MAX_REASON_CHARS:
                error(
                    "placement_reason_too_long",
                    f"{field} reason is {len(placement.reason)} chars "
                    f"(max {MAX_REASON_CHARS})",
                    field,
                )

            self._check_quote(placement, field, haystack, error)

            if placement.claim_id and placement.claim_id not in known_claim_ids:
                error(
                    "placement_unknown_claim",
                    f"{field} claim_id {placement.claim_id!r} was not supplied "
                    "(use null when the quote comes from the paper-level text)",
                    field,
                )

            key = placement.key()
            if key in seen_keys:
                warn(
                    "placement_duplicate_key",
                    f"{field} duplicates (domain_key, node_id, perspective)="
                    f"{key}; the first occurrence is kept",
                    field,
                )
                continue
            seen_keys.add(key)
            accepted.append(placement)

        if errors:
            return None, errors, warnings

        if not accepted and item.has_material() and not unplaced:
            # LS10: 「置けない」は正当な答えだが、**黙って空**は答えではない。
            error(
                "no_placement_and_no_unplaced_declaration",
                "no usable placement was produced and no unplaced_domains reason was "
                "declared; if the paper does not fit any anchor, say so per domain",
            )
            return None, errors, warnings

        placements, truncated, truncated_count, cap_note = self._apply_cap(
            accepted, item.max_placements, warn
        )

        if placements and len(placements) < TARGET_MIN_PLACEMENTS:
            # 本当に1領域だけの論文もある。捨てずに残す（P4）。
            warn(
                "fewer_placements_than_target",
                f"only {len(placements)} placement(s) produced (design target is "
                f"{TARGET_MIN_PLACEMENTS}+ across domains/perspectives)",
            )

        return (
            LandscapePlacementResult(
                document_id=item.document_id,
                placements=placements,
                unplaced_domains=unplaced,
                cartridge_id=cartridge_id or item.cartridge_id,
                placement_version=LANDSCAPE_PLACEMENT_VERSION,
                truncated=truncated,
                truncated_count=truncated_count,
                skipped_reason=None,
                review_notes=[cap_note] if cap_note else [],
                validation_issues=issues,
            ),
            [],
            warnings,
        )

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _check_quote(
        placement: PlacementCandidate,
        field: str,
        haystack: str,
        error,
    ) -> None:
        if not placement.evidence_quote:
            error(
                "placement_evidence_quote_missing",
                f"{field} has no evidence_quote (every placement must be traceable "
                "to the material)",
                field,
            )
            return
        if len(placement.evidence_quote) > MAX_EVIDENCE_QUOTE_CHARS:
            error(
                "placement_evidence_quote_too_long",
                f"{field} evidence_quote is {len(placement.evidence_quote)} chars "
                f"(max {MAX_EVIDENCE_QUOTE_CHARS})",
                field,
            )
        if len(placement.evidence_quote) < MIN_EVIDENCE_QUOTE_CHARS:
            error(
                "placement_evidence_quote_too_short",
                f"{field} evidence_quote is too short to ground anything "
                f"(min {MIN_EVIDENCE_QUOTE_CHARS} chars)",
                field,
            )
        elif normalize_for_quote_match(placement.evidence_quote) not in haystack:
            error(
                "placement_evidence_quote_not_verbatim",
                f"{field} evidence_quote does not appear verbatim in the supplied "
                "material (copy the source text, do not translate or paraphrase)",
                field,
            )

    @staticmethod
    def _collect_unplaced(
        raw_unplaced,
        item: LandscapePlacementInput,
        warn,
    ) -> list[UnplacedDomain]:
        """``unplaced_domains`` の収集（理由なしでも落とさない・P4）。"""
        if not isinstance(raw_unplaced, list):
            return []
        known = set(item.domain_keys())
        out: list[UnplacedDomain] = []
        seen: set[str] = set()
        for index, entry in enumerate(raw_unplaced):
            if not isinstance(entry, dict):
                warn(
                    "unplaced_not_object",
                    f"unplaced_domains[{index}] is not an object; ignored",
                )
                continue
            declared = UnplacedDomain.from_dict(entry)
            if not declared.domain_key or declared.domain_key in seen:
                continue
            if declared.domain_key not in known:
                warn(
                    "unplaced_unknown_domain",
                    f"unplaced_domains[{index}] names domain "
                    f"{declared.domain_key!r}, which was not offered",
                )
                continue
            if not declared.reason:
                warn(
                    "unplaced_reason_missing",
                    f"unplaced_domains[{index}] ({declared.domain_key}) has no reason",
                )
            seen.add(declared.domain_key)
            out.append(declared)
        return out

    @staticmethod
    def _apply_cap(
        placements: list[PlacementCandidate],
        max_placements: int,
        warn,
    ) -> tuple[list[PlacementCandidate], bool, int, str]:
        """件数上限で決定論的に切る（weight 降順 → 入力順のタイブレーク）。"""
        cap = int(max_placements or 0)
        if cap <= 0 or len(placements) <= cap:
            return list(placements), False, 0, ""
        ordered = sorted(
            enumerate(placements), key=lambda pair: (-float(pair[1].weight), pair[0])
        )
        kept_indices = sorted(index for index, _ in ordered[:cap])
        kept = [placements[index] for index in kept_indices]
        dropped = len(placements) - len(kept)
        warn(
            "placements_over_cap",
            f"{dropped} placement(s) beyond the per-document cap ({cap}) were not "
            "kept (lowest weight first)",
        )
        note = (
            f"{dropped} placement(s) beyond the per-document cap ({cap}) were not kept"
        )
        return kept, True, dropped, note
