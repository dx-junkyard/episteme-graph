"""コーパスを補う論文 — レンズA（地図の薄い領域）/ レンズB（検証記録の無い前提）。

設計正本: ``docs/features/corpus_complement_design.md`` §5.1（不変条項 CC1〜CC8）。

「近い論文」ではなく「読むと知見が足される論文」を、**コーパスがすでに持つ構造**への
補完として決定論的に判定する層。判定の材料は2つだけで、どちらも既存の構造から読む。

- **レンズA 地図の薄い領域**: 凍結骨格の concept ノードのうち、生きた配置
  （``landscape_placements``）の distinct document 数が上限以下のもの。そこへ着地
  しそうな候補に「薄い領域に着地」の事実を付ける。
- **レンズB 検証記録の無い前提**: 台帳（``epistemic_ledger``）で ``untested`` かつ
  スコープ空欄の前提・主張の本文に近い内容を扱う候補に、閉世界の事実文を付ける。

このモジュールが守るもの:

- **CC1 補完の根拠はコーパス構造のみ**: 入力は配置と台帳だけ。学習者の痕跡
  （関心信号・つまづき・違和感）を選定入力にしない（CR10 / IG2 を構造として守る）。
- **CC2 決定論・非LLM**: ここは DB 読みと純計算のみ。埋め込みは ``ranking.py`` の
  既存1バッチに相乗りし、発見層の ``core.llm`` 接触点を増やさない。
- **CC4 数値非表示**: cosine・配置件数・台帳の件数を返り値に載せない。判定は
  「該当あり」の有無と、根拠となる**名前**（ノード名・前提文・出典タイトル）だけ。
- **CC5 閉世界語彙の固定**: 検証記録の不在について言えるのは
  :data:`CLOSED_WORLD_SKY_NOTE` の1文だけ（SL1 継承）。分野全体についての言明を
  作らない。レンズA も骨格版を明示した「地図の中で」の言明に留める（VA8）。
- **CC6 仮説文体**: 判定は推定であり、断定語（良い論文・必読）を作らない。
- **CC8 fail-soft**: レンズが成立しないときは**そのレンズだけ**事実文で縮退する。
  DB 不達・表の不在は例外にせず空へ倒し、検索そのものは必ず成立させる。

FastAPI 非 import・``core.llm`` 非 import（ガードレールが構造として固定する）。
書き込みは一切行わない（``commit`` しない・DDL/DML を書かない）。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import text as sa_text

from core.atlas_vectors.query import cosine_similarity, nearest_anchors
from core.label_vocab import ANCHOR_LANDING_THRESHOLD_NEAR, COMPLEMENT_SKY_THRESHOLD
from core.paper_discovery import corpus

logger = logging.getLogger(__name__)

#: レンズA: 1候補に付ける「薄い領域」の上限件数。
MAX_FILLS_PER_CANDIDATE = 2

#: レンズB: 1候補に付ける前提文の上限件数。
MAX_SKIES_PER_CANDIDATE = 2

#: レンズB: 埋め込みに載せる前提文の上限（同一バッチに相乗りするための防波堤）。
MAX_SKY_STATEMENTS = 30

#: レンズB: 前提文1件の最大文字数（切り詰め）。
MAX_SKY_STATEMENT_CHARS = 300

#: 骨格ノードの種別（``atlas_vectors.builder._skeleton_node_specs`` と同じ値）。
#: レンズA が対象にするのは concept のみ（region は粗すぎて「薄い」と言えない）。
NODE_KIND_CONCEPT = "concept"

#: レンズB の閉世界固定文（SL1）。台帳に記帳が無いことについて言えるのはこれだけで、
#: 分野全体・世界全体についての言明（未検証・誰も検証していない 等）は作らない。
CLOSED_WORLD_SKY_NOTE = "このコーパスの中では検証記録がありません"

# 事実文（数値を含めない — CC4）。
NOTE_NO_SKELETON = (
    "この分野には凍結された分野の地図がないため、「地図の薄い領域」は判定しません。"
)
NOTE_NO_ANCHORS = (
    "分野の地図のベクトル索引が未構築のため、「地図の薄い領域」は判定しません。"
)
NOTE_NO_THIN_NODES = (
    "分野の地図の概念はいずれも取り込み済み論文で覆われているため、"
    "「地図の薄い領域」の候補はありません。"
)
NOTE_NO_SKIES = "このコーパスの台帳には、検証記録の無い前提の記帳がありません。"
NOTE_EMBEDDING_UNAVAILABLE = (
    "候補の埋め込みが作れなかったため、補完の判定を行いませんでした。"
)

#: 台帳から本文へ解決するときに採用する状態（**人間が確定したものだけ**）。
#: AI 候補（``candidate`` の前提 / 未承認の主張）を補完の根拠にしない（CC1 / W2 同族）。
ASSUMPTION_HUMAN_STATUSES = ("confirmed", "operationalized")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


# ---------------------------------------------------------------------------
# レンズA — 地図の薄い領域
# ---------------------------------------------------------------------------


def thin_node_ids(
    session,
    domain_key: str,
    anchors: Iterable[Any],
    *,
    max_documents: int,
) -> set[str]:
    """凍結骨格の concept ノードのうち「配置が薄い」ものの ID 集合。

    「薄い」= 生きた配置（``status NOT IN ('superseded', 'rejected')``）の distinct
    ``document_id`` 数が ``max_documents`` **以下**（未配置 = 0 を含む）。AI 推定
    （``inferred``）も「もう地図に現れている」として数える — 未確定の推定を無視して
    「薄い」を水増ししないための慎重側（LS3 / PR2 と同じ規律）。

    region ノードは対象外（領域は概念より粗く、「薄い」の言明が雑になるため）。

    Args:
        anchors: 現行凍結版のアンカー列（``AnchorVector``。``node_kind`` /
            ``node_id`` を読むだけで、ベクトルは使わない）。

    Returns:
        薄い concept の ``node_id`` の集合。**件数は外へ出さない**（CC4 — 呼び出し側は
        集合の要素だけを使う）。アンカー不在・DB 不達はいずれも空集合へ fail-soft
        （レンズA が出ないだけで検索は成立する — CC8）。
    """
    domain = _clean(domain_key)
    concept_ids = [
        str(getattr(anchor, "node_id", "") or "").strip()
        for anchor in (anchors or ())
        if str(getattr(anchor, "node_kind", "") or "") == NODE_KIND_CONCEPT
    ]
    concept_ids = [node_id for node_id in dict.fromkeys(concept_ids) if node_id]
    if not domain or not concept_ids:
        return set()

    try:
        threshold = int(max_documents)
    except (TypeError, ValueError):
        return set()
    if threshold < 0:
        return set()

    try:
        rows = session.execute(
            sa_text(
                """
                SELECT node_id, COUNT(DISTINCT document_id)
                  FROM landscape_placements
                 WHERE domain_key = :domain_key
                   AND node_id = ANY(CAST(:node_ids AS text[]))
                   AND status NOT IN ('superseded', 'rejected')
                 GROUP BY node_id
                """
            ),
            {"domain_key": domain, "node_ids": sorted(concept_ids)},
        ).fetchall()
    except Exception:  # noqa: BLE001 — 配置が読めないだけ（検索は成立させる）
        logger.warning(
            "landscape placements unavailable for domain %s (non-fatal)",
            domain, exc_info=True,
        )
        return set()

    placed: dict[str, int] = {}
    for row in rows:
        node_id = str(row[0] or "").strip()
        if not node_id:
            continue
        try:
            placed[node_id] = int(row[1] or 0)
        except (TypeError, ValueError):
            placed[node_id] = 0

    return {node_id for node_id in concept_ids if placed.get(node_id, 0) <= threshold}


def fills_for_vector(
    vector: Optional[Sequence[float]],
    anchors: Iterable[Any],
    thin_ids: set[str],
    *,
    limit: int = MAX_FILLS_PER_CANDIDATE,
) -> list[dict]:
    """候補が着地しそうな「薄い領域」の事実（純関数・生値なし — CC4）。

    :data:`core.label_vocab.ANCHOR_LANDING_THRESHOLD_NEAR` 以上（= 着地予測の
    **最上位帯のみ**）で ``thin_ids`` に含まれる concept アンカーを近い順に最大
    ``limit`` 件返す。中位帯まで拾うと「なんとなく関連」が補完の根拠として並ぶため、
    「新しい面」（``atlas_vectors.query.new_facet_labels``）と同じ慎重側で足切りする。

    未測定（cosine が ``None``）のアンカーは含めない（``nearest_anchors`` の規律を
    そのまま継承 — 「測れなかった」を「近い」に化けさせない）。

    Returns:
        ``[{"node_label", "region_label"}, ...]``。``node_id`` も cosine も載せない。
        ベクトル不在・アンカー不在・薄いノード不在・``limit <= 0`` は空リスト
        （呼び出し側はキー自体を付けない — VA4 の流儀）。
    """
    items = list(anchors or ())
    if not vector or not items or not thin_ids or int(limit or 0) <= 0:
        return []

    out: list[dict] = []
    for anchor, nearness in nearest_anchors(vector, items, limit=len(items)):
        if nearness < ANCHOR_LANDING_THRESHOLD_NEAR:
            # 近い順に並んでいるので、ここから先は全て帯の外。
            break
        if str(getattr(anchor, "node_kind", "") or "") != NODE_KIND_CONCEPT:
            continue
        node_id = str(getattr(anchor, "node_id", "") or "").strip()
        if node_id not in thin_ids:
            continue
        label = _clean(getattr(anchor, "label", "")) or node_id
        if not label:
            continue
        out.append(
            {
                "node_label": label,
                "region_label": _clean(getattr(anchor, "region_label", "")),
            }
        )
        if len(out) >= int(limit):
            break
    return out


# ---------------------------------------------------------------------------
# レンズB — 検証記録の無い前提
# ---------------------------------------------------------------------------


def _document_titles(session, document_ids: Sequence[str]) -> dict[str, str]:
    """``documents.id`` → タイトル（引けないものはキーごと現れない）。"""
    ids = [str(d or "").strip() for d in (document_ids or ()) if str(d or "").strip()]
    if not ids:
        return {}
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id::text, COALESCE(title, '')
                  FROM documents
                 WHERE id::text = ANY(CAST(:document_ids AS text[]))
                """
            ),
            {"document_ids": sorted(dict.fromkeys(ids))},
        ).fetchall()
    except Exception:  # noqa: BLE001 — タイトルが引けないだけ（本文は出せる）
        logger.warning("document titles unavailable (non-fatal)", exc_info=True)
        return {}
    return {
        str(row[0] or "").strip(): _clean(row[1])
        for row in rows
        if str(row[0] or "").strip()
    }


