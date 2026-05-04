"""FigureTableSemanticsAgent.

Figure / Table の意味を構造化するエージェント。

DocumentStructureResult から figure_caption / table_caption block を抽出し、
caption と周辺本文から inputs / outputs / comparison_axes / interpretation /
teaching_takeaway を組み立てる。

LLM enricher を optional に差し込めるが、本実装ではキャプション主導の
決定論的ベースラインで figure / table 構造を生成する。
"""
from .agent import FigureTableSemanticsAgent
from .schema import (
    FigureRecord,
    FigureTableSemanticsResult,
    TableRecord,
)

__all__ = [
    "FigureTableSemanticsAgent",
    "FigureRecord",
    "FigureTableSemanticsResult",
    "TableRecord",
]
