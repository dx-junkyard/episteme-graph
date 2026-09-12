# LLM 応答のストリーミング（Phase 3）設計書

> **状態:** 設計中（2026-09-11 起草・未実装）。**migration 不要**（新テーブル・新列・
> CHECK 変更なし）。実装後に §12 実装記録を追記する。

本書は UX ロードマップ全3フェーズの **Phase 3**（応答の到着を待たせない）の設計である。
順序と依存の正本は [AI アシスタント UX ロードマップ](../architecture/assistant_ux_roadmap_2026-09-12.md)。
Phase 1 = [学習チャットの入口統合](learning_chat_entry_unification_design.md)、
Phase 2 = [画面文脈アダプター §11（SA層 Phase 4）](assistant_screen_adapter_design.md)。
本書は Phase 1/2 の成果に依存せず単独で実装できる（触る地点が入口・grounding ではなく
**生成の転送方式**だけのため）。

---

## §0 現状（2026-09-11 時点の実測）

**ストリーミングは1箇所も無い。**

- `backend/core/llm.py` の公開テキスト生成は `generate_text`（:779）/
  `generate_text_with_structured_output`（:888）/ `generate_conversation_turn`（:1479）の
  3本で、いずれも**完成した文字列を返す**。OpenAI 経路は
  `client.chat.completions.create(...)`（:859）を呼び、`response.choices[0].message.content`
  を取り出して（:875）返す。`stream=True` はどこにも無い。
- `backend/api/` に存在する `StreamingResponse` は**エクスポートの ZIP ダウンロードだけ**
  （`routes/export.py:13, 3185, 3370`）。既に `io.BytesIO` に完成した bytes を渡しており、
  逐次生成ではない。
- 学習者の1往復は直列連鎖である。`_learning_chat_core`（`api/routes/learning.py:2805`）は
  ① 意図分類 LLM（:3068 の `_classify_intent`）→ ② embedding 検索
  （:3208 `search_chunks_with_metadata`）→ ③ 本体生成（:3390 `generate_text`）→
  ④ 後処理（:3406〜:3577）の順に走り、**④が終わるまで1バイトも返らない**。

体感される沈黙は「①+②+③の合計」である。③は analysis tier の長文回答（
`resolve_model("learning_chat_llm_model", fallback="analysis")`、:3392）なので、支配項は
③の**生成完了待ち**であり、ここだけが先出しできる。①②は先出しできない（分類結果と
検索結果が無ければプロンプトが組めない）。したがって本設計の効果は
**「最初の1文字までの時間」を③の完了時刻から③の開始直後へ前倒しすること**に限られる。
①②の短縮は本書の対象外である（対象外であることを正直に書く）。

### 恩恵のある面／無い面

| 面 | 現在の呼び出し | 判定 |
|---|---|---|
| 学習チャット本文（質問 / discuss / cycle elicit・diff） | `generate_text`（learning.py:3390）1コール・長文 | **Phase 3-a で対象** |
| グラフ要素の説明（要素タップ） | `_generate_graph_element_explanation`（learning.py:1701, 生成は :1803） | Phase 3-b で対象（承認済み説明の即返し分岐（:1745）は元々待ち時間ゼロ） |
| 確認問題の並置（`check_topic_understanding`, learning.py:2170） | 構造化 JSON（`LearningCheckObservation` の配列） | **対象外**。部分 JSON は表示できない |
| 音声・casual（ハンズフリー） | 完成テキスト → TTS（app.js:5205 → :5269） | Phase 3-a では**非対象**。文単位 TTS は Phase 3-c で別途検討 |
| コースビルダー（admin.py:2545 近傍） | `---COURSE_DRAFT_JSON---` マーカーで散文と JSON を1応答に同梱 | 非スコープ（散文半分だけの逐次化は割に合わない） |
| W層 要素対話・グラフ全体対話 | `generate_conversation_turn`（llm.py:1479）の structured output（reply + spoken + 注釈） | Phase 3-d（reply フィールドの部分パースが要る） |
| Admin Copilot | 登録済み KB のテンプレート応答が主 | 恩恵なし・対象外 |

---

## §1 不変条項（ST1〜ST9）

本層は既存の層を**読む側**として積む（原則13）。A層・B層・D層・U層・M層のコードは
変更しない（U層は `metadata` に1キー足すのみで、正本の関数シグネチャを変えない）。

- **ST1 ストリームは表示の先行であって正本ではない。** 保存（`persist_chat_history`）・
  痕跡（`record_interest_trace`）・誤解候補（`detect_and_record_misconception`）・出典突合・
  監査は、**完成テキストに対してだけ**行う。途中経過をどこにも保存しない。
- **ST2 CostGate は最初の1バイトより前に消費する。** `_consume_quota()`（learning.py:3370）は
  `StreamingResponse` を組み立てる**前**に、通常の同期パスで走らせる。429 は SSE の中では
  なく HTTP ステータスとして返す（200 を返してから中で断らない）。
- **ST3 U層の帰属を落とさない。** 1ストリーム = `llm_usage_events` 1行。実測（`reported`）が
  取れなければ推計として正直に記録し、**混ぜない**（U1）。取得経路が違うことは
  `metadata.streamed` で区別できるようにするが、`usage_source` の意味は変えない。
