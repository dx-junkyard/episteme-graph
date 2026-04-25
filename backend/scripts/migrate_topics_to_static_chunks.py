"""移行スクリプト: 既存コースのトピックに target_chunk_ids を付与する。

各トピック名でベクトル検索を1回だけ実行し、上位チャンクのIDを
target_chunk_ids として learning_courses テーブルに保存する。

実行方法:
    docker compose exec api-server python -m scripts.migrate_topics_to_static_chunks

オプション:
    --dry-run    DBへの書き込みをスキップして確認のみ行う
    --top-k N    1トピックあたり取得するチャンク数（デフォルト: 3）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from sqlalchemy import text as sa_text

from core.llm import generate_embeddings, get_embedding_dim
from core.postgres import get_session as _pg_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _embed(text: str) -> list[float]:
    return generate_embeddings([text])[0]


def _search_top_chunks(query: str, top_k: int) -> list[str]:
    """クエリに類似するチャンクIDを top_k 件返す。"""
    try:
        vector = _embed(query)
    except Exception as exc:
        logger.warning("Embedding failed for query %r: %s", query[:60], exc)
        return []

    session = _pg_session()
    try:
        dim = get_embedding_dim()
        rows = session.execute(
            sa_text(f"""
                SELECT id::text
                FROM chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding::halfvec({dim}) <=> CAST(:vec AS halfvec({dim}))
                LIMIT :lim
            """),
            {"vec": str(vector), "lim": top_k},
        ).fetchall()
        return [row[0] for row in rows]
    except Exception as exc:
        logger.warning("Vector search failed: %s", exc)
        return []
    finally:
        session.close()


def _fetch_all_courses(session) -> list[dict]:
    rows = session.execute(
        sa_text("SELECT id, data FROM learning_courses ORDER BY created_at ASC")
    ).fetchall()
    return [{"id": str(row[0]), "data": row[1]} for row in rows]


def _update_course_data(session, course_id: str, data: dict) -> None:
    session.execute(
        sa_text("UPDATE learning_courses SET data = :data WHERE id = CAST(:cid AS uuid)"),
        {"data": json.dumps(data, ensure_ascii=False), "cid": course_id},
    )
    session.commit()


def migrate(dry_run: bool = False, top_k: int = 3) -> None:
    session = _pg_session()
    try:
        courses = _fetch_all_courses(session)
    finally:
        session.close()

    logger.info("Found %d courses", len(courses))

    for course in courses:
        course_id = course["id"]
        data = course["data"]
        if isinstance(data, str):
            data = json.loads(data)

        topics = data.get("topics", [])
        updated = False

        for topic in topics:
            existing = topic.get("target_chunk_ids", [])
            if existing:
                logger.debug("Skip topic %r (already has %d chunk IDs)", topic.get("id"), len(existing))
                continue

            query = topic.get("title", "") + " " + " ".join(topic.get("prerequisites", []))
            query = query.strip()
            if not query:
                logger.warning("Topic %r in course %s has no title, skipping", topic.get("id"), course_id)
                continue

            chunk_ids = _search_top_chunks(query, top_k)
            topic["target_chunk_ids"] = chunk_ids
            updated = True
            logger.info(
                "Course %s | topic %r → %d chunk IDs assigned",
                course_id, topic.get("title", topic.get("id")), len(chunk_ids),
            )

        if not updated:
            logger.info("Course %s: no topics needed update", course_id)
            continue

        if dry_run:
            logger.info("[dry-run] Would update course %s", course_id)
        else:
            session = _pg_session()
            try:
                _update_course_data(session, course_id, data)
            finally:
                session.close()
            logger.info("Updated course %s", course_id)

    logger.info("Migration complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="DBへの書き込みをスキップ")
    parser.add_argument("--top-k", type=int, default=3, help="1トピックあたりのチャンク数")
    args = parser.parse_args()

    try:
        migrate(dry_run=args.dry_run, top_k=args.top_k)
    except Exception:
        logger.exception("Migration failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
