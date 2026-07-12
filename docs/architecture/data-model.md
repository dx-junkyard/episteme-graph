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

### 学習者体験・関心痕跡（B層, マイグレーション 020 / 022）
| テーブル | 役割 |
|---|---|
| `interest_traces` | 学習者の問い・寄り道・誤答・違和感（tension）候補の痕跡。`kind`（raw / question / detour / misconception / **tension**）、`status`（open / revisited / resolved / **candidate / dismissed / articulated / connected / abstracted**）、`payload(JSONB)` に tension_type / paraphrase / evidence_quote / confidence / tension_hint / casual などを保持。tension の candidate / dismissed は「問いの軌跡」には出さず、本人向けダイジェスト経由でのみ提示 |

> tension 行は [TensionMiningAgent](../backend/rag-chat.md)（B層, マイグレーション 022）が `status='candidate'` で生成し、
> 本人の confirm / dismiss を経てのみ確定します。教員へは k-匿名化した集計のみ提示されます。

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
| `theory_review_events` | 状態変更の監査ログ（`entity_type`: claim / component / endorsement / explanation / citation） |

### 承認・共有レイヤー（C層, マイグレーション 021）
A層（生成パイプライン）を書き換えず、その上に「教員による査読承認」と「教員間の共有」を積む層。詳細は [承認・共有レイヤー](../features/endorsement-sharing.md)。

| テーブル | 役割 |
|---|---|
| `component_explanations` | 1コンポーネントに複数の説明バージョンを並存（`kind='standard'` はA層 summary から遅延生成 / `kind='personal'` は教員の独自解釈）。`backing_claims`, `origin_query_id`, `review_status`, `shared` |
| `component_endorsements` | 個々の教員の承認を1行ずつ記録（**explanation 単位**）。`UNIQUE(explanation_id, endorser_id)` で二重カウント防止、取り消しは `revoked=TRUE`（履歴保持）。`level`(provisional/endorsed/strong), `expertise_tag` |
| `component_citations` | 承認済み説明の再利用・引用を帰属付きで記録 |
| `component_explanation_endorsement_summary`（VIEW） | endorsements から承認の厚みを都度集計（endorser_count / strong_count / provisional_count / expertise_breadth） |

### パイプライン実行・リビジョン
| テーブル | 役割 |
|---|---|
| `document_analysis_runs` | Agent パイプライン実行履歴（`status`, `current_stage`, `stage_outputs(JSONB)`, `run_type`, `base_run_id`, `revision_status`, `created_by`, マイグレーション041で `options(JSONB)` 追加 — アップロード時オプションのスナップショット） |
| `background_tasks` | 非同期ジョブ追跡（`task_type`, `status`, `result_data`, `error_message`） |

### 分野の地図（Field Atlas, マイグレーション 023・024・026・027・028）
3層モデル（S=骨格 / C=状態導出キャッシュ / P=個人層 `interest_traces`）で「いま学習中の箇所が
分野全体のどこか」を示す。詳細は `docs/features/field_atlas_*.md` 系設計書群、CLAUDE.md
「分野の地図（Field Atlas）」参照。

| テーブル | 役割 |
|---|---|
| `atlas_correction_reports`（023） | 骨格への修正報告（帰属必須・匿名不可、`reporter_id NOT NULL`）。`status`(pending/accepted/declined/merged)。採用は骨格次版に反映され `applied_version` に刻印 |
| `atlas_overlay_cache`（024） | 状態導出キャッシュ（3層モデルの C）。`entry_type`(region/node/chain/meta) 単位で `(cartridge_id, skeleton_version)` ごとに保持。既存データからの決定論的導出のみでリアルタイム LLM 生成はしない |
| `atlas_overlay_dirty`（024） | `atlas_overlay_cache` の差分更新契機管理（イベント発生で1行立ち、refresh 処理後に消える） |
| `atlas_cue_events`（026） | 見晴らしの導線（ミニマップ・初回ログイン等）の内部計測。`cue`/`event`。数値・進捗率はユーザーに見せない |
| `atlas_skeletons`（027） | 骨格（3層モデルの S）の DB 管理化。`status`(draft/frozen)。draft は domain につき1行（`revision` 楽観ロック、衝突は409）、凍結版は `(domain_key, version)` で複数版を履歴保持 |
| `atlas_domain_meta`（028） | カートリッジファイルの無い新分野（例: `modified_gravity`）のメタデータ永続化。`domain_key` 単位、generate API の `body.domain` 省略時のフォールバックに使用 |

