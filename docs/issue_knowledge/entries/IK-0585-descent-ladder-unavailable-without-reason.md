---
id: IK-0585
title: "降下路（足場ダイヤル）が要素をコース内で解決できないとき {available: false} だけを返し、理由と次の操作の事実文が無い"
status: resolved
recorded_at: 2026-10-01
resolved_at: 2026-10-01
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.9
feature_context:
  realizing: "ペルソナ通し受講 第 17 周（4 つの根の是正後の再演・c-astro-structure-30）の場面"
  layers: [structure_descent, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    `routes/descent.py` と `core/descent/engine.py` の解決不能の分岐が `{"available": False}` だけを返す。空段の理由（IK-0519）は付いたが、要素そのものが解決できない縮退には事実文が無い（接続軸 information = 縮退の理由が画面に渡らない）。第 17 周ではハーネスが表示名（「式 (3)」）を element_id に送ったことが直接の引き金だが、縮退の無言化は製品側の欠落。
generalization:
  level: repo_pattern
  general_form: "解決できない入力に対して可否の真偽値だけを返し、なぜ使えないかを返さない"
pattern: failure-reported-as-success
discovery:
  perspective: [reproduction, trace_walk]
  note: "第 17 周 s-descend-and-nearby の頭脳メモ（available: false だけ）をルートとエンジンの分岐で確かめた。"
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: '降下路が要素をコース内で解決できないとき、{available: false} だけで終わらせず、解決できなかった理由と次の操作の事実文を返す。'
  landed_in:
    - backend/core/descent/engine.py
    - backend/core/descent/resolve.py
    - backend/tests/test_descent_core.py
    - docs/features/structure_descent_design.md
    - 7b90670
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（次の周で確かめる）"
      - ブラウザでの画面確認
related: [IK-0519]
view_of: []
history:
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '第 18 波で降下路の不能理由と次の操作の事実文を返すようにした（ガードレールで確認・再演は次の周）。（persona_enactment_testing_design.md §18.10）'
---

## 課題

降下路を開くと available: false の1行だけが出て、何が足りないか・どこから降りられるかが分からない。

## 発見の観点

第 17 周 s-descend-and-nearby の頭脳メモ（available: false だけ）をルートとエンジンの分岐で確かめた。

## 解決の観点

解決器が不能の理由を区別して返し、降下路はその理由と次の操作の事実文を添える（7b90670）。

## 一般化

解決できない入力に対して可否の真偽値だけを返し、なぜ使えないかを返さない。
