"""Build ClaimQualificationAgent LLM inputs.

P0-1 と同じ是正をこのステージにも適用した（`docs/architecture/
knowledge_structure_review_2026-09-12.md` §4 / 付属資料 A）:

1. **既定では打ち切らない**。従来は ``_MAX_SPANS = 96`` で**文書順の先頭 96 span**
   に切り捨てていたため、結論・限界の節が丸ごと claim 化されない論文があった
   （実測: 803 ブロックの論文で先頭 151 ブロック分しか到達せず、§3〜§5 と付録の
   span が 1 件も採否判定されなかった）。既定は上限なし（0）で、上限を敷きたい
   運用だけが ``config["max_spans"]`` か env ``CLAIM_QUALIFICATION_MAX_SPANS``
   で明示する。
2. **上限がある場合も先頭切り捨てにしない**。節単位の層化サンプリング
   （正本 ``episteme_graph.agents.stratified_sampling``）で文書全体に配分する。
3. **取りこぼしの量を報告する**。``build_with_coverage()`` が
   ``agents/coverage_report.py::build_coverage_report`` の共通形式で返し、
   agent が ``summary_stats["coverage"]`` に載せる。
"""
from __future__ import annotations

from episteme_graph.agents.coverage_report import build_coverage_report
from episteme_graph.agents.document_structure.schema import DocumentStructureResult
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.equation_semantics.input_builder import (
    EquationSemanticsInputBuilder,
    context_excerpt,
)
from episteme_graph.agents.equation_semantics.normalizer import EquationNormalizer
from episteme_graph.agents.rhetorical_role.input_builder import (
    ends_as_equation_intro,
    refers_to_preceding_equation,
)
from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult, SpanAnnotation
from episteme_graph.agents.text_truncation import excerpt_around
from episteme_graph.agents.stratified_sampling import (
    NO_SECTION_KEY,
    order_blocks,
    resolve_limit,
    select_indices,
    unprocessed_section_entries,
)

from .schema import (
    DETERMINISTIC_REJECT_REASON,
    CartridgeContext,
    QualificationLLMInput,
    is_deterministic_reject,
)

# 0 = 上限なし（既定）。かつての 96 打ち切りは廃止した。
_DEFAULT_MAX_SPANS = 0
_MAX_SPANS_ENV = "CLAIM_QUALIFICATION_MAX_SPANS"
_MAX_SOURCE_BLOCK_CHARS = 1800
#: ``adjacent_equations`` で渡す式の長さの上限（rhetorical_role の preceding_equation と同じ）。
_MAX_ADJACENT_EQUATION_CHARS = 400
_ADJACENT_CONTEXT_BLOCK_TYPES = {"body_paragraph", "equation_block"}
#: 原文ブロックを上限で切って渡した理由（coverage ``details.truncated_blocks`` — IK-0808）。
TRUNCATED_SOURCE_BLOCK_REASON = "source_block_over_char_limit"

_COVERAGE_UNIT = "spans"
_TRUNCATION_REASON = "max_spans"
# roles の並び（rhetorical_role が文書順に並べた結果）をそのまま使う。
_SORT_KEY = "role_annotation_order"


class _SpanSlot:
    """層化サンプリングにかける1件（span とその文脈）。"""

    __slots__ = ("annotation", "spans", "index")

    def __init__(self, annotation, spans: list[SpanAnnotation], index: int) -> None:
        self.annotation = annotation
        self.spans = spans
        self.index = index

    @property
    def span(self) -> SpanAnnotation:
        return self.spans[self.index]

    @property
    def section_key(self) -> str:
        return getattr(self.annotation, "section_id", None) or NO_SECTION_KEY


