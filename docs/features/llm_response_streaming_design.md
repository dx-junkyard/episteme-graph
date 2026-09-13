# LLM 応答のストリーミング（Phase 3）設計書

> **状態:** 実装済み（正本・凍結）2026-09-12 — **Phase 3-a のみ**（学習チャット本文のテキスト
> 経路）。3-b（グラフ要素の説明）/ 3-c（文単位 TTS）/ 3-d（W層・グラフ全体対話）は**未着手**。
> **migration 不要**（新テーブル・新列・CHECK 変更なし）。以後は §12 実装記録の追記のみ
> （本文 §0〜§11 は設計時点の歴史記録であり、実装との差分は §12 に列挙する）。
>
> 本文の `learning.py:NNNN` / `app.js:NNNN` は **2026-09-12（Phase 1/2 実装後・本実装前）時点の
> 行番号**。境界の定義は行番号ではなく名前付きの目印（関数名・逐語）で行う（§3.3）。

本書は UX ロードマップ全3フェーズの **Phase 3**（応答の到着を待たせない）の設計である。
順序と依存の正本は [AI アシスタント UX ロードマップ](../architecture/assistant_ux_roadmap_2026-09-12.md)。
Phase 1 = [学習チャットの入口統合](learning_chat_entry_unification_design.md)、
Phase 2 = [画面文脈アダプター §11（SA層 Phase 4）](assistant_screen_adapter_design.md)。
**Phase 1 / Phase 2 はいずれも 2026-09-12 実装済みで、本書が次の段**である。
本書は Phase 1/2 の成果に依存せず単独で実装できる（触る地点が入口・grounding ではなく
**生成の転送方式**だけのため）。

---

## §0 現状（2026-09-12 時点の実測）

**ストリーミングは1箇所も無い。**

- `backend/core/llm.py` の公開テキスト生成は `generate_text`（:779）/
  `generate_text_with_structured_output`（:888）/ `generate_conversation_turn`（:1479）の
  3本で、いずれも**完成した文字列を返す**。OpenAI 経路は
  `client.chat.completions.create(...)`（:859）を呼び、`response.choices[0].message.content`
  を取り出して（:875）返す。`stream=True` はどこにも無い。
- `backend/api/` に存在する `StreamingResponse` は**エクスポートの ZIP ダウンロードだけ**
  （`routes/export.py:13, 3185, 3370`）。既に `io.BytesIO` に完成した bytes を渡しており、
  逐次生成ではない。
- 学習者の1往復は直列連鎖である。`_learning_chat_core`（`api/routes/learning.py:3110`）は
  ① 意図分類 LLM（`_classify_intent`）→ ② embedding 検索
  （:3547 `search_chunks_with_metadata`）→ ③ 本体生成（:3800 `generate_text`）→
  ④ 後処理（:3816 `_reconcile_citation_markers` 〜 :4032 `return LearningChatResponse(`）の順に走り、**④が終わるまで1バイトも返らない**。

体感される沈黙は「①+②+③の合計」である。③は analysis tier の長文回答（
`resolve_model("learning_chat_llm_model", fallback="analysis")`、:3803）なので、支配項は
③の**生成完了待ち**であり、ここだけが先出しできる。①②は先出しできない（分類結果と
検索結果が無ければプロンプトが組めない）。したがって本設計の効果は
**「最初の1文字までの時間」を③の完了時刻から③の開始直後へ前倒しすること**に限られる。
①②の短縮は本書の対象外である（対象外であることを正直に書く）。

### 恩恵のある面／無い面

| 面 | 現在の呼び出し | 判定 |
|---|---|---|
| 学習チャット本文（質問 / discuss / cycle elicit・diff） | `generate_text`（learning.py:3800）1コール・長文 | **Phase 3-a で対象** |
| グラフ要素の説明（要素タップ） | `_generate_graph_element_explanation`（learning.py:2006, 生成は :2108） | Phase 3-b で対象（承認済み説明の即返し分岐は元々待ち時間ゼロ） |
| 確認問題の並置（`check_topic_understanding`, learning.py:2475） | 構造化 JSON（`LearningCheckObservation` の配列） | **対象外**。部分 JSON は表示できない |
| 音声・casual（ハンズフリー） | 完成テキスト → TTS（app.js:5454 `speakVoiceAnswer`） | Phase 3-a では**非対象**。文単位 TTS は Phase 3-c で別途検討 |
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
- **ST2 CostGate は最初の1バイトより前に消費する。** `_consume_quota()`（learning.py:3714）は
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
  （learning.py:3094）のリクエスト・レスポンス・処理順序・**テストの patch seam**
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
data: {"stance":{"stance":"tutor","source":"inferred","label":"…"}}

event: delta
data: {"t":"部分テキスト"}

event: final
data: {  ... LearningChatResponse と同一の JSON ... }

