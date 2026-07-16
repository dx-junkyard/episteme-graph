"""Shared figure-presentation modes and fail-soft analysis adapters.

The image pipeline historically treated every extracted image as an apparatus
candidate.  This module keeps those artifacts readable while exposing a small
presentation vocabulary suitable for the deliberation UI.  Vision output is
always a *suggestion*; only ``document_figures.reviewed_mode`` is a teacher
decision.
"""
from __future__ import annotations

import re
from typing import Any


FIGURE_MODES = (
    "functional_diagram",
    "data_plot",
    "descriptive_image",
    "mixed",
    "unknown",
)

MODE_REVIEW_PENDING = "pending"
MODE_REVIEW_REVIEWED = "reviewed"


def normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in FIGURE_MODES else "unknown"


def effective_mode(suggested_mode: Any, reviewed_mode: Any) -> str:
    reviewed = str(reviewed_mode or "").strip().lower()
    if reviewed in FIGURE_MODES:
        return reviewed
    return normalize_mode(suggested_mode)


def infer_mode_from_text(text: Any, legacy_figure_type: Any = None) -> tuple[str, str]:
    """Conservative caption/nearby-text fallback used when vision is absent.

    It deliberately returns ``unknown`` when the words do not distinguish a
    graph from a diagram or a descriptive image.  The heuristic is not a
    teacher decision and callers must expose its reason.
    """
    value = f"{legacy_figure_type or ''} {text or ''}".lower()
    data_terms = (
        "plot", "graph", "chart", "histogram", "scatter", "curve", "spectrum",
        "distribution", "error bar", "heatmap", "boxplot", "versus", " vs ",
    )
    functional_terms = (
        "apparatus", "setup", "schematic", "block diagram", "architecture",
        "pipeline", "flow diagram", "circuit", "optical path", "signal path",
    )
    descriptive_terms = (
        "photograph", "photo ", "micrograph", "microscopy", "illustration",
        "rendering", "image of", "snapshot", "artist", "specimen",
    )
    data_match = any(
        (re.search(r"\bgraph\b", value) is not None) if term == "graph" else term in value
        for term in data_terms
    )
    matches = [
        ("data_plot", data_match),
        ("functional_diagram", any(term in value for term in functional_terms)),
        ("descriptive_image", any(term in value for term in descriptive_terms)),
    ]
    selected = [mode for mode, matched in matches if matched]
    if len(selected) > 1:
        return "mixed", "caption_or_legacy_type_contains_multiple_mode_cues"
    if selected:
        return selected[0], "caption_or_legacy_type_heuristic"
    return "unknown", "insufficient_classification_signal"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value or [] if isinstance(item, dict)]


def _ports(value: Any, prefix: str) -> list[dict[str, Any]]:
    ports: list[dict[str, Any]] = []
    for index, raw in enumerate(_list_of_dicts(value), start=1):
        item = dict(raw)
        item["id"] = str(item.get("id") or f"{prefix}_{index}")
        item["name"] = str(item.get("name") or item["id"])
        ports.append(item)
    return ports


