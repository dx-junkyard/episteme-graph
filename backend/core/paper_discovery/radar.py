"""論文レーダー — 教材（seed）起点の候補探索（読み時導出・非LLM）。

設計正本: ``docs/features/paper_radar_design.md`` §5.1（不変条項 PR1〜PR8）。
分野購読の4層のじょうご（網 / 絞り / 並べ替え / 人間の弁）を、分野ではなく
**論文1本**を起点に読み替える層で、既存の発見層（PD1〜PD8）の機構をそのまま使う。

このモジュールが守るもの:

- **PR1 起点は教材1件・候補は読み時導出**: seed の解決も候補一覧も毎回導出し、
  保存しない。レーダー専用のテーブル・列を持たない（migration 0）。
  唯一の「教員の判断」の書き込みは :func:`register_arxiv_provenance`（既存列
  ``documents.source_url`` への arXiv 出所の後付け記帳）で、これは教員の明示操作を
  受けた route 層だけが呼ぶ。探索経路（:func:`resolve_seed` /
  :func:`run_radar_search`）が触れるのは **arXiv が返したメタデータの写し**
  （migration 085 の外部事実キャッシュ）だけで、候補・帯・教員の選択は保存しない
  （CC3 と同型の設計明示例外 — 設計書 §14）。
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
import re
from typing import Any, Callable, Mapping, Optional

from sqlalchemy import text as sa_text

from core import document_refs as _document_refs
from core.label_vocab import (
    RADAR_DISTANCE_LABEL_FAR,
    RADAR_DISTANCE_LABEL_MID,
    RADAR_DISTANCE_LABEL_NEAR,
)
from core.paper_discovery import arxiv_client, corpus, metadata_cache, search, store
from core.paper_discovery import ranking as pd_ranking
from core.paper_discovery.schema import (
    RADAR_DISTANCES,
    abs_url_for,
    arxiv_id_from_filename,
    normalize_arxiv_id,
    normalize_categories,
    normalize_keyphrases,
    titles_match,
)

logger = logging.getLogger(__name__)

#: カテゴリの供給元（PR7 — どこから来た条件かを必ず DTO に明示する）。
CATEGORIES_SOURCE_ARXIV = "arxiv"
CATEGORIES_SOURCE_SUBSCRIPTION = "subscription"
CATEGORIES_SOURCE_MANUAL = "manual"
#: ``documents.source_url`` に**登録されていない**教材について、ファイル名・タイトルから
#: 推定した arXiv ID のメタデータで検索条件を埋めた場合の供給元（登録済み ``arxiv`` とは
#: 別語彙にして「推定である」ことを DTO に残す — PR7 / 出所の正直さ）。
CATEGORIES_SOURCE_ARXIV_INFERRED = "arxiv_inferred"

#: arXiv 出所（``documents.source_url``）の状態語彙（``seed["provenance"]["status"]``）。
#: ``registered`` = 登録済み（取り込み経由 or 教員の確定） / ``inferred`` = ファイル名等
#: からの推定のみ（**未記帳**） / ``none`` = 推定もできない。
PROVENANCE_STATUS_REGISTERED = "registered"
PROVENANCE_STATUS_INFERRED = "inferred"
PROVENANCE_STATUS_NONE = "none"

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

#: arXiv からアクセスを制限された（HTTP 429）ために引けなかったときの事実文。
#: 到達失敗と同じ文言にしない（「繋がらない」なら設定や回線を疑い、「制限されている」
#: なら時間をおくしかない — 教員の次の一手が違う。PR7）。
#: **「混雑」とは言わない**（2026-09-15 オーナー指示 §14.7）— 制限されているのは
#: 観測できた事実で、向こうが混んでいるかどうかはこちらには分からない。条件を変えれば
#: 通るかのように読ませないため、問い合わせを止めていることも併せて言う。
#: 指す操作は「検索」ではなく「開き直す」— seed のメタデータを取り直すのは
#: ``GET /radar/seed``（モーダルを開いたとき）だけで、検索ボタンの再押下では
#: 取り直さない（§13.5）。効かない操作を案内しない。
NOTE_ARXIV_RATE_LIMITED = (
    "arXiv からアクセスを制限されているため、論文情報を取得できませんでした。"
    "この画面からの arXiv への問い合わせは、しばらくの間止めています。"
    "時間をおいてから、もう一度この画面を開いてください。"
)

#: いま arXiv からアクセスを制限されている（クールダウン中）という**状態**の事実文
#: （§14.7）。上の :data:`NOTE_ARXIV_RATE_LIMITED` が「この取得が失敗した」を言うのに対し、
#: こちらは「いまは何を押しても arXiv へは出ていかない」を言う。**残り時間・回数などの
#: 数値は書かない**（PR2 — 窓の残り時間を読む関数は ``arxiv_client`` の内側の判定材料
#: であって、API / UI には出さない）。条件を変えても解けないことを明示するのは、教員が
#: 条件をいじって同じ結果を何度も踏むのを防ぐため。
NOTE_ARXIV_BLOCKED = (
    "arXiv からアクセスを制限されています。"
    "この画面からの arXiv への問い合わせは、しばらくの間止めています。"
    "制限が解けるまでは、条件を変えても検索・比較はできません。"
    "時間をおいてから、もう一度この画面を開いてください。"
)

#: seed のメタデータを引けなかった理由（``seed["metadata_failure"]``。**引けたときは
#: キー自体を付けない**）。上の ``NOTE_ARXIV_*`` 3文と1対1に対応する機械可読の目印で、
#: フロントが文面の部分一致で理由を当てにいかなくて済むようにする（§14.7）。
METADATA_FAILURE_RATE_LIMITED = "rate_limited"
METADATA_FAILURE_UNAVAILABLE = "unavailable"
METADATA_FAILURE_NOT_FOUND = "not_found"

#: arXiv には届いたが、その ID の論文情報が返らなかったときの事実文。
#: 到達失敗と混ぜると「繋がらなかった」と誤読させる（PR7）。
NOTE_ARXIV_METADATA_NOT_FOUND = (
    "arXiv からこの論文の情報が返らなかったため、別の供給元から検索条件を組み立てました。"
)

#: seed のメタデータの出所（``seed["metadata_source"]``。事実ラベルであって数値ではない）。
#: ``arxiv`` = 今この操作で arXiv から引いた / ``cache`` = 保存済みの写しを読んだ
#: （migration 085 の外部事実キャッシュ）。**引けなかったときはキー自体を付けない**
#: （引けたのか読んだのかを黙らせない一方で、無い事実を捏造もしない — PR7）。
METADATA_SOURCE_ARXIV = "arxiv"
METADATA_SOURCE_CACHE = "cache"


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
    # 両形の突き合わせは core/document_refs に委ねる（IK-0550・id 一致を優先する規則も同じ）。
    entry = _document_refs.resolve_document_row(
        session, ref, columns=("title", "filename", "source_url"),
    )
    if not entry:
        return None
    title = str(entry.get("title") or "").strip() or str(entry.get("filename") or "").strip()
    return {
        "document_id": str(entry.get("document_id") or "").strip(),
        "source_path": str(entry.get("material_id") or "").strip(),
        "title": _clean(title),
        "source_url": str(entry.get("source_url") or "").strip(),
        # ファイル名は arXiv 出所の**推定**にだけ使う（``arXiv-2407.01221v2.tar.gz``）。
        "filename": _clean(entry.get("filename") or ""),
    }


#: キーフレーズの供給段（設計書 §16 — 上から順に詰め、:data:`MAX_SEED_KEYPHRASES` で止める）。
#: 出所の語彙は ``core.paper_discovery.schema.KEYPHRASE_SOURCES`` の要素（PD3）。
KEYPHRASE_SOURCE_COMPONENT = "component"
KEYPHRASE_SOURCE_SKELETON = "skeleton"
KEYPHRASE_SOURCE_CONCEPT = "concept"
KEYPHRASE_SOURCE_TITLE = "title"

KEYPHRASE_SOURCE_THESIS = "thesis"

#: 題名・中心命題から取る名詞句の上限（段④ — 題名と中心命題を併合した上限）。
MAX_TITLE_THESIS_KEYPHRASES = 6
#: 互換名（旧名）。
MAX_TITLE_KEYPHRASES = MAX_TITLE_THESIS_KEYPHRASES

#: 名詞句の語数（1 語は「その句が丸ごと 1 語だった」ときだけ。部分の 1 語は使わない）。
PHRASE_MAX_WORDS = 4
#: 互換名（旧名）。
TITLE_PHRASE_MIN_WORDS = 2
TITLE_PHRASE_MAX_WORDS = PHRASE_MAX_WORDS

#: 中心命題の句を「題名に無くても採る」ための最小出現回数（段④の第2群）。
THESIS_MIN_OCCURRENCES = 2

#: 中心命題・要旨に現れる固有名の 1 語（``DHOST`` / ``Horndeski``）の上限（§20 C）。
#: 題名・中心命題の句の上限（:data:`MAX_TITLE_THESIS_KEYPHRASES`）とは別枠で数える
#: （句で枠が埋まると固有名が必ず締め出されるため）。
MAX_PROPER_NOUN_KEYPHRASES = 2
#: 固有名とみなす 1 語の最短の字数（``GR`` のような 2 字の略号は網が広すぎる）。
PROPER_NOUN_MIN_CHARS = 3
_PROPER_NOUN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")
#: 固有名の直後に続く小文字の語（``Doppler shifts`` の ``shifts``・§23）。
_FOLLOWING_LOWER_WORD_RE = re.compile(r"[ \t]+([a-z][a-z\-]*)")

#: 名詞句を区切る英語の機能語・動詞・論文の定型語（分野の語は入れない — domain-independent）。
_TITLE_STOPWORDS = frozenset(
    """
    a an the and or but nor of in on at to for from by with without within into onto
    as via about over under between among through toward towards beyond against upon
    is are was were be been being am do does did done has have had having
    can could may might must shall should will would
    it its their our we us they them this that these those which what who whom whose
    whether how why when where while if than then thus hence also not no such both
    each either neither all any some many much more most other another same only
    using use used uses based new novel study studies analysis approach approaches
    method methods probe probes test tests testing tested role case cases
    implications evidence revisited revisiting note notes remarks comment
    built build builds derive derives derived deriving show shows shown showing
    find finds found obtain obtains obtained provide provides provided
    propose proposes proposed present presents presented demonstrate demonstrates
    demonstrated construct constructs constructed calculate calculated calculating
    argue argues argued yield yields hold holds held remain remains remained
    depend depends apply applies applied related relate relates encode encoded
    investigate investigated constrain constrains constrained consider considered
    allow allows allowed lead leads led make makes made give gives given
    whereas thereby therefore however
    """.split()
)

_PHRASE_SEGMENT_SPLIT_RE = re.compile(r"[^A-Za-z0-9\-\s]+")
_TITLE_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*[A-Za-z0-9]|[A-Za-z0-9]")

#: 主張の概念（段③）の検索語としての上限。文のような「概念」（実測 2407.01221:
#: 「Conditional joint forecast: approximately 0.62 ≤ λ ≤ 1.41」）を arXiv の
#: ``all:"…"`` に入れると該当ゼロになる。
#: 第 4 波（§21 A）で 5 語 / 48 字 → 4 語 / 40 字に締めた（実測 2610.08908:
#: 「Fisher forecast of covariance-modeling impact」= 5 語 45 字が網を広げた）。
CONCEPT_PHRASE_MAX_WORDS = 4
CONCEPT_PHRASE_MAX_CHARS = 40
_CONCEPT_FORBIDDEN_RE = re.compile(r"[0-9:<>=≤≥≈±×\\$^_{}|/]")

#: 検索語として使わない単位・記号の 1 語（照合は小文字・§21 A）。分野の語彙ではなく
#: 物理量の単位の一般表（domain-independent）。実測 2610.08908 で ``Gpc`` が中心命題から
#: 固有名として供給された。
_UNIT_TOKENS = frozenset(
    """
    pc kpc mpc gpc au ly km cm mm nm um m s ms us ns sec min hr yr kyr myr gyr
    ev kev mev gev tev pev erg j kj mj w kw mw gw hz khz mhz ghz thz k mk
    g kg mg t gauss tesla jy mjy ujy deg rad sr arcmin arcsec mas
    sigma dex mag sol msun lsun rsun
    """.split()
)
#: 「数字 + 単位」（``3Gpc`` / ``10 keV``）。
_NUMBER_UNIT_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:" + "|".join(sorted(_UNIT_TOKENS, key=len, reverse=True)) + r")$",
    re.IGNORECASE,
)
#: 一般動詞の動名詞の 1 語（どの分野の要旨にも現れ、単独では網を広げるだけ・§21 A）。
#: ``clustering`` / ``lensing`` のような分野の名詞は入れない。
_GENERIC_GERUNDS = frozenset(
    """
    averaging binning modeling modelling fitting smoothing sampling marginalizing
    marginalising filtering weighting scaling rescaling normalizing normalising
    interpolating computing estimating testing training processing matching ordering
    counting combining comparing measuring analyzing analysing using applying
    including improving extending constraining forecasting simulating
    """.split()
)
#: 1 語のキーフレーズの最短の字数（``P`` / ``B`` を落とす・§21 A）。
SINGLE_WORD_MIN_CHARS = 3

#: 略語のフル表記として採る句の最大語数（§23 — ``Effective Field Theory of Large Scale
#: Structure`` = 7 語は採る。それより長いものは文の取り込み違いとみなす）。
ABBREVIATION_FULL_MAX_WORDS = 8
#: 短い略語（大文字だけの 2〜3 字 — ``EFT`` / ``LSS``）。この長さの略語は分野をまたいで多義に
#: なりやすく、単独で ``OR`` の網を分野外へ広げる（judge-scan-09: ``EFT`` だけで一致した候補が
#: アクシオン・暗黒物質・高階微分重力へ広がった）。フル表記の句を別に供給できるときは略語を
#: 落とす（§23.5）。``FoG`` / ``SPCF`` のような論文固有の略語はこの形に当たらないので残る —
#: 一般語の表は作らず、区別は「フル表記の句が別に供給されているか」だけで決める。
_SHORT_ABBREVIATION_RE = re.compile(r"[A-Z]{2,3}")
#: 本文（chunks）から略語のフル表記を探すときに読むチャンクの上限（§23.5）。
BODY_ABBREVIATION_MAX_CHUNKS = 5
#: 名詞句の内側に残す前置詞（§23 — ``Fingers of God`` を 1 句として保つ）。冠詞を伴う
#: ``of the`` はつながない（``Kinetic Theory of the EFTofLSS`` は 2 句のまま）。
_PHRASE_INNER_CONNECTOR = "of"
#: ``X of Y`` の片側の最大語数（それより長い連なりは端の語だけをつなぐ）。
_COMPOUND_SIDE_MAX_WORDS = 2

#: 供給の事実文（``seed["keyphrase_supply"]["note"]``。PR7 / PD6 — 縮退を黙らせない）。
#: 承認済みの理論部品があるときは事実文を出さない（毎回出ると景色になる）。
NOTE_KEYPHRASE_SUPPLY_EMPTY = (
    "この教材からはキーフレーズを取り出せませんでした"
    "（承認済みの理論部品・確認済みの地図上の位置・主張の概念・題名・中心命題のいずれからも"
    "取れませんでした）。"
)
NOTE_KEYPHRASE_SUPPLY_WITHOUT_APPROVED = (
    "この教材にはまだ承認済みの理論部品が無いため、"
    "解析結果から取り出した語でキーフレーズを補いました（各語の出所はチップに表示しています）。"
)

#: 検索がキーフレーズ無しで走ったときの事実文（``result["keyphrase_note"]``・距離 near のみ）。
#: カテゴリ（数十万件の網）だけで arXiv を引くと、返るのはテーマと無関係な新着になる。
#: それを「近い論文」と読ませないために言う（PD6 / PR7）。
NOTE_SEARCH_WITHOUT_KEYPHRASES = (
    "キーフレーズが無いため、テーマでは絞り込まず、カテゴリの新着から取った候補です。"
)

#: 近さの根拠が出ない理由の事実文（``result["facts"]``・設計書 §19 A — PR7）。
#: ``relation_context.available=false`` や承認済み部品ゼロは DTO の上では分かるが、
#: 画面に文として届かないと「近さの根拠が全候補で空」が「近くない」に読める
#: （judge-scan-01 §4 仮説 3）。数値は書かない（PR2）。出すのは検索した回だけ。
FACT_RELATION_NO_DOMAIN = (
    "起点の論文を教材に含むコースに分野の地図が設定されていないため、"
    "着地予測と「新しい面」は出していません。"
)
FACT_RELATION_NO_ANCHORS = (
    "起点の論文の分野の地図に照合用の索引（凍結版のベクトル索引）が無いため、"
    "着地予測と「新しい面」は出していません。"
)
FACT_NO_APPROVED_COMPONENTS = (
    "起点の論文には承認済みの理論部品が無いため、部品との重なり（≒）は出していません。"
)

#: 検索語の当たり外れの事実文（``result["keyphrase_hits_note"]`` /
#: ``result["keyphrase_widening_note"]``・設計書 §20 A / B）。候補カード単位の一致チップを
#: 全部読まないと「どの語がどの候補にも当たっていないか」が分からなかった
#: （judge-scan-05 §5 仮説 1）。件数は書かない（PD4 / PR2）。語の列挙は呼び出し側で付ける。
NOTE_KEYPHRASES_NOT_HIT_HEAD = "この検索で取得した候補のどれにも当たらなかった語: "
NOTE_KEYPHRASES_WIDENING_HEAD = (
    "取得した候補のすべてに当たった語（この語が網を広げている可能性があります。"
    "外すかどうかは教員が選びます）: "
)
#: 「網を広げている語」を言うのに要る候補の数と、有効な語の数の下限。1 件・1 語では
#: 「すべてに当たった」が自明に真になり、網の幅について何も言わない。
WIDENING_MIN_CANDIDATES = 3
#: 「この語だけに一致した候補」が取得した候補の過半を占める語の事実文（§23.5・
#: judge-scan-09: ``EFT`` が単独で網の大半を連れてきたのに「すべてに当たった」の条件に
#: 掛からず、印も事実文も出なかった）。件数・割合は書かない（PD4 / PR2）。
NOTE_KEYPHRASE_SOLE_WIDENING = "この語だけに一致した候補が多く、網を広げています"
NOTE_KEYPHRASES_SOLE_WIDENING_HEAD = (
    NOTE_KEYPHRASE_SOLE_WIDENING + "（外すかどうかは教員が選びます）: "
)
WIDENING_MIN_ENABLED_KEYPHRASES = 2

#: 帯と関係の種類の読み方の事実文（``result["reading_note"]``・設計書 §20 D）。
#: 帯（要旨の埋め込みの相対比較）・一致チップ（語の一致）・関係の種類（AI の読み）の
#: 根拠の違いが画面上で区別されず、「近い」と「目立った共通点なし」が同じカードで矛盾して
#: 見えた（judge-scan-05 §5 仮説 4 / 5）。画面は比較結果（関係の種類）が描かれたときだけ出す。
READING_NOTE_BANDS_AND_RELATIONS = (
    "距離帯は取得した候補の中での要旨どうしの近さ、関係の種類は AI が要旨を読んだ結果で、"
    "同じ語でも別の対象を指していることがあります。"
)
READING_NOTE_NEAR_ORDER = "近い帯の候補は要旨の近さの順に並んでいます。"
READING_NOTE_NEWEST_ORDER = "候補は arXiv の新着順に並んでいます。"

#: 起点の題名がファイル名のときの事実文（``seed["title_note"]``・設計書 §19 E）。
#: arXiv の題名を主表示にしたことを言う（教材名は書き換えない — PR1）。
NOTE_SEED_TITLE_IS_FILENAME = (
    "教材名はファイル名のままです。見出しには arXiv の題名を表示しています。"
)

#: ファイル名様の教材名（``arXiv-2508.06322v2`` / ``arXiv-2508.06322v2.tar.gz`` /
#: ``2508.06322.pdf``）。内容を表さない文字列を見出しにしないための判定。
_FILENAME_LIKE_TITLE_RE = re.compile(
    r"^(?:arxiv[-_:\s]?)?\d{4}\.\d{4,5}(?:v\d+)?(?:\.[A-Za-z0-9]+)*$",
    re.IGNORECASE,
)


def title_looks_like_filename(title: Any) -> bool:
    """教材名が arXiv のファイル名そのもの（内容を表さない）か。"""
    return bool(_FILENAME_LIKE_TITLE_RE.match(_clean(title)))


#: 教材名がファイル名のままで、arXiv の題名も引けていないとき（題名を捏造しない — §22.4）。
NOTE_TITLE_UNRESOLVED = (
    "教材名はファイル名のままで、題名は未取得です（arXiv の題名の写しがまだありません）。"
)


def display_title_for(title: Any, arxiv_title: Any = "") -> tuple[str, str]:
    """教材の表示題名と添える事実文 ``(display_title, title_note)`` の正本（純関数・§19 E / §22.4）。

    - 教材名がファイル名でなければ ``(教材名, "")``。
    - ファイル名で arXiv の題名があれば ``(arXiv の題名, NOTE_SEED_TITLE_IS_FILENAME)``。
    - ファイル名で arXiv の題名が無ければ ``(教材名, NOTE_TITLE_UNRESOLVED)``（推測で埋めない）。

    レーダーの起点（``resolve_seed``）と、理論モジュールの近い構造の相手
    （``core.library.structural_matches``）が同じ関数を使う。教材名は書き換えない（PR1）。
    """
    raw = _clean(title)
    if not title_looks_like_filename(raw):
        return raw, ""
    arxiv = _clean(arxiv_title)
    if arxiv:
        return arxiv, NOTE_SEED_TITLE_IS_FILENAME
    return raw, NOTE_TITLE_UNRESOLVED


def _paper_title_from_artifacts(artifacts: Any) -> str:
    """採用 run の ``document_structure`` artifact が読んだ論文の題名（無ければ空）。

    教材名（``documents.title``）はファイル名のことがあり、arXiv の題名との照合相手に
    ならない（judge-scan-02 ②・型 ``same-name-different-referents``）。照合の第2の
    相手として論文本文から復元した題名を使う（書き込みはしない — PR1）。
    """
    if not isinstance(artifacts, dict):
        return ""
    payload = artifacts.get("document_structure")
    if not isinstance(payload, dict):
        return ""
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        title = _clean(metadata.get("title"))
        if title:
            return title
    return _clean(payload.get("title"))


def category_candidates(categories: Any, primary: Any) -> tuple[list[str], list[dict]]:
    """arXiv のカテゴリ列から「既定で有効な集合」とチップ候補を作る（設計書 §19 B）。

    主カテゴリ（``primary``・無ければ先頭）だけを既定で有効にし、副カテゴリは
    ``enabled=False``（打ち消し表示）で並べる。副カテゴリを等価に OR すると主題から
    離れた分類の新着が網の半分を占める（judge-scan-01 §4 仮説 4・型
    ``scope-widened-silently``）。教員はチップで足せる（PD3 — 条件を広げるのは教員）。

    Returns:
        ``(enabled, candidates)``。``enabled`` は主カテゴリ1件（カテゴリ無しなら空）、
        ``candidates`` は ``[{text, enabled, primary}]``（主カテゴリが先頭）。
    """
    normalized = normalize_categories(categories)
    primary_text = _clean(primary)
    if primary_text:
        normalized = normalize_categories([primary_text] + normalized)
    if not normalized:
        return [], []
    head = normalized[0]
    candidates = [
        {"text": value, "enabled": value == head, "primary": value == head}
        for value in normalized
    ]
    return [head], candidates


def _all_enabled_candidates(categories: list[str]) -> list[dict]:
    """購読・手入力由来のカテゴリ（教員が選んだ条件）はすべて有効のまま並べる。"""
    return [{"text": value, "enabled": True, "primary": False} for value in categories]


def _document_refs_of(document_row: dict[str, str]) -> list[str]:
    return [
        value
        for value in dict.fromkeys(
            [document_row.get("document_id", ""), document_row.get("source_path", "")]
        )
        if value
    ]


def _approved_component_phrases(session, refs: list[str]) -> list[str]:
    """段①: seed の承認済み理論部品ラベル（既存の ``component`` 供給）。

    ``theory_components.document_id`` は documents.id（UUID 文字列）と material_id
    （``source_path``）のどちらも取りうるため、両方を候補に入れる
    （``corpus.domain_document_refs`` と同じ二面性）。承認前の部品（candidate 等）は
    **使わない**（確定は人間 — PD3 / KR2）。
    """
    rows = session.execute(
        sa_text(
            """
            SELECT DISTINCT name
              FROM theory_components_live
             -- refs は UUID 形と material_id 形の混在（corpus.domain_document_refs）。
             -- migration 080 で document_id は uuid になったが、混在配列を uuid[] に
             -- キャストすると例外になるため text 比較のまま突き合わせる。
             WHERE document_id::text = ANY(CAST(:refs AS text[]))
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
    return [_clean(row[0]) for row in rows]


