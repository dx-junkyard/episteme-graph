# 帰還の扉 設計書（Phase 2 — 帰還の三段 v1）

**状態:** 実装済み（Phase 2 v1、2026-08-15。§6 実装記録）
**作成日:** 2026-08-15
**由来:** [討論記録](../architecture/ai_assistant_personalization_debate_2026-08-15.md) 提案2
「帰還の三段」v1 + 追補討論 EX-2 裁定（対話型への置換は棄却・逐語トレイのみ採用）。
実装計画は [personalization_implementation_plan.md](../architecture/personalization_implementation_plan.md)。

migration: **不要**（`interest_traces` の kind 追加なし。`intention` の role 語彙追加のみ）。

---

## 1. 不変条項

- **RD1 扉は本人の言葉のみ・AI要約ゼロ** — 扉（再入口インレイ）に表示されるのは本人が書いた
  書き置き・本人が確定した痕跡の逐語のみ。AI の要約・言い換え・敷衍が一行でも混ざる経路を
  作らない（討論 §8「AIは朗読者・計器であって著者ではない」。ガードレールで構造固定）。
- **RD2 UC4 遵守** — トリガーは本人の再訪のみ。間（セッション外）の通知・督促・
  「14日ぶりですね」等の経過日数表示を作らない。
- **RD3 書かなければ何も出ない** — 書き置きは任意。書き置きを促す追加の確認プロンプトを
  出さない（P7）。LEAVE 時に AI 対話を必須の玄関にしない（EX-2 裁定の禁止事項）。
- **RD4 非LLM・読み時導出** — 扉の合成・トレイの列挙・欄外の印はすべて非LLM の決定論
  読み時導出（P6 / PN-2 / UC8）。
- **RD5 数えない** — 件数・日数・未消化数を出さない（UC9 / P7）。

## 2. v1 の3部品

### 2.1 書き置きの扉（leave_note）

- `core/cycle/schema.py` の `INTENTION_ROLES` に `leave_note` を追加（kind は既存 `intention` の
  まま — 痕跡登録簿の kind 追加は不要。登録簿 `writers` の記述だけ更新）。
- 記録は `services.record_cycle_intention` 相乗り。carryover と同じ「本人×コースにつき
  active 最大1件・新規記録時に旧行 `superseded`」規約（`_supersede_active_carryover` の
  role 別一般化）。
- 記入欄は discuss 着地画面（LEAVE）の末尾に1つ（「未来の自分への書き置き」）。既存の
  reflection・carryover と並ぶ任意入力。
- 扉 = コースビュー最上部の再入口インレイ（専用画面を作らない）:
  書き置き1 + 持ち越しの問い（carryover）1 + 最後に確定した tension 1 を表示。
  `core/cycle/derive.py` に純関数 `build_return_door(rows)` を追加し、
  読みは `cycle/queries.py` の既存関数＋最小追加で行う。
  API は `GET /api/learning/courses/{id}/cycle/return-door`（読み取り専用・本人のみ）。
- leave_note と carryover が同時に存在するときの提示順: 書き置き → 問い → tension の固定順
  （討論の未解決問いに対する v1 の仮決め。統合はしない — 別々の行として出す）。

### 2.2 「今日のあなたの言葉」トレイ（EX-2 裁定）

- 着地画面の書き置き欄の脇に**既定畳み**のトレイ。当日セッションの本人発話（chat 履歴の
  user ロール行）の逐語のみを非LLM で列挙し、タップで一文がそのまま書き置き欄へ引用される。
- サーバ API `GET /api/learning/courses/{id}/cycle/todays-words` は
  `learning_chat_history` から**当日・本人・user ロールのみ**を返す（assistant 行を返す
  経路を作らない — ガードレールで role フィルタを固定）。
- 表示には常に「あなたの言葉」ラベルを付す。
- これは UC7 が唯一許す個人化「本人の産出物を本人に見せる」に該当する（migration 0・LLM 0）。

### 2.3 欄外の印

- 教材区画の余白レイヤに、本人の確定痕跡（`structure_anchor` を持つ確定済み問い・確定 tension）を
  素材位置に淡い点として表示。ホバーで本人の言葉（逐語）を出す。
- `map_excluded` の痕跡は表示しない（既存の訂正操作を尊重）。opt-out 可（表示トグル。
  サーバに設定テーブルを作らず localStorage — 精読モードと同型）。
- 表示上限（新しい順に最大 12 点。間引きは数を見せずに行う）。減衰表示は v2。

## 3. 対話から思考を掬う仕事の委譲（明文化）

対話から思考・疑問・予測を掬い上げて痕跡化する仕事は、既存の tension/anchor digest
（非同期候補 → 本人 confirm）と discuss 着地 reflection（articulated 直接記録・非LLM）が担う。
本フェーズはそれらに変更を加えない（置換ではなく結線 — §9.6）。

## 4. テスト

- `test_return_door_core.py`（純関数: 3部品の合成・superseded 規約・AI 文非混入）
- `test_return_door_api.py`（本人のみ・読み取り専用・todays-words の user ロール限定）
- `test_return_door_ui_static.py`（「あなたの言葉」ラベル・経過日数表示の禁止語彙
  （「ぶりです」等）・setInterval 禁止・扉が textContent 描画であること）

## 5. 実装記録（2026-08-15）

- 実装: `core/cycle/schema.py` に `ROLE_LEAVE_NOTE`（INTENTION_ROLES 4値化）/
  `services.record_cycle_intention` の leave_note 分岐（`_supersede_active_carryover` の role
  パラメータ化 — carryover 挙動は不変）/ `core/cycle/queries.py` に leave_note・最後の確定
  tension・当日発話の3読み / `core/cycle/derive.py` に純関数 `build_return_door` /
  `build_todays_words` / `routes/cycle.py` に GET `return-door`・`todays-words` /
  `discuss.js` の書き置き欄＋逐語トレイ / `app.js` の扉インレイ `#return-door`（textContent
  描画・×はメモリ内のみ）＋欄外の印 `#margin-marks`（上限12・map_excluded 除外・
  トグルは localStorage `eg_margin_marks:<courseId>` — 精読モードと同型）/
  UIアンカー `material.return-door`（manual §14 `{#return-door}`）。
- 実装判断・近似（v1 として許容した逸脱）:
  ①欄外の印は素材位置への対応付けをせず**新しい順の縦並び** + ツールチップ冒頭に
  `anchor_label` を〔〕付きで表示する近似（位置対応は v2 候補）。並びも interest-traces
  応答の既存順（status 優先）先頭12点の近似 — 厳密な時系列には `created_at` 露出が必要。
  ②todays-words の「当日」は履歴行の `updated_at >= CURRENT_DATE` 近似（JSONB 1行方式の
  ため。docstring に明記）。③最後の確定 tension は `TENSION_OWNED_STATUSES` 正本を使用
  （リテラル再掲禁止を優先 — dead status `abstracted` も形式上対象）。
  ④扉 DTO は非空時 `empty: false` を同梱・carryover には `trace_id` を含む。
- テスト: `test_return_door_{core,api,ui_static}.py`（計 91）。バックエンド全スイート
  9,611 passed / 0 failed（2026-08-15）。

## 6. 非スコープ（v2）

- 扉の内側の想起リンク＋二列並置（右列既定畳み）・精読モード限定の1タップ想起ゲート
- 減衰表示（時間定数の設計込み）
- leave_note の「わたしの地図」時間層表示
