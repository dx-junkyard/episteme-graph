---
name: episteme-graph-knowledge
description: >
  Episteme Graphのナレッジグラフ、PDF教材処理パイプライン、DSL定義、パターンマッチング、
  およびPDF解析Agentパイプライン（DocumentStructureAgent / PaperSkeletonAgent /
  RhetoricalRoleAgent / ClaimQualificationAgent / EquationSemanticsAgent /
  ThesisReconstructionAgent / DSLLinkingAgent / ComponentAssemblyAgent /
  ComponentAssemblyAgentが生成する理論操作グラフ TheoryOperationGraph (ComponentGraphAgent)）に
  関する実装や修正を行う際に使用します。ユーザーから「抽出ロジックを変更して」
  「ナレッジグラフの構造を修正して」「パイプラインを改善して」「DSLを拡張して」
  「パターンマッチングを調整して」「Agentを実装して」「カートリッジを更新して」
  などの依頼があった場合に自動的に発動してください。
---
# Episteme Graph — ナレッジグラフ開発スキル

## 概要

教材知識の DSL 変換とナレッジグラフ管理に関する開発ルールを定義するスキル。
PDF 教材からの知識抽出、グラフ構造の定義、パターンマッチングを対象とする。

## データストア構成

すべてのデータは **PostgreSQL (pgvector & JSONB)** に統合されている。

| テーブル | 役割 |
|---|---|
| `documents` | 教材メタデータ (タイトル、著者、ステータス、knowledge_graph JSONB) |
| `chunks` | テキストチャンク + embedding (pgvector) + DSL メタデータ |

- ベクトル検索: `chunks.embedding` カラム (pgvector cosine distance)
- ナレッジグラフ構造: `documents.knowledge_graph` (JSONB)
- DSL 情報: `chunks.smiles_dsl`, `chunks.variables`, `chunks.ancestors` (JSONB)

### Embedding 次元数に関する注意事項

pgvector の次元数は LLM プロバイダと Embedding モデルによって異なる。
**次元数は絶対にハードコードしない**。必ず `core/llm.py` の `get_embedding_dim()` または `settings.llm_embedding_dim` を参照すること。

| LLM プロバイダ / モデル | Embedding モデル | 次元数 |
|---|---|---|
| OpenAI `text-embedding-3-large` | `LLM_PROVIDER=openai` のデフォルト | **3072** |
| Gemini `text-embedding-004` | `LLM_PROVIDER=gemini` で典型的に使用 | **768** |
| Gemini `gemini-embedding-exp-03-07` | 高精度 Gemini Embedding | **3072** |

```python
# NG — 次元数ハードコード禁止
ALTER TABLE chunks ADD COLUMN embedding vector(3072);
pgvector_query = "SELECT * FROM chunks ORDER BY embedding <=> $1::vector(3072)"

# OK — 設定値から取得する
from core.llm import get_embedding_dim
dim = get_embedding_dim()  # settings.llm_embedding_dim の値を返す
```

DBスキーマ変更・マイグレーション作成時は `LLM_EMBEDDING_DIM` の値を確認し、現在の次元数に合わせた SQL を生成すること。

データモデルの最新定義は `backend/core/models.py` の `Document` / `Chunk` クラスを直接読んで確認すること。

## PDF 処理パイプライン

```
1. Upload (POST /api/admin/materials/upload)
   └── PDF バイト列を受信

2. Text Extraction (PyMuPDF)
   └── 全ページからテキスト抽出
   └── GROBID が利用可能なら TEI-XML パースを優先

3. Chunking
   └── テキストをチャンク分割

4. Embedding (core/llm.py → generate_embeddings)
   └── バッチ処理 → PostgreSQL chunks テーブルに pgvector で保存

5. Knowledge Graph Construction (core/llm.py → generate_text)
   └── LLM でテキストを解析
   └── 概念・関係・章構成を JSON で出力

6. Persistence
   └── documents テーブルに knowledge_graph (JSONB) として保存
   └── 抽出構造を MinIO (extracted-structures バケット) に保存
```

実装の詳細は `backend/core/extractor.py` と `backend/core/embedder.py` を直接読んで確認すること。

## SMILES DSL (抽象構造記述言語)

構造的同型性を表現するための独自 DSL。
`backend/core/schema.py` の `AbstractStructure.smiles_dsl` に格納。

### 記法
```
(varID:OntologyType:value) ==[CorePredicate:verb:polarity]=> (...)
```

