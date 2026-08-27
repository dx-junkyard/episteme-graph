"""引用グラフによる候補供給（Phase 3 / 設計書 §6 — 第2の供給源）。

「同じ分野」の最強シグナルは引用ネットワークである、という設計判断の実装。
**取り込み済み論文をシードに**引用グラフ API（``citation_client``）を引き、
出てきた論文を Phase 1 と同じ注釈（``ingested`` / ``dismissed`` / ``new``）つきで
候補として返す。

不変条項:

- **PD1 発見は自動、取り込みは教員の明示承認のみ**: ここは候補を返すだけで、
  論文本体の取得・受理の経路を持たない（Phase 1 の core と同じく、取得層・受理関数を
  import しない — 取り込みは route 層の教員操作だけが持つ）。
- **PD4 数値スコアを見せない**: 推薦の順序は API の返した順そのままで、点数・
  類似度を DTO に足さない。
- **PD5 候補は保存しない**: シードも候補もテーブルに書かない（読み時導出）。
- **PD6 閉世界の正直さ**: 返す DTO は「取り込み済み論文からの引用・推薦関係で
  辿れた範囲」であることを :data:`CLOSED_WORLD_NOTE` で必ず明示し、シードが無い
  ことを「該当なし」と偽らない（``available: False`` + 事実文）。
- **オプトイン**: ``DISCOVERY_CITATION_SOURCE_ENABLED`` が偽なら**外部 API を
  呼ばない**（ゲートを core に置き、route 層の分岐忘れで外へ出ないようにする）。
- LLM 0回・embedding 0回。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from core.paper_discovery import citation_client, corpus, search as search_mod, store

logger = logging.getLogger(__name__)

#: 候補一覧に必ず添える事実文（PD6）。
CLOSED_WORLD_NOTE = (
    "この一覧は取り込み済み論文の引用・推薦関係から導出した範囲のみを示します。"
)

#: シードにする取り込み済み論文の既定件数（新しい順）。
DEFAULT_MAX_SEEDS = 5

#: 1シードあたりに取得する推薦の既定件数。
DEFAULT_LIMIT_PER_SEED = 10

NOTE_DISABLED = (
    "引用グラフによる候補供給は有効化されていません。"
    "サーバ環境変数 DISCOVERY_CITATION_SOURCE_ENABLED で有効化できます。"
)
NOTE_NO_SEEDS = (
    "この分野には、引用関係をたどる起点になる取り込み済みの arXiv 論文がまだありません。"
)


def citation_source_enabled() -> bool:
    """引用グラフ供給が有効か（既定 off — 設計書 §6 のオプトイン）。"""
    from core.config import get_settings  # 遅延 import（core の純粋性を保つ）

    try:
        return bool(get_settings().discovery_citation_source_enabled)
    except Exception:  # noqa: BLE001 — 設定が読めないときは有効化しない（fail-closed）
        logger.warning("failed to read discovery citation source setting", exc_info=True)
        return False


def _disabled_result() -> dict:
    return {
        "enabled": False,
        "available": False,
        "note": NOTE_DISABLED,
        "candidates": [],
        "seeds": [],
    }


def run_citation_search(
    session,
    domain_key: str,
    *,
    max_seeds: int = DEFAULT_MAX_SEEDS,
    limit_per_seed: int = DEFAULT_LIMIT_PER_SEED,
) -> dict:
    """取り込み済み論文をシードに、引用グラフから候補を導出する。

    Returns:
        ``{"available", "enabled", "candidates", "closed_world_note", "seeds"}``。
        各候補は :meth:`CitationEntry.to_dict` に ``status``
        （``new`` / ``ingested`` / ``dismissed``）と ``derived_from``
        （``[{"arxiv_id", "title"}]`` — どの取り込み済み論文から辿ったか）を足したもの。
        オプトイン未設定・シードゼロは ``available: False`` + 事実文。

    Raises:
        citation_client.CitationApiError: 全シードの照会に失敗した場合。
            部分的に成功していれば取れた分を返す（黙って空にしない — PD6）。
    """
    if not citation_source_enabled():
        return _disabled_result()

    key = str(domain_key or "").strip()
    try:
        seed_count = max(1, int(max_seeds))
    except (TypeError, ValueError):
        seed_count = DEFAULT_MAX_SEEDS
    try:
        per_seed = max(1, int(limit_per_seed))
    except (TypeError, ValueError):
        per_seed = DEFAULT_LIMIT_PER_SEED

    seeds = corpus.domain_ingested_papers(session, key, limit=seed_count) if key else []
    result: dict[str, Any] = {
        "enabled": True,
        "available": False,
        "domain_key": key,
        "candidates": [],
        "seeds": [{"arxiv_id": s["arxiv_id"], "title": s["title"]} for s in seeds],
        "closed_world_note": CLOSED_WORLD_NOTE,
    }
    if not seeds:
        result["note"] = NOTE_NO_SEEDS
        return result

    seed_titles = {s["arxiv_id"]: s["title"] for s in seeds}
    seed_ids = set(seed_titles)

    by_arxiv_id: dict[str, dict] = {}
    failures = 0
    last_error: Optional[Exception] = None
    for seed in seeds:
        try:
            entries = citation_client.recommendations_for_arxiv(
                seed["arxiv_id"], limit=per_seed
            )
        except citation_client.CitationApiError as exc:
            # 1シードの失敗で全体を落とさない（残りのシードは引ける）。
            failures += 1
            last_error = exc
            logger.info(
                "citation recommendations failed for seed %s: %s", seed["arxiv_id"], exc
            )
            continue

        for entry in entries:
            if entry.arxiv_id in seed_ids:
                # シード自身が推薦に混ざることがある（自分自身は候補にしない）。
                continue
            payload = by_arxiv_id.get(entry.arxiv_id)
            if payload is None:
                payload = entry.to_dict()
                payload["derived_from"] = []
                by_arxiv_id[entry.arxiv_id] = payload
            origin = {
                "arxiv_id": seed["arxiv_id"],
                "title": seed_titles.get(seed["arxiv_id"], ""),
            }
            if origin not in payload["derived_from"]:
                payload["derived_from"].append(origin)

    if failures == len(seeds):
        # 1件も引けていない = 外部 API に到達できていない。空一覧を「該当なし」と
        # 偽らず、呼び出し側が事実文で degrade できるよう例外で返す（PD6）。
        raise citation_client.CitationApiError(
            "引用グラフ API から候補を取得できませんでした"
        ) from last_error

    ingested = search_mod.ingested_arxiv_ids(session)
    dismissed = store.dismissed_ids(session, key) if key else set()

    candidates: list[dict] = []
    for arxiv_id, payload in by_arxiv_id.items():
        if arxiv_id in ingested:
            payload["status"] = "ingested"
        elif arxiv_id in dismissed:
            payload["status"] = "dismissed"
        else:
            payload["status"] = "new"
        candidates.append(payload)

    result["available"] = True
    result["candidates"] = candidates
    if failures:
        # 一部のシードが引けなかった事実を黙らせない（件数は事実であって点数ではない）。
        result["partial"] = True
    return result
