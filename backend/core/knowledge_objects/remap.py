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
        # epistemic_ledger: 行キー (document_id, target_type, target_id)（migration 092・IK-0899）に
        # 当たる行は書き換えない（教員の記帳を別の台帳行に合流させない）。
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
                            AND e2.document_id::text = :document_id
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
                        AND e2.document_id::text = :document_id
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


def record_key_remaps(
    session,
    *,
    document_id: str,
    run_id: str | None,
    kind: str,
    key_remaps: Sequence[tuple[str, str, str]],
) -> int:
    """``stable_key`` の付け替えを ``element_id_remap`` に**記録だけ**する（P1-R3）。

    :func:`~.sync.sync_live_rows` の第2段突合（本文 / 名前の完全一致）で、
    バックフィルの近似キーを agent 側の計算結果へ引き継いだ事実を残す。

    ``old_id`` / ``new_id`` には **stable_key**（``k1:`` 接頭辞つき）が入る。agent 側 ID を
    持つ参照の再係留は行わない（参照が持っているのは agent ID であって stable_key では
    ないため。agent ID が同時に変わっていれば、その分は ``remaps`` 側の
    :func:`record_and_reanchor` が扱う）。区別のため ``reanchored`` に
    ``{"remap_kind": "stable_key"}`` を刻む。

    Returns:
        記録した行数。
    """
    pairs = [
        (_clean(old_key), _clean(new_key), _clean(agent_id))
        for old_key, new_key, agent_id in (key_remaps or [])
    ]
    pairs = [p for p in pairs if p[0] and p[1] and p[0] != p[1]]
    if not pairs:
        return 0

    recorded = 0
    for old_key, new_key, agent_id in pairs:
        session.execute(
            sa_text(
                f"""
                INSERT INTO {TABLE_REMAP} (
                    document_id, run_id, object_kind, old_id, new_id, stable_key, reanchored
                )
                VALUES (
                    :document_id, CAST(:run_id AS uuid), :object_kind,
                    :old_id, :new_id, :stable_key, CAST(:reanchored AS jsonb)
                )
                """
            ),
            {
                "document_id": document_id,
                "run_id": run_id,
                "object_kind": kind,
                "old_id": old_key,
                "new_id": new_key,
                "stable_key": new_key,
                "reanchored": json.dumps(
                    {"remap_kind": "stable_key", "agent_id": agent_id},
                    ensure_ascii=False,
                ),
            },
        )
        recorded += 1
    return recorded


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


# ===========================================================================
# 版をまたぐ同一性（第 25 周 第 6 波・IK-1012 / IK-1020 / IK-1021）
# ===========================================================================
#
# オーナー判断（推奨どおり）: 再解析で名前が変わり supersede された部品に付いた**人の記録**
# （C層 ``component_explanations`` とそれに吊られた支持・引用、``element_identity_links`` の
# 部品側、台帳 ``epistemic_ledger`` の component 行、W層の注釈・対話）は、旧部品と新部品が
# **出典の重なり（evidence_claims / source_chunks から引いた block_id の集合）で 1 対 1 に
# 決まるときだけ**新部品へ付け替え、``element_id_remap`` に ``remap_kind="superseded_component"``
# で記録する。決まらなければ旧行に残し（消さない）、画面は :data:`STRANDED_RECORD_NOTE` で言う。
# 凍結したコースの ⚓ が UUID で旧部品を指していれば、読み時に :func:`successor_component_id`
# がこの記録を辿る（推測で結ばない）。

#: remap の種類（``reanchored.remap_kind``）。stable_key の付け替え（P1-R3）とは別の語。
REMAP_KIND_SUPERSEDED_COMPONENT = "superseded_component"

#: 1 対 1 に「決まる」とみなす重なりの下限（Jaccard）。相互に唯一の最良でも、これ未満で
#: どちらかの側に別の重なりの相手があるなら結ばない（推測で寄せない）。
SUPERSEDED_MATCH_MIN_JACCARD = 0.5

