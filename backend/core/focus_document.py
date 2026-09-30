"""焦点論文（Focus Document）— 「いまの会話・操作はどの論文についてか」の唯一の解決器。

正本設計書: ``docs/features/focus_document_design.md``（FD1〜FD5）。

会話の往復（トピック学習 / discuss / DIFF / 前提知識の説明）・構造帰属・記号の定義・
要素文脈（claim / equation / component）・降下路・「いまここの周り」の範囲表示は、
それぞれ独自に「どの論文を先に見るか」を決めていた（トピックの論文を先に / 直前の引用を
先に / ``>`` と ``>=`` の食い違い）。本モジュールがその判断を1箇所に集める。

- :func:`resolve_focus_document` — 焦点の論文を**1つの段順**で決める（FD1）。
  段は ①明示（document 直付け discuss・タップ位置のチャンク）→ ②トピックの論文 →
  ③直前の回答が引用した論文 → ④画面で選んだ論文（discuss 開幕の選択）。各段は
  スコープとの積だけを採り、スコープを広げない（FD2 = DM1）。
- :func:`prefer_focus` — 検索結果の並べ替えと別論文の除外の**唯一の優先規則**（FD3）。
- :func:`resolve_in_focus` — 論文をまたいで衝突する ID（``eq_5`` / ``comp_001`` /
  記号）の解決規則（FD4: 焦点の段の中でちょうど1論文に当たるときだけ、焦点に無ければ
  スコープ内で一意のときだけ、それ以外は ``None`` = fail-closed）。
- :func:`scope_chunks_to_focus` — 痕跡の ``cited_chunk_ids`` を焦点の論文に絞る
  （焦点のチャンクが1つも無ければ絞らない fail-soft）。

FastAPI / sqlalchemy / LLM を import しない純関数のみ（開発ルール2）。記録してよいのは
``FocusDocument.source`` の enum 文字列だけ（FD5。document_id の列や件数を痕跡に焼かない）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: 採用の下限（類似度）。学習チャットの RAG が文脈に採る下限と同じ値。
ADOPTION_THRESHOLD = 0.30

SOURCE_EXPLICIT = "explicit"
SOURCE_TOPIC = "topic"
SOURCE_PREVIOUS_CITATION = "previous_citation"
SOURCE_OPENING = "opening"
SOURCE_NONE = "none"

#: 段の順序（先頭が強い）。``FocusDocument.source`` の語彙の正本。
FOCUS_SOURCES: tuple[str, ...] = (
    SOURCE_EXPLICIT,
    SOURCE_TOPIC,
    SOURCE_PREVIOUS_CITATION,
    SOURCE_OPENING,
    SOURCE_NONE,
)


@dataclass(frozen=True)
class FocusDocument:
    """焦点の論文（段順つき）と、それを決めた段。

    ``document_ids`` はスコープ内の document_id だけを段の中の順で持つ。
    ``source == "none"`` のとき ``document_ids`` は空。
    """

    document_ids: tuple[str, ...] = ()
    source: str = SOURCE_NONE

    @property
    def id_set(self) -> set[str]:
        return set(self.document_ids)

    def __bool__(self) -> bool:  # 焦点があるか
        return bool(self.document_ids)


NO_FOCUS = FocusDocument()


def _ordered_ids(values: Any) -> list[str]:
    """ID 群を空白除去・重複除去した list にする。

    文字列は1要素、``set`` / ``frozenset`` は決定論のためソート、それ以外の反復可能は
    与えられた順を保つ。``FocusDocument`` はその ``document_ids``。
    """
    if values is None:
        return []
    if isinstance(values, FocusDocument):
        return list(values.document_ids)
    if isinstance(values, str):
        text = values.strip()
        return [text] if text else []
    if isinstance(values, (set, frozenset)):
        items: Iterable[Any] = sorted(str(v) for v in values if v is not None)
    else:
        try:
            items = list(values)
        except TypeError:
            text = str(values).strip()
            return [text] if text else []
    out: list[str] = []
    seen: set[str] = set()
    for value in items:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def resolve_focus_document(
    *,
    allowed_document_ids: Iterable[str] | None,
    explicit_document_id: Any = None,
    topic_document_ids: Any = (),
    previous_cited_document_ids: Any = (),
    opening_document_id: Any = None,
) -> FocusDocument:
    """焦点の論文を段順（明示 → トピック → 直前の引用 → 画面の選択）で決める（FD1）。

    各段はスコープ（``allowed_document_ids``）との積だけを採り、最初に空でない段が勝つ。
    どの段も空なら ``source="none"``。スコープの外の論文を焦点にしない（FD2 = DM1）。
    ``allowed_document_ids`` が ``None`` / 空なら常に ``none``（fail-closed）。

    段を入れるかどうかの条件は呼び出し側が持つ（設計書 §2 FD-note）:

    - document 直付け discuss で学習者が ``discuss_scope="all_visible"`` を明示したときは、
      その単一 document を明示の段に入れない（明示の広いスコープを焦点で狭めない）。
    - 直前の引用の段は議論（discuss / ``_discussion``）だけで使う。通常のトピックで
      束ねる論文がスコープに無いときに入れると、前回引用した論文へ往復が張り付く。
    """
    allowed = set(_ordered_ids(allowed_document_ids))
    if not allowed:
        return NO_FOCUS
    tiers = (
        (SOURCE_EXPLICIT, explicit_document_id),
        (SOURCE_TOPIC, topic_document_ids),
        (SOURCE_PREVIOUS_CITATION, previous_cited_document_ids),
        (SOURCE_OPENING, opening_document_id),
    )
    for source, values in tiers:
        inside = tuple(d for d in _ordered_ids(values) if d in allowed)
        if inside:
            return FocusDocument(document_ids=inside, source=source)
    return NO_FOCUS


def _focus_id_set(focus: Any) -> set[str]:
    return set(_ordered_ids(focus))


def _score(row: Mapping[str, Any], score_key: str) -> float:
    try:
        return float(row.get(score_key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def prefer_focus(
    rows: Iterable[Mapping[str, Any]] | None,
    focus: Any,
    *,
    doc_key: str = "document_id",
    score_key: str = "score",
    adoption: float = ADOPTION_THRESHOLD,
    limit: int | None = None,
    reorder: bool = True,
    drop_off_focus: bool = True,
) -> list:
    """検索結果に焦点の論文を優先する**唯一の規則**（FD3・決定論・純関数）。

    1. 並べ替え（``reorder``）: ``(採用の下限未満, 焦点の論文でない, -score)`` の安定ソート。
       採用の下限を満たす行を先に置くので、切り詰めで採用できる別論文の行が採用できない
       焦点の行に押し出されることはない。``doc_key`` を持たない行は焦点の外として扱う。
    2. 切り詰め（``limit``）。
    3. 除外（``drop_off_focus``）: 焦点の論文に採用の下限（``>= adoption``）を満たす行が
       1件以上あるときは、焦点の外の行は**焦点内の最良より類似度が厳密に高い（``>``）もの
       だけ**残す。焦点内に採用できる行が無ければ何も外さない（コース全体の資料で答える
       経路を塞がない）。並びは変えない。

    焦点が空なら入力をそのまま（list にして）返す。
    """
    items = list(rows or [])
    ids = _focus_id_set(focus)
    if not ids:
        return items[:limit] if limit is not None else items

    def _in_focus(row: Mapping[str, Any]) -> bool:
        return str(row.get(doc_key) or "") in ids

    if reorder:
        items = sorted(
            items,
            key=lambda r: (_score(r, score_key) < adoption, not _in_focus(r), -_score(r, score_key)),
        )
    if limit is not None:
        items = items[:limit]
    if not drop_off_focus:
        return items
    focus_scores = [_score(r, score_key) for r in items if _in_focus(r) and _score(r, score_key) >= adoption]
    if not focus_scores:
        return items
    best = max(focus_scores)
    return [r for r in items if _in_focus(r) or _score(r, score_key) > best]


def _group_candidates(pairs: Iterable[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for pair in pairs or []:
        try:
            document_id, item = pair
        except (TypeError, ValueError):
            continue
        key = str(document_id or "").strip()
        if key:
            grouped.setdefault(key, []).append(item)
    return grouped


def _decide_focus_group(grouped: Mapping[str, list[Any]], focus_ids: list[str]) -> tuple[bool, Any]:
    """焦点の段をまとまりとして判定する（FD4）。``(決着したか, 値)`` を返す。

    焦点の論文のどれにも候補が無ければ未決着（スコープ内の一意へ落とす）。候補を持つ
    焦点の論文が1つならその候補が1つのときだけ値、2つ以上なら ``None`` で決着する。
    """
    hits = [d for d in focus_ids if grouped.get(d)]
    if not hits:
        return False, None
    if len(hits) > 1:
        return True, None
    items = grouped[hits[0]]
    return True, (items[0] if len(items) == 1 else None)


def resolve_in_focus(
    candidates: Mapping[str, Any] | Iterable[Any] | Callable[[list[str]], Iterable[Any]],
    focus: Any,
    *,
    scope_document_ids: Iterable[str] | None = None,
) -> Any | None:
    """論文をまたいで衝突する ID の解決規則（FD4）。

    ``candidates`` は次のどれか:

    - ``Mapping[document_id, Sequence[item]]``
    - ``(document_id, item)`` の反復
    - ``Callable[[list[document_id]], Iterable[(document_id, item)]]``（遅延読み。焦点の
      論文ごとに ``[d]`` で呼び、最後に残りのスコープで1回呼ぶ。巨大な artifact を全部
      読まずに済む）。遅延読みのときは ``scope_document_ids`` を渡す。

    規則:

    1. 焦点の段は**まとまり**として見る。焦点の論文のうち候補を持つ論文が**ちょうど1つ**で、
       その候補が1つのときだけ返す。焦点の論文が2つ以上（トピックが2本の論文を束ねる等）
       あり、その**2つ以上に候補がある**なら ``None``（段の中の並び = UUID 順で勝者を
       決めない。同じ論文の中で複数に当たるときも ``None``）。
    2. 焦点の論文のどれにも候補が無ければ、スコープの中で候補を持つ論文が**ちょうど1つ**
       かつその候補が1つのときだけ返す。
    3. それ以外（複数の論文に当たる / 何も無い）は ``None``（推測で選ばない = fail-closed）。
    """
    focus_ids = _ordered_ids(focus)
    if callable(candidates) and not isinstance(candidates, Mapping):
        scope = _ordered_ids(scope_document_ids)
        scope_set = set(scope)
        focus_in_scope = [d for d in focus_ids if not scope_set or d in scope_set]
        if focus_in_scope:
            focus_grouped = _group_candidates(candidates(focus_in_scope))
            decided, value = _decide_focus_group(focus_grouped, focus_in_scope)
            if decided:
                return value
        rest = [d for d in scope if d not in set(focus_ids)]
        grouped = _group_candidates(candidates(rest)) if rest else {}
    else:
        if isinstance(candidates, Mapping):
            grouped = {
                str(k).strip(): list(v or [])
                for k, v in candidates.items()
                if str(k or "").strip()
            }
        else:
            grouped = _group_candidates(candidates)
        if scope_document_ids is not None:
            scope_set = set(_ordered_ids(scope_document_ids))
            grouped = {k: v for k, v in grouped.items() if k in scope_set}
        decided, value = _decide_focus_group(grouped, focus_ids)
        if decided:
            return value
        grouped = {k: v for k, v in grouped.items() if k not in set(focus_ids)}
    hits = {k: v for k, v in grouped.items() if v}
    if len(hits) == 1:
        (items,) = hits.values()
        if len(items) == 1:
            return items[0]
    return None


def unique_row_lookup(
    fetch_one: Callable[[list[str]], Any],
    *,
    is_exact: Callable[[Any], bool] = lambda item: False,
) -> Callable[[list[str]], list[tuple[str, Any]]]:
    """「文書集合から1行だけ引く」関数を :func:`resolve_in_focus` の遅延読みにする。

    ``fetch_one(doc_ids) -> (document_id, item) | None``。1行目が完全一致（``is_exact``。
    DB UUID の一致など一意な ID）ならそれだけを返し、そうでなければ**残りの文書**で
    もう1回だけ引いて、別の論文にも当たるか（= 曖昧か）を確かめる（最大2クエリ）。
    同じ論文の中の複数一致は ``fetch_one`` 側の並び（LIMIT 1）に委ねる。
    """

    def _lookup(doc_ids: list[str]) -> list[tuple[str, Any]]:
        ids = list(doc_ids or [])
        if not ids:
            return []
        first = fetch_one(ids)
        if not first:
            return []
        document_id, item = first
        hits = [(str(document_id or ""), item)]
        if is_exact(item):
            return hits
        rest = [d for d in ids if d != str(document_id or "")]
        if rest:
            second = fetch_one(rest)
            if second:
                hits.append((str(second[0] or ""), second[1]))
        return hits

    return _lookup


def scope_chunks_to_focus(
    chunk_ids: Iterable[str], chunk_to_doc: Mapping[str, str], focus: Any
) -> list[str]:
    """チャンク id の列を焦点の論文のものに絞る（純関数・順序保持）。

    焦点が空、または焦点の論文のチャンクが1つも無ければ絞らない（fail-soft。推測で空にしない）。
    """
    ids = list(chunk_ids or [])
    focus_ids = _focus_id_set(focus)
    if not focus_ids:
        return ids
    inside = [c for c in ids if chunk_to_doc.get(c) in focus_ids]
    return inside if inside else ids


__all__ = [
    "ADOPTION_THRESHOLD",
    "FOCUS_SOURCES",
    "FocusDocument",
    "NO_FOCUS",
    "SOURCE_EXPLICIT",
    "SOURCE_NONE",
    "SOURCE_OPENING",
    "SOURCE_PREVIOUS_CITATION",
    "SOURCE_TOPIC",
    "prefer_focus",
    "resolve_focus_document",
    "resolve_in_focus",
    "scope_chunks_to_focus",
    "unique_row_lookup",
]
