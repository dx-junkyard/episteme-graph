# RAG チャットフロー

[← ドキュメント目次](../README.md) ｜ [← コアエンジン](core-engine.md)

> **更新注記（2026-08-14）:** `intent_mode` の 4 値目 discuss（§3・§3.5）と
> 可視性フィルタ（§2.5）を追補した。他の節は 2026-07-17 時点の記述で、
> 残る差分は CLAUDE.md の該当節を参照。

学生の質問に答える RAG（Retrieval-Augmented Generation）の流れを解説します。
実チャットの正本は `POST /api/learning/courses/{cid}/topics/{tid}/chat`（`api/routes/learning.py::learning_chat`）で、
コンテキスト構築・出所判定（content_grounding）・casual / discuss のモード分岐までここに実装されています。
`backend/core/chat.py` は tier 付き chunk 検索ユーティリティ（`search_chunks()` / `_embed_query()`）のみを提供する下請けモジュールで、
`learning_chat` の RAG 検索自体は `services.py::search_chunks_with_metadata()` を使います。

---

## 1. 全体フロー

```
ユーザー質問
  │
  ① 前提知識チェック                     … learning_support_agent.py: check_prerequisites()
  │   未習得の前提があれば RAG より先に逆質問で会話を止める（casual/discuss/寄り道復帰時はスキップ）
  │
  ② pgvector で関連チャンク検索          … services.py: search_chunks_with_metadata() (top_k=8)
  │   本人が閲覧できる document に絞って検索（§2.5）、tier(L1信頼性) 付与、スコア 0.30 以上を採用
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
> `intent_mode="discuss"` でも ① はスキップされ、⑥ は実質発火しません（→ §3.5）。
> ②〜⑤⑦ は casual / discuss を含む全モードで実行されます。

---

## 2. 各ステップ詳細

### ① 前提知識チェック（`learning_support_agent.py::check_prerequisites`）
- 現在トピックの `prerequisites` を取得し、各前提について関連トピックのチャット履歴（`learning_chat_history`）の有無で習得を判定。
- 未習得なら逆質問を返し、`mode="prerequisite_review"` などの構造化アクションを付与して RAG より前に応答を返す。
- 学生が「理解している」と答えれば、またはコース側の atlas 文脈中であればスキップして ② へ進む。
- 「学習パスに戻る」「詳細を続ける」などの遷移は `next_actions` として返り、UI がボタン化します。

### ② ベクトル検索（`services.py::search_chunks_with_metadata`）
コースの特定教材には絞らず、**本人が閲覧できる document 集合**（§2.5）の範囲でチャンクを pgvector 類似度検索し（`top_k=8`）、各チャンクに tier（L1信頼性、教員承認状況から導出）を付与して返します。スコア `>= 0.30` のチャンクのみ回答コンテキストの根拠として採用します。

### ③ 出所判定（content_grounding・overall_tier）
採用チャンクの `material_id` が現在コースの `sources[].material_id` に含まれれば `course_material`、含まれなければ `other_material`、採用チャンクが一つもなければ `model_generated` と判定します（`tier` = 教員承認状況とは別軸）。
また採用チャンクの tier を `aggregate_overall_tier()` で最弱根拠へ安全側集約し、採用根拠が無ければ `out_of_source` になります。

### ④⑤ プロンプト組み立てと生成（`routes/learning.py::learning_chat`）
system プロンプトは通常モードで `_get_integrated_tutor_system_prompt()`、casual モードで `_get_casual_teacher_system_prompt()`、discuss モードで `_get_discuss_system_prompt()`（理解サイクルの `cycle_mode` があればそちらが優先）を使い、`overall_tier == out_of_source` のときは `out_of_source_guard_instruction()`（断定回避・予想促し）を全モード共通で追加注入します。

- SMILES DSL を使って因果・関係グラフを説明する
- 回答末尾に **ドリルダウンリンク**（関連子ノード・前提ノード・親ノード）を Markdown のリストで提示する
  ```
  - [〇〇について詳しく聞く]
  - [前提条件Bについて詳しく聞く]
  - [親概念Cの全体像について詳しく聞く]
  ```

生成は `generate_text(messages, temperature=0.3)`（精度重視）。`overall_tier == out_of_source` かつ非 casual のときは、`out_of_source_notice()` の注意書きを回答冒頭に追加します（discuss でも維持 — §3.5）。

### ⑥ 誤解検出
LLM の回答に誤解訂正のシグナル（`"訂正"`, `"より正確です"`, `"誤解"` など）が含まれると、
`detect_and_record_misconception(...)` が誤解を抽出し、`event_type="misconception"` として個人レイヤーに記録します。
この記録はマスター教材ではなく **個人レイヤー**（`learning_states` 由来）に保存され、レスポンスの `course_update.personal_layer.misconceptions_by_topic` で UI に返ります。casual モードではスキップされます。

---

## 2.5 検索の可視性フィルタ（`allowed_document_ids`）

全域ベクトル検索は、必ず**本人が閲覧できる document 集合**に絞ってから発行します
（[discussion_mode_design.md](../features/discussion_mode_design.md) §6.1 Phase 0。
discuss とは独立に、通常の学習チャットにも効いています）。

- `services.py::search_chunks_with_metadata(query, top_k, *, allowed_document_ids)` の
  `allowed_document_ids` は **必須キーワード引数**です（呼び忘れを構造的に防ぐ。
  `core/help_kb/manual.py::search_manual(..., audience)` と同じ規律）。
- 絞り込みは SQL 内の `c.document_id = ANY(CAST(:doc_ids AS uuid[]))` で行い、
  **取得後の Python 判定にはしません**。
- **空集合（非 `None`）なら SQL を発行せず即 `[]`** を返します（fail-closed）。
  `None` は無フィルタ（全域検索）ですが、テスト・本番未接続コード専用です。
- 可視集合の正本は `services.py::list_visible_document_ids(user_id)`。1 SQL で
  「document 直接可視（所有 / public / group / `object_group_permissions`）」∪
  「アクセス可能なコース（所有 / 公開テンプレート / グループ / 受講中）の `sources[]` が
  指す document」の和集合を返します。後者を含めるのは、受講コースの教材（教員の private
  文書であることが多い）を RAG できないと既存の学習体験が壊れるためで、コースへの
  アクセス自体が sources の開示根拠であるという設計判断です。例外時は空集合（fail-closed）。
- コース単位のスコープが要るときは `services.py::list_course_source_document_ids(course_data)`
  が正本（discuss の `course_sources` と出典ポップアップが使用）。
- **チャンク直読み API も同じ規律**です。`get_chunk_passage(chunk_id, *, allowed_document_ids)`
  も必須キーワード引数で、`GET /courses/{cid}/source-chunk/{chunk_id}` は
  「全域可視集合」ではなく **URL の course の sources** をスコープにします
  （そのコースに紐づかない別コース・public 文書のチャンクを読ませないため。
  積集合は取らない — コースへの正規アクセスが開示根拠）。

---

## 3. インテントモード（on_path / explore / casual / discuss）

学習チャットは `intent_mode` で振る舞いを切り替えます。UI の「教材に沿って質問」「自由に質問」の
2 ボタンは廃止され「質問」1 つに統合済みで、on_path / explore はフロントが寄り道状態から自動判定して送る
内部値です（寄り道中なら explore、そうでなければ on_path）。

| intent_mode | 用途 | 雑談拒否 | 前提知識ゲート | 誤解検出 | origin/status_label | U層 feature |
|---|---|---|---|---|---|---|
| explore（既定） | 寄り道・探索 | ✓ | ✓ | ✓ | 返す（復帰導線） | `learning:chat` |
| on_path | 本筋の質問 | ✓ | ✓ | ✓ | 返さない | `learning:chat` |
| **casual** | 気軽に話せる先生（音声会話主体） | スキップ | スキップ | スキップ | 返さない | `learning:chat_casual` |
| **discuss** | 論文と話す（係留付きディスカッション） | スキップ | スキップ | 発火しない※ | 返さない | `learning:chat_discuss` |

※ 誤解検出は `_is_discuss` では明示バイパスしていませんが、discuss の会話は
予約疑似トピック `_discussion` の上で行われ `topic_info` が `None` になるため、
結果として発火しません（条件は `not _is_casual and topic_info and …`）。

**casual モード**（`learning.py` の `_is_casual`）は、意図分類（CHIT_CHAT ルート）・前提知識ゲート・
誤解検出をバイパスし、短い会話調（箇条書き/ドリルダウンマーカーなし、音声読み上げ向き）で応答します。
ただし**根拠の一線は維持**: RAG 検索・tier 集約・OutOfSourceGuard の system 注入はそのまま通し、
可視の注意書きプレフィックスのみ省略します（tier はレスポンスで返す）。interest_traces 記録と
tension プレフィルタも通常どおり効きます（payload に `casual: true`）。

判定順は **usage_help pre-route（`_is_usage_question`）→ casual → discuss** で、
この順序は崩さないこと（音声・casual 経路にマニュアル回答を届ける唯一の位置が
pre-route であるため）。

### 3.5 discuss モードの分岐

`intent_mode='discuss'`（`learning.py` の `_is_discuss`。正本:
[discussion_mode_design.md](../features/discussion_mode_design.md) DM1〜DM8）は casual と
同型の 3 点バイパス（意図分類・前提知識ゲート・寄り道化）を共有しますが、応答スタイルと
出所の扱いが異なります。**migration 0・新テーブル 0・新チャットエンドポイント 0**。

- **予約疑似トピック `_discussion`**: 会話は `DISCUSSION_TOPIC_ID = "_discussion"` の上で
  行われます。既存トピックに存在しないため、`topic_title` / 痕跡の `context_label` は
  `DISCUSSION_TOPIC_LABEL = "論文との議論"` へ 1 箇所で変換します
  （生の `_discussion` を UI・プロンプトに出さない）。
- **`discuss_scope` の検証（422）**: `course_sources`（既定）/ `all_visible` 以外は
  **422**。検証は書き直し（`replace_message_id`）による履歴 truncate よりも**前**で
  行うため、不正値のリクエストが履歴を切り詰めてしまうことはありません。
  検索範囲は `course_sources` → `list_course_source_document_ids(course_data)`、
  `all_visible` → `list_visible_document_ids(user_id)`（§2.5）。
- **無断フォールバックの禁止（DM1）**: 選択スコープで採用チャンクが 0 件でも、
  他スコープへは広げません。`context_block` を「※選択中の検索範囲には…範囲は広げていません」
  という事実文に差し替え、範囲外知識を使う場合は出所を明示するよう指示します
  （非 discuss の「関連セクションが見つかりませんでした」文とは別文言）。
- **system プロンプト `_get_discuss_system_prompt()`**: 学術ディスカッション調（LaTeX・
  `[出典N]` マーカーはチューターモードと同様に使用）。発話タイプ別に、質問には即答、
  解釈・立場の表明には言い直し（revoice）から、詰まりには一点だけの足場かけで応じ、
  **回答末尾に生成プロンプトを構造的に必須化**します（学習者の直前の発話を引用・組み込んだ
  言い換え／予測／自己説明の誘い、または why / how / what-if の問い返しのいずれか 1 つ。
  汎用の決まり文句は不可）。数値スコア（検索件数・一致度・網羅率）は出しません。
  対話進行の正本は
  [discuss_dialogue_alignment_design.md](../features/discuss_dialogue_alignment_design.md)。
- **足場メッセージの中立化**: discuss のときだけ、context 注入ターンの定型文を
  「以下の質問に答えてください」→「発話タイプ別の応答ルールに従って、以下の学生の発話に
  応じてください」に差し替えます（Q&A フレームの再導入で revoice 指示が打ち消されるため）。
  casual・通常モードの足場は変更しません。
- **`out_of_source_notice()` は維持**: casual では可視の注意書きプレフィックスを省略しますが、
  discuss では**意図的に維持**します（DM1: 出所の正直さを弱めない）。OutOfSourceGuard の
  system 注入は全モード共通です。
- **寄り道化しない（DM5）**: `on_path` / `casual` と同じく `origin=None` で返すため、
  既存フロントの寄り道バナー・復帰導線は出ません。UI 文言にも「寄り道」を使いません。
- **U層タグの分離**: `learning:chat_discuss` で計測し、通常チャット・casual と独立に
  コストを実測します（専用の日次上限は設けず、既存の
  `LEARNING_CHAT_MAX_CALLS_PER_DAY` に相乗り）。
- **痕跡**: `interest_traces` の payload に `entry_mode: 'discuss'` と
  `discuss_scope` を焼き込みます（後から U層・k-匿名集計・個人知識ネットワークが
  discuss 由来を区別できるように）。tension プレフィルタ・structure_anchor は通常どおり効きます。

理解サイクルの AI モードは、この discuss の 1 コール地点に相乗りする内部値
`cycle_mode ∈ {elicit, diff}`（不正値 422）で切り替わり、system プロンプトのみ
`_get_cycle_elicit_system_prompt()` / `_get_cycle_diff_system_prompt()` に差し替わります
（U層 feature は `learning:cycle_elicit` / `learning:cycle_diff`）。詳細は
[understanding_cycle_design.md](../features/understanding_cycle_design.md) §8。

開幕画面 `GET /api/learning/courses/{id}/discuss/opening` と着地の
`POST .../discuss/reflection` は、いずれも **LLM 0 回**の別エンドポイントです
（→ [学習機能 §3.8](../features/learning.md#38-論文と話すdiscuss-モード)）。

---

## 4. 出所判定（content_grounding）

`tier`（教員承認状況）とは**別軸**で、「回答が何に基づくか」を RAG 実行後に判定し、回答バブルと出典タブに提示します。

| 値 | 意味 |
|---|---|
| `course_material` | このコースの教材に基づく（cited チャンクの `material_id` がコースの `sources[].material_id` に含まれる、**または現在表示中のトピック教材（`student_material` 等）をコンテキストに注入した場合**） |
| `other_material` | 別の資料（cited はあるがコース教材外） |
| `model_generated` | RAG ヒットもトピック教材も無い — モデルの一般知識（出典なし） |

判定は `search_chunks_with_metadata`（`services.py`）が返す `material_id` を使うため、
**この関数のクエリを変更する際は `material_id` の SELECT を落とさないこと**。

トピック教材はプロンプトへ `[現在表示中の教材]` として注入される。この場合、回答には
実根拠があるため `overall_tier` の集約結果を `source` を下限に引き上げる
（`tier_floor(overall_tier, TIER_SOURCE)`、`routes/learning.py`）。これにより
「📘 教材から回答」と「参考（out_of_source）+ 未踏ガード」の矛盾表示は発生しない。
承認チェーン由来ではないため **approved へは昇格させない**（不可侵の一線）。

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