### CorePredicate (概念間の関係型)
- `CAUSES`: 因果関係
- `INHIBITS`: 抑制関係
- `CORRELATES`: 相関関係
- `DEFINES`: 定義関係
- `MEASURES`: 測定関係
- `TRANSFORMS`: 変換関係
- `REQUIRES`: 依存関係
- `CONTAINS`: 包含関係
- `EQUIVALENT`: 同値関係

CorePredicate の定義は `backend/core/schema.py` を直接読んで最新の列挙値を確認すること。

## パターンマッチング

`backend/core/batch.py` で実装。

1. 新しい AbstractionPattern が登録される
2. PostgreSQL pgvector でパターンベクトルに類似する論文チャンクを検索
3. LLM が構造的同型性を評価 (信頼度スコア 0.0-1.0)
4. 閾値以上で結果を保存

実装の詳細は `backend/core/batch.py` を直接読んで確認すること。

## LLM プロバイダ環境変数仕様

ナレッジグラフ・パイプライン実装時に参照が必要な主要環境変数:

| 環境変数 | 説明 | デフォルト値 |
|---|---|---|
| `LLM_PROVIDER` | LLM バックエンド (`openai` / `gemini`) | `openai` |
| `LLM_API_KEY` | API キー（`OPENAI_API_KEY` / `GEMINI_API_KEY` も可） | — |
| `LLM_ANALYSIS_MODEL` | テキスト生成に使用するモデル名 | `o3-mini` |
| `LLM_EMBEDDING_MODEL` | Embedding に使用するモデル名 | `text-embedding-3-large` |
| `LLM_EMBEDDING_DIM` | Embedding ベクトル次元数（pgvector スキーマと一致が必要） | `3072` |
| `TENSION_MAX_CALLS_PER_SESSION` | TensionMiningAgent: 1セッションあたり LLM コール上限 | `3` |
| `TENSION_MAX_CALLS_PER_DAY` | TensionMiningAgent: 1ユーザー1日あたり LLM コール上限 | `10` |
| `TENSION_LLM_MODEL` | TensionMiningAgent が使うモデル（空なら fast tier に委譲） | （空） |
| `ANCHOR_MAX_CALLS_PER_SESSION` | StructureAnchorAgent: 1セッションあたり LLM コール上限 | `3` |
| `ANCHOR_MAX_CALLS_PER_DAY` | StructureAnchorAgent: 1ユーザー1日あたり LLM コール上限 | `10` |
| `ANCHOR_LLM_MODEL` | StructureAnchorAgent が使うモデル（空なら fast tier に委譲） | （空） |
| `ANCHOR_CONFIRM_MAX_PER_SESSION` | 回答末尾の帰属確認プロンプト（C）を出すセッション内上限 | `3` |
| `DOUBT_SCOPE_MAX_CALLS_PER_DAY` | D層: 検証スコープ候補抽出の 1 日あたり LLM コール上限 | `10` |
| `DOUBT_SCOPE_LLM_MODEL` | D層: スコープ候補抽出が使うモデル（空なら fast tier に委譲） | （空） |
| `DOUBT_ASSUMPTION_MAX_CALLS_PER_DAY` | D層: 暗黙前提の LLM 正規化の 1 日あたりコール上限 | `10` |
| `DOUBT_ASSUMPTION_LLM_MODEL` | D層: 前提正規化が使うモデル（空なら fast tier に委譲） | （空） |
| `LLM_TRANSCRIBE_MODEL` | 音声文字起こしモデル（ハンズフリー会話、openai プロバイダのみ） | `whisper-1` |
| `ASSISTANT_MAX_CALLS_PER_DAY` | Admin Copilot: chat の 1 ユーザー 1 日あたり LLM コール上限 | `20` |
| `ASSISTANT_LLM_MODEL` | Admin Copilot: intent 分類/応答が使うモデル（空なら fast tier に委譲） | （空） |

> **禁止**: `google-cloud-aiplatform` (Vertex AI) を新規パイプラインコードで使用すること。
> Google の LLM を使う場合は必ず `LLM_PROVIDER=gemini` (`google-generativeai`) を指定すること。

### OpenAI → Gemini のロールマッピング

`core/llm.py` はプロバイダ間のロール変換を内部で自動処理する。
パイプライン実装時は OpenAI 形式のメッセージリストをそのまま渡してよい。

