# Episteme Graph — ナレッジグラフ開発スキル

## 概要

教材知識のDSL変換とナレッジグラフ管理に関する開発ルールを定義するスキル。
PDF教材からの知識抽出、グラフ構造の定義、パターンマッチングを対象とする。

## ナレッジグラフ データモデル

### 概念ノード (Concept)
```
(:Concept {
  id: "concept_xxx",
  name: "概念名",
  description: "説明",
  type: "definition|theorem|method|example",
  domain: "分野名"
})
```

### 関係エッジ (Relationship)
```
(:Concept)-[:RELATION {
  relation: "REQUIRES|CONTAINS|CAUSES|DEFINES|EXTENDS|APPLIES_TO",
  description: "関係の説明",
  core_predicate: "CorePredicate列挙値",
  polarity: "+|-|?",
  is_core: true|false
}]->(:Concept)
```

### 教材ノード (Material)
```
(:Material {
  id: "material_xxx",
  filename: "file.pdf",
  title: "教材タイトル",
  status: "uploaded|processing|completed|failed",
  knowledge_graph: "{JSON}",
  text_length: 12345,
  chunk_count: 50
})
```

## PDF処理パイプライン

```
1. Upload (POST /api/admin/materials/upload)
   └── PDFバイト列を受信

2. Text Extraction (PyMuPDF)
   └── 全ページからテキスト抽出

3. Chunking
   └── 1000文字ごと (100文字オーバーラップ)

4. Embedding (OpenAI text-embedding-3-large)
   └── 50チャンクずつバッチ処理 → Qdrant保存

5. Knowledge Graph Construction (LLM)
   └── テキスト先頭8000文字を解析
   └── 概念・関係・章構成を JSON で出力

6. Persistence
   └── Neo4j に Material ノードとして保存
```

## SMILES DSL (抽象構造記述言語)

構造的同型性を表現するための DSL。
`backend/core/schema.py` の `AbstractStructure.smiles_dsl` に格納。

### 記法例
```
A-[CAUSES]->B-[INHIBITS]->C
(Resource)-[TRANSFORMS]->(Product)-[MEASURES]->(Metric)
```

## パターンマッチング

`backend/core/batch.py` で実装。

1. 新しい AbstractionPattern が登録される
2. Qdrant でパターンベクトルに類似する論文を検索
3. LLM が構造的同型性を評価 (信頼度スコア 0.0-1.0)
4. 閾値 0.5 以上で Neo4j に MATCHES_PATTERN エッジを保存

## 開発ガイドライン

1. **スキーマ変更**: `backend/core/schema.py` の Pydantic モデルを先に更新
2. **新しい関係型**: `CorePredicate` 列挙型に追加し、既存コードを更新
3. **抽出精度向上**: `backend/core/extractor.py` のプロンプトを調整
4. **検索精度向上**: `backend/core/embedder.py` のチャンク戦略を調整
