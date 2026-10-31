---
id: IK-0531
title: "ゼミ前ブリーフが台帳を course_id 付きで引くため、course_id 空で記帳された台帳行を拾えず、台帳が無いことも言わなかった"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/features/seminar_brief_mirroring_design.md
feature_context:
  realizing: "教員がゼミ前ブリーフで脆い前提を見る"
  layers: [seminar_brief, doubt_d]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [condition]
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
    course_id 空の台帳を合成し、無いときは REASON_NO_LEDGER。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "記帳側と読み側で絞り込みキーの有無が食い違う"
pattern: condition-not-propagated
discovery:
  perspective: [data_inspection]
  note: "ブリーフが空だった。"
resolution:
  perspective: [single_point_fix]
  note: "course_id 空の台帳を合成し、無いときは REASON_NO_LEDGER。"
  landed_in:
    - backend/core/doubt/seminar_brief.py
    - backend/tests/test_seminar_brief_api.py
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified: []
related: []
view_of: []
history:
  - date: '2026-09-30'
    field: resolution.verification
    from: guardrail のみ・砂場再演は未確認
    to: guardrail + 砂場再演
    reason: >-
      第 15 周（persona_enactment_testing_design.md §18.7）の再演で症状の消失を頭脳メモが GONE と記録した。
---

## 課題

ブリーフが台帳を拾えなかった。

## 発見の観点

ブリーフが空だった。

## 解決の観点

course_id 空の台帳を合成し、無いときは REASON_NO_LEDGER。

## 一般化

記帳側と読み側で絞り込みキーの有無が食い違う。
