"""agent ID の付け替え記録と参照の再係留（knowledge_objects_design.md §5.5・KO8）。

再解析で stable_key が一致した行は同じ UUID のまま残る（KO3）ので、UUID を直接持つ
参照は書き換え不要。書き換えが要るのは **agent 側 ID をそのまま持っている参照**
（``element_explanations.element_id`` / ``epistemic_ledger.target_id`` /
``challenges.target_id`` / ``element_annotations.element_id`` /
``deliberation_sessions.element_id`` / ``element_identity_links.instance_element_id``）だけで、
その付け替えは **stable_key が一致したときに限り** 決定論的に行う（推測で結び直さない）。

一意制約に当たる行は書き換えず ``reanchored.skipped`` に記録する。呼び出し側の
セッションで動き、**commit しない**（同一トランザクションに同乗する）。
FastAPI / LLM は import しない。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text as sa_text

from .schema import TABLE_REMAP

logger = logging.getLogger(__name__)

#: 知識オブジェクト種別 → W層系テーブルの element_type。
KIND_ELEMENT_TYPES: dict[str, str] = {
    "claim": "theory_claim",
    "component": "theory_component",
    "equation": "equation",
}

#: 知識オブジェクト種別 → D層系テーブルの target_type。
KIND_TARGET_TYPES: dict[str, str] = {
    "claim": "claim",
    "component": "component",
    "equation": "equation",
}

#: challenges.target_type の CHECK は ('assumption', 'claim') のみ（migration 031）。
_CHALLENGE_TARGET_TYPES = {"claim"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rowcount(result: Any) -> int:
    try:
        return int(getattr(result, "rowcount", 0) or 0)
    except (TypeError, ValueError):  # pragma: no cover - 防御的
        return 0


def _scalar(result: Any) -> int:
    try:
        row = result.fetchone()
    except AttributeError:  # pragma: no cover - fake session 防御
        return 0
    if not row:
        return 0
    try:
        return int(row[0] or 0)
    except (TypeError, ValueError):  # pragma: no cover
        return 0


def record_and_reanchor(
    session,
    *,
    document_id: str,
    run_id: str | None,
    kind: str,
    remaps: Sequence[tuple[str, str, str]],
) -> dict:
    """``element_id_remap`` に記録し、同一トランザクションで参照を再係留する。

    Args:
        remaps: ``[(old_agent_id, new_agent_id, stable_key)]``（:class:`~.sync.SyncResult`
            の ``remaps``）。
        kind: ``core/schema.py::KNOWLEDGE_OBJECT_KINDS`` の1語彙。
            ``evidence`` / ``derivation_step`` / ``symbol`` は agent ID を持つ参照表が
            無いため記録だけ行う（捏造した対応表を作らない）。

    Returns:
        ``{"recorded": n, "reanchored": {表名: 件数}, "skipped": {表名: 件数}}``。
    """
    pairs = [
        (_clean(old), _clean(new), _clean(key))
        for old, new, key in (remaps or [])
    ]
    pairs = [p for p in pairs if p[0] and p[1] and p[0] != p[1]]
    summary: dict[str, Any] = {"recorded": 0, "reanchored": {}, "skipped": {}}
    if not pairs:
        return summary

    element_type = KIND_ELEMENT_TYPES.get(kind, "")
    target_type = KIND_TARGET_TYPES.get(kind, "")

    for old_id, new_id, stable_key in pairs:
        row = session.execute(
            sa_text(
                f"""
                INSERT INTO {TABLE_REMAP} (
                    document_id, run_id, object_kind, old_id, new_id, stable_key, reanchored
                )
                VALUES (
                    :document_id, CAST(:run_id AS uuid), :object_kind,
                    :old_id, :new_id, :stable_key, CAST('{{}}' AS jsonb)
                )
                RETURNING id
                """
            ),
            {
                "document_id": document_id,
                "run_id": run_id,
                "object_kind": kind,
                "old_id": old_id,
                "new_id": new_id,
                "stable_key": stable_key,
            },
        ).fetchone()
        summary["recorded"] += 1
        remap_id = str(row[0]) if row else ""

        counts = _reanchor_one(
            session,
            document_id=document_id,
            element_type=element_type,
            target_type=target_type,
            old_id=old_id,
            new_id=new_id,
        )
        for table, value in (counts.get("reanchored") or {}).items():
            summary["reanchored"][table] = summary["reanchored"].get(table, 0) + value
        for table, value in (counts.get("skipped") or {}).items():
            summary["skipped"][table] = summary["skipped"].get(table, 0) + value

        if remap_id:
            session.execute(
                sa_text(
                    f"""
                    UPDATE {TABLE_REMAP}
                    SET reanchored = CAST(:reanchored AS jsonb)
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {"id": remap_id, "reanchored": json.dumps(counts, ensure_ascii=False)},
            )
    return summary


