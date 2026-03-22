# Episteme Graph — 開発支援スキル

## 概要

Episteme Graph の開発を支援するスキル。コアエンジンの抽出・検索機能と
学習UIの連携に関する開発ルールを定義する。

## アーキテクチャ

| モジュール | 役割 | 主要ファイル |
|---|---|---|
| `backend/api/main.py` | 統合APIサーバー | 認証・学習・Admin |
| `backend/core/extractor.py` | PDF構造化抽出 | 仮説検証型チャンク解析 |
| `backend/core/embedder.py` | ベクトル検索 | Qdrant FANNS |
| `backend/core/schema.py` | データスキーマ | Pydanticモデル |
| `backend/core/chat.py` | RAGチャット | コンテキスト検索+LLM |
| `backend/core/storage.py` | ストレージ | MinIO管理 |
| `frontend/public/js/app.js` | 学習UI | SPA ロジック |

## コア抽出フロー

```
PDF → テキスト抽出 (PyMuPDF)
    → チャンク分割
    → 仮説生成 (LLM)
    → チャンク検証ループ (逐次状態更新)
    → PaperStructure 確定
    → Qdrant embedding (並行)
```

## DSLとグラフ構造

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

### ナレッジグラフ JSON スキーマ
```json
{
  "concepts": [
    {"id": "string", "name": "string", "type": "definition|theorem|method|example"}
  ],
  "relationships": [
    {"source": "concept_id", "target": "concept_id", "relation": "CorePredicate"}
  ],
  "chapters": [
    {"title": "string", "topics": [{"id": "string", "title": "string"}]}
  ]
}
```

## 開発時の注意

1. **LLM呼び出し**: OpenAI APIキーは環境変数から取得。ハードコード禁止
2. **Neo4j Cypher**: MERGE を使用してべき等な書き込みを行う
3. **Qdrant**: コレクション `papers` に 3072次元ベクトルを保存
4. **認証**: JWT トークンは `_JWT_SECRET` で署名。全APIエンドポイントで `_get_current_user` を使用
5. **フロントエンド**: Vanilla JS のみ。フレームワーク不使用