def functional_profile_from_apparatus(record: dict[str, Any]) -> dict[str, Any]:
    """Convert the legacy parts/connections contract to a functional graph.

    A part is represented as a function with multiple ``inputs`` and
    ``outputs``.  Legacy edges do not identify explicit port names, so the
    relation is preserved as a provisional port label instead of inventing a
    physical signal or material.
    """
    parts = _list_of_dicts(record.get("parts"))
    connections = _list_of_dicts(record.get("connections"))
    name_to_id: dict[str, str] = {}
    functions: list[dict[str, Any]] = []
    for index, part in enumerate(parts, start=1):
        function_id = str(part.get("function_id") or part.get("id") or f"function_{index}")
        name = str(part.get("name") or part.get("label_ref") or function_id)
        name_to_id.setdefault(name, function_id)
        functions.append({
            "id": function_id,
            "name": name,
            "role": str(part.get("role") or ""),
            "inputs": _ports(part.get("inputs"), f"{function_id}_input"),
            "outputs": _ports(part.get("outputs"), f"{function_id}_output"),
            "label_ref": part.get("label_ref"),
            "expanded_name": str(part.get("expanded_name") or ""),
            "bbox": part.get("bbox"),
            "evidence_quote": str(part.get("evidence_quote") or ""),
            "reason": str(part.get("reason") or ""),
            "confidence": part.get("confidence", 0.0),
        })

    by_id = {item["id"]: item for item in functions}
    normalized_connections: list[dict[str, Any]] = []
    for index, connection in enumerate(connections, start=1):
        source_name = str(connection.get("from_part") or connection.get("from_function") or "")
        target_name = str(connection.get("to_part") or connection.get("to_function") or "")
        source_id = str(connection.get("from_function") or name_to_id.get(source_name) or source_name)
        target_id = str(connection.get("to_function") or name_to_id.get(target_name) or target_name)
        relation = str(connection.get("relation") or connection.get("medium") or "")
        from_output_id = str(
            connection.get("from_output_id") or connection.get("from_output")
            or f"{source_id}_output_{index}"
        )
        to_input_id = str(
            connection.get("to_input_id") or connection.get("to_input")
            or f"{target_id}_input_{index}"
        )
        normalized = {
            "id": str(connection.get("id") or f"connection_{index}"),
            "from_function_id": source_id,
            "from_output_id": from_output_id,
            "to_function_id": target_id,
            "to_input_id": to_input_id,
            "relation": relation,
            "medium": str(connection.get("medium") or ""),
            "reason": str(connection.get("reason") or ""),
            "confidence": connection.get("confidence", 0.0),
        }
        normalized_connections.append(normalized)
        if source_id in by_id:
            by_id[source_id]["outputs"].append({
                "id": from_output_id,
                "name": relation or from_output_id,
                "to_function_id": target_id,
                "connection_id": normalized["id"],
            })
        if target_id in by_id:
            by_id[target_id]["inputs"].append({
                "id": to_input_id,
                "name": relation or to_input_id,
                "from_function_id": source_id,
                "connection_id": normalized["id"],
            })

    return {
        "overall_function": str(
            record.get("overall_function") or record.get("apparatus_name_candidate") or ""
        ),
        "external_inputs": _list_of_dicts(record.get("external_inputs")),
        "external_outputs": _list_of_dicts(record.get("external_outputs")),
        "functions": functions,
        "connections": normalized_connections,
    }


def _normalize_functional_profile(raw: dict[str, Any], legacy: dict[str, Any]) -> dict[str, Any]:
    functions: list[dict[str, Any]] = []
    for index, source in enumerate(_list_of_dicts(raw.get("functions")), start=1):
        item = dict(source)
        function_id = str(item.get("id") or item.get("function_id") or f"function_{index}")
        item["id"] = function_id
        item["name"] = str(item.get("name") or function_id)
        item["role"] = str(item.get("role") or "")
        item["inputs"] = _ports(item.get("inputs"), f"{function_id}_input")
        item["outputs"] = _ports(item.get("outputs"), f"{function_id}_output")
        functions.append(item)
    if not functions:
        functions = legacy["functions"]

    connections: list[dict[str, Any]] = []
    for index, source in enumerate(_list_of_dicts(raw.get("connections")), start=1):
        relation = str(source.get("relation") or source.get("medium") or "")
        connections.append({
            **source,
            "id": str(source.get("id") or f"connection_{index}"),
            "from_function_id": str(
                source.get("from_function_id") or source.get("from_function")
                or source.get("from_part") or ""
            ),
            "from_output_id": str(
                source.get("from_output_id") or source.get("from_output") or ""
            ),
            "to_function_id": str(
                source.get("to_function_id") or source.get("to_function")
                or source.get("to_part") or ""
            ),
            "to_input_id": str(source.get("to_input_id") or source.get("to_input") or ""),
            "relation": relation,
        })
    if not connections:
        connections = legacy["connections"]

    return {
        "overall_function": str(raw.get("overall_function") or legacy["overall_function"]),
        "external_inputs": _ports(
            raw.get("external_inputs") or legacy["external_inputs"], "external_input"
        ),
        "external_outputs": _ports(
            raw.get("external_outputs") or legacy["external_outputs"], "external_output"
        ),
        "functions": functions,
        "connections": connections,
    }


