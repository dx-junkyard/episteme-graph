"""Async batch evaluation for abstraction pattern matching.

新しい AbstractionPattern が登録されたとき、過去の論文群に対して
構造的同型性（Structural Isomorphism）を評価するバックグラウンドタスク。

処理フロー
----------
1. Qdrant でパターンのベクトルと類似度の高い過去の論文を上位 N 件取得
2. 候補論文の PaperStructure を MinIO からロード
3. LLM（Reasoning モデル）で構造的同型性を評価
4. 閾値以上の場合、Neo4j に MATCHES_PATTERN エッジを作成

Notes on Reasoning models
-------------------------
system ロールは使用不可。user ロールのみ。
temperature / max_tokens は指定しない。
"""

from __future__ import annotations

import json
import logging

from core.db import get_driver
from core.embedder import search_similar_papers
from core.llm import get_client, get_settings
from core.schema import AbstractionPattern, PaperStructure, PatternMatch

logger = logging.getLogger(__name__)

# [PHASE 4 — ISOMORPHISM EVALUATION]
# 同型性評価の自信度スコア閾値
# クロスドメイン（例: 生態学 ↔ 経済学）では意味的距離が大きいため、
# 表面的な語彙の一致ではなく構造的同型性のみを根拠とするスコアは
# 自然に低めになる傾向がある。0.6 は同一ドメイン内マッチに適した値であり、
# 異分野横断パターン検索では偽陰性（見逃し）が増加する。
# 0.5 に下げることで、構造的に有効なクロスドメインマッチを捕捉しやすくする。
_CONFIDENCE_THRESHOLD = 0.5

# Qdrant から取得する候補論文数
_TOP_K = 5


def _build_pattern_query_text(pattern: AbstractionPattern) -> str:
    """パターンの検索クエリテキストを構築する。"""
    rules = "; ".join(pattern.structural_rules) if pattern.structural_rules else ""
    variables = ", ".join(pattern.variables_template) if pattern.variables_template else ""
    return (
        f"{pattern.name}. {pattern.description} "
        f"Variables: {variables}. Rules: {rules}"
    )


def _load_paper_structure(arxiv_id: str, storage_client) -> PaperStructure | None:
    """MinIO から PaperStructure を読み込む。見つからなければ None を返す。"""
    safe_id = arxiv_id.replace("/", "_")
    try:
        response = storage_client.get_object("extracted-structures", f"{safe_id}.json")
        data = response.read()
        response.close()
        response.release_conn()
        return PaperStructure.model_validate_json(data)
    except Exception:
        logger.warning("Could not load PaperStructure for %s", arxiv_id)
        return None


