"""BlockClassifier: レイアウト情報とルールベースで各ブロックに block_type を付与する。

設計方針:
- structure-first（パーサ・レイアウト信号を優先）
- 意味解釈は行わない
- cartridge があれば notation_patterns を補助信号として使用（依存はしない）
- 曖昧なブロックは unknown に落とす（情報を削除しない）
"""
from __future__ import annotations

import re
from typing import Optional

from .schema import CartridgeContext, RawBlock, TypedBlock

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Section number prefixes: "1 Intro", "1.1 Sub", "A Appendix", "A.1 Sub", "B.2 Sub"
_SECTION_NUM_RE = re.compile(
    r"^(\d+\.?(?:\d+\.?)*|[A-Z](?:\.\d+)*)\s+[A-Z　-鿿]"
)

# Equation label at end of text: "(1)", "(1.1)", "(3.14)", "(A.1)"
_EQ_LABEL_RE = re.compile(r"\(\s*[A-Z]?\d+(?:\.\d+)*\s*\)\s*$")

# References section header (English / Japanese)
_REFS_HEADER_RE = re.compile(
    r"^(References|Bibliography|REFERENCES|参考文献|文献)\s*$"
)

# Reference entry: [1] ... or [Smith, 2020] ...
_REF_ENTRY_RE = re.compile(r"^\[[\w,\.&\s\d]+\]")

# Figure caption: "Figure 1.", "Fig. 1", "FIG. 1", "図 1"
_FIGURE_RE = re.compile(r"^(Figure|Fig\.?|FIG\.?|図)\s*\d+", re.IGNORECASE)

# Table caption: "Table 1", "TABLE 1", "表 1"
_TABLE_RE = re.compile(r"^(Table|TABLE|表)\s*\d+", re.IGNORECASE)

# High-density math: common math / Greek symbols
_MATH_SYMBOLS = frozenset(
    "=<>≤≥≠∝∈∉∋⊂⊃⊆⊇∩∪∧∨¬∀∃∅∞∂∇√∫∮∑∏"
    "αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
    "±×÷·−"
)

# Footnote-number prefix: "1 ", "* ", "† "
_FOOTNOTE_PREFIX_RE = re.compile(r"^[\d\*†‡§¶]+\s")


