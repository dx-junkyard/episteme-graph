"""FigureTableSemanticsAgent.

DocumentStructureResult から figure_caption / table_caption block を抽出し、
構造化された FigureRecord / TableRecord を組み立てる。

design:
- caption-first: caption から figure_type と comparison_axes を推定する
- 周辺 body_paragraph を inputs / outputs / interpretation の素材に使う
- LLM enricher 注入のフックを提供する（callable）
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from episteme_graph.agents.document_structure.schema import (
    DocumentStructureResult,
    TypedBlock,
)

from .schema import (
    FigureRecord,
    FigureSourceLocation,
    FigureTableSemanticsResult,
    TableRecord,
    ValidationIssue,
)
from episteme_graph.agents.figure_modes import infer_mode_from_text

_FIG_LABEL_RE = re.compile(r"^(?:Figure|Fig\.?)\s*([0-9A-Za-z\.]+)\s*[:.\-]?\s*", re.IGNORECASE)
_TBL_LABEL_RE = re.compile(r"^Table\s*([0-9A-Za-z\.]+)\s*[:.\-]?\s*", re.IGNORECASE)


class FigureTableSemanticsAgent:
    def __init__(
        self,
        *,
        figure_enricher: Optional[Callable[[FigureRecord, list[TypedBlock]], FigureRecord]] = None,
        table_enricher: Optional[Callable[[TableRecord, list[TypedBlock]], TableRecord]] = None,
    ) -> None:
        self._figure_enricher = figure_enricher
        self._table_enricher = table_enricher

    def run(
        self,
        structure: DocumentStructureResult,
        *,
        cartridge_id: str | None = None,
        evidence_index: dict[str, list[str]] | None = None,
        claim_link_index: dict[str, list[str]] | None = None,
    ) -> FigureTableSemanticsResult:
        evidence_index = evidence_index or {}
        claim_link_index = claim_link_index or {}
        figures: list[FigureRecord] = []
        tables: list[TableRecord] = []
        issues: list[ValidationIssue] = []

        block_index: dict[str, TypedBlock] = {b.block_id: b for b in structure.blocks}
        ordered = sorted(structure.blocks, key=lambda b: (b.page, b.order))

        for idx, block in enumerate(ordered):
            if block.block_type == "figure_caption":
                fig = self._build_figure_record(
                    block,
                    structure.document_id,
                    self._gather_neighbors(ordered, idx, block_index),
                    evidence_index,
                    claim_link_index,
                )
                if self._figure_enricher:
                    try:
                        fig = self._figure_enricher(fig, list(block_index.values()))
                    except Exception as exc:
                        issues.append(ValidationIssue(
                            rule_id="figure_enricher_failed",
                            severity="warning",
                            message=str(exc),
                            field=fig.figure_id,
                        ))
                figures.append(fig)
            elif block.block_type == "table_caption":
                tbl = self._build_table_record(
                    block,
                    structure.document_id,
                    self._gather_neighbors(ordered, idx, block_index),
                    evidence_index,
                    claim_link_index,
                )
                if self._table_enricher:
                    try:
                        tbl = self._table_enricher(tbl, list(block_index.values()))
                    except Exception as exc:
                        issues.append(ValidationIssue(
                            rule_id="table_enricher_failed",
                            severity="warning",
                            message=str(exc),
                            field=tbl.table_id,
                        ))
                tables.append(tbl)

        for fig in figures:
            if not fig.caption:
                issues.append(ValidationIssue(
                    rule_id="figure_caption_empty",
                    severity="warning",
                    message=f"figure {fig.figure_id} has empty caption",
                    field=fig.figure_id,
                ))
            if fig.source_location.page is None and not fig.source_location.caption_block_id:
                issues.append(ValidationIssue(
                    rule_id="figure_missing_source_location",
                    severity="warning",
                    message=f"figure {fig.figure_id} has no caption_block_id or page",
                    field=fig.figure_id,
                ))
            if not fig.inputs and not fig.outputs and not fig.comparison_axes:
                issues.append(ValidationIssue(
                    rule_id="figure_missing_inputs_outputs_axes",
                    severity="warning",
                    message=f"figure {fig.figure_id} has no inputs, outputs, or comparison_axes",
                    field=fig.figure_id,
                ))
            if not fig.linked_claim_ids:
                issues.append(ValidationIssue(
                    rule_id="figure_missing_linked_claim_ids",
                    severity="info",
                    message=f"figure {fig.figure_id} has no linked_claim_ids",
                    field=fig.figure_id,
                ))
            if not fig.teaching_takeaway:
                issues.append(ValidationIssue(
                    rule_id="figure_missing_teaching_takeaway",
                    severity="info",
                    message=f"figure {fig.figure_id} has no teaching_takeaway",
                    field=fig.figure_id,
                ))
        for tbl in tables:
            if not tbl.caption:
                issues.append(ValidationIssue(
                    rule_id="table_caption_empty",
                    severity="warning",
                    message=f"table {tbl.table_id} has empty caption",
                    field=tbl.table_id,
                ))
            if tbl.source_location.page is None and not tbl.source_location.caption_block_id:
                issues.append(ValidationIssue(
                    rule_id="table_missing_source_location",
                    severity="warning",
                    message=f"table {tbl.table_id} has no caption_block_id or page",
                    field=tbl.table_id,
                ))
            if not tbl.columns and not tbl.comparison_axes and not tbl.rows_summary:
                issues.append(ValidationIssue(
                    rule_id="table_missing_columns_or_axes",
                    severity="warning",
                    message=f"table {tbl.table_id} has no columns, comparison_axes, or rows_summary",
                    field=tbl.table_id,
                ))
            if not tbl.linked_claim_ids:
                issues.append(ValidationIssue(
                    rule_id="table_missing_linked_claim_ids",
                    severity="info",
                    message=f"table {tbl.table_id} has no linked_claim_ids",
                    field=tbl.table_id,
                ))
            if not tbl.teaching_takeaway:
                issues.append(ValidationIssue(
                    rule_id="table_missing_teaching_takeaway",
                    severity="info",
                    message=f"table {tbl.table_id} has no teaching_takeaway",
                    field=tbl.table_id,
                ))

        return FigureTableSemanticsResult(
            document_id=structure.document_id,
            cartridge_id=cartridge_id,
            figures=figures,
            tables=tables,
            validation_issues=issues,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _gather_neighbors(
        ordered: list[TypedBlock],
        idx: int,
        block_index: dict[str, TypedBlock],
        radius: int = 3,
    ) -> list[TypedBlock]:
        lo = max(0, idx - radius)
        hi = min(len(ordered), idx + radius + 1)
        return [
            b for b in ordered[lo:hi]
            if b.block_type == "body_paragraph"
        ]

    def _build_figure_record(
        self,
        block: TypedBlock,
        document_id: str,
        neighbors: list[TypedBlock],
        evidence_index: dict[str, list[str]],
        claim_link_index: dict[str, list[str]],
    ) -> FigureRecord:
        label, caption_text = self._split_label(block.text, _FIG_LABEL_RE)
        figure_id = f"fig_{label}" if label else block.block_id
        figure_type = self._infer_figure_type(caption_text)
        suggested_mode, mode_reason = infer_mode_from_text(caption_text, figure_type)
        comparison_axes = self._infer_comparison_axes(
            caption_text + " " + " ".join(b.text for b in neighbors)
        )
        return FigureRecord(
            figure_id=figure_id,
            document_id=document_id,
            figure_type=figure_type,
            source_location=FigureSourceLocation(
                page=block.page,
                caption_block_id=block.block_id,
                section_id=block.section_id,
            ),
            caption=caption_text,
            inputs=[],
            outputs=[],
            comparison_axes=comparison_axes,
            visual_elements=[],
            linked_claim_ids=list(claim_link_index.get(block.block_id, [])),
            linked_component_candidates=[],
            interpretation="",
            teaching_takeaway=self._figure_takeaway(figure_type, caption_text, comparison_axes),
            source_evidence_ids=list(evidence_index.get(block.block_id, [])),
            suggested_mode=suggested_mode,
            mode_reason=mode_reason,
        )

    def _build_table_record(
        self,
        block: TypedBlock,
        document_id: str,
        neighbors: list[TypedBlock],
        evidence_index: dict[str, list[str]],
        claim_link_index: dict[str, list[str]],
    ) -> TableRecord:
        label, caption_text = self._split_label(block.text, _TBL_LABEL_RE)
        table_id = f"table_{label}" if label else block.block_id
        table_type = self._infer_table_type(caption_text)
        comparison_axes = self._infer_comparison_axes(
            caption_text + " " + " ".join(b.text for b in neighbors)
        )
        return TableRecord(
            table_id=table_id,
            document_id=document_id,
            table_type=table_type,
            source_location=FigureSourceLocation(
                page=block.page,
                caption_block_id=block.block_id,
                section_id=block.section_id,
            ),
            caption=caption_text,
            columns=[],
            rows_summary="",
            comparison_axes=comparison_axes,
            linked_claim_ids=list(claim_link_index.get(block.block_id, [])),
            interpretation="",
            teaching_takeaway=self._table_takeaway(table_type, caption_text, comparison_axes),
            source_evidence_ids=list(evidence_index.get(block.block_id, [])),
        )

    @staticmethod
    def _figure_takeaway(
        figure_type: str, caption: str, comparison_axes: list[str]
    ) -> str:
        if not caption:
            return ""
        if figure_type == "unknown" and not comparison_axes:
            return ""
        if comparison_axes:
            return f"Use this {figure_type} to discuss {', '.join(comparison_axes)}."
        return f"Use this {figure_type} to support: {caption[:80]}".rstrip()

    @staticmethod
    def _table_takeaway(
        table_type: str, caption: str, comparison_axes: list[str]
    ) -> str:
        if not caption:
            return ""
        if table_type == "unknown" and not comparison_axes:
            return ""
        if comparison_axes:
            return f"Use this {table_type} to compare {', '.join(comparison_axes)}."
        return f"Use this {table_type} to support: {caption[:80]}".rstrip()

    @staticmethod
    def _split_label(text: str, label_re: re.Pattern) -> tuple[str | None, str]:
        if not text:
            return None, ""
        m = label_re.match(text.strip())
        if not m:
            return None, text.strip()
        label = m.group(1).strip().rstrip(".")
        rest = text.strip()[m.end():].strip()
        return label, rest

    @staticmethod
    def _infer_figure_type(caption: str) -> str:
        c = caption.lower()
        if any(w in c for w in ("distribution", "histogram")):
            return "probability_distribution"
        if any(w in c for w in ("comparison", "vs.", "versus")):
            return "comparison_plot"
        if any(w in c for w in ("schematic", "diagram")):
            return "schematic"
        if any(w in c for w in ("flow", "pipeline", "architecture")):
            return "flow_diagram"
        return "unknown"

    @staticmethod
    def _infer_table_type(caption: str) -> str:
        c = caption.lower()
        if any(w in c for w in ("comparison", "vs.")):
            return "comparison_table"
        if any(w in c for w in ("uncertainty", "error", "systematic")):
            return "uncertainty_table"
        if any(w in c for w in ("result", "summary")):
            return "results_summary"
        return "unknown"

    @staticmethod
    def _infer_comparison_axes(text: str) -> list[str]:
        axes: list[str] = []
        lower = text.lower()
        if " vs " in lower or "versus" in lower or "vs." in lower:
            axes.append("pairwise_comparison")
        if "current" in lower and "future" in lower:
            axes.append("current_vs_future")
        if "framework" in lower or "model" in lower and "comparison" in lower:
            axes.append("framework_comparison")
        return axes
