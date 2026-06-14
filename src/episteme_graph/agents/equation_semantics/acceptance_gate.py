"""EquationAcceptanceGate: candidate acceptance / rejection / merge classification.

Issue #245 で定義された受け入れ基準に従い、EquationCandidate を決定論的に分類する。
Issue #259: high-signal 候補を provisional として保持する。
LLM を使用しない。
"""
from __future__ import annotations

import re

from .schema import EquationCandidate

# --- heuristic patterns ---

# ラベルのみ: "(3.14)", "(A.2)", "(B.10)" などの式番号だけのもの
# パターン: letter.digits  or  (optional_letter)digits(.digits)*
_LABEL_ONLY_RE = re.compile(
    r"^\s*\("
    r"(?:[A-Za-z]\.\d+|[A-Za-z]?\d+(?:\.\d+)*)"
    r"\)\s*$"
)

# 関係・代入・等式演算子の存在 (式本体があることの証拠)
# ただし operator-only チェックの後で使うため、LHS/RHS を伴う文脈で判定する
_RELATION_RE = re.compile(r"[<>≤≥≠≈∝]|\\(?:leq|geq|neq|approx|equiv|sim|propto)\b|(?<=[A-Za-z0-9\s])=(?=[A-Za-z0-9\s])")

# 演算子のみ (断片の証拠) — = を含む単独演算子も fragment として扱う
# 注意: LaTeX コマンド名の文字 (s, u, m, ...) を誤って含めないよう、Unicode 演算子のみを指定する
_OPERATOR_ONLY_RE = re.compile(
    r"^[\s"
    r"=\+\-\*\/\×\÷\±\^"
    r"∑∫∏∂∆∇√"
    r"≤≥≠≈∝→←↔⟨⟩\|"
    r"]+$"
)

# 単独の LaTeX コマンドのみ (例: \sum, \int, \lim) → fragment
_LATEX_ONLY_RE = re.compile(r"^\s*\\[A-Za-z]+(?:\[[^\]]*\])?(?:\{[^}]*\})?\s*$")

# 明らかに短すぎる (<=3 文字で関係演算子なし → fragment)
_MAX_FRAGMENT_LEN = 3

# 式本体として認められる最小の記号パターン
_EQUATION_BODY_RE = re.compile(
    r"[A-Za-z0-9α-ωΑ-Ω]"  # letter / greek
    r".*"
    r"[=<>≤≥≠≈∝]"                              # relation
)