```
OpenAI 形式                      → Gemini への変換
─────────────────────────────────────────────────────
{"role": "system",  "content": "..."} → GenerativeModel(system_instruction="...")
{"role": "user",    "content": "..."} → contents[{"role": "user",  "parts": [...]}]
{"role": "assistant","content": "..."} → contents[{"role": "model", "parts": [...]}]
```

この変換は `core/llm.py` 内部で行われるため、`extractor.py` / `chat.py` / `batch.py` 側での分岐は不要。

## レクチャー生成とアセスメント拡張パイプライン (新規追加)

テキストチャンクから講義用スクリプト（`lecture.py` 等）や構造を生成する際、以下の拡張要素をLLMに推論させるパイプラインを構築する。

1. **アセスメント（問題）生成**:
   学習者の理解度を測るため、テキストチャンクの主要概念に基づくクイズ（選択肢、正解、解説を含むJSON構造）を生成させる。生成した問題データはスキーマ（例: `AssessmentItem`）に従ってパースし、DBに永続化する。
2. **チャート（視覚化）生成**:
   物理法則や概念の依存関係など、図解が有効なセグメントにおいては、テキストのみに頼らず `Mermaid.js` 等のDSLによるチャートコードを生成させる。出力は数式と同様に `[[CHART_0]]` のようなプレースホルダーで本文と分離し、メタデータ配列として抽出する。

※ 上記のプロンプトを設計する際は、出力フォーマット（JSON）が崩れないよう、構造化出力（`generate_text_with_structured_output` または JSONモード）を積極的に活用すること。


## PDF解析エージェントパイプライン（src/episteme_graph/agents/）

ドキュメントアップロード後、コース作成と切り離した形で実行されるagent群。
実装場所は `src/episteme_graph/agents/<agent_name>/`（`backend/` には置かない）。

### パイプライン構成と依存関係

| Agent | Issue | 入力 | 出力 | 設計方針 |
|---|---|---|---|---|
| DocumentStructureAgent | #216 | PDF | DocumentStructureResult | structure-first, parser-driven |
| PaperSkeletonAgent | #217 | DocumentStructureResult | PaperSkeletonResult | LLM-first |
| RhetoricalRoleAgent | #218 | Structure + Skeleton | RhetoricalRoleResult | LLM-first |
| ClaimQualificationAgent | #219 | Structure + Skeleton + Roles | ClaimQualificationResult | LLM-first |
| EquationSemanticsAgent | #220 | Structure + Skeleton + Roles | EquationSemanticsResult | LLM-first |
| SymbolRegistryBuilder | #355 | EquationSemanticsResult | SymbolRegistryResult | 非LLM, deterministic（表記ゆれ正規化・redefinition 検出・scope 導出） |
| ThesisReconstructionAgent | #221 | Skeleton + Claims + Equations | ThesisReconstructionResult | LLM-first |
| DSLLinkingAgent | #222 | Claims + Equations + Thesis | DSLLinkingResult | LLM-first |
| ComponentAssemblyAgent | #223 | Claims + Equations + Thesis + DSL | ComponentAssemblyResult | LLM-first + cartridge-aware |
| NarrativeAnnotator | #360 | ComponentGraphResult + Thesis + Derivations | NarrativeAnnotationResult | LLM-first（annotation のみ、graph 構造は変更しない・全出力 provisional） |

### 各Agentの標準ファイル構成

```
src/episteme_graph/agents/<agent_name>/
  __init__.py
  agent.py           → クラス定義と run() メソッド
  cartridge_loader.py → CartridgeLoader（cartridge_id → CartridgeContext）
  input_builder.py   → 前段agentの出力 → LLM入力への変換
  prompt.py          → system/user promptの構築
  llm_client.py      → OpenAI structured output呼び出し
  schema.py          → 入出力dataclass定義（JSONシリアライズ可能）
  validator.py       → 出力スキーマ検証・consistency check
  repair.py          → validation失敗時の再試行ロジック
  examples/          → サンプル入出力JSON
```

### CartridgeLoader 共通パターン

各agentは独自の `CartridgeLoader` を持つが、同一インターフェースを維持する:

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

class CartridgeLoader:
    def load(self, cartridge_id: str) -> CartridgeContext:
        # backend/cartridges/<cartridge_id>/ から JSON を読み込む
        ...
