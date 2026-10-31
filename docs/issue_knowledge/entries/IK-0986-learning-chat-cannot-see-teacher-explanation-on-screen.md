---
id: IK-0986
title: "学生が部品の文脈で読んだ教員の説明について音声（casual）で「先生の説明と論文の違い」を尋ねると、回答は教員の説明に触れず、AI 自身の前の回答を「先生の説明」と取り違える — 音声の往復には画面文脈が渡らず、学生に届いている教員の説明が回答の材料に無い"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が部品の文脈に出た教員の説明と論文の記述の違いを、学習チャット（音声）で尋ねる"
  layers: [rag_chat, screen_adapter_sa, endorsement_c, tts_voice]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [unknown]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 接続軸 information — SA層の学習チャットの規約で casual（音声）は画面文脈を注入しない（選択テキストだけ）ため、部品の文脈に出ている承認済みの教員の説明（element 解決器の explanation 行）が回答の材料に入らない。casual の system が AI を「先生」として話させるので、「先生の説明」は AI 自身の前の発話と読まれる。確定には、同じ問いをテキスト（非 casual）で部品を選んだ状態から送り、解決器が教員の説明を渡すかを見ること、と runner が部品を開いたまま screen_context を送っているか（実画面の app.js の selection）を確かめることが要る。統制軸は unknown（音声に画面文脈を渡さない設計判断の範囲の問題かもしれない）。
generalization:
  level: repo_pattern
  general_form: "利用者に見えている材料が、伝達の形式（音声）の都合で対話の材料から外れ、利用者の指す対象を対話側が別のものと取り違える"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: >-
    st-07 の 2 問目（uxsim/runs/c-astro-r25-share-r5/20261027T071522Z-64789 seq11）。直前の seq9 で学生は te-04 の説明を読んでおり、回答は「さっき僕が…と言い切ったところ」と自分の前の回答を直した。製品頭脳 A・B の第 5 周の所見とも照らした。
resolution:
  perspective: [carry_through]
  note: >-
    casual（音声）でも画面文脈の element だけを解決する（kinds=("element",)。表示モード・台帳・位置づけ・実行記録は引かない・LLM 回数不変・保存 message 不変）。承認済みの説明の事実文は学習画面の区画名で「学習者の画面に出ているこの要素の「教員の説明」（承認済み）」と指す。フロントは開いている部品の文脈を state.openContextElement に控え、ラッチの無い送信（音声ループ）でもポップオーバーが表示中なら selection.element_* を申告する。仮説の確認: route が _is_casual のとき画面文脈を一切解決していなかったこと、app.js の selection が payload.element_id（ラッチ）からしか作られず部品の文脈を開いただけでは申告されなかったことをコードで見た。統制軸（音声に画面文脈を渡さない判断の範囲）は SA層設計書 §11.18 に改訂として記した。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/core/assistant_context/resolvers/learning.py"
    - "backend/core/assistant_context/schema.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_assistant_context_learning_route.py"
    - "backend/tests/test_assistant_context_learning_core.py"
    - "docs/features/assistant_screen_adapter_design.md"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面で部品の文脈を開いたまま音声で「先生の説明」と尋ねる往復（砂場の再演）"
      - "runner が部品を開いたまま screen_context を送っていたか"
related: [IK-0920]
view_of: []
history: []
---

## 課題

症状: 音声の学生が「先生の説明」と言うと、学生に届いている教員の説明ではなく AI の前の回答が対象になる。原因（仮説）: 音声（casual）の往復に画面文脈が渡らず、教員の説明が材料に無い。

## 発見の観点

再現（st-07 (d) seq9→seq11）。

## 解決の観点

第 25 周 第 5 波で解決（frontmatter の resolution）。当初の是正案: ① 音声でも element（選んでいる部品）の解決器だけは注入する（教員の説明・確定済みの共通部品の 1〜2 行）— LC/SA の casual の除外は「画面文脈の量」の都合で、学生が開いている部品の教員の説明は話題そのもの。② casual の system に「学生が『先生』と言うときは、教材・教員の説明を指すことがある」と 1 文。どちらも LLM 呼び出し回数は不変。

## 一般化

見えている材料が形式の都合で対話に渡らない型。確かめ方は、部品を開いたまま音声で「先生の説明」と尋ね、回答が教員の説明を引くことを見ること。
