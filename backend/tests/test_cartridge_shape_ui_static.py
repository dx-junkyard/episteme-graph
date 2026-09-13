"""再解析モーダルの「分野の適合」区画の UI 契約（概念レジストリ P3-7 / §8）。

固定するのは:

1. 区画の器（``#reanalyze-domain-fit-row``）が admin.js の再解析モーダルにあり、
   ``data-ui-anchor="materials.reanalyze-domain-fit"`` を担っている。
2. ``admin-cartridge-fit.js`` が fit API を 1 回だけ呼び、分野の選び直しで引き直す。
3. **数値を描かない**（KR6）・**文言をフロントに焼き込まない**（サーバの facts をそのまま）。
4. **fail-soft**（取得失敗で何も描かない・ポーリングしない）。
5. アンカー3点セット（担体 / ``admin_ui_anchors.py`` / マニュアル節）。
6. admin.html が admin.js より前にモジュールを読み込む。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
FIT_JS = ROOT / "frontend" / "public" / "js" / "admin-cartridge-fit.js"
MANUAL = ROOT / "docs" / "manual" / "teacher" / "11-admin-materials.md"

ANCHOR_ID = "materials.reanalyze-domain-fit"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fit_body() -> str:
    """先頭のブロックコメント（設計の引用）を除いたコード本体。"""
    js = _read(FIT_JS)
    return js[js.index("*/") + 2:]


class TestCarrier:
    def test_module_file_exists(self):
        assert FIT_JS.exists()

    def test_reanalyze_modal_has_the_container(self):
        js = _read(ADMIN_JS)
        assert 'id="reanalyze-domain-fit-row"' in js
        assert 'data-ui-anchor="%s"' % ANCHOR_ID in js

    def test_container_sits_next_to_the_domain_row(self):
        """分野の選択のすぐ下（§8 の配置）。"""
        js = _read(ADMIN_JS)
        assert js.index('id="reanalyze-domain-row"') < js.index('id="reanalyze-domain-fit-row"')
        assert js.index('id="reanalyze-domain-fit-row"') < js.index('id="reanalyze-llm-model-row"')

    def test_admin_js_mounts_the_module(self):
        js = _read(ADMIN_JS)
        assert "window.AdminCartridgeFit.mount(" in js
        assert "window.AdminCartridgeFit.init({ apiFetch: apiFetch })" in js

    def test_admin_html_loads_before_admin_js(self):
        html = _read(ADMIN_HTML)
        assert "/js/admin-cartridge-fit.js" in html
        assert html.index("/js/admin-cartridge-fit.js") < html.index("/js/admin.js?")


class TestModuleBehaviour:
    def test_calls_the_fit_endpoint(self):
        js = _read(FIT_JS)
        assert "/admin/cartridges/" in js
        assert "/fit?document_id=" in js

    def test_reloads_when_the_domain_selection_changes(self):
        js = _read(FIT_JS)
        assert 'addEventListener("change"' in js
        assert "#reanalyze-domain-select" in js

    def test_no_polling(self):
        js = _read(FIT_JS)
        assert "setInterval" not in js
        assert "setTimeout" not in js

    def test_fail_soft_renders_nothing(self):
        js = _read(FIT_JS)
        assert 'catch(function () { container.innerHTML = ""; })' in js

    def test_no_domain_selected_renders_nothing(self):
        """「指定しない」のときは適合を語らない。"""
        js = _read(FIT_JS)
        assert 'if (!key) { container.innerHTML = ""; return; }' in js

    def test_read_only(self):
        js = _read(FIT_JS)
        for forbidden in ('method: "POST"', 'method: "PATCH"', 'method: "DELETE"'):
            assert forbidden not in js

    def test_renders_only_server_facts(self):
        """事実文はサーバが正本（フロントに適合の文言を焼き込まない）。"""
        js = _fit_body()
        assert "data.facts" in js or "(data && data.facts)" in js
        for forbidden in ("配置があります", "扱わないと宣言", "解析されていない"):
            assert forbidden not in js

    def test_no_numbers(self):
        """件数・スコアを描かない（KR6）。"""
        js = _fit_body()
        for forbidden in ("confidence", "score", ".length + ", "件"):
            assert forbidden not in js

    def test_module_is_es5_iife(self):
        js = _read(FIT_JS)
        assert js.lstrip().startswith("/*")
        js = _fit_body()
        assert '"use strict";' in js
        for forbidden in ("=>", "const ", "let ", "`"):
            assert forbidden not in js

    def test_exposes_only_init_and_mount(self):
        js = _read(FIT_JS)
        assert "window.AdminCartridgeFit = { init: init, mount: mount };" in js


class TestAnchorTriplet:
    def test_registry_entry(self):
        from core.help_kb import admin_ui_anchors

        assert ANCHOR_ID in admin_ui_anchors.KNOWN_ADMIN_UI_ANCHOR_IDS
        assert (
            admin_ui_anchors.ADMIN_UI_ANCHORS[ANCHOR_ID]
            == "teacher/11-admin-materials.md#reanalyze-domain-fit"
        )

    def test_manual_section_exists(self):
        text = _read(MANUAL)
        assert "{#reanalyze-domain-fit}" in text

    def test_manual_section_states_it_is_not_a_verdict(self):
        """判定でも推薦でもないことを明記する（KR6 / 原則1）。"""
        text = _read(MANUAL)
        section = text[text.index("{#reanalyze-domain-fit}"):]
        section = section[: section.index("\n### ")]
        assert "判定" in section
        assert "数値" in section

    def test_manual_section_has_no_disabled_state_gap(self):
        """無効化され得る要素の作法（何も出ない場合の説明）を持つ。"""
        text = _read(MANUAL)
        section = text[text.index("{#reanalyze-domain-fit}"):]
        section = section[: section.index("\n### ")]
        assert "表示されない場合" in section

    def test_is_scanned_by_the_anchor_coverage_test(self):
        src = (BACKEND / "tests" / "test_admin_help_inspect_ui_static.py").read_text(
            encoding="utf-8"
        )
        block = src[src.index("_ADMIN_FRONTEND_SOURCES"):]
        block = block[: block.index("]")]
        assert "admin-cartridge-fit.js" in block

    def test_module_uses_no_unregistered_anchors(self):
        from core.help_kb.admin_ui_anchors import KNOWN_ADMIN_UI_ANCHOR_IDS

        used = set(re.findall(r'data-ui-anchor="([^"]+)"', _read(FIT_JS)))
        assert sorted(a for a in used if a not in KNOWN_ADMIN_UI_ANCHOR_IDS) == []
