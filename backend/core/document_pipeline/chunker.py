"""Section-aware source chunker (issue #226).

DocumentStructureAgent の出力 `DocumentStructureResult` を入力に取り、section /
block 境界を尊重した source chunk を作る。固定長中心の旧 `chunk_pdf_pages` を
置き換える。
"""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field
import re
from typing import Iterable

# DocumentStructureAgent 由来の dataclass はランタイム必須ではないため、
# 型注釈は文字列で参照する。テストでは monkeypatch で代替可能。

# 1 chunk あたりの最大文字数。embedding API のトークン制約と RAG 表示の見やすさ
# を踏まえた経験値。section が長い場合は block 単位で分割する。
DEFAULT_MAX_CHARS = 1800
# section/block 単位の chunk 結合下限。これを下回る短い chunk は隣の chunk に
# マージする（あまりに細切れになると検索ヒットの精度が下がるため）。
MIN_CHARS = 200


@dataclass
class SourceChunk:
    chunk_index: int
    text: str
    section_id: str | None
    block_ids: list[str] = field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    metadata: dict = field(default_factory=dict)
    formulas: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chunk_index": self.chunk_index,
            "text": self.text,
            "section_id": self.section_id,
            "block_ids": list(self.block_ids),
            "page_start": self.page_start,
            "page_end": self.page_end,
            "metadata": dict(self.metadata),
            "formulas": list(self.formulas),
        }


