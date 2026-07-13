"""分野マップを状態起点で運用できることの静的回帰テスト。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_admin_starts_with_status_and_one_next_action():
    html = read("frontend/public/admin.html")
    js = read("frontend/public/js/admin.js")
    assert 'id="atlas-overview"' in html
    assert 'id="atlas-overview-action"' in js
    assert "次にすること" in js
    assert 'activeView = "preview"' in js
    assert 'switchView("preview")' in js


def test_admin_shows_the_four_step_publish_cycle():
    html = read("frontend/public/admin.html")
    for label in ("次版を作る", "地図を確認・編集", "報告と影響を確認", "学習者向けに公開"):
        assert label in html


def test_report_flow_separates_accept_incorporate_and_publish():
    js = read("frontend/public/js/admin.js")
    core = read("backend/core/atlas_reports.py")
    migration = read("backend/db/046_atlas_report_incorporation.sql")
    assert "次版で対応済みにする" in js
    assert "/incorporate" in js
    assert "acceptedOpenCount" in js
    assert "incorporated_at IS NOT NULL" in core
    assert "incorporated_at" in migration


def test_course_placement_projects_attention_counts():
    admin = read("backend/api/routes/admin.py")
    js = read("frontend/public/js/admin.js")
    for field in ("atlas_cartridge_id", "atlas_topic_count", "topic_count"):
        assert field in admin
    assert 'apiFetch("/admin/courses")' in js
    assert "atlas:binding-saved" in js


def test_system_admin_next_steps_refresh_is_safe_before_init():
    src = read("frontend/public/js/admin-next-steps.js")
    refresh = src.split("function refresh()", 1)[1].split("function init(", 1)[0]
    guard = "if (!initialized) return;"
    assert guard in refresh
    assert refresh.index(guard) < refresh.index('apiFetch("/admin/assistant/next-steps")')


def test_learner_map_explains_current_location_and_action_priority():
    overlay = read("frontend/public/js/atlas-overlay.js")
    panel = read("frontend/public/js/atlas-panel.js")
    css = read("frontend/public/css/atlas.css")
    assert "現在地:" in overlay
    assert "atlas-context" in overlay and ".atlas-context" in css
    assert "atlas-act-primary" in panel and ".atlas-act-primary" in css
    assert 'label: "気になる ↗"' in panel
