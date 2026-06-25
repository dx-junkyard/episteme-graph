# データモデル

[← ドキュメント目次](../README.md) ｜ [← アーキテクチャ概要](overview.md)

PostgreSQL（正本）のテーブル構成と、マイグレーションの履歴をまとめます。
ORM 定義は `backend/core/models.py`、スキーマ初期化は `backend/db/init.sql`、以降の変更は `backend/db/0NN_*.sql` です。

---

## 1. テーブルを関心ごとにグルーピング

### 認証・ユーザー
| テーブル | 役割 | 主なカラム |
|---|---|---|
| `users` | アカウント | `id(UUID)`, `email(UNIQUE)`, `role`(learner/instructor/admin), `password_hash`, `auth_provider` |
| `sessions` | トークンセッション | `id`, `user_id`, `token_hash`, `expires_at`, `ip_address`, `user_agent` |

> ロール名は DB 上は `learner` / `instructor` / `admin`。アプリ上の STUDENT / TEACHER / SYSTEM_ADMIN へマッピングされます（[認証・権限](../features/auth-visibility.md)）。

### 教材・ドキュメント
| テーブル | 役割 | 主なカラム |
|---|---|---|
| `documents` | 教材メタデータ | `id`, `title`, `authors[]`, `filename`, `status`(uploaded/processing/completed/failed), `knowledge_graph(JSONB)`, `source_path`, `visibility`, `group_id`, `active_analysis_run_id` |
| `chunks` | テキスト + 埋め込み | `id`, `document_id`, `chunk_index`, `text`, `chunk_type`, `embedding(vector 3072)`, `material_id`, `smiles_dsl`, `variables(JSONB)`, `ancestors(JSONB)`, `display_text`, `section_id`, `block_ids` |
| `document_embeddings` | 派生埋め込み（DSL グラフ・要約など） | `document_id`, `embedding_type`, `text`, `embedding`, `metadata(JSONB)` |

### コース・学習
| テーブル | 役割 | 主なカラム |
|---|---|---|
| `learning_courses` | コース定義（マスター） | `id(TEXT)`, `user_id`, `title`, `data(JSONB)`, `visibility`, `group_id`, `is_template`, `is_published`, `cloned_from`, `description` |
| `learning_states` | ユーザーごとの受講状態（可変層） | `user_id`, `course_id`, `progress_data` …（マスターと進捗の分離: マイグレーション 011） |
| `course_builder_sessions` | コースビルダーの対話履歴 | `id`, `user_id`, `history(JSONB)`, `course_draft(JSONB)`, `status` |
| `learner_profiles` | 学習者状態 | `user_id(UNIQUE)`, `current_level` |
| `learner_mastered_concepts` | 習得済み概念 | `learner_id`, `concept_id`, `mastered_at`, `confidence` |
| `learner_struggling_concepts` | つまずき | `learner_id`, `concept_id`, `attempts`, `last_attempt_at` |
| `learner_misconception_corrections` | 誤解の訂正記録 | `learner_id`, `concept_id`, `wrong_statement`, `correct_statement` |

### 対話履歴
| テーブル | 役割 |
|---|---|
| `chat_sessions` / `chat_messages` | 会話セッションと個別メッセージ（`is_correction` で訂正を記録） |
| `learning_chat_history` | トピック単位のチャット履歴（前提知識チェックの判定に使用） |
| `unanswered_query_logs` | システムが答えられなかった質問（スキーマ進化の入力） |
| `student_stumble_events` | 学生のつまずきイベント（教員向け分析） |

### 講義モード
| テーブル | 役割 |
|---|---|
| `lecture_audio_cache` | TTS 音声キャッシュ（`chunk_id` + `voice` UNIQUE, `audio_data`, `duration_ms`, `word_timestamps`） |
| `lecture_sessions` / 関連状態 | レクチャーの再生状態（マイグレーション 006） |

### グループ・開示範囲
| テーブル | 役割 |
|---|---|
| `groups` | グループ（`invite_code` UNIQUE, `created_by`） |
| `group_members` | メンバーシップ（`role`: admin/member） |
| `group_invitations` | 招待（`status`: pending/accepted/declined/revoked） |
| `course_group_permissions` | コース×グループ権限（viewer/editor） |

### スキーマ進化
| テーブル | 役割 |
|---|---|
| `schema_ontology_types` | 動的 OntologyType（`is_builtin` で組込/追加を区別） |
| `schema_predicates` | 動的 CorePredicate |
| `schema_proposals` / `schema_proposal_items` | スキーマ拡張提案とその項目 |
| `reextraction_jobs` | スキーマ更新後の再抽出ジョブ |

