"""LandscapePlacementAgent: places one paper on the frozen reference maps.

Design: ``docs/features/knowledge_landscape_design.md`` §7.3 (Phase L1,
migration 065). LLM-first, cartridge-optional, **1 document = 1 LLM call** for
*all* domains (so the domains are weighed against each other), plus up to 2
repair attempts through the shared ``core/llm_worker/repair.run_with_repair``
loop.

What this agent does NOT do:

- It does not touch A-layer agents or their artifacts (LS2) — it only reads the
  already-resolved text handed over by ``core/landscape/builder.py``.
- It does not persist anything and it does not emit a status. The builder writes
  rows as ``status='inferred'``; only a teacher can confirm them (LS3).
- It does not move the map. ``node_id`` values must exist in the supplied frozen
  skeletons; the validator rejects invented anchors (LS7).
- It does not force a fit. A document that matches no anchor yields
  ``placements=[]`` with ``unplaced_domains`` reasons — recorded, not hidden
  (LS10 / P4).
- It does not grow the map. ``category_gaps``
  (``docs/features/category_gap_candidates_design.md`` §5.1) are *candidate*
  categories declared on the same LLM call; they are validated by a
  warning-only collector (a bad candidate never costs a placement) and the
  teacher-side decision / skeleton edit lives entirely outside this agent.
"""
from __future__ import annotations

import logging

from .cartridge_loader import CartridgeContext, CartridgeLoader
from .input_builder import LandscapePlacementInputBuilder
from .llm_client import LandscapePlacementLLMClient
from .prompt import LandscapePlacementPromptFactory
from .repair import build_repair_failed_result, build_repair_prompt
from .schema import (
    LandscapePlacementInput,
    LandscapePlacementResult,
    SKIPPED_REASON_LLM_CALL_FAILED,
    SKIPPED_REASON_NO_DOMAINS,
    SKIPPED_REASON_NO_MATERIAL,
)
from .validator import LandscapePlacementValidator

logger = logging.getLogger(__name__)


