---
audience: system_admin
---

# システム管理者編（技術者向け）

[← マニュアル索引](../README.md)

本ドキュメントは、episteme-graph をホストし運用する**システム管理者（SYSTEM_ADMIN）**向けの
技術者向けマニュアルです。読む前に、まず全ロール共通の
[仕様編](../student/01-specification.md) に目を通し、システム全体像・ロールと権限・用語を
把握しておくことを推奨します。アーキテクチャとロールの運用上の詳細は
[仕様編（運用・アーキテクチャ）](01-operations-spec.md) にまとめています。

システム管理者は API 上、教員（TEACHER）ができることをすべて行えるため、
[教員編](../teacher/03-teacher.md) もあわせて読むことをおすすめします。ただし**管理UIの
タブ構成はロールで出し分けられており、システム管理者では教材管理・コースビルダー・
コース管理・原稿スタジオのタブが表示されません**（教材の投入やコース制作は教員アカウントで
行ってください）。詳細は
[管理UIのタブはロールで出し分けられます](01-operations-spec.md#admin-tab-visibility) を
参照してください。

管理者専用タブの画面単位のリファレンスは、以下にあります。

| タブ | ページ |
|---|---|
| 教員管理 | [10-admin-teachers.md](10-admin-teachers.md) |
| システム統計 | [11-admin-system-stats.md](11-admin-system-stats.md) |
| エラー解析 | [12-admin-error-analysis.md](12-admin-error-analysis.md) |
| LLM使用量 | [13-admin-llm-usage.md](13-admin-llm-usage.md) |
| マニュアル編集 | [14-admin-manual-editor.md](14-admin-manual-editor.md) |
| discuss 観測 | [15-admin-discuss-observation.md](15-admin-discuss-observation.md) |
| AIモデル | [16-admin-llm-models.md](16-admin-llm-models.md) |
| AIモデル（末尾の区画） | [17-admin-url-fetch-domains.md](17-admin-url-fetch-domains.md) |

---

## 1. 初期構築 {#initial-setup}

### 1.1 `.env` の設定 {#env-setup}

リポジトリ直下の `.env.example` を `.env` にコピーし、値を設定します。

```bash
cp .env.example .env
```

以下の3つは**必ず**既定値から変更してください（詳細は [9. セキュリティ要点](#security-notes)）。

| 変数 | 用途 |
|---|---|
| `LLM_API_KEY` | LLM プロバイダの API キー（OpenAI / Gemini。後方互換で `OPENAI_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_API_KEY` も使用可）。`LLM_PROVIDER=google`（Vertex AI + ADC）の場合は不要 |
| `JWT_SECRET` | JWT 署名鍵 |
| `ADMIN_PASSWORD` | 初期システム管理者アカウントのパスワード |

### 1.2 起動 {#startup}

```bash
docker compose up -d
```

Compose ファイルの使い分け（本番用の `docker-compose.yml` 単体では `postgres` が含まれない点に注意）は
[2. Compose ファイルの使い分け](#compose-files) を参照してください。

### 1.3 初期管理者アカウント {#initial-admin-account}

初期アカウントは `.env` の `ADMIN_PASSWORD` で作成される**システム管理者アカウントのみ**です。
教員・学生アカウントはこの初期管理者が管理 UI から作成します（[6. 教員アカウントの作成](#create-teacher-account) 参照）。

### 1.4 アクセス先 {#access-urls}

| サービス | URL | 備考 |
|---|---|---|
| 学習UI | http://localhost:3000 | 受講者・教員・システム管理者共通の入口 |
| 管理UI | http://localhost:3000/admin.html | 教員・システム管理者向け |
| Swagger UI | http://localhost:8001/docs | ローカル開発で直接公開する設定の場合のみ |
| MinIO コンソール | http://localhost:9001 | ローカル開発（`docker-compose.local.yml` 併用時） |
| GROBID | http://localhost:8070 | ローカル開発（`docker-compose.local.yml` 併用時） |
| ngrok Web UI | http://localhost:4040 | `docker-compose.prod.yml` で ngrok トンネルを併用する場合 |

本番・共通構成で外部に公開されるのは **学習UI（frontend, 3000番）のみ**です。詳細は
[3. サービス構成](#service-architecture) を参照してください。

---

## 2. Compose ファイルの使い分け {#compose-files}

| ファイル | 用途 | 追加するもの |
|---|---|---|
| `docker-compose.yml` | 本番 / CI 共通のベース | grobid, minio, api-server, frontend |
| `docker-compose.local.yml` | ローカル開発 | `postgres`（`pgvector/pgvector:pg16`）、DB クライアント向けポート公開など |
| `docker-compose.prod.yml` | 本番補助 | `ngrok` トンネル |

```bash
# 本番 / CI
docker compose up -d

# ローカル開発（postgres コンテナ + ngrok などを併用）
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d

# API のみ再ビルド（コード変更後）
docker compose up -d --build api-server

# ログ確認
docker compose logs -f api-server
```

**重要:** ベースの `docker-compose.yml` には `postgres` サービスが定義されていません。
`api-server` は `DATABASE_URL=postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}`
を参照するため、本番運用ではマネージド PostgreSQL（例: Cloud SQL）を `DB_HOST` 等で指定する
ことが前提になっています。ローカル開発で自前の PostgreSQL コンテナを使う場合のみ、
`docker-compose.local.yml` を重ねて `postgres` サービス（`pgvector/pgvector:pg16` イメージ）を
追加してください。

`docker-compose.prod.yml` は `ngrok` による固定ドメインの外部公開トンネルを追加する補助ファイルです。
使う場合は事前に ngrok アカウントで固定ドメインを取得し、`.env` に `NGROK_AUTHTOKEN` /
`NGROK_DOMAIN` を設定してください。

---

## 3. サービス構成 {#service-architecture}

| サービス | イメージ / ビルド | 役割 |
|---|---|---|
| `grobid` | `lfoppiano/grobid:0.8.1` | PDF → TEI-XML 解析（8070番） |
| `minio` | `minio/minio:latest` | S3 互換オブジェクトストレージ（コンソール 9001番）。PDF原本・図画像を保存 |
| `api-server` | `backend/Dockerfile` ビルド | FastAPI 本体（`backend/api` + `backend/core` + `src/episteme_graph/agents`）。`.gcp` を `/app/.gcp:ro` でマウント |
| `frontend` | `frontend/Dockerfile` ビルド | nginx。静的 SPA 配信 + `/api/*` を api-server へリバースプロキシ（3000番公開） |

- 全サービスは Docker 内部ネットワーク `episteme`（bridge）で相互接続します。
- ボリューム: `postgres_data`, `minio_data`。
- `api-server` は `postgres`（healthy）/ `minio` / `grobid` の起動を待って起動します（`depends_on`）。

### ネットワーク設計（セキュリティ） {#network-design}

- **外部に公開されるポートは frontend の 3000番のみ**です。
- `api-server`（8001番）は本番・ローカルとも直接公開されず、必ず nginx（3000番）経由でアクセスします。
- API リバースプロキシの定義は `frontend/nginx.conf` にあり、`/api/learning`, `/api/auth`,
  `/api/admin`, `/api/groups`, `/api/me`, `/api/atlas`, `/api/courses`, `/api/documents` を
  api-server にプロキシします。
- **API のパスを増やしたときは、この `nginx.conf` にも location を足してください。** 定義が
  無いパスは SPA フォールバックに吸われて `index.html` が 200 で返るため、画面側では
  「JSON として読めない」という分かりにくい失敗になります。

---

## 4. 主要環境変数 {#env-vars}

`api-server` の environment は `docker-compose.yml` で `.env` から注入されます。実在する変数のみ
記載しています。

### 4.1 LLM プロバイダ {#env-llm-provider}

| 変数 | 説明 |
|---|---|
| `LLM_PROVIDER` | `openai`（既定） / `gemini`（Google AI Studio） / `google`（Vertex AI + ADC）。`gemini-vertex` は廃止予定 |
| `LLM_API_KEY` | 共通 API キー。後方互換で `OPENAI_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_API_KEY` も使用可。`LLM_PROVIDER=google` では不要（ADC を使用） |
| `LLM_FAST_MODEL` / `LLM_FAST_EFFORT` | 軽い判断用のモデル・推論努力度 |
| `LLM_STANDARD_MODEL` / `LLM_STANDARD_EFFORT` | 標準分析用 |
| `LLM_DEEP_MODEL` / `LLM_DEEP_EFFORT` | 複雑推論用 |
| `LLM_ANALYSIS_MODEL` | 非 OpenAI プロバイダのフォールバック等に使用 |
| `LLM_FAST_MODEL_MAX_TOKENS` / `LLM_STANDARD_MODEL_MAX_TOKENS` / `LLM_ANALYSIS_MODEL_MAX_TOKENS` / `LLM_DEEP_MODEL_MAX_TOKENS` | ティアごとの最大出力トークン数 |
| `LLM_EMBEDDING_MODEL` | 埋め込みモデル（既定 `text-embedding-3-large`） |
| `LLM_EMBEDDING_DIM` | pgvector の次元数（既定 3072。Gemini 系では 768 など） |

### 4.2 Google Cloud（Vertex AI / ADC） {#env-gcp}

| 変数 | 説明 |
|---|---|
| `GCP_PROJECT_ID`（または `GOOGLE_CLOUD_PROJECT`） | GCP プロジェクト ID |
| `GCP_LOCATION` | Vertex AI のリージョン（既定 `us-central1`） |
| `GCP_USE_VERTEX_AI` | Vertex AI 経由利用フラグ |
| `GOOGLE_APPLICATION_CREDENTIALS` | ADC 認証情報ファイルパス（既定 `/app/.gcp/application_default_credentials.json`） |

ホストの `./.gcp` ディレクトリがコンテナの `/app/.gcp:ro` に読み取り専用でマウントされます。

### 4.3 データストア {#env-datastore}

| 変数 | 説明 |
|---|---|
| `DATABASE_URL` | `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` / `DB_NAME` から組み立てられる PostgreSQL 接続文字列 |
| `MINIO_ENDPOINT` | MinIO エンドポイント |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | MinIO 認証情報（`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` にフォールバック） |
| `MINIO_PUBLIC_ENDPOINT` | クライアント側から見える MinIO エンドポイント |
| `GROBID_URL` | GROBID サービスの URL |

### 4.4 認証・CORS・カートリッジ {#env-auth-cors}

| 変数 | 説明 |
|---|---|
| `JWT_SECRET` | JWT 署名鍵。**必ず既定値から変更すること** |
| `ADMIN_PASSWORD` | 初期システム管理者パスワード |
| `CORS_ORIGINS` | 許可オリジン（カンマ区切り）。既定 `*`（全許可・開発用）。本番は明示リスト推奨 |
| `ADMIN_ERROR_LOG_MAX_ITEMS` | Admin エラー解析画面で保持・返却するログ最大件数（既定 1000） |
| `EPISTEME_DEFAULT_CARTRIDGE_ID` | 既定のドメインカートリッジ（既定 `particle_physics`） |
| `EPISTEME_CARTRIDGES_DIR` | カートリッジ定義ディレクトリの上書き（未指定時は `backend/cartridges`） |
| `ATLAS_DATA_SOURCE` | 分野の地図のデータソース。`api`（既定・本番推奨）/ `fixture`（ローカル確認用のモック地図。本番で使うと全ユーザーにモック地図が表示されるため注意） |

### 4.5 音声・文字起こし {#env-voice}

| 変数 | 説明 |
|---|---|
| `LLM_TRANSCRIBE_MODEL` | 音声文字起こしモデル（既定 `whisper-1`。openai プロバイダのみ対応） |

### 4.6 機能別 LLM コール数上限（コスト制御） {#env-call-limits}

各機能は互いに独立したカウンタでレート制限されます。

| 変数 | 既定値 | 対象機能 |
|---|---|---|
| `TENSION_MAX_CALLS_PER_SESSION` | 3 | TensionMiningAgent（B層）セッションあたり |
| `TENSION_MAX_CALLS_PER_DAY` | 10 | TensionMiningAgent 1ユーザー1日あたり |
| `ANCHOR_MAX_CALLS_PER_SESSION` | 3 | 構造帰属型の問い記録（B層）セッションあたり |
| `ANCHOR_MAX_CALLS_PER_DAY` | 10 | 構造帰属型の問い記録 1ユーザー1日あたり |
| `DOUBT_SCOPE_MAX_CALLS_PER_DAY` | 10 | D層 検証スコープ候補抽出 |
| `DOUBT_ASSUMPTION_MAX_CALLS_PER_DAY` | 10 | D層 暗黙前提マイニングの LLM 正規化 |
| `DOUBT_FALSIFICATION_MAX_CALLS_PER_DAY` | 10 | 賭け金の台帳（SL層）反証条件の候補抽出 |
| `ATLAS_ASSIST_MAX_CALLS_PER_DAY` | 60 | 分野の地図 骨格エディタ AI アシスト編集（1教員あたり） |
| `ATLAS_VECTOR_MAX_CALLS_PER_DAY` | 50 | 分野マップのアンカー埋め込み構築（VA層） |
| `ASSISTANT_MAX_CALLS_PER_DAY` | 20 | Admin Copilot（横断ユーティリティ層） |
| `RECON_MAX_ITEMS_PER_DOCUMENT` | 30 | 再構成ループ（R層）自動生成 item 上限 |
| `RECON_MAX_CALLS_PER_DAY` | 10 | 再構成ループ item オーサリング worker |
| `APPARATUS_MAX_IMAGES_PER_DOCUMENT` | 20 | 画像読み取りパイプライン（L層）1文書あたりの vision 対象図数上限 |
| `APPARATUS_MAX_CALLS_PER_DAY` | 30 | 画像読み取りパイプライン vision 呼び出し日次上限 |
| `DELIBERATION_MAX_CALLS_PER_SESSION` | 8 | 要素検討ワークスペース（W層）1セッションあたり |
| `DELIBERATION_MAX_CALLS_PER_DAY` | 40 | 要素検討ワークスペース + グラフ対話レビューの対話（1ユーザー1日） |
| `DELIBERATION_VOICE_MAX_CALLS_PER_DAY` | 200 | グラフ対話レビューの音声入出力（文字起こし・読み上げ共通。対話の上限とは別カウンタ） |
| `STDPART_MAX_CALLS_PER_DAY` | 10 | 共通部品の標準化判定（W層 Phase S） |
| `FIGURE_STUDIO_MAX_CALLS_PER_DAY` | 60 | 教材図スタジオの対話生成 |
| `FIGURE_SUGGEST_MAX_CALLS_PER_DAY` | 20 | 教材図スタジオの図の提案（ギャップ検出） |
| `CTXEXPL_MAX_CALLS_PER_DAY` | 20 | 二層説明のうち文脈依存説明の生成ステージ |
| `DISCUSS_OPENING_MAX_ITEMS_PER_DOCUMENT` | 4 | discuss 開幕素材（議論のきっかけ）の1論文あたり生成上限 |
| `DISCUSS_OPENING_MAX_CALLS_PER_DAY` | 20 | discuss 開幕素材の生成ステージ |
| `LANDSCAPE_MAX_CALLS_PER_DAY` | 20 | 知識ランドスケープ（論文の配置）の生成・再提案 |
| `LANDSCAPE_MAX_PLACEMENTS_PER_DOCUMENT` | 8 | 1論文あたりの配置候補の上限 |
| `DISCOVERY_RANKING_MAX_CALLS_PER_DAY` | 100 | 論文ディスカバリー・レーダーの関連度ランキング（埋め込み） |
| `DISCOVERY_COMPARE_MAX_CALLS_PER_DAY` | 20 | 論文レーダーの比較分析（1教員1日） |
| `LEARNING_CHAT_MAX_CALLS_PER_DAY` | 300 | 学習チャット本体（1ユーザー1日。discuss モード・理解サイクルの AI 補助も同じカウンタ） |
| `COURSE_BUILDER_MAX_CALLS_PER_DAY` | 100 | コースビルダーチャット（1ユーザー1日） |
| `LECTURE_REWRITE_MAX_CALLS_PER_DAY` | 100 | 原稿スタジオ rewrite（1ユーザー1日） |

上限に達したときの挙動は機能ごとに異なり、「その回だけ実行しない（記録は残す）」か
「事実文とともに断る」のいずれかです。**利用者に残り回数などの数値は表示しません。**

モデル指定用の `*_LLM_MODEL` 変数（例: `TENSION_LLM_MODEL` / `DOUBT_SCOPE_LLM_MODEL` /
`ATLAS_ASSIST_LLM_MODEL` / `ASSISTANT_LLM_MODEL` / `RECON_LLM_MODEL` /
`LEARNING_CHAT_LLM_MODEL` / `COURSE_BUILDER_LLM_MODEL` など）は空欄にすると fast tier または
analysis tier のモデルに自動的に委譲されます（各変数のコメントを `.env.example` で確認してください）。
これらの env より優先される「場面ごとのモデル指定」は管理UIの「AIモデル」タブから設定できます
（[AIモデル（管理画面）](16-admin-llm-models.md)）。

### 4.7 その他の画像パイプライン設定 {#env-image-pipeline}

| 変数 | 説明 |
|---|---|
| `APPARATUS_LLM_MODEL` | vision 同定モデル（既定 `gpt-4o`。v1 は OpenAI 経路のみ） |
| `APPARATUS_FEWSHOT_IMAGES` | 含有承認済み例示画像の few-shot 添付（既定 `false`） |
| `APPARATUS_RETRIEVAL_TOP_K` | ライブラリ凍結版 retrieval の候補数（既定 5） |
| `APPARATUS_ANALYSIS_MODE` | 図解析の方式。`iterative`（既定。文脈仮説 → 独立観察 → 照合 → 再スキャン）/ `one_shot`（旧方式） |
| `APPARATUS_VERIFY_MAX_ITERATIONS` | バッチ解析での再スキャン最大試行回数（既定 3） |
| `APPARATUS_REANALYZE_MAX_ITERATIONS` | 教員指示付き再解析（同期）での最大試行回数（既定 1） |

### 4.8 LLM トークン使用量推計（U層） {#env-llm-usage}

| 変数 | 説明 |
|---|---|
| `LLM_USAGE_TRACKING_ENABLED` | 記録の有効/無効（既定 `true`。`false` でテスト・ローカル用に no-op 化） |
| `LLM_USAGE_BUFFER_MAX` | in-memory バッファ上限（既定 1000。超過分は破棄され `dropped_events` に計上） |
| `LLM_USAGE_FLUSH_INTERVAL_SECONDS` | バックグラウンド flusher の周期（既定 10秒） |
| `LLM_USAGE_FLUSH_BATCH` | この件数到達で flusher を即時起動（既定 100） |
| `LLM_PRICE_TABLE_PATH` | モデル単価表 JSON のパス。空なら概算費用は常に `null`（価格をハードコードしない方針） |

### 4.9 ローカル開発 / ngrok {#env-local-ngrok}

`docker-compose.local.yml` を使ったローカル開発時のみ必要です。

| 変数 | 説明 |
|---|---|
| `NGROK_AUTHTOKEN` | ngrok 認証トークン |
| `NGROK_DOMAIN` | ngrok 固定ドメイン |

### 4.10 モデル選択・論文の探索 {#env-model-discovery}

| 変数 | 説明 |
|---|---|
| `LLM_MODEL_CATALOG_PATH` | 「AIモデル」タブで選べるモデルのカタログ JSON のパス。**未設定なら同梱カタログ**（`backend/config/llm_models.json`）を使います。カタログを明示的に無効化したい場合のみ、存在しないパスを指定します |
| `DISCOVERY_CITATION_SOURCE_ENABLED` | 引用グラフによる候補の追加供給源のオプトイン（既定 `false`。有効にすると外部 API を追加で参照します） |

---

## 5. 起動時の自動処理 {#startup-automation}

`api-server` 起動時、`backend/api/main.py` の lifespan で以下が実行されます。

1. **DB マイグレーションの適用** — `backend/db/` の SQL ファイル（`init.sql` + 番号順ファイル群）が
   正本です。`backend/core/migrations.py` の薄いランナーが**毎起動、番号順に全ファイルを冪等に
   再実行**します（`pg_advisory_lock` による多重起動排他つき）。すべてのファイルは冪等に書かれて
   いる前提のため、既存データの列追加・次元変更なども安全に繰り返し適用されます。
2. **ビルトインスキーマの seed** — `schema_registry.seed_builtin_schema()` が `OntologyType` /
   `CorePredicate` のビルトイン語彙を DB に投入します（`is_builtin=true`）。
3. **システム管理者アカウントの初期化** — `.env` の `ADMIN_PASSWORD` で初期システム管理者
   アカウント（表示名 `Administrator`）を作成します。既に存在する場合は何もしません。
4. **同梱データの冪等な取込** — カートリッジ同梱の分野の地図（骨格）の凍結版、ナレッジ
   ライブラリ、場面別モデル指定の env → DB シードを、既存の DB 行を上書きしない形で
   取り込みます。いずれも失敗しても起動は止まりません。
5. **利用者マニュアル（ヘルプKB）の検証** — `docs/manual/` と管理画面・学習画面の
   UI アンカー表の整合を検証し、違反はログに警告として出します（起動は止めません）。
   併せて配信スナップショットのハッシュを記録し、変化したときだけ監査記録を残します。

### 常駐する定期処理 {#background-workers}

起動後、次のバックグラウンド処理が動きます。いずれも起動に失敗しても API 本体は動きます。

| 処理 | 役割 | 制御する環境変数 |
|---|---|---|
| 削除猶予スイーパ | 共有物・アカウントの削除予約が猶予期間を過ぎたものを実際に削除します | `VERSION_SWEEPER_ENABLED`（既定 有効）/ `VERSION_SWEEP_INTERVAL_SECONDS`（既定 3600 秒） |
| 論文取り込みキュー worker | 教員がキューに積んだ論文だけを順に取得・解析します（自分で論文を探すことはしません） | `PAPER_DISCOVERY_WORKER_ENABLED`（既定 有効）/ `PAPER_DISCOVERY_WORKER_INTERVAL_SECONDS`（既定 30 秒） |
| 状態遷移 watcher | 教材・コースの状態変化を検知して通知インボックスへ配信します | `STATUS_WATCH_ENABLED`（既定 有効）/ `STATUS_WATCH_INTERVAL`（既定 60 秒） |
| ヘルプKB のベクトル同期 | マニュアルの節を補助検索用に埋め込み直します | `HELP_KB_VECTOR_ENABLED`（既定 有効） |

---

## 6. 教員アカウントの作成 {#create-teacher-account}

**必要ロール:** システム管理者（SYSTEM_ADMIN）のみ

教員アカウントの作成は、システム管理者だけが実行できる操作です。教員（TEACHER）権限では
実行できません。**アカウント作成は取り消せません。** 送信すると確認ダイアログを挟まず
即座に作成されるため、入力内容をよく確認してから送信してください。

具体的な手順は [教員アカウント作成](../../admin_operations/teachers.md#create-teacher) を参照してください
（対象タブ: 教員管理）。

なお、学生アカウントの作成は教員以上のロールで実行可能です
（[学生アカウントを作成する](../../admin_operations/students.md#create-student) 参照）。

### アカウントのライフサイクル運用 {#account-lifecycle}

作成したアカウントは、教員管理タブ（学生については学生管理タブ）の一覧から運用します。
アカウントの行そのものは消さず、状態の遷移として扱います。

| 操作 | 実行できるロール | 効果 |
|---|---|---|
| 停止 / 再開 | 学生への操作は教員以上、教員への操作はシステム管理者のみ | 効果は**ログインの拒否だけ**です。所有権・共有設定・受講者の学習状態は変わりません |
| パスワード再設定 | システム管理者のみ（対象が学生でも教員でも） | 発行済みのログイン状態がすべて無効になります |
| 利用状況の照会 | システム管理者のみ | ログインの記録と AI 利用実績を表示します。**学習の評価には使いません** |
| 削除予約 / 取消 | システム管理者のみ | 停止中のアカウントにだけ予約できます（既定の猶予は 14 日）。猶予後にスイーパが削除しますが、教材・コース・グループを所有したままなら削除は中止され、事実が通知されます |
| 所有物の移管 | システム管理者のみ | 教材・コース・グループの所有者を、別の有効な教員・管理者へ引き継ぎます |

自分自身のアカウントと、初期作成の `Administrator` は、停止・削除できません（誰もログイン
できなくなる事故を防ぐためです）。手順とエラー時の対処は
[教員管理（管理画面）](10-admin-teachers.md) を参照してください。

---

## 7. 監視 {#monitoring}

### 7.1 システム統計 {#system-stats}

**対象タブ:** システム統計（system-stats） / **必要ロール:** システム管理者（SYSTEM_ADMIN）のみ / **取り消し可否:** 該当なし（読み取り専用・確認ダイアログなし）

教材数・処理状況などのシステム統計を確認できます。詳細は
[システム統計を見る](../../admin_operations/system.md#stats) を参照してください。

### 7.2 エラーログ {#error-logs}

**対象タブ:** エラー解析（error-analysis） / **必要ロール:** システム管理者（SYSTEM_ADMIN）のみ / **取り消し可否:** 該当なし（読み取り専用・確認ダイアログなし）

システムのエラーログを確認できます。保持・返却されるログの最大件数は環境変数
`ADMIN_ERROR_LOG_MAX_ITEMS`（既定 1000）で制御されます。詳細は
[エラーログを見る](../../admin_operations/system.md#error-logs) を参照してください。

### 7.3 LLM 使用量 {#llm-usage-monitoring}

**対象タブ:** LLM使用量（llm-usage） / **必要ロール:** システム管理者（SYSTEM_ADMIN）のみ / **取り消し可否:** 該当なし（読み取り専用・確認ダイアログなし）

システム全体の LLM トークン消費量を確認できます。

- **実測（reported）と推計（estimated_tokenizer / estimated_heuristic）は分離して集計表示**
  され、混ぜた単一数値は表示されません。
- バッファ溢れ（`dropped_events`）がある場合はそのまま表示されます（隠しません）。
- 概算費用は `LLM_PRICE_TABLE_PATH` が設定されている場合のみ表示されます。設定が無い場合は
  費用は `null` のまま表示されません（金額はハードコードしません）。

- 「ユーザー別」の集計軸を選ぶと、誰がどれだけ使ったかの個票を確認できます。この個票を
  見られるのはシステム管理者だけで、教員・学生本人には表示されません。

詳細は [LLM使用量メトリクスを確認する](../../admin_operations/llm_usage.md#view-metrics) と
[LLM使用量（管理画面）](13-admin-llm-usage.md) を参照してください。
教材ごとの解析コスト事前見積り（レンジのみ・金額なし）は**教材管理タブ**から確認できますが、
このタブはシステム管理者のロールでは表示されないため、確認には教員アカウントを使います。

### 7.4 discuss（論文と話す）の観測 {#discuss-observation-monitoring}

**対象タブ:** discuss 観測（discuss-observation） / **必要ロール:** システム管理者（SYSTEM_ADMIN）のみ

「論文と話す」モードの利用状況（仮名化済みの集計。学習者の発話本文は含みません）を確認し、
分析用のダンプを取得できます。詳細は
[discuss 観測（管理画面）](15-admin-discuss-observation.md) を参照してください。

### 7.5 利用者マニュアル（ヘルプKB）の配信 {#manual-editor-ops}

**対象タブ:** マニュアル編集（manual-editor） / **必要ロール:** システム管理者（SYSTEM_ADMIN）のみ

AI ヘルプ応答が参照する利用者マニュアルは、既定ではリポジトリ同梱のファイルを配信します。
運用中に修正が必要になった場合は、このタブで下書きを編集し、検証を通過したものを凍結版として
配信できます。詳細は [マニュアル編集（管理画面）](14-admin-manual-editor.md) を参照してください。

---

## 8. スキーマ進化の運用 {#schema-evolution-ops}

システムは固定の `OntologyType` / `CorePredicate` に加えて、運用中に得た学生の質問から
グラフ DSL の語彙を動的に成長させる仕組み（スキーマ進化）を持っています。全体ワークフロー
（未回答クエリの蓄積 → AI によるスキーマ提案生成 → Shadow Testing による検証 → 教員の承認/棄却
→ 再抽出ジョブ）の詳細は [動的スキーマ進化](../../pipeline/schema-evolution.md) を参照してください。

「スキーマ提案」タブでの承認操作には2つの選択肢があります。

- **システム全体に適用** — **全教材の再抽出ジョブ**を開始します。処理対象がシステム全体の
  教材に及ぶため**コストが大きく、取り消せません**。実行前に確認ダイアログが表示されます。
- **カナリアリリース** — 選択した特定コースのみに適用し、影響を限定して試すことができます。
  全体適用前の検証手段として活用してください。

具体的な操作手順は [スキーマ提案を確認・承認する](../../admin_operations/schema_proposals.md#review)
を参照してください（対象タブ: スキーマ提案 / 必要ロール: 教員以上）。

---

## 9. セキュリティ要点 {#security-notes}

- **`JWT_SECRET` と `ADMIN_PASSWORD` は既定値から必ず変更してください。** 既定値のまま本番運用
  すると、認証トークンの偽造や初期管理者アカウントへの不正ログインを許すことになります。
- **`CORS_ORIGINS` は本番では明示リストに限定してください。** 既定の `*`（全オリジン許可）は
  開発用です。例: `CORS_ORIGINS=https://your-domain.example.com`。
- **`api-server`（8001番）を直接外部公開しないでください。** 必ず nginx（3000番）経由でアクセス
  する構成を維持してください。
- **機微データを URL パラメータに載せないでください。** クエリ文字列はログやブラウザ履歴に残る
  ため、個人情報・認証情報等を含めないよう API 設計・運用の両面で注意してください。
- **k-匿名化された学習データを成績評価に使わないでください。** TensionMiningAgent（B層）・
  D層の素朴な問いの計器化・分野の地図の踏破状況など、学習者の関心・つまずき・違和感に関する
  集計は、本人への学習支援を目的とした k-匿名化集計（k=3、n<3 のセルは非表示）であり、
  個々の学習者の評価・成績判定に転用しないことが設計上の不変条項です。

---

[← マニュアル索引](../README.md)
