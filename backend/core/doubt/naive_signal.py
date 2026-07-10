"""部品6 — 素朴な問いの計器化（B層集計の D層ビュー, D1-5）。

「複数の学習者が独立に同じ前提の手前でつまずく」を、前提側から見える集計にする。
読み取り専用ビュー。学習者側には何も変わらない・何も見えない。

守るべき一線:
  - 対象は **本人が引き受けた行のみ**:
      tension: TENSION_OWNED_STATUSES（open / articulated / connected / abstracted）
      structure_anchor: attribution_source ∈ (learner_selected, confirmed) かつ
      status ≠ dismissed
    candidate（本人未確定）の trace は集計に入れない（P1）。
  - k-匿名化 k=3。n<3 の anchor セル・doubt_type セルはレスポンスに一切含めない（P3）。
  - 件数はレンジ表示（3-5 / 6-10 / 11+）のみ。生数値・個人特定フィールドを返さない。
  - 評価利用禁止。一覧・ランキング化しない（呼び出し側 UI もノード詳細での気づきのみ）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.schema import NAIVE_SIGNAL_K_ANONYMITY, count_range_label
from core.postgres import get_session

logger = logging.getLogger(__name__)

# tension 側の「本人が引き受けた」status（core/tension/schema.py TENSION_OWNED_STATUSES と同値。
# D層は B層モジュールに依存方向を作らないためここに保守的に再掲する）
_TENSION_OWNED_STATUSES = ("open", "articulated", "connected", "abstracted")

_ANCHOR_OWNED_SOURCES = ("learner_selected", "confirmed")


def _payload_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _collect_anchor_rows(session, course_id: str) -> list[tuple[str, str, str, str, str]]:
    """(user_id, anchor_type, anchor_id, anchor_label, doubt_type) のリストを返す。"""
    entries: list[tuple[str, str, str, str, str]] = []

    # --- structure_anchor（構造帰属型の問い）: 本人確定済みのみ -------------
    rows = session.execute(
        sa_text("""
            SELECT user_id::text, payload
            FROM interest_traces
            WHERE course_id = :cid
              AND kind IN ('question', 'misconception')
              AND payload->'structure_anchor' IS NOT NULL
              AND (payload->'structure_anchor'->>'attribution_source') = ANY(:sources)
              AND COALESCE(payload->'structure_anchor'->>'status', 'active') <> 'dismissed'
        """),
        {"cid": course_id, "sources": list(_ANCHOR_OWNED_SOURCES)},
    ).fetchall()
    for row in rows:
        payload = _payload_dict(row[1])
        anchor = payload.get("structure_anchor")
        if not isinstance(anchor, dict):
            continue
        anchor_type = str(anchor.get("anchor_type") or "").strip()
        anchor_id = str(anchor.get("anchor_id") or "").strip()
        if not anchor_type or not anchor_id:
            continue
        entries.append((
            str(row[0]),
            anchor_type,
            anchor_id,
            str(anchor.get("anchor_label") or ""),
            str(anchor.get("doubt_type") or "unclassified"),
        ))

    # --- tension（違和感）: 本人が引き受けた status のみ ----------------------
    rows = session.execute(
        sa_text("""
            SELECT user_id::text, payload
            FROM interest_traces
            WHERE course_id = :cid
              AND kind = 'tension'
              AND status = ANY(:statuses)
        """),
        {"cid": course_id, "statuses": list(_TENSION_OWNED_STATUSES)},
    ).fetchall()
    for row in rows:
        payload = _payload_dict(row[1])
        refs = payload.get("target_refs")
        if not isinstance(refs, dict):
            continue
        for key, anchor_type in (
            ("component_ids", "component"),
            ("claim_ids", "claim"),
            ("equation_ids", "equation"),
            ("chunk_ids", "chunk"),
        ):
            values = refs.get(key)
            if not isinstance(values, list):
                continue
            for anchor_id in values:
                aid = str(anchor_id or "").strip()
                if aid:
                    entries.append((str(row[0]), anchor_type, aid, "", "unclassified"))

    return entries


def aggregate_entries(entries: list[tuple[str, str, str, str, str]]) -> list[dict]:
    """(user_id, anchor_type, anchor_id, anchor_label, doubt_type) の列を
    anchor 単位で k-匿名集計する（純関数・DB 非依存）。

    - anchor 単位はユニーク学習者数で数える（同一学習者の連打で膨らませない）
    - n < 3（k-匿名閾値未満）の anchor / doubt_type セルは結果に含めない（P3）
    - 生の件数・user_id は結果に存在しない（count_range のみ）
    """
    anchor_users: dict[tuple[str, str], set[str]] = {}
    anchor_labels: dict[tuple[str, str], str] = {}
    doubt_users: dict[tuple[str, str, str], set[str]] = {}

    for user_id, anchor_type, anchor_id, anchor_label, doubt_type in entries:
        key = (anchor_type, anchor_id)
        anchor_users.setdefault(key, set()).add(user_id)
        if anchor_label and not anchor_labels.get(key):
            anchor_labels[key] = anchor_label
        dt = doubt_type or "unclassified"
        doubt_users.setdefault((anchor_type, anchor_id, dt), set()).add(user_id)

    signals = []
    for (anchor_type, anchor_id), users in anchor_users.items():
        count = len(users)
        if count < NAIVE_SIGNAL_K_ANONYMITY:
            continue  # n<3 セルは非表示（P3）
        doubt_list = []
        for (a_type, a_id, dt), dt_users in doubt_users.items():
            if (a_type, a_id) != (anchor_type, anchor_id):
                continue
            if len(dt_users) < NAIVE_SIGNAL_K_ANONYMITY:
                continue  # doubt_type セルも k-匿名
            doubt_list.append({
                "doubt_type": dt,
                "count_range": count_range_label(len(dt_users)),
            })
        doubt_list.sort(key=lambda d: d["doubt_type"])
        signals.append({
            "anchor_type": anchor_type,
            "anchor_id": anchor_id,
            "anchor_label": anchor_labels.get((anchor_type, anchor_id), ""),
            "count_range": count_range_label(count),
            "doubt_types": doubt_list,
        })

    # 並びは anchor_type → anchor_id の辞書順（ランキングにしない）
    signals.sort(key=lambda s: (s["anchor_type"], s["anchor_id"]))
    return signals


def aggregate_naive_signals(course_id: str) -> dict:
    """コース内の素朴な問いシグナルを anchor 単位で k-匿名集計する。

    Returns:
        {"course_id": ..., "signals": [
            {"anchor_type": ..., "anchor_id": ..., "anchor_label": ...,
             "count_range": "3-5" | "6-10" | "11+",
             "doubt_types": [{"doubt_type": ..., "count_range": ...}, ...]}
        ]}
    """
    course_id = str(course_id or "").strip()
    if not course_id:
        return {"course_id": "", "signals": []}

    session = get_session()
    try:
        entries = _collect_anchor_rows(session, course_id)
    except Exception:
        logger.warning("naive signal aggregation failed for course=%s", course_id, exc_info=True)
        return {"course_id": course_id, "signals": []}
    finally:
        session.close()

    return {"course_id": course_id, "signals": aggregate_entries(entries)}


def has_naive_signal(anchor_type: str, anchor_id: str, course_id: str = "") -> bool:
    """特定 anchor に k≥3 のシグナルがあるか（台帳表示の「学習者の問い」行, D1-5）。

    True でも件数は返さない。表示は
    「複数の学習者がここで問いを立てています」の事実文のみ。
    """
    anchor_type = str(anchor_type or "").strip()
    anchor_id = str(anchor_id or "").strip()
    if not anchor_type or not anchor_id:
        return False

    session = get_session()
    try:
        params: dict[str, Any] = {
            "atype": anchor_type,
            "aid": anchor_id,
            "sources": list(_ANCHOR_OWNED_SOURCES),
            "statuses": list(_TENSION_OWNED_STATUSES),
        }
        course_filter = ""
        if course_id:
            course_filter = "AND course_id = :cid"
            params["cid"] = course_id
        row = session.execute(
            sa_text(f"""
                SELECT COUNT(DISTINCT user_id) FROM (
                    SELECT user_id
                    FROM interest_traces
                    WHERE kind IN ('question', 'misconception')
                      {course_filter}
                      AND (payload->'structure_anchor'->>'attribution_source') = ANY(:sources)
                      AND COALESCE(payload->'structure_anchor'->>'status', 'active') <> 'dismissed'
                      AND (payload->'structure_anchor'->>'anchor_type') = :atype
                      AND (payload->'structure_anchor'->>'anchor_id') = :aid
                    UNION
                    SELECT user_id
                    FROM interest_traces
                    WHERE kind = 'tension'
                      {course_filter}
                      AND status = ANY(:statuses)
                      AND payload->'target_refs' IS NOT NULL
                      AND (
                        (payload->'target_refs'->'component_ids') @> to_jsonb(:aid::text)
                        OR (payload->'target_refs'->'claim_ids') @> to_jsonb(:aid::text)
                        OR (payload->'target_refs'->'equation_ids') @> to_jsonb(:aid::text)
                      )
                ) owned
            """),
            params,
        ).fetchone()
        return int(row[0] or 0) >= NAIVE_SIGNAL_K_ANONYMITY
    except Exception:
        logger.debug("has_naive_signal failed for %s/%s", anchor_type, anchor_id, exc_info=True)
        return False
    finally:
        session.close()
