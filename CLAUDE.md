# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ミッション

大学院生の学習プロセスを支援する知識グラフ管理システム。
文献から抽出した知識をグラフ構造で管理し、RAGベースの対話型学習を実現する。

## 技術スタック

| レイヤー | 技術 |
|---|---|
| フロントエンド | Vanilla JS SPA + nginx |
| API | FastAPI (Python 3.11) |
| グラフDB | Neo4j 5 (Cypher) |
| ベクトルDB | Qdrant (cosine, 3072次元) |
| ストレージ | MinIO (S3互換) |
| LLM | OpenAI API (gpt-4o / text-embedding-3-large) |
| 認証 | JWT (HS256) + bcrypt |

## コマンド

```bash
# 開発サーバー起動
docker compose up -d

# APIサーバーのみ再ビルド（コード変更後）
docker compose up -d --build api-server

# ログ確認
docker compose logs -f api-server

# テスト実行（全件）
cd backend && pytest backend/tests/

# テスト実行（単一ファイル）
cd backend && pytest backend/tests/test_diff_merge.py -v

# アクセス先
# http://localhost:3000        → 学習UI
# http://localhost:8001/docs   → Swagger UI
# http://localhost:7474        → Neo4j Browser
# http://localhost:9001        → MinIO コンソール
```

## アーキテクチャ

### ディレクトリ構成

```
frontend/          → SPA (HTML/CSS/JS) + nginx リバースプロキシ
backend/api/       → FastAPI サーバー（認証・学習・Admin エンドポイント）
backend/core/      → コアエンジン（スキーマ・抽出・埋め込み・検索）
backend/tests/     → pytest テスト
backend/scripts/   → 初期化スクリプト
```

### バックエンド主要ファイル

| ファイル | 役割 |
|---|---|
| `backend/core/schema.py` | 全 Pydantic モデル定義（OntologyType, CorePredicate, PaperStructure など） |
| `backend/api/main.py` | FastAPI アプリ本体（全エンドポイント・API固有モデル） |
| `backend/core/extractor.py` | PDF→GROBID→LLM 構造抽出パイプライン |
| `backend/core/embedder.py` | Qdrant ベクトル保存・検索 |
| `backend/core/chat.py` | RAG チャットロジック |
| `backend/core/db.py` | Neo4j ドライバシングルトン |
| `backend/core/llm.py` | OpenAI クライアントファクトリ |
| `backend/core/storage.py` | MinIO S3互換ストレージ |

### PDF → ナレッジグラフ パイプライン

1. PDF アップロード → MinIO (`raw-papers` バケット)
2. GROBID TEI-XML パース（利用不可の場合は PyMuPDF → 文分割にフォールバック）
3. LLM で仮説駆動型分析 → `PaperStructure` 生成
4. テキストチャンク → Qdrant 埋め込み（3072次元）
5. 概念ノード・エッジ → Neo4j 保存
6. 抽出構造 → MinIO (`extracted-structures` バケット)

### RAG チャットフロー

1. ユーザー質問 → Qdrant ベクトル検索（コース/論文でフィルタ）
2. 上位チャンク + MinIO から `PaperStructure` 取得
3. チャット履歴 + コンテキストで LLM プロンプト構築
4. レスポンスから誤解検出（「訂正」「誤り」「間違い」パターン）
5. ドリルダウンリンク提示（`[〇〇について詳しく聞く]`）

### 認可モデル (RBAC)

- **STUDENT**: 学習UIアクセス、チャット
- **TEACHER**: 教材アップロード、学生アカウント作成
- **SYSTEM_ADMIN**: 全権限（教師アカウント作成を含む）

## 開発ルール

### 1. 環境変数
- シークレット値はハードコードしない。全て環境変数経由（`.env.example` 参照）
- 主要変数: `OPENAI_API_KEY`, `JWT_SECRET`, `ADMIN_PASSWORD`, `NEO4J_AUTH`, `MINIO_ACCESS_KEY`

### 2. Pydanticスキーマ
- データモデルの定義は `backend/core/schema.py` に集約
- API固有のリクエスト/レスポンスモデルは `backend/api/main.py` 内に定義
- `core/` モジュールには FastAPI のインポートを入れない（テスタビリティ確保）

### 3. ナレッジグラフ DSL
- 概念間の関係は `CorePredicate` 列挙型で定義:
  `CAUSES, INHIBITS, CORRELATES, DEFINES, MEASURES, TRANSFORMS, REQUIRES, CONTAINS, EQUIVALENT`
