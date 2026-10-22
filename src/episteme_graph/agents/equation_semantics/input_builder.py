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
from episteme_graph.agents.text_truncation import truncate_text
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
#: 近傍の式一覧（``neighbor_equations``）の上限・節外を拾う窓・LaTeX の先頭の長さ。
_MAX_NEIGHBOR_EQUATIONS = 12
_NEIGHBOR_EQUATION_WINDOW = 24
_NEIGHBOR_LATEX_HEAD_CHARS = 80
_ELLIPSIS = "…"
#: 文脈ブロックを上限で切って渡した理由（coverage ``details.truncated_blocks`` — IK-0808）。
TRUNCATED_CONTEXT_REASON = "context_block_over_char_limit"


def context_excerpt(text: str, limit: int, *, keep_end: bool = False) -> str:
    """文脈の抜粋を ``limit`` 字以内で、文・語の境界と数式（``$…$``）の外で切って返す（J0 / IK-0808）。

    前文脈（``keep_end=True``）は式に近い末尾側を残す。切ったときは ``…`` を付ける。
    規則の正本は ``episteme_graph.agents.text_truncation.truncate_text``。
    """
    return truncate_text(text, limit, keep_end=keep_end)[0]


_COVERAGE_UNIT = "equation_candidates"
_TRUNCATION_REASON = "max_equations"
_INLINE_TRUNCATION_REASON = "max_inline_equations"

# extraction_status のうち再構成が必要なもの
_NEEDS_RECONSTRUCTION_STATUSES = {"partial", "fragment_only", "label_only", "missing", "unparsed"}
# PDF 経路（テキスト層）の inline 候補。右辺は小数点・べき（``0.1`` / ``10^12``）の途中で
# 切らない（旧版は ``.`` と ``^`` を含めず ``0.1`` を ``= 0`` と切っていた — IK 起票中）。
# 左端は語・添字・TeX コマンドの途中から始めない（``\mathrm{halo}} = K`` のような切り出し防止）。
_INLINE_MATH_RE = re.compile(
    r"(?<![A-Za-z0-9_^{}\\])"
    r"(?P<expr>"
    r"(?:[A-Za-zα-ωΑ-Ω][A-Za-z0-9_{}()α-ωΑ-Ω]{0,16})"
    r"\s*"
    r"(?:=|≠|≈|≤|≥|<|>|\\neq|\\approx)"
    r"\s*"
    r"(?:[A-Za-z0-9_{}()α-ωΑ-Ω+\-*/π^]{1,24}(?:\.[0-9][A-Za-z0-9_{}()α-ωΑ-Ω+\-*/π^]{0,16})?)"
    r")"
)

# TeX 経路の inline 数式。``$…$`` / ``\(…\)`` の**原文境界**で切り出す（正規表現で関係式を
# 推測しない）。``tex_archive._clean_prose`` が本文の inline 数式を ``$…$`` のまま残す。
_TEX_INLINE_MATH_RE = re.compile(
    r"(?<![\\$])\$(?P<d>(?:\\\$|[^$])+?)(?<!\\)\$(?!\$)"
    r"|\\\((?P<p>.+?)\\\)",
    re.S,
)