def _evaluate_isomorphism(
    pattern: AbstractionPattern,
    paper: PaperStructure,
) -> PatternMatch | None:
    """LLM を使ってパターンと論文の構造的同型性を評価する。

    Returns
    -------
    PatternMatch | None
        閾値以上の自信度の場合は PatternMatch を返す。閾値未満なら None。
    """
    client = get_client()
    settings = get_settings()

    prompt = (
        "あなたはメタ構造転写エンジンの同型性評価モジュールです。\n"
        "以下の「抽象化パターン」が、対象論文の構造と「構造的同型性（Structural Isomorphism）」\n"
        "を持つかどうかを評価してください。\n\n"
        "=== [PHASE 4 — CROSS-DOMAIN ISOMORPHISM EVALUATION] ===\n"
        "【重要】このシステムの目的は「異分野横断」パターン検索です。\n"
        "パターンと論文が同一ドメイン（例: どちらも経済学）である必要はありません。\n"
        "生態学の論文から抽出されたパターンが経済学論文の構造と同型であれば、\n"
        "それは価値ある発見です。表面的な語彙の一致ではなく、\n"
        "「変数間の関係構造」が対応しているかどうかを判断してください。\n\n"
        "【クロスドメイン評価基準】\n"
        "- パターンの変数（X, Y, Z等）が論文の具体的な概念に「構造的に」マッピングできるか\n"
        "  （ドメインが異なっていても、役割・機能が一致すれば有効）\n"
        "- パターンの構造ルール（変数間の関係：inhibits / enables / causes 等）が\n"
        "  論文の因果構造と同じ論理的パターンを持つか\n"
        "- マッピングが論理的に整合しているか（ドメイン語彙の差異は評価対象外）\n\n"
        "【スコアリング指針】\n"
        "- 0.8〜1.0: 構造が高精度で一致。変数マッピングが明確で論理的\n"
        "- 0.5〜0.8: 構造的に対応しているが、一部の関係が緩やかな一致または近似\n"
        "- 0.3〜0.5: 部分的な構造の一致があるが、重要な差異も存在する\n"
        "- 0.0〜0.3: 構造的同型性が認められない\n"
        f"現在の承認閾値は {_CONFIDENCE_THRESHOLD} です。"
        "クロスドメインでは 0.5 以上を有効なマッチとして扱います。\n\n"
        f"--- 抽象化パターン ---\n{pattern.model_dump_json(indent=2)}\n\n"
        f"--- 対象論文の構造 ---\n{paper.model_dump_json(indent=2)}\n\n"
        "以下のJSONのみで回答してください:\n"
        "{\n"
        '  "is_isomorphic": true/false,\n'
        '  "confidence_score": 0.0〜1.0,\n'
        '  "mapping_explanation": "変数Xは論文の○○に対応し…（ドメイン横断の対応関係を明示）"\n'
        "}"
    )

    try:
        resp = client.chat.completions.create(
            model=settings.analysis_model,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content or "{}"

        # JSON 抽出
        import re
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            logger.warning("Could not extract JSON from LLM response for pattern %s / paper %s",
                           pattern.pattern_id, paper.paper_id)
            return None

        result = json.loads(match.group())
        confidence = float(result.get("confidence_score", 0.0))

        if confidence < _CONFIDENCE_THRESHOLD:
            logger.info(
                "Pattern %s does not match paper %s (confidence=%.2f < %.2f)",
                pattern.pattern_id, paper.paper_id, confidence, _CONFIDENCE_THRESHOLD,
            )
            return None

        return PatternMatch(
            pattern_id=pattern.pattern_id,
            target_arxiv_id=paper.paper_id,
            mapping_explanation=result.get("mapping_explanation", ""),
            confidence_score=confidence,
        )

    except Exception:
        logger.exception(
            "Isomorphism evaluation failed for pattern %s / paper %s",
            pattern.pattern_id, paper.paper_id,
        )
        return None


def _save_match_to_neo4j(match: PatternMatch) -> None:
    """PatternMatch を Neo4j に保存する（MATCHES_PATTERN エッジ）。"""
    driver = get_driver()
    with driver.session() as session:
        session.run(
            """
            MERGE (p:Paper {arxiv_id: $target_arxiv_id})
            MERGE (ap:AbstractionPattern {pattern_id: $pattern_id})
            MERGE (p)-[r:MATCHES_PATTERN]->(ap)
            SET r.match_id = $match_id,
                r.mapping_explanation = $mapping_explanation,
                r.confidence_score = $confidence_score
            """,
            target_arxiv_id=match.target_arxiv_id,
            pattern_id=match.pattern_id,
            match_id=match.match_id,
            mapping_explanation=match.mapping_explanation,
            confidence_score=match.confidence_score,
        )
    logger.info(
        "Saved PatternMatch %s → %s (confidence=%.2f) to Neo4j",
        match.pattern_id, match.target_arxiv_id, match.confidence_score,
    )


def run_pattern_evaluation_task(
    pattern: AbstractionPattern,
    storage_client,
) -> list[PatternMatch]:
    """新しいパターンに対して、過去の論文群から構造的同型性を評価するバッチタスク。

    Parameters
    ----------
    pattern:
        評価対象の AbstractionPattern。
    storage_client:
        MinIO クライアント（PaperStructure のロードに使用）。

    Returns
    -------
    list[PatternMatch]
        閾値以上のマッチ結果のリスト。
    """
    logger.info("Starting pattern evaluation task for pattern_id=%s", pattern.pattern_id)

    client = get_client()
    settings = get_settings()

    # 1. パターンのテキスト表現で類似論文を検索
    query_text = _build_pattern_query_text(pattern)
    try:
        candidates = search_similar_papers(
            query_text=query_text,
            openai_client=client,
            embedding_model=settings.embedding_model,
            top_k=_TOP_K,
            exclude_arxiv_id=pattern.source_arxiv_id,
        )
    except Exception:
        logger.exception("Qdrant search failed for pattern %s", pattern.pattern_id)
        return []

    if not candidates:
        logger.info("No candidate papers found for pattern %s", pattern.pattern_id)
        return []

    logger.info(
        "Found %d candidate papers for pattern %s: %s",
        len(candidates),
        pattern.pattern_id,
        [c["arxiv_id"] for c in candidates],
    )

    # 2–4. 各候補論文に対して同型性を評価
    matches: list[PatternMatch] = []
    for candidate in candidates:
        arxiv_id = candidate["arxiv_id"]

        # 2. PaperStructure をロード
        paper = _load_paper_structure(arxiv_id, storage_client)
        if paper is None:
            continue

        # 3. LLM で同型性を評価
        match = _evaluate_isomorphism(pattern, paper)
        if match is None:
            continue

        # 4. Neo4j に保存
        try:
            _save_match_to_neo4j(match)
            matches.append(match)
        except Exception:
            logger.exception("Failed to save match to Neo4j for %s", arxiv_id)

    logger.info(
        "Pattern evaluation completed for pattern_id=%s: %d matches found",
        pattern.pattern_id, len(matches),
    )
    return matches
