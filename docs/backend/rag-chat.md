# RAG チャットフロー

[← ドキュメント目次](../README.md) ｜ [← コアエンジン](core-engine.md)

学生の質問に答える RAG（Retrieval-Augmented Generation）の流れを解説します。
実チャットの正本は `POST /api/learning/courses/{cid}/topics/{tid}/chat`（`api/routes/learning.py::learning_chat`）で、
コンテキスト構築・出所判定（content_grounding）・casual モード分岐までここに実装されています。
`backend/core/chat.py` は tier 付き chunk 検索ユーティリティ（`search_chunks()` / `_embed_query()`）のみを提供する下請けモジュールで、
`learning_chat` の RAG 検索自体は `services.py::search_chunks_with_metadata()` を使います。

---

## 1. 全体フロー

```
ユーザー質問
  │
  ① 前提知識チェック                     … learning_support_agent.py: check_prerequisites()
  │   未習得の前提があれば RAG より先に逆質問で会話を止める（casual/寄り道復帰時はスキップ）
  │
  ② pgvector で関連チャンク検索          … services.py: search_chunks_with_metadata() (top_k=8)
  │   コース全教材横断で検索、tier(L1信頼性) 付与、スコア 0.30 以上を採用
  │
  ③ 出所判定（content_grounding）        … learning.py（course_material / other_material / model_generated）
  │   採用チャンクの tier を安全側集約した overall_tier（out_of_source 判定）も算出
  │
  ④ プロンプト組み立て                   … learning.py: _get_integrated_tutor_system_prompt() 等
  │   system プロンプト + 関連チャンク + 会話履歴。out_of_source なら OutOfSourceGuard を追加注入
  │
  ⑤ LLM 生成（temperature=0.3）          … llm.py: generate_text()
  │   末尾にドリルダウンリンクを Markdown で提示
  │
  ⑥ 誤解検出 → 個人レイヤーへ記録        … learning.py + detect_and_record_misconception()
  │
  ⑦ 関心痕跡の記録 + tension プレフィルタ … services.py + core/tension/prefilter.py（同期・非LLM）
  ▼
回答 + next_actions + content_grounding + course_update（誤解・アンカー）
```

> ①⑥ は `intent_mode="casual"`（カジュアル対話モード）ではスキップされます（→ §3）。
> ②〜⑤⑦ は casual を含む全モードで実行されます。

---

## 2. 各ステップ詳細

### ① 前提知識チェック（`learning_support_agent.py::check_prerequisites`）
- 現在トピックの `prerequisites` を取得し、各前提について関連トピックのチャット履歴（`learning_chat_history`）の有無で習得を判定。
- 未習得なら逆質問を返し、`mode="prerequisite_review"` などの構造化アクションを付与して RAG より前に応答を返す。
- 学生が「理解している」と答えれば、またはコース側の atlas 文脈中であればスキップして ② へ進む。
- 「学習パスに戻る」「詳細を続ける」などの遷移は `next_actions` として返り、UI がボタン化します。

### ② ベクトル検索（`services.py::search_chunks_with_metadata`）
コースの特定教材に絞らず、システム全域のチャンクを pgvector 類似度検索し（`top_k=8`）、各チャンクに tier（L1信頼性、教員承認状況から導出）を付与して返します。スコア `>= 0.30` のチャンクのみ回答コンテキストの根拠として採用します。

### ③ 出所判定（content_grounding・overall_tier）
採用チャンクの `material_id` が現在コースの `sources[].material_id` に含まれれば `course_material`、含まれなければ `other_material`、採用チャンクが一つもなければ `model_generated` と判定します（`tier` = 教員承認状況とは別軸）。
また採用チャンクの tier を `aggregate_overall_tier()` で最弱根拠へ安全側集約し、採用根拠が無ければ `out_of_source` になります。

### ④⑤ プロンプト組み立てと生成（`routes/learning.py::learning_chat`）
system プロンプトは通常モードで `_get_integrated_tutor_system_prompt()`、casual モードで `_get_casual_teacher_system_prompt()` を使い、`overall_tier == out_of_source` のときは `out_of_source_guard_instruction()`（断定回避・予想促し）を追加注入します。

- SMILES DSL を使って因果・関係グラフを説明する
- 回答末尾に **ドリルダウンリンク**（関連子ノード・前提ノード・親ノード）を Markdown のリストで提示する
  ```
  - [〇〇について詳しく聞く]
  - [前提条件Bについて詳しく聞く]
  - [親概念Cの全体像について詳しく聞く]
  ```

生成は `generate_text(messages, temperature=0.3)`（精度重視）。`overall_tier == out_of_source` かつ非 casual のときは、`out_of_source_notice()` の注意書きを回答冒頭に追加します。

