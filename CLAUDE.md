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
| `backend/api/main.py` | FastAPI アプリ本体（lifespan・全ルーターのフラット登録。admin 系子ルーター13本は `prefix="/api/admin"` で main.py から直接登録する — admin.router に子ルーターを include しない（Tier 3-17c）） |
| `backend/api/routes/lecture_studio/` | 原稿スタジオルーター（Tier 3-17a で `_shared` / `scripts` / `pipeline` / `topics` に分割したパッケージ。`__init__.py` が router と互換シンボルを再エクスポートするため import 面は旧単一ファイルと同じ） |
| `backend/core/extractor.py` | GROBID 変換（PDF→TEI XML）。orchestrator の下請け。旧 diff/merge は本番未使用のため削除済み（2026-07） |
| `backend/core/embedder.py` | pgvector ベクトル保存・検索 (PostgreSQL) |
| `backend/core/chat.py` | tier 付き chunk 検索ユーティリティ（実 RAG チャットは `routes/learning.py`） |
| `backend/core/postgres.py` | PostgreSQL セッション管理 |
| `backend/core/llm.py` | OpenAI クライアントファクトリ |
| `backend/core/storage.py` | MinIO S3互換ストレージ |
| `backend/core/llm_worker/` | 非同期 LLM worker 共通基盤（client / run_with_repair / CostGate。フル骨格は6系統が利用、CostGate 等の部分利用が別途あり） |
| `backend/core/privacy.py` | k-匿名ゲートの正本（K_ANONYMITY=3・件数レンジ導出） |
| `backend/core/notification_recipients.py` | 通知宛先解決の共通 JOIN プリミティブ（status 系 / V層が利用） |
| `backend/core/course_data.py` | `learning_courses.data` JSONB の正本スキーマ（CourseData 系 Pydantic モデル＝全て `extra="allow"` + アクセサ群）。course_data への素の dict アクセスを新規に書かない（Tier 3-18） |
| `backend/core/revision_store.py` | draft/freeze/楽観ロックの共通プリミティブ（`RevisionConflictError` / `update_with_revision_lock` / `idempotent_seed_import`。atlas_store と library/store が委譲。Tier 3-20） |
| `backend/core/status/projector.py` | 教材・コース状態導出の正本（MaterialStatus / CourseStatus、バッチ導出 `project_material_statuses_bulk` 付き）。`/api/admin/materials` は projector の導出 + legacy 語彙マッピングを使う — status を独自 JOIN で再合成しない（Tier 3-16） |
| `backend/core/migrations.py` | マイグレーションランナー。`backend/db/*.sql`（init.sql + 番号順ファイル群）が正本で、毎起動・番号順に全ファイルを冪等再実行する（pg_advisory_lock で多重起動排他） |

### データストア構成

- **PostgreSQL（正本）:** ユーザー・認証、教材メタデータ、チャンク本文+embedding (pgvector)、概念構造
  （`theory_components` / `theory_claims` / `theory_component_graphs`）、学習者状態、コース管理、対話履歴、
  コースビルダーセッション、承認・共有（`component_explanations` / `component_endorsements` /
  `component_citations` — 承認・共有レイヤー(C層)）
- **MinIO:** PDF原本 (`raw-papers`)、図画像 (`figure-images`)

### PDF → ナレッジグラフ パイプライン