> マイグレーション 025（structure_anchor）は新規テーブル・新規カラムを追加しない
> （既存 `interest_traces` へのインデックス追加のみ）ため、次の学習者体験セクションを参照。

### D層（疑義・認識的地位台帳, マイグレーション 029〜033）
「合意の強さ」と「検証の強さ」をデータ構造レベルで分離する層。A層は読むだけ・非改変。
正本は `docs/features/doubt_layer_issues.md`、CLAUDE.md「D層（Doubt Layer）」参照。

| テーブル | 役割 |
|---|---|
| `epistemic_ledger`（029） | 認識的地位台帳。`UNIQUE(target_id, target_type)`。`verification_status`(directly_verified/indirectly_supported/untested/refuted/unknown) と `verification_scopes(JSONB配列)`（単一ブールにしない。0件は正常状態）。`scope_candidates` はLLM候補（教員確定まで本体に入らない）。`load_score` は生数値をAPIに出さない |
| `assumption_nodes`（030） | 暗黙前提マイニングの受け皿。`origin`(mined_gap/mined_corpus/naive_aggregate/manual)、`status`(candidate/confirmed/operationalized/dismissed)。`cluster_key` の一意性で再候補化を防止 |
| `challenges`（031） | 疑義（承認 `component_endorsements` と対になる一級市民）。`challenger_id NOT NULL` + `reason <> ''`（匿名不可）。`challenge_type`(scope_extrapolation/untested_in_domain/definitional/hidden_lemma) |
| `verification_proposals`（032） | 疑義 → 検証提案への昇格。`challenge_id` 参照。昇格時に元 challenge を `led_to_verification` に遷移 |
| `counterfactual_sessions`（033） | 反実仮想（前提を仮に偽に倒す）。`collapsed_subgraph`/`surviving_subgraph`/`indeterminate_subgraph` を非LLM決定論計算で保存。`shared_scope`(private/group/public) は既存 Visibility 語彙を流用 |

### 横断ユーティリティ層（Admin Copilot, マイグレーション 034）
| テーブル | 役割 |
|---|---|
| `assistant_actions`（034） | Admin Copilot が代行した操作の戻す台帳。`before_snapshot`/`after_snapshot`（P3）、`status`(applied/reverted/failed/confirm_pending)。`reversible=FALSE` の操作は戻す UI で無効化 |

### パイプライン成果のグループ共有（マイグレーション 035）
| テーブル | 役割 |
|---|---|
| `document_group_permissions`（035） | ドキュメント×グループ権限マッピング。`course_group_permissions`（010）の完全な移植版。`PRIMARY KEY(document_id, group_id)`、`permission`(viewer/editor)。成果テーブル（theory_*）には列を足さず、権限はドキュメント単位に集約してそこから継承する |

### 再構成ループ（Reconstruction Loop, R層, マイグレーション 036）
学習者に理論の再構成（予測/言い直し）をさせ、`theory_claims` を答えキーとして構造照合する。
正本は `docs/features/reconstruction_loop_design.md`。

