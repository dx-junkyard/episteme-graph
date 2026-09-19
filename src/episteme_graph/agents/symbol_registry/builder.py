"""SymbolRegistryBuilder (issue #355).

EquationSemanticsResult の defined_symbols / used_symbols を走査し、文書単位の
SymbolRegistryResult を決定論的に構築する非LLMビルダー。

- 表記ゆれは一般的な数式記法の正規化（LaTeX ギリシャ文字コマンド → Unicode、
  装飾コマンド除去）と cartridge の aliases で同一 symbol_id に集約する
- 同一記号が複数の式で定義される場合は redefinition として
  review_reasons=["symbol_redefinition"] を付与する
- scope（equation_local / section / document）は定義・使用の分布から導出する
- unit / domain_constraints は原文に無ければ None / 空のまま保持する
  （情報を落とさない。LLM enrichment を将来追加する場合は llm_proposed 止まり）
"""
from __future__ import annotations

import logging
import re

from episteme_graph.agents.cartridge_loader import load_cartridge_or_none
from episteme_graph.agents.coverage_report import build_coverage_report

from .cartridge_loader import CartridgeContext, CartridgeLoader
from .schema import (
    MAX_SYMBOL_LENGTH,
    SYMBOL_REGISTRY_VERSION,
    SymbolRecord,
    SymbolRegistryResult,
    ValidationIssue,
)

logger = logging.getLogger(__name__)

# General math notation only — no field- or paper-specific vocabulary here
# (domain knowledge belongs in the cartridge).
_LATEX_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "θ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ",
    "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω",
}

_WRAPPER_COMMANDS = ("mathrm", "mathbf", "mathit", "mathcal", "text", "boldsymbol")


def normalize_symbol(raw: str) -> str:
    """Normalise general math notation so variants share a canonical key.

    ``\\beta`` → ``β``, ``\\mathrm{b}_1`` → ``b_1``, ``$x$`` → ``x``.
    Case is preserved (b and B are different symbols in math).
    """
    text = str(raw or "").strip().strip("$").strip()
    if not text:
        return ""
    for command in _WRAPPER_COMMANDS:
        text = re.sub(r"\\" + command + r"\{([^{}]*)\}", r"\1", text)
    for name, glyph in _LATEX_GREEK.items():
        text = re.sub(r"\\" + name + r"(?![A-Za-z])", glyph, text)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\s+", "", text)
    return text


# ---------------------------------------------------------------------------
# 候補の形式ゲート（2026-09-19）
#
# 上流（equation_semantics）の ``defined_symbols`` / ``used_symbols`` には記号で
# ないものが混ざる。2026-09-15 のコーパスでは ``0.015`` ``1`` ``10^-2`` のような
# 数値リテラル、``angular variables / basis functions`` のような語句、
# ``\begin{pmatrix}…`` のような環境、``Equation12`` ``Eq. (3.7)`` のような参照語が
# canonical_symbol として登録されていた。判定は**書式だけ**で行い、分野語は
# ハードコードしない。弾いた候補は捨てずに coverage に理由付きで残す（P4）。
# ---------------------------------------------------------------------------

# 文書内参照の語（Eq. (3.7) / Equation12 / Fig. 2 / Table III / Sec. 4）。言語
# レベルの構造語であって分野語彙ではない。
_REFERENCE_WORD_RE = re.compile(
    r"^(?:eq|eqn|eqs|equation|equations|fig|figs|figure|figures|tab|table|tables"
    r"|sec|section|sections|app|appendix|chap|chapter|ref|refs|thm|theorem|lemma)"
    r"\.?\s*\(?[0-9IVXivx][0-9A-Za-z.\-]*\)?$",
    re.IGNORECASE,
)

# 3 文字以上の英字の連なり（語らしさの指標）。
_WORD_RUN_RE = re.compile(r"[A-Za-z]{3,}")

_LATEX_ENVIRONMENT_MARKERS = ("\\begin", "\\end")


