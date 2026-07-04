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
from core.config import get_settings as _get_settings
from core.postgres import get_session as _pg_session, check_connection as _pg_check

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
error_logs.install_error_log_capture()


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
        # NOTE: cloned_from カラムは Issue #133 (Migration 011) で廃止された。
        for ddl in [
            "ALTER TABLE learning_courses ADD COLUMN IF NOT EXISTS is_template  BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE learning_courses ADD COLUMN IF NOT EXISTS is_published BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE learning_courses ADD COLUMN IF NOT EXISTS owner_id     UUID REFERENCES users(id)",
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

        # Migration 005: background_tasks テーブル (Issue #63)
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS background_tasks (
                id            TEXT PRIMARY KEY,
                task_type     TEXT NOT NULL DEFAULT 'material_processing',
                status        TEXT NOT NULL DEFAULT 'pending'
                                  CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
                created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
                result_data   JSONB,
                error_message TEXT,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_bg_tasks_status ON background_tasks(status)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_bg_tasks_created_by ON background_tasks(created_by)"
        ))

        # Migration 006: Interactive Lecture Mode (Issue #66)
        session.execute(sa_text(
            "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS spoken_text TEXT"
        ))
        session.execute(sa_text(
            "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS display_text TEXT"
        ))
        session.execute(sa_text(
            "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS formulas JSONB DEFAULT '[]'::jsonb"
        ))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS lecture_audio_cache (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                chunk_id        UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
                voice           TEXT NOT NULL DEFAULT 'alloy',
                audio_data      BYTEA NOT NULL,
                duration_ms     INTEGER NOT NULL DEFAULT 0,
                word_timestamps JSONB DEFAULT '[]'::jsonb,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(chunk_id, voice)
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_lecture_audio_cache_chunk_id ON lecture_audio_cache(chunk_id)"
        ))

        # Migration 007: arxiv_id カラムを廃止し material_id に統一 (Issue #70)
        # UPDATE chunks SET material_id = arxiv_id は既存DBからの移行用のため削除済み
        # クリーンなDBには arxiv_id カラムが存在しないため実行しない
        session.execute(sa_text("DROP INDEX IF EXISTS idx_chunks_arxiv"))
        session.execute(sa_text("ALTER TABLE chunks DROP COLUMN IF EXISTS arxiv_id"))

        # Migration 009: グループ + 資料開示範囲 (Issue #121)
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS groups (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name         TEXT NOT NULL,
                description  TEXT NOT NULL DEFAULT '',
                invite_code  TEXT UNIQUE,
                created_by   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_groups_created_by ON groups(created_by)"
        ))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS group_members (
                id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                group_id   UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role       TEXT NOT NULL DEFAULT 'member'
                              CHECK (role IN ('admin', 'member')),
                joined_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(group_id, user_id)
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id)"
        ))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS group_invitations (
                id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                group_id         UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                invitee_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                inviter_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                status           TEXT NOT NULL DEFAULT 'pending'
                                    CHECK (status IN ('pending', 'accepted', 'declined', 'revoked')),
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                responded_at     TIMESTAMPTZ,
                UNIQUE(group_id, invitee_user_id, status)
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_invitations_invitee ON group_invitations(invitee_user_id, status)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_invitations_group ON group_invitations(group_id)"
        ))
        for ddl in [
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'private'",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS group_id UUID REFERENCES groups(id) ON DELETE SET NULL",
            "ALTER TABLE learning_courses ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'private'",
            "ALTER TABLE learning_courses ADD COLUMN IF NOT EXISTS group_id UUID REFERENCES groups(id) ON DELETE SET NULL",
            "ALTER TABLE learning_courses ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''",
        ]:
            session.execute(sa_text(ddl))
        # 既存の公開テンプレートを 'public' 扱いに移行（後方互換）
        session.execute(sa_text("""
            UPDATE learning_courses
                SET visibility = 'public'
                WHERE is_published = true AND is_template = true AND visibility = 'private'
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_documents_visibility ON documents(visibility)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_courses_visibility ON learning_courses(visibility)"
        ))

        # Migration 010: コース × グループ 権限マッピング (Issue #125)
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS course_group_permissions (
                course_id   TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
                group_id    UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                permission  TEXT NOT NULL DEFAULT 'viewer'
                                CHECK (permission IN ('viewer', 'editor')),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (course_id, group_id)
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_cgp_course ON course_group_permissions(course_id)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_cgp_group ON course_group_permissions(group_id)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_cgp_group_permission "
            "ON course_group_permissions(group_id, permission)"
        ))

        # Migration 011: コース原本と学習状態の完全分離 (Issue #133)
        # マスターコースとユーザーごとの学習状態を分離する。
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS learning_states (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                course_id TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
                progress_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                personal_graph JSONB NOT NULL DEFAULT '{}'::jsonb,
                enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (user_id, course_id)
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_learning_states_user ON learning_states(user_id)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_learning_states_course ON learning_states(course_id)"
        ))
        # Migration 012: チャンク別グラフサジェストと教員向けつまづき記録 (Issue #160)
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS chunk_graph_mentions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
                material_id TEXT,
                element_id TEXT NOT NULL,
                element_type TEXT NOT NULL
                    CHECK (element_type IN ('concept', 'relationship', 'formula', 'keyword', 'reference', 'citation')),
                surface_text TEXT NOT NULL DEFAULT '',
                importance_score REAL NOT NULL DEFAULT 0.5,
                offsets JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (chunk_id, element_id, element_type)
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_chunk_graph_mentions_chunk "
            "ON chunk_graph_mentions(chunk_id)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_chunk_graph_mentions_material "
            "ON chunk_graph_mentions(material_id)"
        ))
        session.execute(sa_text(
            "ALTER TABLE chunk_graph_mentions "
            "DROP CONSTRAINT IF EXISTS chunk_graph_mentions_element_type_check"
        ))
        session.execute(sa_text("""
            ALTER TABLE chunk_graph_mentions
            ADD CONSTRAINT chunk_graph_mentions_element_type_check
            CHECK (element_type IN ('concept', 'relationship', 'formula', 'keyword', 'reference', 'citation'))
        """))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS student_stumble_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                instructor_id UUID REFERENCES users(id) ON DELETE SET NULL,
                student_id UUID REFERENCES users(id) ON DELETE CASCADE,
                course_id TEXT REFERENCES learning_courses(id) ON DELETE CASCADE,
                material_id TEXT,
                chunk_id UUID REFERENCES chunks(id) ON DELETE SET NULL,
                element_id TEXT,
                element_label TEXT,
                event_type TEXT NOT NULL
                    CHECK (event_type IN (
                        'clicked_explain',
                        'explanation_missing',
                        'generated_for_student',
                        'misconception'
                    )),
                user_message TEXT,
                generated_explanation TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_student_stumble_events_instructor "
            "ON student_stumble_events(instructor_id)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_student_stumble_events_course "
            "ON student_stumble_events(course_id)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_student_stumble_events_element "
            "ON student_stumble_events(element_id, event_type)"
        ))
        # Migration 013: 原稿スタジオ 理論コンポーネント化UI
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS theory_components (
                id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                course_id           TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
                primary_chunk_id    UUID REFERENCES chunks(id) ON DELETE SET NULL,
                name                TEXT NOT NULL,
                component_type      TEXT NOT NULL DEFAULT 'theory'
                                        CHECK (component_type IN ('theory', 'concept', 'law', 'mechanism', 'operator', 'observation')),
                summary             TEXT NOT NULL DEFAULT '',
                status              TEXT NOT NULL DEFAULT 'candidate'
                                        CHECK (status IN ('candidate', 'draft', 'teacher_reviewed', 'rejected')),
                source_chunks       JSONB NOT NULL DEFAULT '[]'::jsonb,
                inputs              JSONB NOT NULL DEFAULT '[]'::jsonb,
                outputs             JSONB NOT NULL DEFAULT '[]'::jsonb,
                preconditions       JSONB NOT NULL DEFAULT '[]'::jsonb,
                constraints         JSONB NOT NULL DEFAULT '[]'::jsonb,
                invalid_conditions  JSONB NOT NULL DEFAULT '[]'::jsonb,
                dependencies        JSONB NOT NULL DEFAULT '[]'::jsonb,
                blackbox_policy     JSONB NOT NULL DEFAULT jsonb_build_object('default_level', 'summary', 'expand_if_unlearned', true),
                validation_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
                teacher_notes       TEXT NOT NULL DEFAULT '',
                created_by          UUID REFERENCES users(id),
                created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_components_course ON theory_components(course_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_components_chunk ON theory_components(primary_chunk_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_components_status ON theory_components(status)"))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS theory_claims (
                id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                document_id        TEXT NOT NULL DEFAULT '',
                chunk_id           UUID REFERENCES chunks(id) ON DELETE CASCADE,
                source_scope       JSONB NOT NULL DEFAULT '{}'::jsonb,
                claim_type         TEXT NOT NULL DEFAULT 'diagnostic_claim'
                                       CHECK (claim_type IN (
                                           'definition', 'assumption', 'approximation', 'equation', 'relation',
                                           'derivation_step', 'observable_definition', 'correction',
                                           'uncertainty', 'limitation', 'result', 'diagnostic_claim'
                                       )),
                text               TEXT NOT NULL,
                normalized_text    TEXT NOT NULL DEFAULT '',
                concepts           JSONB NOT NULL DEFAULT '[]'::jsonb,
                support_status     TEXT NOT NULL DEFAULT 'source_backed',
                evidence_text      TEXT NOT NULL DEFAULT '',
                review_status      TEXT NOT NULL DEFAULT 'teacher_review_required',
                created_by         UUID REFERENCES users(id),
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_claims_document ON theory_claims(document_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_claims_chunk ON theory_claims(chunk_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_claims_review ON theory_claims(review_status)"))
        for ddl in [
            "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS source_scope JSONB NOT NULL DEFAULT '{}'::jsonb",
            "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS evidence_claims JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS maturity_level TEXT NOT NULL DEFAULT 'paper_claim'",
            "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS maturity_source TEXT NOT NULL DEFAULT 'llm_proposed'",
            "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'teacher_review_required'",
            "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS cautions JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS connectors JSONB NOT NULL DEFAULT '{}'::jsonb",
            "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS component_type_text TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS internal_flow JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS duplicate_candidates JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE theory_claims ADD COLUMN IF NOT EXISTS equation JSONB NOT NULL DEFAULT '{}'::jsonb",
        ]:
            session.execute(sa_text(ddl))
        session.execute(sa_text("ALTER TABLE theory_claims DROP CONSTRAINT IF EXISTS theory_claims_claim_type_check"))
        session.execute(sa_text("""
            ALTER TABLE theory_claims ADD CONSTRAINT theory_claims_claim_type_check CHECK (claim_type IN (
                'definition', 'assumption', 'approximation', 'equation', 'relation',
                'derivation_step', 'observable_definition', 'correction',
                'uncertainty', 'limitation', 'result', 'diagnostic_claim',
                'equation_definition', 'equation_relation', 'equation_transformation',
                'equation_approximation', 'equation_constraint'
            ))
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_components_review ON theory_components(review_status)"))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS theory_review_events (
                id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                entity_type     TEXT NOT NULL,
                entity_id       TEXT NOT NULL,
                old_status      TEXT NOT NULL DEFAULT '',
                new_status      TEXT NOT NULL DEFAULT '',
                changed_by      UUID REFERENCES users(id),
                metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_review_events_entity ON theory_review_events(entity_type, entity_id)"))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS theory_component_links (
                id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                course_id             TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
                source_component_id   UUID NOT NULL REFERENCES theory_components(id) ON DELETE CASCADE,
                target_component_id   UUID NOT NULL REFERENCES theory_components(id) ON DELETE CASCADE,
                link_type             TEXT NOT NULL DEFAULT 'output_to_input'
                                          CHECK (link_type IN ('output_to_input', 'requires', 'depends_on', 'conflicts_with', 'analogous_to')),
                status                TEXT NOT NULL DEFAULT 'candidate'
                                          CHECK (status IN ('candidate', 'valid', 'warning', 'conflict', 'rejected')),
                validation_result     JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_by            UUID REFERENCES users(id),
                created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_component_links_course ON theory_component_links(course_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_component_links_source ON theory_component_links(source_component_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_component_links_target ON theory_component_links(target_component_id)"))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS theory_component_graphs (
                id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                course_id          TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
                document_id        TEXT NOT NULL,
                scope              JSONB NOT NULL DEFAULT jsonb_build_object('level', 'paper'),
                graph_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
                validation_results JSONB NOT NULL DEFAULT '[]'::jsonb,
                updated_by         UUID REFERENCES users(id),
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(course_id, document_id)
            )
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_component_graphs_course ON theory_component_graphs(course_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_component_graphs_document ON theory_component_graphs(document_id)"))
        # Migration 014: セクション単位の論理要素抽出ステータス
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS section_assembly_status (
                id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                course_id                   TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
                document_id                 TEXT NOT NULL,
                section_id                  TEXT NOT NULL,
                component_assembly_status   TEXT NOT NULL DEFAULT 'pending'
                                                CHECK (component_assembly_status IN ('pending', 'success', 'failed', 'skipped')),
                error_type                  TEXT NOT NULL DEFAULT '',
                error_message               TEXT NOT NULL DEFAULT '',
                components_generated        INT NOT NULL DEFAULT 0,
                updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(course_id, document_id, section_id)
            )
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_section_assembly_status_course ON section_assembly_status(course_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_section_assembly_status_status ON section_assembly_status(component_assembly_status)"))
        # Migration 015: Document-first analysis pipeline (issue #226)
        session.execute(sa_text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS section_id      TEXT"))
        session.execute(sa_text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS block_ids       JSONB NOT NULL DEFAULT '[]'::jsonb"))
        session.execute(sa_text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks(section_id)"))
        session.execute(sa_text("ALTER TABLE theory_components ALTER COLUMN course_id DROP NOT NULL"))
        session.execute(sa_text("ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS document_id TEXT NOT NULL DEFAULT ''"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_components_document ON theory_components(document_id)"))
        session.execute(sa_text("ALTER TABLE theory_component_links ALTER COLUMN course_id DROP NOT NULL"))
        session.execute(sa_text("ALTER TABLE theory_component_links ADD COLUMN IF NOT EXISTS document_id TEXT NOT NULL DEFAULT ''"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_theory_component_links_document ON theory_component_links(document_id)"))
        session.execute(sa_text("ALTER TABLE theory_component_graphs ALTER COLUMN course_id DROP NOT NULL"))
        session.execute(sa_text("""
            DELETE FROM theory_component_graphs t
            USING (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY document_id
                        ORDER BY updated_at DESC, created_at DESC, id DESC
                    ) AS rn
                FROM theory_component_graphs
            ) d
            WHERE t.id = d.id
              AND d.rn > 1
        """))
        session.execute(sa_text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'theory_component_graphs_document_uq'
                ) THEN
                    ALTER TABLE theory_component_graphs
                        ADD CONSTRAINT theory_component_graphs_document_uq UNIQUE (document_id);
                END IF;
            END $$
        """))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS document_embeddings (
                id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                document_id     TEXT NOT NULL,
                material_id     TEXT,
                embedding_type  TEXT NOT NULL,
                source_version  TEXT NOT NULL DEFAULT 'v1',
                text            TEXT NOT NULL,
                embedding       vector(3072),
                metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(document_id, embedding_type, source_version)
            )
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_document_embeddings_document ON document_embeddings(document_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_document_embeddings_type     ON document_embeddings(embedding_type)"))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS document_analysis_runs (
                id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                document_id     TEXT NOT NULL,
                material_id     TEXT,
                cartridge_id    TEXT,
                status          TEXT NOT NULL DEFAULT 'pending'
                                    CHECK (status IN ('pending', 'running', 'completed', 'failed')),
                current_stage   TEXT,
                error_message   TEXT NOT NULL DEFAULT '',
                stage_outputs   JSONB NOT NULL DEFAULT '{}'::jsonb,
                started_at      TIMESTAMPTZ,
                completed_at    TIMESTAMPTZ,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_document_analysis_runs_document ON document_analysis_runs(document_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_document_analysis_runs_status   ON document_analysis_runs(status)"))
        # 既存の cloned_from カラムがあれば、クローンコースとその履歴をハードリセット後にカラム廃止
        session.execute(sa_text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'learning_courses' AND column_name = 'cloned_from'
                ) THEN
                    DELETE FROM learning_chat_history
                    WHERE course_id IN (
                        SELECT id FROM learning_courses WHERE cloned_from IS NOT NULL
                    );
                    DELETE FROM learning_courses WHERE cloned_from IS NOT NULL;
                    ALTER TABLE learning_courses DROP COLUMN cloned_from;
                END IF;
            END $$
        """))

        # Migration 016: chunks.embedding / document_embeddings.embedding を 768 → 3072 次元に変更
        # text-embedding-3-large は 3072 次元を返すが init.sql が vector(768) で作成されていたため
        # INSERT 時に "expected 768 dimensions, not 3072" が発生していた。
        # 既存の 768 次元 embedding は 3072 次元モデルと互換性がないため NULL にリセットする。
        session.execute(sa_text("""
            DO $$
            DECLARE
                col_type TEXT;
            BEGIN
                SELECT format_type(atttypid, atttypmod) INTO col_type
                FROM pg_attribute
                WHERE attrelid = 'chunks'::regclass
                  AND attname = 'embedding'
                  AND attnum > 0;

                IF col_type IS NOT NULL AND col_type != 'vector(3072)' THEN
                    DROP INDEX IF EXISTS idx_chunks_embedding;
                    ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(3072) USING NULL;
                    CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks
                        USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);
                END IF;
            END $$
        """))
        session.execute(sa_text("""
            DO $$
            DECLARE
                col_type TEXT;
            BEGIN
                SELECT format_type(atttypid, atttypmod) INTO col_type
                FROM pg_attribute
                WHERE attrelid = 'document_embeddings'::regclass
                  AND attname = 'embedding'
                  AND attnum > 0;

                IF col_type IS NOT NULL AND col_type != 'vector(3072)' THEN
                    ALTER TABLE document_embeddings ALTER COLUMN embedding TYPE vector(3072) USING NULL;
                END IF;
            END $$
        """))

        # Migration 018: course_builder_sessions ステータス・命名改善 (Issue #310)
        session.execute(sa_text("""
            ALTER TABLE course_builder_sessions
                ADD COLUMN IF NOT EXISTS source_file_name   TEXT,
                ADD COLUMN IF NOT EXISTS display_name       TEXT,
                ADD COLUMN IF NOT EXISTS status             TEXT NOT NULL DEFAULT 'draft',
                ADD COLUMN IF NOT EXISTS published_course_id TEXT
        """))

        # Migration 019: 反復改善パイプライン — analysis run 版管理 (Issue #402)
        session.execute(sa_text("""
            ALTER TABLE document_analysis_runs
                ADD COLUMN IF NOT EXISTS run_type           TEXT NOT NULL DEFAULT 'initial',
                ADD COLUMN IF NOT EXISTS base_run_id        UUID,
                ADD COLUMN IF NOT EXISTS parent_revision_id UUID,
                ADD COLUMN IF NOT EXISTS revision_status    TEXT,
                ADD COLUMN IF NOT EXISTS created_by         UUID
        """))
        session.execute(sa_text("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'document_analysis_runs_run_type_chk') THEN
                    ALTER TABLE document_analysis_runs ADD CONSTRAINT document_analysis_runs_run_type_chk
                        CHECK (run_type IN ('initial', 'revision'));
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'document_analysis_runs_revision_status_chk') THEN
                    ALTER TABLE document_analysis_runs ADD CONSTRAINT document_analysis_runs_revision_status_chk
                        CHECK (revision_status IS NULL OR revision_status IN
                            ('preparing', 'auditing', 'proposed', 'accepted', 'rejected', 'superseded'));
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'document_analysis_runs_base_run_required_chk') THEN
                    ALTER TABLE document_analysis_runs ADD CONSTRAINT document_analysis_runs_base_run_required_chk
                        CHECK (run_type <> 'revision' OR base_run_id IS NOT NULL);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'document_analysis_runs_base_run_fk') THEN
                    ALTER TABLE document_analysis_runs ADD CONSTRAINT document_analysis_runs_base_run_fk
                        FOREIGN KEY (base_run_id) REFERENCES document_analysis_runs(id);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'document_analysis_runs_parent_revision_fk') THEN
                    ALTER TABLE document_analysis_runs ADD CONSTRAINT document_analysis_runs_parent_revision_fk
                        FOREIGN KEY (parent_revision_id) REFERENCES document_analysis_runs(id);
                END IF;
            END $$
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_document_analysis_runs_run_type ON document_analysis_runs(run_type)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_document_analysis_runs_base     ON document_analysis_runs(base_run_id)"))
        session.execute(sa_text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS active_analysis_run_id UUID"))
        session.execute(sa_text("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'documents_active_analysis_run_fk') THEN
                    ALTER TABLE documents ADD CONSTRAINT documents_active_analysis_run_fk
                        FOREIGN KEY (active_analysis_run_id) REFERENCES document_analysis_runs(id);
                END IF;
            END $$
        """))
        session.execute(sa_text("""
            UPDATE documents d
            SET active_analysis_run_id = sub.id
            FROM (
                SELECT DISTINCT ON (document_id) document_id, id
                FROM document_analysis_runs
                WHERE status = 'completed'
                ORDER BY document_id, completed_at DESC NULLS LAST, created_at DESC, id DESC
            ) sub
            WHERE d.id::text = sub.document_id
              AND d.active_analysis_run_id IS NULL
        """))

        # Migration 020: 学習者体験レイヤー(B層) 関心痕跡 InterestTraceStore (Stage 3)
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS interest_traces (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id       UUID NOT NULL,
                course_id     TEXT NOT NULL,
                topic_id      TEXT,
                kind          TEXT NOT NULL DEFAULT 'raw',
                payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
                weight        REAL NOT NULL DEFAULT 1.0,
                status        TEXT NOT NULL DEFAULT 'open',
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                analyzed_at   TIMESTAMPTZ
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_interest_traces_user_course ON interest_traces(user_id, course_id)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_interest_traces_status ON interest_traces(status)"
        ))

        # Migration 021: 承認・共有レイヤー(C層)
        # A層(生成パイプライン)には手を入れず、A層が出力した theory_components /
        # theory_claims を「読む側」として承認と共有の情報を新規テーブルに積む。
        # 既存テーブルは変更しない。設計上の確定: 承認は「説明バージョン(explanation)」単位。
        #
        # component_explanations: 1コンポーネントに複数の説明バージョンを並存させる。
        #   kind='standard' は A層/AI 由来の標準説明(遅延生成)、'personal' は教員の独自解釈。
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS component_explanations (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                component_id    UUID NOT NULL REFERENCES theory_components(id) ON DELETE CASCADE,
                course_id       TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
                kind            TEXT NOT NULL DEFAULT 'personal'
                                    CHECK (kind IN ('standard', 'personal')),
                author_id       UUID REFERENCES users(id) ON DELETE SET NULL,
                title           TEXT NOT NULL DEFAULT '',
                body            TEXT NOT NULL DEFAULT '',
                backing_claims  JSONB NOT NULL DEFAULT '[]'::jsonb,
                origin_query_id TEXT NOT NULL DEFAULT '',
                review_status   TEXT NOT NULL DEFAULT 'teacher_review_required'
                                    CHECK (review_status IN ('teacher_review_required', 'teacher_approved', 'needs_revision', 'rejected')),
                shared          BOOLEAN NOT NULL DEFAULT FALSE,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_component_explanations_component ON component_explanations(component_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_component_explanations_author ON component_explanations(author_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_component_explanations_shared ON component_explanations(shared) WHERE shared = TRUE"))
        # 1コンポーネントに標準説明は1つだけ(遅延生成の二重作成を防ぐ)
        session.execute(sa_text("CREATE UNIQUE INDEX IF NOT EXISTS uq_component_explanations_standard ON component_explanations(component_id) WHERE kind = 'standard'"))

        # component_endorsements: 個々の教員の承認を1行ずつ記録(theory_review_events=状態遷移の監査ログ
        #   とは役割が異なり、こちらは「現在有効な承認の集合」)。承認は explanation 単位。
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS component_endorsements (
                id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                explanation_id UUID NOT NULL REFERENCES component_explanations(id) ON DELETE CASCADE,
                endorser_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                level          TEXT NOT NULL DEFAULT 'endorsed'
                                   CHECK (level IN ('provisional', 'endorsed', 'strong')),
                expertise_tag  TEXT NOT NULL DEFAULT '',
                note           TEXT NOT NULL DEFAULT '',
                revoked        BOOLEAN NOT NULL DEFAULT FALSE,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(explanation_id, endorser_id)
            )
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_component_endorsements_explanation ON component_endorsements(explanation_id)"))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_component_endorsements_endorser ON component_endorsements(endorser_id)"))

        # component_citations: 他教員が承認済み説明を再利用・引用したことを帰属付きで記録。
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS component_citations (
                id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                explanation_id    UUID NOT NULL REFERENCES component_explanations(id) ON DELETE CASCADE,
                citing_course_id  TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
                citing_user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        session.execute(sa_text("CREATE INDEX IF NOT EXISTS idx_component_citations_explanation ON component_citations(explanation_id)"))

        # 承認の重みの集計ビュー(テーブルは持たず endorsements から都度算出=last-write 競合を避ける)。
        session.execute(sa_text("""
            CREATE OR REPLACE VIEW component_explanation_endorsement_summary AS
            SELECT
                explanation_id,
                COUNT(*) FILTER (WHERE NOT revoked)                                              AS endorser_count,
                COUNT(*) FILTER (WHERE NOT revoked AND level = 'strong')                         AS strong_count,
                COUNT(*) FILTER (WHERE NOT revoked AND level = 'provisional')                    AS provisional_count,
                COUNT(DISTINCT expertise_tag) FILTER (WHERE NOT revoked AND expertise_tag <> '') AS expertise_breadth
            FROM component_endorsements
            GROUP BY explanation_id
        """))

        # Migration 022: TensionMiningAgent (B層) — 違和感候補のインデックス（列追加ゼロ）
        # interest_traces を kind='tension' で拡張利用する。候補は status='candidate' で
        # 保存され、学習者本人の confirm/dismiss でのみ状態遷移する（P1/P4）。
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_interest_traces_kind_status ON interest_traces(kind, status)"
        ))
        session.execute(sa_text("""
            CREATE INDEX IF NOT EXISTS idx_interest_traces_candidate
                ON interest_traces(user_id, course_id)
                WHERE kind = 'tension' AND status = 'candidate'
        """))

        # Migration 023: 分野の地図 — 修正報告フロー (Issue D)
        # 骨格への修正報告を帰属つき(匿名不可)・骨格バージョンつきで記録する。
        # レビューは既存の C層教員レビュー導線を流用(監査は theory_review_events)。
        # 正本リファレンス: backend/db/023_atlas_correction_reports.sql
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS atlas_correction_reports (
                id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                kind             TEXT NOT NULL DEFAULT 'map_correction',
                cartridge_id     TEXT NOT NULL DEFAULT '',
                skeleton_version TEXT NOT NULL,
                node_id          TEXT NOT NULL DEFAULT '',
                region_id        TEXT NOT NULL DEFAULT '',
                level            INTEGER NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 3),
                node_label       TEXT NOT NULL DEFAULT '',
                report_text      TEXT NOT NULL,
                reporter_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                status           TEXT NOT NULL DEFAULT 'pending'
                                     CHECK (status IN ('pending', 'accepted', 'declined', 'merged')),
                resolution_note  TEXT NOT NULL DEFAULT '',
                resolved_by      UUID REFERENCES users(id) ON DELETE SET NULL,
                resolved_at      TIMESTAMPTZ,
                merged_into      UUID REFERENCES atlas_correction_reports(id) ON DELETE SET NULL,
                applied_version  TEXT NOT NULL DEFAULT '',
                notified_at      TIMESTAMPTZ,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (node_id <> '' OR region_id <> '')
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_atlas_reports_cartridge_status ON atlas_correction_reports(cartridge_id, status)"
        ))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_atlas_reports_reporter ON atlas_correction_reports(reporter_id)"
        ))
        session.execute(sa_text("""
            CREATE INDEX IF NOT EXISTS idx_atlas_reports_unnotified
                ON atlas_correction_reports(reporter_id)
                WHERE resolved_at IS NOT NULL AND notified_at IS NULL
        """))

        # Migration 024: 分野の地図 — 状態導出キャッシュ atlas_overlay_cache (Issue E)
        # 骨格(カートリッジ同梱)の上へ、既存データから近似導出した状態を差分バッチで
        # 重ねるキャッシュ。導出規則は core/atlas_state.py に一箇所隔離する。
        # 正本リファレンス: backend/db/024_atlas_overlay_cache.sql
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS atlas_overlay_cache (
                id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                cartridge_id     TEXT NOT NULL,
                skeleton_version TEXT NOT NULL,
                entry_type       TEXT NOT NULL CHECK (entry_type IN ('region', 'node', 'chain', 'meta')),
                entry_id         TEXT NOT NULL,
                region_id        TEXT NOT NULL DEFAULT '',
                label            TEXT NOT NULL DEFAULT '',
                status           TEXT NOT NULL DEFAULT '',
                status_source    TEXT NOT NULL DEFAULT 'derived',
                verify_line      TEXT NOT NULL DEFAULT '',
                endorse_line     TEXT NOT NULL DEFAULT '',
                learn_enabled    BOOLEAN NOT NULL DEFAULT TRUE,
                evid_enabled     BOOLEAN NOT NULL DEFAULT TRUE,
                layout           JSONB NOT NULL DEFAULT '{}'::jsonb,
                placement        JSONB NOT NULL DEFAULT '{}'::jsonb,
                evidence         JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(cartridge_id, skeleton_version, entry_type, entry_id)
            )
        """))
        session.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS idx_atlas_overlay_cache_key ON atlas_overlay_cache(cartridge_id, skeleton_version)"
        ))
        session.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS atlas_overlay_dirty (
                cartridge_id TEXT PRIMARY KEY,
                reason       TEXT NOT NULL DEFAULT '',
                marked_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))

        session.commit()
        logger.info("Migrations (002-024) applied successfully.")

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


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "episteme-graph-api"}
