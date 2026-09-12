# RAG チャットフロー

[← ドキュメント目次](../README.md) ｜ [← コアエンジン](core-engine.md)

> **更新注記（2026-09-03）:** 実装（`api/routes/learning.py`）と再突合し、
> 会話履歴のウィンドウ化・コスト上限・LLM 失敗時の縮退・楽屋（backstage）フラグ・
> `_learning_chat_core` への分離（§2.6〜§2.9）を追補した。

学生の質問に答える RAG（Retrieval-Augmented Generation）の流れを解説します。
実チャットの正本は `POST /api/learning/courses/{cid}/topics/{tid}/chat`（`api/routes/learning.py::learning_chat`）で、
コンテキスト構築・出所判定（content_grounding）・casual / discuss のモード分岐までここに実装されています。

ルートハンドラ `learning_chat` は 1 行の委譲で、本体は **`_learning_chat_core(course_id, topic_id, body, current_user)`**
に分離されています。コーパス回遊 Phase B の「コース無し論文議論」
（`POST /api/learning/documents/{ref}/discuss/chat`）が同じコアを、予約センチネル
`course_id = "_doc:{document_id}"`（正本 `core/discuss/context.py`）で呼び出すためです
（コース経路の挙動は不変）。

旧 `backend/core/chat.py`（tier 付き chunk 検索のレガシーモジュール・本番の呼び出し元なし）は
2026-09-10 のエージェント棚卸しで削除済みです。`_learning_chat_core` の RAG 検索は
`services.py::search_chunks_with_metadata()` を使います。

---

## 1. 全体フロー

```
ユーザー質問
  │
  ① 前提知識チェック                     … services.py: check_prerequisites()
  │   本人が「理解している」と答えていない前提があれば RAG より先に逆質問で会話を止める
  │   （casual/discuss/寄り道復帰時はスキップ。前提の説明要求は ①-b の3段解決へ）
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
  ④-b 画面文脈ブロック・選択箇所ブロック  … core/assistant_context/（SA層 Phase 4・決定論・非LLM）
  │   画面が送った参照をサーバが学習者射影で解決し、当該ターンの発話の前にだけ置く（§④-b）
  │
  ⑤ LLM 生成（temperature=0.3）          … llm.py: generate_text() / generate_text_stream()
  │   末尾にドリルダウンリンクを Markdown で提示
  │   転送方式だけが分岐する（SSE の逐次配信は §④-c。前処理・後処理は 1 本のまま）
  │
  ⑥ 誤解検出 → 個人レイヤーへ記録        … learning.py + detect_and_record_misconception()
  │
  ⑦ 関心痕跡の記録 + tension プレフィルタ … services.py + core/tension/prefilter.py（同期・非LLM）
  │   痕跡 kind は既存シグナルから決定（楽屋なら backstage_question。§2.8）。
  │   ヒットすれば tension / structure_anchor の非同期 worker を best-effort で起動
  ▼
回答 + next_actions + content_grounding + course_update（誤解・アンカー）
```

> ①⑥ は `intent_mode="casual"`（カジュアル対話モード）ではスキップされます（→ §3）。
> `intent_mode="discuss"` でも ① はスキップされ、⑥ は実質発火しません（→ §3.5）。
> ②〜⑤⑦ は casual / discuss を含む全モードで実行されます。

---

## 2. 各ステップ詳細

### ① 前提知識チェック（`services.py::check_prerequisites`）
- 現在トピックの `prerequisites` を取得し、**本人が明示的に「理解している」と答えた記録**
  （`learning_states.progress_data.acknowledged_prerequisites`。正規化名 → 記帳時刻）に
  無いものを未習得として扱う。
- 未習得なら逆質問を返し、`mode="prerequisite_review"` などの構造化アクションを付与して RAG より前に応答を返す。
- 学生が「理解している」と答えれば、またはコース側の atlas 文脈中であればスキップして ② へ進む。
  肯定の答えは記帳され、以後同じ前提では問い返さない（否定形を含む発話は記帳しない）。
- 「学習パスに戻る」「詳細を続ける」などの遷移は `next_actions` として返り、UI がボタン化します。

