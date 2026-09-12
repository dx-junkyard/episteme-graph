# 学習チャットの入口統合（Learning Chat Entry Unification — 様相はサーバが読む）

> **状態: 実装済み（正本）**（3段ロードマップの Phase 1。Phase 2 = 学習チャットへの構造
> grounding ＝ SA層 Phase 4 も **2026-09-12 実装済み**
> （[assistant_screen_adapter_design.md](assistant_screen_adapter_design.md) §11.15）／
> Phase 3 = ストリーミングは**未着手**。
> **migration なし** — 既存 DTO の optional フィールドと痕跡 payload のキー追加だけで足り、
> 新テーブル・新エンドポイント・新 env・新 LLM コールはいずれも作っていない）
> 起票 2026-09-11（現状の事実はすべて同日の実機 grep で裏取り）・実装 2026-09-12。
> 以後は §13 実装記録の追記のみ。

**正本**: 本ドキュメント。
**関連**: [「論文と話す」discuss モード](discussion_mode_design.md)（DM1〜DM8 — 特に DM1 無断
フォールバック禁止 / DM5 「寄り道」語彙の追放）/
[discuss 対話の歩調合わせ](discuss_dialogue_alignment_design.md)（DA1〜DA6 — casual と discuss の
文体差の正本）/ [理解サイクル](understanding_cycle_design.md)（UC1 ELICIT-first は opt-in /
UC5 沈黙適応をしない — 本書 LC4 の親条項）/ [discuss 観測基盤](discuss_observation_design.md)
（DO1〜DO6 — `entry_mode` 帰属）/ [利用者マニュアル KB](manual_help_kb_design.md)（§1-3 —
HELP pre-route の位置）/ [チャット型 AI の共通規約](assistant_common_infra_design.md)
（window_history / CostGate / degraded）/ [Admin Copilot](admin_assistant_design.md)（P6 —
ヒューリスティック一次分類 + LLM 一段リファインの先例）/
[RAG チャット](../backend/rag-chat.md)（§2.9 判定順 / §3 インテントモードの現行リファレンス）/
[vision](../vision.md) §6（14原則。照合は本書 §11）。

---

## 1. 目的 — 「どう話すか」を学習者に選ばせるのをやめる

学習者は、話しかける前に **UI の語彙で話し方を選ばされている**。

| 選ばせているもの | 現物 | 学習者が知る必要のある内部語彙 |
|---|---|---|
| `intent_mode`（on_path / explore / casual / discuss） | `backend/api/schemas.py:302`、`app.js:4864` の `sendWith` | 寄り道／casual／discuss |
| `discuss_scope`（course_sources / all_visible） | `schemas.py:307`、`app.js:3711` `renderDiscussBar` | 検索範囲の 2 段 |
| `cycle_mode`（elicit / diff） | `schemas.py:313`、`discuss.js:298`/`:346` | 予想／差分 |
| 精読モード | `app.js:1136`（localStorage `eg_precision_reading:<courseId>`） | ELICIT-first の on/off |
| 再構成・楽屋 | `reconstruction.js` / `app.js:7817` 以降 | 出題／記録の私有化 |

一方 **サーバはすでに 4 値の意図分類器を持っている**（`_classify_intent`、
`backend/api/routes/learning.py:906`。CHIT_CHAT / LEARNING_ADVICE / USAGE_HELP / DOMAIN_RAG）。
つまり「様相をサーバが読む」機構は存在するのに、**casual と discuss はそれを丸ごと
バイパスする明示スイッチ**として上に積まれている（`learning.py:3045`）。

さらに決定的な事実として、**casual にはテキストの入口が 1 つも無い**。
`intent_mode:"casual"` を送るのはハンズフリー音声ループだけで（`app.js:5190`、ループ本体は
`app.js:4942` 以降）、discuss 中は fail-closed で塞がれている（`app.js:4986-4996` /
`app.js:5179-5184` のコメントが理由を明記 — ①スコープ表示と実検索範囲の食い違い
②`out_of_source_notice` の casual 分岐での抑制（DM1）③痕跡が casual として記録され
`entry_mode='discuss'` 集計から漏れる）。結果、キーボードの学習者は「気軽に話す」を
**選ぶことすらできず**、雑談めいた発話は CHIT_CHAT ルートで拒否文を返される
（`learning.py:3050` 付近の定型文「私は…学習支援に特化したAIです」）。

本層のねらいは 1 つ: **入口を 1 つにして、様相（会話の調子）はサーバが当該発話から読む**。
検索範囲・出題モード・記録の私有化は**推定しない**（そこは学習者の明示のまま）。

## 2. 不変条項（LC1〜LC8）

既存条項（DM1〜DM8 / UC1〜UC10 / DO1〜DO6 / P1〜P7）を継承したうえで、本層に課す。

