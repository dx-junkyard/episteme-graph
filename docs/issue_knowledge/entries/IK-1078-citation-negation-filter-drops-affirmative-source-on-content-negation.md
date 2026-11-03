---
id: IK-1078
title: "学習チャットの回答が文中の内容の否定（「…サンプリングを使わない点が違う、と書かれています [出典9]」）を含む文で出典を引くと、否定の言及を引用と数えない判定が「使わない」に当たってその [出典9] を引用から外し、本文には [出典9]（論文に明記）が残るのに出典の一覧にも履歴にも 9 が無い"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "学習者が回答の [出典N] から、回答が根拠にした教材の箇所を必ず開ける"
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 logic（条件式の誤り）: routes/learning.py _affirmative_citation_indices は文を 。．！？ で区切り、_CITATION_NEGATION_RE（使っていません|使わない|無関係…）に
    当たる文のマーカーを引用と数えない。「外部の ODE ソルバーや事後分布のサンプリングを使わない点が違う、と書かれています [出典9]」は出典を肯定的に引く文だが、
    内容の否定「使わない」で文ごと除外される（.venv で正規表現を呼んで一致位置を確かめた）。文脈には [出典9] が在った（uxsim/runs/mailbox/req-04287.json）ので、
    LLM の捏造ではない。除外された番号は _sources_cited_in_answer から落ち、出典の一覧と履歴の出典メタにも載らない（製品頭脳 4288 は次の往復で「前回の [出典9] が
    文脈に無い」と記した）。
generalization:
  level: repo_pattern
  general_form: "言及が否定かどうかを語の出現で判定し、文中の別の対象についての否定まで言及の否定と読んで、肯定の言及を落とす"
pattern: substring-match-false-positive
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    第 25 周 面 A 第 9 周 st-07 (d) の選択質問 1（uxsim/runs/c-astro-r25-share-r9/20261031T015336Z-38765 seq10）。応答の sources は 5・6・8、本文は 5・6・8・9 を引用。
    文脈のプロンプトと応答の sources を突き合わせ、判定の関数を呼んで落ちた理由を確かめた。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    routes/learning.py _affirmative_citation_indices を文単位からマーカー単位に変えた。_marker_is_negated は、マーカーの直後に
    （「と」「、」で並ぶ別のマーカーを挟んで）助詞（は・も・が・を・について・is/was…）が続き、その後ろ — 次のマーカーの手前まで —
    に否定の語があるときだけ否定の言及と読む。文末の帰属（「…を使わない点が違う、と書かれています [出典9]」）と、後ろの別の
    マーカーについての否定（「結果です[出典1]が、[出典3]は無関係」の 1）は引用に数える。履歴の持ち越し・出典の一覧・痕跡の
    cited_chunk_ids が同じ関数を通る。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave9_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "st-07 の選択質問 1 の再演で出典の一覧と次の往復の履歴に 9 が載ること（第 10 周）"
      - "否定の語が助詞を挟まずマーカーの前に来る形（「無関係な[出典3]」）は否定と読まない（引用に数える側に倒した）"
related: [IK-0494, IK-0432]
view_of: []
history: []
---

## 課題

症状: 回答の本文が「[出典9]（論文に明記）」と言うのに、出典の一覧に 9 が無く、学習者は根拠の箇所を開けない。次の往復では履歴の出典メタにも 9 が無い。

原因: 否定の言及を引用と数えない判定が文単位で、文中のどこかに「使わない」などがあれば、その文のマーカーをすべて引用から外す。

## 発見の観点

再現: st-07 の選択質問の回答。データの確認: 文脈のプロンプトの [出典9] と応答の sources、判定の正規表現の一致位置。

## 解決の観点

否定の判定の範囲を「マーカーを主語・目的語にする節」に狭めた（第 25 周 第 9 波）。

## 一般化

「否定の言及か」を語の出現で決める判定は、判定の対象（マーカー）と否定の語の結び付きを見ないと、ほかの対象への否定に当たる。型 substring-match-false-positive。
確かめ方は、内容の否定を含み出典を肯定的に引く文で、出典の一覧に番号が載るかを見ること。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

run では再現の条件の文（内容の否定を含む文の [出典N]）が無く、3 往復とも本文の [出典N] は一覧にあった。`routes.learning._affirmative_citation_indices` を直接呼ぶと第 9 周の文「…サンプリングを使わない点が違う、と書かれています [出典9]」は [9] を返し、「[出典2] を引用していません」は [] — コードは GONE。run: uxsim/runs/c-astro-r25-share-r10/20261031T054227Z-72202（seq10・11・13）。
