# 学習チャットの入口統合（Learning Chat Entry Unification — 様相はサーバが読む）

> **状態: 設計中**（3段ロードマップの Phase 1。Phase 2 = 学習チャットへの構造 grounding
> ＝ SA層 Phase 4 / Phase 3 = ストリーミング。**migration は不要** — 既存 DTO の optional
> フィールドと痕跡 payload のキー追加だけで足りる。新テーブル・新エンドポイントなし）
> 起票 2026-09-11（現状の事実はすべて同日の実機 grep で裏取り）。

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
| **LC4** | **推定の入力は「当該発話 + 画面の明示状態」だけ**。過去の産出・正答率・滞在時間・過去の様相からの学習者モデルを作らない。推定は**セッションを跨いで持ち越さない** | UC5（沈黙適応をしない）/ UC7（cold start で能力推定をしない） |
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
- **学習チャットへの構造 grounding の配線**（= Phase 2 / SA層 Phase 4。着手時は
  `assistant_screen_adapter_design.md` §6 に節を足す）。
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

## 12. 未決事項（オーナー判断が要るもの）

実装方式（ヒューリスティックの語彙・モジュール配置・チップ文言・段階分割）は担当が決める。
以下 2 件だけは**不変条項の解釈変更**にあたるため判断を仰ぐ。

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

## 付録 変更予定ファイル一覧

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

**変更**

| ファイル | 変更点 |
|---|---|
| `backend/api/routes/learning.py` | 推定器の呼び出し（:3045 の `else (` 内へ一次判定を差し込み）/ CHIT_CHAT 分岐の置換（:3049〜）/ `_get_casual_teacher_system_prompt` の `spoken` 引数（:1269, :3294）/ 痕跡 payload に `stance`・`stance_source`（:3482）/ レスポンスへ `stance` |
| `backend/api/schemas.py` | `LearningChatResponse.stance` の追加（:376〜。リクエストは無改変） |
| `backend/core/label_vocab.py` | `LEARNING_STANCE_LABELS`（表示ラベルの正本） |
| `backend/core/discuss/observation.py` | `METRIC_EVENT_VOCAB` に `stance_corrected`（:318）/ payload 値語彙に `stance`（:358 付近）/ ダンプ列に `stance`・`stance_source`（:770 付近の説明文含む） |
| `frontend/public/js/app.js` | 様相チップと訂正チップの描画・再送配線（`sendWith`:4864 は無改変）/ `stance_corrected` の送信 |
| `docs/backend/rag-chat.md` | §2.9 判定順に推定器の位置を追記 / §3 の intent_mode 表に「様相はサーバが推定・明示は上書き」を追記（規約 5-1 の必須更新） |
| `docs/features/learning.md` | 学習者向け機能の記述更新（規約 5-1 の必須更新） |
| `docs/features/discussion_mode_design.md` | §6.4 近傍に解消/非スコープ注記（音声版 discuss は依然非スコープであることの再確認、規約 5-3） |
| `backend/tests/test_domain_neutral_wording.py` / `backend/tests/test_help_usage_route.py` | §9 の表のとおり 2 アサーションの移設 |

**migration: なし**（DB スキーマ変更なし。`interest_traces.payload` / `discuss_metric_events.payload`
はいずれも既存 JSONB のキー追加のみ）。