> **2026-09-10 是正（F4 / 六つのレンズ 提案6）**: 習得判定から
> 「関連トピックのチャット履歴（`learning_chat_history`）の有無」を撤去した。質問した・
> 開いたという**接触の痕跡は理解の根拠にならず**、履歴による自動スキップは学習者の状態を
> AI が暗黙に推定する沈黙適応（UC5 / §3.6）だった。判定の根拠は本人への明示的な問いと
> その答えだけに寄せてある。

### ①-b 前提知識の説明（3段解決 / `routes/learning.py`）
前提の**説明**（`support_action ∈ _PREREQUISITE_ACTIONS` または「前提知識…確認/復習/必要」の
発話 → `LEARNING_ADVICE`）は、`_resolve_prerequisite_context()` が段階的に解決する。
LLM の追加コールは無い（②相当の検索1回 = 通常の RAG ターンと同じ、生成は既存 advice の1コール）。

1. **同コースの topic** に前提名が一致すれば、その `student_material` を抜粋にする
   → `content_grounding="course_material"`。
2. 一致しなければ **本人が閲覧できる document のチャンク**を
   `search_chunks_with_metadata(..., allowed_document_ids=list_visible_document_ids(user_id))`
   で1回検索し、スコア `>= 0.30` を `[出典N]` 付きの抜粋にする（コース sources 外のヒットは
   `other_material`）。「その前提を扱っている」の判定は**逐語一致のみ** — ベクトル近傍で
   引けただけの資料を「扱っている」とは言わない。
3. どこにも無ければ LLM の説明を返すが `content_grounding="model_generated"` を設定し、
   閉世界の事実文「このコーパスの中には、この前提を扱う資料がありません。」（SL1 継承。
   分野レベルの不在は言わない）をサーバ側で添える。

`LEARNING_ADVICE` のどの分岐でも `content_grounding` を `None` にしない（原則8）。
学習相談の一般アドバイスは `model_generated`。教員側には G層ルール
`course.prerequisite_uncovered`（recommended・道案内のみ）が対応する。

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

#### ④-b 画面文脈ブロック・選択箇所ブロック（SA層 Phase 4）

**CostGate の直後・`generate_text` の前**（429 で返る往復では解決を走らせない）で、当該ターンの
user メッセージだけを **「画面文脈ブロック → 選択箇所ブロック → 発話」** に組み替えます
（正本: [assistant_screen_adapter_design.md](../features/assistant_screen_adapter_design.md)
§11 / 実装記録 §11.15）。足場ターン（`messages[1]` = `context_block`）には混ぜません。

- **画面文脈ブロック**: フロント（`app.js::getScreenContext`）が送るのは**参照だけ**
  （要素の種別と ID・トピック・スライド・表示モード・チップの ID と40字の題名）。サーバが
  権限3段（受講ゲート済み `course_data` → `scope_document_ids` または
  `list_course_source_document_ids` → 各射影内の `ANY(:doc_ids)`）を通した**学習者射影**で解決し、
  `core/assistant_context/resolvers/learning.py` の4解決器（`element` / `verification` /
  `placement` / `view`）が事実文にします。**discuss の `all_visible` でも範囲は広がりません**（DM1）。
  台帳の事実が実際に載った往復だけ、system の末尾に閉世界の拘束文
  （`LEARNING_VERIFICATION_OUTPUT_CONSTRAINT`）が付きます。
- **選択箇所ブロック**: `selection_text` の逐語（最大600字）を、表示中教材本文との突き合わせ
  結果（「一致を確認済み」／「一致は確認できていません」）を**必ず併記して**載せます。
  不一致でも落としません。画面の `segment_id` と `selection_segment_id` が食い違えば後者を
  事実文に使います。
- **信頼境界**: 両ブロックは PDF 由来の untrusted 入力（逐語・claim 抜粋）を運ぶので、足場に
  `UNTRUSTED_SOURCE_NOTICE` が無い往復（検索ヒットもトピック教材も無いとき）は当該ターンの
  先頭に同じ注意書きを1回だけ添えます（TB1〜TB4）。

| 様相・モード | 画面文脈ブロック | 選択箇所ブロック |
|---|---|---|
| 通常（tutor / on_path / explore） | ○ | ○ |
| `discuss`（`_doc:` 直付けを含む） | ○ | ○ |
| `casual` | ✗（短い会話調と事実列挙が衝突する） | ○ |
| `cycle_mode="elicit"` | 表示モードの事実1行だけ（主張本文・検証事実は問いの答えの手渡しになる。DB も引かない） | ○ |
| `cycle_mode="diff"` / `backstage` / `check_scaffold` | ○ | ○ |

