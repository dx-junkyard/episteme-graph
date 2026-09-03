# デプロイ構成

[← ドキュメント目次](../README.md) ｜ [← アーキテクチャ概要](overview.md)

Docker Compose によるサービス構成、ネットワーク設計、環境変数をまとめます。

---

## 1. Compose ファイルの使い分け

| ファイル | 用途 | 追加するもの |
|---|---|---|
| `docker-compose.yml` | 本番 / CI 共通のベース | grobid, minio, api-server, frontend |
| `docker-compose.local.yml` | ローカル開発 | `postgres`（`pgvector/pgvector:pg16`）、DB クライアント、デバッグポート公開など |
| `docker-compose.prod.yml` | 本番補助 | `ngrok` トンネルなど |

```bash
# 本番 / CI
docker compose up -d

# ローカル開発（postgres コンテナ + ngrok などを併用）
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d

# API のみ再ビルド（コード変更後）
docker compose up -d --build api-server

# ログ
docker compose logs -f api-server
```

> **重要:** ベースの `docker-compose.yml` には `postgres` サービスが定義されていません。
> `api-server` は `DATABASE_URL=postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}` を参照し、
> 本番ではマネージド PostgreSQL（例: Cloud SQL）を、ローカルでは `docker-compose.local.yml` の `postgres` コンテナを使います。

---

## 2. サービス一覧（docker-compose.yml）

`docker-compose.yml` の `services:` ブロックの内容（行番号は変動するため示さない）。

| サービス | イメージ / ビルド | 役割 |
|---|---|---|
| `grobid` | `lfoppiano/grobid:0.8.1` | PDF → TEI-XML 解析（8070） |
| `minio` | `minio/minio:latest` | S3 互換ストレージ（コンソール 9001） |
| `api-server` | `backend/Dockerfile` ビルド | FastAPI 本体。`.gcp` を `/app/.gcp:ro` でマウント |
| `frontend` | `frontend/Dockerfile` ビルド | nginx（3000 公開） |

- ネットワーク: `episteme`（bridge）。全サービスがこのネットワークで相互接続。
- ボリューム: `postgres_data`, `minio_data`。
- `api-server` は `postgres`(healthy) / `minio` / `grobid` の起動を待って起動（`depends_on`）。

> 旧 `neo4j` サービスは書き込み経路がなく実質未使用だったため 2026-07 に撤去済み。

### ネットワーク設計（セキュリティ）
- 外部公開ポートは **frontend:3000 のみ**。
- `api-server`（8001）は本番・ローカルとも直接公開されず、必ず nginx（3000）経由でアクセスする。
- API リバースプロキシの定義は `frontend/nginx.conf`。プロキシ対象は `/api/learning/`, `/api/auth/`, `/api/admin/`, `/api/groups/`（+ 末尾スラッシュなしの `= /api/groups`）, `/api/me/`, `/api/atlas/`（+ 末尾スラッシュなしの `= /api/atlas`）, `/api/courses/`, `/api/documents/` の10 location（`location /` の静的配信を除く）。詳細は [フロントエンド構成](../frontend/overview.md)。
- **`/api/atlas` は必須項目**（末尾スラッシュ有無の2 location とも）。CLAUDE.md が明記するとおり、この location が欠落すると SPA フォールバック（`location /` の `try_files ... /index.html`）が index.html を **200 で返し**、フロントの JSON パースが失敗して分野の地図が事故る。

---

## 3. 主要な環境変数（.env.example）

> **変数名と既定値の正本はコード側**（`backend/core/config.py` の `Settings` — `AliasChoices` で
> env 名を宣言 — と、各モジュールの `os.getenv`）。`.env.example` はサンプルであって全変数を
> 網羅していません（2026-09-03 時点で `.env.example` は 62 行、`Settings` の env 別名は 86 件）。
> 以下は実コードで確認した主要な抜粋です。

`api-server` の environment は `docker-compose.yml` の `api-server.environment:` で `.env` から
注入されます。

