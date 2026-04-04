# episteme-graph

大学院生の学習プロセスを支援する知識グラフ管理システム。
PDF文献から概念・関係性を自動抽出してナレッジグラフを構築し、RAGベースの対話型学習・インタラクティブ講義を実現します。

## コンセプト

研究者・大学院生が直面する「散在する先行研究の統合」「前提知識の体系的習得」という課題を解決するため、次の3層で設計されています。

1. **知識の構造化** — PDFをアップロードするだけで概念グラフが自動生成される
2. **適応的学習** — 習得状態を追跡し、前提知識に応じた問いかけで理解を深める
3. **没入型講義** — TTS音声＋カラオケ式ハイライトで、論文をセミナー形式に変換する

## 技術スタック

| レイヤー | 技術 |
|---|---|
| フロントエンド | Vanilla JS SPA + nginx |
| APIサーバー | FastAPI (Python 3.11) |
| RDB + ベクトル検索 | PostgreSQL 16 + pgvector (cosine, 3072次元) |
| グラフDB | Neo4j 5（概念グラフ走査専用） |
| オブジェクトストレージ | MinIO（S3互換） |
| LLM | OpenAI API (gpt-4o, text-embedding-3-large) |
| TTS 音声合成 | OpenAI TTS API (tts-1) |
| 認証 | JWT (HS256) + bcrypt |

## セットアップ

### 前提条件

- Docker & Docker Compose
- OpenAI APIキー

### 起動手順

```bash
# 1. 環境変数を設定
cp .env.example .env
# .env を編集: OPENAI_API_KEY, ADMIN_PASSWORD, JWT_SECRET を必ず設定

# 2. 全サービスを起動
docker compose up -d

# 3. ログ確認（初回はマイグレーション完了を確認）
docker compose logs -f api-server
```

### アクセス先

| サービス | URL |
|---|---|
| 学習UI | http://localhost:3000 |
| 管理UI | http://localhost:3000/admin.html |
| API（Swagger） | http://localhost:8001/docs |
| Neo4j Browser | http://localhost:7474 |
| MinIO コンソール | http://localhost:9001 |

### 初期アカウント

起動直後は `ADMIN_PASSWORD` で設定したパスワードで管理者としてログインできます。
教師アカウント・学生アカウントは管理UIから作成します。

## 主な機能

### 1. 学習UI（学生向け）

3パネルレイアウト（コース一覧・チャット・詳細）で学習を進めます。

- **RAGチャット** — pgvector検索で関連チャンクを取得し、LLMが回答
- **誤解検出** — 応答に「訂正」「間違い」が含まれると自動記録
- **学習進捗トラッキング** — 習得概念・学習中概念・連続学習日数
- **前提知識チェック** — コース内 `prerequisites` に基づく適応的ルーティング（未習得なら逆質問）
- **コース受講登録** — 公開されたテンプレートコースをクローンして自分用インスタンスを作成

#### インタラクティブ・レクチャーモード

論文チャンクをセミナー形式の音声講義に変換する没入型学習機能。

- TTS音声（tts-1）＋カラオケ風ワードハイライト
- 数式のLaTeX表示と音声読み上げテキスト自動生成
- 習得状態に基づく適応的シーケンス（既知チャンクのスキップ・簡易版変換）
- **中断チャット** — 再生を一時停止して質問 → コンテキスト保持で回答 → 再開

---

### 2. PDF → ナレッジグラフ パイプライン

```
PDF アップロード
  → PyMuPDF テキスト抽出
  → LLM 仮説駆動型分析 → PaperStructure 生成
  → テキストチャンク → PostgreSQL pgvector（3072次元）
  → 概念ノード・エッジ → Neo4j（REQUIRES / RELATES_TO / CONTAINS）
  → PaperStructure JSON → MinIO（extracted-structures バケット）
```

- **DSL（SMILES形式）** — 抽象構造を `(varID:OntologyType:value) ==[CorePredicate:verb:polarity]=> (...)` で表現
- **構造的同型性評価** — パターン登録時にバックグラウンドで過去論文群とのクロスドメインマッチングを非同期実行
- **arXiv ハーベスター** — 商業出版社フィルタリング付きでarXiv論文を自動収集
- **.isom シリアライズ** — OSLパイプライン連携用の `.isom` ファイル出力

---

### 3. 管理UI（教師・管理者向け）

#### 教材管理

