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

`docker-compose.yml:3-108` の内容。

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
- API リバースプロキシの定義は `frontend/nginx.conf`（`/api/learning`, `/api/auth`, `/api/admin`, `/api/groups`, `/api/me`, `/api/courses`, `/api/documents` などをプロキシ）。詳細は [フロントエンド構成](../frontend/overview.md)。

---

## 3. 主要な環境変数（.env.example）

`api-server` の environment は `docker-compose.yml:37-68` で `.env` から注入されます。

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

---

## 4. 起動時の処理

`api-server` 起動時、`backend/api/main.py` の lifespan で以下が実行されます。

1. **マイグレーション適用** — `backend/db/` の `init.sql`〜`022_*.sql` を冪等に適用（pgvector 次元の変更や列追加を含む）。
2. **ビルトインスキーマの seed** — `schema_registry.seed_builtin_schema()` が `OntologyType` / `CorePredicate` を DB に投入。
3. **システム管理者アカウント初期化** — `ADMIN_PASSWORD` で初期管理者を作成。

マイグレーション一覧は [データモデル](data-model.md#マイグレーション一覧) を参照。

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
