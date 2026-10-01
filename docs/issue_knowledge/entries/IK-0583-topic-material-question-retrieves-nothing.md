---
id: IK-0583
title: "表示中トピックの教材の内容を問う短い質問で検索がゼロ件になり、回答が「出典を追えない AI の説明」に倒れて出典の往復が始められない"
status: resolved
recorded_at: 2026-10-01
resolved_at: 2026-10-01
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.9
feature_context:
  realizing: "ペルソナ通し受講 第 17 周（4 つの根の是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 第 17 周 s-source-roundtrip（t8・DTD の論文）で「3つに分かれるという根拠はどこ？一言で。」に対し、製品の要求（mailbox）に教材からのコンテキストが一件も無く未踏ガードが立ち、応答の sources が空だった。回答の中身は表示中トピックの教材と一致しており、表示中トピックの教材チャンクが検索の母集合か閾値で落ちている疑い（接続軸 target = 表示中トピックという対象が検索に渡っていない）。同じ場面で直前と同じ内容の要約の出所が「教材に基づく」から「出典を追えない AI の説明」に反転した。検索の母集合・スコア・トピック教材の注入経路を砂場 DB で確かめれば確定する。
generalization:
  level: repo_pattern
  general_form: "画面に表示している資料を、問いの文面だけで引く検索の結果に依存させ、表示中であることを検索の条件にしない"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 17 周のペルソナの頭脳メモ（出典チップが出ず往復が始められない）と、製品頭脳の受けた要求（教材コンテキストなし・未踏ガード）を突き合わせた。"
resolution:
  perspective: [carry_through, guardrail_fix]
  note: '表示中トピックという対象を検索の外から運び込み、トピックの出典チャンクを問いの文面に依らず [出典N] として採用する。検索がゼロ件でも表示中の教材を根拠に答え、出典チップの往復が立つ。'
  landed_in:
    - backend/api/routes/learning.py
    - backend/core/course_data.py
    - backend/tests/test_wave17_topic_source_grounding.py
    - docs/backend/rag-chat.md
    - 3123f2d
    - cb301bb
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "ブラウザでの画面確認"
      - "出典の場所が「冒頭」と節名で不揃いな件（IK-0544）は別課題"
related: [IK-0511, IK-0382, IK-0580]
view_of: []
history:
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '第 18 波で表示中トピックの出典チャンクを [出典N] として採用した（ガードレールで確認・再演は次の周）。（persona_enactment_testing_design.md §18.10）'
  - date: '2026-10-01'
    field: resolution.verification.methods
    from: '[guardrail]'
    to: '[guardrail, reproduction_rerun]'
    reason: '第 18 周の再演（st-05 s-source-roundtrip）で、表示中トピックの教材を問う質問に出典チップが付き、出典を開く往復が始まった。（persona_enactment_testing_design.md §18.11）'
---

## 課題

t8 を開いた学習者が教材の内容をそのまま尋ねても、検索がゼロ件になり、回答の先頭に「出典が提示できない参考情報」の警告が付き、出典チップが出ない。出典本文を開く・紐づく主張を見るという往復の起点が立たない。

## 発見の観点

第 17 周のペルソナの頭脳メモ（出典チップが出ず往復が始められない）と、製品頭脳の受けた要求（教材コンテキストなし・未踏ガード）を突き合わせた。

## 解決の観点

表示中トピックの出典チャンクを検索結果とは別に [出典N] として採用するようにした（3123f2d・cb301bb の learning.py）。対象（表示中トピック）を検索の母集合の外から運び込む見立て。
原因は起票時の仮説のまま（classification.basis）で、是正後の再演で確かめる。

## 一般化

画面に表示している資料を、問いの文面だけで引く検索の結果に依存させ、表示中であることを検索の条件にしない。