#: 付け替えられず再解析前の部品に残った記録の事実文（教員・学習者の画面で同じ文）。
STRANDED_RECORD_NOTE = (
    "この記録は再解析前の部品に付いています（その部品は解析のやり直しで置き換わり、"
    "出典の重なりから対応する新しい部品が一つに決まりませんでした）。記録は消さずに残しています。"
)
#: 凍結したコースの ⚓ が指す部品が置き換わり、出典の重なりで新しい部品へつないだときの事実文。
REMAPPED_COMPONENT_NOTE = (
    "この部品は解析のやり直しで置き換わりました。出典の重なる新しい部品につないで表示しています。"
)
#: 凍結したコースの ⚓ が指す部品が置き換わり、新しい部品が決まらなかったときの事実文（学習者向け）。
SUPERSEDED_COMPONENT_LEARNER_NOTE = (
    "この部品は解析のやり直しで置き換わりました。対応する新しい部品が一つに決まらないため、"
    "ここでは詳しい文脈を表示できません。"
)

_SUPERSEDED_MAX_HOPS = 8


def match_superseded_components(
    old_blocks: Mapping[str, Any],
    new_blocks: Mapping[str, Any],
    *,
    min_jaccard: float = SUPERSEDED_MATCH_MIN_JACCARD,
) -> list[tuple[str, str]]:
    """旧部品 ⇄ 新部品の 1 対 1 の対応（純関数・決定論）。

    結ぶ条件（両方を満たす組だけ）:

    1. **相互に唯一の最良**: 旧 o にとって重なり（Jaccard）が最大の新 n が 1 つだけで、
       n にとっても最大の旧が o 1 つだけ（同点があれば結ばない）。
    2. **決まっている**: Jaccard が ``min_jaccard`` 以上、または o と n のどちらにも他の重なりの
       相手が無い（重なりのグラフで次数 1 どうし）。

    出典の集合が空の側は結ばない。戻り値は旧 ID の昇順。
    """
    olds = {str(k): {str(b) for b in (v or ()) if str(b)} for k, v in (old_blocks or {}).items()}
    news = {str(k): {str(b) for b in (v or ()) if str(b)} for k, v in (new_blocks or {}).items()}
    olds = {k: v for k, v in olds.items() if k and v}
    news = {k: v for k, v in news.items() if k and v}
    if not olds or not news:
        return []

    scores: dict[tuple[str, str], float] = {}
    for o, ob in olds.items():
        for n, nb in news.items():
            inter = len(ob & nb)
            if inter:
                scores[(o, n)] = inter / len(ob | nb)

    def _best(candidates: list[tuple[str, float]]) -> str | None:
        if not candidates:
            return None
        top = max(score for _, score in candidates)
        winners = [key for key, score in candidates if score == top]
        return winners[0] if len(winners) == 1 else None

    out: list[tuple[str, str]] = []
    for o in sorted(olds):
        o_edges = [(n, sc) for (oo, n), sc in scores.items() if oo == o]
        n = _best(o_edges)
        if n is None:
            continue
        n_edges = [(oo, sc) for (oo, nn), sc in scores.items() if nn == n]
        if _best(n_edges) != o:
            continue
        jac = scores[(o, n)]
        if jac >= min_jaccard or (len(o_edges) == 1 and len(n_edges) == 1):
            out.append((o, n))
    return out