```

カートリッジファイルは `backend/cartridges/<cartridge_id>/` に配置:
- `ontology.json` — concept types / aliases / notation_patterns / normalization_hints
- `validation_rules.json` — フィールド妥当性チェック
- `component_types.json` — 許可component type語彙（ComponentAssemblyAgentが使用）
- `relation_types.json` — dependency / connector 語彙

### Agent実装ルール

1. **cartridge-aware だが cartridge-dependent ではない**
   - cartridge_id が None でも単独動作すること
   - cartridge の hints は補助信号として使い、主軸のロジックはcartridgeに依存しない

2. **LLM-first agents（#217〜#223）の必須パターン**
   ```python
   # Step 1: input packaging（非LLM）
   llm_input = input_builder.build(structure, skeleton, cartridge, config)
   # Step 2: LLM structured output
   raw_output = llm_client.generate(prompt_factory.build_messages(llm_input, cartridge), schema)
   # Step 3: validation
   issues = validator.validate(raw_output, cartridge)
   # Step 4: repair/retry if needed
   if issues:
       result = repairer.repair(llm_input, raw_output, issues, cartridge)
   ```

3. **domain-specific ロジックのハードコード禁止**
   - 概念名・記号名・validation ルールを agent 内に直書きしない
   - 必要な hints は active cartridge から読む

4. **出力フィールドの必須要件**
   - `reason`: なぜその判断をしたかの根拠
   - `confidence`: 0.0〜1.0 の確信度
   - maturity / review_status の最終確定禁止（provisional のみ）

5. **DocumentStructureAgent (#216) の特別ルール**
   - structure-first（parser / layout signal 優先）
   - 意味解釈・claim解釈は行わない
   - 曖昧blockのみLLM補助判定に回す
   - 不明blockは `unknown` で保持し削除しない

### テスト配置（agents用）

```
src/tests/agents/<agent_name>/
  test_agent.py      → agent.run() の統合テスト（LLMはmock）
  test_validator.py  → validator の単体テスト
  test_schema.py     → schemaのserialize/deserializeテスト