| テーブル | 役割 |
|---|---|
| `reconstruction_items`（036） | claim → ELICIT 変換の出題。`elicit_mode`(predict/restate/symbol)、`status`(auto/flagged/retired/confirmed)。LLM が自動オーサリングし教員確定なしに配信、教員は事後の監査役 |
| `learner_reconstructions`（036） | 学習者の産出物・改訂履歴。`machine_verdict`(match/mismatch/na)、`self_check`(agreed/disagreed/verdict_wrong)、`revision_of` で自己参照（改訂チェーン） |
| `reconstruction_item_health`（036, VIEW） | item 健全性の集計ビュー（`n_responses`/`n_mismatch`/`n_verdict_dissent`/`n_users` 等）。疑わしさランクはアプリ側（`core/reconstruction/health.py`）で計算しSQLに埋め込まない |

### 共有物のバージョン管理（V層, マイグレーション 037）
発行版（Release）による不変スナップショット化 + 更新通知 + 削除猶予。正本は
`docs/features/shared_versioning_design.md`、CLAUDE.md「共有物のバージョン管理（V層）」参照。

| テーブル | 役割 |
|---|---|
| `shared_versions`（037） | 不変 Release。`object_type CHECK('course','document')` / `object_id TEXT`（ポリモーフィック・FK なし）/ `version_no` / `snapshot(JSONB)`。`UNIQUE(object_type, object_id, version_no)` |
| `shared_version_state`（037） | `PRIMARY KEY(object_type, object_id)`。`active_release_id`/`latest_version_no`/`lifecycle`(active/pending_deletion/purged)/削除予約情報 |
| `shared_version_subscriptions`（037） | 消費者のピン。`UNIQUE(object_type, object_id, subscriber_id)`。`pinned_release_id` が消費者の見る版（所有者が新版発行しても既存ピンは動かない） |
| `share_notifications`（037） | 通知インボックス。`kind`(version_published/deletion_scheduled/deletion_cancelled/deleted)、`read_at`/`acted_at` |
| `component_citations`（021 を ALTER, 037） | 引用の版固定列 `source_object_type`/`source_object_id`/`source_release_id`/`source_version_no` を追加 |

### 状態管理・通知基盤（マイグレーション 038）
教材・コースの「現在状態」は既存テーブルからの決定論的投影（`core/status/projector.py`、保存
しない）。本マイグレーションは「遷移が起きた事実」と汎用通知インボックスのみ追加する。正本は
`docs/features/status_notification_design.md`（設計時点では「039 想定」と記載されたが実装では
038 が割り当てられた。039 は後発の G層が使用）。

| テーブル | 役割 |
|---|---|
| `status_events`（038） | 遷移イベント（append-only の事実ストリーム）。`event_kind`（例 `material.analysis_completed`）、`source_table`/`source_id` に対し `UNIQUE(source_table, source_id, event_kind)` で冪等 |
| `user_notifications`（038） | 汎用通知インボックス（`share_notifications` と同形・V層非改変）。`read_at`/`dismissed_at` で状態遷移（行削除しない） |

### ガイダンス層（G層, マイグレーション 039）
「次にやること」バッジ + 状態導出型 To-Do。正本は `docs/features/guidance_layer_design.md`
（設計時点では「038」と記載されたが実装では 039 が割り当てられた。038 は状態管理・通知基盤が
先に使用）。

| テーブル | 役割 |
|---|---|
| `assistant_step_dismissals`（039） | Next Steps の却下台帳（To-Do 本体は保存せず毎回投影する）。`UNIQUE(user_id, step_key)`。`step_key = "{rule_id}:{target_id}"`。復元は行削除でなく `revoked` への遷移。初回ログイン cue の一度きりフラグも `step_key='cue:first_login'` 行で代用（専用テーブルを増やさない） |

### レクチャースライド同期 + 音声言語切替（マイグレーション 040）
正本は `docs/features/lecture_slide_sync_design.md`。新規テーブルは無く、既存テーブルへの列追加のみ。

