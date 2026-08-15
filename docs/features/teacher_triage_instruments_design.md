# 宣言された弁と静かな計器 設計書（Phase 4 — 教員支援 v1）

**状態:** 実装済み（Phase 4 v1、2026-08-15。§5 精査記録・§6 実装記録）
**作成日:** 2026-08-15
**由来:** [討論記録](../architecture/ai_assistant_personalization_debate_2026-08-15.md) 提案4
「宣言された弁」v1 + 提案5「静かな計器」v1。
実装計画は [personalization_implementation_plan.md](../architecture/personalization_implementation_plan.md)。

migration: **不要**（読み時導出とソートパラメータのみ）。

---

## 1. 不変条項

- **TT1 沈黙の並べ替えを作らない** — UC5 の精神を教員面へ自己適用する。既定は従来順・
  明示トグル・適用中の並び順を常に宣言表示する。
- **TT2 数値を見せない** — load の生値・生カウント・金額・残回数を出さない。段階ラベルは
  `label_vocab` 正本のみ（独自辞書禁止）。UC9 / U5 / M8 / D層 load と同型。
- **TT3 来歴を偽らない** — どの並び順の下で確定したかを監査 metadata に記帳する（RR3 同型）。
- **TT4 開始をブロックしない** — 計器は事実文の提示までで、ボタンの無効化・処理の中止を
  しない（提案5「リリースを止めない」系の思想。fail-open）。
- **TT5 学習者データ非入力** — 計器の入力は素材由来のみ（P3 / KN-4 非交差）。
- **TT6 AIは原稿を書かない** — 分割マーカー `===` を書くのは教員の手のみ（KN-3 同型の人間の弁）。

## 2. 負荷順トリアージ（提案4 v1）

- 対象キュー（v1 は2本から）: 説明レビューキュー（二層説明の review 一覧 API）と
  R層 item 監査キュー（`GET /api/admin/reconstruction/items/review-queue`）。
- 各キュー API に `sort` パラメータ（`default` | `load`）を追加。`load` は
  candidate の対象要素を D層 `load_calculator`（下流到達集合サイズ・決定論）で引き、
  段階ラベル（低/中/高/最高位 — 既存 D層語彙）で降順にする。**生値は返さない**。
- 一覧 UI に「並び順: 負荷の高い順」トグル + 適用中の並び順を宣言する一行
  （「基盤への影響が大きい順に並んでいます」型の事実文）。既定は従来順。
- 確定操作（既存 PATCH / confirm API）の監査 metadata に `sort_order` を追記
  （既存 `record_review_event` の metadata 拡張のみ — 監査語彙・entity_type は不変）。
- 対応が引けない candidate（graph ノードと直接対応しない等）は末尾に置き、
  「影響度を導出できない候補」と正直にラベルする（討論の未解決問い①への v1 の答え:
  縮退は隠さない）。

## 3. 静かな計器（提案5 v1 — 2枚とも「平常時は視界に無い」）

### 3.1 コスト見通しの一行

- 場所: 教材アップロードゾーンのモデルサマリ行直下と再解析モーダル。
- 導出: U層の既存 estimate API（`GET /api/admin/llm-usage/estimate/documents/{id}` —
  TEACHER 可・レンジのみ）+ `CostGate.daily_remaining`（反復照合解析が使用中の既存部品）。
- 表示: 日次枠に**収まらない可能性があるときのみ**
  「この規模の処理は、今日のAI利用枠に収まらない可能性があります。分けて実行することもできます」。
  収まる見込みのときは行自体を出さない（G6 の督促化防止）。数値・残回数なし。
- CostGate はプロセスローカルで厳密になり得ない制約を仮説文体（「可能性があります」）で
  正直に反映する（監査済みの表現）。fail-open: 導出失敗時は何も出さず処理を止めない。

### 3.2 ワーキングメモリ・レンズ（WMレンズ）

- 場所: 原稿スタジオのスライドプレビュー（`POST /api/admin/lecture-studio/preview-split` の
  応答に相乗り — 最薄の配線）。
- 導出（非LLM・読み時）: `build_topic_slides` / `split_slides` を**非改変で読み**、
  スライドごとに symbol_registry・derivation_chain artifact と join して
  要素相互作用性（相互依存する記号・数式の同時出現）を段階ラベル化する。
  閾値は D層 load と同じ段階方式で `label_vocab` に登録。
- 表示: 高負荷スライドにのみ小さな段階ラベル。展開で事実文
  「このスライドには相互に依存する記号 β・Ω_m・γ と数式2件が同時に現れます。
  読み上げ音声は添字・上付きを運べません」。
- 分割候補の点線提示・`===` 自動挿入はしない（v2。挿すのは常に教員の手 — TT6）。
- トピック教材由来スライド（記号照合が textual になる）は縮退を事実文で正直表示（v2 で拡充）。

## 4. テスト