- **ST4 衛生（`strip_control_sequences`）は chunk と完成テキストの両方に掛ける。** ただし
  ST7 との整合のため、**掛けるのは表示に流す delta だけ**（詳細は §5.2）。
- **ST5 途中の失敗は同じストリームの中で degraded に落とし、200 で閉じる。** 既存の I3
  「会話は死なせない」（`assistant_common_infra_design.md:14`）をそのまま継承する。
  **部分テキストを正本にしない** — 生成が途中で切れたターンは、部分応答を保存せず、
  そのターン自体を記録しない（§4.5）。
- **ST6 後処理由来のメタは終端イベントで一括して送る。** 出典（`sources` / `overall_tier` /
  `content_grounding`）・ドリルダウン（`next_actions`）・鏡（`mirror`）・帰属確認
  （`anchor_confirm`）・`structure_anchor` / `position_anchor` / `degraded` は
  delta では送らず `final` にまとめる。**delta には本文以外を載せない。**
- **ST7 非ストリーム API は不変。** 既存 `POST /courses/{id}/topics/{tid}/chat`
  （learning.py:2785）のリクエスト・レスポンス・処理順序・**テストの patch seam**
  （`api.routes.learning.generate_text` を差し替える20箇所）を1バイトも変えない。
  ストリーム版の `final` は非ストリーム版のレスポンスと**同一の DTO・同一の値**でなければ
  ならない。
- **ST8 学習者に数値を見せない。** トークン数・経過秒・残回数・生成速度を UI に出さない
  （原則4 / U5）。「止めました」等の事実文だけを出す。
- **ST9 段階導入は既定 off から始める。** `LEARNING_CHAT_STREAMING_ENABLED`（既定 false）で
  ストリーム経路を封じられる。off のときフロントは従来の JSON 経路のみを使う（fail-closed
  ではなく **fail-to-current**: 既存挙動へ戻るだけ）。

---

## §2 転送方式

### 2.1 SSE 形式 + `fetch` + `ReadableStream`（推奨）

| 候補 | 判定 |
|---|---|
| `EventSource`（本来の SSE クライアント） | **不可**。カスタムヘッダを付けられず、`apiFetch` が付ける `Authorization: Bearer`（app.js:246）が乗らない。クエリにトークンを載せるのは「個人・機微データを URL に置かない」規約に反する |
| `fetch` + `ReadableStream` で `text/event-stream` を自前パース | **採用**。ヘッダが付けられ、`AbortController` で停止でき、フレーム規約は SSE の枯れた形をそのまま使える |
| chunked JSON Lines（`application/x-ndjson`） | 可。ただし nginx・プロキシの取り扱いが `text/event-stream` ほど定型でなく、`X-Accel-Buffering` 以外の緩衝も踏みうる。SSE を採る |
| WebSocket | 不採用。認証・再接続・nginx 設定・サーバの同期スタイル（`def` エンドポイント）すべてに新しい面が増える |

同一オリジン（nginx の `/api/learning/` proxy 経由、`frontend/nginx.conf:15-21`）。CORS 設定は
増やさない。

### 2.2 イベントスキーマ

```
event: start
data: {"message_id":"<user msg id>","stance":null}

event: delta
data: {"t":"部分テキスト"}

event: final
data: {  ... LearningChatResponse と同一の JSON ... }

event: error
data: {"reason":"upstream"}     ← 実運用では出さない（§4.5。degraded を final で返す）
```

- `data:` は必ず **JSON1行**（本文の改行が SSE のフレーム境界を壊さないため）。
- `start` の `stance` は将来 `label_vocab.AI_READING_LABEL` 相当の固定ラベルを載せるための
  枠（v1 は常に `null`。学習チャットには現在ラベル方式が無い）。
- `final` は `LearningChatResponse.model_dump()` そのもの。**キーを間引かない**
  （ガードレールで全フィールド一致を固定する）。
- `error` は「`final` すら組み立てられなかった」場合のみの保険。通常の LLM 失敗は
  `final`（`degraded: true` + 固定文）で閉じる（ST5）。

### 2.3 nginx

`/api/learning/` の location（`frontend/nginx.conf:15-21`）は `proxy_buffering` を明示して
いない＝既定の **on** である。バッファされると delta が溜まってから届き、ストリームの意味が
消える。対処は次の2段で、**①だけで機能する**。

1. **アプリ側でレスポンスヘッダ `X-Accel-Buffering: no` を付ける**（nginx が公式に解釈して
   当該レスポンスのみバッファを切る）。nginx.conf を変更せずに済み、`/api/atlas` /
   `/api/indicators` / `/api/disclosure` のような「location 追加漏れで SPA フォールバックが
   index.html を 200 で返す」事故形（nginx.conf:73-77 のコメント）とも無縁である
   （`/api/learning/` の location は既にあるため、新パスでもフォールバックしない）。