class EquationAcceptanceGate:
    """候補に acceptance_status / extraction_status を付与する決定論的ゲート。"""

    def process(self, candidates: list[EquationCandidate]) -> list[EquationCandidate]:
        """全候補を分類し、needs_merge 候補の merge hint を付与して返す。"""
        classified = [self._classify(c) for c in candidates]
        self._propagate_merge_hints(classified)
        return classified

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify(self, candidate: EquationCandidate) -> EquationCandidate:
        text = candidate.raw_text.strip()

        if not text:
            return self._mark(candidate, "missing", "context_only", True, ["equation_body_missing"])

        # Issue #368: a candidate with table provenance (or one strongly matching
        # a coefficient table) must not auto-confirm as a standalone equation.
        # High-signal candidates are kept review-required as provisional so the
        # downstream reconstruction / review layer can still inspect them.
        if self._is_table_derived(candidate, text):
            reasons = ["table_derived_equation_candidate", "needs_math_review"]
            if self._is_high_signal(candidate):
                return self._mark(candidate, "partial", "provisional", True, reasons)
            return self._mark(candidate, "partial", "context_only", True, reasons)

        if _LABEL_ONLY_RE.match(text):
            # High-signal: has a matched_label or display math → provisional (issue #259)
            if self._is_high_signal(candidate):
                return self._mark(candidate, "label_only", "provisional", True,
                                  ["label_only_candidate", "needs_math_review"])
            return self._mark(candidate, "label_only", "rejected", True, ["label_only_candidate"])

        if self._is_operator_only(text):
            if self._is_high_signal(candidate):
                return self._mark(candidate, "fragment_only", "provisional", True,
                                  ["fragment_only_candidate", "needs_merge"])
            return self._mark(candidate, "fragment_only", "needs_merge", True,
                              ["fragment_only_candidate", "needs_merge"])

        if self._is_too_short_without_relation(text):
            if self._is_high_signal(candidate):
                return self._mark(candidate, "fragment_only", "provisional", True,
                                  ["fragment_only_candidate", "needs_merge"])
            return self._mark(candidate, "fragment_only", "needs_merge", True,
                              ["fragment_only_candidate", "needs_merge"])

        if _EQUATION_BODY_RE.search(text) or _RELATION_RE.search(text):
            # 式本体と認められる
            needs_review = self._is_suspicious(text)
            if needs_review:
                reasons = ["unparsed_math"] if not _RELATION_RE.search(text) else []
                return self._mark(candidate, "partial", "accepted", True, reasons)
            candidate.extraction_status = "complete"
            candidate.acceptance_status = "accepted"
            candidate.needs_math_review = False
            return candidate

        # 何も合致しない → unparsed; high-signal candidates get provisional
        if self._is_high_signal(candidate):
            return self._mark(candidate, "unparsed", "provisional", True, ["ambiguous_math_text"])
        return self._mark(candidate, "unparsed", "context_only", True, ["ambiguous_math_text"])

    @classmethod
    def _is_table_derived(cls, candidate: EquationCandidate, text: str) -> bool:
        """Table provenance or strong coefficient-table match (issue #368)."""
        src_loc = candidate.source_location or {}
        # table provenance is authoritative — accept it unconditionally.
        if src_loc.get("table_provenance"):
            return True
        if "table_derived_equation_candidate" in (candidate.review_reason or []):
            return True
        return cls._looks_like_coefficient_table(text)

    # A row of bare numeric coefficients separated by whitespace / delimiters
    # with no relational equation structure. e.g. "0.12  0.34  0.56".
    _COEFF_ROW_RE = re.compile(r"^[\s\d.,;:+\-eE×x()]+$")
    # A "decimal coefficient × parameter" product, e.g. "3.488 beta_2",
    # "0.28 beta_K2", "1.2 \alpha". The parameter must be a multi-letter token
    # (or a LaTeX command), so single-symbol coefficients like "2x" / "0.5 x"
    # are not matched.
    _COEFF_PARAM_RE = re.compile(
        r"\d+\.\d+\s*\*?\s*(?:\\[A-Za-z]+|[A-Za-z]{2,})(?:_\{?[A-Za-z0-9]+\}?)?"
    )
    # Minimum number of coefficient×parameter products for a linear-combination
    # table cell (issue #368 example has "3.488 beta_2 + 0.28 beta_K2 + ...").
    _MIN_COEFF_PARAM_TERMS = 2

    @classmethod
    def _looks_like_coefficient_table(cls, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        # 1) A bare numeric row with no relation operator (pure table cells).
        if not _RELATION_RE.search(stripped):
            numbers = re.findall(r"[-+]?\d*\.\d+|[-+]?\d+", stripped)
            if len(numbers) >= 3 and cls._COEFF_ROW_RE.match(stripped):
                return True
        # 2) A linear combination of decimal-coefficient × parameter products,
        #    even when it carries an "=" (the table lists an example formula).
        if len(cls._COEFF_PARAM_RE.findall(stripped)) >= cls._MIN_COEFF_PARAM_TERMS:
            return True
        return False

    @staticmethod
    def _is_high_signal(candidate: EquationCandidate) -> bool:
        """High-signal 判定: 以下のいずれかを満たす候補は provisional として保持する (issue #259).

        - matched_label が non-empty (式番号参照あり) — 最も強い信号
        - detection_method に equation_number_pattern が含まれる (label pattern-matched)
        - document_structure_equation_block 由来で分類信頼度が高い

        注意: 古いPDFでは式番号や演算子が壊れ、式本体が unparsed になることがある。
        DocumentStructureAgent 側で equation_block として高信頼に分類済みなら、
        vision/reconstruction 層へ渡すため provisional として保持する。
        """
        if candidate.matched_label:
            return True
        dm = candidate.detection_method or []
        if "equation_number_pattern" in dm:
            return True
        if "document_structure_equation_block" in dm and candidate.candidate_score >= 0.75:
            return True
        return False

    @staticmethod
    def _is_operator_only(text: str) -> bool:
        return bool(_OPERATOR_ONLY_RE.fullmatch(text)) or bool(_LATEX_ONLY_RE.match(text))

    @staticmethod
    def _is_too_short_without_relation(text: str) -> bool:
        """3 文字以下で関係演算子を含まない場合は fragment とみなす。"""
        stripped = text.strip()
        return len(stripped) <= _MAX_FRAGMENT_LEN and not _RELATION_RE.search(stripped)

    @staticmethod
    def _is_suspicious(text: str) -> bool:
        """partial / needs_review の判定: 括弧や分数の構造が明らかに壊れている兆候。

        issue #245 注記: `x = 0`, `m = 1`, `a = b` のように短くても式本体として
        成立しているものは suspicious にしない。構造破損（括弧不一致）のみ検知。
        """
        open_parens = text.count("(") - text.count(")")
        open_braces = text.count("{") - text.count("}")
        if abs(open_parens) >= 2 or abs(open_braces) >= 2:
            return True
        return False

    @staticmethod
    def _mark(
        candidate: EquationCandidate,
        extraction_status: str,
        acceptance_status: str,
        needs_review: bool,
        reasons: list[str],
    ) -> EquationCandidate:
        candidate.extraction_status = extraction_status
        candidate.acceptance_status = acceptance_status
        candidate.needs_math_review = needs_review
        for r in reasons:
            if r not in candidate.review_reason:
                candidate.review_reason.append(r)
        return candidate

    @staticmethod
    def _propagate_merge_hints(candidates: list[EquationCandidate]) -> None:
        """隣接する needs_merge 候補に merge_target_hint を付与する。"""
        for i, c in enumerate(candidates):
            if c.acceptance_status != "needs_merge":
                continue
            # 前後の accepted / needs_merge 候補を探す
            for offset in (-1, 1):
                j = i + offset
                if 0 <= j < len(candidates):
                    neighbor = candidates[j]
                    if neighbor.acceptance_status in ("accepted", "needs_merge"):
                        same_section = (
                            c.source_location.get("section_id")
                            == neighbor.source_location.get("section_id")
                        )
                        same_page = (
                            c.source_location.get("page")
                            == neighbor.source_location.get("page")
                        )
                        if same_section and same_page:
                            c.merge_target_hint = neighbor.candidate_id
                            break
