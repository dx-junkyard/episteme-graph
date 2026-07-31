"""Validation for DiscussOpeningAgent output.

Design: ``docs/features/discuss_opening_authoring_design.md`` §4.1 / §8
(Phase 1 guardrails). The validate signature matches
``core/llm_worker/repair.run_with_repair``'s contract:
``validate(raw) -> (result | None, errors, warnings)``.

Hard errors (they force a repair attempt; 2 failures ⇒ nothing is generated —
we would rather deliver nothing than deliver a fabricated discussion starter):

- ``seeds`` missing / not a list, or every entry unusable.
- ``body`` empty, over the length cap, or **not a question** (must end with
  ``？`` / ``?``) — the artifact is defined as "立場を求める問い".
- ``body`` not written in the requested language (``ja``: must contain at least
  one Japanese character).
- ``evidence_quote`` empty, too short, or **not verbatim** in the supplied
  material (whitespace-normalized containment). This is the grounding guard:
  DM1 "論文の内容として提示するものは論文まで辿れること".
- Forbidden vocabulary (the D-layer denylist — 「疑え」「ノーベル賞」…) anywhere
  in ``body`` / ``reason``.
- ``confidence`` out of ``[0, 1]``.

Warnings (kept, never block): fewer seeds than the design's 2〜3 target,
unknown ``grounded_in`` value, more thesis-only seeds than expected.
"""
from __future__ import annotations

import re

from .input_builder import DiscussOpeningInputBuilder, normalize_for_quote_match
from .schema import (
    DISCUSS_OPENING_VERSION,
    DiscussOpeningInput,
    DiscussOpeningResult,
    DiscussionSeed,
    FORBIDDEN_PHRASES,
    GROUNDING_ASSUMPTION,
    GROUNDING_AUTHOR_CHOICE,
    GROUNDING_THESIS,
    MAX_EVIDENCE_QUOTE_CHARS,
    MAX_SEED_BODY_CHARS,
    MIN_EVIDENCE_QUOTE_CHARS,
    TARGET_MIN_SEEDS,
    ValidationIssue,
)

_QUESTION_SUFFIXES = ("？", "?")
_JA_CHAR_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
_GROUNDING_VOCAB = (GROUNDING_ASSUMPTION, GROUNDING_AUTHOR_CHOICE, GROUNDING_THESIS)