2. 追加の堅牢化として、正規表現 location でストリームパスだけ `proxy_buffering off;` /
   `proxy_read_timeout 300s;` を明示してもよい（任意）。`proxy_read_timeout 120s`
   （nginx.conf:20）は**読み取り間隔**の上限なので、delta が流れている限り抵触しない。
   抵触するのは「最初の delta までが 120 秒を超える」場合だけで、これは①②の合計時間
   （§0）に対する現実的な上限として妥当なので、既定のままとする。

gzip は本 nginx.conf で有効化されていない（`gzip` ディレクティブ無し）ため、圧縮バッファの
問題は生じない。

---

## §3 サーバ設計

### 3.1 `core/llm.py::generate_text_stream`

```python
def generate_text_stream(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    timeout: float | None = None,
    usage_ctx: dict | None = None,     # ← §3.2 の contextvar 回避（重要）
) -> Iterator[str]:
```

- **モデル解決は入口で1回**（M1）。`generate_text` の :810-815 と同一の分岐
  （`model` 明示 > `resolve_scene_model(current_usage_context().feature)`）をそのまま写す。
  ただし呼び出し側（§3.3）は**具体的なモデル名を明示で渡す**運用にする。
- OpenAI 経路: `_adapt_messages_for_model`（llm.py:174）と `_build_api_kwargs`（llm.py:195）を
  そのまま使い、`stream=True` と `stream_options={"include_usage": True}` を足す。
  各 chunk の `choices[0].delta.content` を `yield` し、内部で全文を蓄積する。
  `usage` を持つ最終 chunk（`choices == []` で届く）は `yield` せず退避する。
- **openai 以外のプロバイダ**（`gemini` / `google` / `gemini-vertex`、config.py:47）は v1 では
  ストリームを実装せず、`generate_text(...)` の結果を **1個の delta として yield** する
  （挙動は「遅い1回」= 現状と同じ。UI は同じコードで動く）。将来の対応地点をここ1箇所に
  閉じ込める。
- **観測は `finally` で必ず1回**。正常終了・例外・呼び出し側の早期 `close()`（＝クライアント
  切断）のいずれでも `observe_chat` を通す（U2 / ST3）。詳細は §6。

### 3.2 contextvar がスレッドを跨がないことへの対処（実装上の要）

Starlette は同期ジェネレータを `iterate_in_threadpool` で回すため、**`next()` ごとに別スレッド
になりうる**。`usage_context` / `llm_policy.model_override` は contextvar なので、
「ジェネレータの中で `with` に入って yield し、あとで観測する」書き方は**壊れる**
（U層の帰属が `unattributed` に落ちる、コース単位モデル上書きが効かない）。

対処（採用）:

1. **モデルは呼び出し側で解決して具体名で渡す。** 既存 `_learning_chat_core` は
   `with ... _course_chat_override:` の内側で `resolve_model(...)` を呼んでいる
   （learning.py:3376-3393）。ストリーム経路では、この `with` ブロックの中で
   `llm_policy.resolve_scene_model(...)` により**実効モデル名を確定**し、その文字列を
   `generate_text_stream(model=...)` に渡す。contextvar はジェネレータの外で閉じる。
2. **U層帰属は値渡しにする。** ルートで `current_usage_context()` 相当の値
   （feature / user_id / course_id）を dict にして `usage_ctx=` で渡し、
   `generate_text_stream` の `finally` の中で `with usage_context(**usage_ctx):` を
   **観測の直前に開いて直後に閉じる**（1回の `next()`／`close()` の内側で完結するので
   スレッド越えが起きない）。`core/llm_usage/observe.py` は非改変。

不採用案: 生成を専用 producer thread に追い出して `queue.Queue` で受ける方式。正しく書けるが
リクエストスコープでスレッドを立てるのは本プロジェクトの thread 用法（tension worker /
V層スイーパ / ingest worker＝いずれも背景処理）と性格が違い、切断・例外・上限の面が増える。

### 3.3 ルートの分解（`_learning_chat_core` を割らずに共有する）

同じ前処理・後処理を2つのエンドポイントが**別々に持たない**ことが本設計の生命線である。
`api/routes/learning.py` の中で、モジュールレベル関数として3つに割る。

| 新関数 | 中身（現行の行範囲） | 返り値 |
|---|---|---|
| `_prepare_turn(course_id, topic_id, body, current_user, *, course_data, scope_document_ids)` | :2828（quota state）〜:3370（`_consume_quota()`）の全部。早期 return する経路（`return_to_learning_path` / `EXPLAIN_GRAPH_ELEMENT` / usage_help / atlas アクション / CHIT_CHAT / LEARNING_ADVICE / 前提知識ゲート）も含む | `TurnPlan`（生成に進む）または `LearningChatResponse`（LLM 本体を呼ばず確定した応答） |
| `_finalize_turn(plan, answer, *, degraded)` | :3406（`_reconcile_citation_markers`）〜:3577（`return LearningChatResponse(...)`） | `LearningChatResponse` |
| `_learning_chat_core` | `_prepare_turn` → （plan なら）`generate_text` → `_finalize_turn` | 現行と**同一** |

