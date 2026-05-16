"""EvidenceRegistry / SourceSpanStore.

PDF 原文由来の Evidence を一元管理し、Claim / Equation / Figure / Component から
参照できる evidence_id を発行するモジュール。

- evidence_text には PDF 原文のみを格納する
- LLM のレビューコメントは review_note に分離する
- public export では location pointer のみを露出させる public_export_policy を持つ
"""
from .builder import EvidenceRegistryBuilder
from .schema import (
    EvidenceRecord,
    EvidenceRegistryResult,
    EvidenceSource,
    PUBLIC_EXPORT_POLICIES,
    EVIDENCE_ROLES,
)

__all__ = [
    "EvidenceRegistryBuilder",
    "EvidenceRecord",
    "EvidenceRegistryResult",
    "EvidenceSource",
    "PUBLIC_EXPORT_POLICIES",
    "EVIDENCE_ROLES",
]
