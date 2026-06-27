"""EquationLinkNormalizer (issue #432).

Inter-equation links (``input_equation_ids`` / ``output_equation_ids``) in the
equation registry must be *derived from structural cues*, never copied verbatim
from paper-specific LLM guesses. This deterministic, domain-agnostic normalizer
rebuilds the link graph from three structural signals and records, for every
resolved link, the provenance kind(s) that justify it:

  - ``shared_symbol``     — equation D *defines* a symbol that equation U *uses*
                            (read from the document SymbolRegistry), so D → U.
  - ``textual_reference`` — equation U's text references equation R's label
                            (e.g. "Eq. (3)"), so R → U.
  - ``derivation_step``   — an upstream-declared input/output link that still
                            resolves to a real registry equation is preserved.

Dangling links (ids that do not resolve to a registry equation) and self-loops
are dropped. Every result/relation equation that ends up with no input link is
given an explicit ``link_status`` justification derived purely from structural
signals (``axiomatic`` when it introduces symbols with no resolvable upstream,
``external_reference`` when its text cites an external source, otherwise
``unresolved``). No field- or paper-specific vocabulary is used.
"""
from __future__ import annotations

import re

from episteme_graph.agents.equation_semantics.schema import (
    ALLOWED_NO_INPUT_LINK_STATUSES,
    EquationSemanticsResult,
)
from .builder import normalize_symbol

# Domain-neutral reference patterns. They capture an *equation number* token
# (numeric / appendix style) that follows an equation-reference cue word or a
# bare parenthesised number. A captured token is only accepted as a link when it
# maps to a *different* registry equation, which keeps false positives low.
_EQ_REFERENCE_RE = re.compile(
    r"(?:eq(?:s|n|ns)?\.?|equations?|relation)\s*\(?\s*"
    r"([A-Za-z]?\.?\d+(?:\.\d+)*[A-Za-z]?)\s*\)?",
    re.IGNORECASE,
)
_PAREN_NUMBER_RE = re.compile(r"\(\s*([A-Za-z]?\d+(?:\.\d+)*[A-Za-z]?)\s*\)")

# External citation cues (domain-neutral): bracketed numeric citations or
# reference/citation words. Used only to justify a no-input equation.
_EXTERNAL_CITATION_RE = re.compile(
    r"\[\s*\d+(?:\s*[,–\-]\s*\d+)*\s*\]|\bref\.?\b|\breference|\bcit|\bet al",
    re.IGNORECASE,
)