event: error
data: {"reason":"upstream"}     ← 実運用では出さない（§4.5。degraded を final で返す）
```

- `data:` は必ず **JSON1行**（本文の改行が SSE のフレーム境界を壊さないため）。
- `start` に `message_id` は載せない（この時点ではまだ `persist_chat_history` 前で user メッセージの
  id をサーバが持たない。偽の値を作らない — 実装時に確定）。
- `start` の `stance` は **入口統合 Phase 1 の `LearningChatResponse.stance`（`{stance, source,
  label}`）と同一の DTO** を載せる（2026-09-12 追随。起草時の「学習チャットにはラベル方式が
  無い」は Phase 1 実装で古くなった）。様相の解決 `resolve_stance(...)` は回答本文に依存しない
  （入力は `cycle_mode` / `_is_discuss` / `_is_casual` / `_explicit_casual` / typed action /
  atlas_context の6つで、いずれも生成前に確定している）ので、生成前に1回解決して `start` に
  載せ、`final.stance` には**同じ値**を入れる（`start` と `final` の `stance` は常に一致 —
  ガードレールで固定）。これによりフロントは様相の1行（`chat.stance-chip`）を本文到着前に
  出せる。解決が不能な経路（LLM 非経由の確定応答）では `null`。
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

- **モデル解決は入口で1回**（M1）。`generate_text`（llm.py:810-815）と同一の分岐
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
   （learning.py:3778-3804）。ストリーム経路では、この `with` ブロックの中で
   `llm_policy.resolve_scene_model(...)` により**実効モデル名を確定**し、その文字列を
   `generate_text_stream(model=...)` に渡す。contextvar はジェネレータの外で閉じる。
2. **U層帰属は値渡しにする。** 呼び出し側で `_chat_feature` / `user_id` / `course_id` を
   dict にして `usage_ctx=` で渡し、
   `generate_text_stream` の `finally` の中で `with usage_context(**usage_ctx):` を
   **観測の直前に開いて直後に閉じる**（1回の `next()`／`close()` の内側で完結するので
   スレッド越えが起きない）。`core/llm_usage/observe.py` は非改変。

不採用案: 生成を専用 producer thread に追い出して `queue.Queue` で受ける方式。正しく書けるが
リクエストスコープでスレッドを立てるのは本プロジェクトの thread 用法（tension worker /
V層スイーパ / ingest worker＝いずれも背景処理）と性格が違い、切断・例外・上限の面が増える。

### 3.3 継ぎ目の置き方（`_learning_chat_core` を割らず、生成器にする）

同じ前処理・後処理を2つのエンドポイントが**別々に持たない**ことが本設計の生命線である。
起草時は `_prepare_turn` / `_finalize_turn` への3分割を想定したが、2026-09-12 の現物確認で
**関数本体の字面を固定する既存ガードレールが多い**ことが判明したため、**分割せず
`_learning_chat_core` を生成器（generator）にする**方式へ確定した（分解の粒度は実装者判断 —
§10）。

**固定されている字面（分割方式が落ちる理由）**:

- `_learning_chat_core` の**関数本体**を `extract_function_source(_LEARNING_SRC,
  "_learning_chat_core")` で切り出して字面検査するテストが **7 本**ある
  （`test_learning_stance_guardrails.py` / `test_discuss_mode.py` / `test_discuss_guardrails.py` /
  `test_descent_guardrails.py` / `test_document_discuss_guardrails.py` /
  `test_anchor_ladder_hint.py` / `test_check_options_ui_static.py`）。本体を別関数へ移すと
  検査対象から外れて落ちる。
- `test_mirroring_prompt_guardrails.py:284, 290` は `def learning_chat(` から**次の `\n@router`
  まで**を1塊としてスライスし、`if _is_discuss and not degraded:` /
  `clean_answer, _mirror = extract_mirror(clean_answer, body.message)` / `mirror=_mirror` と、
  `persist_chat_history(` `record_interest_trace(` が `extract_mirror(` より前にあることを求める。
  → `learning_chat` と `_learning_chat_core` の**間に `@router` を挟まない**（新しいルートは
  `_learning_chat_core` の**後**に置く）。
- 逐語3点: `window_history(body.history, max_messages=20, max_chars=2000)` /
  `if _is_discuss:\n        _scaffold_user_instruction = (` / `messages: list[dict] = [`
  （`test_mirroring_prompt_guardrails.py:118-123` / `test_discuss_mode.py:276`）。
- `test_discuss_mode.py:77-83`: `_is_discuss = (body.intent_mode or "").strip() == "discuss"` が
  `_is_casual` 定義の直後 300 文字以内。
- `api.routes.learning.generate_text` を patch する既存テスト（20 箇所）→ 非ストリーム経路は
  **`generate_text` をモジュール属性として呼び続ける**。

**採用方式 — 生成器の継ぎ目**:

| 要素 | 内容 |
|---|---|
| `_learning_chat_core(..., *, course_data=None, scope_document_ids=None, stream: bool = False)` | **generator 関数**にする。本体は現行のまま（1関数・同じ字面）。生成の直前に `yield ("start", stance_dto)` を1回、`stream=True` のときだけ本文 delta を `yield ("delta", text)`、最後に `return LearningChatResponse(...)`（StopIteration.value）。LLM 非経由の確定応答（`return_to_learning_path` / `EXPLAIN_GRAPH_ELEMENT` / usage_help / atlas / LEARNING_ADVICE / 前提知識ゲート）は**1度も yield せず** `return` する |
| `_run_learning_turn(gen) -> LearningChatResponse` | 同期ドライバ。`next()` を回して StopIteration の `.value` を返すだけ（イベントは捨てる）。**モジュールレベル関数** |
| `learning_chat`（既存 route） | `return _run_learning_turn(_learning_chat_core(course_id, topic_id, body, current_user))`。呼び出しの逐語 `_learning_chat_core(course_id, topic_id, body, current_user)` を残す（`test_document_discuss_guardrails.py:207`） |
| `document_discuss_chat` | 同じドライバで包む（`stream=False`。Phase 3-a では非対象） |
| `_stream_answer(messages, *, model, usage_ctx) -> Generator[tuple, None, str]` | モジュールレベル generator。`core.llm.generate_text_stream(...)` を回して `("delta", t)` を yield し、全文を蓄積して `return` する。**このモジュールで `generate_text_stream` を呼ぶのはここ1箇所** |

生成ブロックの書き換え（現行 :3778-3811。挙動不変の部分は字面を変えない）:

```python
    try:
        with usage_context(_chat_feature, user_id=current_user["id"], course_id=course_id), _course_chat_override:
            # M1: 実効モデルは contextvar（コース上書き）の内側で1回だけ確定する
            _effective_model = resolve_model("learning_chat_llm_model", fallback="analysis")
            if not stream:
                answer = generate_text(
                    messages=messages,
                    temperature=0.3,
                    model=_effective_model,
                )
        if stream:
            # contextvar の外で yield する（§3.2。Starlette は next() ごとに context を複製する）
            answer = yield from _stream_answer(
                messages,
                model=_effective_model,
                usage_ctx={"feature": _chat_feature, "user_id": current_user["id"], "course_id": course_id},
            )
    except Exception:
        ...  # 既存の degraded 縮退（不変）
```

- `yield` は **`with usage_context(...)` / `model_override(...)` の外**に置く。生成器の
  `next()` ごとに anyio が `copy_context()` した別スレッドで再開されるため、`with` を跨いだ
  yield は `ContextVar.reset()` の token 不一致で落ちる（§3.2 の実体）。ガードレールは
  「`yield` が `with usage_context` ブロックの内側に無い」ことを AST で固定する。
- クライアント切断で route 側の frame generator が `close()` されると、`GeneratorExit` が
  `yield from` の地点に届く。`except Exception` は `GeneratorExit`（BaseException）を捕まえ
  ない → 後処理（保存・痕跡）へ進まず終わる（ST1/ST5 の非保存はこの構造で成立する）。
  `generate_text_stream` の `finally` で観測1件が走る（§6）。
- **`start` イベントの stance**: 現行 :3891 の `resolve_stance(...)` ブロックを生成の直前
  （`_consume_quota()` と Phase 4 の画面文脈注入の**後**）へ移し、`_stance` /
  `_stance_source` を `start` と `final` の両方で使う。移動先は入力6引数がすべて確定した
  後であればよい（`_is_casual` の CHIT_CHAT 再代入は分類時点で済んでいる）。
- **Phase 4（SA層）の注入は継ぎ目の前**: `_consume_quota()`（:3714）→ 画面文脈・選択ブロック
  で `messages[-1]` を組み替え → `structured_grounding_present` 記録（:3716-3776）までが
  「前処理」で、`start` の yield はこの後。`_stream_answer` に渡す `messages` は注入後のもの。
- ローカル変数を dataclass に写す作業は**発生しない**（起草時の `TurnPlan` は不要）。

### 3.4 新エンドポイント

```python
@router.post("/courses/{course_id}/topics/{topic_id}/chat/stream")
def learning_chat_stream(...) -> StreamingResponse:

@router.get("/client-features")
def learning_client_features(...) -> dict:   # {"chat_streaming": bool}
```

処理順（**この順序が ST2 の実体**）:

1. `settings.learning_chat_streaming_enabled` が false → 422 ではなく **404**
   （機能が存在しない状態を正直に返し、フロントは JSON 経路へ）。
2. `gen = _learning_chat_core(course_id, topic_id, body, current_user, stream=True)` を作り、
   **`first = next(gen)` を同期に呼ぶ**。前処理の 404 / 422 / 429 は通常の `HTTPException`
   としてここで出る（まだ 200 を返していないので、正しいステータスで返せる）。
3. `next()` が `StopIteration` で終わった（LLM 非経由の確定応答）なら、**delta を1つも出さずに**
   `start`（`stance` は `response.stance`）→ `final` だけを流して閉じる。クライアントの
   コードパスを1本に保つため。
4. `first == ("start", stance_dto)` なら `StreamingResponse(_sse_frames(gen, first),
   media_type="text/event-stream", headers={"X-Accel-Buffering": "no",
   "Cache-Control": "no-cache"})` を返す。
5. `_sse_frames` は `start` フレーム → 以降の `("delta", t)` を衛生（§4.2）して `delta`
   フレーム → `StopIteration.value` を `final` フレーム（`model_dump()`）にして閉じる。
   `final` すら組めない例外は `error` フレーム（§2.2）。`GeneratorExit` は `gen.close()` へ
   伝える。

`def`（同期）エンドポイントのままでよい。FastAPI は同期ジェネレータをスレッドプールで回す
ため、`services.py` の同期 SQLAlchemy セッションをそのまま使える（非同期化しない）。

**フラグの配布**: `GET /api/learning/client-features` は認証必須・`{"chat_streaming":
<bool>}` だけを返す（設定値の鏡。数値なし）。フロントはログイン後1回だけ取得し、取得失敗は
`false` 扱い（fail-to-current）。既存の `/api/auth/me`（`UserOut`）には混ぜない。

document 直付け discuss（`document_discuss_chat`, learning.py:4223）は Phase 3-a では
**非対象**（`_run_learning_turn` で包むだけ。`_learning_chat_core` を通す構造は既に共有されて
いるので、後から `stream=True` の入口を薄く足せる）。

### 3.5 タイムアウト・切断・停止

| 事象 | サーバの振る舞い | 学習者に見えるもの |
|---|---|---|
| LLM が最初の chunk を返す前に失敗 | 既存の `except Exception` が捕捉 → `answer = _CHAT_DEGRADED_MESSAGE`（learning.py:272）/ `degraded=True` → 既存の後処理 → `final` を流して 200 で閉じる（ST5・I3） | 既存の degraded バブルと同一 |
| LLM が途中で失敗（delta を何個か出したあと） | 同上。**部分テキストは捨てる**（`final.answer` は degraded 固定文）。クライアントはバブルを差し替える | 「AI 応答を生成できませんでした…」（既存と同じ文） |
| 学習者が停止ボタン（`AbortController`） | ジェネレータが `close()` され、`finally` で観測1件（`metadata.client_aborted=true`・推計）→ **`GeneratorExit` が `except Exception` を素通りし後処理へ進まない = 履歴も痕跡も書かない**（ST1/ST5・§3.3） | 部分バブルを消し、送信した本文を入力欄へ戻す（§5.1）。事実文「途中で止めました。この応答は記録に残していません。」 |
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
| 出典マーカー突合 `_reconcile_citation_markers` | learning.py:1500 / :3816 | ○ | `[出典3]` はそのままの素のテキストとして流れる（チップにはならない） |
| out_of_source 前置き | :3627 付近（生成前に確定・本文に前置） | ○ | 本文の前に**後から**挿入される（`final` でバブル全体を差し替えるので破綻しない） |
| 誤解候補の検出 | :3837 `detect_and_record_misconception`（非LLM 文字列一致） | ○ | 表示なし |
| ドリルダウン抽出 `extract_inline_actions` | `core/learning_support_agent.py` / learning.py:3260 付近 | ○ | マーカーの生テキストが一瞬見える（`final` で除去＋チップ化）。§5.2 の段落バッファでほぼ隠れる |
| 鏡面化 `extract_mirror` | learning.py:3995 | ○ | 〔鏡〕マーカーが素で見える（同上） |
| `anchor_confirm` ゲート | :4027 付近 | ○ | `final` でのみ現れる |
| 履歴保存 `persist_chat_history` | :3844 | ○（1回だけ） | — |
| 痕跡 `record_interest_trace` + tension prefilter | :3884 `judge_tension_hint` 以降 | ○ | — |
| `content_grounding` / `tier` / `sources` | 生成前に確定（`search_chunks_with_metadata` :3547 の直後） | 生成前に確定済み | `final` で送る（ST6） |
| degraded | :3805-3811 | ○ | §3.5 |

**DM4（応答末尾に必ず言い換え・問い返しを1つ）は完成テキストでしか検証できない**が、現行も
プログラム的な検証はしていない（`discussion_mode_design.md:521`「DM4 の遵守自体の計測は
非スコープ」）。ストリーミングは新しい検証義務を作らない。

**UC8（サイクルの骨格は非LLM・同期）** は影響を受けない。ELICIT の出題・選択式 DIFF・
着地・帰還の扉はいずれも非LLM の別経路で、AI モード（`cycle_mode`）だけが本チャットの
1コール地点に相乗りしている。ストリームが失敗しても骨格は degraded で閉じるだけで、
サイクルはストリームの成否に依存しない。

### 4.2 ST4（衛生）と ST7（非ストリーム不変）の両立

現在、学習チャットの回答本文に `strip_control_sequences`（`core/text_hygiene.py:51`）は
**掛かっていない**（learning.py は `UNTRUSTED_SOURCE_NOTICE` のみ import、:115）。掛ける対象は
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
  `_learning_chat_core` の後処理1箇所で**両エンドポイントに同時に**掛ける（片側だけに入れない）。

---

## §5 フロント

### 5.1 送信・停止

`sendMessage`（app.js:4381）に分岐を足す。フラグ（ログイン後1回の `GET /api/learning/client-features` の `chat_streaming`。
取得失敗は false）が真かつ通常のテキスト送信のときだけストリーム経路を使い、
それ以外（音声・casual、`replace_message_id` 付きの書き直し、typed action）は従来どおり
JSON 経路（`apiFetch` の POST `/chat`）。

- 送信直後に `state.sending = true`（:4407）→ **タイピングインジケータの代わりに**
  空のストリーミングバブル `<div class="mg ai streaming">` を `#chat-area` に**直接 append**
  する。`renderChat()`（app.js:776）は毎回 `ca.innerHTML = html` で全再構築し、出典チップ・
  数式・各種リスナを貼り直す（:938 `renderMathInElement` まで）。delta ごとにこれを呼ぶのは論外なので、
  **ストリーミング中は `renderChat()` を呼ばない**。
- delta は `bubble.textContent += t` のみ（HTML 挿入をしない＝エスケープ不要）。
  CSS は `white-space: pre-wrap`。
- `start` を受けたら、`stance` が非 null かつ `source === "inferred"` かつ `stance !== "tutor"` の
  ときだけ既存 `renderStanceLine()` と同じ規則で様相の1行を先に出してよい（出さなくても可。
  出すなら `final` 後の `renderChat()` で同じ行が再描画されるので二重にならないこと）。
- `final` を受けたら、従来と同じ形で `state.chatMessages.push({...})`（app.js:4505 と同一の
  フィールド）→ ストリーミングバブルを除去 → `renderChat()` を**1回だけ**呼ぶ。
  これでマークダウン・KaTeX（:938 の `renderMathInElement`）・出典チップ・ドリルダウン・
  鏡ブロック・`anchor_confirm` が一度に正しい形になる。
- 停止ボタン: 送信ボタンを送信中だけ「停止」に差し替え、`AbortController.abort()`。
  中断時はストリーミングバブルを消し、`state.chatMessages` から**当該 user メッセージを
  取り除いて**本文を入力欄へ戻す（書き直し `startEditMessage` と同じ作法）。
  こうしないと、次の送信で送る `history`（app.js:4467）にサーバが知らない片肺の往復が
  混ざり、`persist_chat_history` の対の整合が崩れる。

### 5.2 部分マークダウン・数式の扱い

Phase 3-a は **プレーンテキスト表示に限定する**（段落単位の中間レンダリングをしない）。

- 未完の `$x^2` / `**強調` / `- 箇条書き` の途中は、レンダラに掛けると壊れるか、掛かって
  すぐ形が変わってちらつく。
- `renderAiContent`（app.js:1707）は LaTeX 退避 → HTML エスケープ → ブロック整形 → KaTeX 復元、
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
  `_pendingScrollMsgId`（app.js:4565）の「新しい問いの先頭へ着地」挙動は `final` 後の
  `renderChat()` でそのまま効く（変更しない）。

### 5.4 音声・casual

Phase 3-a では**従来どおり**（完成テキスト → `speakVoiceAnswer`、app.js:5454）。
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
  learning.py:3705-3707 の `_chat_feature`）。**新 feature を作らない**（`KNOWN_FEATURES` を増やさない）。
- 教員向け見積り（`GET /api/admin/llm-usage/estimate/...`）とダッシュボードは無変更。
  ストリーム由来行が推計に寄る場合は `usage_source` 別の内訳にそのまま現れる（U1・U8）。

---

## §7 段階導入

| 段 | 範囲 | 追加で要るもの |
|---|---|---|
| **3-a** | 学習チャット本文（通常質問 / discuss / cycle elicit・diff）のテキスト経路 | `generate_text_stream` / `_learning_chat_core` の生成器化 + `_run_learning_turn` + `_stream_answer` / `/chat/stream` + `/client-features` / app.js の逐次バブル |
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
| 11 | fail-closed | 権限・可視性の判定はすべて `_learning_chat_core` の前処理（＝現行コードそのもの）の内側。ストリームは 200 を返す**前**に権限判定を終える（§3.4 手順2） |
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
  404/403 で落ちること（最初の `next()` を `StreamingResponse` の前で呼んでいることの証明）。
- `GET /client-features` が bool 1キーのみを返し、未認証は 401。

**guardrails**
- delta 経路に `strip_control_sequences` が入っていること（ソース検査、ST4）。
- delta のペイロードに本文以外のキー（`sources` / `tier` / `confidence` 等）が
  載らないこと（ST6・ST8）。
- 学習者に届く文字列にトークン数・秒数・残回数の数字が現れないこと（禁止語彙 + 数字パターン、
  ST8）。
- `learning.py` に §3.3 の逐語3点（`window_history(...)` / `if _is_discuss:` の scaffold /
  `messages: list[dict] = [`）が残っていること（既存テストの再掲・生成器化の回帰検出）。
- `_learning_chat_core` が generator 関数であり、`yield from _stream_answer(` が本体に**ちょうど
  1回**、`yield ("start"` が `_consume_quota()` より**後**にあること（AST / 字面）。
- `_learning_chat_core` 本体の `yield` / `yield from` が `with usage_context(` /
  `model_override(` ブロックの**内側に無い**こと（AST。§3.2）。
- `learning_chat` と `document_discuss_chat` の両方が `_run_learning_turn(` で包んでいること。
- `learning_chat` から次の `@router` までのスライスに `_learning_chat_core` 本体が含まれ続ける
  こと（新ルートを間に挟んでいない = `test_mirroring_prompt_guardrails.py` の前提）。
- `start.stance` と `final.stance` が一致すること（api）。

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

### オーナー判断（1件・**裁定済み**）

- **O-1 中断・失敗した往復を保存しないこと。** 本設計は「途中で切れた応答は履歴にも痕跡にも
  残さず、学習者の発話は入力欄へ戻す」を推奨する（ST1/ST5）。原則3「情報を落とさない」を
  「起きなかった往復も残す」と読むなら逆の設計（部分応答を `degraded` 相当で保存）もあり得る。
  **推奨は非保存** — 部分テキストは AI の未完成の出力であって学習者の産出ではなく、これを
  履歴に残すと次ターンの `window_history` に半端な文脈が入り、`persist_chat_history` の
  対の整合も崩れるため。
  **裁定（2026-09-12、オーナー）: 推奨どおり非保存で確定。** 部分応答は保存せず、そのターンは
  記録しない。quota は消費されたまま（ST2）。§8 行3 の解釈（「起きなかった往復」）を正とする。

これ以外（転送方式・継ぎ目の方式・フラグ名・段階の順序）は実装者判断で足りる（継ぎ目は
§3.3 のとおり生成器方式で確定）。

---

## §11 migration

**不要。** 新テーブル・新列・CHECK 変更・シードのいずれも無い。`llm_usage_events` は
`operation='chat'` のまま既存行と同じ形で書き込み、ストリームであることは `metadata` JSONB に
入れる（§6）。設定は環境変数1本（`LEARNING_CHAT_STREAMING_ENABLED`）のみ。

---

## §12 実装記録

### 12.1 実装した範囲（2026-09-12）

**Phase 3-a のみ**（§7 の表の1段目）。学習チャット本文のテキスト経路だけを SSE で逐次配信する。
**3-b（グラフ要素の説明）/ 3-c（文単位 TTS）/ 3-d（W層要素対話・グラフ全体対話）は未着手**で、
`_generate_graph_element_explanation` / `speakVoiceAnswer` / `core/llm_worker/chat_turn.py` は
1バイトも触っていない。document 直付け discuss（`document_discuss_chat`）も §3.4 のとおり
非対象で、`_run_learning_turn` で包んだだけ（ストリーム入口を持たない）。

migration は**実際に不要だった**（§11 のとおり。`llm_usage_events.operation` は `'chat'` のまま・
新テーブル・新列なし）。新しい環境変数は `LEARNING_CHAT_STREAMING_ENABLED`（既定 false）1本で、
**既定 off のまま出荷する**（ST9。on にするまで学習者の体験は1バイトも変わらない）。

### 12.2 実装の骨格（設計どおりに入った部分）

- `core/llm.py::generate_text_stream`（**追加**。既存3関数は非改変 = ST7/原則13）。openai 経路は
  `stream=True` + `stream_options={"include_usage": True}` で、`choices == []` かつ `usage` を持つ
  最終 chunk を握って `observe_chat(response=<最終chunk>)` に渡す（`usage_source="reported"`）。
  観測は `finally` で**必ず1回**（正常終了・例外・`close()` = クライアント切断のいずれでも）。
- `api/routes/learning.py`: `_learning_chat_core` を **generator 関数**化（§3.3 の採用方式）。
  `_run_learning_turn(gen)` が同期ドライバ、`_stream_answer(...)` が本モジュールで
  `generate_text_stream` を呼ぶ唯一の箇所。`yield ("start", stance_dto)` は `_consume_quota()` と
  SA層 Phase 4 の画面文脈注入の**後**（＝前処理の終わり）に置き、`resolve_stance(...)` の解決を
  生成の前へ移した。`yield` / `yield from` は `with usage_context(...)` / `model_override(...)` の
  **外**（§3.2）。
- 新エンドポイント2本: `POST /api/learning/courses/{cid}/topics/{tid}/chat/stream`（SSE。フラグ
  off は 404、最初の `next()` は `StreamingResponse` を返す**前**に同期で呼ぶ = 権限・可視性・
  422・429 は通常の HTTP ステータスで出る）と `GET /api/learning/client-features`（bool 1キー）。
- SSE フレーム: `_sse_frame` / `_sse_frames`。`delta` は `strip_control_sequences` を掛けてから
  流し、chunk 境界でエスケープ列が割れないよう**末尾 `_SSE_HYGIENE_TAIL = 16` 文字を保留**して
  終端でフラッシュする（§4.2）。`final` は `LearningChatResponse.model_dump()` そのままで、
  `final.answer` に衛生は掛けない（非ストリーム版と同値 = ST7）。
- フロント（`app.js`）: `state.clientFeatures` / `fetchClientFeaturesOnce()`（ログイン後1回・
  失敗は false のまま）/ `shouldStreamChatTurn()`（**音声・casual・書き直し（`replace_message_id`）・
  typed action・`ui_anchor` は従来の JSON 経路**）/ `runChatStream()`（`fetch` + `ReadableStream`。
  `EventSource` 不使用 = §2.1）/ 停止ボタン（送信ボタンの差し替え・新しい帯を作らない）/
  `rollbackStreamedTurn()`（発話を入力欄へ戻し、クライアント履歴も切り詰める）。
  **ストリーミング中は `renderChat()` を呼ばず**、delta は `textContent` 追記のみ。`final` 後に
  `renderChat()` を1回だけ呼んで KaTeX・出典チップ・ドリルダウン・鏡・`anchor_confirm` を作る。
- 事実文（数値なし = ST8）: 停止は「途中で止めました。この応答は記録に残していません。」、
  start 後の切断・`error` は「応答を受け取れませんでした。もう一度お試しください。」。

### 12.3 設計から逸れた点（実装判断）

1. **`start` に `message_id` を載せない**（§2.2 で「実装時に確定」としていた点の確定）。この時点で
   `persist_chat_history` 前でありサーバが user メッセージの id を持たないため、偽の値を作らない。
   フロントは自分が採番した `userMsgId` を使って中断時のロールバックができるので不足しない。
2. **`core/llm_usage/observe.py` を非改変にできなかった**（§6・§8 行13 の「observe.py は非改変」
   からの逸れ）。`observe_chat(...)` に **additive なキーワード引数 `extra_metadata: dict | None`**
   を1つ足した（省略時は従来と完全に同一の挙動。`operation` も `usage_source` の意味も不変 =
   U1/ST3）。理由は、`metadata.streamed` / `client_aborted` を記録する口が既存シグネチャに無く、
   呼び出し側で `UsageEvent` を組み直すと推計フォールバック（`estimated_tokenizer` /
   `estimated_heuristic` の分離）を二重実装することになるため。**U層の集計軸・`KNOWN_FEATURES`・
   `OPERATIONS` は増やしていない。**
3. **非 openai プロバイダのフォールバックで `usage_ctx` を再セットする**。`generate_text_stream` は
   同期ジェネレータなので Starlette が `iterate_in_threadpool` で別 context に再開する（§3.2）。
   非対応プロバイダ経路は `generate_text` を1回呼んで結果を1 delta として返すだけだが、その
   `generate_text` 内の観測が帰属を失わないよう、**値渡しの `usage_ctx` で `usage_context` を
   開き直してから**呼ぶ（「ストリームのふり」はしない＝ delta は1個のまま = 原則8）。
4. **フロントの「応答の適用」ブロックを関数に切り出さなかった**。設計 §5.1 は `final` 受信後に
   `state.chatMessages.push(...)` する流れだけを規定しており分解には触れていないが、実装では
   `sendMessage` 内の既存 `if (res.ok)` ブロックを `if (data)` に変えて**ストリーム経路と JSON
   経路で共有**する形にとどめた。`test_learning_screen_context_ui_static.py` が
   `_function_block("sendMessage")` で**関数本体の字面**を検査しており（`screen_context` の合流点
   が `sendMessage` 内にあることの固定）、適用ブロックを外へ出すと検査対象から外れて落ちるため。
   結果として「完成した応答 `data` を1つ得るところまで」だけが2経路に分かれる。
5. **停止と `final` が競合したら `final` を採る**。`runChatStream` は `final` を受け取っていれば
   `_streamState.aborted` が立っていても `{status:"final"}` を返す。サーバ側は `final` を送った
   時点で保存・痕跡まで完了しているので、**保存された往復をクライアントだけ捨てる**と次ターンの
   `history` がサーバ正本とずれる（§5.1 が片肺の往復を禁じたのと同じ理由）。中断の非保存（O-1 /
   ST1）が効くのは `final` 前に切れた場合だけ、という切り分けを実装で確定させた。
6. **`unsupported`（404 / start 前のネットワーク失敗 / 401）はその1回だけ JSON 経路で再送し、
   以後は `state.clientFeatures.chat_streaming = false` にしてセッション中は試さない**。401 は
   `apiFetch` の既存ログアウト処理へ合流させるため、あえてストリーム側でハンドリングしない。
   退避時は消したタイピングインジケータを戻す（沈黙のまま待たせない）。
7. **CSS を1行では済ませなかった**（付録A の「`white-space: pre-wrap` 程度」からの逸れ）。
   `.mg.ai.streaming`（pre-wrap）に加えて、本文到着前の空バブルの印（`:empty::after` の「…」）・
   事実文 `.stream-notice`（警告色にしない）・`#send-btn.stop-mode` を足した。いずれも `.ca` の
   内側か既存ボタンの差し替えで、**下段の `flex: 0 0 auto` と `.ca { min-height: 120px }` は不変**
   （`test_learning_layout_static.py` の規律 = §5.3）。
8. **`frontend/nginx.conf` は変更なし**（§2.3 の①だけで足りた。`X-Accel-Buffering: no` +
   `Cache-Control: no-cache` をレスポンスヘッダで付ける）。

**設計どおり見送ったもの**: 部分マークダウン・数式のレンダリング（Phase 3-a′。delta はプレーン
テキストのまま = §5.2）/ 回答本文そのものへの `strip_control_sequences`（§4.2 の別件）/
`operation='stream'` の新設（§6）/ 専用の CostGate・新 feature（既存
`LEARNING_CHAT_MAX_CALLS_PER_DAY` と既存 feature 語彙に相乗り）。

### 12.4 ガードレール

新規4本（2026-09-12 時点の件数。**正確な件数は各テストファイルが正**）:

| ファイル | 件数 | 主に固定するもの |
|---|---|---|
| `backend/tests/test_llm_streaming_core.py` | 13 | `generate_text_stream` の delta 列・include_usage 最終 chunk の reported 観測・非 openai の1 delta フォールバックと `usage_ctx` 再セット・`finally` の観測1回（中断時 `client_aborted`）・`operation` が `"chat"` のまま |
| `backend/tests/test_llm_streaming_api.py` | 16 | フラグ off の 404・`start`/`delta`/`final` の順序と JSON 1行・`final` が非ストリーム DTO と同値・`start.stance == final.stance`・権限/422/429 が `StreamingResponse` の前に出ること・`GET /client-features` の bool 1キーと 401 |
| `backend/tests/test_llm_streaming_guardrails.py` | 27 | delta 経路の `strip_control_sequences`・delta に本文以外のキーが載らないこと・学習者へ数値を出さないこと・`_learning_chat_core` が generator で `yield ("start"` が `_consume_quota()` より後・`yield` が `with usage_context(` の内側に無いこと（AST）・§3.3 の逐語3点の残存・2ルートが `_run_learning_turn(` を通ること |
| `backend/tests/test_llm_streaming_ui_static.py` | 40 | ストリーム中に `renderChat()` を呼ばないこと・delta が `textContent`・停止時のロールバック・自動スクロールが最下部付近限定・下段レイアウトの不変 |

合計 96 pass（`backend/.venv/bin/python -m pytest tests/test_llm_streaming_*.py`）。

### 12.5 変更ファイル

| ファイル | 変更 |
|---|---|
| `backend/core/llm.py` | **追加** `generate_text_stream`（既存3関数は非改変） |
| `backend/core/config.py` | **追加** `learning_chat_streaming_enabled`（既定 false） |
| `backend/core/llm_usage/observe.py` | `observe_chat(..., extra_metadata=None)` の additive 追加（12.3-2） |
| `backend/api/routes/learning.py` | `_learning_chat_core` の generator 化 + `_run_learning_turn` / `_stream_answer` / `_sse_frame` / `_sse_frames` / `learning_chat_stream` / `learning_client_features`。`resolve_stance` を生成前へ移動 |
| `frontend/public/js/app.js` | `state.clientFeatures` / `fetchClientFeaturesOnce` / `shouldStreamChatTurn` / `runChatStream` / `runStreamingChatTurn` / 停止ボタン / `rollbackStreamedTurn` / `sendMessage` の経路分岐 |
| `frontend/public/css/styles.css` | `.mg.ai.streaming` / `.stream-notice` / `#send-btn.stop-mode`（12.3-7） |
| `.env.example` | `LEARNING_CHAT_STREAMING_ENABLED=false` |
| `backend/tests/test_llm_streaming_{core,api,guardrails,ui_static}.py` | 新規4本 |

**非改変**（設計どおり）: `backend/api/schemas.py` / `core/llm_policy.py` /
`core/llm_worker/chat_turn.py` / `core/llm_usage/{recorder,estimator,schema}.py` /
`src/episteme_graph/agents/**`（A層）/ `backend/db/**` / `frontend/nginx.conf`。

### 12.6 ドキュメントの追随（`development_checklist.md` §5-1）

`docs/README.md`（索引の状態表記）/ `docs/architecture/assistant_ux_roadmap_2026-09-12.md`
（§6 判定表の Phase 3 行）/ `docs/features/learning.md` §3（学習者視点の小節）/
`docs/backend/rag-chat.md`（④⑤ の転送方式・§2.7・§6）/ `docs/backend/api.md`（経路2行）/
`docs/manual/student/02-student.md` §6（`{#chat-streaming}`）/ `CLAUDE.md`（本層の節）/
`docs/architecture/deployment.md` と `docs/manual/system_admin/04-system-admin.md`（env 1行）/
`docs/architecture/layer_registry.md` §1（レイヤー行）。**migration・ルーターの新設は無い**ため
`data-model.md` と `layer_registry.md` §3 は該当なし。

---

## 付録A 変更予定ファイル

| ファイル | 変更 |
|---|---|
| `backend/core/llm.py` | **追加**: `generate_text_stream`（既存3関数は非改変） |
| `backend/core/config.py` | **追加**: `learning_chat_streaming_enabled` |
| `backend/api/routes/learning.py` | **生成器化**: `_learning_chat_core` に `stream` kwarg + `yield`（挙動不変）。**追加**: `_run_learning_turn` / `_stream_answer` / `learning_chat_stream` / `learning_client_features` |
| `backend/api/schemas.py` | 変更なし（`LearningChatResponse` をそのまま `final` に流す） |
| `frontend/public/js/app.js` | `sendMessage` にストリーム分岐・逐次バブル・停止ボタン |
| `frontend/public/css/styles.css` | `.mg.ai.streaming { white-space: pre-wrap }` 程度 |
| `frontend/nginx.conf` | **原則変更なし**（`X-Accel-Buffering: no` で足りる。任意の堅牢化のみ §2.3） |
| `backend/tests/test_llm_streaming_*.py` | 新規4本（§9） |
| `docs/features/learning.md` / `docs/backend/rag-chat.md` | 学習者向け機能の変更として同時更新（`development_checklist.md` §5-1 の表） |
| `docs/backend/api.md` | 新エンドポイント2本の追記（ルーター新設は無いが経路が増える） |

**非改変**: `core/llm_usage/*`（observe / recorder / estimator / schema）、`core/llm_policy.py`、
`core/llm_worker/chat_turn.py`、`src/episteme_graph/agents/**`（A層）、`backend/db/**`。

## 付録B 着手前チェック（`docs/development_checklist.md` §5）

- [x] 本書を索引から参照する（`docs/README.md` で参照済み。実装完了に合わせて
      「設計中」→「実装済み（3-a）」へ更新済み・2026-09-12）。
- [x] 状態ヘッダ（冒頭）を実装完了時に「実装済み（正本・凍結）」へ更新する（2026-09-12。
      **Phase 3-a のみ**である旨を併記）。
- [x] 想定 migration 番号を書かない（本書は「不要」と明記。実装でも実際に不要だった）。
- [x] §5-1 の表に従い `docs/features/learning.md` / `docs/backend/rag-chat.md` /
      `docs/backend/api.md` を同じ PR で更新する（追随の全一覧は §12.6）。
