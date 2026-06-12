"""Validation for ClaimQualificationAgent outputs."""
from __future__ import annotations

import re

from .context_lint import find_unresolved_reference
from .schema import (
    ATOMIC_CLAIM_ATOMICITY,
    ATOMIC_CLAIM_LEADING_CONNECTORS,
    ATOMIC_CLAIM_STATUSES,
    CLAIM_TIERS,
    CORE_CLAIM_TYPE_FALLBACKS,
    EVIDENCE_ADEQUACY,
    EXCLUSION_ROLE_LABELS,
    GRANULARITIES,
    QUALIFICATION_STATUSES,
    REVIEWABILITY,
    CartridgeContext,
    ClaimQualificationResult,
    QualifiedSpanRecord,
    ValidationIssue,
)


class ClaimQualificationValidator:
    def validate(
        self,
        result: ClaimQualificationResult,
        cartridge: CartridgeContext | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        records = self._all_records(result)
        if not records:
            issues.append(ValidationIssue(
                rule_id="no_qualified_records",
                severity="error",
                message="No accepted/rejected/deferred records are present",
                field="qualified_spans",
            ))
            return issues

        issues += self._check_required_vocabularies(records)
        issues += self._check_confidence(records)
        issues += self._check_consistency(records)
        issues += self._check_claim_type_candidates(records, cartridge)
        issues += self._check_atomic_claims(records)
        return issues

    def _all_records(self, result: ClaimQualificationResult) -> list[QualifiedSpanRecord]:
        records = list(result.qualified_spans)
        records.extend(self._dict_to_record(r) for r in result.rejected_spans)
        records.extend(self._dict_to_record(r) for r in result.deferred_spans)
        return records

    @staticmethod
    def _dict_to_record(raw: dict) -> QualifiedSpanRecord:
        qualification = raw.get("qualification")
        if not isinstance(qualification, dict):
            qualification = {
                "status": raw.get("status", "rejected"),
                "claim_tier": raw.get("claim_tier", "meta"),
                "claim_type_candidate": raw.get("claim_type_candidate", "unknown"),
                "granularity": raw.get("granularity", "too_narrow"),
                "evidence_adequacy": raw.get("evidence_adequacy", "weak"),
                "reviewability": raw.get("reviewability", "moderate"),
            }
        return QualifiedSpanRecord(
            span_id=raw.get("span_id", ""),
            block_id=raw.get("block_id", ""),
            section_id=raw.get("section_id"),
            text=raw.get("text", ""),
            role_labels=list(raw.get("role_labels", [])),
            qualification=qualification,
            edit_suggestions=raw.get("edit_suggestions", {}),
            reason=raw.get("reason", ""),
            confidence=float(raw.get("confidence", 0.0)),
            atomic_claims=list(raw.get("atomic_claims", []) or []),
        )

    def _check_required_vocabularies(
        self, records: list[QualifiedSpanRecord]
    ) -> list[ValidationIssue]:
        checks = [
            ("status", QUALIFICATION_STATUSES),
            ("claim_tier", CLAIM_TIERS),
            ("granularity", GRANULARITIES),
            ("evidence_adequacy", EVIDENCE_ADEQUACY),
            ("reviewability", REVIEWABILITY),
        ]
        issues = []
        for record in records:
            for field, allowed in checks:
                value = record.qualification.get(field)
                if value not in allowed:
                    issues.append(ValidationIssue(
                        rule_id=f"invalid_{field}",
                        severity="error",
                        message=f"{record.span_id} has invalid {field}={value!r}",
                        field=f"{record.span_id}.qualification.{field}",
                    ))
        return issues

    def _check_confidence(
        self, records: list[QualifiedSpanRecord]
    ) -> list[ValidationIssue]:
        issues = []
        for record in records:
            if not (0.0 <= record.confidence <= 1.0):
                issues.append(ValidationIssue(
                    rule_id="confidence_out_of_range",
                    severity="error",
                    message=f"{record.span_id} confidence={record.confidence} is out of range",
                    field=f"{record.span_id}.confidence",
                ))
        return issues

    def _check_consistency(
        self, records: list[QualifiedSpanRecord]
    ) -> list[ValidationIssue]:
        issues = []
        for record in records:
            q = record.qualification
            status = q.get("status")
            tier = q.get("claim_tier")
            granularity = q.get("granularity")
            evidence = q.get("evidence_adequacy")
            suggestions = record.edit_suggestions or {}
            roles = set(record.role_labels)

            if status == "accepted" and tier == "meta":
                issues.append(ValidationIssue(
                    rule_id="accepted_meta_claim",
                    severity="warning",
                    message=f"{record.span_id} is accepted with claim_tier=meta",
                    field=f"{record.span_id}.qualification.claim_tier",
                ))
            if status == "accepted" and evidence == "broken":
                issues.append(ValidationIssue(
                    rule_id="accepted_broken_evidence",
                    severity="warning",
                    message=f"{record.span_id} is accepted despite broken evidence",
                    field=f"{record.span_id}.qualification.evidence_adequacy",
                ))
            if granularity == "too_broad" and not suggestions.get("should_split"):
                issues.append(ValidationIssue(
                    rule_id="too_broad_without_split",
                    severity="warning",
                    message=f"{record.span_id} is too_broad without split suggestion",
                    field=f"{record.span_id}.edit_suggestions.should_split",
                ))
            if (
                granularity == "too_narrow"
                and status == "accepted"
                and not suggestions.get("should_merge_with_prev")
                and not suggestions.get("should_merge_with_next")
            ):
                issues.append(ValidationIssue(
                    rule_id="too_narrow_accepted_without_merge",
                    severity="warning",
                    message=f"{record.span_id} is accepted though too_narrow without merge suggestion",
                    field=f"{record.span_id}.qualification.granularity",
                ))
            if "prior_work" in roles and tier == "paper_core":
                issues.append(ValidationIssue(
                    rule_id="prior_work_as_paper_core",
                    severity="error",
                    message=f"{record.span_id} has prior_work role but claim_tier=paper_core",
                    field=f"{record.span_id}.qualification.claim_tier",
                ))
            if roles & EXCLUSION_ROLE_LABELS and status == "accepted" and tier in ("paper_core", "paper_supporting"):
                issues.append(ValidationIssue(
                    rule_id="exclusion_role_accepted_as_core",
                    severity="warning",
                    message=f"{record.span_id} has exclusion role(s) accepted as core/supporting",
                    field=f"{record.span_id}.qualification",
                ))
        return issues

    def _check_claim_type_candidates(
        self,
        records: list[QualifiedSpanRecord],
        cartridge: CartridgeContext | None,
    ) -> list[ValidationIssue]:
        allowed = set(CORE_CLAIM_TYPE_FALLBACKS)
        if cartridge:
            allowed.update(self._cartridge_claim_types(cartridge))
        issues = []
        for record in records:
            value = record.qualification.get("claim_type_candidate")
            if value not in allowed:
                issues.append(ValidationIssue(
                    rule_id="invalid_claim_type_candidate",
                    severity="error",
                    message=f"{record.span_id} has unsupported claim_type_candidate={value!r}",
                    field=f"{record.span_id}.qualification.claim_type_candidate",
                ))
        return issues

    def _check_atomic_claims(
        self, records: list[QualifiedSpanRecord]
    ) -> list[ValidationIssue]:
        """Validate LLM atomic-rewrite output (issue #317, criteria #2 / #3 / #9)."""
        issues: list[ValidationIssue] = []
        for record in records:
            atomic_claims = getattr(record, "atomic_claims", None) or []
            suggestions = record.edit_suggestions or {}
            granularity = record.qualification.get("granularity")

            # too_broad / should_split spans should carry atomic claims (criterion #1).
            if (
                record.qualification.get("status") == "accepted"
                and (granularity == "too_broad" or suggestions.get("should_split"))
                and not atomic_claims
            ):
                issues.append(ValidationIssue(
                    rule_id="split_required_without_atomic_claims",
                    severity="warning",
                    message=(
                        f"{record.span_id} is marked for splitting but has no atomic_claims"
                    ),
                    field=f"{record.span_id}.atomic_claims",
                ))

            for idx, claim in enumerate(atomic_claims):
                path = f"{record.span_id}.atomic_claims[{idx}]"
                if not isinstance(claim, dict):
                    issues.append(ValidationIssue(
                        rule_id="atomic_claim_not_object",
                        severity="error",
                        message=f"{path} is not an object",
                        field=path,
                    ))
                    continue
                text = str(claim.get("text", "")).strip()
                if not text:
                    issues.append(ValidationIssue(
                        rule_id="atomic_claim_empty_text",
                        severity="error",
                        message=f"{path} has empty text",
                        field=path,
                    ))
                    continue
                atomicity = str(claim.get("atomicity", "atomic"))
                if atomicity not in ATOMIC_CLAIM_ATOMICITY:
                    issues.append(ValidationIssue(
                        rule_id="invalid_atomic_claim_atomicity",
                        severity="error",
                        message=f"{path} has invalid atomicity={atomicity!r}",
                        field=f"{path}.atomicity",
                    ))
                status = str(claim.get("status", "accepted"))
                if status not in ATOMIC_CLAIM_STATUSES:
                    issues.append(ValidationIssue(
                        rule_id="invalid_atomic_claim_status",
                        severity="error",
                        message=f"{path} has invalid status={status!r}",
                        field=f"{path}.status",
                    ))
                # criterion #3: a confirmed atomic claim must be a standalone
                # proposition — no leading connector and not a bare fragment.
                if atomicity == "atomic" and status == "accepted":
                    first = re.findall(r"[A-Za-z]+", text)
                    if first and first[0].lower() in ATOMIC_CLAIM_LEADING_CONNECTORS:
                        issues.append(ValidationIssue(
                            rule_id="atomic_claim_starts_with_connector",
                            severity="error",
                            message=(
                                f"{path} starts with connector {first[0]!r}; "
                                "an atomic claim must be a standalone proposition"
                            ),
                            field=f"{path}.text",
                        ))
                    if len(text.split()) < 3:
                        issues.append(ValidationIssue(
                            rule_id="atomic_claim_too_short",
                            severity="warning",
                            message=f"{path} is too short to be a full proposition",
                            field=f"{path}.text",
                        ))
                    # criterion #5: confirmed atomic claims must stay traceable.
                    if not str(claim.get("source_span_id", "")).strip() and not str(
                        claim.get("evidence_quote", "")
                    ).strip():
                        issues.append(ValidationIssue(
                            rule_id="atomic_claim_missing_source",
                            severity="warning",
                            message=(
                                f"{path} has no source_span_id or evidence_quote; "
                                "it cannot be traced back to the source"
                            ),
                            field=path,
                        ))
                    # issue #357: an accepted atomic claim must be context-free.
                    # The parse-time lint demotes these; this is the safety net
                    # for reloaded artifacts that bypassed the lint.
                    fragment = find_unresolved_reference(
                        str(claim.get("normalized_text") or text)
                    )
                    if fragment:
                        issues.append(ValidationIssue(
                            rule_id="atomic_claim_unresolved_reference",
                            severity="warning",
                            message=(
                                f"{path} starts with unresolved reference "
                                f"{fragment!r}; it cannot stand alone outside "
                                "its original context"
                            ),
                            field=f"{path}.text",
                        ))
        return issues

    @staticmethod
    def _cartridge_claim_types(cartridge: CartridgeContext) -> set[str]:
        types: set[str] = set()
        for key in ("claim_types", "component_types", "concept_types"):
            raw = cartridge.ontology.get(key)
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str):
                        types.add(item)
                    elif isinstance(item, dict):
                        for name_key in ("id", "type", "name", "canonical"):
                            if item.get(name_key):
                                types.add(str(item[name_key]))
            elif isinstance(raw, dict):
                types.update(str(k) for k in raw.keys())
        if cartridge.aliases:
            types.update(cartridge.aliases.keys())
        return types
