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

#: 取り込み run の ``stage_outputs`` に残す参照の健全性のキー（パイプラインと同じ綴り）。
REFERENCE_HEALTH_KEY = "reference_health"

#: **人間が確定した** review_status（束に無くても supersede しない = P4-R2）。
#: 承認だけでなく却下・要修正も人間の判断で、外から来た束が倒してよいものではない。
HUMAN_DECIDED_REVIEW_STATUSES: tuple[str, ...] = (
    "teacher_approved",
    "teacher_reviewed",
    "endorsed",
    "rejected",
    "needs_revision",
)

#: 保護対象を列挙するときのラベルの上限（dry-run の表示。件数は別に返す）。
SUPERSEDE_LABEL_MAX = 20

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


# ---------------------------------------------------------------------------
# P4-R2: 人間が確定した live 行を supersede から守る
#
# `replace=true` は「再解析と同じ規則で置き換える」だが、束は**別インスタンスが
# 書き出した外部入力**であって、この教材を再解析した結果ではない。stable_key が
# 一致しない（＝束に無い）live 行を一律に supersede すると、この教材で教員が
# 承認・却下した行が 1 回の取り込みで表示対象から落ちる。回復には手作業が要る。
#
# そこで人間が確定した行は、束に無くても **incoming に「そのまま」合流させて**
# sync の一致経路に載せる（sync 側は非改変 = F1 所有）。内容列を渡さないので
# UPDATE は agent ID と produced_by_run_id だけに当たり、本文・確定列は動かない。
# ---------------------------------------------------------------------------


class _KindSpec:
    """保護・プレビューのための種別ごとの読み口（表・ラベル・触った判定）。"""

    def __init__(
        self,
        key: str,
        label: str,
        source: str,
        agent_id_column: str,
        label_sql: str,
        *,
        live_view: bool = False,
        extra_columns: tuple[str, ...] = (),
        touched=None,
        has_review_status: bool = True,
    ):
        self.key = key
        self.label = label
        self.source = source
        self.agent_id_column = agent_id_column
        self.label_sql = label_sql
        self.live_view = live_view
        self.extra_columns = extra_columns
        self.touched = touched
        self.has_review_status = has_review_status

    def select_sql(self) -> str:
        columns = [
            "id::text AS id",
            "stable_key",
            f"{self.agent_id_column} AS agent_id",
            (
                "COALESCE(review_status, '') AS review_status"
                if self.has_review_status
                else "'' AS review_status"
            ),
            f"{self.label_sql} AS label",
        ]
        columns += [c for c in self.extra_columns]
        where = "document_id = CAST(:doc AS uuid)"
        if not self.live_view:
            where += " AND superseded_at IS NULL"
        return f"SELECT {', '.join(columns)} FROM {self.source} WHERE {where}"


def _row_human_decided(row: Any) -> bool:
    """人間の判断が乗った live 行か（review_status の語彙 + 種別ごとの追加判定）。"""
    review_status = _text(row.get("review_status") if hasattr(row, "get") else None)
    return review_status in HUMAN_DECIDED_REVIEW_STATUSES


def _component_protected(row: Any) -> bool:
    return _row_human_decided(row) or _component_human_touched(row)


KIND_SPECS: tuple[_KindSpec, ...] = (
    _KindSpec(
        "claims", "主張", VIEW_CLAIMS_LIVE, "agent_claim_id",
        "COALESCE(NULLIF(normalized_text, ''), text)", live_view=True,
    ),
    _KindSpec(
        "components", "部品", VIEW_COMPONENTS_LIVE, "agent_component_id",
        "name", live_view=True,
        extra_columns=("COALESCE(status, '') AS status",
                       "COALESCE(teacher_notes, '') AS teacher_notes",
                       "COALESCE(maturity_source, '') AS maturity_source"),
        touched=_component_protected,
    ),
    _KindSpec(
        "equations", "式", TABLE_EQUATIONS, "agent_equation_id",
        "COALESCE(NULLIF(label, ''), plain_text)",
    ),
    # evidence は人間の確定列を持たない（078 に review_status 列が無い = V-1）ので、
    # 守る対象が無い。SELECT も review_status を読まない。
    _KindSpec(
        "evidence", "根拠", TABLE_EVIDENCE, "agent_evidence_id", "evidence_text",
        has_review_status=False,
    ),
    _KindSpec(
        "derivation_steps", "導出", TABLE_DERIVATION_STEPS, "agent_step_id",
        "COALESCE(NULLIF(operation, ''), agent_derivation_id)",
    ),
)

_SPECS_BY_KEY = {spec.key: spec for spec in KIND_SPECS}


def _live_rows(session, spec: _KindSpec, document_id: str) -> list[dict]:
    try:
        return [
            dict(row)
            for row in session.execute(
                sa_text(spec.select_sql()), {"doc": document_id}
            ).mappings().all()
        ]
    except Exception:  # noqa: BLE001 — 読めなければ保護もプレビューも諦める（書き込みは止めない）
        logger.warning("import: live row scan failed for %s", spec.key, exc_info=True)
        return []