- PDFアップロード → 非同期バックグラウンドタスクで抽出・グラフ化
- `GET /api/admin/tasks/{task_id}` でポーリングして進捗確認
- 教材詳細で抽出された概念グラフ構造を閲覧

#### AI支援コースビルダー

- LLMとの対話形式でコース構造（章・トピック・前提知識・到達目標）を設計
- セッション履歴をPostgreSQLに永続化（ページリロード後も継続可能）
- 承認時に `is_template = TRUE` でコースを登録し、学生への公開が可能

#### Lecture Script Studio

教員向けのレクチャー原稿事前構築・編集機能。

- コースに紐づくチャンクに対して**バッチスクリプト生成**（非同期＋ポーリング）
- AIによるスクリプト書き換え（トーン・難易度・長さ調整）
- 手動スクリプト保存
- 全チャンクの**バッチ音声生成**（非同期＋ポーリング）

#### 動的スキーマ進化

固定のOntologyType/CorePredicateを超えてドメイン固有の概念・関係性を拡張できます。

1. 未回答クエリ（`unanswered_query_logs`）をメタ分析エンジンが自動解析
2. 不足しているスキーマ要素をLLMが提案（SchemaProposal）
3. Shadow Testing — 提案を承認前に既存教材でプレビュー検証（Before/After差分）
4. 教員が承認 → 新スキーマを `schema_registry` に追加
5. バックグラウンドで**再抽出ジョブ**を実行し、既存ナレッジグラフを新スキーマで再構築

#### ユーザー管理

- 学生アカウント作成（TEACHER以上）
- 教師アカウント作成（SYSTEM_ADMINのみ）

---

### 4. 認証・ロール管理

| ロール | 権限 |
|---|---|
| STUDENT | 学習UI、チャット、コース受講登録 |
| TEACHER | 上記＋教材アップロード、コース作成・公開、学生アカウント作成 |
| SYSTEM_ADMIN | 全権限（教師アカウント作成を含む） |

---

## ディレクトリ構成

```
episteme-graph/
├── frontend/
│   ├── public/
│   │   ├── index.html         # 学習UI（3パネルレイアウト）
│   │   ├── admin.html         # 管理UI
│   │   ├── css/styles.css     # デザインシステム
│   │   └── js/
│   │       ├── app.js         # 学習SPA（ES6+）
│   │       └── admin.js       # 管理SPA（ES5互換）
│   ├── nginx.conf             # リバースプロキシ設定
│   └── Dockerfile
├── backend/
│   ├── api/
│   │   ├── main.py            # FastAPI アプリ本体・マイグレーション
│   │   ├── dependencies.py    # 認証依存関係
│   │   ├── schemas.py         # API固有 Pydantic モデル
│   │   ├── services.py        # ビジネスロジック共通関数
│   │   ├── routes/
│   │   │   ├── auth.py        # /api/auth/*
│   │   │   ├── learning.py    # /api/learning/*
│   │   │   ├── admin.py       # /api/admin/*
│   │   │   ├── lecture.py     # /api/learning/lecture/*
│   │   │   └── lecture_studio.py  # /api/admin/lecture-studio/*
│   │   └── Dockerfile
│   ├── core/
│   │   ├── schema.py          # 全 Pydantic モデル（OntologyType, CorePredicate など）
│   │   ├── schema_registry.py # 動的スキーマ（DBから読み込み・キャッシュ）
│   │   ├── models.py          # SQLAlchemy ORM モデル
│   │   ├── postgres.py        # PostgreSQL セッション管理
│   │   ├── db.py              # Neo4j ドライバ
│   │   ├── extractor.py       # PDF → 構造化データ抽出
│   │   ├── embedder.py        # pgvector ベクトル保存・検索
│   │   ├── chat.py            # RAG チャットロジック
│   │   ├── lecture.py         # レクチャーシーケンス生成・TTS補助
│   │   ├── llm.py             # OpenAI クライアント
│   │   ├── storage.py         # MinIO S3互換ストレージ
│   │   ├── batch.py           # 構造的同型性評価バッチ
│   │   ├── meta_analyzer.py   # 未回答クエリ → スキーマ拡張提案
│   │   ├── simulator.py       # スキーマ提案の Shadow Testing
│   │   ├── reextractor.py     # スキーマ更新後の再抽出ワークフロー
│   │   ├── harvester.py       # arXiv API 連携
│   │   ├── isom.py            # .isom DSL シリアライズ
│   │   └── config.py          # 設定管理
│   ├── db/
│   │   ├── init.sql           # 初期スキーマ
│   │   ├── 002_a1_a2_a3.sql   # コースビルダー永続化・受講登録・前提知識
│   │   ├── 003_unanswered_queries.sql
│   │   ├── 004_schema_evolution.sql
│   │   ├── 005_background_tasks.sql
│   │   ├── 006_lecture_mode.sql
│   │   └── 007_drop_arxiv_id.sql
│   └── tests/                 # pytest テスト
├── docker-compose.yml
└── .env.example
```

