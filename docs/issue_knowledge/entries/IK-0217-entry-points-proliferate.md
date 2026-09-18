---
id: IK-0217
title: 操作と案内の入口が機能追加のたびに横並びで増え、総量を誰も見ていない
status: open
recorded_at: 2026-08-01
resolved_at: null
sources:
  - docs/architecture/admin_ux_issues_2026-08-01.md §2
  - docs/architecture/feature_consolidation_proposals_2026-08-13.md §2-4
  - docs/architecture/vision_ux_gap_survey_2026-07.md §2 G5
feature_context:
  realizing: 増え続ける機能を、既存の画面に足しながら教員が使える形で提供する
  layers: [frontend_admin_ui, guidance_g, admin_copilot]
classification:
  primary: structure
  facets: [structure.decomposition, governance.review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    個々の入口はそれぞれ正しく動き、必要でもある。原因は、入口を置く粒度と優先度の設計が
    無いまま並列に足し続ける構造で、既存の入口を直しても次の機能追加で同じ密度へ戻ること。
    追加時に総量を見る担当が置かれていない点は副次の統制の問題。
generalization:
  level: repo_pattern
  general_form: >-
    機能を足すたびに同じ場所へ入口が並列に増え、優先度も総量も誰も管理していない
discovery:
  perspective: [symptom_report, inventory]
  note: >-
    「操作の列にボタンが多すぎる」という指摘を起点に、一行に並ぶ操作を数え、同種の入口が
    他にどれだけあるかを棚卸しした。同じ関心を扱う案内の入口が三つ、要素を深く見る入口が
    六箇所以上、教員向けのレビュー待ち一覧が七種以上あることが分かった。
resolution:
  perspective: [pending]
  note: >-
    教材の行は頻度で二層に分け（常時出すのは二つ、残りは畳む）、異常時の導線だけは畳まずに
    別の場所へ寄せた。ただしこれは一画面の是正であり、入口の総量を抑える規律
    （これ以上入口を増やさない・役割の境界を明文化する）は未着手。何を一つの入口とみなすかの
    基準が決まれば、他の増殖箇所にも同じ整理を当てられる。
  landed_in:
    - frontend/public/js/admin.js
    - docs/architecture/admin_ux_issues_2026-08-01.md §2.3
related: [IK-0218, IK-0203]
view_of: []
pattern: entry-point-proliferation
history: []
---

## 課題

**症状**: 教材一覧の一行に最大で九つの操作が横並びし、行ごとに幅が変わる。名前の似た別機能が
隣り合い、注記で区別している。同じ構図は他にもあり、案内の入口が三つ（説明・道案内・次の一歩）、
要素を深く検討する入口が六箇所以上、教員向けのレビュー待ち一覧が七種以上に増えている。

**原因**: 機能を足すときの置き場所が「既存の並びの隣」であり、頻度や重大度による階層化も、
入口の総量を見る担当も無い。個々の追加はどれも妥当なので、誰も止めない。

## 発見の観点

`symptom_report`（使いにくいという指摘）を起点に `inventory`（同種の入口の全棚卸し）。
一画面だけを見ると「ボタンが多い」で終わるが、棚卸しすると同じ増え方が複数の画面で
進行していることが分かる。

## 解決の観点

未解決。着地したのは一画面の是正だけで、①頻度による二層化 ②異常時の導線は畳まない
（畳むと異常に気づけなくなる）③目印の担い手は消さずに付け替える、という原則は得られている。
残るのは「これ以上入口を増やさない」ための規律と、役割の境界（どれが説明・どれが誘導・
どれが実行か）の明文化で、これは製品としての情報設計の判断を要する。

## 一般化

長寿命の管理画面で必ず起きる。個々の追加が妥当であることと、全体の密度が妥当であることは
別問題であり、後者を見る視点を誰かが持たない限り単調に増える。辞書に同型が無いため新しい
型として提案する（`entry-point-proliferation`）。
