"""コース側から「学ぶ単位」（``learning_units``）を候補として提示・解決する層。

正本: ``docs/features/learning_units_design.md`` §6.2（P2-3）。

役割は3つだけで、いずれも**決定論・非LLM**（LU3）:

1. ``list_unit_candidates`` — コースビルダーへ提示する候補を ``learning_units_live``
   から決定論的な並びで読み、``U1..Un`` の handle を振る。
2. ``render_unit_candidates_block`` — その候補を教材コンテキストの1区画（テキスト）へ
   組む。数値（件数・confidence・order_index）は書かない（LU5）。
3. ``resolve_unit_handles`` — LLM が返した handle を候補表で引き当てて
   ``topic.units[]`` の要素に写す。**候補に無い handle は捨てる**（捏造ガード = LU3）。

加えて freeze（``course_content_builder``）が ``topic.units[].stable_key`` から
live 行を読むための ``load_units_by_keys`` を置く。

規律:
- FastAPI を import しない（``core/`` 共通ルール）。DB は呼び出し側から session を受ける。
- 読むのは **``learning_units_live`` ビューだけ**（基表 ``learning_units`` を
  SELECT してよいのは persistence / versioning のみ = KO5）。
- ``document_id`` は migration 080 以降 uuid 列なので ``CAST(:x AS uuid)`` で束縛する。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text as sa_text

from core import label_vocab
from core.course_data import UNIT_SOURCE_TEACHER_SELECTED, is_symbol_concept_name
from core.schema import LEARNING_UNIT_KINDS, LEARNING_UNIT_KINDS_FOR_COURSE

logger = logging.getLogger(__name__)

#: handle の接頭辞（``U1`` … ``Un``）。コースビルダーのプロンプト規則と対になる。
UNIT_HANDLE_PREFIX = "U"

#: 候補区画に出す summary の切り詰め（設計書 §6.2「summary 40 字」）。
_SUMMARY_LIMIT = 40

#: 候補提示の上限（**種別ごと・document ごと**）。設計書 §6.2 は
#: section_block / thesis_support / parent_component を全件、figure を 8 件とする。
#: ここに現れない種別は上限なし。``dsl_node`` はそもそも
#: ``LEARNING_UNIT_KINDS_FOR_COURSE`` に入っていないので提示されない。
UNIT_KIND_CANDIDATE_LIMITS: dict[str, int] = {"figure": 8}

#: 種別の並び（``LEARNING_UNIT_KINDS`` の宣言順）。handle の決定論の一部。
_KIND_ORDER = {kind: index for index, kind in enumerate(LEARNING_UNIT_KINDS)}


@dataclass(frozen=True)
class UnitCandidate:
    """コースビルダーへ提示する1候補（読み取り専用の値）。"""

    handle: str
    unit_id: str
    stable_key: str
    unit_kind: str
    label: str
    summary: str
    document_id: str

    def as_topic_unit(self, source: str = UNIT_SOURCE_TEACHER_SELECTED) -> dict:
        """``topic.units[]`` の要素へ写す（``course_data.CourseTopicUnit`` の形）。"""
        return {
            "kind": self.unit_kind,
            "stable_key": self.stable_key,
            "unit_id": self.unit_id,
            "label": self.label,
            "source": source,
        }


def unit_kind_label(unit_kind: str) -> str:
    """種別の表示名。語彙表が未整備なら kind をそのまま返す（fail-soft）。

    表の正本は ``core/label_vocab.py::LEARNING_UNIT_KIND_LABELS``。ここで第2の
    日本語表を作らない（label_vocab のガードレール）。
    """
    labels = getattr(label_vocab, "LEARNING_UNIT_KIND_LABELS", None) or {}
    try:
        return str(labels.get(unit_kind) or unit_kind or "")
    except Exception:  # pragma: no cover - 語彙表が dict でない場合の防御
        return str(unit_kind or "")


def _short(text: object, limit: int = _SUMMARY_LIMIT) -> str:
    value = str(text or "").strip().replace("\n", " ")
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _document_placeholders(document_ids: list[str], prefix: str) -> tuple[str, dict]:
    params = {f"{prefix}{index}": did for index, did in enumerate(document_ids)}
    placeholders = ", ".join(f"CAST(:{prefix}{index} AS uuid)" for index in range(len(document_ids)))
    return placeholders, params


def list_unit_candidates(
    session,
    document_ids: list[str],
    *,
    kinds: tuple[str, ...] = LEARNING_UNIT_KINDS_FOR_COURSE,
) -> list[UnitCandidate]:
    """``learning_units_live`` からコースビルダー向けの候補を決定論的に読む。

    並びは **(document_ids の順, kind の LEARNING_UNIT_KINDS 順, order_index, label)**
    で、同じ入力なら常に同じ handle が振られる（DB の返す行順に依存しない）。
    ``review_status = 'dismissed'`` の unit は候補から外す（教員が見送った単位を
    AI が再提示しない）。行削除はしない（LU2）。

    表が無い / 読めない場合は空リストへ縮退する — 候補区画が消えるだけで、
    コースビルダー自体は従来どおり動く（LU8「配信・作業を止めない」と同じ姿勢）。
    """
    document_ids = [str(did).strip() for did in (document_ids or []) if str(did or "").strip()]
    kinds = tuple(k for k in (kinds or ()) if k in _KIND_ORDER)
    if not document_ids or not kinds:
        return []

    placeholders, params = _document_placeholders(document_ids, "did_")
    kind_placeholders = ", ".join(f":kind_{index}" for index in range(len(kinds)))
    params.update({f"kind_{index}": kind for index, kind in enumerate(kinds)})
    rows = session.execute(
        sa_text(f"""
            SELECT id::text, document_id::text, stable_key, unit_kind, label, summary, order_index
            FROM learning_units_live
            WHERE document_id IN ({placeholders})
              AND unit_kind IN ({kind_placeholders})
              AND review_status <> 'dismissed'
        """),
        params,
    ).fetchall()

    doc_order = {did: index for index, did in enumerate(document_ids)}
    items: list[dict] = []
    for row in rows:
        stable_key = str(row[2] or "").strip()
        document_id = str(row[1] or "").strip()
        unit_kind = str(row[3] or "").strip()
        # 種別の絞り込みは SQL 側でも掛けるが、Python 側でも同じ述語を通す
        # （提示してよい種別の判断を1箇所にしない = fail-closed）。
        if not stable_key or unit_kind not in kinds:
            continue
        items.append({
            "unit_id": str(row[0] or ""),
            "document_id": document_id,
            "stable_key": stable_key,
            "unit_kind": unit_kind,
            "label": str(row[4] or "").strip(),
            "summary": str(row[5] or "").strip(),
            "order_index": int(row[6]) if isinstance(row[6], int) else 0,
        })

    items.sort(key=lambda item: (
        doc_order.get(item["document_id"], len(doc_order)),
        _KIND_ORDER.get(item["unit_kind"], len(_KIND_ORDER)),
        item["order_index"],
        item["label"],
        item["stable_key"],
    ))

    candidates: list[UnitCandidate] = []
    used: dict[tuple[str, str], int] = {}
    for item in items:
        bucket = (item["document_id"], item["unit_kind"])
        limit = UNIT_KIND_CANDIDATE_LIMITS.get(item["unit_kind"])
        taken = used.get(bucket, 0)
        if limit is not None and taken >= limit:
            continue
        used[bucket] = taken + 1
        candidates.append(UnitCandidate(
            handle=f"{UNIT_HANDLE_PREFIX}{len(candidates) + 1}",
            unit_id=item["unit_id"],
            stable_key=item["stable_key"],
            unit_kind=item["unit_kind"],
            label=item["label"],
            summary=item["summary"],
            document_id=item["document_id"],
        ))
    return candidates


def render_unit_candidates_block(candidates: list[UnitCandidate]) -> str:
    """候補を教材コンテキストの1区画（テキスト）へ組む。

    1行 = ``U{n} [種別] label — summary``。**数値は書かない**（件数・order_index・
    confidence を出さない = LU5）。候補ゼロなら空文字（区画ごと出さない）。
    """
    if not candidates:
        return ""
    lines = [
        "## 学ぶ単位の候補",
        "以下は、論文の解析結果から決定論的に取り出した「教える単位」の候補です。"
        "各トピックの `units` には、ここに列挙された handle（U1, U2 …）だけを使ってください。",
        "",
    ]
    for candidate in candidates:
        line = f"- {candidate.handle} [{unit_kind_label(candidate.unit_kind)}] {candidate.label}"
        summary = _short(candidate.summary)
        if summary:
            line += f" — {summary}"
        lines.append(line)
    return "\n".join(lines)


def unit_concept_terms_by_document(
    session, document_ids: list[str]
) -> dict[str, list[str]]:
    """``learning_units_live.teaches`` の **concept 項**（分野の言葉）を document ごとに読む。

    正本: ``docs/features/claim_concept_grounding_design.md`` §8（案 E）。コースビルダー・
    教材一覧へ渡す概念の供給を「記号の羅列」から Phase 2 の学ぶ単位が教える言葉に寄せる
    ための読み。**決定論・非LLM**で、SQL は 1 本だけ発行する。

    - 並びは **(kind 順, order_index, label)** = :func:`list_unit_candidates` と同じ規則で、
      同じ入力なら常に同じ並び（DB の返す行順に依存しない）。
    - ``review_status = 'dismissed'`` の unit は読まない（教員が見送った単位の言葉を出さない）。
    - 記号は :func:`core.course_data.is_symbol_concept_name` で除く（CG6）。
    - 表が無い / 読めない場合は空 dict へ縮退する（供給が消えるだけ = LU8）。
    """
    document_ids = [str(did).strip() for did in (document_ids or []) if str(did or "").strip()]
    if not document_ids:
        return {}

    placeholders, params = _document_placeholders(document_ids, "did_")
    try:
        rows = session.execute(
            sa_text(f"""
                SELECT document_id::text, unit_kind, order_index, label, teaches
                FROM learning_units_live
                WHERE document_id IN ({placeholders})
                  AND review_status <> 'dismissed'
            """),
            params,
        ).fetchall()
    except Exception:  # noqa: BLE001 — 供給ごと fail-soft
        logger.warning("learning unit concept terms unavailable", exc_info=True)
        return {}

    ordered = sorted(
        rows,
        key=lambda row: (
            str(row[0] or ""),
            _KIND_ORDER.get(str(row[1] or ""), len(_KIND_ORDER)),
            int(row[2]) if isinstance(row[2], int) else 0,
            str(row[3] or ""),
        ),
    )

    terms_by_document: dict[str, list[str]] = {}
    for row in ordered:
        document_id = str(row[0] or "").strip()
        if not document_id:
            continue
        teaches = row[4] if isinstance(row[4], list) else []
        for item in teaches:
            if not isinstance(item, dict) or str(item.get("kind") or "") != "concept":
                continue
            name = str(item.get("label") or item.get("ref") or "").strip()
            if not name or is_symbol_concept_name(name):
                continue
            bucket = terms_by_document.setdefault(document_id, [])
            if name not in bucket:
                bucket.append(name)
    return terms_by_document


def candidate_keys(candidates: list[UnitCandidate]) -> list[str]:
    """候補表の参照キー（stable_key）を順序保持・重複除去で返す。

    コース登録の一括確定（decision_context の ``presented_ids``）の材料。学習者向け経路
    （routes/learning.py）は内部列名をソースに書かない規律（KO10 ガードレール）があるため、
    そこからはこの関数を経由して読む。
    """
    return list(dict.fromkeys(c.stable_key for c in (candidates or []) if c.stable_key))


def resolve_unit_handles(candidates: list[UnitCandidate], handles: object) -> list[dict]:
    """LLM / クライアントが返した handle を ``topic.units[]`` の要素へ写す。

    - 候補表に無い handle は**捨てる**（捏造ガード = LU3）。
    - 既に解決済みの dict（``stable_key`` 付き）が来た場合も、候補表に同じ
      ``stable_key`` があるときだけ通す（同じ弁を通す）。
    - 重複は先勝ちで落とす。順序は入力順。
    """
    by_handle = {c.handle.upper(): c for c in candidates or []}
    by_key = {c.stable_key: c for c in candidates or []}
    if not isinstance(handles, list):
        return []
    resolved: list[dict] = []
    seen: set[str] = set()
    for raw in handles:
        candidate: UnitCandidate | None = None
        if isinstance(raw, str):
            candidate = by_handle.get(raw.strip().upper())
        elif isinstance(raw, dict):
            key = str(raw.get("stable_key") or "").strip()
            if key:
                candidate = by_key.get(key)
            if candidate is None:
                handle = str(raw.get("handle") or raw.get("unit") or "").strip().upper()
                if handle:
                    candidate = by_handle.get(handle)
        if candidate is None or candidate.stable_key in seen:
            continue
        seen.add(candidate.stable_key)
        resolved.append(candidate.as_topic_unit())
    return resolved


_UNIT_ROW_COLUMNS = """
            SELECT id::text, document_id::text, stable_key, unit_kind, label, summary,
                   linked_claim_ids, linked_equation_ids, linked_component_ids,
                   linked_figure_ids, agent_payload, order_index
            FROM learning_units_live
