# ゼミ前ブリーフと鏡面化 設計書（Phase 5）

**状態:** 実装済み（Phase 5 v1、2026-08-15。§3 精査記録・§4 実装記録）
**作成日:** 2026-08-15
**由来:** [討論記録](../architecture/ai_assistant_personalization_debate_2026-08-15.md) 提案1
「手渡しの弁」v1（ゼミ前ブリーフ）+ 追補討論 EX-3b 裁定（鏡面化 move）。
実装計画は [personalization_implementation_plan.md](../architecture/personalization_implementation_plan.md)。

migration: **不要**（両者とも読み時合成とプロンプト move 追加のみ）。
**鏡面化の実装前提条件**: Phase 1 の主権台帳 v1（来歴欄）が先に存在すること（EX-3b 裁定）。

---

## 1. ゼミ前ブリーフ（提案1 v1）

輪講の前に教員が対象論文の「賭け金」を10分で把握するための read-only 合成ビュー。

### 1.1 不変条項

- **SB1 新テーブル・新LLMゼロ** — 既存投影の読み時合成のみ（`compile_open_assumptions` +
  `support_paths` + claim つまづきサマリー）。
- **SB2 数値を見せない** — 件数・人数の生値なし。既存のレンジ・段階ラベル・事実文のみ。
- **SB3 第4区画は空欄で予約** — 「学習者からの手渡し」区画は v1 では空欄のまま設ける
  （手渡しチャネル本体は P3 改正の例外設計書を経る v2）。空欄は発見の流儀で、
  警告色・催促文にしない。
- **SB4 誰が何を挙げたかの集計を作らない** — ブリーフに学習者個人・学習者別件数の
  クエリ経路を作らない（P3。ガードレールで構造固定）。

### 1.2 構成（4区画）

1. **脆い前提** — 未検証 × 下流影響「高」の前提（D層 open-assumptions の投影を
   load 段階ラベル降順で上位のみ）
2. **一点吊りの支持線** — SL層 `support_paths` の `level=single` の事実文
3. **晴れ間** — 「このコーパスの中では検証記録が**見つかり**ません」の閉世界事実文
   （SL1 語彙。実装固定文は `FACT_LINE_NO_VERIFICATION_RECORD` =
   「このコーパスの中では検証記録が見つかりません。」— SL1 ガードレール
   `test_stakes_ledger_guardrails.py` 準拠の文言で、本節の記述は実装に揃える）。
   なお晴れ間は `compile_open_assumptions` の投影（区画①と同一ソース）から
   untested × スコープ空欄を選ぶため、**高負荷（load_level ∈ {high, highest}）の
   前提に限定される**のは設計どおり（低負荷対象の晴れ間網羅はブリーフの責務外 —
   全域の晴れ間は SL層の未検証合意リスト側の守備範囲）。
4. **学習者からの問い** — v1 は空欄予約（「（この区画は手渡しの仕組みの実装後に使われます）」）

### 1.3 実装

- API: `GET /api/admin/documents/{ref}/seminar-brief`（`_require_teacher` +
  `_ensure_document_viewable`。読み時合成の admin API 1本）。
- UI: 教材管理の行 ⋯メニュー「ゼミ前ブリーフ」→ モーダル（admin.js）。
  3点セット（teacher マニュアル節 + ADMIN_UI_ANCHORS + data-ui-anchor）。
- テスト: `test_seminar_brief_api.py`（権限 fail-closed・数値非漏洩・第4区画の空欄予約）+
  `test_seminar_brief_ui_static.py`。

## 2. 鏡面化 move（EX-3b 裁定 — 7人格全員一致の修正採用）

discuss の歩調合わせ（DA設計）に、学習者の解釈表明・詰まりに対する「鏡面化」move を正式追加する。
UC7 が唯一許す個人化「本人の産出物を本人に見せる」の対話版。

### 2.1 確定仕様（裁定 §9.5 EX-3b の逐条）

1. **発動は解釈表明・詰まりの発話のみ** — 質問への即答（DA1）は非改変。鏡による回答の
   置換を禁止（プロンプト契約フレーズで固定）。
