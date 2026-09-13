"""URL指定による教材取得 — 取得先ドメイン許可リストと SSRF ガード付きダウンローダ。

DDL は ``backend/db/070_url_fetch_domains.sql``。API 層は
``backend/api/routes/admin.py``（``/api/admin/url-fetch-domains`` /
``/api/admin/materials/upload-from-url``）。

このモジュールが担うのは2つ:

1. **許可リストの CRUD**（``url_fetch_domains`` テーブル）。ドメイン文字列の正規化
   （:func:`normalize_domain`）と照合規則（:func:`domain_allowed`）の正本。
2. **取得**（:func:`fetch_source_from_url`）。許可リスト照合 → DNS 解決結果の
   IP 検査 → 手動リダイレクト追跡（各ホップで再検査）→ サイズ上限つき
   ストリーム読み出し → 実バイトのマジックによる形式判定。

設計方針（不変条項）:

- **FastAPI 非 import**（開発ルール2）。HTTP ステータスへの写像は API 層の責務で、
  ここでは :class:`UrlFetchError` の派生型で理由を表現する。
- **許可リスト照合なしに取得できる公開関数を作らない。** :func:`fetch_source_from_url`
  は ``allowed_domains`` を必須引数に取り、空リストは
  :class:`NoDomainsConfiguredError`（機能未設定）として拒否する。初期状態は空 =
  機能無効（fail-closed）。
- **リダイレクトを HTTP クライアントに自動追跡させない。** ``allow_redirects=False``
  で1ホップずつ取得し、**各ホップでドメイン照合と IP 検査をやり直す**
  （許可ドメインから private IP へ 302 させる SSRF を塞ぐ唯一の方法）。
- **形式判定は実バイトのマジックのみ。** URL の拡張子・``Content-Type``
  ヘッダは攻撃者/配信側が自由に名乗れるので信用しない。
- 環境変数を読まない（上限値はモジュール定数）。
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence
from urllib.parse import unquote, urljoin, urlparse, urlsplit

import requests
import urllib3.util
from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

#: ダウンロードのサイズ上限（バイト）。ストリーム読み出し中に強制する。
MAX_FETCH_BYTES = 100 * 1024 * 1024  # 100MB

#: 1ホップあたりの HTTP タイムアウト（秒）。
FETCH_TIMEOUT_SECONDS = 60

#: 手動で追跡するリダイレクトの最大ホップ数。
MAX_REDIRECTS = 5

#: 許容する URL scheme。
ALLOWED_SCHEMES = ("http", "https")

#: ストリーム読み出しのチャンクサイズ。
_CHUNK_BYTES = 64 * 1024

#: PDF のマジックバイト。
_MAGIC_PDF = b"%PDF"

#: gzip のマジックバイト（arXiv の TeX ソースは .tar.gz）。
_MAGIC_GZIP = b"\x1f\x8b"

#: ホスト名の妥当性（ラベルは英数とハイフン、ハイフンで開始/終了しない）。
_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

#: Content-Disposition の filename パラメータ。
_CD_FILENAME_STAR_RE = re.compile(r"filename\*\s*=\s*([^;]+)", re.IGNORECASE)
_CD_FILENAME_RE = re.compile(r"filename\s*=\s*(\"[^\"]*\"|[^;]+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 例外（API 層が型で HTTP ステータスへ写像する）
# ---------------------------------------------------------------------------


class UrlFetchError(Exception):
    """URL 取得にまつわる失敗の基底。"""


class NoDomainsConfiguredError(UrlFetchError):
    """許可リストが空 = 機能が未設定（管理者の登録待ち）。"""


class DomainNotAllowedError(UrlFetchError):
    """URL（またはリダイレクト先）のドメインが許可リストにない。"""


class PrivateAddressError(UrlFetchError):
    """名前解決の結果が private / loopback / link-local 等の内部アドレスだった。"""


class FetchFailedError(UrlFetchError):
    """ネットワーク障害・HTTP エラー・リダイレクト過多などの取得失敗。"""


class UnsupportedContentError(UrlFetchError):
    """取得できたが PDF でも gzip アーカイブでもなかった。"""


class TooLargeError(UrlFetchError):
    """サイズ上限（:data:`MAX_FETCH_BYTES`）を超えた。"""


@dataclass
class FetchedSource:
    """取得した教材ソース。

    ``source_kind`` は既存アップロード経路と同じ語彙（``"pdf"`` / ``"tex_archive"``）。
    """

    content: bytes
    source_kind: str
    filename: str


# ---------------------------------------------------------------------------
# ドメインの正規化・照合
# ---------------------------------------------------------------------------


def normalize_domain(raw: str) -> str:
    """許可リストへ登録するドメイン文字列を正規化する。

    ``https://ARXIV.org/pdf/`` / ``arxiv.org:443`` / ``arxiv.org.`` のような入力を
    ``arxiv.org`` へ畳む。scheme・パス・ポート・ユーザ情報・前後の空白を落とし、
    小文字化したうえでホスト名として妥当かを検証する。

    Raises:
        ValueError: 空・IP アドレス・ラベル規則違反など、ドメインとして扱えない入力。
    """
    if not isinstance(raw, str):
        raise ValueError("domain must be a string")

    text = raw.strip()
    if not text:
        raise ValueError("domain must not be empty")

    # scheme 付きなら urlsplit に解釈させる。scheme 無しの "arxiv.org/pdf" も
    # "//" を前置すれば netloc として解釈される。
    candidate = text if "://" in text else "//" + text
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:  # pragma: no cover — urlsplit は滅多に投げない
        raise ValueError(f"invalid domain: {raw!r}") from exc

    if parts.scheme and parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError(f"unsupported scheme in domain: {raw!r}")

    host = parts.hostname or ""
    host = host.strip().lower().rstrip(".")
    if not host:
        raise ValueError(f"invalid domain: {raw!r}")

    if len(host) > 253:
        raise ValueError(f"domain is too long: {raw!r}")

    # IP アドレスは許可リストの単位にしない（SSRF ガードで internal を弾く方針と
    # 二重管理になり、ホスト名ベースの照合規則も適用できないため）。
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError(f"IP addresses are not allowed as domains: {raw!r}")

    labels = host.split(".")
    if len(labels) < 2:
        raise ValueError(f"domain must have at least two labels: {raw!r}")
    for label in labels:
        if not _DOMAIN_LABEL_RE.match(label):
            raise ValueError(f"invalid domain label {label!r} in {raw!r}")

    return host


def domain_allowed(host: str, domains: Iterable[str]) -> bool:
    """``host`` が許可リスト ``domains`` のいずれかに一致するか。

    一致は「完全一致」または「**ドット境界の**サブドメイン一致」のみ:

    - ``arxiv.org`` は ``arxiv.org`` / ``export.arxiv.org`` に一致する
    - ``arxiv.org`` は ``evilarxiv.org`` / ``arxiv.org.evil.com`` には一致しない
      （前者は接尾辞一致の罠、後者はドメイン所有者が違う）
    """
    if not host:
        return False
    normalized_host = host.strip().lower().rstrip(".")
    if not normalized_host:
        return False

    for entry in domains or ():
        if not entry:
            continue
        allowed = str(entry).strip().lower().rstrip(".")
        if not allowed:
            continue
        if normalized_host == allowed:
            return True
        if normalized_host.endswith("." + allowed):
            return True
    return False


# ---------------------------------------------------------------------------
# 許可リストの CRUD（session は呼び出し側が管理する: commit / close は API 層）
# ---------------------------------------------------------------------------


def list_url_fetch_domains(session) -> list[dict]:
    """許可ドメインをドメイン名昇順で返す。"""
    rows = session.execute(
        sa_text("SELECT domain, created_at FROM url_fetch_domains ORDER BY domain ASC")
    ).fetchall()
    return [
        {"domain": row[0], "created_at": _iso(row[1])}
        for row in rows
    ]


def add_url_fetch_domain(session, domain: str, user_id) -> str:
    """許可ドメインを登録する（冪等 upsert）。正規化後のドメインを返す。

    Raises:
        ValueError: ``domain`` がドメインとして妥当でない（API 層で 422）。
    """
    normalized = normalize_domain(domain)
    session.execute(
        sa_text("""
            INSERT INTO url_fetch_domains (domain, added_by)
            VALUES (:domain, CAST(:added_by AS uuid))
            ON CONFLICT (domain) DO NOTHING
        """),
        {"domain": normalized, "added_by": str(user_id) if user_id else None},
    )
    return normalized


def remove_url_fetch_domain(session, domain: str) -> bool:
    """許可ドメインを解除する。削除した場合 True、行が無ければ False。"""
    try:
        normalized = normalize_domain(domain)
    except ValueError:
        # 不正な文字列は「登録されていない」と同義（存在しない → False）。
        return False
    row = session.execute(
        sa_text("DELETE FROM url_fetch_domains WHERE domain = :domain RETURNING domain"),
        {"domain": normalized},
    ).fetchone()
    return row is not None


def _iso(value) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# SSRF ガード
# ---------------------------------------------------------------------------


def _is_public_address(ip: ipaddress._BaseAddress) -> bool:
    """外部へ出て良いアドレスか（内部レンジを全部弾く）。"""
    # IPv4-mapped / 6to4 等でラップされた内部アドレスも展開して検査する。
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _is_public_address(mapped)
    sixtofour = getattr(ip, "sixtofour", None)
    if sixtofour is not None:
        return _is_public_address(sixtofour)

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return False
    # 上の列挙は IANA 特殊用途レジストリの区分が変わると穴があく（実例: Python 3.13 で
    # 共有アドレス空間 100.64.0.0/10（CGNAT）が ``is_private`` から外れ、上の条件を
    # すべてすり抜けるようになった）。``is_global`` は同レジストリを直接参照するため、
    # 個別列挙の取りこぼしをまとめて塞ぐ最後の関門として併用する（fail-closed）。
    return bool(ip.is_global)


def _assert_public_host(host: str) -> None:
    """``host`` の名前解決結果が**すべて**外部アドレスであることを検証する。

    1つでも内部アドレスが混ざっていたら拒否する（DNS ラウンドロビンによる
    すり抜けを防ぐ）。
    """
    if not host:
        raise FetchFailedError("URL にホスト名が含まれていません")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchFailedError("URL のホスト名を解決できませんでした") from exc
    except OSError as exc:  # pragma: no cover — 環境依存
        raise FetchFailedError("URL のホスト名を解決できませんでした") from exc

    if not infos:
        raise FetchFailedError("URL のホスト名を解決できませんでした")

    for info in infos:
        sockaddr = info[4]
        raw_addr = str(sockaddr[0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw_addr)
        except ValueError:  # pragma: no cover — getaddrinfo が非 IP を返すことはない
            raise PrivateAddressError("URL の接続先アドレスを検証できませんでした")
        if not _is_public_address(ip):
            # detail に IP を載せない（内部ネットワーク構成の漏洩を避ける）。
            raise PrivateAddressError("URL の接続先が内部アドレスのため取得できません")


def _parsed_hostname(url: str) -> str:
    """``urlparse`` でホスト名を取り出す（不正 URL は :class:`DomainNotAllowedError`）。

    ``parsed.hostname`` は角括弧の対応が取れていない URL（``https://arxiv.org]/x`` 等）で
    ``ValueError`` を送出する。ここで畳んでおかないと API 層の
    :class:`UrlFetchError` 写像を素通りして 500 になる。
    """
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError as exc:
        raise DomainNotAllowedError("URLの形式が正しくありません") from exc


def _assert_single_authority(url: str, host: str) -> None:
    """検証したホストと、HTTP クライアントが実際に接続するホストの一致を強制する。

    :func:`urlparse`（RFC 3986: userinfo は最後の ``@`` で分割）と、requests/urllib3 が
    送信時に使う ``urllib3.util.parse_url``（WHATWG: ``\\`` も authority の終端）は
    同じ URL から**別のホスト**を導きうる。この差分があると、許可リストと IP 検査は
    片方のホストに対して行われ、ソケットはもう片方へ繋がる。

        https://169.254.169.254\\@arxiv.org/x
            urlparse   → arxiv.org        （許可リスト通過・公開 IP と判定）
            urllib3    → 169.254.169.254  （実際の接続先）

    そこで送信側パーサでも解析し、ホストが一致しなければ拒否する（fail-closed）。
    個別文字の denylist ではなく**両パーサの合意**を要求することで、将来のパーサ差分にも
    そのまま効く。
    """
    try:
        sent_host = urllib3.util.parse_url(url).host
    except Exception as exc:  # noqa: BLE001 — LocationParseError 等は不正 URL として畳む
        raise DomainNotAllowedError("URLの形式が正しくありません") from exc

    normalized_sent = str(sent_host or "").lower().rstrip(".").strip("[]")
    if normalized_sent != host.strip("[]"):
        # detail に解析結果のホストを載せない（内部到達先の示唆を避ける）。
        raise DomainNotAllowedError("URLの形式が正しくありません")


def _validated_target(url: str, allowed_domains: Sequence[str]) -> str:
    """1ホップ分の URL を検証し、ホスト名を返す（scheme / 許可リスト / IP）。"""
    parsed = urlparse(url) if _is_parseable(url) else None
    scheme = (parsed.scheme if parsed else "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise DomainNotAllowedError("http/https 以外の URL は取得できません")

    host = _parsed_hostname(url)
    # 許可リスト照合・IP 検査の前に、送信側パーサとホストが一致することを確かめる
    # （食い違ったまま先へ進むと、以降の検証が別のホストに対する検証になる）。
    _assert_single_authority(url, host)

    if not domain_allowed(host, allowed_domains):
        raise DomainNotAllowedError("このURLのドメインは許可されていません")

    _assert_public_host(host)
    return host


def _is_parseable(url: str) -> bool:
    try:
        urlparse(url)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 形式判定・ファイル名導出
# ---------------------------------------------------------------------------


def detect_source_kind(content: bytes) -> str:
    """**実バイトのマジックだけ**で形式を判定する。

    ``Content-Type`` や URL の拡張子には依存しない（arXiv の ``/pdf/<id>`` /
    ``/src/<id>`` はどちらも拡張子を持たないうえ、ヘッダは配信側が自由に名乗れる）。

    Raises:
        UnsupportedContentError: PDF でも gzip でもない。
    """
    if content.startswith(_MAGIC_PDF):
        return "pdf"
    if content.startswith(_MAGIC_GZIP):
        return "tex_archive"
    raise UnsupportedContentError(
        "取得したファイルはPDFでもTeXアーカイブ（.tar.gz）でもありません"
    )


def _filename_from_content_disposition(header: Optional[str]) -> str:
    if not header:
        return ""
    star = _CD_FILENAME_STAR_RE.search(header)
    if star:
        value = star.group(1).strip()
        # RFC 5987: charset'lang'percent-encoded-value
        if "''" in value:
            value = value.split("''", 1)[1]
        return unquote(value.strip().strip('"'))
    plain = _CD_FILENAME_RE.search(header)
    if plain:
        return plain.group(1).strip().strip('"')
    return ""


def _sanitize_filename(name: str) -> str:
    """パス区切り・制御文字を落とし、ファイル名として安全な形にする。"""
    cleaned = (name or "").replace("\\", "/").split("/")[-1]
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() and ch not in '"\r\n\t')
    cleaned = cleaned.strip().strip(".")
    return cleaned[:200]


def derive_filename(url: str, source_kind: str, content_disposition: Optional[str] = None) -> str:
    """``Content-Disposition`` → URL パス末尾 → ``"download"`` の順で名前を決め、
    ``source_kind`` に対応する拡張子を保証する。"""
    name = _sanitize_filename(_filename_from_content_disposition(content_disposition))
    if not name:
        path = urlparse(url).path or ""
        name = _sanitize_filename(unquote(path.rstrip("/").split("/")[-1]))
    if not name:
        name = "download"

    lower = name.lower()
    if source_kind == "tex_archive":
        if not (lower.endswith(".tar.gz") or lower.endswith(".tgz")):
            name = name + ".tar.gz"
    else:
        if not lower.endswith(".pdf"):
            name = name + ".pdf"
    return name


# ---------------------------------------------------------------------------
# 取得
# ---------------------------------------------------------------------------


def _read_limited(response) -> bytes:
    """サイズ上限を強制しながらレスポンス本文を読む。"""
    # Content-Length が上限超えを自己申告しているなら本文を読まずに落とす。
    declared = response.headers.get("Content-Length")
    if declared:
        try:
            if int(declared) > MAX_FETCH_BYTES:
                raise TooLargeError("ファイルサイズが上限を超えています")
        except (TypeError, ValueError):
            pass  # 壊れた Content-Length は無視し、ストリーム側の上限で守る

    buffer = bytearray()
    for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
        if not chunk:
            continue
        buffer.extend(chunk)
        if len(buffer) > MAX_FETCH_BYTES:
            raise TooLargeError("ファイルサイズが上限を超えています")
    return bytes(buffer)


def fetch_source_from_url(url: str, allowed_domains: Sequence[str]) -> FetchedSource:
    """許可ドメインの URL から教材ソースを取得する。

    Args:
        url: 取得先。http/https のみ。
        allowed_domains: 許可ドメイン（``url_fetch_domains.domain`` の値）。
            **必須引数**。空リストは機能未設定として拒否する（fail-closed）。

    Raises:
        NoDomainsConfiguredError: 許可リストが空。
        DomainNotAllowedError: scheme 不正、またはドメイン（リダイレクト先含む）が不許可。
        PrivateAddressError: 名前解決の結果が内部アドレス。
        FetchFailedError: 通信失敗・HTTP エラー・リダイレクト過多。
        TooLargeError: サイズ上限超過。
        UnsupportedContentError: PDF でも gzip アーカイブでもない。
    """
    domains = [d for d in (allowed_domains or ()) if d]
    if not domains:
        raise NoDomainsConfiguredError(
            "URLからの取得は、管理者が取得先ドメインを許可リストに登録すると利用できます"
        )

    if not isinstance(url, str) or not url.strip():
        raise DomainNotAllowedError("http/https 以外の URL は取得できません")

    current_url = url.strip()
    session = requests.Session()
    try:
        for _hop in range(MAX_REDIRECTS + 1):
            # 各ホップで scheme・許可リスト・IP を必ず検証し直す
            # （許可ドメインから内部アドレスへ 302 させる SSRF を塞ぐ要）。
            _validated_target(current_url, domains)

            try:
                response = session.get(
                    current_url,
                    stream=True,
                    timeout=FETCH_TIMEOUT_SECONDS,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                raise FetchFailedError("URLからの取得に失敗しました") from exc

            try:
                if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise FetchFailedError("URLからの取得に失敗しました")
                    current_url = urljoin(current_url, location)
                    continue

                if response.status_code != 200:
                    raise FetchFailedError("URLからの取得に失敗しました")

                # 本文読み出し中の失敗（read timeout・接続リセット・chunked 破損）も
                # UrlFetchError に畳む。session.get だけを包んでいると、ここから
                # requests の例外が素通りして API 層で 500 になる。
                try:
                    content = _read_limited(response)
                except requests.RequestException as exc:
                    raise FetchFailedError("URLからの取得に失敗しました") from exc
                content_disposition = response.headers.get("Content-Disposition")
            finally:
                try:
                    response.close()
                except Exception:  # pragma: no cover — close の失敗は握りつぶす
                    pass

            if not content:
                raise UnsupportedContentError(
                    "取得したファイルはPDFでもTeXアーカイブ（.tar.gz）でもありません"
                )

            source_kind = detect_source_kind(content)
            filename = derive_filename(current_url, source_kind, content_disposition)
            return FetchedSource(content=content, source_kind=source_kind, filename=filename)

        raise FetchFailedError("URLのリダイレクトが多すぎます")
    finally:
        try:
            session.close()
        except Exception:  # pragma: no cover
            pass