def _protected(spec: _KindSpec, row: dict) -> bool:
    predicate = spec.touched or _row_human_decided
    try:
        return bool(predicate(row))
    except Exception:  # pragma: no cover - 述語は純粋な想定
        logger.warning("import: protection predicate failed for %s", spec.key, exc_info=True)
        return True


def merge_protected_rows(live_rows: list[dict], incoming: list[dict], spec: _KindSpec) -> list[dict]:
    """束に無い「人間が確定した行」を incoming の先頭へそのまま合流させる（純関数）。

    ``values`` は空 dict。sync は一致経路に入り、内容列を 1 つも UPDATE しない
    （``produced_by_run_id`` と ``updated_at`` だけが動く）。stable_key の無い行は
    sync が一致させられないので合流できない（:func:`supersede_preview` が事実として
    列挙する）。
    """
    incoming_keys = {_text(item.get("stable_key")) for item in incoming}
    incoming_keys.discard("")
    merged: list[dict] = []
    seen: set[str] = set()
    for row in live_rows:
        stable_key = _text(row.get("stable_key"))
        if not stable_key or stable_key in incoming_keys or stable_key in seen:
            continue
        if not _protected(spec, row):
            continue
        seen.add(stable_key)
        merged.append({
            "stable_key": stable_key,
            "agent_id": _text(row.get("agent_id")),
            "values": {},
        })
    return merged + list(incoming)


def supersede_preview(session, *, document_id: str, incoming_keys: dict[str, set[str]]) -> dict:
    """`replace=true` で何が表示対象から外れるか（dry-run の事実。書き込みなし）。

    返すのは種別ごとの ``{"superseded": n, "kept_human_decided": m, "labels": [...],
    "unmatchable": k}``。件数は教員向けの運用情報で、学習者には出ない（KT7）。
    """
    out: dict[str, Any] = {}
    for spec in KIND_SPECS:
        keys = {k for k in (incoming_keys.get(spec.key) or set()) if k}
        rows = _live_rows(session, spec, document_id)
        superseded: list[dict] = []
        kept: list[dict] = []
        unmatchable = 0
        for row in rows:
            stable_key = _text(row.get("stable_key"))
            if stable_key and stable_key in keys:
                continue
            if not stable_key:
                # 同一性キーが無い行は合流させられない（保護の対象にできない）。
                unmatchable += 1
                superseded.append(row)
                continue
            (kept if _protected(spec, row) else superseded).append(row)
        entry = {
            "label": spec.label,
            "superseded": len(superseded),
            "kept_human_decided": len(kept),
            "unmatchable": unmatchable,
            "labels": [
                _label_snippet(row.get("label")) for row in superseded[:SUPERSEDE_LABEL_MAX]
            ],
            "kept_labels": [
                _label_snippet(row.get("label")) for row in kept[:SUPERSEDE_LABEL_MAX]
            ],
            "labels_truncated": len(superseded) > SUPERSEDE_LABEL_MAX,
        }
        out[spec.key] = entry
    return out


def _label_snippet(value: Any) -> str:
    text = _text(value)
    return text[:80] if text else ""