def _confirmed_placement_phrases(session, document_id: str) -> list[str]:
    """段②: seed の**確認済み**の配置（``landscape_placements.status='confirmed'``）の骨格ノードラベル。

    AI 推定（``inferred``）は使わない（教員が確認した位置づけだけ — LS2 / PD3）。
    ラベルは配置が属する分野の現行凍結骨格から引く。現行版に無い node（版改訂で消えた
    もの）は使わない（ラベルを捏造しない）。concept を region より先に置く
    （region ラベルは検索語として広い — vocab の骨格供給と同じ判断）。
    """
    if not document_id:
        return []
    rows = session.execute(
        sa_text(
            """
            SELECT DISTINCT domain_key, node_id, node_kind
              FROM landscape_placements
             WHERE document_id = CAST(:document_id AS uuid)
               AND status = 'confirmed'
             ORDER BY domain_key, node_id
            """
        ),
        {"document_id": document_id},
    ).fetchall()
    if not rows:
        return []

    from core import atlas_store  # 遅延 import（テスト時の依存を最小にする）

    labels_by_domain: dict[str, dict[str, str]] = {}
    concepts: list[str] = []
    regions: list[str] = []
    for row in rows:
        domain_key = _clean(row[0])
        node_id = _clean(row[1])
        node_kind = _clean(row[2])
        if not domain_key or not node_id:
            continue
        if domain_key not in labels_by_domain:
            labels: dict[str, str] = {}
            skeleton = atlas_store.load_learner_skeleton(domain_key, session)
            for region in getattr(skeleton, "regions", ()) or ():
                region_id = _clean(getattr(region, "id", ""))
                if region_id:
                    labels[region_id] = _clean(getattr(region, "label", ""))
                for concept in getattr(region, "concepts", ()) or ():
                    concept_id = _clean(getattr(concept, "id", ""))
                    if concept_id:
                        labels[concept_id] = _clean(getattr(concept, "label", ""))
            labels_by_domain[domain_key] = labels
        label = labels_by_domain[domain_key].get(node_id, "")
        if not label:
            continue
        (regions if node_kind == "region" else concepts).append(label)
    return concepts + regions


def is_searchable_concept_name(value: Any) -> bool:
    """段③の概念名が arXiv の検索語として使えるか（純関数）。

    英字を含み・:data:`CONCEPT_PHRASE_MAX_WORDS` 語以下・:data:`CONCEPT_PHRASE_MAX_CHARS`
    字以下で、数字・比較記号・コロン・LaTeX の制御記法（``\\``・``$``・``^``・``_``・
    ``{}``）を含まないもの。文のような「概念」と数式片をここで落とす。
    """
    text = _clean(value)
    if not text or not re.search(r"[A-Za-z]", text):
        return False
    if len(text) > CONCEPT_PHRASE_MAX_CHARS or len(text.split()) > CONCEPT_PHRASE_MAX_WORDS:
        return False
    return not _CONCEPT_FORBIDDEN_RE.search(text)


def is_quality_keyphrase(value: Any, *, require_uppercase_single: bool = False) -> bool:
    """機械供給のキーフレーズが arXiv の検索語として網を広げすぎないか（純関数・§21 A）。

    - 英字を含む
    - 単位・記号の 1 語（:data:`_UNIT_TOKENS`）と「数字 + 単位」を除く
    - 1 語（空白なし・ハイフン語は 1 語）は :data:`SINGLE_WORD_MIN_CHARS` 字以上で、
      一般動詞の動名詞（:data:`_GENERIC_GERUNDS`）でない
    - ``require_uppercase_single`` のとき 1 語は大文字を含む（中心命題・要旨から取った
      固有名の 1 語。``FoG`` は可・``averaging`` は不可）

    教員が承認した部品・確認した配置ノード（``component`` / ``skeleton``）には掛けない
    （確定は人間 — PD3）。
    """
    text = _clean(value)
    if not text or not re.search(r"[A-Za-z]", text):
        return False
    if _NUMBER_UNIT_RE.match(text):
        return False
    words = text.split()
    if len(words) == 1:
        word = words[0]
        if word.casefold() in _UNIT_TOKENS:
            return False
        if len(word) < SINGLE_WORD_MIN_CHARS:
            return False
        if word.casefold() in _GENERIC_GERUNDS:
            return False
        if require_uppercase_single and not any(c.isupper() for c in word):
            return False
    return True


