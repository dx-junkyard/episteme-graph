"""LedgerBuilder — A層成果物からの決定論的バックフィル（非LLM, D1-2）。

教員が 1 文字も入力しなくても台帳が既定値で埋まり、「検証スコープの空欄」が
見え始める状態を作る。A層成果物（theory_claims / theory_component_graphs）を
**読むだけ**で、A層のコード・テーブルは変更しない。

保守的マッピングの原則:
  - claim: 既定 `unknown`。evidence_text を持つ `source_backed` claim は
    `indirectly_supported`。「原文に書いてある」≠「検証されている」なので
    `directly_verified` には **しない**（人間の記帳専用語彙）。
  - equation / component: source_backing_status から保守的にマップ
    （`source_backed` → `indirectly_supported`、`inferred` ほか → `unknown`）。
  - 既に人間が触った行（verification_status が unknown 以外、または
    verification_scopes が非空）の verification_status は上書きしない。
  - consensus_behavioral: グラフ上でその対象に依存する参照数（行動上の合意の簡易前身）。
  - consensus_explicit: C層 component_explanation_endorsement_summary の参照値転記。

再実行は冪等（UPSERT）。ドキュメント解析パイプライン完了時のフックと
backend/scripts/backfill_epistemic_ledger.py（既存コーパス向け）から呼ばれる。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.schema import VerificationStatus
from core.postgres import get_session

logger = logging.getLogger(__name__)

# builder が書いてよい verification_status（人間専用語彙の混入をコードレベルで禁止）
_BUILDER_ALLOWED_STATUSES = (
    VerificationStatus.UNKNOWN.value,
    VerificationStatus.INDIRECTLY_SUPPORTED.value,
    VerificationStatus.UNTESTED.value,
)


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return []


def _graph_payload(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _node_id(node: dict) -> str:
    return str(node.get("component_id") or node.get("id") or "").strip()


def _edge_endpoints(edge: dict) -> tuple[str, str]:
    source = str(edge.get("source_component_id") or edge.get("source") or "").strip()
    target = str(edge.get("target_component_id") or edge.get("target") or "").strip()
    return source, target


def _edge_evidence(edge: dict) -> dict:
    evidence = edge.get("evidence")
    return evidence if isinstance(evidence, dict) else edge


def _claim_status(support_status: str, evidence_text: str) -> str:
    """claim の保守的な初期 verification_status。"""
    if str(support_status or "").strip() == "source_backed" and str(evidence_text or "").strip():
        return VerificationStatus.INDIRECTLY_SUPPORTED.value
    return VerificationStatus.UNKNOWN.value


def _backing_status(source_backing_status: str) -> str:
    """equation / component の保守的な初期 verification_status。

    inferred / review_required / partially / 未分類はすべて unknown に倒す。
    """
    if str(source_backing_status or "").strip() == "source_backed":
        return VerificationStatus.INDIRECTLY_SUPPORTED.value
    return VerificationStatus.UNKNOWN.value


def _collect_targets(
    session,
    document_id: str,
) -> tuple[list[dict], str]:
    """A層成果物から台帳対象（claim / equation / component）を列挙する。

    Returns:
        (targets, course_id)。target は
        {target_id, target_type, verification_status, consensus_behavioral,
         consensus_explicit} の dict。
    """
    targets: dict[tuple[str, str], dict] = {}
    course_id = ""

    # --- claims -----------------------------------------------------------
    claim_rows = session.execute(
        sa_text("""
            SELECT id, claim_type, support_status, evidence_text, equation
            FROM theory_claims
            WHERE document_id = :doc
        """),
        {"doc": document_id},
    ).fetchall()

    equation_backing: dict[str, str] = {}
    for row in claim_rows:
        claim_id = str(row[0])
        status = _claim_status(str(row[2] or ""), str(row[3] or ""))
        targets[(claim_id, "claim")] = {
            "target_id": claim_id,
            "target_type": "claim",
            "verification_status": status,
            "consensus_behavioral": 0,
            "consensus_explicit": {},
        }
        equation = row[4]
        if isinstance(equation, str):
            try:
                equation = json.loads(equation)
            except Exception:
                equation = {}
        if isinstance(equation, dict):
            eq_id = str(equation.get("equation_id") or "").strip()
            if eq_id:
                # 同じ式に複数 claim が触れる場合は最も強い（保守的順序の）方を残す
                prev = equation_backing.get(eq_id, VerificationStatus.UNKNOWN.value)
                if status == VerificationStatus.INDIRECTLY_SUPPORTED.value:
                    equation_backing[eq_id] = status
                else:
                    equation_backing.setdefault(eq_id, prev)

    # --- component graph ---------------------------------------------------
    graph_rows = session.execute(
        sa_text("""
            SELECT course_id, graph_json
            FROM theory_component_graphs
            WHERE document_id = :doc
        """),
        {"doc": document_id},
    ).fetchall()

    reference_counts: dict[tuple[str, str], int] = {}
    direct_dependents: dict[str, set[str]] = {}

    for row in graph_rows:
        if not course_id:
            course_id = str(row[0] or "")
        payload = _graph_payload(row[1])
        nodes = [n for n in _as_list(payload.get("nodes")) if isinstance(n, dict)]
        edges = [e for e in _as_list(payload.get("edges")) if isinstance(e, dict)]

        for node in nodes:
            nid = _node_id(node)
            if not nid:
                continue
            layer = str(node.get("graph_layer") or "main")
            backing = str(node.get("source_backing_status") or "")
            # debug 層（fallback / inferred）のノードは台帳対象にしない
            # （弱い backing を台帳の既定値に混ぜない）
            if layer == "debug":
                continue
            targets.setdefault(
                (nid, "component"),
                {
                    "target_id": nid,
                    "target_type": "component",
                    "verification_status": _backing_status(backing),
                    "consensus_behavioral": 0,
                    "consensus_explicit": {},
                },
            )
            # ノードが参照する claim / equation の依存頻度を数える
            for key in ("linked_claim_ids", "input_claim_ids", "output_claim_ids", "required_claim_ids"):
                for cid in _as_list(node.get(key)):
                    ref = (str(cid), "claim")
                    reference_counts[ref] = reference_counts.get(ref, 0) + 1
            for key in (
                "linked_equation_ids", "input_equation_ids", "output_equation_ids",
                "definition_equation_ids", "constraint_equation_ids",
            ):
                for eid in _as_list(node.get(key)):
                    eq_id = str(eid).strip()
                    if not eq_id:
                        continue
                    ref = (eq_id, "equation")
                    reference_counts[ref] = reference_counts.get(ref, 0) + 1
                    equation_backing.setdefault(eq_id, VerificationStatus.UNKNOWN.value)

        for edge in edges:
            source, target = _edge_endpoints(edge)
            if source and target:
                direct_dependents.setdefault(source, set()).add(target)
            evidence = _edge_evidence(edge)
            for cid in _as_list(evidence.get("evidence_claim_ids")) + _as_list(evidence.get("evidence_claims")):
                ref = (str(cid), "claim")
                reference_counts[ref] = reference_counts.get(ref, 0) + 1
            for eid in _as_list(evidence.get("evidence_equation_ids")):
                eq_id = str(eid).strip()
                if not eq_id:
                    continue
                ref = (eq_id, "equation")
                reference_counts[ref] = reference_counts.get(ref, 0) + 1
                equation_backing.setdefault(eq_id, VerificationStatus.UNKNOWN.value)

    # --- equations（claims の equation JSONB + グラフ参照から発見したもの） ---
    for eq_id, status in equation_backing.items():
        targets.setdefault(
            (eq_id, "equation"),
            {
                "target_id": eq_id,
                "target_type": "equation",
                "verification_status": status,
                "consensus_behavioral": 0,
                "consensus_explicit": {},
            },
        )

    # --- consensus_behavioral（行動上の合意 = 依存頻度） ---------------------
    for (tid, ttype), count in reference_counts.items():
        entry = targets.get((tid, ttype))
        if entry is not None:
            entry["consensus_behavioral"] = entry["consensus_behavioral"] + count
    for nid, dependents in direct_dependents.items():
        entry = targets.get((nid, "component"))
        if entry is not None:
            entry["consensus_behavioral"] = entry["consensus_behavioral"] + len(dependents)

    # --- consensus_explicit（C層承認集計の参照値転記, component のみ） --------
    component_ids = [tid for (tid, ttype) in targets if ttype == "component"]
    if component_ids:
        try:
            rows = session.execute(
                sa_text("""
                    SELECT ce.component_id::text,
                           COALESCE(SUM(s.endorser_count), 0),
                           COALESCE(SUM(s.strong_count), 0),
                           COALESCE(SUM(s.provisional_count), 0)
                    FROM component_explanations ce
                    JOIN component_explanation_endorsement_summary s
                      ON s.explanation_id = ce.id
                    WHERE ce.component_id::text = ANY(:ids)
                    GROUP BY ce.component_id
                """),
                {"ids": component_ids},
            ).fetchall()
            for row in rows:
                entry = targets.get((str(row[0]), "component"))
                if entry is not None:
                    entry["consensus_explicit"] = {
                        "endorser_count": int(row[1] or 0),
                        "strong_count": int(row[2] or 0),
                        "provisional_count": int(row[3] or 0),
                    }
        except Exception:
            # graph node の component_id が theory_components 由来でない場合は転記なし
            logger.debug("consensus_explicit transfer skipped for document=%s", document_id, exc_info=True)

    return list(targets.values()), course_id


def _upsert_targets(session, targets: list[dict], document_id: str, course_id: str) -> int:
    """台帳行の冪等 UPSERT。

    人間が触った行（verification_status ≠ unknown、または scopes 非空）の
    verification_status は上書きしない。directly_verified は builder から
    絶対に書かれない（コードレベルのガード）。
    """
    written = 0
    for target in targets:
        status = str(target.get("verification_status") or VerificationStatus.UNKNOWN.value)
        if status not in _BUILDER_ALLOWED_STATUSES:
            # 人間専用語彙（directly_verified / refuted）が紛れ込んだら unknown に倒す
            logger.warning(
                "ledger_builder attempted non-builder status %r for %s/%s; forcing unknown",
                status, target.get("target_type"), target.get("target_id"),
            )
            status = VerificationStatus.UNKNOWN.value
        session.execute(
            sa_text("""
                INSERT INTO epistemic_ledger
                    (target_id, target_type, document_id, course_id,
                     verification_status, consensus_explicit, consensus_behavioral)
                VALUES
                    (:tid, :ttype, :doc, :course, :status,
                     CAST(:explicit AS jsonb), :behavioral)
                ON CONFLICT (target_id, target_type) DO UPDATE SET
                    document_id = CASE
                        WHEN epistemic_ledger.document_id = '' THEN EXCLUDED.document_id
                        ELSE epistemic_ledger.document_id END,
                    course_id = CASE
                        WHEN epistemic_ledger.course_id = '' THEN EXCLUDED.course_id
                        ELSE epistemic_ledger.course_id END,
                    consensus_explicit = EXCLUDED.consensus_explicit,
                    consensus_behavioral = EXCLUDED.consensus_behavioral,
                    verification_status = CASE
                        WHEN epistemic_ledger.verification_status = 'unknown'
                             AND epistemic_ledger.verification_scopes = '[]'::jsonb
                        THEN EXCLUDED.verification_status
                        ELSE epistemic_ledger.verification_status END,
                    updated_at = now()
            """),
            {
                "tid": target["target_id"],
                "ttype": target["target_type"],
                "doc": document_id,
                "course": course_id or "",
                "status": status,
                "explicit": json.dumps(target.get("consensus_explicit") or {}, ensure_ascii=False),
                "behavioral": int(target.get("consensus_behavioral") or 0),
            },
        )
        written += 1
    return written


def backfill_document_ledger(document_id: str, course_id: str = "") -> dict:
    """1 ドキュメント分の台帳バックフィル（冪等）。

    A層パイプライン完了後のフック、および backfill スクリプトから呼ばれる。
    """
    document_id = str(document_id or "").strip()
    if not document_id:
        return {"document_id": "", "written": 0}
    session = get_session()
    try:
        targets, derived_course = _collect_targets(session, document_id)
        written = _upsert_targets(session, targets, document_id, course_id or derived_course)
        session.commit()
        logger.info(
            "epistemic ledger backfill: document=%s targets=%d", document_id, written
        )
        return {"document_id": document_id, "written": written}
    except Exception:
        session.rollback()
        logger.warning("epistemic ledger backfill failed for document=%s", document_id, exc_info=True)
        return {"document_id": document_id, "written": 0, "failed": True}
    finally:
        session.close()


def backfill_all() -> dict:
    """既存コーパス全体の台帳バックフィル（再実行冪等）。"""
    session = get_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT DISTINCT document_id FROM theory_claims WHERE document_id <> ''
                UNION
                SELECT DISTINCT document_id FROM theory_component_graphs WHERE document_id <> ''
            """)
        ).fetchall()
        document_ids = [str(r[0]) for r in rows]
    finally:
        session.close()

    results = []
    for document_id in document_ids:
        results.append(backfill_document_ledger(document_id))
    total = sum(int(r.get("written") or 0) for r in results)
    logger.info("epistemic ledger backfill_all: documents=%d targets=%d", len(document_ids), total)
    return {"documents": len(document_ids), "written": total, "results": results}