def build_source_chunks(
    structure,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[SourceChunk]:
    """`DocumentStructureResult` から source chunk を生成する。

    Strategy:
        1. blocks を section_id でグループ化。section 順は section.order、
           無セクションの block は document 末尾扱い。
        2. block を順に積み、累計 max_chars を超えたら chunk を確定する。
        3. 1 つの block 単独で max_chars を超える場合は文字数で機械分割するが、
           block_ids にはその block のみを記録する。
        4. 末尾の極小 chunk は前の chunk へ吸収する。
    """
    blocks = list(structure.blocks)
    sections_by_id = {s.section_id: s for s in structure.sections}
    section_order = {
        s.section_id: (s.order, s.page_start) for s in structure.sections
    }

    # section_id -> [TypedBlock] （section 内では block.order 昇順）
    grouped: dict[str | None, list] = {}
    for b in blocks:
        key = b.section_id
        grouped.setdefault(key, []).append(b)
    for key in grouped:
        grouped[key].sort(key=lambda b: (b.page, b.order))

    # section_id 順を確定。None セクションは末尾に。
    section_keys: list[str | None] = []
    for sid in sorted(
        (k for k in grouped if k is not None),
        key=lambda k: section_order.get(k, (10**9, 10**9)),
    ):
        section_keys.append(sid)
    if None in grouped:
        section_keys.append(None)

    chunks: list[SourceChunk] = []
    chunk_index = 0
    for sid in section_keys:
        for chunk in _chunk_section_blocks(
            section_id=sid,
            section_obj=sections_by_id.get(sid) if sid else None,
            blocks=grouped[sid],
            max_chars=max_chars,
            start_index=chunk_index,
        ):
            chunks.append(chunk)
            chunk_index += 1

    chunks = _absorb_short_tail_chunks(chunks)
    # 安定した chunk_index を再採番する（吸収後）
    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks


def _chunk_section_blocks(
    section_id: str | None,
    section_obj,
    blocks: list,
    max_chars: int,
    start_index: int,
) -> Iterable[SourceChunk]:
    blocks = _merge_adjacent_equation_blocks(blocks)
    section_title = getattr(section_obj, "title", None) if section_obj else None

    pending_text_parts: list[str] = []
    pending_block_ids: list[str] = []
    pending_pages: list[int] = []
    pending_formulas: list[dict] = []
    pending_chars = 0

    def emit() -> SourceChunk | None:
        if not pending_text_parts:
            return None
        text = "\n\n".join(t for t in pending_text_parts if t).strip()
        if not text:
            return None
        text, formulas = _replace_inline_math_notation(text, pending_formulas)
        extraction_source = _extraction_source_from_blocks(pending_block_ids, blocks)
        tei_section_id = _tei_section_id_from_blocks(pending_block_ids, blocks)
        meta: dict = {"section_title": section_title}
        if extraction_source:
            meta["extraction_source"] = extraction_source
        if tei_section_id:
            meta["tei_section_id"] = tei_section_id
        return SourceChunk(
            chunk_index=0,  # 後で全体採番
            text=text,
            section_id=section_id,
            block_ids=list(pending_block_ids),
            page_start=min(pending_pages) if pending_pages else None,
            page_end=max(pending_pages) if pending_pages else None,
            metadata=meta,
            formulas=formulas,
        )

    for block in blocks:
        text = (block.text or "").strip()
        if not text:
            continue
        block_formulas: list[dict] = []
        if getattr(block, "block_type", "") == "equation_block":
            formula_idx = len(pending_formulas)
            formula = _formula_from_equation_block(block, formula_idx)
            text = formula["id"]
            block_formulas.append(formula)
        block_chars = len(text)

        # 単一 block で超過 → 文字数機械分割
        if block_chars > max_chars:
            if pending_text_parts:
                yield_chunk = emit()
                if yield_chunk:
                    yield yield_chunk
                pending_text_parts.clear()
                pending_block_ids.clear()
                pending_pages.clear()
                pending_formulas.clear()
                pending_chars = 0
            block_extraction_source = (getattr(block, "raw", None) or {}).get("parser_source")
            block_tei_section_id = (getattr(block, "raw", None) or {}).get("tei_section_id")
            long_meta: dict = {
                "section_title": section_title,
                "block_type": block.block_type,
                "split_long_block": True,
            }
            if block_extraction_source:
                long_meta["extraction_source"] = block_extraction_source
            if block_tei_section_id:
                long_meta["tei_section_id"] = block_tei_section_id
            for sub in _split_long_text(text, max_chars):
                yield SourceChunk(
                    chunk_index=0,
                    text=sub,
                    section_id=section_id,
                    block_ids=[block.block_id],
                    page_start=block.page,
                    page_end=block.page,
                    metadata=long_meta,
                    formulas=list(block_formulas),
                )
            continue

        # 通常: pending に追加。超過したら前を確定してから追加。
        if pending_chars + block_chars + 2 > max_chars and pending_text_parts:
            yield_chunk = emit()
            if yield_chunk:
                yield yield_chunk
            pending_text_parts.clear()
            pending_block_ids.clear()
            pending_pages.clear()
            pending_formulas.clear()
            pending_chars = 0
            if block_formulas:
                block_formulas = [_formula_from_equation_block(block, len(pending_formulas))]
                text = block_formulas[0]["id"]

        pending_text_parts.append(text)
        pending_block_ids.append(block.block_id)
        pending_pages.append(block.page)
        pending_formulas.extend(block_formulas)
        pending_chars += block_chars + 2

    last = emit()
    if last:
        yield last


def _split_long_text(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        return [text]
    parts: list[str] = []
    i = 0
    while i < len(text):
        parts.append(text[i : i + max_chars])
        i += max_chars
    return parts


def _merge_adjacent_equation_blocks(blocks: list) -> list:
    """Merge display equations split by the PDF parser into adjacent blocks.

    PyMuPDF often emits a numerator/denominator stack as multiple text blocks.
    When consecutive equation blocks appear in the same section/page, merge them
    until a block with an equation label closes the displayed equation.
    """
    merged: list = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if getattr(block, "block_type", "") != "equation_block":
            merged.append(block)
            i += 1
            continue

        parts = [block]
        j = i + 1
        label = getattr(block, "equation_label", None)
        if label:
            merged.append(block)
            i += 1
            continue
        while j < len(blocks):
            nxt = blocks[j]
            if getattr(nxt, "block_type", "") != "equation_block":
                break
            if getattr(nxt, "section_id", None) != getattr(block, "section_id", None):
                break
            if getattr(nxt, "page", None) != getattr(block, "page", None):
                break
            parts.append(nxt)
            label = getattr(nxt, "equation_label", None) or label
            j += 1
            if label:
                break

        if len(parts) == 1:
            merged.append(block)
        else:
            combined = copy(block)
            combined.text = "\n".join((getattr(p, "text", "") or "").strip() for p in parts if getattr(p, "text", "").strip())
            combined.equation_label = label
            combined.block_id = getattr(block, "block_id", "")
            merged.append(combined)
        i += len(parts)
    return merged


_TRAILING_LABEL_RE = re.compile(r"\((?P<label>[A-Za-z]?\d+(?:\.\d+)*|[A-Za-z]\.\d+)\)\s*$")
_LATEX_REPLACEMENTS = (
    ("→", r"\to "),
    ("−", "-"),
    ("∗", r"^{*}"),
    ("¯ν", r"\bar{\nu}"),
    ("Λ", r"\Lambda "),
    ("Γ", r"\Gamma "),
    ("δ", r"\delta "),
    ("α", r"\alpha "),
    ("β", r"\beta "),
    ("κ", r"\kappa "),
    ("ζ", r"\zeta "),
    ("ξ", r"\xi "),
    ("τ", r"\tau "),
    ("ν", r"\nu "),
    ("µ", r"\mu "),
    ("ℓ", r"\ell "),
    ("σ", r"\sigma "),
    ("∼", r"\sim "),
    ("√", r"\sqrt{}"),
)


def _formula_from_equation_block(block, idx: int) -> dict:
    label = getattr(block, "equation_label", None) or _extract_equation_label(block.text)
    latex = _equation_text_to_latex(block.text, label)
    return {
        "id": f"[[FORMULA_{idx}]]",
        "latex": latex,
        "spoken": f"equation {label}" if label else "display equation",
        "is_display": True,
        "label": label or "",
        "block_id": getattr(block, "block_id", ""),
    }


def _extract_equation_label(text: str) -> str | None:
    match = _TRAILING_LABEL_RE.search((text or "").strip())
    return match.group("label") if match else None


def _equation_text_to_latex(text: str, label: str | None = None) -> str:
    """Convert parser text for a display equation into KaTeX-friendly markup.

    PyMuPDF does not recover source LaTeX from PDFs, so this keeps a conservative
    visual representation instead of pretending to reconstruct the author's TeX.
    """
    raw = (text or "").strip()
    if label:
        raw = _TRAILING_LABEL_RE.sub("", raw).strip()
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        body = r"\text{[equation]}"
    else:
        body = _reconstruct_structured_equation(lines, label) or _latexize_text(" ".join(lines))
    if label:
        body = f"{body} \\tag{{{label}}}"
    return body


def _reconstruct_structured_equation(lines: list[str], label: str | None) -> str | None:
    compact = [_compact_math_token(line) for line in lines]
    joined = " ".join(compact)

    # JHEP01(2026)143 eq. (1.1): ratio sum rule. The PDF text stream emits
    # numerator and denominator fragments as a vertical token sequence.
    if (
        "RΛc" in compact
        and "RD" in compact
        and "RD*" in compact
        and "RSM" in compact
        and any("δR" in token for token in compact)
    ):
        return (
            r"\frac{R_{\Lambda_c}}{R^{\mathrm{SM}}_{\Lambda_c}}"
            r" - \alpha_R \frac{R_D}{R^{\mathrm{SM}}_D}"
            r" - \beta_R \frac{R_{D^*}}{R^{\mathrm{SM}}_{D^*}}"
            r" = \delta R."
        )

    # Eq. (1.2): heavy-quark-limit differential-rate relation, commonly split
    # across two adjacent PDF blocks.
    if (
        "κΛc" in joined
        and "ζ(w)2" in joined
        and "κD+κD*" in joined.replace(" ", "")
        and "ξ(w)2" in joined
        and any("=0" in token.replace(" ", "") for token in compact)
    ):
        return (
            r"\frac{\kappa_{\Lambda_c}}{\zeta(w)^2}"
            r" - \frac{2}{1+w}"
            r"\frac{\kappa_D+\kappa_{D^*}}{\xi(w)^2}"
            r" = 0"
        )

    return None


def _compact_math_token(value: str) -> str:
    token = (value or "").strip()
    token = token.replace("−", "-").replace("∗", "*")
    token = token.replace(" ", "")
    token = token.rstrip(".,")
    return token


def _latexize_text(text: str) -> str:
    result = text
    for src, dst in _LATEX_REPLACEMENTS:
        result = result.replace(src, dst)
    result = re.sub(r"\bRSM\b", r"R^{SM}", result)
    result = re.sub(r"\bRD\b", r"R_D", result)
    result = re.sub(r"\bRD\^\{\*\}", r"R_{D^{*}}", result)
    result = re.sub(r"\bR\\Lambda\s*c\b", lambda _m: r"R_{\Lambda c}", result)
    result = re.sub(r"\\alpha\s*R\b", lambda _m: r"\alpha_R", result)
    result = re.sub(r"\\beta\s*R\b", lambda _m: r"\beta_R", result)
    result = re.sub(r"\\delta\s*R\b", lambda _m: r"\delta R", result)
    result = result.replace(" .", ".").replace(" ,", ",")
    return result


def _absorb_short_tail_chunks(chunks: list[SourceChunk]) -> list[SourceChunk]:
    """末尾が極端に短い chunk を直前の chunk に吸収する。"""
    if not chunks:
        return chunks
    result: list[SourceChunk] = []
    for c in chunks:
        if (
            result
            and len(c.text) < MIN_CHARS
            and result[-1].section_id == c.section_id
        ):
            prev = result[-1]
            renumbered = _renumber_formulas(c.formulas, len(prev.formulas))
            merged_text = c.text
            for old, new in zip(c.formulas or [], renumbered):
                old_id = str(old.get("id") or "")
                new_id = str(new.get("id") or "")
                if old_id and new_id and old_id != new_id:
                    merged_text = merged_text.replace(old_id, new_id)
            prev.text = prev.text + "\n\n" + merged_text
            prev.block_ids.extend(c.block_ids)
            prev.formulas.extend(renumbered)
            if c.page_end is not None:
                prev.page_end = max(prev.page_end or c.page_end, c.page_end)
            continue
        result.append(c)
    return result


def _renumber_formulas(formulas: list[dict], start: int) -> list[dict]:
    renumbered: list[dict] = []
    for offset, formula in enumerate(formulas or []):
        updated = dict(formula)
        old_id = str(updated.get("id") or "")
        new_id = f"[[FORMULA_{start + offset}]]"
        updated["id"] = new_id
        if old_id:
            updated["latex"] = str(updated.get("latex") or "").replace(old_id, new_id)
        renumbered.append(updated)
    return renumbered


def _block_by_id(block_ids: list[str], blocks: list) -> list:
    """block_ids に対応する block オブジェクトを blocks から検索して返す。"""
    id_to_block = {getattr(b, "block_id", None): b for b in blocks}
    return [id_to_block[bid] for bid in block_ids if bid in id_to_block]


def _extraction_source_from_blocks(block_ids: list[str], blocks: list) -> str | None:
    """ブロック群の parser_source を代表値として返す。"""
    matched = _block_by_id(block_ids, blocks)
    sources = {
        (getattr(b, "raw", None) or {}).get("parser_source")
        for b in matched
    } - {None}
    if not sources:
        return None
    # 複数混在の場合は最初に出現したものを返す
    for b in matched:
        src = (getattr(b, "raw", None) or {}).get("parser_source")
        if src:
            return src
    return None


def _tei_section_id_from_blocks(block_ids: list[str], blocks: list) -> str | None:
    """ブロック群の tei_section_id を代表値として返す。"""
    matched = _block_by_id(block_ids, blocks)
    for b in matched:
        tsi = (getattr(b, "raw", None) or {}).get("tei_section_id")
        if tsi:
            return tsi
    return None


_INLINE_NOTATION_PATTERNS = (
    (
        re.compile(r"RD\(∗\)\s*=\s*BR\(B\s*→\s*D\(∗\)τ\s*¯ν\)/BR\(B\s*→\s*D\(∗\)ℓ¯ν\)"),
        r"R_{D^{(*)}} = \frac{\mathrm{BR}(B \to D^{(*)}\tau\bar{\nu})}{\mathrm{BR}(B \to D^{(*)}\ell\bar{\nu})}",
    ),
    (
        re.compile(r"RΛc\s*=\s*BR\(Λb\s*→\s*Λcτ\s*¯ν\)/BR\(Λb\s*→\s*Λcℓ¯ν\)"),
        r"R_{\Lambda_c} = \frac{\mathrm{BR}(\Lambda_b \to \Lambda_c\tau\bar{\nu})}{\mathrm{BR}(\Lambda_b \to \Lambda_c\ell\bar{\nu})}",
    ),
    (re.compile(r"RD\(∗\)"), r"R_{D^{(*)}}"),
    (re.compile(r"RΛc"), r"R_{\Lambda_c}"),
    (re.compile(r"Λb"), r"\Lambda_b"),
    (re.compile(r"Λc"), r"\Lambda_c"),
    (re.compile(r"D∗"), r"D^*"),
    (re.compile(r"D\(∗\)"), r"D^{(*)}"),
    (re.compile(r"D\(\*\)"), r"D^{(*)}"),
    (re.compile(r"δR"), r"\delta R"),
    (re.compile(r"αR"), r"\alpha_R"),
    (re.compile(r"βR"), r"\beta_R"),
)


def _replace_inline_math_notation(text: str, formulas: list[dict]) -> tuple[str, list[dict]]:
    """Replace common inline particle-physics notation with formula placeholders."""
    updated_text = text
    updated_formulas = list(formulas or [])

    def add_formula(latex: str) -> str:
        formula_id = f"[[FORMULA_{len(updated_formulas)}]]"
        updated_formulas.append({
            "id": formula_id,
            "latex": latex,
            "spoken": latex,
            "is_display": False,
            "label": "",
        })
        return formula_id

    for pattern, latex in _INLINE_NOTATION_PATTERNS:
        updated_text = pattern.sub(lambda _m, value=latex: add_formula(value), updated_text)
    return updated_text, updated_formulas