def _claim_concept_phrases(session, refs: list[str]) -> list[str]:
    """段③: seed の live 主張（``theory_claims_live.concepts``）の概念名を出現回数順に。

    ``concept_type == "symbol"`` の要素は使わない（実測 2407.01221 では 129 行のほぼ全部が
    記号）。名前は :func:`is_searchable_concept_name` を通り、記号様（P0-3 / V-6 の正本
    ``learner_context_common.is_symbol_like_concept``）でないものだけ。数は外へ出さない
    （並び順にだけ使う — PR2）。
    """
    from core.learner_context_common import (  # 遅延 import
        is_symbol_like_concept,
        json_list,
        safe_text,
    )

    rows = session.execute(
        sa_text(
            """
            SELECT concepts
              FROM theory_claims_live
             WHERE document_id::text = ANY(CAST(:refs AS text[]))
               AND concepts IS NOT NULL
             ORDER BY id
            """
        ),
        {"refs": refs},
    ).fetchall()
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    display: dict[str, str] = {}
    order = 0
    for row in rows:
        seen_in_claim: set[str] = set()
        for item in json_list(row[0]):
            if isinstance(item, dict):
                if str(item.get("concept_type") or "").strip().casefold() == "symbol":
                    continue
                raw = item.get("name") or item.get("canonical") or item.get("normalized")
            else:
                raw = item
            text = _clean(safe_text(raw))
            if not is_searchable_concept_name(text) or is_symbol_like_concept(text):
                continue
            if not is_quality_keyphrase(text):
                continue  # 単位・動名詞・2 字以下の 1 語（§21 A）
            key = text.casefold()
            if key in seen_in_claim:
                continue  # 1 主張の中の重複は 1 回に数える
            seen_in_claim.add(key)
            if key not in counts:
                counts[key] = 0
                first_seen[key] = order
                display[key] = text
                order += 1
            counts[key] += 1
    ranked = sorted(counts, key=lambda key: (-counts[key], first_seen[key]))
    return [display[key] for key in ranked]


def _norm_word(word: str) -> str:
    """句の照合用の 1 語の正規形（小文字・素朴な単数化）。表示には使わない。"""
    w = word.casefold()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "is", "us")):
        return w[:-1]
    return w


def _norm_phrase(text: str) -> str:
    return " ".join(_norm_word(w) for w in re.split(r"[\s\-]+", text) if w)


def _phrase_runs(text: Any) -> list[list[str]]:
    """句読点・機能語・動詞で区切った語の連なり（ハイフン語は 1 語のまま保持）。"""
    runs: list[list[str]] = []
    for segment in _PHRASE_SEGMENT_SPLIT_RE.split(str(text or "")):
        current: list[str] = []
        for token in _TITLE_WORD_RE.findall(segment):
            if token.casefold() in _TITLE_STOPWORDS or not re.search(r"[A-Za-z]", token):
                if current:
                    runs.append(current)
                current = []
                continue
            current.append(token)
        if current:
            runs.append(current)
    return runs


def _phrase_candidates(text: Any) -> list[str]:
    """名詞句の候補（出現順・表示形）。

    - 連なり全体（:data:`PHRASE_MAX_WORDS` 語以下。長い連なりは先頭から 4 語ずつ）は
      1 語でも採る（``kurtosis`` のように丸ごと 1 語の句）。
    - 連なりの部分列は **2 語以上**だけを採る（``gravity theories`` の ``gravity`` の
      ような断片の 1 語は検索語として広すぎる）。
    """
    out: list[str] = []
    for run in _phrase_runs(text):
        windows = [run[i:i + PHRASE_MAX_WORDS] for i in range(0, len(run), PHRASE_MAX_WORDS)]
        for window in windows:
            out.append(" ".join(window))
            for size in range(len(window) - 1, 1, -1):
                for start in range(0, len(window) - size + 1):
                    out.append(" ".join(window[start:start + size]))
    return out


def _looks_like_participle(word: str) -> bool:
    """1 語が動詞の過去分詞・過去形らしいか（``Resummed``・§23。ハイフン語は対象外）。"""
    w = str(word or "").casefold()
    return (
        "-" not in w and len(w) > 4 and w.endswith("ed") and not w.endswith("eed")
    )


def _is_acronym_like(word: str) -> bool:
    """1 語が略号らしいか（大文字を 2 つ以上含む: ``EFTofLSS`` / ``DHOST`` / ``FoG``）。"""
    return sum(1 for c in str(word or "") if c.isupper()) >= 2


def is_short_abbreviation(word: Any) -> bool:
    """1 語が短い略語（大文字だけの 2〜3 字 — ``EFT``）か（§23.5・純関数）。"""
    return bool(_SHORT_ABBREVIATION_RE.fullmatch(str(word or "").strip()))


def _of_compounds(text: Any) -> list[tuple[str, bool]]:
    """``X of Y`` の複合語の候補（出現順・表示形）と「両側とも 1 語の連なりか」（§23）。

    前置詞 ``of`` だけを挟んで隣り合う 2 つの語の連なり（機能語・句読点で区切った
    :func:`_phrase_runs` と同じ連なり）をつなぐ。片側が長いときは ``of`` に接する
    :data:`_COMPOUND_SIDE_MAX_WORDS` 語だけを使う。冠詞を挟む ``of the`` はつながない。
    両側とも 1 語の連なり（``Fingers`` / ``God``）は、分けると意味を失う断片なので
    1 句として採る材料になる。
    """
    out: list[tuple[str, bool]] = []
    for segment in _PHRASE_SEGMENT_SPLIT_RE.split(str(text or "")):
        tokens = _TITLE_WORD_RE.findall(segment)

        def _content(tok: str) -> bool:
            return tok.casefold() not in _TITLE_STOPWORDS and bool(re.search(r"[A-Za-z]", tok))

        for i, token in enumerate(tokens):
            if token.casefold() != _PHRASE_INNER_CONNECTOR or i == 0 or i + 1 >= len(tokens):
                continue
            if not (_content(tokens[i - 1]) and _content(tokens[i + 1])):
                continue
            left: list[str] = []
            j = i - 1
            while j >= 0 and _content(tokens[j]):
                left.insert(0, tokens[j])
                j -= 1
            right: list[str] = []
            j = i + 1
            while j < len(tokens) and _content(tokens[j]):
                right.append(tokens[j])
                j += 1
            single_single = len(left) == 1 and len(right) == 1
            phrase = " ".join(
                left[-_COMPOUND_SIDE_MAX_WORDS:] + [token] + right[:_COMPOUND_SIDE_MAX_WORDS]
            )
            out.append((phrase, single_single))
    return out


def _contains_phrase(haystack_norm: str, needle_norm: str) -> bool:
    return bool(needle_norm) and f" {needle_norm} " in f" {haystack_norm} "


def _count_phrase(haystack_norm: str, needle_norm: str) -> int:
    if not needle_norm:
        return 0
    return f" {haystack_norm} ".count(f" {needle_norm} ")


def proper_noun_keyphrases(
    texts: Any, *, exclude_norms: Any = (), limit: int = MAX_PROPER_NOUN_KEYPHRASES,
) -> list[str]:
    """中心命題・要旨から固有名の 1 語（``DHOST`` / ``Horndeski``）を取り出す（純関数・§20 C）。

    固有名とみなす語（品詞解析はしない素朴な規則 — domain-independent）:

    - 英字だけ・:data:`PROPER_NOUN_MIN_CHARS` 字以上・機能語表（:data:`_TITLE_STOPWORDS`）に無い
    - **全部大文字**（略号）か、**文頭以外で大文字始まり**（文頭の大文字は固有名の手がかりにならない）
    - 同じ語が小文字でも現れていない（``Gravity`` と ``gravity`` が両方あれば普通名詞）

    並びは出現回数の多い順・初出順。``exclude_norms``（既に採った句の正規形）と包含関係に
    ある語は採らない。最大 ``limit`` 件。
    """
    try:
        cap = max(0, int(limit))
    except (TypeError, ValueError):
        cap = MAX_PROPER_NOUN_KEYPHRASES
    if cap == 0:
        return []
    if isinstance(texts, str):
        texts = [texts]
    lower_tokens: set[str] = set()
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    display: dict[str, str] = {}
    order = 0
    for text in texts or ():
        body = str(text or "")
        for match in _PROPER_NOUN_TOKEN_RE.finditer(body):
            token = match.group(0)
            if token[0].islower():
                lower_tokens.add(token.casefold())
                continue
            if not token.isalpha() or len(token) < PROPER_NOUN_MIN_CHARS:
                continue
            if token.casefold() in _TITLE_STOPWORDS:
                continue
            is_acronym = token.isupper()
            if not is_acronym:
                before = body[: match.start()].rstrip()
                if not before or before[-1] in ".?!:;":
                    continue  # 文頭の大文字
                follow = _FOLLOWING_LOWER_WORD_RE.match(body, match.end())
                if follow and follow.group(1).casefold() not in _TITLE_STOPWORDS:
                    # 小文字の名詞を修飾する人名・地名（``Doppler shifts``・§23）は
                    # 句の一部で、1 語だけでは検索語にならない（網を広げるだけ）。
                    continue
            key = token.casefold()
            counts[key] = counts.get(key, 0) + 1
            if key not in first_seen:
                first_seen[key] = order
                display[key] = token
            order += 1
    excluded = [str(n) for n in exclude_norms or () if n]
    out: list[str] = []
    for key in sorted(counts, key=lambda k: (-counts[k], first_seen[k])):
        if key in lower_tokens:
            continue
        if not is_quality_keyphrase(display[key], require_uppercase_single=True):
            continue  # ``Gpc`` のような単位を固有名として採らない（§21 A）
        norm = _norm_phrase(display[key])
        if any(_contains_phrase(e, norm) or _contains_phrase(norm, e) for e in excluded):
            continue
        out.append(display[key])
        if len(out) >= cap:
            break
    return out


