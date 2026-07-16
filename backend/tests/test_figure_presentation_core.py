from __future__ import annotations

import inspect
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for path in (str(BACKEND), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.figure_presentation import presentation_payload
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


def test_migration_has_separate_candidate_review_and_attribution_columns():
    sql = (BACKEND / "db" / "052_figure_presentation_modes.sql").read_text()
    for token in (
        "suggested_mode", "analysis_profile", "reviewed_mode", "mode_review_status",
        "mode_reviewed_by", "mode_reviewed_at",
    ):
        assert token in sql


def test_reextraction_clears_stale_ai_profile_but_preserves_teacher_override():
    source = inspect.getsource(figure_images._save_figure)
    assert "suggested_mode = 'unknown'" in source
    assert "mode_reason = ''" in source
    assert "analysis_profile = '{}'::jsonb" in source
    conflict_update = source.split("ON CONFLICT", 1)[1]
    assert "reviewed_mode =" not in conflict_update