#: 台帳 → 本文の解決 SQL（assumption / claim で表と採用条件だけが違う）。
_SKY_SQL_ASSUMPTION = """
    SELECT l.target_id, l.document_id, a.statement
      FROM epistemic_ledger l
      JOIN assumption_nodes a ON a.id::text = l.target_id
     WHERE l.target_type = 'assumption'
       AND l.verification_status = 'untested'
       AND jsonb_array_length(l.verification_scopes) = 0
       AND l.document_id = ANY(CAST(:document_ids AS text[]))
       AND a.status = ANY(CAST(:statuses AS text[]))
       AND COALESCE(a.statement, '') <> ''
     ORDER BY l.document_id, l.target_id
     LIMIT :limit
"""

_SKY_SQL_CLAIM = """
    SELECT l.target_id, l.document_id, c.text
      FROM epistemic_ledger l
      JOIN theory_claims c ON c.id::text = l.target_id
     WHERE l.target_type = 'claim'
       AND l.verification_status = 'untested'
       AND jsonb_array_length(l.verification_scopes) = 0
       AND l.document_id = ANY(CAST(:document_ids AS text[]))
       AND c.review_status = ANY(CAST(:statuses AS text[]))
       AND COALESCE(c.text, '') <> ''
     ORDER BY l.document_id, l.target_id
     LIMIT :limit
"""


