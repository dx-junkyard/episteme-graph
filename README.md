# episteme-graph

大学院生の学習プロセスを支援し、文献から抽出した知識をグラフ構造で管理するシステム。
散在する先行研究やアイデアをナレッジグラフとして繋ぎ合わせ、研究プロセスを加速させます。

## アーキテクチャ

```
episteme-graph/
├── frontend/                  # 学習支援UI (HTML/CSS/JS + nginx)
│   ├── public/
│   │   ├── index.html         # 学習UI (3パネルレイアウト)
│   │   ├── admin.html         # 管理UI (教材・コース管理)
│   │   ├── css/styles.css     # デザインシステム
│   │   └── js/
│   │       ├── app.js         # 学習SPA ロジック
│   │       └── admin.js       # 管理SPA ロジック
│   ├── nginx.conf             # リバースプロキシ設定
│   └── Dockerfile
├── backend/
│   ├── api/                   # 統合APIサーバー (FastAPI)
│   │   └── main.py            # 認証・学習・Admin API
│   ├── core/                  # コアエンジン
│   │   ├── schema.py          # Pydanticスキーマ定義
│   │   ├── models.py          # SQLAlchemy ORMモデル
│   │   ├── postgres.py        # PostgreSQL接続管理
│   │   ├── db.py              # Neo4jドライバ
│   │   ├── extractor.py       # PDF→構造化データ抽出
│   │   ├── embedder.py        # pgvectorベクトル検索・登録
│   │   ├── chat.py            # RAGチャットロジック
│   │   ├── llm.py             # OpenAIクライアント
│   │   ├── storage.py         # MinIOオブジェクトストレージ
│   │   ├── batch.py           # パターン評価バッチ
│   │   ├── harvester.py       # arXiv API連携
│   │   └── isom.py            # .isom DSLシリアライズ
│   ├── db/
│   │   └── init.sql           # PostgreSQL スキーマ定義
│   └── Dockerfile
├── docker-compose.yml         # 全サービスオーケストレーション
├── .env.example               # 環境変数テンプレート
└── CLAUDE.md                  # 開発コンテキスト
```

## 主な機能

### 1. 学習支援UI
- 3パネルレイアウト（サイドバー・チャット・詳細パネル）
- コース管理（章・トピック・概念マップ）
- RAGベースの対話型学習チャット
- 誤解検出と自動記録（LLM応答に「訂正」が含まれたら記録）
- 学習進捗トラッキング（習得概念・学習中概念・連続学習日数）
- 前提知識チェック（コースデータ内 prerequisites による適応的ルーティング）
- **インタラクティブ・レクチャーモード**（Issue #66）
  - セミナー形式の段階的音声解説（TTS音声 + カラオケ風ワードハイライト）
  - 数式のLaTeX表示と音声読み上げテキスト自動生成
  - 習得状態に基づく適応的シーケンス（既知チャンクのスキップ/簡易版変換）
  - レクチャー中断チャット（一時停止して質問→コンテキスト保持で回答→再開）
  - フェードイン・アニメーション付きセグメント表示

### 2. 知識抽出・グラフ化エンジン
- PDF文献からの構造化データ抽出（PyMuPDFテキスト抽出 + LLM解析）
- pgvectorによるセマンティックベクトル検索（cosine距離, 3072次元）
- DSL (SMILES) ベースの抽象構造表現
- 構造的同型性に基づくクロスドメインパターンマッチング

### 3. 管理UI（教師・管理者向け）
- PDFアップロード → テキスト抽出 → ナレッジグラフ自動構築
- LLMによる概念・関係性の自動抽出
- AI支援コースビルダー（対話形式でコース構造を設計）
- ユーザーアカウント管理（学生・教師アカウントの作成）

### 4. 認証・ロール管理
- JWT + bcryptによる認証
- 3段階のロール: STUDENT / TEACHER / SYSTEM_ADMIN
- ロールに応じたAPI・機能のアクセス制御

## セットアップ

### 前提条件
- Docker & Docker Compose
- OpenAI APIキー

### 起動手順

```bash
# 1. 環境変数を設定
cp .env.example .env
# .env を編集して OPENAI_API_KEY, ADMIN_PASSWORD を設定

# 2. コンテナを起動
docker compose up -d

# 3. アクセス
# 学習UI:  http://localhost:3000
# 管理UI:  http://localhost:3000/admin.html
# API:     http://localhost:8001
# Neo4j:   http://localhost:7474
# MinIO:   http://localhost:9001
```

## 技術スタック

| コンポーネント | 技術 |
|---|---|
| フロントエンド | Vanilla JS + CSS (nginx配信) |
| APIサーバー | FastAPI (Python 3.11) |
| RDB + ベクトル検索 | PostgreSQL 16 + pgvector (cosine, 3072次元) |
| グラフDB | Neo4j 5 (概念グラフ走査用) |
| オブジェクトストレージ | MinIO (S3互換) |
| LLM | OpenAI API (gpt-4o, text-embedding-3-large) |
| TTS 音声合成 | OpenAI TTS API (tts-1) |
| 認証 | JWT + bcrypt |

## ライセンス

Apache-2.0