"""


def load_units_by_keys(session, document_ids: list[str], keys: list[str]) -> dict[str, dict]:
    """``stable_key -> live 行``（freeze が成果を束ねるための読み）。

    document 集合でスコープを固定する（別コース・非可視 document の unit を
    stable_key だけで引けないようにする）。キーが空なら **SQL を発行しない**。
    """
    document_ids = [str(did).strip() for did in (document_ids or []) if str(did or "").strip()]
    keys = list(dict.fromkeys(str(k).strip() for k in (keys or []) if str(k or "").strip()))
    if not document_ids or not keys:
        return {}

    placeholders, params = _document_placeholders(document_ids, "did_")
    key_placeholders = ", ".join(f":key_{index}" for index in range(len(keys)))
    params.update({f"key_{index}": key for index, key in enumerate(keys)})
    rows = session.execute(
        sa_text(f"""{_UNIT_ROW_COLUMNS}
            WHERE document_id IN ({placeholders})
              AND stable_key IN ({key_placeholders})
        """),
        params,
    ).fetchall()
    return _units_from_rows(rows)


def load_units_for_documents(session, document_ids: list[str]) -> dict[str, dict]:
    """``stable_key -> live 行``（document 集合の全 live unit）。

    freeze は①トピックが選んだ unit の解決②救済（文字列一致）で当たった component が
    どの unit の子かの逆引き、の2つに同じ dict を使うため、キー指定ではなく
    document 集合で1回読む。``document_ids`` が空なら SQL を発行しない。
    """
    document_ids = [str(did).strip() for did in (document_ids or []) if str(did or "").strip()]
    if not document_ids:
        return {}
    placeholders, params = _document_placeholders(document_ids, "did_")
    rows = session.execute(
        sa_text(f"""{_UNIT_ROW_COLUMNS}
            WHERE document_id IN ({placeholders})
        """),
        params,
    ).fetchall()
    return _units_from_rows(rows)


def _units_from_rows(rows) -> dict[str, dict]:
    units: dict[str, dict] = {}
    for row in rows:
        stable_key = str(row[2] or "").strip()
        if not stable_key:
            continue
        agent_payload = row[10] if isinstance(row[10], dict) else {}
        units[stable_key] = {
            "unit_id": str(row[0] or ""),
            "document_id": str(row[1] or ""),
            "stable_key": stable_key,
            "unit_kind": str(row[3] or ""),
            "label": str(row[4] or "").strip(),
            "summary": str(row[5] or "").strip(),
            "linked_claim_ids": _as_str_list(row[6]),
            "linked_equation_ids": _as_str_list(row[7]),
            "linked_component_ids": _as_str_list(row[8]),
            "linked_figure_ids": _as_str_list(row[9]),
            "agent_payload": agent_payload,
            "order_index": int(row[11]) if isinstance(row[11], int) else 0,
        }
    return units


def unit_component_agent_ids(unit_row: dict | None) -> list[str]:
    """unit が束ねる component の **agent 側 ID**（artifact 名前空間）。

    正本は ``agent_payload.linked_component_agent_ids``（設計書 §4.1: 「コース側が
    artifact と突合するために必須」）。``linked_component_ids`` は DB UUID なので
    artifact の component 索引とは突合できない — 混ぜない。
    """
    if not isinstance(unit_row, dict):
        return []
    payload = unit_row.get("agent_payload")
    payload = payload if isinstance(payload, dict) else {}
    return list(dict.fromkeys(_as_str_list(payload.get("linked_component_agent_ids"))))


def unit_equation_agent_ids(unit_row: dict | None) -> list[str]:
    """unit が束ねる式の agent 側 equation_id（``linked_equation_ids`` が既に agent ID）。"""
    if not isinstance(unit_row, dict):
        return []
    return list(dict.fromkeys(_as_str_list(unit_row.get("linked_equation_ids"))))


def unit_claim_agent_ids(unit_row: dict | None) -> list[str]:
    """unit が束ねる claim の **agent 側 ID** 候補。

    ``linked_claim_ids`` は DB UUID（設計書 §4.1）なので artifact の claim 索引
    （``claim_object_builder`` の ``claim_id``）とは別名前空間。突合できるのは
    ``agent_payload`` に素の record として残っている agent 側 ID だけなので、
    そちらを優先して返す。**UUID を agent ID として流さない**（呼び出し側が
    artifact 索引に存在するものだけを採る）。
    """
    if not isinstance(unit_row, dict):
        return []
    payload = unit_row.get("agent_payload")
    payload = payload if isinstance(payload, dict) else {}
    ids: list[str] = []
    for field in ("linked_claim_agent_ids", "claim_ids"):
        ids.extend(_as_str_list(payload.get(field)))
    # record 側が UUID しか持たない場合の保険として linked_claim_ids も候補に含めるが、
    # 呼び出し側で artifact 索引に存在するものだけを採るため、混線は起きない。
    ids.extend(_as_str_list(unit_row.get("linked_claim_ids")))
    return list(dict.fromkeys(ids))