| # | 条項 | 根拠 |
|---|---|---|
| **LC1** | **推定してよいのは「様相」だけ**。検索範囲（`discuss_scope`）・出題モード（`cycle_mode`）・記録の私有化（`backstage`）・確認問題の壁打ち（`check_scaffold`）はサーバが推定で切り替えない | DM1（無断でスコープを広げない）/ UC1（ELICIT-first は opt-in）/ SD4（楽屋は本人の宣言） |
| **LC2** | **明示は常に推定に勝つ**。クライアントが様相を明示した往復では推定器を走らせない | 押し付けない（原則12） |
| **LC3** | **HELP pre-route は非LLM・最前段のまま**。推定器はその**後**に置く | manual_help_kb §1-3（音声・casual にマニュアル回答を届ける唯一の位置）/ rag-chat §2.9 |
| **LC4** | **推定の入力は「当該発話 + 画面の明示状態」だけ**。過去の産出・正答率・滞在時間・過去の様相からの学習者モデルを作らない。推定は**セッションを跨いで持ち越さない**。§11 末尾の**4条件**（①入力は当該発話 + 画面の明示状態のみ ②セッションを跨がない ③変わるのは文体だけ ④推定を本人に見せ 1 タップで覆せる）は**本条項の一部＝恒久条項**であり、どれかを外す拡張は UC5 の再解釈にあたるため本書の改訂を要する（2026-09-12 オーナー判断 §12-2 で格上げ） | UC5（沈黙適応をしない）/ UC7（cold start で能力推定をしない） |
| **LC5** | **LLM 呼び出しを増やさない**。様相の判断は既存の意図分類コールに吸収し、非LLM 一次判定で**むしろ減らす**。分類不能・例外は tutor-RAG（現行既定）へ倒す | 原則9（同期パスに LLM を入れない）/ Copilot P6 |
| **LC6** | **推定した事実は記録し、本人に見せ、1タップで訂正できる**。訂正は常に学習者の行為で、サーバが自動で様相を切り替え直さない | 原則1（確定は人間）/ 原則8（出所の正直さ） |
| **LC7** | **数値を見せない**。confidence・一致度・推定の当たり外れをレスポンスにも UI にも出さない | 原則4 / DM6 / UC9 |
| **LC8** | **既存層は非改変**。`_is_casual` / `_is_discuss` 以降の下流（プロンプト選択・バイパス・痕跡・U層タグ）は書き換えず、**解決済みの様相を上流で確定させて流し込む**だけにする | 原則13 / UC10。既存のソース grep ガードレールが green のまま通ることを設計の合格条件にする |

## 3. 現状の事実（2026-09-11 実機確認）

判定順（`_learning_chat_core` = `learning.py:2805`）。**この順序は崩さない**（rag-chat §2.9）。

| 順 | 内容 | 位置 |
|---|---|---|
| 1 | `cycle_mode` 語彙検証（elicit/diff 以外 422） | `learning.py:2855` |
| 2 | 楽屋フラグ確定（`body.action` / `body.atlas_context` を落とす） | `learning.py:2872` |
| 3 | typed action `EXPLAIN_GRAPH_ELEMENT` の early-return | `learning.py:2960` 付近 |
| 4 | **HELP pre-route**（非LLM `_is_usage_question` = `learning.py:862`、または typed `usage_help`） | `learning.py:2991` |
| 5 | `_is_casual` | `learning.py:3010` |
| 6 | `_is_discuss` | `learning.py:3014` |
| 7 | `_cycle_mode` / U層 feature 分岐の元 | `learning.py:3019` |
| 8 | 意図分類（casual / discuss / atlas はバイパス） | `learning.py:3045` |
| 9 | CHIT_CHAT 拒否 / USAGE_HELP 委譲 / LEARNING_ADVICE 早期 return | `learning.py:3049` 〜 `3160` |
| 10 | 前提知識ゲート（casual / discuss / atlas はバイパス） | `learning.py:3170` |
| 11 | RAG 検索スコープ解決（discuss のみ 2 段） | `learning.py:3198`-`3215` |
| 12 | system プロンプト選択（cycle → discuss → casual → tutor） | `learning.py:3288`-`3296` |
| 13 | U層 feature 分岐 / CostGate 消費 | `learning.py:3362`-`3370` |
| 14 | 痕跡 payload（`casual` / `entry_mode` / `discuss_scope` / `backstage`） | `learning.py:3482`-`3503` |
| 15 | detour 化の判定（`("on_path","casual","discuss")` は非 detour） | `learning.py:3573` |

- 5 つの system プロンプト: `_get_integrated_tutor_system_prompt`(:1241) /
  `_get_casual_teacher_system_prompt`(:1269) / `_get_discuss_system_prompt`(:1294) /
  `_get_cycle_elicit_system_prompt`(:1368) / `_get_cycle_diff_system_prompt`(:1393)。
- **casual プロンプトは音声前提で書かれている**（:1269 の本文が「2〜4文の短い話し言葉」
  「箇条書き・記号・絵文字禁止」「LaTeX 禁止」を強制）。つまり現行の casual は
  **様相（軽い調子）と伝達形式（読み上げ）を 1 つの値に畳んでいる**。
- CostGate は 1 リクエスト 1 消費（`_consume_learning_chat_quota` = `learning.py:244`、
  `_quota_state` で多重消費を防止）。**実 LLM 回数**は tutor 経路が 2（分類 + 生成）、
  casual / discuss / cycle が 1、HELP テキスト経路が 0。
- 痕跡 `entry_mode` の読み手は discuss 観測基盤だけ（`core/discuss/observation.py:239` ほか、
  宣言は `core/trace_registry.py:296`）。楽屋には焼き込まない（`learning.py:3494`）。

## 4. 設計

### 4.1 3 つの軸に分ける（畳まれているものをほどく）

| 軸 | 値 | 決め方 |
|---|---|---|
| **様相**（stance） | `tutor` / `casual_light` / `discuss` / `cycle_elicit` / `cycle_diff` / `usage_help` / `backstage` | tutor と casual_light の**間だけ**サーバが推定。他は明示のみ |
| **伝達**（delivery） | `text` / `spoken` | `body.screen_mode`（`app.js:4207` `resolveScreenMode()` が全送信経路で付与）から決定論導出 |
| **順路との関係**（path） | `on_path` / `explore` | 現行どおりフロントが寄り道状態から自動判定（`app.js:4895`）。**推定対象外** |

現行 `intent_mode` はこの 3 軸を 1 つの enum に畳んでいる。**enum は増やさない**
（`intent_mode` の値集合は不変）。ほどくのは内部の扱いだけで、
「casual = 軽い調子 **かつ** 読み上げ向き」を「casual = 軽い調子」＋
「spoken = 読み上げ向き」に分離する。

### 4.2 様相の解決順（推定器はこの 4 段）

