"""Shadow Testing シミュレーター — スキーマ提案のプレビュー検証。

スキーマ提案を承認する前に、テスト用ドキュメントに対して
新スキーマでの再抽出をインメモリで行い、Before/After の差分を生成する。

Usage::

    from core.simulator import run_simulation

    result = run_simulation(proposal_id="abc123")
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import text as sa_text

from core.llm import generate_text
from core.postgres import get_session as _pg_session
from core.schema_registry import (
    build_ontology_type_prompt,
    build_predicate_prompt,
    get_ontology_type_names,
    get_ontology_types,
    get_predicate_names,
    get_predicates,
)

logger = logging.getLogger(__name__)

# 各カテゴリの最大ドキュメント数
_MAX_TARGET_DOCS = 3
_MAX_SIMILAR_DOCS = 3
_MAX_CONTROL_DOCS = 2


# ---------------------------------------------------------------------------
# ドキュメント選出
# ---------------------------------------------------------------------------


def _select_target_documents(proposal_id: str) -> list[dict]:
    """Target: 提案のトリガーとなった未回答クエリに紐づくドキュメントを選出する。"""
    session = _pg_session()
    try:
        # 提案に関連する未回答クエリのcourse_idを取得し、
        # そのコースに紐づくドキュメントを取得する
        rows = session.execute(
            sa_text("""
                SELECT DISTINCT d.id, d.title, d.filename, d.knowledge_graph
                FROM documents d
                JOIN chunks c ON c.document_id = d.id
                JOIN learning_courses lc ON lc.data::text LIKE '%%' || c.material_id || '%%'
                JOIN unanswered_query_logs uql ON uql.course_id = lc.id
                WHERE d.status = 'completed'
                  AND d.knowledge_graph IS NOT NULL
                ORDER BY d.title
                LIMIT :limit
            """),
            {"limit": _MAX_TARGET_DOCS},
        ).fetchall()
    except Exception:
        logger.warning("Target document selection via query logs failed, falling back")
        rows = []
    finally:
        session.close()

    if not rows:
        # フォールバック: 最新の completed ドキュメントを取得
        session = _pg_session()
        try:
            rows = session.execute(
                sa_text("""
                    SELECT id, title, filename, knowledge_graph
                    FROM documents
                    WHERE status = 'completed' AND knowledge_graph IS NOT NULL
                    ORDER BY updated_at DESC
                    LIMIT :limit
                """),
                {"limit": _MAX_TARGET_DOCS},
            ).fetchall()
        finally:
            session.close()

    return [
        {
            "doc_id": str(r[0]),
            "title": r[1] or "",
            "filename": r[2] or "",
            "knowledge_graph": r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {}),
        }
        for r in rows
    ]


def _select_similar_documents(target_doc_ids: list[str]) -> list[dict]:
    """Similar: Target と同じコースやタグに属するドキュメントを選出する。"""
    if not target_doc_ids:
        return []

    session = _pg_session()
    try:
        # Target と同じコースに紐づく別のドキュメントを取得
        placeholders = ", ".join(f"'{did}'" for did in target_doc_ids)
        rows = session.execute(
            sa_text(f"""
                SELECT DISTINCT d.id, d.title, d.filename, d.knowledge_graph
                FROM documents d
                WHERE d.status = 'completed'
                  AND d.knowledge_graph IS NOT NULL
                  AND CAST(d.id AS text) NOT IN ({placeholders})
                ORDER BY d.updated_at DESC
                LIMIT :limit
            """),
            {"limit": _MAX_SIMILAR_DOCS},
        ).fetchall()
    finally:
        session.close()

    return [
        {
            "doc_id": str(r[0]),
            "title": r[1] or "",
            "filename": r[2] or "",
            "knowledge_graph": r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {}),
        }
        for r in rows
    ]


def _select_control_documents(exclude_ids: list[str]) -> list[dict]:
    """Control: Target/Similar と関連性が低いベースラインドキュメントを選出する。

    参照回数が多い（chunks テーブルでの行数が多い）ドキュメントを優先。
    """
    if not exclude_ids:
        exclude_ids = ["__none__"]

    session = _pg_session()
    try:
        placeholders = ", ".join(f"'{did}'" for did in exclude_ids)
        rows = session.execute(
            sa_text(f"""
                SELECT d.id, d.title, d.filename, d.knowledge_graph,
                       COUNT(c.id) as chunk_count
                FROM documents d
                LEFT JOIN chunks c ON c.document_id = d.id
                WHERE d.status = 'completed'
                  AND d.knowledge_graph IS NOT NULL
                  AND CAST(d.id AS text) NOT IN ({placeholders})
                GROUP BY d.id, d.title, d.filename, d.knowledge_graph
                ORDER BY chunk_count DESC
                LIMIT :limit
            """),
            {"limit": _MAX_CONTROL_DOCS},
        ).fetchall()
    finally:
        session.close()

    return [
        {
            "doc_id": str(r[0]),
            "title": r[1] or "",
            "filename": r[2] or "",
            "knowledge_graph": r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {}),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 差分計算
# ---------------------------------------------------------------------------


def _extract_concepts_from_kg(kg: dict) -> list[dict]:
    """ナレッジグラフからコンセプト（ノード）のリストを抽出する。"""
    concepts = []

    # abstract_structure.variables から抽出
    abstract = kg.get("abstract_structure", {})
    variables = abstract.get("variables", [])
    for v in variables:
        if isinstance(v, str):
            concepts.append({"name": v, "type": "variable"})
        elif isinstance(v, dict):
            concepts.append({
                "name": v.get("name", v.get("id", str(v))),
                "type": v.get("ontology_type", "variable"),
            })

    # abstract_structure.edges から source/target を抽出
    edges = abstract.get("edges", [])
    for e in edges:
        if isinstance(e, dict):
            src = e.get("source", "")
            tgt = e.get("target", "")
            if src and src not in [c["name"] for c in concepts]:
                concepts.append({"name": src, "type": e.get("ontology_level", "unknown")})
            if tgt and tgt not in [c["name"] for c in concepts]:
                concepts.append({"name": tgt, "type": e.get("ontology_level", "unknown")})

    return concepts


def _extract_relations_from_kg(kg: dict) -> list[dict]:
    """ナレッジグラフからリレーション（エッジ）のリストを抽出する。"""
    relations = []
    abstract = kg.get("abstract_structure", {})
    edges = abstract.get("edges", [])
    for e in edges:
        if isinstance(e, dict):
            relations.append({
                "source": e.get("source", ""),
                "target": e.get("target", ""),
                "predicate": e.get("core_predicate", ""),
                "domain_verb": e.get("domain_verb", ""),
                "polarity": e.get("polarity", ""),
                "is_core": e.get("is_core", False),
            })
    return relations


def _compute_kg_diff(before_kg: dict, after_kg: dict) -> dict:
    """Before/After の知識グラフの差分を計算する。"""
    before_concepts = _extract_concepts_from_kg(before_kg)
    after_concepts = _extract_concepts_from_kg(after_kg)

    before_names = {c["name"] for c in before_concepts}
    after_names = {c["name"] for c in after_concepts}

    added_concepts = [c for c in after_concepts if c["name"] not in before_names]
    removed_concepts = [c for c in before_concepts if c["name"] not in after_names]

    before_relations = _extract_relations_from_kg(before_kg)
    after_relations = _extract_relations_from_kg(after_kg)

    def _rel_key(r: dict) -> str:
        return f"{r['source']}:{r['predicate']}:{r['target']}"

    before_rel_keys = {_rel_key(r) for r in before_relations}
    after_rel_keys = {_rel_key(r) for r in after_relations}

    added_relations = [r for r in after_relations if _rel_key(r) not in before_rel_keys]
    removed_relations = [r for r in before_relations if _rel_key(r) not in after_rel_keys]

    return {
        "before_concept_count": len(before_concepts),
        "after_concept_count": len(after_concepts),
        "added_concepts": added_concepts,
        "removed_concepts": removed_concepts,
        "before_relation_count": len(before_relations),
        "after_relation_count": len(after_relations),
        "added_relations": added_relations,
        "removed_relations": removed_relations,
    }


# ---------------------------------------------------------------------------
# インメモリ再抽出（LLM を使用したシミュレーション）
# ---------------------------------------------------------------------------


def _build_extended_schema_prompt(proposal_items: list[dict]) -> tuple[str, str]:
    """既存スキーマ + 提案アイテムを結合した OntologyType / Predicate プロンプトを生成する。"""
    # 既存定義
    current_types = get_ontology_types()
    current_preds = get_predicates()

    type_names = [t["id"] for t in current_types]
    pred_names = [p["id"] for p in current_preds]

    type_lines = []
    for t in current_types:
        suffix = f" — {t['description']}" if t["description"] else ""
        type_lines.append(f"  {t['label']}{suffix}")

    pred_lines = []
    for p in current_preds:
        suffix = f" → {p['description']}" if p["description"] else ""
        pred_lines.append(f"  {p['label']}{suffix}")

    # 提案アイテムを追加
    for item in proposal_items:
        if item["item_type"] == "ontology_type":
            if item["key"] not in type_names:
                type_names.append(item["key"])
                suffix = f" — {item['description']}" if item.get("description") else ""
                type_lines.append(f"  {item['label']}{suffix} [NEW]")
        elif item["item_type"] == "predicate":
            if item["key"] not in pred_names:
                pred_names.append(item["key"])
                suffix = f" → {item['description']}" if item.get("description") else ""
                pred_lines.append(f"  {item['label']}{suffix} [NEW]")

    return "\n".join(type_lines), "\n".join(pred_lines)


def _simulate_extraction_for_doc(
    doc: dict,
    proposal_items: list[dict],
) -> dict:
    """単一ドキュメントに対し、新スキーマでのインメモリ再抽出シミュレーションを行う。"""
    kg = doc.get("knowledge_graph", {})

    # 既存の知識グラフから主要情報を要約
    smiles_dsl = kg.get("abstract_structure", {}).get("smiles_dsl", "")
    title = kg.get("title", doc.get("title", ""))
    domain = kg.get("domain", "")

    # 拡張スキーマプロンプトを構築
    extended_types, extended_preds = _build_extended_schema_prompt(proposal_items)

    # 提案アイテムの説明
    new_items_desc = "\n".join(
        f"- [{item['item_type']}] {item['key']}: {item.get('description', '')}"
        for item in proposal_items
    )

    prompt = (
        "あなたはナレッジグラフ抽出シミュレーターです。\n"
        "以下の論文の既存ナレッジグラフに対し、新しいスキーマ定義が追加された場合に\n"
        "どのような変化が起きるかをシミュレートしてください。\n\n"
        f"## 論文タイトル: {title}\n"
        f"## 分野: {domain}\n\n"
        f"## 既存のDSL（SMILES形式）:\n{smiles_dsl[:2000]}\n\n"
        f"## 新しく追加されるスキーマ要素:\n{new_items_desc}\n\n"
        f"## 拡張後のOntologyType一覧:\n{extended_types}\n\n"
        f"## 拡張後のCorePredicate一覧:\n{extended_preds}\n\n"
        "## タスク:\n"
        "新しいスキーマ要素を考慮して、この論文のナレッジグラフがどう変化するかを\n"
        "シミュレートしてください。以下のJSON形式で回答してください:\n\n"
        "```json\n"
        "{\n"
        '  "added_concepts": [{"name": "...", "type": "新しいOntologyType"}],\n'
        '  "removed_concepts": [{"name": "...", "type": "既存のOntologyType"}],\n'
        '  "added_relations": [{"source": "...", "target": "...", "predicate": "...", "domain_verb": "...", "polarity": "+"}],\n'
        '  "removed_relations": [{"source": "...", "target": "...", "predicate": "...", "domain_verb": "...", "polarity": "+"}],\n'
        '  "reclassified_nodes": [{"name": "...", "old_type": "...", "new_type": "..."}],\n'
        '  "summary": "変化の要約（日本語）"\n'
        "}\n"
        "```\n\n"
        "ルール:\n"
        "- 新しいスキーマ要素が適用可能な場合のみ変更を提案してください。\n"
        "- 無理に変更を加える必要はありません。変化がない場合は空配列で回答してください。\n"
        "- reclassified_nodes: 既存ノードのOntologyTypeが新タイプに変更される場合に記述。\n"
        "- JSONのみで回答してください。"
    )

    try:
        raw = generate_text(messages=[{"role": "user", "content": prompt}])
        # JSONを抽出
        import re
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            sim_result = json.loads(match.group())
        else:
            sim_result = {}
    except Exception:
        logger.warning("Simulation extraction failed for doc %s", doc.get("doc_id", ""))
        sim_result = {}

    return {
        "doc_id": doc["doc_id"],
        "title": doc.get("title", ""),
        "added_concepts": sim_result.get("added_concepts", []),
        "removed_concepts": sim_result.get("removed_concepts", []),
        "added_relations": sim_result.get("added_relations", []),
        "removed_relations": sim_result.get("removed_relations", []),
        "reclassified_nodes": sim_result.get("reclassified_nodes", []),
        "summary": sim_result.get("summary", ""),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_proposal_with_items(proposal_id: str) -> dict | None:
    """提案とそのアイテムを取得する。"""
    from core.meta_analyzer import _get_proposal_items

    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT id, status, summary, reasoning, source_query_count,
                       created_at, reviewed_at
                FROM schema_proposals
                WHERE id = :proposal_id
            """),
            {"proposal_id": proposal_id},
        ).fetchone()
    finally:
        session.close()

    if not row:
        return None

    items = _get_proposal_items(proposal_id)
    return {
        "proposal_id": row[0],
        "status": row[1],
        "summary": row[2],
        "reasoning": row[3],
        "source_query_count": row[4],
        "items": items,
    }


