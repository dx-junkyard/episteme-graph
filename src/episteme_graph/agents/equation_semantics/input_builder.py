"""Build EquationSemanticsAgent LLM inputs.

Issue #245: build_candidates() で EquationCandidate を生成し、
acceptance gate 通過済みの accepted 候補のみ LLM 入力を作る。

P0-1 と同じ是正（`docs/architecture/knowledge_structure_review_2026-09-12.md` §4 /
付属資料 A）:

1. **既定では打ち切らない**。従来は ``_MAX_EQUATIONS = 64`` で ``(page, order)`` 順の
   先頭 64 候補に切り捨てていた（実測: 10 本中 8 本が**ちょうど 64**、式ブロック 348 の
   論文で 64）。既定は上限なし（0）で、上限を敷きたい運用だけが
   ``config["max_equations"]`` か env ``EQUATION_SEMANTICS_MAX_EQUATIONS`` で明示する。
2. **並び順は ``order`` が一意なら ``order`` のみ**（正本
   ``episteme_graph.agents.stratified_sampling``）。``page`` は GROBID 経路で既定 1 の
   ままのブロックが多く（実測: 803 中 558）、``(page, order)`` だと突合失敗群が先頭に
   集まって順序が内容と無関係になる。
3. **上限がある場合も先頭切り捨てにしない**。display の equation_block を節単位で
   層化サンプリングし、inline 数式候補は残枠に入れる。
4. **inline 数式候補には既定の上限を敷く**（``_DEFAULT_MAX_INLINE_EQUATIONS = 32`` /
   env ``EQUATION_SEMANTICS_MAX_INLINE_EQUATIONS``、0 = 上限なし）。inline は
   ``x = 1`` のような本文中の表記を正規表現で拾う**弱い候補**で、1 件ごとに LLM
   コールを消費する。display の式ブロック（論文が式として組んだもの）は上限なしの
   まま、inline だけを既定で抑える。打ち切りは coverage の理由コード
   ``max_inline_equations`` に必ず出す（黙って切らない）。
5. **取りこぼしの量を報告する**。母集合（display block + inline 候補）と処理数は
   :func:`compute_candidate_coverage` が返し、orchestrator が stage payload の
   ``coverage`` に載せる（``schema.py`` に手を入れずに済ませるための出口）。
"""
from __future__ import annotations

import uuid
import re

from episteme_graph.agents.document_structure.schema import DocumentStructureResult, TypedBlock
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult
from episteme_graph.agents.stratified_sampling import (
    NO_SECTION_KEY,
    order_blocks,
    resolve_limit,
    select_indices,
    unprocessed_section_entries,
)

from .normalizer import EquationNormalizer
from .schema import (
    CartridgeContext,
    EquationCandidate,
    EquationLLMInput,
)

_CONTEXT_BLOCK_TYPES = {"body_paragraph", "equation_block"}
# 候補全体（display + inline）の上限。0 = 上限なし（既定）。かつての 64 打ち切りは廃止した。
_DEFAULT_MAX_EQUATIONS = 0
_MAX_EQUATIONS_ENV = "EQUATION_SEMANTICS_MAX_EQUATIONS"
# inline 数式候補だけの上限。display の式ブロック（論文が式として組んだもの）は
# 上限なしのままで、正規表現由来の弱い候補である inline のみ既定で抑える。
_DEFAULT_MAX_INLINE_EQUATIONS = 32
_MAX_INLINE_EQUATIONS_ENV = "EQUATION_SEMANTICS_MAX_INLINE_EQUATIONS"
_MAX_CONTEXT_BLOCKS = 6
_MAX_CONTEXT_CHARS = 1200

_COVERAGE_UNIT = "equation_candidates"
_TRUNCATION_REASON = "max_equations"
_INLINE_TRUNCATION_REASON = "max_inline_equations"

# extraction_status のうち再構成が必要なもの
_NEEDS_RECONSTRUCTION_STATUSES = {"partial", "fragment_only", "label_only", "missing", "unparsed"}
_INLINE_MATH_RE = re.compile(
    r"(?<![A-Za-z])"
    r"(?P<expr>"
    r"(?:[A-Za-zα-ωΑ-Ω][A-Za-z0-9_{}()α-ωΑ-Ω]{0,16})"
    r"\s*"
    r"(?:=|≠|≈|≤|≥|<|>|\\neq|\\approx)"
    r"\s*"
    r"(?:[A-Za-z0-9_{}()α-ωΑ-Ω+\-*/π]{1,24})"
    r")"
)


