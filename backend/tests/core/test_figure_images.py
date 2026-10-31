"""figure_image_extraction (`core/document_pipeline/figure_images.py`) の純関数部分の単体テスト。

設計: docs/features/image_pipeline_knowledge_library_design.md §4。DB / MinIO への
実接続は行わない（DB セッションと storage は差し替える。``load_document_figures`` はテスト対象外）。

- ``_normalize_figure_key``: caption text からの figure_key 正規化
- ``_find_best_image_match``: caption bbox と embedded image bbox の近接対応付け
- ``_prev_block_bottom``: reading order 上の直前ブロック下端の探索
- ``_estimate_region_bbox``: 領域レンダリングの bbox 推定（caption に bbox が無い経路の縮退先）
- ``extract_document_figures``: 合成 PDF で DB / MinIO なしに通す（§17）
  （PyMuPDF の Page オブジェクトが要るため ``pytest.importorskip("fitz")`` でガードする）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]
_SRC = _BACKEND.parent / "src"
for _p in (str(_BACKEND), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.document_pipeline import figure_images as fi  # noqa: E402


class TestSaveFigureOrphanAdoption:
    def test_captioned_reanalysis_reuses_matching_orphan_row(self):
        statements: list[tuple[str, dict]] = []
        figure_id = "11111111-1111-1111-1111-111111111111"

        class _Result:
            def __init__(self, row=None):
                self._row = row

            def fetchone(self):
                return self._row

        class _Session:
            def execute(self, statement, params):
                sql = str(statement)
                statements.append((sql, dict(params)))
                return _Result((figure_id,)) if "INSERT INTO document_figures" in sql else _Result()

            def commit(self):
                pass

            def rollback(self):
                raise AssertionError("rollback should not run")

        class _Storage:
            def upload_bytes(self, bucket, key, data, content_type):
                assert bucket == "figure-images"
                assert key.endswith(f"/{figure_id}.png")
                assert data == b"png"
                assert content_type == "image/png"

        bbox = [183.7, 135.7, 410.9, 310.8]
        saved_id, ok = fi._save_figure(
            _Session(),
            document_id="doc-1",
            run_id="22222222-2222-2222-2222-222222222222",
            figure_key="fig_2_7",
            figure_label="Figure 2.7",
            page=39,
            bbox=bbox,
            caption_block_id="blk_pdf_fig_39_454",
            caption_text="Figure 2.7: Schematic.",
            extraction_method="embedded",
            region_confidence=None,
            inner_labels=[],
            image_bytes=b"png",
            storage=_Storage(),
        )

        assert saved_id == figure_id
        assert ok is True
        adoption_sql, adoption_params = statements[0]
        assert "UPDATE document_figures AS target" in adoption_sql
        assert "orphan.caption_block_id IS NULL" in adoption_sql
        assert "orphan.bbox = CAST(:bbox AS jsonb)" in adoption_sql
        assert "NOT EXISTS" in adoption_sql
        assert adoption_params["figure_key"] == "fig_2_7"
        assert adoption_params["page"] == 39


class TestNormalizeFigureKey:
    def test_figure_with_number_label(self):
        key, label = fi._normalize_figure_key("Figure 3: A schematic of the apparatus.", page=2, index=0)
        assert key == "fig_3"
        assert label == "Figure 3"

    def test_fig_abbreviation_with_letter_suffix(self):
        key, label = fi._normalize_figure_key("Fig. 2a - detail view", page=1, index=0)
        assert key == "fig_2a"
        assert label == "Figure 2a"

    def test_no_caption_falls_back_to_page_index(self):
        key, label = fi._normalize_figure_key("", page=5, index=2)
        assert key == "p5_i2"
        assert label is None

    def test_unrecognized_caption_format_falls_back(self):
        key, label = fi._normalize_figure_key("A diagram without a figure label", page=1, index=0)
        assert key == "p1_i0"
        assert label is None

    def test_missing_page_defaults_to_zero_in_fallback(self):
        key, _ = fi._normalize_figure_key("", page=None, index=1)
        assert key == "p0_i1"

    def test_dotted_numeric_label_is_sanitized(self):
        key, label = fi._normalize_figure_key("Figure 3.2: sub-part", page=1, index=0)
        assert key == "fig_3_2"
        assert label == "Figure 3.2"


class TestFindBestImageMatch:
    def test_prefers_image_directly_above_caption(self):
        # caption top edge (y0) = 200; candidate 0 sits far above, candidate 1 sits
        # immediately above (small gap) and should win.
        candidates = [
            {"bbox": [0, 0, 100, 50]},
            {"bbox": [0, 150, 100, 195]},
        ]
        idx = fi._find_best_image_match(candidates, used_indices=set(), cap_bbox=[0, 200, 100, 220])
        assert idx == 1

    def test_returns_none_when_no_bbox_available(self):
        candidates = [{"bbox": None}]
        assert fi._find_best_image_match(candidates, set(), [0, 200, 100, 220]) is None

    def test_returns_none_when_caption_bbox_missing(self):
        candidates = [{"bbox": [0, 150, 100, 195]}]
        assert fi._find_best_image_match(candidates, set(), None) is None

    def test_skips_already_used_indices(self):
        candidates = [{"bbox": [0, 150, 100, 195]}]
        idx = fi._find_best_image_match(candidates, used_indices={0}, cap_bbox=[0, 200, 100, 220])
        assert idx is None

    def test_returns_none_when_beyond_max_match_distance(self):
        candidates = [{"bbox": [0, 0, 100, 10]}]  # gap to caption (y0=1000) far exceeds threshold
        idx = fi._find_best_image_match(candidates, set(), [0, 1000, 100, 1020])
        assert idx is None


class TestPrevBlockBottom:
    def test_returns_bottom_of_closest_prior_block(self):
        blocks = [
            {"order": 1, "bbox": [0, 0, 100, 50]},
            {"order": 2, "bbox": [0, 60, 100, 120]},
            {"order": 5, "bbox": [0, 500, 100, 600]},  # after caption, ignored
        ]
        assert fi._prev_block_bottom(blocks, cap_order=3) == 120

    def test_returns_none_when_no_prior_blocks(self):
        blocks = [{"order": 5, "bbox": [0, 500, 100, 600]}]
        assert fi._prev_block_bottom(blocks, cap_order=1) is None

    def test_ignores_blocks_without_bbox(self):
        blocks = [{"order": 1, "bbox": None}, {"order": 2, "bbox": [0, 10, 100, 40]}]
        assert fi._prev_block_bottom(blocks, cap_order=3) == 40


class TestEstimateRegionBbox:
    """領域レンダリングの bbox 推定（PyMuPDF Page が要るため fitz を importorskip する）。"""

    @pytest.fixture(autouse=True)
    def _require_fitz(self):
        pytest.importorskip("fitz")

    def _page(self):
        import fitz

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        return doc, page

    def test_bbox_none_when_caption_has_no_bbox(self):
        _, page = self._page()
        bbox, confidence = fi._estimate_region_bbox(page, {"bbox": None}, [])
        assert bbox is None
        assert confidence == 0.0

    def test_uses_previous_block_bottom_when_available(self):
        _, page = self._page()
        cap = {"bbox": [50, 300, 500, 320], "order": 3}
        blocks_same_page = [{"order": 1, "bbox": [50, 100, 500, 150]}]
        bbox, confidence = fi._estimate_region_bbox(page, cap, blocks_same_page)
        assert bbox is not None
        # y0 は前ブロック下端(150) + 2.0 のマージン
        assert bbox[1] == pytest.approx(152.0)
        assert confidence == 0.75

    def test_falls_back_to_page_top_margin_without_prior_block(self):
        _, page = self._page()
        cap = {"bbox": [50, 300, 500, 320], "order": 1}
        bbox, confidence = fi._estimate_region_bbox(page, cap, [])
        assert bbox is not None
        assert bbox[1] == pytest.approx(page.rect.y0 + fi._PAGE_TOP_MARGIN)
        assert confidence == 0.4

    def test_confidence_penalized_for_oversized_region(self):
        _, page = self._page()
        # caption near the bottom of a tall page with no prior block → falls back to
        # page-top margin, producing a region taller than 60% of the page height.
        cap = {"bbox": [50, 700, 500, 720], "order": 1}
        bbox, confidence = fi._estimate_region_bbox(page, cap, [])
        assert bbox is not None
        region_height_ratio = (bbox[3] - bbox[1]) / page.rect.height
        assert region_height_ratio > 0.6
        assert confidence < 0.4

    def test_returns_none_when_region_collapses_to_empty(self):
        _, page = self._page()
        # caption sits at/above the page top margin with no prior block → y0 >= y1.
        cap = {"bbox": [50, 5, 500, 10], "order": 1}
        bbox, confidence = fi._estimate_region_bbox(page, cap, [])
        assert bbox is None
        assert confidence == 0.0


class TestExtractDocumentFiguresEndToEnd:
    """合成 PDF で ``extract_document_figures`` を DB / MinIO なしで通す（§17）。"""

    @pytest.fixture(autouse=True)
    def _require_fitz(self):
        pytest.importorskip("fitz")

    def _build_pdf(self):
        import fitz

        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(
            fitz.Rect(89, 60, 505, 100),
            "Another effect of the interaction is polarization rotation caused by the field. " * 2,
            fontsize=10, fontname="helv",
        )
        # ベクター図 + 部品として置かれた小さな sprite（透過マスク付き）。
        page.draw_line((110, 140), (280, 140), width=2)
        page.draw_line((320, 140), (480, 140), width=2)
        page.insert_text((130, 125), "a   g", fontsize=16, fontname="helv")
        sprite = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 120), True)
        sprite.clear_with(0)
        page.insert_image(fitz.Rect(190, 140, 200, 220), pixmap=sprite)
        page.insert_image(fitz.Rect(400, 140, 410, 220), pixmap=sprite)
        page.insert_text((89, 250), "Figure 2.1: Feynman diagrams of the effect.", fontsize=10, fontname="helv")
        page.insert_textbox(
            fitz.Rect(89, 270, 505, 330),
            "Figure 2.1 shows the diagrams of the effect and of the inverse effect in this setup. " * 2,
            fontsize=10, fontname="helv",
        )
        # caption の無い写真（情報あり）と、caption の無い空白画像（情報なし）。
        photo = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 150), False)
        photo.clear_with(255)
        for x in range(0, 200, 10):
            for y in range(0, 150):
                photo.set_pixel(x, y, (20, 60, 200))
        page.insert_image(fitz.Rect(150, 400, 450, 625), pixmap=photo)
        blank = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100), False)
        blank.clear_with(255)
        page.insert_image(fitz.Rect(150, 660, 300, 760), pixmap=blank)
        # 2 頁目: 本文に挟まれ、図の中身が何も描かれていない caption。
        page2 = doc.new_page(width=595, height=842)
        filler = "The polarization rotation is measured with the balanced detection scheme. " * 12
        page2.insert_textbox(fitz.Rect(89, 100, 505, 290), filler, fontsize=10, fontname="helv")
        page2.insert_text((89, 303), "Figure 9: Nothing is drawn here.", fontsize=10, fontname="helv")
        page2.insert_textbox(fitz.Rect(89, 312, 505, 700), filler * 2, fontsize=10, fontname="helv")
        data = doc.tobytes()
        caption_bbox = [89.0, 241.0, 300.0, 253.0]
        structure = {"blocks": [
            {"block_id": "b1", "page": 1, "order": 1, "block_type": "figure_caption",
             "text": "Figure 2.1: Feynman diagrams of the effect.", "bbox": caption_bbox},
            {"block_id": "b2", "page": 1, "order": 2, "block_type": "figure_caption",
             "text": "Figure 2.1 shows the diagrams of the effect", "bbox": [89.0, 270.0, 505.0, 330.0]},
        ]}
        return data, structure

    def _run(self, monkeypatch, extra_blocks=None):
        rows: dict[str, dict] = {}
        uploads: dict[str, bytes] = {}
        updates: list[tuple[str, dict]] = []

        class _Result:
            def __init__(self, row=None):
                self._row = row

            def fetchone(self):
                return self._row

        class _Session:
            def execute(self, statement, params):
                sql = str(statement)
                if "INSERT INTO document_figures" in sql:
                    rows[params["figure_key"]] = dict(params)
                    return _Result((params["id"],))
                if "SET status = 'superseded'" in sql:
                    updates.append((sql, dict(params)))
                return _Result()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        class _Storage:
            def upload_bytes(self, bucket, key, data, content_type):
                uploads[key] = data

        monkeypatch.setattr(fi, "_pg_session", lambda: _Session())
        data, structure = self._build_pdf()
        structure["blocks"].extend(extra_blocks or [])
        summary = fi.extract_document_figures(
            pdf_bytes=data, document_id="doc-1", run_id=None, structure=structure, storage=_Storage(),
        )
        self.updates = updates
        return summary, rows, uploads

    def test_vector_figure_with_sprite_is_region_rendered_not_the_sprite(self, monkeypatch):
        summary, rows, _ = self._run(monkeypatch)
        fig = rows["fig_2_1"]
        assert fig["extraction_method"] == "region_render"
        bbox = json.loads(fig["bbox"])
        # 図全体（左右の線と上のラベル）を覆い、sprite の矩形だけではない。
        assert bbox[0] <= 110 and bbox[2] >= 480 and bbox[1] <= 125
        assert bbox[1] > 100, "本文を巻き込んでいる"
        # sprite は図の部品なので caption の無い図として別に出さない。
        assert not any(key.startswith("p1_i") and json.loads(r["bbox"])[3] <= 230 for key, r in rows.items())
        assert summary["failed"] == 0

    def test_prose_reference_is_not_extracted_as_a_second_figure(self, monkeypatch):
        summary, rows, _ = self._run(monkeypatch)
        assert "fig_2_1_1" not in rows
        assert summary["skipped"]["non_caption"] == 1

    def test_uncaptioned_blank_image_is_skipped_and_photo_is_kept(self, monkeypatch):
        summary, rows, uploads = self._run(monkeypatch)
        uncaptioned = {k: r for k, r in rows.items() if k.startswith("p1_i")}
        assert len(uncaptioned) == 1
        (row,) = uncaptioned.values()
        assert json.loads(row["bbox"])[1] >= 395
        assert summary["skipped"]["low_information"] >= 1
        assert len(uploads) == summary["embedded"] + summary["region_render"]

    def test_region_render_uses_higher_resolution_than_72dpi(self, monkeypatch):
        import fitz

        _, rows, uploads = self._run(monkeypatch)
        fig = rows["fig_2_1"]
        bbox = json.loads(fig["bbox"])
        data = next(v for k, v in uploads.items() if k.endswith(f"{fig['id']}.png"))
        pix = fitz.Pixmap(data)
        assert pix.width >= (bbox[2] - bbox[0]) * 2

    def test_caption_without_position_creates_no_row(self, monkeypatch):
        """GROBID の figure 要素で PDF と突合できなかった caption（bbox なし・page=1 既定）は
        図の領域を決められないので、絵の無い行を作らない（2026-09-25 実測: p1_i0 等）。"""
        summary, rows, _ = self._run(monkeypatch, extra_blocks=[
            {"block_id": "g1", "page": 1, "order": 9, "block_type": "figure_caption",
             "text": "[P T (f )] = 4σ 4 = P (f ) 2 (B.6)", "bbox": None},
        ])
        assert not any(r["caption_block_id"] == "g1" for r in rows.values())
        assert summary["skipped"]["no_caption_position"] == 1
        assert summary["not_saved"][0]["reason"] == "no_caption_position"
        assert summary["failed"] == 0

    def test_caption_with_nothing_drawn_creates_no_row(self, monkeypatch):
        """位置はあっても図の中身が描かれていない caption は行を作らない（絵の無い図を一覧に出さない）。"""
        summary, rows, _ = self._run(monkeypatch, extra_blocks=[
            {"block_id": "b9", "page": 2, "order": 9, "block_type": "figure_caption",
             "text": "Figure 9: Nothing is drawn here.", "bbox": [89.0, 294.0, 260.0, 306.0]},
        ])
        assert "fig_9" not in rows
        assert summary["skipped"]["captioned_no_image"] == 1
        assert all(r["caption_block_id"] != "b9" for r in rows.values())

    def test_rows_not_created_by_this_run_are_superseded(self, monkeypatch):
        summary, rows, _ = self._run(monkeypatch)
        assert len(self.updates) == 1
        sql, params = self.updates[0]
        assert params["document_id"] == "doc-1"
        assert set(params["touched"]) == set(summary["figure_ids"])
        assert "DELETE" not in sql

    def test_caption_on_wrong_page_with_mangled_text_is_anchored_to_pdf_text(self, monkeypatch):
        """GROBID 経路の再現: caption が別の頁に置かれ、ラベルの空白が崩れていても、
        PDF の文字層の「Figure 2.1:」に結び付けて正しい頁・key で図を作る。"""
        import fitz

        data, structure = self._build_pdf()
        structure["blocks"] = [
            {"block_id": "g1", "page": 2, "order": 1, "block_type": "figure_caption",
             "text": "Figure 2 . 1 : Feynman diagrams of the effect.", "bbox": [89.0, 294.0, 260.0, 306.0]},
            {"block_id": "g2", "page": 1, "order": 2, "block_type": "figure_caption",
             "text": "Another effect of the interaction is polarization rotation", "bbox": [89.0, 60.0, 505.0, 100.0]},
        ]
        monkeypatch.setattr(self, "_build_pdf", lambda: (data, structure))
        summary, rows, _ = self._run(monkeypatch)
        assert rows["fig_2_1"]["page"] == 1
        assert rows["fig_2_1"]["caption_block_id"] == "g1"
        assert not any(r["caption_block_id"] == "g2" for r in rows.values())
        assert summary["skipped"]["unanchored_caption"] == 1

    def test_caption_missing_from_structure_is_taken_from_text_layer(self, monkeypatch):
        data, structure = self._build_pdf()
        structure["blocks"] = []
        monkeypatch.setattr(self, "_build_pdf", lambda: (data, structure))
        summary, rows, _ = self._run(monkeypatch)
        assert "fig_2_1" in rows
        assert rows["fig_2_1"]["caption_block_id"] is None
        assert any(q.get("caption_source") == "text_layer" for q in summary["quality"])
        # 本文中の図参照（"Figure 2.1 shows ..."）は文字層からも足さない。
        assert "fig_2_1_1" not in rows

    def test_summary_reports_quality_per_figure(self, monkeypatch):
        summary, _, _ = self._run(monkeypatch)
        keys = {q["figure_key"] for q in summary["quality"]}
        assert "fig_2_1" in keys
        for q in summary["quality"]:
            assert set(q) >= {"method", "truncated_edges", "information"}


class TestListedFigure:
    def test_only_extracted_rows_are_listed(self):
        assert fi.is_listed_figure({"status": "extracted", "minio_key": "k"})
        assert not fi.is_listed_figure({"status": "failed", "minio_key": "k"})
        assert not fi.is_listed_figure({"status": "superseded", "minio_key": "k"})

    def test_list_routes_and_inventory_filter_unlisted_rows(self):
        root = Path(__file__).resolve().parents[2]
        for rel in ("api/routes/figure_presentation.py", "api/routes/admin.py"):
            assert "is_listed_figure(row)" in (root / rel).read_text(encoding="utf-8"), rel
        inventory = (root / "core/deliberation/inventory.py").read_text(encoding="utf-8")
        assert "AND status = 'extracted'" in inventory
        next_steps = (root / "core/admin_assistant/next_steps.py").read_text(encoding="utf-8")
        assert "f.status = 'extracted'" in next_steps

    def test_migration_087_adds_superseded_status(self):
        root = Path(__file__).resolve().parents[2]
        sql = (root / "db/087_document_figures_superseded.sql").read_text(encoding="utf-8")
        assert "'superseded'" in sql and "DELETE" not in sql.upper().replace("DROP CONSTRAINT", "")


class TestFilterFigureCaptions:
    def test_overlapping_minority_style_caption_is_dropped(self):
        captions = [
            {"page": 45, "text": "FIG. 1. The schematic of an experimental setup",
             "bbox": [295, 330, 596, 342]},
            {"page": 45, "text": "Figure 2.11: Schematic of the initial setup.",
             "bbox": [89, 334, 505, 360]},
            {"page": 46, "text": "Figure 2.12: Simplified schematic.", "bbox": [89, 306, 505, 363]},
        ]
        kept, skipped = fi._filter_figure_captions(captions)
        texts = [c["text"] for c in kept]
        assert texts == ["Figure 2.11: Schematic of the initial setup.", "Figure 2.12: Simplified schematic."]
        assert skipped["overlapping_caption"] == 1
