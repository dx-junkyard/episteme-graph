from __future__ import annotations

import inspect
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for path in (str(BACKEND), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.figure_presentation import (
    normalize_figure_analysis_candidate,
    presentation_payload,
)
from core.document_pipeline import figure_images


def test_old_row_uses_latest_legacy_artifact_fail_soft():
    payload = presentation_payload(
        {"suggested_mode": "unknown", "reviewed_mode": None, "analysis_profile": {}},
        {
            "apparatus_name_candidate": "Optical system",
            "parts": [{"name": "laser"}, {"name": "detector"}],
            "connections": [
                {"from_part": "laser", "to_part": "detector", "relation": "light"}
            ],
        },
    )
    assert payload["suggested_mode"] == "functional_diagram"
    assert payload["effective_mode"] == "functional_diagram"
    assert payload["mode_review_status"] == "pending"
    assert payload["analysis_profile"]["connections"][0]["from_function_id"]


def test_reviewed_override_wins_and_returns_matching_empty_profile_shape():
    payload = presentation_payload({
        "suggested_mode": "functional_diagram",
        "reviewed_mode": "data_plot",
        "analysis_profile": {
            "overall_function": "system",
            "functions": [{"id": "a", "name": "A"}],
        },
    })
    assert payload["effective_mode"] == "data_plot"
    assert payload["mode_review_status"] == "reviewed"
    assert "axes" in payload["analysis_profile"]
    assert "functions" not in payload["analysis_profile"]


def _functional_candidate() -> dict:
    return {
        "candidate_type": "figure_analysis",
        "text": "Laser から Detector へ光を渡す構成",
        "presentation_mode": "functional_diagram",
        "analysis_profile": {
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
                    "role": "光を信号へ変換する",
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
        },
    }


def test_structured_figure_candidate_requires_consistent_ports():
    normalized = normalize_figure_analysis_candidate(_functional_candidate())
    assert normalized is not None
    assert normalized["presentation_mode"] == "functional_diagram"
    assert normalized["analysis_profile"]["connections"][0]["from_output_id"] == "laser_out"

    invalid = _functional_candidate()
    invalid["analysis_profile"]["connections"][0]["to_input_id"] = "missing"
    assert normalize_figure_analysis_candidate(invalid) is None
    assert normalize_figure_analysis_candidate({"text": "旧形式の自由文"}) is None


def test_teacher_reviewed_analysis_wins_without_overwriting_ai_suggestion():
    reviewed = _functional_candidate()["analysis_profile"]
    payload = presentation_payload({
        "suggested_mode": "data_plot",
        "reviewed_mode": "functional_diagram",
        "analysis_profile": {"axes": [{"orientation": "x", "label": "time"}]},
        "reviewed_analysis_mode": "functional_diagram",
        "reviewed_analysis_profile": reviewed,
        "analysis_review_status": "reviewed",
    })
    assert payload["suggested_mode"] == "data_plot"
    assert payload["effective_mode"] == "functional_diagram"
    assert payload["analysis_source"] == "teacher_reviewed"
    assert payload["analysis_review_status"] == "reviewed"
    assert payload["analysis_profile"]["functions"][0]["id"] == "laser"


def test_mismatched_reviewed_analysis_is_not_shown_under_another_mode():
    payload = presentation_payload({
        "suggested_mode": "data_plot",
        "reviewed_mode": "data_plot",
        "analysis_profile": {"axes": [{"orientation": "x", "label": "time"}]},
        "reviewed_analysis_mode": "functional_diagram",
        "reviewed_analysis_profile": _functional_candidate()["analysis_profile"],
    })
    assert payload["analysis_source"] == "ai_suggestion"
    assert payload["analysis_review_status"] == "pending"
    assert "axes" in payload["analysis_profile"]


def test_migration_has_separate_candidate_review_and_attribution_columns():
    sql = (BACKEND / "db" / "052_figure_presentation_modes.sql").read_text()
    for token in (
        "suggested_mode", "analysis_profile", "reviewed_mode", "mode_review_status",
        "mode_reviewed_by", "mode_reviewed_at",
    ):
        assert token in sql


def test_migration_053_keeps_teacher_analysis_separate_from_ai_profile():
    sql = (BACKEND / "db" / "053_figure_reviewed_analysis.sql").read_text()
    for token in (
        "reviewed_analysis_mode",
        "reviewed_analysis_profile",
        "analysis_review_status",
        "analysis_reviewed_by",
        "analysis_reviewed_at",
        "analysis_review_source_annotation_id",
    ):
        assert token in sql
    assert "ADD COLUMN IF NOT EXISTS" in sql


def test_reextraction_clears_stale_ai_profile_but_preserves_teacher_override():
    source = inspect.getsource(figure_images._save_figure)
    assert "suggested_mode = 'unknown'" in source
    assert "mode_reason = ''" in source
    assert "analysis_profile = '{}'::jsonb" in source
    conflict_update = source.split("ON CONFLICT", 1)[1]
    assert "reviewed_mode =" not in conflict_update
