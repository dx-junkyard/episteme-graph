# episteme-graph

大学院生の学習プロセスを支援し、文献から抽出した知識をグラフ構造で管理するシステム。
散在する先行研究やアイデアをナレッジグラフとして繋ぎ合わせ、研究プロセスを加速させます。

## アーキテクチャ

```
episteme-graph/
├── frontend/                  # 学習支援UI (HTML/CSS/JS + nginx)
│   ├── public/
│   │   ├── index.html         # メインHTML (3パネルレイアウト)
│   │   ├── css/styles.css     # デザインシステム
│   │   └── js/app.js          # SPA アプリケーションロジック
│   ├── nginx.conf             # リバースプロキシ設定
│   └── Dockerfile
├── backend/
│   ├── api/                   # 統合APIサーバー (FastAPI)
│   │   └── main.py            # 認証・学習・Admin PDF管理
│   ├── core/                  # コアエンジン (抽出・検索・グラフ化)
│   │   ├── schema.py          # Pydanticスキーマ定義
│   │   ├── extractor.py       # PDF→構造化データ抽出
│   │   ├── embedder.py        # Qdrantベクトル検索
│   │   ├── llm.py             # OpenAIクライアント
│   │   ├── storage.py         # MinIOオブジェクトストレージ
│   │   ├── chat.py            # RAGチャットロジック
│   │   ├── db.py              # Neo4jドライバ
│   │   ├── isom.py            # .isom DSLシリアライズ
│   │   ├── batch.py           # パターン評価バッチ
│   │   └── harvester.py       # arXiv API連携
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
- 誤解検出と記録
- 学習進捗トラッキング

### 2. 知識抽出・グラフ化エンジン
- PDF文献からの構造化データ抽出（仮説検証型チャンク解析）
- ベクトル検索によるセマンティック検索
- DSL (SMILES) ベースの抽象構造表現
- 構造的同型性に基づくクロスドメインパターンマッチング

### 3. Admin PDF教材管理
- PDFアップロード → テキスト抽出 → ナレッジグラフ自動構築
- LLMによる概念・関係性の自動抽出
- Qdrantへのチャンクembedding（RAGチャットの教材コンテキスト）

## セットアップ

### 前提条件
- Docker & Docker Compose
- OpenAI APIキー

### 起動手順

```bash
# 1. 環境変数を設定
cp .env.example .env
# .env を編集して OPENAI_API_KEY を設定

# 2. コンテナを起動
docker compose up -d

# 3. アクセス
# 学習UI:  http://localhost:3000
# API:     http://localhost:8001
# Neo4j:   http://localhost:7474
# MinIO:   http://localhost:9001
```

## 技術スタック

| コンポーネント | 技術 |
|---|---|
| フロントエンド | Vanilla JS + CSS (nginx配信) |
| APIサーバー | FastAPI (Python 3.11) |
| グラフDB | Neo4j 5 |
| ベクトルDB | Qdrant |
| オブジェクトストレージ | MinIO |
| LLM | OpenAI API (gpt-4o, text-embedding-3-large) |
| 認証 | JWT + bcrypt |

## ライセンス

MIT
