"""装置図理解機能の拡張（Phase 2）— 装置候補オーバーレイ UI の静的ガードレール。

frontend/public/js/admin.js に追加された figures-modal 上の第2モーダル
（図の上にパーツ・図中ラベルの位置を重ねて確認する「図で確認」導線）を、
既存の atlas / deliberation 系静的テスト（test_atlas_minimap_and_cues_static.py /
test_deliberation_ui_static.py）と同様、ソースの静的検証で受け入れ条件を固定する。

検査項目:
1. openApparatusOverlayModal / _figureRelativeRect / _renderApparatusOverlayBoxes
   が定義されていること
2. `.figure-overlay-btn` のイベント配線（querySelectorAll + 該当ボタン markup）
3. オーバーレイ詳細描画（パーツ選択・ラベル選択・パーツ表・モーダル見出し）で
   escHtml(...) が使われていること（XSS ガード）
4. `_figureRelativeRect` に 0 除算ガード（幅/高さ <= 0 の早期 return）があること
5. `openApparatusOverlayModal` 本体に書き込み系 API 呼び出し（POST/PUT/DELETE）が
   無いこと（装置候補は常に review_required の candidate であり、位置確認のみを
   行う — 確定操作は既存のライブラリ昇格導線に委ねる、candidate-only 原則）
6. admin.js 全体の ES5 準拠リグレッションガード（アロー関数・const/let 宣言が
   ファイル全体に一切無いことを確認したうえで固定する）。テンプレートリテラルの
   バッククォートはファイル内に markdown コードフェンス文字として複数箇所
   existing する（実際の ES6 template literal ではない）ため、この検査だけは
   Phase 2 で追加されたオーバーレイ領域のソースに絞る。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"

_OVERLAY_REGION_START = "// ── 装置候補オーバーレイ"
_OVERLAY_REGION_END = "// ── ナレッジライブラリ"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _overlay_region(src: str) -> str:
    """Phase 2 で追加された装置候補オーバーレイのソース領域全体を切り出す。"""
    start = src.index(_OVERLAY_REGION_START)
    end = src.index(_OVERLAY_REGION_END, start)
    return src[start:end]


def _function_body(src: str, name: str, next_marker: str) -> str:
    """`name` 関数の定義から次のマーカー（次の関数定義や見出しコメント）までを
    切り出す。既存の静的テスト（test_deliberation_ui_static.py の
    ``src.index("function ...")`` パターン）と同じ簡易抽出方式。"""
    start = src.index(f"function {name}")
    end = src.index(next_marker, start)
    return src[start:end]


class TestOverlayFunctionsDefined:
    """検査項目1: 3つの主要関数が定義されていること。"""

    def test_open_apparatus_overlay_modal_defined(self):
        src = _read(ADMIN_JS)
        assert "function openApparatusOverlayModal(fig, candidateIdx)" in src

    def test_figure_relative_rect_defined(self):
        src = _read(ADMIN_JS)
        assert "function _figureRelativeRect(fb, b)" in src

    def test_render_apparatus_overlay_boxes_defined(self):
        src = _read(ADMIN_JS)
        assert "function _renderApparatusOverlayBoxes(figBbox, parts, innerLabels)" in src


class TestFigureOverlayButtonWiring:
    """検査項目2: `.figure-overlay-btn` のイベント配線。"""

    def test_button_markup_present(self):
        src = _read(ADMIN_JS)
        assert 'class="admin-action-btn figure-overlay-btn"' in src

    def test_query_selector_wiring_present(self):
        src = _read(ADMIN_JS)
        assert 'body.querySelectorAll(".figure-overlay-btn")' in src

    def test_click_handler_opens_overlay_modal(self):
        src = _read(ADMIN_JS)
        start = src.index('body.querySelectorAll(".figure-overlay-btn")')
        end = src.index("});", start)
        wiring = src[start:end]
        assert "addEventListener(\"click\"" in wiring
        assert "openApparatusOverlayModal(fig, candIdx)" in wiring

    def test_overlay_button_only_shown_when_position_data_available(self):
        """「図で確認」ボタンは fig.bbox + (part.bbox または inner_labels) が
        揃っているときのみ表示される（位置と対応付けできない候補では出さない）。"""
        src = _read(ADMIN_JS)
        assert "var canOverlay = !!fig.bbox" in src


class TestOverlayDetailRenderingUsesEscHtml:
    """検査項目3: XSS ガード。動的テキストの描画は escHtml(...) を経由する。"""

    def test_select_part_detail_uses_esc_html(self):
        src = _read(ADMIN_JS)
        body = _function_body(src, "_apparatusOverlaySelectPart", "function _apparatusOverlaySelectLabel")
        assert body.count("escHtml(") >= 3

    def test_select_label_detail_uses_esc_html(self):
        src = _read(ADMIN_JS)
        body = _function_body(src, "_apparatusOverlaySelectLabel", "function _renderApparatusOverlayBoxes")
        assert "escHtml(" in body

    def test_parts_table_uses_esc_html(self):
        src = _read(ADMIN_JS)
        body = _function_body(src, "_apparatusOverlayPartsTableHtml", "function openApparatusOverlayModal")
        assert body.count("escHtml(") >= 3

    def test_modal_heading_uses_esc_html(self):
        src = _read(ADMIN_JS)
        body = _function_body(src, "openApparatusOverlayModal", _OVERLAY_REGION_END)
        assert "escHtml(figLabel)" in body
        assert "escHtml(nameCandidate)" in body

    def test_overlay_region_esc_html_usage_is_pervasive(self):
        """領域全体で escHtml の使用が十分にあることをまとめて確認する
        （個々の関数チェックの合計と整合する下限値）。"""
        region = _overlay_region(_read(ADMIN_JS))
        assert region.count("escHtml(") >= 10


class TestFigureRelativeRectZeroDivisionGuard:
    """検査項目4: `_figureRelativeRect` の 0 除算ガード。"""

    def test_width_and_height_guard_present(self):
        src = _read(ADMIN_JS)
        body = _function_body(src, "_figureRelativeRect", "function _apparatusOverlayClearSelection")
        assert "if (w <= 0 || h <= 0) return null;" in body

    def test_degenerate_result_rect_also_guarded(self):
        """変換後の相対幅・高さが 0 以下になるケース（bbox が図領域と交差しない等）
        も同様に null を返す（呼び出し側で描画をスキップできる）。"""
        src = _read(ADMIN_JS)
        body = _function_body(src, "_figureRelativeRect", "function _apparatusOverlayClearSelection")
        assert "if (width <= 0 || height <= 0) return null;" in body


class TestOverlayModalHasNoWriteApiCalls:
    """検査項目5: candidate-only 原則。装置候補の確定操作はライブラリ昇格導線に
    委ね、オーバーレイモーダル自体は位置確認のみ（書き込み系 fetch を呼ばない）。"""

    def test_no_write_http_methods_in_modal_body(self):
        src = _read(ADMIN_JS)
        body = _function_body(src, "openApparatusOverlayModal", _OVERLAY_REGION_END)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert f'"{method}"' not in body
            assert f"'{method}'" not in body

    def test_only_read_only_image_fetch_present(self):
        """モーダル本体が呼ぶ唯一の API 呼び出しは画像取得（apiFetchRaw、GET 相当）
        のみであること。"""
        src = _read(ADMIN_JS)
        body = _function_body(src, "openApparatusOverlayModal", _OVERLAY_REGION_END)
        calls = re.findall(r"api(?:Fetch|FetchRaw)\(", body)
        assert calls, "openApparatusOverlayModal 内に API 呼び出しが見つかりません"
        assert all(c == "apiFetchRaw(" for c in calls), (
            f"想定外の API 呼び出しがあります（GET 相当の apiFetchRaw のみを想定）: {calls}"
        )


class TestAdminJsEs5RegressionGuard:
    """検査項目6: admin.js は ES5 準拠（開発ルール5）。追加分の装置候補オーバーレイ
    実装がこのリグレッションを崩していないことを固定する。"""

    def test_no_arrow_functions_anywhere_in_file(self):
        """ファイル全体に既存の `=>` 使用が無いことを確認済みのため、ファイル全体
        で検査する（追加分だけに絞る必要が無い）。"""
        src = _read(ADMIN_JS)
        assert "=>" not in src

    def test_no_const_or_let_declarations_anywhere_in_file(self):
        src = _read(ADMIN_JS)
        assert re.search(r"\bconst\s+\w", src) is None
        assert re.search(r"\blet\s+\w", src) is None

    def test_no_template_literals_in_overlay_region(self):
        """バッククォートはファイルの他箇所（markdown コードフェンスを組み立てる
        通常の文字列リテラル中の文字、例: "```json"）に既存で複数存在するため、
        ファイル全体検査だと既存コードに誤って引っかかる。したがってこの検査は
        Phase 2 で追加された装置候補オーバーレイ領域のソースに絞る（追加分に
        実際の ES6 template literal が無いことを確認する）。"""
        region = _overlay_region(_read(ADMIN_JS))
        assert "`" not in region

    def test_overlay_region_uses_var_not_const_or_let(self):
        region = _overlay_region(_read(ADMIN_JS))
        assert "var " in region
        assert re.search(r"\bconst\s+\w", region) is None
        assert re.search(r"\blet\s+\w", region) is None
