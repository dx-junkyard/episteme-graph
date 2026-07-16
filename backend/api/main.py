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
GET  /api/admin/courses/{course_id}/groups                      コースに紐づくグループ権限一覧 (Issue #125)
POST /api/admin/courses/{course_id}/groups                      コースにグループ権限を付与 (viewer/editor)
DELETE /api/admin/courses/{course_id}/groups/{group_id}         コースからグループ権限を削除
POST /api/admin/users/student                                   学生アカウント作成 (TEACHER)
POST /api/admin/users/teacher                                   教員アカウント作成 (SYSTEM_ADMIN)
POST /api/groups                                                グループ作成
GET  /api/groups                                                自グループ一覧
GET  /api/groups/{group_id}                                     グループ詳細
POST /api/groups/{group_id}/members                             ユーザーを直接招待
POST /api/groups/join-by-code                                   招待コードで参加
GET  /api/me/invitations                                        自分宛ての招待一覧
POST /api/me/invitations/{inv}/accept                           招待を承諾
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
from routes import auth, learning, admin, lecture, groups, error_logs, export as export_routes
from routes import atlas as atlas_routes
from routes import atlas_view as atlas_view_routes
from routes import doubt as doubt_routes
from routes import reconstruction as reconstruction_routes
from routes import library as library_routes
from routes import llm_usage as llm_usage_routes
from routes import personal_map as personal_map_routes
# Tier 3-17c: 旧 routes/admin.py 末尾で `router.include_router(...)` されていた
# 13個の子ルーターを、admin.py 経由の二段ネストではなく main.py から直接
# `/api/admin` prefix でフラットにマウントする（下記「ルーターのマウント」参照）。
from routes.lecture_studio import router as _lecture_studio_router
from routes.theory_components import router as _theory_components_router
from routes.cartridges import router as _cartridges_router
from routes.revisions import router as _revisions_router
from routes.admin_assistant import admin_router as _admin_assistant_router
from routes.versioning import router as _versioning_router
from routes.status import router as _status_router
from routes.notifications import router as _notifications_router
from routes.deliberation import router as _deliberation_router
from core.config import get_settings as _get_settings
from core.postgres import get_session as _pg_session, check_connection as _pg_check
from core.migrations import run_migrations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
error_logs.install_error_log_capture()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(application: FastAPI):
    """起動時に migration 適用 → システム管理者アカウント初期化 → 各種シードを行う（PostgreSQL）。

    DDL の正本は ``backend/db/*.sql``（``core/migrations.py::run_migrations()`` が
    番号順に適用する。旧 ``_run_migrations()`` のインライン DDL は廃止済み）。
    """
    _ADMIN_PASSWORD = _get_settings().admin_password
    if not _ADMIN_PASSWORD:
        logger.critical("CRITICAL: ADMIN_PASSWORD is not set in .env.")
        sys.exit(1)

    # PostgreSQL の起動を待つ（最大30秒）
    max_retries = 10
    for attempt in range(max_retries):
        try:
            run_migrations()

            # Administrator ユーザー作成は migration 適用後に行う
            # （フレッシュ DB では users テーブルがランナー実行後に初めて存在するため）。
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

            # 骨格 DB 管理化 (027): カートリッジ同梱の凍結骨格を一度だけ DB へ取り込む (冪等)
            atlas_session = _pg_session()
            try:
                from core import atlas_store

                atlas_store.import_bundled_skeletons(atlas_session)
                atlas_session.commit()
            except Exception:  # noqa: BLE001
                atlas_session.rollback()
                logger.warning("bundled atlas skeleton import skipped", exc_info=True)
            finally:
                atlas_session.close()

            # Seed builtin schema types/predicates
            from core.schema_registry import seed_builtin_schema
            seed_builtin_schema()

            # ナレッジライブラリ（L層, migration 042）: カートリッジ同梱ライブラリの
            # 起動時冪等取込（atlas_skeletons と同じシードパターン）。seed 側は自身の
            # 例外を握る設計だが、念のため呼び出し側でも起動を止めない。
            try:
                from core.library import seed as _library_seed

                _library_seed.import_bundled_library()
            except Exception:  # noqa: BLE001
                logger.warning("bundled library seed import skipped", exc_info=True)
            break
        except Exception as exc:
            if attempt < max_retries - 1:
                wait = 3 * (attempt + 1)
                logger.warning("PostgreSQL not ready (attempt %d/%d): %s. Retrying in %ds...", attempt + 1, max_retries, exc, wait)
                await asyncio.sleep(wait)
            else:
                logger.critical("Failed to connect to PostgreSQL after %d attempts.", max_retries)
                sys.exit(1)

    # V層（migration 037）: 削除猶予の定期スイーパを起動（best-effort。起動を止めない）
    try:
        from core.versioning import worker as _versioning_worker

        _versioning_worker.start_background_sweeper()
    except Exception:  # noqa: BLE001
        logger.warning("versioning sweeper startup skipped", exc_info=True)

    # 状態管理・通知基盤（migration 038）: 遷移検知 watcher を起動（best-effort）
    try:
        from core.status import watcher as _status_watcher

        _status_watcher.start_watcher()
    except Exception:  # noqa: BLE001
        logger.warning("status watcher startup skipped", exc_info=True)

    yield


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="Episteme Graph API", version="0.1.0", lifespan=_lifespan)
_cors_origins_raw = _get_settings().cors_origins
_cors_origins = _cors_origins_raw.split(",") if _cors_origins_raw != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
error_logs.register_error_log_middleware(app)

# ルーターのマウント
app.include_router(auth.router)
app.include_router(learning.router)
app.include_router(admin.router)
app.include_router(error_logs.router)
app.include_router(lecture.router)
app.include_router(groups.router)
app.include_router(export_routes.router)
app.include_router(atlas_routes.learning_router)
app.include_router(atlas_routes.report_router)
app.include_router(atlas_view_routes.router)
app.include_router(doubt_routes.learning_router)
app.include_router(reconstruction_routes.learning_router)
app.include_router(library_routes.router)
app.include_router(llm_usage_routes.router)
app.include_router(personal_map_routes.router)
app.include_router(personal_map_routes.me_router)

# Tier 3-17c: 旧 routes/admin.py の `router.include_router(...)` 二段ネストを
# フラット化。以下13ルーターは admin.router と同じ "/api/admin" prefix で
# 直接マウントする（URL・認可・レスポンスは従来と完全に不変）。
app.include_router(_lecture_studio_router, prefix="/api/admin")
app.include_router(_theory_components_router, prefix="/api/admin")
app.include_router(_cartridges_router, prefix="/api/admin")
app.include_router(_revisions_router, prefix="/api/admin")
app.include_router(atlas_routes.router, prefix="/api/admin")
app.include_router(atlas_routes.admin_atlas_router, prefix="/api/admin")
app.include_router(atlas_routes.binding_router, prefix="/api/admin")
app.include_router(doubt_routes.admin_router, prefix="/api/admin")
app.include_router(_admin_assistant_router, prefix="/api/admin")
app.include_router(reconstruction_routes.admin_router, prefix="/api/admin")
app.include_router(_versioning_router, prefix="/api/admin")
app.include_router(_status_router, prefix="/api/admin")
app.include_router(_notifications_router, prefix="/api/admin")
app.include_router(_deliberation_router, prefix="/api/admin")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "episteme-graph-api"}