def run_simulation(proposal_id: str) -> dict | None:
    """スキーマ提案に対するShadow Testingシミュレーションを実行する。

    Parameters
    ----------
    proposal_id : str
        シミュレーション対象のスキーマ提案ID。

    Returns
    -------
    dict | None
        シミュレーション結果。提案が見つからない場合はNone。
    """
    proposal = get_proposal_with_items(proposal_id)
    if not proposal:
        return None

    proposal_items = proposal["items"]

    # Step 1: テストドキュメントの選出
    target_docs = _select_target_documents(proposal_id)
    target_ids = [d["doc_id"] for d in target_docs]

    similar_docs = _select_similar_documents(target_ids)
    similar_ids = [d["doc_id"] for d in similar_docs]

    exclude_ids = target_ids + similar_ids
    control_docs = _select_control_documents(exclude_ids)

    # Step 2: 各ドキュメントに対してシミュレーション実行
    results: dict[str, list[dict]] = {
        "target": [],
        "similar": [],
        "control": [],
    }

    for doc in target_docs:
        sim = _simulate_extraction_for_doc(doc, proposal_items)
        results["target"].append(sim)

    for doc in similar_docs:
        sim = _simulate_extraction_for_doc(doc, proposal_items)
        results["similar"].append(sim)

    for doc in control_docs:
        sim = _simulate_extraction_for_doc(doc, proposal_items)
        results["control"].append(sim)

    # Step 3: サマリー統計の計算
    total_added = 0
    total_removed = 0
    total_reclassified = 0
    for category in results.values():
        for sim in category:
            total_added += len(sim.get("added_concepts", []))
            total_removed += len(sim.get("removed_concepts", []))
            total_reclassified += len(sim.get("reclassified_nodes", []))

    return {
        "proposal_id": proposal_id,
        "proposal_summary": proposal["summary"],
        "proposal_items": proposal_items,
        "results": results,
        "stats": {
            "target_doc_count": len(target_docs),
            "similar_doc_count": len(similar_docs),
            "control_doc_count": len(control_docs),
            "total_added_concepts": total_added,
            "total_removed_concepts": total_removed,
            "total_reclassified_nodes": total_reclassified,
        },
    }