def exclusion_reason(raw: str, normalized: str) -> str | None:
    """候補を記号として登録しない理由（登録してよければ ``None``）。

    ``raw`` は上流が渡した生の文字列、``normalized`` は
    :func:`normalize_symbol` を通した後の形。
    """
    raw_text = str(raw or "").strip()
    token = str(normalized or "").strip()
    if not token:
        return None  # 空は呼び出し側が既に落としている
    lowered = raw_text.lower()
    if any(marker in lowered for marker in _LATEX_ENVIRONMENT_MARKERS):
        return "latex_environment"
    if _REFERENCE_WORD_RE.match(token) or _REFERENCE_WORD_RE.match(raw_text):
        return "document_reference"
    if not any(ch.isalpha() for ch in token):
        # 英字もギリシャ文字も無い = 数値・演算子の並び（0.015 / 2! / (3,2)）。
        return "numeric_literal"
    if token.startswith("("):
        # 括弧で始まるのは式・組（``(2π)^3``）であって記号の名前ではない。
        return "expression"
    if "\\" not in raw_text and len(_WORD_RUN_RE.findall(raw_text)) >= 2 and (
        " " in raw_text or "/" in raw_text or "," in raw_text
    ):
        # 3 文字以上の語が 2 つ以上並ぶ区切り付きの文字列は語句であって記号でない。
        # LaTeX 制御綴りを含むもの（``f_{\mathrm{Nyq}}``）は除外しない。
        return "phrase"
    if len(token) > MAX_SYMBOL_LENGTH:
        return "too_long"
    return None


# ---------------------------------------------------------------------------
# 単位の抽出（2026-09-19）
#
# ``unit`` は 100 % 空だった。原文が単位を明示している形のうち、決定論で確実に
# 取れるものだけを拾う。単位「語彙」はハードコードしない（分野固有になる）ので、
# 拾った語が単位記号の**書式**（大文字か数字か ``/ ^ ⊙`` を含む: Mpc / GeV /
# km/s / h^-1）を満たすときに限り採用する。``in units of the critical density``
# のような全小文字の語句は採らない（保守側に倒す）。原文に無ければ None のまま。
# ---------------------------------------------------------------------------

_UNIT_PHRASE_RE = re.compile(r"\bin\s+units?\s+of\s+([^\s,.;:()]+)", re.IGNORECASE)
_UNIT_BRACKET_RE = re.compile(r"\bunits?\s*[:=]?\s*\[([^\]\s]+)\]", re.IGNORECASE)
_MAX_UNIT_LENGTH = 16


def _looks_like_unit(token: str) -> bool:
    text = str(token or "").strip().strip(".,;:")
    if not text or len(text) > _MAX_UNIT_LENGTH:
        return False
    if not any(ch.isalpha() for ch in text):
        return False
    return any(ch.isupper() or ch.isdigit() or ch in "/^⊙" for ch in text)


def extract_unit(texts) -> str | None:
    """First unit stated verbatim by the definition evidence (else ``None``)."""
    for text in texts or []:
        for pattern in (_UNIT_BRACKET_RE, _UNIT_PHRASE_RE):
            for match in pattern.finditer(str(text or "")):
                token = match.group(1).strip().strip(".,;:")
                if _looks_like_unit(token):
                    return token
    return None


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_")
    return slug or "sym"


class _SymbolAccumulator:
    def __init__(self, canonical: str) -> None:
        self.canonical = canonical
        self.variants: list[str] = []
        self.defining_equation_ids: list[str] = []
        self.used_in_equation_ids: list[str] = []
        self.section_ids: set[str] = set()
        self.evidence_texts: list[str] = []
        self.evidence_ids: list[str] = []
        self.upstream_redefined = False

    def add_variant(self, raw: str) -> None:
        raw = str(raw or "").strip()
        if raw and raw not in self.variants:
            self.variants.append(raw)

    def add_defining(self, eq_id: str) -> None:
        if eq_id and eq_id not in self.defining_equation_ids:
            self.defining_equation_ids.append(eq_id)

    def add_used(self, eq_id: str) -> None:
        if eq_id and eq_id not in self.used_in_equation_ids:
            self.used_in_equation_ids.append(eq_id)


