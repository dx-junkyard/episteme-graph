"""arXiv API 呼び出しの唯一の入口（PD7 — 外部 API の行儀）。

設計正本: ``docs/features/paper_discovery_design.md`` §4.2。

不変条項:

- **宛先は定数** :data:`~core.paper_discovery.schema.ARXIV_API_HOST` に固定する。
  呼び出し側から URL を渡せる引数を作らない（教員が任意の URL を渡せる経路は
  ``core/url_fetch.py`` の許可リスト側に閉じ込めてある。宛先固定の API クライアントは
  性質が異なるため許可リスト照合の対象にはしないが、その前提は「宛先を動かせない」
  ことに依存する — 設計書 §4.2）。
- **モジュールレベルの 3 秒スロットル**（:data:`_MIN_INTERVAL_SECONDS`）。前回
  リクエスト時刻をモジュール変数で保持し、間隔が足りなければ不足分だけ待つ。
  プロセス内の全呼び出しに効かせるため、関数引数やインスタンス状態にしない。
- タイムアウトと件数上限を持ち、失敗は :class:`ArxivApiError` で諦める（リトライ
  ループを持たない — 呼び出し側が事実文で degrade する、PD6）。arXiv 側の混雑
  （HTTP 429）だけは部分型 :class:`ArxivRateLimitedError` で区別する（「繋がらない」と
  「混んでいる」では教員の次の一手が違うため。区別しない呼び出し側は基底型の
  except 節のまま動く）。
- **429 を受けたら一定時間 arXiv を呼ばない（混雑クールダウン、2026-09-14）**。
  混雑中も画面操作のたびにリクエストが出ると、ブロックの窓が人の操作で延び続ける。
  429 を受けた時刻から :func:`_cooldown_seconds` 秒のあいだは **HTTP を出さずに**
  :class:`ArxivRateLimitedError` を投げる。これは自動リトライ（PD7 が禁じるもの）の
  逆で、**抑制**である — 待ち直しも再送もせず、呼ぶこと自体をやめる。窓が明ければ
  次の呼び出しは普通に出る（人の操作を起点に、というPD1/PR5の規律は変わらない）。
  秒数の正本は env ``ARXIV_RATE_LIMIT_COOLDOWN_SECONDS``（既定 600・0 で抑制なし）。
- FastAPI 非 import・``core.llm`` 非 import（発見層は LLM 0回）。
- Atom のパースは stdlib の ``xml.etree`` のみ（外部依存を足さない）。

例外メッセージは日本語の事実文で、内部情報（解決 IP・スタックトレース）を載せない。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional
from xml.etree import ElementTree

import requests

from core.paper_discovery.schema import (
    ARXIV_API_HOST,
    ArxivEntry,
    abs_url_for,
    normalize_arxiv_id,
    pdf_url_for,
    split_arxiv_ref,
)

logger = logging.getLogger(__name__)

#: arXiv API の最小リクエスト間隔（秒）。arXiv の利用条件に合わせた 3 秒（PD7）。
_MIN_INTERVAL_SECONDS = 3.0

#: 1リクエストの HTTP タイムアウト（秒）の既定値。
DEFAULT_TIMEOUT_SECONDS = 30.0

#: 1リクエストで要求できる件数の上限（UI 表示は数十件 + 「さらに読み込む」で足りる）。
MAX_RESULTS_LIMIT = 200

#: ``id_list`` で一度に指定できる ID の上限（論文レーダー §5.4。seed のメタデータ取得は
#: 1件、比較分析の候補取り直しは :data:`~core.paper_discovery.compare.
#: RADAR_COMPARE_MAX_CANDIDATES` 件なので、控えめな定数で足りる）。
MAX_ID_LIST = 20

#: arXiv がレート制限で応答を拒むときの HTTP ステータス。
RATE_LIMITED_STATUS = 429

#: 混雑クールダウンの既定秒数（設定を読めないときのフォールバック。config の既定と同値）。
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 600.0

#: クールダウン中に投げる事実文（数値を書かない — 残り秒数を人に見せない）。
RATE_LIMIT_COOLDOWN_MESSAGE = (
    "arXiv 側の混雑を受けて、しばらく arXiv への問い合わせを止めています"
)

#: 並び順の語彙（arXiv API の ``sortBy`` / ``sortOrder``）。
#: v1 は日付順のみ（並び順は新着順 = 機械の点数を持ち込まない、PD4）。
SORT_BY_VALUES = ("submittedDate", "lastUpdatedDate")
SORT_ORDER_VALUES = ("descending", "ascending")

_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"
_OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"

_NS = {"atom": _ATOM_NS, "arxiv": _ARXIV_NS, "opensearch": _OPENSEARCH_NS}

#: スロットル状態（モジュールレベル。プロセス内の全呼び出しで共有する）。
_throttle_lock = threading.Lock()
_last_request_at: Optional[float] = None

#: 混雑クールダウンの期限（``time.monotonic()`` 基準。None = クールダウンなし）。
#: スロットルと同じ ``_throttle_lock`` で守る（状態が2つに割れないように）。
_cooldown_until: Optional[float] = None


class ArxivApiError(Exception):
    """arXiv API への到達・応答・パースの失敗。"""


class ArxivRateLimitedError(ArxivApiError):
    """arXiv 側のレート制限（HTTP 429）で応答を得られなかった。

    :class:`ArxivApiError` の部分型なので、区別しない既存の ``except`` 節は
    そのまま動く（呼び出し側を一斉に直さなくてよい）。区別したい側だけが先に
    捕まえて別の事実文へ分岐する。**ここでも待ち直さない** — リトライを持たない
    のは基底型と同じ規律で、待つかどうかは人間が決める（PD7）。
    """


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
    """スロットル状態と混雑クールダウンを初期化する（テスト用。本番コードから呼ばない）。"""
    global _last_request_at, _cooldown_until
    with _throttle_lock:
        _last_request_at = None
        _cooldown_until = None


# ---------------------------------------------------------------------------
# 混雑クールダウン（429 のあと arXiv を呼ばない窓）
# ---------------------------------------------------------------------------


def _cooldown_seconds() -> float:
    """クールダウンの長さ（秒）。0 以下ならクールダウンなし。

    正本は env ``ARXIV_RATE_LIMIT_COOLDOWN_SECONDS``（``core.config``）。
    設定を読めない場合は :data:`DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS` に倒す
    （抑制を外して混雑中に呼び続けるより、抑制側で誤る方が安全側）。
    """
    try:
        from core.config import get_settings  # 遅延 import（core の純粋性を保つ）

        value = float(get_settings().arxiv_rate_limit_cooldown_seconds)
    except Exception:  # pragma: no cover - 設定読み込みの異常系
        logger.debug("falling back to the default arXiv cooldown", exc_info=True)
        return DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
    if value <= 0:
        return 0.0
    return value


def _begin_cooldown() -> None:
    """429 を受けた事実を記録し、以後しばらく arXiv を呼ばないようにする。"""
    global _cooldown_until
    seconds = _cooldown_seconds()
    if seconds <= 0:
        # 0 = 抑制なし（運用で明示的に切った場合）。窓を作らない。
        return
    with _throttle_lock:
        _cooldown_until = time.monotonic() + seconds


def cooldown_remaining_seconds() -> float:
    """クールダウンの残り秒数（窓が無ければ 0.0）。

    **API レスポンス・UI に載せない**（数値は出さない — PD4 / PR2 の規律）。
    運用ログと本モジュール内の判定のためだけの読み取り。
    """
    with _throttle_lock:
        until = _cooldown_until
    if until is None:
        return 0.0
    remaining = until - time.monotonic()
    return remaining if remaining > 0 else 0.0


def cooldown_active() -> bool:
    """いま arXiv への問い合わせを止めている最中か。"""
    return cooldown_remaining_seconds() > 0


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _api_url() -> str:
    """API のエンドポイント URL（宛先は定数ホストに固定 — PD7）。"""
    return f"https://{ARXIV_API_HOST}/api/query"


def _http_get(params: dict, timeout: float) -> str:
    """arXiv API へ1回だけ GET する（クールダウン検査 + スロットル込み）。本文文字列を返す。

    直前に 429 を受けている間は **HTTP を出さずに** :class:`ArxivRateLimitedError`
    を投げる（リトライではなく抑制 — モジュール docstring 参照）。
    """
    if cooldown_active():
        raise ArxivRateLimitedError(RATE_LIMIT_COOLDOWN_MESSAGE)

    _throttle()
    try:
        response = requests.get(_api_url(), params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise ArxivApiError("arXiv への接続に失敗しました") from exc

    if response.status_code == RATE_LIMITED_STATUS:
        # 混雑は「接続できない」と別の事実にする（時間をおけば通る — PD7）。
        # ここで窓を立て、以後しばらくは呼ばない（人の操作でブロックを延ばさない）。
        _begin_cooldown()
        raise ArxivRateLimitedError("arXiv 側が混雑しています")
    if response.status_code != 200:
        raise ArxivApiError("arXiv からの応答を取得できませんでした")

    return response.text


# ---------------------------------------------------------------------------
# Atom パース
# ---------------------------------------------------------------------------


def _text_of(node) -> str:
    if node is None:
        return ""
    return " ".join((node.text or "").split())


def _entry_from_element(element) -> Optional[ArxivEntry]:
    raw_id = _text_of(element.find("atom:id", _NS))
    arxiv_id, version = split_arxiv_ref(raw_id)
    if not arxiv_id:
        # ID を正規化できない項目は重複判定も見送り記録もできないため落とす
        # （黙殺せず debug に残す）。
        logger.debug("skipping arXiv entry with unparsable id: %r", raw_id)
        return None

    authors = [
        _text_of(name)
        for name in element.findall("atom:author/atom:name", _NS)
        if _text_of(name)
    ]
    categories = [
        str(cat.get("term") or "").strip()
        for cat in element.findall("atom:category", _NS)
        if str(cat.get("term") or "").strip()
    ]

    primary = element.find("arxiv:primary_category", _NS)
    primary_category = str(primary.get("term") or "").strip() if primary is not None else ""

    pdf_url = ""
    abs_url = ""
    for link in element.findall("atom:link", _NS):
        href = str(link.get("href") or "").strip()
        if not href:
            continue
        if str(link.get("title") or "").lower() == "pdf" or str(link.get("type") or "") == "application/pdf":
            pdf_url = href
        elif str(link.get("rel") or "") == "alternate":
            abs_url = href

    return ArxivEntry(
        arxiv_id=arxiv_id,
        version=version,
        title=_text_of(element.find("atom:title", _NS)),
        authors=authors,
        summary=_text_of(element.find("atom:summary", _NS)),
        categories=categories,
        primary_category=primary_category or None,
        published=_text_of(element.find("atom:published", _NS)),
        updated=_text_of(element.find("atom:updated", _NS)),
        pdf_url=pdf_url or pdf_url_for(arxiv_id),
        abs_url=abs_url or abs_url_for(arxiv_id),
    )


def parse_atom(payload: str) -> tuple[int, list[ArxivEntry]]:
    """arXiv API の Atom フィードを ``(totalResults, entries)`` へ変換する。

    Raises:
        ArxivApiError: XML として解釈できない。
    """
    try:
        root = ElementTree.fromstring(payload or "")
    except ElementTree.ParseError as exc:
        raise ArxivApiError("arXiv からの応答を解釈できませんでした") from exc

    total = 0
    total_node = root.find("opensearch:totalResults", _NS)
    if total_node is not None:
        try:
            total = int(_text_of(total_node) or 0)
        except (TypeError, ValueError):
            total = 0

    entries: list[ArxivEntry] = []
    for element in root.findall("atom:entry", _NS):
        entry = _entry_from_element(element)
        if entry is not None:
            entries.append(entry)

    # totalResults が欠けている応答でも、取り出せた件数までは正直に返す。
    if total <= 0 and entries:
        total = len(entries)
    return (total, entries)


# ---------------------------------------------------------------------------
# 検索
# ---------------------------------------------------------------------------


def search(
    query: str,
    *,
    start: int = 0,
    max_results: int = 50,
    sort_by: str = "submittedDate",
    sort_order: str = "descending",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, list[ArxivEntry]]:
    """arXiv API を検索し ``(totalResults, entries)`` を返す。

    Args:
        query: arXiv の ``search_query`` 文字列（組み立ては
            ``core/paper_discovery/search.py::build_search_query``）。
        start: ページング開始位置（0 以上）。
        max_results: 取得件数（1〜:data:`MAX_RESULTS_LIMIT` に丸める）。
        sort_by / sort_order: 並び順（語彙外は既定値へ落とす）。
        timeout: HTTP タイムアウト（秒）。

    Raises:
        ArxivApiError: 空クエリ・接続失敗・非200・パース不能
            （混雑 = HTTP 429 は部分型 :class:`ArxivRateLimitedError`。直前の 429 で
            クールダウン中は HTTP を出さずに同じ型で諦める）。
    """
    search_query = " ".join(str(query or "").split())
    if not search_query:
        # 条件ゼロで API を叩くと分野と無関係な全件が返るため、呼ばない（PD6）。
        raise ArxivApiError("検索条件が指定されていません")

    try:
        start_value = max(0, int(start))
    except (TypeError, ValueError):
        start_value = 0
    try:
        count = int(max_results)
    except (TypeError, ValueError):
        count = 50
    count = max(1, min(count, MAX_RESULTS_LIMIT))

    params = {
        "search_query": search_query,
        "start": start_value,
        "max_results": count,
        "sortBy": sort_by if sort_by in SORT_BY_VALUES else "submittedDate",
        "sortOrder": sort_order if sort_order in SORT_ORDER_VALUES else "descending",
    }

    payload = _http_get(params, timeout)
    return parse_atom(payload)


def fetch_by_ids(
    ids,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[ArxivEntry]:
    """arXiv ID を指定してメタデータを1コールで取得する（論文レーダー §5.4）。

    ``search()`` と同じ :func:`_throttle` / タイムアウト / :func:`parse_atom` を通り、
    宛先は定数ホストのまま（PR6 / PD7）。ID は
    :func:`~core.paper_discovery.schema.normalize_arxiv_id` で正規化してから
    ``id_list`` に載せる（版違い・URL 表記のゆれを吸収する）。

    Args:
        ids: arXiv ID / URL の列。先頭 :data:`MAX_ID_LIST` 件だけを使う
            （超過分は黙って捨てるのではなく、呼び出し側が上限を持って渡す前提。
            防波堤としてここでも切り詰める）。
        timeout: HTTP タイムアウト（秒）。

    Returns:
        取得できた :class:`ArxivEntry` の列。引けなかった ID は結果に現れない
        （呼び出し側が「引けなかった事実」を正直に返す — 黙って埋めない）。
        正規化できる ID が1件も無ければ **API を呼ばず** 空リスト。

    Raises:
        ArxivApiError: 接続失敗・非200・パース不能
            （混雑 = HTTP 429 は部分型 :class:`ArxivRateLimitedError`。直前の 429 で
            クールダウン中は HTTP を出さずに同じ型で諦める）。
    """
    normalized: list[str] = []
    for ref in ids or ():
        arxiv_id = normalize_arxiv_id(ref)
        if arxiv_id and arxiv_id not in normalized:
            normalized.append(arxiv_id)
        if len(normalized) >= MAX_ID_LIST:
            break

    if not normalized:
        # 指定ゼロで API を叩くと全件が返る（search() の空クエリと同じ理由 — PD6）。
        return []

    params = {
        "id_list": ",".join(normalized),
        "start": 0,
        "max_results": len(normalized),
    }
    payload = _http_get(params, timeout)
    return parse_atom(payload)[1]