2. **本人発話の逐語引用を必須** — 鏡文は学習者の直前発話からの verbatim 引用を必ず含む
   （uptake 規約の流用・引用ゼロの純合成禁止）。鏡が映してよいのは発話であって能力・傾向ではない。
3. **推量形 + 訂正チップ** — 「〜と捉えている、で合っていますか」型（P2 と同一機構）。
4. **窓の内のみ** — 素材は当該セッションの `window_history` 内のみ。セッション横断の傾向合成・
   鏡像の保存・プロファイル化を禁止。鏡の内容を提示内容・提示順・対話方針へ還流しない
   （UC5 への防火壁 — ガードレールで固定）。
5. **著者性の視覚区別** — 鏡文は AI 由来であることを本人発話と区別して表示する。
6. **痕跡化は本人 confirm 経由のみ** — 鏡への訂正・confirm は既存の tension/anchor digest 弁に
   流れる（新しい確定経路を作らない）。confirm 済み痕跡は主権台帳の第一級対象
   （Phase 1 で整備済みの台帳に自動で載る — 台帳は全 kind を読むため追加実装不要）。
7. **一般知識の持ち込み禁止は鏡 move の内部制約に限定** — 回答モードの RAG・出所ラベル・
   出典リビール（UC2）は不変。鏡は出典リビールの前段であって代替ではない（橋本の順序論）。

### 2.2 実装方式（着手時に確定する2案）

- **案A（プロンプト契約のみ）**: `_get_discuss_system_prompt` の move 指示に鏡面化を追加し、
  契約フレーズをガードレールで grep 固定（既存 DA 実装と同方式・LLM 1コール不変・P6 安全）。
- **案B（構造分離 + 検査）**: 応答を structured output で `mirror` 部と本文に分離し、
  verbatim 引用の機械検査（不合格時は鏡部のみ落として本文配信 — 縮退・再生成なし）。
- v1 は案A で入れ、討論記録の「validator 強制」要求（§9.5 EX-3b ②）は案B を v1.1 として
  追うことを本書に明記する（同期パスの再生成コストを避ける段階導入）。

### 2.3 テスト

- `test_mirroring_prompt_guardrails.py`（契約フレーズの逐語存在・質問 move 非改変・
  「保存しない」「プロファイル化しない」の構造検査 — 鏡文の DB 書き込み経路が無いこと）
- `test_discuss_mode.py` への回帰追加（DA1 即答契約の既存テストが壊れないこと）

## 3. 実装前精査記録（2026-08-15）

① **鏡面化は案B-lite で実装**（案A 単独は不採用）: 既存規律「本文中マーカーはサーバ側で
決定論的に構造化フィールドへ正規化し、フロントは構造化フィールドのみ描画する」
（`extract_inline_actions` の一元化方針）により、案A（プロンプトのみ）ではEX-3b⑤の視覚区別が
フロント regex になり規律に反する。v1 = プロンプトで鏡文を固定マーカー `〔鏡〕…〔/鏡〕` に
出させ、learning.py の既存後処理位置でサーバが決定論抽出 → `LearningChatResponse.mirror`
（optional）。**verbatim 検査**: 鏡文中の「」引用が学習者の直前発話の逐語部分文字列で
なければ鏡部をマーカーだけ剥がして通常本文に縮退（再生成なし・P6）。
② **発話タイプ別ルールの番号を増やさない**: 鏡面化の指示は既存ルール2（言い直し）・
ルール3（足場かけ）の内部に追記する（「ルール1を優先」等の契約フレーズ・関数シグネチャ・
足場分岐の切り出しキーは既存テストが固定 — 非改変）。
③ **窓内再注入は禁止対象外**: 鏡文は会話履歴として `window_history` の窓内にいる間
次ターンの LLM 入力に含まれる（全応答共通の構造）。設計の「素材は窓の内のみ」が許容する
範囲そのものであり、禁止されるのは**窓の外への持ち出し**（痕跡化・専用テーブル・
プロファイル化・別機能への還流）— ガードレールは窓外経路の不在を固定する。
④ **compile_open_assumptions に optional `document_id` を追加**（SQL に AND 1条件・
既定は従来挙動。percentile は course 全体のまま = 「高」の意味を保つ）。ブリーフは
`include_challenger_names=False` 固定・`dependent_count` / `n_items` 等の生数値を落とす（SB2）。
⑤ **晴れ間の事実文は新規生成**: 「このコーパスの中では検証記録が見つかりません」型の
固定文はコード上に存在しないため `core/doubt/` 配下に新設し、SL1 ガードレール
（`test_stakes_ledger_guardrails.py` の検査対象 `_assets()`）に新モジュールを追加登録する。
訂正チップ v1 は入力欄への文言プリフィル（[そのとおり]/[少し違う]）とし、送信は通常の
学習者発話 = 既存の tension/anchor digest 弁にそのまま流れる（新確定経路を作らない）。

