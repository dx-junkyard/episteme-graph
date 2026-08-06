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
    assign_review_question_ids,
    normalize_figure_analysis_candidate,
    persist_suggestions,
)
from core.llm_policy import SOURCE_ENV, SOURCE_TIER_DEFAULT, resolve_scene_model
from core.llm_usage import usage_context
from core.llm_worker.cost_gate import CostGate, today_str
from core.storage import get_storage_client
from episteme_graph.agents.apparatus_semantics.agent import ApparatusSemanticsAgent
from episteme_graph.agents.apparatus_semantics.schema import FigureImageInput, IterativeConfig

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - PyMuPDF is a hard runtime dependency
    fitz = None  # type: ignore[assignment]


_cost_gate = CostGate()
logger = logging.getLogger(__name__)

# U層の feature / M層の scene キー（``deliberation:figure_reanalysis`` は
# ``core/llm_policy.py::scene_for_feature`` が ``pipeline.vision`` に束ねる — 実体は
# apparatus vision エンジンそのものなので、非 vision モデルへ落とさないため）。
_FIGURE_REANALYSIS_FEATURE = "deliberation:figure_reanalysis"

# Guidance validation bounds (§3/§4-1 of the design doc). No new env vars
# (GF6) — these are structural input bounds, not a cost/rate limit.
_MAX_HINT_CHARS = 2000
_MIN_FOCUS_DIM = 0.02
_GUIDANCE_REASON_PREFIX = "教員指示付き再解析: "

# Unresolved-item-directed re-analysis bounds
# (docs/features/contextual_figure_analysis_iterative_verification.md).
_MAX_UNRESOLVED_ITEM_IDS = 10
_MAX_UNRESOLVED_ITEM_ID_CHARS = 64
_UNRESOLVED_ITEM_HINT_PREFIX = "未解決箇所の再確認: "
_FOCUS_BBOX_PADDING_RATIO = 0.05


class FigureReanalysisError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