```
[0] HELP pre-route（非LLM・最前段）                 … 現行のまま（LC3）
[1] 明示チェック   explicit?                        … discuss / casual / cycle_mode /
                                                      backstage / check_scaffold / typed action /
                                                      atlas_context のいずれか → そのまま採用（LC2）
[2] 非LLM 一次判定 prejudge(message, cartridge)      … 「明らかに教材内容の問い」なら
                                                      intent="DOMAIN_RAG" を先に確定させ、
                                                      **意図分類の LLM コールを省く**（LC5）
[3] 既存 LLM 分類  _classify_intent(...)             … 一次判定が決められなかったときだけ。
                                                      呼び出し回数は現行と同じ 1
[4] ラベル→様相    CHIT_CHAT → casual_light          … ★ 本設計の中心（4.3）
                   LEARNING_ADVICE / USAGE_HELP → 現行のまま早期 return
                   DOMAIN_RAG / 例外 / 不明 → tutor（fail-safe, LC5）
```

`prejudge` は `backend/core/learning_stance/heuristic.py`（FastAPI / LLM 非 import の純関数）
に置く。判定材料は既存資産の再利用に限る — `learning.py:_CONTENT_QUESTION_TERMS` と
`_cartridge_content_terms(cartridge_id)`（`learning.py:842` 付近。分野語をコードに書かない
規律をそのまま引き継ぐ）、数式記号・`$…$`・十分な長さ。**「casual らしさ」は
ヒューリスティックで判定しない**（軽口の検出をキーワードでやると誤爆が人格評価に見える）。
迷ったら [3] へ落とすのが唯一の縮退。

### 4.3 CHIT_CHAT の意味を「拒否」から「軽い調子で、根拠は保ったまま応じる」へ

現行の CHIT_CHAT は定型の拒否文を返して早期 return する（`learning.py:3049`-`3065`）。
これは casual が丸ごとバイパスしていた分岐であり、**「casual のテキスト入口が無い」の
裏返し**でもある。本設計では拒否をやめ、CHIT_CHAT を **`casual_light` 様相の合図**として
扱い、通常の RAG フローへ合流させる。

実装上は `_is_casual` を **CHIT_CHAT 分岐の中で `True` に再代入する**だけでよい。

- `_is_casual` の**定義行（:3010）は 1 文字も変えない**（LC8。既存ガードレールが
  `'_is_casual = (body.intent_mode or "").strip() == "casual"'` の逐語一致を固定している）。
- 分類（:3045）は `_is_casual` の**後**に走るので、再代入は下流のすべて
  （前提知識ゲート :3170 / プロンプト選択 :3294 / notice 抑制 :3413 / 誤解検出 :3424 /
  U層タグ :3364 / 痕跡 :3482 / detour 非化 :3573）に自然に効く。**下流の条件式は無改変**。
- `intent = None if (_is_casual or _is_discuss or _atlas_ctx) else (…)`（:3045）の逐語も
  維持する（一次判定は `else (` の内側に `_prejudged or` として差し込む）。

結果、**明示 casual（音声）と推定 casual_light（テキスト）は、同じ 1 本の下流を通る**。
根拠の一線（RAG 検索・tier 集約・OutOfSourceGuard の system 注入・`content_grounding`）は
どちらでも変わらない。

### 4.4 伝達形式の分離（casual プロンプトの二枚化）

`_get_casual_teacher_system_prompt(domain, response_persona, *, spoken: bool = True)` にする。

- `spoken=True`（現行の本文をそのまま維持）: 2〜4 文の話し言葉・記号なし・LaTeX なし。
- `spoken=False`（テキストの casual_light）: 軽い調子・相づち・ときどきの聞き返しは維持し、
  **LaTeX と `[出典N]` は許可**する（数式を言葉に潰すのはテキストでは劣化）。

`spoken` は `body.screen_mode == "voice"`（`app.js:4207` が全送信経路で付与）から導く。
`screen_mode` 未指定 + 明示 casual は後方互換で `spoken=True`（既存 API クライアント・
既存テストが `intent_mode="casual"` 単独で音声想定の応答を期待しているため）。

**HELP ルート側は変更不要**: `_usage_help_response`(:2621) の `is_casual` 判定（:2700 付近）は
`body.intent_mode` を読むが、推定 casual は `body.intent_mode` を書き換えないため到達しない
（HELP pre-route も分類の USAGE_HELP 委譲も、推定より手前か、`intent_mode` が
on_path/explore のまま）。**推定によって HELP が 1 コール増えることはない**。

### 4.5 推定しないもの（LC1 の具体化）

- **discuss を推定しない**。discuss は検索スコープの意味変更（`learning.py:3198`-`3215`）を
  伴い、無断のスコープ変更は DM1 違反。「議論として続けたい」は 二枚看板（`app.js:590` 付近）
  の明示操作のまま。推定器は discuss を**提案すらしない**（事実文で場所を案内するに留める）。
- **cycle_elicit / cycle_diff を推定しない**（UC1）。AI が「予想を先に言わせる」に勝手に
  入るのは ELICIT-first の opt-in を壊す。**提案（chip の提示）は可、遷移は不可**。
- **backstage / check_scaffold を推定しない**（記録の私有化・出題の壁打ちは本人の宣言）。
- **discuss セッション中は casual_light を推定しない**。`_is_discuss` が真なら [1] で確定し
  推定器に入らない（`app.js:4986-4996` が挙げた 3 つの事故を構造的に再現しない）。

### 4.6 LLM 呼び出し予算（LC5）

| 経路 | 現行 | 本設計 |
|---|---|---|
| tutor（一次判定が決めた） | 2（分類 + 生成） | **1**（生成のみ） |
| tutor（一次判定が迷った） | 2 | 2（不変） |
| casual_light（推定） | —（入口が無い） | 2（分類 + 生成。CHIT_CHAT 判定に既存コールを使う） |
| casual（明示・音声） | 1 | 1（不変） |
| discuss / cycle | 1 | 1（不変） |
| HELP テキスト / HELP 音声 | 0 / 1 | 不変 |

