---
id: IK-0917
title: "同一性リンクの確定画面に部品側の意味（要約・入出力）が無く、粒度の違う対（論文の部品「TOV を課す構造ネットワーク」⇄ 共通部品「TOV 方程式」）を同じものとして確かめずに確定でき、その確定が学生の「共通部品として」にそのまま届いて意味がずれる"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が論文の部品と共通部品を同じものとして結び、学生が部品の文脈から共通部品をたどる"
  layers: [deliberation_w, concept_registry, learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [review]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 meaning: 同一性リンク（element_identity_links）は「同じもの」の 1 つの意味しか持たない（KN-2）。確定画面（deliberation.js
    _identityLinkRowHtml）は共通部品の名前・要約だけを描き、部品側の要約・入出力（overview の decomposition にある）を並べない。学生側は
    core/component_context.py:574-605 の _build_shared_part が確定リンクをそのまま「共通部品として」に出す。構造軸 representation:
    「同じもの」と「使う・課す」を区別する表現が無い。統制軸 review: 判断の弁はあるが、弁を通るときに判断材料（両側の意味）が並ばない。
generalization:
  level: repo_pattern
  general_form: "2 つのものを同一と記録する手続に、両側の意味を並べて照合する材料が無く、粒度の違う対が同一として確定されて下流の利用者に届く"
pattern: unit-of-work-undefined
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-02 は部品の説明を見ずに確定し（uxsim/runs/c-astro-r25-share-r3/20261023T032742Z-99528 seq6-7）、続く学生 st-09・st-04 が「この部品は TOV
    方程式を満たすように学習するネットワークで方程式そのものではない」と記した（uxsim/runs/c-astro-r25-share-r3/20261023T033153Z-19784
    seq11・uxsim/runs/c-astro-r25-share-r3/20261023T033353Z-33782 seq8）。確定の画面と学生の DTO を突き合わせた。
resolution:
  perspective: [representation_change]
  note: >-
    確定の判断材料を弁の位置に置いた。未確定の行の応答に confirm_note（identity_links.CONFIRM_NOTE「確定すると、この 2 つを同じものとして扱います。学生の画面では…共通部品に左の共通部品が出ます。部品が共通部品を使う・満たすだけの関係なら…確定しないでください」）を載せ、deliberation.js は共通部品の名前・要約と部品側の名前・要約・入出力を並べた直後、確定ボタンの直前にこの文を描く。学生側は component_context の shared_part.heading「教員がこの部品と同じものとして確かめた共通部品」を app.js が素通しで描く。リンクの種類は足さない（指揮者の判断・KN-2 の「同じもの」を保つ）。
  landed_in:
    - "backend/core/deliberation/identity_links.py"
    - "backend/api/routes/deliberation.py"
    - "frontend/public/js/deliberation.js"
    - "backend/core/component_context.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_share_round25_identity_links.py"
    - "backend/tests/test_share_round25_identity_consumers.py"
  verification:
    methods: [guardrail]
    unverified:
      - "粒度の違う対を確定しようとしたときの実画面（次周の te-05 × (a')）"
      - "使う・満たす関係の記録（v1 では記録しない）"
related: [IK-0916, IK-0920]
view_of: []
history: []
---

## 課題

症状: 「TOV network map」（構造ネットワーク）を共通部品「TOV 方程式（静水圧平衡）」に結んで確定でき、学生の部品の文脈に「共通部品として: TOV 方程式」が出て、学生が意味のずれに気づく。原因: 同一性リンクは同じものの 1 意味しか持たず、確定の画面に部品側の意味が並ばない。

## 発見の観点

教員と学生のペルソナを同じ周で続けて通し（reproduction）、教員の確定から学生の表示まで辿った（trace_walk）。

## 解決の観点

是正済み（第 3 波）。確定の直前に両側の意味と「同じものとして扱う・学生に何が出るか」の事実文を並べ、学生側の見出しを「教員がこの部品と同じものとして確かめた共通部品」にした。リンクの種類は足していない。

## 一般化

照合の材料が弁の位置に無い型。確かめ方は、粒度の違う対（部品 ⇄ それが使う方程式）を確定しようとしたとき、確定の直前に両側の意味が並び、学生側の見出しが「同じものとして確かめた」と言うことを画面で見ること。
