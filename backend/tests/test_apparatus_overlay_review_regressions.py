"""Static regressions for apparatus overlay alignment and async isolation."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"


def _overlay_source() -> str:
    source = ADMIN_JS.read_text(encoding="utf-8")
    start = source.index("// ── 装置候補オーバーレイ")
    end = source.index("// ── ナレッジライブラリ", start)
    return source[start:end]


def test_loaded_image_and_positioning_container_share_the_same_box():
    source = _overlay_source()
    assert 'style="display:none;width:100%;height:auto"' in source
    assert 'wrap.style.minHeight = "0";' in source
    assert 'wrap.style.display = "block";' in source


def test_stale_image_requests_cannot_paint_a_new_modal():
    source = _overlay_source()
    assert "requestId: 0" in source
    assert "var requestId = ++_apparatusOverlayState.requestId;" in source
    assert "requestId !== _apparatusOverlayState.requestId" in source


def test_async_dom_queries_are_scoped_to_the_modal_instance():
    source = _overlay_source()
    assert 'overlay.querySelector("#apparatus-overlay-img")' in source
    assert 'overlay.querySelector("#apparatus-overlay-img-placeholder")' in source

