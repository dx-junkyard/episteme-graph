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
| `learning_courses` | コース定義（マスター） | `id(TEXT)`, `user_id`, `title`, `data(JSONB)`, `visibility`, `group_id`, `is_template`, `is_published`, `description`, `llm_models(JSONB)`（※`cloned_from` は 011 で DROP 済み） |
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
| `chunk_graph_mentions` | チャンク本文中に現れたグラフ要素の言及（マイグレーション 012）。`chunk_id` → `chunks(id)` CASCADE、`element_type` は concept / relationship / formula / keyword / reference / citation の6値 CHECK（017 が旧4値 DB を治癒）、`UNIQUE(chunk_id, element_id, element_type)` |

### 学習者体験・関心痕跡（B層, マイグレーション 020 / 022）
| テーブル | 役割 |
|---|---|
| `interest_traces` | 学習者の問い・寄り道・誤答・違和感（tension）候補の痕跡。**`kind` 語彙の正本は `backend/core/trace_registry.py::TRACE_KINDS`**（raw / question / backstage_question / detour / misconception / tension / help_usage / intention / anchor_mark / frontier_interest。**新しい kind は登録簿に露出3宣言つきで足す**）。`status` は open / revisited / resolved / candidate / dismissed / articulated / connected / abstracted / superseded（表示ラベルの正本は `core/label_vocab.py::TRACE_STATUS_LABELS`。`superseded` は書き直し・削除で差し替えられた行で、worker・digest・問いの軌跡から除外される）。`payload(JSONB)` に tension_type / paraphrase / evidence_quote / confidence / tension_hint / casual などを保持。tension の candidate / dismissed は「問いの軌跡」には出さず、本人向けダイジェスト経由でのみ提示 |

> tension 行は [TensionMiningAgent](../backend/rag-chat.md)（B層, マイグレーション 022）が `status='candidate'` で生成し、
> 本人の confirm / dismiss を経てのみ確定します。教員へは k-匿名化した集計のみ提示されます。

### 講義モード
| テーブル | 役割 |
|---|---|
| `lecture_audio_cache` | TTS 音声キャッシュ（040 で `UNIQUE(chunk_id, slide_index, voice)` に張替え・`language` 列追加。`audio_data`, `duration_ms`, `word_timestamps`） |
| `lecture_sessions` / 関連状態 | レクチャーの再生状態（マイグレーション 006） |

### グループ・開示範囲
| テーブル | 役割 |
|---|---|
| `groups` | グループ（`invite_code` UNIQUE, `created_by`） |
| `group_members` | メンバーシップ（`role`: admin/member） |
| `group_invitations` | 招待（`status`: pending/accepted/declined/revoked） |
| `object_group_permissions`（旧 `course_group_permissions`, マイグレーション044で統合） | コース×グループ権限（viewer/editor）。`object_type='course'` 行が対応。詳細は後述「グループ権限テーブルの統合（マイグレーション 044）」参照 |

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
| `theory_review_events` | 状態変更の監査ログ。**`entity_type` の語彙カタログの正本は `backend/core/schema.py` の `AUDIT_ENTITY_*` 定数（`AUDIT_ENTITY_TYPES`。層が増えるたびに増える — 2026-09-03 時点 40 語彙）**。層をまたいで全ての確定操作がここに記帳される（claim / component / endorsement / explanation / citation から始まり、atlas / landscape / library / user_account / paper_discovery ほか）。ドキュメントに全列挙を書き写さない |

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

### 分野の地図（Field Atlas, マイグレーション 023・024・026・027・028・046・057）
3層モデル（S=骨格 / C=状態導出キャッシュ / P=個人層 `interest_traces`）で「いま学習中の箇所が
分野全体のどこか」を示す。詳細は `docs/features/field_atlas_*.md` 系設計書群、CLAUDE.md
「分野の地図（Field Atlas）」参照。

| テーブル | 役割 |
|---|---|
| `atlas_correction_reports`（023、046 で列追加） | 骨格への修正報告（帰属必須・匿名不可、`reporter_id NOT NULL`）。`status`(pending/accepted/declined/merged)。採用は骨格次版に反映され `applied_version` に刻印。046 で `incorporated_at`/`incorporated_by`/`incorporation_note` を追加し「採用」と「次版で反映済み」を区別 |
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