class EquationSemanticsInputBuilder:
    def __init__(self, normalizer: EquationNormalizer | None = None) -> None:
        self._normalizer = normalizer or EquationNormalizer()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_candidates(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult | None = None,
        roles: RhetoricalRoleResult | None = None,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> list[EquationCandidate]:
        """全 equation_block から EquationCandidate を生成する (acceptance gate 適用前)。

        既定では打ち切らない。上限があるときは display の equation_block を節単位で
        層化サンプリングし、残枠に inline 数式候補を入れる（先頭切り捨てにしない）。
        """
        cfg = config or {}
        self._normalizer.set_extra_label_patterns(self._cartridge_label_patterns(cartridge))
        plan = self._plan_candidates(structure, cfg)

        candidates: list[EquationCandidate] = [
            self._block_to_candidate(block, structure.document_id)
            for block in plan["display_blocks"]
        ]
        candidates.extend(plan["inline_candidates"])
        return candidates

    def _plan_candidates(self, structure: DocumentStructureResult, cfg: dict) -> dict:
        """候補の母集合・選抜結果を決定論的に組み立てる（coverage と共有する内部計画）。"""
        max_equations = resolve_limit(
            cfg, "max_equations", _MAX_EQUATIONS_ENV, default=_DEFAULT_MAX_EQUATIONS
        )
        max_inline = resolve_limit(
            cfg,
            "max_inline_equations",
            _MAX_INLINE_EQUATIONS_ENV,
            default=_DEFAULT_MAX_INLINE_EQUATIONS,
        )
        include_inline = bool(cfg.get("include_inline", True))
        ordered_blocks, sort_key_name = order_blocks(structure.blocks)

        display_indices = [
            idx
            for idx, block in enumerate(ordered_blocks)
            if block.block_type == "equation_block"
        ]
        selected_display = select_indices(
            ordered_blocks,
            display_indices,
            max_equations,
            section_key_of=lambda b: getattr(b, "section_id", None) or NO_SECTION_KEY,
        )

        inline_all: list[EquationCandidate] = []
        if include_inline:
            for block in ordered_blocks:
                if block.block_type != "body_paragraph":
                    continue
                inline_all.extend(
                    self._inline_candidates_for_block(block, structure.document_id)
                )

        # inline は「文書順に上限まで」。display の層化とは独立の弁で、
        # display の式ブロックはこの上限では減らない。
        inline_selected = inline_all[:max_inline] if max_inline > 0 else list(inline_all)
        inline_cut_by_inline_limit = len(inline_selected) < len(inline_all)
        inline_cut_by_total_limit = False
        if max_equations > 0:
            remaining = max(max_equations - len(selected_display), 0)
            if len(inline_selected) > remaining:
                inline_cut_by_total_limit = True
            inline_selected = inline_selected[:remaining]
        display_cut_by_total_limit = len(selected_display) < len(display_indices)

        return {
            "ordered_blocks": ordered_blocks,
            "sort_key": sort_key_name,
            "display_indices": display_indices,
            "selected_display_indices": selected_display,
            "display_blocks": [ordered_blocks[idx] for idx in selected_display],
            "inline_total": len(inline_all),
            "inline_candidates": inline_selected,
            "inline_cut_by_inline_limit": inline_cut_by_inline_limit,
            "inline_cut_by_total_limit": inline_cut_by_total_limit,
            "display_cut_by_total_limit": display_cut_by_total_limit,
            "include_inline": include_inline,
        }

    def compute_candidate_coverage(
        self,
        structure: DocumentStructureResult,
        config: dict | None = None,
    ) -> dict:
        """候補生成の母集合・処理数の**事実**を返す（``build_coverage_report`` の引数）。

        戻り値は ``{"population", "processed", "reasons", "unit", "details"}``。
        報告そのものを組み立てないのは、orchestrator 側の ``_attach_coverage`` だけが
        ``coverage`` キーを作るという規律（`test_pipeline_coverage_report.py`）を
        保つため。母集合は **display の equation_block + inline 数式候補**で、
        従来 orchestrator が式ブロックだけを数えていたために inline の切り捨てが
        ``truncated: 0`` と報告されていた取り違えを直す。
        """
        plan = self._plan_candidates(structure, config or {})
        population = len(plan["display_indices"]) + plan["inline_total"]
        processed = len(plan["selected_display_indices"]) + len(plan["inline_candidates"])
        details: dict = {
            "sort_key": plan["sort_key"],
            "includes_inline_candidates": bool(plan["include_inline"]),
        }
        unprocessed = unprocessed_section_entries(
            plan["ordered_blocks"],
            plan["display_indices"],
            plan["selected_display_indices"],
            section_key_of=lambda b: getattr(b, "section_id", None) or NO_SECTION_KEY,
            title_of=lambda key: getattr(
                {s.section_id: s for s in structure.sections}.get(key), "title", None
            ),
        )
        if unprocessed:
            details["unprocessed_sections"] = unprocessed
        # 理由コードは**実際に効いた弁だけ**を書く（``build_coverage_report`` は
        # truncated==0 のとき理由を落とすが、inline 上限だけが効いたときに
        # ``max_equations`` を並べると「全体上限で切った」と読めてしまう）。
        reasons: list[str] = []
        if plan["display_cut_by_total_limit"] or plan["inline_cut_by_total_limit"]:
            reasons.append(_TRUNCATION_REASON)
        if plan["inline_cut_by_inline_limit"]:
            reasons.append(_INLINE_TRUNCATION_REASON)
        return {
            "population": population,
            "processed": processed,
            "reasons": reasons,
            "unit": _COVERAGE_UNIT,
            "details": details,
        }

    def build_llm_inputs(
        self,
        structure: DocumentStructureResult,
        accepted_candidates: list[EquationCandidate],
        skeleton: PaperSkeletonResult | None = None,
        roles: RhetoricalRoleResult | None = None,
        cartridge: CartridgeContext | None = None,
        accepted_statuses: set[str] | None = None,
        force_reconstruction: bool = False,
    ) -> list[EquationLLMInput]:
        """accepted_candidates に対して LLM 入力を構築する。"""
        if not accepted_candidates:
            return []

        statuses = accepted_statuses or {"accepted"}
        _accepted = [c for c in accepted_candidates if c.acceptance_status in statuses]
        if not _accepted:
            return []
        self._normalizer.set_extra_label_patterns(self._cartridge_label_patterns(cartridge))
        sections_by_id = {s.section_id: s for s in structure.sections}
        backbone_by_section = self._map_backbone_by_section(skeleton)
        spans_by_block = self._map_spans_by_block(roles)
        normalized_terms = self._build_normalized_terms(cartridge) if cartridge else None
        ordered_blocks, _sort_key = order_blocks(structure.blocks)
        block_by_id = {b.block_id: b for b in ordered_blocks}
        index_by_block_id = {b.block_id: i for i, b in enumerate(ordered_blocks)}

        inputs: list[EquationLLMInput] = []
        for candidate in _accepted:
            block_id = str(candidate.source_location.get("block_id") or "")
            block = block_by_id.get(block_id)
            if not block:
                continue
            idx = index_by_block_id.get(block.block_id, 0)
            if block.block_type == "equation_block":
                equation = self._normalizer.normalize(block)
                equation_id = equation.equation_id
                equation_text = equation.text
                latex = equation.latex
                plain_text = equation.plain_text
                label = equation.label
                extraction_source = self._extraction_source(block)
            else:
                label = candidate.matched_label
                equation_id = candidate.accepted_equation_id or f"eq_{candidate.candidate_id}"
                equation_text = candidate.raw_text
                latex = None
                plain_text = candidate.raw_text
                extraction_source = "inline_pdf_text_layer"
            source_is_trusted = extraction_source == "tex_source"
            section = sections_by_id.get(block.section_id or "")
            needs_reconstruction = (
                not source_is_trusted
                and (force_reconstruction or candidate.extraction_status in _NEEDS_RECONSTRUCTION_STATUSES)
            )
            prev_texts = self._neighbor_texts(ordered_blocks, idx, -1, _MAX_CONTEXT_BLOCKS)
            next_texts = self._neighbor_texts(ordered_blocks, idx, 1, _MAX_CONTEXT_BLOCKS)
            if block.block_type != "equation_block":
                start = int(candidate.source_location.get("span_start") or 0)
                end = int(candidate.source_location.get("span_end") or start)
                current = (
                    block.text[:start]
                    + "[INLINE_MATH_TARGET_OMITTED_AS_UNTRUSTED]"
                    + block.text[end:]
                )
                prev_texts.append(f"[{block.block_id}] {current[:_MAX_CONTEXT_CHARS]}")

            inputs.append(EquationLLMInput(
                document_id=structure.document_id,
                cartridge_id=cartridge.cartridge_id if cartridge else structure.cartridge_id,
                equation_id=equation_id,
                block_id=block.block_id,
                section_id=block.section_id,
                section_title=section.title if section else None,
                backbone_block_type=backbone_by_section.get(block.section_id or ""),
                label=label,
                equation_text=equation_text,
                latex=latex,
                plain_text=plain_text,
                prev_texts=prev_texts,
                next_texts=next_texts,
                nearby_span_annotations=spans_by_block.get(block.block_id, []),
                normalized_terms=normalized_terms,
                candidate_id=candidate.candidate_id,
                extraction_status=candidate.extraction_status,
                acceptance_status=candidate.acceptance_status,
                needs_reconstruction=needs_reconstruction,
                extraction_source=extraction_source,
                source_is_trusted=source_is_trusted,
            ))
        return inputs

    # ------------------------------------------------------------------
    # Legacy build() — backward compat: runs candidates + accepted filter internally
    # (used by tests that don't use the new pipeline)
    # ------------------------------------------------------------------

    def build(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult | None = None,
        roles: RhetoricalRoleResult | None = None,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> list[EquationLLMInput]:
        """全 equation_block から LLM 入力を直接生成 (acceptance gate なし)。

        agent.py は新パイプライン (build_candidates → acceptance_gate → build_llm_inputs)
        を使う。このメソッドは旧 API 互換のために残す。
        """
        candidates = self.build_candidates(structure, skeleton=skeleton, roles=roles, cartridge=cartridge, config=config)
        # acceptance gate なしで全候補を accepted として扱う
        for c in candidates:
            if not c.acceptance_status:
                c.acceptance_status = "accepted"
        return self.build_llm_inputs(
            structure, candidates, skeleton=skeleton, roles=roles, cartridge=cartridge
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _block_to_candidate(self, block: TypedBlock, document_id: str) -> EquationCandidate:
        equation = self._normalizer.normalize(block)
        detection: list[str] = ["document_structure_equation_block"]
        if equation.label:
            detection.append("equation_number_pattern")
        extraction_source = self._extraction_source(block)
        if extraction_source == "tex_source":
            detection.append("tex_source")

        bbox_list: list[float] = []
        if block.bbox:
            bbox_list = list(block.bbox)

        source_location: dict = {
            "page": block.page,
            "section_id": block.section_id,
            "block_id": block.block_id,
            "span_start": 0,
            "span_end": len(block.text),
            "bbox": bbox_list,
            "extraction_source": extraction_source,
        }

        review_reason: list[str] = []
        # Issue #368: a rejected PDF/GROBID label is recorded for audit and the
        # equation_label is normalized to None (matched_label stays None).
        if not equation.label_is_valid and equation.rejected_label is not None:
            review_reason.append("invalid_equation_label")
            source_location["rejected_equation_label"] = equation.rejected_label

        # Issue #368: table / table-cell provenance. The acceptance gate uses
        # this to keep the candidate out of the auto-confirmed equation set.
        table_provenance = self._table_provenance(block)
        if table_provenance:
            source_location["table_provenance"] = table_provenance
            review_reason.append("table_derived_equation_candidate")

        return EquationCandidate(
            candidate_id=f"eqcand_{block.block_id}_{uuid.uuid4().hex[:8]}",
            document_id=document_id,
            source_location=source_location,
            raw_text=block.text.strip(),
            matched_label=equation.label,
            detection_method=detection,
            candidate_score=block.confidence,
            extraction_status="unparsed",   # gate が上書きする
            acceptance_status="rejected",   # gate が上書きする
            accepted_equation_id=None,
            merge_target_hint=None,
            needs_math_review=False,
            review_reason=review_reason,
        )

    @staticmethod
    def _table_provenance(block: TypedBlock) -> dict | None:
        """Detect table / table-cell provenance for an equation candidate (#368).

        Best-effort: reads provenance hints that DocumentStructure may carry on
        ``block.raw`` (no hard dependency on a schema change). Recognized hints:
        ``table_id`` / ``table_cell`` / ``in_table`` / ``from_table`` /
        ``container_type == 'table'`` / ``parent_block_type == 'table_*'``.
        """
        raw = block.raw if isinstance(block.raw, dict) else {}
        provenance: dict = {}
        for key in ("table_id", "table_cell", "row", "col"):
            value = raw.get(key)
            if value not in (None, "", []):
                provenance[key] = value
        if raw.get("in_table") or raw.get("from_table"):
            provenance.setdefault("in_table", True)
        container = str(raw.get("container_type") or raw.get("parent_block_type") or "")
        if container.startswith("table"):
            provenance.setdefault("container_type", container)
        return provenance or None

    def _inline_candidates_for_block(self, block: TypedBlock, document_id: str) -> list[EquationCandidate]:
        candidates: list[EquationCandidate] = []
        for match in _INLINE_MATH_RE.finditer(block.text):
            expr = " ".join(match.group("expr").split())
            if len(expr) < 3 or len(expr) > 80:
                continue
            candidates.append(EquationCandidate(
                candidate_id=f"eqcand_inline_{block.block_id}_{match.start()}_{uuid.uuid4().hex[:8]}",
                document_id=document_id,
                source_location={
                    "page": block.page,
                    "section_id": block.section_id,
                    "block_id": block.block_id,
                    "span_start": match.start(),
                    "span_end": match.end(),
                    "bbox": list(block.bbox) if block.bbox else [],
                },
                raw_text=expr,
                matched_label=None,
                detection_method=["inline_equation_heuristic"],
                candidate_score=min(block.confidence, 0.55),
                extraction_status="unparsed",
                acceptance_status="provisional",
                accepted_equation_id=None,
                merge_target_hint=None,
                needs_math_review=True,
                review_reason=["inline_pdf_math_untrusted"],
            ))
        return candidates

    @staticmethod
    def _map_backbone_by_section(
        skeleton: PaperSkeletonResult | None,
    ) -> dict[str, str]:
        if not skeleton:
            return {}
        mapping: dict[str, str] = {}
        for logical_block in skeleton.logical_blocks:
            for section_id in logical_block.section_ids:
                mapping.setdefault(section_id, logical_block.block_type)
        return mapping

    @staticmethod
    def _map_spans_by_block(roles: RhetoricalRoleResult | None) -> dict[str, list[dict]]:
        if not roles:
            return {}
        result: dict[str, list[dict]] = {}
        for annotation in roles.role_annotations:
            result[annotation.block_id] = [
                {
                    "span_id": span.span_id,
                    "text": span.text,
                    "role_labels": span.role_labels,
                    "is_claim_candidate": span.is_claim_candidate,
                    "is_reject_candidate": span.is_reject_candidate,
                }
                for span in annotation.span_annotations
            ]
        return result

    @staticmethod
    def _neighbor_texts(
        blocks: list[TypedBlock],
        index: int,
        direction: int,
        limit: int,
    ) -> list[str]:
        texts: list[str] = []
        i = index + direction
        while 0 <= i < len(blocks) and len(texts) < limit:
            block = blocks[i]
            if block.block_type in _CONTEXT_BLOCK_TYPES:
                if block.block_type == "equation_block":
                    label = EquationNormalizer.extract_label(block.text) or getattr(block, "equation_label", None) or ""
                    label_part = f" label={label}" if label else ""
                    if EquationSemanticsInputBuilder._is_trusted_tex_block(block):
                        texts.append(f"[{block.block_id}]{label_part} {block.text[:_MAX_CONTEXT_CHARS]}")
                    else:
                        texts.append(
                            f"[{block.block_id}]{label_part} [EQUATION_TEXT_FROM_PDF_OMITTED_AS_UNTRUSTED]"
                        )
                else:
                    texts.append(f"[{block.block_id}] {block.text[:_MAX_CONTEXT_CHARS]}")
            i += direction
        if direction < 0:
            texts.reverse()
        return texts

    @staticmethod
    def _cartridge_label_patterns(cartridge: CartridgeContext | None) -> list[str]:
        """Cartridge-provided extra allowed equation label patterns (#368)."""
        if not cartridge:
            return []
        rules = cartridge.validation_rules if isinstance(cartridge.validation_rules, dict) else {}
        patterns = rules.get("equation_label_patterns")
        if isinstance(patterns, list):
            return [str(p) for p in patterns if p]
        return []

    @staticmethod
    def _extraction_source(block: TypedBlock) -> str:
        raw = block.raw if isinstance(block.raw, dict) else {}
        source = str(raw.get("extraction_source") or "")
        if source:
            return source
        if raw.get("parser_source") == "tex_archive":
            return "tex_source"
        return "pdf_text_layer"

    @staticmethod
    def _is_trusted_tex_block(block: TypedBlock) -> bool:
        return EquationSemanticsInputBuilder._extraction_source(block) == "tex_source"

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