# 関係演算子（波括弧の深さ 0 でのみ分割する）。
_RELATION_TOKEN_RE = re.compile(
    r"\\(?:leq|le|geq|ge|neq|ne|approx|simeq|sim|equiv|propto|lesssim|gtrsim|ll|gg"
    r"|coloneqq|doteq|leqslant|geqslant)(?![A-Za-z])"
    r"|:=|<=|>=|!=|[=<>≤≥≠≈∼≃≡∝]"
)
# 数値リテラル（符号・小数・``\times 10^{n}``・べき）。
_NUMBER_HEAD_RE = re.compile(
    r"[+\-−]?\s*(?:\d+(?:\.\d*)?|\.\d+)"
    r"(?:\s*\^\s*(?:\{\s*[+\-−]?\s*\d+(?:\.\d+)?\s*\}|[+\-−]?\d))?"
    r"(?:\s*(?:\\times|\\cdot|×|x)\s*10\s*\^\s*(?:\{\s*[+\-−]?\s*\d+(?:\.\d+)?\s*\}|[+\-−]?\d))?"
)
# 数値の後ろに続いてよい単位・空白の字句（これだけで尽きれば「単位付き数値」）。
_UNIT_TOKEN_RE = re.compile(
    r"\s+|\\[,;:! ]|~|/|\\%|%|[()]"
    r"|\^\s*(?:\{\s*[+\-−]?\s*\d+(?:\.\d+)?\s*\}|[+\-−]?\d)"
    r"|M_\{?\s*\\(?:odot|sun)\s*\}?|\\(?:odot|msun|Msun|degree|circ|AA)(?![A-Za-z])"
    r"|\\(?:mathrm|rm|text|textrm|mbox|unit|mathit)\s*\{[A-Za-z\s.]*\}"
    r"|\{\s*\\rm\s+[A-Za-z\s.]*\}"
    r"|(?:h|Mpc|kpc|pc|Gpc|km|s|Gyr|Myr|yr|K|eV|keV|MeV|GeV|TeV|cm|mm|nm|m|g|kg|Hz|kHz|MHz|GHz"
    r"|deg|arcmin|arcsec|sr|Jy|erg|W|J|dex)(?![A-Za-z])"
)
# 候補から除いた inline 数式を coverage の details に挙げる上限（件数ではなく列挙の上限）。
_MAX_EXCLUDED_INLINE_LISTED = 40
INLINE_EXCLUSION_VALUE_SPECIFICATION = "value_specification"


def _split_relation_sides(expr: str) -> list[str]:
    """波括弧の深さ 0 にある関係演算子で式を分割する（関係演算子が無ければ 1 要素）。"""
    sides: list[str] = []
    depth = 0
    last = 0
    i = 0
    n = len(expr)
    while i < n:
        ch = expr[i]
        if ch == "\\" and i + 1 < n and expr[i + 1] in "{}":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(depth - 1, 0)
        elif depth == 0:
            m = _RELATION_TOKEN_RE.match(expr, i)
            if m:
                sides.append(expr[last:i])
                last = m.end()
                i = m.end()
                continue
        i += 1
    sides.append(expr[last:])
    return sides


def _strip_outer_braces(text: str) -> str:
    text = text.strip()
    while text.startswith("{") and text.endswith("}"):
        inner = text[1:-1]
        depth = 0
        balanced = True
        for ch in inner:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    balanced = False
                    break
        if not balanced or depth != 0:
            break
        text = inner.strip()
    return text


def is_numeric_value_side(side: str) -> bool:
    """辺が数値リテラル（単位付きを含む）だけで尽きるか。``3R_{\\rm vir}`` は数値ではない。"""
    text = _strip_outer_braces(side)
    if not text:
        return False
    m = _NUMBER_HEAD_RE.match(text)
    if not m or m.end() == 0:
        return False
    rest = text[m.end():]
    while rest:
        u = _UNIT_TOKEN_RE.match(rest)
        if not u or u.end() == 0:
            return False
        rest = rest[u.end():]
    return True