### パイプライン成果のグループ共有（マイグレーション 035 → 044 でテーブル統合）
成果テーブル（theory_*）には列を足さず、権限はドキュメント単位に集約してそこから継承する方針は
不変。当初は `course_group_permissions`（010）の完全な移植版として独立テーブル
`document_group_permissions`（035, `PRIMARY KEY(document_id, group_id)`）が実装されたが、
下記「グループ権限テーブルの統合（マイグレーション 044）」で `course_group_permissions` と
統合され、`object_group_permissions`（`object_type='document'` 行）に一本化された。

### グループ権限テーブルの統合（マイグレーション 044）
`course_group_permissions`（010）と `document_group_permissions`（035）は permission 語彙
（viewer/editor）・PK 形・インデックスまで完全に相似の構造だったため、V層（037）の
`object_type` ポリモーフィック方式を踏襲して1枚に統合した。

| テーブル | 役割 |
|---|---|
| `object_group_permissions`（044） | コース×グループ / ドキュメント×グループ権限の統合テーブル。`object_type CHECK('course','document')` + `object_id TEXT`（FK なしポリモーフィック）+ `group_id`（`groups(id)` に `ON DELETE CASCADE`）で `PRIMARY KEY(object_type, object_id, group_id)`。`permission`(viewer/editor)。`object_id` に FK が張れないため、course/document の削除経路（`_purge_course`/`_purge_document`/`services.delete_course_data`/`admin.delete_material`/`admin.delete_course` 等7箇所）が明示 `DELETE` で孤児行を防ぐ。旧2テーブルは移行後 `DROP TABLE` 済み（`010_course_group_permissions.sql` / `035_document_group_permissions.sql` はコメントのみのスタブに書き換え）。API パス・レスポンス形式は不変 |

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
| ~~`share_notifications`（037）~~ | 通知インボックス。当初は専用テーブルとして実装（`kind`(version_published/deletion_scheduled/deletion_cancelled/deleted)、`read_at`/`acted_at`）。**マイグレーション 045 で `user_notifications`（状態管理・通知基盤, 038）に統合済み**（`source='shared'` で区別）。詳細は下記「状態管理・通知基盤」節と「通知テーブルの統合（マイグレーション 045）」参照 |
| `component_citations`（021 を ALTER, 037） | 引用の版固定列 `source_object_type`/`source_object_id`/`source_release_id`/`source_version_no` を追加 |

### 状態管理・通知基盤（マイグレーション 038）
教材・コースの「現在状態」は既存テーブルからの決定論的投影（`core/status/projector.py`、保存
しない）。本マイグレーションは「遷移が起きた事実」と汎用通知インボックスのみ追加する。正本は
`docs/features/status_notification_design.md`（設計時点では「039 想定」と記載されたが実装では
038 が割り当てられた。039 は後発の G層が使用）。

| テーブル | 役割 |
|---|---|
| `status_events`（038） | 遷移イベント（append-only の事実ストリーム）。`event_kind`（例 `material.analysis_completed`）、`source_table`/`source_id` に対し `UNIQUE(source_table, source_id, event_kind)` で冪等 |
| `user_notifications`（038、045 で拡張） | 汎用通知インボックス。当初は `share_notifications`（V層, 037）と同形の独立テーブルだったが、マイグレーション 045 で両者を統合する存続テーブルとなり `source`(`status`\|`shared`)・`release_id`・`acted_at` 列を追加。`read_at`/`dismissed_at`/`acted_at` で状態遷移（行削除しない）。詳細は下記「通知テーブルの統合（マイグレーション 045）」参照 |

### 通知テーブルの統合（マイグレーション 045）
`share_notifications`（037, V層）と `user_notifications`（038, 状態管理・通知基盤）は
`id`/`recipient_id`/`payload`/`created_at`/`read_at` が完全に同型だったため、`user_notifications`
を存続テーブルとして統合した。差分列（V層固有の `source`/`release_id`/`acted_at`）は ALTER で
追加し、`share_notifications` は移行後 `DROP TABLE`（`037_shared_versioning.sql` の
share_notifications セクションはコメントのみのスタブに書き換え）。`source='status'` は状態管理・
通知基盤由来、`source='shared'` は V層由来の行を示し、両者を区別することで二重表示と
purge 誤削除を防止する。`kind` に DB CHECK は付けず、V層側の4値ゲートは
`core/versioning/schema.py::NOTIFICATION_KINDS` が Python 側で引き続き担う（open-vocab 設計の
状態層側に合わせた）。`routes/notifications.py` は単一クエリでの読み取りに簡素化され、旧来の
2テーブル個別問い合わせによる二重試行を廃止した。API 形式・フロントは不変。

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

