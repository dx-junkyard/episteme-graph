"""figure_image_extraction: PDF から図画像を抽出する非LLM・決定論的ステージ。

設計: docs/features/image_pipeline_knowledge_library_design.md §4。

- 図領域は ``figure_regions`` が幾何とインクの連結成分から決める（§17、2026-09-24 改訂）。
  図領域のほぼ全体を1枚の埋め込み画像が覆うときは ``embedded``（配置を元画像の解像度で
  描き直す）、それ以外は領域レンダリング ``region_render``（``page.get_pixmap(clip=...)``）。
  情報量の乏しい画像（空白・一様な塗りつぶし）は図として提示しない。
- 保存は MinIO（bucket ``figure-images``）+ PostgreSQL（``document_figures``）。
  agent ディレクトリを作らない（LLM を使わない決定論的工程のため。
  evidence_registry / derivation_chain と同じ扱い、CLAUDE.md 参照）。
- 絵の無い図は行を作らない（artifact の ``not_saved`` に残す）。保存に失敗した図は
  ``status='failed'``、今回の抽出で作られなかった前回までの行は ``status='superseded'``
  （行は消さない、P4。一覧は ``status='extracted'`` だけを出す）。
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

from . import figure_regions

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - PyMuPDF is a hard runtime dependency
    fitz = None  # type: ignore[assignment]

# 埋め込み画像とcaptionの対応付けを許容する最大距離 (pt)。
# これを超える場合は「対応するembedded imageなし」として region_render にフォールバックする。
_MAX_MATCH_DISTANCE = 500.0
# 領域レンダリングでページ上端にフォールバックする際のマージン (pt)。
_PAGE_TOP_MARGIN = 20.0
# caption/画像の左右に加える余白 (page width に対する割合)。
_COLUMN_PAD_RATIO = 0.03
# 図中ラベル抽出: 同一行内で語を1ラベルにマージする最大水平ギャップ (pt)。
_INNER_LABEL_WORD_GAP = 6.0
# 領域レンダリングの倍率（1.0 = 72dpi）。旧実装は 72dpi 固定で小さな文字が潰れていた。
_REGION_RENDER_ZOOM = 2.5
# 埋め込み画像の配置を描き直すときの倍率の上限（元画像の解像度に合わせ、これで頭打ち）。
_EMBEDDED_RENDER_MAX_ZOOM = 4.0
# 出力画像の長辺の上限 (px)。
_MAX_RENDER_SIDE_PX = 4096
# caption のない埋め込み画像が図の領域にこの割合以上含まれていたら、図の部品として扱う。
_ORPHAN_INSIDE_FIGURE_RATIO = 0.5


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
    """caption text から 'fig_3' 形式の figure_key を導出する。抽出できなければ 'p{page}_i{n}'。

    ラベルの切り出しは ``figure_regions.caption_label_raw``（空白の無い文字層の
    "Figure1.Theflowchart..." でもラベルだけを取る。2026-09-25 以前は英数字とピリオドを
    貪欲に取り、``fig_1_theflowchart...`` のような key になっていた）。
    """
    raw = figure_regions.caption_label_raw((caption_text or "").strip())
    if raw:
        label_raw = raw.rstrip(".")
        key = re.sub(r"[^0-9A-Za-z]+", "_", label_raw).strip("_").lower()
        if key:
            return f"fig_{key}", f"Figure {label_raw}"
    return f"p{page or 0}_i{index}", None


def normalize_figure_join_key(value: str | None) -> str:
    """図ID表記ゆれの突合用正規化（``document_figures.figure_key`` と同じ文字規則）。

    ``figure_table_semantics`` の ``FigureRecord.figure_id`` は caption ラベルを
    そのまま使う ``fig_3.3``（ピリオド保持）形式、一方 ``document_figures.figure_key``
    は ``_normalize_figure_key`` により ``fig_3_3``（非英数字→アンダースコア）形式で、
    章番号付きラベルでは素朴な文字列一致が恒常的に失敗する。両者を突合する側は
    必ず本関数で両辺を正規化してから比較・索引すること（生成側の正本規則は
    ``_normalize_figure_key`` のまま変えない）。"""
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower()


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


def _render_pixmap(page: Any, bbox: list[float] | None, zoom: float = _REGION_RENDER_ZOOM) -> Any:
    """bbox を zoom 倍で描いた Pixmap（長辺は ``_MAX_RENDER_SIDE_PX`` で頭打ち）。

    ``page`` は ``fitz.Page`` でも ``fitz.DisplayList``（ページの描画を使い回す）でもよい。"""
    if not bbox or fitz is None:
        return None
    try:
        rect = fitz.Rect(*bbox)
        if rect.is_empty or rect.width <= 1 or rect.height <= 1:
            return None
        longest = max(rect.width, rect.height)
        zoom = max(1.0, min(float(zoom), _MAX_RENDER_SIDE_PX / max(longest, 1.0)))
        return page.get_pixmap(clip=rect, matrix=fitz.Matrix(zoom, zoom), alpha=False)
    except Exception:
        logger.warning("figure_image_extraction: region render failed", exc_info=True)
        return None


def _embedded_zoom(placement: Any) -> float:
    """埋め込み画像の配置を元画像の解像度で描き直すための倍率。"""
    bbox = placement.bbox
    width_pt = max(bbox[2] - bbox[0], 1.0)
    height_pt = max(bbox[3] - bbox[1], 1.0)
    native = max((placement.width or 0) / width_pt, (placement.height or 0) / height_pt, 1.0)
    return min(native, _EMBEDDED_RENDER_MAX_ZOOM)


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
        if caption_block_id and bbox is not None:
            # 旧runで caption を取得できなかった画像は ``p39_i0`` のような残余keyで
            # 保存されている。caption補完後に ``fig_2_7`` を新規INSERTすると同じ画像が
            # 二重化し、旧カードUUIDは永久に文脈なしのまま残る。document/page が一致し、
            # bbox が完全一致するか新しい図領域の中に収まる未接続行だけを新keyへ移し、
            # UUIDと教員レビュー済み列を保持する（図領域の推定を改めると bbox は埋め込み
            # 画像の矩形から図全体へ広がるため、包含でも引き継ぐ。完全一致を優先）。
            session.execute(
                sa_text(
                    """
                    UPDATE document_figures AS target
                    SET figure_key = :figure_key,
                        figure_label = :figure_label
                    WHERE target.id = (
                        SELECT orphan.id
                        FROM document_figures AS orphan
                        WHERE orphan.document_id = :document_id
                          AND orphan.page = :page
                          AND (
                              orphan.bbox = CAST(:bbox AS jsonb)
                              OR (
                                  jsonb_typeof(orphan.bbox) = 'array'
                                  AND jsonb_array_length(orphan.bbox) = 4
                                  AND (orphan.bbox->>0)::float >= :x0 - 2
                                  AND (orphan.bbox->>1)::float >= :y0 - 2
                                  AND (orphan.bbox->>2)::float <= :x1 + 2
                                  AND (orphan.bbox->>3)::float <= :y1 + 2
                              )
                          )
                          AND orphan.caption_block_id IS NULL
                          AND orphan.figure_key ~ '^p[0-9]+_i[0-9]+$'
                          AND NOT EXISTS (
                              SELECT 1 FROM document_figures AS current
                              WHERE current.document_id = :document_id
                                AND current.figure_key = :figure_key
                          )
                        ORDER BY (orphan.bbox = CAST(:bbox AS jsonb)) DESC, orphan.created_at ASC
                        LIMIT 1
                        FOR UPDATE
                    )
                    """
                ),
                {
                    "document_id": document_id,
                    "figure_key": figure_key,
                    "figure_label": figure_label,
                    "page": page,
                    "bbox": json.dumps(bbox),
                    "x0": float(bbox[0]), "y0": float(bbox[1]),
                    "x1": float(bbox[2]), "y1": float(bbox[3]),
                },
            )

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
                    iterative_analysis = '{}'::jsonb,
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


_CAPTION_PREFIX_RE = re.compile(r"^\s*(figure|fig|図)", re.IGNORECASE)


def _bbox_overlap_ratio(a: list[float] | None, b: list[float] | None) -> float:
    """小さい方の面積に対する重なりの割合（どちらか欠ければ 0）。"""
    if not a or not b:
        return 0.0
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / max(smaller, 1e-6)


def _filter_figure_captions(captions: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """caption 候補から本文中の図参照と重ね書きの重複を除く（決定論）。

    - ``Figure 1.2 shows ...`` のようにラベルの後に小文字の語が続くものは本文
      （``figure_regions.looks_like_figure_caption``）。
    - 同じページで bbox が重なる caption が2つあるときは、文書で多数派の表記の方を残す
      （``_drop_overlapping_captions``）。位置を文字層へ結び付け直した後にも同じ規則を掛ける。
    """
    skipped = {"non_caption": 0, "overlapping_caption": 0}
    candidates: list[dict] = []
    for cap in captions:
        if figure_regions.looks_like_figure_caption(cap.get("text", "")):
            candidates.append(cap)
        else:
            skipped["non_caption"] += 1
    return _drop_overlapping_captions(candidates, skipped), skipped


def _drop_overlapping_captions(candidates: list[dict], skipped: dict[str, int]) -> list[dict]:
    """同じページで bbox が 30% 以上重なる caption は、文書で多数派の表記（``Figure`` /
    ``Fig`` / ``図``）の方を残す。埋め込まれた別 PDF の図に含まれていた caption（見えない
    位置に切り抜かれた文字）が本物の caption と重なる場合の対策（2026-09-24 実測:
    fujimoto_d.pdf p.45 の ``FIG. 1.``）。bbox の無いものは判定しない。"""

    def prefix(cap: dict) -> str:
        text = cap.get("anchor_text") or cap.get("text", "") or ""
        match = _CAPTION_PREFIX_RE.match(text)
        return match.group(1).lower() if match else ""

    counts: dict[str, int] = {}
    for cap in candidates:
        counts[prefix(cap)] = counts.get(prefix(cap), 0) + 1
    dominant = max(counts, key=lambda k: (counts[k], k)) if counts else ""

    kept: list[dict] = []
    for cap in candidates:
        clash = next(
            (
                other for other in kept
                if other.get("page") == cap.get("page")
                and _bbox_overlap_ratio(other.get("bbox"), cap.get("bbox")) >= 0.3
            ),
            None,
        )
        if clash is None:
            kept.append(cap)
            continue
        skipped["overlapping_caption"] += 1
        if prefix(clash) != dominant and prefix(cap) == dominant:
            kept[kept.index(clash)] = cap
    return kept


def _normalized_caption_text(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (text or "").lower())[:120]


def _anchor_captions(
    captions: list[dict],
    stats: Any,
    skipped: dict[str, int],
    not_saved: list[dict],
) -> list[dict]:
    """構造化の caption を PDF の文字層の caption（``figure_regions.anchor_caption``）へ結び付ける。

    - 結び付いたら頁・位置を文字層の値に置き換え、key を作るための文字列（``anchor_text``）を持つ。
    - 同じ文字層の caption に複数が結び付いたら、文字列がいちばん近いものだけを残す
      （GROBID が崩した「Figure 5 . 4 ) 4 shows ...」の本文段落と本物の caption が重なる場合）。
    - 結び付かないもの: ラベルと位置があり文書に文字層の caption が1つも無いときは申告位置を
      そのまま使う（文字層の無い PDF の救済）。それ以外は画像を作れないので落とし、
      ``not_saved`` に残す（位置が無い = ``no_caption_position`` / 本文段落などラベルも
      文字層の対応も無い = ``unanchored_caption``）。
    """
    import difflib

    has_anchors = bool(getattr(stats, "caption_anchors", None))
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for cap in captions:
        text = cap.get("text", "") or ""
        anchor = figure_regions.anchor_caption(
            stats, text=text, page=cap.get("page"), bbox=cap.get("bbox"),
        )
        if anchor is None:
            if cap.get("bbox") and figure_regions.caption_label(text) and not has_anchors:
                resolved = dict(cap)
                key: tuple = ("declared", cap.get("page"), tuple(cap["bbox"]))
            else:
                reason = "no_caption_position" if not cap.get("bbox") else "unanchored_caption"
                skipped[reason] += 1
                not_saved.append({
                    "reason": reason,
                    "caption_block_id": cap.get("block_id"),
                    "caption_excerpt": text[:80],
                })
                continue
        else:
            resolved = dict(cap, page=anchor.page, bbox=list(anchor.bbox), anchor_text=anchor.text)
            key = ("anchor", anchor.page, tuple(anchor.bbox))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(resolved)

    kept: list[dict] = []
    for key in order:
        members = groups[key]
        if len(members) > 1:
            target = _normalized_caption_text(members[0].get("anchor_text") or "")
            members.sort(key=lambda c: -difflib.SequenceMatcher(
                None, _normalized_caption_text(c.get("text", "")), target,
            ).ratio())
            for extra in members[1:]:
                skipped["duplicate_caption"] += 1
                not_saved.append({
                    "reason": "duplicate_caption",
                    "caption_block_id": extra.get("block_id"),
                    "caption_excerpt": (extra.get("text") or "")[:80],
                })
        kept.append(members[0])
    kept.sort(key=lambda c: (c.get("page") or 0, (c.get("bbox") or [0, 0])[1]))
    return kept


# 構造化に無い caption を文字層から足すときの厳しめの形（ラベルの直後が「:」「.」）。
_STRICT_CAPTION_RE = re.compile(
    r"^\s*(?:Figure|Fig\.?|FIG\.?)\s*(?:[A-Z]\.?)?\d+(?:[.\-]\d+)*[A-Za-z]?\s*[:.．：]\s*\S"
    r"|^\s*図\s*(?:[A-Z]\.?)?\d+(?:[.\-]\d+)*\s*[:.．：\s]\s*\S",
    re.IGNORECASE,
)


def _captions_missing_from_structure(anchored: list[dict], stats: Any) -> list[dict]:
    """PDF の文字層にあるのに構造化の caption に無い図 caption を足す（caption_block_id なし）。

    GROBID 経路は付録の図などの caption を落とすことがあり（2026-09-25 実測: Figure A.1 /
    C.1 / C.2 / C.4 / C.5）、そのままでは図そのものが抽出されない。ラベルの直後が「:」「.」の
    厳しめの形だけを、ラベルごとに最初の1つだけ足す（本文中の「Figure 3 (a) ...」を拾わない）。
    """
    claimed = {(c.get("page"), tuple(c.get("bbox") or ())) for c in anchored}
    used_labels = {
        figure_regions.caption_label(c.get("anchor_text") or c.get("text", "")) for c in anchored
    }
    added: list[dict] = []
    for label, anchors in sorted((getattr(stats, "caption_anchors", None) or {}).items()):
        if label in used_labels:
            continue
        for anchor in sorted(anchors, key=lambda a: (a.page, a.bbox[1])):
            if (anchor.page, tuple(anchor.bbox)) in claimed or not _STRICT_CAPTION_RE.match(anchor.text):
                continue
            added.append({
                "block_id": None,
                "page": anchor.page,
                "order": 0,
                "text": anchor.text,
                "anchor_text": anchor.text,
                "bbox": list(anchor.bbox),
                "caption_source": "text_layer",
            })
            used_labels.add(label)
            break
    return added


def _is_low_information(pix: Any) -> tuple[bool, str]:
    if pix is None:
        return True, "not_rendered"
    assessment = figure_regions.assess_information(pix)
    return assessment.low_information, assessment.reason


def extract_document_figures(
    *,
    pdf_bytes: bytes,
    document_id: str,
    run_id: str | None,
    structure: Any = None,
    storage: Any = None,
) -> dict:
    """PDF から図画像を抽出し MinIO + document_figures に保存する（非LLM・決定論的）。

    図領域は ``figure_regions`` が決める（§17）: 障害物（本文・見出し・他の caption・柱）に
    挟まれた caption の上（空なら下）の帯から、インクの連結成分を集めて外接矩形を取る。
    帯の境界の向こうへ連続する成分は取り込み、障害物に阻まれて切れた辺は
    ``quality[].truncated_edges`` に残す。図領域のほぼ全体を1枚の埋め込み画像が覆うときだけ
    ``embedded``（その配置を元画像の解像度で描き直す）、それ以外は ``region_render``。
    **絵の無いものは行を作らない**（§18、2026-09-25）: 位置の取れない caption・情報量の乏しい
    画像（空白・一様な塗りつぶし）は保存せず、件数を ``skipped``、中身を ``not_saved`` に残す。
    保存（MinIO へのアップロード）に失敗した図だけが ``status='failed'`` の行になる。
    抽出が最後まで通ったら、今回作られなかった前回までの行を ``status='superseded'`` にする
    （行は消さない）。
    """
    if fitz is None:  # pragma: no cover - defensive, PyMuPDF is a hard dependency
        logger.warning("figure_image_extraction: PyMuPDF (fitz) is not installed")
        return {"figures": 0, "embedded": 0, "region_render": 0, "failed": 0, "figure_ids": []}

    storage = storage or get_storage_client()
    blocks = _blocks_from_structure(structure)
    raw_captions = sorted(
        (b for b in blocks if b.get("block_type") == "figure_caption" and b.get("page")),
        key=lambda b: (b.get("page") or 0, b.get("order") or 0),
    )
    # 本文中の図参照だけを先に除く（重なりの判定は位置を文字層へ結び付けた後 — 構造化の頁は
    # 当てにならず、別の頁の caption と誤って重なって見えるため）。
    figure_captions = [
        c for c in raw_captions if figure_regions.looks_like_figure_caption(c.get("text", ""))
    ]
    skipped = {"non_caption": len(raw_captions) - len(figure_captions), "overlapping_caption": 0}
    skipped.update({
        "no_caption_position": 0, "unanchored_caption": 0, "duplicate_caption": 0,
        "captioned_no_image": 0,
        "inside_figure": 0, "small": 0, "low_information": 0, "duplicate_image": 0,
    })
    # 画像を作れなかった caption（一覧には出さない）。件数だけでなく何を落としたかを残す（P4）。
    not_saved: list[dict] = []
    all_blocks_sorted = sorted(
        (b for b in blocks if b.get("page")),
        key=lambda b: (b.get("page") or 0, b.get("order") or 0),
    )
    blocks_by_page: dict[int, list[dict]] = {}
    for b in all_blocks_sorted:
        blocks_by_page.setdefault(b.get("page") or 0, []).append(b)
    counts = {"figures": 0, "embedded": 0, "region_render": 0, "failed": 0}
    figure_ids: list[str] = []
    quality: list[dict] = []

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        logger.warning(
            "figure_image_extraction: failed to open PDF for document=%s", document_id, exc_info=True,
        )
        return {**counts, "figure_ids": figure_ids, "skipped": skipped, "quality": quality}

    try:
        stats = figure_regions.collect_document_stats(doc)
    except Exception:
        logger.warning("figure_image_extraction: layout stats failed document=%s", document_id, exc_info=True)
        stats = figure_regions.DocumentLayoutStats()

    # caption の位置を PDF の文字層に結び付け直す（構造化の頁・位置は当てにしない。§18）。
    anchored = _anchor_captions(figure_captions, stats, skipped, not_saved)
    text_layer_only = _captions_missing_from_structure(anchored, stats)
    figure_captions = _drop_overlapping_captions(
        sorted(
            anchored + text_layer_only,
            key=lambda c: (c.get("page") or 0, (c.get("bbox") or [0, 0])[1]),
        ),
        skipped,
    )
    # 図領域の障害物として、ページ上の caption（図・表）すべてを渡す。
    caption_bboxes_by_page: dict[int, list[list[float]]] = {}
    for cap in figure_captions:
        caption_bboxes_by_page.setdefault(cap["page"], []).append(cap["bbox"])
    for b in all_blocks_sorted:
        if b.get("block_type") == "table_caption" and b.get("bbox"):
            caption_bboxes_by_page.setdefault(b.get("page") or 0, []).append(b["bbox"])

    layouts: dict[int, Any] = {}
    display_lists: dict[int, Any] = {}

    def renderer_for(page_num: int, page_obj: Any) -> Any:
        """ページの描画を1度だけ作って使い回す（大きな画像の復号を繰り返さない）。

        保持するのは直近のページだけ（メモリを抱え込まない）。"""
        if page_num not in display_lists:
            display_lists.clear()
            try:
                display_lists[page_num] = page_obj.get_displaylist()
            except Exception:
                display_lists[page_num] = page_obj
        return display_lists[page_num]

    def layout_for(page_num: int, page_obj: Any) -> Any:
        if page_num not in layouts:
            try:
                layouts[page_num] = figure_regions.analyze_page(
                    page_obj, stats, caption_bboxes=caption_bboxes_by_page.get(page_num, []),
                )
            except Exception:
                logger.warning(
                    "figure_image_extraction: page layout failed document=%s page=%s",
                    document_id, page_num, exc_info=True,
                )
                layouts[page_num] = None
        return layouts[page_num]

    def record(ok: bool, fig_id: str, method: str) -> None:
        counts["figures"] += 1
        if ok:
            counts[method] += 1
            figure_ids.append(fig_id)
        else:
            counts["failed"] += 1
            if fig_id:
                figure_ids.append(fig_id)

    session = _pg_session()
    try:
        # ── Phase 1: 各ページの画像配置を収集（同じ画像の複数配置も全部） ─
        placements_by_page: dict[int, list[Any]] = {}
        for page_index in range(len(doc)):
            try:
                placements_by_page[page_index + 1] = figure_regions.image_placements(doc[page_index])
            except Exception:
                placements_by_page[page_index + 1] = []

        used_placements: dict[int, set[int]] = {p: set() for p in placements_by_page}
        figure_regions_by_page: dict[int, list[list[float]]] = {}
        seen_keys: set[str] = set()

        # ── Phase 2: caption ごとに図領域を決め、埋め込み画像か領域レンダリングで保存 ─
        for idx, cap in enumerate(figure_captions):
            if not cap.get("bbox"):
                # 位置の取れない caption（GROBID の figure 要素で PDF 側の行と突合できなかったもの）は
                # ページも既定値（1）で当てにならず、図の領域を決められない。画像の無い行を作らない。
                skipped["no_caption_position"] += 1
                not_saved.append({
                    "reason": "no_caption_position",
                    "caption_block_id": cap.get("block_id"),
                    "caption_excerpt": (cap.get("text") or "")[:80],
                })
                continue
            page_num = cap.get("page") or 0
            figure_key, figure_label = _normalize_figure_key(
                cap.get("anchor_text") or cap.get("text", ""), page_num, idx,
            )
            if figure_key in seen_keys:
                figure_key = f"{figure_key}_{idx}"
            seen_keys.add(figure_key)
            page_obj = doc[page_num - 1] if 1 <= page_num <= len(doc) else None
            placements = placements_by_page.get(page_num, [])
            used = used_placements.setdefault(page_num, set())

            layout = layout_for(page_num, page_obj) if page_obj is not None else None
            region = None
            if layout is not None and cap.get("bbox"):
                try:
                    region = figure_regions.locate_figure_region(
                        page_obj, layout, cap["bbox"], renderer=renderer_for(page_num, page_obj),
                    )
                except Exception:
                    logger.warning(
                        "figure_image_extraction: region location failed document=%s figure=%s",
                        document_id, figure_key, exc_info=True,
                    )
                    region = None

            method = "region_render"
            bbox: list[float] | None = None
            confidence: float | None = None
            pix = None
            source = "legacy"
            renderer = renderer_for(page_num, page_obj) if page_obj is not None else None
            if region is not None:
                covering = figure_regions.single_image_covering(layout, region.bbox)
                if covering is not None:
                    pix = _render_pixmap(renderer, list(covering.bbox), _embedded_zoom(covering))
                    low, _ = _is_low_information(pix)
                    if not low:
                        method = "embedded"
                        bbox = [round(v, 2) for v in covering.bbox]
                if method != "embedded":
                    bbox = region.bbox
                    confidence = region.confidence
                    pix = _render_pixmap(renderer, bbox)
                source = region.source
            elif page_obj is not None and layout is None:
                # ページの解析自体に失敗したときだけの従来経路。解析できて図の中身が見つからなかった
                # ときは使わない（従来の推定は本文ごと切り出すため — §17-1 の Figure 1.1）。
                candidates = [{"bbox": list(p.bbox)} for p in placements]
                match_idx = _find_best_image_match(
                    candidates, {i for i, p in enumerate(placements) if p.index in used}, cap.get("bbox"),
                )
                if match_idx is not None:
                    placement = placements[match_idx]
                    pix = _render_pixmap(renderer, list(placement.bbox), _embedded_zoom(placement))
                    method = "embedded"
                    bbox = [round(v, 2) for v in placement.bbox]
                else:
                    bbox, confidence = _estimate_region_bbox(
                        page_obj, cap, blocks_by_page.get(page_num, []),
                    )
                    pix = _render_pixmap(renderer, bbox)

            low, info_reason = _is_low_information(pix) if pix is not None else (True, "not_rendered")
            image_bytes = None
            if pix is not None and not low:
                try:
                    image_bytes = pix.tobytes("png")
                except Exception:
                    logger.warning("figure_image_extraction: png encode failed", exc_info=True)
            if bbox is not None and layout is not None:
                for placement in layout.images:
                    if figure_regions.rect_inside_ratio(placement.bbox, bbox) >= _ORPHAN_INSIDE_FIGURE_RATIO:
                        used.add(placement.index)
                figure_regions_by_page.setdefault(page_num, []).append(bbox)

            if image_bytes is None:
                # 絵が無いものは図として一覧に出さない（2026-09-25 オーナー指示）。行は作らず、
                # 何を落としたかは artifact の not_saved に残す。
                skipped["captioned_no_image"] += 1
                not_saved.append({
                    "reason": info_reason,
                    "figure_key": figure_key,
                    "page": page_num,
                    "caption_block_id": cap.get("block_id"),
                    "caption_excerpt": (cap.get("text") or "")[:80],
                })
                continue

            fig_id, ok = _save_figure(
                session,
                document_id=document_id, run_id=run_id,
                figure_key=figure_key, figure_label=figure_label,
                page=page_num, bbox=bbox,
                caption_block_id=cap.get("block_id"), caption_text=cap.get("text", ""),
                extraction_method=method, region_confidence=confidence,
                inner_labels=(
                    _extract_inner_labels(page_obj, bbox, cap.get("bbox"))
                    if page_obj is not None else []
                ),
                image_bytes=image_bytes, storage=storage,
            )
            record(ok, fig_id, method)
            quality.append({
                "figure_key": figure_key,
                "method": method,
                "caption_source": cap.get("caption_source", "structure"),
                "region_source": source,
                "side": region.side if region is not None else None,
                "truncated_edges": list(region.truncated_edges) if region is not None else [],
                "demoted_obstacles": region.demoted_obstacles if region is not None else 0,
                "information": info_reason,
            })

        # ── Phase 3: caption と対応しない残余の画像。図の部品・小さすぎる・情報量の
        # 乏しいものは図として提示しない（件数は skipped に数える）。 ─
        for page_num, placements in placements_by_page.items():
            used = used_placements.get(page_num, set())
            page_obj = doc[page_num - 1] if 1 <= page_num <= len(doc) else None
            if page_obj is None:
                continue
            regions = figure_regions_by_page.get(page_num, [])
            seen_images: set[tuple] = set()
            for placement in placements:
                if placement.index in used:
                    continue
                identity = (
                    ("xref", placement.xref) if placement.xref
                    else ("dims", placement.width, placement.height, placement.size)
                )
                if identity in seen_images:
                    skipped["duplicate_image"] += 1
                    continue
                seen_images.add(identity)
                renderer = renderer_for(page_num, page_obj)
                visible = figure_regions.visible_content_bbox(renderer, placement.bbox)
                if visible is None:
                    skipped["low_information"] += 1
                    continue
                bbox = [round(v, 2) for v in visible]
                if any(
                    figure_regions.rect_inside_ratio(bbox, r) >= _ORPHAN_INSIDE_FIGURE_RATIO for r in regions
                ):
                    skipped["inside_figure"] += 1
                    continue
                if max(bbox[2] - bbox[0], bbox[3] - bbox[1]) < figure_regions.MIN_FIGURE_SIDE_PT:
                    skipped["small"] += 1
                    continue
                pix = _render_pixmap(renderer, bbox, _embedded_zoom(placement))
                low, info_reason = _is_low_information(pix)
                if low:
                    skipped["low_information"] += 1
                    continue
                figure_key = f"p{page_num}_i{placement.index}"
                if figure_key in seen_keys:
                    continue
                seen_keys.add(figure_key)
                try:
                    image_bytes = pix.tobytes("png")
                except Exception:
                    image_bytes = None
                fig_id, ok = _save_figure(
                    session,
                    document_id=document_id, run_id=run_id,
                    figure_key=figure_key, figure_label=None,
                    page=page_num, bbox=bbox,
                    caption_block_id=None, caption_text="",
                    extraction_method="embedded", region_confidence=None,
                    inner_labels=_extract_inner_labels(page_obj, bbox),
                    image_bytes=image_bytes, storage=storage,
                )
                record(ok, fig_id, "embedded")
                quality.append({
                    "figure_key": figure_key,
                    "method": "embedded",
                    "region_source": "uncaptioned_image",
                    "side": None,
                    "truncated_edges": [],
                    "demoted_obstacles": 0,
                    "information": info_reason,
                })

        # ── Phase 4: 今回の抽出で作られなかった行を superseded にする（行は消さない） ─
        counts["superseded"] = _supersede_untouched_figures(session, document_id, figure_ids)
    finally:
        session.close()
        try:
            doc.close()
        except Exception:
            pass

    counts["truncated"] = sum(1 for q in quality if q["truncated_edges"])
    counts["low_information"] = sum(
        1 for q in quality if q["information"] not in ("informative", "unmeasured")
    )
    return {
        **counts, "figure_ids": figure_ids, "skipped": skipped, "quality": quality,
        "not_saved": not_saved,
    }


def _supersede_untouched_figures(session: Any, document_id: str, touched_ids: list[str]) -> int:
    """この document の図のうち、今回の抽出で作られなかった行を ``status='superseded'`` にする。

    抽出が最後まで通ったときだけ呼ぶ（途中で例外なら呼ばれない）。行・教員のレビュー列・
    MinIO の画像は消さない（P4）。同じ key が次の抽出で再び作られれば ``_save_figure`` の
    upsert が status を 'extracted' に戻す。一覧・検出要素・G層は 'extracted' だけを数える
    （migration 087）。
    """
    try:
        result = session.execute(
            sa_text(
                """
                UPDATE document_figures
                SET status = 'superseded'
                WHERE document_id = :document_id
                  AND status <> 'superseded'
                  AND NOT (id::text = ANY(:touched))
                """
            ),
            {"document_id": document_id, "touched": [str(i) for i in touched_ids if i]},
        )
        session.commit()
        return int(getattr(result, "rowcount", 0) or 0)
    except Exception:
        session.rollback()
        logger.warning(
            "figure_image_extraction: superseding untouched figures failed document=%s",
            document_id, exc_info=True,
        )
        return 0


def is_listed_figure(row: dict) -> bool:
    """図・画像一覧・検出要素に出してよい行か（絵のある最新の行だけ）。

    ``status='failed'``（画像の保存に失敗）と ``'superseded'``（最新の抽出で作られなかった
    前回までの行）は出さない（2026-09-25 オーナー指示「絵が無いのであれば一覧で表示する
    必要はない」）。行自体は消さず、画像配信や参照解決は従来どおり id で引ける。
    """
    return (row.get("status") or "extracted") == "extracted"


def load_document_figures(document_id: str) -> list[dict]:
    """document_figures 行を dict のリストで返す（図配信 API とステージ2で共用）。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id::text, document_id::text AS document_id, run_id::text, figure_key, figure_label,
                       page, bbox, caption_block_id, caption_text, minio_key,
                       extraction_method, region_confidence, status, created_at, inner_labels,
                       suggested_mode, mode_reason, analysis_profile, reviewed_mode,
                       mode_review_status, mode_reviewed_by::text, mode_reviewed_at,
                       reviewed_analysis_mode, reviewed_analysis_profile,
                       analysis_review_status, analysis_reviewed_by::text,
                       analysis_reviewed_at, analysis_review_source_annotation_id::text,
                       iterative_analysis
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
            iterative_analysis = item.get("iterative_analysis")
            if isinstance(iterative_analysis, str):
                try:
                    item["iterative_analysis"] = json.loads(iterative_analysis)
                except (ValueError, TypeError):
                    item["iterative_analysis"] = {}
            elif not isinstance(iterative_analysis, dict):
                item["iterative_analysis"] = {}
            result.append(item)
        return result
    finally:
        session.close()