### ⑥ 誤解検出
LLM の回答に誤解訂正のシグナル（`"訂正"`, `"より正確です"`, `"誤解"` など）が含まれると、
`detect_and_record_misconception(...)` が誤解を抽出し、`event_type="misconception"` として個人レイヤーに記録します。
この記録はマスター教材ではなく **個人レイヤー**（`learning_states` 由来）に保存され、レスポンスの `course_update.personal_layer.misconceptions_by_topic` で UI に返ります。casual モードではスキップされます。

---

## 3. インテントモード（on_path / explore / casual）

学習チャットは `intent_mode` で振る舞いを切り替えます。UI の「教材に沿って質問」「自由に質問」の
2 ボタンは廃止され「質問」1 つに統合済みで、on_path / explore はフロントが寄り道状態から自動判定して送る
内部値です（寄り道中なら explore、そうでなければ on_path）。

| intent_mode | 用途 | 雑談拒否 | 前提知識ゲート | 誤解検出 | origin/status_label |
|---|---|---|---|---|---|
| explore（既定） | 寄り道・探索 | ✓ | ✓ | ✓ | 返す（復帰導線） |
| on_path | 本筋の質問 | ✓ | ✓ | ✓ | 返さない |
| **casual** | 気軽に話せる先生（音声会話主体） | スキップ | スキップ | スキップ | 返さない |

**casual モード**（`learning.py` の `_is_casual`）は、意図分類（CHIT_CHAT ルート）・前提知識ゲート・
誤解検出をバイパスし、短い会話調（箇条書き/ドリルダウンマーカーなし、音声読み上げ向き）で応答します。
ただし**根拠の一線は維持**: RAG 検索・tier 集約・OutOfSourceGuard の system 注入はそのまま通し、
可視の注意書きプレフィックスのみ省略します（tier はレスポンスで返す）。interest_traces 記録と
tension プレフィルタも通常どおり効きます（payload に `casual: true`）。

---

## 4. 出所判定（content_grounding）

`tier`（教員承認状況）とは**別軸**で、「回答が何に基づくか」を RAG 実行後に判定し、回答バブルと出典タブに提示します。

| 値 | 意味 |
|---|---|
| `course_material` | このコースの教材に基づく（cited チャンクの `material_id` がコースの `sources[].material_id` に含まれる） |
| `other_material` | 別の資料（cited はあるがコース教材外） |
| `model_generated` | `cited_sources` が完全に空 — モデルの一般知識（出典なし） |

判定は `search_chunks_with_metadata`（`services.py`）が返す `material_id` を使うため、
**この関数のクエリを変更する際は `material_id` の SELECT を落とさないこと**。

---

## 5. tension プレフィルタと TensionMiningAgent（B層）

チャット応答を遅延させないため、同期パスに置くのは非 LLM のプレフィルタだけです。

1. **Stage 0（同期）** — `core/tension/prefilter.py: judge_tension_hint(message, 直近のユーザー発話)`。
   ヘッジ/逆接マーカー（「気がする」「でも」「矛盾」等）や直近 3 往復内の同語再訪でヒント判定し、
   関心痕跡（`interest_traces`）の `payload.tension_hint` に保存。納得クロース（「なるほど」等）はヒントを打ち消す。
2. **Stage 1（非同期）** — ヒントが立つと `core/tension/worker.py: maybe_schedule_tension_mining()` を
   best-effort で起動（失敗してもチャットは止めない）。未解析ヒント累積 5 件 or セッション終了（20分無活動）で
   `TensionMiningAgent` が LLM 1 コール/会話窓で候補を抽出し、`kind='tension'` / `status='candidate'` で保存。
3. **Stage 2（本人確定）** — 学習者ダイジェスト（`GET /courses/{cid}/tension/digest`）から
   confirm（→ open / articulated）・dismiss（→ dismissed）・connect。教員へは k-匿名化集計のみ。

設計原則（P1〜P7: 違和感を生成するのは人間 / 断定しない / 監視にしない / 情報を落とさない /
evidence-based / 応答を遅延させない / 演技化させない）は [CLAUDE.md](../../CLAUDE.md) と
`backend/core/tension/` の実装を参照。

---

## 6. レスポンス形

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
  "content_grounding": "course_material | other_material | model_generated",
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

## 7. 関連

- 概念グラフ DSL（SMILES）: [DSL と理論操作グラフ](../pipeline/theory-graph.md)
- 答えられなかった質問は `unanswered_query_logs` に記録され、[動的スキーマ進化](../pipeline/schema-evolution.md) の入力になります。

---

[← コアエンジン](core-engine.md) ｜ 次へ: [パイプライン概要 →](../pipeline/overview.md)
