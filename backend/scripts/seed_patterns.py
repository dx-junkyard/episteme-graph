"""Foundation Pattern Importer — コールドスタート解消のためのシード注入スクリプト.

人類が既に確立している強力な抽象化パターンを
``backend/data/foundation_seeds.json`` から読み込み、
LLM で ``AbstractionPattern`` スキーマに変換した後、
Qdrant (ベクトル) と Neo4j (グラフ) に一括登録する。

実行方法
--------
backend/ ディレクトリで::

    python -m scripts.seed_patterns

環境変数
--------
OPENAI_API_KEY, OPENAI_ANALYSIS_MODEL, OPENAI_EMBEDDING_MODEL,
QDRANT_HOST, QDRANT_PORT, NEO4J_URI, NEO4J_AUTH
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from core.db import get_driver
from core.embedder import embed_and_store_pattern
from core.llm import get_client, get_settings
from core.schema import AbstractionPattern

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

_SEEDS_PATH = Path(__file__).resolve().parent.parent / "data" / "foundation_seeds.json"


def _load_seeds(path: Path = _SEEDS_PATH) -> list[dict]:
    """foundation_seeds.json を読み込んで返す."""
    if not path.exists():
        logger.error("Seed file not found: %s", path)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        seeds = json.load(f)
    logger.info("Loaded %d seed concepts from %s", len(seeds), path.name)
    return seeds


def _generate_pattern(seed: dict, client, settings) -> AbstractionPattern:
    """1 つのシードデータを LLM で AbstractionPattern に変換する."""
    prompt = (
        "あなたはメタ構造転写エンジンの一部です。\n"
        "提供された学術的背景と参考文献を元に、分野横断で適用可能な"
        "最高の「型のテンプレート（Abstraction Pattern）」を生成してください。\n\n"
        "【入力データ】\n"
        f"概念名: {seed['concept_name']}\n\n"
        f"コアメカニズム:\n{seed['core_mechanism']}\n\n"
        f"変数: {json.dumps(seed['variables'], ensure_ascii=False)}\n\n"
        f"分野横断の適用例:\n"
        + "\n".join(f"- {ex}" for ex in seed["cross_domain_examples"])
        + "\n\n"
        f"主要参考文献:\n"
        + "\n".join(f"- {ref}" for ref in seed["key_references"])
        + "\n\n"
        "【生成ルール】\n"
        "- name: パターンの簡潔な名称（英語、20語以内）\n"
        "- description: パターンの説明（具体ドメイン用語ではなく抽象変数で記述し、"
        "上記のコアメカニズムと参考文献の知見を統合した高品質な記述にすること）\n"
        "- variables_template: パターン内で使われる抽象変数のリスト"
        "（例: [\"X\", \"Y\", \"Z\"]）。入力の変数を抽象化して定義せよ\n"
        "- structural_rules: 変数間の関係ルール"
        "（例: [\"X inhibits Y\", \"Y enables Z\"]）\n"
        '- source_arxiv_id: "foundation_seed" を設定\n'
        "- smarts_regex: 抽出した構造を汎用的に検索・分類するためのSMILES DSL正規表現（SMARTS形式）を生成してください。"
        "特定のエンティティ名に依存しないよう、適宜 .* のワイルドカードを使用してください。"
        "例: '\\\\[.*:Agent:.*\\\\] ==\\\\[CAUSES:.*\\\\]=>'\n"
        "- unresolved_limitations: このパターンを現行のSMILES DSL（ノード・エッジ表現）で"
        "記述する際に感じた表現の限界があれば、リストとして記述してください。"
        "限界が特にない場合は空リスト [] としてください。\n\n"
        "JSONスキーマに厳格に従って出力してください。"
    )

    resp = client.beta.chat.completions.parse(
        model=settings.analysis_model,
        messages=[{"role": "user", "content": prompt}],
        response_format=AbstractionPattern,
    )
    pattern: AbstractionPattern = resp.choices[0].message.parsed
    return pattern.model_copy(update={"source_arxiv_id": "foundation_seed"})


def _store_to_qdrant(pattern: AbstractionPattern, client, settings) -> None:
    """パターンを Qdrant patterns コレクションに UPSERT する."""
    pattern_text = (
        f"{pattern.name}\n{pattern.description}\n"
        + "\n".join(pattern.structural_rules)
    )
    embed_and_store_pattern(
        pattern_id=pattern.pattern_id,
        pattern_text=pattern_text,
        openai_client=client,
        embedding_model=settings.embedding_model,
    )


def _store_to_neo4j(pattern: AbstractionPattern) -> None:
    """パターンを Neo4j に MERGE（UPSERT）する."""
    driver = get_driver()
    with driver.session() as session:
        session.run(
            """
            MERGE (ap:AbstractionPattern {name: $name})
            ON CREATE SET
                ap.pattern_id              = $pattern_id,
                ap.description             = $description,
                ap.variables_template      = $variables_template,
                ap.structural_rules        = $structural_rules,
                ap.source_arxiv_id         = $source_arxiv_id,
                ap.smarts_regex            = $smarts_regex,
                ap.unresolved_limitations  = $unresolved_limitations
            ON MATCH SET
                ap.pattern_id              = $pattern_id,
                ap.description             = $description,
                ap.variables_template      = $variables_template,
                ap.structural_rules        = $structural_rules,
                ap.source_arxiv_id         = $source_arxiv_id,
                ap.smarts_regex            = $smarts_regex,
                ap.unresolved_limitations  = $unresolved_limitations
            """,
            pattern_id=pattern.pattern_id,
            name=pattern.name,
            description=pattern.description,
            variables_template=pattern.variables_template,
            structural_rules=pattern.structural_rules,
            source_arxiv_id=pattern.source_arxiv_id,
            smarts_regex=pattern.smarts_regex,
            unresolved_limitations=pattern.unresolved_limitations,
        )


def main() -> None:
    """シードデータの読み込み → LLM 変換 → DB 登録のメインフロー."""
    seeds = _load_seeds()
    client = get_client()
    settings = get_settings()

    succeeded = 0
    failed = 0

    for i, seed in enumerate(seeds, 1):
        concept = seed.get("concept_name", f"seed_{i}")
        logger.info("[%d/%d] Processing: %s", i, len(seeds), concept)

        try:
            pattern = _generate_pattern(seed, client, settings)
            logger.info("  -> Generated pattern: %s (id=%s)", pattern.name, pattern.pattern_id)
        except Exception:
            logger.exception("  !! LLM generation failed for: %s", concept)
            failed += 1
            continue

        try:
            _store_to_qdrant(pattern, client, settings)
            logger.info("  -> Stored in Qdrant")
        except Exception:
            logger.exception("  !! Qdrant storage failed for: %s", concept)
            failed += 1
            continue

        try:
            _store_to_neo4j(pattern)
            logger.info("  -> Stored in Neo4j")
        except Exception:
            logger.exception("  !! Neo4j storage failed for: %s", concept)
            failed += 1
            continue

        succeeded += 1

    logger.info(
        "Seed import complete: %d succeeded, %d failed out of %d total",
        succeeded,
        failed,
        len(seeds),
    )

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