`TurnPlan` は dataclass（`messages` / `model` / `course_override` / `chat_feature` /
`cited_sources` / `overall_tier` / `content_grounding` / `topic_info` / `support_agent` /
`support_origin` / `_is_*` フラグ群 / `_seg` / `_scroll` / `_atlas_ctx` / `_discuss_scope` …）。
**保持するのは既存ローカル変数そのもの**で、意味を足さない。

**割るときの厳守事項（ガードレールが grep で固定している文字列がある）**:

- `window_history(body.history, max_messages=20, max_chars=2000)` の**逐語**が
  `learning.py` に残ること（`test_mirroring_prompt_guardrails.py:123` /
  `test_discuss_mode.py:276`）。
- `if _is_discuss:\n        _scaffold_user_instruction = (` と `messages: list[dict] = [` の
  **逐語とインデント**が残ること（`test_mirroring_prompt_guardrails.py:118-120`）。
  → 抽出先は**モジュールレベル関数**にする（`if` が 4 スペース・本体が 8 スペースのまま）。
  ネスト関数やクラスメソッドに移すとインデントが変わり落ちる。
- `api.routes.learning.generate_text` を patch する既存テスト（20 箇所）が通ること
  = `_learning_chat_core` が **`generate_text` をモジュール属性として呼び続ける**こと。

### 3.4 新エンドポイント

```python
@router.post("/courses/{course_id}/topics/{topic_id}/chat/stream")
def learning_chat_stream(...) -> StreamingResponse:
```

処理順（**この順序が ST2 の実体**）:

1. `settings.learning_chat_streaming_enabled` が false → 422 ではなく **404**
   （機能が存在しない状態を正直に返し、フロントは JSON 経路へ）。
2. `_prepare_turn(...)` を**同期に**呼ぶ。ここで 404 / 422 / 429 は通常の `HTTPException` と
   して出る（まだ 200 を返していないので、正しいステータスで返せる）。
3. 戻りが `LearningChatResponse`（LLM 非経由の確定応答）なら、**delta を1つも出さずに**
   `start` → `final` だけを流して閉じる。クライアントのコードパスを1本に保つため。
4. 戻りが `TurnPlan` なら `StreamingResponse(_frames(plan), media_type="text/event-stream",
   headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})` を返す。
5. `_frames` の中で `generate_text_stream` を回し、delta を送りつつ全文を蓄積 →
   `_finalize_turn(plan, answer, degraded=...)` → `final`。

`def`（同期）エンドポイントのままでよい。FastAPI は同期ジェネレータをスレッドプールで回す
ため、`services.py` の同期 SQLAlchemy セッションをそのまま使える（非同期化しない）。

document 直付け discuss（`document_discuss_chat`, learning.py:3789）は Phase 3-a では
**非対象**（コース経路が安定してから同じ委譲で足せる。`_learning_chat_core` を通す構造は
既に共有されているので、後から薄く足せる）。

### 3.5 タイムアウト・切断・停止

| 事象 | サーバの振る舞い | 学習者に見えるもの |
|---|---|---|
| LLM が最初の chunk を返す前に失敗 | 例外を捕捉 → `answer = _CHAT_DEGRADED_MESSAGE`（learning.py:241）/ `degraded=True` → `_finalize_turn` → `final` を流して 200 で閉じる（ST5・I3） | 既存の degraded バブルと同一 |
| LLM が途中で失敗（delta を何個か出したあと） | 同上。**部分テキストは捨てる**（`final.answer` は degraded 固定文）。クライアントはバブルを差し替える | 「AI 応答を生成できませんでした…」（既存と同じ文） |
| 学習者が停止ボタン（`AbortController`） | ジェネレータが `close()` され、`finally` で観測1件（`metadata.client_aborted=true`・推計）→ **`_finalize_turn` を呼ばない = 履歴も痕跡も書かない**（ST1/ST5） | 部分バブルを消し、送信した本文を入力欄へ戻す（§5.1）。事実文「途中で止めました。この応答は記録に残していません。」 |
| ネットワーク切断・タブ閉じ | 上と同じ（通知先が無いだけ） | — |

**quota は消費されたままにする**（ST2）。止めても呼び出しは起きているので、消費を巻き戻すのは
「出所の正直さ」に反する。ただし残数は表示しない（ST8）。

---

## §4 後処理との整合

### 4.1 各後処理の扱い

すべて**完成テキストに対してのみ**動く（ST1）。ストリーミング中の表示は「まだ整形されて
いない本文」であることを、UI の見た目（プレーンテキスト）で示す。

