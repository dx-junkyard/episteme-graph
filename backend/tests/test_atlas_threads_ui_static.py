"""推定の糸（学習者側フロント）の静的ガードレール。

設計正本: ``docs/features/atlas_relation_edges_design.md`` §6（学習者向け: 推定の糸）。
親: ``docs/architecture/field_map_display_principles_2026-08-29.md``。
test_landscape_ui_static.py と同じ流儀で、ソース文字列の部分一致検査のみを行う
（実サーバ・実DOM・実API 不使用）。教員側（辺候補レビュー UI）は別ファイルの担当。

検証観点:
1. アンカー3点セット（KNOWN_UI_ANCHOR_IDS 登録 / UI_ANCHORS マップ / マニュアル節
   ``{#relation-threads}`` の実在 / フロント担体 data-ui-anchor）+ resolve_ui_anchors()
   で本文が引けること。
2. RE2 出所必須: 点線（stroke-dasharray）で描き、固定の出所文
   「AIによる推定（未確認）」と骨格版を必ず併記する。
3. 既定オフ: チェックボックスは checked を立てない（明示的に false）。
   状態は localStorage に保存しない（既定オフが毎回の既定）。
4. RE4 数値非表示: 近さの段階ラベル（かなり近い / 近い可能性 / 遠い）や cosine・
   件数をフロントに直書きしない（v1 は nearness_label すら描かない）。
5. RE6 追加フェッチなし: fetch / setInterval を書かず、AtlasOverlay.data を読む。
6. 3フック契約と結線: window.AtlasThreadsLayer が3メソッドを公開し、
   atlas-overlay.js の3箇所から ``if (window.AtlasThreadsLayer)`` ガード付きで
   呼ばれ、index.html の script タグが landscape-layer.js より後にある。
7. L2 のみ描画・fail-closed（データなしはコントロールごと非表示）。
8. CSS（.threads-controls / .threads-toggle / .threads-fact）が実在する。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONT = ROOT / "frontend" / "public"
INDEX_HTML = FRONT / "index.html"
ATLAS_OVERLAY_JS = FRONT / "js" / "atlas-overlay.js"
THREADS_JS = FRONT / "js" / "atlas-threads-layer.js"
ATLAS_CSS = FRONT / "css" / "atlas.css"
STUDENT_MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"

BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.help_kb.ui_anchors import (  # noqa: E402
    KNOWN_UI_ANCHOR_IDS,
    UI_ANCHORS,
    resolve_ui_anchors,
)

ANCHOR_ID = "atlas.relation-threads"
PROVENANCE_TEXT = "AIによる推定（未確認）"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_js_comments(src: str) -> str:
    """ブロック/行コメントを落とす（設計意図の記述を検査対象から外す）。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


# ===========================================================================
# 1. 学習者ヘルプアンカー 3点セット（正本 = core/help_kb/ui_anchors.py）
# ===========================================================================


class TestLearnerHelpAnchor:
    def test_anchor_registered_in_known_ids(self):
        assert ANCHOR_ID in KNOWN_UI_ANCHOR_IDS

    def test_anchor_mapped_to_student_manual_section(self):
        assert UI_ANCHORS.get(ANCHOR_ID) == "student/02-student.md#relation-threads"

    def test_manual_section_exists_with_explicit_anchor(self):
        md = _read(STUDENT_MANUAL)
        assert "{#relation-threads}" in md
        assert "推定の糸" in md
        # RE2: 出所（AI推定・未確認）と骨格版・実線との区別・既定オフを説明する
        assert PROVENANCE_TEXT in md
        assert "骨格 版" in md
        assert "実線" in md

    def test_frontend_carrier_exists(self):
        js = _read(THREADS_JS)
        used = set(re.findall(r'data-ui-anchor="([a-zA-Z0-9_.\-]+)"', js))
        assert ANCHOR_ID in used
        # 未登録アンカーを新規に持ち込まない
        assert not (used - set(KNOWN_UI_ANCHOR_IDS))
        # 1属性1ID（複合値を入れない）
        assert used == {ANCHOR_ID}

    def test_resolve_ui_anchors_returns_non_empty_body(self):
        resolved = resolve_ui_anchors()
        assert ANCHOR_ID in resolved, "マニュアル節が解決できません"
        assert resolved[ANCHOR_ID]["body"].strip()
        assert resolved[ANCHOR_ID]["manual_anchor"].startswith("student/")


# ===========================================================================
# 2. RE2 出所必須（点線 + 固定の出所文 + 骨格版）
# ===========================================================================


class TestProvenanceAndDashes:
    def test_lines_are_dotted(self):
        js = _strip_js_comments(_read(THREADS_JS))
        assert "stroke-dasharray" in js, "点線（stroke-dasharray）で描いていません"

    def test_fixed_provenance_string_present(self):
        js = _strip_js_comments(_read(THREADS_JS))
        assert PROVENANCE_TEXT in js

    def test_fact_line_includes_skeleton_version(self):
        js = _strip_js_comments(_read(THREADS_JS))
        assert "skeleton_version" in js
        assert "骨格 版" in js

    def test_thread_stroke_is_not_the_solid_edge_color(self):
        """実線の骨格辺（atlas-overlay.js C.edge = #B4B2A9）と同色で描かない。"""
        js = _strip_js_comments(_read(THREADS_JS))
        assert "b4b2a9" not in js.lower()


# ===========================================================================
# 3. 既定オフ・状態を永続化しない
# ===========================================================================