**LLM 呼び出し回数は不変**（1ターン1コール）。`screen_context` を送らない要求では
プロンプトが1バイトも変わりません。**保存（`learning_chat_history`）と痕跡には
`screen_context` を焼き込みません**（SA6）。ブロックが非空の往復は、種別だけの観測イベント
`structured_grounding_present` をサーバが記録します（学習者には何も表示しません）。

#### ④-c 転送方式の分岐（ストリーミング Phase 3-a）

`_learning_chat_core` は **generator 関数**で、**前処理 → 生成 → 後処理は 1 本のまま**です。
分岐するのは転送方式だけで、非ストリーム経路は同期ドライバ `_run_learning_turn(gen)` が
`next()` を回して `StopIteration.value`（＝ `LearningChatResponse`）を受け取ります
（正本: [llm_response_streaming_design.md](../features/llm_response_streaming_design.md)
§3.3 / §12 実装記録。フラグ `LEARNING_CHAT_STREAMING_ENABLED` は**既定 off**）。

継ぎ目は 1 箇所だけで、**前処理が終わった地点**（権限・可視性・値検証・CostGate・SA層の画面
文脈注入まで済んだ直後）に置かれます。

| イベント | 位置 | 中身 |
|---|---|---|
| `start` | `_consume_quota()` と §④-b の注入の**後**、生成の**前**に 1 回 | `{"stance": {stance, source, label}}`。`resolve_stance(...)` の入力6つは回答本文に依存しないので生成前に解決し、`final.stance` と**同じ値**を使う（`message_id` は載せない — この時点では `persist_chat_history` 前でサーバが id を持たないため） |
| `delta` | `stream=True` のときだけ `_stream_answer(...)` が本文の差分ごとに | `{"t": "部分テキスト"}`。`strip_control_sequences` 済み（chunk 境界でエスケープ列が割れないよう末尾16文字を保留して終端でフラッシュ）。**本文以外のキーを載せない** |
| `final` | 後処理をすべて終えた最後に 1 回 | `LearningChatResponse.model_dump()` そのまま（キーを間引かない）。同じ入力に対する非ストリーム経路のレスポンスと**同値** |
| `error` | `final` すら組み立てられなかったときだけ | `{"reason": "upstream"}`。通常の LLM 失敗はここではなく `final`（`degraded: true`）で閉じる |

- 本文の逐次生成は `core/llm.py::generate_text_stream`（**追加**。`generate_text` ほか既存3関数は
  非改変で、非ストリーム経路は従来どおり `generate_text` をモジュール属性として呼ぶ）。
  openai は `stream=True` + `stream_options={"include_usage": True}`、**非対応プロバイダは
  `generate_text` の結果を 1 delta として返す**（「ストリームのふり」をしない）。
- `yield` は `with usage_context(...)` / `model_override(...)` の**外**に置きます。Starlette は
  同期ジェネレータを `next()` ごとに別スレッド（複製した context）で再開するため、`with` を
  跨いだ yield は contextvar の reset で落ちます。U層の帰属は `_stream_answer` に**値渡し**します。
- U層の記録は **1 ストリーム = 1 行**（`generate_text_stream` の `finally`）。`operation` は
  `'chat'` のままで、転送方式は `metadata.streamed` / 中断は `metadata.client_aborted` に入ります
  （`usage_source` の意味・`KNOWN_FEATURES` は不変 = U1）。
- **中断（クライアント切断・停止ボタン）は後処理へ進みません**。`GeneratorExit` は
  `except Exception` を素通りするので、保存（`persist_chat_history`）も痕跡も書かれません
  （ストリームは表示の先行であって正本ではない = ST1。quota は消費されたまま）。

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
  積集合は取らない — コースへの正規アクセスが開示根拠）。副作用として、コース sources 外の
  引用（`other_material` grounding・discuss の `all_visible`）の出典ポップアップは 404 へ
  縮退し、フロントは事実文で degrade します。

---

## 2.6 会話履歴のウィンドウ化（`window_history`）

