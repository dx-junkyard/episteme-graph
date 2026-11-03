---
id: IK-0667
title: "切り上げの挨拶や理解サイクルの予想が「問い［未解決］」として記録・軌跡・地図に並ぶ（問いでない発話の判定はあるのに記録の種類に使われていない）"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/understanding_cycle_design.md"
  - "docs/features/trace_registry_sovereignty_ledger_design.md"
feature_context:
  realizing: "学習者が予想を書いたり議論を切り上げたりした後、自分の記録と問いの軌跡を見返す"
  layers: [learner_experience_b, understanding_cycle, trace_registry]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 学習チャットは雑談・締めくくり・理解サイクルの予想を not_a_content_question と判定する（IK-0470）が、その判定は構造帰属と引っかかりのヒントを外すことにだけ使われ、痕跡の kind は question のまま記録される。わたしの記録では「今日はもう時間がないので、ここまでにします。」と予想が［未解決］の問いとして並んだ。処理軸 logic = 判定結果を記録の種類に渡さない分岐。構造軸 representation = 「問いでない発話」を表す種類が無い。
generalization:
  level: repo_pattern
  general_form: "ある種類の対象を見分ける判定を作ったのに、その判定を一部の後処理にしか使わず、記録の分類は見分ける前の既定のままにしている"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、夜の議論を挨拶で終えた学生と予想を書いた学生がわたしの記録を開き「挨拶が問い・未解決になっている」「予想だったことが分からない」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core）: not_a_content_question の判定を痕跡の表示種別にも運び、予想は「予想」、締めくくりは記録の種類を分ける（行は消さない）。"
related: [IK-0666]
view_of: []
history: []
---

## 課題

症状: 挨拶・予想が問い［未解決］として並び、本人が「未解決と言われると落ち着かない」と読む。原因: 問いでない発話の判定はあるのに、記録の種類は常に question になる。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。既にある判定を記録の種類まで配線すれば足りる（新しい判定は作らない）。

## 一般化

見分ける判定が後処理の一部にしか配線されていないと、利用者に見える分類は見分ける前のままになる。確かめ方は、問いでない発話を送り、記録・軌跡・地図のすべてで問いと区別されることを見ること。