> **重要（2026-09-03 時点の実測）:** compose には `env_file:` の指定がなく、`backend/Dockerfile`
> も `.env` をイメージに COPY しません（`Settings` の `env_file=".env"` はコンテナ内で対象
> ファイルを見つけられない）。したがって **Docker 実行時に効くのは
> `docker-compose.yml` の `environment:` に列挙された変数だけ**です。
> 2026-09-03 時点で列挙されているのは `JWT_SECRET` / `LLM_*` / `GCP_*` / `GOOGLE_*` /
> `DATABASE_URL` / `MINIO_*` / `ADMIN_PASSWORD` / `CORS_ORIGINS` / `EPISTEME_*` /
> `GROBID_URL` / `LLM_TRANSCRIBE_MODEL` / `TENSION_*` / `LLM_MODEL_CATALOG_PATH` のみで、
> 以下に挙げる機能別のコスト上限などは**列挙されていない＝コンテナではコード既定値が使われます**。
> これらを実際に運用で変えたい場合は、compose の `environment:` に行を足すか `env_file:` を
> 追加する必要があります（ローカルで `uvicorn` を直接動かす場合は `.env` が読まれるため効きます）。

### LLM
```bash
LLM_PROVIDER=openai          # openai | gemini | google(=Vertex AI) | gemini-vertex(廃止予定)
LLM_API_KEY=sk-...           # 共通キー（OPENAI_API_KEY / GEMINI_API_KEY でも可）
LLM_FAST_MODEL=...           # 軽い判断用（既定 o3-mini 系）
LLM_STANDARD_MODEL=...       # 標準分析用
LLM_DEEP_MODEL=...           # 複雑推論用
LLM_ANALYSIS_MODEL=...       # 非 OpenAI プロバイダのフォールバック
LLM_EMBEDDING_MODEL=text-embedding-3-large
LLM_EMBEDDING_DIM=3072       # pgvector の次元（Gemini なら 768 など）
```
> LLM アダプタの切替ロジックは [コアエンジン](../backend/core-engine.md) の `llm.py` / `config.py` を参照。

### Google Cloud（Vertex AI / ADC を使う場合）
```bash
GCP_PROJECT_ID=...           # または GOOGLE_CLOUD_PROJECT
GCP_LOCATION=us-central1
GCP_USE_VERTEX_AI=true
GOOGLE_APPLICATION_CREDENTIALS=/app/.gcp/application_default_credentials.json
```
ホストの `./.gcp` がコンテナ `/app/.gcp:ro` にマウントされます。

### データストア
```bash
DATABASE_URL=postgresql://<user>:<pass>@<host>:<port>/<db>  # DB_* から組み立て
MINIO_ENDPOINT=...
MINIO_ACCESS_KEY=${MINIO_ROOT_USER}
MINIO_SECRET_KEY=${MINIO_ROOT_PASSWORD}
GROBID_URL=http://grobid:8070
```

### 認証・その他
```bash
JWT_SECRET=...               # JWT 署名鍵（必ず変更）
ADMIN_PASSWORD=...           # 初期システム管理者パスワード
CORS_ORIGINS=*               # 本番は明示リスト推奨
EPISTEME_DEFAULT_CARTRIDGE_ID=particle_physics
EPISTEME_CARTRIDGES_DIR=     # カートリッジのパス上書き（未設定なら自動探索）
```

### ハンズフリー音声会話 / TensionMiningAgent（B層）
```bash
LLM_TRANSCRIBE_MODEL=whisper-1   # 音声文字起こしモデル（openai プロバイダのみ）
TENSION_MAX_CALLS_PER_SESSION=3  # tension 解析の LLM コール上限（1セッションあたり）
TENSION_MAX_CALLS_PER_DAY=10     # 同・1ユーザー1日あたり
TENSION_LLM_MODEL=               # 空なら fast tier（LLM_FAST_MODEL）を使用
```

### 機能別の LLM コスト上限（各機能で独立したカウンタ）

