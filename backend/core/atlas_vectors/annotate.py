"""分野マップのベクトル係留層 — ギャップ候補への近傍注記（読み時導出・保存しない）。

正本: ``docs/features/atlas_vector_anchoring_design.md`` §7。

カテゴリギャップ候補（``core/atlas_gaps/store.py::derive_candidates`` の cluster）の
``proposed_label`` を埋め込み、現行凍結版アンカーと照合して「既存概念の別表記の
可能性」を注記する。これは**読み時導出であり保存しない**（v1 に alias candidate 行は
存在しない — 登録は教員の明示操作だけが作る, VA1）。

不変条項:

- **VA4 fail-soft**: アンカー不在・埋め込み失敗・日次ゲート超過のいずれでも
  **例外を出さず** clusters をそのまま返す（注記キー自体が無い =
  既存レスポンスと完全後方互換）。
- **VA2 数値非表示**: 注記に生スコアを載せない（段階ラベルのみ）。
- **VA3 教員起点のみ**: 呼び出し地点はギャップレビュー画面（教員）の組み立てだけ。
  日次ゲートは :mod:`core.atlas_vectors.builder` と**同じインスタンス**を共有する
  （層で1本の embedding 予算）。
- in-process キャッシュ key は ``(domain_key, skeleton_version, normalized_label)``。
  版が変われば自動的にキャッシュミス = 再計算になる。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Mapping, Optional, Sequence

from core.atlas_vectors import builder, schema
from core.atlas_vectors.query import nearest_anchors
from core.label_vocab import ANCHOR_NEARNESS_SCALE, ANCHOR_NEARNESS_THRESHOLD_NEAR

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
#: ``(domain_key, skeleton_version, normalized_label) -> list[float]``
_label_vectors: dict[tuple[str, str, str], list[float]] = {}

#: キャッシュの上限（プロセス内。超えたら丸ごと捨てる — LRU を持つほどの規模ではない）。
_CACHE_MAX_ENTRIES = 2000


def reset_cache() -> None:
    """in-process キャッシュを初期化する（テスト用。本番コードから呼ばない）。"""
    with _cache_lock:
        _label_vectors.clear()


def _cached(key: tuple[str, str, str]) -> Optional[list[float]]:
    with _cache_lock:
        return _label_vectors.get(key)


def _store_cached(items: Mapping[tuple[str, str, str], list[float]]) -> None:
    with _cache_lock:
        if len(_label_vectors) + len(items) > _CACHE_MAX_ENTRIES:
            _label_vectors.clear()
        _label_vectors.update(items)


def annotate_gap_clusters(
    session: Any,
    domain_key: str,
    skeleton_version: str,
    clusters: Sequence[Mapping[str, Any]],
    *,
    daily_limit: Optional[int] = None,
) -> list[dict]:
    """ギャップ候補 cluster に ``near_anchor`` 注記を付ける（fail-soft）。

    最上位帯（:data:`ANCHOR_NEARNESS_THRESHOLD_NEAR` 以上）の cluster にだけ、
    **入力の複製**へ次のキーを足す（設計書 §7 / §9）::

        "near_anchor": {"node_id", "node_label", "region_label",
                        "nearness_label", "skeleton_version"}

    Args:
        session: DB セッション（アンカー読み出しにだけ使う）。
        domain_key: 分野キー。
        skeleton_version: 現行凍結版（アンカーの版と一致しなければ注記しない）。
        clusters: ``derive_candidates`` の返り値（変更しない）。
        daily_limit: 省略時は ``ATLAS_VECTOR_MAX_CALLS_PER_DAY``。

    Returns:
        注記済み clusters（複製）。何も注記できないときは入力と同じ内容の複製。
    """
    items = [dict(c) for c in (clusters or [])]
    if not items:
        return items

    domain = str(domain_key or "").strip()
    version = str(skeleton_version or "").strip()
    if not domain or not version:
        return items

    try:
        anchors, resolved_version = builder.anchors_with_labels(session, domain, version)
    except Exception:  # noqa: BLE001 — 注記が出ないだけ（既存レスポンスは壊さない）
        logger.warning(
            "atlas anchor annotate: anchors unavailable for %s (non-fatal)",
            domain, exc_info=True,
        )
        return items
    if not anchors or resolved_version != version:
        return items

    # ラベルごとに1つだけ埋め込む（同一クラスタ名の重複は1回で済ませる）。
    wanted: dict[tuple[str, str, str], str] = {}
    keys_by_index: dict[int, tuple[str, str, str]] = {}
    for index, cluster in enumerate(items):
        label = " ".join(str(cluster.get("proposed_label") or "").split())
        if not label:
            continue
        normalized = schema.normalize_label(label)
        if not normalized:
            continue
        key = (domain, version, normalized)
        keys_by_index[index] = key
        if _cached(key) is None and key not in wanted:
            wanted[key] = label

    if wanted:
        if daily_limit is None:
            try:
                from core.config import get_settings

                daily_limit = get_settings().atlas_vector_max_calls_per_day
            except Exception:  # noqa: BLE001 — 設定が読めないなら注記しない
                logger.warning("atlas anchor annotate: settings unavailable", exc_info=True)
                return items
        if not builder.check_daily_gate(int(daily_limit)):
            # 予算切れ = 静かに注記なし（VA4。既存レスポンスと後方互換）。
            return items
        keys = list(wanted)
        try:
            vectors = builder.embed_texts([wanted[k] for k in keys])
        except Exception:  # noqa: BLE001 — 埋め込み失敗で画面を壊さない
            logger.warning(
                "atlas anchor annotate: label embedding failed for %s (non-fatal)",
                domain, exc_info=True,
            )
            return items
        if len(vectors) != len(keys):
            logger.warning(
                "atlas anchor annotate: embedding count mismatch (%s vs %s)",
                len(keys), len(vectors),
            )
            return items
        _store_cached(
            {key: list(vector) for key, vector in zip(keys, vectors) if vector}
        )

    for index, cluster in enumerate(items):
        key = keys_by_index.get(index)
        if key is None:
            continue
        vector = _cached(key)
        if not vector:
            continue
        best = nearest_anchors(vector, anchors, limit=1)
        if not best:
            continue
        anchor, similarity = best[0]
        if similarity < ANCHOR_NEARNESS_THRESHOLD_NEAR:
            # 最上位帯のみ注記する（設計書 §9 — 「なんとなく関連」を出さない）。
            continue
        cluster["near_anchor"] = {
            "node_id": anchor.node_id,
            "node_label": anchor.label or anchor.node_id,
            "region_label": anchor.region_label or "",
            "nearness_label": ANCHOR_NEARNESS_SCALE.label_for(similarity),
            "skeleton_version": version,
        }

    return items


__all__ = ["annotate_gap_clusters", "reset_cache"]