LLM へ渡す会話履歴は、必ず横断基盤の
`core/llm_worker/history.py::window_history(history, max_messages, max_chars, head_keep, current_message)`
を通します（チャット型 AI の共通規約。**保存用の履歴はウィンドウ化しない**）。

- 学習チャット本体: **20 件 / 2000 字**
- グラフ要素タップの説明生成パス（承認済み説明が無いとき）: **6 件 / 2000 字**

他のチャット型 AI の窓（コースビルダー 20/4000/head_keep=2、W層 16/4000/head_keep=1、
Copilot 8/500）は [assistant_common_infra_design.md](../features/assistant_common_infra_design.md) を参照。

---

## 2.7 コスト上限と LLM 失敗時の縮退

- **CostGate（day-only）**: `LEARNING_CHAT_MAX_CALLS_PER_DAY`（既定 300・`(日付, user_id)` キー）。
  消費は **そのリクエストで最初に LLM を呼ぶ直前に 1 回だけ**（意図分類〜本体まで含めて 1）で、
  LLM を一度も呼ばないパス（承認済み説明があるグラフ要素タップ、usage_help のテキスト経路等）は
  消費しません。超過は **429 + 事実文のみ**（残数などの数値を返さない）。
  CostGate は in-memory・プロセスローカルです。
- **LLM 失敗は 500 にしない**: 本体生成が失敗したときは `degraded=true` の固定文 + 200 を返し、
  履歴は保存します。回答本文に依存する後処理（ドリルダウンマーカーの構造化・誤解検出・
  `out_of_source_notice` の付与）は degraded ターンではスキップします。
- **ストリーム経路（§④-c）でも同じ**です。CostGate は `StreamingResponse` を組み立てる**前**に
  消費するので、**429 は最初のバイトより前に通常の HTTP ステータスとして返ります**
  （200 を返してから中で断らない）。生成の途中で LLM が失敗した往復は、部分テキストを捨てて
  `final`（`degraded: true` + 既存と同じ固定文）で 200 のまま閉じ、履歴も従来どおり保存します。
  **例外は中断**（クライアント切断・停止ボタン）で、この場合は後処理へ進まず履歴も痕跡も
  書きません（quota は消費されたまま・残数は表示しません）。

---

## 2.8 楽屋（backstage）フラグ

構造の降下路（`docs/features/structure_descent_design.md`）の「楽屋」からの質問は、
`LearningChatRequest.backstage = true` で送られます。サーバ側は typed action / 地図アクションを
無視して常に通常の楽屋質問として処理し、痕跡の kind を **`backstage_question`** にします
（`payload.backstage = true`）。この kind は教員向け集約・digest・わたしの地図から除外され、
tension プレフィルタも常に打ち消されます（SD4）。

---

## 2.9 判定順（早期 return の並び）

`_learning_chat_core` の冒頭は次の順に判定します。**この順序は崩さないこと**。

1. `cycle_mode` の語彙検証（`elicit` / `diff` 以外は **422**）
2. 楽屋フラグ（`backstage`）の確定
3. **usage_help pre-route**（typed action `usage_help` または非LLM の `_is_usage_question()`。
   分野の地図の ↗ アクション（`atlas_context`）が無いときのみ判定）— casual バイパスより
   **手前**。音声・casual 経路にマニュアル回答を届ける唯一の位置。テキスト経路は
   マニュアル本文の素通し + `manual_citations` で **LLM 0 回・quota 非消費**、
   音声 / casual 経路のみ 1 コールで会話調に整えます（U層 feature `learning:help_usage`）
4. casual 判定（`_is_casual`）
5. discuss 判定（`_is_discuss`）と `discuss_scope` の検証（**422**。書き直しによる履歴
   truncate よりも前）
6. **様相（stance）の一次判定**（入口統合 Phase 1。正本:
   [learning_chat_entry_unification_design.md](../features/learning_chat_entry_unification_design.md)
   LC1〜LC8）— 非LLM の純関数 `core/learning_stance/heuristic.py::prejudge()` が
   「明らかに教材内容の問い」だけを `DOMAIN_RAG` と先に確定させ、意図分類の LLM コールを
   省きます。明示の様相（casual / discuss / 地図アクション）が立っている往復と、
   挨拶（`_is_greeting`）では**そもそも計算しません**（LC2）。決められなければ `None` で
   既存の `_classify_intent` へ落ちます（縮退はこの 1 本だけ）。
   入力は**当該発話のみ**で、履歴・過去の様相・学習者モデルは使いません（LC4 / UC5）。

