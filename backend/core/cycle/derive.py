"""理解サイクル（UCサイクル）Phase 1 の純関数（rows → DTO）。

``core.personal_graph.derive`` と同じ「純粋関数と DB 読み出しの分離」に倣い、DB に
触れず fake rows（dict のリスト）だけで単体テストできる（``test_understanding_cycle_core.py``
参照）。**数値（件数・率）を出力に含めない**（UC9）。
"""

from __future__ import annotations

from typing import Any

from core.cycle.schema import QUICK_LABELS

_MAX_REVISIT_FACTS = 3
_MAX_LANDING_CANDIDATES = 5
_EXCERPT_LEN = 80
_ANCHOR_EXCERPT_LEN = 60


def _excerpt(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def build_revisit_facts(
    carryover_row: dict | None,
    rows: list[dict],
    map_diff_facts: list[str] | None = None,
) -> list[str]:
    """REVISIT 直後に見せる差分の事実文を最大3件、列挙型で組み立てる（設計書 §5.2）。

    件数・率は出さない（「〜を2件確定しています」のような数値文は作らない。UC9）。
    ``rows`` は ``core.cycle.queries.fetch_recent_traces_since`` の生データ想定だが、
    フィルタ条件をここでも再検査する（personal_graph/derive.py と同じ二重防御）。

    ``map_diff_facts``（Phase 2 §6「帰り道の景色」、既定 None＝後方互換）は
    ``core.cycle.map_diff.build_map_diff_facts`` が導出した個人知識ネットワークの
    構造差分事実文。指定時は先頭に合流させ（構造差分を優先表示）、合計
    ``_MAX_REVISIT_FACTS`` 件で打ち切る。
    """
    if not carryover_row:
        return []
    facts: list[str] = []
    for row in rows:
        if len(facts) >= _MAX_REVISIT_FACTS:
            break
        kind = row.get("kind")
        status = row.get("status")
        payload = row.get("payload") or {}
        if kind == "tension" and status == "articulated":
            text = payload.get("learner_text") or payload.get("text") or ""
            text = _excerpt(text, _EXCERPT_LEN)
            if not text:
                continue
            facts.append(
                f"前回の問いのあとに、あなたはこの引っかかりを言葉にしています: 『{text}』"
            )
        elif kind == "anchor_mark":
            anchor = payload.get("structure_anchor") or {}
            if anchor.get("status") != "active":
                continue
            text = _excerpt(payload.get("text") or "", _EXCERPT_LEN)
            if not text:
                continue
            quick_label = payload.get("quick_label")
            label_jp = QUICK_LABELS.get(quick_label, {}).get("label", "")
            suffix = f"（{label_jp}）" if label_jp else ""
            facts.append(
                f"前回の問いのあとに、あなたはこの箇所に印を残しています: 『{text}』{suffix}"
            )
        elif kind == "question":
            anchor = payload.get("structure_anchor") or {}
            if anchor.get("attribution_source") not in ("learner_selected", "confirmed"):
                continue
            text = _excerpt(payload.get("text") or "", _EXCERPT_LEN)
            if not text:
                continue
            facts.append(
                f"前回の問いのあとに、あなたはこの箇所への問いを確定しています: 『{text}』"
            )
    combined = list(map_diff_facts or []) + facts
    return combined[:_MAX_REVISIT_FACTS]


def build_landing_candidates(rows: list[dict], limit: int = _MAX_LANDING_CANDIDATES) -> list[dict]:
    """LEAVE（持ち越す問いの選択リスト）候補を優先順に組み立てる（設計書 §5.5）。

    並び順: revisit=true の anchor_mark → articulated tension →
    その他 active anchor_mark → confirmed/learner_selected question。
    各要素は ``{trace_id, kind, label, revisit}`` のみ（数値は含めない。UC9）。
    """
    revisit_anchors: list[dict] = []
    tensions: list[dict] = []
    other_anchors: list[dict] = []
    questions: list[dict] = []

    for row in rows:
        kind = row.get("kind")
        status = row.get("status")
        payload = row.get("payload") or {}
        trace_id = row.get("id")

        if kind == "anchor_mark":
            anchor = payload.get("structure_anchor") or {}
            if anchor.get("status") != "active":
                continue
            text = (payload.get("text") or "").strip()
            quick_label = payload.get("quick_label")
            label_jp = QUICK_LABELS.get(quick_label, {}).get("label", "")
            if text:
                label = f"{label_jp}: {_excerpt(text, _ANCHOR_EXCERPT_LEN)}" if label_jp else _excerpt(
                    text, _ANCHOR_EXCERPT_LEN
                )
            else:
                label = label_jp
            revisit = bool(payload.get("revisit"))
            item = {"trace_id": trace_id, "kind": kind, "label": label, "revisit": revisit}
            (revisit_anchors if revisit else other_anchors).append(item)
        elif kind == "tension" and status == "articulated":
            text = payload.get("learner_text") or payload.get("text") or ""
            label = _excerpt(text, _EXCERPT_LEN)
            if not label:
                continue
            tensions.append({"trace_id": trace_id, "kind": kind, "label": label, "revisit": False})
        elif kind == "question":
            anchor = payload.get("structure_anchor") or {}
            if anchor.get("attribution_source") not in ("learner_selected", "confirmed"):
                continue
            label = _excerpt(payload.get("text") or "", _EXCERPT_LEN)
            if not label:
                continue
            questions.append({"trace_id": trace_id, "kind": kind, "label": label, "revisit": False})

    ordered = revisit_anchors + tensions + other_anchors + questions
    return ordered[:limit]


def build_intention_dto(carryover_row: dict | None, has_any_intention: bool) -> dict[str, Any]:
    """discuss opening へ同梱する ``intention`` フィールドを組み立てる（設計書 §5.1/§5.2）。

    ``has_motive``: 当該コースに intention 痕跡が一件でもあるか（初回判定の裏返し。
    True なら初回の「なぜ今開きましたか」プロンプトは再提示しない）。
    """
    carryover: dict[str, Any] | None = None
    if carryover_row:
        carryover = {
            "trace_id": carryover_row.get("id"),
            "text": carryover_row.get("text", ""),
            "created_at": carryover_row.get("created_at", ""),
        }
    return {"carryover": carryover, "has_motive": bool(has_any_intention)}