class SymbolRegistryBuilder:
    """Deterministic registry assembly. Works with or without a cartridge."""

    def __init__(self, cartridge_base_dir: str | None = None) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)

    def run(
        self,
        equations,
        cartridge_id: str | None = None,
        annotate_equations: bool = True,
    ) -> SymbolRegistryResult:
        cartridge = self._load_cartridge(cartridge_id)
        document_id = str(getattr(equations, "document_id", "") or "")
        records = list(getattr(equations, "equations", []) or [])

        alias_map = self._alias_map(cartridge)
        accumulators: dict[str, _SymbolAccumulator] = {}
        excluded: list[dict] = []
        excluded_index: dict[tuple[str, str], dict] = {}

        for record in records:
            eq_id = str(getattr(record, "equation_id", "") or "")
            sem = getattr(record, "semantics", None)
            if sem is None:
                continue
            src = getattr(record, "source_extraction", None)
            location = getattr(src, "source_location", None) or {}
            section_id = str(location.get("section_id") or "")
            sem_evidence_ids = [
                str(v) for v in (getattr(sem, "source_evidence_ids", []) or []) if v
            ]

            for ds in getattr(sem, "defined_symbols", []) or []:
                raw = str(getattr(ds, "symbol", "") or "")
                if not raw:
                    continue
                acc = self._accumulator_or_exclude(
                    accumulators, raw, alias_map, eq_id, excluded, excluded_index
                )
                if acc is None:
                    continue
                acc.add_variant(raw)
                if section_id:
                    acc.section_ids.add(section_id)
                status = str(getattr(ds, "definition_status", "") or "")
                if status in ("defined", "redefined"):
                    acc.add_defining(eq_id)
                    evidence = getattr(ds, "evidence_text", None)
                    if evidence and evidence not in acc.evidence_texts:
                        acc.evidence_texts.append(str(evidence))
                    for ev_id in sem_evidence_ids:
                        if ev_id not in acc.evidence_ids:
                            acc.evidence_ids.append(ev_id)
                    if status == "redefined":
                        acc.upstream_redefined = True
                else:
                    acc.add_used(eq_id)

            for raw in getattr(sem, "used_symbols", []) or []:
                raw = str(raw or "")
                if not raw:
                    continue
                acc = self._accumulator_or_exclude(
                    accumulators, raw, alias_map, eq_id, excluded, excluded_index
                )
                if acc is None:
                    continue
                acc.add_variant(raw)
                acc.add_used(eq_id)
                if section_id:
                    acc.section_ids.add(section_id)

        symbol_records, symbol_id_by_key = self._build_records(
            document_id, accumulators, cartridge
        )

        if annotate_equations:
            record_by_id = {r.symbol_id: r for r in symbol_records}
            self._annotate_equations(
                records, alias_map, symbol_id_by_key, record_by_id
            )

        excluded_keys = {entry["normalized"] for entry in excluded}
        return SymbolRegistryResult(
            document_id=document_id,
            registry_version=SYMBOL_REGISTRY_VERSION,
            cartridge_id=cartridge.cartridge_id if cartridge else cartridge_id,
            records=symbol_records,
            validation_issues=[],
            coverage=build_coverage_report(
                population=len(symbol_records) + len(excluded_keys),
                processed=len(symbol_records),
                reasons=[entry["reason"] for entry in excluded],
                unit="symbol_candidates",
                details={"excluded": excluded} if excluded else None,
            ),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_cartridge(self, cartridge_id: str | None) -> CartridgeContext | None:
        if not cartridge_id:
            return None
        return load_cartridge_or_none(self._cartridge_loader, cartridge_id)

    @staticmethod
    def _alias_map(cartridge: CartridgeContext | None) -> dict[str, str]:
        """normalized alias → cartridge canonical name."""
        mapping: dict[str, str] = {}
        if not cartridge or not cartridge.aliases:
            return mapping
        for canonical, aliases in cartridge.aliases.items():
            canonical_key = normalize_symbol(canonical)
            if canonical_key:
                mapping[canonical_key] = str(canonical)
            for alias in aliases or []:
                alias_key = normalize_symbol(alias)
                if alias_key:
                    mapping[alias_key] = str(canonical)
        return mapping

    @classmethod
    def _canonical_key(cls, raw: str, alias_map: dict[str, str]) -> str:
        normalized = normalize_symbol(raw)
        canonical = alias_map.get(normalized)
        if canonical:
            return normalize_symbol(canonical)
        return normalized

    @classmethod
    def _accumulator_or_exclude(
        cls,
        accumulators: dict[str, _SymbolAccumulator],
        raw: str,
        alias_map: dict[str, str],
        eq_id: str,
        excluded: list[dict],
        excluded_index: dict[tuple[str, str], dict],
    ) -> _SymbolAccumulator | None:
        """Accumulator for ``raw``, or ``None`` when the candidate is not a symbol.

        A candidate the cartridge itself declares (it is in ``alias_map``) is
        always accepted — the domain vocabulary wins over the format gate.
        """
        normalized = normalize_symbol(raw)
        if not normalized:
            return None
        if normalized not in alias_map:
            reason = exclusion_reason(raw, normalized)
            if reason:
                key = (raw, reason)
                entry = excluded_index.get(key)
                if entry is None:
                    entry = {
                        "raw": raw,
                        "normalized": normalized,
                        "reason": reason,
                        "equation_ids": [],
                    }
                    excluded_index[key] = entry
                    excluded.append(entry)
                if eq_id and eq_id not in entry["equation_ids"]:
                    entry["equation_ids"].append(eq_id)
                return None
        return cls._accumulator(accumulators, raw, alias_map)

    @classmethod
    def _accumulator(
        cls,
        accumulators: dict[str, _SymbolAccumulator],
        raw: str,
        alias_map: dict[str, str],
    ) -> _SymbolAccumulator:
        normalized = normalize_symbol(raw)
        cartridge_canonical = alias_map.get(normalized)
        key = normalize_symbol(cartridge_canonical) if cartridge_canonical else normalized
        acc = accumulators.get(key)
        if acc is None:
            acc = _SymbolAccumulator(cartridge_canonical or normalized)
            accumulators[key] = acc
        return acc

    def _build_records(
        self,
        document_id: str,
        accumulators: dict[str, _SymbolAccumulator],
        cartridge: CartridgeContext | None,
    ) -> tuple[list[SymbolRecord], dict[str, str]]:
        records: list[SymbolRecord] = []
        symbol_id_by_key: dict[str, str] = {}
        used_ids: set[str] = set()
        doc_slug = _slug(document_id)

        for key in sorted(accumulators):
            acc = accumulators[key]
            symbol_id = f"sym_{doc_slug}_{_slug(acc.canonical)}"
            suffix = 2
            while symbol_id in used_ids:
                symbol_id = f"sym_{doc_slug}_{_slug(acc.canonical)}_{suffix}"
                suffix += 1
            used_ids.add(symbol_id)
            symbol_id_by_key[key] = symbol_id

            review_reasons: list[str] = []
            if len(acc.defining_equation_ids) > 1 or acc.upstream_redefined:
                definition_status = "redefined"
                review_reasons.append("symbol_redefinition")
            elif acc.defining_equation_ids:
                definition_status = "defined"
            elif acc.used_in_equation_ids:
                definition_status = "used"
                review_reasons.append("definition_missing")
            else:
                definition_status = "unknown"

            if acc.defining_equation_ids and acc.evidence_texts:
                confidence = 0.9
                reason = "defined with source evidence text"
            elif acc.defining_equation_ids:
                confidence = 0.7
                reason = "defined without quoted evidence text"
            else:
                confidence = 0.4
                reason = "used but never defined in the document"

            records.append(SymbolRecord(
                symbol_id=symbol_id,
                document_id=document_id,
                canonical_symbol=acc.canonical,
                notation_variants=list(acc.variants),
                kind=self._classify_kind(acc, cartridge),
                unit=extract_unit(acc.evidence_texts),
                domain_constraints=[],
                scope=self._scope(acc),
                defining_equation_ids=list(acc.defining_equation_ids),
                used_in_equation_ids=list(acc.used_in_equation_ids),
                source_evidence_ids=list(acc.evidence_ids),
                definition_status=definition_status,
                definition_evidence_texts=list(acc.evidence_texts),
                review_reasons=review_reasons,
                maturity_source="deterministic",
                confidence=confidence,
                reason=reason,
            ))
        return records, symbol_id_by_key

    @staticmethod
    def _scope(acc: _SymbolAccumulator) -> str:
        equation_ids = set(acc.defining_equation_ids) | set(acc.used_in_equation_ids)
        if len(equation_ids) <= 1:
            return "equation_local"
        if len(acc.section_ids) > 1:
            return "document"
        return "section"

    @staticmethod
    def _classify_kind(
        acc: _SymbolAccumulator,
        cartridge: CartridgeContext | None,
    ) -> str:
        if not cartridge or not cartridge.notation_patterns:
            return "unknown"
        for entry in cartridge.notation_patterns:
            if not isinstance(entry, dict):
                continue
            pattern = str(entry.get("pattern") or "")
            if not pattern:
                continue
            try:
                matched = any(
                    re.fullmatch(pattern, variant)
                    for variant in [acc.canonical] + acc.variants
                )
            except re.error:
                continue
            if not matched:
                continue
            concept_type = str(entry.get("concept_type") or "").lower()
            for kind in ("parameter", "observable", "operator", "constant", "variable"):
                if kind in concept_type:
                    return kind
            return "unknown"
        return "unknown"

    @classmethod
    def _annotate_equations(
        cls,
        records: list,
        alias_map: dict[str, str],
        symbol_id_by_key: dict[str, str],
        record_by_id: dict | None = None,
    ) -> None:
        """Project SymbolRegistry data onto equation DefinedSymbols in place.

        Sets ``symbol_id`` so equations reference the registry, and (issue #439)
        ``meaning`` + ``source_evidence_id`` so each equation object is
        self-describing without loading the registry. Meaning is the resolved
        definition evidence text when available; source is the registry's first
        source_evidence_id for the symbol.
        """
        record_by_id = record_by_id or {}
        for record in records:
            sem = getattr(record, "semantics", None)
            if sem is None:
                continue
            for ds in getattr(sem, "defined_symbols", []) or []:
                raw = str(getattr(ds, "symbol", "") or "")
                if not raw or not hasattr(ds, "symbol_id"):
                    continue
                key = cls._canonical_key(raw, alias_map)
                symbol_id = symbol_id_by_key.get(key)
                if not symbol_id:
                    continue
                ds.symbol_id = symbol_id
                symbol_record = record_by_id.get(symbol_id)
                if symbol_record is None:
                    continue
                if hasattr(ds, "meaning") and not getattr(ds, "meaning", None):
                    texts = list(getattr(symbol_record, "definition_evidence_texts", []) or [])
                    own = getattr(ds, "evidence_text", None)
                    ds.meaning = (own or (texts[0] if texts else "")) or None
                if hasattr(ds, "source_evidence_id") and not getattr(ds, "source_evidence_id", None):
                    sources = list(getattr(symbol_record, "source_evidence_ids", []) or [])
                    if sources:
                        ds.source_evidence_id = sources[0]


def build_symbol_index(symbol_registry) -> dict:
    """Build a symbol_index for EquationSemanticsResult.to_equations_export (#439).

    Maps both ``symbol_id`` and the canonical symbol text to
    ``{"meaning", "source_evidence_id"}`` so the equation export can resolve any
    free symbol. Accepts a SymbolRegistryResult or its dict form; tolerates None.
    """
    index: dict = {}
    if symbol_registry is None:
        return index
    if isinstance(symbol_registry, dict):
        records = symbol_registry.get("records") or []
    else:
        records = getattr(symbol_registry, "records", []) or []
    for record in records:
        if isinstance(record, dict):
            symbol_id = str(record.get("symbol_id") or "")
            canonical = str(record.get("canonical_symbol") or "")
            texts = list(record.get("definition_evidence_texts") or [])
            sources = list(record.get("source_evidence_ids") or [])
        else:
            symbol_id = str(getattr(record, "symbol_id", "") or "")
            canonical = str(getattr(record, "canonical_symbol", "") or "")
            texts = list(getattr(record, "definition_evidence_texts", []) or [])
            sources = list(getattr(record, "source_evidence_ids", []) or [])
        entry = {
            "meaning": texts[0] if texts else "",
            "source_evidence_id": sources[0] if sources else None,
        }
        if symbol_id:
            index[symbol_id] = entry
        if canonical and canonical not in index:
            index[canonical] = entry
    return index
