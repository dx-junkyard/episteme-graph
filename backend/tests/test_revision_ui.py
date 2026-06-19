"""Source-assertion tests for the revisions admin UI (#408).

Follows the repo convention (cf. test_course_selector_ui / test_schema_proposals_ui)
of asserting on the vanilla-JS/HTML/CSS source so the UI wiring is covered
without a browser.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ADMIN_JS = REPO / "frontend/public/js/admin.js"
STYLES = REPO / "frontend/public/css/styles.css"


@pytest.fixture(scope="module")
def admin_js():
    return ADMIN_JS.read_text(encoding="utf-8")


def test_revisions_module_exposed(admin_js):
    assert "window.EGRevisions" in admin_js
    assert "var EGRevisions = (function ()" in admin_js


def test_launch_button_present(admin_js):
    assert "material-revision-item" in admin_js
    assert "EGRevisions.open(docId)" in admin_js


def test_distinguishes_active_and_latest_run(admin_js):
    # active run (adopted artifacts) vs latest run (processing status) must be
    # shown distinctly, never conflated.
    assert "active_run_id" in admin_js
    assert "latest_run_id" in admin_js
    assert "採用版 (active run)" in admin_js


def test_decision_actions_present(admin_js):
    for fn in ("acceptRevision", "rejectRevision", "reviseRevision", "startRevision"):
        assert fn in admin_js
    assert "/accept" in admin_js and "/reject" in admin_js and "/revise" in admin_js


def test_accept_handles_conflict_and_block(admin_js):
    assert "409" in admin_js  # optimistic-concurrency conflict surfaced
    assert "422" in admin_js  # hard-error / protected block surfaced


def test_protected_change_warning_and_confirm(admin_js):
    assert "eg-rev-confirm-protected" in admin_js
    assert "confirm_protected" in admin_js
    assert "保護項目" in admin_js


def test_before_after_metrics_and_filters(admin_js):
    assert "品質指標 (before / after)" in admin_js
    assert "quality_before" in admin_js and "quality_after" in admin_js
    # diff filterable by claim/equation/component/graph
    for f in ("claim", "equation", "component", "graph_edge"):
        assert f in admin_js


def test_entity_change_traceability_rendered(admin_js):
    assert "checkpoint" in admin_js
    assert "evidence" in admin_js
    assert "原文" in admin_js


def test_styles_added():
    css = STYLES.read_text(encoding="utf-8")
    assert ".eg-rev-overlay" in css
    assert ".eg-rev-badge-protected" in css
