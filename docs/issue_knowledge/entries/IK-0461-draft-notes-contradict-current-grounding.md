---
id: IK-0461
title: "前の生成が本文に書いた「図・式・主張は紐づいておらず」の注記が、主張 14 件・図 3 件を供給した再生成の「現在の下書き」に残っていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の再生成が、前回の下書きを材料として渡しつつ、いまの根拠候補に基づいて書き直させる
  layers: [course_builder]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [version]
    governance: [resume]
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
    接続軸: IK-0440 は builder の定数と一致する文だけを外し、モデルが書いた自由文の注記（> 注: …）は外せなかった。いまの根拠候補の有無（種類ごと）もプロンプトに事実として渡していなかったため、モデルは前回の注記を正として引き継げた。版の食い違い（前回の根拠についての文）がそのまま次の版の材料になる。
generalization:
  level: repo_pattern
  general_form: 前の版の派生物（根拠についての注記）が、根拠が変わった次の版の入力に残る
pattern: stale-derivative-served
discovery:
  perspective: [data_inspection]
  note: 再生成プロンプトの「現在の下書き」の注記と、同じプロンプトの available_references の種類を突き合わせた。
resolution:
  perspective: [canonical_source, single_point_fix]
  note: >-
    grounding_facts_for_references が種類（主張・図・式・部品・原文抜粋）ごとの有無を事実文にしてプロンプトに渡す（件数は書かない）。生成の下書きを渡すときは「> 注 …根拠…」の行を外し、生成結果の本文と注意点からは、いまある種類について「紐づいていない／根拠に含まれていない」と言う行を外す（strip_contradicted_grounding_notes）。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0459_0465_course_draft_regen11.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成
      - 種類で言えない食い違い（「どの観測と緊張しているかは根拠に無い」— 緊張の主張は供給済み）は判定できず残る
related: [IK-0429, IK-0438, IK-0440, IK-0443, IK-0454]
view_of: []
history: []
---

## 課題

前回の根拠についての注記が、根拠の変わった再生成に持ち越された。

## 発見の観点

下書きの注記といまの根拠候補の種類を突き合わせた。

## 解決の観点

根拠の有無をいまの件数から事実として渡し、食い違う注記は種類で機械的に外す。

## 一般化

前の版の派生物を次の版に渡すときは、いまの状態から作った事実を並べ、食い違う派生物は外す。