## API エンドポイント一覧

### 認証 `/api/auth`

| メソッド | パス | 説明 |
|---|---|---|
| POST | `/register` | ユーザー登録 |
| POST | `/login` | ログイン（JWTトークン取得） |
| GET | `/me` | 現在のユーザー情報 |

### 学習 `/api/learning`

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/courses` | コース一覧（公開テンプレート含む） |
| POST | `/courses` | コース新規作成 |
| GET | `/courses/{id}` | コース詳細 |
| PUT | `/courses/{id}` | コース更新 |
| DELETE | `/courses/{id}` | コース削除 |
| GET | `/courses/{id}/progress` | 学習進捗 |
| POST | `/courses/{id}/enroll` | 公開コースに受講登録（クローン） |
| GET | `/courses/{cid}/topics/{tid}/chat` | チャット履歴 |
| POST | `/courses/{cid}/topics/{tid}/chat` | RAGチャット |

### レクチャーモード `/api/learning/lecture`

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/sequence` | レクチャーシーケンス取得 |
| POST | `/tts` | TTS音声生成 |
| POST | `/interrupt` | 中断チャット |

### 管理 `/api/admin`

| メソッド | パス | 説明 |
|---|---|---|
| POST | `/materials/upload` | PDF教材アップロード（非同期） |
| GET | `/materials` | 教材一覧 |
| GET | `/materials/{id}` | 教材詳細（グラフ構造） |
| GET | `/tasks/{task_id}` | バックグラウンドタスク進捗 |
| POST | `/course-builder/sessions` | コース構築セッション作成 |
| GET | `/course-builder/sessions` | セッション一覧 |
| GET | `/course-builder/sessions/{id}` | セッション取得 |
| PUT | `/course-builder/sessions/{id}` | セッション更新 |
| POST | `/course-builder/chat` | コース構築AIチャット |
| GET | `/courses` | 管理用コース一覧 |
| GET | `/courses/{id}/draft-format` | コースのドラフト形式取得 |
| PUT | `/courses/{id}/publish` | コースを学生に公開 |
| GET | `/courses/{id}/unanswered-queries` | 未回答クエリ一覧 |
| POST | `/users/student` | 学生アカウント作成 |
| POST | `/users/teacher` | 教師アカウント作成 |
| GET | `/schema/types` | OntologyType 一覧 |
| POST | `/schema/types` | OntologyType 追加 |
| GET | `/schema/predicates` | CorePredicate 一覧 |
| POST | `/schema/predicates` | CorePredicate 追加 |
| GET | `/schema-proposals` | スキーマ拡張提案一覧 |
| POST | `/schema-proposals/analyze` | 未回答クエリからスキーマ提案を生成 |
| PUT | `/schema-proposals/{id}/approve` | スキーマ提案を承認 |
| PUT | `/schema-proposals/{id}/approve-with-scope` | スコープ指定で承認 |
| PUT | `/schema-proposals/{id}/reject` | スキーマ提案を棄却 |
| GET | `/reextraction-jobs` | 再抽出ジョブ一覧 |

### Lecture Script Studio `/api/admin`

| メソッド | パス | 説明 |
|---|---|---|
| POST | `/lecture-studio/courses/{id}/generate-scripts` | バッチスクリプト生成開始（非同期） |
| GET | `/lecture-studio/courses/{id}/scripts` | スクリプト一覧 |
| POST | `/lecture-studio/scripts/{id}/save` | スクリプト手動保存 |
| POST | `/lecture-studio/scripts/{id}/rewrite` | AIスクリプト書き換え |
| POST | `/lecture-studio/courses/{id}/generate-audio` | バッチ音声生成開始（非同期） |

## 開発

```bash
# APIサーバーをコード変更後に再ビルド
docker compose up -d --build api-server

# ログ確認
docker compose logs -f api-server

# テスト実行（全件）
cd backend && pytest backend/tests/

# テスト実行（単一ファイル）
cd backend && pytest backend/tests/test_diff_merge.py -v
```

## ライセンス

Apache-2.0
