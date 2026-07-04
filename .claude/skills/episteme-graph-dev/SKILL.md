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

### FastAPI バックエンド（backend/）

| モジュール | 役割 | 主要ファイル |
|---|---|---|
| `backend/api/main.py` | FastAPI アプリ本体 (lifespan, CORS, ルーター統合) | エントリポイント |
| `backend/api/routes/auth.py` | 認証ルーター | 登録・ログイン・ユーザー情報 |
| `backend/api/routes/learning.py` | 学習ルーター | コース管理・RAGチャット（intent_mode: on_path/explore/casual）・進捗・tension 確定 API・structure_anchor 確定 API（`/api/learning/anchors/...`）・音声会話（`/voice/transcribe`=Whisper STT, `/voice/speak`=TTS） |
| `backend/api/routes/admin.py` | 管理ルーター | 教材アップロード・コースビルダー・ユーザー管理 |
| `backend/api/routes/atlas.py` | 分野の地図（骨格・報告・導線） | 骨格の生成/レビュー/凍結（教員）、修正報告（`POST /api/atlas/report`）、見晴らしの導線の内部計測と初回自動表示フラグ（`/api/learning/atlas/cues/...`、migration 026 `atlas_cue_events`） |
| `backend/api/routes/atlas_view.py` | 分野の地図（閲覧） | `GET /api/atlas`（骨格+`atlas_overlay_cache`+個人層合成）・`GET /api/atlas/node/{id}`。状態判定はサーバ側 `core/atlas_state.py` のみ |
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
| `backend/core/tension/` | TensionMiningAgent (B層) | 会話からの違和感候補検出（prefilter=同期非LLM / agent=非同期LLM / validator・repair / worker）。候補は `interest_traces` kind='tension' status='candidate' に保存し、学習者本人の confirm/dismiss API（`/api/learning/tension/...`）で確定。教員へは k-匿名化集約のみ |
| `backend/core/structure_anchor/` | StructureAnchorAgent (B層) | 学習チャットの問いを「構造のどこに・どう引っかかったか」へ帰属（agent=非同期LLM / validator・repair / worker、tension と同型の独立モジュール）。候補は `interest_traces.payload.structure_anchor` に `attribution_source='llm_candidate'` で保存し（行 status は変更しない）、学習者本人の confirm/dismiss API（`/api/learning/anchors/...`）で確定。明示アンカー（テキスト選択・要素タップ）は同期・非LLMで `learner_selected` 記録。教員へは k-匿名化集約のみ |
| `frontend/public/js/app.js` | 学習 UI | ES6+ SPA |
| `frontend/public/js/admin.js` | 管理 UI | ES5互換 Vanilla JS |

### PDF解析Agentパイプライン（src/episteme_graph/）

ドキュメントアップロード後の処理をコース作成と切り離して実行するagent群。
FastAPIバックエンドとは**独立したPythonパッケージ**として実装する。

