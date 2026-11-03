"""凍結したコースの部品参照（⚓）を DB UUID で持つ・読み時に版をまたいで辿る（第 25 周 第 6 波・IK-1021）。

課題: コースは凍結時に部品を agent ID（``comp_003``）で埋め込み、学習者 API は live の行の
``legacy_ids`` で解決していた。agent ID は解析 run ごとの連番なので、論文を再解析すると同じ
``comp_003`` が別の部品に割り当たり、教材の ⚓「TOV network map」が別の部品（損失）を開いた。

オーナー判断（推奨どおり）:

- **新しい凍結から** ⚓ の部品を DB UUID で指す: :func:`stamp_component_db_ids` が凍結時の
  ``content_blocks`` の部品の行と ``evidence_links`` の部品のリンクに ``db_id``（その時点の live 行の
  UUID）を足す。教材本文の ``![[component:comp_003]]`` と ``component_id`` は変えない（教材の
  解決規則・原稿スタジオ・ほかの読み手の契約を変えないため）。学習者 API は ⚓ の ID から
  :func:`frozen_component_ref` で ``db_id`` を引いて UUID で解決する。
- **旧い凍結**（``db_id`` の無い参照）は、凍結時の部品名（``label``）を手がかりに、名前の一致する
  行（live を優先・無ければ superseded）を引き、superseded なら ``element_id_remap`` の
  ``superseded_component`` の記録を辿る（:mod:`core.knowledge_objects.remap`）。

推測で結ばない: 1 対 1 に決まらなければ従来の解決に戻すか、置き換わった事実を言う。
FastAPI / LLM は import しない。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

#: 凍結の参照に足すキー（部品の DB UUID）。``_id`` で終わるので表示投影は値を遮断しない（番地キー）。
DB_ID_KEY = "db_id"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def load_component_uuid_index(session, document_ids: Iterable[str]) -> dict[tuple[str, str], str]:
    """``(document_id, agent component ID) -> live 行の UUID``（1 行に決まる組だけ）。

    読むのは live ビューだけ。失敗は空（凍結を止めない — ``db_id`` が付かないだけ）。
    """
    docs = [str(d) for d in (document_ids or ()) if str(d or "").strip()]
    if not docs:
        return {}
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id::text, document_id::text, agent_component_id,
                       source_scope->'legacy_ids' AS legacy_ids
                FROM theory_components_live
                WHERE document_id = ANY(CAST(:docs AS uuid[]))
                """
            ),
            {"docs": docs},
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("frozen component refs: uuid index read failed", exc_info=True)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {}
    seen: dict[tuple[str, str], set[str]] = {}
    for row in rows or []:
        uuid, doc = _clean(row[0]), _clean(row[1])
        if not uuid or not doc:
            continue
        agents = {_clean(row[2])} if len(row) > 2 and row[2] else set()
        legacy = row[3] if len(row) > 3 else None
        if isinstance(legacy, list):
            agents |= {_clean(a) for a in legacy if _clean(a)}
        for agent in agents:
            if agent:
                seen.setdefault((doc, agent), set()).add(uuid)
    return {key: next(iter(ids)) for key, ids in seen.items() if len(ids) == 1}