def title_thesis_keyphrases(
    title: Any,
    thesis_text: Any,
    *,
    summary: Any = "",
    limit: int = MAX_TITLE_THESIS_KEYPHRASES,
    body_full_form: Optional[Callable[[str], str]] = None,
) -> list[tuple[str, str]]:
    """段④: 題名・中心命題（+ 要旨）から名詞句を取り出し、併合順に ``[(句, 出所)]`` で返す（純関数）。

    併合順（句は最大 ``limit`` 件）:

    1. 題名と中心命題の**両方に現れる**句（出所 ``title`` — 表示は題名の表記）。要旨にも
       現れる句を群の中で先にする（§20 B）
    2. 題名か中心命題に現れ、**要旨にも現れる**句（出所は題名にあれば ``title``・
       無ければ ``thesis``。§20 B — 要旨は起点の論文の自己要約なので、題名の後半の
       汎用語より主題に近い句を先に採る材料になる）
    3. 中心命題に :data:`THESIS_MIN_OCCURRENCES` 回以上現れる句（出所 ``thesis``）
    4. 題名の残りの句（出所 ``title``）

    各群の中では長い句を先に採り、既に採った句と包含関係にある句は採らない（同じ語の
    言い換えでチップを埋めない）。照合は小文字・ハイフン無視・素朴な単数化の正規形で
    行い、表示は原文の表記のまま（訳語・言い換えを捏造しない）。英字の語が無い文
    （日本語の中心命題など）からは何も取らない。

    最後に、中心命題・要旨の固有名の 1 語（:func:`proper_noun_keyphrases`）を
    別枠で最大 :data:`MAX_PROPER_NOUN_KEYPHRASES` 件足す（出所 ``thesis``・§20 C）。
    要旨にしか現れない句（固有名以外）は採らない（要旨は起点の主題を言い換える材料で、
    新しい主題を足す材料ではない）。

    短い略語（:func:`is_short_abbreviation` — ``EFT``）は、フル表記の句が別に供給されて
    いれば落とす（§23.5）。フル表記は題名・中心命題・目的・要旨の「フル表記 (略語)」、
    無ければ ``body_full_form``（本文の「フル表記 (略語)」を引く注入関数・呼び出し側が
    DB を読む）から取り、別枠の略語の位置にフル表記を置く。フル表記が見つからない・
    採れないときは略語を残す（情報を落とさない）。
    """
    try:
        cap = max(0, int(limit))
    except (TypeError, ValueError):
        cap = MAX_TITLE_THESIS_KEYPHRASES
    if cap == 0:
        return []

    title_text = str(title or "")
    thesis = str(thesis_text or "")
    summary_text = str(summary or "")
    title_norm = " ".join(_norm_phrase(" ".join(r)) for r in [_TITLE_WORD_RE.findall(title_text)])
    thesis_norm = " ".join(_norm_phrase(" ".join(r)) for r in [_TITLE_WORD_RE.findall(thesis)])
    summary_norm = " ".join(
        _norm_phrase(" ".join(r)) for r in [_TITLE_WORD_RE.findall(summary_text)]
    )

    def _compounds(text: str, others: tuple[str, ...]) -> list[str]:
        # ``X of Y`` は両側が 1 語の連なり（分けると断片）か、他の材料にも同じ表記で
        # 現れるときだけ 1 句として採る（§23）。句から単語への分解はしない。
        out: list[str] = []
        for phrase, single_single in _of_compounds(text):
            norm = _norm_phrase(phrase)
            if single_single or any(_contains_phrase(o, norm) for o in others):
                out.append(phrase)
        return out

    title_compounds = _compounds(title_text, (thesis_norm, summary_norm))
    thesis_compounds = _compounds(thesis, (title_norm, summary_norm))
    title_cands = title_compounds + _phrase_candidates(title_text)
    thesis_cands = thesis_compounds + _phrase_candidates(thesis)

    def _in_summary(cand: str) -> bool:
        return _contains_phrase(summary_norm, _norm_phrase(cand))

    def _sorted(cands: list[str]) -> list[str]:
        indexed = list(dict.fromkeys(cands))
        return sorted(
            indexed,
            key=lambda c: (
                -len(_norm_phrase(c).split()),
                0 if _in_summary(c) else 1,
                indexed.index(c),
            ),
        )

    # 略語の辞書（題名・中心命題・目的・要旨の「フル表記 (略語)」。正本 core/abbreviations）。
    # フル表記の句を優先し、略語は 2 本目として固有名の別枠に回す（§23）。
    from core.abbreviations import extract_abbreviations  # 遅延 import（純関数）

    abbreviations = extract_abbreviations(
        [title_text, thesis, summary_text], shortest=True, normalize_width=True,
    )
    abbreviation_pairs: list[tuple[str, str]] = []
    for abbr, full in abbreviations.items():
        if abbr.endswith("s") and abbr[:-1] in abbreviations:
            continue  # 複数形（FoGs）は単数形（FoG）にまとめる
        if len(full.split()) > ABBREVIATION_FULL_MAX_WORDS:
            continue
        abbreviation_pairs.append((abbr, full))

    chosen: list[tuple[str, str]] = []
    chosen_norms: list[str] = []

    full_form_cache: dict[str, str] = {}

    def _full_form_of(abbr: str) -> str:
        # 短い略語のフル表記（材料の「フル表記 (略語)」→ 本文の注入関数の順・§23.5）。
        if abbr in full_form_cache:
            return full_form_cache[abbr]
        full = abbreviations.get(abbr) or abbreviations.get(abbr + "s") or ""
        if not full and body_full_form is not None:
            try:
                full = str(body_full_form(abbr) or "")
            except Exception:  # noqa: BLE001 — 本文が読めなくても略語のまま続ける
                logger.warning("radar body abbreviation lookup failed (non-fatal)", exc_info=True)
                full = ""
        full = full.strip()
        if len(full.split()) > ABBREVIATION_FULL_MAX_WORDS:
            full = ""
        full_form_cache[abbr] = full
        return full

    def _supplied(norm: str) -> bool:
        return bool(norm) and any(_contains_phrase(e, norm) for e in chosen_norms)

    def _try(phrase: str, source: str, *, ceiling: int = cap) -> None:
        if len(chosen) >= ceiling:
            return
        norm = _norm_phrase(phrase)
        if not norm:
            return
        if is_short_abbreviation(phrase):
            # 短い略語（``EFT``）は多義で網を分野外へ広げる。フル表記の句を同じ位置に先に
            # 置き、フル表記が供給されていれば略語は落とす。フル表記が無い・採れないときは
            # 略語を残す（§23.5）。
            full = _full_form_of(phrase.strip())
            if full:
                full_norm = _norm_phrase(full)
                if not _supplied(full_norm):
                    _try(
                        full,
                        KEYPHRASE_SOURCE_TITLE
                        if _contains_phrase(title_norm, full_norm) else KEYPHRASE_SOURCE_THESIS,
                        ceiling=ceiling,
                    )
                if _supplied(full_norm):
                    return
                if len(chosen) >= ceiling:
                    return
        # 動詞の過去分詞・過去形の 1 語（``Resummed``）は検索語にしない（§23）。
        if len(norm.split()) == 1 and _looks_like_participle(phrase):
            return
        # 中心命題・要旨から取った 1 語は固有名として大文字を要する（§21 A）。
        if not is_quality_keyphrase(
            phrase, require_uppercase_single=(source == KEYPHRASE_SOURCE_THESIS),
        ):
            return
        for existing in chosen_norms:
            if _contains_phrase(existing, norm) or _contains_phrase(norm, existing):
                return
        chosen.append((phrase, source))
        chosen_norms.append(norm)

    def _multi(cands: list[str]) -> list[str]:
        return [c for c in cands if len(_norm_phrase(c).split()) >= 2]

    def _single(cands: list[str]) -> list[str]:
        return [c for c in cands if len(_norm_phrase(c).split()) == 1]

    # 1. 両方に現れる句（題名の表記を優先し、題名に無い表記なら中心命題の表記）。
    both: list[str] = []
    for cand in title_cands + thesis_cands:
        norm = _norm_phrase(cand)
        if _contains_phrase(title_norm, norm) and _contains_phrase(thesis_norm, norm):
            both.append(cand)
    title_forms = {_norm_phrase(c): c for c in reversed(title_cands)}
    both = [title_forms.get(_norm_phrase(c), c) for c in _sorted(both)]

    # 2. 題名か中心命題に現れ、要旨にも現れる句（要旨が無ければ空）。
    in_summary: list[tuple[str, str]] = []
    if summary_norm.strip():
        seen_norms: set[str] = set()
        for cand in _sorted(title_cands + thesis_cands):
            norm = _norm_phrase(cand)
            if not norm or norm in seen_norms or not _in_summary(cand):
                continue
            seen_norms.add(norm)
            if _contains_phrase(title_norm, norm):
                in_summary.append((title_forms.get(norm, cand), KEYPHRASE_SOURCE_TITLE))
            else:
                in_summary.append((cand, KEYPHRASE_SOURCE_THESIS))

    # 3. 中心命題に繰り返し現れる句。
    repeated = [
        c for c in thesis_cands
        if _count_phrase(thesis_norm, _norm_phrase(c)) >= THESIS_MIN_OCCURRENCES
    ]
    indexed = list(dict.fromkeys(repeated))
    repeated = sorted(
        indexed,
        key=lambda c: (
            -_count_phrase(thesis_norm, _norm_phrase(c)),
            -len(_norm_phrase(c).split()),
            indexed.index(c),
        ),
    )

    # 4. 題名の残り — 連なり全体（4 語ずつの窓）だけ。部分列を使うと
    #    ``Kurtosis consistency`` のような句の切れ端がチップに出る。
    title_rest = list(dict.fromkeys(title_compounds + [
        " ".join(run[i:i + PHRASE_MAX_WORDS])
        for run in _phrase_runs(title_text)
        for i in range(0, len(run), PHRASE_MAX_WORDS)
    ]))
    # 題名にしか出ない 1 語は、略号（``EFTofLSS``）のときだけ採る。大文字 1 語の
    # 普通語・固有名（``God``）は単独では検索語にならない（§23）。
    title_rest_single_ok = {
        c for c in title_rest
        if len(_norm_phrase(c).split()) == 1 and (
            _is_acronym_like(c)
            or _contains_phrase(thesis_norm, _norm_phrase(c))
            or _contains_phrase(summary_norm, _norm_phrase(c))
        )
    }

    # 2 語以上の句を群の順に採ってから、1 語の句を採る（``gravity`` のような 1 語が
    # 先に入って ``gravity theories`` を包含で締め出さないように）。
    for cand in _multi(both):
        _try(cand, KEYPHRASE_SOURCE_TITLE)
    for cand, source in in_summary:
        if len(_norm_phrase(cand).split()) >= 2:
            _try(cand, source)
    for cand in _multi(repeated):
        _try(cand, KEYPHRASE_SOURCE_THESIS)
    for cand in _multi(title_rest):
        _try(cand, KEYPHRASE_SOURCE_TITLE)
    for _abbr, full in abbreviation_pairs:
        _try(
            full,
            KEYPHRASE_SOURCE_TITLE
            if _contains_phrase(title_norm, _norm_phrase(full)) else KEYPHRASE_SOURCE_THESIS,
        )
    for cand in _single(both):
        _try(cand, KEYPHRASE_SOURCE_TITLE)
    for cand, source in in_summary:
        if len(_norm_phrase(cand).split()) == 1:
            _try(cand, source)
    for cand in _single(repeated):
        _try(cand, KEYPHRASE_SOURCE_THESIS)
    for cand in _single(title_rest):
        if cand in title_rest_single_ok:
            _try(cand, KEYPHRASE_SOURCE_TITLE)

    # 5. 別枠（§20 C / §23）: 略語（フル表記の 2 本目）→ 固有名の 1 語。
    #    略語のフル表記に含まれる語（``Effective Field Theory …`` の ``Field``）と
    #    略語の複数形（``FoGs``）は固有名として採らない。
    extra_cap = MAX_PROPER_NOUN_KEYPHRASES
    abbreviation_norms = [
        _norm_phrase(x) for pair in abbreviations.items() for x in pair if _norm_phrase(x)
    ]
    proper = proper_noun_keyphrases(
        [thesis, summary_text], exclude_norms=chosen_norms + abbreviation_norms,
        limit=extra_cap,
    )
    added = 0

    for word in [abbr for abbr, _full in abbreviation_pairs] + proper:
        if added >= extra_cap:
            break
        before = len(chosen)
        _try(word, KEYPHRASE_SOURCE_THESIS, ceiling=len(chosen) + 1)
        added += len(chosen) - before
    return chosen


def title_keyphrases(title: Any, *, limit: int = MAX_TITLE_THESIS_KEYPHRASES) -> list[str]:
    """題名だけから名詞句を取り出す（:func:`title_thesis_keyphrases` の題名単独版）。"""
    return [phrase for phrase, _src in title_thesis_keyphrases(title, "", limit=limit)]


def _thesis_text_from_artifacts(artifacts: Any) -> str:
    """中心の問い + 中心命題 + 論文の目的の本文（無ければ空）。

    ``thesis_reconstruction`` の ``central_question`` / ``central_thesis`` と
    ``paper_skeleton`` の ``paper_goal``（§23 — 生成言語が日本語でも、目的の文に英語の
    用語の定義「Fingers of God（FoG）」が残ることが多い）。
    """
    if not isinstance(artifacts, dict):
        return ""
    payload = artifacts.get("thesis_reconstruction")
    if not isinstance(payload, dict):
        payload = {}
    parts: list[str] = []
    for key in ("central_question", "central_thesis"):
        value = payload.get(key)
        if isinstance(value, dict):
            value = value.get("text")
        text = _clean(value)
        if text:
            parts.append(text)
    skeleton = artifacts.get("paper_skeleton")
    if isinstance(skeleton, dict):
        goal = skeleton.get("paper_goal")
        if isinstance(goal, dict):
            goal = goal.get("text")
        text = _clean(goal)
        if text:
            parts.append(text)
    return " . ".join(parts)


def _seed_artifacts(session, document_id: str) -> dict:
    """seed の採用 run の artifact（正本 ``persistence.document_run_artifacts``・adopted）。"""
    if not document_id:
        return {}
    from core.document_pipeline.persistence import document_run_artifacts  # 遅延 import

    return document_run_artifacts(document_id, policy="adopted", session=session) or {}


def _cached_seed_summary(session, document_row: dict[str, str]) -> str:
    """seed の arXiv 要旨を**保存済みの写しだけ**から読む（arXiv を呼ばない・§20 C）。

    ID は登録済みの出所（``source_url``）か、ファイル名・教材名からの推定。写しが無い・
    読めないときは空（要旨の段を足さないだけ — 本文チャンクで代替しない）。
    """
    arxiv_id = normalize_arxiv_id(document_row.get("source_url")) or (
        arxiv_id_from_filename(document_row.get("filename") or "")
        or arxiv_id_from_filename(document_row.get("title") or "")
    )
    if not arxiv_id:
        return ""
    try:
        cached = metadata_cache.read_fresh(session, [arxiv_id])
    except Exception:  # noqa: BLE001 — 写しが読めなくても題名・中心命題で続ける
        logger.warning("radar cached seed summary unavailable (non-fatal)", exc_info=True)
        return ""
    entry = cached.get(normalize_arxiv_id(arxiv_id) or arxiv_id)
    return _clean(getattr(entry, "summary", "")) if entry is not None else ""


def body_full_form(session, document_id: str, abbreviation: str) -> str:
    """seed の本文（chunks）の「フル表記 (略語)」から略語のフル表記を引く（§23.5）。

    読むのは ``(略語`` を含むチャンクを先頭から最大 :data:`BODY_ABBREVIATION_MAX_CHUNKS` 件だけ
    （SELECT のみ・LLM / embedding / 外部 API なし）。規則は正本 ``core/abbreviations``
    （initials が合う最短の接尾辞）。見つからない・読めないときは空（略語のまま残す）。
    本文は第三者が書いた untrusted 入力だが、ここで取り出すのは英字・ハイフン・空白だけの句
    （``ABBR_AFTER_FULL_RE``）で、検索語として句の引用符の内側に入るだけ。
    """
    abbr = str(abbreviation or "").strip()
    if not document_id or not is_short_abbreviation(abbr):
        return ""
    from core.abbreviations import extract_abbreviations  # 遅延 import（純関数）

    rows = session.execute(
        sa_text(
            """
            SELECT text
              FROM chunks
             WHERE document_id = CAST(:document_id AS uuid)
               AND (strpos(text, :half) > 0 OR strpos(text, :full_width) > 0)
             ORDER BY chunk_index
             LIMIT :limit
            """
        ),
        {
            "document_id": document_id,
            "half": "(" + abbr,
            "full_width": "\uff08" + abbr,
            "limit": BODY_ABBREVIATION_MAX_CHUNKS,
        },
    ).fetchall()
    found = extract_abbreviations(
        [row[0] for row in rows], shortest=True, normalize_width=True,
    )
    return str(found.get(abbr) or found.get(abbr + "s") or "")


def seed_keyphrase_candidates(
    session, document_row: dict[str, str], *, summary: Optional[str] = None,
) -> list[dict]:
    """seed 教材からキーフレーズ候補を段ごとに供給する（PD3 — 出所を必ず明示する）。

    段の順（設計書 §17。上から詰めて :data:`MAX_SEED_KEYPHRASES` で止める）:

    1. 承認済み理論部品（``component``）
    2. 確認済みの地図上の位置の骨格ノードラベル（``skeleton``）
    3. live 主張の概念名（``concept``。記号・文のような名前は除く）
    4. 題名と中心命題（``thesis_reconstruction``）の名詞句（``title`` / ``thesis``・
       :func:`title_thesis_keyphrases` の併合順・最大 :data:`MAX_TITLE_THESIS_KEYPHRASES`）。
       seed の arXiv 要旨（``summary``。``None`` なら保存済みの写しだけを読む —
       arXiv は呼ばない）を併合順の材料と固有名の供給に使う（§20 B / C）

    承認前の部品は使わない（確定は人間 — PD3 / KR2）。同じ語は先の段が勝つ（出所を
    後の段で上書きしない）。各段は fail-soft（落ちた段は抜けるだけ — PR7）。
    """
    refs = _document_refs_of(document_row)
    if not refs:
        return []
    document_id = document_row.get("document_id", "")

    def _title_thesis() -> list[tuple[str, str]]:
        try:
            thesis = _thesis_text_from_artifacts(_seed_artifacts(session, document_id))
        except Exception:  # noqa: BLE001 — 中心命題が読めなくても題名だけで続ける
            logger.warning("radar thesis artifact unavailable (non-fatal)", exc_info=True)
            thesis = ""
        abstract = summary if summary is not None else _cached_seed_summary(session, document_row)
        return title_thesis_keyphrases(
            document_row.get("title") or "", thesis, summary=abstract or "",
            body_full_form=lambda abbr: body_full_form(session, document_id, abbr),
        )

    stages = (
        (
            KEYPHRASE_SOURCE_COMPONENT,
            lambda: [(p, KEYPHRASE_SOURCE_COMPONENT) for p in _approved_component_phrases(session, refs)],
        ),
        (
            KEYPHRASE_SOURCE_SKELETON,
            lambda: [(p, KEYPHRASE_SOURCE_SKELETON) for p in _confirmed_placement_phrases(session, document_id)],
        ),
        (
            KEYPHRASE_SOURCE_CONCEPT,
            lambda: [(p, KEYPHRASE_SOURCE_CONCEPT) for p in _claim_concept_phrases(session, refs)],
        ),
        (KEYPHRASE_SOURCE_TITLE, _title_thesis),
    )

    out: list[dict] = []
    seen: set[str] = set()
    for stage, supplier in stages:
        if len(out) >= MAX_SEED_KEYPHRASES:
            break
        try:
            phrases = supplier()
        except Exception:  # noqa: BLE001 — 1段の失敗で他の段を巻き添えにしない
            logger.warning(
                "radar keyphrase supplier %s failed (non-fatal)", stage, exc_info=True,
            )
            continue
        for value, source in phrases:
            if len(out) >= MAX_SEED_KEYPHRASES:
                break
            text = _clean(value)
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            out.append({"text": text, "source": source, "enabled": True})
    return out


