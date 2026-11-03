---
id: IK-0985
title: "説明カードが状態と操作・知らせを対応させない — 査読済み（公開可）の説明にも［学生に公開してよいと査読する］が残り、共有の切り替えの知らせは「共有設定を更新しました」で ON / OFF のどちらになったかを言わない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が自分の説明を共有し査読して、いまの状態を 1 操作で確かめる"
  layers: [endorsement_c, lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording（操作の見え方を含む）: admin-lecture-studio.js lsRenderExplanationCard は can_edit なら review_status を見ずに［学生に公開してよいと査読する］を出し、共有の知らせは方向を含まない固定文。IK-0955 / IK-0925 と同じ形（状態ごとの出し分けの欠け）で、権限・データ・接続は正しい。
generalization:
  level: repo_pattern
  general_form: "操作ボタンと完了の知らせを状態に合わせず、済んだ操作を再び促し、何が変わったかを言わない"
pattern: permission-and-affordance-asymmetric
discovery:
  perspective: [reproduction]
  note: >-
    te-04 が査読の後「もう一度押す必要があるのか迷う」と記した（uxsim/runs/c-astro-r25-share-r5/20261027T070623Z-23499 seq5）。共有 ON の知らせが方向を言わないことも同 seq4。カードの描画条件で確かめた。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    査読ボタンは review_status が teacher_approved でないときだけ出す（admin-lecture-studio.js lsRenderExplanationCard）。PATCH の応答に outcome_fact（「共有を ON にしました。」「共有を OFF にしました。」「学生に公開してよいと査読しました。」「もう一人の査読者として査読しました。」+ いまの届く先の文）を返し、lsCallExplanation はこれを優先して出す（文言の正本は core/explanation_governance.py）。標準の説明には共有の切り替えを出さない（届く範囲は査読者の人数で決まる）。査読を取り消す操作は C層の状態遷移の語彙と合わせて次に決める。
  landed_in:
    - "backend/api/routes/theory_components.py"
    - "backend/core/explanation_governance.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "backend/tests/test_share_round25_r5_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面での査読済みカードと共有の知らせ"
related: [IK-0955, IK-0925, IK-0924]
view_of: []
history: []
---

## 課題

症状: 査読済みのカードにも査読ボタンが残り、教員が再操作の要否に迷う。共有の知らせからは ON になったのか OFF になったのか分からない。原因: ボタンと知らせが状態を見ない。

## 発見の観点

再現（te-04 (b1) seq4・seq5）。

## 解決の観点

是正案: 査読ボタンは review_status が teacher_approved 以外のときだけ（査読済みには「査読を取り消す（査読待ちに戻す）」を出すかは C層の状態遷移の語彙と合わせて決める）。共有の知らせは「共有を ON にしました（…の学生に届く範囲が変わります）」/「共有を OFF にしました」と方向を言う（文言の正本はサーバの事実文）。

## 一般化

状態と操作の対応の欠け。確かめ方は、査読済みの説明に査読ボタンが出ず、共有の知らせが方向を言うことを見ること。

## 第 5 波の是正（第 25 周・面 A、2026-10-27）

是正済み。査読ボタンは review_status が teacher_approved でないときだけ出す（admin-lecture-studio.js lsRenderExplanationCard）。PATCH の応答に outcome_fact（「共有を ON にしました。」「共有を OFF にしました。」「学生に公開してよいと査読しました。」「もう一人の査読者として査読しました。」+ いまの届く先の文）を返し、lsCallExplanation はこれを優先して出す（文言の正本は core/explanation_governance.py）。標準の説明には共有の切り替えを出さない（届く範囲は査読者の人数で決まる）。査読を取り消す操作は C層の状態遷移の語彙と合わせて次に決める。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: 観測範囲で GONE。査読済みの教員の説明（te-04 の説明）に［学生に公開してよいと査読する］が出ず、標準の説明に［共有 ON/OFF］が出ない。共有の切り替えの知らせ（outcome_fact）は今周押されず未評価。run: uxsim/runs/c-astro-r25-share-r6/20261027T123545Z-11458 seq3
