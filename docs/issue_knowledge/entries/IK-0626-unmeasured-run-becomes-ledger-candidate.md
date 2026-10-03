---
id: IK-0626
title: "共有した実行は比較の判定を問わず台帳の候補になるため、何も測れていない（未測定）実行からも候補が立ち、教員に空の根拠が確定の対象として届く"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "学習者の共有した実行を、主張の検証状態の候補として台帳に記帳する"
  layers: [simulation_bridge_sb, doubt_d]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [condition]
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
    確認: 第 21 周の時点の backend/core/simulation_bridge/service.py の ledger_candidates_for_run は agreement_level を候補に写すだけで、未測定でも候補を作る。教員場面では一致度・分離・記載値の比較がすべて未測定の共有実行に確定の操作が並んだ。統制軸 completion = 「共有した」を「台帳に載せる価値がある結果」の代わりに使う。接続軸 condition = 比較が成立したかという条件が記帳に伝わらない。処理軸 logic。構造軸 none。
generalization:
  level: repo_pattern
  general_form: "記録の可否を利用者の操作（共有）だけで決め、記録に足る中身があるかを確かめないため、空の結果が他の人の判断の対象として届く"
pattern: completion-defined-by-proxy
discovery:
  perspective: [reproduction]
  note: "第 21 周の教員場面（t-sim-review-shared）で、未測定の実行に確定の操作が並び、ペルソナが確定してよいか Copilot に尋ねた。"
resolution:
  perspective: [fail_closed, explicit_contract]
  note: "比較が 3 軸とも未測定の run は、共有はされるが台帳の候補には記帳しない（service.is_unmeasured_run が判定し、share 応答に FACT_SHARED_UNMEASURED）。SB13 の読み替えとして設計書 §12.4 に記録した（確定が教員にあることは不変）。"
  landed_in:
    - backend/core/simulation_bridge/service.py
    - backend/core/simulation_bridge/schema.py
    - backend/tests/test_simulation_bridge_service.py
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail, reproduction_rerun, docker_e2e]
    unverified:
      - "教員が未測定の実行でも候補に残したい場合の扱い"
related: [IK-0606, IK-0623, IK-0618]
view_of: []
history: []
---

## 課題

症状: 何も測れていない実行が台帳の候補として教員に届く。原因: 共有の操作が候補の記帳を直接起こし、比較が成立したかを見ない。

## 発見の観点

ペルソナの通し受講（reproduction）で教員が疑問を持った場面から、候補記帳の条件を読んだ。

## 解決の観点

未測定の run を候補にしない弁を置き、共有の応答でその事実を返す。再演で未測定の共有が台帳の候補にならず、事実文が出たことを見た（GONE）。

## 一般化

代理の操作で記録の可否を決めると、中身の無い記録が人の判断を消費する。型は completion-defined-by-proxy。確かめ方は、未測定の実行を共有し、候補が立たないか、立っても未測定と明記されることを見ること。