- `test_teacher_triage_core.py`（load 導出の決定論・段階ラベルが label_vocab 由来・生値非漏洩）
- `test_teacher_triage_api.py`（既定 sort が従来順・sort_order の監査記帳・導出不能候補の末尾配置）
- `test_quiet_instruments_core.py`（WMレンズ非LLM・素材由来入力のみ・学習者データ非参照）
- `test_quiet_instruments_ui_static.py`（宣言一行・収まる時は非表示・ボタン非無効化・数値禁止語彙）

## 5. 実装前精査記録（2026-08-15）

① **load 段階ラベルの正本は `doubt/schema.py`**（`LOAD_LEVELS` / `LOAD_LEVEL_LABELS` =
低/中/高/最高位、`load_level_for_score` はコーパス内パーセンタイル型）。`label_vocab.py` §27 の
住み分け宣言（パーセンタイル型は doubt/schema 側）に従い、**再利用して新語彙を作らない**。
TT2 の「label_vocab 正本のみ」はこの場合「既存正本表を再利用し独自辞書を作らない」と読む。
② **load の読みはバッチ1クエリ**: `load_percentiles`（キューにつき1回）+
`epistemic_ledger` への `target_id = ANY(:ids)` 1クエリ + `load_level_for_score`。
`recompute_load_scores` は全行 UPDATE のジョブであり **item ごとに呼ばない**。
`load_score IS NULL` は「影響度を導出できない候補」として末尾（正直な縮退）。
③ **説明キューの element_id は agent 側 ID**（claim/component）のため、既存の一括変換器
（`context_lens._claim_id_lookup(document_id)` / `_component_id_lookup(document_id)`）で
DB UUID に解決してから台帳を引く。equation はそのまま、figure / document スコープは導出不能扱い。
④ **preview-split には document_id が無い** — optional フィールドとして追加（既定 None・
既存呼び出し不変。`auto_paginate` 追加時の派生モデル方式と同じ手口）。トピック教材経路は
equation_id が解決時に落ちるため v1 は textual 照合（`normalize_symbol`）+ 縮退の事実文。
⑤ **「今日のAI利用枠」は単一カウンタが存在しない** — 実体は末端4ステージの独立カウンタ
（apparatus は DB 集計・他3つはプロセスローカル CostGate）の**最小残数**による正直な近似。
アップロードゾーン（document 未存在）はカウンタのみ、再解析モーダルは estimate 上振れ×残数の
合成。単位不整合（回数枠 vs トークン量）は仮説文体「可能性があります」に織り込む。

## 6. 実装記録（2026-08-15）

- 実装: `core/teacher_triage.py`（台帳バッチ読み・agent ID 解決・安定ソート・導出不能の末尾配置・
  `LOAD_LEVEL_LABELS` 再利用）/ `core/llm_usage/forecast.py`（4カウンタ最小残数の近似・
  `{show, message}` のみ・fail-open）/ `core/lecture_wm.py`（normalize_symbol の textual 突合・
  決定論・最低段は wm キー省略）/ `label_vocab.py` に WM_INTERACTION 段階3種（固定閾値型）/
  ルート: 説明キュー GET `sort`（不正422）+ approve/dismiss/bulk の `sort_order` 監査追記、
  R層 review-queue `sort` + `ItemPatchRequest.sort_order`、`GET /api/admin/llm-usage/forecast`
  （+ `/documents/{id}`）、preview-split に optional `document_id` + wm 相乗り /
  フロント: 両キューのソートトグル＋load 時のみの宣言一行（既定は無宣言）・コスト見通し行2箇所
  （hidden 器・show=true のみ）・WMレンズ表示（サーバ label 素通し・JS 語彙表なし）/
  アンカー4件（deliberation.review-sort-toggle 等、カウント 260→264）+ teacher マニュアル3節。
- 実装判断: ①WM スコアは v1 では symbol_registry のみ（derivation_chain 結合は v2）
  ②WM 閾値 many=5 / very_many=9 は発明値（根拠コメント付き）③`sort=load` 時のみ応答に
  `"sort"` キー（宣言一行の素材）④sort 不正値は 422 ⑤course_id 混在時はキュー全件を導出不能
  扱い ⑥preview-split は response_model を外し条件付き wm 注入（`wm: null` の常時出力を回避・
  既存応答キーは完全不変）⑦再解析の見通しは analyze_images 変更時に再取得（イベント駆動）。
- テスト: `test_teacher_triage_{core,api}.py` / `test_quiet_instruments_{core,ui_static}.py`
  （計110）。全スイート 9,829 passed / 0 failed（2026-08-15）。

## 7. 非スコープ（v2）

- 通りすがりの開封（連続上限・撤退条件の事前定義が前提）・便り（📮）
- 配置キュー・カテゴリギャップキューへのトリアージ拡張
- 分割候補の点線提示・音声一括生成モーダルへの見通し展開
- 教材の密度（学習者向け射影 — EX-1 裁定。WMレンズと同一エンジンの射影として実装する）
