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
| `chunks` | テキストチャンク + embedding (pgvector 3072次元) + DSL メタデータ |

- ベクトル検索: `chunks.embedding` カラム (pgvector cosine distance)
- ナレッジグラフ構造: `documents.knowledge_graph` (JSONB)
- DSL 情報: `chunks.smiles_dsl`, `chunks.variables`, `chunks.ancestors` (JSONB)

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

## 開発ガイドライン

1. **スキーマ変更**: `backend/core/schema.py` の Pydantic モデルを先に更新し、`backend/core/models.py` の ORM モデルも同期する
2. **新しい関係型**: `CorePredicate` 列挙型に追加し、既存コードへの影響を確認
3. **抽出精度向上**: `backend/core/extractor.py` のプロンプトを調整
4. **検索精度向上**: `backend/core/embedder.py` のチャンク戦略・クエリ戦略を調整
5. **LLM 呼び出し**: 必ず `core/llm.py` の公開 API (`generate_text`, `generate_embeddings`, `generate_text_with_structured_output`) を経由する。`openai` SDK を直接使用しない
6. **設定値**: `core/config.py` の `get_settings()` を経由する。`os.environ` を直接使用しない