def _norm_label(label: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", str(label or "")).lower()


class EquationLinkNormalizer:
    """Rebuild equation links from structural cues and record their provenance."""

    def normalize(
        self,
        equations: EquationSemanticsResult,
        symbol_registry=None,
    ) -> dict:
        """Mutate ``equations`` in place; return a summary dict.

        Summary keys: ``link_count``, ``provenance_counts`` (per kind),
        ``dangling_dropped``, ``link_status_counts``.
        """
        records = list(getattr(equations, "equations", []) or [])
        registry_ids = {
            str(getattr(r, "equation_id", "") or "") for r in records
        }
        registry_ids.discard("")

        label_to_id = self._label_index(records)
        define_map, use_map = self._symbol_maps(records, symbol_registry, registry_ids)

        # edges[(src, dst)] -> set of provenance kinds (src is input of dst).
        edges: dict[tuple[str, str], set[str]] = {}
        dangling_dropped = 0

        def _add_edge(src: str, dst: str, kind: str) -> None:
            if not src or not dst or src == dst:
                return
            if src not in registry_ids or dst not in registry_ids:
                return
            edges.setdefault((src, dst), set()).add(kind)

        # 1. derivation_step: preserve upstream-declared links that still resolve.
        for r in records:
            eq_id = str(getattr(r, "equation_id", "") or "")
            sem = getattr(r, "semantics", None)
            if sem is None:
                continue
            for src in list(sem.input_equation_ids or []):
                src = str(src)
                if src in registry_ids:
                    _add_edge(src, eq_id, "derivation_step")
                elif src and src != eq_id:
                    dangling_dropped += 1
            for dst in list(sem.output_equation_ids or []):
                dst = str(dst)
                if dst in registry_ids:
                    _add_edge(eq_id, dst, "derivation_step")
                elif dst and dst != eq_id:
                    dangling_dropped += 1

        # 2. shared_symbol: definer → user, from the symbol registry maps.
        for canonical, definers in define_map.items():
            users = use_map.get(canonical) or set()
            for d in definers:
                for u in users:
                    _add_edge(d, u, "shared_symbol")

        # 3. textual_reference: equation that cites another equation's label.
        for r in records:
            eq_id = str(getattr(r, "equation_id", "") or "")
            for ref_label in self._referenced_labels(r):
                target = label_to_id.get(ref_label)
                if target and target != eq_id:
                    _add_edge(target, eq_id, "textual_reference")

        # Apply edges back onto the records.
        inputs: dict[str, dict[str, list[str]]] = {}
        outputs: dict[str, set[str]] = {}
        for (src, dst), kinds in edges.items():
            inputs.setdefault(dst, {})[src] = sorted(kinds)
            outputs.setdefault(src, set()).add(dst)

        provenance_counts: dict[str, int] = {}
        link_count = 0
        for r in records:
            eq_id = str(getattr(r, "equation_id", "") or "")
            sem = getattr(r, "semantics", None)
            if sem is None:
                continue
            in_links = inputs.get(eq_id, {})
            sem.input_equation_ids = sorted(in_links.keys())
            sem.output_equation_ids = sorted(outputs.get(eq_id, set()))
            sem.link_provenance = {k: list(v) for k, v in in_links.items()}
            link_count += len(in_links)
            for kinds in in_links.values():
                for kind in kinds:
                    provenance_counts[kind] = provenance_counts.get(kind, 0) + 1
            sem.link_status = self._link_status(r, define_map, use_map, registry_ids)

        link_status_counts: dict[str, int] = {}
        for r in records:
            sem = getattr(r, "semantics", None)
            if sem is None:
                continue
            link_status_counts[sem.link_status] = (
                link_status_counts.get(sem.link_status, 0) + 1
            )

        return {
            "link_count": link_count,
            "provenance_counts": provenance_counts,
            "dangling_dropped": dangling_dropped,
            "link_status_counts": link_status_counts,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _label_index(records) -> dict[str, str]:
        """normalized label → equation_id (first writer wins)."""
        index: dict[str, str] = {}
        for r in records:
            key = _norm_label(getattr(r, "label", None))
            eq_id = str(getattr(r, "equation_id", "") or "")
            if key and eq_id and key not in index:
                index[key] = eq_id
        return index

    @staticmethod
    def _symbol_maps(records, symbol_registry, registry_ids):
        """Build canonical-symbol → {defining ids}, {using ids}.

        Prefer the SymbolRegistry (already canonicalised). Fall back to the
        equations' own defined/used symbols so the normalizer also works when no
        registry is supplied.
        """
        define_map: dict[str, set[str]] = {}
        use_map: dict[str, set[str]] = {}

        sym_records = list(getattr(symbol_registry, "records", []) or [])
        if sym_records:
            for sym in sym_records:
                canonical = normalize_symbol(
                    str(getattr(sym, "canonical_symbol", "") or "")
                )
                if not canonical:
                    continue
                for d in getattr(sym, "defining_equation_ids", []) or []:
                    if str(d) in registry_ids:
                        define_map.setdefault(canonical, set()).add(str(d))
                for u in getattr(sym, "used_in_equation_ids", []) or []:
                    if str(u) in registry_ids:
                        use_map.setdefault(canonical, set()).add(str(u))
            return define_map, use_map

        # Fallback: derive from equation semantics directly.
        for r in records:
            eq_id = str(getattr(r, "equation_id", "") or "")
            sem = getattr(r, "semantics", None)
            if sem is None or eq_id not in registry_ids:
                continue
            for ds in getattr(sem, "defined_symbols", []) or []:
                raw = str(getattr(ds, "symbol", "") or "")
                status = str(getattr(ds, "definition_status", "") or "")
                canonical = normalize_symbol(raw)
                if not canonical:
                    continue
                if status in ("defined", "redefined"):
                    define_map.setdefault(canonical, set()).add(eq_id)
                else:
                    use_map.setdefault(canonical, set()).add(eq_id)
            for raw in getattr(sem, "used_symbols", []) or []:
                canonical = normalize_symbol(str(raw or ""))
                if canonical:
                    use_map.setdefault(canonical, set()).add(eq_id)
        return define_map, use_map

    @staticmethod
    def _equation_text(record) -> str:
        sem = getattr(record, "semantics", None)
        src = getattr(record, "source_extraction", None)
        parts = [
            str(getattr(record, "label", "") or ""),
            str(getattr(src, "raw_text", "") or "") if src else "",
            str(getattr(src, "plain_text", "") or "") if src else "",
            str(getattr(sem, "summary", "") or "") if sem else "",
            str(getattr(sem, "reason", "") or "") if sem else "",
        ]
        return " ".join(p for p in parts if p)

    @classmethod
    def _referenced_labels(cls, record) -> set[str]:
        text = cls._equation_text(record)
        if not text:
            return set()
        self_label = _norm_label(getattr(record, "label", None))
        found: set[str] = set()
        for match in _EQ_REFERENCE_RE.finditer(text):
            key = _norm_label(match.group(1))
            if key and key != self_label:
                found.add(key)
        for match in _PAREN_NUMBER_RE.finditer(text):
            key = _norm_label(match.group(1))
            if key and key != self_label:
                found.add(key)
        return found

    @classmethod
    def _link_status(cls, record, define_map, use_map, registry_ids) -> str:
        sem = getattr(record, "semantics", None)
        if sem is None:
            return "unresolved"
        if sem.input_equation_ids:
            return "derived"
        # No input links — derive an explicit justification from structural cues.
        if _EXTERNAL_CITATION_RE.search(cls._equation_text(record)):
            return "external_reference"
        eq_id = str(getattr(record, "equation_id", "") or "")
        defines_symbol = any(
            eq_id in definers for definers in define_map.values()
        )
        # ``axiomatic`` = it introduces symbols but uses nothing defined upstream
        # (there is genuinely no in-document equation to link from). Because we
        # are already in the no-input branch, no shared symbol produced an edge,
        # so this reduces to: it defines at least one symbol.
        if defines_symbol:
            return "axiomatic"
        return "unresolved"


def normalize_equation_links(
    equations: EquationSemanticsResult,
    symbol_registry=None,
) -> dict:
    """Module-level convenience wrapper around :class:`EquationLinkNormalizer`."""
    return EquationLinkNormalizer().normalize(equations, symbol_registry)


__all__ = [
    "EquationLinkNormalizer",
    "normalize_equation_links",
    "ALLOWED_NO_INPUT_LINK_STATUSES",
]
