"""教員向け anchor インサイト — stage / doubt_type 単位の k-匿名化集約（設計書 §7 Stage 3）。

正本設計書: ``docs/features/structure-anchored-questions.md`` §7 Stage 3 / §8-5。
現行仕様の要約は CLAUDE.md「構造帰属型の問い記録」節。

**責務の切り分け（既存集計と重複させない）**:

- ``api/services.py::aggregate_interest_dashboard`` — **トピック単位**のホットスポット。
  構造帰属（anchor_type / doubt_type）の内訳は持たない。
- ``core/doubt/naive_signal.py`` — **anchor 単位**（anchor_type × anchor_id）で
  「どの前提の手前でつまずいたか」を D層台帳のノード詳細に出すためのビュー。
  tension も合流させる。
- **本モジュール** — 個々の anchor ではなく「**理論構成のどの段階に、どういう型の
  引っかかりが集まっているか**」の二次元セル（anchor_type〔stage は stage 別〕
  × doubt_type）。教材改善ループ（設計書 §7 Stage 3）のための粗い断面で、
  anchor_id 単位まで割らない。tension は混ぜない（構造帰属型の問いだけを見る）。

守るべき一線（P3 / P7 を継承）:

- 対象は **本人が引き受けた帰属のみ** — ``attribution_source ∈
  (learner_selected, confirmed)``。``llm_candidate``（本人未確定）は入れない（P1）。
- ``structure_anchor.status = 'dismissed'``（本人が帰属を棄却）と、行の
  ``status = 'superseded'``（書き直し・削除で取り除かれた往復）は集計から外す。
  どちらも行は消していない（P4）ので、除外は読み側の責務。
- k-匿名化 k=3（正本は ``core/privacy.py``）。distinct **学習者数**で判定し、
  同一学習者の連打でセルを膨らませない。閾値未満のセルはレスポンスに含めない。
- 件数はレンジ表示（3-5 / 6-10 / 11+）のみ。生件数・user_id・質問原文・
  confidence・anchor_id は返さない。
- ランキングにしない（並びは語彙の辞書順で固定。多い順に並べ替えない。P7）。
- 評価利用禁止。読み取り専用・監査記帳なし・LLM 0 回。

``payload.map_excluded`` は**参照しない**。あれは「わたしの地図」（personal_graph）の
表示から本人が外す訂正操作であって、帰属そのものの取り消しではない
（取り消しは ``structure_anchor.status='dismissed'`` 側）。地図に出さない選択を
教員向け集約への opt-out として読み替えると、本人の意図しない意味を後付けすることに
なるため、ここでは無視する。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.element_vocab import theory_stage_label
from core.privacy import bucket_count_range, meets_k_anonymity
from core.structure_anchor.schema import (
    ANCHOR_TYPE_LABELS,
    ATTRIBUTION_CONFIRMED,
    ATTRIBUTION_LEARNER_SELECTED,
    DOUBT_TYPE_LABELS,
    THEORY_STAGES,
)

logger = logging.getLogger(__name__)

__all__ = ["aggregate_anchor_insights", "aggregate_cells", "collect_anchor_entries"]

# 本人が引き受けた帰属だけを集計対象にする（llm_candidate は含めない。P1）
_OWNED_SOURCES = (ATTRIBUTION_LEARNER_SELECTED, ATTRIBUTION_CONFIRMED)

# 教員向けの注記（数値ではなく事実文で、集約の性質と利用制限を明示する）
INSIGHTS_NOTE = (
    "学習者個人は特定できません（k-匿名集約・件数はレンジ表示のみ）。"
    "本人が確定した帰属のみを対象にしています。評価利用は禁止です。"
)

SUPPRESSED_NOTE = (
    "表示できる集計はまだありません"
    "（同じ箇所・同じ型の引っかかりが十分な人数に達していません）。"
)


def _payload_dict(raw: Any) -> dict:
    """payload（dict / JSON 文字列 / それ以外）を dict へ正規化する。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def collect_anchor_entries(session, course_id: str) -> list[tuple[str, str, str, str]]:
    """``(user_id, anchor_type, stage_key, doubt_type)`` のリストを返す（DB 読み取り）。

    ``stage_key`` は ``anchor_type == 'stage'`` のときだけ ``anchor_id``
    （= theory stage の語彙）を入れ、それ以外は空文字。**anchor_id そのものは
    ここから先へ持ち出さない**（stage 以外は個別要素まで割らない断面にする）。
    """
    rows = session.execute(
        sa_text("""
            SELECT user_id::text, payload
            FROM interest_traces
            WHERE course_id = :cid
              AND kind = 'question'
              AND status <> 'superseded'
              AND payload->'structure_anchor' IS NOT NULL
              AND (payload->'structure_anchor'->>'attribution_source') = ANY(:sources)
              AND COALESCE(payload->'structure_anchor'->>'status', 'active') <> 'dismissed'
        """),
        {"cid": course_id, "sources": list(_OWNED_SOURCES)},
    ).fetchall()

    entries: list[tuple[str, str, str, str]] = []
    for row in rows:
        anchor = _payload_dict(row[1]).get("structure_anchor")
        if not isinstance(anchor, dict):
            continue
        anchor_type = str(anchor.get("anchor_type") or "").strip()
        if anchor_type not in ANCHOR_TYPE_LABELS:
            continue  # 語彙外は集計しない（行は残っている。P4）
        stage_key = ""
        if anchor_type == "stage":
            candidate = str(anchor.get("anchor_id") or "").strip()
            # domain-independent な stage 語彙に載っているものだけを stage として扱う
            stage_key = candidate if candidate in THEORY_STAGES else ""
        doubt_type = str(anchor.get("doubt_type") or "").strip() or "unclassified"
        if doubt_type not in DOUBT_TYPE_LABELS:
            doubt_type = "unclassified"
        entries.append((str(row[0]), anchor_type, stage_key, doubt_type))
    return entries