def component_source_blocks(session, component_ids: Sequence[str]) -> dict[str, set[str]]:
    """部品（superseded を含む基表の行）ごとの出典 block_id の集合。

    材料は ``evidence_claims``（主張の ``source_scope.block_id`` と主張のチャンクの ``block_ids``）と
    ``source_chunks``（チャンクの ``block_ids``）。どちらも引けない部品は空集合。
    """
    ids = [i for i in dict.fromkeys(_clean(c) for c in (component_ids or ())) if i]
    if not ids:
        return {}
    from .schema import TABLE_CLAIMS, TABLE_COMPONENTS

    rows = session.execute(
        sa_text(
            f"""
            SELECT id::text, source_chunks, evidence_claims
            FROM {TABLE_COMPONENTS}
            WHERE id = ANY(CAST(:ids AS uuid[]))
            """
        ),
        {"ids": ids},
    ).fetchall()
    chunk_refs: dict[str, list[str]] = {}
    claim_refs: dict[str, list[str]] = {}
    for row in rows:
        cid = str(row[0])
        chunk_refs[cid] = [str(x) for x in _as_list_value(row[1]) if str(x or "").strip()]
        claim_refs[cid] = [str(x) for x in _as_list_value(row[2]) if str(x or "").strip()]

    all_claims = sorted({c for refs in claim_refs.values() for c in refs})
    claim_blocks: dict[str, set[str]] = {}
    claim_chunks: dict[str, str] = {}
    if all_claims:
        for row in session.execute(
            sa_text(
                f"""
                SELECT id::text, source_scope->>'block_id', chunk_id::text
                FROM {TABLE_CLAIMS}
                WHERE id::text = ANY(:ids)
                """
            ),
            {"ids": all_claims},
        ).fetchall():
            blocks = claim_blocks.setdefault(str(row[0]), set())
            if row[1]:
                blocks.add(str(row[1]))
            if row[2]:
                claim_chunks[str(row[0])] = str(row[2])

    all_chunks = sorted(
        {c for refs in chunk_refs.values() for c in refs} | set(claim_chunks.values())
    )
    chunk_blocks: dict[str, set[str]] = {}
    if all_chunks:
        for row in session.execute(
            sa_text("SELECT id::text, block_ids FROM chunks WHERE id::text = ANY(:ids)"),
            {"ids": all_chunks},
        ).fetchall():
            chunk_blocks[str(row[0])] = {str(b) for b in _as_list_value(row[1]) if str(b or "").strip()}

    out: dict[str, set[str]] = {}
    for cid in ids:
        blocks: set[str] = set()
        for claim in claim_refs.get(cid, []):
            blocks |= claim_blocks.get(claim, set())
            chunk = claim_chunks.get(claim)
            if chunk:
                blocks |= chunk_blocks.get(chunk, set())
        for chunk in chunk_refs.get(cid, []):
            blocks |= chunk_blocks.get(chunk, set())
        out[cid] = blocks
    return out