### 理論コンポーネント / 理論操作グラフ
| テーブル | 役割 |
|---|---|
| `theory_components` | 再利用可能な理論ユニット（inputs/outputs/preconditions/constraints/dependencies） |
| `theory_claims` | ソース由来の atomic claim（support_status, evidence_text, review_status） |
| `theory_component_links` | コンポーネント間の関係 |
| `theory_component_graphs` | TheoryOperationGraph の JSON（ドキュメント単位） |
| `theory_review_events` | 状態変更の監査ログ |

### パイプライン実行・リビジョン
| テーブル | 役割 |
|---|---|
| `document_analysis_runs` | Agent パイプライン実行履歴（`status`, `current_stage`, `stage_outputs(JSONB)`, `run_type`, `base_run_id`, `revision_status`, `created_by`） |
| `background_tasks` | 非同期ジョブ追跡（`task_type`, `status`, `result_data`, `error_message`） |

---

## 2. 重要な設計パターン

### マスター / 個人レイヤーの分離（#133, マイグレーション 011）
コース本体（`learning_courses`, 不変のマスター）と、ユーザーごとの進捗（`learning_states`, 可変）を分離。
公開テンプレートを「クローン」する代わりに、マスターを単一の真実とし、受講者の進捗だけを別テーブルに持たせます。
`learning_states` に `UNIQUE(user_id, course_id)` を置き、二重受講を防止。

### 非同期バックグラウンドタスク（#63, マイグレーション 005）
重い処理（教材解析、スクリプト/音声生成、再抽出）は 202 を返して `task_id` を発行し、
クライアントは `GET /api/admin/tasks/{task_id}` でポーリングします。状態は pending → processing → completed/failed。

### リビジョンランによる反復改善（#, マイグレーション 019）
`document_analysis_runs` に `run_type`(initial/revision)・`base_run_id`・`parent_revision_id`・`revision_status`
（preparing → auditing → proposed → accepted/rejected/superseded）を追加。
`documents.active_analysis_run_id` が現在アクティブな（承認済み）ランを指します。
複数のリビジョン候補が並存でき、アクティブは常に 1 つ。

---

## 3. マイグレーション一覧

`backend/db/` のファイルを起動時に冪等適用します。

| マイグレーション | 主な追加内容 |
|---|---|
| `init.sql` | 基盤スキーマ（users, documents, chunks, learning_courses, chat_*, learner_profiles …） |
| `002_a1_a2_a3.sql` | コースビルダー永続化・学生への公開・前提知識（course_builder_sessions, course_group_permissions, テンプレ/公開フラグ） |
| `003_unanswered_queries.sql` | 未回答クエリログ（unanswered_query_logs） |
| `004_schema_evolution.sql` | 動的スキーマ進化（schema_ontology_types, schema_predicates, schema_proposals, reextraction_jobs） |
| `005_background_tasks.sql` | 非同期タスク追跡（background_tasks） |
| `006_lecture_mode.sql` | 講義モード（TTS・再生状態） |
| `007_drop_arxiv_id.sql` | スキーマ整理（arxiv_id 削除） |
| `008_display_text.sql` | chunks に display_text 追加 |
| `009_groups_visibility.sql` | グループと Visibility（public/group/private） |
| `010_course_group_permissions.sql` | コース×グループ権限 |
| `011_course_states_separation.sql` | マスター/個人レイヤーの分離 |
| `012_graph_suggestions.sql` | グラフへの修正・誤解サジェスト |
| `013_theory_components.sql` | 理論コンポーネント群（theory_*） |
| `014_section_assembly_status.sql` | セクション組立状態 |
| `015_document_pipeline.sql` | ドキュメントファースト・パイプライン（document_analysis_runs, document_embeddings, chunks へ section_id/block_ids） |
| `016_embedding_dim_3072.sql` | pgvector 次元 3072 へ（インデックス再構築） |
| `017_tex_references_mentions.sql` | TeX 参照・mention の拡張 |
| `018_course_builder_session_status.sql` | コースビルダーセッションの status |
| `019_revision_runs.sql` | リビジョンラン（run_type, base_run_id, revision_status; documents.active_analysis_run_id） |

---

## 4. Neo4j 側のグラフ

PostgreSQL とは別に、Neo4j に概念グラフを保持します（走査専用）。

- 概念ノード ＋ エッジ: `REQUIRES`（前提）, `RELATES_TO`（関連）, `CONTAINS`（包含）
- チャンク↔概念のクロスリンク
- 構造的同型パターンとのマッチ: `MATCHES_PATTERN`（[batch.py の同型評価](../pipeline/theory-graph.md#構造的同型性評価)）
- システムメタ提案: `SystemMetaProposal` ノードと `RAISED_META_ISSUE` エッジ（`db.py`）

---

[← デプロイ構成](deployment.md) ｜ 次へ: [API とルーティング →](../backend/api.md)
