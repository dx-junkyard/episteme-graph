---
id: IK-0779
title: "理論モジュールの構造照合が指紋の完全一致だけで、工程の型の集合が近い対（接点の本数だけ違う）について何も言えない"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §22"
  - "docs/features/theory_module_layer_design.md 追補（2026-10-16）"
feature_context:
  realizing: "教員が 2 本の論文の理論モジュールの構造の近さを読み、転用の手がかりにする"
  layers: [paper_radar, graph_review]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（builder の指紋と related の照合）: 指紋は工程の型の多重度・受け取る式と出す式の本数・前提の有無を 1 本の文字列にしたもので、
    照合は完全一致だけ。部分一致の段を持たない表現なので、型の集合が近い対は表せず落ちる。構造軸 representation。
    同一性候補（TM13）とは独立に、表示専用の段を足せば解ける。
generalization:
  level: general
  general_form: "比較の表現が完全一致の 1 本のキーだけで、近さの段が表現できないため、近い対が「一致なし」と同じ扱いで落ちる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "深い段の審判が両論文のモジュールの工程の型を手で並べ、型の集合が近く接点本数だけ違う対があることを記録した（推定量の正規化 ↔ 流入テンソルの規格化 など）。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: "related に表示専用の near_structure（汎用でない工程の型を 2 種以上共有する外枠の対・最大 5・ラベルと共通工程の訳語だけ）を additive に足す。指紋の完全一致は modules 側に残し、近い構造は同一性候補にしない（候補キー・書き込みなしをテストで固定 = TM13・O-6 従属）。閲覧できない相手は黙って外す。"
  landed_in:
    - backend/core/theory_modules/related.py
    - backend/core/library/structural_matches.py
    - frontend/public/js/admin-graph-review.js
    - "docs/features/paper_radar_design.md §22"
  verification:
    methods: [guardrail]
    unverified:
      - "実コーパスでの近い構造の出方（汎用手順の近さがノイズになる度合い）"
      - "コーパスが大きくなったときの他 document 外枠の全件読みの負荷"
related: [IK-0778]
view_of: []
history: []
---

## 課題

症状: 起点の「結果を導出・定義・正規化」と候補の「正規化・定義・近似・結果を導出」のように工程の型の集合が近いモジュールの対があっても、
指紋が多重度と接点本数まで要求するため、related は何も言えなかった。原因: 照合の表現が完全一致の 1 本のキーだけだった。

出所: uxsim/runs/radar_loop/judge-deep-06.md §①-b-3・§④-2。

## 発見の観点

記録の両側モジュールの工程を並べ、近いが一致しない対を確かめた（data_inspection）。

## 解決の観点

完全一致とは別に、型の集合の重なりを表示専用の段として表した（representation_change）。同一性に使わないことをガードレールで固定した。

## 一般化

完全一致のキーで比べる層に「近さ」を足すときは、近さを確定や候補化の根拠に流さない表示専用の段にし、その境界をテストで固定する。
確かめ方は、近さの出力を消費する経路を列挙し、書き込み・候補化の経路に届かないことを見る。近さは意味の近さではない（汎用手順の一致）ことを
画面で言う。
