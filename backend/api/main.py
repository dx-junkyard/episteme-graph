"""Episteme Graph — 学習支援APIサーバー。

大学院生の学習プロセスを支援し、文献から抽出した知識をグラフ構造で管理する。
コース管理・RAGチャット・進捗追跡・認証・PDF教材管理を統合的に担当する。

Endpoints
---------
POST /api/auth/register                                         ユーザー登録
POST /api/auth/login                                            ログイン
GET  /api/auth/me                                               現在のユーザー情報
POST /api/learning/courses                                      コースを新規作成
GET  /api/learning/courses                                      コース一覧 (公開テンプレート含む)
GET  /api/learning/courses/{course_id}                          コース詳細
PUT  /api/learning/courses/{course_id}                          コースを更新
DELETE /api/learning/courses/{course_id}                        コースを削除
GET  /api/learning/courses/{course_id}/progress                 進捗データ
POST /api/learning/courses/{course_id}/enroll                   公開コースに受講登録
GET  /api/learning/courses/{cid}/topics/{tid}/chat              チャット履歴
POST /api/learning/courses/{cid}/topics/{tid}/chat              RAGチャット
POST /api/admin/materials/upload                                PDF教材アップロード
GET  /api/admin/materials                                       教材一覧
GET  /api/admin/materials/{material_id}                         教材詳細(グラフ構造)
POST /api/admin/course-builder/sessions                         コース構築セッション作成
GET  /api/admin/course-builder/sessions                         セッション一覧
GET  /api/admin/course-builder/sessions/{session_id}            セッション取得
PUT  /api/admin/course-builder/sessions/{session_id}            セッション更新
POST /api/admin/course-builder/chat                             コース構築AIチャット
PUT  /api/admin/courses/{course_id}/publish                     コースを学生に公開
POST /api/admin/users/student                                   学生アカウント作成 (TEACHER)
POST /api/admin/users/teacher                                   教員アカウント作成 (SYSTEM_ADMIN)
GET  /healthz                                                   ヘルスチェック
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text as sa_text

from dependencies import _hash_password
from routes import auth, learning, admin
from core.config import get_settings as _get_settings
from core.postgres import get_session as _pg_session, check_connection as _pg_check

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


def _run_migrations() -> None:
    """既存DBに対してマイグレーション002-003を適用する。IF NOT EXISTS で冪等。"""
    session = _pg_session()
    try:
        # A1: course_builder_sessions テーブル
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS course_builder_sessions (
                id          TEXT PRIMARY KEY,
                user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title       TEXT NOT NULL DEFAULT '新しいセッション',
                history     JSONB NOT NULL DEFAULT '[]',
                course_draft JSONB,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_cb_sessions_user ON course_builder_sessions(user_id)"
        ))

        # A2: learning_courses への追加カラム
        for ddl in [
            "ALTER TABLE learning_courses ADD COLUMN IF NOT EXISTS is_template  BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE learning_courses ADD COLUMN IF NOT EXISTS is_published BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE learning_courses ADD COLUMN IF NOT EXISTS owner_id     UUID REFERENCES users(id)",
            "ALTER TABLE learning_courses ADD COLUMN IF NOT EXISTS cloned_from  TEXT REFERENCES learning_courses(id)",
        ]:
            session.execute(sa_text(ddl))
        session.execute(sa_text(
            "UPDATE learning_courses SET owner_id = user_id WHERE owner_id IS NULL"
        ))
        session.execute(sa_text("""
            CREATE INDEX IF NOT EXISTS idx_courses_published ON learning_courses(is_published, is_template)
            WHERE is_published = true AND is_template = true
        """))

        # Migration 003: unanswered_query_logs テーブル（つまづきデータ蓄積）
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS unanswered_query_logs (
                id        TEXT PRIMARY KEY,
                user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                course_id TEXT NOT NULL,
                topic_id  TEXT NOT NULL,
                question  TEXT NOT NULL,
                asked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_unanswered_course ON unanswered_query_logs(course_id)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_unanswered_user ON unanswered_query_logs(user_id)"
        ))

        # Migration 004: Schema Evolution (Issue #36)
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS schema_ontology_types (
                id          TEXT PRIMARY KEY,
                label       TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                is_builtin  BOOLEAN NOT NULL DEFAULT false,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS schema_predicates (
                id          TEXT PRIMARY KEY,
                label       TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                is_builtin  BOOLEAN NOT NULL DEFAULT false,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS schema_proposals (
                id          TEXT PRIMARY KEY,
                status      TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'approved', 'rejected')),
                summary     TEXT NOT NULL DEFAULT '',
                reasoning   TEXT NOT NULL DEFAULT '',
                source_query_count INTEGER NOT NULL DEFAULT 0,
                created_by  UUID REFERENCES users(id),
                reviewed_by UUID REFERENCES users(id),
                reviewed_at TIMESTAMPTZ,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_schema_proposals_status ON schema_proposals(status)"
        ))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS schema_proposal_items (
                id          TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL REFERENCES schema_proposals(id) ON DELETE CASCADE,
                item_type   TEXT NOT NULL CHECK (item_type IN ('ontology_type', 'predicate')),
                key         TEXT NOT NULL,
                label       TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_proposal_items_proposal ON schema_proposal_items(proposal_id)"
        ))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS reextraction_jobs (
                id            TEXT PRIMARY KEY,
                proposal_id   TEXT REFERENCES schema_proposals(id),
                status        TEXT NOT NULL DEFAULT 'pending'
                                  CHECK (status IN ('pending', 'running', 'completed', 'failed')),
                total_docs    INTEGER NOT NULL DEFAULT 0,
                processed_docs INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                started_at    TIMESTAMPTZ,
                completed_at  TIMESTAMPTZ,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_reextraction_status ON reextraction_jobs(status)"
        ))

        session.commit()
        logger.info("Migrations (002-004) applied successfully.")

        # Seed builtin schema types/predicates
        from core.schema_registry import seed_builtin_schema
        seed_builtin_schema()
    except Exception as exc:
        session.rollback()
        logger.error("Migration failed: %s", exc)
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(application: FastAPI):
    """起動時にシステム管理者アカウントを初期化する（PostgreSQL）。"""
    _ADMIN_PASSWORD = _get_settings().admin_password
    if not _ADMIN_PASSWORD:
        logger.critical("CRITICAL: ADMIN_PASSWORD is not set in .env.")
        sys.exit(1)

    # PostgreSQL の起動を待つ（最大30秒）
    max_retries = 10
    for attempt in range(max_retries):
        try:
            session = _pg_session()
            try:
                existing = session.execute(
                    sa_text("SELECT id FROM users WHERE display_name = 'Administrator' LIMIT 1")
                ).fetchone()
                if not existing:
                    admin_id = uuid.uuid4()
                    hashed_pw = _hash_password(_ADMIN_PASSWORD)
                    session.execute(
                        sa_text("""
                            INSERT INTO users (id, email, display_name, role, password_hash)
                            VALUES (:id, 'admin@system.local', 'Administrator', 'admin', :pw)
                        """),
                        {"id": admin_id, "pw": hashed_pw},
                    )
                    session.commit()
                    logger.info("Created system administrator account 'Administrator' (id=%s)", admin_id)
                else:
                    logger.info("System administrator account 'Administrator' already exists.")
            finally:
                session.close()
            _run_migrations()
            break
        except Exception as exc:
            if attempt < max_retries - 1:
                wait = 3 * (attempt + 1)
                logger.warning("PostgreSQL not ready (attempt %d/%d): %s. Retrying in %ds...", attempt + 1, max_retries, exc, wait)
                await asyncio.sleep(wait)
            else:
                logger.critical("Failed to connect to PostgreSQL after %d attempts.", max_retries)
                sys.exit(1)
    yield


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="Episteme Graph API", version="0.1.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ルーターのマウント
app.include_router(auth.router)
app.include_router(learning.router)
app.include_router(admin.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "episteme-graph-api"}