| 後処理 | 実装 | 完成テキストで実行 | ストリーム中の見え方 |
|---|---|---|---|
| 出典マーカー突合 `_reconcile_citation_markers` | learning.py:1443 / :3406 | ○ | `[出典3]` はそのままの素のテキストとして流れる（チップにはならない） |
| out_of_source 前置き | :3415 | ○ | 本文の前に**後から**挿入される（`final` でバブル全体を差し替えるので破綻しない） |
| 誤解候補の検出 | :3427 `detect_and_record_misconception`（非LLM 文字列一致） | ○ | 表示なし |
| ドリルダウン抽出 `extract_inline_actions` | `core/learning_support_agent.py:208` / learning.py:3553 | ○ | マーカーの生テキストが一瞬見える（`final` で除去＋チップ化）。§5.2 の段落バッファでほぼ隠れる |
| 鏡面化 `extract_mirror` | learning.py:3565 | ○ | 〔鏡〕マーカーが素で見える（同上） |
| `anchor_confirm` ゲート | :3520-3540 | ○ | `final` でのみ現れる |
| 履歴保存 `persist_chat_history` | :3434 | ○（1回だけ） | — |
| 痕跡 `record_interest_trace` + tension prefilter | :3474 / :3513 | ○ | — |
| `content_grounding` / `tier` / `sources` | :3200-3260（生成前に確定） | 生成前に確定済み | `final` で送る（ST6） |
| degraded | :3400 | ○ | §3.5 |

**DM4（応答末尾に必ず言い換え・問い返しを1つ）は完成テキストでしか検証できない**が、現行も
プログラム的な検証はしていない（`discussion_mode_design.md:521`「DM4 の遵守自体の計測は
非スコープ」）。ストリーミングは新しい検証義務を作らない。

**UC8（サイクルの骨格は非LLM・同期）** は影響を受けない。ELICIT の出題・選択式 DIFF・
着地・帰還の扉はいずれも非LLM の別経路で、AI モード（`cycle_mode`）だけが本チャットの
1コール地点に相乗りしている。ストリームが失敗しても骨格は degraded で閉じるだけで、
サイクルはストリームの成否に依存しない。

### 4.2 ST4（衛生）と ST7（非ストリーム不変）の両立

現在、学習チャットの回答本文に `strip_control_sequences`（`core/text_hygiene.py:51`）は
**掛かっていない**（learning.py は `UNTRUSTED_SOURCE_NOTICE` のみ import、:114）。掛ける対象は
チャンク本文（`services.get_chunk_passage`）と W層・グラフ対話の応答で、ガードレール
`test_pdf_trust_boundary_guardrails.py:443-463` もその3経路だけを固定している。

したがって:

- **delta（表示に流す文字列）には `strip_control_sequences` を掛ける**。資料由来の
  制御シーケンスがそのまま画面に出るのを防ぐ（TB4 の精神）。
- **`final.answer` には掛けない**。掛けると非ストリーム版と値が食い違い、ST7（`final` は
  非ストリーム版と同値）と履歴の一貫性が壊れる。
- chunk 境界でエスケープ列が割れる問題は、**末尾 16 文字を次の chunk までバッファ**して
  から衛生を掛けることで回避する（フラッシュは終端で行う）。
- 「回答本文そのものにも衛生を掛ける」是正は**別件・非スコープ**。やるなら
  `_finalize_turn` の1箇所で**両エンドポイントに同時に**掛ける（片側だけに入れない）。

---

## §5 フロント

### 5.1 送信・停止

`sendMessage`（app.js:4213）に分岐を足す。フラグ（サーバから配られる `streaming` 可否か、
起動時 1 回の設定取得）が真かつ通常のテキスト送信のときだけストリーム経路を使い、
それ以外（音声・casual、`replace_message_id` 付きの書き直し、typed action）は従来どおり
JSON 経路（app.js:4290）。

- 送信直後に `state.sending = true`（:4239）→ **タイピングインジケータの代わりに**
  空のストリーミングバブル `<div class="mg ai streaming">` を `#chat-area` に**直接 append**
  する。`renderChat()`（app.js:776）は毎回 `ca.innerHTML = html` で全再構築し、出典チップ・
  数式・各種リスナを貼り直す（:855-930）。delta ごとにこれを呼ぶのは論外なので、
  **ストリーミング中は `renderChat()` を呼ばない**。
- delta は `bubble.textContent += t` のみ（HTML 挿入をしない＝エスケープ不要）。
  CSS は `white-space: pre-wrap`。
- `final` を受けたら、従来と同じ形で `state.chatMessages.push({...})`（app.js:4329 と同一の
  フィールド）→ ストリーミングバブルを除去 → `renderChat()` を**1回だけ**呼ぶ。
  これでマークダウン・KaTeX（:930 の `renderMathInElement`）・出典チップ・ドリルダウン・
  鏡ブロック・`anchor_confirm` が一度に正しい形になる。
- 停止ボタン: 送信ボタンを送信中だけ「停止」に差し替え、`AbortController.abort()`。
  中断時はストリーミングバブルを消し、`state.chatMessages` から**当該 user メッセージを
  取り除いて**本文を入力欄へ戻す（書き直し `startEditMessage` と同じ作法）。
  こうしないと、次の送信で送る `history`（app.js:4292）にサーバが知らない片肺の往復が
  混ざり、`persist_chat_history` の対の整合が崩れる。

### 5.2 部分マークダウン・数式の扱い

Phase 3-a は **プレーンテキスト表示に限定する**（段落単位の中間レンダリングをしない）。

- 未完の `$x^2` / `**強調` / `- 箇条書き` の途中は、レンダラに掛けると壊れるか、掛かって
  すぐ形が変わってちらつく。