def sky_statements(
    session,
    domain_key: str,
    *,
    limit: int = MAX_SKY_STATEMENTS,
) -> list[dict]:
    """この分野の台帳にある「検証記録の無い前提・主張」の本文（読み取りのみ）。

    対象は ``epistemic_ledger`` のうち ``verification_status='untested'`` かつ
    ``verification_scopes`` が空配列の行（= SL層の「晴れ間」）で、本文は
    **人間が確定したものだけ**へ解決する:

    - ``assumption`` → ``assumption_nodes.statement``
      （``status`` が :data:`ASSUMPTION_HUMAN_STATUSES` のもの）
    - ``claim`` → ``theory_claims.text``
      （``review_status`` が ``reconstruction.schema.APPROVED_REVIEW_STATUSES`` のもの）

    AI 候補（``candidate`` の前提 / 未承認の主張）は根拠にしない（CC1）。

    Returns:
        ``[{"target_id", "target_type", "statement", "document_id",
        "document_title"}, ...]``（assumption → claim の順・各群は document / target で
        決定論順）。**該当ゼロは正常な状態**（台帳にまだ記帳が無い）。分野に document が
        無い・表が読めない場合も空リストへ fail-soft（CC8）。
    """
    from core.reconstruction.schema import APPROVED_REVIEW_STATUSES  # 遅延 import

    try:
        row_limit = max(0, int(limit))
    except (TypeError, ValueError):
        row_limit = MAX_SKY_STATEMENTS
    if row_limit == 0:
        return []

    document_ids = corpus.domain_document_ids(session, domain_key)
    if not document_ids:
        # 空配列で SQL を撃たない（空 IN 句は全件条件に化けやすい — corpus.py と同じ規律）。
        return []

    out: list[dict] = []
    for target_type, sql, statuses in (
        ("assumption", _SKY_SQL_ASSUMPTION, list(ASSUMPTION_HUMAN_STATUSES)),
        ("claim", _SKY_SQL_CLAIM, list(APPROVED_REVIEW_STATUSES)),
    ):
        remaining = row_limit - len(out)
        if remaining <= 0:
            break
        try:
            rows = session.execute(
                sa_text(sql),
                {
                    "document_ids": sorted(dict.fromkeys(document_ids)),
                    "statuses": statuses,
                    "limit": remaining,
                },
            ).fetchall()
        except Exception:  # noqa: BLE001 — 台帳が読めないだけ（検索は成立させる）
            logger.warning(
                "epistemic ledger unavailable for domain %s (non-fatal)",
                _clean(domain_key), exc_info=True,
            )
            continue
        for row in rows:
            statement = _clean(row[2])[:MAX_SKY_STATEMENT_CHARS]
            if not statement:
                continue
            out.append(
                {
                    "target_id": str(row[0] or "").strip(),
                    "target_type": target_type,
                    "statement": statement,
                    "document_id": str(row[1] or "").strip(),
                    "document_title": "",
                }
            )

    titles = _document_titles(session, [item["document_id"] for item in out])
    for item in out:
        item["document_title"] = titles.get(item["document_id"], "")
    return out