### トピック教材ベースのレクチャー音声キャッシュ（マイグレーション 047）
正本は `docs/features/lecture_slide_sync_design.md`（トピック教材レクチャーへの拡張）。レクチャー受講の
表示をトピック教材（`student_material`/`spoken_script`）優先に揃えたことに伴い、音声キャッシュも
チャンク単位の `lecture_audio_cache`（040）とは別キーで持つ。

| テーブル | 役割 |
|---|---|
| `topic_lecture_audio_cache`（047） | トピック単位の TTS 音声キャッシュ。キーは `(course_id, topic_id, slide_index, voice)`（`course_id`/`topic_id` は FK なしのテキストキー）。トピックの授業用教材/読み上げ原稿を編集すると当該トピックの音声行が DELETE される |

### 画像読み取りパイプライン（マイグレーション 041・051・052・053・054）
正本は `docs/features/image_pipeline_knowledge_library_design.md`。既存 agent は非改変。

| テーブル/変更 | 役割 |
|---|---|
| `document_analysis_runs`（041, ALTER） | `options(JSONB)` を追加（アップロード時オプション `analyze_images` 等のスナップショット。`stage_outputs` への相乗り禁止で独立列） |
| `document_figures`（041） | PDF から抽出した図画像のレジストリ。`extraction_method`(embedded/region_render)、`UNIQUE(document_id, figure_key)`（再解析は upsert） |
| `theory_components.component_type`（041, CHECK 拡張） | `apparatus`/`instrument`/`part` を追加（装置・実験機器コンポーネント候補を組み立て可能に） |
| `document_figures.inner_labels`（051, ALTER） | 図領域内のテキストラベル抽出結果を追加（`JSONB`配列、`[{"text","bbox"}]`、決定論的・非LLM）。`apparatus_semantics` の `label_ref` 突合に使用 |
| `document_figures`（052, ALTER, #496） | 図の提示モード分類。vision 提案（`suggested_mode`/`mode_reason`/`analysis_profile`）と教員レビュー確定（`reviewed_mode`/`mode_review_status`/`mode_reviewed_by`/`mode_reviewed_at`）を分離して保持 |
| `document_figures`（053, ALTER） | モード別詳細分析（functions/ports/connections 等）の教員レビュー確定を分離保存（`reviewed_analysis_mode`/`reviewed_analysis_profile`/`analysis_review_status`/`analysis_reviewed_by`/`analysis_reviewed_at`/`analysis_review_source_annotation_id`）。再解析は AI 候補（`analysis_profile`）のみ更新し、レビュー済み内容は上書きしない |

### 分野別ナレッジライブラリ（L層, マイグレーション 042・050）
`atlas_skeletons`（027）と同じ「draft が正本・凍結版が履歴・カートリッジ同梱シードを起動時に
冪等取込」パターンを踏襲。正本は `docs/features/image_pipeline_knowledge_library_design.md`。

| テーブル | 役割 |
|---|---|
| `library_entries`（042） | エントリ本体（draft が正本）。`domain_key`（cartridge_id と同一名前空間）、`entry_type`(apparatus/theory_component)、`status`(active/retired)、`revision` 楽観ロック。削除 API は無く `retired` 遷移のみ |
| `library_entry_versions`（042） | 凍結版（不変・履歴保持）。パイプラインの retrieval はここだけを読む（draft は読まない）。`embedding vector(3072)` を凍結時に計算（pgvector 3072次元, マイグレーション016 を流用） |
| `library_entries.standardization_status`（050, ALTER） | 標準化判定 worker（Phase S、`core/deliberation/standardization/`）の確定先ガバナンス列。`standard/field_standard/emerging_common/novel/unknown`。draft 本文編集（`revision` 楽観ロック）とは独立した関心事として扱う |

### LLM トークン使用量推計（U層, マイグレーション 043）
正本は `docs/features/llm_usage_metering_design.md`。呼び出し側コードは変更せず `core/llm.py`
にフックを一元化する観測レイヤー。

| テーブル/ビュー | 役割 |
|---|---|
| `llm_usage_events`（043） | LLM 呼び出しのトークン使用量台帳（append-only、削除 API を作らない）。`usage_source`(reported/estimated_tokenizer/estimated_heuristic) を分離集計。`feature`（帰属、既定 `unattributed`）。FK なし・金額列なし（価格は集計時に価格表で都度換算） |
| `llm_usage_daily`（043, VIEW） | day × feature × model × usage_source の SUM 集計ビュー。専用カウンタテーブルは持たない |