def _component_block_items(topic: Mapping[str, Any]) -> list[dict]:
    items: list[dict] = []
    for block in (topic or {}).get("content_blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "components":
            continue
        for item in block.get("items") or []:
            if isinstance(item, dict):
                items.append(item)
    return items


def stamp_component_db_ids(topics: Iterable[dict], uuid_index: Mapping[tuple[str, str], str]) -> int:
    """凍結するトピックの部品参照に ``db_id`` を足す（入力を書き換える・純関数）。

    - ``content_blocks`` の部品の行: 自分の ``document_id`` × ``component_id`` で引く。
    - ``evidence_links`` の部品のリンク: 同じトピックの部品の行から document を引き（同じ agent ID が
      2 論文に当たれば付けない）、同じ規則で引く。

    Returns:
        ``db_id`` を足した参照の数（監査・テスト用）。
    """
    if not uuid_index:
        return 0
    stamped = 0
    for topic in topics or ():
        if not isinstance(topic, dict):
            continue
        docs_by_agent: dict[str, set[str]] = {}
        for item in _component_block_items(topic):
            agent = _clean(item.get("component_id"))
            doc = _clean(item.get("document_id"))
            if not agent or not doc:
                continue
            docs_by_agent.setdefault(agent, set()).add(doc)
            uuid = uuid_index.get((doc, agent))
            if uuid:
                item[DB_ID_KEY] = uuid
                stamped += 1
        for link in topic.get("evidence_links") or []:
            if not isinstance(link, dict) or str(link.get("kind") or "") != "component":
                continue
            agent = _clean(link.get("target_id") or link.get("id"))
            docs = docs_by_agent.get(agent) or set()
            if len(docs) != 1:
                continue
            uuid = uuid_index.get((next(iter(docs)), agent))
            if uuid:
                link[DB_ID_KEY] = uuid
                stamped += 1
    return stamped


def frozen_component_ref(
    course_data: Mapping[str, Any] | None, component_id: str, topic_id: str | None = None
) -> dict | None:
    """凍結したコースの中の ⚓（``component_id``）の記録 ``{db_id, label, document_id}`` を引く。

    ``topic_id`` があればそのトピックを先に見る。同じ ID が複数のトピックで別の部品（別の
    ``db_id`` / 別の論文）を指していて決まらなければ ``None``。見つからなければ ``None``。
    """
    ref = _clean(component_id)
    if not ref or not isinstance(course_data, Mapping):
        return None
    try:
        from core.course_data import iter_all_topics

        topics = [t for t in iter_all_topics(course_data) if isinstance(t, dict)]
    except Exception:  # noqa: BLE001
        topics = [t for t in (course_data.get("topics") or []) if isinstance(t, dict)]
    wanted_topic = _clean(topic_id)
    ordered = (
        [t for t in topics if _clean(t.get("id")) == wanted_topic]
        + [t for t in topics if _clean(t.get("id")) != wanted_topic]
        if wanted_topic else topics
    )

    def _records(topic: dict) -> list[dict]:
        found: list[dict] = []
        for item in _component_block_items(topic):
            if _clean(item.get("component_id")) == ref:
                found.append({
                    "db_id": _clean(item.get(DB_ID_KEY)),
                    "label": _clean(item.get("label")),
                    "document_id": _clean(item.get("document_id")),
                })
        for link in topic.get("evidence_links") or []:
            if (
                isinstance(link, dict)
                and str(link.get("kind") or "") == "component"
                and _clean(link.get("target_id") or link.get("id")) == ref
            ):
                found.append({
                    "db_id": _clean(link.get(DB_ID_KEY)),
                    "label": _clean(link.get("label")),
                    "document_id": "",
                })
        return found

    if wanted_topic:
        first = [t for t in ordered[:1] if _clean(t.get("id")) == wanted_topic]
        records = _records(first[0]) if first else []
        if records:
            return _merge(records)
    records = [r for t in ordered for r in _records(t)]
    return _merge(records) if records else None


def _merge(records: list[dict]) -> dict | None:
    db_ids = {r["db_id"] for r in records if r.get("db_id")}
    docs = {r["document_id"] for r in records if r.get("document_id")}
    labels = [r["label"] for r in records if r.get("label")]
    if len(db_ids) > 1 or len(docs) > 1:
        return None
    return {
        "db_id": next(iter(db_ids)) if db_ids else "",
        "label": labels[0] if labels else "",
        "document_id": next(iter(docs)) if docs else "",
    }


def resolve_frozen_component(
    session,
    *,
    component_id: str,
    frozen: Mapping[str, Any] | None,
    document_ids: Iterable[str],
) -> dict | None:
    """凍結の ⚓ を、版をまたいで今の部品 UUID に解決する（読み時・推測で結ばない）。

    Returns:
        ``{"status": "live" | "remapped" | "superseded", "id": UUID}``。
        ``remapped`` は ``id`` が後継の live 行・``superseded`` は後継が決まらない旧行。
        ``superseded`` には ``reason``（``not_found`` = 出典の重なる新しい部品が無い /
        ``ambiguous`` = 1 対 1 に決まらない）が付く。記録が無い置き換わりは読み時に同じ規則で
        つなぐ（``read_time: True``・第 7 波 IK-1051）。
        手がかりが無い・凍結時の部品が引けない・live のまま変わっていないときは ``None``
        （呼び出し側は従来どおり ⚓ の ID で解決する）。
    """
    from core.knowledge_objects.remap import (
        READ_TIME_MATCHED,
        read_time_superseded_successor,
        successor_component_id,
        superseded_component_by_agent_id,
    )

    docs = [str(d) for d in (document_ids or ()) if str(d or "").strip()]
    db_id = _clean((frozen or {}).get("db_id"))

    def _from_superseded(uuid: str) -> dict:
        # 再解析時の記録（superseded_component）があればそれを辿る。無ければ（第 6 波より前の
        # 再解析）、同じ規則（出典の重なりで 1 対 1）を読み時に当てる（IK-1051・オーナー判断）。
        successor = successor_component_id(session, uuid)
        if successor:
            return {"status": "remapped", "id": successor}
        found_id, reason = read_time_superseded_successor(session, uuid)
        if found_id and reason == READ_TIME_MATCHED:
            return {"status": "remapped", "id": found_id, "read_time": True}
        return {"status": "superseded", "id": uuid, "reason": reason}

    try:
        if db_id:
            successor = successor_component_id(session, db_id)
            if successor == db_id:
                return {"status": "live", "id": db_id}
            if successor:
                return {"status": "remapped", "id": successor}
            return _from_superseded(db_id)
        label = _clean((frozen or {}).get("label"))
        if not label:
            return None
        found = superseded_component_by_agent_id(
            session, agent_id=component_id, document_ids=docs, frozen_label=label
        )
        if not found:
            return None
        uuid, state = found
        if state == "live":
            return {"status": "live", "id": uuid}
        return _from_superseded(uuid)
    except Exception:  # noqa: BLE001 — 読み時の補助。引けなければ従来の解決に任せる
        logger.debug("frozen component resolve failed for %s", component_id, exc_info=True)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


# ── 所有者への案内（第 7 波・IK-1051・オーナー判断）──────────────────────────────
#: 原稿スタジオの見出しに出す事実文（``{names}`` は凍結時の部品名の列挙）。G層 To-Do は作らない。
FROZEN_ANCHOR_STALE_NOTICE = (
    "このコースの教材の部品の参照のうち、論文の解析のやり直しで置き換わり、新しい部品が決まらないものが"
    "あります（{names}）。学生の画面では、この部品の文脈と説明を開けません。凍結した教材の本文は変えていません。"
    "［コース内容生成］でコースを凍結し直すと、新しい部品につながります（自動では凍結し直しません）。"
)
#: 保存できない閲覧者（editor 共有）向けに添える 1 文。
FROZEN_ANCHOR_STALE_OWNER_ONLY = "凍結し直せるのはコースの所有者です。"
_STALE_SCAN_MAX = 60
_STALE_NAMES_MAX = 5


def _frozen_component_refs_in_course(course_data: Mapping[str, Any]) -> list[tuple[str, str]]:
    """コースの凍結の ⚓（``(topic_id, component_id)``）を重複なく列挙する（上限つき）。"""
    try:
        from core.course_data import iter_all_topics

        topics = [t for t in iter_all_topics(course_data) if isinstance(t, dict)]
    except Exception:  # noqa: BLE001
        topics = [t for t in (course_data.get("topics") or []) if isinstance(t, dict)]
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for topic in topics:
        topic_id = _clean(topic.get("id"))
        ids = [_clean(i.get("component_id")) for i in _component_block_items(topic)]
        ids += [
            _clean(l.get("target_id") or l.get("id"))
            for l in topic.get("evidence_links") or []
            if isinstance(l, dict) and str(l.get("kind") or "") == "component"
        ]
        for cid in ids:
            if cid and cid not in seen:
                seen.add(cid)
                out.append((topic_id, cid))
            if len(out) >= _STALE_SCAN_MAX:
                return out
    return out


def stale_frozen_component_labels(
    session, course_data: Mapping[str, Any] | None, document_ids: Iterable[str]
) -> list[str]:
    """凍結の ⚓ のうち、置き換わって後継の決まらない部品の凍結時の名前（読み時・fail-soft）。

    学生の画面で ``available: false`` になる ⚓ と同じ判定（:func:`resolve_frozen_component`）。
    読み時に後継へつながる ⚓（``remapped``）は含めない。
    """
    if not isinstance(course_data, Mapping):
        return []
    docs = [str(d) for d in (document_ids or ()) if str(d or "").strip()]
    names: list[str] = []
    for topic_id, cid in _frozen_component_refs_in_course(course_data):
        frozen = frozen_component_ref(course_data, cid, topic_id)
        if not frozen:
            continue
        resolved = resolve_frozen_component(
            session, component_id=cid, frozen=frozen, document_ids=docs
        )
        if isinstance(resolved, dict) and resolved.get("status") == "superseded":
            label = _clean(frozen.get("label")) or "名前の記録が無い部品"
            if label not in names:
                names.append(label)
    return names


def frozen_anchor_stale_notice(names: list[str], *, can_save: bool) -> str:
    """原稿スタジオの見出しの事実文（無ければ空文字）。件数は出さず名前を列挙する。"""
    if not names:
        return ""
    shown = [f"「{n}」" for n in names[:_STALE_NAMES_MAX]]
    if len(names) > _STALE_NAMES_MAX:
        shown.append("ほか")
    text = FROZEN_ANCHOR_STALE_NOTICE.format(names="・".join(shown))
    return text if can_save else text + FROZEN_ANCHOR_STALE_OWNER_ONLY