def _as_list_value(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def reanchor_superseded_components(
    session,
    *,
    document_id: str,
    run_id: str | None,
    superseded_ids: Sequence[str],
    inserted_ids: Sequence[str],
) -> dict:
    """supersede された旧部品の人の記録を、出典の重なりで 1 対 1 に決まる新部品へ付け替える。

    呼び出し側のセッションで動き **commit しない**（``persist_components`` の同期と同じ
    トランザクション）。決まった組は人の記録が無くても ``element_id_remap`` に記録する
    （凍結したコースの ⚓ の読み時の付け替えに使う）。

    Returns:
        ``{"pairs": n, "reanchored": {表名: 件数}, "skipped": {表名: 件数}, "unmatched": n}``。
        ``unmatched`` は付け替え先が決まらなかった旧部品の数（記録は旧行に残る）。
    """
    summary: dict[str, Any] = {"pairs": 0, "reanchored": {}, "skipped": {}, "unmatched": 0}
    olds = [i for i in dict.fromkeys(_clean(x) for x in (superseded_ids or ())) if i]
    news = [i for i in dict.fromkeys(_clean(x) for x in (inserted_ids or ())) if i]
    if not olds:
        return summary
    blocks = component_source_blocks(session, olds + news) if news else {}
    pairs = match_superseded_components(
        {o: blocks.get(o, set()) for o in olds},
        {n: blocks.get(n, set()) for n in news},
    )
    summary["unmatched"] = len(olds) - len(pairs)
    if not pairs:
        return summary

    from .schema import TABLE_COMPONENTS

    keys = {
        str(row[0]): str(row[1] or "")
        for row in session.execute(
            sa_text(
                f"SELECT id::text, stable_key FROM {TABLE_COMPONENTS} WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [n for _, n in pairs]},
        ).fetchall()
    }
    for old_id, new_id in pairs:
        counts = _reanchor_component_records(
            session, document_id=document_id, old_id=old_id, new_id=new_id
        )
        session.execute(
            sa_text(
                f"""
                INSERT INTO {TABLE_REMAP} (
                    document_id, run_id, object_kind, old_id, new_id, stable_key, reanchored
                )
                VALUES (
                    :document_id, CAST(:run_id AS uuid), 'component',
                    :old_id, :new_id, :stable_key, CAST(:reanchored AS jsonb)
                )
                """
            ),
            {
                "document_id": document_id,
                "run_id": run_id,
                "old_id": old_id,
                "new_id": new_id,
                "stable_key": keys.get(new_id, ""),
                "reanchored": json.dumps(
                    {"remap_kind": REMAP_KIND_SUPERSEDED_COMPONENT, **counts}, ensure_ascii=False
                ),
            },
        )
        summary["pairs"] += 1
        for table, value in (counts.get("reanchored") or {}).items():
            summary["reanchored"][table] = summary["reanchored"].get(table, 0) + value
        for table, value in (counts.get("skipped") or {}).items():
            summary["skipped"][table] = summary["skipped"].get(table, 0) + value
    return summary


def _reanchor_component_records(session, *, document_id: str, old_id: str, new_id: str) -> dict:
    """旧部品 UUID を指す人の記録を新部品 UUID へ（一意制約に当たる行は書き換えず記録）。"""
    reanchored: dict[str, int] = {}
    skipped: dict[str, int] = {}
    params = {"old_id": old_id, "new_id": new_id, "document_id": document_id}

    # C層の説明（支持・引用は explanation_id に吊られて一緒に動く）。標準の説明は部品に 1 本
    # （uq_component_explanations_standard）なので、新部品に既にあれば旧い方は動かさない。
    skipped_std = _scalar(
        session.execute(
            sa_text(
                """
                SELECT count(*) FROM component_explanations e
                WHERE e.component_id = CAST(:old_id AS uuid) AND e.kind = 'standard'
                  AND EXISTS (SELECT 1 FROM component_explanations s
                              WHERE s.component_id = CAST(:new_id AS uuid) AND s.kind = 'standard')
                """
            ),
            params,
        )
    )
    count = _rowcount(
        session.execute(
            sa_text(
                """
                UPDATE component_explanations e
                SET component_id = CAST(:new_id AS uuid), updated_at = now()
                WHERE e.component_id = CAST(:old_id AS uuid)
                  AND NOT (e.kind = 'standard' AND EXISTS (
                      SELECT 1 FROM component_explanations s
                      WHERE s.component_id = CAST(:new_id AS uuid) AND s.kind = 'standard'))
                """
            ),
            params,
        )
    )
    if count:
        reanchored["component_explanations"] = count
    if skipped_std:
        skipped["component_explanations"] = skipped_std

    # 同一性リンクの部品側（4 列 UNIQUE に当たる行は動かさない）。
    element_type = KIND_ELEMENT_TYPES["component"]
    conflicts = _scalar(
        session.execute(
            sa_text(
                """
                SELECT count(*) FROM element_identity_links l
                WHERE l.instance_element_id = :old_id AND l.instance_element_type = :element_type
                  AND l.instance_document_id::text = :document_id
                  AND EXISTS (SELECT 1 FROM element_identity_links e2
                              WHERE e2.instance_element_id = :new_id
                                AND e2.instance_element_type = :element_type
                                AND e2.instance_document_id::text = :document_id
                                AND e2.shared_part_id = l.shared_part_id)
                """
            ),
            {**params, "element_type": element_type},
        )
    )
    count = _rowcount(
        session.execute(
            sa_text(
                """
                UPDATE element_identity_links l
                SET instance_element_id = :new_id
                WHERE l.instance_element_id = :old_id AND l.instance_element_type = :element_type
                  AND l.instance_document_id::text = :document_id
                  AND NOT EXISTS (SELECT 1 FROM element_identity_links e2
                                  WHERE e2.instance_element_id = :new_id
                                    AND e2.instance_element_type = :element_type
                                    AND e2.instance_document_id::text = :document_id
                                    AND e2.shared_part_id = l.shared_part_id)
                """
            ),
            {**params, "element_type": element_type},
        )
    )
    if count:
        reanchored["element_identity_links"] = count
    if conflicts:
        skipped["element_identity_links"] = conflicts

    # 台帳の component 行（行キー (document_id, target_type, target_id) に当たる行は動かさない）。
    conflicts = _scalar(
        session.execute(
            sa_text(
                """
                SELECT count(*) FROM epistemic_ledger
                WHERE target_id = :old_id AND target_type = 'component'
                  AND document_id::text = :document_id
                  AND EXISTS (SELECT 1 FROM epistemic_ledger e2
                              WHERE e2.target_id = :new_id AND e2.target_type = 'component'
                                AND e2.document_id::text = :document_id)
                """
            ),
            params,
        )
    )
    count = _rowcount(
        session.execute(
            sa_text(
                """
                UPDATE epistemic_ledger
                SET target_id = :new_id, updated_at = now()
                WHERE target_id = :old_id AND target_type = 'component'
                  AND document_id::text = :document_id
                  AND NOT EXISTS (SELECT 1 FROM epistemic_ledger e2
                                  WHERE e2.target_id = :new_id AND e2.target_type = 'component'
                                    AND e2.document_id::text = :document_id)
                """
            ),
            params,
        )
    )
    if count:
        reanchored["epistemic_ledger"] = count
    if conflicts:
        skipped["epistemic_ledger"] = conflicts

    # W層の注釈・対話（UUID で部品を指す行だけ。一意制約なし）。
    for table in ("element_annotations", "deliberation_sessions"):
        count = _rowcount(
            session.execute(
                sa_text(
                    f"""
                    UPDATE {table}
                    SET element_id = :new_id
                    WHERE element_id = :old_id AND element_type = :element_type
                      AND document_id::text = :document_id
                    """
                ),
                {**params, "element_type": element_type},
            )
        )
        if count:
            reanchored[table] = count
    return {"reanchored": reanchored, "skipped": skipped}


def successor_component_id(session, component_id: str) -> str | None:
    """superseded な部品 UUID から、``superseded_component`` の記録を辿った live の後継（読み時）。

    記録が無い・途中で途切れる・live に着かないときは ``None``（推測で結ばない）。
    live の部品を渡したらそのまま返す。
    """
    current = _clean(component_id)
    if not current:
        return None
    from .schema import TABLE_COMPONENTS

    for _ in range(_SUPERSEDED_MAX_HOPS):
        row = session.execute(
            sa_text(
                f"SELECT superseded_at IS NULL FROM {TABLE_COMPONENTS} WHERE id::text = :id"
            ),
            {"id": current},
        ).fetchone()
        if row is None:
            return None
        if bool(row[0]):
            return current
        nxt = session.execute(
            sa_text(
                f"""
                SELECT new_id FROM {TABLE_REMAP}
                WHERE object_kind = 'component' AND old_id = :old_id
                  AND reanchored->>'remap_kind' = :kind
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"old_id": current, "kind": REMAP_KIND_SUPERSEDED_COMPONENT},
        ).fetchone()
        if nxt is None or not nxt[0]:
            return None
        current = str(nxt[0])
    return None


#: 読み時の付け替え（第 7 波・IK-1051）の結果の語彙。
READ_TIME_MATCHED = "matched"
READ_TIME_NOT_FOUND = "not_found"
READ_TIME_AMBIGUOUS = "ambiguous"

#: 凍結したコースの ⚓ が指す部品が置き換わり、出典の重なる新しい部品が 1 つも無かったときの
#: 事実文（学習者向け。「一つに決まらない」と言わない — IK-1051）。
SUPERSEDED_COMPONENT_NOT_FOUND_LEARNER_NOTE = (
    "この部品は解析のやり直しで置き換わりました。出典の重なる新しい部品は見つからないため、"
    "ここでは詳しい文脈を表示できません。"
)

#: 同じ supersede の束を探すときの、新しい行の作成時刻の許容（同じトランザクションの now()）。
_READ_TIME_CREATED_MARGIN = "10 minutes"


def read_time_superseded_successor(session, component_id: str) -> tuple[str | None, str]:
    """記録の無い置き換わり（第 6 波より前の再解析）を、読み時に出典の重なりで後継へつなぐ（IK-1051）。

    新しい凍結・再解析時の付け替え（:func:`reanchor_superseded_components`）と**同じ規則**
    （:func:`match_superseded_components`）を、旧い行が置き換わった run の束に読み時で当てる:

    - 旧の側 = 同じ論文で、同じ run（``superseded_by_run_id``）で置き換わった部品の全部
    - 新の側 = 同じ論文で、その run が新しく入れた部品（``produced_by_run_id`` が同じで、
      置き換わりと同じ時刻帯に作られた行。内容を更新しただけの既存の行は含めない）

    書き込みはしない（``element_id_remap`` に記帳しない — 読み時の導出。推測で結ばない）。

    Returns:
        ``(後継の部品 UUID | None, 結果)``。結果は ``matched`` / ``not_found``（出典の重なる
        新しい部品が 1 つも無い）/ ``ambiguous``（重なる相手はあるが 1 対 1 に決まらない）。
        後継が更に置き換わっていれば記録を辿った live の後継を返す（辿れなければ ``None``）。
    """
    current = _clean(component_id)
    if not current:
        return None, READ_TIME_NOT_FOUND
    from .schema import TABLE_COMPONENTS

    head = session.execute(
        sa_text(
            f"""
            SELECT document_id::text, superseded_by_run_id::text, superseded_at
            FROM {TABLE_COMPONENTS}
            WHERE id::text = :id AND superseded_at IS NOT NULL
            """
        ),
        {"id": current},
    ).fetchone()
    if head is None or not head[1] or head[2] is None:
        return None, READ_TIME_NOT_FOUND
    document_id, run_id, superseded_at = str(head[0]), str(head[1]), head[2]
    olds = [
        str(r[0])
        for r in session.execute(
            sa_text(
                f"""
                SELECT id::text FROM {TABLE_COMPONENTS}
                WHERE document_id::text = :doc AND superseded_by_run_id::text = :run
                """
            ),
            {"doc": document_id, "run": run_id},
        ).fetchall()
    ]
    news = [
        str(r[0])
        for r in session.execute(
            sa_text(
                f"""
                SELECT id::text FROM {TABLE_COMPONENTS}
                WHERE document_id::text = :doc AND produced_by_run_id::text = :run
                  AND created_at >= CAST(:at AS timestamptz) - INTERVAL '{_READ_TIME_CREATED_MARGIN}'
                  AND (superseded_by_run_id IS NULL OR superseded_by_run_id::text <> :run)
                """
            ),
            {"doc": document_id, "run": run_id, "at": superseded_at},
        ).fetchall()
    ]
    if current not in olds:
        olds.append(current)
    if not news:
        return None, READ_TIME_NOT_FOUND
    blocks = component_source_blocks(session, olds + news)
    old_blocks = {o: blocks.get(o, set()) for o in olds}
    new_blocks = {n: blocks.get(n, set()) for n in news}
    mine = old_blocks.get(current) or set()
    if not mine or not any(mine & nb for nb in new_blocks.values()):
        return None, READ_TIME_NOT_FOUND
    pairs = dict(match_superseded_components(old_blocks, new_blocks))
    successor = pairs.get(current)
    if not successor:
        return None, READ_TIME_AMBIGUOUS
    live = successor_component_id(session, successor)
    if not live:
        return None, READ_TIME_AMBIGUOUS
    return live, READ_TIME_MATCHED


#: 再解析前の部品に付いたままの説明を開いたときの事実文（読み取り専用・IK-1012 の残り）。
STRANDED_EXPLANATIONS_READONLY_NOTE = (
    "再解析前の部品に付いたままの説明です（読み取り専用）。新しい部品への付け替えは、"
    "出典の重なりで 1 対 1 に決まらなかったため行っていません。記録は消さずに残しています。"
)
#: 作成者だけが新しい部品に書き直せる案内（作成者の行に付ける）。
STRANDED_EXPLANATION_REWRITE_OWN = (
    "この説明はあなたが書いたものです。新しい部品の説明として使うときは、新しい部品の"
    "「説明バージョン」の［追加］から書き直してください（元の記録はこのまま残ります）。"
)
#: 作成者以外の行に付ける案内。
STRANDED_EXPLANATION_REWRITE_OTHERS = (
    "新しい部品に書き直せるのは、この説明の作成者だけです。"
)
#: 標準の説明（著者なし）の行に付ける案内。
STRANDED_EXPLANATION_STANDARD = (
    "標準の説明は、新しい部品の要約から新しい部品の標準の説明として作り直されます。"
)
_STRANDED_RECORDS_MAX = 20


def stranded_component_explanation_records(session, document_id: str) -> list[dict]:
    """再解析前の部品に付いたまま後継の決まらない説明の中身（読み取り専用の表示用・上限つき）。

    返すのは表示の材料だけ（部品名・種類・題名・本文・著者の user_id と表示名の材料・査読の状態・
    作成日）。部品・説明の UUID は返さない。
    """
    from .schema import TABLE_COMPONENTS

    doc = _clean(document_id)
    if not doc:
        return []
    rows = session.execute(
        sa_text(
            f"""
            SELECT c.name, e.kind, e.title, e.body, e.author_id::text,
                   COALESCE(NULLIF(BTRIM(u.profile_name), ''), ''), e.review_status, e.created_at
            FROM component_explanations e
            JOIN {TABLE_COMPONENTS} c ON c.id = e.component_id
            LEFT JOIN users u ON u.id = e.author_id
            WHERE c.document_id::text = :doc
              AND c.superseded_at IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM {TABLE_REMAP} r
                  WHERE r.object_kind = 'component' AND r.old_id = c.id::text
                    AND r.reanchored->>'remap_kind' = :kind
              )
            ORDER BY e.created_at ASC
            LIMIT {_STRANDED_RECORDS_MAX}
            """
        ),
        {"doc": doc, "kind": REMAP_KIND_SUPERSEDED_COMPONENT},
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        created = r[7]
        out.append({
            "component_name": str(r[0] or ""),
            "kind": str(r[1] or "personal"),
            "title": str(r[2] or ""),
            "body": str(r[3] or ""),
            "author_id": str(r[4] or ""),
            "author_profile_name": str(r[5] or ""),
            "review_status": str(r[6] or ""),
            "created_on": (created.isoformat() if hasattr(created, "isoformat") else str(created or ""))[:10],
        })
    return out


def superseded_component_by_agent_id(
    session, *, agent_id: str, document_ids: Sequence[str], frozen_label: str
) -> tuple[str, str] | None:
    """旧い凍結の ⚓（agent ID + 凍結時の部品名）から、凍結時に指していた部品を引く（読み時）。

    agent ID は解析 run ごとの連番なので、再解析後は同じ ID が別の部品を指し得る（IK-1021）。
    凍結時の名前と一致する行（live を優先・無ければ superseded）だけを採り、
    ``(component_uuid, "live" | "superseded")`` を返す。名前が一致しない・複数の論文に当たる
    ときは ``None``（従来の解決に任せる）。
    """
    agent = _clean(agent_id)
    label = _clean(frozen_label)
    docs = [str(d) for d in (document_ids or ()) if str(d or "").strip()]
    if not agent or not label or not docs:
        return None
    from .schema import TABLE_COMPONENTS

    rows = session.execute(
        sa_text(
            f"""
            SELECT id::text, document_id::text, superseded_at IS NULL AS live
            FROM {TABLE_COMPONENTS}
            WHERE document_id = ANY(CAST(:docs AS uuid[]))
              AND name = :label
              AND (agent_component_id = :agent
                   OR (jsonb_typeof(source_scope->'legacy_ids') = 'array'
                       AND source_scope->'legacy_ids' ? :agent))
            ORDER BY (superseded_at IS NULL) DESC, superseded_at DESC NULLS FIRST
            """
        ),
        {"docs": docs, "label": label, "agent": agent},
    ).fetchall()
    if not rows:
        return None
    if len({str(r[1]) for r in rows}) > 1:
        return None
    top = rows[0]
    return str(top[0]), ("live" if bool(top[2]) else "superseded")


#: 教員の説明一覧に添える事実文（付け替えられずに再解析前の部品に残った説明の列挙）。
STRANDED_EXPLANATIONS_FACT_HEAD = "この論文には、再解析前の部品に付いたままの説明があります（"
STRANDED_EXPLANATIONS_FACT_TAIL = "）。" + STRANDED_RECORD_NOTE
_STRANDED_LIST_MAX = 5


def stranded_component_explanations(session, document_id: str) -> list[dict]:
    """同じ論文の superseded な部品に付いたまま、後継の決まらない説明（読み時・件数上限つき）。

    返すのは ``{component_name, title}`` だけ（UUID を返さない）。後継が決まった（記録がある）
    部品の説明は付け替え済みなのでここには出ない。
    """
    from .schema import TABLE_COMPONENTS

    doc = _clean(document_id)
    if not doc:
        return []
    rows = session.execute(
        sa_text(
            f"""
            SELECT c.name, e.title, e.kind
            FROM component_explanations e
            JOIN {TABLE_COMPONENTS} c ON c.id = e.component_id
            WHERE c.document_id::text = :doc
              AND c.superseded_at IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM {TABLE_REMAP} r
                  WHERE r.object_kind = 'component' AND r.old_id = c.id::text
                    AND r.reanchored->>'remap_kind' = :kind
              )
            ORDER BY e.created_at ASC
            LIMIT {_STRANDED_LIST_MAX + 1}
            """
        ),
        {"doc": doc, "kind": REMAP_KIND_SUPERSEDED_COMPONENT},
    ).fetchall()
    return [
        {"component_name": str(r[0] or ""), "title": str(r[1] or ""), "kind": str(r[2] or "")}
        for r in rows
    ]


def stranded_explanations_fact(items: Sequence[Mapping[str, Any]]) -> str:
    """:func:`stranded_component_explanations` の列挙を 1 文にする（無ければ空文字）。"""
    if not items:
        return ""
    names: list[str] = []
    for item in list(items)[:_STRANDED_LIST_MAX]:
        part = _clean(item.get("component_name")) or "名前の無い部品"
        title = _clean(item.get("title"))
        if _clean(item.get("kind")) == "standard":
            names.append(f"「{part}」の標準の説明")
        else:
            names.append(f"「{part}」への説明「{title or '無題'}」")
    if len(items) > _STRANDED_LIST_MAX:
        names.append("ほか")
    return STRANDED_EXPLANATIONS_FACT_HEAD + "・".join(names) + STRANDED_EXPLANATIONS_FACT_TAIL