def aggregate_cells(entries: list[tuple[str, str, str, str]]) -> dict:
    """``(user_id, anchor_type, stage_key, doubt_type)`` の列を k-匿名集計する（純関数）。

    Returns:
        ``{"cells": [...], "suppressed": bool}``。``cells`` の各要素は
        anchor_type / doubt_type の**日本語ラベル付き**セルで、``count_range``
        以外の数値を持たない。``suppressed`` は「集計候補はあったが、すべて
        k 未満で1件も出せなかった」ことを正直に示す（k 未満の内訳・件数・
        どのセルだったかは一切返さない）。
    """
    cell_users: dict[tuple[str, str, str], set[str]] = {}
    for user_id, anchor_type, stage_key, doubt_type in entries:
        key = (anchor_type, stage_key, doubt_type)
        cell_users.setdefault(key, set()).add(str(user_id))

    cells: list[dict] = []
    for (anchor_type, stage_key, doubt_type), users in cell_users.items():
        if not meets_k_anonymity(len(users)):
            continue  # n<3 セルは結果に含めない（P3）
        cell = {
            "anchor_type": anchor_type,
            "anchor_type_label": ANCHOR_TYPE_LABELS.get(anchor_type, anchor_type),
            "doubt_type": doubt_type,
            "doubt_type_label": DOUBT_TYPE_LABELS.get(doubt_type, doubt_type),
            "count_range": bucket_count_range(len(users)),
        }
        if stage_key:
            cell["stage"] = stage_key
            # theory stage の訳語の正本は core/element_vocab.py（表を再定義しない）
            cell["stage_label"] = theory_stage_label(stage_key) or stage_key
        cells.append(cell)

    # 並びは語彙の辞書順で固定する（多い順に並べ替えてランキングにしない。P7）
    cells.sort(key=lambda c: (c["anchor_type"], c.get("stage", ""), c["doubt_type"]))

    return {
        "cells": cells,
        "suppressed": bool(cell_users) and not cells,
    }


def aggregate_anchor_insights(session, course_id: str) -> dict:
    """コース内の構造帰属型の問いを stage / doubt_type 単位で k-匿名集計する。

    Args:
        session: SQLAlchemy セッション（呼び出し側が open / close する）。
        course_id: 対象コース ID。

    Returns:
        ``{"course_id", "cells", "suppressed", "note"}``。集計に失敗しても
        例外を投げず空集計へ縮退する（教員画面をエラーで止めない）。
    """
    course_id = str(course_id or "").strip()
    if not course_id:
        return {"course_id": "", "cells": [], "suppressed": False, "note": INSIGHTS_NOTE}

    try:
        entries = collect_anchor_entries(session, course_id)
    except Exception:
        logger.warning(
            "anchor insights aggregation failed for course=%s", course_id, exc_info=True,
        )
        return {
            "course_id": course_id, "cells": [], "suppressed": False, "note": INSIGHTS_NOTE,
        }

    result = aggregate_cells(entries)
    return {
        "course_id": course_id,
        "cells": result["cells"],
        "suppressed": result["suppressed"],
        "note": SUPPRESSED_NOTE if result["suppressed"] else INSIGHTS_NOTE,
    }
