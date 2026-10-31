---
id: IK-0436
title: "論文の海から開く document 直付けの議論で「この論文の最も重要な結果を一文で」と聞くと、検索が当たらず未踏ガードだけの回答になっていた（開幕画面は同じ論文の問い・目的・主張を出しているのに、会話の文脈には渡していなかった）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コースの外から論文を開いた学習者が、その論文の要旨について質問して答えを得られる
  layers: [corpus_roaming, discuss]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: backend/core/discuss/opening.py の project_thesis は thesis_reconstruction / paper_skeleton artifact から
    central_question・paper_goal・central_thesis_text を開幕画面に投影しているが、document 直付けのチャット
    （routes/learning.py document_discuss_chat → _learning_chat_core）の文脈はチャンク検索の結果だけで組まれ、
    要約の問いは本文のどの一節とも近くないため 0 件になった（information）。処理・構造・統制は none（要旨の投影
    自体は正しく、スコープの規則 DM1 も正しい）。確認: 第 9 周の transcript で、要旨の問いに出典 0 件・ガードのみの
    回答が返り、同じ document の開幕 DTO には central_thesis があった。
generalization:
  level: repo_pattern
  general_form: 画面の一方が既に持っている要約を、同じ対象についての対話の入力には渡していない
pattern: available-but-unwired
discovery:
  perspective: [trace_walk]
  note: 第 9 周の論文の海の往復で、開幕 DTO とチャットのプロンプト入力を並べた。
resolution:
  perspective: [carry_through]
  note: >-
    core/discuss/opening.py に document_thesis_fact_lines（project_thesis を再利用・読み取りのみ・LLM 0 回・
    fail-soft）を足し、document 直付けの discuss のときだけ、その事実行を「この論文の要旨（解析で再構成したもの・
    番号付き出典ではない）」の見出しで文脈の先頭に置く。cited_sources・content_grounding には数えず、検索スコープも
    広げない（DM1）。未踏ガードが付く往復では、要旨に書かれた問い・主張はガードの対象外として答え、解析結果の要旨に
    基づくと一言添える補足を system に足す。コース経路の discuss には足していない。
  landed_in:
    - backend/core/discuss/opening.py
    - backend/api/routes/learning.py
    - backend/tests/test_ik0432_0437_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（要旨の問いに要旨ブロックから答えるかは生成モデル次第で、実機で見ていない）
      - 要旨が英語で保存されている論文では英語の事実行がそのまま渡る（和訳しない = DM8）
      - コース経路の discuss（開幕画面あり）には要旨ブロックを渡していない
related: []
view_of: []
history: []
---

## 課題

document 直付けの議論で、論文の要旨についての問いに答えられなかった。

## 発見の観点

開幕画面の DTO とチャットの入力を並べた。

## 解決の観点

開幕画面と同じ投影を、番号付き出典ではない事実行として会話の文脈に運ぶ。

## 一般化

画面が持つ要約を、同じ対象の対話の入力には渡していない型。
