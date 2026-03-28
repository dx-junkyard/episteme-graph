"""Shadow Testing シミュレーター — スキーマ提案を承認前にテスト適用する。

提案されたスキーマ拡張を実際に承認する前に、対象・類似・非類似の
3層のドキュメント群に対して新スキーマをインメモリで適用し、
既存グラフ (Before) と新グラフ (After) の差分を計算する。

Usage::

    from core.simulator import run_simulation

    result = run_simulation(proposal_id="abc123")
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import text as sa_text

from core.llm import generate_text
from core.postgres import get_session as _pg_session
from core.schema_registry import get_ontology_types, get_predicates

logger = logging.getLogger(__name__)

# 各カテゴリで選出するドキュメント数の上限
_MAX_DOCS_PER_CATEGORY = 3


# ---------------------------------------------------------------------------
# Document selection
# ---------------------------------------------------------------------------


def _get_proposal_info(proposal_id: str) -> dict | None:
    """提案とそのアイテム情報を取得する。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT id, status, summary, reasoning, source_query_count
                FROM schema_proposals
                WHERE id = :id
            """),
            {"id": proposal_id},
        ).fetchone()
        if not row:
            return None

        items = session.execute(
            sa_text("""
                SELECT id, item_type, key, label, description
                FROM schema_proposal_items
                WHERE proposal_id = :pid
            """),
            {"pid": proposal_id},
        ).fetchall()
    finally:
        session.close()

    return {
        "proposal_id": row[0],
        "status": row[1],
        "summary": row[2],
        "reasoning": row[3],
        "source_query_count": row[4],
        "items": [
            {
                "item_type": i[1],
                "key": i[2],
                "label": i[3],
                "description": i[4],
            }
            for i in items
        ],
    }


def _select_target_docs(proposal_id: str) -> list[dict]:
    """Target: 提案のトリガーとなった未回答クエリに紐づくドキュメントを選出する。

    未回答クエリの course_id からコースに登録された教材を逆引きする。
    """
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT DISTINCT d.id, d.title, d.filename, d.knowledge_graph
                FROM documents d
                JOIN learning_courses lc ON lc.id IN (
                    SELECT DISTINCT course_id FROM unanswered_query_logs
                    ORDER BY asked_at DESC
                    LIMIT 50
                )
                WHERE d.status = 'completed' AND d.knowledge_graph IS NOT NULL
                ORDER BY d.created_at DESC
                LIMIT :lim
            """),
            {"lim": _MAX_DOCS_PER_CATEGORY},
        ).fetchall()
    finally:
        session.close()

    # フォールバック: 紐づくドキュメントがなければ最新のドキュメントを取得
    if not rows:
        session = _pg_session()
        try:
            rows = session.execute(
                sa_text("""
                    SELECT id, title, filename, knowledge_graph
                    FROM documents
                    WHERE status = 'completed' AND knowledge_graph IS NOT NULL
                    ORDER BY created_at DESC
                    LIMIT :lim
                """),
                {"lim": _MAX_DOCS_PER_CATEGORY},
            ).fetchall()
        finally:
            session.close()

    return [
        {
            "doc_id": str(r[0]),
            "title": r[1] or "",
            "filename": r[2] or "",
            "knowledge_graph": r[3] if isinstance(r[3], dict) else (
                json.loads(r[3]) if r[3] else {}
            ),
        }
        for r in rows
    ]


def _select_similar_docs(target_doc_ids: list[str]) -> list[dict]:
    """Similar: Targetと同じコースやタグに属するドキュメントを選出する。"""
    if not target_doc_ids:
        return []

    session = _pg_session()
    try:
        # Target以外の completed ドキュメントで、最近アップロードされたもの
        placeholders = ", ".join(f"CAST(:id_{i} AS uuid)" for i in range(len(target_doc_ids)))
        params = {f"id_{i}": tid for i, tid in enumerate(target_doc_ids)}
        params["lim"] = _MAX_DOCS_PER_CATEGORY

        rows = session.execute(
            sa_text(f"""
                SELECT id, title, filename, knowledge_graph
                FROM documents
                WHERE status = 'completed'
                  AND knowledge_graph IS NOT NULL
                  AND id NOT IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT :lim
            """),
            params,
        ).fetchall()
    finally:
        session.close()

    return [
        {
            "doc_id": str(r[0]),
            "title": r[1] or "",
            "filename": r[2] or "",
            "knowledge_graph": r[3] if isinstance(r[3], dict) else (
                json.loads(r[3]) if r[3] else {}
            ),
        }
        for r in rows
    ]


