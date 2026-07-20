from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for path in (str(BACKEND), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import figure_reanalysis  # noqa: E402
from core.document_pipeline.figure_context import FigureContext  # noqa: E402
from episteme_graph.agents.apparatus_semantics.schema import (  # noqa: E402
    ApparatusRecord,
    ApparatusSemanticsResult,
)


FIGURE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


class _Storage:
    def __init__(self, content: bytes = b"image") -> None:
        self.content = content
        self.calls = []

    def get_object(self, bucket, key):
        self.calls.append((bucket, key))
        return self.content


class _Agent:
    def __init__(self, result: ApparatusSemanticsResult) -> None:
        self.result = result
        self.inputs = []

    def run(self, **kwargs):
        self.inputs.append(kwargs)
        return self.result


def _row() -> dict:
    return {
        "id": FIGURE_ID,
        "figure_key": "p39_i0",
        "figure_label": None,
        "caption_text": "Optical system",
        "status": "extracted",
        "minio_key": "doc/p39_i0.png",
        "inner_labels": [{"text": "Laser", "bbox": [0, 0, 10, 10]}],
    }


def _result(mode: str = "functional_diagram") -> ApparatusSemanticsResult:
    profile = {
        "overall_function": "光を検出する",
        "functions": [
            {
                "id": "laser",
                "name": "Laser",
                "role": "光を出す",
                "inputs": [],
                "outputs": [{"id": "laser_out", "name": "光"}],
            },
            {
                "id": "detector",
                "name": "Detector",
                "role": "光を検出する",
                "inputs": [{"id": "detector_in", "name": "光"}],
                "outputs": [],
            },
        ],
        "connections": [{
            "id": "beam",
            "from_function_id": "laser",
            "from_output_id": "laser_out",
            "to_function_id": "detector",
            "to_input_id": "detector_in",
            "relation": "光",
        }],
    }
    if mode == "unknown":
        profile = {"summary": "判定できない"}
    record = ApparatusRecord(
        figure_id=FIGURE_ID,
        figure_key="p39_i0",
        apparatus_name_candidate="Optical system",
        matched_library_entry_id=None,
        matched_library_version_no=None,
        match_status="novel",
        evidence_quote="Laser と Detector のラベル",
        reason="画像に明示されている",
        confidence=0.8,
        suggested_mode=mode,
        mode_reason="vision",
        analysis_profile=profile,
    )
    return ApparatusSemanticsResult(
        document_id="doc-1",
        cartridge_id=None,
        apparatus_records=[record],
        validation_issues=[],
    )


def _patch_dependencies(monkeypatch):
    monkeypatch.setattr(figure_reanalysis, "load_document_figures", lambda _doc: [_row()])
    monkeypatch.setattr(
        figure_reanalysis,
        "_latest_context",
        lambda _doc, _row_value: (None, None, None),
    )
    monkeypatch.setattr(
        figure_reanalysis,
        "collect_figure_context",
        lambda *_args, **_kwargs: FigureContext(
            nearby_text=["Laser sends light to Detector"],
            abbreviations={},
        ),
    )
    monkeypatch.setattr(
        figure_reanalysis,
        "get_settings",
        lambda: SimpleNamespace(
            apparatus_context_max_items=12,
            apparatus_context_max_chars=6000,
            apparatus_max_calls_per_day=100,
        ),
    )


def test_reanalysis_persists_ai_suggestion_and_creates_structured_candidate(monkeypatch):
    _patch_dependencies(monkeypatch)
    persisted = []
    annotations = []
    monkeypatch.setattr(
        figure_reanalysis,
        "persist_suggestions",
        lambda document_id, records: persisted.append((document_id, records)) or 1,
    )

    def fake_create(ref, **kwargs):
        annotations.append((ref, kwargs))
        return {"id": "candidate-1", "status": "candidate", **kwargs}

    monkeypatch.setattr(figure_reanalysis.deliberation_store, "create_annotation", fake_create)
    storage = _Storage()
    agent = _Agent(_result())
    result = figure_reanalysis.reanalyze_figure(
        "doc-1",
        FIGURE_ID,
        created_by="user-1",
        agent=agent,
        storage=storage,
        enforce_cost_gate=False,
    )

    assert result["suggested_mode"] == "functional_diagram"
    assert result["annotation"]["status"] == "candidate"
    assert persisted and persisted[0][0] == "doc-1"
    body = annotations[0][1]["body"]
    assert body["candidate_type"] == "figure_analysis"
    assert body["analysis_profile"]["connections"][0]["from_output_id"] == "laser_out"
    assert annotations[0][1]["session_id"] is None
    assert storage.calls == [("figure-images", "doc/p39_i0.png")]
    assert agent.inputs[0]["figures"][0].image_bytes == b"image"


def test_unknown_analysis_is_not_persisted_or_exposed_as_candidate(monkeypatch):
    _patch_dependencies(monkeypatch)
    monkeypatch.setattr(
        figure_reanalysis,
        "persist_suggestions",
        lambda *_args, **_kwargs: pytest.fail("unknown result must not be persisted"),
    )
    monkeypatch.setattr(
        figure_reanalysis.deliberation_store,
        "create_annotation",
        lambda *_args, **_kwargs: pytest.fail("unknown result must not create a candidate"),
    )
    with pytest.raises(figure_reanalysis.FigureReanalysisError) as excinfo:
        figure_reanalysis.reanalyze_figure(
            "doc-1",
            FIGURE_ID,
            created_by="user-1",
            agent=_Agent(_result("unknown")),
            storage=_Storage(),
            enforce_cost_gate=False,
        )
    assert excinfo.value.kind == "invalid"


def test_missing_image_does_not_consume_cost_budget(monkeypatch):
    _patch_dependencies(monkeypatch)
    monkeypatch.setattr(
        figure_reanalysis,
        "_consume_budget",
        lambda *_args: pytest.fail("missing image must not consume budget"),
    )
    with pytest.raises(figure_reanalysis.FigureReanalysisError):
        figure_reanalysis.reanalyze_figure(
            "doc-1",
            FIGURE_ID,
            created_by="user-1",
            agent=_Agent(_result()),
            storage=_Storage(b""),
            enforce_cost_gate=True,
        )


# ===========================================================================
# Guided re-analysis (guided_figure_reanalysis_design.md)
# ===========================================================================


class TestNormalizeGuidance:
    """``_normalize_guidance`` is the single validation authority (§5) — the
    API route reuses the exact same function, so these boundary tests cover
    both call paths."""

    def test_none_input_returns_none(self):
        assert figure_reanalysis._normalize_guidance(None) is None

    def test_empty_dict_returns_none(self):
        assert figure_reanalysis._normalize_guidance({}) is None

    def test_whitespace_only_hint_and_no_bbox_returns_none(self):
        assert figure_reanalysis._normalize_guidance(
            {"hint_text": "   ", "focus_bbox": None}
        ) is None

    def test_hint_is_stripped(self):
        result = figure_reanalysis._normalize_guidance({"hint_text": "  hello  "})
        assert result == {"hint_text": "hello", "focus_bbox": None}

    def test_hint_at_2000_chars_is_accepted(self):
        text = "a" * 2000
        result = figure_reanalysis._normalize_guidance({"hint_text": text})
        assert result["hint_text"] == text

    def test_hint_over_2000_chars_is_rejected(self):
        with pytest.raises(figure_reanalysis.FigureReanalysisError) as excinfo:
            figure_reanalysis._normalize_guidance({"hint_text": "a" * 2001})
        assert excinfo.value.kind == "invalid"
        assert "長すぎ" in str(excinfo.value)

    def test_focus_bbox_wrong_length_is_rejected(self):
        with pytest.raises(figure_reanalysis.FigureReanalysisError) as excinfo:
            figure_reanalysis._normalize_guidance({"focus_bbox": [0.1, 0.1, 0.9]})
        assert excinfo.value.kind == "invalid"

    def test_focus_bbox_non_numeric_is_rejected(self):
        with pytest.raises(figure_reanalysis.FigureReanalysisError):
            figure_reanalysis._normalize_guidance(
                {"focus_bbox": ["a", 0.1, 0.9, 0.9]}
            )

    def test_focus_bbox_below_zero_is_rejected(self):
        with pytest.raises(figure_reanalysis.FigureReanalysisError):
            figure_reanalysis._normalize_guidance(
                {"focus_bbox": [-0.1, 0.1, 0.9, 0.9]}
            )

    def test_focus_bbox_above_one_is_rejected(self):
        with pytest.raises(figure_reanalysis.FigureReanalysisError):
            figure_reanalysis._normalize_guidance(
                {"focus_bbox": [0.1, 0.1, 1.1, 0.9]}
            )

    def test_focus_bbox_x1_not_greater_than_x0_is_rejected(self):
        with pytest.raises(figure_reanalysis.FigureReanalysisError):
            figure_reanalysis._normalize_guidance(
                {"focus_bbox": [0.5, 0.1, 0.1, 0.9]}
            )

    def test_focus_bbox_y1_not_greater_than_y0_is_rejected(self):
        with pytest.raises(figure_reanalysis.FigureReanalysisError):
            figure_reanalysis._normalize_guidance(
                {"focus_bbox": [0.1, 0.5, 0.9, 0.1]}
            )

    def test_focus_bbox_too_small_width_is_rejected(self):
        with pytest.raises(figure_reanalysis.FigureReanalysisError) as excinfo:
            figure_reanalysis._normalize_guidance(
                {"focus_bbox": [0.1, 0.1, 0.11, 0.9]}
            )
        assert "小さすぎ" in str(excinfo.value)

    def test_focus_bbox_too_small_height_is_rejected(self):
        with pytest.raises(figure_reanalysis.FigureReanalysisError) as excinfo:
            figure_reanalysis._normalize_guidance(
                {"focus_bbox": [0.1, 0.1, 0.9, 0.11]}
            )
        assert "小さすぎ" in str(excinfo.value)

    def test_focus_bbox_clearly_above_minimum_size_is_accepted(self):
        # 0.121 - 0.1 = 0.021, unambiguously above the 0.02 threshold even
        # accounting for float rounding (unlike a hardcoded 0.12 - 0.1,
        # which rounds to slightly *below* 0.02 in IEEE754 double).
        result = figure_reanalysis._normalize_guidance(
            {"focus_bbox": [0.1, 0.1, 0.121, 0.121]}
        )
        assert result["focus_bbox"] == [0.1, 0.1, 0.121, 0.121]

    def test_hint_and_focus_bbox_together(self):
        result = figure_reanalysis._normalize_guidance({
            "hint_text": "左下のEOM",
            "focus_bbox": [0.1, 0.1, 0.5, 0.5],
        })
        assert result == {"hint_text": "左下のEOM", "focus_bbox": [0.1, 0.1, 0.5, 0.5]}


class TestLabelsInFocus:
    def test_returns_empty_when_figure_bbox_missing(self):
        assert figure_reanalysis._labels_in_focus(
            [{"text": "Laser", "bbox": [0, 0, 10, 10]}], None, [0.0, 0.0, 0.5, 0.5]
        ) == []

    def test_returns_empty_when_figure_bbox_degenerate(self):
        assert figure_reanalysis._labels_in_focus(
            [{"text": "Laser", "bbox": [0, 0, 10, 10]}],
            [10.0, 10.0, 10.0, 50.0],
            [0.0, 0.0, 0.5, 0.5],
        ) == []

    def test_intersection_includes_boundary_straddling_label(self):
        figure_bbox = [0.0, 0.0, 100.0, 100.0]
        inner_labels = [
            {"text": "Laser", "bbox": [0, 0, 10, 10]},        # fully inside
            {"text": "Detector", "bbox": [60, 60, 90, 90]},   # fully outside
            {"text": "PBS", "bbox": [45, 45, 55, 55]},        # straddles the boundary
        ]
        focus_bbox = [0.0, 0.0, 0.5, 0.5]  # -> page coords [0, 0, 50, 50]
        labels = figure_reanalysis._labels_in_focus(inner_labels, figure_bbox, focus_bbox)
        assert labels == ["Laser", "PBS"]

    def test_deduplicates_repeated_label_text(self):
        figure_bbox = [0.0, 0.0, 100.0, 100.0]
        inner_labels = [
            {"text": "Laser", "bbox": [0, 0, 10, 10]},
            {"text": "Laser", "bbox": [5, 5, 15, 15]},
        ]
        focus_bbox = [0.0, 0.0, 0.5, 0.5]
        labels = figure_reanalysis._labels_in_focus(inner_labels, figure_bbox, focus_bbox)
        assert labels == ["Laser"]

    def test_ignores_labels_missing_text_or_bbox(self):
        figure_bbox = [0.0, 0.0, 100.0, 100.0]
        inner_labels = [
            {"text": "", "bbox": [0, 0, 10, 10]},
            {"text": "NoBbox"},
            "not-a-dict",
        ]
        focus_bbox = [0.0, 0.0, 0.5, 0.5]
        assert figure_reanalysis._labels_in_focus(inner_labels, figure_bbox, focus_bbox) == []


class _RaisingFitz:
    @staticmethod
    def open(*_args, **_kwargs):
        raise RuntimeError("boom")


class TestCropFocusImage:
    def test_returns_none_when_fitz_unavailable(self, monkeypatch):
        monkeypatch.setattr(figure_reanalysis, "fitz", None)
        assert figure_reanalysis._crop_focus_image(b"data", [0.0, 0.0, 0.5, 0.5]) is None

    def test_returns_none_for_empty_image_bytes(self):
        assert figure_reanalysis._crop_focus_image(b"", [0.0, 0.0, 0.5, 0.5]) is None

    def test_fail_soft_returns_none_on_exception(self, monkeypatch):
        monkeypatch.setattr(figure_reanalysis, "fitz", _RaisingFitz())
        assert figure_reanalysis._crop_focus_image(
            b"\x89PNG\r\n\x1a\nfakepngbytes", [0.0, 0.0, 0.5, 0.5]
        ) is None


def test_invalid_guidance_is_rejected_before_any_storage_or_db_access(monkeypatch):
    """Validation must happen before the paid vision boundary (§5) — and, as
    a consequence, before any figure/image lookup at all."""

    def _fail_load(_document_id):
        pytest.fail("load_document_figures must not run for invalid guidance")

    monkeypatch.setattr(figure_reanalysis, "load_document_figures", _fail_load)
    with pytest.raises(figure_reanalysis.FigureReanalysisError) as excinfo:
        figure_reanalysis.reanalyze_figure(
            "doc-1",
            FIGURE_ID,
            created_by="user-1",
            guidance={"hint_text": "a" * 2001},
            enforce_cost_gate=False,
        )
    assert excinfo.value.kind == "invalid"


def test_unguided_reanalysis_is_fully_unchanged(monkeypatch):
    """guidance=None (or omitted) must behave exactly as before this feature:
    no ``guidance`` key in the annotation body, no reason prefix, and
    guidance/guidance_note in the result default to falsy."""
    _patch_dependencies(monkeypatch)
    monkeypatch.setattr(figure_reanalysis, "persist_suggestions", lambda *_a, **_k: 1)
    captured: dict = {}

    def fake_create(ref, **kwargs):
        captured.update(kwargs)
        return {"id": "candidate-1", "status": "candidate", **kwargs}

    monkeypatch.setattr(figure_reanalysis.deliberation_store, "create_annotation", fake_create)
    result = figure_reanalysis.reanalyze_figure(
        "doc-1",
        FIGURE_ID,
        created_by="user-1",
        agent=_Agent(_result()),
        storage=_Storage(),
        enforce_cost_gate=False,
    )
    assert "guidance" not in captured["body"]
    assert not captured["reason"].startswith("教員指示付き再解析")
    assert result["guidance"] is None
    assert result["guidance_note"] == ""


def test_guided_reanalysis_adds_guidance_to_annotation_body_and_reason_prefix(monkeypatch):
    _patch_dependencies(monkeypatch)
    monkeypatch.setattr(figure_reanalysis, "persist_suggestions", lambda *_a, **_k: 1)
    captured: dict = {}

    def fake_create(ref, **kwargs):
        captured.update(kwargs)
        return {"id": "candidate-1", "status": "candidate", **kwargs}

    monkeypatch.setattr(figure_reanalysis.deliberation_store, "create_annotation", fake_create)
    result = figure_reanalysis.reanalyze_figure(
        "doc-1",
        FIGURE_ID,
        created_by="user-1",
        guidance={"hint_text": "左下のEOM", "focus_bbox": None},
        agent=_Agent(_result()),
        storage=_Storage(),
        enforce_cost_gate=False,
    )
    assert captured["body"]["guidance"] == {"hint_text": "左下のEOM", "focus_bbox": None}
    assert captured["reason"].startswith("教員指示付き再解析: ")
    assert result["guidance"] == {"hint_text": "左下のEOM", "focus_bbox": None}
    # ``_result()``'s ApparatusRecord uses the schema default (no guidance_note
    # set by the fake agent) — the field is forwarded verbatim either way.
    assert result["guidance_note"] == ""


def _row_with_page_bbox() -> dict:
    row = _row()
    row["bbox"] = [0.0, 0.0, 100.0, 100.0]
    row["inner_labels"] = [
        {"text": "Laser", "bbox": [0, 0, 10, 10]},
        {"text": "Detector", "bbox": [60, 60, 90, 90]},
    ]
    return row


def test_guided_reanalysis_propagates_focus_fields_to_figure_image_input(monkeypatch):
    _patch_dependencies(monkeypatch)
    monkeypatch.setattr(
        figure_reanalysis, "load_document_figures", lambda _doc: [_row_with_page_bbox()]
    )
    monkeypatch.setattr(figure_reanalysis, "persist_suggestions", lambda *_a, **_k: 1)
    monkeypatch.setattr(
        figure_reanalysis.deliberation_store,
        "create_annotation",
        lambda ref, **kwargs: {"id": "candidate-1", "status": "candidate", **kwargs},
    )
    monkeypatch.setattr(figure_reanalysis, "_crop_focus_image", lambda *_a, **_k: b"cropped-bytes")

    agent = _Agent(_result())
    figure_reanalysis.reanalyze_figure(
        "doc-1",
        FIGURE_ID,
        created_by="user-1",
        guidance={"hint_text": "  左下のEOM  ", "focus_bbox": [0.0, 0.0, 0.5, 0.5]},
        agent=agent,
        storage=_Storage(),
        enforce_cost_gate=False,
    )
    fig_input = agent.inputs[0]["figures"][0]
    assert fig_input.guidance_text == "左下のEOM"
    assert fig_input.focus_bbox_rel == [0.0, 0.0, 0.5, 0.5]
    assert fig_input.focus_image_bytes == b"cropped-bytes"
    assert fig_input.focus_label_texts == ["Laser"]


def test_unguided_reanalysis_never_sets_focus_fields_on_figure_image_input(monkeypatch):
    _patch_dependencies(monkeypatch)
    monkeypatch.setattr(figure_reanalysis, "persist_suggestions", lambda *_a, **_k: 1)
    monkeypatch.setattr(
        figure_reanalysis.deliberation_store,
        "create_annotation",
        lambda ref, **kwargs: {"id": "candidate-1", "status": "candidate", **kwargs},
    )
    agent = _Agent(_result())
    figure_reanalysis.reanalyze_figure(
        "doc-1",
        FIGURE_ID,
        created_by="user-1",
        agent=agent,
        storage=_Storage(),
        enforce_cost_gate=False,
    )
    fig_input = agent.inputs[0]["figures"][0]
    assert fig_input.guidance_text == ""
    assert fig_input.focus_bbox_rel is None
    assert fig_input.focus_image_bytes is None
    assert fig_input.focus_label_texts == []


def test_guided_reanalysis_uses_same_cost_gate_key_as_unguided(monkeypatch):
    """GF6: no new limit/key — guided and unguided calls must consume the
    exact same (figure_id, user_id) CostGate bucket."""
    _patch_dependencies(monkeypatch)
    monkeypatch.setattr(figure_reanalysis, "persist_suggestions", lambda *_a, **_k: 1)
    monkeypatch.setattr(
        figure_reanalysis.deliberation_store,
        "create_annotation",
        lambda ref, **kwargs: {"id": "candidate-1", "status": "candidate", **kwargs},
    )
    consumed = []
    monkeypatch.setattr(
        figure_reanalysis,
        "_consume_budget",
        lambda figure_id, user_id: consumed.append((figure_id, user_id)),
    )
    figure_reanalysis.reanalyze_figure(
        "doc-1",
        FIGURE_ID,
        created_by="user-1",
        guidance={"hint_text": "hello", "focus_bbox": None},
        agent=_Agent(_result()),
        storage=_Storage(),
        enforce_cost_gate=True,
    )
    assert consumed == [(FIGURE_ID, "user-1")]