class LandscapePlacementAgent:
    """Generate placement candidates for one document across all frozen maps."""

    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = LandscapePlacementInputBuilder()
        self._prompt_factory = LandscapePlacementPromptFactory(self._input_builder)
        self._llm_client = LandscapePlacementLLMClient(model=llm_model)
        self._validator = LandscapePlacementValidator(self._input_builder)

    def run(
        self,
        document_input: LandscapePlacementInput | dict,
        *,
        cartridge_id: str | None = None,
        config: dict | None = None,
    ) -> LandscapePlacementResult:
        try:
            item = (
                document_input
                if isinstance(document_input, LandscapePlacementInput)
                else LandscapePlacementInput.from_dict(document_input)
            )
        except Exception as exc:  # noqa: BLE001 — never propagate into the pipeline
            logger.warning("landscape_placement: unusable input: %s", exc, exc_info=True)
            return LandscapePlacementResult(
                document_id="",
                skipped_reason=SKIPPED_REASON_LLM_CALL_FAILED,
                review_notes=[f"unusable input: {exc}"],
            )

        cartridge = self._load_cartridge(cartridge_id or item.cartridge_id)
        resolved_cartridge_id = (
            cartridge.cartridge_id if cartridge else (cartridge_id or item.cartridge_id)
        )

        max_placements = self._resolve_max_placements(item, config)
        max_gaps = self._resolve_max_gaps(item, config)

        if not item.has_domains():
            # 凍結骨格が無ければ配置先が無い（設計書 §7.2 の no_frozen_skeleton）。
            logger.info(
                "landscape_placement: no frozen skeleton node offered for document %s; "
                "skipping generation",
                item.document_id,
            )
            return LandscapePlacementResult.make_skipped(
                item,
                SKIPPED_REASON_NO_DOMAINS,
                "no domain with frozen skeleton nodes was supplied",
                cartridge_id=resolved_cartridge_id,
            )

        if not item.has_material():
            # 縮退（設計書 §7.2 / P4）: 素材が無い document は LLM を呼ばずに
            # 「配置しない」を理由付きで返す（根拠の無い配置を創作しない）。
            logger.info(
                "landscape_placement: no thesis / goal / claim material for document %s; "
                "skipping generation",
                item.document_id,
            )
            return LandscapePlacementResult.make_skipped(
                item,
                SKIPPED_REASON_NO_MATERIAL,
                "no thesis, goal, question, headline or claim text available",
                cartridge_id=resolved_cartridge_id,
            )

        content = self._prompt_factory.build_content(item, cartridge)
        result = self._run_with_repair(item, content, resolved_cartridge_id)
        result.llm_call_count = int(getattr(self._llm_client, "calls", 0) or 0)
        result = self._apply_placement_cap(result, max_placements)
        return self._apply_gap_cap(result, max_gaps)

    # -- internals ---------------------------------------------------------

    def _run_with_repair(
        self,
        item: LandscapePlacementInput,
        content: str,
        cartridge_id: str | None,
    ) -> LandscapePlacementResult:
        # 共通骨格（tension / structure_anchor / reconstruction / doubt /
        # deliberation.standardization / discuss_opening と同じ）を使う。
        # ここでコピー実装しない。
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
                log_label="landscape_placement",
            )
        except Exception as exc:  # noqa: BLE001 — never propagate into the pipeline
            logger.warning(
                "landscape_placement generation failed for document %s: %s",
                item.document_id, exc, exc_info=True,
            )
            return LandscapePlacementResult.make_skipped(
                item,
                SKIPPED_REASON_LLM_CALL_FAILED,
                str(exc),
                cartridge_id=cartridge_id,
                llm_call_count=int(getattr(self._llm_client, "calls", 0) or 0),
            )

    @staticmethod
    def _resolve_max_placements(
        item: LandscapePlacementInput, config: dict | None
    ) -> int:
        raw = (config or {}).get("max_placements")
        try:
            configured = int(raw) if raw is not None else int(item.max_placements)
        except (TypeError, ValueError):
            configured = int(item.max_placements)
        return configured if configured > 0 else item.max_placements

    @staticmethod
    def _resolve_max_gaps(item: LandscapePlacementInput, config: dict | None) -> int:
        """カテゴリギャップ候補の上限（設計書 §5.1 の ``max_gaps_per_document``）。

        ``max_placements`` と同じ流儀: 既定は入力（builder が env
        ``LANDSCAPE_GAP_MAX_PER_DOCUMENT`` から解決して渡す）で、``config`` に
        入っていればそちらを優先する。**0 は「候補を作らない」という有効な設定**
        なので既定値へ戻さない。
        """
        raw = (config or {}).get("max_gaps_per_document")
        try:
            configured = (
                int(raw) if raw is not None else int(item.max_gaps_per_document)
            )
        except (TypeError, ValueError):
            try:
                configured = int(item.max_gaps_per_document)
            except (TypeError, ValueError):
                configured = 0
        return max(0, configured)

    @staticmethod
    def _apply_gap_cap(
        result: LandscapePlacementResult, max_gaps: int
    ) -> LandscapePlacementResult:
        """候補件数の上限で切る（validator と同じ入力順・冪等）。

        validator は入力の ``max_gaps_per_document`` で既に切っているので、ここが
        実際に効くのは ``config`` でより小さい上限を渡した場合だけ。候補は配置と
        独立なので ``truncated``（配置の切り詰めフラグ）には数えない。
        """
        cap = max(0, int(max_gaps or 0))
        gaps = list(result.category_gaps or [])
        if len(gaps) > cap:
            dropped = len(gaps) - cap
            result.category_gaps = gaps[:cap]
            result.review_notes.append(
                f"{dropped} category gap candidate(s) beyond the per-document cap "
                f"({cap}) were not kept"
            )
        return result

    @staticmethod
    def _apply_placement_cap(
        result: LandscapePlacementResult, max_placements: int
    ) -> LandscapePlacementResult:
        """件数上限で切り、切ったことを正直に記録する（validator と同じ決定論順序）。

        validator は入力の ``max_placements`` で既に切っているため、ここが実際に
        効くのは ``config`` でより小さい上限を渡した場合だけ（二重に切っても
        結果は同じ = 冪等）。
        """
        cap = int(max_placements or 0)
        if cap > 0 and len(result.placements) > cap:
            ordered = sorted(
                enumerate(result.placements),
                key=lambda pair: (-float(pair[1].weight), pair[0]),
            )
            kept_indices = sorted(index for index, _ in ordered[:cap])
            kept = [result.placements[index] for index in kept_indices]
            dropped = len(result.placements) - len(kept)
            result.placements = kept
            result.truncated = True
            result.truncated_count = int(result.truncated_count or 0) + dropped
            result.review_notes.append(
                f"{dropped} placement(s) beyond the per-document cap ({cap}) were not kept"
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
