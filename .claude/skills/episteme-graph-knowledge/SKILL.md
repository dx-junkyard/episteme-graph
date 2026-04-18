---
name: episteme-graph-knowledge
description: >
  Episteme Graphのナレッジグラフ、PDF教材処理パイプライン、DSL定義、パターンマッチングに
  関する実装や修正を行う際に使用します。ユーザーから「抽出ロジックを変更して」
  「ナレッジグラフの構造を修正して」「パイプラインを改善して」「DSLを拡張して」
  「パターンマッチングを調整して」などの依頼があった場合に自動的に発動してください。
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
