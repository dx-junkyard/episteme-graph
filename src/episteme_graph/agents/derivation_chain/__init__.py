"""DerivationChainAgent.

EquationSemanticsAgent の出力（local derivation_links）を統合し、
式から式への導出階段（derivation chain）を構造化する。

教材生成では「なぜこの式になるのか」を説明する必要があるため、
operation / assumption_refs / required_claim_ids を明示的に保持する。
"""
from .agent import DerivationChainAgent
from .schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)

__all__ = [
    "DerivationChainAgent",
    "DerivationChainRecord",
    "DerivationChainResult",
    "DerivationStep",
]
