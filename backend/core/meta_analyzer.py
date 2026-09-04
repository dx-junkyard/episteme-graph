"""メタ分析エンジン — つまづきデータからスキーマ拡張を提案する。

蓄積された未回答クエリ（unanswered_query_logs）を分析し、
現在のDSLスキーマに不足している概念カテゴリや関係性を推論・提案する。

Usage::

    from core.meta_analyzer import analyze_unanswered_queries

    proposal = analyze_unanswered_queries()  # SchemaProposal dict or None
"""

from __future__ import annotations

import json
import logging
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from core.llm import generate_text_with_structured_output
from core.postgres import get_session as _pg_session
from core.schema_registry import get_ontology_types, get_predicates

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM structured output models
# ---------------------------------------------------------------------------


class ProposedSchemaItem(BaseModel):
    """LLMが提案する個別のスキーマ拡張アイテム。"""

    item_type: str = Field(description="'ontology_type' or 'predicate'")
    key: str = Field(description="新しいType/Predicateの識別子 (例: 'Experiment', 'PROVED_BY')")
    label: str = Field(description="表示名")
    description: str = Field(description="説明文")


class SchemaAnalysisResult(BaseModel):
    """LLMのスキーマ分析結果。"""

    has_proposals: bool = Field(description="拡張提案があるか")
    summary: str = Field(description="分析結果のサマリー（教員向け）")
    reasoning: str = Field(description="提案の根拠（どのクエリパターンから導出したか）")
    items: list[ProposedSchemaItem] = Field(
        default_factory=list, description="提案する新しいスキーマ要素のリスト"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_unanswered_queries(
    min_queries: int = 3,
    limit: int = 200,
    course_ids: list[str] | None = None,
) -> dict | None:
    """未回答クエリを分析し、スキーマ拡張提案を生成する。

    Parameters
    ----------
    min_queries : int
        分析に必要な最小クエリ数。これ未満の場合はNoneを返す。
    limit : int
        分析対象の最大クエリ数。
    course_ids : list[str] | None
        分析対象を限定するコース ID 群（**目的外利用の禁止**）。学習者の質問原文は
        そのコースの運営者にだけ開かれる情報なので、呼び出し側（route 層）が
        「その教員が編集できるコース」を解決して渡す。空リストは「対象ゼロ」で、
        SQL も LLM も呼ばずに ``None`` を返す（fail-closed）。``None`` は制限なしで、
        全コースを横断できる SYSTEM_ADMIN 経路専用。

    Returns
    -------
    dict | None
        提案がある場合はスキーマ提案のdict、ない場合はNone。
    """
    if course_ids is not None and not course_ids:
        # 対象コースがゼロなら、質問原文を1件も読まずに終える（LLM も呼ばない）。
        logger.info("Schema analysis skipped: no course in scope for the caller")
        return None

    # 未回答クエリを取得（course_ids 指定時は SQL 内で対象を絞る）
    scope_sql = "" if course_ids is None else "WHERE uql.course_id = ANY(:course_ids)"
    params: dict = {"limit": limit}
    if course_ids is not None:
        params["course_ids"] = list(course_ids)
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(f"""
                SELECT uql.question, uql.topic_id, uql.course_id, uql.asked_at
                FROM unanswered_query_logs uql
                {scope_sql}
                ORDER BY uql.asked_at DESC
                LIMIT :limit
            """),
            params,
        ).fetchall()
    finally:
        session.close()

    if len(rows) < min_queries:
        logger.info(
            "Not enough unanswered queries for analysis (%d < %d)",
            len(rows), min_queries,
        )
        return None

    # 現在のスキーマ定義を取得
    current_types = get_ontology_types()
    current_preds = get_predicates()

    type_list = "\n".join(
        f"  - {t['id']}: {t['description']}" for t in current_types
    )
    pred_list = "\n".join(
        f"  - {p['id']}: {p['description']}" for p in current_preds
    )

    # クエリのテキストをまとめる
    query_texts = []
    for r in rows:
        query_texts.append(f"- [{r[1]}] {r[0]}")
    queries_block = "\n".join(query_texts[:100])

    prompt = f"""あなたはナレッジグラフのスキーマ設計エキスパートです。

以下の「学生が質問したが、システムが回答できなかった質問リスト」を分析し、
現在のDSLスキーマ（概念カテゴリ・関係性）に不足している要素を特定してください。

## 現在のOntologyType（概念カテゴリ）:
{type_list}

## 現在のCorePredicate（関係性タイプ）:
{pred_list}

## 未回答クエリリスト（{len(rows)}件中、最新{min(len(rows), 100)}件）:
{queries_block}

## タスク:
1. 未回答クエリをクラスタリングし、共通するテーマやパターンを特定してください。
2. 現在のスキーマでカバーできていない概念カテゴリや関係性を推論してください。
3. 具体的な拡張提案を生成してください。

## ルール:
- 既存のType/Predicateと重複する提案は出さないでください。
- 提案は具体的で、なぜその拡張が必要かの根拠を示してください。
- item_type は 'ontology_type' または 'predicate' のいずれかです。
- key は英大文字のスネークケース（例: EXPERIMENT, PROVED_BY）で指定してください。
  ただしontology_typeの場合はパスカルケース（例: Experiment, Instrument）も可です。
- 本当に必要な拡張のみを提案してください。不要な拡張は has_proposals=false としてください。
- summaryとreasoningは日本語で記述してください。"""

    try:
        result = generate_text_with_structured_output(
            messages=[{"role": "user", "content": prompt}],
            response_format=SchemaAnalysisResult,
        )
    except Exception:
        logger.exception("Schema analysis LLM call failed")
        return None

    if not result.has_proposals or not result.items:
        logger.info("No schema proposals generated from analysis")
        return None

    # DBに提案を保存
    proposal_id = str(uuid.uuid4())[:12]
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO schema_proposals (id, status, summary, reasoning, source_query_count)
                VALUES (:id, 'pending', :summary, :reasoning, :query_count)
            """),
            {
                "id": proposal_id,
                "summary": result.summary,
                "reasoning": result.reasoning,
                "query_count": len(rows),
            },
        )

        for item in result.items:
            item_id = str(uuid.uuid4())[:12]
            session.execute(
                sa_text("""
                    INSERT INTO schema_proposal_items
                        (id, proposal_id, item_type, key, label, description)
                    VALUES (:id, :proposal_id, :item_type, :key, :label, :description)
                """),
                {
                    "id": item_id,
                    "proposal_id": proposal_id,
                    "item_type": item.item_type,
                    "key": item.key,
                    "label": item.label,
                    "description": item.description,
                },
            )

        session.commit()
        logger.info(
            "Schema proposal created: %s (%d items)",
            proposal_id, len(result.items),
        )
    except Exception:
        session.rollback()
        logger.exception("Failed to save schema proposal")
        return None
    finally:
        session.close()

    return {
        "proposal_id": proposal_id,
        "summary": result.summary,
        "reasoning": result.reasoning,
        "items": [item.model_dump() for item in result.items],
        "source_query_count": len(rows),
        "status": "pending",
    }


def get_proposals(status: str | None = None) -> list[dict]:
    """スキーマ提案一覧を取得する。"""
    session = _pg_session()
    try:
        if status:
            rows = session.execute(
                sa_text("""
                    SELECT id, status, summary, reasoning, source_query_count,
                           created_at, reviewed_at
                    FROM schema_proposals
                    WHERE status = :status
                    ORDER BY created_at DESC
                """),
                {"status": status},
            ).fetchall()
        else:
            rows = session.execute(
                sa_text("""
                    SELECT id, status, summary, reasoning, source_query_count,
                           created_at, reviewed_at
                    FROM schema_proposals
                    ORDER BY created_at DESC
                """),
            ).fetchall()
    finally:
        session.close()

    proposals = []
    for r in rows:
        items = _get_proposal_items(r[0])
        proposals.append({
            "proposal_id": r[0],
            "status": r[1],
            "summary": r[2],
            "reasoning": r[3],
            "source_query_count": r[4],
            "created_at": r[5].isoformat() if r[5] else "",
            "reviewed_at": r[6].isoformat() if r[6] else "",
            "items": items,
        })

    return proposals


def _get_proposal_items(proposal_id: str) -> list[dict]:
    """提案アイテムを取得する。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, item_type, key, label, description
                FROM schema_proposal_items
                WHERE proposal_id = :proposal_id
            """),
            {"proposal_id": proposal_id},
        ).fetchall()
    finally:
        session.close()

    return [
        {
            "id": r[0],
            "item_type": r[1],
            "key": r[2],
            "label": r[3],
            "description": r[4],
        }
        for r in rows
    ]


def approve_proposal(proposal_id: str, reviewer_id: str) -> bool:
    """スキーマ提案を承認し、新しいType/Predicateを登録する。"""
    from core.schema_registry import add_ontology_type, add_predicate, invalidate_cache

    items = _get_proposal_items(proposal_id)
    if not items:
        return False

    # 各アイテムをスキーマに追加
    for item in items:
        if item["item_type"] == "ontology_type":
            add_ontology_type(item["key"], item["label"], item["description"])
        elif item["item_type"] == "predicate":
            add_predicate(item["key"], item["label"], item["description"])

    # ステータスを更新
    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE schema_proposals
                SET status = 'approved',
                    reviewed_by = CAST(:reviewer_id AS uuid),
                    reviewed_at = now()
                WHERE id = :proposal_id AND status = 'pending'
                RETURNING id
            """),
            {"proposal_id": proposal_id, "reviewer_id": reviewer_id},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    invalidate_cache()
    logger.info("Schema proposal %s approved by %s", proposal_id, reviewer_id)
    return result is not None


def reject_proposal(proposal_id: str, reviewer_id: str) -> bool:
    """スキーマ提案を却下する。"""
    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE schema_proposals
                SET status = 'rejected',
                    reviewed_by = CAST(:reviewer_id AS uuid),
                    reviewed_at = now()
                WHERE id = :proposal_id AND status = 'pending'
                RETURNING id
            """),
            {"proposal_id": proposal_id, "reviewer_id": reviewer_id},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info("Schema proposal %s rejected by %s", proposal_id, reviewer_id)
    return result is not None