def incoming_stable_keys(bundle: ImportBundle, document_id: str) -> dict[str, set[str]]:
    """束から作られる incoming 行の stable_key（dry-run のプレビュー用・書き込みなし）。

    実行時と**同じ行ビルダー**を通す（プレビューと実行が食い違わない）。
    """
    def keys(items: list[dict]) -> set[str]:
        return {_text(item.get("stable_key")) for item in items} - {""}

    source = bundle.source()
    return {
        "claims": keys(import_rows.claim_rows(
            document_id, bundle.claims, evidence=bundle.evidence,
            equations=bundle.equations, source=source,
        )),
        "components": keys(import_rows.component_rows(
            document_id, bundle.components, claims=bundle.claims,
            evidence=bundle.evidence, source=source,
        )),
        "equations": keys(import_rows.equation_rows(document_id, bundle.equations, source=source)),
        "evidence": keys(import_rows.evidence_rows(document_id, bundle.evidence, source=source)),
        "derivation_steps": keys(import_rows.derivation_step_rows(
            document_id, bundle.derivation_chains, equations=bundle.equations, source=source,
        )),
    }


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
    """取り込み run の ``stage_outputs`` を書く。

    参照の健全性（P4-R7）は ``stage_outputs.reference_health`` に**パイプラインと同じ
    位置**で置く（読み手が取り込み run とパイプライン run を同じ形で読める）。
    """
    stats = dict(stats or {})
    health = stats.pop(REFERENCE_HEALTH_KEY, None)
    outputs: dict[str, Any] = {IMPORT_STAGE: stats}
    if health:
        outputs[REFERENCE_HEALTH_KEY] = health
    session.execute(
        sa_text(
            """
            UPDATE document_analysis_runs
            SET stage_outputs = CAST(:outputs AS jsonb), updated_at = now()
            WHERE id = CAST(:run_id AS uuid)
            """
        ),
        {"run_id": run_id, "outputs": _json_dumps(outputs)},
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


def _sync(
    session, *, table, document_id, run_id, items, content_columns, agent_id_column,
    kind: str = "", **kwargs,
):
    """1 種別を同期する（人間が確定した行を守ってから sync へ渡す = P4-R2）。"""
    incoming = import_rows.dedupe(items)
    spec = _SPECS_BY_KEY.get(kind)
    if spec is not None:
        incoming = merge_protected_rows(_live_rows(session, spec, document_id), incoming, spec)
    return sync_live_rows(
        session,
        table=table,
        document_id=document_id,
        run_id=run_id,
        incoming=incoming,
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
            # P4-R2: 「保たれます」とだけ言うのは嘘だった（束に無い行は表示対象から
            # 外れる）。何が外れて何が残るかを 2 文に分けて言う。
            facts.append(
                "この教材には既に解析結果があります。束に無い既存の項目は、"
                "この教材の表示対象から外れます（行は残り、履歴として参照できます）。"
            )
            facts.append(
                "教員が確定した項目（承認・却下・要修正）は、束に無くても表示対象から外しません。"
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
        agent_id_column="agent_claim_id", kind="claims",
        preserved_columns=_CLAIM_PRESERVED_COLUMNS,
        column_casts=_CLAIM_COLUMN_CASTS,
    )
    stats["claims"] = dict(claim_sync.stats)
    # V-11: 親子（atomic rewrite の子 claim）は sync が id を確定させたあとに張る。
    parent_links = link_claim_parents(session, claims=bundle.claims, id_map=claim_sync.id_map)
    if parent_links:
        stats["claims"]["parent_links"] = parent_links

    component_items = import_rows.component_rows(
        document_id, bundle.components, claims=bundle.claims, evidence=bundle.evidence,
        claim_id_map=claim_sync.id_map, source=source,
    )
    component_sync = _sync(
        session, table=TABLE_COMPONENTS, document_id=document_id, run_id=run_id,
        items=component_items, content_columns=_COMPONENT_CONTENT_COLUMNS,
        agent_id_column="agent_component_id", kind="components",
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
        agent_id_column="agent_equation_id", kind="equations",
        preserved_columns=_KNOWLEDGE_PRESERVED_COLUMNS,
    )
    stats["equations"] = dict(equation_sync.stats)

    evidence_sync = _sync(
        session, table=TABLE_EVIDENCE, document_id=document_id, run_id=run_id,
        items=import_rows.evidence_rows(document_id, bundle.evidence, source=source),
        content_columns=_EVIDENCE_CONTENT_COLUMNS,
        agent_id_column="agent_evidence_id", kind="evidence",
        # evidence に人間の確定列は無い（078 に review_status 列が無い = V-1）。
        preserved_columns=(),
    )
    stats["evidence"] = dict(evidence_sync.stats)

    derivation_sync = _sync(
        session, table=TABLE_DERIVATION_STEPS, document_id=document_id, run_id=run_id,
        items=import_rows.derivation_step_rows(
            document_id, bundle.derivation_chains, equations=bundle.equations, source=source,
        ),
        content_columns=_DERIVATION_CONTENT_COLUMNS,
        agent_id_column="agent_step_id", kind="derivation_steps",
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

    # P4-R7: 取り込み直後の参照の整合を、この取り込み run の事実として残す
    # （best-effort。検査に失敗しても取り込みは止めない）。同じトランザクションで
    # 読むので「いま書いた行」を含んだ検査になる。
    stats[REFERENCE_HEALTH_KEY] = _reference_health(session, document_id)

    return stats


def link_claim_parents(session, *, claims: list[dict], id_map: dict[str, str]) -> int:
    """束の claim の親子を取り込み先の UUID で張り直す（V-11）。

    ``theory_claims.parent_claim_id`` は内容列ではなく**同一バッチ内の行を指す FK**
    なので、sync が id を確定させたあとでしか書けない。解決できた組だけを書き、
    解決できない親は**触らない**（NULL で潰さない = 情報を落とさない）。

    Returns:
        張り直した件数。
    """
    links = import_rows.claim_parent_links(claims or [])
    if not links or not id_map:
        return 0
    written = 0
    for child_agent_id, parent_agent_id in links:
        child_id = _text(id_map.get(child_agent_id))
        parent_id = _text(id_map.get(parent_agent_id))
        if not child_id or not parent_id or child_id == parent_id:
            continue
        session.execute(
            sa_text(
                f"""
                UPDATE {TABLE_CLAIMS}
                SET parent_claim_id = CAST(:parent AS uuid), updated_at = now()
                WHERE id = CAST(:child AS uuid)
                """
            ),
            {"parent": parent_id, "child": child_id},
        )
        written += 1
    return written


def _reference_health(session, document_id: str) -> dict:
    """取り込み直後の参照の健全性（失敗は「確認できなかった」という事実に畳む）。"""
    from core.reference_health import check_document_references, unchecked_result

    try:
        return check_document_references(session, document_id)
    except Exception:  # noqa: BLE001 — 検査は運用情報。取り込みの成否を左右させない。
        logger.warning("import: reference health check failed", exc_info=True)
        return unchecked_result()
