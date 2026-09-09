"""引用グラフ API の唯一の入口（Phase 3 / 設計書 §6。PD7 — 外部 API の行儀）。

第2の候補供給源として Semantic Scholar の **recommendations API** を叩く
（``GET /recommendations/v1/papers/forpaper/arXiv:{id}``）。API キー不要の匿名利用で、
レート制限に配慮してモジュールレベルのスロットルを持つ。

「コーパスを補う論文」レンズC（基盤論文。正本
``docs/features/corpus_complement_design.md`` §5.3）は、同じホスト・同じスロットル・
同じ失敗の扱いで **graph API の参照リスト**（``GET /graph/v1/paper/arXiv:{id}/references``）
も引く。エンドポイントの違いは :data:`_PATH_RECOMMENDATIONS` /
:data:`_PATH_REFERENCES` のパス定数だけで、宛先ホストと HTTP 経路は1本のまま
（迂回口を作らない）。

``arxiv_client.py`` と**同じ規律**を独立に実装する:

- **宛先は定数** :data:`SEMANTIC_SCHOLAR_API_HOST`（正本は
  ``core.paper_discovery.schema``）。呼び出し側から URL を渡せる引数を作らない。
- **モジュールレベルの 3 秒スロットル**。arXiv 側とは**共有しない** — スロットルは
  ホストごとの行儀であり、片方の呼び出しがもう片方を待たせる理由はない。
- タイムアウトを持ち、失敗は :class:`CitationApiError` で諦める（リトライループを
  持たない。呼び出し側が事実文で degrade する — PD6）。
- FastAPI 非 import・``core.llm`` 非 import（引用グラフ供給は LLM 0回）。

**arXiv ID を持つ論文だけ**を返す。既存の取り込み経路（url_fetch → arXiv の PDF）に
乗らない論文を候補として見せても取り込めないため（PD2 / fail-closed の表示）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import requests

from core.paper_discovery.schema import (
    SEMANTIC_SCHOLAR_API_HOST,
    CitationEntry,
    normalize_arxiv_id,
)

logger = logging.getLogger(__name__)

#: 最小リクエスト間隔（秒）。匿名利用のレート制限に配慮した 3 秒（PD7 の同族）。
_MIN_INTERVAL_SECONDS = 3.0

#: 1リクエストの HTTP タイムアウト（秒）の既定値。
DEFAULT_TIMEOUT_SECONDS = 30.0

#: 1シードあたりに要求する推薦件数の上限。
MAX_LIMIT = 100

#: 1シードあたりに要求する参照（references）件数の上限（レンズC）。
MAX_REFERENCE_LIMIT = 200

#: 要求するフィールド（余分なフィールドを取りに行かない）。
FIELDS = "title,abstract,year,authors,externalIds"

#: 参照リストで要求するフィールド（推薦と同じ — 候補カードに要る分だけ）。
REFERENCE_FIELDS = "title,abstract,year,authors,externalIds"

#: 推薦 API のパス（``{arxiv_id}`` は正規化済み ID で埋める）。
_PATH_RECOMMENDATIONS = "/recommendations/v1/papers/forpaper/arXiv:{arxiv_id}"

#: 参照リスト API のパス（レンズC）。
_PATH_REFERENCES = "/graph/v1/paper/arXiv:{arxiv_id}/references"

#: スロットル状態（モジュールレベル。プロセス内の全呼び出しで共有する）。
_throttle_lock = threading.Lock()
_last_request_at: Optional[float] = None


class CitationApiError(Exception):
    """引用グラフ API への到達・応答・パースの失敗。"""


# ---------------------------------------------------------------------------
# スロットル
# ---------------------------------------------------------------------------


def _throttle() -> None:
    """前回リクエストから :data:`_MIN_INTERVAL_SECONDS` 未満なら不足分だけ待つ。"""
    global _last_request_at
    with _throttle_lock:
        now = time.monotonic()
        if _last_request_at is not None:
            wait = _MIN_INTERVAL_SECONDS - (now - _last_request_at)
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
        _last_request_at = now


def reset_throttle() -> None:
    """スロットル状態を初期化する（テスト用。本番コードから呼ばない）。"""
    global _last_request_at
    with _throttle_lock:
        _last_request_at = None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _api_url(arxiv_id: str, path: str = _PATH_RECOMMENDATIONS) -> str:
    """引用グラフ API のエンドポイント URL（宛先は定数ホストに固定 — PD7）。

    ``path`` はこのモジュール内のパス定数（推薦 / 参照リスト）だけを受ける。
    呼び出し側から宛先そのものを渡せる引数は作らない。
    """
    return f"https://{SEMANTIC_SCHOLAR_API_HOST}{path.format(arxiv_id=arxiv_id)}"


def _http_get(arxiv_id: str, params: dict, timeout: float, path: str = _PATH_RECOMMENDATIONS) -> dict:
    """引用グラフ API へ1回だけ GET する（スロットル込み）。JSON を返す。

    推薦・参照リストのどちらも**この関数だけ**を通る（スロットル・タイムアウト・
    エラー写像を1本に保つ）。
    """
    _throttle()
    try:
        response = requests.get(_api_url(arxiv_id, path), params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise CitationApiError("引用グラフ API への接続に失敗しました") from exc

    if response.status_code != 200:
        raise CitationApiError("引用グラフ API からの応答を取得できませんでした")

    try:
        payload = response.json()
    except ValueError as exc:
        raise CitationApiError("引用グラフ API の応答を解釈できませんでした") from exc

    if not isinstance(payload, dict):
        raise CitationApiError("引用グラフ API の応答を解釈できませんでした")
    return payload


# ---------------------------------------------------------------------------
# パース
# ---------------------------------------------------------------------------


def _entry_from_payload(raw: Any, seed_arxiv_id: str) -> Optional[CitationEntry]:
    if not isinstance(raw, dict):
        return None
    external_ids = raw.get("externalIds")
    if not isinstance(external_ids, dict):
        return None
    arxiv_id = normalize_arxiv_id(external_ids.get("ArXiv"))
    if not arxiv_id:
        # arXiv ID の無い論文は既存の取り込み経路に乗らない（PD2）。黙殺せず debug に残す。
        logger.debug("skipping paper without arXiv id: %r", raw.get("title"))
        return None

    authors: list[str] = []
    for author in raw.get("authors") or ():
        if isinstance(author, dict):
            name = " ".join(str(author.get("name") or "").split())
        else:
            name = " ".join(str(author or "").split())
        if name:
            authors.append(name)

    year: Optional[int] = None
    try:
        if raw.get("year") is not None:
            year = int(raw["year"])
    except (TypeError, ValueError):
        year = None

    return CitationEntry(
        arxiv_id=arxiv_id,
        title=" ".join(str(raw.get("title") or "").split()),
        summary=" ".join(str(raw.get("abstract") or "").split()),
        authors=authors,
        year=year,
        seed_arxiv_id=seed_arxiv_id,
    )


def parse_recommendations(payload: dict, seed_arxiv_id: str) -> list[CitationEntry]:
    """推薦 API の JSON を :class:`CitationEntry` の列へ変換する。

    ``recommendedPapers`` 以外の形（キー欠落・型違い）は空リストへ縮退させる
    （API 側の仕様変更で例外を投げるより、候補ゼロ + 事実文の方が安全）。
    """
    papers = (payload or {}).get("recommendedPapers")
    if not isinstance(papers, list):
        return []
    out: list[CitationEntry] = []
    for raw in papers:
        entry = _entry_from_payload(raw, seed_arxiv_id)
        if entry is not None:
            out.append(entry)
    return out


# ---------------------------------------------------------------------------
# 推薦の取得
# ---------------------------------------------------------------------------


def recommendations_for_arxiv(
    arxiv_id: str,
    *,
    limit: int = 20,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[CitationEntry]:
    """1本の arXiv 論文に対する推薦論文（arXiv ID を持つものだけ）を返す。

    Args:
        arxiv_id: シード論文の arXiv ID（正規化前の表記も受ける）。
        limit: 要求件数（1〜:data:`MAX_LIMIT` に丸める）。
        timeout: HTTP タイムアウト（秒）。

    Raises:
        CitationApiError: ID 不正・接続失敗・非200・パース不能。
    """
    seed = normalize_arxiv_id(arxiv_id)
    if not seed:
        raise CitationApiError("arXiv ID として解釈できませんでした")

    try:
        count = int(limit)
    except (TypeError, ValueError):
        count = 20
    count = max(1, min(count, MAX_LIMIT))

    payload = _http_get(seed, {"fields": FIELDS, "limit": count}, timeout)
    return parse_recommendations(payload, seed)


# ---------------------------------------------------------------------------
# 参照リストの取得（レンズC「基盤論文」）
# ---------------------------------------------------------------------------


def parse_references(payload: dict, seed_arxiv_id: str) -> list[CitationEntry]:
    """参照リスト API の JSON を :class:`CitationEntry` の列へ変換する。

    graph API の応答は ``{"offset", "next", "data": [{"citedPaper": {...}}]}``。
    ``citedPaper`` が「引用された側」＝候補で、``seed_arxiv_id`` は**引用している側**
    （取り込み済みのシード論文）を指す。

    ``data`` 以外の形（キー欠落・型違い）は空リストへ縮退させる（推薦 API と同じ
    規律 — API 側の仕様変更で例外を投げるより、候補ゼロ + 事実文の方が安全）。
    arXiv ID を持たない参照は落とす（既存の取り込み経路に乗らないため — PD2）。
    """
    rows = (payload or {}).get("data")
    if not isinstance(rows, list):
        return []
    out: list[CitationEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        entry = _entry_from_payload(row.get("citedPaper"), seed_arxiv_id)
        if entry is not None:
            out.append(entry)
    return out


def references_for_arxiv(
    arxiv_id: str,
    *,
    limit: int = 100,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[CitationEntry]:
    """1本の arXiv 論文が**引用している**論文（arXiv ID を持つものだけ）を返す。

    レンズC（基盤論文）の唯一の外部入力。推薦 API と同じホスト定数・同じ 3 秒
    スロットル・同じタイムアウト・同じ :class:`CitationApiError` を通る。

    Args:
        arxiv_id: 引用している側（取り込み済みシード）の arXiv ID。
        limit: 要求件数（1〜:data:`MAX_REFERENCE_LIMIT` に丸める）。
        timeout: HTTP タイムアウト（秒）。

    Raises:
        CitationApiError: ID 不正・接続失敗・非200・パース不能。
    """
    seed = normalize_arxiv_id(arxiv_id)
    if not seed:
        raise CitationApiError("arXiv ID として解釈できませんでした")

    try:
        count = int(limit)
    except (TypeError, ValueError):
        count = 100
    count = max(1, min(count, MAX_REFERENCE_LIMIT))

    payload = _http_get(
        seed,
        {"fields": REFERENCE_FIELDS, "limit": count},
        timeout,
        _PATH_REFERENCES,
    )
    return parse_references(payload, seed)