def keyphrase_supply(candidates: Any) -> dict:
    """``seed["keyphrase_supply"]`` — 供給の出所（段の順）と事実文（PD3 / PR7）。

    ``note`` は承認済みの理論部品から取れたときは ``None``（毎回出すと景色になる）。
    件数は出さない（PR2）。
    """
    sources: list[str] = []
    for item in candidates or ():
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "")
        if source and source not in sources:
            sources.append(source)
    if not sources:
        note: Optional[str] = NOTE_KEYPHRASE_SUPPLY_EMPTY
    elif KEYPHRASE_SOURCE_COMPONENT not in sources:
        note = NOTE_KEYPHRASE_SUPPLY_WITHOUT_APPROVED
    else:
        note = None
    return {"sources": sources, "note": note}


def arxiv_blocked() -> bool:
    """いま arXiv への問い合わせを止めている最中か（読み取りのみ・fail-soft）。

    正本は :func:`arxiv_client.cooldown_active`。**残り時間は読まない**（窓の秒数を
    返す関数は、このモジュールからも route からも参照しない — 数値を人に見せないため。
    PR2 / §14.7。ガードレールが識別子の不在で固定する）。状態を読めない異常時は
    「止めていない」に倒す（余計なブロック表示で操作を塞がない）。
    """
    try:
        return bool(arxiv_client.cooldown_active())
    except Exception:  # noqa: BLE001 — 状態が読めないだけで探索は成立させる
        logger.debug("arXiv cooldown state unavailable", exc_info=True)
        return False


def _mark_arxiv_blocked(seed: dict) -> None:
    """seed に「制限されているか」の目印を付ける（常在キー — §14.7）。

    **キーは常に付ける**（不在で黙らせない）。真のときだけ事実文
    :data:`NOTE_ARXIV_BLOCKED` を併記する。

    判定は「クールダウン中」**または**「この取得がまさに 429 で失敗した」。後者を
    足すのは、429 を受けた瞬間（クールダウンを立てた直後）のこの応答でも目印が真に
    なるようにするため — 制限されている事実は、窓が立ったかどうかより先に観測できる。
    """
    blocked = (
        arxiv_blocked()
        or seed.get("metadata_failure") == METADATA_FAILURE_RATE_LIMITED
    )
    seed["arxiv_blocked"] = blocked
    if blocked:
        seed["arxiv_blocked_note"] = NOTE_ARXIV_BLOCKED


def _fetch_seed_entry(session, seed: dict, arxiv_id: str):
    """seed のメタデータを1件だけ解決する（引けなければ ``None``）。

    **保存済みの写し（migration 085）を先に読む**（設計書 §14 — 教員の一連の操作で
    arXiv API を叩くのは最大2回）。新鮮な写しがあれば arXiv を呼ばず、
    ``seed["metadata_source"]`` に出所（``cache`` / ``arxiv``）を残す。画面を開き
    直すたびに同じ論文のメタデータを引き直さないための読み時キャッシュで、
    保存するのは外部事実だけ（候補も教員の判断も残さない — PD5 / CC3 同型）。

    PR7 の要（かなめ）: **引けなかったことを黙って空カテゴリにしない**。取得できない
    理由は3つあり、教員の次の一手がそれぞれ違うので事実文も分ける。

    - アクセスの制限（HTTP 429）→ :data:`NOTE_ARXIV_RATE_LIMITED`（時間をおくしかない）
    - その他の到達・解釈の失敗 → :data:`NOTE_ARXIV_METADATA_UNAVAILABLE`
    - 200 で返ったが該当 ID の項目が無い → :data:`NOTE_ARXIV_METADATA_NOT_FOUND`
      （撤回・ID 誤りなど。**到達失敗と混ぜない**）

    いずれも例外にせず ``None`` を返す（検索そのものは成立させ、購読フォールバックへ
    続ける）。note は ``seed`` に載せるので、呼び出し側は分岐を持たない。
    """
    cached = metadata_cache.read_fresh(session, [arxiv_id]).get(
        normalize_arxiv_id(arxiv_id) or arxiv_id
    )
    if cached is not None:
        seed["metadata_source"] = METADATA_SOURCE_CACHE
        return cached

    try:
        entries = arxiv_client.fetch_by_ids([arxiv_id])
    except arxiv_client.ArxivRateLimitedError:
        logger.info("radar seed metadata rate-limited for %s", arxiv_id)
        seed["note"] = NOTE_ARXIV_RATE_LIMITED
        seed["metadata_failure"] = METADATA_FAILURE_RATE_LIMITED
        return None
    except arxiv_client.ArxivApiError:
        # seed のメタデータが引けなくても検索そのものは成立させる（PR7）。
        logger.info("radar seed metadata unavailable for %s", arxiv_id)
        seed["note"] = NOTE_ARXIV_METADATA_UNAVAILABLE
        seed["metadata_failure"] = METADATA_FAILURE_UNAVAILABLE
        return None
    if not entries:
        logger.info("radar seed metadata not found for %s", arxiv_id)
        seed["note"] = NOTE_ARXIV_METADATA_NOT_FOUND
        seed["metadata_failure"] = METADATA_FAILURE_NOT_FOUND
        return None
    # 引けたものだけを写しとして残す（失敗はキャッシュしない）。
    metadata_cache.remember(session, entries[:1])
    seed["metadata_source"] = METADATA_SOURCE_ARXIV
    return entries[0]


def resolve_seed(session, document_id: str, *, fetch_arxiv: bool = True) -> dict:
    """seed 教材のメタデータ・検索条件の供給元を解決する（PR1 — 保存しない）。

    カテゴリの供給順（設計書 §5.1）:

    1. ``documents.source_url`` から arXiv ID が取れ、``fetch_arxiv=True`` なら
       :func:`arxiv_client.fetch_by_ids` のメタデータ（``categories_source="arxiv"``。
       要旨も同時に得る）。**arXiv 到達の失敗・該当なしは fail-soft** で 3. へ縮退し、
       :func:`_fetch_seed_entry` が理由ごとに別の ``note`` を残す（黙って条件を
       すり替えない — PR7）。
    2. ``source_url`` が空でも、ファイル名（次点でタイトル）から arXiv ID を
       **推定**できて ``fetch_arxiv=True`` なら、そのメタデータ
       （``categories_source="arxiv_inferred"``）。推定であることは供給元語彙と
       ``provenance`` の両方に残し、``documents`` へは**書かない**（記帳は教員の
       明示操作だけ — 後付け登録 API の責務）。
    3. seed の分野（:func:`corpus.document_domain_keys` の先頭）の購読
       ``arxiv_categories``（``categories_source="subscription"``）。
    4. どれも無ければ空（``categories_source="manual"`` = 教員の手入力待ち。
       **条件ゼロでは arXiv を呼ばない** — PD6）。

    Returns:
        ``{document_id, title, arxiv_id, abs_url, summary, categories,
        categories_source, keyphrase_candidates, domain_key, provenance,
        arxiv_blocked, arxiv_blocked_note?, note?, metadata_source?,
        metadata_failure?}``。``metadata_source`` はメタデータを引けたときだけ付く
        事実ラベル（``arxiv`` = 今引いた / ``cache`` = 保存済みの写しを読んだ）。
        ``arxiv_blocked`` は**常に付く**（arXiv からアクセスを制限されている最中か。
        真のときだけ ``arxiv_blocked_note`` を併記 — §14.7）。``metadata_failure``
        （``rate_limited`` / ``unavailable`` / ``not_found``）は**引けなかったときだけ**
        付く機械可読の理由で、``note`` の文面と1対1に対応する。
        ``arxiv_id`` / ``abs_url`` は **``source_url`` に登録済みの場合だけ**入る
        （推定 ID をここへ混ぜない — 判定不能・未記帳を偽装しない）。推定の結果は
        ``provenance``（``{status, arxiv_id, arxiv_title, arxiv_abs_url,
        document_title, title_match, fetched}``）で読む。
        ``provenance`` に権限情報（``can_register``）は入れない — core はユーザーを
        知らないので、必要なら route 層が注入する。

    Raises:
        LookupError: document が存在しない（権限判定は route 層の責務）。
    """
    row = _document_row(session, document_id)
    if row is None:
        raise LookupError("document not found")

    arxiv_id = normalize_arxiv_id(row.get("source_url"))
    inferred_id = ""
    if not arxiv_id:
        # 記帳されていない教材のみ推定する（登録済みの出所を推定で上書きしない）。
        inferred_id = arxiv_id_from_filename(row.get("filename") or "") or (
            arxiv_id_from_filename(row.get("title") or "")
        )

    domain_keys = corpus.document_domain_keys(session, row["document_id"])
    domain_key = domain_keys[0] if domain_keys else ""

    if arxiv_id:
        provenance_status = PROVENANCE_STATUS_REGISTERED
    elif inferred_id:
        provenance_status = PROVENANCE_STATUS_INFERRED
    else:
        provenance_status = PROVENANCE_STATUS_NONE

    provenance: dict[str, Any] = {
        "status": provenance_status,
        "arxiv_id": arxiv_id or inferred_id or None,
        "arxiv_title": "",
        "arxiv_abs_url": None,
        "document_title": row.get("title") or "",
        "title_match": False,
        "fetched": False,
    }

    seed: dict[str, Any] = {
        "document_id": row["document_id"],
        "title": row.get("title") or "",
        "arxiv_id": arxiv_id,
        "abs_url": abs_url_for(arxiv_id) if arxiv_id else None,
        "summary": "",
        "categories": [],
        # 主カテゴリだけ既定で有効・副カテゴリは enabled=False（§19 B）。
        "category_candidates": [],
        "primary_category": "",
        "categories_source": CATEGORIES_SOURCE_MANUAL,
        # キーフレーズはメタデータ（要旨）を解決した後に供給する（§20 C）。
        "keyphrase_candidates": [],
        "keyphrase_supply": keyphrase_supply([]),
        "domain_key": domain_key,
        "provenance": provenance,
    }

    def _apply_arxiv_categories(entry, source: str) -> None:
        enabled, candidates = category_candidates(
            list(entry.categories or ()), entry.primary_category
        )
        if enabled:
            seed["categories"] = enabled
            seed["category_candidates"] = candidates
            seed["primary_category"] = enabled[0]
            seed["categories_source"] = source

    if arxiv_id and fetch_arxiv:
        entry = _fetch_seed_entry(session, seed, arxiv_id)
        if entry is not None:
            seed["summary"] = entry.summary or ""
            seed["arxiv_title"] = _clean(entry.title)
            if entry.abs_url:
                seed["abs_url"] = entry.abs_url
            _apply_arxiv_categories(entry, CATEGORIES_SOURCE_ARXIV)
    elif inferred_id and fetch_arxiv:
        # 推定 ID でも取得の失敗は黙らせず、購読フォールバックへ続ける（PR7）。
        entry = _fetch_seed_entry(session, seed, inferred_id)
        if entry is not None:
            provenance["fetched"] = True
            provenance["arxiv_title"] = entry.title or ""
            provenance["arxiv_abs_url"] = entry.abs_url or abs_url_for(inferred_id)
            seed["arxiv_title"] = _clean(entry.title)
            # 照合相手は教材名と、論文本文から復元した題名の2つ（§19 E）。教材名が
            # ファイル名のときも、本文の題名が一致すれば自動記帳の条件を満たす
            # （一致の判定 = ``titles_match`` の完全一致は不変 — §11.3）。
            if titles_match(entry.title or "", row.get("title") or ""):
                provenance["title_match"] = True
                provenance["title_match_via"] = "document_title"
            else:
                try:
                    paper_title = _paper_title_from_artifacts(
                        _seed_artifacts(session, row["document_id"])
                    )
                except Exception:  # noqa: BLE001 — 本文の題名が読めなくても照合を続ける
                    logger.warning("radar paper title unavailable (non-fatal)", exc_info=True)
                    paper_title = ""
                if paper_title:
                    provenance["paper_title"] = paper_title
                if paper_title and titles_match(entry.title or "", paper_title):
                    provenance["title_match"] = True
                    provenance["title_match_via"] = "paper_title"
            seed["summary"] = entry.summary or ""
            _apply_arxiv_categories(entry, CATEGORIES_SOURCE_ARXIV_INFERRED)

    if not seed["categories"] and domain_key:
        subscription = store.get_subscription(session, domain_key) or {}
        categories = normalize_categories(subscription.get("arxiv_categories"))
        if categories:
            # 購読の条件は教員が選んだものなので、すべて有効のまま並べる。
            seed["categories"] = categories
            seed["category_candidates"] = _all_enabled_candidates(categories)
            seed["categories_source"] = CATEGORIES_SOURCE_SUBSCRIPTION

    # 要旨は上で解決したもの（写しを先に読む ``_fetch_seed_entry`` 経由）だけを使う。
    # ``fetch_arxiv=False``・取得失敗では空のまま（写しを読み直さない・arXiv の呼び出しも
    # 増やさない — §14 / §20 C）。
    keyphrases = seed_keyphrase_candidates(session, row, summary=seed["summary"] or "")
    seed["keyphrase_candidates"] = keyphrases
    seed["keyphrase_supply"] = keyphrase_supply(keyphrases)

    # 教材名がファイル名のままで arXiv の題名が引けたら、見出しは arXiv の題名にする（§19 E）。
    if title_looks_like_filename(seed["title"]) and seed.get("arxiv_title"):
        seed["display_title"], seed["title_note"] = display_title_for(
            seed["title"], seed["arxiv_title"]
        )

    # 取得を試みた**後**に見る（この操作で 429 を受けた場合もここで真になる — §14.7）。
    _mark_arxiv_blocked(seed)
    return seed


