"""figure_image_extraction: PDF から図画像を抽出する非LLM・決定論的ステージ。

設計: docs/features/image_pipeline_knowledge_library_design.md §4。

- 埋め込み画像（PyMuPDF ``page.get_images()`` + xref 抽出）と、
  caption 近傍に埋め込み画像が見つからない場合の領域レンダリング
  （``page.get_pixmap(clip=...)``）の2段構え（§4-2）。
- 保存は MinIO（bucket ``figure-images``）+ PostgreSQL（``document_figures``）。
  agent ディレクトリを作らない（LLM を使わない決定論的工程のため。
  evidence_registry / derivation_chain と同じ扱い、CLAUDE.md 参照）。
- 図単位の失敗は ``status='failed'`` 行を残して継続する（非致命、P4）。
- 図領域内のテキストラベル（TikZ 系ベクター図の "ECDL" 等）を ``_extract_inner_labels``
  で抽出し ``document_figures.inner_labels`` に保存する（装置図理解機能拡張, G2 ギャップ解消）。
- FastAPI を import しない（core/ 共通ルール）。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session as _pg_session
from core.storage import get_storage_client

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - PyMuPDF is a hard runtime dependency
    fitz = None  # type: ignore[assignment]

_FIG_LABEL_RE = re.compile(r"^(?:Figure|Fig\.?)\s*([0-9A-Za-z\.]+)\s*[:.\-]?\s*", re.IGNORECASE)

# 埋め込み画像とcaptionの対応付けを許容する最大距離 (pt)。
# これを超える場合は「対応するembedded imageなし」として region_render にフォールバックする。
_MAX_MATCH_DISTANCE = 500.0
# 領域レンダリングでページ上端にフォールバックする際のマージン (pt)。
_PAGE_TOP_MARGIN = 20.0
# caption/画像の左右に加える余白 (page width に対する割合)。
_COLUMN_PAD_RATIO = 0.03
# 図中ラベル抽出: 同一行内で語を1ラベルにマージする最大水平ギャップ (pt)。
_INNER_LABEL_WORD_GAP = 6.0


# ---------------------------------------------------------------------------
# structure（DocumentStructureResult）正規化 — dict / dataclass 両対応
# ---------------------------------------------------------------------------


def _blocks_from_structure(structure: Any) -> list[dict]:
    """DocumentStructureResult（dataclass か dict）から block の平坦な dict 列を作る。"""
    if structure is None:
        return []
    if isinstance(structure, dict):
        raw_blocks = structure.get("blocks") or []
    else:
        raw_blocks = getattr(structure, "blocks", None) or []

    blocks: list[dict] = []
    for b in raw_blocks:
        if isinstance(b, dict):
            bbox = b.get("bbox")
            blocks.append({
                "block_id": b.get("block_id"),
                "page": b.get("page"),
                "order": b.get("order", 0) or 0,
                "text": b.get("text", "") or "",
                "block_type": b.get("block_type"),
                "bbox": list(bbox) if bbox else None,
                "section_id": b.get("section_id"),
            })
        else:
            bbox = getattr(b, "bbox", None)
            blocks.append({
                "block_id": getattr(b, "block_id", None),
                "page": getattr(b, "page", None),
                "order": getattr(b, "order", 0) or 0,
                "text": getattr(b, "text", "") or "",
                "block_type": getattr(b, "block_type", None),
                "bbox": list(bbox) if bbox else None,
                "section_id": getattr(b, "section_id", None),
            })
    return blocks


def _normalize_figure_key(caption_text: str, page: int | None, index: int) -> tuple[str, str | None]:
    """caption text から 'fig_3' 形式の figure_key を導出する。抽出できなければ 'p{page}_i{n}'。"""
    text = (caption_text or "").strip()
    match = _FIG_LABEL_RE.match(text)
    if match:
        label_raw = match.group(1).strip().rstrip(".")
        key = re.sub(r"[^0-9A-Za-z]+", "_", label_raw).strip("_").lower()
        if key:
            return f"fig_{key}", f"Figure {label_raw}"
    return f"p{page or 0}_i{index}", None


# ---------------------------------------------------------------------------
# 幾何: caption ⇔ embedded image の対応付け / 領域推定
# ---------------------------------------------------------------------------


def _find_best_image_match(
    candidates: list[dict],
    used_indices: set[int],
    cap_bbox: list[float] | None,
) -> int | None:
    """caption bbox に対応する embedded image の index を返す（page + bbox 近接、caption 直上優先）。"""
    if not cap_bbox:
        return None
    best_idx: int | None = None
    best_score: float | None = None
    for i, img in enumerate(candidates):
        if i in used_indices:
            continue
        bbox = img.get("bbox")
        if not bbox:
            continue
        # 画像が caption の直上にある場合を優先する（bbox は top-left 原点、y 増加が下方向）。
        gap_above = cap_bbox[1] - bbox[3]
        if gap_above >= -5.0:
            score = abs(gap_above)
        else:
            # caption より下・重なっている画像はペナルティを付けつつ許容する。
            score = 1000.0 + abs(gap_above)
        if best_score is None or score < best_score:
            best_score = score
            best_idx = i
    if best_idx is not None and best_score is not None and best_score < _MAX_MATCH_DISTANCE:
        return best_idx
    return None


def _prev_block_bottom(blocks_same_page: list[dict], cap_order: int) -> float | None:
    """caption より reading order で手前にある直近ブロックの bbox 下端 (y1) を返す。"""
    candidates = [
        b for b in blocks_same_page
        if (b.get("order") or 0) < cap_order and b.get("bbox")
    ]
    if not candidates:
        return None
    prev = max(candidates, key=lambda b: b.get("order") or 0)
    return prev["bbox"][3]


def _estimate_region_bbox(
    page: Any,
    cap: dict,
    blocks_same_page: list[dict],
) -> tuple[list[float] | None, float]:
    """caption 直上〜前のテキストブロック/ページ上端までの図領域を推定する（§4-2）。

    Returns: (bbox [x0,y0,x1,y1] or None, region_confidence)
    """
    cap_bbox = cap.get("bbox")
    if not cap_bbox:
        return None, 0.0

    page_rect = page.rect
    page_width = page_rect.width
    pad = page_width * _COLUMN_PAD_RATIO
    x0 = max(page_rect.x0, cap_bbox[0] - pad)
    x1 = min(page_rect.x1, cap_bbox[2] + pad)
    y1 = max(page_rect.y0, cap_bbox[1])  # caption の上端 = 図領域の下端

    prev_bottom = _prev_block_bottom(blocks_same_page, cap.get("order") or 0)
    if prev_bottom is not None and prev_bottom < y1:
        y0 = max(page_rect.y0, prev_bottom + 2.0)
        confidence = 0.75
    else:
        y0 = page_rect.y0 + _PAGE_TOP_MARGIN
        confidence = 0.4

    if y0 >= y1 or x0 >= x1:
        return None, 0.0

    region_height_ratio = (y1 - y0) / max(page_rect.height, 1.0)
    if region_height_ratio > 0.6:
        confidence = max(0.05, confidence - 0.15)

    return [x0, y0, x1, y1], round(confidence, 2)


def _render_region(page: Any, bbox: list[float] | None) -> bytes | None:
    if not bbox:
        return None
    try:
        rect = fitz.Rect(*bbox)
        if rect.is_empty or rect.width <= 1 or rect.height <= 1:
            return None
        pix = page.get_pixmap(clip=rect)
        return pix.tobytes("png")
    except Exception:
        logger.warning("figure_image_extraction: region render failed", exc_info=True)
        return None


def _extract_embedded_image_png(doc: Any, xref: int) -> bytes | None:
    """xref の画像を正規化された PNG bytes として取り出す（CMYK 等も RGB へ変換）。"""
    try:
        pix = fitz.Pixmap(doc, xref)
        if pix.colorspace and pix.colorspace.n >= 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        data = pix.tobytes("png")
        pix = None
        return data
    except Exception:
        logger.warning("figure_image_extraction: embedded image decode failed xref=%s", xref, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# 図中ラベル抽出（inner_labels, TikZ 系ベクター図の "ECDL" / "EOM" / "PBS" 等）
# ---------------------------------------------------------------------------


def _extract_inner_labels(
    page: Any,
    figure_bbox: list[float] | None,
    caption_bbox: list[float] | None = None,
) -> list[dict]:
    """図領域内に埋め込まれたテキストラベルを決定論的・非LLM で抽出する。

    (block_no, line_no) でグルーピングしたうえで x0 昇順に整列し、隣接語の水平ギャップが
    ``_INNER_LABEL_WORD_GAP`` 以下なら1ラベルにマージする（"CCD camera" や "f = 75 mm" の
    ような複数トークンのラベル・パラメータ表記を1つにまとめる）。caption_bbox と交差する語は
    キャプション本文の混入として除外する。パラメータ表記も含め意味的なフィルタは行わない
    （情報を落とさない。フィルタは下流の LLM プロンプト側の責務、P4）。
    """
    if not figure_bbox or fitz is None:
        return []
    try:
        words = page.get_text("words", clip=fitz.Rect(*figure_bbox))
        groups: dict[tuple[Any, Any], list[tuple[float, float, float, float, str]]] = {}
        for w in words:
            x0, y0, x1, y1, word_text, block_no, line_no = w[0], w[1], w[2], w[3], w[4], w[5], w[6]
            if not word_text or not word_text.strip():
                continue
            if caption_bbox is not None and not (
                x1 <= caption_bbox[0] or x0 >= caption_bbox[2]
                or y1 <= caption_bbox[1] or y0 >= caption_bbox[3]
            ):
                continue
            groups.setdefault((block_no, line_no), []).append((x0, y0, x1, y1, word_text))

        labels: list[dict] = []
        for tokens in groups.values():
            tokens.sort(key=lambda t: t[0])
            texts: list[str] = []
            bbox: list[float] | None = None
            prev_x1: float | None = None
            for x0, y0, x1, y1, word_text in tokens:
                if bbox is not None and prev_x1 is not None and (x0 - prev_x1) <= _INNER_LABEL_WORD_GAP:
                    texts.append(word_text)
                    bbox = [min(bbox[0], x0), min(bbox[1], y0), max(bbox[2], x1), max(bbox[3], y1)]
                else:
                    if bbox is not None:
                        labels.append({"text": " ".join(texts), "bbox": bbox})
                    texts = [word_text]
                    bbox = [x0, y0, x1, y1]
                prev_x1 = x1
            if bbox is not None:
                labels.append({"text": " ".join(texts), "bbox": bbox})

        labels.sort(key=lambda lbl: (round(lbl["bbox"][1], 1), lbl["bbox"][0]))
        return labels
    except Exception:
        logger.warning("figure_image_extraction: inner label extraction failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# 永続化（MinIO + PostgreSQL）
# ---------------------------------------------------------------------------


def _mark_figure_failed(session: Any, figure_id: str) -> None:
    try:
        session.execute(
            sa_text("UPDATE document_figures SET status = 'failed' WHERE id = CAST(:id AS uuid)"),
            {"id": figure_id},
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("figure_image_extraction: failed to mark figure %s as failed", figure_id, exc_info=True)


def _save_figure(
    session: Any,
    *,
    document_id: str,
    run_id: str | None,
    figure_key: str,
    figure_label: str | None,
    page: int | None,
    bbox: list[float] | None,
    caption_block_id: str | None,
    caption_text: str,
    extraction_method: str,
    region_confidence: float | None,
    inner_labels: list[dict] | None = None,
    image_bytes: bytes | None,
    storage: Any,
) -> tuple[str, bool]:
    """document_figures へ1行 upsert し、画像を MinIO へアップロードする。

    Returns: (figure_id, success). 失敗しても figure_id は極力返す（P4: レコードは残す）。
    """
    candidate_id = str(uuid.uuid4())
    placeholder_key = f"figures/{document_id}/{candidate_id}.png"
    try:
        row = session.execute(
            sa_text(
                """
                INSERT INTO document_figures (
                    id, document_id, run_id, figure_key, figure_label, page, bbox,
                    caption_block_id, caption_text, minio_key, extraction_method,
                    region_confidence, inner_labels, status
                )
                VALUES (
                    CAST(:id AS uuid), :document_id, CAST(:run_id AS uuid), :figure_key,
                    :figure_label, :page, CAST(:bbox AS jsonb), :caption_block_id,
                    :caption_text, :minio_key, :extraction_method, :region_confidence,
                    CAST(:inner_labels AS jsonb), 'extracted'
                )
                ON CONFLICT (document_id, figure_key) DO UPDATE SET
                    run_id = EXCLUDED.run_id,
                    page = EXCLUDED.page,
                    bbox = EXCLUDED.bbox,
                    caption_block_id = EXCLUDED.caption_block_id,
                    caption_text = EXCLUDED.caption_text,
                    extraction_method = EXCLUDED.extraction_method,
                    region_confidence = EXCLUDED.region_confidence,
                    inner_labels = EXCLUDED.inner_labels,
                    suggested_mode = 'unknown',
                    mode_reason = '',
                    analysis_profile = '{}'::jsonb,
                    status = 'extracted'
                RETURNING id::text
                """
            ),
            {
                "id": candidate_id,
                "document_id": document_id,
                "run_id": run_id,
                "figure_key": figure_key,
                "figure_label": figure_label,
                "page": page,
                "bbox": json.dumps(bbox) if bbox is not None else None,
                "caption_block_id": caption_block_id,
                "caption_text": caption_text or "",
                "minio_key": placeholder_key,
                "extraction_method": extraction_method,
                "region_confidence": region_confidence,
                "inner_labels": json.dumps(inner_labels or []),
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        logger.warning(
            "figure_image_extraction: upsert failed document=%s figure_key=%s",
            document_id, figure_key, exc_info=True,
        )
        return "", False

    figure_id = row[0]

    if not image_bytes:
        _mark_figure_failed(session, figure_id)
        return figure_id, False

    final_key = f"figures/{document_id}/{figure_id}.png"
    try:
        storage.upload_bytes("figure-images", final_key, image_bytes, content_type="image/png")
    except Exception:
        logger.warning(
            "figure_image_extraction: MinIO upload failed document=%s figure=%s",
            document_id, figure_id, exc_info=True,
        )
        _mark_figure_failed(session, figure_id)
        return figure_id, False

    try:
        session.execute(
            sa_text(
                "UPDATE document_figures SET minio_key = :key, status = 'extracted' "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"key": final_key, "id": figure_id},
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning(
            "figure_image_extraction: minio_key update failed figure=%s", figure_id, exc_info=True,
        )
        return figure_id, False

    return figure_id, True


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def extract_document_figures(
    *,
    pdf_bytes: bytes,
    document_id: str,
    run_id: str | None,
    structure: Any = None,
    storage: Any = None,
) -> dict:
    """PDF から図画像を抽出し MinIO + document_figures に保存する（非LLM・決定論的）。

    埋め込み画像（xref 抽出）+ caption 近傍に埋め込み画像が無い場合の領域レンダリング
    の2段構え（§4-2）。図単位の失敗は ``status='failed'`` として保存し継続する。
    """
    if fitz is None:  # pragma: no cover - defensive, PyMuPDF is a hard dependency
        logger.warning("figure_image_extraction: PyMuPDF (fitz) is not installed")
        return {"figures": 0, "embedded": 0, "region_render": 0, "failed": 0, "figure_ids": []}

    storage = storage or get_storage_client()
    blocks = _blocks_from_structure(structure)
    figure_captions = sorted(
        (b for b in blocks if b.get("block_type") == "figure_caption" and b.get("page")),
        key=lambda b: (b.get("page") or 0, b.get("order") or 0),
    )
    all_blocks_sorted = sorted(
        (b for b in blocks if b.get("page")),
        key=lambda b: (b.get("page") or 0, b.get("order") or 0),
    )
    blocks_by_page: dict[int, list[dict]] = {}
    for b in all_blocks_sorted:
        blocks_by_page.setdefault(b.get("page") or 0, []).append(b)

    counts = {"figures": 0, "embedded": 0, "region_render": 0, "failed": 0}
    figure_ids: list[str] = []

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        logger.warning(
            "figure_image_extraction: failed to open PDF for document=%s", document_id, exc_info=True,
        )
        return {**counts, "figure_ids": figure_ids}

    session = _pg_session()
    try:
        # ── Phase 1: 各ページの埋め込み画像を収集（xref 重複排除は page 単位） ─
        embedded_by_page: dict[int, list[dict]] = {}
        for page_index in range(len(doc)):
            page = doc[page_index]
            page_num = page_index + 1
            seen_xrefs: set[int] = set()
            per_page_images: list[dict] = []
            try:
                images = page.get_images(full=True)
            except Exception:
                images = []
            for img in images:
                xref = img[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    rects = page.get_image_rects(xref)
                except Exception:
                    rects = []
                bbox = list(rects[0]) if rects else None
                image_bytes = _extract_embedded_image_png(doc, xref)
                per_page_images.append({"xref": xref, "bbox": bbox, "image_bytes": image_bytes})
            embedded_by_page[page_num] = per_page_images

        used_image_indices: dict[int, set[int]] = {p: set() for p in embedded_by_page}
        seen_keys: set[str] = set()

        # ── Phase 2: caption ごとに embedded image を対応付け、無ければ region_render ─
        for idx, cap in enumerate(figure_captions):
            page_num = cap.get("page") or 0
            candidates = embedded_by_page.get(page_num, [])
            used = used_image_indices.setdefault(page_num, set())
            match_idx = _find_best_image_match(candidates, used, cap.get("bbox"))

            figure_key, figure_label = _normalize_figure_key(cap.get("text", ""), page_num, idx)
            if figure_key in seen_keys:
                figure_key = f"{figure_key}_{idx}"
            seen_keys.add(figure_key)
            page_obj = doc[page_num - 1] if 1 <= page_num <= len(doc) else None

            if match_idx is not None:
                used.add(match_idx)
                image = candidates[match_idx]
                fig_id, ok = _save_figure(
                    session,
                    document_id=document_id, run_id=run_id,
                    figure_key=figure_key, figure_label=figure_label,
                    page=page_num, bbox=image.get("bbox"),
                    caption_block_id=cap.get("block_id"), caption_text=cap.get("text", ""),
                    extraction_method="embedded", region_confidence=None,
                    inner_labels=(
                        _extract_inner_labels(page_obj, image.get("bbox"), cap.get("bbox"))
                        if page_obj is not None else []
                    ),
                    image_bytes=image.get("image_bytes"), storage=storage,
                )
                method = "embedded"
            else:
                region_bbox, confidence = (
                    _estimate_region_bbox(page_obj, cap, blocks_by_page.get(page_num, []))
                    if page_obj is not None else (None, 0.0)
                )
                image_bytes = _render_region(page_obj, region_bbox) if page_obj is not None else None
                fig_id, ok = _save_figure(
                    session,
                    document_id=document_id, run_id=run_id,
                    figure_key=figure_key, figure_label=figure_label,
                    page=page_num, bbox=region_bbox,
                    caption_block_id=cap.get("block_id"), caption_text=cap.get("text", ""),
                    extraction_method="region_render", region_confidence=confidence,
                    inner_labels=(
                        _extract_inner_labels(page_obj, region_bbox, cap.get("bbox"))
                        if page_obj is not None else []
                    ),
                    image_bytes=image_bytes, storage=storage,
                )
                method = "region_render"

            counts["figures"] += 1
            if ok:
                counts[method] += 1
                figure_ids.append(fig_id)
            else:
                counts["failed"] += 1
                if fig_id:
                    figure_ids.append(fig_id)

        # ── Phase 3: caption と対応しない残余 embedded image も P4 のため保持する ─
        for page_num, candidates in embedded_by_page.items():
            used = used_image_indices.get(page_num, set())
            page_obj = doc[page_num - 1] if 1 <= page_num <= len(doc) else None
            for i, image in enumerate(candidates):
                if i in used:
                    continue
                figure_key = f"p{page_num}_i{i}"
                if figure_key in seen_keys:
                    continue
                seen_keys.add(figure_key)
                fig_id, ok = _save_figure(
                    session,
                    document_id=document_id, run_id=run_id,
                    figure_key=figure_key, figure_label=None,
                    page=page_num, bbox=image.get("bbox"),
                    caption_block_id=None, caption_text="",
                    extraction_method="embedded", region_confidence=None,
                    inner_labels=(
                        _extract_inner_labels(page_obj, image.get("bbox"))
                        if page_obj is not None else []
                    ),
                    image_bytes=image.get("image_bytes"), storage=storage,
                )
                counts["figures"] += 1
                if ok:
                    counts["embedded"] += 1
                    figure_ids.append(fig_id)
                else:
                    counts["failed"] += 1
                    if fig_id:
                        figure_ids.append(fig_id)
    finally:
        session.close()
        try:
            doc.close()
        except Exception:
            pass

    return {**counts, "figure_ids": figure_ids}


def load_document_figures(document_id: str) -> list[dict]:
    """document_figures 行を dict のリストで返す（図配信 API とステージ2で共用）。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id::text, document_id, run_id::text, figure_key, figure_label,
                       page, bbox, caption_block_id, caption_text, minio_key,
                       extraction_method, region_confidence, status, created_at, inner_labels,
                       suggested_mode, mode_reason, analysis_profile, reviewed_mode,
                       mode_review_status, mode_reviewed_by::text, mode_reviewed_at,
                       reviewed_analysis_mode, reviewed_analysis_profile,
                       analysis_review_status, analysis_reviewed_by::text,
                       analysis_reviewed_at, analysis_review_source_annotation_id::text
                FROM document_figures
                WHERE document_id = :document_id
                ORDER BY page NULLS LAST, figure_key
                """
            ),
            {"document_id": document_id},
        ).mappings().all()
        result = []
        for row in rows:
            item = dict(row)
            bbox = item.get("bbox")
            if isinstance(bbox, str):
                try:
                    item["bbox"] = json.loads(bbox)
                except (ValueError, TypeError):
                    item["bbox"] = None
            inner_labels = item.get("inner_labels")
            if isinstance(inner_labels, str):
                try:
                    item["inner_labels"] = json.loads(inner_labels)
                except (ValueError, TypeError):
                    item["inner_labels"] = []
            elif inner_labels is None:
                item["inner_labels"] = []
            analysis_profile = item.get("analysis_profile")
            if isinstance(analysis_profile, str):
                try:
                    item["analysis_profile"] = json.loads(analysis_profile)
                except (ValueError, TypeError):
                    item["analysis_profile"] = {}
            elif not isinstance(analysis_profile, dict):
                item["analysis_profile"] = {}
            reviewed_profile = item.get("reviewed_analysis_profile")
            if isinstance(reviewed_profile, str):
                try:
                    item["reviewed_analysis_profile"] = json.loads(reviewed_profile)
                except (ValueError, TypeError):
                    item["reviewed_analysis_profile"] = {}
            elif not isinstance(reviewed_profile, dict):
                item["reviewed_analysis_profile"] = {}
            result.append(item)
        return result
    finally:
        session.close()
