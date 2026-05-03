"""Cleanup duplicate and overlapping components."""
from __future__ import annotations

from .schema import ComponentAssemblyResult, ComponentRecord


class ComponentOverlapCleanup:
    def cleanup(self, result: ComponentAssemblyResult) -> ComponentAssemblyResult:
        components, id_map = self._dedupe_components(result.components)
        for component in components:
            for dep in component.dependencies:
                dep["component_refs"] = [
                    id_map.get(ref, ref)
                    for ref in dep.get("component_refs", [])
                    if id_map.get(ref, ref) != component.component_id
                ]
        result.components = components
        return result

    @staticmethod
    def _dedupe_components(
        components: list[ComponentRecord],
    ) -> tuple[list[ComponentRecord], dict[str, str]]:
        seen: dict[tuple[str, str], ComponentRecord] = {}
        id_map: dict[str, str] = {}
        output: list[ComponentRecord] = []
        for component in components:
            key = (component.component_type, component.label.strip().lower())
            if key in seen:
                canonical = seen[key]
                id_map[component.component_id] = canonical.component_id
                canonical.evidence_refs = _merge_evidence(canonical.evidence_refs, component.evidence_refs)
                canonical.confidence = max(canonical.confidence, component.confidence)
                canonical.review_notes.extend(component.review_notes)
            else:
                seen[key] = component
                id_map[component.component_id] = component.component_id
                output.append(component)
        return output, id_map


def _merge_evidence(a: dict, b: dict) -> dict:
    merged = dict(a or {})
    for key in ("claim_ids", "equation_ids", "thesis_refs"):
        values = []
        values.extend(merged.get(key, []) or [])
        values.extend((b or {}).get(key, []) or [])
        merged[key] = sorted({str(v) for v in values})
    dsl_a = merged.get("dsl_refs", {}) or {}
    dsl_b = (b or {}).get("dsl_refs", {}) or {}
    merged["dsl_refs"] = {
        "node_ids": sorted({*(str(v) for v in dsl_a.get("node_ids", []) or []), *(str(v) for v in dsl_b.get("node_ids", []) or [])}),
        "edge_ids": sorted({*(str(v) for v in dsl_a.get("edge_ids", []) or []), *(str(v) for v in dsl_b.get("edge_ids", []) or [])}),
    }
    return merged
