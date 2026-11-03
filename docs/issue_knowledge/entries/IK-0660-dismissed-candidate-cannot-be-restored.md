---
id: IK-0660
title: "学習者が「違う」で見送った引っかかり・問いの帰属の候補を戻す操作が無い"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/understanding_cycle_design.md"
  - "docs/features/trace_registry_sovereignty_ledger_design.md"
  - "docs/features/disclosure_axes_design.md"
feature_context:
  realizing: "学習者が AI の候補（引っかかり・問いの帰属）を見送った後、考え直して引き受け直す"
  layers: [learner_experience_b, trace_registry]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
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
    確認: tension と問いの帰属の API は confirm / dismiss だけで、dismissed からの戻しが無い（routes/learning.py）。行は状態として残るが、逆向きの遷移が定義されていない。構造軸 representation = 状態語彙に見送りの取り消しが無い。統制軸 review = 本人の否定の判断を本人が訂正する手続が無い。処理軸 none。接続軸 none。
generalization:
  level: general
  general_form: "否定の判断は状態として残すが、肯定の判断と違って本人が取り消す遷移を持たない"
pattern: negative-decision-second-class
discovery:
  perspective: [invariant_audit]
  note: "第 23 周の机上レビューで、可視性の宣言（見送った候補は後から戻せる）と現物の API を読み合わせて見えた。"
resolution:
  perspective: [pending]
  note: "未解決。宣言は現物どおり「戻す操作はありません」に直した。戻すなら、地図の除外と同じく状態遷移の API と本人向けの入口を足せば解ける。"
related: [IK-0654]
view_of: []
history: []
---

## 課題

症状: 「違う」で見送った候補を本人が引き受け直せない。原因: 見送りからの逆向きの遷移と入口が無い。

## 発見の観点

宣言 × 候補の API の読み合わせ（invariant_audit）。

## 解決の観点

未解決。行は残っているので、状態遷移を 1 つ足せば足りる見込み。見送りを戻せるようにするかは本人の訂正主権の範囲で、宣言との整合を先に取った。

## 一般化

否定の判断を一方通行にすると、本人は誤って押した見送りを直せない。型は negative-decision-second-class。確かめ方は、見送った候補を本人が戻し、確定・地図・台帳に元の扱いで現れることを見ること。