class TestDefaultOff:
    def test_checkbox_is_not_checked_by_default(self):
        js = _strip_js_comments(_read(THREADS_JS))
        assert "checkbox.checked = false" in js
        assert "checkbox.checked = true" not in js
        assert 'checkbox.setAttribute("checked"' not in js

    def test_no_local_storage_persistence(self):
        js = _strip_js_comments(_read(THREADS_JS))
        for banned in ("localStorage", "sessionStorage"):
            assert banned not in js, f"{banned} に状態を保存しています（既定オフの毎回性）"

    def test_closing_overlay_resets_toggle(self):
        js = _strip_js_comments(_read(THREADS_JS))
        assert "function onOverlayClosed" in js
        block = js.split("function onOverlayClosed", 1)[1].split("\n  }", 1)[0]
        assert "state.enabled = false" in block
        assert "checked = false" in block


# ===========================================================================
# 4. RE4 数値非表示・段階ラベルを直書きしない
# ===========================================================================


class TestNoNumbers:
    def test_no_nearness_scale_labels_hardcoded(self):
        js = _read(THREADS_JS)
        for banned in ("かなり近い", "近い可能性", "遠い"):
            assert banned not in js, f"近さの段階ラベル「{banned}」を直書きしています"

    def test_nearness_label_is_not_rendered_in_v1(self):
        js = _strip_js_comments(_read(THREADS_JS))
        assert "nearness_label" not in js, "v1 は近さラベルを描かない（§10 非スコープ）"

    def test_no_score_or_count_wording(self):
        js = _strip_js_comments(_read(THREADS_JS))
        for banned in ("スコア", "類似度", "件数", "cosine", "confidence", "weight"):
            assert banned not in js, f"数値・スコア表現「{banned}」が含まれています"


# ===========================================================================
# 5. RE6 追加フェッチなし・ポーリングなし
# ===========================================================================


class TestNoFetch:
    def test_reads_atlas_overlay_data_only(self):
        js = _strip_js_comments(_read(THREADS_JS))
        assert "window.AtlasOverlay" in js
        assert "data.threads" in js or ".threads" in js
        for banned in ("fetch(", "XMLHttpRequest", "setInterval"):
            assert banned not in js, f"{banned} を使っています（RE6: 追加フェッチなし）"


# ===========================================================================
# 6. 3フック契約と結線
# ===========================================================================


class TestWiring:
    def test_public_api_exposes_three_hooks(self):
        js = _strip_js_comments(_read(THREADS_JS))
        assert "window.AtlasThreadsLayer" in js
        for name in ("mountControls", "onLevelRendered", "onOverlayClosed"):
            assert f"function {name}" in js

    def test_atlas_overlay_calls_all_three_hooks_guarded(self):
        js = _read(ATLAS_OVERLAY_JS)
        for name in ("mountControls(sheet)", "onLevelRendered(level, canvas)", "onOverlayClosed()"):
            call = f"if (window.AtlasThreadsLayer) window.AtlasThreadsLayer.{name};"
            assert call in js, f"atlas-overlay.js に {call} がありません"
        # 3箇所ちょうど（余計な結線を増やさない）
        assert js.count("window.AtlasThreadsLayer.") == 3

    def test_index_html_loads_layer_after_landscape_layer(self):
        html = _read(INDEX_HTML)
        assert "js/atlas-threads-layer.js" in html
        assert html.index("js/landscape-layer.js") < html.index("js/atlas-threads-layer.js")
        # atlas-overlay.js のフックから呼ばれる側なので、その後に読み込む
        assert html.index("js/atlas-overlay.js") < html.index("js/atlas-threads-layer.js")


# ===========================================================================
# 7. L2 のみ描画・fail-closed
# ===========================================================================


class TestLevelGateAndFailClosed:
    def test_only_level_two_is_drawn(self):
        js = _strip_js_comments(_read(THREADS_JS))
        assert "const THREAD_LEVEL = 2" in js
        assert "state.lastLevel !== THREAD_LEVEL" in js

    def test_controls_hidden_when_no_threads_data(self):
        js = _strip_js_comments(_read(THREADS_JS))
        block = js.split("function refreshControls", 1)[1].split("\n  }", 1)[0]
        assert "threadsData()" in block
        assert "hidden = !visible" in block

    def test_fail_closed_helper_exists_and_is_used(self):
        js = _strip_js_comments(_read(THREADS_JS))
        assert "function failClosed" in js
        assert js.count("failClosed();") >= 4  # 各公開フック + トグル

    def test_available_flag_is_required(self):
        js = _strip_js_comments(_read(THREADS_JS))
        assert "threads.available !== true" in js

    def test_endpoints_missing_from_current_level_are_skipped(self):
        js = _strip_js_comments(_read(THREADS_JS))
        assert "if (!a || !b) return;" in js

    def test_does_not_touch_existing_map_elements(self):
        """既存の地形・実線エッジ・ミニマップに触らない（RE1）。"""
        js = _strip_js_comments(_read(THREADS_JS))
        assert "atlas-minimap" not in js
        # 自分のレイヤー g だけを remove する
        assert 'const LAYER_CLASS = "threads-layer"' in js
        assert js.count(".remove()") == 1


# ===========================================================================
# 8. CSS
# ===========================================================================


class TestStyles:
    def test_atlas_css_has_threads_styles(self):
        css = _read(ATLAS_CSS)
        for selector in (".threads-controls", ".threads-toggle", ".threads-fact", ".threads-line"):
            assert selector in css, f"atlas.css に {selector} のスタイルがありません"
