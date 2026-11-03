---
id: IK-0798
title: "式由来の合成主張 synth_claim_0087 が根拠の evidence を約 150 件持つ連立系全体の主張なのに atomic=True で、強い backing に使える主張として後段に渡る"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
  - "CLAUDE.md"
feature_context:
  realizing: "式から合成した主張に atomic の印を付け、非 atomic の主張を ComponentGraph の強い backing に使わない規律を保つ"
  layers: [pipeline_a, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [completion]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（2026-10-16 にコードで確認・旧記述は仮説）: equation_claim_synthesis が合成主張に atomicity を一律で atomic と付け、連立系（system レベル）の主張のように多数の式・
    evidence を束ねるものも区別しない。統制軸 completion = 「1 主張 = 1 命題」であることを、合成した経路（決定論の定型文）で代理判定
    している。構造軸 representation = 合成主張の粒度（式 1 本か連立系全体か）を表す欄が atomic の判定に使われていない。処理軸 none /
    接続軸 none。確定には合成主張の atomicity の付け方と、system レベルの合成主張の生成条件を読む。
generalization:
  level: general
  general_form: "決定論で作った項目に品質の印を一律で付けるため、印の条件を満たさない大きな束も印付きで後段に渡る"
pattern: completion-defined-by-proxy
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 T0 が component_assembly（seq 2934）の available_claims を読み、synth_claim_0087 が ev 約 150 件を根拠に持つ巨大な system 主張で atomic=True になっていることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md）。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "system-level の合成主張は、命題の引数（消去 + 保持の記号）が MAX_SYMBOLS_PER_ATOMIC_SYSTEM_CLAIM（6）を超えるか本文が 300 字を超えるとき split_required / is_atomic=False / review_required + review_reasons=non_atomic_system_claim にする。根拠 evidence の件数は判定に使わない。"
  landed_in:
    - "src/episteme_graph/agents/claim_object_builder/equation_claim_synthesis.py"
    - "src/tests/agents/claim_object_builder/test_split_points_and_system_atomicity.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail]
    unverified:
      - "再解析での実 run の再演（次の周で確認）"
      - "閾値 6 記号の妥当性（実 run の system 主張の分布での確認）"
related: [IK-0797, IK-0728]
view_of: []
history:
  - date: '2026-10-16'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '原因確定: _make_claim が合成主張に一律 atomicity=atomic / is_atomic=True を付け、system-level の束にも既存の非 atomic 判定が適用されていなかった。'
---

## 課題

症状: synth_claim_0087 は evidence 約 150 件を根拠に持つ連立系全体の主張なのに atomic=True。非 atomic を強い backing に使わない規律（#317）を
すり抜けて、部品の主張として選べる。
原因（仮説）: 合成主張の atomic の印が経路で一律に付き、粒度を見ていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2934）。

## 発見の観点

合成主張の atomic の印を、その根拠の evidence と式の数に突き合わせた（data_inspection）。

## 解決の観点

合成経路に命題の引数の数と本文の長さによる非 atomic 判定を足した（single_point_fix）。

## 一般化

経路で品質の印を一律に付けると、条件を満たさない束も印付きで流れる。辞書の型 completion-defined-by-proxy。
