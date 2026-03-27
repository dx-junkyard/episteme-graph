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
import datetime
import hashlib
import json
import logging
import os
import sys
import threading
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache

import jwt
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from neo4j import GraphDatabase
from openai import OpenAI
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import text as sa_text

from core.postgres import get_session as _pg_session, check_connection as _pg_check

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (環境変数から読み込み)
# ---------------------------------------------------------------------------

_JWT_SECRET: str = os.environ.get("JWT_SECRET", "episteme-dev-secret-change-in-prod")
_JWT_ALGORITHM: str = "HS256"
_JWT_EXPIRE_HOURS: int = 24

_NEO4J_URI: str = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
_NEO4J_AUTH_STR: str = os.environ.get("NEO4J_AUTH", "neo4j/episteme")

_OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
_OPENAI_ANALYSIS_MODEL: str = os.environ.get("OPENAI_ANALYSIS_MODEL", "gpt-4o")
_OPENAI_EMBEDDING_MODEL: str = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")

_MINIO_ENDPOINT: str = os.environ.get("MINIO_ENDPOINT", "minio:9000")
_MINIO_ACCESS_KEY: str = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
_MINIO_SECRET_KEY: str = os.environ.get("MINIO_SECRET_KEY", "minioadmin")

_ADMIN_PASSWORD: str = os.environ.get("ADMIN_PASSWORD", "")

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------

ROLE_STUDENT = "STUDENT"
ROLE_TEACHER = "TEACHER"
ROLE_SYSTEM_ADMIN = "SYSTEM_ADMIN"

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_bearer = HTTPBearer()


def _run_migrations() -> None:
    """既存DBに対してマイグレーション002（A1/A2/A3）を適用する。IF NOT EXISTS で冪等。"""
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

        session.commit()
        logger.info("Migration 002 (A1/A2/A3) applied successfully.")
    except Exception as exc:
        session.rollback()
        logger.error("Migration 002 failed: %s", exc)
        raise
    finally:
        session.close()


@asynccontextmanager
async def _lifespan(application: FastAPI):
    """起動時にシステム管理者アカウントを初期化する（PostgreSQL）。"""
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


app = FastAPI(title="Episteme Graph API", version="0.1.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@lru_cache(maxsize=1)
def _neo4j_driver():
    user, password = _NEO4J_AUTH_STR.split("/", 1)
    return GraphDatabase.driver(_NEO4J_URI, auth=(user, password))


@lru_cache(maxsize=1)
def _openai() -> OpenAI:
    return OpenAI(api_key=_OPENAI_API_KEY)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def _verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def _create_token(user_id: str, username: str, email: str, role: str = ROLE_STUDENT) -> str:
    expire = datetime.datetime.utcnow() + datetime.timedelta(hours=_JWT_EXPIRE_HOURS)
    payload = {"sub": user_id, "username": username, "email": email, "role": role, "exp": expire}
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def _get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Bearer トークンをデコードしてユーザー情報を返す。"""
    try:
        payload = jwt.decode(
            credentials.credentials, _JWT_SECRET, algorithms=[_JWT_ALGORITHM]
        )
        return {
            "id": payload["sub"],
            "username": payload["username"],
            "email": payload["email"],
            "role": payload.get("role", ROLE_STUDENT),
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _pg_role_to_app_role(pg_role: str) -> str:
    """PostgreSQL の role 値をアプリケーションの role 定数にマッピングする。"""
    mapping = {"learner": ROLE_STUDENT, "instructor": ROLE_TEACHER, "admin": ROLE_SYSTEM_ADMIN}
    return mapping.get(pg_role, ROLE_STUDENT)


def _app_role_to_pg_role(app_role: str) -> str:
    """アプリケーションの role 定数を PostgreSQL の role 値にマッピングする。"""
    mapping = {ROLE_STUDENT: "learner", ROLE_TEACHER: "instructor", ROLE_SYSTEM_ADMIN: "admin"}
    return mapping.get(app_role, "learner")


def _require_teacher(current_user: dict = Depends(_get_current_user)) -> dict:
    """TEACHER または SYSTEM_ADMIN ロールを要求する。"""
    if current_user["role"] not in (ROLE_TEACHER, ROLE_SYSTEM_ADMIN):
        raise HTTPException(status_code=403, detail="Teacher or admin role required")
    return current_user


def _require_system_admin(current_user: dict = Depends(_get_current_user)) -> dict:
    """SYSTEM_ADMIN ロールを要求する。"""
    if current_user["role"] != ROLE_SYSTEM_ADMIN:
        raise HTTPException(status_code=403, detail="System admin role required")
    return current_user


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    username: str
    email: str
    role: str = ROLE_STUDENT


class CreateUserRequest(BaseModel):
    """学生または教員アカウント作成リクエスト。"""
    username: str
    email: str
    password: str


class MaterialOut(BaseModel):
    material_id: str
    filename: str
    title: str
    status: str  # uploaded | processing | completed | failed
    uploaded_at: str
    knowledge_graph: dict | None = None


class LearningPrerequisite(BaseModel):
    name: str
    status: str = "not_started"  # mastered | partial | not_started


class LearningMisconception(BaseModel):
    label: str = ""
    wrong: str
    correct: str


class LearningTopic(BaseModel):
    id: str
    title: str
    chapter_index: int
    status: str = "locked"  # completed | in_progress | locked
    prerequisites: list[LearningPrerequisite] = []
    misconceptions: list[LearningMisconception] = []


class LearningChapter(BaseModel):
    title: str
    status: str = "locked"  # completed | in_progress | locked
    progress_pct: int = 0


class LearningConcept(BaseModel):
    name: str
    status: str = "future"  # mastered | learning | future
    children: list[str] = []
    expanded: bool = False


class LearningSource(BaseModel):
    title: str
    subtitle: str = ""
    license: str = ""
    used_section: str = ""
    arxiv_id: str = ""  # 論文IDと紐付ける場合
    material_id: str = ""  # アップロード教材と紐付ける場合


class LearningReferencedSection(BaseModel):
    source: str
    section: str
    title: str
    note: str = ""


class CourseCreateRequest(BaseModel):
    """コース新規作成リクエスト。"""

    title: str
    chapters: list[LearningChapter] = []
    topics: list[LearningTopic] = []
    concepts: list[LearningConcept] = []
    sources: list[LearningSource] = []
    is_template: bool = False  # Trueの場合、教員作成テンプレートとして扱う


class CourseUpdateRequest(BaseModel):
    """コース更新リクエスト。部分更新に対応。"""

    title: str | None = None
    chapters: list[LearningChapter] | None = None
    topics: list[LearningTopic] | None = None
    concepts: list[LearningConcept] | None = None
    sources: list[LearningSource] | None = None


class LearningCourseOut(BaseModel):
    id: str
    title: str
    is_template: bool = False
    is_published: bool = False
    is_enrollable: bool = False  # True: 自分未登録の公開テンプレート


class LearningCourseDetail(BaseModel):
    id: str
    title: str
    chapters: list[LearningChapter] = []
    topics: list[LearningTopic] = []
    concepts: list[LearningConcept] = []
    sources: list[LearningSource] = []
    referenced_sections: list[LearningReferencedSection] = []
    progress: dict | None = None


class LearningSession(BaseModel):
    date: str
    topic: str
    duration: str


class LearningProgress(BaseModel):
    mastered_concepts: int = 0
    learning_concepts: int = 0
    misconceptions: int = 0
    streak_days: int = 0
    sessions: list[LearningSession] = []


class LearningChatRequest(BaseModel):
    message: str
    history: list[dict] = []


class LearningChatResponse(BaseModel):
    answer: str
    course_update: dict | None = None


class LearningChatHistoryResponse(BaseModel):
    history: list[dict]


class CourseBuilderChatRequest(BaseModel):
    """コース構築AIチャットリクエスト。"""
    message: str
    history: list[dict] = []
    session_id: str | None = None


class CourseBuilderChatResponse(BaseModel):
    """コース構築AIチャットレスポンス。"""
    answer: str
    course_draft: dict | None = None


class CourseBuilderSessionOut(BaseModel):
    """コース構築セッション情報。"""
    session_id: str
    title: str
    created_at: str
    updated_at: str


class CourseBuilderSessionCreate(BaseModel):
    title: str = "新しいセッション"


class CourseBuilderSessionUpdate(BaseModel):
    title: str | None = None
    history: list[dict] | None = None
    course_draft: dict | None = None


# ---------------------------------------------------------------------------
# Neo4j helpers
# ---------------------------------------------------------------------------

def _get_course_data(user_id: str, course_id: str) -> dict | None:
    """PostgreSQL から LearningCourse データを取得する。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT data FROM learning_courses
                WHERE user_id = CAST(:user_id AS uuid) AND id = :course_id
                LIMIT 1
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchone()
        if record and record[0]:
            return record[0] if isinstance(record[0], dict) else json.loads(record[0])
    finally:
        session.close()
    return None


def _save_course_data(user_id: str, course_id: str, data: dict, is_template: bool = False) -> None:
    """LearningCourse データを PostgreSQL に UPSERT する。

    is_template は初回作成時のみ設定される（更新では変更しない）。
    """
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO learning_courses (id, user_id, title, data, is_template, is_published, owner_id)
                VALUES (
                    :course_id,
                    CAST(:user_id AS uuid),
                    :title,
                    CAST(:data AS jsonb),
                    :is_template,
                    false,
                    CAST(:user_id AS uuid)
                )
                ON CONFLICT (id) DO UPDATE
                SET data = CAST(EXCLUDED.data AS jsonb),
                    title = EXCLUDED.title,
                    updated_at = now()
            """),
            {
                "course_id": course_id,
                "user_id": user_id,
                "title": data.get("title", course_id),
                "data": json.dumps(data, ensure_ascii=False),
                "is_template": is_template,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _delete_course_data(user_id: str, course_id: str) -> bool:
    """LearningCourse レコードを削除する。"""
    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                DELETE FROM learning_courses
                WHERE id = :course_id AND user_id = CAST(:user_id AS uuid)
                RETURNING id
            """),
            {"course_id": course_id, "user_id": user_id},
        ).fetchone()
        session.commit()
        return result is not None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# RAG helpers (Qdrant + OpenAI Embedding)