**どの経路でも現行を上回らない**（新設の推定専用コールは作らない）。CostGate
（`LEARNING_CHAT_MAX_CALLS_PER_DAY`）は 1 リクエスト 1 消費のまま、専用上限も新 env も作らない。

## 5. API / DTO の変更（migration なし）

**リクエスト（`backend/api/schemas.py::LearningChatRequest`）: 変更なし。**
`intent_mode` の値集合も増やさない（訂正チップは既存の `casual` / `on_path` / `explore` を
明示送信するだけ）。

**レスポンス（`LearningChatResponse`）に optional 1 フィールドを追加**する。

```python
# 様相の事実（LC6/LC7）。RAG 応答でのみ設定。confidence・スコアは持たない。
#   {"stance": "tutor"|"casual_light"|"discuss"|"cycle_elicit"|"cycle_diff",
#    "source": "explicit"|"inferred",
#    "label": "<label_vocab の表示ラベル>"}
stance: dict | None = None
```

- 既存キーの意味・順序は不変（追加のみ）。
- **数値を入れない**（LC7）。`label` はサーバ側 `core/label_vocab.py` の
  `LEARNING_STANCE_LABELS` から引く（フロントに表を持たせない — JS 側は既存のミラー規律に
  従い逐語ミラーテストで固定する）。
- 語彙そのもの（`STANCES` / `STANCE_SOURCES`）の正本は
  `backend/core/learning_stance/schema.py`。

**新エンドポイントは作らない。** 訂正は既存の書き直し経路（`replace_message_id` →
`truncate_chat_and_supersede`、`learning.py:2890` 付近）に相乗りする — 直前の user メッセージを
明示 `intent_mode` で**同じ位置から再処理**するだけで、旧往復の派生痕跡は
`status='superseded'` で保持される（P4 / 原則3）。

## 6. フロント（`frontend/public/js/app.js`）

- **composer は 1 つのまま**。`sendWith`(:4864) / `sendCurrent`(:4895) は無改変
  （on_path / explore の自動判定も、discuss 時の上書きも現行どおり）。
- **様相チップ**（事実であって督促ではない）: `data.stance.source === "inferred"` かつ
  `stance !== "tutor"` のときだけ、回答バブルの下に 1 行。
  例: 「気軽な調子で答えました。」+ チップ `[ふつうの質問として聞き直す]`。
  tutor で答えた往復には**何も出さない**（既定は無表示 = 静音、`renderModeBar`(:1558) が
  on-path でバーを出さないのと同じ流儀）。
- **訂正チップは 2 種のみ**（v1）: `[ふつうの質問として聞き直す]`（推定 casual → tutor）と
  `[気軽に聞き直す]`（tutor → casual、任意配置）。どちらも直前発話を
  `_replace_message_id` + 明示 `intent_mode` で再送する。
  **「議論として続ける」は置かない** — discuss は範囲の変更を伴うため、二枚看板への
  事実文の案内に留める（DM1 / LC1）。
- **様相は往復ごとに解決し、クライアントに mode 状態を持たせない**（sticky にしない）。
  訂正はその 1 往復にだけ効く。理由: 隠れたモード状態は、前提知識ゲートと誤解検出の
  バイパスを学習者に見えない形で継続させてしまう。
- **「寄り道」語彙は casual / discuss の文言に使わない**（DM5）。
- 音声ループ（:4942〜、`handleVoiceSegment`:5155）は**無改変**。discuss 中の
  fail-closed（:4997 `updateVoiceAvailability` / :5179 の再チェック）もそのまま残す。

## 7. 観測・計測（DO1〜DO6 継承）

- **痕跡 payload**（`learning.py:3482` の `_trace_payload`）に 2 キーを追加:
  `"stance": <enum>` / `"stance_source": "explicit"|"inferred"`。
  本文・逐語は入れない（enum のみ = DO1）。**楽屋には焼き込まない**
  （`entry_mode` と同じ `and not _is_backstage` ガードを付ける = SD4）。
  `entry_mode` の書き込み条件・値は**一切変えない**（`core/discuss/observation.py` の
  SQL・`trace_registry.py:296` の宣言は無改変）。
- **観測ダンプ**: `discuss_traces.jsonl` の列に `stance` / `stance_source` を追加
  （SYSTEM_ADMIN のみ・仮名化済み。DO2/DO3）。これが「誤ルーティングがどれだけ起きたか」を
  後から読むための唯一の材料になる。
- **metric event を 1 つ追加**: `stance_corrected`（訂正チップのタップ。フロントから
  fire-and-forget）。`METRIC_EVENT_VOCAB`（`core/discuss/observation.py:318`）と
  `_METRIC_EVENT_PAYLOAD_VALUE_VOCAB` に `"stance": frozenset({"tutor","casual_light"})` を
  同時登録する（キーも値も列挙型で検証 = DO1）。
- **教員向け集約は作らない**（v1）。作るなら k=3（`core/privacy.py` 正本）に加えて
  **制度指標カタログ（`core/indicator_catalog.py`）への登録が必須**（IG4）。v1 は
  カタログに 1 件も足さない。
- 学習者には数値を出さない（DO3 / LC7）。「推定が当たった率」のような指標は作らない。

## 8. ガードレール（テスト名案）

新規 `backend/tests/test_learning_stance_{core,routing,guardrails,ui_static}.py`。