| 変更対象 | 内容 |
|---|---|
| `lecture_audio_cache`（040, ALTER） | `slide_index`（既定0）/ `language`（既定'ja'）を追加。`UNIQUE(chunk_id, voice)` → `UNIQUE(chunk_id, slide_index, voice)` に張替え |
| `chunks`（040, ALTER） | `spoken_language` を追加（原稿の生成言語。NULL は 'ja' とみなす） |

### 画像読み取りパイプライン（マイグレーション 041）
正本は `docs/features/image_pipeline_knowledge_library_design.md`。既存 agent は非改変。

| テーブル/変更 | 役割 |
|---|---|
| `document_analysis_runs`（041, ALTER） | `options(JSONB)` を追加（アップロード時オプション `analyze_images` 等のスナップショット。`stage_outputs` への相乗り禁止で独立列） |
| `document_figures`（041） | PDF から抽出した図画像のレジストリ。`extraction_method`(embedded/region_render)、`UNIQUE(document_id, figure_key)`（再解析は upsert） |
| `theory_components.component_type`（041, CHECK 拡張） | `apparatus`/`instrument`/`part` を追加（装置・実験機器コンポーネント候補を組み立て可能に） |

### 分野別ナレッジライブラリ（L層, マイグレーション 042）
`atlas_skeletons`（027）と同じ「draft が正本・凍結版が履歴・カートリッジ同梱シードを起動時に
冪等取込」パターンを踏襲。正本は `docs/features/image_pipeline_knowledge_library_design.md`。

| テーブル | 役割 |
|---|---|
| `library_entries`（042） | エントリ本体（draft が正本）。`domain_key`（cartridge_id と同一名前空間）、`entry_type`(apparatus/theory_component)、`status`(active/retired)、`revision` 楽観ロック。削除 API は無く `retired` 遷移のみ |
| `library_entry_versions`（042） | 凍結版（不変・履歴保持）。パイプラインの retrieval はここだけを読む（draft は読まない）。`embedding vector(3072)` を凍結時に計算（pgvector 3072次元, マイグレーション016 を流用） |

### LLM トークン使用量推計（U層, マイグレーション 043）
正本は `docs/features/llm_usage_metering_design.md`。呼び出し側コードは変更せず `core/llm.py`
にフックを一元化する観測レイヤー。

| テーブル/ビュー | 役割 |
|---|---|
| `llm_usage_events`（043） | LLM 呼び出しのトークン使用量台帳（append-only、削除 API を作らない）。`usage_source`(reported/estimated_tokenizer/estimated_heuristic) を分離集計。`feature`（帰属、既定 `unattributed`）。FK なし・金額列なし（価格は集計時に価格表で都度換算） |
| `llm_usage_daily`（043, VIEW） | day × feature × model × usage_source の SUM 集計ビュー。専用カウンタテーブルは持たない |

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

