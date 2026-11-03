---
id: IK-0352
title: AI 対話の材料が「論文自体の事実」と「論文を処理したときの記録」を1つの一覧に混ぜていたため、概要を尋ねた応答に未レビュー・式詳細の但し書きが混入し、表示専用の立場ラベルも本文に書かれていた
status: resolved
recorded_at: 2026-09-22
resolved_at: 2026-09-22
sources:
  - docs/features/graph_dialogue_review_design.md §18
  - docs/features/graph_dialogue_review_design.md §15
feature_context:
  realizing: グラフ対話レビューで、理論操作グラフの全体像を AI との対話でおおづかみに掴む
  layers: [graph_review, deliberation_w, frontend_admin_ui, tts_voice]
classification:
  axes:
    processing: [wording]
    structure: [decomposition, responsibility]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: grounding が「論文の理論構成」と「システムが処理したときの記録」（レビュー状態・
    検証記録・展開しなかった層・省略・件数）を区切りなく1つの一覧で渡していた混在
    （decomposition）。加えて、留保と網羅性を出す責務が画面（チップ・ラベル）と本文（LLM）の
    2 箇所に割れていた（responsibility）。接続軸: ヘッダの「…のラベルで示されます」は画面仕様の
    説明のつもりが生成側には指示に読め、処理側の記録も論文の事実と同じ資格で語られた — 段階間で
    文の意味がずれる（meaning。contract と迷ったので medium）。処理軸: 出力の文言が重複・冗長に
    なった面は wording だが、原因はむしろ材料の構成なので medium。統制軸: 順序・予算・権限の
    統制は正しく働いているので none。確認手段: オーナーの画面写真でチップと本文冒頭が同一文字列
    であること、graph_grounding_to_text の旧実装が主グラフ行に「レビュー:」を同居させ未レビュー欄に
    式の詳細層のノード名を数十行並べていたこと（実データ相当の fixture で描画して確認）、
    読み上げが本文由来の spoken を読む admin-graph-review.js の finish 呼び出し。
generalization:
  level: general
  general_form: 対象についての事実と、その対象を処理した側の記録を、区別の付かない1つの文脈にまとめて生成器へ渡す
pattern: wording-mismatch
discovery:
  perspective: [symptom_report, boundary_walk]
  note: >-
    オーナーが画面写真を添えて「ラベルは表示だけでよく読み上げは不要」「末尾の但し書きは
    ラベル程度でよい」と報告。続けて「論文自体の評価と、論文を処理したときのコメントが
    混在しているのが問題ではないか」と材料側を指した。grounding の生成（処理の記録）と
    対話の応答（論文の事実）の境界を歩き、渡している一覧を実データ相当の fixture で描画して
    材料の量と構成を確かめた。
resolution:
  perspective: [responsibility_move, explicit_contract]
  note: >-
    材料を2区画に分けるのが先で、プロンプトの禁止はその後という順序で直した。論文の事実の区画
    （原文の裏付けはその事実の確からしさを限定するので残す）と、処理・レビューの記録の区画
    （見出しに「教員が尋ねたときだけ使う」と明示）に割り、名前で挙げる未レビューは論文区画と
    同じ母集合（主グラフ）だけにして、件数・残り件数は渡さないことにした（「多数ありますが」の
    出所）。プロンプトは禁止の列挙ではなく区画の使い分けの宣言にした（explicit_contract）。
    留保と網羅性の常在表示は画面が引き受け（responsibility_move）、本文に書かれた場合に備えて
    受け取り側の共通骨格でも先頭ラベルを落とす。
  landed_in:
    - backend/core/deliberation/graph_dialogue.py
    - backend/core/llm_worker/chat_turn.py
    - backend/core/deliberation/dialogue.py
    - backend/core/label_vocab.py
    - frontend/public/js/admin-graph-review.js
    - backend/tests/test_llm_worker_stance_prefix.py
  verification:
    methods: [guardrail]
    unverified:
      - 実機で LLM が区画の使い分けに従い、処理側の但し書きの無い応答を返すこと
      - 音声ループでの読み上げがラベルから始まらないこと
      - 教員が処理側について尋ねたときに、その区画から答えられること
related: [IK-0350]
view_of: []
history: []
---

## 課題

**症状**: グラフ全体対話の応答が、画面の立場チップ「AIの読み（未確認）」の右に、本文として
もう一度「AIの読み（未確認）：……」から始まっていた。読み上げは本文（`spoken`）を読むため、
音声もラベルの朗読から始まる。さらに応答の末尾に「未レビューのノードや式詳細は多数ありますが、
この一覧に出ている範囲では追加の関係はグラフには現れていません。」という 2 文が毎回付いていた。

**原因**: 材料の混在。grounding（`graph_grounding_to_text`）が次を1つの平らな一覧で渡していた。

- 主グラフの各行に `（裏付け: … / レビュー: …）` — 論文の事実に処理の状態を同居させる
- `[式の詳細層] 式単位のステップが 28 ノード…（この一覧には展開していません）` — 渡された一覧
  自身の不完全さについての文。数を伴い、答えの材料にはならず但し書きにしかならない
- `[未レビューのノード]` に式の詳細層のノード名まで数十行 — 論文区画には出ない名前が並び、
  論文の事実 2〜3 行に対して処理側が数十行になる
- `(注記) …残り N ノードは省略。` — 同じく一覧自身の不完全さと数

この構成では、「この論文は何をしているか」を尋ねても処理側の状態が視界に入り続け、締めくくりの
但し書きとして出てくる。立場ラベルの二重化は別原因で、ヘッダの「不確かさは返答全体に付く
「AIの読み（未確認）」のラベルで示されます。」が、画面仕様の説明のつもりで生成側には
「そう書け」と読めたことによる。

## 発見の観点

オーナーの症状報告（`symptom_report`）から入り、「論文自体の評価と処理のコメントの混在では
ないか」という指摘を受けて、grounding を作る側と応答を作る側の境界を歩いた（`boundary_walk`）。
実データ相当の fixture で一覧を描画すると、論文の事実 4 行に対し処理側が 30 行を超えていた。

## 解決の観点

材料を「論文についての事実」と「処理・レビューの記録」の2区画に割り、後者は見出しで
「教員が尋ねたときだけ使う」と宣言した（`explicit_contract`）。留保と網羅性の常在表示は画面の
ラベルへ移し、本文に書かれた場合に備えて受け取り側でも先頭ラベルを落とす（`responsibility_move`）。
原文の裏付けだけは論文側に残した — 推定を確定として語らせないための限定であり、処理の記録では
なく事実の確からしさだから。

## 一般化

対象についての事実と、その対象を処理した側の記録（進捗・レビュー状態・検証ログ・打ち切りの
注記）を、区別の付かない1つの文脈で生成器へ渡すと、受け手はそれを同じ資格の材料として使い、
求められていない但し書きとして出力に混ぜる。**混ざる材料を渡さないのが先で、プロンプトの禁止は
その後**。渡す必要があるなら区画を分け、既定で使う側と尋ねられたときだけ使う側を宣言する。
辞書の `wording-mismatch`（出力文言の重複・冗長）に当たる型だが、原因は材料の構成側にある。
構造軸の `decomposition`（分割の粒度・混在）がこの「性質の異なる事実を1つの文脈に混ぜる」を
そのまま言えるので、新しい値は提案していない。
