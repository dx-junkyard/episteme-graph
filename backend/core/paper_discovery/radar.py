"""論文レーダー — 教材（seed）起点の候補探索（読み時導出・非LLM）。

設計正本: ``docs/features/paper_radar_design.md`` §5.1（不変条項 PR1〜PR8）。
分野購読の4層のじょうご（網 / 絞り / 並べ替え / 人間の弁）を、分野ではなく
**論文1本**を起点に読み替える層で、既存の発見層（PD1〜PD8）の機構をそのまま使う。

このモジュールが守るもの:

- **PR1 起点は教材1件・候補は読み時導出**: seed の解決も候補一覧も毎回導出し、
  保存しない。レーダー専用のテーブル・列を持たない（migration 0）。
- **PR2 距離は段階ラベルのみ**: 帯分けは ``ranking.band_candidates`` に委ね、
  cosine の生値はここへ出てこない。測れなかった候補にはラベルが付かない。
- **PR5 教員の明示操作のみ**: worker / cron からこのモジュールを呼ぶ経路を作らない
  （呼び出し元は ``routes/paper_discovery.py`` の教員 API だけ）。
- **PR6 外部 API は既存クライアント経由**: arXiv への到達は ``arxiv_client``
  （宛先定数 + モジュールレベル3秒スロットル）のみ。
- **PR7 閉世界の正直さ**: 検索条件（``query``）と ``closed_world_note`` を必ず同梱し、
  カテゴリが引けない・帯を作れないは事実文で degrade する（黙って別の条件に
  すり替えない）。
- 分野購読の ``last_checked_at`` / ``last_search_found_new`` を**更新しない**
  （購読条件による検索ではないため。コーパス回遊層「地図の端」の集約1ビットを
  汚さない — 設計書 §5.1）。
- **見送り（dismissal）を読まない・書かない**（dismissal は分野購読の共同財概念で、
  教材起点の一時的な探索には持ち込まない — 設計書 §8）。

FastAPI 非 import・``core.llm`` 非 import（embedding は ``ranking.py``、比較文は
``compare.py`` が唯一の接触点。ガードレールが構造として固定する）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text as sa_text

from core.label_vocab import (
    RADAR_DISTANCE_LABEL_FAR,
    RADAR_DISTANCE_LABEL_MID,
    RADAR_DISTANCE_LABEL_NEAR,
)
from core.paper_discovery import arxiv_client, corpus, search, store
from core.paper_discovery import ranking as pd_ranking
from core.paper_discovery.schema import (
    RADAR_DISTANCES,
    abs_url_for,
    normalize_arxiv_id,
    normalize_categories,
    normalize_keyphrases,
)

logger = logging.getLogger(__name__)

#: カテゴリの供給元（PR7 — どこから来た条件かを必ず DTO に明示する）。
CATEGORIES_SOURCE_ARXIV = "arxiv"
CATEGORIES_SOURCE_SUBSCRIPTION = "subscription"
CATEGORIES_SOURCE_MANUAL = "manual"

#: seed から供給するキーフレーズ候補の上限（チップ欄を埋め尽くさない控えめな値）。
MAX_SEED_KEYPHRASES = 12

#: 承認済みとみなす ``theory_components.review_status``。
#: 語彙は ``vocab.APPROVED_REVIEW_STATUSES`` と同じ集合（分野スコープの供給③の
#: document スコープ版。片方だけ増やさないこと）。
APPROVED_REVIEW_STATUSES = ("teacher_approved", "teacher_reviewed", "endorsed")

#: seed の arXiv メタデータを引けなかったときの事実文（PR7 — 黙って縮退しない）。
NOTE_ARXIV_METADATA_UNAVAILABLE = (
    "arXiv から論文情報を取得できなかったため、別の供給元から検索条件を組み立てました。"
)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


# ---------------------------------------------------------------------------
# seed の解決
# ---------------------------------------------------------------------------


def _document_row(session, document_ref: str) -> Optional[dict[str, str]]:
    """``documents.id`` / ``source_path`` のどちらでも document 1行を解決する。"""
    ref = _clean(document_ref)
    if not ref:
        return None
    row = session.execute(
        sa_text(
            """
            SELECT id::text,
                   COALESCE(source_path, ''),
                   COALESCE(NULLIF(title, ''), NULLIF(filename, ''), ''),
                   COALESCE(source_url, '')
              FROM documents
             WHERE id::text = :ref OR source_path = :ref
             LIMIT 1
            """
        ),
        {"ref": ref},
    ).fetchone()
    if not row:
        return None
    return {
        "document_id": str(row[0] or "").strip(),
        "source_path": str(row[1] or "").strip(),
        "title": _clean(row[2]),
        "source_url": str(row[3] or "").strip(),
    }


def seed_keyphrase_candidates(session, document_row: dict[str, str]) -> list[dict]:
    """seed 教材の承認済み理論部品ラベルをキーフレーズ候補にする（PD3 の流儀）。

    ``theory_components.document_id`` は documents.id（UUID 文字列）と material_id
    （``source_path``）のどちらも取りうるため、両方を候補に入れる
    （``corpus.domain_document_refs`` と同じ二面性）。供給元は既存語彙
    ``component`` を再利用する（語彙を増やさない — 設計書 §5.1）。
    """
    refs = [
        value
        for value in dict.fromkeys(
            [document_row.get("document_id", ""), document_row.get("source_path", "")]
        )
        if value
    ]
    if not refs:
        return []

    rows = session.execute(
        sa_text(
            """
            SELECT DISTINCT name
              FROM theory_components
             WHERE document_id = ANY(CAST(:refs AS text[]))
               AND review_status = ANY(CAST(:statuses AS text[]))
               AND COALESCE(name, '') <> ''
             ORDER BY name
             LIMIT :limit
            """
        ),
        {
            "refs": refs,
            "statuses": list(APPROVED_REVIEW_STATUSES),
            "limit": MAX_SEED_KEYPHRASES,
        },
    ).fetchall()

    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        text = _clean(row[0])
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append({"text": text, "source": "component", "enabled": True})
    return out


def resolve_seed(session, document_id: str, *, fetch_arxiv: bool = True) -> dict:
    """seed 教材のメタデータ・検索条件の供給元を解決する（PR1 — 保存しない）。

    カテゴリの供給順（設計書 §5.1）:

    1. ``documents.source_url`` から arXiv ID が取れ、``fetch_arxiv=True`` なら
       :func:`arxiv_client.fetch_by_ids` のメタデータ（``categories_source="arxiv"``。
       要旨も同時に得る）。**arXiv 到達の失敗は fail-soft** で 2. へ縮退し、
       ``note`` に事実文を残す（黙って条件をすり替えない — PR7）。
    2. seed の分野（:func:`corpus.document_domain_keys` の先頭）の購読
       ``arxiv_categories``（``categories_source="subscription"``）。
    3. どちらも無ければ空（``categories_source="manual"`` = 教員の手入力待ち。
       **条件ゼロでは arXiv を呼ばない** — PD6）。

    Returns:
        ``{document_id, title, arxiv_id, abs_url, summary, categories,
        categories_source, keyphrase_candidates, domain_key, note?}``。
        ``arxiv_id`` / ``abs_url`` は arXiv 由来として登録されていない教材では
        ``None``（判定不能を偽装しない — 設計書 §8 の既存規約）。

    Raises:
        LookupError: document が存在しない（権限判定は route 層の責務）。
    """
    row = _document_row(session, document_id)
    if row is None:
        raise LookupError("document not found")

    arxiv_id = normalize_arxiv_id(row.get("source_url"))
    domain_keys = corpus.document_domain_keys(session, row["document_id"])
    domain_key = domain_keys[0] if domain_keys else ""

    seed: dict[str, Any] = {
        "document_id": row["document_id"],
        "title": row.get("title") or "",
        "arxiv_id": arxiv_id,
        "abs_url": abs_url_for(arxiv_id) if arxiv_id else None,
        "summary": "",
        "categories": [],
        "categories_source": CATEGORIES_SOURCE_MANUAL,
        "keyphrase_candidates": seed_keyphrase_candidates(session, row),
        "domain_key": domain_key,
    }

    if arxiv_id and fetch_arxiv:
        try:
            entries = arxiv_client.fetch_by_ids([arxiv_id])
        except arxiv_client.ArxivApiError:
            # seed のメタデータが引けなくても検索そのものは成立させる（PR7）。
            logger.info("radar seed metadata unavailable for %s", arxiv_id)
            entries = []
            seed["note"] = NOTE_ARXIV_METADATA_UNAVAILABLE
        if entries:
            entry = entries[0]
            categories = normalize_categories(
                list(entry.categories) + ([entry.primary_category] if entry.primary_category else [])
            )
            seed["summary"] = entry.summary or ""
            if entry.abs_url:
                seed["abs_url"] = entry.abs_url
            if categories:
                seed["categories"] = categories
                seed["categories_source"] = CATEGORIES_SOURCE_ARXIV

    if not seed["categories"] and domain_key:
        subscription = store.get_subscription(session, domain_key) or {}
        categories = normalize_categories(subscription.get("arxiv_categories"))
        if categories:
            seed["categories"] = categories
            seed["categories_source"] = CATEGORIES_SOURCE_SUBSCRIPTION

    return seed


# ---------------------------------------------------------------------------
# クエリ組み立て
# ---------------------------------------------------------------------------


def build_radar_query(distance: str, categories: Any, keyphrases: Any) -> str:
    """距離に応じた arXiv の ``search_query`` を組み立てる（設計書 §3 / §5.1）。

    - ``near``: カテゴリ **AND** キーフレーズ（テーマまで近い候補を狙う）
    - ``mid`` / ``far``: カテゴリのみ（キーフレーズで絞ると「近い」に寄るため。
      テーマの遠さは第2層の帯分けが表す）

    著者項は使わない（分野購読の補助シグナルであって、教材起点の距離とは無関係）。

    Raises:
        ValueError: ``distance`` が :data:`~core.paper_discovery.schema.RADAR_DISTANCES`
            の語彙外（fail-closed — 黙って ``near`` に倒さない）。
    """
    if distance not in RADAR_DISTANCES:
        raise ValueError(f"unknown radar distance: {distance!r}")
    if distance == "near":
        return search.build_search_query(categories, keyphrases)
    return search.build_search_query(categories)


# ---------------------------------------------------------------------------
# 検索の実行
# ---------------------------------------------------------------------------


#: 選択した距離 → 既定で展開する帯のラベル（正本は ``core.label_vocab`` —
#: ラベル文字列をここで再定義しない。フロントはこの ``primary_label`` の帯を
#: 展開表示し、他の帯は折りたたみで保持する — 設計書 §4.2 / PR2）。
_DISTANCE_PRIMARY_LABELS = {
    "near": RADAR_DISTANCE_LABEL_NEAR,
    "mid": RADAR_DISTANCE_LABEL_MID,
    "far": RADAR_DISTANCE_LABEL_FAR,
}


def _banding_info(banded: dict, distance: str) -> dict:
    info: dict[str, Any] = {"available": bool(banded.get("available"))}
    if info["available"]:
        primary = _DISTANCE_PRIMARY_LABELS.get(distance)
        if primary:
            info["primary_label"] = primary
    note = banded.get("note")
    if note:
        info["note"] = note
    return info


def _merge_distance_labels(originals: list[dict], banded_items: list[dict]) -> list[dict]:
    """元の並び順（新着順）を保ったまま ``distance_label`` だけを移す。

    ``mid`` / ``far`` は「遠い順に並べる」ことをしない（疑似精度になる — 設計書 §3）。
    帯分けは付いたラベルだけを使い、並びは arXiv の新着順のままにする。
    """
    labels = {
        str(item.get("arxiv_id") or ""): item["distance_label"]
        for item in banded_items
        if item.get("distance_label")
    }
    out: list[dict] = []
    for item in originals:
        payload = dict(item)
        label = labels.get(str(item.get("arxiv_id") or ""))
        if label:
            payload["distance_label"] = label
        out.append(payload)
    return out


def run_radar_search(
    session,
    document_id: str,
    *,
    distance: str = "near",
    categories: Any = None,
    keyphrases: Any = None,
    start: int = 0,
    max_results: int = search.DEFAULT_MAX_RESULTS,
) -> dict:
    """seed 教材の周辺を arXiv から探し、距離帯つきの候補一覧を返す。

    ``categories`` / ``keyphrases`` を渡した場合は seed 由来の供給より優先する
    （条件を保存せずに試せる。分野購読は書き換えない — PR1 / PD3）。

    Returns:
        ``{"seed", "query", "distance", "total", "start", "candidates", "banding",
        "closed_world_note"}``。``candidates`` の各要素は
        :meth:`ArxivEntry.to_dict` に ``status``（``new`` / ``ingested``）と
        ``matched_keyphrases``、測れた場合のみ ``distance_label`` を足したもの。
        条件が空なら arXiv を呼ばず ``query=""`` / ``candidates=[]``（PD6）。

    Raises:
        LookupError: document が存在しない。
        ValueError: ``distance`` が語彙外。
        arxiv_client.ArxivApiError: arXiv API への到達・応答・パースの失敗
            （呼び出し側が事実文で degrade する）。
    """
    if distance not in RADAR_DISTANCES:
        raise ValueError(f"unknown radar distance: {distance!r}")

    # カテゴリが明示指定されているときは seed の arXiv 取得を省く（不要な外部
    # コールを増やさない。要旨は帯分けのフォールバック素材なので、重心が作れる
    # 前提の明示指定では取りに行かない — PR6 の行儀）。
    seed = resolve_seed(session, document_id, fetch_arxiv=categories is None)

    effective_categories = normalize_categories(
        categories if categories is not None else seed.get("categories")
    )
    effective_keyphrases = normalize_keyphrases(
        keyphrases if keyphrases is not None else seed.get("keyphrase_candidates")
    )

    query = build_radar_query(distance, effective_categories, effective_keyphrases)

    result: dict[str, Any] = {
        "seed": seed,
        "query": query,
        "distance": distance,
        "total": 0,
        "start": max(0, int(start or 0)),
        "candidates": [],
        "banding": {"available": False},
        "closed_world_note": search.CLOSED_WORLD_NOTE,
    }
    if not query:
        # 条件ゼロで arXiv を呼ばない（無関係な全件が返るため — PD6）。
        return result

    total, entries = arxiv_client.search(
        query, start=result["start"], max_results=max_results
    )

    ingested = search.ingested_arxiv_ids(session)
    seed_arxiv_id = seed.get("arxiv_id") or ""

    candidates: list[dict] = []
    for entry in entries:
        payload = entry.to_dict()
        arxiv_id = payload.get("arxiv_id") or ""
        if seed_arxiv_id and arxiv_id == seed_arxiv_id:
            # seed 自身は候補ではない（同一正規化 ID で除外 — 設計書 §4.2）。
            continue
        # 見送り（dismissal）は分野購読の概念なので、レーダーでは注釈しない。
        payload["status"] = "ingested" if arxiv_id in ingested else "new"
        payload["matched_keyphrases"] = (
            search.matched_keyphrases(payload, effective_keyphrases)
            if distance == "near"
            else []
        )
        candidates.append(payload)

    banded = pd_ranking.band_candidates(
        session,
        candidates,
        seed_vector=pd_ranking.document_centroid(session, seed["document_id"]),
        seed_text=seed.get("summary") or "",
    )
    ordered = list(banded.get("ordered") or candidates)
    if distance == "near":
        # 近い帯は類似度降順（Phase 3 の relevance 並びと同じ扱い）。
        result["candidates"] = ordered
    else:
        # mid / far は新着順のまま（遠い順に並べるのは疑似精度 — 設計書 §3）。
        result["candidates"] = _merge_distance_labels(candidates, ordered)
    result["banding"] = _banding_info(banded, distance)
    result["total"] = int(total or 0)
    return result


__all__ = [
    "APPROVED_REVIEW_STATUSES",
    "CATEGORIES_SOURCE_ARXIV",
    "CATEGORIES_SOURCE_MANUAL",
    "CATEGORIES_SOURCE_SUBSCRIPTION",
    "MAX_SEED_KEYPHRASES",
    "NOTE_ARXIV_METADATA_UNAVAILABLE",
    "build_radar_query",
    "resolve_seed",
    "run_radar_search",
    "seed_keyphrase_candidates",
]