- `renderAiContent`（app.js:1655）は LaTeX 退避 → HTML エスケープ → ブロック整形 → KaTeX 復元、
  の順で**全文前提**の変換をしており、途中文字列に対する保証が無い。
- 任意の追加改良（Phase 3-a′）: 空行（`\n\n`）で確定した**段落だけ**を `renderAiContent` に
  通し、末尾の未確定段落はプレーンのまま追記する。実装するならこの規則1つに限定し、
  「最後に全体を `final` で再描画する」ことは変えない。

### 5.3 1画面レイアウトを壊さない

`test_learning_layout_static.py` が固定する規律に触れない:

- ストリーミングバブルは `.ca`（`frontend/public/css/styles.css:264-272`、`flex:1 1 0` /
  `min-height:120px`）の**内側**に増えるだけで、新しい行を下段に足さない
  （下段は `flex: 0 0 auto` を維持、テスト :80-83）。
- 停止ボタンは既存の送信ボタンの**差し替え**（新規の帯を作らない）。
- 自動スクロールは「利用者がすでに最下部付近（40px 以内）にいるときだけ
  `ca.scrollTop = ca.scrollHeight`」。生成中に読み返している最中の強制スクロールをしない。
  `_pendingScrollMsgId`（app.js:4384）の「新しい問いの先頭へ着地」挙動は `final` 後の
  `renderChat()` でそのまま効く（変更しない）。

### 5.4 音声・casual

Phase 3-a では**従来どおり**（完成テキスト → `speakVoiceAnswer`、app.js:5205）。
ハンズフリーは「聞こえ始めるまで」が体感であり、テキストの逐次表示は効かない。文単位 TTS は
Phase 3-c で、句点区切り × TTS 呼び出し回数の増加（U層 `learning:voice_tts` の実測）を
見てから判断する。

---

## §6 U層計測

- **`operation` は `"chat"` のまま**。`llm_usage_events.operation` は
  `CHECK (operation IN ('chat','structured','embedding','vision','transcribe','tts'))`
  （`backend/db/043_llm_usage_events.sql:19-20`、語彙の正本は
  `core/llm_usage/schema.py` の `OPERATIONS`）。`"stream"` を足すと migration が要る。
  **足さない**（ストリームは転送方式であって操作種別ではない）。
- **ストリームである事実は `metadata` に**: `{"streamed": true}`、中断時は
  `{"streamed": true, "client_aborted": true}`。`UsageEvent.metadata`（schema.py 末尾）は
  自由 JSONB で、`estimated` 経路が既に `{"reasoning_excluded": true}` を入れている
  （observe.py:196）ので前例どおり。
- **reported（実測）**: OpenAI は `stream_options={"include_usage": True}` を付けると
  **最後に `choices == []` かつ `usage` を持つ chunk** を送る。この chunk をそのまま
  `observe_chat(response=<最終chunk>, response_text=<蓄積全文>)` に渡せば、既存の
  `_extract_openai_usage`（observe.py:46）がそのまま通り、`usage_source="reported"` になる。
  **observe.py は非改変**。
- **estimated（推計）フォールバック**: 最終 chunk が来ない場合（中断・プロバイダ非対応・
  古い SDK）は `response=None` で渡す。observe.py:190-217 の既存フォールバックが
  入力を tokenizer/heuristic 推計、出力を蓄積テキストから推計して
  `estimated_tokenizer` / `estimated_heuristic` として記録する。**U1 の分離集計はそのまま
  効く**（reported と混ぜない）。
- **必ず1件・必ず1回**。`generate_text_stream` の `finally` に置く。例外時は既存の
  `observe_chat(error=exc)` 経路（`success=False`）と同じ形にする。
- `feature` は既存語彙のまま（`learning:chat` / `learning:chat_discuss` /
  `learning:chat_casual` / `learning:cycle_elicit` / `learning:cycle_diff`。
  learning.py:3360-3368）。**新 feature を作らない**（`KNOWN_FEATURES` を増やさない）。
- 教員向け見積り（`GET /api/admin/llm-usage/estimate/...`）とダッシュボードは無変更。
  ストリーム由来行が推計に寄る場合は `usage_source` 別の内訳にそのまま現れる（U1・U8）。

---

## §7 段階導入

| 段 | 範囲 | 追加で要るもの |
|---|---|---|
| **3-a** | 学習チャット本文（通常質問 / discuss / cycle elicit・diff）のテキスト経路 | `generate_text_stream` / `_prepare_turn` `_finalize_turn` / `/chat/stream` / app.js の逐次バブル |
| **3-b** | グラフ要素の説明（`_generate_graph_element_explanation` の LLM 分岐、learning.py:1803） | 生成後の数式再注入が本文依存なので、3-a と同じ「delta は素・`final` で差し替え」で吸収。承認済み分岐は即返しのまま |
| **3-c** | 文単位 TTS（音声・casual） | 句点分割 + TTS 逐次再生。U層 `learning:voice_tts` の実測を見てから判断（**着手時は本書に §追補、または別設計書**） |
| **3-d** | 教員側 W層 対話・グラフ全体対話 | structured output の `reply` フィールド部分パース。`core/llm_worker/chat_turn.py::structured_turn` は**触らず**、隣に `structured_turn_stream` を足す（同期契約・degraded 規約・`spoken_variant` の扱いをバイト単位で維持） |