| テスト | 固定する事実 |
|---|---|
| `test_help_preroute_precedes_stance_resolution` | HELP pre-route(:2991) の index < 推定器の index（LC3） |
| `test_stance_never_sets_discuss_scope` | 推定経路のソースに `discuss_scope` への代入が無い（LC1 / DM1） |
| `test_stance_never_sets_cycle_mode_or_backstage` | 同上（`cycle_mode` / `backstage` / `check_scaffold`）（LC1 / UC1） |
| `test_casual_not_inferred_during_discuss` | `_is_discuss` が真なら推定器を呼ばない（`app.js:4986` の 3 事故の再現防止） |
| `test_explicit_intent_mode_wins_over_inference` | 明示 casual / discuss は推定器に入らない（LC2） |
| `test_prejudge_is_pure_and_non_llm` | `core/learning_stance/` が fastapi / `core.llm` / sqlalchemy を import しない（`guardrail_helpers.assert_module_tree_does_not_import`） |
| `test_no_additional_llm_call_per_turn` | 推定経路に `generate_text` の新規呼び出しが無い（LC5） |
| `test_stance_falls_back_to_tutor_on_failure` | 分類例外・未知ラベルで tutor（fail-safe） |
| `test_stance_source_recorded_in_trace_payload` | `"stance_source"` が payload に入り、**backstage では入らない**（SD4） |
| `test_stance_response_has_no_numeric_confidence` | `stance` DTO に confidence / score / 数値キーが再帰的に無い（LC7） |
| `test_stance_labels_come_from_label_vocab` | 表示ラベルの逐語重複が JS 側と一致（既存ミラー規律） |
| `test_casual_prompt_spoken_and_text_variants` | `spoken=True` 分岐に「2〜4文」「箇条書き」「ACTION_BUTTON」が残り、`spoken=False` 分岐で LaTeX が許可される |
| `test_stance_chip_not_shown_for_tutor` | 既定は無表示（押し付けない・静音） |
| `test_correction_chip_uses_replace_message_id` | 訂正が既存の書き直し経路を使い、新エンドポイントを作らない |
| `test_no_new_indicator_registered` | `indicator_catalog` に本層由来の spec が無い（v1 の宣言と一致） |
| `test_discuss_prompt_contracts_unchanged` | DA6 契約フレーズ・DM4 必須要素が無傷（既存 `test_discuss_mode.py` の再確認） |

## 9. 段階的導入と後方互換

**green のまま通らなければならない既存テスト**（本設計はこれを合格条件にしている）:

- `test_discuss_mode.py`（`TestFourBypassPoints` の逐語 grep 群 —
  `_is_casual` / `_is_discuss` の定義行・`intent = None if (_is_casual or _is_discuss or _atlas_ctx) else (`・
  前提知識ゲートの逐語・detour タプル・U層タグ分岐・pre-route の index 順）
- `test_voice_casual_chat.py`（`TestCasualModeRouting` 全件・音声 UI 配線）
- `test_discuss_guardrails.py` / `test_discuss_ui_static.py` / `test_discuss_phase2_ui_static.py`
- `test_help_usage_route.py`（pre-route 挿入位置・casual 経路の 1 コール・`screen_mode` 伝播）
- `test_understanding_cycle_phase2.py`（`cycle_mode` の定義順・422 precheck・プロンプト分岐）
- `test_intent_routing.py`（`_classify_intent` の 4 ラベル・保守フォールバック・fast tier）
- `test_document_discuss_{api,guardrails}.py` / `test_trace_registry_guardrails.py` /
  `test_learning_chat_infra.py` / `test_chat_history_source_persistence.py`

**変更が要る既存テスト**（CHIT_CHAT の意味変更に伴う 2 件のみ。事前に洗い出し済み）:

| テスト | 現在固定している事実 | 変更内容 |
|---|---|---|
| `test_domain_neutral_wording.py::TestChitChatWording::test_chit_chat_uses_course_title_not_a_field_name` | CHIT_CHAT 分岐に `_scope_label` / `course_title` / 「この教材」があること | 分野中立の検査対象を**拒否文から casual_light 合流の事実文へ**移す（分野語ハードコード禁止の趣旨は維持） |
| `test_help_usage_route.py::TestChitChatReguidanceAndServicesChanges::test_chit_chat_mentions_usage_help` | 拒否文に「画面の使い方についての質問にもお答えできます」が含まれること | 使い方への再誘導は **HELP pre-route + 分類 USAGE_HELP** が担う経路のみに一本化されるため、当該アサーションを HELP 経路側へ移す |

**段階**: ①（サーバ）prejudge + 様相解決 + `stance` DTO + 痕跡キー（UI 変更なし。この時点で
LLM コールが減り、casual_light がテキストで成立する）→ ②（フロント）様相チップと訂正チップ
→ ③（観測）ダンプ列と `stance_corrected`。①だけでも後方互換で、②③は独立に落とせる。

## 10. 非スコープ（v1）

- **discuss / cycle / backstage の推定**（LC1 で恒久的に排除）。
- **音声版 discuss**（`discussion_mode_design.md` §6.4 の非スコープを継承）。
- **様相の sticky 化・学習者ごとの既定様相の保存**（UC5 / §6 の理由）。
- **教員向けの様相集約・誤ルーティング率の表示**（IG4 の登録なしには作らない）。
- **精読モード・再構成・楽屋の入口統合**（本層は「話しかける」入口だけを扱う）。
- **学習チャットへの構造 grounding の配線**（= Phase 2 / SA層 Phase 4）。
  → **2026-09-12 に実装**（`assistant_screen_adapter_design.md` §11 / 実装記録 §11.15）。
  本設計が解決した様相（stance）を読む側であって、本設計の実装自体は不変。
- **ストリーミング応答**（= Phase 3）。
- `intent_mode` enum の再設計・`on_path` / `explore` の撤去（寄り道復帰導線が依存）。

## 11. vision §6（14原則）照合

