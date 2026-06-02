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
| ThesisReconstructionAgent | #221 | Skeleton + Claims + Equations | ThesisReconstructionResult | LLM-first |
| DSLLinkingAgent | #222 | Claims + Equations + Thesis | DSLLinkingResult | LLM-first |
| ComponentAssemblyAgent | #223 | Claims + Equations + Thesis + DSL | ComponentAssemblyResult | LLM-first + cartridge-aware |

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

グラフは 2 層に分離する (#306):

- **main 層** (`graph_layer="main"`, `component_type="TheoryOperationNode"`): 上位理論構成。
  式 step を `(derivation_id, operation family)` で集約した少数 node。generic operation
  (`transform`/`relate`/`connect`/`support`/`associate`) は main にしない。
- **equation_detail 層** (`graph_layer="equation_detail"`, `component_type="EquationOperationNode"`):
  derivation step ごとの式単位 node。`parent_component_id` で main node を、main node は
  `member_component_ids` で detail node を相互参照する。generic step は `debug` 層へ。

その他の必須ルール:

- **source-backing を明示**: 各 node は `linked_equation_ids` / `linked_derivation_ids` /
  `linked_claim_ids` / `linked_evidence_ids` と `source_backing_status` を持つ。
- **atomic claim を優先**: 主たる backing は atomic claim（短く evidence_text が非空、paper-level
  でない）を優先。無ければ `review_reasons=["missing_atomic_claim"]`、equation ID だけの label は
  `partially_source_backed`。空 evidence を強い backing にしない。
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

## CI テストパターンの更新（必須）

**ナレッジグラフ関連の変更を行った場合、必ず対応するテストパターンも追加・更新すること。**

実装完了後、`episteme-graph-ci-tests` スキルの手順に従い、以下を実行する:

1. 変更対象に対応するテストファイルが `backend/tests/core/` に存在するか確認
2. スキーマ変更時: バリデーション・シリアライズ・デフォルト値のテストを追加
3. 抽出ロジック変更時: `test_diff_merge.py` のヘルパー (`_make_structure`) が新フィールドに対応しているか確認
4. パターンマッチング変更時: `backend/tests/core/test_batch.py` にテストを追加
5. `cd backend && python -m pytest tests/ -v` でテストが通ることを確認

テストの配置規則・コード規約・設計原則の詳細は `episteme-graph-ci-tests` スキルを参照。
