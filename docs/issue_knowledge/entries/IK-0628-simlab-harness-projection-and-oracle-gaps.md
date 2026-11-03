---
id: IK-0628
title: "検証シミュレーションの通し受講ハーネスが画面と違う形で結果を写し（観測の列・改訂案）、審判の判定範囲も実行の単位とずれていたため、画面に無い症状を報告し・画面にある症状を見逃した"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
feature_context:
  realizing: "ペルソナ通し受講で、学習画面と同じものをペルソナと審判に見せて所見を取る"
  layers: [cycle_verification, tests_guardrails]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [contract, target]
    governance: [review]
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
    確認: uxsim/runner/simlab_exec.py の結果の投影は模擬観測の列を value で読んでいたが、画面（simulation-lab.js）は平均 ± ばらつきで描くため、ハーネスだけが空を見せていた。改訂案の投影は画面に無い操作語・部品の枠名を出していた。共有ボタンの確認文は API の経路では見えない。審判の LLM 予算の判定は並列レーンの時間窓で数え、別ペルソナの呼び出しまで含めていた。言語の判定は句点の無い英語の断片を拾わない。接続軸 contract = 画面の描画契約とハーネスの投影の片側だけが変わる。target = 判定の対象範囲（どのペルソナの呼び出しか）がずれる。処理軸 logic。統制軸 review = 検証の手続が見ているものの正しさ。構造軸 none。
generalization:
  level: repo_pattern
  general_form: "検証用の写しが本物の表示と別の規則で値を読み、判定の範囲も検証単位とずれるため、検証が本物に無い症状を報告し本物の症状を見逃す"
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 21 周の run の transcript を画面の描画規則と突き合わせ、投影の列・改訂案の語・予算の数え方が画面・実行単位と食い違っていると分かった。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "投影を画面の描画規則（平均 ± ばらつき・数値の書式・改訂案は理由の文だけ）に揃え、確認文を成功した実行の投影に添え、予算の判定を実行のペルソナの利用者に限定した。言語の判定の限界は SB 固有の規則を足す第 2 波に残す。"
  landed_in:
    - uxsim/runner/simlab_exec.py
    - uxsim/oracles/simulation.py
  verification:
    methods: [guardrail]
    unverified:
      - "修正後の投影での通し受講の再実行（第 21 周の再演はまだ）"
      - "句点の無い英語の断片を拾う言語の判定（第 2 波で規則を足すまで判定の限界として残る）"
      - "教員場面の共有者表示が表示名かログイン名か（製品は表示名を引く。砂場アカウントの表示名がログイン名と同じだけかを未確認）"
related: [IK-0614]
view_of: []
history: []
---

## 課題

症状: ハーネスの投影では模擬観測の列が空、改訂案に操作語が出るなど、画面に無いものをペルソナと審判が見た。LLM 予算の判定が並列の別レーンを数えて偽陽性を出し、言語の判定は英語の断片を見逃した。原因: 投影が画面の描画規則と別の読み方をしており、審判の対象範囲が実行の単位（ペルソナ）ではなく時間窓だった。

## 発見の観点

run の transcript と画面の描画規則を突き合わせ（data_inspection）、ハーネスの再現の過程で食い違いを見つけた（reproduction）。製品側の空表示（IK-0614）と切り分けた。

## 解決の観点

投影は画面と同じ規則で読む 1 箇所に揃え、予算の判定は実行のペルソナの利用者に限定した。言語の判定に断片の規則を足す案は第 2 波（判定の限界として記録）。画面の描画を直接読む案はハーネスの API 経路の設計を変えるので採らない。

## 一般化

検証の写しは本物の表示と同じ契約で読まないと、検証自体が症状を作る・隠す。型は contract-changed-one-side。確かめ方は、ハーネスの投影と画面の描画を同じ実行で並べ、列ごとに同じ値が出ることを見ること。