| # | 原則 | 本設計の関わり |
|---|---|---|
| 1 | AIは候補まで・確定は人間 | 様相の推定は**確定ではない**（応答の調子だけを変え、記録・承認・スコープを確定しない）。訂正は常に学習者の行為（LC6）。**例外なし** |
| 2 | evidence-based | 該当なし（生成の根拠規律は現行の RAG 経路のまま無改変） |
| 3 | 情報を落とさない | 訂正は既存 supersede 経路（行削除なし）。痕跡キーは追加のみ |
| 4 | 数値の用途と粒度を統治する | confidence を返さない・表示しない（LC7）。教員向け集約を作らないので指標カタログ登録も無し |
| 5 | 監視しない | 記録するのは enum 2 つのみ（本文非含有）。本人可視・評価利用なし。楽屋には焼き込まない |
| 6 | egocentric のみ | 該当なし |
| 7 | リンクであってマージではない | 該当なし |
| 8 | 出所の正直さ | `content_grounding` / tier / `out_of_source_notice` の扱いは全経路で無改変。加えて「どの様相で答えたか」を事実として返す（推定を隠さない） |
| 9 | 同期パスに LLM を入れない | 一次判定は非LLM、LLM は既存 1 コールに吸収、失敗は tutor へ縮退（LC5） |
| 10 | 完了フラグを持たない | 様相は毎往復サーバで解決し保存しない（sticky を作らない） |
| 11 | fail-closed | 可視性・スコープは推定で動かない（LC1）。discuss 中の音声 fail-closed も維持 |
| 12 | 押し付けない | **要注意点**: 既定表示は無表示（tutor では何も出さない）。cycle へは提案のみで遷移しない。訂正は opt-in |
| 13 | 層は積層し、下層を改変しない | 下流は無改変（LC8）。追加は `core/learning_stance/` と optional フィールド・payload キー |
| 14 | 監査必須・帰属必須 | 該当なし（学習者の痕跡は既存どおり監査台帳の対象外。教員の確定行為を増やさない） |

**UC5（沈黙適応）との線引き（重要）**: 本設計の推定は「当該発話 1 つから会話の**調子**を
決める」ものであって、「学習者モデル（能力・理解度・スキーマ距離）から**提示する内容と順序**を
暗黙に変える」ものではない（LC4）。区別を成立させる 4 条件を設計に埋め込む —
①入力は当該発話 + 画面の明示状態のみ ②セッションを跨いで持ち越さない
③**変わるのは文体だけで、検索範囲・提示順・提示内容は変わらない**
④推定したことを本人に見せ、1 タップで覆せる。この 4 条件のどれかを外す拡張は、UC5 の
再解釈になるため本書の改訂を要する。

## 12. オーナー判断（2026-09-12 判断済み・推奨どおり採用）

実装方式（ヒューリスティックの語彙・モジュール配置・チップ文言・段階分割）は担当が決める。
以下 2 件だけは**不変条項の解釈変更**にあたるため判断を仰ぎ、**いずれも推奨どおり採用された**。

- **1 → 採用**（拒否をやめる）。実装は `_is_casual` の再代入 1 行のみで、下流は無改変
  （§13.2）。根拠の一線（RAG 検索・tier 集約・OutOfSourceGuard・`content_grounding`）は
  全経路共通のまま残っている。
- **2 → 採用**（LC4 への格上げ）。§2 の LC4 行に 4 条件を恒久条項として明記した。

1. **CHIT_CHAT の拒否をやめること**（§4.3）。現行は「私は…学習支援に特化したAIです」と
   突き放す。本設計はこれを casual_light 様相での応答に置き換える（RAG・tier・
   OutOfSourceGuard の一線は維持、`content_grounding` は正直に `model_generated`）。
   **これは「学習支援に特化する」という対外的な線を引き直す変更**である。
   **推奨: 賛成**。理由 — ①拒否文は原則12（押し付けない）と最も摩擦の大きい現行 UX で、
   casual が丸ごとバイパスしていた分岐そのものである ②根拠の一線は落とさない
   ③代替（拒否のまま casual をテキスト UI で明示選択させる）は「入口を増やさない」という
   本層の目的と正面から衝突する。
2. **「様相の推定」を UC5 の例外にしないための線引き**（§11 末尾の 4 条件）を、
   本書 LC4 として恒久条項に格上げしてよいか。**推奨: 格上げする**（4 条件を満たす限り
   沈黙適応ではない、と明文化しておかないと、後続の実装が「履歴も見れば精度が上がる」で
   静かに越境する）。

---

## 13. 実装記録（2026-09-12）

§9 の段階①②③（サーバ → フロント → 観測）を同日にまとめて実装した。**migration なし・
新エンドポイントなし・新 env なし・新 LLM コールなし**。以下は設計との差分を含む実装の事実で、
以後の一次情報は常にコードとテスト。

### 13.1 様相の正本モジュール（新設）

`backend/core/learning_stance/`（`__init__` / `schema` / `heuristic`。FastAPI・sqlalchemy・
`core.llm` を import しない純データ + 純関数）。

- **`schema.py`** = 語彙の正本。`STANCES`（`tutor` / `casual_light` / `discuss` /
  `cycle_elicit` / `cycle_diff`）・`STANCE_SOURCES`（`explicit` / `inferred`）・
  `resolve_stance(...)`（優先順位は cycle → discuss → casual（`explicit_casual` なら明示・
  でなければ推定）→ tutor（typed action か `atlas_context` があれば明示・自然文なら推定））・
  `build_stance_dto(...)`（`{stance, source, label}` の 3 キーのみ。数値キーを持たない = LC7）。
  **`discuss_scope` / `cycle_mode` / `backstage` / `check_scaffold` を引数に取らない設計**に
  して、様相がそれらを切り替えられないことを型で固定した（LC1）。
- **`heuristic.py::prejudge(message, *, content_terms)`** = 非LLM 一次判定。返り値は
  `"DOMAIN_RAG"` か `None` の 2 値のみで、**casual らしさは判定しない**（§4.2）。
  決める規則は 3 本 — A: 相異なる内容語 2 つ以上 / B: 数式らしさ（`$` が 2 つ以上・
  LaTeX コマンド・`∫∂Σ√∝≒`・両隣が英数字の `=`）かつ（内容語 1 つ以上 or 問い形）/
  C: 内容語 1 つ + 問い形 + 12 文字以上。内容語は**呼び出し側が渡す**
  （`learning.py` の `_CONTENT_QUESTION_TERMS` + `_cartridge_content_terms(cartridge_id)`。
  `cartridge_id` が空なら後者を呼ばない規律は呼び出し側に残した = 開発ルール7）。