**推定してよいのは「様相」（会話の調子）だけ**です（LC1）。検索範囲（`discuss_scope`）・
出題モード（`cycle_mode`）・記録の私有化（`backstage`）・確認問題の壁打ち（`check_scaffold`）を
サーバが推定で切り替えることはありません。

---

## 3. インテントモード（on_path / explore / casual / discuss）

学習チャットは `intent_mode` で振る舞いを切り替えます。UI の「教材に沿って質問」「自由に質問」の
2 ボタンは廃止され「質問」1 つに統合済みで、on_path / explore はフロントが寄り道状態から自動判定して送る
内部値です（寄り道中なら explore、そうでなければ on_path）。

| intent_mode | 用途 | 雑談拒否 | 前提知識ゲート | 誤解検出 | origin/status_label | U層 feature |
|---|---|---|---|---|---|---|
| explore（既定） | 寄り道・探索 | — ※2 | ✓ | ✓ | 返す（復帰導線） | `learning:chat` |
| on_path | 本筋の質問 | — ※2 | ✓ | ✓ | 返さない | `learning:chat` |
| **casual** | 気軽に話せる先生（音声会話主体） | スキップ | スキップ | スキップ | 返さない | `learning:chat_casual` |
| **discuss** | 論文と話す（係留付きディスカッション） | スキップ | スキップ | 発火しない※ | 返さない | `learning:chat_discuss` |

※2 **雑談は拒否しません**（入口統合 Phase 1・オーナー判断）。意図分類が `CHIT_CHAT` と
読んだ往復は、定型の拒否文を返す代わりに `_is_casual = True` を再代入して
**casual_light 様相で通常の RAG フローへ合流**します（下流の条件式は無改変）。根拠の一線
（RAG 検索・tier 集約・OutOfSourceGuard の system 注入・`content_grounding`）は落ちません。

※ 誤解検出は `_is_discuss` では明示バイパスしていませんが、discuss の会話は
予約疑似トピック `_discussion` の上で行われ `topic_info` が `None` になるため、
結果として発火しません（条件は `not _is_casual and topic_info and …`）。

**casual モード**（`learning.py` の `_is_casual`）は、意図分類（CHIT_CHAT ルート）・前提知識ゲート・
誤解検出をバイパスし、短い会話調（箇条書き/ドリルダウンマーカーなし、音声読み上げ向き）で応答します。
ただし**根拠の一線は維持**: RAG 検索・tier 集約・OutOfSourceGuard の system 注入はそのまま通し、
可視の注意書きプレフィックスのみ省略します（tier はレスポンスで返す）。interest_traces 記録と
tension プレフィルタも通常どおり効きます（payload に `casual: true`）。

判定順は **usage_help pre-route（`_is_usage_question`）→ casual → discuss → 様相の一次判定**
で、この順序は崩さないこと（音声・casual 経路にマニュアル回答を届ける唯一の位置が
pre-route であるため）。

### 3.4 様相（stance）はサーバが読む

`intent_mode` は「様相（会話の調子）」「伝達（読み上げ向きか）」「順路との関係（on_path /
explore）」の 3 軸を 1 つの enum に畳んでいました。入口統合 Phase 1（正本:
[learning_chat_entry_unification_design.md](../features/learning_chat_entry_unification_design.md)）
は **enum を増やさずに**内部の扱いだけをほどきます。

- **様相の推定はサーバ側で、明示は常に推定に勝つ（LC2）**。推定するのは `tutor` と
  `casual_light` の**間だけ**で、`discuss` / `cycle_elicit` / `cycle_diff` は明示のみです
  （範囲や出題モードの無断変更を作らないため = DM1 / UC1）。
- **伝達形式は `screen_mode` から決定論導出**（§4.4）。
  `_get_casual_teacher_system_prompt(..., spoken=)` が二枚に分かれ、`spoken=True`（音声モード、
  および `screen_mode` 未指定の明示 casual）は従来どおり 2〜4 文・LaTeX 禁止、
  `spoken=False`（テキストの casual_light）は**LaTeX と `[出典N]` を許可**します。