## 4. 実装記録（2026-08-15）

- ブリーフ: `core/doubt/seminar_brief.py`（document 解決 → course_id 導出 → 4区画合成。
  DTO キーは `fragile_assumptions` / `single_support_lines` / `clear_skies` / `learner_handoff`。
  `_strip_numeric_keys` 再帰安全網・stumble は has_data 時の段階ラベルのみ）+
  `compile_open_assumptions` の optional `document_id` 拡張 + `routes/seminar_brief.py`
  （GET 1本・権限2段ゲート）+ admin.js のモーダル（landscape 雛形・textContent 基調・
  生トークンは逐語ミラー表でラベル化）+ アンカー2件（264→266）+ teacher マニュアル節。
  SL1 ガードレールの検査対象に seminar_brief.py を追加登録。
- 鏡面化: `_get_discuss_system_prompt` のルール2・3内部に 〔鏡〕 契約を追記（番号・既存文言・
  シグネチャ・足場分岐キーは非改変 — 既存契約テスト全 pass）+ `core/discuss/mirroring.py`
  （純関数 `extract_mirror`・「」内 verbatim 検査・不合格はマーカー剥がしの縮退・複数マーカーは
  2個目以降も記号のみ除去）+ `LearningChatResponse.mirror` + app.js の `.mirror-block`
  （「AIによる言い直し」ラベル・訂正チップ2つは入力欄プリフィルのみ = 送信は通常発話として
  既存の弁へ）+ 履歴復元時のマーカー剥がし。
- テスト: `test_seminar_brief_{api,ui_static}.py`（85）+ `test_mirroring_prompt_guardrails.py`（16）
  + `test_mirroring_ui_static.py`。全スイート 9,930 passed / 0 failed（2026-08-15）。

### 4.1 レビュー是正（2026-08-15 同日）

- **Fix 1（決定論化）**: `seminar_brief._derive_course_id` の theory_component_graphs
  クエリに `ORDER BY created_at DESC, course_id` を追加（複数コースに同一 document が
  紐づく場合に返す course_id が実行ごとに揺れないよう、最新の解析グラフを優先・
  同時刻は辞書順タイブレーク）。
- **Fix 2（verbatim 検査の all-quotes 化）**: `mirroring._has_verbatim_quote` を
  「鏡文中の**すべて**の「」引用が学習者の直前発話の逐語部分文字列」判定に変更
  （any-quote だと逐語引用1つを添えれば残りを自由に捏造できた）。あわせて1文字だけの
  引用は逐語証拠として弱すぎるため最短2文字ガード（`_MIN_QUOTE_CHARS = 2`）を追加。
  引用ゼロの不合格は従来どおり。
- **Fix 3（マーカー断片の残存防止）**: `extract_mirror` で 〔鏡〕/〔/鏡〕 のペアが
  成立しない場合（閉じ・開きマーカー欠落）も、マーカー断片だけ剥がした answer を返す
  （mirror は None・中身のテキストは本文に残す）。
- **Fix 4（ネスト時の鏡文非汚染）**: `mirror_text` は `_strip_markers(match.group(1))`
  を通してから採用（ネストした残存マーカーが鏡文へ混入しない）。

## 5. 非スコープ（v2）

- 手渡しチャネル本体（記名・撤回の双方向墓標・出口ルーティング — **P3 改正の例外設計書必須**）
- ブリーフ第4区画の実配信・包含来歴の記録
- 鏡面化の構造分離検査（案B）・鏡への訂正の専用 UI