def _select_control_docs(exclude_ids: list[str]) -> list[dict]:
    """Control: 関連性が低いが参照回数の多いベースラインドキュメントを選出する。"""
    if not exclude_ids:
        exclude_ids = ["00000000-0000-0000-0000-000000000000"]

    session = _pg_session()
    try:
        placeholders = ", ".join(f"CAST(:id_{i} AS uuid)" for i in range(len(exclude_ids)))
        params = {f"id_{i}": eid for i, eid in enumerate(exclude_ids)}
        params["lim"] = _MAX_DOCS_PER_CATEGORY

        # チャンク参照数が多い（＝RAGでよく使われる）ドキュメントを選出
        rows = session.execute(
            sa_text(f"""
                SELECT d.id, d.title, d.filename, d.knowledge_graph
                FROM documents d
                LEFT JOIN chunks c ON c.document_id = d.id
                WHERE d.status = 'completed'
                  AND d.knowledge_graph IS NOT NULL
                  AND d.id NOT IN ({placeholders})
                GROUP BY d.id, d.title, d.filename, d.knowledge_graph
                ORDER BY COUNT(c.id) DESC, d.created_at ASC
                LIMIT :lim
            """),
            params,
        ).fetchall()
    finally:
        session.close()

    return [
        {
            "doc_id": str(r[0]),
            "title": r[1] or "",
            "filename": r[2] or "",
            "knowledge_graph": r[3] if isinstance(r[3], dict) else (
                json.loads(r[3]) if r[3] else {}
            ),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# In-memory re-extraction simulation
# ---------------------------------------------------------------------------


def _build_extended_schema_prompt(proposal_items: list[dict]) -> str:
    """現在のスキーマ + 提案アイテムを含む拡張プロンプトを構築する。"""
    current_types = get_ontology_types()
    current_preds = get_predicates()

    type_lines = [f"  - {t['label']}: {t['description']}" for t in current_types]
    pred_lines = [f"  - {p['label']}: {p['description']}" for p in current_preds]

    # 提案アイテムを追加
    for item in proposal_items:
        if item["item_type"] == "ontology_type":
            type_lines.append(f"  - {item['label']}: {item['description']} [NEW]")
        elif item["item_type"] == "predicate":
            pred_lines.append(f"  - {item['label']}: {item['description']} [NEW]")

    return (
        "## 利用可能な概念カテゴリ (OntologyType):\n"
        + "\n".join(type_lines)
        + "\n\n## 利用可能な関係性タイプ (CorePredicate):\n"
        + "\n".join(pred_lines)
    )


def _simulate_extraction_for_doc(
    doc: dict,
    proposal_items: list[dict],
    extended_schema_prompt: str,
) -> dict:
    """1つのドキュメントに対し、拡張スキーマでインメモリ再抽出を行い差分を返す。"""
    existing_kg = doc.get("knowledge_graph", {})

    # 既存グラフの概念・関係を Before として整理
    before_concepts = existing_kg.get("concepts", [])
    before_relationships = existing_kg.get("relationships", [])

    # 新しいスキーマ定義のキーを抽出
    new_type_keys = [
        item["key"] for item in proposal_items if item["item_type"] == "ontology_type"
    ]
    new_pred_keys = [
        item["key"] for item in proposal_items if item["item_type"] == "predicate"
    ]

    # LLMに既存グラフを新スキーマで再評価させる
    existing_concepts_text = json.dumps(before_concepts, ensure_ascii=False, indent=2)
    existing_rels_text = json.dumps(before_relationships, ensure_ascii=False, indent=2)

    prompt = f"""あなたはナレッジグラフのスキーマ評価エキスパートです。

以下の教材「{doc.get('title', '')}」の既存ナレッジグラフに対し、
新しく追加されるスキーマ要素を適用した場合の変更点を分析してください。

{extended_schema_prompt}

## 既存の概念ノード:
{existing_concepts_text[:3000]}

## 既存の関係エッジ:
{existing_rels_text[:3000]}

## 新規追加されるスキーマ要素:
- 概念カテゴリ: {json.dumps(new_type_keys, ensure_ascii=False) if new_type_keys else "なし"}
- 関係性タイプ: {json.dumps(new_pred_keys, ensure_ascii=False) if new_pred_keys else "なし"}

## タスク:
新しいスキーマ要素を使うと、この教材のナレッジグラフがどう変わるかを分析し、
以下のJSON形式で回答してください:

{{
  "added_concepts": [
    {{"id": "new_concept_1", "name": "概念名", "type": "新カテゴリ名", "reason": "追加理由"}}
  ],
  "removed_concepts": [
    {{"id": "existing_id", "name": "概念名", "reason": "削除/再分類理由"}}
  ],
  "reclassified_concepts": [
    {{"id": "existing_id", "name": "概念名", "old_type": "旧タイプ", "new_type": "新タイプ", "reason": "再分類理由"}}
  ],
  "added_relationships": [
    {{"source": "concept_a", "target": "concept_b", "relation": "新関係タイプ", "reason": "追加理由"}}
  ],
  "removed_relationships": [
    {{"source": "concept_a", "target": "concept_b", "old_relation": "旧関係", "reason": "削除理由"}}
  ],
  "summary": "変更の要約（1-2文）"
}}

JSON のみで回答してください。変更が不要な場合は各配列を空にしてください。"""

    try:
        raw = generate_text(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
        diff_result = json.loads(raw)
    except Exception as exc:
        logger.warning("Simulation extraction failed for doc %s: %s", doc.get("doc_id"), exc)
        diff_result = {
            "added_concepts": [],
            "removed_concepts": [],
            "reclassified_concepts": [],
            "added_relationships": [],
            "removed_relationships": [],
            "summary": f"シミュレーション抽出に失敗しました: {exc}",
        }

    return {
        "doc_id": doc["doc_id"],
        "title": doc["title"],
        "before": {
            "concept_count": len(before_concepts),
            "relationship_count": len(before_relationships),
        },
        "after": {
            "concept_count": (
                len(before_concepts)
                + len(diff_result.get("added_concepts", []))
                - len(diff_result.get("removed_concepts", []))
            ),
            "relationship_count": (
                len(before_relationships)
                + len(diff_result.get("added_relationships", []))
                - len(diff_result.get("removed_relationships", []))
            ),
        },
        "diff": diff_result,
    }


# ---------------------------------------------------------------------------
# Public: メインシミュレーション実行
# ---------------------------------------------------------------------------


def run_simulation(proposal_id: str) -> dict:
    """スキーマ提案のシミュレーションを実行する。

    Parameters
    ----------
    proposal_id : str
        シミュレーション対象の提案ID。

    Returns
    -------
    dict
        シミュレーション結果。Target/Similar/Control の各ドキュメントの
        Before/After差分を含む。
    """
    # 提案情報を取得
    proposal = _get_proposal_info(proposal_id)
    if not proposal:
        raise ValueError(f"Proposal not found: {proposal_id}")

    proposal_items = proposal["items"]
    if not proposal_items:
        return {
            "proposal_id": proposal_id,
            "summary": proposal["summary"],
            "target_docs": [],
            "similar_docs": [],
            "control_docs": [],
            "overall_summary": "提案にスキーマアイテムが含まれていないため、シミュレーション不要です。",
        }

    # 拡張スキーマプロンプトを構築
    extended_prompt = _build_extended_schema_prompt(proposal_items)

    # ドキュメント選出
    target_docs = _select_target_docs(proposal_id)
    target_ids = [d["doc_id"] for d in target_docs]

    similar_docs = _select_similar_docs(target_ids)
    similar_ids = [d["doc_id"] for d in similar_docs]

    all_used_ids = target_ids + similar_ids
    control_docs = _select_control_docs(all_used_ids)

    # 各ドキュメントに対してシミュレーション実行
    target_results = []
    for doc in target_docs:
        result = _simulate_extraction_for_doc(doc, proposal_items, extended_prompt)
        target_results.append(result)

    similar_results = []
    for doc in similar_docs:
        result = _simulate_extraction_for_doc(doc, proposal_items, extended_prompt)
        similar_results.append(result)

    control_results = []
    for doc in control_docs:
        result = _simulate_extraction_for_doc(doc, proposal_items, extended_prompt)
        control_results.append(result)

    # 全体サマリー生成
    total_added = sum(
        len(r["diff"].get("added_concepts", []))
        for r in target_results + similar_results + control_results
    )
    total_removed = sum(
        len(r["diff"].get("removed_concepts", []))
        for r in target_results + similar_results + control_results
    )
    total_reclassified = sum(
        len(r["diff"].get("reclassified_concepts", []))
        for r in target_results + similar_results + control_results
    )

    # Control群での変化が大きすぎると過剰適合の警告
    control_changes = sum(
        len(r["diff"].get("added_concepts", []))
        + len(r["diff"].get("removed_concepts", []))
        + len(r["diff"].get("reclassified_concepts", []))
        for r in control_results
    )
    target_changes = sum(
        len(r["diff"].get("added_concepts", []))
        + len(r["diff"].get("removed_concepts", []))
        + len(r["diff"].get("reclassified_concepts", []))
        for r in target_results
    )

    overfitting_warning = ""
    if control_changes > 0 and target_changes > 0 and control_changes >= target_changes:
        overfitting_warning = (
            " ⚠ Control群での変化がTarget群と同等以上です。"
            "過剰適合のリスクがあるため、カナリアリリースを推奨します。"
        )

    overall_summary = (
        f"全{len(target_results) + len(similar_results) + len(control_results)}件のドキュメントを分析。"
        f"追加概念: {total_added}件, 削除概念: {total_removed}件, "
        f"再分類: {total_reclassified}件。{overfitting_warning}"
    )

    return {
        "proposal_id": proposal_id,
        "summary": proposal["summary"],
        "target_docs": target_results,
        "similar_docs": similar_results,
        "control_docs": control_results,
        "overall_summary": overall_summary,
    }
