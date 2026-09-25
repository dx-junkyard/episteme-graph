"""図領域の幾何推定と情報量判定（``core/document_pipeline/figure_regions.py``）の単体テスト。

設計: docs/features/image_pipeline_knowledge_library_design.md §17。2026-09-24 に
fujimoto_d.pdf で実測した3つの失敗（caption 幅で図の両端が切れる / reading order 依存で
本文を巻き込む / ベクター図の部品画像を図として採用し黒い帯になる）を、PyMuPDF で
その場で組んだ合成 PDF で再現して固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

_BACKEND = Path(__file__).resolve().parents[2]
_SRC = _BACKEND.parent / "src"
for _p in (str(_BACKEND), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.document_pipeline import figure_regions as fr  # noqa: E402

_LOREM = (
    "The interaction between the field and the photons induces a rotational oscillation of "
    "linear polarization. By increasing the optical path length of the linearly polarized light "
    "the amplitude of this rotational oscillation can be enhanced considerably in the cavity. "
)
_W, _H = 595.0, 842.0


def _body(page, rect, text=_LOREM * 8, fontsize=10.0):
    """rect に収まる分だけ本文を流し込む（収まらなければ語を削って入れ直す）。"""
    words = text.split()
    while words:
        rc = page.insert_textbox(fitz.Rect(*rect), " ".join(words), fontsize=fontsize, fontname="helv")
        if rc >= 0:
            return
        words = words[: max(1, int(len(words) * 0.85)) - (1 if len(words) > 1 else 0)]
    raise AssertionError("body text did not fit")


def _caption(page, x, y, text, fontsize=10.0):
    page.insert_text((x, y), text, fontsize=fontsize, fontname="helv")
    return [x, y - fontsize * 0.9, x + fitz.get_text_length(text, "helv", fontsize), y + fontsize * 0.25]


def _header(page, text="2.1 Axion search experiments"):
    page.insert_text((89, 80), text, fontsize=10, fontname="helv")
    page.draw_line((89, 85), (505, 85), width=0.4)


def _box_figure(page, rect, label=True):
    page.draw_rect(fitz.Rect(*rect), color=(1, 0, 0), width=1.3)
    page.draw_line((rect[0] + 10, rect[1] + 10), (rect[2] - 10, rect[3] - 10), color=(0, 0, 1), width=2)
    if label:
        page.insert_text((rect[0] + 15, rect[1] + 25), "Final DANCE", fontsize=12, fontname="helv")


def _stats(doc):
    return fr.collect_document_stats(doc)


def _locate(doc, page_index, cap_bbox):
    page = doc[page_index]
    layout = fr.analyze_page(page, _stats(doc), caption_bboxes=[cap_bbox])
    return fr.locate_figure_region(page, layout, cap_bbox), layout


class TestLooksLikeFigureCaption:
    @pytest.mark.parametrize("text", [
        "Figure 1.1: Candidates for dark matter.",
        "Fig. 3 Results of the measurement.",
        "FIG. 2. The schematic of the setup.",
        "Figure E.1: Photo of the entire experimental setup.",
        "Figure 2 (a) Transfer function",
        "図 2 実験装置の概略図",
        "Figure 5",
    ])
    def test_captions(self, text):
        assert fr.looks_like_figure_caption(text)

    @pytest.mark.parametrize("text", [
        "Figure 1.2 shows an overview of the entire DANCE project.",
        "Figure 3.4 illustrates the shot noise-limited sensitivity.",
        "Fig. 2 and 3 show the spectra.",
    ])
    def test_prose_references_are_not_captions(self, text):
        assert not fr.looks_like_figure_caption(text)


class TestLocateFigureRegion:
    def test_wide_figure_with_narrow_caption_is_not_cut_to_caption_width(self):
        """Figure 1.2 の再現: 図の横幅は caption の横幅ではなく図の中身で決まる。"""
        doc = fitz.open()
        page = doc.new_page(width=_W, height=_H)
        _body(page, (89, 100, 505, 330))
        _box_figure(page, (72, 390, 270, 610))
        _box_figure(page, (326, 390, 523, 610))
        cap = _caption(page, 174, 636, "Figure 1.2: Overview of the entire project.")
        _body(page, (89, 660, 505, 740))

        region, _ = _locate(doc, 0, cap)

        assert region is not None
        x0, y0, x1, y1 = region.bbox
        assert x0 <= 72 and x1 >= 523, region.bbox  # caption (174..~380) より広い
        assert y0 >= 330, "本文を巻き込んでいる"
        assert y1 <= cap[1]
        assert region.truncated_edges == []

    def test_region_does_not_depend_on_reading_order(self):
        """Figure 1.1 の再現: 図の上端は幾何で直上の本文の下端（order は使わない）。"""
        doc = fitz.open()
        page = doc.new_page(width=_W, height=_H)
        _body(page, (89, 100, 505, 480))
        page.draw_rect(fitz.Rect(148, 530, 435, 560), color=(0, 0, 0), fill=(0.3, 0.7, 1.0))
        page.insert_text((237, 515), "Dark Matter Mass [GeV]", fontsize=12, fontname="helv")
        cap = _caption(page, 202, 592, "Figure 1.1: Candidates for dark matter.")
        _body(page, (89, 640, 505, 740))

        region, _ = _locate(doc, 0, cap)

        assert region is not None
        assert region.bbox[1] >= 480  # 本文の下
        assert region.bbox[1] <= 505  # 図のタイトルは含む
        assert region.bbox[3] <= cap[1]

    def test_page_top_figure_excludes_running_header(self):
        doc = fitz.open()
        for _ in range(3):
            page = doc.new_page(width=_W, height=_H)
            _header(page)
            _body(page, (89, 400, 505, 700))
        page = doc[1]
        page.draw_line((110, 140), (270, 140), width=2)
        page.draw_line((190, 140), (190, 220), width=2)
        page.insert_text((130, 125), "a  g", fontsize=16, fontname="helv")
        cap = _caption(page, 89, 250, "Figure 2.1: Feynman diagrams of the effect.")

        region, layout = _locate(doc, 1, cap)

        assert layout.header_bottom >= 85
        assert region is not None
        assert region.bbox[1] > 90, "柱（ヘッダと罫線）を巻き込んでいる"

    def test_two_column_page_limits_seed_to_caption_column(self):
        doc = fitz.open()
        page = doc.new_page(width=_W, height=_H)
        _body(page, (40, 60, 285, 300), text=_LOREM * 2)
        _body(page, (310, 60, 555, 780), text=_LOREM * 7)
        page.draw_rect(fitz.Rect(60, 330, 260, 450), color=(0, 0, 0), fill=(0.9, 0.5, 0.2))
        cap = _caption(page, 40, 470, "Fig. 1. Left column figure.")
        _body(page, (40, 490, 285, 780), text=_LOREM * 2)

        region, layout = _locate(doc, 0, cap)

        assert len(layout.columns) == 2
        assert region is not None
        assert region.bbox[2] < 300, "右段の本文まで広がっている"
        assert region.bbox[0] <= 60 and region.bbox[2] >= 260

    def test_paragraph_like_text_inside_figure_is_demoted_when_figure_continues(self):
        """Figure 7.4 の再現: 図中の説明文を本文と誤認しても、線が両側に続けば図に戻す。"""
        doc = fitz.open()
        page = doc.new_page(width=_W, height=_H)
        _body(page, (89, 60, 505, 100))
        page.draw_line((290, 115), (290, 360), width=1.2)  # 縦軸（説明文を貫く）
        page.draw_line((120, 300), (460, 300), width=1.2)  # 横軸
        page.insert_text((230, 125), "Phase Quadrature", fontsize=11, fontname="helv")
        _body(page, (213, 189, 442, 240), text=(
            "Sidebands from LO frequency noise p-pol. from misaligned input GLP here"
        ), fontsize=10)
        cap = _caption(page, 89, 380, "Figure 7.4: Phasor diagram illustrating the mechanism.")
        _body(page, (89, 400, 505, 740))

        region, _ = _locate(doc, 0, cap)

        assert region is not None
        assert region.bbox[1] <= 125, "説明文より上の図が切れている"
        assert region.demoted_obstacles >= 1
        assert region.truncated_edges == []

    def test_returns_none_when_nothing_is_drawn(self):
        doc = fitz.open()
        page = doc.new_page(width=_W, height=_H)
        _body(page, (89, 100, 505, 400))
        cap = _caption(page, 89, 430, "Figure 9: Missing figure.")
        _body(page, (89, 450, 505, 740))

        region, _ = _locate(doc, 0, cap)

        assert region is None

    def test_caption_above_figure_uses_band_below(self):
        doc = fitz.open()
        page = doc.new_page(width=_W, height=_H)
        _body(page, (89, 100, 505, 300))
        cap = _caption(page, 89, 330, "Figure 3: Caption placed above the figure.")
        page.draw_rect(fitz.Rect(150, 350, 450, 500), color=(0, 0, 0), fill=(0.2, 0.8, 0.2))
        _body(page, (89, 540, 505, 740))

        region, _ = _locate(doc, 0, cap)

        assert region is not None
        assert region.side == "below"
        assert region.bbox[1] >= cap[3] - 1
        assert region.bbox[3] <= 540

    def test_flags_truncation_when_figure_runs_into_body_text(self):
        """障害物に阻まれて連続が切れた辺は truncated_edges に正直に残す。"""
        doc = fitz.open()
        page = doc.new_page(width=_W, height=_H)
        _body(page, (89, 100, 505, 300), text=_LOREM * 4)
        page.draw_line((300, 150), (300, 420), width=2)  # 本文段落（長い）を貫く線
        page.draw_rect(fitz.Rect(150, 330, 450, 420), color=(0, 0, 0), width=1)
        cap = _caption(page, 89, 450, "Figure 4: A figure overlapping the text.")
        _body(page, (89, 470, 505, 740))

        region, _ = _locate(doc, 0, cap)

        assert region is not None
        assert "top" in region.truncated_edges
        assert region.confidence < 0.9


class TestImagePlacements:
    def _page_with_images(self):
        doc = fitz.open()
        page = doc.new_page(width=_W, height=_H)
        sprite = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 120), False)
        sprite.clear_with(0)
        page.insert_image(fitz.Rect(180, 130, 195, 240), pixmap=sprite)
        page.insert_image(fitz.Rect(400, 130, 415, 240), pixmap=sprite)
        return doc, page

    def test_all_placements_of_the_same_image_are_listed(self):
        _, page = self._page_with_images()
        placements = fr.image_placements(page)
        assert len(placements) == 2
        assert all(p.width == 20 and p.height == 120 for p in placements)

    def test_small_sprite_does_not_cover_the_figure_region(self):
        """Figure 2.1 の再現: ベクター図の部品として置かれた小さな画像は図の代わりにしない。"""
        doc, page = self._page_with_images()
        layout = fr.analyze_page(page, _stats(doc))
        assert fr.single_image_covering(layout, [100, 110, 490, 250]) is None

    def test_full_figure_image_covers_the_region(self):
        doc = fitz.open()
        page = doc.new_page(width=_W, height=_H)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 200), False)
        pix.clear_with(128)
        page.insert_image(fitz.Rect(100, 100, 400, 300), pixmap=pix)
        layout = fr.analyze_page(page, _stats(doc))
        covering = fr.single_image_covering(layout, [98, 98, 402, 302])
        assert covering is not None
        assert covering.width == 300

    def test_visible_content_bbox_ignores_blank_area(self):
        doc = fitz.open()
        page = doc.new_page(width=_W, height=_H)
        page.draw_rect(fitz.Rect(120, 120, 180, 160), color=(0, 0, 0), fill=(0, 0, 0))
        visible = fr.visible_content_bbox(page, [100, 100, 400, 400])
        assert visible is not None
        assert visible[0] >= 115 and visible[2] <= 186
        assert fr.visible_content_bbox(page, [300, 300, 400, 400]) is None


class TestAssessInformation:
    def _pix(self, fill):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 60), False)
        pix.clear_with(fill)
        return pix

    def test_blank_image_is_low_information(self):
        result = fr.assess_information(self._pix(255))
        assert result.low_information and result.reason == "blank"

    def test_solid_black_band_is_low_information(self):
        """SMask を落として黒く塗りつぶされた帯（Figure 2.1 の旧出力）は図として出さない。"""
        result = fr.assess_information(self._pix(0))
        assert result.low_information

    def test_line_drawing_is_informative_even_with_mostly_white_background(self):
        doc = fitz.open()
        page = doc.new_page(width=400, height=300)
        page.draw_line((20, 150), (380, 150), width=1)
        page.draw_line((200, 20), (200, 280), width=1)
        page.insert_text((210, 40), "Phase Quadrature", fontsize=11, fontname="helv")
        pix = page.get_pixmap()
        result = fr.assess_information(pix)
        assert not result.low_information, result


class TestDocumentStats:
    def test_running_header_is_detected_across_pages(self):
        doc = fitz.open()
        for i in range(3):
            page = doc.new_page(width=_W, height=_H)
            _header(page, text=f"{i + 1}.3 Axion and Axion-like particles")
            _body(page, (89, 120, 505, 400))
        stats = _stats(doc)
        assert stats.running_keys
        assert abs(stats.body_size - 10.0) < 0.6
        assert stats.left_margin is not None and abs(stats.left_margin - 89) <= 3


class TestCaptionAnchoring:
    """構造化の caption を PDF の文字層の caption に結び付け直す（§18、2026-09-25 実測）。"""

    def _doc(self):
        doc = fitz.open()
        for i in range(3):
            page = doc.new_page(width=_W, height=_H)
            _body(page, (89, 100, 505, 300))
            page.draw_rect(fitz.Rect(150, 330, 450, 450), color=(0, 0, 0), fill=(0.2, 0.5, 0.9))
            _caption(page, 89, 470, f"Figure {i + 1}.{i + 2}: Caption of figure on page {i + 1}.")
            _body(page, (89, 490, 505, 740))
        return doc

    def test_label_variants(self):
        assert fr.caption_label("Figure 5.22: Photo") == "5_22"
        assert fr.caption_label("Figure 5 . 4 ) 4 shows") == "5_4"
        assert fr.caption_label("Figure1.Theflowchart") == "1"
        assert fr.caption_label("Figure5.19showsthe") == "5_19"
        assert fr.caption_label("Fig. 2a - detail") == "2a"
        assert fr.caption_label("FigureA1.Cross") == "a1"
        assert fr.caption_label("[P T (f )] = 4σ") is None

    def test_caption_declared_on_wrong_page_is_moved_to_its_text(self):
        doc = self._doc()
        stats = fr.collect_document_stats(doc)
        anchor = fr.anchor_caption(
            stats, text="Figure 2.3: Caption of figure on page 2.", page=3, bbox=[89, 460, 400, 472],
        )
        assert anchor is not None and anchor.page == 2

    def test_caption_without_position_is_found_by_label(self):
        doc = self._doc()
        stats = fr.collect_document_stats(doc)
        anchor = fr.anchor_caption(stats, text="Figure 3 . 4 ) broken text", page=1, bbox=None)
        assert anchor is not None and anchor.page == 3

    def test_body_paragraph_declared_as_caption_is_not_anchored(self):
        doc = self._doc()
        stats = fr.collect_document_stats(doc)
        anchor = fr.anchor_caption(
            stats, text="The interaction between the field and the photons", page=1,
            bbox=[89, 100, 505, 300],
        )
        assert anchor is None


class TestHeaderAndBodyDetection:
    def test_axis_tick_numbers_near_the_top_are_not_page_numbers(self):
        """軸の目盛り（"7" など）をページ番号と誤認して図の上部を柱として落とさない。"""
        doc = fitz.open()
        for i in range(4):
            page = doc.new_page(width=_W, height=_H)
            page.insert_text((300, 30), str(i + 1), fontsize=10, fontname="helv")  # ページ番号
            page.draw_rect(fitz.Rect(150, 60, 450, 260), color=(0, 0, 0), width=1)
            for k, y in enumerate((70, 110, 150)):
                page.insert_text((135, y), str(7 - k), fontsize=6, fontname="helv")  # 目盛り
            _caption(page, 89, 285, f"Figure {i + 1}: Profile.")
            _body(page, (89, 310, 505, 700))
        stats = fr.collect_document_stats(doc)
        layout = fr.analyze_page(doc[1], stats)
        assert layout.header_bottom < 45, layout.header_bottom

    def test_plot_title_repeated_on_two_pages_is_not_a_running_header(self):
        doc = fitz.open()
        for i in range(8):
            page = doc.new_page(width=_W, height=_H)
            _body(page, (89, 300, 505, 700))
            if i in (2, 5):
                page.insert_text((200, 60), "FLAMINGO: halo-matter profiles", fontsize=8, fontname="helv")
        stats = fr.collect_document_stats(doc)
        assert not any("flamingo" in key for key in stats.running_keys)

    def test_caption_continuation_block_is_part_of_the_caption(self):
        doc = fitz.open()
        page = doc.new_page(width=_W, height=_H)
        page.draw_rect(fitz.Rect(100, 80, 300, 200), color=(0, 0, 0), fill=(0.3, 0.3, 0.8))
        page.insert_text((89, 220), "Figure 5. Fractional reconstruction error induced by the", fontsize=9)
        page.insert_text((89, 236), "slice. The red, blue, and green curves mark the boundaries", fontsize=9)
        page.draw_rect(fitz.Rect(100, 260, 300, 380), color=(0, 0, 0), fill=(0.8, 0.3, 0.3))
        cap6 = _caption(page, 89, 400, "Figure 6. Relative error.", fontsize=9)
        layout = fr.analyze_page(page, fr.collect_document_stats(doc), caption_bboxes=[cap6])
        region = fr.locate_figure_region(page, layout, cap6)
        assert region is not None
        assert region.bbox[1] > 238, "前の図の caption の続きを取り込んでいる"

    def test_short_body_line_in_body_font_at_column_margin_is_an_obstacle(self):
        doc = fitz.open()
        page = doc.new_page(width=_W, height=_H)
        _body(page, (89, 60, 505, 200))
        page.insert_text((89, 222), "where all legs satisfy k < 0.25.", fontsize=10, fontname="helv")
        page.draw_rect(fitz.Rect(150, 240, 450, 380), color=(0, 0, 0), fill=(0.3, 0.6, 0.3))
        cap = _caption(page, 89, 400, "Figure 6: Comparison.")
        _body(page, (89, 420, 505, 700))
        region, _ = _locate(doc, 0, cap)
        assert region is not None
        assert region.bbox[1] > 224, "図の直前の短い本文行を取り込んでいる"