- 表示ラベルの正本は `core/label_vocab.py::LEARNING_STANCE_LABELS`
  （ふつうの質問として / 気軽な調子で / 議論として / 予想を先に聞く形で /
  予想と照らし合わせる形で）。**JS 側に日本語表をミラーしない** — サーバが解決済みの
  文字列を返し、フロントは `label + "答えました。"` を描くだけにした。

### 13.2 `learning.py`（LC8 の逐語を維持したままの差し込み）

- 一次判定は `intent = None if (_is_casual or _is_discuss or _atlas_ctx) else (` の
  **逐語を変えずに** `_route_for_typed_action(...) or _prejudged or _classify_intent(...)` の
  順で差し込んだ。`_prejudged` の計算自体も `_is_casual / _is_discuss / _atlas_ctx` の
  いずれかが立っていれば走らない（LC2）。
- `_explicit_casual = _is_casual` を分類の直前で控え、**明示 casual（音声）と推定
  casual_light（テキスト）を後段で区別できるように**した。
- **CHIT_CHAT の拒否文を削除**し、分岐の中身を `_is_casual = True` の再代入 1 行にした
  （§4.3・オーナー判断 §12-1）。下流（前提知識ゲート / プロンプト選択 / notice 抑制 /
  誤解検出 / U層タグ / 痕跡 / detour 非化）は**条件式を 1 行も変えていない**。
- `_get_casual_teacher_system_prompt(domain, persona, *, spoken: bool = True)` に分離。
  `spoken=True` の本文は**従来のまま**（2〜4 文・記号なし・LaTeX 禁止）。`spoken=False` は
  3〜6 文の話し言葉で **LaTeX と `[出典N]` を許可**し、`[ACTION_BUTTON: ...]` 等の
  システム記法は引き続き禁止。`spoken` は `screen_mode == "voice"`、または
  （明示 casual かつ `screen_mode` 未指定）の 2 条件（後者は既存 API クライアント・
  既存テストの後方互換）。

### 13.3 DTO・痕跡・観測

- `LearningChatResponse.stance: dict | None = None`（optional 追加のみ。既存キーの意味・
  順序は不変）。設定するのは RAG 応答の最終 return だけで、HELP / 学習相談 / 地図 /
  要素説明の早期 return は `None` のまま。
- 痕跡 payload に `stance` / `stance_source` の enum 2 つ（本文・逐語・confidence は
  入れない）。**楽屋（`_is_backstage`）にはキー自体を足さない**（`entry_mode` と同じ
  SD4 のガード）。`entry_mode` の書き込み条件・値・`trace_registry` の宣言は無改変。
- 観測（`core/discuss/observation.py`）: `METRIC_EVENT_VOCAB` に `stance_corrected`、
  payload 値語彙に `"stance": {"tutor", "casual_light"}`（v1 の訂正チップが出す 2 値だけに
  絞った fail-closed。`STANCES` を import して広げると推定しない様相が計測に混ざる）、
  `discuss_traces.jsonl` に `stance` / `stance_source` 列（ダンプ説明文も追随）。

### 13.4 フロント（`app.js` / `styles.css`）

- `renderStanceLine(msg)` = `source === "inferred"` かつ `stance !== "tutor"` かつ
  非 discuss のときだけ、出所バッジの直後に 1 行（`.stance-line`・
  `data-ui-anchor="chat.stance-chip"`）。**tutor では何も出さない**（既定は無表示＝静音）。
- `correctStance(replyToId, stance)` = `sendDiscussMetric("stance_corrected", ...)` の後、
  既存の書き直し経路に相乗り（`_replace_message_id` + `support_action: "ask_question"`＝
  既存 typed action で DOMAIN_RAG を明示確定 + 寄り道状態から導いた `intent_mode`）。
  **新しい API パスを作らない**・sticky にしない（効くのはその 1 往復だけ）。
- assistant メッセージに `stance` と `reply_to_id` を**メモリ内だけ**保持する
  （localStorage へ保存しない）。`sendWith` / `sendCurrent` / 音声ループ・discuss 中の
  音声 fail-closed は**無改変**。
- 学習側 UI アンカー `chat.stance-chip` → `docs/manual/student/02-student.md#stance-chip`
  の 3 点セット（`core/help_kb/ui_anchors.py` + マニュアル節 + `data-ui-anchor`）を揃えた。

### 13.5 設計と異なる判断（5 件）

1. **`prejudge` に「降りる」条件を 2 つ追加した**（設計 §4.2 より保守的）。学習相談の合図
   （進め方・学習計画・何から…）と**使い方の合図**（画面・ボタン・操作・マイク…）が
   1 つでも含まれたら一次判定は `None` を返して LLM 分類へ落とす。理由: 一次判定が
   `LEARNING_ADVICE` / `USAGE_HELP` の受け皿を奪うと、rag-chat §2.9 の判定順を実質的に
   崩してしまうため（LC3 の趣旨を一次判定側でも守る）。HELP 側の pre-route が
   「参照語 × 問い形」の共起で判定するのに対し、こちらは**参照語だけ**で降りる。
2. **楽屋（backstage）でも `stance` DTO はレスポンスに返す**（痕跡には焼き込まない）。
   SD4 は「楽屋の痕跡を集計に入れない」という宣言であって、本人への事実提示（LC6）を
   止めるものではない、と解釈した。焼き込まないのは痕跡 payload だけ。
3. **訂正チップは 1 種のみ**にした。設計 §6 は 2 種（`[ふつうの質問として聞き直す]` と
   `[気軽に聞き直す]`）を挙げていたが、後者は tutor の往復＝**既定で何も表示しない**
   往復に UI を足すことになるため置かなかった（静音を優先。原則12）。