def classify_inline_relation(expr: str) -> str | None:
    """inline 数式を式候補にしてよいかを決める。

    戻り値 ``None`` = 候補（記号どうしの等式・関係式）、``"no_relation"`` = 関係式でない、
    ``"value_specification"`` = 値の代入・パラメータ指定・閾値（``z=0`` / ``a=5`` /
    ``M>10^{12}M_\\odot`` / ``0<z<1``）。値の言及を式にすると後段（DSL・部品グラフ・
    二層説明）に偽の依存ができ、LLM 呼び出しも増える。
    """
    sides = [s for s in _split_relation_sides(expr or "")]
    if len(sides) < 2:
        return "no_relation"
    non_empty = [s for s in sides if s.strip()]
    if len(non_empty) < 2:
        # ``\\sim 10^{12}`` / ``>5`` のような片側だけの比較は値の言及。
        return INLINE_EXCLUSION_VALUE_SPECIFICATION if non_empty and all(
            is_numeric_value_side(s) for s in non_empty
        ) else "no_relation"
    numeric = sum(1 for s in non_empty if is_numeric_value_side(s))
    if numeric >= len(non_empty) - 1:
        return INLINE_EXCLUSION_VALUE_SPECIFICATION
    return None


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
        inline_excluded: list[dict] = []
        if include_inline:
            for block in ordered_blocks:
                if block.block_type != "body_paragraph":
                    continue
                found, excluded = self._scan_inline_block(block, structure.document_id)
                inline_all.extend(found)
                inline_excluded.extend(excluded)

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
            "inline_excluded": inline_excluded,
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
        truncated_blocks = self._truncated_context_blocks(plan)
        if truncated_blocks:
            # 文脈の本文を上限で切って渡したブロック（IK-0808。切った側に「…」が付く）。
            details["truncated_blocks"] = truncated_blocks
        # 値の代入・パラメータ指定として式候補から除いた inline 数式（P4: 落とした事実を残す）。
        # 母集合には入れない（式ではないので「取りこぼし」ではない）が、理由付きで列挙する。
        excluded = plan["inline_excluded"]
        if excluded:
            details["excluded_inline"] = excluded[:_MAX_EXCLUDED_INLINE_LISTED]
            details["excluded_inline_listing_truncated"] = (
                len(excluded) > _MAX_EXCLUDED_INLINE_LISTED
            )
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

    @classmethod
    def _truncated_context_blocks(cls, plan: dict) -> list[dict]:
        """文脈として上限で切って渡すブロックを ``[{block_id, reason}]`` で（IK-0808・決定論）。

        ``_neighbor_texts`` と同じ歩き方（同じ節・前後 ``_MAX_CONTEXT_BLOCKS``）で、本文が
        ``_MAX_CONTEXT_CHARS`` を超える文脈ブロックを列挙する。件数は書かない。
        """
        ordered_blocks = plan["ordered_blocks"]
        index_by_id = {b.block_id: i for i, b in enumerate(ordered_blocks)}
        targets = list(plan["selected_display_indices"])
        for candidate in plan["inline_candidates"]:
            idx = index_by_id.get(str(candidate.source_location.get("block_id") or ""))
            if idx is not None:
                targets.append(idx)
                if len(ordered_blocks[idx].text or "") > _MAX_CONTEXT_CHARS:
                    targets.append(-1 - idx)  # 対象の段落そのものも抜粋される
        seen: set[str] = set()
        result: list[dict] = []

        def _add(block) -> None:
            if block.block_id in seen:
                return
            seen.add(block.block_id)
            result.append({"block_id": block.block_id, "reason": TRUNCATED_CONTEXT_REASON})

        for target in sorted(set(targets), key=lambda v: v if v >= 0 else -1 - v):
            if target < 0:
                _add(ordered_blocks[-1 - target])
                continue
            own_section = getattr(ordered_blocks[target], "section_id", None)
            for direction in (-1, 1):
                i = target + direction
                taken = 0
                while 0 <= i < len(ordered_blocks) and taken < _MAX_CONTEXT_BLOCKS:
                    block = ordered_blocks[i]
                    if getattr(block, "section_id", None) != own_section:
                        break
                    if block.block_type in _CONTEXT_BLOCK_TYPES:
                        taken += 1
                        trusted_or_prose = (
                            block.block_type != "equation_block" or cls._is_trusted_tex_block(block)
                        )
                        if trusted_or_prose and len(block.text or "") > _MAX_CONTEXT_CHARS:
                            _add(block)
                    i += direction
        return result

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

        equation_entries = self._equation_block_entries(ordered_blocks)

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
                if candidate.source_location.get("extraction_source") == "tex_source":
                    # TeX 原文の ``$…$`` 境界で切り出した式。復元しない（reconstruction=none）。
                    latex = candidate.raw_text
                    plain_text = None
                    extraction_source = "tex_source"
                else:
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
                if source_is_trusted:
                    # TeX 原文は伏せない。対象の位置を印で示す（同じ段落に複数の式があっても曖昧にしない）。
                    target = (
                        "[INLINE_MATH_TARGET]" + block.text[start:end] + "[/INLINE_MATH_TARGET]"
                    )
                else:
                    target = "[INLINE_MATH_TARGET_OMITTED_AS_UNTRUSTED]"
                # 対象が抜粋の外に出ないよう、対象の前後をそれぞれ抜粋して挟む。
                before = context_excerpt(block.text[:start], _MAX_CONTEXT_CHARS * 2 // 3, keep_end=True)
                after = context_excerpt(block.text[end:], _MAX_CONTEXT_CHARS // 3)
                prev_texts.append(f"[{block.block_id}] {before}{target}{after}")

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
                neighbor_equations=self._neighbor_equations(
                    equation_entries, idx, block.section_id, equation_id
                ),
                candidate_text_spans=(
                    self._candidate_text_spans(ordered_blocks, idx, spans_by_block)
                    if roles is not None
                    else None
                ),
            ))
        return inputs

    @staticmethod
    def _candidate_text_spans(
        ordered_blocks: list[TypedBlock],
        index: int,
        spans_by_block: dict[str, list[dict]],
    ) -> list[dict]:
        """linked_text_spans の候補（IK-0809）。

        式のブロック自身（inline 候補なら本文の段落）と、同じ節の直前・直後の本文ブロック
        （式ブロックは飛ばす）の役割付き span を文書順に並べる。display 式でも
        「この式を導入・説明する本文」の span_id を指せるようにする。
        """
        own_section = getattr(ordered_blocks[index], "section_id", None)
        block_ids: list[str] = []
        for direction in (-1, 1):
            i = index + direction
            while 0 <= i < len(ordered_blocks):
                neighbor = ordered_blocks[i]
                if getattr(neighbor, "section_id", None) != own_section:
                    break
                if neighbor.block_type != "equation_block" and neighbor.block_type in _CONTEXT_BLOCK_TYPES:
                    block_ids.append(neighbor.block_id)
                    break
                i += direction
        ordered_ids = [
            b.block_id
            for b in ordered_blocks
            if b.block_id in set(block_ids) | {ordered_blocks[index].block_id}
        ]
        result: list[dict] = []
        for block_id in ordered_ids:
            for span in spans_by_block.get(block_id, []):
                result.append({
                    "span_id": span.get("span_id"),
                    "block_id": block_id,
                    "text": span.get("text"),
                    "role_labels": list(span.get("role_labels") or []),
                })
        return result

    def _equation_block_entries(self, ordered_blocks: list[TypedBlock]) -> list[tuple[int, str | None, dict]]:
        """文書順の式ブロックを ``(index, section_id, {equation_id, label, latex_head})`` に。

        ID は ``build_llm_inputs`` が display 式に付けるのと同じ正規化
        （``EquationNormalizer.normalize``）で決める。PDF 由来の式の文字列は信用しない
        ので ``latex_head`` は None（ID とラベルだけを渡す）。
        """
        entries: list[tuple[int, str | None, dict]] = []
        for i, block in enumerate(ordered_blocks):
            if block.block_type != "equation_block":
                continue
            equation = self._normalizer.normalize(block)
            latex_head = None
            if self._is_trusted_tex_block(block):
                head = " ".join((equation.latex or block.text or "").split())
                latex_head = head[:_NEIGHBOR_LATEX_HEAD_CHARS] or None
            entries.append((i, block.section_id, {
                "equation_id": equation.equation_id,
                "label": equation.label,
                "latex_head": latex_head,
            }))
        return entries

    @staticmethod
    def _neighbor_equations(
        entries: list[tuple[int, str | None, dict]],
        index: int,
        section_id: str | None,
        own_equation_id: str,
    ) -> list[dict]:
        """対象の近傍の式（同一節優先 → 距離 → 文書順で上限まで選び、文書順で返す）。"""
        picked: list[tuple[int, int, int, dict]] = []
        for i, sec, entry in entries:
            if entry["equation_id"] == own_equation_id or i == index:
                continue
            same = sec == section_id
            if not same and abs(i - index) > _NEIGHBOR_EQUATION_WINDOW:
                continue
            picked.append((0 if same else 1, abs(i - index), i, entry))
        picked.sort(key=lambda t: (t[0], t[1], t[2]))
        chosen = sorted(picked[:_MAX_NEIGHBOR_EQUATIONS], key=lambda t: t[2])
        seen: set[str] = set()
        result: list[dict] = []
        for _, _, _, entry in chosen:
            if entry["equation_id"] in seen:
                continue
            seen.add(entry["equation_id"])
            result.append(dict(entry))
        return result

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
        return self._scan_inline_block(block, document_id)[0]

    def _scan_inline_block(
        self, block: TypedBlock, document_id: str
    ) -> tuple[list[EquationCandidate], list[dict]]:
        """本文ブロックの inline 式候補と、値の指定として除いたものを返す。

        TeX 経路（``tex_source``）は ``$…$`` / ``\\(…\\)`` の原文境界で切り出し、PDF 経路は
        テキスト層を正規表現で拾う。どちらも値の代入・パラメータ指定は候補にしない。
        """
        if self._is_trusted_tex_block(block):
            return self._tex_inline_candidates(block, document_id)
        return self._pdf_inline_candidates(block, document_id)

    def _tex_inline_candidates(
        self, block: TypedBlock, document_id: str
    ) -> tuple[list[EquationCandidate], list[dict]]:
        candidates: list[EquationCandidate] = []
        excluded: list[dict] = []
        for match in _TEX_INLINE_MATH_RE.finditer(block.text):
            latex = " ".join((match.group("d") or match.group("p") or "").split())
            if len(latex) < 3 or len(latex) > 300:
                continue
            verdict = classify_inline_relation(latex)
            if verdict == "no_relation":
                continue
            if verdict:
                excluded.append({"block_id": block.block_id, "text": latex, "reason": verdict})
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
                    "extraction_source": "tex_source",
                },
                raw_text=latex,
                matched_label=None,
                detection_method=["inline_equation_heuristic", "tex_source"],
                candidate_score=min(block.confidence, 0.55),
                extraction_status="unparsed",   # gate が上書きする
                acceptance_status="provisional",
                accepted_equation_id=None,
                merge_target_hint=None,
                needs_math_review=False,
                review_reason=[],
            ))
        return candidates, excluded

    def _pdf_inline_candidates(
        self, block: TypedBlock, document_id: str
    ) -> tuple[list[EquationCandidate], list[dict]]:
        candidates: list[EquationCandidate] = []
        excluded: list[dict] = []
        for match in _INLINE_MATH_RE.finditer(block.text):
            expr = " ".join(match.group("expr").split()).rstrip(".,;:")
            if len(expr) < 3 or len(expr) > 80:
                continue
            verdict = classify_inline_relation(expr)
            if verdict == "no_relation":
                continue
            if verdict:
                excluded.append({"block_id": block.block_id, "text": expr, "reason": verdict})
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
        return candidates, excluded

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
        # 文脈は同じ節の中に限る（付録の式の前に結論の段落を入れない — J1 seq 1858）。
        own_section = getattr(blocks[index], "section_id", None) if 0 <= index < len(blocks) else None
        i = index + direction
        while 0 <= i < len(blocks) and len(texts) < limit:
            block = blocks[i]
            if getattr(block, "section_id", None) != own_section:
                break
            if block.block_type in _CONTEXT_BLOCK_TYPES:
                if block.block_type == "equation_block":
                    label = EquationNormalizer.extract_label(block.text) or getattr(block, "equation_label", None) or ""
                    label_part = f" label={label}" if label else ""
                    if EquationSemanticsInputBuilder._is_trusted_tex_block(block):
                        texts.append(
                            f"[{block.block_id}]{label_part} "
                            f"{context_excerpt(block.text, _MAX_CONTEXT_CHARS, keep_end=direction < 0)}"
                        )
                    else:
                        texts.append(
                            f"[{block.block_id}]{label_part} [EQUATION_TEXT_FROM_PDF_OMITTED_AS_UNTRUSTED]"
                        )
                else:
                    texts.append(
                        f"[{block.block_id}] "
                        f"{context_excerpt(block.text, _MAX_CONTEXT_CHARS, keep_end=direction < 0)}"
                    )
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
        raw = getattr(block, "raw", None)
        raw = raw if isinstance(raw, dict) else {}
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