class DiscussOpeningValidator:
    def __init__(self, input_builder: DiscussOpeningInputBuilder | None = None) -> None:
        self._input_builder = input_builder or DiscussOpeningInputBuilder()

    def validate(
        self,
        raw: dict,
        item: DiscussOpeningInput,
        *,
        cartridge_id: str | None = None,
    ) -> tuple[DiscussOpeningResult | None, list[str], list[str]]:
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

        raw_seeds = raw.get("seeds")
        skipped_reason = str(raw.get("skipped_reason") or "").strip() or None

        if raw_seeds is None or not isinstance(raw_seeds, list):
            error("seeds_missing", "output has no 'seeds' array")
            return None, errors, warnings

        if not raw_seeds:
            # 素材が無いなら「生成しない」が正しい（設計書 §4.1 の縮退）。
            # 素材があるのに空で返してきた場合は repair 対象にする。
            if not item.has_material():
                return (
                    DiscussOpeningResult.make_skipped(
                        item,
                        skipped_reason or "no_source_material",
                        "LLM reported no material to work from",
                        cartridge_id=cartridge_id,
                    ),
                    [],
                    warnings,
                )
            error(
                "empty_seeds_with_material",
                "seeds is empty although untested assumptions / author choices "
                "were provided",
            )
            return None, errors, warnings

        haystack = self._input_builder.quote_haystack(item)
        seeds: list[DiscussionSeed] = []
        thesis_only = 0

        for index, entry in enumerate(raw_seeds):
            field = f"seeds[{index}]"
            if not isinstance(entry, dict):
                error("seed_not_object", f"{field} is not an object", field)
                continue
            seed = DiscussionSeed.from_dict(entry)

            if not seed.body:
                error("seed_body_empty", f"{field} has an empty body", field)
                continue
            if len(seed.body) > MAX_SEED_BODY_CHARS:
                error(
                    "seed_body_too_long",
                    f"{field} body is {len(seed.body)} chars "
                    f"(max {MAX_SEED_BODY_CHARS})",
                    field,
                )
            if not seed.body.endswith(_QUESTION_SUFFIXES):
                error(
                    "seed_not_a_question",
                    f"{field} body must be a question ending with ？ or ? "
                    "(a discussion seed asks the learner for a position)",
                    field,
                )
            if item.language == "ja" and not _JA_CHAR_RE.search(seed.body):
                error(
                    "seed_language_mismatch",
                    f"{field} body must be written in Japanese "
                    "(language='ja')",
                    field,
                )

            for phrase in FORBIDDEN_PHRASES:
                if phrase in seed.body or phrase in seed.reason:
                    error(
                        "forbidden_phrase",
                        f"{field} contains forbidden phrase {phrase!r} "
                        "(do not provoke or command the learner)",
                        field,
                    )

            if not seed.reason:
                error("seed_reason_missing", f"{field} has no reason", field)

            if not seed.evidence_quote:
                error(
                    "seed_evidence_quote_missing",
                    f"{field} has no evidence_quote (every seed must be traceable "
                    "to the material)",
                    field,
                )
            else:
                if len(seed.evidence_quote) > MAX_EVIDENCE_QUOTE_CHARS:
                    error(
                        "seed_evidence_quote_too_long",
                        f"{field} evidence_quote is {len(seed.evidence_quote)} chars "
                        f"(max {MAX_EVIDENCE_QUOTE_CHARS})",
                        field,
                    )
                if len(seed.evidence_quote) < MIN_EVIDENCE_QUOTE_CHARS:
                    error(
                        "seed_evidence_quote_too_short",
                        f"{field} evidence_quote is too short to ground anything "
                        f"(min {MIN_EVIDENCE_QUOTE_CHARS} chars)",
                        field,
                    )
                elif normalize_for_quote_match(seed.evidence_quote) not in haystack:
                    error(
                        "seed_evidence_quote_not_verbatim",
                        f"{field} evidence_quote does not appear verbatim in the "
                        "supplied material (copy the source text, do not translate "
                        "or paraphrase)",
                        field,
                    )

            if not 0.0 <= float(seed.confidence) <= 1.0:
                error(
                    "seed_confidence_out_of_range",
                    f"{field} confidence {seed.confidence} out of [0,1]",
                    field,
                )

            if seed.grounded_in and seed.grounded_in not in _GROUNDING_VOCAB:
                warn(
                    "seed_unknown_grounding",
                    f"{field} grounded_in {seed.grounded_in!r} is outside the known "
                    "vocabulary",
                    field,
                )
            if seed.grounded_in == GROUNDING_THESIS:
                thesis_only += 1

            seeds.append(seed)

        if errors:
            return None, errors, warnings

        if not seeds:
            error("no_usable_seed", "no usable seed remained after validation")
            return None, errors, warnings

        if len(seeds) < TARGET_MIN_SEEDS:
            # 設計書は 2〜3 件を狙うが、1件しか根拠が無いこともある。捨てずに残す（P4）。
            warn(
                "fewer_seeds_than_target",
                f"only {len(seeds)} seed(s) produced (design target is "
                f"{TARGET_MIN_SEEDS}+)",
            )
        if thesis_only > 1:
            warn(
                "too_many_thesis_only_seeds",
                f"{thesis_only} seeds are grounded only in the thesis "
                "(the main fuel should be untested assumptions / author choices)",
            )

        return (
            DiscussOpeningResult(
                document_id=item.document_id,
                seeds=seeds,
                language=item.language,
                cartridge_id=cartridge_id,
                opening_version=DISCUSS_OPENING_VERSION,
                skipped_reason=None,
                validation_issues=issues,
            ),
            [],
            warnings,
        )