### W層（要素検討ワークスペース, マイグレーション 048・049・064）
教員が図・理論コンポーネント・claim・数式などパイプライン成果を「深く検討」するための対話・注釈層。
A層は非改変。正本は `docs/features/element_deliberation_workspace_design.md`（親文書
`docs/features/knowledge_network_vision.md`）。設計書は当初 migration 046 への同乗を想定していたが、
046/047 が先に他機能へ割り当てられたため実装は 048（Phase W-β）・049（Phase 2）を使う。

| テーブル | 役割 |
|---|---|
| `element_identity_links`（048） | 同一性リンク（Phase W-β）。インスタンス側（figure/theory_component/theory_claim/equation、`scope='document'` のポリモーフィック行・FK なし）と共通部品側（`shared_part_id` → `library_entries` への実FK）を非破壊に対応付ける。`status`(candidate/confirmed/rejected)。一意制約は `instance_document_id` を含む4列（equation の `element_id` は論文間で衝突しうるため） |
| `deliberation_sessions`（049） | 対話的検討（Phase 2）のセッション。`scope`(document/domain)、`element_type`/`element_id`、`messages(JSONB)`（追記のみ） |
| `element_annotations`（049） | 候補注釈（Phase 2）。`kind`(meaning/decomposition/positioning_note/interpretation/identity/standardization)、`status`(candidate/committed/dismissed)。`committed_target` にコミット先の既存構造を記録 |

> `deliberation_sessions.element_type` の CHECK は後続 migration で拡張されている:
> 064 で `evidence` / `derivation`（W層 Phase 5）、075 で `document_graph`（グラフ対話レビューの
> 疑似要素型。`element_annotations` 側の CHECK は**変更していない** — グラフ全体対話は候補注釈を
> 生成しないため）。

### 二層説明（generic / contextual, マイグレーション 055・056・062）
要素ごとの説明を「一般的な説明（generic）」と「この論文の文脈での説明（contextual）」に
分けて並存させる台帳。正本は `docs/features/hierarchical_context_explanation_design.md`。

| テーブル / 変更 | 役割 |
|---|---|
| `theory_components.thesis_context` / `theory_claims.thesis_refs`（055, ALTER） | thesis 構造メタの DB 永続化（次回再解析で埋まる冪等列） |
| `element_explanations`（056） | 全要素型ポリモーフィックな説明台帳。`kind`(generic/contextual)、`status`(candidate/approved/dismissed/superseded)。要素側テーブルに列を足さない |
| `element_explanations`（062, ALTER） | element_type に `'document'`（element_id = document_id）と `role`（NULL または `'discussion_seed'`）を追加。discuss 開幕素材オーサリングが同じ台帳に相乗りする |

### 利用者マニュアル KB（help_kb, マイグレーション 058・059）
`docs/manual` を AI アシスタントの知識源にする層。**`chunks` への相乗りは禁止**（全域検索の
教材回答へ混入するため）。正本は `docs/features/manual_help_kb_design.md`。

| テーブル | 役割 |
|---|---|
| `manual_sections`（058） | ベクトル補助層（Phase 3①）。専用テーブルで `chunks` を汚染しない。全置換スナップショット同期（孤児行は同一トランザクションで DELETE — 設計明示の例外）。凍結検証違反時は埋め込まない |
| `manual_kb_drafts` / `manual_kb_versions` / `manual_kb_state`（059） | DB draft/freeze ストア（Phase 3②）。draft は `revision` 楽観ロック（衝突 409）、版は append-only。**配信既定は files のまま**で、DB 配信は freeze 実行後のみ |

### discuss 観測基盤（マイグレーション 060）
discuss Phase 3 の着手判断のための内部計測。正本は `docs/features/discuss_observation_design.md`
（DO1〜DO6: 本文非含有 / 仮名化 / 学習者に数値非表示 / 削除 API なし / 参考目安を自動ゲートにしない）。

| テーブル | 役割 |
|---|---|
| `discuss_metric_events`（060） | 発話本文を含まない append-only のイベント台帳（FK なし）。理解サイクルの `cycle_*` 語彙も同じ表に載る |

### 場面別 LLM モデル選択（M層, マイグレーション 061）
モデル決定の正本は `backend/core/llm_policy.py`。DB 行は解決順序のうち user / system 段。

