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
