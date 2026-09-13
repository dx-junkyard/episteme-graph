"""取り込みの実行（knowledge_transfer_design.md §4.2 の「書き込み」欄）。

呼び出し側のセッションで動き、**commit しない**（route が 1 トランザクションで束ねる）。
書き込みは 4 つだけ:

1. ``document_analysis_runs`` に取り込み run を 1 行（``current_stage='import'``）。
   **採用 run にしない**（採用は教員の既存操作）。
2. 束の項目 → :func:`core.knowledge_objects.sync.sync_live_rows`（Phase 1 と同じ
   supersede 同期。**DELETE を書かない** = KT5）。
3. ``theory_component_graphs`` を新 UUID 写像で書き換えて upsert。
4. 監査（``record_knowledge_audit``。``AUDIT_ENTITY_IMPORT`` の記帳は route 側）。

FastAPI / LLM は import しない（KT3）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.knowledge_objects import remap as ko_remap
from core.knowledge_objects.schema import (
    TABLE_CLAIMS,
    TABLE_COMPONENTS,
    TABLE_DERIVATION_STEPS,
    TABLE_EQUATIONS,
    TABLE_EVIDENCE,
    VIEW_CLAIMS_LIVE,
    VIEW_COMPONENTS_LIVE,
)
from core.knowledge_objects.sync import sync_live_rows

from . import rows as import_rows
from .bundle import ImportBundle

logger = logging.getLogger(__name__)

#: 取り込み run の ``current_stage``（パイプラインのステージ名ではない印）。
IMPORT_STAGE = "import"

#: 同期の対象（表名・agent ID 列・内容列）。列集合は Phase 1 の persistence と揃える。
_CLAIM_CONTENT_COLUMNS = (
    "chunk_id", "source_scope", "claim_type", "claim_type_text", "text",
    "normalized_text", "concepts", "equation", "support_status", "evidence_text",
    "origin", "claim_tier",
)
_CLAIM_COLUMN_CASTS = {"chunk_id": "uuid"}
_CLAIM_PRESERVED_COLUMNS = ("review_status", "created_by")

_COMPONENT_CONTENT_COLUMNS = (
    "course_id", "name", "component_type", "component_type_text", "summary",
    "source_chunks", "inputs", "outputs", "preconditions", "constraints",
    "invalid_conditions", "dependencies", "blackbox_policy", "validation_warnings",
    "source_scope", "evidence_claims", "maturity_level", "maturity_source",
    "cautions", "connectors", "internal_flow", "duplicate_candidates", "operation",
    "teaching_takeaway", "teaching_granularity", "prerequisite_concepts",
    "assumptions", "approximations", "linked_claim_ids", "linked_equation_ids",
    "linked_evidence_ids", "linked_derivation_ids", "agent_payload",
)
_COMPONENT_PRESERVED_COLUMNS = ("review_status", "status", "teacher_notes", "created_by")
_COMPONENT_PROTECTED_WHEN_TOUCHED = ("name", "summary", "maturity_source")

_EQUATION_CONTENT_COLUMNS = (
    "label", "latex", "plain_text", "raw_text", "block_id", "section_id", "page",
    "equation_type", "semantic_status", "defined_symbols", "used_symbols",
    "input_equation_ids", "output_equation_ids", "linked_claim_ids",
    "source_evidence_ids", "needs_math_review", "agent_payload",
)

_EVIDENCE_CONTENT_COLUMNS = (
    "block_id", "section_id", "page", "span_start", "span_end", "evidence_text",
    "evidence_role", "parent_evidence_id", "public_export_policy", "agent_payload",
)

_DERIVATION_CONTENT_COLUMNS = (
    "agent_derivation_id", "step_index", "operation", "operation_subtype", "chain_type",
    "input_equation_ids", "output_equation_ids", "input_claim_ids", "output_claim_ids",
    "required_claim_ids", "assumption_ids", "source_evidence_ids", "teaching_takeaway",
    "agent_payload",
)

_KNOWLEDGE_PRESERVED_COLUMNS = ("review_status",)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _component_human_touched(row: Any) -> bool:
    """live component 行を人間が触ったか（persistence の同名述語と同じ判定）。"""
    get = row.get if hasattr(row, "get") else (lambda _k, _d=None: None)
    status = _text(get("status"))
    review_status = _text(get("review_status"))
    teacher_notes = _text(get("teacher_notes"))
    maturity_source = _text(get("maturity_source"))
    return bool(
        (status and status != "candidate")
        or (review_status and review_status != import_rows.IMPORT_REVIEW_STATUS)
        or teacher_notes
        or maturity_source == "teacher_reviewed"
    )


# ---------------------------------------------------------------------------
# 取り込み先の状態（dry-run の事実）
# ---------------------------------------------------------------------------


def live_row_counts(session, document_id: str) -> dict:
    """取り込み先に既にある live 行の件数（claim / component）。

    件数は教員向けの運用情報で、学習者には出ない（KT7 は学習者向けの条項）。
    """
    counts: dict[str, int] = {}
    for key, view in (("claims", VIEW_CLAIMS_LIVE), ("components", VIEW_COMPONENTS_LIVE)):
        row = session.execute(
            sa_text(f"SELECT count(*) FROM {view} WHERE document_id = CAST(:doc AS uuid)"),
            {"doc": document_id},
        ).fetchone()
        counts[key] = int(row[0]) if row and row[0] is not None else 0
    return counts


def has_live_rows(session, document_id: str) -> bool:
    counts = live_row_counts(session, document_id)
    return any(int(value) > 0 for value in counts.values())


def preserve_adopted_run(session, document_id: str) -> str:
    """取り込み run が採用 run を横取りしないように、現在の採用先を固定する。

    ``resolve_artifact_runs(policy="adopted")`` は
    ``documents.active_analysis_run_id`` が NULL のとき「最新の completed run」へ
    後方互換 fallback する。取り込み run は ``status='completed'`` で入るので、
    何もしないと**解析していないのに採用 run になり**、成果物の読み手（export /
    論文層 / グラフレビュー）が空の artifact を見ることになる。

    そこで取り込み run を作る**前に**、採用先が未設定なら「いまの採用先（最新の
    completed run）」を明示的に書き込んで固定する。新しい run を採用するわけでは
    ないので、教員の採用操作の意味は変えない（設計書 §4.2「採用 run にはしない」の
    実装上の担保）。解析 run が 1 つも無い document では何もしない。

    Returns:
        固定した run_id（何もしなかったときは ``""``）。
    """
    row = session.execute(
        sa_text(
            """
            UPDATE documents d
            SET active_analysis_run_id = (
                    SELECT r.id FROM document_analysis_runs r
                    WHERE r.document_id = d.id AND r.status = 'completed'
                    ORDER BY r.completed_at DESC NULLS LAST, r.created_at DESC, r.id DESC
                    LIMIT 1
                ),
                updated_at = now()
            WHERE d.id = CAST(:doc AS uuid)
              AND d.active_analysis_run_id IS NULL
              AND EXISTS (
                    SELECT 1 FROM document_analysis_runs r2
                    WHERE r2.document_id = d.id AND r2.status = 'completed'
                )
            RETURNING active_analysis_run_id::text
            """
        ),
        {"doc": document_id},
    ).fetchone()
    return str(row[0]) if row and row[0] else ""


def create_import_run(session, *, document_id: str, options: dict) -> str:
    """取り込み run を 1 行作る（``status='completed'`` / ``current_stage='import'``）。"""
    row = session.execute(
        sa_text(
            """
            INSERT INTO document_analysis_runs (
                document_id, status, current_stage, error_message,
                stage_outputs, options, started_at, completed_at
            )
            VALUES (
                CAST(:document_id AS uuid), 'completed', :stage, '',
                '{}'::jsonb, CAST(:options AS jsonb), now(), now()
            )
            RETURNING id::text
            """
        ),
        {
            "document_id": document_id,
            "stage": IMPORT_STAGE,
            "options": _json_dumps({IMPORT_STAGE: options}),
        },
    ).fetchone()
    return str(row[0]) if row else ""


def record_run_outputs(session, *, run_id: str, stats: dict) -> None:
    session.execute(
        sa_text(
            """
            UPDATE document_analysis_runs
            SET stage_outputs = CAST(:outputs AS jsonb), updated_at = now()
            WHERE id = CAST(:run_id AS uuid)
            """
        ),
        {"run_id": run_id, "outputs": _json_dumps({IMPORT_STAGE: stats})},
    )


# ---------------------------------------------------------------------------
# グラフの書き換え
# ---------------------------------------------------------------------------


def rewrite_graph(
    component_graph: dict,
    *,
    document_id: str,
    component_id_map: dict[str, str],
    claim_id_map: dict[str, str],
) -> dict:
    """束の component_graph を取り込み先の UUID 写像で書き換える（純関数）。

    写せない ID は**そのまま残す**（推測で結び直さない・情報を落とさない）。
    """
    nodes_out: list[dict] = []
    seen: set[str] = set()
    for node in (component_graph.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        agent_id = _text(node.get("component_id") or node.get("node_id") or node.get("id"))
        if not agent_id:
            continue
        db_id = component_id_map.get(agent_id, agent_id)
        if db_id in seen:
            continue
        seen.add(db_id)
        stored = dict(node)
        stored["id"] = db_id
        stored["component_id"] = db_id
        stored["agent_component_id"] = agent_id
        stored.setdefault("type", "component")
        for key in ("linked_claim_ids", "evidence_claim_ids", "evidence_claims"):
            if isinstance(stored.get(key), list):
                stored[key] = [claim_id_map.get(_text(v), _text(v)) for v in stored[key] if _text(v)]
        nodes_out.append(stored)

    edges_out: list[dict] = []
    for edge in (component_graph.get("edges") or []):
        if not isinstance(edge, dict):
            continue
        stored = dict(edge)
        source = _text(edge.get("source_component_id"))
        target = _text(edge.get("target_component_id"))
        stored["source_component_id"] = component_id_map.get(source, source)
        stored["target_component_id"] = component_id_map.get(target, target)
        evidence = edge.get("evidence")
        if isinstance(evidence, dict):
            evidence = dict(evidence)
            claims = evidence.get("evidence_claims")
            if isinstance(claims, list):
                evidence["evidence_claims"] = [
                    claim_id_map.get(_text(v), _text(v)) for v in claims if _text(v)
                ]
            stored["evidence"] = evidence
        edges_out.append(stored)

    return {
        "graph_id": f"graph_{document_id}",
        "document_id": document_id,
        "graph_schema_version": _text(component_graph.get("graph_schema_version")) or "0.1.0",
        "scope": {"level": "paper"},
        "nodes": nodes_out,
        "edges": edges_out,
        "dsl": component_graph.get("dsl") if isinstance(component_graph.get("dsl"), dict) else {"nodes": [], "edges": []},
        "validation_results": [],
    }


def _upsert_graph(session, *, document_id: str, run_id: str, graph: dict) -> None:
    """``persist_component_graph`` と同じ course_id 規約（取り込みはコース非結合 = NULL）。"""
    session.execute(
        sa_text(
            """
            INSERT INTO theory_component_graphs (
                course_id, document_id, scope, graph_json, validation_results,
                produced_by_run_id
            )
            VALUES (
                NULL, CAST(:document_id AS uuid), CAST(:scope AS jsonb),
                CAST(:graph_json AS jsonb), '[]'::jsonb, CAST(:run_id AS uuid)
            )
            ON CONFLICT (document_id) DO UPDATE SET
                course_id = EXCLUDED.course_id,
                scope = EXCLUDED.scope,
                graph_json = EXCLUDED.graph_json,
                validation_results = EXCLUDED.validation_results,
                produced_by_run_id = EXCLUDED.produced_by_run_id,
                updated_at = now()
            """
        ),
        {
            "document_id": document_id,
            "run_id": run_id,
            "scope": _json_dumps({"level": "paper"}),
            "graph_json": _json_dumps(graph),
        },
    )


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


def _sync(session, *, table, document_id, run_id, items, content_columns, agent_id_column, **kwargs):
    return sync_live_rows(
        session,
        table=table,
        document_id=document_id,
        run_id=run_id,
        incoming=import_rows.dedupe(items),
        content_columns=content_columns,
        agent_id_column=agent_id_column,
        **kwargs,
    )


def plan_facts(bundle: ImportBundle, *, has_live: bool, replace: bool) -> list[str]:
    """dry-run で教員に見せる事実文（数値スコアは書かない・煽らない）。"""
    facts = [
        "この束の主張・部品・式・根拠・導出の各項目を、この教材の知識として取り込みます。",
        "取り込んだ項目は未確認（教員の確認待ち）の候補として着地します。"
        "束の中の承認状態は事実として残しますが、このインスタンスの承認にはしません。",
        "同一性キー（stable_key）は取り込み先の教材で計算し直します。"
        "束の中のキーは出所として残します。",
    ]
    if not bundle.evidence:
        facts.append(
            "束に根拠（evidence_snippets）が入っていないため、主張の出典ブロックが解決できません。"
        )
    if not bundle.component_graph.get("nodes"):
        facts.append("束に部品のグラフが入っていないため、グラフは更新されません。")
    if has_live:
        if replace:
            facts.append(
                "この教材には既に解析結果があります。再解析と同じ規則で置き換えます"
                "（教員が確定した状態は保たれます）。"
            )
        else:
            facts.append(
                "この教材には既に解析結果があります。置き換えを明示しない限り取り込みは行いません。"
            )
    facts.append(
        "束には教材本文・図画像・コースは含まれないため、取り込んだ主張は出典の本文には着地しません。"
    )
    return facts


def apply_import(
    session,
    *,
    bundle: ImportBundle,
    document_id: str,
    run_id: str,
) -> dict:
    """束を live 行へ同期する（commit しない）。

    Returns:
        ``{"claims": {...}, "components": {...}, ..., "graph": {...}}`` の統計。
    """
    source = bundle.source()
    stats: dict[str, Any] = {}

    claim_items = import_rows.claim_rows(
        document_id, bundle.claims, evidence=bundle.evidence,
        equations=bundle.equations, source=source,
    )
    claim_sync = _sync(
        session, table=TABLE_CLAIMS, document_id=document_id, run_id=run_id,
        items=claim_items, content_columns=_CLAIM_CONTENT_COLUMNS,
        agent_id_column="agent_claim_id",
        preserved_columns=_CLAIM_PRESERVED_COLUMNS,
        column_casts=_CLAIM_COLUMN_CASTS,
    )
    stats["claims"] = dict(claim_sync.stats)

    component_items = import_rows.component_rows(
        document_id, bundle.components, claims=bundle.claims, evidence=bundle.evidence,
        claim_id_map=claim_sync.id_map, source=source,
    )
    component_sync = _sync(
        session, table=TABLE_COMPONENTS, document_id=document_id, run_id=run_id,
        items=component_items, content_columns=_COMPONENT_CONTENT_COLUMNS,
        agent_id_column="agent_component_id",
        preserved_columns=_COMPONENT_PRESERVED_COLUMNS,
        human_touched=_component_human_touched,
        protected_when_touched=_COMPONENT_PROTECTED_WHEN_TOUCHED,
        touch_columns=("maturity_source",),
    )
    stats["components"] = dict(component_sync.stats)

    equation_sync = _sync(
        session, table=TABLE_EQUATIONS, document_id=document_id, run_id=run_id,
        items=import_rows.equation_rows(document_id, bundle.equations, source=source),
        content_columns=_EQUATION_CONTENT_COLUMNS,
        agent_id_column="agent_equation_id",
        preserved_columns=_KNOWLEDGE_PRESERVED_COLUMNS,
    )
    stats["equations"] = dict(equation_sync.stats)

    evidence_sync = _sync(
        session, table=TABLE_EVIDENCE, document_id=document_id, run_id=run_id,
        items=import_rows.evidence_rows(document_id, bundle.evidence, source=source),
        content_columns=_EVIDENCE_CONTENT_COLUMNS,
        agent_id_column="agent_evidence_id",
        preserved_columns=_KNOWLEDGE_PRESERVED_COLUMNS,
    )
    stats["evidence"] = dict(evidence_sync.stats)

    derivation_sync = _sync(
        session, table=TABLE_DERIVATION_STEPS, document_id=document_id, run_id=run_id,
        items=import_rows.derivation_step_rows(
            document_id, bundle.derivation_chains, equations=bundle.equations, source=source,
        ),
        content_columns=_DERIVATION_CONTENT_COLUMNS,
        agent_id_column="agent_step_id",
        preserved_columns=_KNOWLEDGE_PRESERVED_COLUMNS,
    )
    stats["derivation_steps"] = dict(derivation_sync.stats)

    # KO8: stable_key が一致して agent ID だけが変わった行の参照を同一トランザクションで
    # 張り替える（説明・台帳・注釈が宙に浮かないようにする）。
    remap_summary: dict[str, int] = {}
    for kind, sync in (
        ("claim", claim_sync),
        ("component", component_sync),
        ("equation", equation_sync),
        ("evidence", evidence_sync),
        ("derivation_step", derivation_sync),
    ):
        if not sync.remaps:
            continue
        summary = ko_remap.record_and_reanchor(
            session, document_id=document_id, run_id=run_id, kind=kind, remaps=sync.remaps,
        )
        remap_summary[kind] = int(summary.get("recorded") or 0)
    if remap_summary:
        stats["remap"] = remap_summary

    if bundle.component_graph.get("nodes"):
        graph = rewrite_graph(
            bundle.component_graph,
            document_id=document_id,
            component_id_map=component_sync.id_map,
            claim_id_map=claim_sync.id_map,
        )
        _upsert_graph(session, document_id=document_id, run_id=run_id, graph=graph)
        stats["graph"] = {
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "stored": True,
        }
    else:
        stats["graph"] = {"nodes": 0, "edges": 0, "stored": False}

    return stats
