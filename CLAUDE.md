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
| RDB + ベクトル | PostgreSQL 16 + pgvector (cosine, 3072次元) |
| グラフDB | Neo4j 5 (Cypher) — グラフ走査専用 |
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
frontend/                      → SPA (HTML/CSS/JS) + nginx リバースプロキシ
backend/api/                   → FastAPI サーバー（認証・学習・Admin エンドポイント）
backend/core/                  → コアエンジン（スキーマ・抽出・埋め込み・検索）
backend/cartridges/            → ドメインカートリッジ定義（particle_physics など）
backend/tests/                 → pytest テスト（FastAPI / core 用）
backend/scripts/               → 初期化スクリプト
src/episteme_graph/agents/     → PDF解析エージェント群（アップロード後処理）
src/tests/                     → agents 用 pytest テスト
```

### バックエンド主要ファイル

| ファイル | 役割 |
|---|---|
| `backend/core/schema.py` | 全 Pydantic モデル定義（OntologyType, CorePredicate, PaperStructure など） |
| `backend/api/main.py` | FastAPI アプリ本体（全エンドポイント・API固有モデル） |
| `backend/core/extractor.py` | PDF→GROBID→LLM 構造抽出パイプライン |
| `backend/core/embedder.py` | pgvector ベクトル保存・検索 (PostgreSQL) |
| `backend/core/chat.py` | RAG チャットロジック |
| `backend/core/postgres.py` | PostgreSQL セッション管理 |
| `backend/core/db.py` | Neo4j ドライバ（グラフ走査専用） |
| `backend/core/llm.py` | OpenAI クライアントファクトリ |
| `backend/core/storage.py` | MinIO S3互換ストレージ |

### データストア構成

- **PostgreSQL（正本）:** ユーザー・認証、教材メタデータ、チャンク本文+embedding (pgvector)、学習者状態、コース管理、対話履歴、コースビルダーセッション
- **Neo4j（グラフ走査専用）:** 概念グラフ (REQUIRES, RELATES_TO, CONTAINS)、チャンク↔概念クロスリンク
- **MinIO:** PDF原本、PaperStructure JSON

### PDF → ナレッジグラフ パイプライン

1. PDF アップロード → MinIO (`raw-papers` バケット)
2. GROBID TEI-XML パース（利用不可の場合は PyMuPDF → 文分割にフォールバック）
3. LLM で仮説駆動型分析 → `PaperStructure` 生成
4. テキストチャンク → PostgreSQL pgvector に埋め込み（3072次元）
5. 概念ノード・エッジ → Neo4j 保存（グラフ走査用）
6. 抽出構造 → MinIO (`extracted-structures` バケット)

### PDF解析エージェント パイプライン（ドキュメントアップロード後処理）

ドキュメントがアップロードされた後、コース作成とは切り離した形で以下のagent群が順番に実行され、最終的に再利用可能な理論コンポーネントを生成する。各agentは独立したモジュールとして `src/episteme_graph/agents/` に実装する。

#### パイプライン概要

```
PDF ファイル
    ↓