いずれも「回数の上限」で、超過しても機能は落ちず `skipped_by_limit` 等で正直に記録されます
（金額ベースの制御は行いません）。空欄のモデル変数は各機能の既定 tier に委譲します。

```bash
# 同期チャット・単発 AI（1ユーザー1日あたり）
LEARNING_CHAT_MAX_CALLS_PER_DAY=300     # 学習チャット本体
COURSE_BUILDER_MAX_CALLS_PER_DAY=100    # コースビルダーチャット
LECTURE_REWRITE_MAX_CALLS_PER_DAY=100   # 原稿スタジオ rewrite
ASSISTANT_MAX_CALLS_PER_DAY=20          # Admin Copilot（intent 分類 + 応答）

# 非同期 worker / 解析（B層・D層・R層・W層）
TENSION_MAX_CALLS_PER_SESSION=3         # ↑ 上掲（B層 tension）
TENSION_MAX_CALLS_PER_DAY=10
ANCHOR_MAX_CALLS_PER_SESSION=3          # 構造帰属型の問い（tension とは独立）
ANCHOR_MAX_CALLS_PER_DAY=10
DOUBT_SCOPE_MAX_CALLS_PER_DAY=10        # D層 検証スコープ候補
DOUBT_ASSUMPTION_MAX_CALLS_PER_DAY=10   # D層 暗黙前提マイニングの LLM 正規化
DOUBT_FALSIFICATION_MAX_CALLS_PER_DAY=10 # SL層 反証条件候補
RECON_MAX_CALLS_PER_DAY=10              # R層 item オーサリング
RECON_MAX_ITEMS_PER_DOCUMENT=30         # 同・1 document あたりの生成 item 上限
DELIBERATION_MAX_CALLS_PER_SESSION=8    # W層 対話的検討
DELIBERATION_MAX_CALLS_PER_DAY=40
STDPART_MAX_CALLS_PER_DAY=10            # W層 Phase S 標準化判定

# 教員向けの生成機能
FIGURE_STUDIO_MAX_CALLS_PER_DAY=60      # 教材図スタジオ 対話生成
FIGURE_SUGGEST_MAX_CALLS_PER_DAY=20     # 同・ギャップ提案（対話とは独立）
ATLAS_ASSIST_MAX_CALLS_PER_DAY=60       # 分野の地図 骨格エディタの AI アシスト
DISCOVERY_COMPARE_MAX_CALLS_PER_DAY=20  # 論文レーダー 比較分析（教員ごと・日次）
DISCOVERY_COMPARE_LLM_MODEL=            # 同・モデル（空 = fast tier）
DISCOVERY_RANKING_MAX_CALLS_PER_DAY=100 # ディスカバリー / レーダーの関連度ランキング（embedding）
DELIBERATION_VOICE_MAX_CALLS_PER_DAY=200 # グラフ対話レビューの音声 STT/TTS（対話上限とは独立）
ATLAS_VECTOR_MAX_CALLS_PER_DAY=50       # VA層 アンカー埋め込みの構築（embedding）

# パイプラインのステージ（1 document = 1 コール系）
DISCUSS_OPENING_MAX_CALLS_PER_DAY=20       # discuss 開幕素材の生成
DISCUSS_OPENING_MAX_ITEMS_PER_DOCUMENT=4
LANDSCAPE_MAX_CALLS_PER_DAY=20             # 知識ランドスケープの配置候補
LANDSCAPE_MAX_PLACEMENTS_PER_DOCUMENT=8
LANDSCAPE_GAP_MAX_PER_DOCUMENT=3           # カテゴリギャップ候補（同一コールに相乗り）
LANDSCAPE_VECTOR_PREFILTER_TOPK=32         # VA層の配置プレフィルタ（0 で無効。region は常に全提示）

# 画像読み取りパイプライン（L層, apparatus_semantics）
APPARATUS_LLM_MODEL=gpt-4o                 # vision 同定モデル（v1 は OpenAI 経路のみ）
APPARATUS_MAX_IMAGES_PER_DOCUMENT=20       # 1 document あたり vision 対象にする図の上限
APPARATUS_MAX_CALLS_PER_DAY=30             # vision 呼び出しの日次上限
APPARATUS_ANALYSIS_MODE=iterative          # iterative（反復照合解析） | one_shot（旧方式）
APPARATUS_VERIFY_MAX_ITERATIONS=3          # バッチ解析での再スキャン最大回数
APPARATUS_REANALYZE_MAX_ITERATIONS=1       # 教員指示付き再解析（同期API）の最大回数
```