| テーブル | 役割 |
|---|---|
| `llm_model_policies`（061） | 場面（scene）ごとのモデル指定。`scope`(system\|user)。起動時に `*_LLM_MODEL` env を `scope='system'` 行として冪等シード（既存 DB 行は上書きしない） |

### 教材図スタジオ（マイグレーション 063）
AI 対話で生成した説明図（SVG）を `![[figure:id]]` で教材に埋め込む層。正本は
`docs/features/teaching_figure_studio_design.md`。

| テーブル | 役割 |
|---|---|
| `course_teaching_figures`（063） | 生成図。`svg_source` が正本で MinIO の `teaching/{course_id}/{id}.svg` は配信スナップショット。`status`(draft/adopted/retired)・行削除 API なし・`revisions(JSONB)` に旧版を append |
| `teaching_figure_suggestions`（063） | 「図があると良さそうな箇所」のギャップ候補。再生成は candidate のみ superseded |

### 知識ランドスケープ（配置層, マイグレーション 065）
論文（document）を分野の地図のアンカーへ複数観点で配置する層。正本は
`docs/features/knowledge_landscape_design.md`（LS1〜LS10。**weight / confidence は DB のみで
教員にも数値を出さない**）。

| テーブル | 役割 |
|---|---|
| `landscape_placements`（065） | 配置。`perspective` 6語彙（subject/question/method/theory/observation/application）、`status`(inferred/confirmed/rejected/review_required/superseded)。`documents(id)` FK CASCADE。一意制約は `status <> 'superseded'` の部分インデックス。再解析は inferred のみ supersede（confirmed / rejected は AI が復活させられない） |

### カテゴリギャップ候補（マイグレーション 066）
「地図に置けなかった」を構造化信号として残し、反復した主題だけを教員レビュー候補に浮上させる層。
正本は `docs/features/category_gap_candidates_design.md`。**レビューキューは毎回読み時導出**で、
完了フラグ・掃除バッチを持たない。

| テーブル | 役割 |
|---|---|
| `landscape_gap_signals`（066） | 論文単位の gap 信号（`documents` FK CASCADE・LS3 と同型の supersede）。`layer`(region/concept)・`proposed_label`・`evidence_quote` |
| `atlas_gap_decisions`（066） | cluster 単位の教員判断のみ。`cluster_key UNIQUE` は**版非依存**（却下ゾンビ防止）。`status`(candidate/accepted/dismissed/merged)、`draft_node_id` / `applied_version` で採用と反映を分離 |

### 賭け金の台帳（SL層, マイグレーション 067）
D層の既存5テーブルの意味論を変えずに「何が崩れたら危ういか」を載せる層（新テーブルなし）。
正本は `docs/features/stakes_ledger_design.md`（SL1〜SL10）。

| 変更対象 | 内容 |
|---|---|
| `epistemic_ledger`（067, ALTER） | `falsification_conditions`（人間の記帳）/ `falsification_candidates`（LLM 候補）/ `falsification_analyzed_at`（worker の冪等マーカー） |
| `verification_proposals`（067, ALTER） | `course_id` / `reachability`（**人間専用語彙**。worker は書かない）/ `external_check`（昇格時必須・空は 422）/ `external_checked_by` |
| `counterfactual_sessions`（067, ALTER） | `toggled_observations`（観測を仮に倒す。伝播ロジック自体は非改変） |

### アカウントライフサイクル管理（マイグレーション 068・069）
**`users` 行を物理 DELETE しない**（削除 = status 遷移 + 匿名化墓標 + 明示 purge）。正本は
`docs/features/account_lifecycle_management_design.md`（AL1〜AL10）。

| テーブル / 変更 | 役割 |
|---|---|
| `users`（068, ALTER） | 状態列を追加: `status`(active/suspended/pending_deletion/deleted)・`status_changed_at`/`status_changed_by`/`status_reason`・`token_generation`（JWT `gen` クレームの照合先＝失効の実体）・`password_updated_at`・`last_login_at`・`last_seen_at`（5分スロットルの列更新のみ）・`purge_after` |
| `auth_events`（068） | 認証イベント台帳（**FK なし・append-only・削除 API なし**）。`event` 語彙の正本は `core/auth_events.py`。IP は X-Real-IP → XFF 末尾 |
| `llm_usage_events`（069, INDEX） | `(user_id, occurred_at)` の部分インデックス（U層のユーザー別集計軸。043 のテーブル定義は非編集） |