4. **変更が要った既存テストは §9 の 2 件ではなく 3 件**。`test_discuss_observation.py` が
   `METRIC_EVENT_VOCAB` の件数とダンプ射影 dict を逐語で固定していたため追随した。
   §9 の「2 件」は CHIT_CHAT の意味変更に伴う分の数え上げで、観測層（§7 の metric event
   追加）は別軸だった — 事前の洗い出しが 1 軸ぶん足りていなかった。
5. **観測ダンプで誤ルーティングを後追いできる範囲には限界がある**。`discuss_traces.jsonl`
   の行フィルタは `entry_mode='discuss'` のままで（DO の既存契約を変えないため）、
   `stance` / `stance_source` 列を足しても**通常チャットの往復はダンプに現れない**。
   したがって「推定がどれだけ外れたか」を読む主たる材料は `discuss_ui_events.jsonl` の
   `stance_corrected` イベントであり、母数（推定 casual_light で答えた往復の総数）は
   このダンプからは取れない。教員向け集約は v1 で作らない（IG4）という判断は据え置きなので、
   母数が要るなら指標カタログへの登録を伴う別件になる。

### 13.6 テスト

新規 `backend/tests/test_learning_stance_{core,routing,guardrails,ui_static}.py`
（LC1〜LC8 の構造検査・解決順と明示優先・fail-safe・チップの既定無表示・語彙ミラー・
スタイルが警告色でないこと。**件数の正本はこの 4 ファイル**）。

変更した既存テストは 3 件 — `test_domain_neutral_wording.py`（分野中立の検査対象を
拒否文から casual_light 合流の事実 + 様相ラベルへ移設）/ `test_help_usage_route.py`
（使い方への再誘導が pre-route + 分類 USAGE_HELP 委譲の 2 経路であることへ移設）/
`test_discuss_observation.py`（§13.5-4）。§9 が「green のまま通らなければならない」と
挙げた既存テスト群（`test_discuss_mode.py` の逐語 grep・`test_voice_casual_chat.py`・
`test_intent_routing.py` ほか）は無改変のまま通っている。

---

## 付録 変更ファイル一覧（実装済み）

**新規**

| ファイル | 内容 |
|---|---|
| `backend/core/learning_stance/__init__.py` | 公開シンボルの再エクスポート |
| `backend/core/learning_stance/schema.py` | `STANCES` / `STANCE_SOURCES` の語彙正本（FastAPI・LLM 非 import） |
| `backend/core/learning_stance/heuristic.py` | 非LLM 一次判定 `prejudge(...)`（純関数） |
| `backend/tests/test_learning_stance_core.py` | 一次判定の決定性・純粋性 |
| `backend/tests/test_learning_stance_routing.py` | 解決順・明示優先・fail-safe・コール数 |
| `backend/tests/test_learning_stance_guardrails.py` | LC1〜LC8 の構造検査 |
| `backend/tests/test_learning_stance_ui_static.py` | チップの既定無表示・訂正の再送経路・語彙ミラー |

**変更**（行番号は書かない — 一次情報は常にコード）

| ファイル | 変更点 |
|---|---|
| `backend/api/routes/learning.py` | 一次判定 `_prejudged` の差し込み（`intent = None if (…) else (` の逐語は不変）/ `_explicit_casual` の控え / CHIT_CHAT 分岐を `_is_casual = True` の再代入へ置換 / `_get_casual_teacher_system_prompt` の `spoken` 引数と `_casual_spoken` の導出 / `resolve_stance` の呼び出し / 痕跡 payload に `stance`・`stance_source`（楽屋は除外）/ レスポンスへ `stance` |
| `backend/api/schemas.py` | `LearningChatResponse.stance` の追加（リクエストは無改変） |
| `backend/core/label_vocab.py` | `LEARNING_STANCE_LABELS`（表示ラベルの正本） |
| `backend/core/discuss/observation.py` | `METRIC_EVENT_VOCAB` に `stance_corrected` / payload 値語彙に `stance` / ダンプ射影とダンプ列に `stance`・`stance_source`（README 説明文含む） |
| `backend/core/help_kb/ui_anchors.py` | 学習側アンカー `chat.stance-chip` → `student/02-student.md#stance-chip`（設計時の一覧に無かった 3 点セットの 1 つ） |
| `frontend/public/js/app.js` | `renderStanceLine` / `correctStance` / assistant メッセージへの `stance`・`reply_to_id` 保持（`sendWith` / `sendCurrent` / 音声ループは無改変） |
| `frontend/public/css/styles.css` | `.stance-line` / `.stance-fact` / `.stance-correct-btn`（警告色・バッジを使わない控えめなトーン） |
| `docs/backend/rag-chat.md` | §2.9 判定順に一次判定の位置 / §3 の intent_mode 表に様相軸の追記（規約 5-1 の必須更新） |
| `docs/features/learning.md` | 学習者向け機能の記述更新（規約 5-1 の必須更新） |
| `docs/features/discussion_mode_design.md` | §8 非スコープの直後に追補（discuss は推定対象外・音声版 discuss は依然非スコープ、規約 5-3） |
| `docs/manual/student/02-student.md` | 「答え方（調子）についての 1 行」節（`{#stance-chip}`）+ 目次 |
| `backend/tests/test_domain_neutral_wording.py` / `backend/tests/test_help_usage_route.py` / `backend/tests/test_discuss_observation.py` | §13.5-4 のとおり 3 ファイルの移設・追随 |
| `docs/README.md` / `docs/architecture/assistant_ux_roadmap_2026-09-12.md` / `CLAUDE.md` | 索引・ロードマップ §6 判定表・レイヤー節の追随（規約 5-1 / 5-2） |

**migration: なし**（DB スキーマ変更なし。`interest_traces.payload` / `discuss_metric_events.payload`
はいずれも既存 JSONB のキー追加のみ）。**新エンドポイント・新 env・新 LLM コール・新指標
（`core/indicator_catalog.py`）もいずれも無し。**