- **解決した様相は事実として返す（LC6 / LC7）**。RAG 応答（最終 return）だけが
  `LearningChatResponse.stance = {"stance", "source", "label"}` を設定します
  （`source` は `explicit` / `inferred`、`label` の正本は `core/label_vocab.py` の
  `LEARNING_STANCE_LABELS`）。**confidence・一致度などの数値は返しません**。
  痕跡 `interest_traces.payload` にも enum 2 つ（`stance` / `stance_source`）だけを
  焼き込みます（楽屋には焼き込まない = SD4）。
- **LLM 回数は増えません（LC5）**。一次判定が決めた tutor 経路は分類コールが省かれて
  2 → **1** コールになり、他の経路は不変です。CostGate は
  `LEARNING_CHAT_MAX_CALLS_PER_DAY` に相乗りのまま（専用上限・専用 env なし）。

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

スキーマの正本は `api/schemas.py::LearningChatResponse`。概形:

```jsonc
{
  "answer": "…回答本文（Markdown, 末尾にドリルダウンリンク）…",
  "next_actions": [
    { "type": "return_to_learning_path", "label": "学習に戻る", "message": "…" }
  ],
  "support_mode": "normal | detail_explanation | prerequisite_review | return_to_learning_path",
  "status_label": "…",
  "origin": { "topic_id": "…", "topic_title": "…" },
  "sources": [ { "chunk_id": "…", "tier": "…" } ],   // L1: 各根拠の tier
  "overall_tier": "…",                                // 最弱根拠へ安全側集約
  "content_grounding": "course_material | other_material | model_generated",
  "position_anchor": { … },                           // L2: 復帰位置アンカー
  "atlas_path_card": { … },                           // 「ここから学ぶ」カード（通常は null）
  "structure_anchor": { … },                          // 同期記録した明示アンカー（方法A）
  "anchor_confirm": { … },                            // 回答末尾の1タップ確認（毎回は出さない）
  "mirror": { "text": "…" },                          // discuss の鏡面化 move（verbatim 検査合格時のみ）
  "manual_citations": [ { "file": "…", "anchor": "…", "title": "…" } ],  // usage_help のみ
  "mock": false,
  "degraded": false,                                  // LLM 失敗で固定文へ縮退したターン
  "course_update": {
    "personal_layer": {
      "misconceptions_by_topic": { "…": [ … ] },
      "chat_anchors": [ … ]
    }
  }
}
```

`manual_citations` は既存 `sources` / `overall_tier` に**相乗りさせません**
（未知 tier が `out_of_source=0` に落ちてしまうため）。

### 6.1 SSE 版（`POST .../chat/stream`）との対応

ストリーム版（§④-c）は**別の DTO を持ちません**。上の JSON がそのまま最後の `final` イベントに
載ります（`LearningChatResponse.model_dump()` — キーを間引かない）。

| SSE イベント | 上の JSON との対応 |
|---|---|
| `start` | `{"stance": …}` のみ。`stance` は `final` の同名キーと**必ず同じ値**（`message_id` は載せない） |
| `delta` | `answer` の部分文字列（`{"t": "…"}`）。**本文以外のキーを載せない** |
| `final` | 上の JSON 全体。同じ入力に対する非ストリーム版のレスポンスと**同値**（`answer` に衛生処理を掛けないのもこのため） |
| `error` | `{"reason": "upstream"}`。`final` すら組めなかったときだけの保険で、通常の LLM 失敗は `final`（`degraded: true`）で返ります |

フラグが off のとき `POST .../chat/stream` は **404**（機能が存在しない状態を正直に返す）で、
クライアントは従来の JSON 経路へ戻ります。配布は `GET /api/learning/client-features`
（`{"chat_streaming": <bool>}` の 1 キーのみ・数値や上限は載せない）。

UI 側の扱いは [学習機能](../features/learning.md)・[フロントエンド構成](../frontend/overview.md) を参照。

---

## 7. 関連

- 概念グラフ DSL（SMILES）: [DSL と理論操作グラフ](../pipeline/theory-graph.md)
- 答えられなかった質問は `unanswered_query_logs` に記録され、[動的スキーマ進化](../pipeline/schema-evolution.md) の入力になります。

---

[← コアエンジン](core-engine.md) ｜ 次へ: [パイプライン概要 →](../pipeline/overview.md)