### URL指定による教材取得（マイグレーション 070）
SSRF ガードの正本は `backend/core/url_fetch.py`。正本設計書は `docs/features/url_material_upload_design.md`。

| テーブル | 役割 |
|---|---|
| `url_fetch_domains`（070） | 取得先ドメインの許可リスト。`domain` 主キー、`added_by` は FK なし（登録者が後に墓標化されうるため）。**migration でシードしない** — 初期状態は空＝機能無効で、管理者が削除した行が再起動で復活しない |

### 論文ディスカバリー層 / コーパス回遊層（マイグレーション 071・072・073）
分野購読で arXiv を検索し、教員が選んだ候補だけを既存の URL 取得経路へ流す層と、
育てたコーパスを学習者がコースの外から歩ける層。正本は
`docs/features/paper_discovery_design.md`（PD1〜PD8）/ `docs/features/corpus_roaming_design.md`（CR1〜CR10）。
**候補一覧のテーブルは持たない**（毎回 API から読み時導出）。

| テーブル / 変更 | 役割 |
|---|---|
| `paper_discovery_subscriptions`（071） | 分野（domain_key）単位1行の購読条件。`arxiv_categories TEXT[]` / `keyphrases(JSONB)`（要素に供給元 `source` と `enabled` を持ち、外した状態も保持）/ `followed_authors` / `last_checked_at`。教員の共同財で last-write-wins |
| `paper_discovery_dismissals`（071） | 見送り記録。`PRIMARY KEY(domain_key, arxiv_id)`。行削除せず `revoked` 遷移で復帰 |
| `documents.source_url`（071, ALTER） | URL 経由取り込みの出所。**「取り込み済み」判定の正本**（手動アップロード分は判定不能と正直に表示する） |
| `paper_discovery_ingest_items`（072） | バッチ取り込みキュー。`status`(queued/fetching/accepted/failed)・`requested_by` は FK なし。失敗は行を消さず `detail` に事実文を残す。行を作るのは教員の明示操作だけ（候補のスナップショットではない） |
| `paper_discovery_subscriptions.last_search_found_new`（073, ALTER） | コーパス回遊「地図の端 — 外の輪」の**集約1ビット**。教員の検索実行時のみ更新。DEFAULT なし = NULL は「まだ検索していない」。学習者起点で外部 API を呼ばないための材料（CR7） |

### 分野マップのベクトル係留（VA層, マイグレーション 074）
骨格ノードにプロトタイプベクトルを与え、配置プレフィルタ・別名レジストリ・着地予測を実現する層。
正本は `docs/features/atlas_vector_anchoring_design.md`（VA1〜VA9。**cosine 生値は表示しない**）。

| テーブル | 役割 |
|---|---|
| `atlas_anchor_embeddings`（074） | 骨格ノードのプロトタイプベクトル。`UNIQUE(domain_key, skeleton_version, node_id)`・`vector(3072)`・`node_kind`(region/concept)・FK / index なし（小規模表）。`source_hash` で不変ノードの再埋め込みをスキップ。(domain, version) 単位の全置換再構築が設計明示の例外 |
| `atlas_anchor_aliases`（074） | 教員確定の別名レジストリ（版非依存）。`UNIQUE(domain_key, node_id, normalized_alias)`・`status`(confirmed/dismissed)・`source`(gap_signal/manual)。削除 API なし |

### 分野マップの関係表示（RE追補, マイグレーション 076）
辺候補は読み時導出で、**保存するのは教員の判断だけ**。正本は
`docs/features/atlas_relation_edges_design.md`（RE1〜RE8）。