def approve_with_scope(
    proposal_id: str,
    reviewer_id: str,
    scope: str = "full",
    course_ids: list[str] | None = None,
) -> dict | None:
    """スキーマ提案をスコープ付きで承認する。

    Parameters
    ----------
    proposal_id : str
        承認する提案ID。
    reviewer_id : str
        承認者のユーザーID。
    scope : str
        "full" (全体適用) または "canary" (特定コースのみ)。
    course_ids : list[str] | None
        scope="canary" の場合に適用対象のコースIDリスト。

    Returns
    -------
    dict | None
        承認結果。提案が見つからない場合はNone。
    """
    from core.meta_analyzer import approve_proposal
    from core.reextractor import enqueue_reextraction

    # 提案を承認（スキーマへの追加は常に行う）
    success = approve_proposal(proposal_id, reviewer_id)
    if not success:
        return None

    if scope == "full":
        # 全ドキュメントを再抽出
        job = enqueue_reextraction(proposal_id)
        return {
            "proposal_id": proposal_id,
            "status": "approved",
            "scope": "full",
            "reextraction_job": job,
        }
    else:
        # カナリアリリース: 指定コースに紐づくドキュメントのみ再抽出
        job = _enqueue_canary_reextraction(proposal_id, course_ids or [])
        return {
            "proposal_id": proposal_id,
            "status": "approved",
            "scope": "canary",
            "target_courses": course_ids or [],
            "reextraction_job": job,
        }


