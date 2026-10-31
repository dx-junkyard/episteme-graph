"""解析の縮退（IK-0715）の UI 静的検査: 教材行チップとグラフレビューのバナー。"""

from __future__ import annotations

from pathlib import Path

from tests.guardrail_helpers import extract_function_source

ROOT = Path(__file__).resolve().parents[2]
_JS = ROOT / "frontend" / "public" / "js"
_ADMIN = (_JS / "admin.js").read_text(encoding="utf-8")
_GR = (_JS / "admin-graph-review.js").read_text(encoding="utf-8")
_HTML = (ROOT / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")


def _js_function(src: str, name: str) -> str:
    start = src.index("function " + name + "(")
    end = src.index("\n  }\n", start)
    return src[start:end]


class TestMaterialsChip:
    def test_chip_rendered_next_to_reference_health(self):
        idx = _ADMIN.index("html += materialReferenceHealthChipHtml(m);")
        assert "html += materialAnalysisDegradationChipHtml(m);" in _ADMIN[idx: idx + 400]

    def test_chip_only_for_degraded(self):
        fn = _js_function(_ADMIN, "materialAnalysisDegradationChipHtml")
        assert 'deg.status !== "degraded"' in fn
        assert 'return "";' in fn
        assert "title=" in fn  # 事実文はツールチップ
        assert "data-ui-anchor" not in fn  # 事実の表示で操作要素ではない

    def test_chip_has_no_counts(self):
        fn = _js_function(_ADMIN, "materialAnalysisDegradationChipHtml")
        assert ".length" not in fn


class TestGraphBanner:
    def test_banner_carrier_and_render(self):
        assert 'id="graph-review-degradation"' in _GR
        fn = _js_function(_GR, "renderDegradationBanner")
        assert 'deg.status === "degraded"' in fn
        assert "el.hidden = true" in fn
        assert "renderDegradationBanner(state.graph)" in _GR

    def test_banner_prefix_mirrors_core(self):
        from core.analysis_degradation import BANNER_PREFIX

        assert f'var DEGRADATION_BANNER_PREFIX = "{BANNER_PREFIX}";' in _GR

    def test_banner_has_no_anchor(self):
        seg = _GR[_GR.index('id="graph-review-degradation"') - 200: _GR.index('id="graph-review-degradation"') + 200]
        assert "data-ui-anchor" not in seg


class TestCacheBust:
    def test_versions_bumped(self):
        # admin.js は並行の是正波が順に上げる（特定の値に固定すると後の波で必ず赤になる）。
        # ここでは「?v= が付いていて、この波の前の値ではない」ことだけを見る。
        import re as _re

        m = _re.search(r'/js/admin\.js\?v=([\w.-]+)"', _HTML)
        assert m and m.group(1) != "atlas-review-ik0848-20261022"
        assert "/js/admin-graph-review.js?v=theory-modules-analysis-degradation-20261022" in _HTML
