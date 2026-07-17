"""Teacher-triggered, single-figure vision re-analysis.

The normal document pipeline analyzes figures in a batch.  The deliberation UI
needs a bounded one-figure path that produces a structured *candidate*, never a
teacher decision.  Confirmation remains a separate annotation commit.

Guided re-analysis (docs/features/guided_figure_reanalysis_design.md): a
teacher may attach an attention directive — ``hint_text`` (free text, <=2000
chars) and/or ``focus_bbox`` (image-relative ``[x0, y0, x1, y1]``, each in
0..1) — to one re-analysis call. ``_normalize_guidance`` is the single
validation authority (used both here and by the API route so a direct core
call is exactly as safe as going through the endpoint). Guidance is an
attention directive only (GF1/GF2): it never changes ``review_status``, is
never treated as ``evidence_quote``, and never becomes a part's ``bbox``
(GF4 — that grounding stays exclusively in
``episteme_graph.agents.apparatus_semantics.agent._attach_label_grounding``).
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

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - PyMuPDF is a hard runtime dependency
    fitz = None  # type: ignore[assignment]


_cost_gate = CostGate()
logger = logging.getLogger(__name__)

# Guidance validation bounds (§3/§4-1 of the design doc). No new env vars
# (GF6) — these are structural input bounds, not a cost/rate limit.
_MAX_HINT_CHARS = 2000
_MIN_FOCUS_DIM = 0.02
_GUIDANCE_REASON_PREFIX = "教員指示付き再解析: "


class FigureReanalysisError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


def _normalize_guidance(guidance: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate and normalize a teacher guidance payload.

    Returns ``None`` when there is nothing to say (both fields absent/blank —
    unguided re-analysis, fully backward compatible). Raises
    ``FigureReanalysisError(kind="invalid")`` on any out-of-range value so the
    API route's existing ``kind -> status`` mapping turns it into a 422
    regardless of whether the caller is the HTTP route or a direct core call.
    """
    if not guidance:
        return None

    hint_raw = guidance.get("hint_text")
    hint_text = str(hint_raw).strip() if hint_raw is not None else ""
    if len(hint_text) > _MAX_HINT_CHARS:
        raise FigureReanalysisError(
            "指示テキストが長すぎます（2000字まで）", kind="invalid"
        )

    focus_bbox_raw = guidance.get("focus_bbox")
    focus_bbox: list[float] | None = None
    if focus_bbox_raw is not None:
        try:
            values = [float(v) for v in focus_bbox_raw]
        except (TypeError, ValueError) as exc:
            raise FigureReanalysisError(
                "指定領域の形式が不正です", kind="invalid"
            ) from exc
        if len(values) != 4:
            raise FigureReanalysisError(
                "指定領域の形式が不正です", kind="invalid"
            )
        if any(not (0.0 <= v <= 1.0) for v in values):
            raise FigureReanalysisError(
                "指定領域の座標は0〜1の範囲で指定してください", kind="invalid"
            )
        x0, y0, x1, y1 = values
        if not (x1 > x0 and y1 > y0):
            raise FigureReanalysisError(
                "指定領域の形式が不正です", kind="invalid"
            )
        if (x1 - x0) < _MIN_FOCUS_DIM or (y1 - y0) < _MIN_FOCUS_DIM:
            raise FigureReanalysisError(
                "指定領域が小さすぎます", kind="invalid"
            )
        focus_bbox = values

    if not hint_text and focus_bbox is None:
        return None
    return {"hint_text": hint_text or None, "focus_bbox": focus_bbox}


def _crop_focus_image(image_bytes: bytes, focus_bbox: list[float]) -> bytes | None:
    """Crop the focus region (image-relative 0..1) out of the original figure.

    Fail-soft (GF3): any failure returns ``None`` so the caller proceeds
    without a magnified crop (hint_text alone is still meaningful). Never
    resized — the crop keeps its natural pixel size (§5-2).
    """
    if fitz is None or not image_bytes:
        return None
    try:
        filetype = "jpeg" if image_bytes[:3] == b"\xff\xd8\xff" else "png"
        doc = fitz.open(stream=image_bytes, filetype=filetype)
        try:
            page = doc[0]
            rect = page.rect
            x0, y0, x1, y1 = focus_bbox
            clip = fitz.Rect(
                rect.x0 + x0 * rect.width,
                rect.y0 + y0 * rect.height,
                rect.x0 + x1 * rect.width,
                rect.y0 + y1 * rect.height,
            )
            pixmap = page.get_pixmap(clip=clip)
            return pixmap.tobytes("png")
        finally:
            doc.close()
    except Exception:
        logger.warning("figure re-analysis: focus crop failed", exc_info=True)
        return None


