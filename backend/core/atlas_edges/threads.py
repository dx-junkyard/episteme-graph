"""分野マップの関係表示（RE層）— 推定の糸レイヤーの読み時導出（設計書 §4 / §6）。

正本: ``docs/features/atlas_relation_edges_design.md``（不変条項 RE1〜RE8 は §2）。

学習者・教員のオーバーレイに重ねる「まだ確定していない関係」を返す。v1 は
**vector 由来のみ**（配置共起の糸は非スコープ — §10）。

不変条項の写像:

- **RE6 embedding を呼ばない**: 保存済みアンカーベクトル（VA層）と凍結骨格の読みだけ。
  学習者の画面から呼ばれても外部 API に触れない（VA3 / CR7 継承）。
- **RE7 ヘアボール防止**: 最上位帯（``ANCHOR_NEARNESS_THRESHOLD_NEAR``）のみ・
  concept–concept のみ・既存辺と同一 region 内ペアを除外（導出側）・1ノードあたり
  :data:`schema.THREADS_MAX_PER_NODE` 本・全体 :data:`schema.THREADS_MAX_TOTAL` 本。
- **RE8 教員の判断は表示に反映される**: 見送り済み（``dismissed``）の edge_key は
  毎回の呼び出しで除外する（キャッシュの後段でフィルタするので、見送りは即座に効く）。
- **RE2 出所必須**: 骨格版（``skeleton_version``）を必ず返す。呼び出し側は点線 +
  「AIによる推定（未確認）」ラベルと併せてしか描いてはならない。
- **RE4 数値非表示**: 近さは段階ラベルのみ。cosine の生値は DTO に載らない。
- **fail-soft**: 例外・アンカー不在・骨格なしは ``{"available": False}``。
  **絶対に送出しない**（``/api/atlas`` のマージが学習者の地図を壊さないため）。

キャッシュ: ``(domain_key, skeleton_version)`` をキーに、**判断を適用する前の**
ペア一覧（近い順）を in-process に保持する。凍結版は不変なので TTL を持たない
（版が変われば自動的にキャッシュミス）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from core.atlas_edges import derive, schema, store

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
#: ``(domain_key, skeleton_version) -> list[dict]``（判断適用前・近い順）
_pair_cache: dict[tuple[str, str], list[dict]] = {}

#: キャッシュの上限（ドメイン数 × 版数。超えたら丸ごと捨てる — LRU を持つ規模ではない）。
_CACHE_MAX_ENTRIES = 64

#: 糸の DTO に載せるキー（**これ以外を足さない** — 生スコア・edge_key・判断は出さない）。
_ITEM_KEYS = ("from", "to", "from_label", "to_label", "nearness_label")


def reset_cache() -> None:
    """in-process キャッシュを初期化する（テスト用。本番コードから呼ばない）。"""
    with _cache_lock:
        _pair_cache.clear()


def _cached(key: tuple[str, str]) -> list[dict] | None:
    with _cache_lock:
        cached = _pair_cache.get(key)
        return list(cached) if cached is not None else None


def _store_cached(key: tuple[str, str], pairs: list[dict]) -> None:
    with _cache_lock:
        if len(_pair_cache) >= _CACHE_MAX_ENTRIES:
            _pair_cache.clear()
        _pair_cache[key] = list(pairs)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _candidate_pairs(session: Any, domain_key: str) -> tuple[list[dict], str]:
    """(判断適用前のペア一覧, 骨格版)。アンカー不在・骨格なしは ``([], "")``。"""
    from core import atlas_store
    from core.atlas_vectors import builder

    skeleton = atlas_store.load_frozen_skeleton(session, domain_key)
    if skeleton is None:
        return [], ""
    version = _clean(getattr(skeleton, "version", ""))
    if not version:
        return [], ""

    cached = _cached((domain_key, version))
    if cached is not None:
        return cached, version

    anchors, anchor_version = builder.anchors_with_labels(
        session, domain_key, version
    )
    if not anchors or _clean(anchor_version) != version:
        # ベクトル未構築 / 版ずれ。古い版のアンカーを現行として出さない（VA8）。
        return [], version

    pairs = [
        {
            "edge_key": schema.build_edge_key(
                domain_key, item["from_id"], item["to_id"]
            ),
            "from": item["from_id"],
            "to": item["to_id"],
            "from_label": item["from_label"],
            "to_label": item["to_label"],
            "nearness_label": item["nearness_label"],
        }
        for item in derive.derive_vector_pairs(skeleton, anchors)
    ]
    _store_cached((domain_key, version), pairs)
    return pairs, version


def _apply_caps(pairs: list[dict]) -> list[dict]:
    """1ノード上限・全体上限を近い順の貪欲で適用する（RE7）。"""
    per_node: dict[str, int] = {}
    out: list[dict] = []
    for pair in pairs:
        if len(out) >= schema.THREADS_MAX_TOTAL:
            break
        left, right = pair["from"], pair["to"]
        if per_node.get(left, 0) >= schema.THREADS_MAX_PER_NODE:
            continue
        if per_node.get(right, 0) >= schema.THREADS_MAX_PER_NODE:
            continue
        per_node[left] = per_node.get(left, 0) + 1
        per_node[right] = per_node.get(right, 0) + 1
        out.append({key: pair[key] for key in _ITEM_KEYS})
    return out


def threads_for_domain(session: Any, domain_key: str) -> dict:
    """推定の糸（``/api/atlas`` の optional キー ``threads`` の中身）。

    Returns:
        ``{"available": True, "skeleton_version": str, "items": [{"from", "to",
        "from_label", "to_label", "nearness_label"}, ...]}``。導出できないときは
        ``{"available": False}`` のみ（キーを増やさない = 呼び出し側はレイヤーごと
        非表示にする）。**例外は送出しない**。
    """
    domain = _clean(domain_key)
    if not domain:
        return {"available": False}
    try:
        pairs, version = _candidate_pairs(session, domain)
        if not version or not pairs:
            return {"available": False}
        dismissed = store.dismissed_edge_keys(session, domain)
        live = [pair for pair in pairs if pair["edge_key"] not in dismissed]
        return {
            "available": True,
            "skeleton_version": version,
            "items": _apply_caps(live),
        }
    except Exception:  # noqa: BLE001 — 糸は付加物。地図の表示を絶対に止めない（RE4/RE2 の fail-soft）
        logger.debug(
            "atlas_edges: failed to derive relation threads for %r", domain,
            exc_info=True,
        )
        return {"available": False}


__all__ = [
    "reset_cache",
    "threads_for_domain",
]
