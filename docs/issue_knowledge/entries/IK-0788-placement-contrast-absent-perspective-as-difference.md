---
id: IK-0788
title: "配置の対比で、片側に配置の無い観点が「起点だけ」の項目として並び、観点の違いに見える"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §22.4"
feature_context:
  realizing: "レーダーの取り込み済み候補で、起点と候補の分野の地図上の配置を観点ごとに対比する"
  layers: [paper_radar]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（radar.placement_contrast の読み）: 対比は node 単位の集合差で、観点は項目の属性として付くだけだった。候補側にある観点の配置が 1 件も無い
    ことを表す欄が無く（構造軸 representation）、その事実が画面に渡らないので「起点だけ: 統計推論（方法から）」が方法の違いに読める（接続軸
    information）。処理・統制は none。
generalization:
  level: general
  general_form: "片側の欠落（その観点の記録が無い）を集合差に畳むため、記録の有無の差が中身の違いとして読まれる"
pattern: upstream-loss-masked-downstream
discovery:
  perspective: [data_inspection, symptom_report]
  note: "深い段の審判が deep-07.json の placements（候補に方法の観点の配置なし）と参照健全性（配置候補の生成で入力の一部を処理していない）を突き合わせた。"
resolution:
  perspective: [explicit_contract]
  note: "placement_contrast に perspective_facts を足し、片側にだけ配置のある観点ごとに「この論文（起点の論文）には○○の観点の配置がありません（違いを示すものではありません）」を事実文で返す。観点の名詞は landscape の perspective_label から「から」を外して作り、訳語表を増やさない。数値なし。"
  landed_in:
    - backend/core/paper_discovery/radar.py
    - frontend/public/js/admin-paper-radar.js
    - "docs/features/paper_radar_design.md §22.4"
  verification:
    methods: [guardrail]
    unverified:
      - "取り込み済みカードの画面上の表示（次の周の浅い段）"
      - "配置候補の生成の取りこぼし（参照健全性の記録）を対比の留保として出すこと"
related: [IK-0750]
view_of: []
history: []
---

## 課題

症状: 候補に方法の観点の配置が 1 件も無いのに、対比は「起点だけ: 統計推論（方法から）」と並べ、方法が違うように読めた。
原因: 対比は node 単位の集合差で、観点ごとの有無を表す欄が無かった。

出所: uxsim/runs/radar_loop/judge-deep-07.md ①・④-3。

## 発見の観点

記録の配置行と対比の規則を突き合わせた（data_inspection / symptom_report）。

## 解決の観点

観点ごとの有無を事実文として DTO に足した（explicit_contract）。

## 一般化

集合差で対比を見せるときは、片側にその種類の記録が無いことを別に言う。無いことは違いではない。