def analysis_profile_for_record(record: Any, *, caption_text: str = "") -> dict[str, Any]:
    """Return the normalized mode-specific contract for a current or old artifact."""
    source = _dict(record)
    raw_profile = _dict(source.get("analysis_profile") or source.get("mode_analysis"))
    raw_suggested_mode = source.get("suggested_mode") or source.get("figure_mode_candidate")
    suggested = normalize_mode(raw_suggested_mode)
    reason = str(source.get("mode_reason") or "")

    if not str(raw_suggested_mode or "").strip():
        legacy_record = _dict(source.get("figure_record"))
        inferred, inferred_reason = infer_mode_from_text(
            caption_text or source.get("caption_text") or legacy_record.get("caption"),
            legacy_record.get("figure_type"),
        )
        if inferred == "unknown" and (
            source.get("parts") or source.get("connections") or source.get("apparatus_name_candidate")
        ):
            inferred, inferred_reason = "functional_diagram", "legacy_apparatus_artifact"
        suggested = inferred
        reason = reason or inferred_reason

    if suggested == "functional_diagram":
        functional = _dict(raw_profile.get("functional_diagram") or raw_profile)
        legacy = functional_profile_from_apparatus(source)
        profile = _normalize_functional_profile(functional, legacy)
    elif suggested == "data_plot":
        plot = _dict(raw_profile.get("data_plot") or raw_profile)
        axes = _list_of_dicts(plot.get("axes"))
        if not axes:
            x_axis = _dict(plot.get("x_axis"))
            if x_axis:
                axes.append({"orientation": "x", **x_axis})
            axes.extend({"orientation": "y", **axis} for axis in _list_of_dicts(plot.get("y_axes")))
        observations = _list_of_dicts(plot.get("observations"))
        for index, observation in enumerate(observations, start=1):
            observation["id"] = str(observation.get("id") or f"observation_{index}")
        highlights = _list_of_dicts(plot.get("highlights"))
        for index, highlight in enumerate(highlights, start=1):
            highlight["id"] = str(highlight.get("id") or f"highlight_{index}")
        profile = {
            "plot_type": str(plot.get("plot_type") or "unknown"),
            "axes": axes,
            "series": _list_of_dicts(plot.get("series")),
            "observations": observations,
            "interpretations": _list_of_dicts(plot.get("interpretations")),
            "highlights": highlights,
        }
    elif suggested == "descriptive_image":
        image = _dict(raw_profile.get("descriptive_image") or raw_profile)
        profile = {
            "summary": str(image.get("summary") or source.get("reason") or ""),
            "subjects": _list_of_dicts(image.get("subjects")),
            "regions": _list_of_dicts(image.get("regions")),
            "teaching_points": _list_of_dicts(image.get("teaching_points")),
        }
    elif suggested == "mixed":
        mixed = _dict(raw_profile.get("mixed") or raw_profile)
        profile = {
            "summary": str(mixed.get("summary") or ""),
            "panels": _list_of_dicts(mixed.get("panels")),
        }
    else:
        unknown = _dict(raw_profile.get("unknown") or raw_profile)
        profile = {
            "summary": str(unknown.get("summary") or ""),
            "reason": str(unknown.get("reason") or reason),
        }

    return {
        "suggested_mode": suggested,
        "mode_reason": reason,
        "analysis_profile": profile,
    }
