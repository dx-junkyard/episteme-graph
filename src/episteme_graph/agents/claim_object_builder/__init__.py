"""ClaimObjectBuilder.

ClaimQualificationAgent が出した QualifiedSpan / EvidenceRegistry / EquationSemantics を
入力に、最終的な claims/claims.json を組み立てる決定論的ビルダー。

責務:
- 1 Claim = 1 主張を保証する
- claim_type を確定する
- concepts を必須化する
- equation_ids / figure_ids / evidence_ids を接続する
- evidence_text を直接持たない（source_evidence_ids 経由）
- LLM コメントは review_note に分離する
"""
from .builder import ClaimObjectBuilder
from .schema import (
    ClaimConcept,
    ClaimObjectRecord,
    ClaimObjectBuildResult,
    ATOMICITY_VALUES,
    SUPPORT_STATUSES,
    REVIEW_STATUSES,
    CONCEPT_ASSIGNMENT_STATUSES,
)

__all__ = [
    "ClaimObjectBuilder",
    "ClaimConcept",
    "ClaimObjectRecord",
    "ClaimObjectBuildResult",
    "ATOMICITY_VALUES",
    "SUPPORT_STATUSES",
    "REVIEW_STATUSES",
    "CONCEPT_ASSIGNMENT_STATUSES",
]
