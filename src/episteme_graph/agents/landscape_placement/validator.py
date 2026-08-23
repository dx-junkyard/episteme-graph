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
- **everything about ``category_gaps``**
  (``docs/features/category_gap_candidates_design.md`` §5.1): the gap section is
  optional and secondary, so :meth:`_collect_category_gaps` is a *soft
  collector* — it never appends to ``errors``. A malformed gap drops that gap
  alone and leaves the placements (and the other gaps) untouched. Making gaps a
  hard error would send the whole output into the repair loop and, after two
  failures, wipe out every placement (design §1-6).
"""
from __future__ import annotations

from .input_builder import LandscapePlacementInputBuilder, normalize_for_quote_match
from .schema import (
    CategoryGapRecord,
    GAP_LAYER_CONCEPT,
    GAP_LAYER_REGION,
    GAP_LAYERS,
    LANDSCAPE_PLACEMENT_VERSION,
    LandscapePlacementInput,
    LandscapePlacementResult,
    MAX_EVIDENCE_QUOTE_CHARS,
    MAX_PROPOSED_LABEL_CHARS,
    MAX_REASON_CHARS,
    MIN_EVIDENCE_QUOTE_CHARS,
    normalize_gap_label,
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

        # 候補は placements と独立に収集する（warning のみ・失敗は当該候補のみ drop）。
        category_gaps = self._collect_category_gaps(
            raw.get("category_gaps"), item, haystack, warn
        )

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
                category_gaps=category_gaps,
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
    def _collect_category_gaps(
        raw_gaps,
        item: LandscapePlacementInput,
        haystack: str,
        warn,
    ) -> list[CategoryGapRecord]:
        """``category_gaps`` の収集（**warning-only の soft collector**）。

        設計書 ``category_gap_candidates_design.md`` §5.1 / §1-6: 候補は任意・
        副次的な出力なので、ここで hard error を積んではならない
        （積むと ``if errors: return None`` → repair 2回失敗 → **placements が全滅**
        する）。この関数は ``errors`` に触らず、``warn`` だけを使う。違反は
        **その候補のみ** drop し、他の候補と placements は無傷で残す。

        drop する条件（いずれも warning）:

        - ``layer`` が語彙外 / ``domain_key`` が未提示 / ``proposed_label`` が空・長すぎ
        - ``layer='concept'`` で ``parent_region_id`` が空（親のない概念は置き場所が無い）
        - ``reason`` が空、``evidence_quote`` が空・短すぎ・長すぎ・**逐語でない**（LS4）
        - 既存の領域・概念と正規化ラベルが一致する（＝言い換えの申告）
        - ``max_gaps_per_document`` の超過分（入力順で先勝ち）

        保持する条件（warning のみ・情報を落とさない）:

        - ``parent_region_id`` が実在しない（設計書 §5.1「実在検査は warning」）
        - ``layer='region'`` に ``parent_region_id`` が付いていた場合は空文字へ正規化
        - ``reason`` が長すぎる
        """
        if raw_gaps is None:
            return []
        if not isinstance(raw_gaps, list):
            warn(
                "category_gaps_not_list",
                "'category_gaps' is not an array; ignored (placements are unaffected)",
            )
            return []

        try:
            cap = max(0, int(item.max_gaps_per_document))
        except (TypeError, ValueError):
            cap = 0

        known_domains = set(item.domain_keys())
        region_index = item.region_index()
        existing_labels = item.existing_labels()

        out: list[CategoryGapRecord] = []
        seen: set[tuple[str, str, str]] = set()

        for index, entry in enumerate(raw_gaps):
            field = f"category_gaps[{index}]"
            if not isinstance(entry, dict):
                warn("category_gap_not_object", f"{field} is not an object; dropped")
                continue
            gap = CategoryGapRecord.from_dict(entry)

            if gap.layer not in GAP_LAYERS:
                warn(
                    "category_gap_unknown_layer",
                    f"{field} layer {gap.layer!r} is outside the vocabulary "
                    f"({', '.join(GAP_LAYERS)}); dropped",
                    field,
                )
                continue
            if not gap.domain_key or gap.domain_key not in known_domains:
                warn(
                    "category_gap_unknown_domain",
                    f"{field} domain_key {gap.domain_key!r} was not offered; dropped",
                    field,
                )
                continue
            if not gap.proposed_label:
                warn(
                    "category_gap_label_missing",
                    f"{field} has no proposed_label; dropped",
                    field,
                )
                continue
            if len(gap.proposed_label) > MAX_PROPOSED_LABEL_CHARS:
                warn(
                    "category_gap_label_too_long",
                    f"{field} proposed_label is {len(gap.proposed_label)} chars "
                    f"(max {MAX_PROPOSED_LABEL_CHARS}); dropped",
                    field,
                )
                continue

            if gap.layer == GAP_LAYER_CONCEPT:
                if not gap.parent_region_id:
                    warn(
                        "category_gap_parent_missing",
                        f"{field} is a concept candidate without parent_region_id; "
                        "dropped",
                        field,
                    )
                    continue
                if (gap.domain_key, gap.parent_region_id) not in region_index:
                    # 実在検査は warning にとどめる（設計書 §5.1）。教員のレビューで
                    # 親を選び直せるので、候補ごと捨てない。
                    warn(
                        "category_gap_unknown_parent",
                        f"{field} parent_region_id {gap.parent_region_id!r} does not "
                        f"exist in domain {gap.domain_key!r}; kept for review",
                        field,
                    )
            elif gap.layer == GAP_LAYER_REGION and gap.parent_region_id:
                warn(
                    "category_gap_parent_ignored",
                    f"{field} is a region candidate but carries parent_region_id "
                    f"{gap.parent_region_id!r}; normalized to an empty string",
                    field,
                )
                gap.parent_region_id = ""

            if not gap.reason:
                warn(
                    "category_gap_reason_missing",
                    f"{field} has no reason; dropped",
                    field,
                )
                continue
            if len(gap.reason) > MAX_REASON_CHARS:
                warn(
                    "category_gap_reason_too_long",
                    f"{field} reason is {len(gap.reason)} chars "
                    f"(max {MAX_REASON_CHARS}); kept",
                    field,
                )

            if not gap.evidence_quote:
                warn(
                    "category_gap_evidence_quote_missing",
                    f"{field} has no evidence_quote; dropped",
                    field,
                )
                continue
            if len(gap.evidence_quote) < MIN_EVIDENCE_QUOTE_CHARS:
                warn(
                    "category_gap_evidence_quote_too_short",
                    f"{field} evidence_quote is too short to ground anything "
                    f"(min {MIN_EVIDENCE_QUOTE_CHARS} chars); dropped",
                    field,
                )
                continue
            if len(gap.evidence_quote) > MAX_EVIDENCE_QUOTE_CHARS:
                warn(
                    "category_gap_evidence_quote_too_long",
                    f"{field} evidence_quote is {len(gap.evidence_quote)} chars "
                    f"(max {MAX_EVIDENCE_QUOTE_CHARS}); dropped",
                    field,
                )
                continue
            if normalize_for_quote_match(gap.evidence_quote) not in haystack:
                warn(
                    "category_gap_evidence_quote_not_verbatim",
                    f"{field} evidence_quote does not appear verbatim in the supplied "
                    "material; dropped",
                    field,
                )
                continue

            normalized_label = normalize_gap_label(gap.proposed_label)
            if normalized_label in existing_labels.get(gap.domain_key, set()):
                warn(
                    "category_gap_duplicates_existing_label",
                    f"{field} proposed_label {gap.proposed_label!r} already exists in "
                    f"domain {gap.domain_key!r} (a rewording of an existing node is "
                    "not a new category); dropped",
                    field,
                )
                continue

            key = gap.key()
            if key in seen:
                warn(
                    "category_gap_duplicate",
                    f"{field} duplicates (domain_key, parent_region_id, label)={key}; "
                    "the first occurrence is kept",
                    field,
                )
                continue

            if len(out) >= cap:
                warn(
                    "category_gaps_over_cap",
                    f"{field} is beyond the per-document candidate cap ({cap}); "
                    "dropped",
                    field,
                )
                continue

            seen.add(key)
            out.append(gap)
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
