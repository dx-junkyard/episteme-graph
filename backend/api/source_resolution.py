"""arXiv 論文の取得形式の解決 — TeX ソースを既定にし、使えなければ PDF へ同期で倒す。

設計正本: ``docs/features/paper_radar_design.md`` §15.9（2026-09-30 既定を TeX に変更）/
``docs/features/url_material_upload_design.md`` の追補（arXiv URL の書き換えと
フォールバック）。

**なぜ API 層にあるか**: 取得は ``core.url_fetch``（許可リスト照合つき）の唯一の
公開関数を通し、TeX として読めるかの判定は ``core.document_pipeline.tex_archive``
（純関数・ネットワークなし）を使う。``core/paper_discovery/`` は ``url_fetch`` を
import しない規約（PD2 のガードレール）を持ち、``url_fetch`` は「1 URL を取る」以上の
方針を持たない。両者を組み合わせる方針（どの URL を何回取るか）はここに閉じる。

不変条項（P-0007 — 外部 API は一連の操作で 1〜2 回）:

- **1件あたり arXiv への取得は最大 2 回**。``/src/<id>`` が PDF を返せば 1 回で終わり
  （PDF のみの投稿は arXiv が ``/src/`` で PDF を返す — マジックで判定）、TeX として
  読めない gzip・404 等のときだけ ``/pdf/<id>`` をもう 1 回取る。
- **HTTP 429（アクセス制限）を受けたら 2 回目を取らない**。制限中に叩き続けると
  ブロックが延びる（論文レーダー設計書 §14）。事実文は
  :data:`DETAIL_ARXIV_RATE_LIMITED`。
- 許可リスト由来の拒否（未設定・不許可ドメイン・内部アドレス）では PDF に倒さない
  （同じ理由で失敗するだけで、呼び出し回数だけが増える）。
- ``documents.source_url`` には**実際にバイト列を返した URL** を記帳する
  （:attr:`ResolvedSource.source_url`）。
- LLM を呼ばない。``arxiv_client``（検索 API）を import しない（取り込み worker が
  使うため — PD1）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Sequence
from urllib.parse import urlparse, urlunparse

from core import url_fetch
from core.document_pipeline.tex_archive import load_tex_archive
from core.paper_discovery import schema as pd_schema

logger = logging.getLogger(__name__)

#: 1件あたりの arXiv への取得回数の上限（P-0007）。テストがこの値で呼び出し回数を縛る。
MAX_FETCH_ATTEMPTS = 2

#: arXiv の配信ホスト（URL 指定取得で「arXiv の論文 URL」とみなすホスト）。
ARXIV_URL_HOSTS = ("arxiv.org", "www.arxiv.org", "export.arxiv.org")

#: TeX ソースを表すパス（``/src/<id>`` / ``/e-print/<id>``）。
_TEX_PATH_PREFIXES = ("src", "e-print")

#: arXiv からアクセスを制限されたときの事実文。先頭の文は論文レーダーのブロック表示
#: （``radar.NOTE_ARXIV_BLOCKED``）と逐語で揃える（同じ事実を言い換えない — テストで固定）。
#: 数値（残り時間・回数）は書かない（PR2）。
DETAIL_ARXIV_RATE_LIMITED = (
    "arXiv からアクセスを制限されています。"
    "制限中は PDF への切り替えも試みていません。"
    "時間をおいてから、もう一度取り込んでください。"
)


@dataclass
class ResolvedSource:
    """取得結果と、その取得がどう決まったかの事実。"""

    fetched: url_fetch.FetchedSource
    #: 実際にバイト列を返した URL（``documents.source_url`` に記帳する値）。
    source_url: str
    #: 実バイトから見た形式（``tex`` / ``pdf``）。教員の選択ではなく結果。
    effective_format: str
    #: TeX を取りに行って使えず、PDF を取り直したか。
    fell_back: bool = False
    #: 取りに行った URL の列（呼び出し回数の監査用。上限は :data:`MAX_FETCH_ATTEMPTS`）。
    attempts: list[str] = field(default_factory=list)


def _format_of(fetched: url_fetch.FetchedSource) -> str:
    return (
        pd_schema.SOURCE_FORMAT_TEX
        if fetched.source_kind == "tex_archive"
        else pd_schema.SOURCE_FORMAT_PDF
    )


def _is_readable_tex(fetched: url_fetch.FetchedSource) -> bool:
    """gzip が TeX ソースとして読めるか（純関数・ネットワークなし）。

    解析パイプラインは受理後に非同期で同じ関数を呼ぶ。ここで先に確かめないと、
    TeX を含まない gzip は 202 を返した後で解析が失敗し、PDF に倒す機会が無い。
    """
    try:
        load_tex_archive(fetched.content, source_file=fetched.filename or "source.tar.gz")
    except Exception:  # noqa: BLE001 — tarfile / gzip / ValueError のどれでも「読めない」
        return False
    return True


def _on_host(url: str, host: Optional[str]) -> str:
    """URL のホストだけを ``host`` に差し替える（パスは変えない）。

    教員が貼った ``export.arxiv.org/abs/...`` を ``/src/`` に書き換えるとき、ホストまで
    ``arxiv.org`` に変えると、許可リストが ``export.arxiv.org`` だけのときに元の URL なら
    取れたのに書き換えた URL は不許可になる（2026-10-01 レビュー m10）。書き換えるのは
    パスだけにし、ホストは貼られたものを保つ。
    """
    if not host:
        return url
    parsed = urlparse(url)
    return urlunparse(parsed._replace(netloc=host))


def _rate_limited(exc: url_fetch.RateLimitedError) -> url_fetch.RateLimitedError:
    err = url_fetch.RateLimitedError(DETAIL_ARXIV_RATE_LIMITED)
    err.__cause__ = exc
    return err


def fetch_arxiv_source(
    arxiv_id: str,
    *,
    source_format: Optional[str],
    allowed_domains: Sequence[str],
    host: Optional[str] = None,
) -> ResolvedSource:
    """arXiv の論文を形式指定で取得する（TeX は PDF への同期フォールバック付き）。

    Args:
        arxiv_id: arXiv ID（version 付きでもよい。URL の組み立てにそのまま使う）。
        source_format: ``tex`` / ``pdf``。語彙外・空は ``DEFAULT_SOURCE_FORMAT``。
        allowed_domains: 許可ドメイン（``url_fetch`` へそのまま渡す）。
        host: 取得先ホスト（URL 指定取得で貼られた URL のホストを保つ — m10）。
            ``None`` は ``arxiv.org``（``pd_schema`` の既定）。

    Raises:
        url_fetch.UrlFetchError: 最終的に取得できなかった（事実文は ``str(exc)``）。
        url_fetch.RateLimitedError: arXiv が 429 を返した（2 回目は試みていない）。
    """
    fmt = pd_schema.normalize_source_format(source_format) or pd_schema.DEFAULT_SOURCE_FORMAT
    attempts: list[str] = []

    if fmt == pd_schema.SOURCE_FORMAT_PDF:
        pdf_url = _on_host(pd_schema.pdf_url_for(arxiv_id), host)
        attempts.append(pdf_url)
        try:
            fetched = url_fetch.fetch_source_from_url(pdf_url, allowed_domains)
        except url_fetch.RateLimitedError as exc:
            raise _rate_limited(exc) from exc
        return ResolvedSource(
            fetched=fetched, source_url=pdf_url, effective_format=_format_of(fetched),
            fell_back=False, attempts=attempts,
        )

    src_url = _on_host(pd_schema.src_url_for(arxiv_id), host)
    attempts.append(src_url)
    try:
        fetched = url_fetch.fetch_source_from_url(src_url, allowed_domains)
    except url_fetch.RateLimitedError as exc:
        # 制限中に PDF を取りに行くとブロックが延びる — 2 回目を取らない。
        raise _rate_limited(exc) from exc
    except (
        url_fetch.NoDomainsConfiguredError,
        url_fetch.DomainNotAllowedError,
        url_fetch.PrivateAddressError,
    ):
        # 許可リスト由来の拒否は PDF でも同じ理由で落ちる — 呼び出しを増やさない。
        raise
    except url_fetch.UrlFetchError as exc:
        logger.info("arXiv TeX source unavailable (%s) for %s; falling back to PDF",
                    type(exc).__name__, arxiv_id)
        fetched = None
    else:
        if fetched.source_kind != "tex_archive":
            # PDF のみの投稿: arXiv が /src/ で PDF を返す。1 回で終わり。
            return ResolvedSource(
                fetched=fetched, source_url=src_url, effective_format=_format_of(fetched),
                fell_back=False, attempts=attempts,
            )
        if _is_readable_tex(fetched):
            return ResolvedSource(
                fetched=fetched, source_url=src_url,
                effective_format=pd_schema.SOURCE_FORMAT_TEX,
                fell_back=False, attempts=attempts,
            )
        logger.info("arXiv TeX source for %s is not a readable TeX archive; falling back to PDF",
                    arxiv_id)

    pdf_url = _on_host(pd_schema.pdf_url_for(arxiv_id), host)
    attempts.append(pdf_url)
    try:
        fetched = url_fetch.fetch_source_from_url(pdf_url, allowed_domains)
    except url_fetch.RateLimitedError as exc:
        raise _rate_limited(exc) from exc
    return ResolvedSource(
        fetched=fetched, source_url=pdf_url, effective_format=_format_of(fetched),
        fell_back=True, attempts=attempts,
    )


def arxiv_ref_from_url(url: str) -> Optional[tuple[str, Optional[str]]]:
    """URL が arXiv の論文 URL なら ``(ID（版付きなら版付き）, パスが示す形式)`` を返す。

    パスが示す形式は ``/src/`` ``/e-print/`` → ``tex``、``/pdf/`` → ``pdf``、
    それ以外（``/abs/`` 等）→ ``None``（形式の指定なし）。arXiv 以外のホストや
    ID を解釈できない URL は ``None``。
    """
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in ARXIV_URL_HOSTS:
        return None
    base, version = pd_schema.split_arxiv_ref(url.strip())
    if not base:
        return None
    ref = f"{base}v{version}" if version else base
    first = next((p for p in (parsed.path or "").split("/") if p), "").lower()
    if first in _TEX_PATH_PREFIXES:
        path_format: Optional[str] = pd_schema.SOURCE_FORMAT_TEX
    elif first == "pdf":
        path_format = pd_schema.SOURCE_FORMAT_PDF
    else:
        path_format = None
    return ref, path_format


def fetch_url_source(
    url: str,
    *,
    source_format: Optional[str],
    allowed_domains: Sequence[str],
) -> ResolvedSource:
    """URL 指定取得の入口。arXiv の論文 URL なら形式を解決して取り直す。

    - arXiv の論文 URL（abs / pdf / src）: 明示の ``source_format`` > URL のパスが示す
      形式 > ``DEFAULT_SOURCE_FORMAT`` の順で形式を決め、:func:`fetch_arxiv_source` へ。
      書き換えるのはパスだけで、ホストは貼られた URL のものを保つ（許可リストの照合を
      書き換えで変えない — m10）。
    - それ以外: 従来どおり URL をそのまま 1 回取得する。
    """
    ref = arxiv_ref_from_url(url)
    if ref is None:
        target = (url or "").strip()
        fetched = url_fetch.fetch_source_from_url(url, allowed_domains)
        return ResolvedSource(
            fetched=fetched, source_url=target, effective_format=_format_of(fetched),
            fell_back=False, attempts=[target],
        )
    arxiv_ref, path_format = ref
    fmt = pd_schema.normalize_source_format(source_format) or path_format
    # 書き換えるのはパスだけ。ホスト（例 export.arxiv.org）は貼られたものを保つ（m10）。
    host = (urlparse(url.strip()).hostname or "").lower() or None
    return fetch_arxiv_source(
        arxiv_ref, source_format=fmt, allowed_domains=allowed_domains, host=host,
    )
