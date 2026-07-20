"""学習者重ね合わせの集約 — 橋候補（知識ネットワークビジョン Phase B）。

設計の正本は ``docs/features/knowledge_network_vision.md``
（§3 修正①「重ね合わせは3系統あり、混ぜない」・§4 KN-4・§7 Phase B）と
``docs/features/personal_knowledge_network_design.md``（§0 PN-1・§3 の bridge 辺）。

- **データ源**: ``interest_traces`` の ``kind='tension'`` かつ ``status='connected'`` 行の
  ``payload.connected_refs.component_ids / edge_ids`` **のみ**。``connected_refs`` は
  ``connect_tension_trace``（``backend/api/services.py``）が本人の connect 操作で
  明示的に指定された ID だけを書く専用キーであり、LLM 候補生成時点で payload に
  書かれる別キーは一切参照しない（``core/personal_graph/derive.py::_tension_anchor``
  の docstring と同じ理由。KN-4/PN-3）。
- **集約**: course 単位・参照先（component / graph edge）ごとの **distinct 学習者数**で
  数え、k-匿名（k=3。正本は ``core/privacy.py`` — リテラル再定義しない）を満たす参照
  だけを「橋候補」として返す。人数はレンジ表示のみ（3-5 / 6-10 / 11+）。
  個別 user_id・生カウントは応答に存在しない（PN-1/P3）。
- **KN-4**: これは学習者の重ね合わせから浮かぶ**教育的知識**（多くの学習者がどこに橋を
  架けるか）の集約であって、ドメイン知識ではない。ドメイン知識候補への自動昇格経路
  （identity link の自動生成等）は作らない — 教員への候補提示のみ。評価利用禁止（P3）。
- **「繋がりを見失う」側（迷いの集約）は v1 では見送り** — 本モジュールは橋のみを扱う。
- 非LLM・読み取り専用（書き込みゼロ・新テーブルなし）。FastAPI / routes / services /
  ``core.llm`` を import しない（PN-5。ガードレールは
  ``backend/tests/test_personal_graph_guardrails.py``）。

純粋部（``collect_bridge_entries`` / ``aggregate_entries``）と DB 部
（``_fetch_connected_tension_rows`` / ``_fetch_component_names`` /
``aggregate_bridge_candidates``）を分離し、純粋部は fake rows（dict のリスト）だけで
ユニットテストできる（``derive.py`` と同じ流儀。テストは
``backend/tests/test_personal_graph_bridges.py``）。

注: Phase P の本人スコープ読み（``queries.py``）と異なり、本モジュールは Phase B の
教員向け**コーススコープ集約**であるため、DB 読みを自前で持つ（``queries.py`` の
「本人スコープ読みの正本」という責務に course 横断集約を混ぜない）。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import text as sa_text

from core.personal_graph.schema import ANCHOR_TYPE_COMPONENT, ANCHOR_TYPE_GRAPH_EDGE
from core.postgres import get_session as _pg_session

# k-匿名閾値とレンジ導出の正本は core/privacy.py（k=3 をここで再定義しない）。
from core.privacy import K_ANONYMITY, bucket_count_range

logger = logging.getLogger(__name__)

# 決定論順のためのレンジ順位（人数レンジの大きい順）。ソートにのみ使い、応答には出ない。
_RANGE_ORDER = {"11+": 0, "6-10": 1, "3-5": 2}


def _payload_dict(raw) -> dict:
    """JSONB 列を dict に正規化する（文字列で来た場合も防御。naive_signal と同型）。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


# ---------------------------------------------------------------------------
# 純粋部（DB 非依存。fake rows で検証可能）
# ---------------------------------------------------------------------------


def collect_bridge_entries(traces: list[dict]) -> list[tuple[str, str, str]]:
    """trace 行（dict）を ``(user_id, ref_type, ref_id)`` の列へ展開する（純粋部）。

    対象は ``kind='tension'`` かつ ``status='connected'`` の行のみ（SQL 側でも絞るが、
    純粋部単体でも同じゲートを持ち、connected 以外の行が混ざっても集計に入らない）。
    参照は ``payload.connected_refs`` の component_ids / edge_ids だけを読む —
    本人が connect 操作で指定していない LLM 候補由来の参照キーは読まない（KN-4/PN-3）。
    """
    entries: list[tuple[str, str, str]] = []
    for trace in traces:
        if trace.get("kind") != "tension" or trace.get("status") != "connected":
            continue
        user_id = str(trace.get("user_id") or "")
        if not user_id:
            continue
        payload = trace.get("payload") or {}
        refs = payload.get("connected_refs")
        if not isinstance(refs, dict):
            # connected_refs を持たない connected 行（本機能導入前のデータ等）は
            # 橋の根拠にしない（fail-closed。derive.py の縮退と同じ思想）。
            continue
        for component_id in refs.get("component_ids") or []:
            if component_id:
                entries.append((user_id, ANCHOR_TYPE_COMPONENT, str(component_id)))
        for edge_id in refs.get("edge_ids") or []:
            if edge_id:
                entries.append((user_id, ANCHOR_TYPE_GRAPH_EDGE, str(edge_id)))
    return entries