### 承認は説明バージョン単位・重みは表示のみ（C層, マイグレーション 021）
承認（endorsement）はコンポーネントではなく **説明バージョン（explanation）単位** に付ける
（「標準説明を承認したのか、A先生の説明を承認したのか」を区別するため）。
承認の重みは `component_explanation_endorsement_summary` から算出し、アプリ層で段階ラベル化する
（例「専門家3名が承認」）。**数値スコアは学習者に提示しない**（B層と一貫し報酬化・点数化を避ける）。
claim 紐づけの最終確定は必ず教員が行い、AI 候補は `backing_claims` に `confirmed=false` で保持する。

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
| `020_interest_trace.sql` | 学習者体験レイヤー(B層) 関心痕跡（interest_traces） |
| `021_endorsement_sharing.sql` | 承認・共有レイヤー(C層)（component_explanations / component_endorsements / component_citations + 集計ビュー） |
| `022_tension.sql` | TensionMiningAgent(B層) — `interest_traces` を `kind='tension'` で拡張利用するためのインデックス追加（新規テーブル・新規カラムなし） |
| `023_atlas_correction_reports.sql` | 分野の地図 — 骨格への修正報告フロー（atlas_correction_reports） |
| `024_atlas_overlay_cache.sql` | 分野の地図 — 状態導出キャッシュ（atlas_overlay_cache, atlas_overlay_dirty） |
| `025_structure_anchor.sql` | 構造帰属型の問い記録(B層) — `interest_traces` へのインデックス追加のみ（新規テーブル・新規カラムなし） |
| `026_atlas_cue_events.sql` | 分野の地図 — 見晴らしの導線の内部計測・初回自動表示フラグ（atlas_cue_events） |
| `027_atlas_skeletons.sql` | 分野の地図 — 骨格（S層）のDB管理化（atlas_skeletons） |
| `028_atlas_domain_meta.sql` | 分野の地図 — カートリッジファイルの無い新分野のメタデータ永続化（atlas_domain_meta） |
| `029_epistemic_ledger.sql` | D層 — 認識的地位台帳（epistemic_ledger） |
| `030_assumption_nodes.sql` | D層 — 暗黙前提ノード（assumption_nodes） |
| `031_challenges.sql` | D層 — 疑義（challenges） |
| `032_verification_proposals.sql` | D層 — 検証提案（verification_proposals） |
| `033_counterfactual_sessions.sql` | D層 — 反実仮想セッション（counterfactual_sessions） |
| `034_assistant_actions.sql` | 横断ユーティリティ層(Admin Copilot) — 操作代行の戻す台帳（assistant_actions） |
| `035_document_group_permissions.sql` | ドキュメント×グループ権限マッピング（document_group_permissions、course_group_permissions=010 の完全な移植） |
| `036_reconstruction_loop.sql` | 再構成ループ(R層) — 出題・産出物・健全性ビュー（reconstruction_items, learner_reconstructions, reconstruction_item_health） |
| `037_shared_versioning.sql` | 共有物のバージョン管理(V層) — 発行版・状態・購読・通知（shared_versions ほか）+ component_citations へ版固定列追加 |
| `038_status_events_notifications.sql` | 状態管理・通知基盤 — 遷移イベント + 統合通知インボックス（status_events, user_notifications） |
| `039_assistant_step_dismissals.sql` | ガイダンス層(G層) — Next Steps 却下台帳（assistant_step_dismissals） |
| `040_lecture_slides.sql` | レクチャースライド同期 + 音声言語切替 — lecture_audio_cache へのスライド単位キー拡張・chunks.spoken_language 追加 |
| `041_image_pipeline.sql` | 画像読み取りパイプライン — document_figures 新設・document_analysis_runs.options 追加・component_type 語彙拡張 |
| `042_knowledge_library.sql` | 分野別ナレッジライブラリ(L層) — library_entries / library_entry_versions |
| `043_llm_usage_events.sql` | LLM トークン使用量推計(U層) — llm_usage_events + llm_usage_daily 集計ビュー |

> 注: マイグレーションは `backend/db/*.sql` を正本リファレンスとしつつ、実際の適用は
> `backend/api/main.py` の `_run_migrations()` に直書きした DDL を起動時に冪等適用する方式です。

---

## 4. Neo4j（撤去済み）

かつて PostgreSQL とは別に Neo4j へ概念グラフ（`REQUIRES` / `RELATES_TO` / `CONTAINS` エッジ、
チャンク↔概念のクロスリンク、[batch.py の同型評価](../pipeline/theory-graph.md#構造的同型性評価削除済み)
による `MATCHES_PATTERN` 等）を保持する設計だったが、書き込み経路（`core/db.py` / `core/batch.py`）の
呼び出し元がゼロで実質未使用だったため、インフラ（docker-compose の `neo4j` サービス）ごと
2026-07 に撤去済み。理論構造は現在 PostgreSQL の `theory_components` / `theory_claims` /
`theory_component_graphs`（本ページ §1〜3）に一元化されている。

---

[← デプロイ構成](deployment.md) ｜ 次へ: [API とルーティング →](../backend/api.md)
