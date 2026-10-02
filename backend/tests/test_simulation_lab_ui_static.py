"""動かして確かめる（検証シミュレーション v1.1 — 学習画面）の静的ガードレール。

設計正本: docs/features/simulation_bridge_design.md §14（SB11〜SB15・§14.4 画面）。
実ブラウザ・実 API に依存せず、フロントエンドソースとマニュアルの静的検査で
受け入れ条件を固定する（test_descent_ui_static.py と同じ方式）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.help_kb.ui_anchors import KNOWN_UI_ANCHOR_IDS, UI_ANCHORS  # noqa: E402
from core.simulation_bridge.schema import FACT_NOT_VERIFICATION  # noqa: E402

JS_DIR = ROOT / "frontend" / "public" / "js"
LAB_JS = JS_DIR / "simulation-lab.js"
APP_JS = JS_DIR / "app.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
STUDENT_MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"

ANCHOR_ID = "material.simulation-lab"
OWN_ONLY = "この記録はあなたにだけ表示されます。成績評価には使用されません。"
SHARE_CONFIRM = "この実行を教員に共有し、関係する主張の台帳に候補として記帳します。確定は教員が行います。"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("//"))


class TestScriptAndEntry:
    def test_script_tag_after_discuss_and_app(self):
        html = _read(INDEX_HTML)
        assert '<script src="/js/simulation-lab.js?v=' in html
        pos_lab = html.index("/js/simulation-lab.js")
        # app.js の initApp は DOMContentLoaded で走るため、同期読み込みなら後ろでよい
        # （reconstruction.js / discuss.js と同じ並び）。
        assert html.index("/js/discuss.js") < pos_lab
        assert html.index("/js/app.js") < pos_lab
        assert html.index("/js/reconstruction.js") < pos_lab

    def test_button_with_anchor_hidden_by_default(self):
        html = _read(INDEX_HTML)
        m = re.search(r'<button[^>]*id="simulation-lab-btn"[^>]*>', html)
        assert m, "#simulation-lab-btn がありません"
        tag = m.group(0)
        assert f'data-ui-anchor="{ANCHOR_ID}"' in tag
        assert " hidden" in tag
        assert "🧪 動かして確かめる" in html

    def test_panel_lives_inside_material_region(self):
        html = _read(INDEX_HTML)
        start = html.index('id="material-region"')
        end = html.index('id="split-handle"')
        assert 'id="simulation-lab-region"' in html[start:end]

    def test_app_js_inits_and_wires_context(self):
        js = _read(APP_JS)
        assert "window.SimulationLab.init({" in js
        assert "getDocumentId: function () { return state.topicMaterialDocumentId || null; }" in js
        assert "window.SimulationLab.setContext()" in js

    def test_public_api(self):
        js = _read(LAB_JS)
        assert "window.SimulationLab = { init, open, close, refresh, setContext };" in js


class TestStatements:
    def test_disclaimers_verbatim(self):
        js = _read(LAB_JS)
        assert FACT_NOT_VERIFICATION in js
        assert OWN_ONLY in js
        assert SHARE_CONFIRM in js

    def test_three_columns(self):
        js = _read(LAB_JS)
        for col in ("あなたの予想", "理論の出力", "模擬観測"):
            assert col in js


class TestNoJudgingOrPolling:
    def test_no_label_table_mirrored(self):
        # 段階ラベルはサーバ（result.labels）が正本。JS に日本語表を書き写さない。
        js = _read(LAB_JS)
        assert "おおむね一致" not in js
        assert "result.labels" in js or "obj(result.labels)" in js

    def test_no_correctness_words(self):
        js = _read(LAB_JS)
        for word in ("正解", "不正解", "当たり", "外れ", "スコア", "点数"):
            assert word not in _strip_js_comments(js).replace("点数は付きません", ""), word

    def test_no_polling(self):
        js = _read(LAB_JS)
        assert "setInterval" not in js
        assert "setTimeout" not in js

    def test_no_auto_open(self):
        js = _strip_js_comments(_read(LAB_JS))
        # open() を呼ぶのはボタンのクリックだけ。
        calls = [m for m in re.finditer(r"(?<![\w.])open\(\)", js)
                 if not js[max(0, m.start() - 9):m.start()].endswith("function ")]
        assert len(calls) == 1, [js[m.start() - 40:m.end()] for m in calls]

    def test_does_not_read_dom_text(self):
        js = _read(LAB_JS)
        for term in ("innerText", "textContent", "getScreenContext"):
            assert term not in js


class TestAnchorAndManual:
    def test_anchor_registered(self):
        assert ANCHOR_ID in KNOWN_UI_ANCHOR_IDS
        assert UI_ANCHORS.get(ANCHOR_ID) == "student/02-student.md#simulation-lab"

    def test_frontend_anchor_values_known(self):
        used: set[str] = set()
        for path in (INDEX_HTML, APP_JS, LAB_JS):
            used |= set(re.findall(r'data-ui-anchor="([a-zA-Z0-9_.\-]+)"', _read(path)))
        assert ANCHOR_ID in used
        assert not (used - set(KNOWN_UI_ANCHOR_IDS))

    def test_manual_sections(self):
        md = _read(STUDENT_MANUAL)
        for anchor in (
            "{#simulation-lab}",
            "{#simulation-lab-steps}",
            "{#simulation-lab-not-verification}",
            "{#simulation-lab-prediction}",
            "{#simulation-lab-own-records}",
            "{#simulation-lab-share}",
            "{#simulation-lab-unavailable}",
        ):
            assert anchor in md, anchor
        assert FACT_NOT_VERIFICATION in md
        assert OWN_ONLY in md
        assert "動かして確かめる" in md

    def test_css_rules(self):
        css = _read(STYLES_CSS)
        for sel in (".simulation-lab-btn", ".simulation-lab-region", ".sim-lab-card",
                    ".material-region.simulation-lab-open"):
            assert sel in css