# ---------------------------------------------------------------------------

def _embed_text(text: str) -> list[float]:
    """テキストを embedding ベクトルに変換する。"""
    client = _openai()
    resp = client.embeddings.create(model=_OPENAI_EMBEDDING_MODEL, input=[text])
    return resp.data[0].embedding


def _search_relevant_chunks(
    query: str,
    arxiv_ids: list[str],
    top_k: int = 5,
) -> list[str]:
    """PostgreSQL pgvector から関連チャンクをベクトル検索する。"""
    if not arxiv_ids:
        return []

    try:
        query_vector = _embed_text(query)
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return []

    try:
        session = _pg_session()
        try:
            # プレースホルダ配列を構築
            placeholders = ", ".join(f":aid_{i}" for i in range(len(arxiv_ids)))
            params: dict = {"query_vector": str(query_vector), "limit": top_k}
            for i, aid in enumerate(arxiv_ids):
                params[f"aid_{i}"] = aid

            rows = session.execute(
                sa_text(f"""
                    SELECT c.text
                    FROM chunks c
                    WHERE c.arxiv_id IN ({placeholders})
                      AND c.embedding IS NOT NULL
                    ORDER BY c.embedding::halfvec(3072) <=> CAST(:query_vector AS halfvec(3072))
                    LIMIT :limit
                """),
                params,
            ).fetchall()
            return [row[0] for row in rows if row[0]]
        finally:
            session.close()
    except Exception as exc:
        logger.warning("pgvector search failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Progress calculation
# ---------------------------------------------------------------------------

def _calculate_progress(user_id: str, course_id: str, course_data: dict) -> dict:
    """コースデータとチャット履歴から進捗を計算する。"""
    topics = course_data.get("topics", [])
    concepts = course_data.get("concepts", [])

    mastered = sum(1 for c in concepts if c.get("status") == "mastered")
    learning = sum(1 for c in concepts if c.get("status") == "learning")

    total_misconceptions = 0
    for t in topics:
        total_misconceptions += len(t.get("misconceptions", []))

    # セッション履歴を PostgreSQL のチャット履歴から構築
    sessions_list = []
    pg_session = _pg_session()
    try:
        records = pg_session.execute(
            sa_text("""
                SELECT topic_id, history, updated_at
                FROM learning_chat_history
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                ORDER BY updated_at DESC
                LIMIT 10
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchall()
    finally:
        pg_session.close()

    for r in records:
        topic_id_val = r[0]
        history = r[1] if isinstance(r[1], list) else []

        # トピック名を取得
        topic_name = topic_id_val
        for t in topics:
            if t.get("id") == topic_id_val:
                topic_name = t.get("title", topic_name)
                break

        msg_count = len(history)
        duration_min = max(5, msg_count * 2)

        date_str = ""
        if r[2]:
            try:
                dt = r[2]
                date_str = f"{dt.month}/{dt.day}"
            except Exception:
                pass

        sessions_list.append({
            "date": date_str or "---",
            "topic": topic_name,
            "duration": f"{duration_min}分",
        })

    streak = _calculate_streak(user_id, course_id)

    return {
        "mastered_concepts": mastered,
        "learning_concepts": learning,
        "misconceptions": total_misconceptions,
        "streak_days": streak,
        "sessions": sessions_list[:5],
    }


def _calculate_streak(user_id: str, course_id: str) -> int:
    """チャット履歴の日付から連続学習日数を算出する。"""
    pg_session = _pg_session()
    try:
        records = pg_session.execute(
            sa_text("""
                SELECT DISTINCT DATE(updated_at) AS d
                FROM learning_chat_history
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                ORDER BY d DESC
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchall()
    finally:
        pg_session.close()

    if not records:
        return 0

    sorted_dates = [r[0] for r in records]
    today = datetime.date.today()

    if sorted_dates[0] < today - datetime.timedelta(days=1):
        return 0

    streak = 1
    for i in range(1, len(sorted_dates)):
        if sorted_dates[i] == sorted_dates[i - 1] - datetime.timedelta(days=1):
            streak += 1
        else:
            break

    return streak


# ---------------------------------------------------------------------------
# Course CRUD endpoints
# ---------------------------------------------------------------------------

@app.post("/api/learning/courses", response_model=LearningCourseOut, status_code=201)
def create_course(
    body: CourseCreateRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseOut:
    """新しいコースを作成する。

    JSON で章構成・トピック・概念マップ・教材リストを登録する。
    """
    import uuid
    course_id = str(uuid.uuid4())[:8]

    data = {
        "id": course_id,
        "title": body.title,
        "chapters": [ch.model_dump() for ch in body.chapters],
        "topics": [t.model_dump() for t in body.topics],
        "concepts": [c.model_dump() for c in body.concepts],
        "sources": [s.model_dump() for s in body.sources],
        "referenced_sections": [],
    }

    _save_course_data(current_user["id"], course_id, data, is_template=body.is_template)

    logger.info("Created course '%s' (id=%s) for user=%s", body.title, course_id, current_user["id"])
    return LearningCourseOut(id=course_id, title=body.title, is_template=body.is_template)


@app.get("/api/learning/courses", response_model=list[LearningCourseOut])
def list_courses(
    current_user: dict = Depends(_get_current_user),
) -> list[LearningCourseOut]:
    """ユーザーが登録しているコース一覧を返す。公開テンプレートも含む。"""
    session = _pg_session()
    try:
        # 自分が所有するコース
        own_records = session.execute(
            sa_text("""
                SELECT id, title,
                       COALESCE(is_template, false) AS is_template,
                       COALESCE(is_published, false) AS is_published
                FROM learning_courses
                WHERE user_id = CAST(:user_id AS uuid)
            """),
            {"user_id": current_user["id"]},
        ).fetchall()

        # 公開テンプレート（自分が未受講 = まだ cloned_from でこのIDを参照していない）
        template_records = session.execute(
            sa_text("""
                SELECT lc.id, lc.title
                FROM learning_courses lc
                WHERE lc.is_published = true AND lc.is_template = true
                  AND lc.user_id != CAST(:user_id AS uuid)
                  AND NOT EXISTS (
                      SELECT 1 FROM learning_courses lc2
                      WHERE lc2.user_id = CAST(:user_id AS uuid)
                        AND lc2.cloned_from = lc.id
                  )
            """),
            {"user_id": current_user["id"]},
        ).fetchall()
    finally:
        session.close()

    courses = [
        LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=bool(r[2]),
            is_published=bool(r[3]),
            is_enrollable=False,
        )
        for r in own_records
    ]
    courses.extend(
        LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=True,
            is_published=True,
            is_enrollable=True,
        )
        for r in template_records
    )
    return courses


@app.get("/api/learning/courses/{course_id}", response_model=LearningCourseDetail)
def get_course(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseDetail:
    """コースの詳細データを返す。"""
    data = _get_course_data(current_user["id"], course_id)
    if not data:
        raise HTTPException(status_code=404, detail="Course not found")

    return LearningCourseDetail(**data)


@app.put("/api/learning/courses/{course_id}", response_model=LearningCourseDetail)
def update_course(
    course_id: str,
    body: CourseUpdateRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseDetail:
    """コースを部分更新する。指定されたフィールドのみ上書き。"""
    data = _get_course_data(current_user["id"], course_id)
    if not data:
        raise HTTPException(status_code=404, detail="Course not found")

    if body.title is not None:
        data["title"] = body.title
    if body.chapters is not None:
        data["chapters"] = [ch.model_dump() for ch in body.chapters]
    if body.topics is not None:
        data["topics"] = [t.model_dump() for t in body.topics]
    if body.concepts is not None:
        data["concepts"] = [c.model_dump() for c in body.concepts]
    if body.sources is not None:
        data["sources"] = [s.model_dump() for s in body.sources]

    _save_course_data(current_user["id"], course_id, data)
    logger.info("Updated course %s for user=%s", course_id, current_user["id"])

    return LearningCourseDetail(**data)


@app.delete("/api/learning/courses/{course_id}", status_code=204)
def delete_course(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> None:
    """コースを削除する。"""
    deleted = _delete_course_data(current_user["id"], course_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Course not found")

    logger.info("Deleted course %s for user=%s", course_id, current_user["id"])


# ---------------------------------------------------------------------------
# Progress endpoint
# ---------------------------------------------------------------------------

@app.get("/api/learning/courses/{course_id}/progress", response_model=LearningProgress)
def get_progress(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningProgress:
    """コースの進捗データを計算して返す。"""
    data = _get_course_data(current_user["id"], course_id)
    if not data:
        raise HTTPException(status_code=404, detail="Course not found")

    progress = _calculate_progress(current_user["id"], course_id, data)
    return LearningProgress(**progress)


# ---------------------------------------------------------------------------
# Chat endpoints (RAG統合)
# ---------------------------------------------------------------------------

@app.get(
    "/api/learning/courses/{course_id}/topics/{topic_id}/chat",
    response_model=LearningChatHistoryResponse,
)
def get_chat_history(
    course_id: str,
    topic_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningChatHistoryResponse:
    """トピックのチャット履歴を返す。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT history FROM learning_chat_history
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id AND topic_id = :topic_id
                LIMIT 1
            """),
            {"user_id": current_user["id"], "course_id": course_id, "topic_id": topic_id},
        ).fetchone()
    finally:
        session.close()

    if not record or not record[0]:
        return LearningChatHistoryResponse(history=[])

    history = record[0] if isinstance(record[0], list) else []
    return LearningChatHistoryResponse(history=history)


_LEARNING_SYSTEM_PROMPT = """あなたは素粒子物理学・場の量子論を専門とする学習者の深い理解を支援する家庭教師です。
以下の原則に従ってください。

**教育方針:**
1. 学生の誤解を発見したら「訂正：」と明記し、**誤謬の構造的理由**を必ず含めてください。
   - 「数式のこの項を見落としがちですが…」のように、数式レベルで誤解の原因を指摘
   - 「〇〇と△△を混同しやすいですが…」のように、類似概念との混同パターンを説明
   - 正答だけでなく、なぜ間違えやすいかの構造を必ず示す
2. 概念の説明は具体的な数式（LaTeX）、ファインマン図の説明、または物理的直観を使って行ってください。
3. 教材から引用できる場合は出典（セクション番号等）を明記してください。
4. 説明の最後に、理解を確認するための質問をしてください。
5. 関連する概念へのドリルダウン選択肢を提示してください。

**数式の導出サポート:**
- 数式の行間（導出）に関する質問には、前提となる数学的公式・定理を最初に提示してください。
- ステップ・バイ・ステップで、各変形の物理的意味を添えて説明してください。
- 例: 「ここで部分積分を使い、表面項が消えることを仮定すると…」

**RAGコンテキスト利用:**
- 提供される「教材チャンク」はシステム全域のベクトル検索で取得した関連箇所です。各チャンクには `[出典: 『書籍名』]` の形式で出典が付いています。
- 回答中で教材を参照する際は必ず「『書籍名』によれば…」「『書籍名』では…と述べられています」のように出典を明記してください。
- 複数の教材から情報を統合する場合は、それぞれの出典を区別して示してください。
- コンテキストに含まれない情報について推測する場合はその旨を明記してください。

**フォーマット:**
- 数式は必ず LaTeX 記法で記述（インラインは `$...$`、ディスプレイは `$$...$$`）
- 誤解の訂正が必要な場合は最初に「訂正：」と記述
- 参照した教材のセクションがあれば言及
- 回答の末尾に深掘りできるトピックを `[〇〇について詳しく聞く]` の形式で提示（必ずこのフォーマットを使用）"""


_GUIDANCE_SYSTEM_PROMPT = """あなたは素粒子物理学・場の量子論を専門とする学習者の深い理解を支援する家庭教師です。
現在、教材テキストの検索結果がありません。しかし、コースの構造情報（章構成、トピック一覧、概念マップ、登録教材）は利用可能です。
以下の方針で回答してください：
1. コースの構造情報を使って、学習の道筋やトピックの概要を案内してください。
2. 「何から学べばよいか」「このトピックの概要は？」といったメタ的な質問には、コースの章順序・トピック依存関係に基づいて具体的に案内してください。
3. 数式の詳細な導出や教材の具体的な内容についての質問には、「この点は教材を参照する必要があります」と正直に伝え、どの章やトピックを参照すべきか示してください。
4. 説明の最後に、関連する概念へのドリルダウン選択肢を `[〇〇について詳しく聞く]` の形式で提示してください。
**フォーマット:**
- 数式は必ず LaTeX 記法で記述（インラインは `$...$`、ディスプレイは `$$...$$`）
- 回答の末尾に深掘りできるトピックを `[〇〇について詳しく聞く]` の形式で提示"""


def _generate_guidance_without_rag(
    course_data: dict,
    topic_title: str,
    user_message: str,
    history: list[dict],
) -> str:
    """RAGチャンクが見つからない場合に、コース構造情報を使ってLLMに学習ガイダンスを生成させる。"""
    course_title = course_data.get("title", "")
    chapters = course_data.get("chapters", [])
    topics = course_data.get("topics", [])
    concepts = course_data.get("concepts", [])
    sources = course_data.get("sources", [])

    structure_parts: list[str] = []

    # 章構成
    if chapters:
        chapter_lines = []
        for i, ch in enumerate(chapters):
            chapter_topics = [t for t in topics if t.get("chapter_index") == i]
            topic_names = ", ".join(t.get("title", "") for t in chapter_topics)
            status = ch.get("status", "locked")
            chapter_lines.append(f"  第{i+1}章: {ch.get('title', '')} (状態: {status})")
            if topic_names:
                chapter_lines.append(f"    トピック: {topic_names}")
        structure_parts.append("## コースの章構成\n" + "\n".join(chapter_lines))

    # 概念マップ
    if concepts:
        concept_lines = []
        for c in concepts:
            name = c.get("name", "")
            status = c.get("status", "future")
            children = c.get("children", [])
            line = f"  - {name} (状態: {status})"
            if children:
                line += f" → 子概念: {', '.join(children)}"
            concept_lines.append(line)
        structure_parts.append("## 概念マップ\n" + "\n".join(concept_lines))

    # 登録教材
    if sources:
        source_lines = []
        for s in sources:
            title = s.get("title", "")
            subtitle = s.get("subtitle", "")
            info = title
            if subtitle:
                info += f" — {subtitle}"
            source_lines.append(f"  - {info}")
        structure_parts.append("## 登録済み教材\n" + "\n".join(source_lines))

    # 現在のトピックの前後関係
    current_topic_info = None
    prev_topic = None
    next_topic = None
    for i, t in enumerate(topics):
        if t.get("title") == topic_title or t.get("id") == topic_title:
            current_topic_info = t
            if i > 0:
                prev_topic = topics[i - 1]
            if i < len(topics) - 1:
                next_topic = topics[i + 1]
            break

    if current_topic_info:
        nav_info = f"## 現在のトピック: {topic_title}\n"
        if prev_topic:
            nav_info += f"  前のトピック: {prev_topic.get('title', '')}\n"
        if next_topic:
            nav_info += f"  次のトピック: {next_topic.get('title', '')}\n"
        prereqs = current_topic_info.get("prerequisites", [])
        if prereqs:
            nav_info += f"  前提知識: {', '.join(p.get('name', '') for p in prereqs)}\n"
        structure_parts.append(nav_info)

    context_block = "\n\n".join(structure_parts) if structure_parts else "(コース構造情報なし)"

    messages: list[dict] = [
        {"role": "system", "content": _GUIDANCE_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"コース: {course_title}\n"
            f"現在のトピック: {topic_title}\n\n"
            f"{context_block}\n\n"
            "上記のコース構造情報を使って質問に回答してください。\n"
            "教材の具体的なテキストは現在利用できませんが、コースの構成に基づいて学習の案内ができます。"
        )},
        {"role": "assistant", "content": (
            f"了解しました。「{topic_title}」について、コースの構成情報を参照しながらご案内します。"
        )},
    ]
    for turn in history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_message})

    try:
        client = _openai()
        response = client.chat.completions.create(
            model=_OPENAI_ANALYSIS_MODEL,
            messages=messages,
            temperature=0.4,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        logger.exception("Guidance generation failed for topic %s", topic_title)
        first_topic = topics[0].get("title", "") if topics else "最初のトピック"
        return (
            f"「{topic_title}」の学習を始めましょう。\n\n"
            f"このコース「{course_title}」では以下の順序で学習を進めることをお勧めします：\n\n"
            + "\n".join(
                f"**第{i+1}章: {ch.get('title', '')}**"
                for i, ch in enumerate(chapters)
            )
            + f"\n\nまずは最初のトピック「{first_topic}」から始めてみましょう。\n\n"
            + f"[{first_topic}について詳しく聞く]"
        )


@app.post(
    "/api/learning/courses/{course_id}/topics/{topic_id}/chat",
    response_model=LearningChatResponse,
)
def learning_chat(
    course_id: str,
    topic_id: str,
    body: LearningChatRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningChatResponse:
    """RAG統合された学習チャットエンドポイント。

    1. コースに紐づいた論文の arxiv_id を収集
    2. Qdrant でユーザーの質問に関連するチャンクをベクトル検索
    3. チャンク + コース情報 + 履歴をプロンプトに組み込んで LLM に回答生成
    4. 応答から誤解検出を行い、コースデータを更新
    """
    # 1. コースデータを取得
    course_data = _get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    # トピック情報を取得
    topic_info = None
    for t in course_data.get("topics", []):
        if t.get("id") == topic_id:
            topic_info = t
            break
    topic_title = topic_info["title"] if topic_info else topic_id

    # 2. Adaptive Routing: 前提知識の自動判定
    # Neo4j から現在のトピックの REQUIRES エッジ（前提知識）を検索
    prerequisite_intervention = _check_prerequisites(
        current_user["id"], course_id, course_data, topic_title, body.message
    )
    if prerequisite_intervention:
        # 未習得の前提知識がある場合、LLMをスキップして逆質問を返す
        _persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, prerequisite_intervention,
        )
        return LearningChatResponse(answer=prerequisite_intervention, course_update=None)

    # 3. RAG: システム全域のチャンクを検索（出典メタデータ付き）
    _RELEVANCE_THRESHOLD = 0.35
    chunk_results = _search_chunks_with_metadata(body.message, top_k=8)

    # 3a. フェイルセーフ: 閾値以上のチャンクが0件の場合はLLMをスキップ
    above_threshold = [r for r in chunk_results if r["score"] >= _RELEVANCE_THRESHOLD]
    if not above_threshold:
        guidance_answer = _generate_guidance_without_rag(
            course_data, topic_title, body.message, body.history,
        )
        _persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, guidance_answer,
        )
        return LearningChatResponse(answer=guidance_answer, course_update=None)

    # 出典情報を付けてコンテキストブロックを構築
    cited_chunks = []
    for r in above_threshold:
        cited_chunks.append(f"[出典: 『{r['source_title']}』]\n{r['text']}")

    context_parts: list[str] = [
        "## 教材から検索された関連箇所（出典付き）\n" + "\n---\n".join(cited_chunks)
    ]

    context_block = "\n\n".join(context_parts)

    # 5. LLM メッセージ構築
    course_title = course_data.get("title", course_id)
    messages: list[dict] = [
        {"role": "system", "content": _LEARNING_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"コース: {course_title}\n"
            f"現在のトピック: {topic_title}\n\n"
            f"{context_block}\n\n"
            "上記のコンテキストを念頭に置いて質問に回答してください。"
        )},
        {"role": "assistant", "content": (
            f"了解しました。「{topic_title}」について、教材を参照しながら学習を進めましょう。"
        )},
    ]

    for turn in body.history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": body.message})

    # 6. LLM 呼び出し
    try:
        client = _openai()
        response = client.chat.completions.create(
            model=_OPENAI_ANALYSIS_MODEL,
            messages=messages,
            temperature=0.3,
        )
        answer = response.choices[0].message.content or ""
    except Exception as exc:
        logger.exception("Learning chat LLM call failed for topic %s", topic_id)
        raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc

    # 7. 誤解検出: LLMの応答に「訂正」が含まれていたらコースデータに追記
    course_update = None
    if topic_info and any(kw in answer for kw in ["訂正：", "訂正:", "【訂正】"]):
        course_update = _detect_and_record_misconception(
            current_user["id"], course_id, course_data, topic_id, body.message, answer
        )

    # 8. チャット履歴を永続化
    _persist_chat_history(
        current_user["id"], course_id, topic_id,
        body.history, body.message, answer,
    )

    return LearningChatResponse(answer=answer, course_update=course_update)


# ---------------------------------------------------------------------------
# Adaptive Routing: 前提知識チェック (Task 2)
# ---------------------------------------------------------------------------

def _check_prerequisites(
    user_id: str,
    course_id: str,
    course_data: dict,
    topic_title: str,
    user_message: str,
) -> str | None:
    """コースデータの prerequisites フィールドを基に前提知識を確認し、未習得なら逆質問を返す。

    「理解している」旨のメッセージが含まれている場合はスキップ。
    チャット履歴の有無で習得状態を判定する。

    Returns None if no intervention needed, otherwise returns the intervention message.
    """
    # ユーザーが理解済みを示すキーワードがあればスキップ
    skip_keywords = ["理解", "わかります", "わかっています", "知っています", "できます", "学習済み"]
    if any(kw in user_message for kw in skip_keywords):
        return None

    try:
        # 現在のトピック情報をコースデータから取得
        current_topic = None
        for t in course_data.get("topics", []):
            if t.get("title") == topic_title or t.get("id") == topic_title:
                current_topic = t
                break

        if not current_topic:
            return None

        prereqs = current_topic.get("prerequisites", [])
        if not prereqs:
            return None

        # このコースでチャット履歴があるトピックIDを取得 (PostgreSQL)
        pg = _pg_session()
        try:
            rows = pg.execute(
                sa_text("""
                    SELECT topic_id FROM learning_chat_history
                    WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                """),
                {"user_id": user_id, "course_id": course_id},
            ).fetchall()
        finally:
            pg.close()
        topics_with_history: set[str] = {r[0] for r in rows}

        # トピックタイトル → topic_id のマッピング
        title_to_id: dict[str, str] = {}
        for t in course_data.get("topics", []):
            title = t.get("title", "").lower().strip()
            if title:
                title_to_id[title] = t.get("id", "")

        # 未習得の前提知識を判定
        unlearned: list[str] = []
        for prereq in prereqs:
            prereq_name = prereq.get("name", "") if isinstance(prereq, dict) else str(prereq)
            prereq_name = prereq_name.strip()
            if not prereq_name:
                continue
            prereq_topic_id = title_to_id.get(prereq_name.lower(), "")
            if prereq_topic_id and prereq_topic_id in topics_with_history:
                continue  # チャット履歴あり → 習得済みとみなす
            unlearned.append(prereq_name)

        if not unlearned:
            return None

        prereq_list = "、".join(unlearned[:3])
        return (
            f"「{topic_title}」を理解するには、まず以下の前提知識を押さえる必要があります：\n\n"
            f"**{prereq_list}**\n\n"
            f"これらの概念については理解していますか？\n"
            f"理解している場合はその旨を伝えてください。そうでなければ、前提知識から順に説明します。\n\n"
            + "".join(f"[{p}について詳しく聞く]" for p in unlearned[:3])
        )
    except Exception:
        logger.warning("Prerequisite check failed, continuing without intervention", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Chat history persistence helper
# ---------------------------------------------------------------------------

def _persist_chat_history(
    user_id: str,
    course_id: str,
    topic_id: str,
    history: list[dict],
    user_message: str,
    assistant_answer: str,
) -> None:
    """チャット履歴を PostgreSQL に永続化する。"""
    updated_history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_answer},
    ]
    try:
        session = _pg_session()
        try:
            session.execute(
                sa_text("""
                    INSERT INTO learning_chat_history (user_id, course_id, topic_id, history, updated_at)
                    VALUES (CAST(:user_id AS uuid), :course_id, :topic_id, CAST(:history AS jsonb), now())
                    ON CONFLICT (user_id, course_id, topic_id)
                    DO UPDATE SET history = CAST(:history AS jsonb), updated_at = now()
                """),
                {
                    "user_id": user_id,
                    "course_id": course_id,
                    "topic_id": topic_id,
                    "history": json.dumps(updated_history, ensure_ascii=False),
                },
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    except Exception:
        logger.exception(
            "Failed to persist learning chat for user=%s topic=%s",
            user_id, topic_id,
        )


# ---------------------------------------------------------------------------
# RAG search with scores (Task 3: failsafe threshold)
# ---------------------------------------------------------------------------

def _search_relevant_chunks_with_scores(
    query: str,
    arxiv_ids: list[str],
    material_ids: list[str] | None = None,
    top_k: int = 5,
) -> tuple[list[str], list[float]]:
    """PostgreSQL pgvector から関連チャンクをベクトル検索し、テキストとスコアの両方を返す。"""
    if not arxiv_ids and not material_ids:
        return [], []

    try:
        query_vector = _embed_text(query)
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return [], []

    try:
        session = _pg_session()
        try:
            # OR条件を構築: arxiv_id IN (...) OR material_id IN (...)
            conditions = []
            params: dict = {"query_vector": str(query_vector), "limit": top_k}

            if arxiv_ids:
                aid_placeholders = ", ".join(f":aid_{i}" for i in range(len(arxiv_ids)))
                conditions.append(f"c.arxiv_id IN ({aid_placeholders})")
                for i, aid in enumerate(arxiv_ids):
                    params[f"aid_{i}"] = aid

            if material_ids:
                mid_placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
                conditions.append(f"c.material_id IN ({mid_placeholders})")
                for i, mid in enumerate(material_ids):
                    params[f"mid_{i}"] = mid

            where_clause = " OR ".join(conditions) if conditions else "TRUE"

            rows = session.execute(
                sa_text(f"""
                    SELECT c.text,
                           1 - (c.embedding::halfvec(3072) <=> CAST(:query_vector AS halfvec(3072))) AS score
                    FROM chunks c
                    WHERE ({where_clause})
                      AND c.embedding IS NOT NULL
                    ORDER BY c.embedding::halfvec(3072) <=> CAST(:query_vector AS halfvec(3072))
                    LIMIT :limit
                """),
                params,
            ).fetchall()

            texts = [row[0] for row in rows if row[0]]
            scores = [float(row[1]) for row in rows]
            return texts, scores
        finally:
            session.close()
    except Exception as exc:
        logger.warning("pgvector search failed: %s", exc)
        return [], []


# ---------------------------------------------------------------------------
# System-wide RAG search with source metadata
# ---------------------------------------------------------------------------

def _search_chunks_with_metadata(
    query: str,
    top_k: int = 8,
) -> list[dict]:
    """システム全域の chunks をベクトル検索し、出典情報（ドキュメントタイトル・ファイル名）を付けて返す。

    Returns:
        list of {"text": str, "source_title": str, "source_file": str, "score": float}
    """
    try:
        query_vector = _embed_text(query)
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return []

    try:
        session = _pg_session()
        try:
            rows = session.execute(
                sa_text("""
                    SELECT c.text,
                           COALESCE(d.title, '') AS source_title,
                           COALESCE(d.filename, '') AS source_file,
                           1 - (c.embedding::halfvec(3072) <=> CAST(:query_vector AS halfvec(3072))) AS score
                    FROM chunks c
                    LEFT JOIN documents d ON c.document_id = d.id
                    WHERE c.embedding IS NOT NULL
                    ORDER BY c.embedding::halfvec(3072) <=> CAST(:query_vector AS halfvec(3072))
                    LIMIT :limit
                """),
                {"query_vector": str(query_vector), "limit": top_k},
            ).fetchall()
            return [
                {
                    "text": row[0],
                    "source_title": row[1] or row[2] or "不明な教材",
                    "source_file": row[2],
                    "score": float(row[3]),
                }
                for row in rows
                if row[0]
            ]
        finally:
            session.close()
    except Exception as exc:
        logger.warning("System-wide pgvector search failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Misconception detection
# ---------------------------------------------------------------------------

def _detect_and_record_misconception(
    user_id: str,
    course_id: str,
    course_data: dict,
    topic_id: str,
    user_message: str,
    ai_response: str,
) -> dict | None:
    """AI応答から誤解を検出し、コースデータに記録する。

    応答に「訂正」が含まれている場合、ユーザーの発言を「誤解」、
    AIの訂正内容を「正しい理解」として記録する。
    """
    # ユーザーの発言を短縮して誤解ラベルにする
    wrong = user_message
    if len(wrong) > 60:
        wrong = wrong[:60] + "…"

    # 訂正部分を抽出（「訂正：」以降の最初の段落）
    correct = ""
    _CORRECTION_MARKERS = ["訂正：", "訂正:", "【訂正】"]
    for line in ai_response.split("\n"):
        matched_marker = next((m for m in _CORRECTION_MARKERS if m in line), None)
        if matched_marker:
            correct = line.split(matched_marker, 1)[1].strip()
            break

    if not correct:
        correct = "（AIの応答を参照してください）"

    today = datetime.date.today()
    misconception = {
        "label": f"{today.month}/{today.day} の訂正",
        "wrong": wrong,
        "correct": correct,
    }

    # コースデータのトピックに誤解を追記
    for t in course_data.get("topics", []):
        if t.get("id") == topic_id:
            if "misconceptions" not in t:
                t["misconceptions"] = []
            t["misconceptions"].insert(0, misconception)
            # 最新5件のみ保持
            t["misconceptions"] = t["misconceptions"][:5]
            break

    _save_course_data(user_id, course_id, course_data)

    # フロントエンドに更新を通知
    return {
        "topics": course_data.get("topics", []),
        "concepts": course_data.get("concepts", []),
    }


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/api/auth/register", response_model=TokenResponse, status_code=201)
def auth_register(body: RegisterRequest) -> TokenResponse:
    """新規ユーザーを PostgreSQL に登録し、JWT を返す。デフォルトは learner ロール。"""
    session = _pg_session()
    try:
        existing = session.execute(
            sa_text("SELECT id FROM users WHERE display_name = :username LIMIT 1"),
            {"username": body.username},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already taken")

        user_id = uuid.uuid4()
        hashed_pw = _hash_password(body.password)
        session.execute(
            sa_text("""
                INSERT INTO users (id, email, display_name, role, password_hash)
                VALUES (:id, :email, :username, 'learner', :pw)
            """),
            {"id": user_id, "email": body.email, "username": body.username, "pw": hashed_pw},
        )
        session.commit()
    finally:
        session.close()

    logger.info("Registered new user '%s' (id=%s, role=learner)", body.username, user_id)
    return TokenResponse(access_token=_create_token(str(user_id), body.username, body.email, ROLE_STUDENT))


@app.post("/api/auth/login", response_model=TokenResponse)
def auth_login(body: LoginRequest) -> TokenResponse:
    """ユーザー名とパスワードを検証し、JWT を返す。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT id, email, password_hash, role
                FROM users WHERE display_name = :username LIMIT 1
            """),
            {"username": body.username},
        ).fetchone()
    finally:
        session.close()

    if not record or not _verify_password(body.password, record[2]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    role = _pg_role_to_app_role(record[3])
    return TokenResponse(
        access_token=_create_token(str(record[0]), body.username, record[1], role)
    )


@app.get("/api/auth/me", response_model=UserOut)
def auth_me(current_user: dict = Depends(_get_current_user)) -> UserOut:
    """Bearer トークンからデコードした現在のユーザー情報を返す。"""
    return UserOut(**current_user)


# ---------------------------------------------------------------------------
# Admin: PDF教材アップロードとグラフ化パイプライン
# ---------------------------------------------------------------------------

# バックグラウンド処理のステータス管理
_material_lock = threading.Lock()
_material_status: dict[str, dict] = {}


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """PDFからテキストを抽出する。PyMuPDFを使用。"""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text())
    doc.close()
    return "\n".join(text_parts).replace("\x00", "")


def _build_knowledge_graph(text: str, title: str) -> dict:
    """抽出したテキストからDSLベースのナレッジグラフを構築する。

    LLMを使って教材テキストを解析し、概念間の関係をグラフ構造として返す。
    """
    client = _openai()

    prompt = f"""以下の教材テキストから知識グラフを構築してください。

**教材タイトル:** {title}

**教材テキスト (抜粋):**
{text[:8000]}

以下のJSON形式で出力してください:
{{
  "title": "教材タイトル",
  "domain": "分野名",
  "concepts": [
    {{
      "id": "concept_1",
      "name": "概念名",
      "description": "概念の説明",
      "type": "definition|theorem|method|example"
    }}
  ],
  "relationships": [
    {{
      "source": "concept_1",
      "target": "concept_2",
      "relation": "REQUIRES|CONTAINS|CAUSES|DEFINES|EXTENDS|APPLIES_TO",
      "description": "関係の説明"
    }}
  ],
  "chapters": [
    {{
      "title": "章タイトル",
      "concepts": ["concept_1", "concept_2"],
      "topics": [
        {{
          "id": "topic_1",
          "title": "トピックタイトル",
          "concepts": ["concept_1"]
        }}
      ]
    }}
  ]
}}"""

    try:
        response = client.chat.completions.create(
            model=_OPENAI_ANALYSIS_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = response.choices[0].message.content or ""
        # マークダウンコードフェンスを除去
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Knowledge graph extraction failed: %s", exc)
        return {"title": title, "concepts": [], "relationships": [], "chapters": []}


def _process_material_background(
    material_id: str,
    doc_id: str,
    filename: str,
    pdf_bytes: bytes,
) -> None:
    """バックグラウンドでPDF処理パイプラインを実行する。

    1. PDFテキスト抽出
    2. テキストをチャンク分割してPostgreSQLにembedding
    3. LLMでナレッジグラフ構築
    4. PostgreSQLに保存
    """
    with _material_lock:
        _material_status[material_id] = {
            "status": "processing",
            "filename": filename,
        }

    try:
        # 1. PDFテキスト抽出
        extracted_text = _extract_pdf_text(pdf_bytes)
        logger.info("Extracted %d chars from PDF %s", len(extracted_text), filename)

        # 2. Embeddingをチャンク分割してPostgreSQLに保存
        chunks = _chunk_text(extracted_text, chunk_size=1000, overlap=100)
        if chunks:
            _embed_chunks(material_id, doc_id, chunks)

        # 3. ナレッジグラフ構築
        title = os.path.splitext(filename)[0]
        knowledge_graph = _build_knowledge_graph(extracted_text, title)

        # 4. PostgreSQLに保存
        session = _pg_session()
        try:
            session.execute(
                sa_text("""
                    UPDATE documents
                    SET status = 'completed',
                        knowledge_graph = CAST(:kg AS jsonb),
                        text_length = :text_length,
                        chunk_count = :chunk_count,
                        updated_at = now()
                    WHERE id = CAST(:doc_id AS uuid)
                """),
                {
                    "doc_id": doc_id,
                    "kg": json.dumps(knowledge_graph, ensure_ascii=False),
                    "text_length": len(extracted_text),
                    "chunk_count": len(chunks),
                },
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        with _material_lock:
            _material_status[material_id]["status"] = "completed"

        logger.info("Material processing completed: %s", material_id)

    except Exception:
        logger.exception("Material processing failed: %s", material_id)
        with _material_lock:
            _material_status[material_id]["status"] = "failed"

        try:
            session = _pg_session()
            try:
                session.execute(
                    sa_text("UPDATE documents SET status = 'failed', updated_at = now() WHERE id = CAST(:doc_id AS uuid)"),
                    {"doc_id": doc_id},
                )
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()
        except Exception:
            pass


def _chunk_text(text: str, chunk_size: int = 1000, overlap: int = 100) -> list[str]:
    """テキストをチャンクに分割する。"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - overlap
    return chunks


def _embed_chunks(material_id: str, doc_id: str, chunks: list[str]) -> None:
    """テキストチャンクをembeddingしてPostgreSQLに保存する。"""
    client = _openai()

    try:
        batch_size = 50
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            resp = client.embeddings.create(
                model=_OPENAI_EMBEDDING_MODEL,
                input=batch,
            )
            session = _pg_session()
            try:
                for j, emb in enumerate(resp.data):
                    import uuid as _uuid
                    chunk_id = _uuid.uuid4()
                    session.execute(
                        sa_text("""
                            INSERT INTO chunks (id, document_id, chunk_index, text, embedding, material_id)
                            VALUES (:id, CAST(:doc_id AS uuid), :idx, :text, :embedding, :material_id)
                        """),
                        {
                            "id": chunk_id,
                            "doc_id": doc_id,
                            "idx": i + j,
                            "text": batch[j],
                            "embedding": str(emb.embedding),
                            "material_id": material_id,
                        },
                    )
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        logger.info("Embedded %d chunks for material %s", len(chunks), material_id)
    except Exception:
        logger.exception("Embedding failed for material %s", material_id)


@app.post("/api/admin/materials/upload", response_model=MaterialOut, status_code=202)
def upload_material(
    file: UploadFile = File(...),
    current_user: dict = Depends(_require_teacher),
) -> MaterialOut:
    """PDF教材をアップロードし、バックグラウンドでグラフ化処理を開始する。"""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    pdf_bytes = file.file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    material_id = str(uuid.uuid4())[:12]
    doc_id = uuid.uuid4()
    now = datetime.datetime.utcnow().isoformat()

    # PostgreSQL に初期レコード作成
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO documents (id, title, filename, status, uploaded_by, doc_type, source_path)
                VALUES (:id, :title, :filename, 'uploaded', CAST(:uploaded_by AS uuid), 'textbook', :material_id)
            """),
            {
                "id": doc_id,
                "title": os.path.splitext(file.filename)[0],
                "filename": file.filename,
                "uploaded_by": current_user["id"],
                "material_id": material_id,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    # バックグラウンド処理を開始
    thread = threading.Thread(
        target=_process_material_background,
        args=(material_id, str(doc_id), file.filename, pdf_bytes),
        daemon=True,
    )
    thread.start()

    logger.info(
        "Material upload accepted: %s (%s) by user=%s",
        material_id, file.filename, current_user["id"],
    )

    return MaterialOut(
        material_id=material_id,
        filename=file.filename,
        title=os.path.splitext(file.filename)[0],
        status="uploaded",
        uploaded_at=now,
    )


@app.get("/api/admin/materials", response_model=list[MaterialOut])
def list_materials(
    current_user: dict = Depends(_require_teacher),
) -> list[MaterialOut]:
    """アップロード済み教材の一覧を返す。"""
    session = _pg_session()
    try:
        records = session.execute(
            sa_text("""
                SELECT source_path, filename, title, status, created_at, knowledge_graph
                FROM documents
                WHERE uploaded_by = CAST(:user_id AS uuid) AND filename IS NOT NULL
                ORDER BY created_at DESC
            """),
            {"user_id": current_user["id"]},
        ).fetchall()
    finally:
        session.close()

    materials = []
    for r in records:
        mid = r[0] or ""
        kg = r[5] if r[5] else None

        status = r[3] or "uploaded"
        with _material_lock:
            if mid in _material_status:
                status = _material_status[mid].get("status", status)

        uploaded_at = r[4].isoformat() if r[4] else ""
        materials.append(MaterialOut(
            material_id=mid,
            filename=r[1] or "",
            title=r[2] or "",
            status=status,
            uploaded_at=uploaded_at,
            knowledge_graph=kg,
        ))

    return materials


@app.get("/api/admin/materials/{material_id}", response_model=MaterialOut)
def get_material(
    material_id: str,
    current_user: dict = Depends(_require_teacher),
) -> MaterialOut:
    """教材の詳細情報（ナレッジグラフ含む）を返す。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT source_path, filename, title, status, created_at, knowledge_graph
                FROM documents
                WHERE uploaded_by = CAST(:user_id AS uuid) AND source_path = :material_id
                LIMIT 1
            """),
            {"user_id": current_user["id"], "material_id": material_id},
        ).fetchone()
    finally:
        session.close()

    if not record:
        raise HTTPException(status_code=404, detail="Material not found")

    kg = record[5] if record[5] else None

    status = record[3] or "uploaded"
    with _material_lock:
        if material_id in _material_status:
            status = _material_status[material_id].get("status", status)

    uploaded_at = record[4].isoformat() if record[4] else ""
    return MaterialOut(
        material_id=record[0] or "",
        filename=record[1] or "",
        title=record[2] or "",
        status=status,
        uploaded_at=uploaded_at,
        knowledge_graph=kg,
    )


# ---------------------------------------------------------------------------
# Admin: Course Builder AI Chat
# ---------------------------------------------------------------------------

_COURSE_BUILDER_SYSTEM_PROMPT = """あなたは大学教員が学習コース（シラバス）を設計するのを支援するAIアシスタントです。

**あなたの役割:**
- 教員の要望に基づいて、体系的な学習コースの構成案を段階的に作成・改善する
- 不足している前提知識や学習順序の問題を指摘する
- 各章・トピックが論理的に繋がるよう構成を提案する

**コース構成のJSONスキーマ (course_draft):**
生成するコース構成案は以下の形式に従ってください:
{
  "title": "コースタイトル",
  "target_audience": "対象者（例: 物理学専攻の大学院1年生）",
  "goal": "到達目標",
  "prerequisites": ["前提知識1", "前提知識2"],
  "chapters": [
    {
      "title": "章タイトル",
      "topics": [
        {"title": "トピック名", "prerequisites": []},
        {"title": "トピック名", "prerequisites": ["前のトピック名"]}
      ]
    }
  ],
  "concepts": [
    {"name": "概念名", "children": ["子概念1", "子概念2"]}
  ],
  "sources": [
    {"title": "教材タイトル", "subtitle": "補足情報"}
  ]
}

**対話の進め方:**
1. まず教員の要望（テーマ、対象者、使用教材等）をヒアリングする
2. 初期のコース構成案を提示する
3. 教員のフィードバックに基づいて構成案を改善する
4. 前提知識の不足や学習順序の問題があれば指摘する

**重要なルール:**
- 応答は必ず日本語で行う
- コース構成案を提示・更新する場合は、応答テキストの最後に `---COURSE_DRAFT_JSON---` という区切り文字の後にJSONを出力する
- JSONは上記スキーマに従った valid JSON であること
- 構成案がまだ不完全な場合でも、現時点の案を出力する
- 教員が単なる質問をしている場合（構成変更を伴わない場合）は区切り文字とJSONを出力しない
- topics[].prerequisites には、そのトピックを学ぶ前に習得しておくべき**同コース内の**トピックのタイトルを列挙する
  - 例: 第2章のトピックは第1章のトピックタイトルを prerequisites に入れる
  - 最初のトピックや前提知識不要なトピックは prerequisites を空配列 [] にする"""


@app.post(
    "/api/admin/course-builder/chat",
    response_model=CourseBuilderChatResponse,
)
def course_builder_chat(
    body: CourseBuilderChatRequest,
    current_user: dict = Depends(_require_teacher),
) -> CourseBuilderChatResponse:
    """教員がAIと対話しながらコースを設計するエンドポイント。

    LLMにコース構成のスキーマを教え、教員の要望に基づいて
    コース構成案を段階的に生成・更新する。
    """
    messages: list[dict] = [
        {"role": "system", "content": _COURSE_BUILDER_SYSTEM_PROMPT},
    ]

    # 利用可能な教材情報をコンテキストに含める
    try:
        pg_session = _pg_session()
        try:
            records = pg_session.execute(
                sa_text("""
                    SELECT title, filename, knowledge_graph
                    FROM documents
                    WHERE uploaded_by = CAST(:user_id AS uuid) AND status = 'completed'
                """),
                {"user_id": current_user["id"]},
            ).fetchall()
        finally:
            pg_session.close()

        if records:
            materials_ctx = "## 利用可能な教材:\n"
            for r in records:
                materials_ctx += f"- {r[0] or r[1] or ''}"
                if r[2] and isinstance(r[2], dict) and r[2].get("domain"):
                    materials_ctx += f" (分野: {r[2]['domain']})"
                materials_ctx += "\n"
            messages.append({
                "role": "user",
                "content": materials_ctx + "\n上記の教材が利用可能です。",
            })
            messages.append({
                "role": "assistant",
                "content": "承知しました。これらの教材を踏まえてコース設計を支援します。",
            })
    except Exception:
        logger.warning("Failed to load materials context for course builder", exc_info=True)

    # 会話履歴を追加
    for turn in body.history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": body.message})

    # LLM呼び出し
    try:
        client = _openai()
        response = client.chat.completions.create(
            model=_OPENAI_ANALYSIS_MODEL,
            messages=messages,
            temperature=0.4,
        )
        raw_answer = response.choices[0].message.content or ""
    except Exception as exc:
        logger.exception("Course builder chat LLM call failed")
        raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc

    # course_draft JSONを分離
    answer = raw_answer
    course_draft = None

    if "---COURSE_DRAFT_JSON---" in raw_answer:
        parts = raw_answer.split("---COURSE_DRAFT_JSON---", 1)
        answer = parts[0].strip()
        json_part = parts[1].strip()
        # マークダウンコードフェンスを除去
        if json_part.startswith("```"):
            json_part = json_part.split("\n", 1)[1] if "\n" in json_part else json_part[3:]
        if json_part.endswith("```"):
            json_part = json_part[:-3]
        json_part = json_part.strip()
        if json_part.startswith("json"):
            json_part = json_part[4:].strip()
        try:
            course_draft = json.loads(json_part)
        except Exception:
            logger.warning("Failed to parse course_draft JSON: %s", json_part[:200])

    logger.info(
        "Course builder chat for user=%s, draft=%s",
        current_user["id"],
        "yes" if course_draft else "no",
    )

    # セッションIDが指定されていれば履歴と draft を永続化
    if body.session_id:
        updated_history = body.history + [
            {"role": "user", "content": body.message},
            {"role": "assistant", "content": answer},
        ]
        _save_cb_session(current_user["id"], body.session_id, updated_history, course_draft)

    return CourseBuilderChatResponse(answer=answer, course_draft=course_draft)


# ---------------------------------------------------------------------------
# A1: Course Builder Session helpers & endpoints
# ---------------------------------------------------------------------------

def _save_cb_session(
    user_id: str,
    session_id: str,
    history: list[dict],
    course_draft: dict | None,
) -> None:
    """コース構築セッションの履歴と draft を PostgreSQL に保存する。"""
    try:
        session = _pg_session()
        try:
            session.execute(
                sa_text("""
                    UPDATE course_builder_sessions
                    SET history = CAST(:history AS jsonb),
                        course_draft = CAST(:course_draft AS jsonb),
                        updated_at = now()
                    WHERE id = :session_id AND user_id = CAST(:user_id AS uuid)
                """),
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "history": json.dumps(history, ensure_ascii=False),
                    "course_draft": (
                        json.dumps(course_draft, ensure_ascii=False)
                        if course_draft is not None else None
                    ),
                },
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    except Exception:
        logger.exception("Failed to save course builder session %s", session_id)


@app.post("/api/admin/course-builder/sessions", response_model=CourseBuilderSessionOut, status_code=201)
def create_cb_session(
    body: CourseBuilderSessionCreate,
    current_user: dict = Depends(_require_teacher),
) -> CourseBuilderSessionOut:
    """コース構築セッションを新規作成する。"""
    session_id = str(uuid.uuid4())[:12]
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO course_builder_sessions (id, user_id, title, history, course_draft)
                VALUES (:session_id, CAST(:user_id AS uuid), :title, '[]', null)
            """),
            {"session_id": session_id, "user_id": current_user["id"], "title": body.title},
        )
        row = session.execute(
            sa_text("SELECT created_at, updated_at FROM course_builder_sessions WHERE id = :id"),
            {"id": session_id},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    created_at = row[0].isoformat() if row and row[0] else ""
    updated_at = row[1].isoformat() if row and row[1] else ""
    logger.info("Created course builder session %s for user=%s", session_id, current_user["id"])
    return CourseBuilderSessionOut(
        session_id=session_id, title=body.title,
        created_at=created_at, updated_at=updated_at,
    )


@app.get("/api/admin/course-builder/sessions", response_model=list[CourseBuilderSessionOut])
def list_cb_sessions(
    current_user: dict = Depends(_require_teacher),
) -> list[CourseBuilderSessionOut]:
    """コース構築セッション一覧を返す（更新日時の降順）。"""
    session = _pg_session()
    try:
        records = session.execute(
            sa_text("""
                SELECT id, title, created_at, updated_at
                FROM course_builder_sessions
                WHERE user_id = CAST(:user_id AS uuid)
                ORDER BY updated_at DESC
            """),
            {"user_id": current_user["id"]},
        ).fetchall()
    finally:
        session.close()
    return [
        CourseBuilderSessionOut(
            session_id=r[0],
            title=r[1] or "新しいセッション",
            created_at=r[2].isoformat() if r[2] else "",
            updated_at=r[3].isoformat() if r[3] else "",
        )
        for r in records
    ]


@app.get("/api/admin/course-builder/sessions/{session_id}")
def get_cb_session(
    session_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コース構築セッションの詳細（履歴・draft含む）を返す。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT id, title, history, course_draft, created_at, updated_at
                FROM course_builder_sessions
                WHERE id = :session_id AND user_id = CAST(:user_id AS uuid)
            """),
            {"session_id": session_id, "user_id": current_user["id"]},
        ).fetchone()
    finally:
        session.close()

    if not record:
        raise HTTPException(status_code=404, detail="Session not found")

    history = record[2] if isinstance(record[2], list) else (
        json.loads(record[2]) if record[2] else []
    )
    course_draft = record[3] if isinstance(record[3], dict) else (
        json.loads(record[3]) if record[3] else None
    )

    return {
        "session_id": record[0],
        "title": record[1] or "新しいセッション",
        "history": history,
        "course_draft": course_draft,
        "created_at": record[4].isoformat() if record[4] else "",
        "updated_at": record[5].isoformat() if record[5] else "",
    }


