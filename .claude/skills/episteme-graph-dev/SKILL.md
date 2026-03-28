---
name: episteme-graph-dev
description: >
  Episteme GraphのバックエンドAPI、データベース操作、ルーティングの実装や修正を行う際に使用します。
  ユーザーから「APIを追加して」「DBのスキーマを変更して」「コードをリファクタリングして」
  「エンドポイントを修正して」「テストを書いて」などの開発タスクを依頼された場合に自動的に発動してください。
---
# Episteme Graph — 開発支援スキル

## 概要

大学院生の学習プロセスを支援する知識グラフ管理システムの開発を支援するスキル。
PDF教材からの知識抽出、RAGベースの対話型学習、コース管理の開発ルールを定義する。

## アーキテクチャ

| モジュール | 役割 | 主要ファイル |
|---|---|---|
| `backend/api/main.py` | FastAPI アプリ本体 (lifespan, CORS, ルーター統合) | エントリポイント |
| `backend/api/routes/auth.py` | 認証ルーター | 登録・ログイン・ユーザー情報 |
| `backend/api/routes/learning.py` | 学習ルーター | コース管理・RAGチャット・進捗 |
| `backend/api/routes/admin.py` | 管理ルーター | 教材アップロード・コースビルダー・ユーザー管理 |
| `backend/core/config.py` | 設定一元管理 | pydantic-settings による環境変数管理 |
| `backend/core/llm.py` | LLM 抽象化レイヤー | Reasoning モデル自動対応 |
| `backend/core/models.py` | SQLAlchemy ORM モデル | 全テーブル定義 |
| `backend/core/schema.py` | Pydantic スキーマ | ドメインモデル (PaperStructure 等) |
| `backend/core/extractor.py` | PDF 構造化抽出 | 仮説検証型チャンク解析 |
| `backend/core/embedder.py` | ベクトル検索 | PostgreSQL pgvector |
| `backend/core/chat.py` | RAG チャット | コンテキスト検索 + LLM |
| `backend/core/postgres.py` | PostgreSQL セッション管理 | SQLAlchemy セッション |
| `backend/core/db.py` | Neo4j ドライバ | グラフ走査専用 (レガシー) |
| `backend/core/storage.py` | MinIO ストレージ | S3互換ファイル管理 |
| `frontend/public/js/app.js` | 学習 UI | ES6+ SPA |
| `frontend/public/js/admin.js` | 管理 UI | ES5互換 Vanilla JS |

## 技術スタックの現状

| レイヤー | 技術 |
|---|---|
| API フレームワーク | FastAPI (ルーター分割済み) |
| ORM | SQLAlchemy 2.x + pgvector |
| RDB + ベクトル | PostgreSQL 16 + pgvector (cosine, 3072次元) |
| グラフDB | Neo4j 5 (Cypher) — レガシー、グラフ走査のみ |
| ストレージ | MinIO (S3互換) |
| LLM | OpenAI API (gpt-4o / text-embedding-3-large、Reasoning モデル対応済み) |
| 認証 | JWT (HS256) + bcrypt |
| 設定管理 | pydantic-settings (core/config.py) |
| フロントエンド | Vanilla JS SPA + nginx |

## 開発時の必須ルール

### 1. 設定の Config 化 — `os.environ` の直書き禁止

すべての環境変数は `core/config.py` の `Settings` クラスに集約されている。
他のモジュールで `os.environ` や `os.getenv()` を直接使用してはならない。

```python
# NG
import os
api_key = os.environ["OPENAI_API_KEY"]

# OK
from core.config import get_settings
settings = get_settings()
api_key = settings.openai_api_key
```

### 2. ルーターの分割 — `main.py` への直書き禁止

新しいエンドポイントは `backend/api/routes/` 配下の適切なルーターに追加する。
`main.py` にエンドポイントを直接定義してはならない。

- 認証系 → `routes/auth.py`
- 学習系 → `routes/learning.py`
- 管理系 → `routes/admin.py`
- 新ドメインが必要な場合 → 新しいルーターファイルを作成

### 3. LLM 抽象化レイヤーの利用 — `openai` SDK の直接利用禁止

LLM の呼び出しは必ず `core/llm.py` の公開 API を経由する。
`import openai` や `from openai import OpenAI` を他のモジュールで行わない。

```python
# NG
from openai import OpenAI
client = OpenAI()

# OK
from core.llm import generate_text, generate_embeddings, generate_text_with_structured_output
```

`core/llm.py` は Reasoning モデル (o1, o3, gpt-5.x) 向けの自動変換を内蔵:
- `system` ロール → `developer` ロールへの変換
- `temperature` / `max_tokens` の自動除去

### 4. データベース操作

- ORM モデルは `core/models.py` で定義 — テーブル追加・変更時はここを更新
- Pydantic スキーマは `core/schema.py` — ドメインモデルの定義
- API 固有のリクエスト/レスポンスモデルは各ルーターファイル内に定義
- PostgreSQL セッションは `core/postgres.py` の `get_session()` を使用し、必ず `try/finally` で `session.close()`

### 5. フロントエンド

- `admin.js` は Vanilla JS (ES5互換) — `var`, `function` キーワードを使用
- `app.js` は ES6+ (const/let, async/await)
- フレームワーク不使用

### 6. 認証

- JWT トークンの署名鍵は `settings.jwt_secret` から取得
- 全 API エンドポイントで認証デコレータを使用
- RBAC: STUDENT / TEACHER / SYSTEM_ADMIN

## 動的参照ガイド

コード生成時は、以下のファイルを直接読み込んで最新の定義を確認すること:

- **ORM モデル (テーブル定義)**: `backend/core/models.py` を読んで確認
- **Pydantic スキーマ**: `backend/core/schema.py` を読んで確認
- **設定項目**: `backend/core/config.py` の `Settings` クラスを読んで確認
- **LLM API**: `backend/core/llm.py` の公開関数を読んで確認
- **既存ルーター**: `backend/api/routes/` 配下のファイルを読んで確認
- **テスト**: `backend/tests/` 配下を読んで既存テストパターンを確認