def _labels_in_focus(
    inner_labels: list[dict] | None,
    figure_bbox: list[float] | None,
    focus_bbox: list[float],
) -> list[str]:
    """In-figure labels that intersect the teacher's focus region.

    ``focus_bbox`` (image-relative 0..1) is mapped into the page coordinate
    system via ``figure_bbox`` (§3: ``page = fig.x0 + rel * (fig.x1 -
    fig.x0)``) and tested for intersection (not center-containment, so a
    label straddling the boundary is still picked up) against each
    ``inner_labels[].bbox``. Returns ``[]`` when ``figure_bbox`` is missing
    (page-coordinate mapping impossible — fail-soft, §3).
    """
    if not figure_bbox or len(figure_bbox) != 4:
        return []
    fx0, fy0, fx1, fy1 = figure_bbox
    width = fx1 - fx0
    height = fy1 - fy0
    if width <= 0 or height <= 0:
        return []
    rx0, ry0, rx1, ry1 = focus_bbox
    page_focus = (
        fx0 + rx0 * width,
        fy0 + ry0 * height,
        fx0 + rx1 * width,
        fy0 + ry1 * height,
    )
    px0, py0, px1, py1 = page_focus

    labels: list[str] = []
    for item in inner_labels or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        bbox = item.get("bbox")
        if not text or not bbox or len(bbox) != 4:
            continue
        lx0, ly0, lx1, ly1 = bbox
        disjoint = lx1 <= px0 or lx0 >= px1 or ly1 <= py0 or ly0 >= py1
        if disjoint:
            continue
        if text not in labels:
            labels.append(text)
    return labels


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
    guidance: dict[str, Any] | None = None,
    agent: ApparatusSemanticsAgent | None = None,
    storage: Any = None,
    enforce_cost_gate: bool = True,
) -> dict[str, Any]:
    """Run vision for one figure, persist the AI suggestion, and create a candidate.

    ``guidance`` (optional): ``{"hint_text": str | None, "focus_bbox": list | None}``
    — a teacher attention directive (guided_figure_reanalysis_design.md).
    Validated by ``_normalize_guidance`` before any paid work; invalid input
    raises ``FigureReanalysisError(kind="invalid")`` regardless of whether
    this is called via the API route or directly (core is the validation
    authority, §5).
    """
    norm_guidance = _normalize_guidance(guidance)
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

    # Guided re-analysis extras (§5): prepared after the image is available
    # but before the cost gate, mirroring the design's "clip/labels may be
    # built before the paid vision boundary" note. Both degrade to their
    # falsy default when there is no guidance or no focus_bbox (GF7-safe —
    # this whole block is skipped for an unguided call).
    focus_image_bytes: bytes | None = None
    focus_label_texts: list[str] = []
    if norm_guidance and norm_guidance.get("focus_bbox"):
        focus_bbox = norm_guidance["focus_bbox"]
        focus_image_bytes = _crop_focus_image(image_bytes, focus_bbox)
        focus_label_texts = _labels_in_focus(
            row.get("inner_labels") or [], row.get("bbox"), focus_bbox
        )

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
        guidance_text=(norm_guidance.get("hint_text") or "") if norm_guidance else "",
        focus_bbox_rel=(norm_guidance.get("focus_bbox") if norm_guidance else None),
        focus_image_bytes=focus_image_bytes,
        focus_label_texts=focus_label_texts,
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
    if norm_guidance is not None:
        # Additive key only — normalize_figure_analysis_candidate's own shape
        # is unchanged (§5, GF5: out-of-source-line audit of the directive).
        body = {**body, "guidance": norm_guidance}

    persist_suggestions(document_id, [record])
    ref = ElementRef(
        scope=SCOPE_DOCUMENT,
        element_type=ELEMENT_FIGURE,
        element_id=figure_id,
        document_id=document_id,
    )
    reason = str(record_data.get("mode_reason") or record_data.get("reason") or "画像再解析")
    if norm_guidance is not None:
        reason = _GUIDANCE_REASON_PREFIX + reason
    annotation = deliberation_store.create_annotation(
        ref,
        kind="decomposition",
        body=body,
        evidence=_evidence(record, row),
        reason=reason,
        confidence=record_data.get("confidence"),
        session_id=None,
        created_by=created_by,
    )
    return {
        "figure_id": figure_id,
        "suggested_mode": body["presentation_mode"],
        "mode_reason": str(record_data.get("mode_reason") or ""),
        "analysis_profile": body["analysis_profile"],
        "guidance": norm_guidance,
        "guidance_note": str(record_data.get("guidance_note") or ""),
        "annotation": annotation,
    }