def aggregate_entries(entries: list[tuple[str, str, str]]) -> list[dict]:
    """``(user_id, ref_type, ref_id)`` の列を参照先単位で k-匿名集約する（純粋部）。

    - 参照先ごとに **distinct 学習者数**で数える（同一学習者の複数 connect は1と数える）
    - 学習者数が ``K_ANONYMITY``（k=3・``core/privacy.py`` 正本）未満の参照は
      結果に一切含めない（P3。セル秘匿でなく行ごと非表示）
    - 人数は ``bucket_count_range`` のレンジ文字列のみ。生の人数・user_id は
      結果のどのキーにも存在しない（PN-1/P3）
    - 並びは決定論: 人数レンジの大きい順 → ref_type → ref_id の辞書順
      （順位付けが目的ではない。生カウントはソートにも使わずレンジで比較する）
    """
    ref_users: dict[tuple[str, str], set[str]] = {}
    for user_id, ref_type, ref_id in entries:
        ref_users.setdefault((ref_type, ref_id), set()).add(user_id)

    bridges: list[dict] = []
    for (ref_type, ref_id), users in ref_users.items():
        if len(users) < K_ANONYMITY:
            continue  # k-匿名未満の参照は非表示（P3）
        bridges.append({
            "ref_type": ref_type,
            "ref_id": ref_id,
            "learner_count_range": bucket_count_range(len(users)),
            "label": "",
        })
    bridges.sort(key=lambda b: (
        _RANGE_ORDER.get(b["learner_count_range"], len(_RANGE_ORDER)),
        b["ref_type"],
        b["ref_id"],
    ))
    return bridges


# ---------------------------------------------------------------------------
# DB 部（core.postgres.get_session 直読み・try/finally close。開発ルール4）
# ---------------------------------------------------------------------------


def _fetch_connected_tension_rows(course_id: str) -> list[dict]:
    """コース内の ``kind='tension'`` かつ ``status='connected'`` 行を読む（読み取り専用）。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT user_id::text, kind, status, payload
                FROM interest_traces
                WHERE course_id = :cid AND kind = 'tension' AND status = 'connected'
            """),
            {"cid": course_id},
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "user_id": str(r[0]),
            "kind": r[1],
            "status": r[2],
            "payload": _payload_dict(r[3]),
        }
        for r in rows
    ]


def _fetch_component_names(component_ids: list[str]) -> dict[str, str]:
    """theory_components.name を id → name で返す（不正 UUID 等は空 dict へ縮退）。"""
    ids = [str(c) for c in component_ids if c]
    if not ids:
        return {}
    session = _pg_session()
    try:
        placeholders = ", ".join(f"CAST(:id_{i} AS uuid)" for i in range(len(ids)))
        try:
            rows = session.execute(
                sa_text(
                    f"SELECT id::text, name FROM theory_components WHERE id IN ({placeholders})"
                ),
                {f"id_{i}": component_id for i, component_id in enumerate(ids)},
            ).fetchall()
        except Exception:
            # ref_id が UUID でない等。label 無しで返す（集約自体は成立させる）。
            return {}
    finally:
        session.close()
    return {str(r[0]): (r[1] or "") for r in rows}


def aggregate_bridge_candidates(course_id: str) -> list[dict]:
    """コース内の橋候補を k-匿名集約して返す（Phase B のエントリポイント）。

    Returns:
        [{"ref_type": "component" | "graph_edge",
          "ref_id": str,
          "learner_count_range": "3-5" | "6-10" | "11+",
          "label": str}]

    ``label`` は component のとき ``theory_components.name``（解決できなければ空文字）、
    graph_edge のときは常に空文字（ref_id のみで提示する）。個別の学習者・個別の痕跡行に
    関する情報は一切含まれない（PN-1/P3。教員への候補提示のみ・評価利用禁止・
    ドメイン知識候補への自動昇格なし。KN-4）。DB 障害時は空リストへ fail-safe
    （``core/doubt/naive_signal.py`` と同じ扱い）。
    """
    course_id = str(course_id or "").strip()
    if not course_id:
        return []
    try:
        traces = _fetch_connected_tension_rows(course_id)
    except Exception:
        logger.warning(
            "bridge aggregation failed for course=%s", course_id, exc_info=True
        )
        return []

    bridges = aggregate_entries(collect_bridge_entries(traces))

    component_ids = [b["ref_id"] for b in bridges if b["ref_type"] == ANCHOR_TYPE_COMPONENT]
    if component_ids:
        names = _fetch_component_names(component_ids)
        for bridge in bridges:
            if bridge["ref_type"] == ANCHOR_TYPE_COMPONENT:
                bridge["label"] = names.get(bridge["ref_id"], "")
    return bridges
