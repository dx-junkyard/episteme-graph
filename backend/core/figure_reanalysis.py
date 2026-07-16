"""Teacher-triggered, single-figure vision re-analysis.

The normal document pipeline analyzes figures in a batch.  The deliberation UI
needs a bounded one-figure path that produces a structured *candidate*, never a
teacher decision.  Confirmation remains a separate annotation commit.
"""
from __future__ import annotations

from dataclasses import asdict
import logging
from typing import Any

from core.config import get_settings
from core.deliberation import store as deliberation_store
from core.deliberation.schema import ELEMENT_FIGURE, SCOPE_DOCUMENT, ElementRef
from core.document_pipeline.figure_context import collect_figure_context
from core.document_pipeline.figure_images import load_document_figures
from core.document_pipeline.persistence import get_latest_analysis_run
from core.figure_presentation import (
    normalize_figure_analysis_candidate,
    persist_suggestions,
)
from core.llm_usage import usage_context
from core.llm_worker.cost_gate import CostGate, today_str
from core.storage import get_storage_client
from episteme_graph.agents.apparatus_semantics.agent import ApparatusSemanticsAgent
from episteme_graph.agents.apparatus_semantics.schema import FigureImageInput


_cost_gate = CostGate()
logger = logging.getLogger(__name__)


class FigureReanalysisError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


def _consume_budget(figure_id: str, user_id: str | None) -> None:
    settings = get_settings()
    allowed = _cost_gate.check_and_count(
        session_limit=3,
        session_key=(user_id or "", figure_id),
        daily_limit=max(1, int(settings.apparatus_max_calls_per_day)),
        daily_key=(today_str(), user_id or ""),
        prune_stale_daily=True,
    )
    if not allowed:
        raise FigureReanalysisError(
            "この図の再解析回数の上限に達しました。時間をおいて再度お試しください",
            kind="limit",
        )


def _latest_context(document_id: str, row: dict[str, Any]) -> tuple[Any, dict | None, str | None]:
    latest = get_latest_analysis_run(document_id=document_id) or {}
    artifacts = ((latest.get("stage_outputs") or {}).get("_artifacts") or {})
    structure = artifacts.get("document_structure")
    figure_record = None
    figure_artifact = artifacts.get("figure_table_semantics") or {}
    for candidate in figure_artifact.get("figures") or []:
        if not isinstance(candidate, dict):
            continue
        source_location = candidate.get("source_location") or {}
        if str(candidate.get("figure_id") or "") in {
            str(row.get("figure_key") or ""),
            str(row.get("id") or ""),
        } or (
            row.get("caption_block_id")
            and str(source_location.get("caption_block_id") or "")
            == str(row.get("caption_block_id"))
        ):
            figure_record = candidate
            break
    return structure, figure_record, latest.get("cartridge_id")


def _summary(mode: str, profile: dict[str, Any]) -> str:
    if mode == "functional_diagram":
        names = [
            str(item.get("name") or item.get("id") or "")
            for item in profile.get("functions") or []
            if isinstance(item, dict)
        ]
        names = [name for name in names if name]
        return (
            f"{len(names)}個の機能要素と{len(profile.get('connections') or [])}本の接続を検出しました"
            + (f"：{', '.join(names[:12])}" if names else "")
        )
    if mode == "data_plot":
        return (
            f"グラフの軸{len(profile.get('axes') or [])}件、系列"
            f"{len(profile.get('series') or [])}件、観測{len(profile.get('observations') or [])}件を検出しました"
        )
    if mode == "descriptive_image":
        return str(profile.get("summary") or "写真・解説画像の対象と確認点を検出しました")
    if mode == "mixed":
        return f"{len(profile.get('panels') or [])}個のパネルを持つ複合図として検出しました"
    return "図を再解析しました"


