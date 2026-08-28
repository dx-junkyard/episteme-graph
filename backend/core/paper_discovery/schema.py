"""論文ディスカバリー層 — 語彙・DTO・arXiv ID 正規化の正本。

設計正本: ``docs/features/paper_discovery_design.md``（不変条項 PD1〜PD8）。
DDL は ``backend/db/071_paper_discovery.sql``。

このモジュールが持つのは:

1. **語彙**（キーフレーズ供給元 / 候補の状態 / arXiv API の宛先ホスト）。他ファイルは
   語彙をリテラルで再定義せず、ここを参照する。
2. **:func:`normalize_arxiv_id`** — 生 ID / abs URL / pdf URL / 旧形式 ID を
   「**version サフィックスを除いた**正規化 ID」へ畳む唯一の正本。
   ``documents.source_url`` からの「取り込み済み」判定（PD5 の読み時導出）と
   見送り記録の主キーが同じ規則で揃うことがこの層の重複判定の土台になる。
   **version 違い（v1/v2）は同一論文とみなす**（設計書 §4.1）。
3. **:class:`ArxivEntry`** — arXiv API から取り出した論文メタデータの DTO。
   JSON シリアライズ可能（:meth:`ArxivEntry.to_dict`）。

設計方針:

- FastAPI 非 import・``core.llm`` 非 import（開発ルール2 / PD の LLM 0回）。
- 数値スコア・類似度をここに置かない（PD4）。DTO が持つのは arXiv が公開している
  メタデータそのものだけで、機械が付けた点数は持たない。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

#: キーフレーズの供給元語彙（PD3 — 各フレーズがどこから来たかを必ず明示する）。
#: ``skeleton`` = atlas 骨格の概念ラベル / ``cartridge`` = カートリッジ ontology /
#: ``alias`` = 教員が確定した骨格ノードの別名
#: （``docs/features/atlas_vector_anchoring_design.md`` §7 の還流2）/
#: ``component`` = 承認済み理論部品のラベル / ``manual`` = 教員が自分で足したもの。
KEYPHRASE_SOURCES = ("skeleton", "cartridge", "component", "alias", "manual")

#: 供給元が不明・不正だったキーフレーズの落とし所（教員が足したものと同じ扱い）。
DEFAULT_KEYPHRASE_SOURCE = "manual"

#: 候補1件の状態（読み時導出。テーブルには保存しない — PD5）。
CANDIDATE_STATUSES = ("new", "ingested", "dismissed")

#: 論文レーダーの距離語彙（正本: ``docs/features/paper_radar_design.md`` §5.1）。
#: ``near`` = カテゴリ + キーフレーズで絞る / ``mid`` / ``far`` = カテゴリのみで網を張り、
#: seed 教材からの意味的な遠さは第2層の帯分け（``ranking.band_candidates``）が担う。
#: 距離帯の**表示ラベル**は語彙ではなく段階ラベルなので、正本は
#: ``core.label_vocab.RADAR_DISTANCE_SCALE`` 側にある（PR2 — 閾値・文字列をここに置かない）。
RADAR_DISTANCES = ("near", "mid", "far")

#: arXiv API の宛先ホスト（PD7 — 呼び出し側から URL を渡せない固定値）。
ARXIV_API_HOST = "export.arxiv.org"

#: 論文ページ / PDF の配信ホスト（``documents.source_url`` に保存する URL の組み立て）。
ARXIV_SITE_HOST = "arxiv.org"

#: 引用グラフ API の宛先ホスト（Phase 3 / 設計書 §6。PD7 — arXiv と同じ規律で固定値。
#: スロットルは**ホストごとに独立**なので arxiv_client とは共有しない）。
SEMANTIC_SCHOLAR_API_HOST = "api.semanticscholar.org"


# ---------------------------------------------------------------------------
# arXiv ID の正規化
# ---------------------------------------------------------------------------

#: 2007年4月以降の ID（``2608.20293`` / 4桁 YYMM + 4〜5桁連番）。
_NEW_ID_RE = re.compile(r"^(\d{4}\.\d{4,5})(?:v(\d+))?$")

#: 旧形式 ID（``hep-ph/9901234`` / ``cond-mat.str-el/0512345``）。
_OLD_ID_RE = re.compile(
    r"^([a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)?/\d{7})(?:v(\d+))?$",
    re.IGNORECASE,
)

#: URL パスの先頭に付く配信形式のプレフィックス（ID の一部ではない）。
_PATH_PREFIXES = ("abs", "pdf", "src", "format", "e-print", "ps", "dvi", "html")

#: 末尾に付きうる拡張子（``/pdf/2608.20293v2.pdf`` のような形）。
_TRAILING_SUFFIXES = (".pdf", ".ps", ".dvi", ".tar.gz", ".gz")


def split_arxiv_ref(ref: Any) -> tuple[Optional[str], Optional[int]]:
    """arXiv への参照を ``(正規化 ID, version)`` へ分解する。

    受け付ける形（いずれも大小文字・前後空白・``arXiv:`` 接頭辞を吸収する）:

    - 生 ID: ``2608.20293`` / ``2608.20293v2`` / ``hep-ph/9901234v1``
    - abs URL: ``https://arxiv.org/abs/2608.20293v1``
    - pdf URL: ``https://arxiv.org/pdf/2608.20293v2`` / ``.../2608.20293.pdf``
    - API の entry id: ``http://arxiv.org/abs/hep-ph/9901234v1``

    Returns:
        ``(id, version)``。解釈できない入力は ``(None, None)``。version が
        書かれていなければ ``(id, None)``。
    """
    if not isinstance(ref, str):
        return (None, None)

    text = ref.strip()
    if not text:
        return (None, None)

    # クエリ・フラグメントを落とす（``?context=astro-ph`` 等）。
    text = text.split("#", 1)[0].split("?", 1)[0]

    # URL なら scheme と host を落としてパス部分だけにする。
    if "://" in text:
        text = text.split("://", 1)[1]
        text = text.split("/", 1)[1] if "/" in text else ""
    elif text.lower().startswith(("arxiv.org/", "www.arxiv.org/", "export.arxiv.org/")):
        text = text.split("/", 1)[1]

    text = text.strip("/")
    if not text:
        return (None, None)

    # ``arXiv:2608.20293`` 表記。
    if text.lower().startswith("arxiv:"):
        text = text[len("arxiv:"):].strip()

    # 配信形式のプレフィックス（``abs/`` / ``pdf/`` …）を落とす。
    parts = [p for p in text.split("/") if p]
    while parts and parts[0].lower() in _PATH_PREFIXES:
        parts = parts[1:]
    if not parts:
        return (None, None)
    candidate = "/".join(parts)

    # 末尾拡張子を落とす。
    lowered = candidate.lower()
    for suffix in _TRAILING_SUFFIXES:
        if lowered.endswith(suffix):
            candidate = candidate[: -len(suffix)]
            break

    candidate = candidate.strip()
    if not candidate:
        return (None, None)

    match = _NEW_ID_RE.match(candidate)
    if match is None:
        match = _OLD_ID_RE.match(candidate)
    if match is None:
        return (None, None)

    base = match.group(1)
    # 旧形式のアーカイブ名は小文字が正（``hep-ph/9901234``）。新形式は数字のみ。
    if "/" in base:
        archive, _, number = base.partition("/")
        base = archive.lower() + "/" + number

    raw_version = match.group(2)
    version = int(raw_version) if raw_version else None
    return (base, version)


def normalize_arxiv_id(ref: Any) -> Optional[str]:
    """arXiv への参照から **version 抜きの**正規化 ID を返す（解釈不能は ``None``）。

    ``documents.source_url`` からの取り込み済み判定・見送り記録の主キー・API から
    受け取った候補 ID のすべてがこの関数を通ることで、v1/v2 の版違いや
    abs/pdf の URL 表記ゆれが同一論文へ畳まれる（設計書 §4.1）。
    """
    return split_arxiv_ref(ref)[0]


# ---------------------------------------------------------------------------
# ファイル名・タイトルからの arXiv 出所の推定（論文レーダーの後付け登録）
# 正本: ``docs/features/paper_radar_design.md``（arXiv 出所の後付け登録・3段階）。
#
# 手動アップロードされた教材は ``documents.source_url`` が空なので、レーダーは
# カテゴリ・要旨を引けない。ここでは**決定論の文字列処理だけ**で「たぶんこの論文だ」
# という**推定**を作る（DB も外部 API も触らない純関数）。推定はあくまで推定として
# 扱い、``documents.source_url`` への記帳は別途 route 層の明示操作でのみ行う。
# ---------------------------------------------------------------------------

#: 自由文字列に埋もれた新形式 arXiv ID（``arXiv-2407.01221v2.tar.gz`` 等）の走査パターン。
#: 前後に数字が続く並び（``12345.678901`` のような別種の数値）は境界の否定先読み・
#: 否定後読みで弾く。version サフィックスは ID の一部ではないので捨てる。
#: 旧形式 ID（``hep-ph/0101001``）は v1 では推定に使わない（区切りのないファイル名では
#: 分野名との切れ目が決まらず、当て推量になるため）。
_FILENAME_ID_SCAN_RE = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)")


def arxiv_id_from_filename(value: Any) -> str:
    """ファイル名・タイトル等の自由文字列から新形式 arXiv ID を**推定**する。

    ``"arXiv-2407.01221v2.tar.gz"`` → ``"2407.01221"``（version は落とす）。

    **相異なる ID が2つ以上見つかったら空文字を返す**（どちらが論文本体かを決められない
    以上、当て推量をしない = 曖昧なら推定しない）。同じ論文の版違い（``2407.01221v1`` と
    ``2407.01221v2``）は :func:`normalize_arxiv_id` と同じ規則で1件へ畳む。

    Returns:
        正規化済み（version 抜き）の ID。該当なし・曖昧・旧形式は ``""``。
    """
    if not isinstance(value, str):
        return ""
    found: list[str] = []
    for match in _FILENAME_ID_SCAN_RE.finditer(value):
        normalized = normalize_arxiv_id(match.group(1))
        if normalized and normalized not in found:
            found.append(normalized)
    if len(found) != 1:
        return ""
    return found[0]


def normalize_title_for_match(value: Any) -> str:
    """タイトル照合用の正規化（NFKC → casefold → 英数字以外を全除去）。

    改行・空白・記号・全角半角・大小文字の揺れを吸収するためのもので、表示には使わない。
    """
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    return "".join(ch for ch in text if ch.isalnum())


#: 照合を成立させるのに必要な正規化後の最短長（短すぎるタイトルの偶然一致を防ぐ）。
TITLE_MATCH_MIN_LENGTH = 10


def titles_match(a: Any, b: Any) -> bool:
    """2つのタイトルが「同じ論文とみなせる」ほど一致するか（決定論・非LLM）。

    :func:`normalize_title_for_match` の結果が**完全一致**し、かつ
    :data:`TITLE_MATCH_MIN_LENGTH` 文字以上あるときだけ真。部分一致・編集距離は
    使わない（曖昧な一致で自動記帳しない — 不一致は教員の明示確定へ回す）。
    """
    left = normalize_title_for_match(a)
    right = normalize_title_for_match(b)
    if not left or not right:
        return False
    if len(left) < TITLE_MATCH_MIN_LENGTH:
        return False
    return left == right


def pdf_url_for(arxiv_id: str) -> str:
    """正規化 ID から PDF の取得 URL を組み立てる。

    実際の取得は必ず url_fetch の許可リスト照合つき取得関数を通す（PD2）—
    ここは URL 文字列を作るだけで、この層は HTTP で論文を取りに行かない。
    """
    return f"https://{ARXIV_SITE_HOST}/pdf/{str(arxiv_id or '').strip()}"


def abs_url_for(arxiv_id: str) -> str:
    """正規化 ID から論文ページ（abs）の URL を組み立てる。"""
    return f"https://{ARXIV_SITE_HOST}/abs/{str(arxiv_id or '').strip()}"


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass
class ArxivEntry:
    """arXiv API が返した論文1件のメタデータ。

    ``arxiv_id`` は :func:`normalize_arxiv_id` 済み（version 抜き）で、版は
    ``version`` に分離して持つ。数値スコアの類は持たない（PD4）。
    """

    arxiv_id: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    summary: str = ""
    categories: list[str] = field(default_factory=list)
    primary_category: Optional[str] = None
    published: str = ""
    updated: str = ""
    version: Optional[int] = None
    pdf_url: str = ""
    abs_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "arxiv_id": self.arxiv_id,
            "version": self.version,
            "title": self.title,
            "authors": list(self.authors),
            "summary": self.summary,
            "categories": list(self.categories),
            "primary_category": self.primary_category,
            "published": self.published,
            "updated": self.updated,
            "pdf_url": self.pdf_url or pdf_url_for(self.arxiv_id),
            "abs_url": self.abs_url or abs_url_for(self.arxiv_id),
        }


@dataclass
class CitationEntry:
    """引用グラフ API が返した推薦論文1件（Phase 3 / 設計書 §6）。

    ``arxiv_id`` は :func:`normalize_arxiv_id` 済み（version 抜き）。**arXiv ID を
    持つ論文だけ**を DTO 化する（既存の取り込み経路＝arXiv の PDF URL に乗せられる
    ものに限る、PD2）。:class:`ArxivEntry` と同じく数値スコアを持たない（PD4）。

    ``seed_arxiv_id`` は「どの取り込み済み論文から辿ったか」の出所で、候補カードに
    「〇〇から辿りました」と事実を書くために持つ（ブラックボックスのおすすめに
    しない — §4.4 の候補カードと同じ規律）。
    """

    arxiv_id: str
    title: str = ""
    summary: str = ""
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    seed_arxiv_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "summary": self.summary,
            "authors": list(self.authors),
            "year": self.year,
            "pdf_url": pdf_url_for(self.arxiv_id),
            "abs_url": abs_url_for(self.arxiv_id),
        }


# ---------------------------------------------------------------------------
# キーフレーズの正規化（購読条件・検索条件の入口で共通に使う）
# ---------------------------------------------------------------------------


def normalize_keyphrase(raw: Any) -> Optional[dict]:
    """キーフレーズ1件を ``{"text", "source", "enabled"}`` へ正規化する。

    文字列（``"dark energy"``）でも辞書（``{"text": ..., "source": ..., "enabled": ...}``）
    でも受ける（フロントは検索の条件上書きを文字列配列で送り、購読の保存は
    オブジェクト配列で送るため、どちらの形も入口で吸収する）。

    不明・不正な ``source`` は :data:`DEFAULT_KEYPHRASE_SOURCE` に落とす（語彙の
    fail-closed。情報は落とさず「教員が足したもの」として扱う）。空文字は ``None``。
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        return {"text": text, "source": DEFAULT_KEYPHRASE_SOURCE, "enabled": True}

    if not isinstance(raw, dict):
        return None

    text = str(raw.get("text") or "").strip()
    if not text:
        return None

    source = str(raw.get("source") or "").strip()
    if source not in KEYPHRASE_SOURCES:
        source = DEFAULT_KEYPHRASE_SOURCE

    enabled = raw.get("enabled", True)
    return {"text": text, "source": source, "enabled": bool(enabled)}


def normalize_keyphrases(raw: Any) -> list[dict]:
    """キーフレーズ列を正規化する（順序保持・同一テキストは先勝ちで重複除去）。"""
    if not isinstance(raw, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for item in raw:
        normalized = normalize_keyphrase(item)
        if normalized is None:
            continue
        key = normalized["text"].casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def normalize_authors(raw: Any) -> list[str]:
    """著者フォロー列を文字列のリストへ正規化する（``{"name": ...}`` も受ける）。"""
    if not isinstance(raw, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def normalize_categories(raw: Any) -> list[str]:
    """arXiv カテゴリ列を正規化する（前後空白除去・順序保持・重複除去）。

    カテゴリ表はハードコードしない（設計書 §9 — 妥当性は検索結果 0 件の事実文で足りる）。
    """
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        value = str(item or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out