@app.put("/api/admin/course-builder/sessions/{session_id}", response_model=CourseBuilderSessionOut)
def update_cb_session(
    session_id: str,
    body: CourseBuilderSessionUpdate,
    current_user: dict = Depends(_require_teacher),
) -> CourseBuilderSessionOut:
    """コース構築セッションのタイトル・履歴・draft を更新する。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT title, history, course_draft, created_at
                FROM course_builder_sessions
                WHERE id = :session_id AND user_id = CAST(:user_id AS uuid)
            """),
            {"session_id": session_id, "user_id": current_user["id"]},
        ).fetchone()
        if not record:
            raise HTTPException(status_code=404, detail="Session not found")

        new_title = body.title if body.title is not None else (record[0] or "新しいセッション")
        new_history = (
            json.dumps(body.history, ensure_ascii=False)
            if body.history is not None
            else json.dumps(
                record[1] if isinstance(record[1], list) else (json.loads(record[1]) if record[1] else []),
                ensure_ascii=False,
            )
        )
        new_draft = (
            json.dumps(body.course_draft, ensure_ascii=False)
            if body.course_draft is not None
            else (json.dumps(record[2]) if record[2] else None)
        )

        updated = session.execute(
            sa_text("""
                UPDATE course_builder_sessions
                SET title = :title,
                    history = CAST(:history AS jsonb),
                    course_draft = CAST(:draft AS jsonb),
                    updated_at = now()
                WHERE id = :session_id AND user_id = CAST(:user_id AS uuid)
                RETURNING updated_at
            """),
            {
                "session_id": session_id,
                "user_id": current_user["id"],
                "title": new_title,
                "history": new_history,
                "draft": new_draft,
            },
        ).fetchone()
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    created_at = record[3].isoformat() if record[3] else ""
    updated_at = updated[0].isoformat() if updated and updated[0] else ""
    return CourseBuilderSessionOut(
        session_id=session_id, title=new_title,
        created_at=created_at, updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# A2: Course publish & enroll endpoints
# ---------------------------------------------------------------------------

@app.put("/api/admin/courses/{course_id}/publish")
def publish_course(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースを学生に公開する。所有者の TEACHER または SYSTEM_ADMIN のみ可。"""
    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE learning_courses
                SET is_published = true, is_template = true, updated_at = now()
                WHERE id = :course_id AND user_id = CAST(:user_id AS uuid)
                RETURNING id
            """),
            {"course_id": course_id, "user_id": current_user["id"]},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if not result:
        raise HTTPException(status_code=404, detail="Course not found")
    logger.info("Course %s published by user=%s", course_id, current_user["id"])
    return {"course_id": course_id, "is_published": True}


@app.post("/api/learning/courses/{course_id}/enroll", response_model=LearningCourseOut, status_code=201)
def enroll_course(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseOut:
    """公開テンプレートコースをクローンして自分のコースとして登録する。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT data FROM learning_courses
                WHERE id = :course_id AND is_published = true AND is_template = true
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()

    if not record or not record[0]:
        raise HTTPException(status_code=404, detail="Published course not found")

    template_data = record[0] if isinstance(record[0], dict) else json.loads(record[0])

    # クローンを作成
    new_course_id = str(uuid.uuid4())[:8]
    cloned_data = dict(template_data)
    cloned_data["id"] = new_course_id

    _save_course_data(current_user["id"], new_course_id, cloned_data)

    # cloned_from を設定
    session = _pg_session()
    try:
        session.execute(
            sa_text("UPDATE learning_courses SET cloned_from = :original_id WHERE id = :id"),
            {"id": new_course_id, "original_id": course_id},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info(
        "User=%s enrolled in course %s (cloned as %s)",
        current_user["id"], course_id, new_course_id,
    )
    return LearningCourseOut(id=new_course_id, title=cloned_data.get("title", ""))


# ---------------------------------------------------------------------------
# Admin: User Management
# ---------------------------------------------------------------------------

@app.post("/api/admin/users/student", response_model=UserOut, status_code=201)
def create_student(
    body: CreateUserRequest,
    current_user: dict = Depends(_require_teacher),
) -> UserOut:
    """教員が学生アカウントを作成する。TEACHER 権限が必要。"""
    session = _pg_session()
    try:
        existing = session.execute(
            sa_text("SELECT id FROM users WHERE display_name = :username LIMIT 1"),
            {"username": body.username},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already taken")

        user_id = uuid.uuid4()
        hashed_pw = _hash_password(body.password)
        session.execute(
            sa_text("""
                INSERT INTO users (id, email, display_name, role, password_hash)
                VALUES (:id, :email, :username, 'learner', :pw)
            """),
            {"id": user_id, "email": body.email, "username": body.username, "pw": hashed_pw},
        )
        session.commit()
    finally:
        session.close()

    logger.info("Teacher '%s' created student '%s' (id=%s)", current_user["username"], body.username, user_id)
    return UserOut(id=str(user_id), username=body.username, email=body.email, role=ROLE_STUDENT)


@app.post("/api/admin/users/teacher", response_model=UserOut, status_code=201)
def create_teacher(
    body: CreateUserRequest,
    current_user: dict = Depends(_require_system_admin),
) -> UserOut:
    """管理者が教員アカウントを作成する。SYSTEM_ADMIN 権限が必要。"""
    session = _pg_session()
    try:
        existing = session.execute(
            sa_text("SELECT id FROM users WHERE display_name = :username LIMIT 1"),
            {"username": body.username},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already taken")

        user_id = uuid.uuid4()
        hashed_pw = _hash_password(body.password)
        session.execute(
            sa_text("""
                INSERT INTO users (id, email, display_name, role, password_hash)
                VALUES (:id, :email, :username, 'instructor', :pw)
            """),
            {"id": user_id, "email": body.email, "username": body.username, "pw": hashed_pw},
        )
        session.commit()
    finally:
        session.close()

    logger.info("Admin '%s' created teacher '%s' (id=%s)", current_user["username"], body.username, user_id)
    return UserOut(id=str(user_id), username=body.username, email=body.email, role=ROLE_TEACHER)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "episteme-graph-api"}