### 起動時の付帯機能（既定値はコード側）

```bash
LLM_MODEL_CATALOG_PATH=      # M層モデルカタログ JSON。空なら同梱 backend/config/llm_models.json
LLM_PRICE_TABLE_PATH=        # U層の単価表 JSON。空なら cost_usd は常に null（価格をハードコードしない）
HELP_KB_VECTOR_ENABLED=1     # help_kb ベクトル補助層の同期・検索（既定 on。0 で無効）
VERSION_SWEEPER_ENABLED=1    # V層 削除猶予スイーパの起動（既定 on）
VERSION_SWEEP_INTERVAL_SECONDS=3600  # 同・実行周期（秒）
PAPER_DISCOVERY_WORKER_ENABLED=1     # 論文ディスカバリー 取り込みキュー worker（既定 on）
PAPER_DISCOVERY_WORKER_INTERVAL_SECONDS=30  # 同・キューが空のときの待ち時間（秒）
DISCOVERY_CITATION_SOURCE_ENABLED=0  # 引用グラフ供給源（Semantic Scholar）のオプトイン（既定 off）
```

> 上記のうち `.env.example` に記載があるのは一部だけで（2026-09-03 時点で
> `LANDSCAPE_MAX_*` / `DELIBERATION_VOICE_MAX_CALLS_PER_DAY` / `DISCOVERY_COMPARE_*` など）、
> 既定値の正本はコード側です — `backend/core/config.py` の `Settings`（`AliasChoices` で
> env 名を宣言）、`core/help_kb/vector.py`、`core/versioning/worker.py`、
> `backend/api/ingest_worker.py`。`.env.example` に無い変数（`ANCHOR_*` / `STDPART_*` /
> `LANDSCAPE_VECTOR_PREFILTER_TOPK` / `DISCOVERY_RANKING_*` / `DISCOVERY_CITATION_SOURCE_ENABLED` /
> `ATLAS_VECTOR_MAX_CALLS_PER_DAY` / `PAPER_DISCOVERY_WORKER_*` 等）も同様に扱ってください。

---

## 4. 起動時の処理

`api-server` 起動時、`backend/api/main.py` の `_lifespan` で以下が実行されます。
`ADMIN_PASSWORD` が未設定の場合は起動せずに終了します（`sys.exit(1)`）。

### DB 接続リトライループ内（1〜6 をまとめて最大10回試行し、成功した時点で抜ける）

| # | 処理 | 実体 | 失敗時 |
|---|---|---|---|
| 1 | **マイグレーション適用** | `core/migrations.py::run_migrations()` が `backend/db/` の `init.sql` → `002_*.sql` 〜 番号順の最新（2026-09-03 時点 `076_*.sql`）までを**毎起動・番号順に全ファイル冪等再実行**する。多重起動は `pg_advisory_lock`（`MIGRATION_LOCK_KEY`）で排他し、ファイル単位で commit する | リトライ（10回失敗で起動中止） |
| 2 | **システム管理者アカウント初期化** | `users` に `Administrator` が無ければ `ADMIN_PASSWORD` で作成（migration 適用後に行う） | 同上 |
| 3 | **分野の地図 骨格のシード取込** | `core/atlas_store.py::import_bundled_skeletons()`。① カートリッジ同梱 `cartridges/<id>/atlas/skeleton.yaml` と ② 骨格専用バンドルドメイン `backend/atlas_domains/<key>/skeleton.yaml`（+ 任意 `domain.json`）を、DB に同版が無いときだけ取り込む（以後 DB が正本） | 警告ログのみ（rollback して継続） |
| 4 | **ビルトインスキーマの seed** | `core/schema_registry.py::seed_builtin_schema()` が `OntologyType` / `CorePredicate` を DB に投入 | リトライ対象 |
| 5 | **ナレッジライブラリ（L層）のシード取込** | `core/library/seed.py::import_bundled_library()`（カートリッジ同梱 `library/*.json` の冪等取込） | 警告ログのみ |
| 6 | **LLM モデル方針（M層）の env シード + DB バックエンド有効化** | `core/llm_policy_store.py::seed_env_policies()` が `*_LLM_MODEL` env を `scope='system'` 行として冪等取込（既存 DB 行は上書きしない）。その後 `llm_policy.set_policy_backend(DbPolicyBackend())` で DB 実装へ差し替え | 警告ログのみ（env/tier 既定のみが効く挙動で継続） |