- 各エッジは `CausalEdge` スキーマに準拠
- 抽象構造は SMILES DSL で表現: `(varID:OntologyType:value) ==[CorePredicate:verb:polarity]=> (...)`
  ※化学の SMILES ではなく独自形式

### 4. LLM 呼び出しの注意点
- `system` ロールと `temperature`/`max_tokens` を避ける（o1/o3-mini 互換のため）
- シングルトンパターン: `db.py`, `llm.py`, `storage.py` は `@lru_cache` または同等の初期化済みインスタンスを使用

### 5. フロントエンド
- `admin.js` は Vanilla JS (ES5互換) で記述すること（既存コードに合わせる）
- `app.js` は ES6+ (const/let, async/await) を使用している
- フレームワーク不使用（Vanilla JS のみ）

### 6. テスト
- `pytest` を使用
- テストファイルは `backend/tests/` に配置
- 既存の `test_diff_merge.py` が `metaweave.extractor` を参照しているのは既知の問題（モジュールパスは実際は `core.extractor`）

## 優先タスク（Priority A）

以下の3課題を順番に実装する。作業は feature branch で行い、各課題ごとに commit する。

### A1: コース構築チャット履歴の永続化

**問題:** 管理画面のコースビルダーでAIとチャットした履歴がメモリにしか保持されておらず、
ページリロードで消失する。以前のコース設計をもとにした修正ができない。

**実装方針:**
1. PostgreSQL に `course_builder_sessions` テーブルを追加（マイグレーションSQL）
2. `main.py` に以下のAPIを追加:
   - `POST /api/admin/course-builder/sessions` — 新規セッション作成
   - `GET /api/admin/course-builder/sessions` — セッション一覧
   - `GET /api/admin/course-builder/sessions/{session_id}` — 履歴取得
   - `PUT /api/admin/course-builder/sessions/{session_id}` — 履歴更新
3. `POST /api/admin/course-builder/chat` に `session_id` パラメータを追加し、
   チャット後に自動で履歴を永続化する
4. `admin.js` を修正:
   - 起動時にセッション一覧をロードし、UIに選択肢を表示
   - 新規セッション作成ボタンを追加
   - 既存セッションを選択するとチャット履歴と course_draft を復元

### A2: 教員が作成したコースを学生が利用できるようにする

**問題:** `learning_courses` テーブルが `user_id` にバインドされているため、
教員がコースビルダーで作成したコースは教員自身にしか見えない。学生のコース一覧は空。

**実装方針:**
1. `learning_courses` に以下のカラムを追加:
   - `is_template BOOLEAN DEFAULT FALSE` — テンプレートフラグ
   - `is_published BOOLEAN DEFAULT FALSE` — 公開フラグ
   - `cloned_from TEXT` — クローン元コースID
   - `description TEXT DEFAULT ''` — コース説明文
2. コースビルダーの「承認してコースを登録」時に `is_template = TRUE` をセット
3. 教員用API追加:
   - `PUT /api/admin/courses/{course_id}/publish` — コースを公開
4. 学生用API修正:
   - `GET /api/learning/courses` を修正: 自分のコース + 公開テンプレート一覧を返す
   - `POST /api/learning/courses/{course_id}/enroll` — テンプレートをクローンして自分用インスタンスを作成
5. `app.js` を修正:
   - コース選択UIに公開テンプレートを「受講可能なコース」として表示
   - 「受講開始」ボタンでクローンAPIを呼び出す
6. `admin.js` を修正:
   - コースビルダーの承認後に「公開する」ボタンを表示

### A3: 前提知識チェックをコースデータで動作させる

**問題:** `_check_prerequisites` が Neo4j の REQUIRES エッジに依存しているが、
コースビルダーで作られたトピック名と Neo4j の概念名が一致しないため、ほぼ機能していない。

**実装方針:**
1. `_check_prerequisites` を書き換え:
   - Neo4j クエリを削除（フォールバックとしてのみ残す）
   - コースデータ内の `topic.prerequisites` を主たるソースにする
   - 学習者の習得状態は、チャット履歴の有無（`learning_chat_history` の存在）で判定
2. 判定ロジック:
   - 現在のトピックの `prerequisites` を取得
   - 各前提知識について、関連するトピックのチャット履歴が存在するか確認
   - 未習得の前提知識があれば逆質問を返す
   - 学生が「理解している」と答えた場合はスキップ（既存ロジックを維持）
3. コースビルダーで生成される `topics[].prerequisites` に適切な値がセットされるよう、
   `_COURSE_BUILDER_SYSTEM_PROMPT` を修正

## 実装時の注意事項

- マイグレーションSQLは `backend/db/` に `002_a1_a2_a3.sql` として配置する