def _enqueue_canary_reextraction(
    proposal_id: str,
    course_ids: list[str],
) -> dict:
    """カナリアリリース用の再抽出ジョブをキューに追加する。"""
    import threading

    session = _pg_session()
    try:
        # 対象コースに紐づくドキュメント数を取得
        if course_ids:
            placeholders = ", ".join(f"'{cid}'" for cid in course_ids)
            count_row = session.execute(
                sa_text(f"""
                    SELECT COUNT(DISTINCT d.id)
                    FROM documents d
                    JOIN chunks c ON c.document_id = d.id
                    JOIN learning_courses lc ON lc.data::text LIKE '%%' || c.material_id || '%%'
                    WHERE lc.id IN ({placeholders})
                      AND d.status = 'completed'
                """),
            ).fetchone()
            total_docs = count_row[0] if count_row else 0
        else:
            total_docs = 0

        job_id = str(uuid.uuid4())[:12]
        session.execute(
            sa_text("""
                INSERT INTO reextraction_jobs (id, proposal_id, status, total_docs)
                VALUES (:id, :proposal_id, 'pending', :total_docs)
            """),
            {"id": job_id, "proposal_id": proposal_id, "total_docs": total_docs},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    # バックグラウンドスレッドで実行
    thread = threading.Thread(
        target=_run_canary_reextraction,
        args=(job_id, course_ids),
        daemon=True,
    )
    thread.start()

    return {
        "job_id": job_id,
        "proposal_id": proposal_id,
        "status": "pending",
        "total_docs": total_docs,
        "processed_docs": 0,
    }


def _run_canary_reextraction(job_id: str, course_ids: list[str]) -> None:
    """カナリアリリース用の再抽出を実行する。"""
    from core.reextractor import _run_reextraction_job

    # 現状は通常の再抽出ジョブと同じロジックを使用
    # TODO: course_ids によるフィルタリングを追加
    _run_reextraction_job(job_id)