フラグ: `LEARNING_CHAT_STREAMING_ENABLED`（`core/config.py` に `bool = Field(default=False,
validation_alias=AliasChoices("LEARNING_CHAT_STREAMING_ENABLED"))`。既存の
`llm_usage_tracking_enabled`（config.py:552）と同じ作法）。off のとき
`/chat/stream` は 404、フロントは従来経路のみ。

---

## §8 vision §6（14 原則）照合

| # | 原則 | 本設計での扱い |
|---|---|---|
| 1 | AIは候補まで・確定は人間 | 変更なし。ストリームは表示の先行で、確定（誤解候補の本人3択・帰属確認・痕跡）はすべて `final` 後の既存経路（ST1/ST6） |
| 2 | evidence-based | 変更なし。出典突合・`sources` は完成テキストで実行（ST1） |
| 3 | 情報を落とさない | 中断ターンを保存しないのは**知識オブジェクトの削除ではなく「起きなかった往復」**の扱い。既存の書き直し（truncate + supersede）と同型。§10 のオーナー確認1件 |
| 4 | 数値の用途と粒度を統治 | ST8。トークン・秒・残数を学習者に出さない。U層の集計軸は不変（新 feature・新 operation を作らない） |
| 5 | 監視しない | 変更なし。痕跡の種類・可視性は不変 |
| 6 | egocentric のみ | 該当なし |
| 7 | リンクであってマージではない | 該当なし |
| 8 | 出所の正直さ | `content_grounding` / tier / out_of_source 前置きは `final` でそのまま。中断でも quota 消費を巻き戻さない。プロバイダ非対応時に「ストリームのふり」をしない（1 delta で返すだけ） |
| 9 | 同期パスに LLM を入れない | **注意点**。ストリームは同期パスの LLM を増やさない（コール数は不変・1往復1コール）。骨格（UC8）はストリームの成否に依存しない（§4.1） |
| 10 | 完了フラグを持たない | 該当なし（新しい状態を保存しない） |
| 11 | fail-closed | 権限・可視性の判定はすべて `_prepare_turn`（＝現行コードそのもの）の内側。ストリームは 200 を返す**前**に権限判定を終える（§3.4 手順2） |
| 12 | 押し付けない | 自動再生・自動スクロール強制をしない（§5.3）。停止はいつでもできる |
| 13 | 層は積層し下層を改変しない | `core/llm.py` に**追加**（既存3関数は非改変）。observe.py・llm_policy.py は非改変。learning.py の分解は**外部から見た挙動を変えない**リファクタで、ガードレールの逐語も維持（§3.3） |
| 14 | 監査必須・帰属必須 | U層1行を必ず記録（ST3）。学習チャットは `theory_review_events` の対象外（現行どおり） |

---

## §9 ガードレール案

新規 `backend/tests/test_llm_streaming_{core,api,guardrails,ui_static}.py`。

**core**
- `generate_text_stream` が `core.llm` にあり、`fastapi` を import しないこと。
- openai 経路で `stream=True` と `stream_options={"include_usage": True}` の**両方**を渡すこと
  （ソース検査）。
- 最終 usage chunk があれば `usage_source="reported"`、無ければ `estimated_*` になること
  （fake client）。**両方の場合で `observe_chat` がちょうど1回**呼ばれること。
- 途中で `close()`（中断）しても観測が1回・`metadata.client_aborted` が立つこと。
- 非 openai プロバイダで例外を出さず1 delta にフォールバックすること。
- `metadata` に `streamed` を入れる一方、`operation` は `"chat"` のまま
  （`OPERATIONS` に `"stream"` を足していないことをソースで固定）。

**api**
- **非ストリーム版の応答が変わらないこと**: 既存の学習チャットテスト群が無改変で緑
  （patch seam `api.routes.learning.generate_text` を維持）。加えて、同一入力に対する
  `/chat` の JSON と `/chat/stream` の `final` が**キー集合・値ともに一致**すること。
- `final` イベントの JSON が `LearningChatResponse` の**全フィールド**を含むこと
  （`model_fields` と突き合わせる網羅テスト）。
- quota 超過時は **1バイトも delta を出さずに 429**（`StreamingResponse` に入る前に落ちる）。
- LLM 例外時に 200 + `final.degraded == true` + 固定文（`_CHAT_DEGRADED_MESSAGE`）で、
  `persist_chat_history` が1回呼ばれること。
- **中断時に `persist_chat_history` / `record_interest_trace` が呼ばれない**こと。
- `X-Accel-Buffering: no` と `media_type == "text/event-stream"` が付くこと。
- フラグ off で 404。
- 権限・可視性: 未受講コース・不可視 document で `/chat/stream` が 200 の中ではなく
  404/403 で落ちること（`_prepare_turn` を通ることの証明）。

**guardrails**
- delta 経路に `strip_control_sequences` が入っていること（ソース検査、ST4）。
- delta のペイロードに本文以外のキー（`sources` / `tier` / `confidence` 等）が
  載らないこと（ST6・ST8）。