| モジュール | 役割 |
|---|---|
| `src/episteme_graph/agents/document_structure/` | DocumentStructureAgent (#216) |
| `src/episteme_graph/agents/paper_skeleton/` | PaperSkeletonAgent (#217) |
| `src/episteme_graph/agents/rhetorical_role/` | RhetoricalRoleAgent (#218) |
| `src/episteme_graph/agents/claim_qualification/` | ClaimQualificationAgent (#219) |
| `src/episteme_graph/agents/equation_semantics/` | EquationSemanticsAgent (#220) |
| `src/episteme_graph/agents/thesis_reconstruction/` | ThesisReconstructionAgent (#221) |
| `src/episteme_graph/agents/dsl_linking/` | DSLLinkingAgent (#222) |
| `src/episteme_graph/agents/component_assembly/` | ComponentAssemblyAgent (#223) |
| `backend/cartridges/<cartridge_id>/` | ドメインカートリッジ定義（全Agent共有） |

**重要**: agentの実装は `src/episteme_graph/` に置き、`backend/` には置かない。
ただしカートリッジファイル（`backend/cartridges/`）は両方から参照される。

## 技術スタックの現状

| レイヤー | 技術 |
|---|---|
| API フレームワーク | FastAPI (ルーター分割済み) |
| ORM | SQLAlchemy 2.x + pgvector |
| RDB + ベクトル | PostgreSQL 16 + pgvector (cosine, 次元数は `LLM_EMBEDDING_DIM` で設定) |
| グラフDB | Neo4j 5 (Cypher) — レガシー、グラフ走査のみ |
| ストレージ | MinIO (S3互換) |
| LLM | **マルチプロバイダ対応** (OpenAI / Gemini)。`LLM_PROVIDER` 環境変数で切替。詳細は下記参照。 |
| 認証 | JWT (HS256) + bcrypt |
| 設定管理 | pydantic-settings (core/config.py) |
| フロントエンド | Vanilla JS SPA + nginx |

### LLM マルチプロバイダ構成

`LLM_PROVIDER` 環境変数で LLM バックエンドを切り替える。

| `LLM_PROVIDER` 値 | バックエンド | 必要な認証 |
|---|---|---|
| `openai` (デフォルト) | OpenAI API | `LLM_API_KEY` または `OPENAI_API_KEY` |
| `gemini` | Google AI Studio (google-generativeai) | `LLM_API_KEY` または `GEMINI_API_KEY` |
| `google` | Vertex AI (google-cloud-aiplatform) | GCP ADC または `GOOGLE_APPLICATION_CREDENTIALS` |
| `gemini-vertex` | Vertex AI + generativeai SDK **(廃止予定)** | GCP ADC |

**重要**: `google` / `gemini-vertex` (Vertex AI) は非推奨。新規実装では `openai` または `gemini` を使用すること。

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

### 3. LLM 抽象化レイヤーの利用 — ベンダ SDK の直接利用禁止

LLM の呼び出しは必ず `core/llm.py` の公開 API を経由する。
`extractor` / `chat` / `batch` 等のサービス層で以下のインポートを直接行ってはならない:

```python
# NG — ベンダ SDK をサービス層で直接インポートしない
import openai
from openai import OpenAI
import google.generativeai
from google.generativeai import GenerativeModel

# OK — 必ず core/llm.py の公開 API を使う
from core.llm import generate_text, generate_embeddings, generate_text_with_structured_output
```

`core/llm.py` は以下を内蔵:
- OpenAI Reasoning モデル (o1, o3, gpt-5.x) 向け自動変換:
  - `system` ロール → `developer` ロールへの変換
  - `temperature` / `max_tokens` の自動除去
- Gemini 向け自動変換:
  - `system` ロールを `system_instruction` として抽出し `GenerativeModel` に渡す
  - `user` / `assistant` ロールは `contents` として渡す

### 3-a. Google LLM を使う際の禁止事項

Google の LLM バックエンドを利用する場合、以下のルールを厳守すること:

| 区分 | 内容 |
|---|---|
| **禁止** | `google-cloud-aiplatform` (`vertexai` SDK) を新規サービスコードで使用すること |
| **禁止** | `LLM_PROVIDER=google` または `LLM_PROVIDER=gemini-vertex` を推奨設定として提案すること |
| **推奨** | `LLM_PROVIDER=gemini` + `google-generativeai` (Google AI Studio 版) を使用すること |

> **理由**: `google-cloud-aiplatform` (Vertex AI) は将来的な廃止を見越して意図的に非推奨としている。
> 新規実装・AIへの提案ではかならず `google-generativeai` (Google AI Studio) を使うこと。

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

### 7. アクセス制御と認可ロジックの実装ルール (新規追加)
資料（Document, LearningCourse 等）を取得・操作するエンドポイントを実装する際は、単に認証済みのユーザーであるかだけでなく、**開示範囲（Visibility）とグループ所属を検証する認可ロジック**を必ず組み込むこと。
- 自分が作成した Private なリソースか？
- 自分が所属している Group に許可されたリソースか？
- Public なリソースか？
これらの判定を行う共通の依存関数（Dependency）を `api/dependencies.py` に実装し、ルーターで再利用すること。

### 8. 多言語対応とTTS実装ルール (新規追加)
教材データからテキストを抽出、またはLLMで音読用スクリプト（`spoken_text`）を生成する際は、言語コード（例: `ja-JP`, `en-US`）を判定しメタデータとして保持する。TTS呼び出し時（`generate_tts_audio` 等）は、この言語メタデータを引数として受け取り、プロバイダの設定を動的に切り替えること（言語指定のハードコード禁止）。


## 実装前のドキュメント更新（条件付き必須）

以下に該当する変更を行う場合、**実装を開始する前に** 対応する CLAUDE.md または SKILL.md を更新すること。
AI アシスタントは各セッション開始時にこれらのファイルを設計書として読むため、実装前に更新することで
設計意図と実装が一致し、手戻りを防ぐ。

### 更新が必須のケース

| 変更の種類 | 更新対象 |
|---|---|
| 新しい Agent / エンドポイント / テーブルの追加 | 該当スキルの SKILL.md（アーキテクチャ表・ファイル構成表） |
| アーキテクチャや設計原則の変更 | CLAUDE.md および該当 SKILL.md |
| 新しい必須ルール・禁止事項の追加 | 該当 SKILL.md の「開発時の必須ルール」セクション |
| 環境変数・設定項目の追加 | `episteme-graph-knowledge/SKILL.md` の環境変数仕様表 |

### 更新が任意のケース

- 既存ロジックの小さなバグ修正
- リファクタリング（外部インターフェース変更なし）
- テストの追加・修正のみ

### 更新手順

1. 実装予定の変更内容を把握する
2. 影響する SKILL.md のセクション（アーキテクチャ表・ルール・パイプライン構成等）を特定する
3. 実装内容を反映した形でドキュメントを更新する
4. 更新後に実装を開始する

## CI テストパターンの更新（必須）

**機能の追加・更新を行った場合、必ず対応するテストパターンも追加・更新すること。**

実装完了後、`episteme-graph-ci-tests` スキルの手順に従い、以下を実行する:

1. 変更対象に対応するテストファイルが `backend/tests/` に存在するか確認
2. 不足しているテストパターンを追加（正常系・エッジケース・異常系）
3. 既存テストが変更後のコードと整合するか確認し、必要に応じて更新
4. `cd backend && python -m pytest tests/ -v` でテストが通ることを確認

テストの配置規則・コード規約・設計原則の詳細は `episteme-graph-ci-tests` スキルを参照。

## 動的参照ガイド

コード生成時は、以下のファイルを直接読み込んで最新の定義を確認すること:

- **ORM モデル (テーブル定義)**: `backend/core/models.py` を読んで確認
- **Pydantic スキーマ**: `backend/core/schema.py` を読んで確認
- **設定項目**: `backend/core/config.py` の `Settings` クラスを読んで確認
- **LLM API**: `backend/core/llm.py` の公開関数を読んで確認
- **既存ルーター**: `backend/api/routes/` 配下のファイルを読んで確認
- **テスト**: `backend/tests/` 配下を読んで既存テストパターンを確認