def _normalize_unresolved_item_ids(raw: Any) -> list[str] | None:
    """Validate/normalize ``unresolved_item_ids`` (#499 unresolved-item-directed
    re-analysis). ``None``/absent stays ``None`` — this is purely additive so a
    caller that never sends this field is unaffected (§ guided re-analysis
    back-compat). At most 10 ids, each a non-empty string of at most 64 chars.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise FigureReanalysisError("未解決箇所の指定形式が不正です", kind="invalid")
    if len(raw) > _MAX_UNRESOLVED_ITEM_IDS:
        raise FigureReanalysisError(
            f"未解決箇所の指定は{_MAX_UNRESOLVED_ITEM_IDS}件までです", kind="invalid"
        )
    normalized: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not (1 <= len(item) <= _MAX_UNRESOLVED_ITEM_ID_CHARS):
            raise FigureReanalysisError("未解決箇所の指定形式が不正です", kind="invalid")
        normalized.append(item)
    return normalized or None


def _normalize_guidance(guidance: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate and normalize a teacher guidance payload.

    Returns ``None`` when there is nothing to say (hint_text, focus_bbox, and
    unresolved_item_ids all absent/blank — unguided re-analysis, fully
    backward compatible). Raises ``FigureReanalysisError(kind="invalid")`` on
    any out-of-range value so the API route's existing ``kind -> status``
    mapping turns it into a 422 regardless of whether the caller is the HTTP
    route or a direct core call.
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

    unresolved_item_ids = _normalize_unresolved_item_ids(guidance.get("unresolved_item_ids"))

    if not hint_text and focus_bbox is None and not unresolved_item_ids:
        return None
    result: dict[str, Any] = {"hint_text": hint_text or None, "focus_bbox": focus_bbox}
    # Additive key only when present — kept out of the dict entirely otherwise
    # so every existing hint/focus-only call site's exact-equality assertions
    # are unaffected (back-compat).
    if unresolved_item_ids:
        result["unresolved_item_ids"] = unresolved_item_ids
    return result


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


# ===========================================================================
# Unresolved-item-directed re-analysis
# (docs/features/contextual_figure_analysis_iterative_verification.md)
#
# A teacher may point at specific unresolved alignment items / review
# questions / conflicts surfaced by a prior iterative analysis and ask for a
# focused re-check, instead of (or in addition to) writing free-text
# guidance. This resolves those ids against the figure's stored
# ``iterative_analysis`` and deterministically synthesizes hint_text/
# focus_bbox that ride the *existing* guided re-analysis path unchanged.
# ===========================================================================


def _resolve_unresolved_items(
    iterative_analysis: Any, item_ids: list[str],
) -> list[tuple[str, dict[str, Any]]]:
    """Resolve ``unresolved_item_ids`` against a figure's stored ``iterative_analysis``.

    Looks across ``alignment_items[].item_id`` / ``review_questions[].question_id``
    (falling back to the same deterministic ``q_{index}`` id
    ``core.figure_presentation.assign_review_question_ids`` assigns for the API
    projection, so an id copied from what the teacher saw always resolves) /
    ``unresolved_conflicts[].item_id``. Raises
    ``FigureReanalysisError(kind="invalid")`` if even one requested id is
    unknown — silently ignoring part of the teacher's request would be worse
    than failing loud.
    """
    data = iterative_analysis if isinstance(iterative_analysis, dict) else {}
    by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    for item in data.get("alignment_items") or []:
        if isinstance(item, dict) and item.get("item_id"):
            by_id.setdefault(str(item["item_id"]), ("alignment_item", item))
    for question in assign_review_question_ids(data.get("review_questions")):
        question_id = str(question.get("question_id") or "")
        if question_id:
            by_id.setdefault(question_id, ("review_question", question))
    for item in data.get("unresolved_conflicts") or []:
        if isinstance(item, dict) and item.get("item_id"):
            by_id.setdefault(str(item["item_id"]), ("unresolved_conflict", item))

    resolved: list[tuple[str, dict[str, Any]]] = []
    unknown: list[str] = []
    for item_id in item_ids:
        hit = by_id.get(item_id)
        if hit is None:
            unknown.append(item_id)
        else:
            resolved.append(hit)
    if unknown:
        raise FigureReanalysisError(
            "指定された未解決箇所が見つかりません: " + ", ".join(unknown), kind="invalid",
        )
    return resolved


def _item_text_for_hint(kind: str, item: dict[str, Any]) -> str:
    """Deterministically extract the human-readable text of one resolved item."""
    if kind == "review_question":
        return str(item.get("question") or "").strip()
    if kind == "alignment_item":
        label = str(item.get("label") or "").strip()
        evidence = str(item.get("text_evidence") or "").strip()
        if label and evidence:
            return f"{label}: {evidence}"
        return label or evidence
    # unresolved_conflict entries are free-form (schema.py keeps them
    # untyped) — fall back across the common descriptive keys.
    for key in ("description", "reason", "label", "question"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _synthesize_unresolved_hint(user_hint: str, segments: list[str]) -> str:
    """Compose the deterministic hint_text addendum, keeping any user hint first."""
    body = "; ".join(segment for segment in segments if segment)
    synthesized = f"{_UNRESOLVED_ITEM_HINT_PREFIX}{body}" if body else _UNRESOLVED_ITEM_HINT_PREFIX
    combined = f"{user_hint}\n{synthesized}" if user_hint else synthesized
    return combined[:_MAX_HINT_CHARS]


def _relative_bbox_from_page_bbox(
    page_bbox: list[float] | None, figure_bbox: list[float] | None,
) -> list[float] | None:
    """Inverse of the page-coordinate mapping ``_labels_in_focus`` uses: turn a
    PDF-page-coordinate bbox (as stored in ``inner_labels[].bbox``) into the
    image-relative ``[x0, y0, x1, y1]`` (0..1) space. ``None`` on any missing/
    degenerate input (fail-soft — the caller falls back to hint_text-only
    guidance)."""
    if not figure_bbox or len(figure_bbox) != 4 or not page_bbox or len(page_bbox) != 4:
        return None
    fx0, fy0, fx1, fy1 = figure_bbox
    width = fx1 - fx0
    height = fy1 - fy0
    if width <= 0 or height <= 0:
        return None
    x0, y0, x1, y1 = page_bbox
    return [
        (x0 - fx0) / width,
        (y0 - fy0) / height,
        (x1 - fx0) / width,
        (y1 - fy0) / height,
    ]


def _synthesize_focus_bbox(
    label_refs: list[str],
    inner_labels: list[dict] | None,
    figure_bbox: list[float] | None,
) -> list[float] | None:
    """Best-effort ``focus_bbox`` synthesis from resolved alignment items'
    ``label_ref``: union of the matching in-figure label bboxes, padded 5%,
    clamped to 0..1, and grown to the existing minimum focus dimension.
    Returns ``None`` when nothing resolves to an in-figure label with a bbox
    (fail-soft — the caller proceeds with hint_text-only guidance)."""
    if not label_refs or not figure_bbox:
        return None
    label_bbox_by_text: dict[str, list[float]] = {}
    for item in inner_labels or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        bbox = item.get("bbox")
        if text and bbox and len(bbox) == 4:
            label_bbox_by_text.setdefault(text, list(bbox))
            label_bbox_by_text.setdefault(text.casefold(), list(bbox))

    rel_boxes: list[list[float]] = []
    for label_ref in label_refs:
        ref = str(label_ref or "").strip()
        if not ref:
            continue
        page_bbox = label_bbox_by_text.get(ref) or label_bbox_by_text.get(ref.casefold())
        rel = _relative_bbox_from_page_bbox(page_bbox, figure_bbox) if page_bbox else None
        if rel:
            rel_boxes.append(rel)
    if not rel_boxes:
        return None

    x0 = min(b[0] for b in rel_boxes)
    y0 = min(b[1] for b in rel_boxes)
    x1 = max(b[2] for b in rel_boxes)
    y1 = max(b[3] for b in rel_boxes)

    pad_x = max((x1 - x0) * _FOCUS_BBOX_PADDING_RATIO, _MIN_FOCUS_DIM / 2)
    pad_y = max((y1 - y0) * _FOCUS_BBOX_PADDING_RATIO, _MIN_FOCUS_DIM / 2)
    x0 -= pad_x
    y0 -= pad_y
    x1 += pad_x
    y1 += pad_y

    if x1 - x0 < _MIN_FOCUS_DIM:
        center = (x0 + x1) / 2.0
        x0 = min(center - _MIN_FOCUS_DIM / 2.0, 1.0 - _MIN_FOCUS_DIM)
        x1 = x0 + _MIN_FOCUS_DIM
    if y1 - y0 < _MIN_FOCUS_DIM:
        center = (y0 + y1) / 2.0
        y0 = min(center - _MIN_FOCUS_DIM / 2.0, 1.0 - _MIN_FOCUS_DIM)
        y1 = y0 + _MIN_FOCUS_DIM

    x0 = max(0.0, min(1.0, x0))
    y0 = max(0.0, min(1.0, y0))
    x1 = max(0.0, min(1.0, x1))
    y1 = max(0.0, min(1.0, y1))
    if not (x1 > x0 and y1 > y0):
        return None
    return [round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6)]


def _daily_budget_key(user_id: str | None) -> tuple[str, str]:
    """CostGate の daily_key を1箇所で定義する。

    ``_consume_budget`` の check_and_count と、デフォルト agent 構築時の
    ``vision_call_budget`` 算出（``daily_remaining``）・run 後の実測超過分の
    事後計上（``count_extra_daily``）が必ず同じキーを指すようにする
    （#499 P1 修正: キーがずれるとカウンタが分裂し上限が機能しなくなる）。
    """
    return (today_str(), user_id or "")


def _daily_budget_limit(settings: Any) -> int:
    """CostGate の daily_limit を1箇所で定義する（``_consume_budget`` と
    ``vision_call_budget`` 算出とで同じ値を使う）。"""
    return max(1, int(settings.apparatus_max_calls_per_day))


def _consume_budget(figure_id: str, user_id: str | None) -> None:
    settings = get_settings()
    allowed = _cost_gate.check_and_count(
        session_limit=3,
        session_key=(user_id or "", figure_id),
        daily_limit=_daily_budget_limit(settings),
        daily_key=_daily_budget_key(user_id),
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

    # Unresolved-item-directed re-analysis (#499): resolve the requested ids
    # against this figure's stored iterative_analysis and deterministically
    # synthesize hint_text/focus_bbox that ride the existing guided path
    # below unchanged. Unknown ids fail loud before any paid work.
    if norm_guidance and norm_guidance.get("unresolved_item_ids"):
        resolved_items = _resolve_unresolved_items(
            row.get("iterative_analysis"), norm_guidance["unresolved_item_ids"],
        )
        synthesized_hint = _synthesize_unresolved_hint(
            norm_guidance.get("hint_text") or "",
            [_item_text_for_hint(kind, item) for kind, item in resolved_items],
        )
        merged_guidance = dict(norm_guidance)
        merged_guidance["hint_text"] = synthesized_hint
        if merged_guidance.get("focus_bbox") is None:
            label_refs = [
                str(item.get("label_ref") or "")
                for kind, item in resolved_items
                if kind == "alignment_item" and item.get("label_ref")
            ]
            synthesized_bbox = _synthesize_focus_bbox(
                label_refs, row.get("inner_labels") or [], row.get("bbox"),
            )
            if synthesized_bbox is not None:
                merged_guidance["focus_bbox"] = synthesized_bbox
        norm_guidance = merged_guidance

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
    # Count only requests that reached the paid vision boundary.  Missing or
    # corrupt stored images must not consume the teacher's re-analysis budget.
    # This must run *before* building a default agent's IterativeConfig below
    # — the remaining-budget computation reads the daily counter this call
    # just incremented (#499 P1 fix; see the long comment below).
    if enforce_cost_gate:
        _consume_budget(figure_id, created_by)
    if agent is not None:
        analyzer = agent
    else:
        # #499 P1 fix: a single call to ``analyzer.run`` can spend far more
        # than 1 vision call — the iterative engine budgets 1 observation +
        # up to 2 observation-repair attempts + max_iterations * (1
        # verification + up to 2 verification-repair attempts). The single
        # ``_consume_budget`` call above only accounts for 1 of those, so
        # leaving ``vision_call_budget=None`` here let a single synchronous
        # re-analysis blow through ``APPARATUS_MAX_CALLS_PER_DAY`` by ~4x.
        # Instead we pass the *actual remaining daily allowance* (including
        # the 1 call already counted) as the engine's own per-run vision
        # budget, so its ``VisionBudget.try_consume`` refuses further vision
        # calls once today's real allowance is exhausted. After ``run()``
        # returns we reconcile the true spend against the 1-call estimate
        # (see below) so unused headroom isn't lost and overshoot is
        # recorded honestly.
        vision_call_budget = None
        if enforce_cost_gate:
            remaining = _cost_gate.daily_remaining(
                daily_limit=_daily_budget_limit(settings),
                daily_key=_daily_budget_key(created_by),
            )
            vision_call_budget = 1 + remaining
        # M層（レビュー指摘 J6）: 監査記録に残すモデル名は env の素読みではなく
        # **実際に解決されるモデル** にする（orchestrator の apparatus ステージと同型）。
        # 実行時の生成コール自体は ``ApparatusSemanticsLLMClient(model=None)`` →
        # ``core/llm.py`` の入口が同じ feature で解決するため、ここで解決結果を
        # 再現しておかないと「記録上のモデル」と「実際に使ったモデル」が食い違う。
        # user ポリシーを拾うため usage_context の内側で解決する（vision capability の
        # fail-closed は llm_policy が feature から導出する）。
        # ポリシー行が無い（env / tier 既定に落ちた）ときは、このモジュールが読んでいる
        # settings の値を優先する — 本番では llm_policy 側の解決結果と同一で、
        # 差が出るのは settings を差し替えたテストのみ（env 層の正本は settings のまま）。
        with usage_context(
            _FIGURE_REANALYSIS_FEATURE, user_id=created_by, document_id=document_id
        ):
            _resolved_vision = resolve_scene_model(_FIGURE_REANALYSIS_FEATURE)
        audit_model_name = _resolved_vision.model
        if _resolved_vision.source in (SOURCE_ENV, SOURCE_TIER_DEFAULT):
            audit_model_name = getattr(settings, "apparatus_llm_model", "") or audit_model_name
        iterative_config = IterativeConfig(
            enabled=(settings.apparatus_analysis_mode != "one_shot"),
            max_iterations=settings.apparatus_reanalyze_max_iterations,
            vision_call_budget=vision_call_budget,
            model_name=audit_model_name,
        )
        analyzer = ApparatusSemanticsAgent(cartridge_id=cartridge_id, iterative_config=iterative_config)
    with usage_context(
        _FIGURE_REANALYSIS_FEATURE,
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
    if enforce_cost_gate and record is not None:
        # Reconcile the 1-call estimate charged by ``_consume_budget`` above
        # against the actual vision spend for this run (this applies
        # regardless of whether the agent was built above or injected by the
        # caller — an externally-configured agent's cost still counts). A
        # record with no ``iterative_analysis`` (one_shot mode, or an older
        # artifact shape) keeps the previous approximation of exactly 1 call.
        iterative_record = getattr(record, "iterative_analysis", None)
        actual_vision_calls = (
            int(getattr(iterative_record, "vision_calls", 1) or 0)
            if iterative_record is not None
            else 1
        )
        extra = max(0, actual_vision_calls - 1)
        if extra:
            _cost_gate.count_extra_daily(daily_key=_daily_budget_key(created_by), amount=extra)
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