1. PDF アップロード → MinIO (`raw-papers` バケット)
2. GROBID TEI-XML パース（利用不可の場合は PyMuPDF → 文分割にフォールバック）
3. テキストチャンク → PostgreSQL pgvector に埋め込み（3072次元）
4. PDF解析Agentパイプライン（下記参照）が LLM で構造抽出・意味解析を実行
5. 概念構造・claim → PostgreSQL (`theory_components` / `theory_claims` / `theory_component_graphs`) に保存

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
[#219] ClaimQualificationAgent  — Claim採否・区分・粒度 + atomic rewrite（LLM-first, #317）
    ↓  ClaimQualificationResult (JSON; atomic_claims を含む)
[#237] ClaimObjectBuilder       — 最終 claims.json の決定論的組立（非LLM, #317）
    ↓  ClaimObjectBuildResult (JSON)
[#220] EquationSemanticsAgent   — 数式ブロック意味役割復元（LLM-first）
                                  + to_equations_export() で equations.json 化
    ↓  EquationSemanticsResult (JSON)
[#355] SymbolRegistryBuilder    — 数式記号の定義・表記ゆれ・スコープの一元管理（非LLM）
    ↓  SymbolRegistryResult (JSON)
[#237] DerivationChainAgent     — 式間導出チェーン構築（非LLM）
    ↓  DerivationChainResult (JSON)
[#237] FigureTableSemanticsAgent — 図表の意味復元（caption-first, LLM enricher 任意）
    ↓  FigureTableSemanticsResult (JSON)
[L層]  ApparatusSemanticsAgent    — 図画像の装置・パーツ候補抽出（vision LLM、`analyze_images` オプトイン時のみ）
    ↓  ApparatusSemanticsResult (JSON; 全出力 review_required 系)
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
  symbol_registry/         → SymbolRegistryBuilder (#355)
  derivation_chain/        → DerivationChainAgent (#237)
  figure_table_semantics/  → FigureTableSemanticsAgent (#237)
  apparatus_semantics/     → ApparatusSemanticsAgent (L層) — 図画像から装置・パーツ候補（vision LLM）
  course_mapping/          → CourseMappingAgent (#237)
  thesis_reconstruction/ → ThesisReconstructionAgent (#221)
  dsl_linking/          → DSLLinkingAgent (#222)
  component_assembly/   → ComponentAssemblyAgent (#223)
  component_graph/      → ComponentGraphAgent (#266) — TheoryOperationGraph 構築
  narrative_annotator/  → NarrativeAnnotator (#360) — main graph への narrative 注釈（LLM-first, graph 構造非変更）
```

各Agentディレクトリは最低限以下のファイルを持つ:
```
__init__.py
agent.py           → Agent本体クラス
cartridge_loader.py → CartridgeLoader（正本は agents/cartridge_loader.py。各agent側は薄い再エクスポート。固有差分があるagentのみサブクラス化）
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

**Claim atomic rewrite の責務分担 (#317)**: 長い / paper-level / 複数命題の claim を
1 claim = 1 minimal proposition に再構成する atomic rewrite は **ClaimQualificationAgent**
の正式ステップ（LLM-first）が担当し、結果を `QualifiedSpanRecord.atomic_claims`
（`text` / `normalized_text` / `claim_type_candidate` / `atomicity` / `status` /
`source_span_id` / `evidence_quote` / `qualification_reason` / `confidence`）に出力する。
atomic 化できない箇所は `atomicity="non_atomic"` / `status="review_required"` で保持する
（情報を落とさない）。**ClaimObjectBuilder** は atomic rewrite を行わず、その候補を
ClaimObjectRecord に変換・リンク・検証するだけに責務を限定する。LLM 候補が無い場合の
deterministic split は `atomicity="split_pending"` / `support_status="review_required"`
の review suggestion に留め、確定 `source_backed` atomic claim にはしない。非 atomic /
split_pending claim（`is_atomic=False`）は ComponentGraph / TheoryOperationGraph の
強い backing に使わない。ExportValidationGate は非 atomic / split_pending を明示 report する。

**全Agent共通ルール**:
1. **cartridge-aware**: active cartridgeが指定されていれば語彙・検証ルールに使う。cartridgeがなくても単独動作すること
2. **structured output**: LLM出力は必ずJSONスキーマ検証し、失敗時はrepair/retryを実行
3. **evidence-based**: 各出力フィールドに `reason` と `confidence` (0.0〜1.0) を付与
4. **情報を落とさない**: 不明は `unknown` / `deferred` で保持し、削除しない
5. **maturity・review情報の最終確定禁止**: LLMが提案しても provisional に留める

#### TheoryOperationGraph (ComponentGraphAgent #266 / #302)

`component_graph/normalizer.py` が DerivationChain から **理論操作グラフ (TheoryOperationGraph)** を
決定論的に構築する。**特定分野・特定論文の用語をハードコードしない**（domain-independent）。

- **node / edge の語彙は `operation` から導出する**: `schema.classify_operation()` が
  `define_* → defines` / `linearize_* → linearizes` / `solve_* → solves` /
  `eliminate_* → eliminates` / `derive_* → derives` / `constrain_* → constrains` /
  `diagnose_* → diagnoses` / `compare_* → compares` のように prefix で edge_type を決める。
  `transform` / `relate` / `connect` / `support` / `associate` など抽象的な operation は
  generic 扱いとし warning を出す。generic step でも input/output 両方の式 backing があれば
  equation_detail 層に `partially_source_backed` + `review_reasons=["generic_operation"]` で残し、
  式 backing が無い generic のみ debug 層で `inferred` にする (#361)。
- **source-backing を必ず明示する**: 各 node は
  `linked_equation_ids` / `linked_derivation_ids` / `linked_claim_ids` / `linked_evidence_ids` と
  `source_backing_status`（`source_backed` / `partially_source_backed` / `inferred` / `review_required`）を持つ。
  各 edge は `evidence_equation_ids` / `evidence_derivation_ids` / `evidence_claim_ids` /
  `source_evidence_ids` を持ち、node と同じ語彙の `source_backing_status`
  （`source_backed` / `partially_source_backed` / `inferred` / `review_required`）と、
  そこから `review_status_for_backing()` で導出される `review_status`
  （`source_backed` / `review_required`）を持つ (#311)。
- **review の理由を必ず付与する**: `review_required` の node / edge は `review_reasons` を空にしない
  （`missing_atomic_claim` / `missing_evidence_link` / `missing_equation_link` /
  `missing_derivation_link` / `equation_needs_math_review` / `edge_not_source_backed` /
  `fallback_or_inferred_node` / `source_span_missing` から選ぶ）。
- **fallback / inferred node を main graph で確定扱いしない**: `deterministic_fallback` や
  `inferred` の node は `graph_layer = "debug"` に分離し、`source_backed` にしてはならない（hard error）。
- **graph を 2 層に分離する (#306 / #308)**: main graph は上位理論構成を表す少数の集約 node
  (`graph_layer = "main"`, `component_type = "TheoryOperationNode"`)、式単位の step は
  (`graph_layer = "equation_detail"`, `component_type = "EquationOperationNode"`) に保持する。
  各 detail node は `parent_component_id`、各 main node は `member_component_ids` で相互参照する。
  generic operation は main node にしない（式 backing があれば detail に `partially_source_backed`、
  無ければ debug に `inferred`。#361）。
- **main graph は theory stage 単位で集約する (#308)**: 式 step は `(derivation_id, operation family)`
  ではなく **theory stage** で集約する。stage は `schema.stage_for_edge_type()` が operation の
  edge_type family から domain-neutral に導出する（`defines → theory_basis` /
  `constructs・normalizes → observable_construction` / `linearizes・approximates・substitutes →
  equation_system` / `solves・eliminates → elimination` / `derives・constrains →
  consistency_relation` / `diagnoses・compares → diagnostic_application`）。stage は全 derivation
  を跨いで集約されるため、main graph は理論構成 5–8 個程度のバックボーンになる。stage の候補語彙は
  `schema.THEORY_STAGES` / 表示名は `schema.THEORY_STAGE_LABELS`。特定分野・特定論文の用語は使わない。
- **main label は短い theory stage label にする (#308)**: main node の label は stage label
  （`Theory basis` / `Observation model` / `Observable construction` / `Equation system` /
  `Elimination` / `Consistency relation` / `Diagnostic / application`）**そのもの**を使い、短く保つ。
  atomic claim text や step reason のような長い説明は label に詰め込まず、node の `description`
  フィールドに入れて UI 詳細ペインで表示する（`Stage: 長い説明文` のような label は作らない）。
  **equation ID fallback は使わない**ので `Define eq_2_7` / `Derive result eq_...` のような label は
  main に出ない。validator は main node の equation-id label (`main_graph_node_equation_id_label`) と
  generic operation (`main_graph_generic_operation`) を hard error として検出する。
- **review_status は source_backing_status から導出する (#306)**: `schema.review_status_for_backing()`
  が `source_backed → source_backed` / `partially_source_backed → teacher_review_required` /
  `inferred・review_required → review_required` にマップする。全 node を一律
  `teacher_review_required` にしない。
- **atomic claim を優先する (#306)**: node の主たる backing は atomic claim（短く、evidence_text が
  非空、paper-level でない）を優先する。atomic claim が無ければ `review_reasons=["missing_atomic_claim"]`
  を付け、equation ID だけの label は `partially_source_backed` に留める。空の evidence_text を
  強い source backing として扱わない。evidence link により `source_backed` になった node でも
  atomic claim が無ければ `missing_atomic_claim` を warning として残す（`review_status` は
  `source_backed` のまま）。
- **UI (`admin.js`)** は `source_backing_status` で表示を区別する
  （source_backed=通常 / partially=細線 / review_required=点線枠 / inferred・fallback=薄色+⚠）。
  グラフ層トグル（主グラフ / 式の詳細 / すべて）で `graph_layer` を切り替え、既定は main を優先表示する。

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

`CartridgeContext` の正本は `src/episteme_graph/agents/cartridge_context.py`、`CartridgeLoader` の正本は
`src/episteme_graph/agents/cartridge_loader.py`（2026-07 整理で12コピーを統合）。各agentの
`cartridge_loader.py` / `schema.py` は正本からの再エクスポートで、prompt builder / validator に渡す
（component_assembly / component_graph はフィールド構成が異なる固有 `CartridgeContext` を維持）:
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

1. ユーザー質問 → PostgreSQL pgvector 検索（`services.search_chunks_with_metadata`、tier(L1信頼性) 付与）
2. 検索結果の `material_id` から `content_grounding`（course_material / other_material / model_generated）を判定
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

### パイプライン成果のグループ共有（migration 035 → 044 でテーブル統合）

コースを作らずに **PDF 解析パイプラインの成果**（`theory_components` / `theory_claims` /
`theory_component_graphs` / `document_analysis_runs`）を指定グループへ共有する層。成果は
すべて `document_id` 由来なので、**権限はドキュメント単位に集約**し、成果はそれを継承する
（成果テーブルに列を足さない）。当初は `course_group_permissions`（migration 010）の完全な
移植として `document_group_permissions`（migration 035）が独立テーブルで実装されたが、
アーキテクチャ整理 Tier 3-14（migration 044）で `course_group_permissions` と統合され、
`object_group_permissions`（`object_type ∈ {course, document}` のポリモーフィック1枚）に
一本化された。以下の記述はテーブル名以外は現行のまま有効。

- **`object_group_permissions`**（migration 044、`object_type='document'` 行が本層に対応）:
  `PRIMARY KEY(object_type, object_id, group_id)`、`object_id` は `document_id` の正規化済み
  テキスト表現。`permission ∈ {viewer, editor}`。viewer=解析成果の閲覧・引用、editor=再解析・
  説明追加等の編集。`object_id` には FK を張らない（ポリモーフィックのため）ので、document
  削除経路（`_purge_document` / `delete_material` 等）が明示 `DELETE` で孤児行を防ぐ
  （`groups(id)` への `group_id` のみ `ON DELETE CASCADE`）。旧 `document_group_permissions`
  （035）は 044 適用時にデータ移行のうえ `DROP TABLE` 済み（`backend/db/035_*.sql` はコメントのみの
  スタブに書き換え済み）。
- **アクセス判定（`services.py`）**: `user_can_view_document` / `user_can_edit_document` /
  `user_owns_document`（共有変更は所有者のみ）。`_resolve_document(ref)` は `documents.id`(UUID) と
  `source_path`(material_id) の両方を解決する。view = 所有者 / public / group単一共有 /
  object_group_permissions(document, viewer|editor)。既存のコース経由（course 側の
  object_group_permissions 行）は `theory_components._ensure_document_viewable/editable` が
  フォールバックで併用する。
- **成果読み取りゲート**: `/api/admin/documents/{document_id}/...`（structure / component-graph /
  sections/{id}/components / chunks/{id}/claims）は `_ensure_document_viewable/editable` を通すため、
  ドキュメント共有だけで全成果が閲覧可能になる（コース不要）。
- **API**（`backend/api/routes/admin.py`、実パス `/api/admin/...`）:
  `GET /documents/{id}/groups`（閲覧可能な者は参照、変更は所有者のみ）、
  `POST /documents/{id}/groups`（共有付与/更新）、`DELETE /documents/{id}/groups/{group_id}`（解除）。
  `list_materials` / `get_material` は object_group_permissions 経由の共有も一覧・取得対象に含める。
  API パス・レスポンス形式は 044 統合後も不変。
- **監査**: 付与・解除を `theory_review_events`（`entity_type='document_share'`）に記録。
- **UI**（`admin.js`）: 教材管理タブの各行「共有」ボタン → `openDocumentShareModal`（コース共有
  モーダルと同型。グループ選択＋viewer/editor）。
- 想定利用は**教員間コラボ**（査読・再利用）。学習者への読み取り開示は現状スコープ外（将来別途）。

### 承認・共有レイヤー（C層, migration 021）

A層（`src/episteme_graph/agents/` の生成パイプライン、export_validation_gate 等）には手を入れず、
その上に「教員による査読承認」と「教員間の共有」を積む層。C層は A層が出力した
`theory_components` / `theory_claims` を**読む側**として実装し、承認・共有情報を新規テーブルに積む
（B層＝学習者体験レイヤーと同じ立場）。**A層のコードは変更しない。**

- **`component_explanations`**: 1コンポーネントに複数の説明バージョンを並存させる。
  `kind='standard'` は A層/AI 由来の標準説明（`theory_components.summary` から**遅延生成**。
  A層は explanation 行を作らないため、承認対象を成立させるために C層が
  `_ensure_standard_explanation()` で補う）、`kind='personal'` は教員の独自解釈。
- **`component_endorsements`**: 個々の教員の承認を1行ずつ記録。**承認は説明バージョン（explanation）単位**
  （どの説明を承認したかを区別できる）。`UNIQUE(explanation_id, endorser_id)` で二重カウントを防ぎ、
  取り消しは行削除ではなく `revoked=TRUE`（履歴を残す）。`level`（provisional/endorsed/strong）と
  `expertise_tag` を集計ビュー `component_explanation_endorsement_summary` で合成する。
- **`component_citations`**: 他教員が承認済み説明を再利用・引用したことを帰属付きで記録。

**設計原則**:
1. **承認の重みは学習者への評価点にしない** — 表示は段階ラベル（例「専門家3名が承認」）で、数値スコアを学習者に出さない。
2. **claim 紐づけの最終確定は必ず教員** — 質問→候補生成（`core/component_candidates.py`）は AI が候補提示に限定し、
   `backing_claims` は `confirmed=False` の候補扱い。教員が確定するまで確定しない。
3. **状態変更は監査** — 承認・引用・共有切替・説明の review_status 遷移は `theory_review_events` に記録
   （`entity_type` を `'endorsement'` / `'explanation'` / `'citation'` に拡張）。既存の `_record_review_event` を再利用。

**API**（`backend/api/routes/theory_components.py`、実パスは `/api/admin/...`）:
`POST /theory-components/candidates/from-query`（候補生成）、
`GET|POST /theory-components/{id}/explanations`、`PATCH /explanations/{id}`（編集・shared切替・review_status）、
`POST|DELETE /explanations/{id}/endorse`、`GET /explanations/{id}/endorsements`、
`POST /explanations/{id}/cite`、`GET /courses/{course_id}/sharing-dashboard`。
学習者向けは `GET /api/learning/courses/{course_id}/components/{component_id}/explanations`（承認済みのみ）。

### TensionMiningAgent（B層, migration 022）

学習者との対話ログから「未理解（gap）」ではなく「理解した上での引っかかり（tension）」の
**候補**を抽出し、学習者本人の確定を経て `interest_traces` に記録する（新テーブルなし・
`kind='tension'` と status 語彙の拡張のみ）。実装は `backend/core/tension/`
（prefilter / agent / input_builder / prompt / llm_client / schema / validator / repair / worker / examples）。

**設計原則（不変条項）**: P1 違和感を生成するのは人間（LLM 出力は常に `status='candidate'`、
本人 confirm なしに確定しない）/ P2 断定しない（`paraphrase` は推量形を validator でハード強制）/
P3 監視にしない（本人のみ可視、教員へは k-匿名化集約のみ、評価利用禁止）/
P4 情報を落とさない（dismiss は `status='dismissed'` で保持、分類不能は `unclassified`）/
P5 evidence-based（逐語 `evidence_quote`・`reason`・`confidence` 必須）/
P6 チャット応答を遅延させない（同期パスは非 LLM prefilter のみ、LLM は非同期バッチ）/
P7 演技化させない（バッジ・ランキング化しない）。

**ステージ構成**: Stage 0 `prefilter.judge_tension_hint()`（同期・非LLM、`payload.tension_hint` 付与）
→ Stage 1 `TensionMiningAgent.run()`（非同期バッチ・LLM 1コール/窓、validator/repair 付き、
2回修復失敗は `unclassified`/`confidence=0.0`/`payload.repair_failed=true` で1行保持）
→ Stage 2 学習者ダイジェスト & 確定 API（confirm → `open`/`articulated`、dismiss → `dismissed`、
`theory_review_events` に `entity_type='tension'` で監査記録）
→ Stage 3 `aggregate_interest_dashboard` の tension 集計
（対象は本人が引き受けた status のみ、n<3 セル非表示の k-匿名化）。

**Worker**（`backend/core/tension/worker.py`）: `threading.Thread` 方式。トリガーは
未解析 `tension_hint` 累積5件 or セッション終了（20分無活動）。冪等性は raw 痕跡の
`analyzed_at` で管理。コスト上限は `TENSION_MAX_CALLS_PER_SESSION`（既定3）/
`TENSION_MAX_CALLS_PER_DAY`（既定10）、モデルは fast tier 既定（`TENSION_LLM_MODEL` で上書き）。

**API**（`backend/api/routes/learning.py`）:
`GET /api/learning/courses/{course_id}/tension/digest`（本人の candidate・confidence≥0.55・最大3件、
confidence 数値は返さない）、`POST /api/learning/tension/{trace_id}/confirm`（body: `learner_text?`）、
`POST /api/learning/tension/{trace_id}/dismiss`、`POST /api/learning/tension/{trace_id}/connect`。
すべて本人のみ（教員・管理者は個別行にアクセス不可）。

### 構造帰属型の問い記録（Structure-Anchored Questions, B層, migration 025）

学習チャットの問いを質問文の保存だけでなく **「提示された情報構造のどこに（anchor）、
どう引っかかったか（doubt_type）」** として記録する。新テーブルなし・
`interest_traces.payload.structure_anchor` の拡張のみ（質問原文 `text` は残す, P4）。
実装は `backend/core/structure_anchor/`
（agent / input_builder / prompt / llm_client / schema / validator / repair / worker / examples、
tension と同型の独立モジュール。コスト上限も tension とは独立）。

**payload 形式**（`payload.structure_anchor`）:
`anchor_type`（`claim | equation | derivation_step | concept | stage | chunk | segment`）/
`anchor_id` / `anchor_label` / `doubt_type`（`definition | justification_gap | premise |
prior_conflict | scope | connection | unclassified`）/
`attribution_source`（`learner_selected | llm_candidate | confirmed`）/
`evidence_quote` / `reason` / `confidence` / `status`（`active | dismissed`）。
帰属語彙は domain-independent（theory stage は `schema.THEORY_STAGES` を使う）。
構造を持たない教材では `claim → concept → chunk → segment` の順で粗い粒度へ縮退させる。

**帰属の3経路**:
- **A 明示アンカー（同期・非LLM）**: 教材区画のテキスト選択→「ここについて質問」
  （`LearningChatRequest.selection_text` / `selection_segment_id`）、または
  数式・claim 要素タップ（既存 `chunk_id`/`element_id`/`element_type`/`element_label`）。
  `attribution_source='learner_selected'` で即確定（doubt_type は `unclassified` のまま可）。
- **B 非同期LLM帰属**: worker が未帰属の `kind='question'` 痕跡をバッチ処理し
  `attribution_source='llm_candidate'` の候補を書く。**行の `status` は変更しない**
  （tension と違い問い自体は確定済み。候補なのは帰属だけなので、確定状態は
  `attribution_source` のみで管理する）。本人の confirm/訂正で `confirmed` になる（P1）。
- **C 回答末尾の確認プロンプト**: `tension_hint` が立ったとき等にゲートして
  doubt_type の1タップ選択肢を回答に添付（`anchor_confirm`）。毎回は出さない（P7）。

**Worker**（`backend/core/structure_anchor/worker.py`）: `threading.Thread` 方式・
冪等性は `payload.anchor_analyzed_at`。コスト上限 `ANCHOR_MAX_CALLS_PER_SESSION`（既定3）/
`ANCHOR_MAX_CALLS_PER_DAY`（既定10）、モデルは fast tier 既定（`ANCHOR_LLM_MODEL` で上書き）。

**API**（`backend/api/routes/learning.py`）:
`GET /api/learning/courses/{course_id}/anchors/digest`（本人の `llm_candidate`・最大3件、
confidence 数値は返さない）、`POST /api/learning/anchors/{trace_id}/confirm`
（body: `doubt_type?` / `anchor_type?` / `anchor_id?` — 訂正可）、
`POST /api/learning/anchors/{trace_id}/dismiss`（`structure_anchor.status='dismissed'` で保持, P4）。
確定・却下は `theory_review_events` に `entity_type='structure_anchor'` で監査記録。
教員向けは `GET /api/admin/courses/{course_id}/anchor-insights`
（stage / doubt_type 単位の k-匿名化集約、k=3・n<3 セル非表示。対象は
`learner_selected` / `confirmed` のみ。評価利用禁止, P3）。

**設計原則**: tension の不変条項（P1/P4/P5/P6/P7）を継承。同期パスは非LLM（A）のみ、
LLM（B）は非同期バッチ。LLM 候補は本人確定まで確定扱いしない。

### 分野の地図（Field Atlas, Stage 2, issue A〜F 実装済み）

学習中の箇所が分野全体のどこに該当するかを示す全画面オーバーレイ + 常設ミニマップ。
仕様の正本は `field_atlas_overlay_spec.md`（3層モデル: S=骨格カートリッジ同梱 /
C=`atlas_overlay_cache` / P=個人層 `interest_traces`）。設計原則: 宣言しない・煽らない・
出所の正直さ・**踏破率を数値にしない**・リアルタイム LLM 生成をしない。

- **バックエンド**: `core/atlas*.py`（骨格・状態導出・配置・報告）、
  `routes/atlas.py`（骨格生成/レビュー/凍結・修正報告・**導線計測**）、
  `routes/atlas_view.py`（`GET /api/atlas` 閲覧 API。状態判定はサーバ側のみ）
- **フロント**: `atlas-overlay.js`（オーバーレイ B/C）/ `atlas-panel.js`（詳細パネル）/
  `atlas-report.js`（修正報告 D）/ `atlas-data.js`（fixture ⇄ API 切替。404=骨格なし→null）/
  `atlas-minimap.js`（F-1）/ `atlas-cues.js`（F-2）
- **骨格は DB 管理（migration 027, `atlas_skeletons`）**: draft/凍結版は
  `core/atlas_store.py` 経由で DB が正本（同梱 `atlas/skeleton.yaml` は起動時に一度だけ
  取込むシード兼フォールバック）。draft の同時編集は**楽観ロック**（`revision` 照合、
  衝突は 409）。凍結版は (domain_key, version) で履歴保持。**カートリッジファイルの無い
  新分野**も generate API の `body.domain`（name/description 等）で骨格を生成でき、
  ファイルデプロイ不要（例: `modified_gravity`）。骨格の読みは必ず
  `atlas_store.load_learner_skeleton()` を使う（`cartridge.learner_atlas_skeleton` 直読み禁止）。
- **コース⇄地図バインディング（S2）**: `POST /api/admin/courses/{id}/atlas-binding/propose`
  が全ドメイン骨格への topic 対応カバレッジを**決定論的に**提案し（LLM 不使用）、教員承認で
  `PUT .../atlas-binding` が `learning_courses.data.cartridge_id` + `topics[].atlas_node_id`
  を保存（監査: `theory_review_events` `entity_type='atlas_binding'`）。コースビルダーの
  登録直後と管理画面「学習マップ編集」から操作。明示 cartridge_id は下記ゲートを免除。
- **コース⇄カートリッジの妥当性ゲート（gap3 hardening）**: カートリッジが**導出**
  （`course_data.cartridge_id` 明示なし）で決まった場合、コースのどのトピックも骨格概念に
  対応しなければ `GET /api/atlas` は 404（骨格なし扱い→地図領域ごと非表示）。解析パイプ
  ラインは既定カートリッジで走るため、`document_analysis_runs` 由来の導出だけでは別分野
  コースに無関係な地図が出る（`atlas_state.course_has_skeleton_anchor`）。
- **フロントの fail-closed**: `atlas-data.js` は API 失敗時にフィクスチャへ退避しない
  （`null`＝非表示）。フィクスチャは `ATLAS_DATA_SOURCE=fixture` の明示時のみ。
  `/api/atlas` は `frontend/nginx.conf` の明示 proxy が必須（欠落すると SPA フォール
  バックが index.html を 200 で返し、JSON パース失敗経由で事故る）。

**F: 常設ミニマップと見晴らしの導線（issue F, migration 026）**
- ミニマップ（左パネル下・切手大）は「いまここ + 状態ドット + 霧ハッチ」**のみ**。
  数値・ラベル・凡例を描かない。L1 をそのまま縮小（縮約アルゴリズムの決定）。
  更新はトピック遷移とオーバーレイ閉時のみ（ポーリング禁止）。骨格なしカートリッジ
  （`/api/atlas` 404）ではミニマップ・導線とも領域ごと非表示。
- 導線4箇所: ①トピック完了直後 ②章末（章の最後のトピックのみ）③寄り道復帰
  （戻った位置を `atlas-pulse` でハイライト）④初回ログインの一度きり自動表示。
  ①〜③はカード提示に留め**自動で開かない**。抑制ルール: 直近10分以内にオーバーレイを
  開いていたら①②のカードを出さない（③は対象外）。カードは常に最新1枚。
- 初回自動表示フラグは `atlas_cue_events` の `(user_id, 'first_login', 'opened')` 行の
  存在で永続化（再ログイン・別端末でも一度きり）。オプトアウトは設定項目ではなく
  オーバーレイ内注記（§16-6 の決定）。フラグ確認不能時は自動表示しない（fail-closed）。
- 内部計測（`POST /api/learning/atlas/cues/events`: shown/opened/dwell/learn_reached、
  `GET /api/learning/atlas/cues/state`）は Stage 2 ゲート判断の材料。
  **数値をユーザーに見せる API・UI は作らない**。

### D層（Doubt Layer, migration 029〜033）

A層（構造化）・B層（学習）・C層（承認）に続く第四の層。「合意の強さ」と「検証の強さ」を
データ構造レベルで分離した**認識的地位台帳（epistemic ledger）**を軸に、暗黙前提の明示化・
疑義・検証提案・反実仮想を制度化する。issue 分割の正本は
`docs/features/doubt_layer_issues.md`。実装は `backend/core/doubt/`（tension /
structure_anchor と同型の独立モジュール群）+ `backend/api/routes/doubt.py`
（実パス `/api/admin/doubt/...`、学習者向け読み取りは `/api/learning/.../ledger` 等）。

**不変条項（§0）**: A層非改変（`src/episteme_graph/agents/` を読むだけ）/
AIに疑わせない（LLM 出力は常に candidate、確定は人間。反実仮想の「再構築」は計算しない）/
帰属必須・匿名疑義なし（全書き込みを `theory_review_events` に監査。entity_type は
`'ledger'` `'assumption'` `'challenge'` `'verification_proposal'` `'counterfactual_session'` を追加）/
情報を落とさない（dismiss・withdraw は status 遷移で保持, P4）/
煽らない・数値を見せない（`load_score`・confidence の生数値を API/UI に出さない。段階ラベルのみ）/
同期パスに LLM を入れない（スコープ候補・前提正規化は非同期 worker, P6）/
学習者を監視しない（naive signals は k-匿名 k=3・n<3 非表示, P3）。

- **台帳（D1, migration 029 `epistemic_ledger`）**: `UNIQUE(target_id, target_type)`。
  `verification_status`（`directly_verified | indirectly_supported | untested | refuted | unknown`）と
  `verification_scopes JSONB`（**配列**。各要素 = condition/domain/precision/system +
  `evidence_ids` + `recorded_by` + `reason`。検証を単一ブールにしない — 構想の心臓部）。
  スコープ 0 件（空欄）は正常状態であり**発見**（エラー表示・警告色にしない）。
  `directly_verified` への昇格はスコープ 1 件以上のときのみ許可（全称検証の構造的禁止）。
  `directly_verified` は人間の記帳専用で、`ledger_builder.py`（非LLM バックフィル）は生成しない
  （evidence 付き `source_backed` atomic claim も `indirectly_supported` 止まり）。
  LLM スコープ候補（`backend/core/doubt/scope_candidates/`）は `scope_candidates` 列に
  `status='candidate'` で保持し、教員確定まで `verification_scopes` 本体に入らない。
  コスト上限 `DOUBT_SCOPE_MAX_CALLS_PER_DAY`（既定 10、tension とは独立）。
- **素朴な問いの計器化（D1-5）**: `naive_signal.py` が interest_traces のうち
  **本人が引き受けた行のみ**（tension: owned status / anchor: `learner_selected`・`confirmed`）を
  anchor 単位で k-匿名集計（件数はレンジ表示 3-5 / 6-10 / 11+）。candidate は集計に入れない（P1）。
- **負荷度（D2-1）**: `load_calculator.py` が TheoryOperationGraph + derivation +
  claim リンクから下流到達集合サイズを決定論的に計算（閉路対応）。`graph_layer='debug'` /
  `inferred` ノードは根拠にしない。生値は DB のみ、API は段階ラベル（低/中/高/最高位）。
- **暗黙前提マイニング（D2-2 経路A / D3-5 経路B, migration 030 `assumption_nodes`）**:
  経路A = `inferred`/`review_required` 補完ステップのコーパス横断クラスタ（**2 論文以上 or
  2 導出以上**の反復のみ候補化、LLM は正規化のみ・非同期）。経路B = 「依存されているが
  被主張・被引用がないノード」の検出（`corpus_audit.py`、3 論文未満のコーパスでは実行しない）。
  出力は常に `status='candidate'`。確定（`confirmed`）・却下（`dismissed` 保持）は教員 API（D2-4）。
  確定時に台帳行を自動生成（既定 `untested`・スコープ空欄）。
- **前提の地図（D2-3, Assumption Atlas）**: 負荷度×検証度の散布図（admin.js タブ +
  `doubt-atlas.js`）。ゾーン名・煽り文句・推奨マークを描かない。空欄スコープの点は
  塗りなし・点線輪郭。**Field Atlas（分野の地図）とは別機能** — コード・API・UI 文言とも
  `doubt-` / `assumption-` プレフィックスで衝突回避。
- **疑義（D3-1, migration 031 `challenges`）**: 承認と対になる一級市民。
  `challenge_type`（`scope_extrapolation | untested_in_domain | definitional | hidden_lemma`）+
  `reason` 必須・匿名不可。withdraw は status 遷移で履歴保持。疑義カードの主語は常に型
  （人格対立の文面にしない）。数値スコア化しない。
- **検証提案 + 未検証合意リスト（D3-2, migration 032 `verification_proposals`）**:
  challenge → proposal 昇格で `led_to_verification` に遷移。open-assumptions リストは
  台帳の投影（高負荷×低検証の自動編纂・編集不可）。
- **反実仮想（D3-3/D3-4, migration 033 `counterfactual_sessions`）**: 前提を仮に偽に倒し
  `collapsed / surviving / indeterminate` の 3 区分を決定論的に伝播計算（再構築は計算しない）。
  セッション保存は既存 Visibility 語彙（private/group/public）。UI は可逆・非破壊を明示し
  「崩壊」でなく「この前提に依存する範囲」と事実で書く。
- **学習者導線（D3-6）**: 読み取り専用 + 間接参加のみ。出典タブ・教材詳細に検証状態を
  一行の事実として併記（未検証と検証済みを同じ精度で併記, §8-1/8-2）。学習者の直接疑義投稿は
  意図的にやらない（地位勾配 §8-3、運用観察後に別 issue で判断）。
  台帳未記帳コースではセクション自体が出ない（fail-closed）。
- **ガードレール（DX-1）**: `backend/tests/test_doubt_guardrails.py` が AI断定禁止・
  匿名疑義なし・数値非表示・k-匿名・P4 保持・禁止語彙（「疑え」「ノーベル賞」等）を構造的に守る。
- **KPI（DX-2）**: `GET /api/admin/doubt/metrics`（SYSTEM_ADMIN のみ）。
  `theory_review_events` の再集計のみで専用カウンタテーブルを持たない。ダッシュボード UI は作らない。

### 横断ユーティリティ層（Admin Copilot, migration 034）

管理画面に点在する AI 機能（コース構築チャット・原稿スタジオ rewrite・コンポーネント候補生成）を、
全タブ横断の**統合 AI アシスタント（Admin Copilot）**に統合する層。正本は
`docs/features/admin_assistant_design.md`。A層/B層/C層/D層の**コードは変更せず**、既存 API を
呼ぶ側として実装する（P7）。実装は `backend/core/admin_assistant/` + `backend/api/routes/admin_assistant.py`
（実パス `/api/admin/assistant/...`）+ `frontend/public/js/admin-assistant.js`（ES5・`window.AdminAssistant`）。

**不変条項**: P1 権限を越えない（fail-closed。**capability registry** に登録され、かつ現在ユーザーの
ロールで許可された操作のみ説明・代行・道案内。判定はサーバ側、フロント表示を信頼しない）/
P2 破壊的操作（`reversible=false`＝削除・公開・freeze・アカウント作成）は代行前に明示確認ゲート/
P3 情報を落とさない（apply 前に before スナップショット、取り消しは状態遷移で行削除しない）/
P4 断定・捏造しない（説明は登録済み KB に基づき根拠併記、無ければ「未整備」）/
P5 監査必須（apply/revert/confirm を `theory_review_events` `entity_type='assistant_action'` に記録）/
P6 同期パスを重くしない（chat は 1 LLM コール上限、失敗時は非LLMヒューリスティックへ縮退）/
P7 既存 A/B/C/D 層コードを変更しない/ P8 道案内は誘導まで（画面遷移＋入力箇所の点灯のみ。
値入力・送信・保存は本人。fail-closed は同じく適用）。

- **3 モード（単一チャットで自動振り分け）**: `intent.py` が `guidance`（説明・DB非変更）/
  `locate`（道案内・DB非変更）/ `action`（代行・変更）/ `clarify`（聞き返し）に分類。
- **Capability Registry（心臓部）**: `core/admin_assistant/capabilities.py` が画面横断の**単一の真実源**。
  各 `Capability` は `id` / `screen` / `required_role` / `scope` / `kind`（`guidance_only`|`action`）/
  `reversible` / `confirm`（`reversible=false` は必ず `confirm=true`）/ `api` / `revert` / `howto_doc` /
  `locate_steps`（道案内の順序付き点灯手順。各要素 = `{screen, anchor_id, hint, precondition?}`。
  `anchor_id` は**論理 ID**でバックエンドは DOM セレクタを持たない）を宣言。全 API を一度に載せず
  安全・高頻度から段階登録（fail-closed）。ロール階層 SYSTEM_ADMIN ≥ TEACHER ≥ STUDENT。
- **操作 KB**: `docs/admin_operations/*.md`（front-matter `capability`/`role`/`screen`）。`knowledge.py` が
  起動時にインデックス化し、検索前に `capabilities_for(role)` で絞る（権限外の手順は出さない）。
  リアルタイム生成しない。KB に無ければ「未整備」。
- **操作代行 + 戻す**: `action` capability は `actions/` に tool（`capture_before`/`apply`/`revert`）として実装し
  `apply` は既存 API/関数を呼ぶだけ（P7）。リスク階層 = 局所可逆(L1・クライアント Undo) /
  永続可逆(L2・サーバ revert) / 不可逆(要確認)。取り消しは `assistant_actions`（migration 034）の
  before/after スナップショットで復元。`reversible=false` の revert は 409。
- **道案内（Locate & Spotlight）**: `locate_plan.steps` をフロント `runLocatePlan()` が順に実行し
  `activateTabView` → `registerUiAnchors` で DOM 解決 → `scrollIntoView` → `.admin-assistant-spotlight` 点灯
  + hint 吹き出し。`precondition` 未達のステップで停止し画面の選択完了を待って次へ。DB 非変更・監査対象外。
- **コスト上限**: `ASSISTANT_MAX_CALLS_PER_DAY`（既定 20）/ モデルは fast tier 既定（`ASSISTANT_LLM_MODEL` で上書き）。
- **ガードレール**: `backend/tests/test_admin_assistant.py` が「全 `reversible=false` は `confirm=true`」
  「`core/admin_assistant/` が FastAPI を import しない」「locate は role で fail-closed」を構造的に守る。

### ガイダンス層（G層, migration 039）

「次にやること」バッジ + 状態導出型 To-Do + 地図 fail-closed 徹底。正本は
`docs/features/guidance_layer_design.md`（設計書は migration 038 と記載だが **実装は 039**。
038 は状態管理・通知基盤が使用済み）。Admin Copilot の capability registry と
`runLocatePlan` を再利用する薄い層で、A/B/C/D/R/V 層のコードは変更しない（G7）。

**不変条項**: G1 完了フラグを持たない（To-Do はサーバ状態から毎回決定論的に導出。実施すれば
自動消滅）/ G2 非LLM・同期 / G3 capability registry を単一の真実源に（ロールで fail-closed。
権限外ルールは評価すらしない）/ G4 押し付けない（バッジは件数のみ。パネル自動表示・ポーリング
禁止）/ G5 却下は保持（dismiss は `revoked` 遷移で行削除しない）/ G6 理由は事実文（煽り・
督促・数値スコア禁止）/ G8 道案内は誘導まで（`AdminAssistant.runLocatePlan` を呼ぶだけ）。

- **エンジン**: `backend/core/admin_assistant/next_steps.py`（FastAPI / LLM 非 import）。
  `compute_next_steps(session, user)` がルールカタログ v1（6件）を本人所有の教材・コースに
  対して評価: `materials.none` / `material.analysis_failed` / `material.no_course`（required）、
  `course.not_published` / `course.no_atlas_binding`（recommended）、`course.audio_missing`
  （optional）。severity→古い順、上限 10 件（切り捨ては `truncated: true` で正直に返す）。
  ルールは「次の一歩だけ」を出すチェーン設計（教材登録→コース作成→binding/公開と順に現れる）。
- **API**（`routes/admin_assistant.py`、TEACHER 以上）: `GET /api/admin/assistant/next-steps`
  → `{steps, hidden, truncated, assistant_cue_pending}`。
  `POST .../next-steps/{step_key}/dismiss` / `POST .../{step_key}/restore`（upsert / `revoked`
  遷移。`theory_review_events` に `entity_type='next_step'` で監査）。
- **DB**（migration 039 `assistant_step_dismissals`）: `UNIQUE(user_id, step_key)`、
  `step_key = "{rule_id}:{target_id}"`。初回ログイン cue のフラグも同テーブルの
  `step_key='cue:first_login'` 行で代用（テーブルを増やさない）。
- **追加 capability**: `course.atlas_binding` / `lecture_studio.generate_audio`
  （いずれも `KIND_GUIDANCE_ONLY`。v1 は道案内のみ）。
- **フロント**: `frontend/public/js/admin-next-steps.js`（ES5・`window.AdminNextSteps`）。
  ヘッダーの `📋 次にやること` バッジ → severity 別パネル → `[案内する]` が
  `AdminAssistant.runLocatePlan(step.locate_plan)` を呼ぶ。再取得はログイン時 / タブ切替 /
  教材アップロード・コース登録・公開・binding 保存の成功後のみ（ポーリング禁止）。
  cue（🤖 pulse）は `assistant_cue_pending` が true のとき一度きり、表示後に
  `cue:first_login` を dismiss して永続化。取得失敗時は出さない（fail-closed）。
- **地図 fail-closed（Phase 0）**: `atlas-data.js` の `DEFAULT_CARTRIDGE = "particle_physics"`
  フォールバックを廃止。コース文脈も明示 cartridge も無ければ取得せず null（地図領域ごと
  非表示）。未設定コースで無関係な素粒子物理の地図が出る最後の経路を塞いだ。
- **ガードレール**: `backend/tests/test_next_steps_guardrails.py`（capability 存在・fail-closed・
  行削除しない・core 非 FastAPI・禁止語彙・上限と truncated の整合）。
- **非スコープ**: 学習者向けバッジ / To-Do 自動実行 / メール・プッシュ通知 / 進捗率表示。

### レクチャーの表示ソースと音声（トピック教材ベース、migration 047）

**レクチャー受講の表示は、非レクチャー時の教材表示（`get_topic_material` =
`topics[].student_material` 最優先）と一致させる。** かつては音声キャッシュのために
「実チャンク教材を持つトピックはチャンク経路（PDF由来チャンク）を優先」していたが、
これだと受講画面のレクチャーが「トピックに紐づく整形済み教材」ではなく生 PDF チャンク
（英語原文・OCR ノイズ）を流してしまい、表示が非レクチャー時と食い違った。現在は逆に
**トピック教材を最優先**する。

- **表示ソース判定の正本は `_lecture_uses_topic_material(topic)`**（`backend/api/routes/lecture.py`）:
  トピックが `student_material`/`content`/`summary` または `spoken_script` を持つなら
  トピック教材経路（`_build_topic_draft_segment`＝display=student_material /
  read=spoken_script をスライド分割）を使う。持たないトピックだけが PDF 由来
  チャンク経路へフォールバックする。`get_lecture_sequence` / `get_topic_audio_status` /
  studio のトピック音声生成の3者が**同じ述語**を使い、表示・ボタン活性・音声生成の
  食い違いを防ぐ（`_topic_has_linkable_material` は撤去済み）。
- **スライド分割の正本は `_build_topic_slides(topic)`**（`routes/lecture.py`、決定論的・LLM 非使用）:
  正規化＋`![[equation]]` 解決のうえ `core/lecture.py::auto_paginate_slides` で分割する。
  受講表示・音声生成・readiness の3者が**この関数を通る**ことで `slide_index` を完全一致させる。
  分割規約: `===` マーカーがあれば教員の明示分割を優先。無く display が長い（既定600字目安・
  数式1個=60字換算）場合は**段落境界で自動ページ分割**し、表示と読み上げを同数ページ・同順で
  対応させる（読み上げが同数ページに割れないときは spoken を空＝タイマー送りに縮退し表示だけ
  分割）。チャンク経路は従来どおり `split_slides`（自動ページ分割しない）。
- **トピック音声は別テーブル `topic_lecture_audio_cache`（migration 047）**にキャッシュする。
  キーは `(course_id, topic_id, slide_index, voice)`。`lecture_audio_cache` は `chunk_id`
  が `chunks(id)` への FK を持ちトピック（JSON キー）を格納できないため独立テーブルにした
  （既存のチャンク音声・#491 readiness には影響しない）。学習側 `generate_tts` は
  chunk_id が `topic:{topic_id}` 形式なら本テーブルから配信する（`_parse_topic_ref` /
  `_get_topic_audio_cache`）。生成は studio の音声生成（`_batch_audio_worker` 内の
  `_generate_course_topic_audio`）が担い、受講側と同じ `split_slides` で分割して各スライドの
  `spoken_text` を TTS 化する（表示スライドと音声スライドが同じ `slide_index` で一致）。
  トピックの授業用教材/読み上げ原稿を編集すると当該トピックのトピック音声は無効化される
  （`save_lecture_studio_course_topic` が `DELETE`）。学習者経路からの音声生成禁止
  （`generate_tts` はキャッシュ配信のみ・404 方針）は不変。

### レクチャースライド同期 + 音声言語切替（migration 040）

受講レクチャーを「スライド + スピーカーノーツ」モデルへ転換し、表示と読み上げを構造的に
一致させる層。正本は `docs/features/lecture_slide_sync_design.md`。

- **スライド = 表示と音声の同期最小単位**: 既定 1チャンク=1スライド。`display_text` /
  `spoken_text` 内の**単独行 `===` マーカー**で対分割できる。分割は DB に保存せず
  `core/lecture.py` の `split_slides()` が読み出し時に決定論的に導出する。表示/読み上げの
  分割数不一致は**1スライドに縮退**（エラーにしない・情報を落とさない）し、スタジオで
  `slide_mismatch` 警告を出す。`formulas` は各スライドの display_text が参照する
  `[[FORMULA_N]]` だけを割当（未参照分は最後のスライドに残す）。
- **音声はスライド単位で生成・キャッシュ**: `lecture_audio_cache` に `slide_index` /
  `language` 列を追加（migration 040、`UNIQUE(chunk_id, slide_index, voice)` に張替え）。
  `_batch_audio_worker` はスライドごとに `generate_tts_audio(spoken_text, language)` を
  呼ぶ。原稿編集・AI書き換え時の無効化はチャンク単位で全スライド分 DELETE（既存挙動）。
  学習者経路からの音声生成禁止（`generate_tts` はキャッシュ配信のみ・404 方針）は不変。
- **言語**: コース単位の `lecture_language`（`ja`|`en`、lecture-studio settings に保持）。
  TTS への言語指定ハードコード禁止（開発ルール8。Google 経路の `ja-JP` 固定は撤廃済み）。
  `chunks.spoken_language` に原稿の生成言語を記録（NULL は `ja` とみなす）し、
  `lecture_language` と不一致の音声は audio-status の ready に数えない（`stale_language`）。
  言語切替は音声生成モーダルで選択 →「原稿再生成 → 音声再生成」の自動チェーン
  （既存音声が無効になることを生成前に明示告知する）。
- **受講画面（`app.js`）**: レクチャーモード中は `#lecture-slide-stage` にスライド1枚
  表示（縦スクロールさせない。収まらない場合はフォント段階縮小 → 等比縮小で全文表示）。
  表示ソースは `segment.slides[].display_text` に一本化（`student_material` 全文表示・
  線形オートスクロール・キャプション領域・非表示ステージング `#lecture-content` は
  レクチャーモードから廃止）。◀▶ = スライド移動、音声 `ended` で自動送り。
  文ハイライトは表示中スライド本文への文字数比近似（`word_timestamps` は非スコープ）。
  音声なしスライドはタイマー送り（ja 300字/分・en 150wpm・最低3秒）+「音声未生成」表示。
- **原稿スタジオ（`admin.js`）**: displayView に `slides` プレビュー（受講画面と同一
  レンダラ・分割整合インジケータ・長さ警告・スライド単位試聴
  `GET /api/admin/chunks/{chunk_id}/lecture-audio`（`_require_teacher`・キャッシュ配信のみ））。
  音声生成はモーダルで言語選択。教員がプレビューで見た分割・聞いた音声がそのまま
  学習者に配信される（プレビューと配信のレンダラ・分割ロジックを共有すること）。
- **分割・readiness の正本は Python 側（2026-07 整理）**: スライド分割のプレビューは
  `POST /api/admin/lecture-studio/preview-split`（`_require_teacher`・DB 非変更）が
  `core/lecture.py::split_slides` をそのまま返す。admin.js の JS 並行実装（`lsSplitSlides`）は
  廃止済み — **クライアント側に分割ロジックを再実装しないこと**。音声準備完了の判定も
  `core/lecture.py::compute_material_audio_readiness()`（スライド単位 + 言語一致）が単一の
  正本で、`core/status/projector.py` と `routes/lecture.py::get_topic_audio_status` の両方が
  これを呼ぶ（G層 To-Do と UI ボタン活性の食い違いを構造的に防止）。
- **音声生成の準備確認フロー（Issue #491、`lecture_studio/scripts.py` + `admin-lecture-studio.js`）**:
  音声生成は「コースを選ぶ → 読み上げ可能を確認 → 言語を選ぶ → 生成」の状態起点操作にする。
  - **フロント**: 音声言語モーダル `#ls-audio-lang-modal` は `class="ls-settings-modal"`（`display:flex`）
    が `[hidden]` を上書きするため、`#ls-audio-lang-modal[hidden]{display:none!important}` を明示
    （でないとコース未選択でも開いたまま漏れる）。モーダルを開けるのは `lsCanGenerateAudio()` が
    真のときの `#ls-audio-all-btn` 押下時だけ（コース選択・タブ初期化・原稿一覧読込では開かない）。
    音声生成の有効化 = `lsCanGenerateAudio()`（コース選択 / lecture-scripts・コース構造の両ロード完了 /
    コース内容完了 / チャンク≥1 / **全チャンクに spoken_text（＝`status!=="ungenerated"`。API の
    `spoken_text` は display_text へフォールバックするため使わない）** / タスク非進行中）。未準備時は
    `lsAudioReadinessState()` で理由＋次操作を近傍表示。コース切替の開始時に前コースの
    chunks・構造・分析・ロード完了フラグをクリアし、遅延応答は courseId 不一致で破棄する。
  - **API**: `POST /api/admin/courses/{id}/lecture-audio/generate` は UI 非経由の呼び出しにも同じ
    前提を強制する。対象チャンクなし=422、通常経路（言語切替チェーンでない）で空 `spoken_text` の
    対象があれば不足件数付き 422。判定は DB 実値 `stored_spoken_text` を使う。言語切替チェーン
    （原稿再生成 → 音声生成）は原稿を作り直すため、この事前チェックを免除する。

### 画像読み取りパイプライン + 分野別ナレッジライブラリ（L層, migration 041/042）

PDF 内の画像（装置図・設計図等）を解析パイプラインに取り込み、分野別ナレッジライブラリを
参照して装置・パーツを候補抽出する層。正本は
`docs/features/image_pipeline_knowledge_library_design.md`。既存 agent は非改変
（`parser.py` の画像スキップ・FigureTableSemanticsAgent はそのまま）。

- **アップロードオプション**: `upload_material` / `reanalyze_document` に
  `analyze_images`（既定 false）。`document_analysis_runs.options JSONB`（migration 041）に
  run 単位で保存（`stage_outputs` への相乗り禁止）。
- **`figure_image_extraction`**（`core/document_pipeline/figure_images.py`、非LLM・**常時実行**、
  `document_structure` 直後）: PyMuPDF 埋め込み画像抽出 + caption 近傍の領域レンダリング
  fallback（`extraction_method='embedded'|'region_render'`）。MinIO `figure-images` バケット +
  `document_figures` テーブル（`UNIQUE(document_id, figure_key)` upsert）。caption 対応が
  取れない画像も `caption_block_id=NULL` で保持（P4）。図単位の失敗は `status='failed'` で
  非致命。**図中ラベル抽出（migration 051）**: 図領域内のテキストスパンを
  `page.get_text("words", clip=...)` で収集し、同一行・近接語をグルーピングして
  `document_figures.inner_labels JSONB`（`[{"text","bbox":[x0,y0,x1,y1]}]`、ページ座標系）に
  保存する（決定論・非LLM。caption ブロックと重なる語は除外。`f = 75 mm` 等の
  パラメータ表記も落とさない、P4）。ベクター描画の図（TikZ 等）でラベルが PDF テキスト層に
  埋まっているケース（装置模式図）のパーツ列挙グラウンディングに使う。
- **`figure_context.py`**（`core/document_pipeline/`、非LLM・決定論的）: 図ごとの文脈収集
  `collect_figure_context(structure, figure_row, inner_labels=...) -> FigureContext`。
  ① caption ブロックが属するセクションの本文 ② `Fig. 5.2` 型の参照メンション段落（±1ブロック、
  図番号は figure_label / figure_key から導出）③ `フル表記 (略語)` / `略語 (フル表記)` パターンの
  略語辞書（inner_labels の語で引けるものに絞る）を優先度順
  （略語定義 > caption 直近 > 参照段落 > セクション残り）に収集。上限は
  `APPARATUS_CONTEXT_MAX_ITEMS`（既定12）/ `APPARATUS_CONTEXT_MAX_CHARS`（既定6000）。
- **`apparatus_semantics`**（`src/episteme_graph/agents/apparatus_semantics/`、vision LLM、
  `figure_table_semantics` 直後・`analyze_images=true` のときのみ）: 画像 + caption + 近傍本文
  （`figure_context.py` の実収集結果。かつて `nearby_text=[]` 固定だったギャップは解消済み）
  + 図中ラベル + 略語辞書 + ライブラリ**凍結版**の retrieval（caption + 近傍本文 + 略語展開の
  テキスト embedding → pgvector top-k、既定5）を入力に、装置同定・パーツ分解を structured
  output で候補化。`ApparatusPart` は `label_ref`（図中ラベルへの参照、LLM出力・validator が
  inner_labels 実在を hard error 検査）/ `expanded_name` / `bbox` を持ち、**bbox と
  expanded_name は agent 側で label_ref → inner_labels / abbreviations の突合により決定論的に
  付与する（LLM 出力からは取らない）**。role は本文からの verbatim quote で裏付け、根拠のない
  役割は書かせない（見た目の推測は reason に留める）。図中ラベルは網羅を促すがパラメータ表記
  （`f = 75 mm`・`s-pol.` 等）はスキップ可、未カバーは warning で保持（P4）。出力は常に
  `review_status='review_required'` 系・`source_backed` を自動付与しない（確定は人間のみ）。
  off 時は `{"skipped_by_option": true}` を `stage_outputs` に正直に記録。ライブラリ 0 件でも
  単独動作（`match_status ∈ {novel, unknown}` に縮退）。参照版は
  `stage_outputs.referenced_library_versions` に記録。vision は `core/llm.py` の
  `generate_structured_with_images()`（v1 は OpenAI 経路のみ）。上限は
  `APPARATUS_MAX_IMAGES_PER_DOCUMENT`（既定20）/ `APPARATUS_MAX_CALLS_PER_DAY`（既定30）、
  超過分は `skipped_by_limit` で保持しステージは正常完了。
- **component_type 語彙拡張**（migration 041）: `theory_components.component_type` CHECK に
  `apparatus` / `instrument` / `part` を追加。カートリッジ `component_types.json` にも同語彙。
  装置候補は ComponentAssembly 経由で `status='candidate'` の theory_components になる。
  **TheoryOperationGraph には組み込まない**（v1。式 backing が無いため）。
- **L層ライブラリ**（migration 042 `library_entries` / `library_entry_versions`、
  `backend/core/library/`（store/search/seed、FastAPI 非 import）+
  `backend/api/routes/library.py`（実パス `/api/admin/library/...`、`_require_teacher`））:
  分野（domain_key = cartridge_id 名前空間）ごとの教員共同財。atlas_skeletons パターン踏襲
  （draft 正本 + `revision` 楽観ロック（衝突 409）+ 凍結版履歴 + カートリッジ同梱
  `library/*.json` シードの冪等取込）。**パイプラインが読むのは凍結版のみ**（draft 不使用）。
  削除 API は無く `status='retired'` 遷移のみ（P4）。retired は retrieval に出ない。
- **昇格は人間の操作のみ**（LLM がライブラリへ書き込む経路を作らない）: 装置候補 /
  theory_components / 白紙の 3 経路 → 昇格モーダル（類似エントリ提示・統合可）。
  **例示画像は既定で含めない** — 含有は元 document 所有者のみが明示確認を経て実行
  （所有者以外は 403、fail-closed）。エントリ本文（テキスト）は教員全体に開示、
  画像は元 document の権限を継承。
- **図画像 API**: `GET /api/admin/documents/{id}/figures` / `GET .../figures/{fid}/image` —
  必ず `_ensure_document_viewable` を通す（権利 fail-closed）。figures 一覧は図の `bbox` /
  `inner_labels` と装置候補パーツの `label_ref` / `expanded_name` / `bbox` / `evidence_quote`
  も返し、管理UI（`admin.js` 図モーダル）が図画像上に bbox オーバーレイ（%座標 = ページ座標を
  図 bbox で正規化。region_render / embedded 両方式で同一変換）+ パーツ詳細を表示する。
  オーバーレイは閲覧・レビュー用で、確定操作は既存のライブラリ昇格導線のみ（candidate-only 原則）。
- **監査**: 作成・draft 更新・凍結・retire/restore・画像含有承認を `theory_review_events`
  `entity_type='library_entry'` に記録。
- **ガードレール**: `backend/tests/test_image_library_guardrails.py`（LLM 直接書込経路なし・
  review_required 徹底・画像既定非含有・配信 API の権限ゲート・行削除 API 不在・
  skipped_by_option 記録・core/library の FastAPI 非 import・retrieval が draft を読まない）。
- **非スコープ（v1）**: 学習者向け表示 / TheoryOperationGraph への装置ノード / CLIP 等の
  画像埋め込みモデル / グループ限定ライブラリ / vision 自動有効化 / table の画像解析。
- **図・画像の分類と「深く検討」UI 切替（#496, migration 052/053）**: vision 解析
  （apparatus_semantics の**同一コール**に相乗り・追加 LLM なし）が図を提示モードに分類する。
  語彙の正本は `src/episteme_graph/agents/figure_modes.py` の `FIGURE_MODES`
  （`functional_diagram`（機能構成図）/ `data_plot`（グラフ）/ `descriptive_image`
  （写真・解説画像）/ `mixed` / `unknown`）。vision 不在時は caption ヒューリスティック
  `infer_mode_from_text()` に縮退（判別不能は `unknown`）。保存先は `document_figures`:
  AI 候補 = `suggested_mode` / `mode_reason` / `analysis_profile JSONB`（052、再解析で置換可）、
  教員の分類オーバーライド = `reviewed_mode` / `mode_review_status(pending|reviewed)` /
  `mode_reviewed_by/at`（052）、教員確定の構造化解析 = `reviewed_analysis_mode` /
  `reviewed_analysis_profile` / `analysis_review_status` / `analysis_reviewed_by/at` /
  `analysis_review_source_annotation_id`（053。W層注釈 commit 由来・AI 再解析で消えない）。
  `effective_mode = reviewed_mode ?? suggested_mode` で、モード訂正後に対応解析が未確定なら
  旧モードの解析を新モードの顔で見せず空プロファイルに縮退（`analysis_source` を明示）。
  API は `routes/figure_presentation.py`（figures GET の正本 / `PATCH .../presentation-mode` /
  `POST .../reanalyze`＝候補注釈化。監査 `entity_type='figure_presentation'`）。UI は
  `deliberation.js`「深く検討」モーダルが `effective_mode` 別に解析ペインを切替
  （機能・ポート・接続 / 軸・系列・観察と解釈の分離 / 被写体・領域・教示ポイント / パネル別）。
  分類・解析とも教員の確定操作なしに reviewed にならない（candidate-only 原則を継承）。
  詳細は `docs/features/image_pipeline_knowledge_library_design.md` §15。
- **retired エントリは読み取り専用（2026-07-17 確定）**: retired の draft 編集・凍結は 409。
  変更は `restore` で active に戻してから（同設計書 §16）。

### LLM トークン使用量推計（U層, migration 043）

全 LLM 呼び出し（agent 群は `ProviderJSONLLMClient` 経由で `core.llm` に集約済み）の
トークン消費を記録・推計する観測レイヤー。正本は `docs/features/llm_usage_metering_design.md`。
呼び出し側のコードは変更せず、フックは `core/llm.py`（+ `core/tts.py`）に一元化する。

- **不変条項**: U1 実測優先・推計は正直に（`usage_source ∈ {reported, estimated_tokenizer,
  estimated_heuristic}` を分離集計、混ぜた単一数値を見せない）/ U2 呼び出しを止めない
  （記録は bounded buffer + flusher thread、`record()` は例外を漏らさない）/ U3 計測点は
  `core/llm.py` に一元化（帰属は contextvars）/ U4 A層非改変 / U5 数値は SYSTEM_ADMIN のみ
  （事前見積りのみ TEACHER・レンジ表示）/ U6 削除 API を作らない（append-only）/
  U7 料金をハードコードしない（価格表は `LLM_PRICE_TABLE_PATH` の JSON、無ければ cost=null）/
  U8 バッファ溢れ（dropped_events）を隠さない。
- **実装**: `backend/core/llm_usage/`（schema / context / estimator / recorder / observe /
  pricing / metrics。FastAPI 非 import）+ migration 043 `llm_usage_events`（FK なし・金額列
  なし）+ `backend/api/routes/llm_usage.py`。
- **帰属**: `usage_context(feature=..., user_id=..., document_id=..., run_id=...)` を
  orchestrator / chat / 各 worker がセット。未設定は `feature='unattributed'` で記録
  （記録自体は fail-open、消費量を落とさない）。feature 語彙は `pipeline:{stage}` /
  `learning:chat` / `admin:course_builder` 等（正本は `llm_usage/schema.py`）。
- **推計**: reported が無いときのみ。tiktoken は optional、フォールバックは
  `ceil(CJK×1.0 + その他/4)` ±40% レンジ。vision は寸法既知なら `85+170×tiles`、
  不明なら 765/枚。structured output は schema JSON も入力に算入。
- **API**: `GET /api/admin/llm-usage/metrics`（SYSTEM_ADMIN、reported/estimated 分離 +
  dropped_events + cost_usd）/ `GET /api/admin/llm-usage/estimate/documents/{id}`
  （TEACHER・`_ensure_document_viewable`・レンジのみ・金額なし）。
- **既存の回数上限（`*_MAX_CALLS_PER_*`）は変更しない**。enforcement・ストリーミング
  usage・学習者向け表示は非スコープ。
- **ガードレール**: `backend/tests/test_llm_usage_guardrails.py`（recorder 非漏洩・
  FastAPI 非 import・削除 API 不在・権限 fail-closed・分離集計・レンジのみ・
  価格ハードコード検出・学習者 API 非漏洩・estimator 決定性）。

### 質問の出所分類（教材/別の資料/モデル生成）

学習チャットの「教材に沿って質問」「自由に質問・探索」ボタンは廃止し「質問」1つに
統合済み（`intent_mode` の on_path/explore 自体は寄り道復帰導線のため内部的に残り、
単一ボタンは Enter キーと同じ判定＝寄り道中なら explore、そうでなければ on_path で送る）。
代わりに **回答が何に基づくか**（`content_grounding`: `course_material` | `other_material` |
`model_generated`）を RAG 実行後に判定し、回答バブルと出典タブに提示する。
`tier`（教員承認状況）とは別軸: `origin` はチャンクの `material_id` がそのコースの
`sources[].material_id` に含まれるか（教材）否か（別の資料）で決め、`cited_sources` が
完全に空なら `model_generated`。判定は `search_chunks_with_metadata` が返す
`material_id` を使うため、この関数のクエリを変更する際は `material_id` の SELECT を
落とさないこと。

### カジュアル対話モード + ハンズフリー音声会話（B層）

学習チャットに「気軽に話せる先生」モード（`intent_mode='casual'`）を追加。
雑談拒否（CHIT_CHAT ルート）・前提知識ゲート・誤解検出をバイパスし、短い会話調
（音声向け・箇条書き/ドリルダウンマーカーなし）で応答する。**根拠の一線は維持**:
RAG 検索・tier 集約・OutOfSourceGuard の system 注入はそのまま
（可視の注意書きプレフィックスのみ casual では省略、tier はレスポンスで返す）。
interest_traces 記録と tension プレフィルタも通常どおり効く（payload に `casual: true`）。

**音声 API**（`backend/api/routes/learning.py`）:
- `POST /api/learning/voice/transcribe` — multipart 音声 → `core.llm.transcribe_audio()`
  （OpenAI Whisper 系、`LLM_TRANSCRIBE_MODEL` 既定 `whisper-1`。openai プロバイダのみ）
- `POST /api/learning/voice/speak` — `{text}` → `core.tts.generate_tts_audio()` で MP3(base64)。
  読み上げ前に LaTeX・markdown 記号・出典マーカーを除去（`_spoken_text_for_voice`）

**フロント**（`app.js`）: 通常学習画面の入力欄に 🤖 ボタン。押すとハンズフリーモード:
MediaRecorder + WebAudio 無音検知（発話後 ~1.4 秒の沈黙）で区切って自動送信 →
casual チャット → 応答を TTS 再生（再生中はマイク停止）→ 再生終了で聞き取り再開。
応答の第1根拠チャンク（`sources[0].chunk_id`）を `/source-chunk/` から取得し
ボイスパネルに教材表示する。

### 学習チャットのメッセージ書き直し・削除（機能3, B層）

学習チャットで、学習者が自分の入力メッセージを **書き直し（✏️）／以降削除（🗑）** できる。
どちらも「そのメッセージ以降の往復を捨てる」truncate セマンティクスで統一する。

- **書き直し**: `LearningChatRequest.replace_message_id` を付けて送信。`learning_chat` は本処理の
  前に `services.truncate_chat_and_supersede()` を呼び、**サーバ正本の履歴**を当該メッセージの
  位置で切り詰め（当該 user メッセージ・その回答・以降の往復を除去）、`body.history` を切り詰め
  済みで上書きしてから、`message` を同じ位置から通常フローで再処理する。誤解検出・tier・
  grounding・intent 分類は再処理で自然に再実行される。
- **削除**: `DELETE /api/learning/courses/{course_id}/topics/{topic_id}/chat/messages/{message_id}`。
  同じ `truncate_chat_and_supersede()` を使い、再送はしない。履歴が空になれば行削除。
- **派生痕跡の後始末（P4 情報を落とさない）**: 取り除いたメッセージ id を `payload.message_id` に
  持つ `interest_traces` は **削除せず** `status='superseded'` にする。以降 tension/anchor の
  worker（`_fetch_pending_*`）と各 digest・問いの軌跡ビューは `status <> 'superseded'` で除外する。
  ※ 誤解記録（`personal_layer.misconceptions_by_topic`）は message_id リンクを持たないため、
  現状は個別 supersede しない（既知の限界。将来 message_id 紐づけを付けてから対応）。
- **フロント（app.js）**: user バブルに ✏️/🗑。書き直しは本文を入力欄へ戻し `editingMessageId` を
  立て、送信で `_replace_message_id`（内部）→ クライアント履歴も同位置で truncate。削除は確認の上
  DELETE API を呼び、成功時にクライアント履歴を truncate。トピック切替で編集状態は解除する。

### 再構成ループ（Reconstruction Loop, R層, migration 036）

学習者の「能動的・生成的理解」を支える第五の学習機構。学習者に理論の再構成（予測 /
言い直し）をさせ、A層が生成した精密な構造（`theory_claims`）を **答えキー（ground truth）**
として構造照合し、ズレを事実として返す閉ループ。併せて claim 単位のつまづき信号を集約し、
原稿スタジオ「根拠リンク」ペインに表示切り替えで提示する。正本は
`docs/features/reconstruction_loop_design.md`。A層（`src/episteme_graph/agents/`）は**読むだけ・非改変**。
実装は `backend/core/reconstruction/`（tension / structure_anchor と同型の独立モジュール）+
`backend/api/routes/reconstruction.py` + `frontend/public/js/reconstruction.js`（学習）/
`admin.js`（原稿スタジオ トグル）。

**融合ループ**: 出題 ELICIT → 提出 CAPTURE → 照合 DIFF(=仮説・非LLM同期) →
開示 REVEAL(=権威) → 自己確認 SELF-CHECK(必須) →（再挑戦 REVISE ↺ / 記号葉へ降下 DESCEND ↓）。

**設計原則（不変条項）**: A層非改変 / 判定は構造（非LLM同期）で権威は出典リビール、
判定を authoritative に見せない（「食い違いの可能性」の仮説文体）/ item は LLM 自動オーサリング
（`status='auto'`=candidate 相当）で教員確定なしに配信、教員は事後の監査役（confirmed 追認 /
retired 回収）/ P4 情報を落とさない（item は削除せず `auto → flagged → retired` 状態遷移。学習者の
成果物・自己確認・異議も行削除しない）/ P6 実行時 DIFF は非LLM（LLM はオーサリング worker のみ）/
P7 スコア・正答率数値を学習者に見せない、REFLECT は事実文のみ、教員向けも段階ラベル + レンジ
（3-5 / 6-10 / 11+）/ P3 教員向け集計は k-匿名（k=3・n<3 セル非表示、個別履歴を見せない、評価利用禁止）。
出題対象は `support_status='source_backed'` かつ承認済み review_status（`teacher_approved` 等）の claim のみ。
葉は主張（claim）が既定、記号（SymbolRegistry）は原因が絞れないときだけ降りる点検口（§1.6）。

- **DB（migration 036）**: `reconstruction_items`（claim → ELICIT 変換の出題。
  `elicit_mode ∈ {predict, restate, symbol}` / `response_space`（predict の選択肢）/ `expected`（想定解）/
  `status ∈ {auto, flagged, retired, confirmed}`）、`learner_reconstructions`（学習者の産出物・改訂履歴。
  `machine_verdict ∈ {match, mismatch, na}` / `self_check ∈ {agreed, disagreed, verdict_wrong}` /
  `descended_to_symbol` / `revision_of`）、`reconstruction_item_health`（集計ビュー。疑わしさランクは
  `core/reconstruction/health.py` がアプリ側で計算し SQL に埋め込まない）。
- **core/reconstruction/**: `schema.py`（語彙・伏せフィールド・承認語彙の正本）/ `item_builder.py`
  （predict 可否判定・restate 縮退・伏せ・降下プローブ。非LLM）/ `diff.py`（実行時 DIFF + REFLECT 事実文。
  非LLM）/ `prompt.py`・`input_builder.py`・`llm_client.py`・`validator.py`・`repair.py`（item オーサリング。
  LLM は選択肢・expected 生成のみ、2回修復失敗で item を生成しない=配信しない）/ `worker.py`
  （オーサリング worker。トリガー: claim 承認時（`theory_components.update_claim` のフック）/ 手動バッチ API。
  冪等性: claim に非 retired item があればスキップ）/ `health.py`（review キュー）/ `stumble.py`
  （claim 単位つまづきサマリー。k-匿名）。
- **API**: 学習者向け（`routes/reconstruction.py` `learning_router`、本人のみ・受講ゲートは
  `get_accessible_course_data`）= `GET /api/learning/courses/{course_id}/topics/{topic_id}/reconstruction/next`
  （伏せフィールドを返さない）/ `POST /api/learning/reconstruction/{item_id}/submit`（応答保存 → DIFF →
  verdict + リビール）/ `POST .../{recon_id}/self-check` / `POST .../{recon_id}/descend` /
  `POST .../{item_id}/revise`。教員向け（`admin_router`、`_require_teacher`）=
  `GET /api/admin/reconstruction/items/review-queue`（疑わしさランク順）/
  `PATCH /api/admin/reconstruction/items/{item_id}`（status 遷移・prompt/expected 修正。削除 API は無い）/
  `POST /api/admin/reconstruction/documents/{document_id}/author`（手動オーサリング）/
  `GET /api/admin/documents/{document_id}/claims/stumble-summary`（つまづきサマリー）。
- **監査**: item 生成 / status 遷移 / self-check(verdict_wrong) / descend を `theory_review_events`
  （`entity_type` を `'reconstruction_item'` / `'reconstruction_response'` に拡張）に記録。
- **コスト上限**: `RECON_MAX_ITEMS_PER_DOCUMENT`（既定 30）/ `RECON_MAX_CALLS_PER_DAY`（既定 10、
  他機能と独立）、モデルは fast tier 既定（`RECON_LLM_MODEL` で上書き）。
- **フロント**: 学習画面は `reconstruction.js`（`window.Reconstruction`。トピック学習ビュー下部に
  「再構成に挑戦」導線。自動では開かない。P7）。原稿スタジオは `admin.js` 右ペインタイトル行の
  トグル `根拠リンク | つまづき`（別タブにしない。`lsState.rightPaneMode`）。
- **ガードレール**: `backend/tests/test_reconstruction_guardrails.py`（伏せフィールド非漏洩・
  スコア非表示・出題対象制限・削除 API 不在・k-匿名・core が FastAPI 非 import・REFLECT 禁止語彙）。

### 共有物のバージョン管理（V層, migration 037）

パイプライン生成物（`theory_components` / `theory_claims` / `theory_component_graphs` +
`document_analysis_runs`）とコース（`learning_courses`）を「発行版（Release）」として不変
スナップショット化し、共有先の教員が**所有者の一方的な更新・削除から保護される**第五の運用機構。
正本は `docs/features/shared_versioning_design.md`。A層は**読むだけ・非改変**。実装は
`backend/core/versioning/`（`schema.py` / `audit.py` / `releases.py` / `subscriptions.py` /
`notifications.py` / `deletion.py` / `resolver.py` / `worker.py`）+
`backend/api/routes/versioning.py`（実パス `/api/admin/shared/...`）+
`frontend/public/js/versioning.js`（ES5・`window.Versioning`）。

**確定した仕様**: 版（Release）は所有者の明示発行のみ（下書き編集は発行するまで共有先に見えない）/
削除猶予は所有者が予約時に指定（既定14日、`DEFAULT_GRACE_DAYS`）。期限（`purge_after`）後に
全ユーザーから物理削除 / 消費側は fork せず発行版にピン留めして読む（同意（adopt）するまで
内容が変わらない）。editor は working copy を編集できるが発行・削除はできず、常に HEAD を読む
（ピンしない）。

- **DB（migration 037）**: `shared_versions`（不変 Release。`object_type CHECK('course','document')` /
  `object_id TEXT`（ポリモーフィック・FK なし）/ `version_no` / `snapshot JSONB` /
  `UNIQUE(object_type,object_id,version_no)`）、`shared_version_state`
  （`PRIMARY KEY(object_type,object_id)`。`active_release_id` / `latest_version_no` /
  `lifecycle CHECK('active','pending_deletion','purged')` / 削除予約情報）、
  `shared_version_subscriptions`（消費者のピン。`UNIQUE(object_type,object_id,subscriber_id)` /
  `pinned_release_id`）、通知インボックス（当初は専用テーブル `share_notifications` として実装
  されたが、アーキテクチャ整理 Tier 3-15（migration 045）で状態管理・通知基盤の
  `user_notifications` に統合済み。V層由来行は `source='shared'` で区別し、`kind`/`release_id`/
  `acted_at` 列は `user_notifications` へ移設されている。下記 API・フロントの挙動・列名の意味は
  不変）。既存 `component_citations`（migration 021）に引用の版固定列
  （`source_object_type` / `source_object_id` / `source_release_id` / `source_version_no`）を追加。
  document の版は成果物を複製せず、既に不変な `document_analysis_runs.stage_outputs` を指す
  `analysis_run_id` をピンする（既存資産の再利用）。
- **API**（`routes/versioning.py`、`/api/admin/shared/...`、全 endpoint `_require_teacher`）:
  `POST /shared/{object_type}/{object_id}/releases`（発行、所有者限定）/
  `GET /shared/{object_type}/{object_id}/releases`・`GET /shared/releases/{release_id}`（版一覧・単一）/
  `GET /shared/{object_type}/{object_id}/version-state`（版状態 + 更新あり/削除予定バッジ）/
  `POST|DELETE /shared/{object_type}/{object_id}/deletion`（削除予約・取消、所有者限定）/
  `POST /shared/{object_type}/{object_id}/subscription/adopt`（取り込み、`expected_pinned_release_id`
  楽観ロック・不一致 409）/ `GET /shared/subscription/me`（本人のピン一覧）/
  `GET /shared/notifications`・`POST /shared/notifications/{id}/read`・`.../read-all`（インボックス）。
  学習者向けは `GET /api/learning/courses/{course_id}/version-notice`（削除予定の一行バナー、fail-open）。
  エラーは `PurgedError`→410 / `PendingDeletionError`・`AdoptConflictError`→409 /
  `VersioningError`→422 にマッピングする。
- **読み取りの版解決**: コースは `services._apply_course_version_view()` に一元化し、学習者の
  全読み取り経路（チャット・lecture・atlas_view 等）が必ず通す。所有者・editor は HEAD（live
  working copy）、純 viewer・学習者は有効な版（ピン or `active_release_id`）のスナップショットを見る。
  版未発行のコースは live へフォールバック（既存挙動・後方互換）。document 成果物の読み取り
  エンドポイント自体のピン凍結ブラウズは v1 未実装（既知の限界。現行は「更新ありバッジ + 通知 +
  引用時の版固定（auto-pin）」で保護する）。
- **物理削除（`purge_object`）**: 1オブジェクトを独立トランザクションで冪等削除し、course/document
  それぞれの既存 orphan（D層 FK-less 孤児含む）まで削除範囲に含める。既存の即時削除
  `delete_material`/`delete_course` 自体は非改変のまま、削除後に `teardown_versioning()` を
  best-effort で呼んで版・ピン・通知を掃除し、state を `purged` 墓標として残す。
- **スイーパ**（`core/versioning/worker.py`）: `main.py _lifespan` で migration 適用後に
  `threading.Thread` daemon として起動（`VERSION_SWEEPER_ENABLED` 既定 on、
  `VERSION_SWEEP_INTERVAL_SECONDS` 既定 3600）。`lifecycle='pending_deletion' AND
  delete_purge_after<=now()` を検出し、権限が消える前に宛先を収集 → `purge_object` →
  `deleted` 通知配信 → 監査。
- **監査**: 発行・削除予約/取消/purge・取り込みを `theory_review_events`
  （`entity_type='shared_release'|'shared_deletion'|'shared_subscription'`）に記録。
- **フロント**: `versioning.js` の `openModal()`（発行・版履歴・削除予約/取消）+ `initInbox()`
  （右下の通知ベル🔔・未読バッジ）。`admin.js` の教材管理行/コース管理（所有者行）に「共有版」
  ボタン。`app.js`（学習者）は受講コースが削除予約中なら猶予バナーを表示するのみ（ピン UI なし）。
- **ガードレール**: `backend/tests/test_shared_versioning_guardrails.py`（core が FastAPI 非 import・
  所有者ガード・purge の orphan gap 解消・スイーパの thread+env・監査語彙・ルータ登録）他、
  `test_shared_versioning_{migration,api,logic}.py`。

### 個人知識ネットワーク（Personal Knowledge Network, Phase P + P-0.5〜P-3）

学習者本人の確定痕跡（tension / 帰属付き問い / 再構成成功 / connect した橋）から
**決定論的に導出される**個人の知識ネットワーク。正本は
`docs/features/personal_knowledge_network_design.md`（§16 に P-0.5〜P-3 の意味論移行）。
親文書は `knowledge_network_vision.md`（KN-1〜4）。**保存物ではなく毎回導出**（PN-2）・
**本人のみ可視**（PN-1、教員向けは Phase B の k-匿名橋候補集約
`GET /api/admin/courses/{id}/bridge-insights` のみ）・candidate を数えない（PN-3）・
数値を見せない（PN-4）・旅は非LLM/境界付き/明示操作のみ（PN-5）・同一性リンクは
confirmed のみ（PN-6）・fail-closed（PN-7）。migration 不要（既存テーブルの読みのみ）。

- **所有単位は本人（P-0.5, 2026-07-16）**: 個人ネットワークの所有は常に `user_id`。
  `course_id` は所有境界ではなく **provenance（出所）+ フィルター**。正本 API は
  `GET /api/me/personal-network`（`{nodes, edges, anchor_groups, courses}`。
  `include_candidate_links=true` は 422 の fail-closed）と
  `GET /api/me/personal-network/journey?node_id=`。コース配下
  `GET /api/learning/courses/{id}/personal-network(...)` は「コースビュー」＝互換。
  コース削除後も本人の痕跡はノードとして残る（タイトルが引けなくなるだけ）。
- **実装**: `backend/core/personal_graph/`（schema / queries / derive / journey / graph_data /
  bridges。FastAPI 非 import・DB 読みは queries.py に集約）+ `routes/personal_map.py`
  （`router`=コースビュー / `me_router`=正本。**両方とも読み取り専用** — 書き込みAPIを
  ここに作らない。ガードレールで固定）。導出純粋部（build_network /
  build_person_network / build_journey / build_person_journey）は fake rows のみで
  テスト可能（sqlalchemy 遅延 import）。
- **ノード導出規則の正本は設計書 §2**: tension は `TENSION_OWNED_STATUSES` +
  connect 済みは `payload.connected_refs` のみアンカー化（LLM 候補由来の `target_refs` は
  使わない・PN-3）。question は本人確定 structure_anchor（llm_candidate 不使用・topic 縮退）。
  reconstruction は revision チェーン終端の match + 非異議（opt-out 同意汲み取り）。
  `payload.map_excluded` truthy な trace は導出から除外。
- **旅（journey）**: 本人ノード→[1]論文ローカルグラフ→[2]confirmed 同一性リンク→
  [3]L層ハブ(active のみ)→他インスタンス→[4]atlas 骨格→[5]本人の別ノード。
  fan-out≤5・step≤12・事実文のみ。コーススコープの旅は当該コース sources 内限定 +
  `cross_course_hint`（別コースに同一アンカーの兄弟がいれば「以前の学習につながる道が
  あります」だけを返す。詳細は本人が開くまで伏せる）。コース横断版
  （`journey_for_person_node`）は can_view_document で hop を個別 fail-closed
  フィルタし、別コース兄弟の事実文にコース名を含める（出所を失わない）。
- **訂正操作（提案書 §6）**: `POST /api/learning/traces/{trace_id}/map-exclude` /
  `.../map-restore`（routes/learning.py。`payload.map_excluded` の状態遷移のみで
  **status・行は触らない**。dismiss とは独立。監査は既存カタログ定数
  tension/structure_anchor で記帳）。`GET .../interest-traces` 各項目に `map_excluded`。
- **フロント**: `personal-map.js`（コースビュー: atlas トグル・kind 別ドット・
  「まだ地図にない」トレイ・旅カード + cross_course_hint 導線 + 「地図には反映しない」
  「地図に戻す」）と `personal-map-home.js`（P-3 最上位「わたしの地図」パネル。
  ヘッダ `#my-map-btn` → いまの地図 / 問いからの旅 / 振り返り の3タブ +
  `/api/me/...` の旅カード。常設注記「この地図はあなたにだけ表示されます。成績評価には
  使用されません。」・ポーリング禁止・数値/進捗/ゲーミフィケーション表示禁止）。
- **ガードレール**: `test_personal_graph_guardrails.py`（core 非 FastAPI・読み取り専用・
  connected_refs・confirmed のみ・Phase B の k=3 は `core/privacy.py` 正本）+
  `test_personal_graph_{derive,person_scope,journey,journey_person,map_ops}.py` +
  `test_personal_map_{ui_guardrails,home_ui_static}.py`。

### 要素検討ワークスペース（W層, migration 048〜050）

一度パイプラインで処理された**任意の1要素**（figure / theory_component / theory_claim /
equation / 共通部品）を選び、内訳・文脈を確認し AI と対話し、解釈を**候補として**付与する
横断ハブ。正本は `docs/features/element_deliberation_workspace_design.md`（親文書
`knowledge_network_vision.md`。KN-1〜4 の不変条項に従う）。Phase 0/1/W-β/2/S 実装済み。
「E層」は Exposition Layer が占有しているため W層（Workspace）。Field Atlas とは別機能
（`deliberation-` / `element-` プレフィックスで衝突回避）。教員（TEACHER 以上）のみ。
実装は `backend/core/deliberation/`（refs / decomposition / positioning / dialogue /
annotations / store / identity_links / standardization/。FastAPI 非 import）+
`backend/api/routes/deliberation.py`（実パス `/api/admin/deliberation/...`）+
`frontend/public/js/deliberation.js`（ES5・`window.Deliberation`）。

**不変条項**: W1 A層非改変（成果テーブルに列を足さず W層専用テーブルに積む）/
W2 確定は人間・AI は候補のみ（対話の解釈は常に `status='candidate'`、`source_backed` を
自動付与しない）/ W3 evidence-based（evidence + reason + confidence、断定せず仮説文体）/
W4 情報を落とさない（対話ログ・候補・却下は削除せず `candidate → committed / dismissed`
遷移。行削除 API なし）/ W5 権限 fail-closed（document-scoped は
`_ensure_document_viewable/editable`、domain-scoped 共通部品は L層の権限モデル）/
W6 同期パスを重くしない（1応答=1 LLM コール、失敗時は非LLM集約へ縮退）/ W7 監査必須
（`entity_type='deliberation'`）/ W8 数値を見せない（confidence は段階ラベル）/
W9 U層計測（`deliberation:chat` / `deliberation:vision` / `deliberation:cross_corpus`）。

- **ElementRef と2スコープ**: `(scope, element_type, element_id, anchor)`。
  `scope='document'`（1論文からの出現: figure=document_figures.id /
  theory_component・theory_claim=DB UUID / equation=equations.json の equation_id —
  テーブル無しのため `stage_outputs` を索く）と `scope='domain'`（共通部品
  `shared_part` = **L層 `library_entries.id`**。W層は共通部品テーブルを新設しない）。
- **3つの面**: ①内訳・同定（`decomposition.py`、A層成果の読み出しのみ。figure は #496 の
  presentation 分類を同梱）②文脈的位置づけ（`positioning.py` の4レンズ = 論文内 /
  コーパス横断（chunk-proxy ベクトル検索・唯一の新下地。閲覧不可 document は route 層
  `_apply_cross_corpus_gate` で除外）/ 分野の地図 / C層承認・D層疑義）③対話的検討
  （`dialogue.py`。figure は vision。`core/llm.py::generate_conversation_turn` で
  マルチターン + 候補注釈を同一コールの structured output で取得。スキーマ検証失敗は
  注釈なしに縮退・LLM 失敗は `degraded:true` の非LLMフォールバック）。
- **DB（migration 049）**: `deliberation_sessions`（対話ログ・追記のみ）/
  `element_annotations`（`kind ∈ {meaning, decomposition, positioning_note, interpretation,
  identity, standardization}`・`status ∈ {candidate, committed, dismissed}`）。
  FK は element_id に張らない（ポリモーフィック）。孤児掃除は document 削除経路に同乗。
- **コミットルーティング（v1 は3経路）**: `interpretation` → C層 explanation
  (`kind='personal'`) / `meaning`・`decomposition` → `theory_components.summary` /
  `teacher_notes` / `identity` → W-β `create_candidate`。`positioning_note` は 422（後続）。
  W層独自の最終格納庫を持たない（既存構造へ返すハブ）。
- **同一性リンク（Phase W-β, migration 048 `element_identity_links`）**: instance ↔
  shared_part の**非破壊リンク**（KN-2。インスタンス側の表記は書き換えず、共通部品側
  `local_expressions` に出所付き表現を追記）。candidate/confirmed/rejected、一意性は
  `instance_document_id` を含む4列（equation の element_id が論文間で衝突しうるため）。
  `confirmed_links_for_document` が P-2 旅 traversal の読み取り正本。閲覧不可 document 由来の
  リンクは一覧から除外し `hidden_count` を正直に返す。
- **標準化判定（Phase S, migration 050）**: `core/deliberation/standardization/`
  （llm_worker 6系統目アダプタ）が三角測量（LLM 事前知識 + L層凍結版類似 + コーパス反復）→
  `aggregate.decide()` の決定論5語彙（`standard / field_standard / emerging_common / novel /
  unknown`）合成。**LLM 単独主張は unknown（幻覚ガード）**。確定は教員 commit のみで
  `library_entries.standardization_status`（migration 050。draft 編集から書けないガバナンス列、
  語彙の正本は `core/library/schema.py`）へ反映。上限 `STDPART_MAX_CALLS_PER_DAY`（既定10）。
- **コスト上限**: `DELIBERATION_MAX_CALLS_PER_SESSION`（既定8）/
  `DELIBERATION_MAX_CALLS_PER_DAY`（既定40）、fast tier 既定（`DELIBERATION_LLM_MODEL`）。
- **フロント導線**: 4要素型すべてに「深く検討」ボタン（`admin.js` 図モーダル・revisions の
  equation 変更、`admin-lecture-studio.js` の論理要素カード・選択中コンポーネント・主張一覧）。
  モーダルは2ペイン（左=内訳+4レンズ / 右=対話+候補注釈カード confirm/dismiss）。figure は
  #496 のモード別解析ペイン切替 + 「AIで図を再解析」（教員指示付き再解析）を持つ。
- **ガードレール**: `test_deliberation_guardrails.py` / `test_deliberation_positioning.py` /
  `test_deliberation_ui_static.py` / `test_deliberation_annotations.py`（FastAPI 非 import・
  candidate-only・削除 API 不在・権限ゲート・confidence 生値非漏洩・A層非改変）。

### 横断基盤（共有ユーティリティ、2026-07 整理で新設）

同型実装のコピペ増殖を止めるための正本モジュール群。**新機能で同種の処理を書くときは
必ずこれらを使う**（正本の所在は `docs/architecture/consolidation_survey_2026-07.md` の
実施記録も参照）。

- **`backend/core/llm_worker/`** — 非同期 LLM worker の共通骨格。`client.py`
  （`BaseJSONLLMClient(model_setting_key)`・`core.llm` 経由で U層計測を維持）/ `repair.py`
  （`run_with_repair(...)`: 1+2回試行、修復失敗時の後処理は `on_repair_failed` 注入で各系統に残す）/
  `cost_gate.py`（`CostGate`(session+daily) / `InMemoryCounterGate`）。フル骨格
  （BaseJSONLLMClient + run_with_repair）は tension / structure_anchor / reconstruction /
  doubt.scope_candidates / doubt.assumption_mining / deliberation.standardization の6系統が利用中。
  ほかに deliberation の対話（`core/deliberation/dialogue.py`。同期パスのため run_with_repair は
  意図的に不使用・縮退方式）と figure_reanalysis が CostGate / resolve_model のみ部分利用する。
  **新系統はコピペせず15〜20行のアダプタで接続すること**。環境変数名・冪等性フラグ・
  トリガー条件・DB 書き込みはドメイン側の責務。
- **`backend/core/privacy.py`** — k-匿名ゲートの正本（`K_ANONYMITY = 3` /
  `meets_k_anonymity` / `bucket_count_range`(3-5 / 6-10 / 11+) 等）。reconstruction/health.py・
  doubt/schema.py・services.py の集計はここに委譲済み（表示文言は各所に残る）。
  **k=3 をリテラルで再定義しない**。
- **監査 entity_type カタログ** — `backend/core/schema.py` の `AUDIT_ENTITY_*` 定数 +
  `AUDIT_ENTITY_TYPES`（26語彙）。`theory_review_events` への記帳は原則
  `services.record_review_event` に委譲する（core 層からの記帳と、呼び出し元トランザクションに
  同乗する `document_pipeline/persistence.py` のみ例外として直接 INSERT を許容。entity_type は
  必ずカタログ定数を使う）。
- **`backend/core/notification_recipients.py`** — 通知宛先解決（所有者 / group member）の共通
  JOIN プリミティブ。宛先集合の方針（status 系 = owner+editor のみ / V層 = viewer+editor・owner 除外）
  は各層に残し、SQL だけを共有する。
- **`services.resolve_document_access(user_id, ref) -> DocumentAccess`** — document の
  view / edit / owner / canonical id を1回で解決する権限判定の入口（`documents.id` と
  `source_path` の両対応）。チャンク単位のループ内で `user_can_view_document` を繰り返し
  呼ばないこと（N+1）。
- **`backend/tests/guardrail_helpers.py`** — ガードレールテスト用共通アサーション
  （`assert_module_tree_does_not_import` / `assert_source_forbids` / `extract_function_source` 等）。
  新しい層のガードレールテストはこれを使って書く。
- **`src/episteme_graph/agents/cartridge_loader.py` / `cartridge_context.py`** —
  agent 側 cartridge 読み込みの正本（上記「カートリッジシステム」参照）。
- **`backend/core/course_data.py`**（Tier 3-18） — `learning_courses.data` の正本スキーマ + アクセサ
  （`course_topics`（フラット）/ `iter_all_topics`（chapters ネスト防御込み）/ `course_sources` /
  `course_source_material_ids` / `course_cartridge_id` / `course_title` / `lecture_studio_settings` /
  `find_course_topic` / `course_atlas_binding_facts` / `validate_course_data`）。
  **course_data への素の dict アクセスを新規に書かない**。モデルは全て `extra="allow"` で
  未知キーを落とさない。atlas binding の判定方針（projector=AND / next_steps=cartridge_id 単独で
  不要）は各層に残る意図的差異 — 走査だけがここに一本化されている。
- **`backend/core/revision_store.py`**（Tier 3-20） — draft/freeze/楽観ロックの共通プリミティブ。
  revision 照合更新と冪等シード取込の**制御フロー**だけを共有し、draft 粒度・freeze 方式・
  status 語彙・セッション規約はドメイン側（atlas_store / library/store）に残す。
  第3の draft/freeze 利用者はコピペせずこれに接続すること。
- **`backend/core/document_pipeline/orchestrator.py` のステージ追加**（Tier 3-19） —
  新ステージは `_stage_<name>(ctx)` 関数 + `_PIPELINE_STEPS` リストへの登録で追加する
  （インライン展開に戻さない）。ステージ間の受け渡しは `PipelineContext` のフィールド。


## 開発ルール

### 1. 環境変数
- シークレット値はハードコードしない。全て環境変数経由（`.env.example` 参照）
- 主要変数: `OPENAI_API_KEY`, `JWT_SECRET`, `ADMIN_PASSWORD`, `MINIO_ACCESS_KEY`

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
- シングルトンパターン: `llm.py`, `storage.py` は `@lru_cache` または同等の初期化済みインスタンスを使用
- PostgreSQL セッションは `core/postgres.py` の `get_session()` を使い、必ず `try/finally` で `session.close()` する

### 5. フロントエンド
- `admin.js` は Vanilla JS (ES5互換) で記述すること（既存コードに合わせる）
- 原稿スタジオ（`ls` 接頭辞の関数群）は `admin-lecture-studio.js` に分離済み（Tier 3-17b。
  ES5・`window.LectureStudio`、公開 API は `init` / `openExportModal` / `getScreenContext`。
  admin-assistant.js と同型の DI 注入、読み込み順は doubt-atlas.js より後・admin.js より前）。
  原稿スタジオの UI 変更はこちらに書く
- `app.js` は ES6+ (const/let, async/await) を使用している
- フレームワーク不使用（Vanilla JS のみ）

### 6. テスト
- `pytest` を使用
- FastAPI / core 用テストは `backend/tests/` に配置
- agents 用テストは `src/tests/agents/<agent_name>/` に配置

### 7. PDF解析Agentの実装ルール
- 実装場所は `src/episteme_graph/agents/<agent_name>/` とする（`backend/` には置かない）
- 各Agentは `agent.py` の `run()` メソッドを公開インターフェースとする
- LLM呼び出しは `llm_client.py` に分離し、`agent.py` から直接LLM SDKを呼ばない
- `CartridgeLoader` / `CartridgeContext` の正本は `agents/cartridge_loader.py` / `agents/cartridge_context.py`。
  各agentディレクトリの `cartridge_loader.py` は正本の薄い再エクスポート（または固有差分のサブクラス）とし、
  実装をコピペしない（import パスの共通インターフェースは維持）
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

**※現行仕様（Issue #133 / migration 011 で更新、クローン方式は廃止済み）**:
上記の `cloned_from` によるコース丸ごとクローン方式は、①マスター更新がクローンに反映されない
（陳腐化）②マスター削除後もクローンが残る（ゴーストデータ）③同一マスターから複数クローンが
増殖する、という技術的負債が判明したため `backend/db/011_course_states_separation.sql` で
廃止された（既存クローンをハードリセットのうえ `learning_courses.cloned_from` カラム自体を
DROP）。現行は「1つの不変なマスターコース（`learning_courses`）+ ユーザーごとの学習状態
（`learning_states`）」の分離方式で、`POST /api/learning/courses/{course_id}/enroll`
（`routes/learning.py::enroll_course`）はコースを複製せず、`services.enroll_user_in_course()`
が `learning_states`（`user_id`, `course_id`, `progress_data`, `personal_graph`,
`UNIQUE(user_id, course_id)` で二重受講を DB レベルで防止）に1行 INSERT するだけになっている。
`app.js` の「受講開始」ボタンが呼ぶ API パス自体は変わっていないが、内部動作はクローン生成
ではなく学習状態の作成である。

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

（※Neo4j は2026-07 のアーキテクチャ整理（Tier 1）で完全撤去済み。現行の `check_prerequisites` は
Neo4j 非依存で、コースデータの `topic.prerequisites` のみを参照する。）

## 実装時の注意事項

- マイグレーションSQLは `backend/db/` に `002_a1_a2_a3.sql` として配置する
  （※これは Priority A 実装時点の記述。以降のマイグレーション運用ルールは次項参照）

### マイグレーションの正本一本化（アーキテクチャ整理 Tier 3-13）

`backend/db/*.sql`（init.sql + 番号順ファイル群）が**唯一の正本**。かつて `backend/api/main.py`
の `_run_migrations()` に約1,600行のインライン DDL が並行して存在したが撤去済みで、現在は
`backend/core/migrations.py` の薄いランナーが **毎起動、番号順に全ファイルを冪等再実行**する
（pg_advisory_lock で多重起動排他・ファイル単位トランザクション）。

- **新しいスキーマ変更は必ず新番号の SQL ファイルを追加する**（既存ファイルの編集は typo や
  冪等性のような「最終状態の是正」に限り、過去に適用済みの意味を変えない）。
- **すべてのファイルは冪等でなければならない**（`CREATE TABLE IF NOT EXISTS` /
  `ADD COLUMN IF NOT EXISTS` / `DO $$ ... $$` の存在確認ガード等）。再起動のたびに全ファイルが
  再実行されるため、非冪等な DDL は次回起動でエラーになるか、既存データを壊す
  （例: 無ガードの `CREATE INDEX` を伴う次元変更は再起動ごとに embedding を全消失させ得る）。
- **`main.py` に DDL を書き戻さない**（DDL の正本を2箇所に増やさない）。
- ガードレールは `backend/tests/test_migrations_runner.py`（冪等性 lint・番号連続性・
  main.py への DDL 再侵入禁止）が構造的に守る。
- 統合系マイグレーション（複数の旧テーブルを1枚に集約するもの。例: migration 044/045）は、
  置換された旧ファイルを空撤去にはせず**コメントのみのスタブ化**に留める（`002_a1_a2_a3.sql` の
  `cloned_from` 列と同じ「最終状態への巻き戻し」パターン。毎起動再実行方式では、旧ファイルを
  削除・空にすると「作って即壊す」往復が起きるため）。