def seed_placed_node_ids(session, document_id: str, domain_key: str) -> set[str]:
    """起点論文が**その分野の地図で既に配置されている**ノード ID（読み取りのみ）。

    「新しい面」チップ（設計書 §4.2 / VA層 §8）の差集合の材料。生きている配置
    （``superseded`` / ``rejected`` 以外 = ``inferred`` / ``confirmed`` /
    ``review_required``）の ``node_id`` を集める。AI 推定（``inferred``）も
    「もう地図に現れている面」として扱う — 未確定の推定を「新しい面」として
    二重に見せないための慎重側（PR2 / LS3）。

    件数はここから外へ出さない（集合の要素だけを返す — 数を見せない PR2 / LS5）。
    domain_key 不明・document_id が UUID として解釈できない・表が読めないは
    いずれも**空集合**へ fail-soft（チップが出ないだけで検索は成立する — PR7）。
    """
    ref = _clean(document_id)
    domain = _clean(domain_key)
    if not ref or not domain:
        return set()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT DISTINCT node_id
                  FROM landscape_placements
                 WHERE document_id = CAST(:document_id AS uuid)
                   AND domain_key = :domain_key
                   AND status NOT IN ('superseded', 'rejected')
                """
            ),
            {"document_id": ref, "domain_key": domain},
        ).fetchall()
    except Exception:  # noqa: BLE001 — 配置が読めないだけ（検索は成立させる）
        logger.warning(
            "landscape placements unavailable for document %s (non-fatal)",
            ref, exc_info=True,
        )
        return set()
    return {str(row[0] or "").strip() for row in rows if str(row[0] or "").strip()}


#: 取り込み済み候補の構造照合が成立しない理由（§21 C。照合できなかった側を名指しする —
#: 「同じ構造の論文は見つかっていません」と読ませない）。
FACT_MODULES_SEED_UNSAVED = (
    "起点の論文の理論モジュールがまだ保存されていないため、構造は照合していません。"
)
FACT_MODULES_CANDIDATE_UNSAVED = (
    "この論文の理論モジュールがまだ保存されていないため、構造は照合していません。"
)


#: 両側とも保存済みのときの事実文（§22 A — 第 5 波。「照合したが一致なし」と「照合の対象外」を
#: 畳まない。照合は工程の形の完全一致で、記号・概念の対応は見ていないことを毎回添える）。
FACT_MODULES_SCOPE = "照合は工程の形の完全一致で、記号・概念の対応は見ていません。"
FACT_MODULES_STALE = (
    "どちらかの論文の理論モジュールが以前の規則で保存されているため、構造は照合していません。"
)
FACT_MODULES_NO_ELIGIBLE_BOTH = (
    "起点の論文にもこの論文にも、照合の対象になる構造を持つモジュールがありません"
    "（工程が少ないか、単一の工程の型だけでできているため）。"
)
FACT_MODULES_NO_ELIGIBLE_SEED = (
    "起点の論文には、照合の対象になる構造を持つモジュールがありません"
    "（工程が少ないか、単一の工程の型だけでできているため）。"
)
FACT_MODULES_NO_ELIGIBLE_CANDIDATE = (
    "この論文には、照合の対象になる構造を持つモジュールがありません"
    "（工程が少ないか、単一の工程の型だけでできているため）。"
)
FACT_MODULES_NO_MATCH = (
    "両方の理論モジュールを照合しましたが、工程の形が同じモジュールはありません。" + FACT_MODULES_SCOPE
)
FACT_MODULES_MATCHED = (
    "起点の論文と工程の形が同じモジュールがあります。" + FACT_MODULES_SCOPE
)


def module_match_note(
    seed_saved: bool,
    candidate_saved: bool,
    *,
    seed_fingerprints: Optional[set] = None,
    candidate_fingerprints: Optional[set] = None,
    stale: bool = False,
) -> Optional[str]:
    """構造照合の成立条件の事実文（純関数）。

    片側が未保存ならその側を名指しする（§21 C）。両側とも保存済みで照合の対象の指紋集合が
    渡されたら、「対象外」「照合したが一致なし」「一致あり」を言い分ける（§22 A）。指紋は
    比較にだけ使い、文面にも DTO にも出さない（TM12）。指紋が渡されなければ ``None``（旧来）。
    """
    if not seed_saved:
        return FACT_MODULES_SEED_UNSAVED
    if not candidate_saved:
        return FACT_MODULES_CANDIDATE_UNSAVED
    if seed_fingerprints is None or candidate_fingerprints is None:
        return None
    if stale:
        return FACT_MODULES_STALE
    if not seed_fingerprints and not candidate_fingerprints:
        return FACT_MODULES_NO_ELIGIBLE_BOTH
    if not seed_fingerprints:
        return FACT_MODULES_NO_ELIGIBLE_SEED
    if not candidate_fingerprints:
        return FACT_MODULES_NO_ELIGIBLE_CANDIDATE
    if set(seed_fingerprints) & set(candidate_fingerprints):
        return FACT_MODULES_MATCHED
    return FACT_MODULES_NO_MATCH


def attach_module_match_notes(session, seed_document_id: str, candidates: list[dict]) -> None:
    """取り込み済み候補に ``module_match_note`` を付ける（§21 C / §22 A・fail-soft・件数なし）。"""
    targets = [c for c in candidates if isinstance(c, dict) and _clean(c.get("document_id"))]
    seed_id = _clean(seed_document_id)
    if not targets or not seed_id:
        return
    from core.library.structural_matches import module_match_states  # 遅延 import
    from core.theory_modules.schema import RULE_VERSION  # 遅延 import

    try:
        states = module_match_states(
            session, [seed_id] + [_clean(c["document_id"]) for c in targets],
            rule_version=RULE_VERSION,
        )
    except Exception:  # noqa: BLE001 — 保存の有無が読めないなら何も言わない
        logger.warning("radar module match note unavailable (non-fatal)", exc_info=True)
        return
    seed_state = states.get(seed_id)
    for cand in targets:
        cand_state = states.get(_clean(cand["document_id"]))
        note = module_match_note(
            seed_state is not None,
            cand_state is not None,
            seed_fingerprints=(seed_state or {}).get("fingerprints"),
            candidate_fingerprints=(cand_state or {}).get("fingerprints"),
            stale=bool((seed_state or {}).get("stale") or (cand_state or {}).get("stale")),
        )
        if note:
            cand["module_match_note"] = note


# ---------------------------------------------------------------------------
# 取り込み済み候補の「配置の対比」（§21 B・読み時導出・数値なし）
# ---------------------------------------------------------------------------

#: 対比に使う配置の status（AI 推定と教員確認。確認待ち・却下・置き換え済みは使わない）。
PLACEMENT_CONTRAST_STATUSES = ("confirmed", "inferred")
#: 片側に生きた配置が無いときの事実文（対比を空の列で黙らせない — PR7）。
FACT_CONTRAST_SEED_UNPLACED = (
    "起点の論文はまだ分野の地図に配置されていないため、配置を対比していません。"
)
FACT_CONTRAST_CANDIDATE_UNPLACED = (
    "この論文はまだ分野の地図に配置されていないため、配置を対比していません。"
)

#: 片側にだけ配置のある観点の事実文（§22.4 — 第 6 波）。起点だけの方法の観点の項目が
#: 「方法が違う」に見えないよう、相手側にその観点の配置が 1 件も無いことを観点ごとに言う。
#: ``{perspective}`` は観点の名詞（「方法」— ``core.landscape.schema.perspective_label`` から
#: 「から」を外したもの。新しい訳語表は作らない）。
FACT_CONTRAST_PERSPECTIVE_ABSENT_CANDIDATE = (
    "この論文には{perspective}の観点の配置がありません"
    "（起点だけに見える{perspective}の項目は、{perspective}が違うことを示すものではありません）。"
)
FACT_CONTRAST_PERSPECTIVE_ABSENT_SEED = (
    "起点の論文には{perspective}の観点の配置がありません"
    "（この論文だけに見える{perspective}の項目は、{perspective}が違うことを示すものではありません）。"
)


def _perspective_noun(perspective: str) -> str:
    from core.landscape.schema import perspective_label  # 遅延 import

    label = perspective_label(perspective)
    return label[: -len("から")] if label.endswith("から") else label


def _side_perspectives(rows: Any, labels: Mapping[tuple[str, str], str]) -> set[str]:
    """1 論文の（現行骨格にある）配置に現れる観点の集合。"""
    out: set[str] = set()
    for slot in _contrast_perspectives(rows, labels).values():
        out.update(slot)
    return out


def _contrast_side(rows: Any, labels: Mapping[tuple[str, str], str]) -> dict[tuple[str, str], bool]:
    """1 論文の配置行 → ``{(domain_key, node_id): confirmed?}``（現行骨格にあるノードだけ）。

    同じノードに複数の観点があれば、どれか 1 つが ``confirmed`` なら確認済み。
    """
    out: dict[tuple[str, str], bool] = {}
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        key = (_clean(row.get("domain_key")), _clean(row.get("node_id")))
        if not key[0] or not key[1] or key not in labels:
            continue  # 現行骨格に無いノードのラベルを捏造しない
        confirmed = _clean(row.get("status")) == "confirmed"
        out[key] = out.get(key, False) or confirmed
    return out


def _contrast_perspectives(rows: Any, labels: Mapping[tuple[str, str], str]) -> dict[tuple[str, str], list[str]]:
    """1 論文の配置行 → ``{(domain_key, node_id): [観点キー, …]}``（正本の順・語彙内だけ）。"""
    from core.landscape.schema import PERSPECTIVES  # 遅延 import

    out: dict[tuple[str, str], list[str]] = {}
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        key = (_clean(row.get("domain_key")), _clean(row.get("node_id")))
        if key not in labels:
            continue
        perspective = _clean(row.get("perspective"))
        if perspective not in PERSPECTIVES:
            continue  # 語彙外の観点を生のまま出さない
        slot = out.setdefault(key, [])
        if perspective not in slot:
            slot.append(perspective)
    for slot in out.values():
        slot.sort(key=PERSPECTIVES.index)
    return out


def placement_contrast(
    seed_rows: Any,
    candidate_rows: Any,
    *,
    labels: Mapping[tuple[str, str], str],
    skeleton_versions: Mapping[str, str],
    layers: Optional[Mapping[tuple[str, str], str]] = None,
) -> Optional[dict]:
    """起点と取り込み済み候補の配置を **重なり / 起点だけ / 候補だけ** に分ける（純関数・§21 B）。

    各項目は ``{"label", "provenance_label"}`` だけ（weight・confidence・件数を載せない — PR2 /
    LS5）。出所ラベルは配置の status から（``core.landscape.schema.provenance_label``）で、
    重なりは**両側とも確認済み**のときだけ「教員確認済み」（慎重側 — LS2）。どちらかの側に
    現行骨格の配置が 1 つも無ければ ``None``（呼び出し側が事実文を出す）。

    §22 C（第 5 波）: 各項目に ``perspective_label``（主たる観点の日本語 = 重なりは両側に共通の
    観点を優先・正本 ``core.landscape.schema.perspective_label``）と ``perspective_labels``
    （その項目に付いた観点すべて）、``layer``（``region`` / ``concept``・骨格の段）を足す。
    """
    from core.landscape.schema import PERSPECTIVES, perspective_label, provenance_label  # 遅延 import

    seed = _contrast_side(seed_rows, labels)
    cand = _contrast_side(candidate_rows, labels)
    if not seed or not cand:
        return None
    order = list(labels)
    seed_persp = _contrast_perspectives(seed_rows, labels)
    cand_persp = _contrast_perspectives(candidate_rows, labels)
    layer_of = layers or {}

    def _item(key: tuple[str, str], confirmed: bool) -> dict:
        mine = seed_persp.get(key, [])
        theirs = cand_persp.get(key, [])
        common = [p for p in mine if p in theirs]
        union = sorted(set(mine) | set(theirs), key=PERSPECTIVES.index)
        primary = (common or union or [""])[0]
        item = {
            "label": labels[key],
            "provenance_label": provenance_label("confirmed" if confirmed else "inferred"),
            "perspective_label": perspective_label(primary) if primary else "",
            "perspective_labels": [perspective_label(p) for p in union],
        }
        layer = layer_of.get(key)
        if layer in ("region", "concept"):
            item["layer"] = layer
        return item

    def _sorted(keys: Any) -> list[tuple[str, str]]:
        return sorted(keys, key=order.index)

    shared = [_item(k, seed[k] and cand[k]) for k in _sorted(set(seed) & set(cand))]
    seed_only = [_item(k, seed[k]) for k in _sorted(set(seed) - set(cand))]
    cand_only = [_item(k, cand[k]) for k in _sorted(set(cand) - set(seed))]
    domains = sorted({k[0] for k in list(seed) + list(cand)})
    versions = [
        f"{domain} 版{skeleton_versions.get(domain)}" if len(domains) > 1 else str(skeleton_versions.get(domain) or "")
        for domain in domains
        if skeleton_versions.get(domain)
    ]
    # 観点ごとの有無（§22.4）。片側にだけ配置のある観点を、相手側の「配置なし」として言う
    # （配置の無い観点を「違い」に見せない・捏造しない — upstream-loss-masked-downstream）。
    seed_kinds = _side_perspectives(seed_rows, labels)
    cand_kinds = _side_perspectives(candidate_rows, labels)
    perspective_facts: list[str] = []
    for perspective in PERSPECTIVES:
        if perspective in seed_kinds and perspective not in cand_kinds:
            perspective_facts.append(
                FACT_CONTRAST_PERSPECTIVE_ABSENT_CANDIDATE.format(perspective=_perspective_noun(perspective))
            )
        elif perspective in cand_kinds and perspective not in seed_kinds:
            perspective_facts.append(
                FACT_CONTRAST_PERSPECTIVE_ABSENT_SEED.format(perspective=_perspective_noun(perspective))
            )
    return {
        "shared": shared,
        "seed_only": seed_only,
        "candidate_only": cand_only,
        "skeleton_version": "、".join(versions),
        "perspective_facts": perspective_facts,
    }


def _skeleton_labels(session, domain_keys: Any) -> tuple:
    """現行の学習者骨格から ``{(domain, node_id): label}`` と ``{domain: version}`` と
    ``{(domain, node_id): "region"|"concept"}``（§22 C の粒度）。"""
    from core import atlas_store  # 遅延 import

    labels: dict[tuple[str, str], str] = {}
    versions: dict[str, str] = {}
    layers: dict[tuple[str, str], str] = {}
    for domain in sorted({_clean(d) for d in domain_keys or () if _clean(d)}):
        try:
            skeleton = atlas_store.load_learner_skeleton(domain, session)
        except Exception:  # noqa: BLE001 — 骨格が読めない分野は対比に使わないだけ
            logger.warning("radar contrast skeleton unavailable for %s", domain, exc_info=True)
            continue
        if skeleton is None:
            continue
        versions[domain] = _clean(getattr(skeleton, "version", ""))
        for region in getattr(skeleton, "regions", ()) or ():
            region_id = _clean(getattr(region, "id", ""))
            concepts = list(getattr(region, "concepts", ()) or ())
            for concept in concepts:
                concept_id = _clean(getattr(concept, "id", ""))
                label = _clean(getattr(concept, "label", ""))
                if concept_id and label:
                    labels[(domain, concept_id)] = label
                    layers[(domain, concept_id)] = "concept"
            label = _clean(getattr(region, "label", ""))
            if region_id and label:
                labels[(domain, region_id)] = label
                layers[(domain, region_id)] = "region"
    return labels, versions, layers


def attach_placement_contrasts(session, seed_document_id: str, candidates: list[dict]) -> None:
    """取り込み済み候補（``document_id`` あり）に ``placement_contrast`` を付ける（§21 B）。

    読み口は ``core.landscape.store.list_for_documents``（起点 + 候補を 1 回で読む）。
    片側に配置が無ければ ``placement_contrast_note``（事実文）だけを付ける。失敗は
    fail-soft（キーが付かないだけ — 検索は成立する — PR7）。
    """
    targets = [c for c in candidates if isinstance(c, dict) and _clean(c.get("document_id"))]
    seed_id = _clean(seed_document_id)
    if not targets or not seed_id:
        return
    from core.landscape import store as landscape_store  # 遅延 import

    try:
        ids = [seed_id] + [_clean(c["document_id"]) for c in targets]
        rows = landscape_store.list_for_documents(
            session, ids, statuses=PLACEMENT_CONTRAST_STATUSES,
        )
        by_doc: dict[str, list[dict]] = {}
        for row in rows:
            by_doc.setdefault(_clean(row.get("document_id")), []).append(row)
        skeleton = _skeleton_labels(
            session, {_clean(r.get("domain_key")) for r in rows}
        )
        labels, versions = skeleton[0], skeleton[1]
        layers = skeleton[2] if len(skeleton) > 2 else {}
    except Exception:  # noqa: BLE001 — 配置が読めないだけ
        logger.warning("radar placement contrast unavailable (non-fatal)", exc_info=True)
        return
    seed_rows = by_doc.get(seed_id, [])
    seed_has = bool(_contrast_side(seed_rows, labels))
    for cand in targets:
        cand_rows = by_doc.get(_clean(cand["document_id"]), [])
        if not seed_has:
            cand["placement_contrast_note"] = FACT_CONTRAST_SEED_UNPLACED
            continue
        contrast = placement_contrast(
            seed_rows, cand_rows, labels=labels, skeleton_versions=versions, layers=layers,
        )
        if contrast is None:
            cand["placement_contrast_note"] = FACT_CONTRAST_CANDIDATE_UNPLACED
        else:
            cand["placement_contrast"] = contrast


# ---------------------------------------------------------------------------
# 承認済み部品との語彙照合（非ベクトル・純関数）
# ---------------------------------------------------------------------------


#: 語彙照合で使う最短ラベル長（これ未満は部分一致の誤爆が多すぎるので見送る）。
MIN_OVERLAP_LABEL_CHARS = 3


def overlap_component_labels(
    candidate: dict,
    component_labels: Any,
    *,
    limit: int = 6,
) -> list[str]:
    """候補の題名・要旨に現れる、起点論文の**承認済み部品ラベル**（決定論・非LLM）。

    「重なり」チップ（設計書 §4.2）の材料。ベクトルも LLM も使わない素朴な
    部分一致で、`ラベルがそのまま候補の文面に現れた` という**観測できる事実**
    だけを返す（意味の近さを主張しない — PR2）。

    規律:

    - 照合は casefold 同士の部分一致。並びは**入力順**（``component_labels`` は
      :func:`seed_keyphrase_candidates` の決定論的な並びで来る）。
    - :data:`MIN_OVERLAP_LABEL_CHARS` 未満のラベルは飛ばす（``CP`` のような短い
      表記が無関係な語の一部に当たる誤爆を避ける）。
    - 一致数・スコアは返さない（ラベル文字列だけ — PR2 / LS5）。
    """
    text = " ".join(
        [
            str(candidate.get("title") or ""),
            str(candidate.get("summary") or ""),
        ]
    ).casefold()
    if not text.strip() or int(limit or 0) <= 0:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for value in component_labels or ():
        label = _clean(value)
        key = label.casefold()
        if len(label) < MIN_OVERLAP_LABEL_CHARS or key in seen:
            continue
        if key not in text:
            continue
        seen.add(key)
        out.append(label)
        if len(out) >= int(limit):
            break
    return out


def _seed_component_labels(seed: dict) -> list[str]:
    """seed の供給候補のうち、承認済み理論部品（``source="component"``）のラベル。"""
    return [
        _clean(item.get("text"))
        for item in (seed.get("keyphrase_candidates") or [])
        if isinstance(item, dict)
        and str(item.get("source") or "") == "component"
        and _clean(item.get("text"))
    ]


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


def _resolve_anchor_context(session, seed: dict, resolver: Any) -> Optional[dict]:
    """アンカー文脈（VA層 §8）を注入された resolver から解決する（fail-soft）。

    解決できたときだけ、起点論文が既に配置されているノード集合
    （:func:`seed_placed_node_ids`）を ``exclude_node_ids`` として足す
    （「新しい面」の差集合の材料 — 設計書 §4.2）。**起点論文がこの分野に
    1件も配置されていないときはキー自体を足さない** — 「起点論文は未言及」
    という対比の主張は、起点側の配置が最低1件あって初めて成立する
    （配置ゼロの分野で全ての近傍アンカーを「新しい面」に見せない — 慎重側）。
    resolver 不在・分野不明・解決失敗はいずれも ``None``
    （着地予測も新しい面も出ないだけ — VA4）。
    """
    domain_key = _clean(seed.get("domain_key"))
    if not resolver or not domain_key:
        return None
    try:
        context = resolver(domain_key)
    except Exception:  # noqa: BLE001 — 地図と突き合わせられないだけ（検索は成立させる）
        logger.warning(
            "anchor context unavailable for domain %s (non-fatal)",
            domain_key, exc_info=True,
        )
        return None
    if not context:
        return None
    context = dict(context)
    placed = seed_placed_node_ids(session, seed.get("document_id") or "", domain_key)
    if placed:
        context["exclude_node_ids"] = placed
    return context


def _relation_context(anchor_context: Optional[dict]) -> dict:
    """「どの骨格版と突き合わせたか」の事実（生スコア・件数なし — VA2 / VA8）。"""
    version = _clean((anchor_context or {}).get("skeleton_version"))
    if not anchor_context or not version:
        return {"available": False}
    return {"available": True, "skeleton_version": version}


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


#: 帯分けの側で候補に付き、元の並び順へ移し替える注釈キー（並びは動かさない）。
#: ``distance_label`` = 距離帯 / ``landing`` = 着地予測 / ``new_facets`` = 新しい面
#: （後者2つは VA層 §8。いずれも measured な候補にしか付かない — PR2）。
_BANDED_ANNOTATION_KEYS = ("distance_label", "landing", "new_facets")


def _merge_distance_labels(originals: list[dict], banded_items: list[dict]) -> list[dict]:
    """元の並び順（新着順）を保ったまま、帯分けが付けた注釈だけを移す。

    ``mid`` / ``far`` は「遠い順に並べる」ことをしない（疑似精度になる — 設計書 §3）。
    帯分けは付いた注釈（:data:`_BANDED_ANNOTATION_KEYS`）だけを使い、並びは arXiv の
    新着順のままにする。付かなかった候補には**キー自体を足さない**（VA4 / PR2）。
    """
    annotations: dict[str, dict] = {}
    for item in banded_items:
        found = {
            key: item[key] for key in _BANDED_ANNOTATION_KEYS if item.get(key)
        }
        if found:
            annotations[str(item.get("arxiv_id") or "")] = found

    out: list[dict] = []
    for item in originals:
        payload = dict(item)
        payload.update(annotations.get(str(item.get("arxiv_id") or "")) or {})
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
    anchor_context_resolver: Any = None,
    visible_document_ids: Any = None,
) -> dict:
    """seed 教材の周辺を arXiv から探し、距離帯つきの候補一覧を返す。

    ``categories`` / ``keyphrases`` を渡した場合は seed 由来の供給より優先する
    （条件を保存せずに試せる。分野購読は書き換えない — PR1 / PD3）。

    Args:
        anchor_context_resolver: ``domain_key -> {"anchors", "skeleton_version"} | None``
            の callable（省略時は着地予測・新しい面を導出しない = 完全後方互換）。
            **注入で受ける**のは、このモジュールが ``core.atlas_vectors`` にも
            ``core.llm`` にも触れない境界を保つため（呼び出し側 = route 層が
            ``routes/paper_discovery.py::_anchor_context`` を渡す）。解決に失敗しても
            例外にせず ``relation_context.available=False`` へ縮退する（VA4 / PR7）。
        visible_document_ids: 閲覧者に見える document.id の集合（route 層が
            ``services.list_visible_document_ids`` から注入する — core はユーザーを知らない）。
            「取り込み済み」はこの集合の中の教材だけで判定し、見えるものには
            ``document_id`` / ``material_id`` / ``analysis_status`` を足す（§19 C）。
            省略・空は「見える取り込み済みなし」（fail-closed — 見えない教材を理由に
            取り込みを塞がない・存在を漏らさない）。

    Returns:
        ``{"seed", "query", "distance", "total", "start", "candidates", "banding",
        "relation_context", "closed_world_note"}``。``candidates`` の各要素は
        :meth:`ArxivEntry.to_dict` に ``status``（``new`` / ``ingested``）と
        ``matched_keyphrases``、承認済み部品との語彙一致があれば
        ``overlap_components``、測れた場合のみ ``distance_label`` /（アンカーが
        読めた場合のみ）``landing`` / ``new_facets`` を足したもの。
        ``relation_context`` は ``{"available": bool, "skeleton_version"?: str}``
        で、**キーは常に付く**（地図と突き合わせたのかどうかを黙らせない — VA8）。
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

    anchor_context = _resolve_anchor_context(session, seed, anchor_context_resolver)

    result: dict[str, Any] = {
        "seed": seed,
        "query": query,
        "distance": distance,
        "total": 0,
        "start": max(0, int(start or 0)),
        "candidates": [],
        "banding": {"available": False},
        "relation_context": _relation_context(anchor_context),
        "closed_world_note": search.CLOSED_WORLD_NOTE,
        # 制限されていないことも**明示**する（キーの不在で黙らせない — §14.7）。
        # ここで真になるのは「seed の取得でいま 429 を受けた」場合だけで、その
        # 直後の :func:`arxiv_client.search` はクールダウンで例外になる（呼び出し側が
        # 502 の事実文にする）。
        "arxiv_blocked": bool(seed.get("arxiv_blocked")),
    }
    if not query:
        # 条件ゼロで arXiv を呼ばない（無関係な全件が返るため — PD6）。
        return result
    if distance == "near" and not any(
        phrase.get("enabled", True) for phrase in effective_keyphrases
    ):
        # キーフレーズ無しの near はカテゴリの新着になる — 黙って「近い」と読ませない
        # （設計書 §16 / PD6）。キーは縮退したときだけ付ける。
        result["keyphrase_note"] = NOTE_SEARCH_WITHOUT_KEYPHRASES

    total, entries = arxiv_client.search(
        query, start=result["start"], max_results=max_results
    )
    # 返ってきたメタデータ（外部事実）を写しとして残す。比較分析（compare）が同じ
    # 論文の要旨を取り直さずに済む（設計書 §14 — 一連の操作で arXiv は最大2回）。
    metadata_cache.remember(session, entries)

    ingested = search.visible_ingested_documents(
        session, visible_document_ids=visible_document_ids
    )
    seed_arxiv_id = seed.get("arxiv_id") or ""
    component_labels = _seed_component_labels(seed)

    candidates: list[dict] = []
    for entry in entries:
        payload = entry.to_dict()
        arxiv_id = payload.get("arxiv_id") or ""
        if seed_arxiv_id and arxiv_id == seed_arxiv_id:
            # seed 自身は候補ではない（同一正規化 ID で除外 — 設計書 §4.2）。
            continue
        # 見送り（dismissal）は分野購読の概念なので、レーダーでは注釈しない。
        ingested_doc = ingested.get(arxiv_id)
        payload["status"] = "ingested" if ingested_doc else "new"
        if ingested_doc:
            # 見える教材だけ（§19 C）。教材の行・グラフレビュー・位置づけ・連鎖の入口に使う。
            payload["document_id"] = ingested_doc["document_id"]
            payload["material_id"] = ingested_doc["material_id"]
            payload["analysis_status"] = ingested_doc["analysis_status"]
        payload["matched_keyphrases"] = (
            search.matched_keyphrases(payload, effective_keyphrases)
            if distance == "near"
            else []
        )
        # 承認済み部品との語彙一致（非ベクトル・帯分けの前に付ける = 帯分け側の
        # dict コピーで並べ替え後の候補にもそのまま乗る）。空ならキー自体を足さない。
        overlap = overlap_component_labels(payload, component_labels)
        if overlap:
            payload["overlap_components"] = overlap
        candidates.append(payload)

    # 取り込み済み候補の配置の対比・構造照合の成立条件（§21 B / C）。帯分けの dict コピーに
    # そのまま乗るよう帯分けの前に付ける。
    attach_placement_contrasts(session, seed["document_id"], candidates)
    attach_module_match_notes(session, seed["document_id"], candidates)

    banded = pd_ranking.band_candidates(
        session,
        candidates,
        seed_vector=pd_ranking.document_centroid(session, seed["document_id"]),
        seed_text=seed.get("summary") or "",
        anchor_context=anchor_context,
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
    facts = relation_facts(seed, anchor_context, anchor_context_resolver)
    if facts:
        result["facts"] = facts
    if distance == "near":
        hits = keyphrase_hits(result["candidates"], effective_keyphrases)
        if hits:
            result["keyphrase_hits"] = hits
            result.update(keyphrase_hit_notes(hits))
    note = reading_note(result["candidates"], distance)
    if note:
        result["reading_note"] = note
    return result


def keyphrase_hits(candidates: Any, keyphrases: Any) -> list[dict]:
    """有効な各キーフレーズが、取得した候補のタイトル + 要旨に当たったか（§20 A / B・純関数）。

    返すのは ``[{"text", "hit": bool, "widens": bool}]``（有効な語の順）。**件数は返さない**
    （PD4 — 真偽だけ）。照合は候補カードの一致チップと同じ
    :func:`search.matched_keyphrases`（画面の一致チップと食い違わせない）。``widens`` は
    「取得した候補のすべてに当たった」で、候補が :data:`WIDENING_MIN_CANDIDATES` 件以上かつ
    有効な語が :data:`WIDENING_MIN_ENABLED_KEYPHRASES` 語以上のときだけ真になり得る
    （1 件・1 語では自明に真になり、網の幅について何も言わない）。候補ゼロでは空
    （「どれにも当たらなかった」は候補が無い事実と区別できないので言わない）。

    §23.5: 同じ下限のもとで、「**この語だけに**一致した候補」が取得した候補の過半を占める
    語も ``widens: true`` とし、その語に限って ``widens_note``
    （:data:`NOTE_KEYPHRASE_SOLE_WIDENING`）を足す（多義の略語が単独で網を連れてくる形 —
    judge-scan-09 の ``EFT``）。「すべてに当たった」語は従来どおりで ``widens_note`` を付けない。
    """
    enabled = [
        str(p.get("text") or "").strip()
        for p in keyphrases or ()
        if isinstance(p, dict) and p.get("enabled", True) and str(p.get("text") or "").strip()
    ]
    items = [c for c in candidates or () if isinstance(c, dict)]
    if not enabled or not items:
        return []
    hit_sets = [
        {t.casefold() for t in search.matched_keyphrases(c, [{"text": t} for t in enabled])}
        for c in items
    ]
    can_widen = (
        len(items) >= WIDENING_MIN_CANDIDATES
        and len(enabled) >= WIDENING_MIN_ENABLED_KEYPHRASES
    )
    out: list[dict] = []
    for text in dict.fromkeys(enabled):
        key = text.casefold()
        hit = any(key in hs for hs in hit_sets)
        widens_all = bool(can_widen and all(key in hs for hs in hit_sets))
        sole = sum(1 for hs in hit_sets if hs == {key})
        widens_sole = bool(can_widen and not widens_all and sole * 2 > len(hit_sets))
        entry: dict = {"text": text, "hit": hit, "widens": widens_all or widens_sole}
        if widens_sole:
            entry["widens_note"] = NOTE_KEYPHRASE_SOLE_WIDENING
        out.append(entry)
    return out


def keyphrase_hit_notes(hits: Any) -> dict:
    """当たり外れの事実文（``keyphrase_hits_note`` / ``keyphrase_widening_note``）。

    該当する語があるときだけキーを返す（景色にしない）。語は教員が見ているチップの表記の
    まま列挙する（件数は書かない — PD4）。網を広げている語は**自動では外さない**（PD3 —
    事実を言うだけで、外すのは教員のクリック）。
    """
    missed = [h["text"] for h in hits or () if isinstance(h, dict) and not h.get("hit")]
    widening = [
        h["text"] for h in hits or ()
        if isinstance(h, dict) and h.get("widens") and not h.get("widens_note")
    ]
    sole = [
        h["text"] for h in hits or ()
        if isinstance(h, dict) and h.get("widens") and h.get("widens_note")
    ]
    out: dict[str, str] = {}
    if missed:
        out["keyphrase_hits_note"] = NOTE_KEYPHRASES_NOT_HIT_HEAD + ", ".join(missed)
    parts: list[str] = []
    if widening:
        parts.append(NOTE_KEYPHRASES_WIDENING_HEAD + ", ".join(widening))
    if sole:
        parts.append(NOTE_KEYPHRASES_SOLE_WIDENING_HEAD + ", ".join(sole))
    if parts:
        out["keyphrase_widening_note"] = " ／ ".join(parts)
    return out


def reading_note(candidates: Any, distance: str) -> str:
    """帯と関係の種類の読み方の事実文（§20 D）。帯ラベルの付いた候補が無ければ空。

    関係の種類は比較（compare）の結果で検索時点には無いので、画面は比較結果が描かれた
    ときだけこの文を出す（帯と関係ラベルが同時に出るときだけ）。並び順の事実も同じ行に
    言う（near は帯の中で要旨の近さの順・mid / far は新着順 — :func:`run_radar_search`）。
    """
    if not any(isinstance(c, dict) and c.get("distance_label") for c in candidates or ()):
        return ""
    order = READING_NOTE_NEAR_ORDER if distance == "near" else READING_NOTE_NEWEST_ORDER
    return READING_NOTE_BANDS_AND_RELATIONS + order


def relation_facts(seed: dict, anchor_context: Optional[dict], resolver: Any) -> list[str]:
    """近さの根拠（着地・新しい面・部品との重なり）が出ない理由の事実文（§19 A・PR7）。

    DTO の上で分かる縮退（``relation_context.available=false`` / 承認済み部品ゼロ）を
    画面に届く文にする（型 ``available-but-unwired``）。resolver を渡されていない
    呼び出し（地図と突き合わせる経路が無い）では地図側の文を出さない。数値は書かない。
    """
    facts: list[str] = []
    if resolver is not None and not anchor_context:
        if not _clean(seed.get("domain_key")):
            facts.append(FACT_RELATION_NO_DOMAIN)
        else:
            facts.append(FACT_RELATION_NO_ANCHORS)
    if not _seed_component_labels(seed):
        facts.append(FACT_NO_APPROVED_COMPONENTS)
    return facts


def blocked_radar_result(
    session,
    document_id: str,
    *,
    distance: str = "near",
    categories: Any = None,
    keyphrases: Any = None,
    start: int = 0,
) -> dict:
    """arXiv からアクセスを制限されている間の「探していない」結果（§14.7）。

    :func:`run_radar_search` と**同じ形**を返し、``candidates`` を空・
    ``arxiv_blocked`` を真・事実文 :data:`NOTE_ARXIV_BLOCKED` を添える。呼ぶのは
    「クールダウン中だと分かっている」ときだけで、arXiv へは一切出ていかない
    （``seed`` も ``fetch_arxiv=False`` で解決する）。

    **0件を「該当なし」と偽らないための道具**である（PR7）。live の 429 は従来どおり
    例外 → 502 のまま（§13.3）で、こちらは「呼ぶ前から分かっている」状態だけを
    200 の事実として返す。検索条件（``query``）は通常経路と同じ規則で組み立てて返す —
    何を探そうとしていたかは、探せなかったときこそ画面に要る。

    Raises:
        LookupError: document が存在しない。
        ValueError: ``distance`` が語彙外。
    """
    if distance not in RADAR_DISTANCES:
        raise ValueError(f"unknown radar distance: {distance!r}")

    seed = resolve_seed(session, document_id, fetch_arxiv=False)
    # seed 側と top-level を**同じ値**にする（フロントは両方を読むので、食い違うと
    # バナーの出し入れが揺れる）。この関数は制限中だと分かっているときしか呼ばない。
    seed["arxiv_blocked"] = True
    seed["arxiv_blocked_note"] = NOTE_ARXIV_BLOCKED

    effective_categories = normalize_categories(
        categories if categories is not None else seed.get("categories")
    )
    effective_keyphrases = normalize_keyphrases(
        keyphrases if keyphrases is not None else seed.get("keyphrase_candidates")
    )
    return {
        "seed": seed,
        "query": build_radar_query(distance, effective_categories, effective_keyphrases),
        "distance": distance,
        "total": 0,
        "start": max(0, int(start or 0)),
        "candidates": [],
        # 帯も作っていない（作れなかったのではなく、候補が無い）。理由を事実文で残す。
        "banding": {"available": False, "note": NOTE_ARXIV_BLOCKED},
        "relation_context": {"available": False},
        "closed_world_note": search.CLOSED_WORLD_NOTE,
        "arxiv_blocked": True,
        "note": NOTE_ARXIV_BLOCKED,
    }


def compare_requires_arxiv(session, document_id: str, arxiv_ids: Any) -> bool:
    """比較分析が arXiv への問い合わせを要するか（保存済みの写しだけで足りないか）。

    ``compare.run_compare`` が組み立てるのと同じ ID 集合（起点論文 + 候補）について、
    新鮮な写し（migration 085）が揃っているかだけを見る純粋な読み取り。制限中でも
    写しだけで比較できるなら、**比較は普通に成立させる**（arXiv 0 コール — §14.3）。

    Raises:
        LookupError: document が存在しない。
    """
    seed = resolve_seed(session, document_id, fetch_arxiv=False)
    seed_arxiv_id = seed.get("arxiv_id") or ""

    requested: list[str] = []
    for raw in arxiv_ids or ():
        normalized = normalize_arxiv_id(raw)
        if normalized and normalized != seed_arxiv_id and normalized not in requested:
            requested.append(normalized)

    needed = ([seed_arxiv_id] if seed_arxiv_id else []) + requested
    if not needed:
        return False
    cached = metadata_cache.read_fresh(session, needed)
    return any(arxiv_id not in cached for arxiv_id in needed)


# ---------------------------------------------------------------------------
# arXiv 出所の後付け記帳（このモジュールで唯一の「教員の判断」の書き込み）
# ---------------------------------------------------------------------------


def register_arxiv_provenance(session, document_id: str, arxiv_id: str) -> str:
    """``documents.source_url`` に arXiv の論文ページ URL を**後から**記帳する。

    レーダーの探索経路（:func:`resolve_seed` / :func:`run_radar_search`）は一切
    書き込まない（PR1 / PR5）。書き込むのはこの関数だけで、呼び出せるのは教員の
    明示操作を受けた route 層に限る（自動記帳の経路を作らない）。判定材料
    （推定 ID とタイトル照合）はサーバ側で導出し直したものを使う前提。

    **空（``''`` / ``NULL``）のときだけ**上書きする。既に出所が入っている教材は
    取り込み経由 or 確定済みなので、推定で塗り替えない（情報を落とさない）。
    ``commit`` は呼び出し側の責務。

    Args:
        session: SQLAlchemy セッション。
        document_id: ``documents.id``（UUID 文字列）。
        arxiv_id: 記帳する arXiv ID（version 付き・URL 表記も正規化して受ける）。

    Returns:
        記帳した論文ページ URL（version 抜き）。

    Raises:
        ValueError: ID を解釈できない / 対象が無い / すでに出所が登録されている。
    """
    normalized = normalize_arxiv_id(arxiv_id)
    if not normalized:
        raise ValueError("arXiv ID として解釈できませんでした。")

    url = abs_url_for(normalized)
    result = session.execute(
        sa_text(
            """
            UPDATE documents
               SET source_url = :url
             WHERE id::text = :document_id
               AND COALESCE(source_url, '') = ''
            """
        ),
        {"url": url, "document_id": str(document_id or "").strip()},
    )
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        raise ValueError("この教材には、すでに取得元が登録されています。")
    return url


__all__ = [
    "BODY_ABBREVIATION_MAX_CHUNKS",
    "NOTE_KEYPHRASE_SOLE_WIDENING",
    "NOTE_KEYPHRASES_SOLE_WIDENING_HEAD",
    "body_full_form",
    "is_short_abbreviation",
    "APPROVED_REVIEW_STATUSES",
    "CATEGORIES_SOURCE_ARXIV",
    "CATEGORIES_SOURCE_ARXIV_INFERRED",
    "CATEGORIES_SOURCE_MANUAL",
    "CATEGORIES_SOURCE_SUBSCRIPTION",
    "MAX_SEED_KEYPHRASES",
    "METADATA_FAILURE_NOT_FOUND",
    "METADATA_FAILURE_RATE_LIMITED",
    "METADATA_FAILURE_UNAVAILABLE",
    "METADATA_SOURCE_ARXIV",
    "METADATA_SOURCE_CACHE",
    "MIN_OVERLAP_LABEL_CHARS",
    "NOTE_ARXIV_BLOCKED",
    "NOTE_ARXIV_METADATA_NOT_FOUND",
    "NOTE_ARXIV_METADATA_UNAVAILABLE",
    "NOTE_ARXIV_RATE_LIMITED",
    "FACT_NO_APPROVED_COMPONENTS",
    "FACT_RELATION_NO_ANCHORS",
    "FACT_RELATION_NO_DOMAIN",
    "NOTE_SEED_TITLE_IS_FILENAME",
    "PROVENANCE_STATUS_INFERRED",
    "PROVENANCE_STATUS_NONE",
    "PROVENANCE_STATUS_REGISTERED",
    "arxiv_blocked",
    "blocked_radar_result",
    "build_radar_query",
    "category_candidates",
    "relation_facts",
    "title_looks_like_filename",
    "compare_requires_arxiv",
    "overlap_component_labels",
    "register_arxiv_provenance",
    "resolve_seed",
    "run_radar_search",
    "seed_keyphrase_candidates",
    "keyphrase_supply",
    "title_keyphrases",
    "title_thesis_keyphrases",
    "is_searchable_concept_name",
    "NOTE_KEYPHRASE_SUPPLY_EMPTY",
    "NOTE_KEYPHRASE_SUPPLY_WITHOUT_APPROVED",
    "NOTE_SEARCH_WITHOUT_KEYPHRASES",
    "NOTE_KEYPHRASES_NOT_HIT_HEAD",
    "NOTE_KEYPHRASES_WIDENING_HEAD",
    "READING_NOTE_BANDS_AND_RELATIONS",
    "READING_NOTE_NEAR_ORDER",
    "READING_NOTE_NEWEST_ORDER",
    "keyphrase_hits",
    "keyphrase_hit_notes",
    "proper_noun_keyphrases",
    "reading_note",
    "seed_placed_node_ids",
]