class BlockClassifier:
    """RawBlock のリストを受け取り TypedBlock のリストを返す。"""

    # Heuristic thresholds
    MAX_HEADING_CHARS = 150
    MIN_MATH_SYMBOL_RATIO = 0.12
    MAX_EQUATION_WORDS = 20
    FOOTNOTE_MAX_FONT_SIZE = 9.0
    FOOTNOTE_PAGE_BOTTOM_RATIO = 0.85  # page の下 15% なら脚注候補

    def classify(
        self,
        blocks: list[RawBlock],
        cartridge: CartridgeContext | None = None,
        page_heights: dict[int, float] | None = None,
    ) -> list[TypedBlock]:
        # Precompile cartridge notation patterns if available
        notation_regexes = self._compile_notation_patterns(cartridge)

        # Compute per-page median font size for relative comparison
        median_font = self._compute_median_font_size(blocks)

        in_references = False
        typed: list[TypedBlock] = []

        for i, block in enumerate(blocks):
            prev_block = blocks[i - 1] if i > 0 else None
            next_block = blocks[i + 1] if i + 1 < len(blocks) else None

            block_type, confidence = self._classify_one(
                block,
                prev_block=prev_block,
                next_block=next_block,
                in_references=in_references,
                median_font=median_font,
                notation_regexes=notation_regexes,
                page_heights=page_heights,
            )

            # Track references section entry
            if _REFS_HEADER_RE.match(block.text.strip()):
                in_references = True

            eq_label: str | None = None
            if block_type == "equation_block":
                m = _EQ_LABEL_RE.search(block.text)
                if m:
                    eq_label = m.group().strip("() ")

            block_id = f"blk_{block.page:03d}_{block.order:04d}"
            typed.append(
                TypedBlock(
                    block_id=block_id,
                    page=block.page,
                    order=block.order,
                    text=block.text,
                    block_type=block_type,
                    bbox=block.bbox,
                    confidence=confidence,
                    equation_label=eq_label,
                    raw={"parser_source": "pymupdf"},
                )
            )

        return typed

    # ------------------------------------------------------------------
    # Core classification logic
    # ------------------------------------------------------------------

    def _classify_one(
        self,
        block: RawBlock,
        *,
        prev_block: RawBlock | None,
        next_block: RawBlock | None,
        in_references: bool,
        median_font: float | None,
        notation_regexes: list,
        page_heights: dict[int, float] | None,
    ) -> tuple[str, float]:
        text = block.text.strip()

        if not text:
            return "unknown", 0.5

        # --- References section header ---
        if _REFS_HEADER_RE.match(text):
            return "section_heading", 0.95

        # --- Inside references section ---
        if in_references:
            return self._classify_in_references(block, text)

        # --- Figure caption ---
        if _FIGURE_RE.match(text):
            return "figure_caption", 0.95

        # --- Table caption ---
        if _TABLE_RE.match(text):
            return "table_caption", 0.95

        # --- Footnote ---
        if self._looks_like_footnote(block, page_heights):
            return "footnote", 0.80

        # --- Equation block ---
        if self._looks_like_equation(block, notation_regexes):
            return "equation_block", 0.85

        # --- Section / Subsection heading ---
        heading_level = self._get_heading_level(block, median_font)
        if heading_level == 1:
            return "section_heading", 0.88
        if heading_level == 2:
            return "subsection_heading", 0.85
        if heading_level == 3:
            return "subsection_heading", 0.80

        # --- Default: body paragraph ---
        return "body_paragraph", 0.80

    def _classify_in_references(
        self, block: RawBlock, text: str
    ) -> tuple[str, float]:
        # Might be an appendix heading after references
        if self._looks_like_strong_heading(block):
            return "section_heading", 0.70
        if _REF_ENTRY_RE.match(text):
            return "reference_entry", 0.92
        return "reference_entry", 0.75

    # ------------------------------------------------------------------
    # Heading detection
    # ------------------------------------------------------------------

    def _get_heading_level(
        self, block: RawBlock, median_font: float | None
    ) -> int:
        """0 = not a heading, 1/2/3 = heading level."""
        text = block.text.strip()

        # Too long
        if len(text) > self.MAX_HEADING_CHARS:
            return 0

        # Ends with sentence-terminating punctuation (not a heading)
        if text.endswith(".") and not re.match(r"^\d+\.", text):
            return 0

        # Has clear section-number prefix
        m = _SECTION_NUM_RE.match(text)
        if m:
            prefix = m.group(1).rstrip(".")  # "2." → "2", "2.1." → "2.1"
            dots = prefix.count(".")
            if dots == 0:
                return 1
            if dots == 1:
                return 2
            return 3

        # Bold + short + starts with capital: likely heading
        if block.is_bold and len(text) < 80 and text[0].isupper():
            return 1

        # Larger-than-median font + short + no period
        if (
            median_font
            and block.font_size
            and block.font_size > median_font * 1.15
            and len(text) < 80
            and not text.endswith(".")
        ):
            return 1

        return 0

    def _looks_like_strong_heading(self, block: RawBlock) -> bool:
        """references セクション内で appendix 見出しを判定する。"""
        text = block.text.strip()
        if len(text) > self.MAX_HEADING_CHARS:
            return False
        if _SECTION_NUM_RE.match(text):
            return True
        if block.is_bold and len(text) < 60:
            return True
        return False

    # ------------------------------------------------------------------
    # Equation detection
    # ------------------------------------------------------------------

    def _looks_like_equation(
        self, block: RawBlock, notation_regexes: list
    ) -> bool:
        text = block.text.strip()

        # Long blocks are prose, not display equations
        if len(text) > 300:
            return False

        # Has equation label → strong signal
        if _EQ_LABEL_RE.search(text):
            # Also require some math content or very short text
            math_chars = sum(1 for c in text if c in _MATH_SYMBOLS)
            if math_chars > 0 or "=" in text or len(text.split()) < 10:
                return True

        # High math-symbol density
        if text:
            ratio = sum(1 for c in text if c in _MATH_SYMBOLS) / len(text)
            if ratio > self.MIN_MATH_SYMBOL_RATIO:
                return True

        # Centered, contains "=" or math, few words
        words = text.split()
        if (
            block.is_centered
            and len(words) < self.MAX_EQUATION_WORDS
            and ("=" in text or any(c in _MATH_SYMBOLS for c in text))
        ):
            non_alpha = sum(1 for w in words if not w.isalpha())
            if non_alpha > len(words) * 0.3:
                return True

        # Cartridge notation patterns match and centered → equation candidate
        if block.is_centered and notation_regexes:
            for pat in notation_regexes:
                if pat.search(text):
                    return True

        return False

    # ------------------------------------------------------------------
    # Footnote detection
    # ------------------------------------------------------------------

    def _looks_like_footnote(
        self, block: RawBlock, page_heights: dict[int, float] | None
    ) -> bool:
        if block.font_size is None:
            return False

        # Very small font + starts with footnote marker
        if block.font_size <= self.FOOTNOTE_MAX_FONT_SIZE:
            if _FOOTNOTE_PREFIX_RE.match(block.text.strip()):
                return True

        # Near page bottom + small font
        if page_heights and block.bbox:
            height = page_heights.get(block.page)
            if height and block.bbox[1] > height * self.FOOTNOTE_PAGE_BOTTOM_RATIO:
                if block.font_size <= self.FOOTNOTE_MAX_FONT_SIZE:
                    return True

        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_median_font_size(blocks: list[RawBlock]) -> float | None:
        sizes = [b.font_size for b in blocks if b.font_size is not None and b.font_size > 0]
        if not sizes:
            return None
        sizes.sort()
        mid = len(sizes) // 2
        return sizes[mid]

    @staticmethod
    def _compile_notation_patterns(cartridge: CartridgeContext | None) -> list:
        if not cartridge or not cartridge.notation_patterns:
            return []
        compiled = []
        for np in cartridge.notation_patterns:
            pattern = np.get("pattern") if isinstance(np, dict) else None
            if pattern:
                try:
                    compiled.append(re.compile(pattern))
                except re.error:
                    pass
        return compiled
