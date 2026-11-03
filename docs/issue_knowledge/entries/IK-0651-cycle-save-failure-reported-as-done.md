---
id: IK-0651
title: "持ち越し・開いた動機・予想・予想との違いの保存が失敗しても「記録しました」等と表示される"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-09
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/understanding_cycle_design.md"
  - "docs/features/return_door_design.md"
feature_context:
  realizing: "学習者が論文との議論の開幕と着地で、動機・予想・違い・持ち越しの問いを書いて残す"
  layers: [understanding_cycle, discuss, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [aggregation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 保存関数が API の戻り値（失敗時 null）や例外を見ずに成功の文を出していた（discuss.js の保存箇所。書き置きだけが成否を見ていた）。処理軸 logic = 成否の分岐が無い。構造軸 aggregation = 保存と事実文の扱いが箇所ごとに書かれ、正しい作法（書き置き）が他に共有されていない。接続軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "保存の結果を確かめずに成功の文を出し、利用者は残っていない記録を残ったと信じる"
pattern: failure-reported-as-success
discovery:
  perspective: [invariant_audit]
  note: "第 23 周の机上レビューで、書き置きの保存（成否を見て失敗文を出す）と他の保存経路を並べて読み、作法の差から見えた。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "discuss.js に保存の結果を見て失敗の事実文を出す共通の扱いを置き、持ち越し・動機・予想・違いがそれを通るようにした。"
  landed_in:
    - "frontend/public/js/discuss.js"
    - "backend/tests/test_round23_learner_cycle.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "discuss.js の中の保存経路を 1 つの扱いに寄せた。再構成の自己確認・地図の除外・教材の印など他のファイルの同型の箇所は対象外で、全経路を走査する検査は置いていない。"
  verification:
    methods: [guardrail]
    unverified:
      - "実際の保存失敗（ネットワーク断・認証切れ）での画面"
      - "再構成の自己確認・地図の除外・教材の印の保存失敗（未是正）"
related: [IK-0650, IK-0661]
view_of: []
history: []
---

## 課題

症状: 保存に失敗しても「次回の開幕で待っています。」「記録しました。」「残しました。」が出る。原因: 保存の戻り値を見ずに成功文を出していた。

## 発見の観点

成否を見る経路（書き置き）と見ない経路の読み比べ（invariant_audit）。

## 解決の観点

成否を見る扱いを 1 箇所に置いて寄せた（canonical_source）。同型の箇所は他のファイルにも残っており、それらは別の課題として扱う。

## 一般化

保存の成否を利用者に返す作法が経路ごとに書かれていると、正しい経路が 1 つあっても他は嘘をつく。型は failure-reported-as-success。確かめ方は、保存 API を失敗させて各経路が失敗の事実文を出すことを見ること。
