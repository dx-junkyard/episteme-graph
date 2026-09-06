"""コーパス回遊層 — コース非依存の「論文の海」の読み時導出（Phase A / C）。

正本設計書: ``docs/features/corpus_roaming_design.md``（不変条項 CR1〜CR10。
§4 = コーパス地図 / §6 = 地図の端）。親層は
``docs/features/paper_discovery_design.md``（PD1〜PD8）と
``docs/features/knowledge_landscape_design.md``（LS1〜LS10）。

本モジュールが構造として守るもの:

- **CR1 document 可視性が唯一のゲート（fail-closed）** — 全ての読み出しは呼び出し側が
  渡した「本人が閲覧できる document.id の集合」（``services.list_visible_document_ids``）を
  **SQL 内 ``= ANY(:doc_ids)`` で強制**する。空集合は SQL を発行せず空を返す
  （「空集合 = 全件」に転ばせない）。route 層でのフィルタ後付けにしない。
- **CR3 数値を見せない** — DTO に重み・確からしさ・類似度・件数の生値を入れない。
  配置には出所ラベル（``core.landscape.schema.provenance_label``）を必ず付ける。
  縁の支持論文は**タイトルの列挙**で、件数・バッジを出さない（LS5 の学習者版）。
- **CR4 閉世界の正直さ** — 縁は「このコーパスの中では地図に置かれていない」、外は
  「教員の検索条件（時点付き）では」しか言わない。分野全体を断定する語彙は
  ``tests/test_corpus_roaming_guardrails.py`` の denylist が構造的に禁止する。
  事実文は :data:`FACT_FRINGE` / :func:`outer_fact_line` の2つだけで、他所で
  文言を組み立てない。
- **CR7 学習者起点で外部 API を呼ばない** — 外の輪は教員の最終検索が残した集約1ビット
  （``paper_discovery_subscriptions.last_search_found_new``, migration 073）からの
  読み時導出のみ。本モジュールは arXiv / Semantic Scholar クライアントを import しない。
- **CR8 情報を落とさない / CR9 同期パスに LLM を入れない** — 読み取り専用（``INSERT`` /
  ``UPDATE`` / ``DELETE`` 文を持たない）・非LLM・保存物なし。

セッションは呼び出し側が管理する（``core/postgres`` のセッションを route 層が
開閉する）。本モジュールは commit / rollback / close を行わない。
FastAPI / core.llm を import しない（開発ルール2）。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Sequence

from sqlalchemy import text as sa_text

from core import atlas_store
from core.atlas_gaps import schema as gap_schema
from core.landscape import schema as landscape_schema

logger = logging.getLogger(__name__)

__all__ = [
    "FACT_FRINGE",
    "RING_FRINGE",
    "RING_OUTER",
    "RINGS",
    "build_corpus_landscape",
    "list_corpus_domains",
    "list_corpus_documents",
    "outer_fact_line",
]


# ---------------------------------------------------------------------------
# 語彙・事実文（CR4: ここが唯一の正本。他所で文言を組み立てない）
# ---------------------------------------------------------------------------

#: 端の輪の語彙（Phase D の関心信号 ``ring`` と共有する）。
RING_FRINGE = "fringe"
RING_OUTER = "outer"
RINGS = (RING_FRINGE, RING_OUTER)

#: 縁（取り込み済みだが地図に置けなかった主題）の固定事実文（§6.1）。
#: 分野全体について断定していると読める形にしない（CR4）。
FACT_FRINGE = "この領域の先に、まだ地図に置かれていない主題を扱う論文があります。"

#: 外（教員の検索条件に一致する未取り込み論文の存在）の事実文テンプレート（§6.2）。
#: 「この検索条件では」の限定と時点を落とさない（CR4）。
_FACT_OUTER_TEMPLATE = (
    "教員の検索条件では、まだ取り込まれていない論文が arXiv にありました（{date} 時点）。"
)


def outer_fact_line(checked_date: str) -> str:
    """外の輪の事実文（時点付き）。``checked_date`` は ``YYYY-MM-DD`` の日付文字列。"""
    return _FACT_OUTER_TEMPLATE.format(date=checked_date)


# ---------------------------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------------------------


def _visible_ids(visible_doc_ids: Iterable[str] | None) -> list[str]:
    """可視 document id を正規化する（CR1: 空なら呼び出し側が SQL を発行しない）。"""
    return sorted(
        {str(d).strip() for d in (visible_doc_ids or ()) if str(d or "").strip()}
    )


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _title(value: Any) -> str:
    return str(value or "").strip()


def _as_date(value: Any) -> str:
    """``last_checked_at`` を ``YYYY-MM-DD`` に整形する（時刻・件数は出さない）。

    datetime / date（``strftime`` を持つ）と ISO 文字列の両方を受ける。解釈できない
    値は空文字を返し、呼び出し側は外の輪を**行ごと出さない**（推測した日付を書かない）。
    """
    if value is None:
        return ""
    strftime = getattr(value, "strftime", None)
    if callable(strftime):
        try:
            return strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            return ""
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return ""


def _skeleton_index(skeleton: Any) -> tuple[dict[str, dict], dict[str, str]]:
    """骨格 → ``({node_id: {"label", "region_id", "kind"}}, {region_id: label})``。

    ``core.landscape.projection.skeleton_node_index`` と同じ索引だが、単一ドメイン
    （``(domain_key, node_id)`` の複合キー不要）なので軽量版をここに置く。
    """
    nodes: dict[str, dict] = {}
    regions: dict[str, str] = {}
    for region in getattr(skeleton, "regions", ()) or ():
        region_id = _clean(getattr(region, "id", ""))
        if not region_id:
            continue
        region_label = _clean(getattr(region, "label", "")) or region_id
        regions[region_id] = region_label
        nodes[region_id] = {
            "label": region_label,
            "region_id": region_id,
            "kind": landscape_schema.NODE_KIND_REGION,
        }
        for concept in getattr(region, "concepts", ()) or ():
            concept_id = _clean(getattr(concept, "id", ""))
            if not concept_id:
                continue
            nodes[concept_id] = {
                "label": _clean(getattr(concept, "label", "")) or concept_id,
                "region_id": region_id,
                "kind": landscape_schema.NODE_KIND_CONCEPT,
            }
    return nodes, regions


# ---------------------------------------------------------------------------
# Phase A-1: ドメイン一覧
# ---------------------------------------------------------------------------


def _domains_with_visible_placements(session: Any, doc_ids: Sequence[str]) -> set[str]:
    """可視 document の配置が1件でもあるドメインの集合（**件数は数えない** — CR3）。"""
    if not doc_ids:
        return set()
    rows = session.execute(
        sa_text(
            """
            SELECT DISTINCT domain_key
              FROM landscape_placements
             WHERE document_id::text = ANY(:doc_ids)
               AND status = ANY(:statuses)
            """
        ),
        {
            "doc_ids": list(doc_ids),
            "statuses": list(landscape_schema.LEARNER_VISIBLE_STATUSES),
        },
    ).fetchall()
    return {_clean(r[0]) for r in rows if _clean(r[0])}


def list_corpus_domains(
    session: Any, visible_doc_ids: Iterable[str] | None
) -> list[dict]:
    """凍結骨格を持つ active ドメインの一覧（§4.1）。

    返す各要素は ``{domain_key, domain_name, frozen_version, has_visible_papers}``。
    ``has_visible_papers`` は「本人が閲覧できる論文の配置が1件でもあるか」の **bool**
    で、**件数は返さない**（CR3）。retired ドメインは一覧に出さない（AB3 と同じ扱い
    — 地図そのものは残るが、回遊の入口には並べない）。並びは ``domain_key`` 昇順。
    """
    doc_ids = _visible_ids(visible_doc_ids)
    try:
        domains = atlas_store.list_domains(session)
    except Exception:  # noqa: BLE001
        logger.warning("corpus domain listing failed", exc_info=True)
        return []
    placed = _domains_with_visible_placements(session, doc_ids)

    out: list[dict] = []
    for entry in domains or []:
        domain_key = _clean(entry.get("domain_key"))
        frozen_version = _clean(entry.get("frozen_version"))
        lifecycle = _clean(entry.get("lifecycle")) or "active"
        if not domain_key or not frozen_version or lifecycle != "active":
            continue
        out.append(
            {
                "domain_key": domain_key,
                "domain_name": _clean(entry.get("domain_name")) or domain_key,
                "frozen_version": frozen_version,
                "has_visible_papers": domain_key in placed,
            }
        )
    out.sort(key=lambda d: d["domain_key"])
    return out


# ---------------------------------------------------------------------------
# Phase A-2 / C: 1ドメインの地図（配置 + 縁 + 外）
# ---------------------------------------------------------------------------


def _visible_placements(session: Any, domain_key: str, doc_ids: Sequence[str]) -> list:
    if not doc_ids:
        return []
    return session.execute(
        sa_text(
            """
            SELECT p.document_id::text,
                   COALESCE(NULLIF(d.title, ''), NULLIF(d.filename, ''), '') AS title,
                   p.node_id,
                   p.perspective,
                   p.status
              FROM landscape_placements p
              JOIN documents d ON d.id = p.document_id
             WHERE p.domain_key = :domain_key
               AND p.status = ANY(:statuses)
               AND p.document_id::text = ANY(:doc_ids)
             ORDER BY p.node_id, p.perspective, title, p.document_id
            """
        ),
        {
            "domain_key": domain_key,
            "doc_ids": list(doc_ids),
            "statuses": list(landscape_schema.LEARNER_VISIBLE_STATUSES),
        },
    ).fetchall()


def _visible_gap_signals(session: Any, domain_key: str, doc_ids: Sequence[str]) -> list:
    """縁の材料（§6.1）。

    読むのは ``landscape_gap_signals``（論文由来の信号）だけで、教員の判断
    （カテゴリギャップ候補の decisions 表）には**一切触れない** — 学習者に見せるのは
    「置けなかった論文が存在する」という事実であって、共有候補の審議状況ではない
    （カテゴリギャップ設計 §5.6 の学習者非開示を維持）。
    """
    if not doc_ids:
        return []
    return session.execute(
        sa_text(
            """
            SELECT s.parent_region_id,
                   COALESCE(NULLIF(d.title, ''), NULLIF(d.filename, ''), '') AS title
              FROM landscape_gap_signals s
              JOIN documents d ON d.id = s.document_id
             WHERE s.domain_key = :domain_key
               AND s.status = :active
               AND s.document_id::text = ANY(:doc_ids)
             ORDER BY s.parent_region_id, title
            """
        ),
        {
            "domain_key": domain_key,
            "doc_ids": list(doc_ids),
            "active": gap_schema.SIGNAL_STATUS_ACTIVE,
        },
    ).fetchall()


def _build_fringe(rows: Iterable[Any], regions: dict[str, str]) -> list[dict]:
    """信号行 → 領域単位の縁（``[{region_id, region_label, fact_line, paper_titles}]``）。

    現行の凍結骨格に無い ``parent_region_id`` の信号は落とす（地図に描けないため。
    行そのものは DB に残り、教員のレビュー候補には出続ける — P4）。
    """
    grouped: dict[str, list[str]] = {}
    for row in rows or ():
        region_id = _clean(row[0])
        if region_id not in regions:
            continue
        title = _title(row[1])
        titles = grouped.setdefault(region_id, [])
        if title and title not in titles:
            titles.append(title)
    out = [
        {
            "region_id": region_id,
            "region_label": regions.get(region_id, region_id),
            "fact_line": FACT_FRINGE,
            "paper_titles": sorted(titles),
        }
        for region_id, titles in grouped.items()
    ]
    out.sort(key=lambda e: e["region_id"])
    return out


def _build_outer(session: Any, domain_key: str) -> dict | None:
    """外の輪（§6.2）。購読なし / ビット未設定 / FALSE / 時点不明は ``None``。"""
    try:
        row = session.execute(
            sa_text(
                """
                SELECT last_search_found_new, last_checked_at
                  FROM paper_discovery_subscriptions
                 WHERE domain_key = :domain_key
                 LIMIT 1
                """
            ),
            {"domain_key": domain_key},
        ).fetchone()
    except Exception:  # noqa: BLE001
        # 購読テーブルが読めなくても地図は出す（外の輪が消えるだけ）。
        logger.warning("corpus outer ring lookup failed", exc_info=True)
        return None
    if not row or not bool(row[0]):
        return None
    checked_date = _as_date(row[1])
    if not checked_date:
        return None
    return {"fact_line": outer_fact_line(checked_date)}


def build_corpus_landscape(
    session: Any, domain_key: str, visible_doc_ids: Iterable[str] | None
) -> dict | None:
    """1ドメインのコーパス地図（配置 + 縁 + 外）を読み時導出する（§4.1 / §6）。

    凍結骨格が無ければ ``None``（呼び出し側は 404 = 地図領域ごと非表示。atlas の
    fail-closed の流儀）。骨格そのもの（領域配置・座標）は既存の
    ``GET /api/atlas?cartridge={domain_key}`` が返すため**ここでは返さない**
    （描画資産を二重管理しない）。

    Returns:
        ``{domain_key, skeleton_version, placements, fringe, outer}``。

        - ``placements``: ``{document_id, document_title, anchor_node_id, node_label,
          region_id, perspective, perspective_label, status, source_label}``。
          現行骨格に無い ``node_id`` の配置は落とす（LS6 と同じ fail-closed）。
          重み・確からしさ・claim_id は構造的に載らない（CR3）。
        - ``fringe``: 領域単位の縁（:func:`_build_fringe`）。
        - ``outer``: 外の輪（:func:`_build_outer`）。無ければ ``None``。
    """
    key = _clean(domain_key)
    if not key:
        return None
    skeleton = atlas_store.load_learner_skeleton(key, session)
    if skeleton is None:
        return None
    nodes, regions = _skeleton_index(skeleton)

    doc_ids = _visible_ids(visible_doc_ids)
    placements: list[dict] = []
    for row in _visible_placements(session, key, doc_ids):
        node_id = _clean(row[2])
        info = nodes.get(node_id)
        if info is None:
            continue
        status = _clean(row[4])
        perspective = _clean(row[3])
        placements.append(
            {
                "document_id": _clean(row[0]),
                "document_title": _title(row[1]),
                "anchor_node_id": node_id,
                "node_label": info["label"],
                "region_id": info["region_id"],
                "perspective": perspective,
                "perspective_label": landscape_schema.perspective_label(perspective),
                "status": status,
                # 出所ラベル（「AIによる推定（未確認）」/「教員確認済み」）は必須（CR3）。
                "source_label": landscape_schema.provenance_label(status),
            }
        )

    return {
        "domain_key": key,
        "skeleton_version": _clean(getattr(skeleton, "version", "")),
        "placements": placements,
        "fringe": _build_fringe(_visible_gap_signals(session, key, doc_ids), regions),
        "outer": _build_outer(session, key),
    }


# ---------------------------------------------------------------------------
# Phase A-3: 論文リスト
# ---------------------------------------------------------------------------


def _authors(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(a).strip() for a in value if str(a or "").strip()]
    text = _clean(value)
    return [text] if text else []


def list_corpus_documents(
    session: Any, domain_key: str, visible_doc_ids: Iterable[str] | None
) -> list[dict]:
    """このドメインに関係づけられた可視論文の一覧（§4.1）。新しい順・数値スコアなし。

    対象は「配置がある論文」∪「地図に置けなかった信号がある論文」。どちらにも
    現れない論文は**この分野の論文として並べない**（解析が届いていないだけの論文を
    分野に割り当てるのは投影の捏造になる — LS1 / CR3）。``placed`` はその区別で、
    ``false`` は「取り込まれているが現行の地図には置かれていない」という事実。

    ``can_discuss`` は Phase B（コース無し論文議論）の入口の可否。v1 は常に ``True``
    で、開幕素材の有無は ``GET .../discuss/opening`` が ``available:false`` として
    正直に返す（ここで解析状態を推測しない）。
    """
    key = _clean(domain_key)
    doc_ids = _visible_ids(visible_doc_ids)
    if not key or not doc_ids:
        return []
    rows = session.execute(
        sa_text(
            """
            SELECT d.id::text,
                   COALESCE(NULLIF(d.title, ''), NULLIF(d.filename, ''), '') AS title,
                   d.authors,
                   d.year,
                   EXISTS (
                       SELECT 1 FROM landscape_placements p
                        WHERE p.document_id = d.id
                          AND p.domain_key = :domain_key
                          AND p.status = ANY(:statuses)
                   ) AS placed
              FROM documents d
             WHERE d.id::text = ANY(:doc_ids)
               AND (
                   EXISTS (
                       SELECT 1 FROM landscape_placements p2
                        WHERE p2.document_id = d.id
                          AND p2.domain_key = :domain_key
                          AND p2.status = ANY(:statuses)
                   )
                   OR EXISTS (
                       SELECT 1 FROM landscape_gap_signals s
                        WHERE s.document_id = d.id
                          AND s.domain_key = :domain_key
                          AND s.status = :active
                   )
               )
             ORDER BY d.created_at DESC, d.id
            """
        ),
        {
            "domain_key": key,
            "doc_ids": list(doc_ids),
            "statuses": list(landscape_schema.LEARNER_VISIBLE_STATUSES),
            "active": gap_schema.SIGNAL_STATUS_ACTIVE,
        },
    ).fetchall()
    return [
        {
            "document_id": _clean(row[0]),
            "title": _title(row[1]),
            "authors": _authors(row[2]),
            "year": int(row[3]) if row[3] is not None else None,
            "placed": bool(row[4]),
            "can_discuss": True,
        }
        for row in rows
    ]