[#216] DocumentStructureAgent   — 文書構造復元（structure-first, parser-driven）
    ↓  DocumentStructureResult (JSON)
[#237] EvidenceRegistryBuilder  — PDF 原文由来 evidence の一元管理（非LLM）
    ↓  EvidenceRegistryResult (JSON)
[#217] PaperSkeletonAgent       — 論文backbone仮説化（LLM-first）
    ↓  PaperSkeletonResult (JSON)
[#218] RhetoricalRoleAgent      — chunk/span の論理役割判定（LLM-first）
    ↓  RhetoricalRoleResult (JSON)
[#219] ClaimQualificationAgent  — Claim採否・区分・粒度（LLM-first）
    ↓  ClaimQualificationResult (JSON)
[#237] ClaimObjectBuilder       — 最終 claims.json の決定論的組立（非LLM）
    ↓  ClaimObjectBuildResult (JSON)
[#220] EquationSemanticsAgent   — 数式ブロック意味役割復元（LLM-first）
                                  + to_equations_export() で equations.json 化
    ↓  EquationSemanticsResult (JSON)
[#237] DerivationChainAgent     — 式間導出チェーン構築（非LLM）
    ↓  DerivationChainResult (JSON)
[#237] FigureTableSemanticsAgent — 図表の意味復元（caption-first, LLM enricher 任意）
    ↓  FigureTableSemanticsResult (JSON)
[#221] ThesisReconstructionAgent — 中心命題・支持構造の再構成（LLM-first）
    ↓  ThesisReconstructionResult (JSON)
[#222] DSLLinkingAgent          — Claim/Equation/Thesis → DSL グラフ接続（LLM-first）
    ↓  DSLLinkingResult (JSON)
[#223] ComponentAssemblyAgent   — 再利用可能コンポーネント生成（LLM-first + cartridge-aware）
    ↓  ComponentAssemblyResult (JSON)
[#237] CourseMappingAgent       — Component → Course topic 接続（決定論的マッピング）
    ↓  CourseMappingResult (course_info.json)
```

#### 各Agentの実装場所

```
src/episteme_graph/agents/
  document_structure/      → DocumentStructureAgent (#216)
  evidence_registry/       → EvidenceRegistryBuilder (#237)
  paper_skeleton/          → PaperSkeletonAgent (#217)
  rhetorical_role/         → RhetoricalRoleAgent (#218)
  claim_qualification/     → ClaimQualificationAgent (#219)
  claim_object_builder/    → ClaimObjectBuilder (#237)
  equation_semantics/      → EquationSemanticsAgent (#220)
  derivation_chain/        → DerivationChainAgent (#237)
  figure_table_semantics/  → FigureTableSemanticsAgent (#237)
  course_mapping/          → CourseMappingAgent (#237)
  thesis_reconstruction/ → ThesisReconstructionAgent (#221)
  dsl_linking/          → DSLLinkingAgent (#222)
  component_assembly/   → ComponentAssemblyAgent (#223)
```

各Agentディレクトリは最低限以下のファイルを持つ:
```
__init__.py
agent.py           → Agent本体クラス
cartridge_loader.py → CartridgeLoader（各agentで実装、共通インターフェース）
input_builder.py   → LLM入力の構築
prompt.py          → プロンプト定義
llm_client.py      → LLM API呼び出し（structured output）
schema.py          → dataclass/Pydanticモデル定義
validator.py       → 出力スキーマ検証
repair.py          → validation失敗時の再試行ロジック
examples/          → サンプル入出力JSON
```

#### 設計原則

**DocumentStructureAgent (#216)**: structure-first（パーサ・レイアウト優先）。意味解釈は行わない。曖昧なblockのみLLM補助。

**PaperSkeletonAgent (#217) 以降**: LLM-first。生成・採否・関係付けの高次判断はLLMに委ねる。入力整形・validation・repairは非LLMで処理する。

**全Agent共通ルール**:
1. **cartridge-aware**: active cartridgeが指定されていれば語彙・検証ルールに使う。cartridgeがなくても単独動作すること
2. **structured output**: LLM出力は必ずJSONスキーマ検証し、失敗時はrepair/retryを実行
3. **evidence-based**: 各出力フィールドに `reason` と `confidence` (0.0〜1.0) を付与
4. **情報を落とさない**: 不明は `unknown` / `deferred` で保持し、削除しない
5. **maturity・review情報の最終確定禁止**: LLMが提案しても provisional に留める

#### カートリッジシステム

ドメイン固有の語彙・ルール・検証定義を持つJSONファイル群。`backend/cartridges/<cartridge_id>/` に配置する。

```
backend/cartridges/particle_physics/
  ontology.json         → concept types / aliases / notation_patterns / normalization_hints
  validation_rules.json → block typing / claim field / component field の妥当性チェック
  component_types.json  → 許可されるcomponent type語彙
  relation_types.json   → dependency / connector 語彙
  support_statuses.json → サポートステータス定義
  maturity_levels.json  → 成熟度レベル定義
```

`CartridgeContext` は各agentの `CartridgeLoader` がロードし、prompt builder / validator に渡す:
```python
@dataclass
class CartridgeContext:
    cartridge_id: str
    ontology: dict
    validation_rules: dict
    aliases: dict | None = None
    notation_patterns: dict | None = None
    normalization_rules: dict | None = None
    extraction_hints: dict | None = None
```

### RAG チャットフロー

1. ユーザー質問 → PostgreSQL pgvector 検索（コース/論文でフィルタ）
2. 上位チャンク + MinIO から `PaperStructure` 取得
3. チャット履歴 + コンテキストで LLM プロンプト構築
4. レスポンスから誤解検出（「訂正」「誤り」「間違い」パターン）
5. ドリルダウンリンク提示（`[〇〇について詳しく聞く]`）

### 認可モデル (RBAC)

- **STUDENT**: 学習UIアクセス、チャット
- **TEACHER**: 教材アップロード、学生アカウント作成
- **SYSTEM_ADMIN**: 全権限（教師アカウント作成を含む）

### 資料の開示範囲 (Visibility)
教材 (Document) や コース (LearningCourse) は、以下のいずれかの開示範囲を持つ。
- **Public**: システム全体（全ユーザー）に公開
- **Group**: 指定されたグループ（Group）の参加メンバーのみアクセス可能
- **Private**: 作成者（自分）のみアクセス可能
※ グループへの参加は、管理者による直接招待、または招待コードにより行われる。


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
- PostgreSQL セッションは `core/postgres.py` の `get_session()` を使い、必ず `try/finally` で `session.close()` する

### 5. フロントエンド
- `admin.js` は Vanilla JS (ES5互換) で記述すること（既存コードに合わせる）
- `app.js` は ES6+ (const/let, async/await) を使用している
- フレームワーク不使用（Vanilla JS のみ）

### 6. テスト
- `pytest` を使用
- FastAPI / core 用テストは `backend/tests/` に配置
- agents 用テストは `src/tests/agents/<agent_name>/` に配置
- 既存の `test_diff_merge.py` が `metaweave.extractor` を参照しているのは既知の問題（モジュールパスは実際は `core.extractor`）

### 7. PDF解析Agentの実装ルール
- 実装場所は `src/episteme_graph/agents/<agent_name>/` とする（`backend/` には置かない）
- 各Agentは `agent.py` の `run()` メソッドを公開インターフェースとする
- LLM呼び出しは `llm_client.py` に分離し、`agent.py` から直接LLM SDKを呼ばない
- `CartridgeLoader` は各agentディレクトリに実装する（共通インターフェースを維持）
- agentの出力は `schema.py` の dataclass に型付けし、必ずJSONシリアライズ可能にする
- cartridgeがない場合でもagentが単独動作できるよう、すべてのcartridge参照は `Optional` とする
- domain-specific なロジックをagent内にハードコードしない（cartridgeから読む）

## 優先タスク（Priority A）— 実装完了 (2026-03-26)

以下の3課題は `feature/a1-a2-a3-priority-fixes` ブランチで実装済み。

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