def skies_for_vector(
    vector: Optional[Sequence[float]],
    sky_vectors: Sequence[Optional[Sequence[float]]],
    statements: Sequence[dict],
    *,
    limit: int = MAX_SKIES_PER_CANDIDATE,
) -> list[dict]:
    """候補が近い内容を扱っている「検証記録の無い前提」（純関数・生値なし — CC4）。

    ``sky_vectors`` は ``statements`` と**同じ並び**の埋め込み（``None`` は埋め込め
    なかったもの）。:data:`core.label_vocab.COMPLEMENT_SKY_THRESHOLD` 以上のものを
    近い順に最大 ``limit`` 件返す。**未測定は不一致扱い**（測れなかったものを
    「近い」に化けさせない）。同点は入力順を保つ安定ソート。

    Returns:
        ``[{"statement", "document_title", "closed_world_note"}, ...]``。
        ``closed_world_note`` は常に :data:`CLOSED_WORLD_SKY_NOTE`（SL1 の固定文 —
        呼び出し側が文言を組み立てない）。
    """
    items = list(statements or [])
    if not vector or not items or int(limit or 0) <= 0:
        return []

    vectors = list(sky_vectors or [])
    scored: list[tuple[int, float]] = []
    for index, item in enumerate(items):
        if index >= len(vectors):
            break
        nearness = cosine_similarity(vector, vectors[index])
        if nearness is None or nearness < COMPLEMENT_SKY_THRESHOLD:
            continue
        scored.append((index, nearness))

    scored.sort(key=lambda row: (-row[1], row[0]))
    out: list[dict] = []
    for index, _nearness in scored[: int(limit)]:
        item = items[index]
        statement = _clean(item.get("statement"))[:MAX_SKY_STATEMENT_CHARS]
        if not statement:
            continue
        out.append(
            {
                "statement": statement,
                "document_title": _clean(item.get("document_title")),
                "closed_world_note": CLOSED_WORLD_SKY_NOTE,
            }
        )
    return out