def _reanchor_one(
    session,
    *,
    document_id: str,
    element_type: str,
    target_type: str,
    old_id: str,
    new_id: str,
) -> dict:
    """1 組の (old_id → new_id) を各参照表に反映する。"""
    reanchored: dict[str, int] = {}
    skipped: dict[str, int] = {}

    if element_type:
        # element_explanations / element_annotations / deliberation_sessions:
        # いずれも document_id で当該論文に閉じる（agent ID は論文間で衝突しうる）。
        for table in ("element_explanations", "element_annotations", "deliberation_sessions"):
            result = session.execute(
                sa_text(
                    f"""
                    UPDATE {table}
                    SET element_id = :new_id
                    WHERE element_id = :old_id
                      AND element_type = :element_type
                      AND document_id::text = :document_id
                    """
                ),
                {
                    "new_id": new_id,
                    "old_id": old_id,
                    "element_type": element_type,
                    "document_id": document_id,
                },
            )
            count = _rowcount(result)
            if count:
                reanchored[table] = count

        # element_identity_links: 4列 UNIQUE(instance_element_type, instance_element_id,
        # instance_document_id, shared_part_id) に当たる行は書き換えず記録に残す。
        conflicts = _scalar(
            session.execute(
                sa_text(
                    """
                    SELECT count(*)
                    FROM element_identity_links l
                    WHERE l.instance_element_id = :old_id
                      AND l.instance_element_type = :element_type
                      AND l.instance_document_id::text = :document_id
                      AND EXISTS (
                          SELECT 1 FROM element_identity_links e2
                          WHERE e2.instance_element_id = :new_id
                            AND e2.instance_element_type = :element_type
                            AND e2.instance_document_id::text = :document_id
                            AND e2.shared_part_id = l.shared_part_id
                      )
                    """
                ),
                {
                    "old_id": old_id,
                    "new_id": new_id,
                    "element_type": element_type,
                    "document_id": document_id,
                },
            )
        )
        result = session.execute(
            sa_text(
                """
                UPDATE element_identity_links l
                SET instance_element_id = :new_id
                WHERE l.instance_element_id = :old_id
                  AND l.instance_element_type = :element_type
                  AND l.instance_document_id::text = :document_id
                  AND NOT EXISTS (
                      SELECT 1 FROM element_identity_links e2
                      WHERE e2.instance_element_id = :new_id
                        AND e2.instance_element_type = :element_type
                        AND e2.instance_document_id::text = :document_id
                        AND e2.shared_part_id = l.shared_part_id
                  )
                """
            ),
            {
                "new_id": new_id,
                "old_id": old_id,
                "element_type": element_type,
                "document_id": document_id,
            },
        )
        count = _rowcount(result)
        if count:
            reanchored["element_identity_links"] = count
        if conflicts:
            skipped["element_identity_links"] = conflicts

    if target_type:
        # epistemic_ledger: UNIQUE(target_id, target_type) に当たる行は書き換えない
        # （教員の記帳を別の台帳行に合流させない）。
        conflicts = _scalar(
            session.execute(
                sa_text(
                    """
                    SELECT count(*)
                    FROM epistemic_ledger
                    WHERE target_id = :old_id
                      AND target_type = :target_type
                      AND document_id::text = :document_id
                      AND EXISTS (
                          SELECT 1 FROM epistemic_ledger e2
                          WHERE e2.target_id = :new_id AND e2.target_type = :target_type
                      )
                    """
                ),
                {
                    "old_id": old_id,
                    "new_id": new_id,
                    "target_type": target_type,
                    "document_id": document_id,
                },
            )
        )
        result = session.execute(
            sa_text(
                """
                UPDATE epistemic_ledger
                SET target_id = :new_id, updated_at = now()
                WHERE target_id = :old_id
                  AND target_type = :target_type
                  AND document_id::text = :document_id
                  AND NOT EXISTS (
                      SELECT 1 FROM epistemic_ledger e2
                      WHERE e2.target_id = :new_id AND e2.target_type = :target_type
                  )
                """
            ),
            {
                "new_id": new_id,
                "old_id": old_id,
                "target_type": target_type,
                "document_id": document_id,
            },
        )
        count = _rowcount(result)
        if count:
            reanchored["epistemic_ledger"] = count
        if conflicts:
            skipped["epistemic_ledger"] = conflicts

        if target_type in _CHALLENGE_TARGET_TYPES:
            # challenges には document_id 列が無い（migration 031）。agent ID は論文間で
            # 衝突しうるため、同じ対象の台帳行が当該論文に在るときだけ書き換える
            # （範囲を絞れない疑義は書き換えない = 推測で結び直さない）。
            result = session.execute(
                sa_text(
                    """
                    UPDATE challenges c
                    SET target_id = :new_id, updated_at = now()
                    WHERE c.target_id = :old_id
                      AND c.target_type = :target_type
                      AND EXISTS (
                          SELECT 1 FROM epistemic_ledger l
                          WHERE l.target_id = :new_id
                            AND l.target_type = :target_type
                            AND l.document_id::text = :document_id
                      )
                    """
                ),
                {
                    "new_id": new_id,
                    "old_id": old_id,
                    "target_type": target_type,
                    "document_id": document_id,
                },
            )
            count = _rowcount(result)
            if count:
                reanchored["challenges"] = count

    return {"reanchored": reanchored, "skipped": skipped}


def summarize(results: Mapping[str, Any] | None) -> int:
    """再係留サマリの総件数（監査 metadata 用の小さなヘルパ）。"""
    if not results:
        return 0
    total = 0
    for value in (results.get("reanchored") or {}).values():
        try:
            total += int(value or 0)
        except (TypeError, ValueError):  # pragma: no cover
            continue
    return total