class ClaimQualificationInputBuilder:
    """Package role-labeled spans with local and paper-level context."""

    def build(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult,
        roles: RhetoricalRoleResult,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> list[QualificationLLMInput]:
        """LLM 入力のリストのみを返す（既存シグネチャ・戻り値は不変）。"""
        inputs, _ = self.build_with_coverage(
            structure, skeleton, roles, cartridge=cartridge, config=config
        )
        return inputs

    def build_with_coverage(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult,
        roles: RhetoricalRoleResult,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> tuple[list[QualificationLLMInput], dict]:
        """LLM 入力と、取りこぼし報告（``build_coverage_report`` 形式）を返す。

        決定論で reject と決まる span（IK-0803）は LLM 入力に含めない。それらの入力は
        ``build_partitioned()`` の 2 番目の戻り値で受け取る。
        """
        inputs, _skipped, coverage = self.build_partitioned(
            structure, skeleton, roles, cartridge=cartridge, config=config
        )
        return inputs, coverage

    def build_partitioned(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult,
        roles: RhetoricalRoleResult,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> tuple[list[QualificationLLMInput], list[QualificationLLMInput], dict]:
        """(LLM に渡す入力, 決定論で reject する入力, 取りこぼし報告) を返す。

        IK-0803: rhetorical_role が reject 候補にし、役割が主張になり得ない語彙
        （``schema.NON_CLAIMABLE_ROLE_LABELS``）だけの span は LLM を呼ばずに reject
        として記録する。母集合（``population``）と上限の配分からは外し、
        ``details.skipped_reject_candidates`` に理由付きで列挙する（P4）。
        ``config["skip_deterministic_rejects"]=False`` で従来どおり LLM に回す。
        """
        cfg = config or {}
        skip_deterministic = bool(cfg.get("skip_deterministic_rejects", True))
        max_spans = resolve_limit(
            cfg, "max_spans", _MAX_SPANS_ENV, default=_DEFAULT_MAX_SPANS
        )
        include_reject_candidates = bool(cfg.get("include_reject_candidates", True))

        sections_by_id = {s.section_id: s for s in structure.sections}
        block_text_by_id = {b.block_id: b.text for b in structure.blocks}
        # 表本体（``raw.in_table``）の span は主張の候補にしない（R-4）。役割判定側でも
        # 母集合から外しているが、旧 run の roles artifact や別経路の annotation が
        # 混ざっても採否の母集合に表のセルを並べないよう、ここでも同じ判定を通す。
        table_block_ids = {
            b.block_id
            for b in structure.blocks
            if isinstance(getattr(b, "raw", None), dict)
            and (b.raw.get("in_table") or b.raw.get("from_table"))
        }
        normalized_terms = self._build_normalized_terms(cartridge) if cartridge else None
        adjacent_by_block = _adjacent_equations_by_block(structure.blocks)
        headline_claim = self._headline_text(skeleton)

        slots: list[_SpanSlot] = []
        target_indices: list[int] = []
        skipped_positions: list[int] = []
        excluded_table_block_ids: list[str] = []
        for annotation in roles.role_annotations:
            if annotation.block_id in table_block_ids:
                if annotation.block_id not in excluded_table_block_ids:
                    excluded_table_block_ids.append(annotation.block_id)
                continue
            spans = annotation.span_annotations
            for idx in range(len(spans)):
                slot = _SpanSlot(annotation, spans, idx)
                position = len(slots)
                slots.append(slot)
                if not self._should_include(slot.span, include_reject_candidates):
                    continue
                if skip_deterministic and is_deterministic_reject(
                    slot.span.role_labels,
                    slot.span.is_claim_candidate,
                    slot.span.is_reject_candidate,
                ):
                    skipped_positions.append(position)
                    continue
                target_indices.append(position)

        selected = select_indices(
            slots,
            target_indices,
            max_spans,
            section_key_of=lambda slot: slot.section_key,
        )

        truncated_blocks: list[dict] = []

        def _make_input(slot: _SpanSlot) -> QualificationLLMInput:
            annotation = slot.annotation
            span = slot.span
            section = sections_by_id.get(annotation.section_id or "")
            return QualificationLLMInput(
                document_id=roles.document_id,
                cartridge_id=cartridge.cartridge_id if cartridge else roles.cartridge_id,
                span_id=span.span_id,
                block_id=annotation.block_id,
                section_id=annotation.section_id,
                section_title=section.title if section else None,
                backbone_block_type=annotation.backbone_block_type,
                headline_claim=headline_claim,
                span_text=span.text,
                role_labels=span.role_labels,
                is_claim_candidate=span.is_claim_candidate,
                is_reject_candidate=span.is_reject_candidate,
                prev_span_text=self._neighbor_span_text(slot.spans, slot.index, -1),
                next_span_text=self._neighbor_span_text(slot.spans, slot.index, 1),
                source_block_text=_source_block_excerpt(
                    block_text_by_id.get(annotation.block_id) or "",
                    span,
                    annotation.block_id,
                    truncated_blocks,
                ),
                normalized_terms=normalized_terms,
                role_confidence=getattr(span, "confidence", None),
                adjacent_equations=_adjacent_equations_for_span(
                    adjacent_by_block.get(annotation.block_id),
                    block_text_by_id.get(annotation.block_id) or "",
                    slot.index,
                    len(slot.spans),
                ),
            )

        inputs = [_make_input(slots[position]) for position in selected]
        skipped = [_make_input(slots[position]) for position in skipped_positions]

        coverage = self._build_coverage(
            slots=slots,
            target_indices=target_indices,
            selected=selected,
            sections_by_id=sections_by_id,
        )
        if excluded_table_block_ids:
            coverage.setdefault("details", {})["excluded_table_block_ids"] = (
                excluded_table_block_ids
            )
        if truncated_blocks:
            coverage.setdefault("details", {})["truncated_blocks"] = truncated_blocks
        if skipped:
            coverage.setdefault("details", {})["skipped_reject_candidates"] = [
                {
                    "span_id": item.span_id,
                    "block_id": item.block_id,
                    "role_labels": list(item.role_labels),
                    "reason": DETERMINISTIC_REJECT_REASON,
                }
                for item in skipped
            ]
        return inputs, skipped, coverage

    # ------------------------------------------------------------------

    @staticmethod
    def _build_coverage(
        slots: list[_SpanSlot],
        target_indices: list[int],
        selected: list[int],
        sections_by_id: dict,
    ) -> dict:
        population = len(target_indices)
        processed = len(selected)
        details: dict = {"sort_key": _SORT_KEY}
        unprocessed = unprocessed_section_entries(
            slots,
            target_indices,
            selected,
            section_key_of=lambda slot: slot.section_key,
            title_of=lambda key: getattr(sections_by_id.get(key), "title", None),
        )
        if unprocessed:
            details["unprocessed_sections"] = unprocessed
        return build_coverage_report(
            population=population,
            processed=processed,
            reasons=[_TRUNCATION_REASON] if population > processed else [],
            unit=_COVERAGE_UNIT,
            details=details,
        )

    @staticmethod
    def _should_include(span: SpanAnnotation, include_reject_candidates: bool) -> bool:
        if span.is_claim_candidate:
            return True
        if include_reject_candidates and span.is_reject_candidate:
            return True
        # 先行研究の言及は reject ではなく文脈（IK-0803）。rhetorical_role は prior_work の
        # reject 印を外すので、印が無くても採否判定の母集合に残す（claim_tier="prior_work"）。
        if "prior_work" in span.role_labels:
            return True
        return "unknown" in span.role_labels

    @staticmethod
    def _neighbor_span_text(
        spans: list[SpanAnnotation],
        index: int,
        direction: int,
    ) -> str | None:
        i = index + direction
        if 0 <= i < len(spans):
            return spans[i].text
        return None

    @staticmethod
    def _headline_text(skeleton: PaperSkeletonResult) -> str | None:
        if not isinstance(skeleton.headline_claim, dict):
            return None
        text = skeleton.headline_claim.get("text")
        return text if isinstance(text, str) and text.strip() else None

    @staticmethod
    def _build_normalized_terms(cartridge: CartridgeContext) -> list[dict]:
        terms: list[dict] = []
        if cartridge.aliases:
            for canonical, aliases in cartridge.aliases.items():
                terms.append({"canonical": canonical, "aliases": aliases})

        hints = cartridge.extraction_hints
        if isinstance(hints, list):
            for hint in hints:
                if isinstance(hint, dict):
                    canonical = hint.get("canonical") or hint.get("label")
                    if canonical and not any(t.get("canonical") == canonical for t in terms):
                        terms.append({**hint, "canonical": canonical})
        elif isinstance(hints, dict):
            for canonical, value in hints.items():
                terms.append({"canonical": canonical, "hints": value})

        return terms if terms else []


def _source_block_excerpt(
    block_text: str,
    span: SpanAnnotation,
    block_id: str,
    truncated_blocks: list[dict],
) -> str:
    """原文ブロックを上限内で、対象 span を含む窓として切り出す（IK-0808）。

    従来は先頭 1,800 字を語の途中で印なしに切り（「…evidence of the robustnes」）、上限より
    後ろの span では対象そのものが原文から落ちていた。対象 span は切らずに残し、前後を
    文・語の境界（``$…$`` の外）で切って切った側に「…」を付け、切ったブロックを理由付きで
    記録する（P4）。
    """
    if len(block_text) <= _MAX_SOURCE_BLOCK_CHARS:
        return block_text
    start = int(getattr(span, "char_start", 0) or 0)
    end = int(getattr(span, "char_end", 0) or 0)
    span_text = getattr(span, "text", "") or ""
    if not (0 <= start < end <= len(block_text)) or block_text[start:end] != span_text:
        found = block_text.find(span_text) if span_text else -1
        start, end = (found, found + len(span_text)) if found >= 0 else (0, 0)
    excerpt, cut = excerpt_around(block_text, start, end, _MAX_SOURCE_BLOCK_CHARS)
    if cut and not any(item.get("block_id") == block_id for item in truncated_blocks):
        truncated_blocks.append({"block_id": block_id, "reason": TRUNCATED_SOURCE_BLOCK_REASON})
    return excerpt


def _equation_entry(block, position: str) -> dict:
    raw = getattr(block, "raw", None) if isinstance(getattr(block, "raw", None), dict) else {}
    label = EquationNormalizer.extract_label(block.text or "") or getattr(block, "equation_label", None)
    printed = raw.get("printed_numbers") if isinstance(raw, dict) else None
    printed_number = printed[0] if isinstance(printed, list) and printed else None
    latex = None
    if EquationSemanticsInputBuilder._is_trusted_tex_block(block):
        latex = context_excerpt(block.text or "", _MAX_ADJACENT_EQUATION_CHARS)
    return {
        "position": position,
        "block_id": block.block_id,
        "label": label,
        "printed_number": printed_number,
        "latex": latex,
    }


def _adjacent_equations_by_block(blocks) -> dict[str, tuple[dict | None, dict | None]]:
    """本文ブロックごとに、直前・直後の文脈ブロックが同じ小節の式ならその式（IK-0714）。

    文脈ブロックの数え方は rhetorical_role の ``_neighbor_text`` と同じ（本文と式だけを数え、
    表本体は飛ばす）。間に別の本文段落があれば隣接式とみなさない。
    """
    ordered, _ = order_blocks(list(blocks))
    context = [
        b for b in ordered
        if b.block_type in _ADJACENT_CONTEXT_BLOCK_TYPES
        and not (isinstance(getattr(b, "raw", None), dict)
                 and (b.raw.get("in_table") or b.raw.get("from_table")))
    ]
    result: dict[str, tuple[dict | None, dict | None]] = {}
    for i, block in enumerate(context):
        if block.block_type != "body_paragraph":
            continue
        own = getattr(block, "section_id", None)
        before = after = None
        if i > 0:
            prev = context[i - 1]
            if prev.block_type == "equation_block" and getattr(prev, "section_id", None) == own:
                before = _equation_entry(prev, "before")
        if i + 1 < len(context):
            nxt = context[i + 1]
            if nxt.block_type == "equation_block" and getattr(nxt, "section_id", None) == own:
                after = _equation_entry(nxt, "after")
        if before or after:
            result[block.block_id] = (before, after)
    return result


def _adjacent_equations_for_span(
    pair: tuple[dict | None, dict | None] | None,
    block_text: str,
    span_index: int,
    span_count: int,
) -> list[dict] | None:
    """span に添える隣接式（前はブロック先頭の span で続き・名指しのとき、後はブロック末尾の
    span で本文が式の導入で終わるとき）。常には渡さない（入力長と誤帰属を増やさない — IK-0794）。
    """
    if not pair:
        return None
    before, after = pair
    out: list[dict] = []
    if before and span_index == 0 and refers_to_preceding_equation(block_text):
        out.append(dict(before))
    if after and span_index == span_count - 1 and ends_as_equation_intro(block_text):
        out.append(dict(after))
    return out or None