# ---------------------------------------------------------------------------
# 材料の組み立てと並べ替え
# ---------------------------------------------------------------------------


def _lens(available: bool, note: str = "") -> dict:
    out: dict[str, Any] = {"available": bool(available)}
    if note:
        out["note"] = note
    return out


def degraded_facts(note: str = NOTE_EMBEDDING_UNAVAILABLE) -> dict:
    """両レンズを同じ事実文で縮退させた ``facts``（埋め込み不能時 — CC8）。

    ``ranking.rank_candidates`` が fail-soft で戻るときに使う（事実文の組み立てを
    ranking 側へ散らさないための小さな共有点）。
    """
    return {"coverage": _lens(False, note), "skies": _lens(False, note)}


def build_complement_context(
    session,
    domain_key: str,
    anchor_context: Optional[dict],
    *,
    thin_max_documents: int,
) -> dict:
    """``ranking.rank_candidates(complement_context=...)`` に渡す材料を組む。

    ``anchor_context`` は ``routes`` 側の ``_anchor_context``（VA層 §8）の返り値
    ``{"anchors": [AnchorVector, ...], "skeleton_version": str}``。骨格が無い・
    ベクトル索引が未構築のときは ``None`` で渡ってくる。

    Returns:
        ``{"thin_node_ids": set[str], "sky_statements": [...],
        "facts": {"coverage": {available, note?}, "skies": {available, note?}}}``。
        レンズは**独立に**縮退する（片方が成立しなくても他方は動く — CC8）。
        薄いノードがゼロ・台帳の記帳がゼロは**正常な状態**（= 発見）であり、
        エラーにしない。
    """
    context = dict(anchor_context or {})
    anchors = list(context.get("anchors") or [])
    version = _clean(context.get("skeleton_version"))

    thin: set[str] = set()
    if not anchor_context:
        coverage = _lens(False, NOTE_NO_SKELETON)
    elif not anchors or not version:
        coverage = _lens(False, NOTE_NO_ANCHORS)
    else:
        thin = thin_node_ids(
            session, domain_key, anchors, max_documents=thin_max_documents
        )
        coverage = _lens(True) if thin else _lens(True, NOTE_NO_THIN_NODES)

    statements = sky_statements(session, domain_key)
    skies = _lens(True) if statements else _lens(False, NOTE_NO_SKIES)

    return {
        "thin_node_ids": thin,
        "sky_statements": statements,
        "facts": {"coverage": coverage, "skies": skies},
    }


def order_complement_first(candidates: Sequence[dict]) -> list[dict]:
    """補完の根拠がある候補を先頭へ（元の順序を保つ安定ソート）。

    並べ替えの基準は ``complement`` キーの**有無だけ**で、数値は使わない（CC4）。
    候補を捨てない（PD6）。
    """
    items = list(candidates or [])
    return sorted(items, key=lambda candidate: 0 if candidate.get("complement") else 1)


__all__ = [
    "ASSUMPTION_HUMAN_STATUSES",
    "CLOSED_WORLD_SKY_NOTE",
    "MAX_FILLS_PER_CANDIDATE",
    "MAX_SKIES_PER_CANDIDATE",
    "MAX_SKY_STATEMENTS",
    "MAX_SKY_STATEMENT_CHARS",
    "NODE_KIND_CONCEPT",
    "NOTE_EMBEDDING_UNAVAILABLE",
    "NOTE_NO_ANCHORS",
    "NOTE_NO_SKELETON",
    "NOTE_NO_SKIES",
    "NOTE_NO_THIN_NODES",
    "build_complement_context",
    "degraded_facts",
    "fills_for_vector",
    "order_complement_first",
    "skies_for_vector",
    "sky_statements",
    "thin_node_ids",
]