- 学習者に届く文字列にトークン数・秒数・残回数の数字が現れないこと（禁止語彙 + 数字パターン、
  ST8）。
- `learning.py` に §3.3 の逐語3点（`window_history(...)` / `if _is_discuss:` の scaffold /
  `messages: list[dict] = [`）が残っていること（既存テストの再掲・分解の回帰検出）。
- `_prepare_turn` / `_finalize_turn` が**モジュールレベル関数**であること（AST 検査。
  インデント逐語の前提）。

**ui_static**
- `app.js` にストリーミング中の `renderChat()` 呼び出しが無いこと（delta ごとの全再構築禁止）。
- delta の適用が `textContent`（`innerHTML` でない）であること。
- 停止時に当該 user メッセージを `state.chatMessages` から取り除き、入力欄へ戻すこと。
- `styles.css` の下段 `flex: 0 0 auto` / `.ca { min-height: 120px }` が不変
  （`test_learning_layout_static.py` の再掲で足りる）。

---

## §10 非スコープ・オーナー判断

### 非スコープ（v1）

確認問題（構造化 JSON）のストリーミング / コースビルダー（`---COURSE_DRAFT_JSON---`）/
Copilot / 意図分類・embedding 検索の短縮 / トークン単位の word timestamp / SSE の再接続・
`Last-Event-ID` による再開 / 複数タブでの同一ターン共有 / 回答本文への
`strip_control_sequences` 適用（§4.2。やるなら両エンドポイント同時の別件）/
document 直付け discuss のストリーム化（3-a の後に薄く足す）。

### オーナー判断（1件）

- **O-1 中断・失敗した往復を保存しないこと。** 本設計は「途中で切れた応答は履歴にも痕跡にも
  残さず、学習者の発話は入力欄へ戻す」を推奨する（ST1/ST5）。原則3「情報を落とさない」を
  「起きなかった往復も残す」と読むなら逆の設計（部分応答を `degraded` 相当で保存）もあり得る。
  **推奨は非保存** — 部分テキストは AI の未完成の出力であって学習者の産出ではなく、これを
  履歴に残すと次ターンの `window_history` に半端な文脈が入り、`persist_chat_history` の
  対の整合も崩れるため。

これ以外（転送方式・分解の粒度・フラグ名・段階の順序）は実装者判断で足りる。

---

## §11 migration

**不要。** 新テーブル・新列・CHECK 変更・シードのいずれも無い。`llm_usage_events` は
`operation='chat'` のまま既存行と同じ形で書き込み、ストリームであることは `metadata` JSONB に
入れる（§6）。設定は環境変数1本（`LEARNING_CHAT_STREAMING_ENABLED`）のみ。

---

## §12 実装記録

（未実装。実装時にここへ追記する。）

---

## 付録A 変更予定ファイル

| ファイル | 変更 |
|---|---|
| `backend/core/llm.py` | **追加**: `generate_text_stream`（既存3関数は非改変） |
| `backend/core/config.py` | **追加**: `learning_chat_streaming_enabled` |
| `backend/api/routes/learning.py` | **分解**: `_prepare_turn` / `_finalize_turn` の抽出（挙動不変）。**追加**: `learning_chat_stream` |
| `backend/api/schemas.py` | 変更なし（`LearningChatResponse` をそのまま `final` に流す） |
| `frontend/public/js/app.js` | `sendMessage` にストリーム分岐・逐次バブル・停止ボタン |
| `frontend/public/css/styles.css` | `.mg.ai.streaming { white-space: pre-wrap }` 程度 |
| `frontend/nginx.conf` | **原則変更なし**（`X-Accel-Buffering: no` で足りる。任意の堅牢化のみ §2.3） |
| `backend/tests/test_llm_streaming_*.py` | 新規4本（§9） |
| `docs/features/learning.md` / `docs/backend/rag-chat.md` | 学習者向け機能の変更として同時更新（`development_checklist.md` §5-1 の表） |
| `docs/backend/api.md` | 新エンドポイント1本の追記（ルーター新設は無いが経路が増える） |

**非改変**: `core/llm_usage/*`（observe / recorder / estimator / schema）、`core/llm_policy.py`、
`core/llm_worker/chat_turn.py`、`src/episteme_graph/agents/**`（A層）、`backend/db/**`。

## 付録B 着手前チェック（`docs/development_checklist.md` §5）

- [ ] 本書を索引（`docs/README.md` / `docs/architecture/layer_registry.md` / `CLAUDE.md` の
      いずれか）から参照する。**未参照だと `backend/tests/test_docs_registry_guardrails.py`
      の孤児検出（:274）が落ちる**（本書は起草のみで索引を触っていないため、
      **実装 PR で必ず対応すること**）。
- [ ] 状態ヘッダ（冒頭）を実装完了時に「実装済み（正本・凍結）」へ更新する。
- [ ] 想定 migration 番号を書かない（本書は「不要」と明記）。
- [ ] §5-1 の表に従い `docs/features/learning.md` / `docs/backend/rag-chat.md` /
      `docs/backend/api.md` を同じ PR で更新する。