### ループを抜けた後（いずれも fail-open。失敗しても警告ログのみで起動は止めない）

| # | 処理 | 実体 |
|---|---|---|
| 7 | **V層 削除猶予スイーパの起動** | `core/versioning/worker.py::start_background_sweeper()`（daemon スレッド。`VERSION_SWEEPER_ENABLED` / `VERSION_SWEEP_INTERVAL_SECONDS`） |
| 8 | **論文ディスカバリー 取り込みキュー worker の起動** | `backend/api/ingest_worker.py::start_background_worker()`（daemon スレッド。`PAPER_DISCOVERY_WORKER_ENABLED` / `PAPER_DISCOVERY_WORKER_INTERVAL_SECONDS`）。**arXiv を検索しない**（`arxiv_client` を import せず、教員がキューに積んだ行だけを処理する — PD1） |
| 9 | **状態遷移 watcher の起動** | `core/status/watcher.py::start_watcher()`（状態管理・通知基盤） |
| 10 | **help_kb マニュアルの検証** | `core/help_kb/validator.py::validate_manual()`（front-matter・anchor・リンク。違反は warning ログ） |
| 11 | **学習画面 UI アンカー表の検証** | 同 `check_ui_anchor_mappings()`（`core/help_kb/ui_anchors.py` の実在・audience 越境） |
| 12 | **管理画面 UI アンカー表の検証** | 同 `check_admin_ui_anchor_mappings()`（`core/help_kb/admin_ui_anchors.py`） |
| 13 | **help_kb 配信スナップショットの content-hash 監査記帳** | `core/help_kb/audit.py::record_snapshot_if_changed()`（変化時のみ `theory_review_events` へ記帳・冪等） |
| 14 | **help_kb ベクトル補助層の同期** | `core/help_kb/vector.py::sync_manual_vectors()` をバックグラウンドスレッドで実行（外部 API を呼ぶため非同期） |

マイグレーション一覧は [データモデル](data-model.md#マイグレーション一覧) を参照。

> **全 migration ファイルは冪等でなければなりません**（毎起動で再実行されるため）。
> 新しいスキーマ変更は必ず**新しい番号のファイルを追加**します。詳細は CLAUDE.md
> 「マイグレーションの正本一本化」および `core/migrations.py` の docstring を参照。

---

## 5. アクセス先

### 本番 / 共通
| サービス | URL |
|---|---|
| 学習UI | http://localhost:3000 |
| 管理UI | http://localhost:3000/admin.html |

### ローカル開発時のみ（local compose 併用）
| サービス | URL |
|---|---|
| Swagger UI | http://localhost:8001/docs（※直接公開する設定の場合） |
| MinIO コンソール | http://localhost:9001 |
| GROBID | http://localhost:8070 |
| ngrok Web UI | http://localhost:4040 |

初期アカウントは `ADMIN_PASSWORD` の管理者のみ。教員・学生アカウントは管理 UI から作成します。

---

[← アーキテクチャ概要](overview.md) ｜ 次へ: [データモデル →](data-model.md)
