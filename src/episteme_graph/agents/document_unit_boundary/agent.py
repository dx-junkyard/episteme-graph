"""DocumentUnitBoundaryAgent: PDF 内の分析対象単位を block-level で確定するエージェント。

design: heuristic-first / cartridge-aware (not cartridge-dependent)
- LLM は使用しない（MVP）
- DocumentStructureResult を入力とし、DocumentUnitBoundaryResult を返す
- TypedBlock.raw に unit annotation を付与する（オプション）
- cartridge がなくても単独で動作する
- pipeline: DocumentStructureAgent → (this) → SourceChunking
"""
from __future__ import annotations

from episteme_graph.agents.document_structure.schema import (
    DocumentStructureResult,
    TypedBlock,
)

from .cartridge_loader import CartridgeLoader
from .detector import UnitDetector
from .schema import (
    CartridgeContext,
    DocumentUnit,
    DocumentUnitBoundaryResult,
)
from .validator import BoundaryValidator


class DocumentUnitBoundaryAgent:
    """DocumentStructureResult を受け取り DocumentUnitBoundaryResult を返すエージェント。

    Parameters
    ----------
    cartridge_base_dir:
        カートリッジの検索ベースディレクトリ。None の場合は CartridgeLoader のデフォルト。
    annotate_blocks:
        True の場合、TypedBlock.raw に unit annotation を付与する。
    """

    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        annotate_blocks: bool = True,
    ) -> None:
        self._detector = UnitDetector()
        self._validator = BoundaryValidator()
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._annotate_blocks = annotate_blocks

    def run(
        self,
        structure: DocumentStructureResult,
        cartridge_id: str | None = None,
        target_unit_id: str | None = None,
    ) -> DocumentUnitBoundaryResult:
        """DocumentStructureResult から DocumentUnitBoundaryResult を生成する。

        Parameters
        ----------
        structure:
            DocumentStructureAgent の出力。
        cartridge_id:
            使用するカートリッジ ID。None の場合はカートリッジなしで動作。
        target_unit_id:
            明示指定する対象 unit ID。None の場合は自動選択。
        """
        # Step 1: Load cartridge (optional)
        cartridge: CartridgeContext | None = None
        if cartridge_id:
            try:
                cartridge = self._cartridge_loader.load(cartridge_id)
            except FileNotFoundError:
                pass

        # Step 2: Detect units
        units, excluded_blocks, overall_confidence, review_reasons = (
            self._detector.detect(
                structure.blocks,
                structure.document_id,
                cartridge=cartridge,
            )
        )

        # Step 3: Determine target unit
        resolved_target_id = self._resolve_target_unit(units, target_unit_id)

        # Step 4: Build result
        result = DocumentUnitBoundaryResult(
            document_id=structure.document_id,
            source_file=structure.source_file,
            unit_detection_confidence=overall_confidence,
            needs_review=bool(review_reasons),
            review_reasons=review_reasons,
            target_unit_id=resolved_target_id,
            units=units,
            excluded_blocks=excluded_blocks,
        )

        # Step 5: Validate
        result.validation_issues = self._validator.validate(result)

        # Step 6: Annotate TypedBlock.raw (optional)
        if self._annotate_blocks:
            self._annotate_typed_blocks(structure.blocks, result)

        return result

    # ------------------------------------------------------------------
    # Target unit selection
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_target_unit(
        units: list[DocumentUnit],
        explicit_id: str | None,
    ) -> str | None:
        if not units:
            return None

        if explicit_id:
            unit_ids = {u.unit_id for u in units}
            if explicit_id in unit_ids:
                return explicit_id

        # 自動選択: confidence 最大の unit（同率なら先頭）
        best = max(units, key=lambda u: u.confidence)
        return best.unit_id

    # ------------------------------------------------------------------
    # Block annotation
    # ------------------------------------------------------------------

    def _annotate_typed_blocks(
        self,
        blocks: list[TypedBlock],
        result: DocumentUnitBoundaryResult,
    ) -> None:
        """TypedBlock.raw に unit annotation を付与する。"""
        # block_id → unit mapping
        block_to_unit: dict[str, DocumentUnit] = {}
        for unit in result.units:
            for bid in unit.included_block_ids:
                block_to_unit[bid] = unit

        # excluded block_id → reason
        excluded_reason: dict[str, str] = {
            e.block_id: e.reason for e in result.excluded_blocks
        }

        target_id = result.target_unit_id

        for block in blocks:
            unit = block_to_unit.get(block.block_id)
            if unit:
                boundary_role = self._determine_boundary_role(block, unit)
                block.raw["unit_id"] = unit.unit_id
                block.raw["unit_type"] = unit.unit_type
                block.raw["is_target_unit_block"] = (unit.unit_id == target_id)
                block.raw["boundary_role"] = boundary_role
                block.raw["exclusion_reason"] = None
            else:
                reason = excluded_reason.get(block.block_id, "outside_target_unit")
                block.raw["unit_id"] = None
                block.raw["unit_type"] = None
                block.raw["is_target_unit_block"] = False
                block.raw["boundary_role"] = "excluded"
                block.raw["exclusion_reason"] = reason

    @staticmethod
    def _determine_boundary_role(block: TypedBlock, unit: DocumentUnit) -> str:
        """block が unit 内でどの役割を持つかを返す。"""
        if block.block_id == unit.title_block_id:
            return "title"
        if block.block_type == "reference_entry":
            return "references"
        if block.block_type in ("section_heading", "subsection_heading"):
            text = block.text.strip().lower()
            if any(kw in text for kw in ("abstract", "要旨", "概要")):
                return "abstract"
            if any(kw in text for kw in ("appendix", "付録")):
                return "appendix"
            if any(kw in text for kw in ("reference", "bibliography", "参考文献", "文献")):
                return "references"
        return "body"
