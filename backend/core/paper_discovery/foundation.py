"""レンズC「基盤論文」— 取り込み済み論文が共通に引用している未取り込み論文。

設計正本: ``docs/features/corpus_complement_design.md`` §5.3（不変条項 CC1〜CC8 は §2）。
親: ``docs/features/paper_discovery_design.md``（PD1〜PD8）。

「良質な論文」を外部の被引用数で決めるのではなく、**このコーパスの論文たちが依拠して
いる**という構造で決める。取り込み済みの arXiv 論文をシードに参照リスト
（``citation_client.references_for_arxiv``）を引き、``min_citing_seeds`` 本以上の
シードが共通に引用している論文を候補として返す。

不変条項:

- **CC1 補完の根拠はコーパス構造のみ**: 入力は取り込み済み論文と参照リストだけ。
  学習者痕跡（``interest_traces`` 系）を選定入力にしない。
- **CC2 決定論・非LLM**: LLM 0回・embedding 0回。並び順も決定論。
- **CC4 数値非表示**: 引用しているシードの本数・被引用数を DTO に出さない。
  本数は**並び順にだけ**使い、根拠はシードのタイトル列挙（``cited_by``）で示す。
- **CC7 取り込みは既存の弁のみ**: ここは候補を返すだけで、取得層（``url_fetch``）も
  受理関数も import しない（citation_search と同じ規律）。取り込みは route 層の
  教員操作だけが持つ。
- **CC8 fail-soft**: シードゼロ・オプトイン未設定は ``available: False`` + 事実文。
  一部シードの失敗は ``partial: True`` で正直に示し、残りの候補は返す。
  **1件も読めなかったときだけ** :class:`CitationApiError` を投げる（空一覧を
  「該当なし」と偽らない — PD6）。
- **PD7 外部 API の行儀**: 1回の教員操作で新たに引くシードは ``fetch_per_call`` 本まで。
  残りは ``pending_seeds: True`` として「もう一度押せば続きを読む」ことを事実で示す。
  参照リストは :mod:`core.paper_discovery.reference_cache` に写す（CC3 の例外）。
- FastAPI 非 import・``core.llm`` 非 import。``commit`` は呼び出し側（API 層）の責務。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from core.paper_discovery import (
    citation_client,
    citation_search,
    corpus,
    reference_cache,
    search as search_mod,
    store,
)
from core.paper_discovery.schema import CitationEntry, normalize_arxiv_id

logger = logging.getLogger(__name__)

#: 候補一覧に必ず添える事実文（PD6 / CC5 — 閉世界を骨格ではなく出所で明示する）。
CLOSED_WORLD_NOTE = (
    "この一覧は取り込み済み論文の参照リストから導出した範囲のみを示します。"
)

#: オプトイン未設定の事実文（引用グラフ供給と共用 — 語彙を増やさない）。
NOTE_DISABLED = citation_search.NOTE_DISABLED

NOTE_NO_SEEDS = (
    "この分野には、参照リストを読む起点になる取り込み済みの arXiv 論文が"
    "まだありません。"
)
NOTE_PENDING_SEEDS = (
    "まだ参照リストを読んでいない取り込み済み論文があります。"
    "もう一度押すと続きを読みます。"
)
NOTE_NO_CANDIDATES = (
    "取り込み済みの複数の論文が共通に引用している未取り込みの論文は、"
    "読めた範囲では見つかりませんでした。"
)


def _disabled_result(domain_key: str) -> dict:
    """オプトイン未設定のときの DTO（外部 API を呼ばない）。

    ``citation_search._disabled_result`` と同型で、レンズC のキー
    （``seeds_read`` / ``pending_seeds``）まで揃えて形を安定させる。
    """
    return {
        "enabled": False,
        "available": False,
        "domain_key": domain_key,
        "note": NOTE_DISABLED,
        "candidates": [],
        "seeds_read": [],
        "pending_seeds": False,
        "closed_world_note": CLOSED_WORLD_NOTE,
    }


def _entry_dict(payload: Any) -> Optional[dict]:
    """キャッシュ / 取得結果の1件を候補 DTO の素へ（arXiv ID の無いものは落とす）。"""
    if isinstance(payload, CitationEntry):
        payload = payload.to_dict()
    if not isinstance(payload, dict):
        return None
    arxiv_id = normalize_arxiv_id(payload.get("arxiv_id"))
    if not arxiv_id:
        return None
    out = dict(payload)
    out["arxiv_id"] = arxiv_id
    return out


def _year_sort_key(value: Any) -> int:
    """年の降順ソート用キー（不明な年は末尾へ — 捏造せず後ろに置く）。"""
    try:
        return -int(value)
    except (TypeError, ValueError):
        return 10**9


def run_foundation_search(
    session,
    domain_key: str,
    *,
    min_citing_seeds: int,
    fetch_per_call: int,
    ttl_days: int,
) -> dict:
    """取り込み済み論文の参照リストから「基盤論文」の候補を導出する。

    Args:
        session: SQLAlchemy セッション（``commit`` は呼び出し側）。
        domain_key: 分野キー（``learning_courses.data.cartridge_id``）。
        min_citing_seeds: 候補として浮上させる最低の「引用しているシード」本数。
        fetch_per_call: 1回の操作で新たに参照リストを引くシード数の上限。
        ttl_days: 参照リストキャッシュを新鮮とみなす日数。

    Returns:
        ``{"enabled", "available", "domain_key", "candidates", "seeds_read",
        "pending_seeds", "closed_world_note", "note"?, "partial"?}``。
        各候補は :meth:`CitationEntry.to_dict` に ``cited_by``
        （``[{"arxiv_id", "title"}]`` — どの取り込み済み論文が引用しているか）と
        ``status``（``new`` / ``ingested`` / ``dismissed``）を足したもの。
        取り込み済みの候補も**外さず** ``status`` で示す（PD6 — 既存一覧と同じ）。

    Raises:
        citation_client.CitationApiError: 参照リストを1件も読めなかった場合
            （キャッシュも無く、今回の取得も全滅）。空一覧を「該当なし」と
            偽らないため、呼び出し側が事実文で degrade する。
    """
    if not citation_search.citation_source_enabled():
        return _disabled_result(str(domain_key or "").strip())

    key = str(domain_key or "").strip()
    try:
        min_seeds = max(1, int(min_citing_seeds))
    except (TypeError, ValueError):
        min_seeds = 2
    try:
        per_call = max(1, int(fetch_per_call))
    except (TypeError, ValueError):
        per_call = 5

    seeds = corpus.domain_ingested_papers(session, key) if key else []
    result: dict[str, Any] = {
        "enabled": True,
        "available": False,
        "domain_key": key,
        "candidates": [],
        "seeds_read": [],
        "pending_seeds": False,
        "closed_world_note": CLOSED_WORLD_NOTE,
    }
    if not seeds:
        result["note"] = NOTE_NO_SEEDS
        return result

    seed_titles = {s["arxiv_id"]: s.get("title") or "" for s in seeds}
    seed_ids = list(seed_titles)

    # --- 1. キャッシュ read-through（新鮮な 'ok' 行だけを「読めた」とみなす） ---
    references_by_seed = reference_cache.get_fresh(session, seed_ids, ttl_days=ttl_days)
    recently_failed = reference_cache.fresh_failed_ids(session, seed_ids, ttl_days=ttl_days)

    # --- 2. 未読シードを新しい順に fetch_per_call 本だけ取りに行く（PD7） ---
    unread = [
        arxiv_id
        for arxiv_id in seed_ids
        if arxiv_id not in references_by_seed and arxiv_id not in recently_failed
    ]
    to_fetch = unread[:per_call]
    failures = 0
    last_error: Optional[Exception] = None
    for arxiv_id in to_fetch:
        try:
            entries = citation_client.references_for_arxiv(arxiv_id)
        except citation_client.CitationApiError as exc:
            # 1シードの失敗で全体を落とさない。TTL 内の再取得も抑える（PD7）。
            failures += 1
            last_error = exc
            logger.info("references failed for seed %s: %s", arxiv_id, exc)
            reference_cache.upsert(
                session, arxiv_id, [], fetch_status=reference_cache.FETCH_STATUS_FAILED
            )
            continue
        payloads = [entry.to_dict() for entry in entries]
        reference_cache.upsert(
            session, arxiv_id, payloads, fetch_status=reference_cache.FETCH_STATUS_OK
        )
        references_by_seed[arxiv_id] = payloads

    if not references_by_seed:
        # 参照リストを1件も読めていない。空一覧を「該当なし」と偽らず、呼び出し側が
        # 事実文で degrade できるよう例外で返す（PD6）。
        raise citation_client.CitationApiError(
            "参照リストを取得できませんでした"
        ) from last_error

    result["seeds_read"] = [
        {"arxiv_id": arxiv_id, "title": seed_titles.get(arxiv_id, "")}
        for arxiv_id in seed_ids
        if arxiv_id in references_by_seed
    ]
    result["pending_seeds"] = len(unread) > len(to_fetch)
    if failures:
        # 一部のシードが読めなかった事実を黙らせない。
        result["partial"] = True

    # --- 3. 参照を arXiv ID で集約（引用しているシードを列挙する） ---
    seed_id_set = set(seed_ids)
    by_arxiv_id: dict[str, dict] = {}
    for seed_arxiv_id in seed_ids:
        for raw in references_by_seed.get(seed_arxiv_id) or ():
            payload = _entry_dict(raw)
            if payload is None or payload["arxiv_id"] in seed_id_set:
                # シード自身（コーパス内の相互引用）は候補にしない。
                continue
            existing = by_arxiv_id.get(payload["arxiv_id"])
            if existing is None:
                payload["cited_by"] = []
                by_arxiv_id[payload["arxiv_id"]] = payload
                existing = payload
            origin = {
                "arxiv_id": seed_arxiv_id,
                "title": seed_titles.get(seed_arxiv_id, ""),
            }
            if origin not in existing["cited_by"]:
                existing["cited_by"].append(origin)

    # --- 4. 反復した信号だけを浮上させる（引用しているシードが min_seeds 本以上） ---
    surfaced = [
        payload
        for payload in by_arxiv_id.values()
        if len(payload["cited_by"]) >= min_seeds
    ]

    # --- 5. 並び順（引用しているシードの本数 → 年 → ID）。本数は DTO に出さない（CC4） ---
    surfaced.sort(
        key=lambda p: (
            -len(p["cited_by"]),
            _year_sort_key(p.get("year")),
            p["arxiv_id"],
        )
    )

    ingested = search_mod.ingested_arxiv_ids(session)
    dismissed = store.dismissed_ids(session, key) if key else set()
    for payload in surfaced:
        if payload["arxiv_id"] in ingested:
            payload["status"] = "ingested"
        elif payload["arxiv_id"] in dismissed:
            payload["status"] = "dismissed"
        else:
            payload["status"] = "new"

    result["available"] = True
    result["candidates"] = surfaced
    if result["pending_seeds"]:
        # まだ読んでいないシードがある事実を、候補の有無より先に置く（PD6）。
        result["note"] = NOTE_PENDING_SEEDS
    elif not surfaced:
        result["note"] = NOTE_NO_CANDIDATES
    return result
