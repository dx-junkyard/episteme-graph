---
id: IK-0482
title: "種類が unknown の主張と短い否定文の主張からも再構成の問いを作っていた（言い直させる問いに答えがそのまま入る）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
  - docs/features/reconstruction_loop_design.md §4.2
feature_context:
  realizing: "学習者が自分で理論を組み立て直す余地のある問いだけを出す"
  layers: [reconstruction_r]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: 出題の対象は「出典に裏付けがあり教員が承認した」ことだけで決まり、主張の中身が問いになるかを見ていなかった（logic）。
    接続軸: 「承認済み」は主張が正しいという意味で、再構成の問いになるという意味ではない。種類が決まっていない主張と
    「この手法は〜を必要としない」型の短い否定文は、問いを書くと否定の対象がそのまま答えになる（meaning）。
generalization:
  level: general
  general_form: "生成の対象を別の目的の合格印（承認済み）で決め、対象の中身が生成物として成り立つかを確かめない"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "砂場の出題依頼 10 件のうち、種類 unknown の 2 件と短い否定文の問いを見た。"
resolution:
  perspective: [single_point_fix]
  note: >-
    種類が unknown（空を含む）の主張と 60 字以下の否定文（先頭 3 語以内に否定が来る英文・否定で終わる日本語文）を対象から外す
    （claim_context.authoring_skip_reason）。行は消さず、理由別の件数を worker.last_authoring_report とログに残す。
  landed_in:
    - backend/core/reconstruction/claim_context.py
    - backend/core/reconstruction/worker.py
    - backend/tests/test_ik0479_0482_recon_authoring_inputs.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（修正後の対象件数を実 DB で確かめていない）"
      - "既に作られた unknown・否定文由来の問いは残したまま（retire は教員の監査操作）"
      - "除外の件数は教員の画面に出していない（ログと読み取り口だけ）"
related: [IK-0480]
view_of: []
history: []
---

## 課題

種類が unknown の主張と短い否定文の主張からも再構成の問いを作っていた（言い直させる問いに答えがそのまま入る）

## 発見の観点

砂場の出題依頼 10 件のうち、種類 unknown の 2 件と短い否定文の問いを見た。

## 解決の観点

種類が unknown の主張と 60 字以下の否定文を対象から外す。行は消さず、理由別の件数を読み取り口とログに残す。

## 一般化

生成の対象を別の目的の合格印で決め、対象の中身が生成物として成り立つかを確かめない。
