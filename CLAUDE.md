# Episteme Graph — 開発コンテキスト

## ミッション

大学院生の学習プロセスを支援する知識グラフ管理システム。
文献から抽出した知識をグラフ構造で管理し、RAGベースの対話型学習を実現する。

## 技術スタック

| レイヤー | 技術 |
|---|---|
| フロントエンド | Vanilla JS SPA + nginx |
| API | FastAPI (Python 3.11) |
| RDB + ベクトル | PostgreSQL 16 + pgvector (cosine, 3072次元) |
| グラフDB | Neo4j 5 (Cypher) — グラフ走査専用 |
| ストレージ | MinIO (S3互換) |
| LLM | OpenAI API |
| 認証 | JWT + bcrypt |

## ディレクトリ構成

```
frontend/          → 学習UI (HTML/CSS/JS, nginx)
backend/api/       → 統合APIサーバー (認証・学習・Admin)
backend/core/      → コアエンジン (抽出・検索・グラフ化)
backend/db/        → PostgreSQL スキーマ定義 (init.sql)
```

## 開発ルール

### 1. 環境変数
- シークレット値（APIキー、パスワード）はハードコードしない
- すべて環境変数経由で設定（`.env.example` 参照）

### 2. Pydanticスキーマ
- データモデルの定義は `backend/core/schema.py` に集約
- API固有のリクエスト/レスポンスモデルは `backend/api/main.py` 内に定義

### 3. ナレッジグラフ DSL
- 概念間の関係は `CorePredicate` 列挙型で定義:
  CAUSES, INHIBITS, CORRELATES, DEFINES, MEASURES, TRANSFORMS, REQUIRES, CONTAINS, EQUIVALENT
- 各エッジは `CausalEdge` スキーマに準拠
- 抽象構造は SMILES DSL で表現可能

### 4. PDF処理パイプライン
1. PDFアップロード → MinIOに保存
2. PyMuPDFでテキスト抽出
3. チャンク分割 → PostgreSQL pgvectorにembedding
4. LLMでナレッジグラフ構築 → PostgreSQL (documents) + Neo4j (グラフ走査用)

### 4a. データストア構成
- **PostgreSQL（正本）:** ユーザー・認証、教材メタデータ、チャンク本文+embedding (pgvector)、学習者状態、コース管理、対話履歴
- **Neo4j（グラフ走査専用）:** 概念グラフ (REQUIRES, RELATES_TO, CONTAINS)、チャンク↔概念クロスリンク
- **MinIO:** PDF原本、PaperStructure JSON

### 5. RAGチャット
- 教材チャンクをベクトル検索でコンテキストとして取得
- 誤解検出: LLM応答に「訂正」が含まれたら自動記録
- ドリルダウン: `[〇〇について詳しく聞く]` 形式で提示

### 6. テスト
- `pytest` を使用
- テストファイルは `backend/tests/` に配置

## コマンド

```bash
# 開発サーバー起動
docker compose up -d

# APIサーバーのみ再ビルド
docker compose up -d --build api-server

# ログ確認
docker compose logs -f api-server
```