| テーブル | 役割 |
|---|---|
| `atlas_edge_decisions`（076） | 無向・版非依存の `edge_key UNIQUE`（`edge\|{domain}\|{min}\|{max}`）。`status`(candidate/accepted/dismissed)・見送りは理由必須・`edge_kind` は採用時に教員が選択・`applied_version` で採用と凍結反映を分離。遷移は `core/candidate_flow.py` 経由（本番初適用） |

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
| `044_object_group_permissions.sql` | アーキテクチャ整理 Tier 3-14 — `course_group_permissions`(010) + `document_group_permissions`(035) を `object_group_permissions` に統合。旧2テーブルはデータ移行後 DROP、ファイル自体はスタブ化 |
| `045_unified_notifications.sql` | アーキテクチャ整理 Tier 3-15 — `share_notifications`(037) を `user_notifications`(038) に統合（`source`/`release_id`/`acted_at` 列を追加）。旧テーブルはデータ移行後 DROP、`037_shared_versioning.sql` の該当セクションはスタブ化 |
| `046_atlas_report_incorporation.sql` | 分野の地図 — 修正報告の「採用」と「次版で反映済み」を分離（`atlas_correction_reports` に `incorporated_at`/`incorporated_by`/`incorporation_note` を追加） |
| `047_topic_lecture_audio.sql` | レクチャーの表示ソースと音声 — トピック教材ベースの音声キャッシュ新設（`topic_lecture_audio_cache`。キーは `(course_id, topic_id, slide_index, voice)`） |
| `048_element_identity_links.sql` | W層（要素検討ワークスペース）Phase W-β — 同一性リンク（`element_identity_links`。instance↔shared_part、`candidate/confirmed/rejected`） |
| `049_deliberation_sessions_annotations.sql` | W層（要素検討ワークスペース）Phase 2 — 対話的検討 + 候補注釈（`deliberation_sessions`, `element_annotations`） |
| `050_library_standardization_status.sql` | 分野別ナレッジライブラリ(L層) — 標準化判定 worker（Phase S）の確定先列を追加（`library_entries.standardization_status`） |
| `051_figure_inner_labels.sql` | 画像読み取りパイプライン — 図中ラベル抽出（`document_figures.inner_labels`、決定論的・非LLM） |
| `052_figure_presentation_modes.sql` | 図の提示モード分類・教員レビュー（#496） — `document_figures` に `suggested_mode`/`reviewed_mode` 等を追加 |
| `053_figure_reviewed_analysis.sql` | 教員レビュー済み図解析プロファイルの分離保存 — `document_figures` に `reviewed_analysis_mode`/`reviewed_analysis_profile` 等を追加（再解析時も教員レビュー済み内容を上書きしない） |
| `054_figure_iterative_analysis.sql` | 図解析の反証型反復パイプライン(L層 #499) — `document_figures.iterative_analysis JSONB`（AI提案層のみ・教員確定列なし） |
| `055_thesis_context_persistence.sql` | 二層説明 Phase 1 — thesis 構造メタの DB 永続化（`theory_components.thesis_context` / `theory_claims.thesis_refs`） |
| `056_element_explanations.sql` | 二層説明 Phase 2 — 全要素型ポリモーフィックな generic/contextual 説明台帳（`element_explanations`） |
| `057_atlas_domain_lifecycle.sql` | 分野の地図 — ドメインのライフサイクル（`atlas_domain_meta.lifecycle` active/retired、`retired_at/by/note`） |
| `058_manual_sections.sql` | help_kb Phase 3① — マニュアルのベクトル補助層（`manual_sections`。chunks 非汚染の専用テーブル） |
| `059_manual_kb_store.sql` | help_kb Phase 3② — DB draft/freeze ストア（`manual_kb_drafts` / `manual_kb_versions` / `manual_kb_state`） |
| `060_discuss_metric_events.sql` | discuss 観測基盤 — `discuss_metric_events`（本文非含有・append-only・FK なし） |
| `061_llm_model_policies.sql` | M層（場面別 LLM モデル選択） — `llm_model_policies`（scope=system\|user） |
| `062_discuss_opening_explanations.sql` | discuss 開幕素材オーサリング — `element_explanations` に element_type `'document'` と `role` 列を追加（056 台帳へ相乗り） |
| `063_teaching_figures.sql` | 教材図スタジオ — `course_teaching_figures` / `teaching_figure_suggestions`（SVG 正本 + ギャップ候補） |
| `064_deliberation_evidence_derivation.sql` | W層 Phase 5 — `deliberation_sessions` / `element_annotations` の element_type CHECK に `evidence` / `derivation` を追加 |
| `065_landscape_placements.sql` | 知識ランドスケープ（配置層） — `landscape_placements`（documents FK CASCADE・supersede 用部分 UNIQUE） |
| `066_category_gap_signals.sql` | カテゴリギャップ候補 — `landscape_gap_signals`（論文単位の信号）+ `atlas_gap_decisions`（cluster 単位の教員判断。`cluster_key` は版非依存） |
| `067_stakes_ledger.sql` | SL層（賭け金の台帳） — `epistemic_ledger.falsification_conditions/candidates/analyzed_at` / `verification_proposals.course_id・reachability・external_check*` / `counterfactual_sessions.toggled_observations` |
| `068_account_lifecycle.sql` | アカウントライフサイクル管理 — `users` に状態列9本（`status`/`status_changed_at/by`/`status_reason`/`token_generation`/`password_updated_at`/`last_login_at`/`last_seen_at`/`purge_after`）+ `auth_events`（FK なし・append-only の認証イベント台帳） |
| `069_llm_usage_user_index.sql` | U層拡張 — `llm_usage_events(user_id, occurred_at)` の部分インデックス（ユーザー別集計軸。043 は非編集） |
| `070_url_fetch_domains.sql` | URL指定による教材取得 — 取得先ドメインの許可リスト `url_fetch_domains`（`domain` 主キー・`added_by` は FK なし。**シード行を入れない** = 初期状態は空で機能無効） |
| `071_paper_discovery.sql` | 論文ディスカバリー層（arXiv 分野購読）— `paper_discovery_subscriptions`（分野単位1行の購読条件）+ `paper_discovery_dismissals`（見送りは `revoked` 遷移で保持）+ `documents.source_url`（取り込み済み判定の正本）。**シード行を入れない**・候補一覧のテーブルを持たない（読み時導出） |
| `072_paper_discovery_ingest_queue.sql` | 論文ディスカバリー層 Phase 2（バッチ取り込み）— `paper_discovery_ingest_items`（`status ∈ {queued, fetching, accepted, failed}`・`requested_by` は FK なし・失敗は行を消さず `detail` に事実文を残す）。**シード行を入れない**。行を作るのは教員の明示操作（`POST /ingest-batch`）だけで、候補のスナップショットではない |
| `073_corpus_roaming_search_state.sql` | コーパス回遊層 Phase C（地図の端 — 外の輪）— `paper_discovery_subscriptions.last_search_found_new BOOLEAN`（教員の最後の検索で `status='new'` の候補が1件以上あったかの**集約1ビット**）。候補のスナップショットを持たない（PD5 と両立）・**シード / 初期値を入れない**（NULL =「まだ検索していない」で、外の輪を行ごと出さない）。学習者起点で arXiv を呼ばないための材料（CR7） |
| `074_atlas_vector_anchoring.sql` | VA層（ベクトル係留）— `atlas_anchor_embeddings`（骨格ノードのプロトタイプベクトル。`UNIQUE(domain_key, skeleton_version, node_id)`・`vector(3072)`・FK なし・index なし（小規模表）。導出データで (domain, version) 単位の全置換再構築が設計明示の例外）+ `atlas_anchor_aliases`（教員確定の別名レジストリ。`status ∈ {confirmed, dismissed}` の状態遷移のみ・削除 API なし・版非依存）。**シード行を入れない** |
| `075_graph_dialogue_sessions.sql` | グラフ対話レビュー — `deliberation_sessions.element_type` CHECK に `'document_graph'`（グラフ全体対話の疑似要素型。element_id = document UUID）を追加。**`element_annotations` の CHECK は変更しない**（グラフ全体対話は候補注釈を生成しない）。新テーブル・シードなし |
| `076_atlas_edge_decisions.sql` | 分野マップの関係表示（辺候補レビュー）— `atlas_edge_decisions`（無向・版非依存の `edge_key` UNIQUE・status ∈ {candidate, accepted, dismissed}・見送りは理由必須・`edge_kind` は採用時に教員が選択・`applied_version` で採用と凍結反映を分離）。候補スナップショットは持たない（読み時導出）・**シード行を入れない** |

> 注（2026-07 アーキテクチャ整理 Tier 3-13 で更新）: マイグレーションの実行方式を一本化した。
> かつては `backend/db/*.sql` を正本リファレンスとしつつ、実際の適用は `backend/api/main.py` の
> `_run_migrations()` に直書きした約1,600行のインライン DDL が別途担う「正本が2つ」の状態
> だったが、この二重管理を撤去した。現在は `backend/db/*.sql`（init.sql + 番号順ファイル群）が
> **唯一の正本**であり、`backend/core/migrations.py` の薄いランナーが毎起動・番号順に全ファイルを
> 冪等再実行する（pg_advisory_lock で多重起動排他・ファイル単位トランザクション）。新しい
> スキーマ変更は新番号のファイルを追加すること。詳細は CLAUDE.md
> 「マイグレーションの正本一本化」および `docs/architecture/consolidation_survey_2026-07.md`
> 第4部（Tier 3-13）を参照。

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
