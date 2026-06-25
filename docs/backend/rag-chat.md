# RAG チャットフロー

[← ドキュメント目次](../README.md) ｜ [← コアエンジン](core-engine.md)

学生の質問に答える RAG（Retrieval-Augmented Generation）の流れを解説します。
学習チャットのエンドポイントは `POST /api/learning/courses/{cid}/topics/{tid}/chat`（`api/routes/learning.py`）、
教材単体に対する基本 RAG ロジックは `backend/core/chat.py` にあります。

---

## 1. 全体フロー

```
ユーザー質問
  │
  ① 質問を埋め込みベクトル化            … chat.py: _embed_query()
  │
  ② pgvector で関連チャンク検索          … chat.py: search_chunks() (top_k=5)
  │   ORDER BY embedding <=> query_vec, material_id で絞り込み
  │
  ③ MinIO から PaperStructure を取得     … chat.py: _get_paper_structure()
  │   （title / abstract_structure(変数・エッジ・SMILES DSL) / methodology …）
  │
  ④ プロンプト組み立て                   … chat.py: generate_chat_response()
  │   system プロンプト + 関連チャンク + PaperStructure + 会話履歴
  │
  ⑤ LLM 生成（temperature=0.3）          … llm.py: generate_text()
  │   末尾にドリルダウンリンクを Markdown で提示
  │
  ⑥ 誤解検出 → 個人レイヤーへ記録        … learning.py + detect_and_record_misconception()
  │
  ⑦ 前提知識チェック・学習支援アクション … learning_support_agent.py
  ▼
回答 + next_actions + course_update（誤解・アンカー）
```

---

## 2. 各ステップ詳細

### ① 質問の埋め込み（`chat.py:_embed_query`）
質問文を `generate_embeddings([question])` で 1 本のベクトル（次元 = `LLM_EMBEDDING_DIM`、既定 3072）に変換します。

### ② ベクトル検索（`chat.py:search_chunks`）
`chunks` テーブルに対し pgvector の距離演算子（`<=>`, cosine）で類似度検索し、上位 `top_k`（既定 5）チャンクを取得します。`material_id` でコース/論文に絞り込みます。

```sql
... ORDER BY embedding::halfvec(3072) <=> :query_vector LIMIT :top_k
```

### ③ PaperStructure 取得（`chat.py:_get_paper_structure`）
MinIO `extracted-structures/{paper_id}.json` から抽出済み構造を読み込みます。
`abstract_structure`（変数・因果エッジ・SMILES DSL）を含み、概念どうしの関係を LLM に伝えるために使います。
取得失敗時は warning を出して続行（構造なしでもチャンクのみで回答可能）。

### ④⑤ プロンプト組み立てと生成（`chat.py:generate_chat_response`）
system プロンプトには次の指示が含まれます（`chat.py:45-50`）。

- SMILES DSL を使って因果・関係グラフを説明する
- 回答末尾に **ドリルダウンリンク**（関連子ノード・前提ノード・親ノード）を Markdown のリストで提示する
  ```
  - [〇〇について詳しく聞く]
  - [前提条件Bについて詳しく聞く]
  - [親概念Cの全体像について詳しく聞く]
  ```

学習チャット側（`learning.py:613`）では、誤解の訂正は冷たい「訂正：」を避け、
「この点については 〇〇 と考えるとより正確です」のように教育的配慮を持って導くよう指示されます。

生成は `generate_text(messages, temperature=0.3)`（精度重視）。

### ⑥ 誤解検出（`learning.py:1213-1216`）
LLM の回答に誤解訂正のシグナル（`"訂正"`, `"より正確です"`, `"誤解"` など）が含まれると、
`detect_and_record_misconception(...)` が誤解を抽出し、`event_type="misconception"` として個人レイヤーに記録します。
この記録はマスター教材ではなく **個人レイヤー**（`learning_states` 由来）に保存され、レスポンスの `course_update.personal_layer.misconceptions_by_topic` で UI に返ります。

### ⑦ 前提知識チェック・学習支援（`learning_support_agent.py`）
- 現在トピックの `prerequisites` を取得し、各前提について関連トピックのチャット履歴（`learning_chat_history`）の有無で習得を判定。
- 未習得なら逆質問を返し、`mode="prerequisite_review"` などの構造化アクションを付与。
- 学生が「理解している」と答えればスキップ。
- 「学習パスに戻る」「詳細を続ける」などの遷移は `next_actions` として返り、UI がボタン化します。

---

## 3. レスポンス形

学習チャットのレスポンス（概形）:

```jsonc
{
  "answer": "…回答本文（Markdown, 末尾にドリルダウンリンク）…",
  "next_actions": [
    { "type": "return_to_learning_path", "label": "学習に戻る", "message": "…" }
  ],
  "support_mode": "normal | detail_explanation | prerequisite_review | return_to_learning_path",
  "status_label": "…",
  "origin": { "topic_id": "…", "topic_title": "…" },
  "course_update": {
    "personal_layer": {
      "misconceptions_by_topic": { "…": [ … ] },
      "chat_anchors": [ … ]
    }
  }
}
```

UI 側の扱いは [学習機能](../features/learning.md)・[フロントエンド構成](../frontend/overview.md) を参照。

---

## 4. 関連

- 概念グラフ DSL（SMILES）: [DSL と理論操作グラフ](../pipeline/theory-graph.md)
- 答えられなかった質問は `unanswered_query_logs` に記録され、[動的スキーマ進化](../pipeline/schema-evolution.md) の入力になります。

---

[← コアエンジン](core-engine.md) ｜ 次へ: [パイプライン概要 →](../pipeline/overview.md)