```

実行コマンド:
```bash
cd src && python -m pytest tests/ -v
```

### TheoryOperationGraph の層構造 (ComponentGraphAgent / #266・#302・#306)

`component_graph/normalizer.py` は DerivationChain から **domain-independent** な理論操作グラフを
決定論的に構築する。node label / edge type は `schema.classify_operation()` が `operation` の
prefix から導出し、特定分野・特定論文の用語をハードコードしない。

グラフは 2 層に分離する (#306 / #308):

- **main 層** (`graph_layer="main"`, `component_type="TheoryOperationNode"`): 上位理論構成。
  式 step を **theory stage** で集約した少数 node（5–8 個程度のバックボーン）。stage は
  `schema.stage_for_edge_type()` が operation の edge_type family から domain-neutral に導出し
  (`defines→theory_basis` / `constructs・normalizes→observable_construction` /
  `linearizes・approximates・substitutes→equation_system` / `solves・eliminates→elimination` /
  `derives・constrains→consistency_relation` / `diagnoses・compares→diagnostic_application`)、
  全 derivation を跨いで集約する。stage 語彙は `schema.THEORY_STAGES`、表示名は
  `schema.THEORY_STAGE_LABELS`。main label は短い stage label **そのもの**を使い、atomic claim/reason
  のような長い説明は label に詰めず node の `description` フィールドへ入れて UI 詳細ペインで表示する
  （`Stage: 長い説明` の形にはしない）。**equation ID fallback は使わない**ので `Define eq_...` /
  `Derive result eq_...` は main に出ない。generic operation
  (`transform`/`relate`/`connect`/`support`/`associate`) も main にしない。validator は main node の
  equation-id label・generic operation を hard error として検出する (#308)。
- **equation_detail 層** (`graph_layer="equation_detail"`, `component_type="EquationOperationNode"`):
  derivation step ごとの式単位 node（ここでは equation ID 入り label を許容）。`parent_component_id` で
  main node を、main node は `member_component_ids` で detail node を相互参照する。generic step は
  input/output 両方の式 backing があれば equation_detail 層に `partially_source_backed` +
  `review_reasons=["generic_operation"]` で残し、式 backing が無い場合のみ `debug` 層へ (#361)。
- **ComponentRefiner は「1 component = 1 reusable theory unit」(#308)**:
  `component_assembly/component_refiner.py` は derivation step を **operation family** 単位の
  再利用可能な理論ユニットに分割する（式 step 1 個 = 1 component ではない）。同 family の複数 step は
  1 component に集約し `internal_flow` に保持する。generic operation だけでは child component を作らず、
  近接ユニットの `internal_flow` に畳み込む。component の label/summary は generic 操作名ではなく
  理論対象を表す。

その他の必須ルール:

- **source-backing を明示**: 各 node は `linked_equation_ids` / `linked_derivation_ids` /
  `linked_claim_ids` / `linked_evidence_ids` と `source_backing_status` を持つ。各 edge も node と
  同じ語彙の `source_backing_status`（`source_backed` / `partially_source_backed` / `inferred` /
  `review_required`）を持ち、`review_status` はそこから `review_status_for_backing()` で導出する (#311)。
- **atomic claim を優先**: 主たる backing は atomic claim（短く evidence_text が非空、paper-level
  でない）を優先。無ければ `review_reasons=["missing_atomic_claim"]`、equation ID だけの label は
  `partially_source_backed`。空 evidence を強い backing にしない。evidence link で `source_backed`
  になった node でも atomic claim が無ければ `missing_atomic_claim` warning を残す。
- **review_status は backing から導出**: `schema.review_status_for_backing()` を使い、全 node を
  一律 `teacher_review_required` にしない。`review_required` の node/edge は `review_reasons` を
  空にしない。
- **UI (`admin.js`)**: 層トグル（主グラフ / 式の詳細 / すべて）で `graph_layer` を切り替え、既定は
  main を優先表示する。

## 開発ガイドライン

1. **スキーマ変更**: `backend/core/schema.py` の Pydantic モデルを先に更新し、`backend/core/models.py` の ORM モデルも同期する
2. **新しい関係型**: `CorePredicate` 列挙型に追加し、既存コードへの影響を確認
3. **抽出精度向上**: `backend/core/extractor.py` のプロンプトを調整
4. **検索精度向上**: `backend/core/embedder.py` のチャンク戦略・クエリ戦略を調整
5. **LLM 呼び出し**: 必ず `core/llm.py` の公開 API (`generate_text`, `generate_embeddings`, `generate_text_with_structured_output`) を経由する。`openai` / `google.generativeai` SDK を直接使用しない
6. **設定値**: `core/config.py` の `get_settings()` を経由する。`os.environ` を直接使用しない
7. **Embedding 次元数**: `get_embedding_dim()` または `settings.llm_embedding_dim` を使用し、絶対にハードコードしない

## 実装前のドキュメント更新（条件付き必須）

以下に該当する変更を行う場合、**実装を開始する前に** 対応する CLAUDE.md または SKILL.md を更新すること。
AI アシスタントは各セッション開始時にこれらのファイルを設計書として読むため、実装前に更新することで
設計意図と実装が一致し、手戻りを防ぐ。

### 更新が必須のケース

| 変更の種類 | 更新対象 |
|---|---|
| 新しい Agent の追加 | CLAUDE.md のパイプライン構成表 + 本 SKILL.md の Agent 一覧 |
| DSL / CorePredicate の変更 | 本 SKILL.md の SMILES DSL セクション |
| パイプラインの依存関係・順序の変更 | CLAUDE.md のパイプライン概要 + 本 SKILL.md のパイプライン構成表 |
| TheoryOperationGraph の設計ルール変更 | CLAUDE.md の該当セクション + 本 SKILL.md の層構造セクション |
| 新しい cartridge ファイル構成の変更 | CLAUDE.md のカートリッジシステムセクション |

### 更新が任意のケース

- 既存 Agent の内部ロジック修正（インターフェース変更なし）
- バグ修正・パフォーマンス改善のみ
- テストの追加・修正のみ

### 更新手順

1. 実装予定の変更内容を把握する
2. 影響する SKILL.md のセクション（パイプライン構成表・Agent 一覧・層構造ルール等）を特定する
3. 実装内容を反映した形でドキュメントを更新する
4. 更新後に実装を開始する

## CI テストパターンの更新（必須）

**ナレッジグラフ関連の変更を行った場合、必ず対応するテストパターンも追加・更新すること。**

実装完了後、`episteme-graph-ci-tests` スキルの手順に従い、以下を実行する:

1. 変更対象に対応するテストファイルが `backend/tests/core/` に存在するか確認
2. スキーマ変更時: バリデーション・シリアライズ・デフォルト値のテストを追加
3. 抽出ロジック変更時: `test_diff_merge.py` のヘルパー (`_make_structure`) が新フィールドに対応しているか確認
4. パターンマッチング変更時: `backend/tests/core/test_batch.py` にテストを追加
5. `cd backend && python -m pytest tests/ -v` でテストが通ることを確認

テストの配置規則・コード規約・設計原則の詳細は `episteme-graph-ci-tests` スキルを参照。