def _evidence(record: Any, row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for value in [
        getattr(record, "evidence_quote", ""),
        *[getattr(part, "evidence_quote", "") for part in getattr(record, "parts", [])],
    ]:
        text = str(value or "").strip()
        if text and text not in values:
            values.append(text)
    labels = [
        str(item.get("text") or "").strip()
        for item in row.get("inner_labels") or []
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    if labels:
        values.append("図中ラベル: " + ", ".join(labels[:30]))
    caption = str(row.get("caption_text") or "").strip()
    if caption and caption not in values:
        values.append(caption)
    if not values:
        values.append(f"原図画像: {row.get('figure_key') or row.get('id')}")
    return values[:8]


def reanalyze_figure(
    document_id: str,
    figure_id: str,
    *,
    created_by: str | None,
    agent: ApparatusSemanticsAgent | None = None,
    storage: Any = None,
    enforce_cost_gate: bool = True,
) -> dict[str, Any]:
    """Run vision for one figure, persist the AI suggestion, and create a candidate."""
    rows = load_document_figures(document_id)
    row = next((item for item in rows if str(item.get("id") or "") == figure_id), None)
    if row is None:
        raise FigureReanalysisError("Figure not found", kind="not_found")
    if row.get("status") != "extracted" or not row.get("minio_key"):
        raise FigureReanalysisError("原図を取得できないため再解析できません", kind="invalid")

    storage = storage or get_storage_client()
    try:
        image_bytes = storage.get_object("figure-images", row["minio_key"])
    except Exception as exc:
        raise FigureReanalysisError("原図を取得できないため再解析できません", kind="invalid") from exc
    if not image_bytes:
        raise FigureReanalysisError("原図を取得できないため再解析できません", kind="invalid")

    try:
        structure, figure_record, cartridge_id = _latest_context(document_id, row)
    except Exception:
        logger.warning(
            "figure re-analysis: failed to load prior context document=%s figure=%s",
            document_id,
            figure_id,
            exc_info=True,
        )
        structure, figure_record, cartridge_id = None, None, None
    settings = get_settings()
    try:
        context = collect_figure_context(
            structure,
            row,
            inner_labels=row.get("inner_labels") or [],
            max_items=settings.apparatus_context_max_items,
            max_chars=settings.apparatus_context_max_chars,
        )
    except Exception:
        logger.warning(
            "figure re-analysis: context collection failed document=%s figure=%s",
            document_id,
            figure_id,
            exc_info=True,
        )
        context = None
    figure_input = FigureImageInput(
        figure_id=figure_id,
        figure_key=str(row.get("figure_key") or figure_id),
        figure_label=row.get("figure_label"),
        caption_text=str(row.get("caption_text") or ""),
        image_bytes=image_bytes,
        nearby_text=(context.nearby_text if context else []),
        figure_record=figure_record,
        inner_labels=row.get("inner_labels") or [],
        abbreviations=(context.abbreviations if context else {}),
    )
    analyzer = agent or ApparatusSemanticsAgent(cartridge_id=cartridge_id)
    # Count only requests that reached the paid vision boundary.  Missing or
    # corrupt stored images must not consume the teacher's re-analysis budget.
    if enforce_cost_gate:
        _consume_budget(figure_id, created_by)
    with usage_context(
        "deliberation:figure_reanalysis",
        user_id=created_by,
        document_id=document_id,
    ):
        result = analyzer.run(
            document_id=document_id,
            figures=[figure_input],
            library_candidates={},
            cartridge_id=cartridge_id,
        )
    record = (result.apparatus_records or [None])[0]
    if record is None or getattr(record, "repair_failed", False):
        raise FigureReanalysisError("図の構造化解析を生成できませんでした", kind="invalid")
    if any(issue.severity == "error" for issue in result.validation_issues or []):
        raise FigureReanalysisError("図の構造化解析が検証を通過しませんでした", kind="invalid")

    record_data = asdict(record)
    body = normalize_figure_analysis_candidate({
        "candidate_type": "figure_analysis",
        "presentation_mode": record_data.get("suggested_mode"),
        "analysis_profile": record_data.get("analysis_profile"),
        "text": _summary(
            str(record_data.get("suggested_mode") or "unknown"),
            record_data.get("analysis_profile") or {},
        ),
    })
    if body is None:
        raise FigureReanalysisError(
            "図の種類または構成要素を十分に検出できませんでした",
            kind="invalid",
        )

    persist_suggestions(document_id, [record])
    ref = ElementRef(
        scope=SCOPE_DOCUMENT,
        element_type=ELEMENT_FIGURE,
        element_id=figure_id,
        document_id=document_id,
    )
    annotation = deliberation_store.create_annotation(
        ref,
        kind="decomposition",
        body=body,
        evidence=_evidence(record, row),
        reason=str(record_data.get("mode_reason") or record_data.get("reason") or "画像再解析"),
        confidence=record_data.get("confidence"),
        session_id=None,
        created_by=created_by,
    )
    return {
        "figure_id": figure_id,
        "suggested_mode": body["presentation_mode"],
        "mode_reason": str(record_data.get("mode_reason") or ""),
        "analysis_profile": body["analysis_profile"],
        "annotation": annotation,
    }
