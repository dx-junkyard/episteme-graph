"""DiscussOpeningAgent: generates the opening screen's "議論のきっかけ".

Design: ``docs/features/discuss_opening_authoring_design.md`` §4.1 (Phase 1,
migration 062). LLM-first, cartridge-optional, **1 document = 1 LLM call**
(plus up to 2 repair attempts through the shared
``core/llm_worker/repair.run_with_repair`` loop).

What this agent does NOT do:

- It does not touch A-layer agents or their artifacts (OA1) — it only reads
  already-resolved text handed over by ``core/discuss/authoring.py``.
- It does not persist anything. The orchestrator writes results into
  ``element_explanations`` as ``status='candidate'`` /
  ``role='discussion_seed'`` rows (OA2 — a teacher confirms later).
- It does not invent material. A document with neither untested assumptions
  nor author choices yields ``seeds=[]`` and
  ``skipped_reason='no_source_material'`` — recorded, not hidden (OA5 / P4).
"""
from __future__ import annotations

import logging

from .cartridge_loader import CartridgeContext, CartridgeLoader
from .input_builder import DiscussOpeningInputBuilder
from .llm_client import DiscussOpeningLLMClient
from .prompt import DiscussOpeningPromptFactory
from .repair import build_repair_failed_result, build_repair_prompt
from .schema import (
    DiscussOpeningInput,
    DiscussOpeningResult,
    SKIPPED_REASON_LLM_CALL_FAILED,
    SKIPPED_REASON_NO_MATERIAL,
)
from .validator import DiscussOpeningValidator

logger = logging.getLogger(__name__)


class DiscussOpeningAgent:
    """Generate 2〜3 candidate ``discussion_seed`` questions for one document."""

    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = DiscussOpeningInputBuilder()
        self._prompt_factory = DiscussOpeningPromptFactory(self._input_builder)
        self._llm_client = DiscussOpeningLLMClient(model=llm_model)
        self._validator = DiscussOpeningValidator(self._input_builder)

    def run(
        self,
        document_input: DiscussOpeningInput | dict,
        cartridge_id: str | None = None,
        config: dict | None = None,
    ) -> DiscussOpeningResult:
        item = (
            document_input
            if isinstance(document_input, DiscussOpeningInput)
            else DiscussOpeningInput.from_dict(document_input)
        )
        cartridge = self._load_cartridge(cartridge_id)
        resolved_cartridge_id = cartridge.cartridge_id if cartridge else cartridge_id

        max_seeds = self._resolve_max_seeds(item, config)

        if not item.has_material():
            # 縮退（設計書 §4.1 / OA5）: 未検証前提も operation も無い document は
            # LLM を呼ばずに「生成しない」を理由付きで返す。
            logger.info(
                "discuss_opening: no untested assumption / author choice for "
                "document %s; skipping generation",
                item.document_id,
            )
            return DiscussOpeningResult.make_skipped(
                item,
                SKIPPED_REASON_NO_MATERIAL,
                "no untested assumption and no derivation operation available",
                cartridge_id=resolved_cartridge_id,
            )

        content = self._prompt_factory.build_content(item, cartridge)
        result = self._run_with_repair(item, content, cartridge, resolved_cartridge_id)
        result.llm_call_count = int(getattr(self._llm_client, "calls", 0) or 0)
        return self._apply_seed_cap(result, max_seeds)

    # -- internals ---------------------------------------------------------

    def _run_with_repair(
        self,
        item: DiscussOpeningInput,
        content: str,
        cartridge: CartridgeContext | None,
        cartridge_id: str | None,
    ) -> DiscussOpeningResult:
        # 共通骨格（tension / structure_anchor / reconstruction / doubt /
        # deliberation.standardization と同じ）を使う。ここでコピー実装しない。
        from core.llm_worker.repair import run_with_repair

        try:
            return run_with_repair(
                self._llm_client,
                content,
                validate=lambda data: self._validator.validate(
                    data, item, cartridge_id=cartridge_id
                ),
                build_repair_prompt=build_repair_prompt,
                on_repair_failed=lambda errors: build_repair_failed_result(
                    item, errors, cartridge_id=cartridge_id
                ),
                log_label="discuss_opening",
            )
        except Exception as exc:  # noqa: BLE001 — never propagate into the pipeline
            logger.warning(
                "discuss_opening generation failed for document %s: %s",
                item.document_id, exc, exc_info=True,
            )
            return DiscussOpeningResult.make_skipped(
                item,
                SKIPPED_REASON_LLM_CALL_FAILED,
                str(exc),
                cartridge_id=cartridge_id,
                llm_call_count=int(getattr(self._llm_client, "calls", 0) or 0),
            )

    @staticmethod
    def _resolve_max_seeds(item: DiscussOpeningInput, config: dict | None) -> int:
        raw = (config or {}).get("max_seeds")
        try:
            configured = int(raw) if raw is not None else int(item.max_seeds)
        except (TypeError, ValueError):
            configured = int(item.max_seeds)
        return configured if configured > 0 else item.max_seeds

    @staticmethod
    def _apply_seed_cap(result: DiscussOpeningResult, max_seeds: int) -> DiscussOpeningResult:
        """OA8: 件数上限で切り、切ったことを正直に記録する。"""
        if max_seeds > 0 and len(result.seeds) > max_seeds:
            dropped = len(result.seeds) - max_seeds
            result.seeds = result.seeds[:max_seeds]
            result.truncated = True
            result.truncated_count = dropped
            result.review_notes.append(
                f"{dropped} seed(s) beyond the per-document cap ({max_seeds}) were not kept"
            )
        return result

    def _load_cartridge(self, cartridge_id: str | None) -> CartridgeContext | None:
        if not cartridge_id:
            return None
        try:
            return self._cartridge_loader.load(cartridge_id)
        except FileNotFoundError:
            logger.warning(
                "Cartridge '%s' not found; proceeding without cartridge", cartridge_id
            )
            return None
