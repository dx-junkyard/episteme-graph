---
id: IK-0625
title: "Copilot の操作 KB（検証シミュレーションの節）が学習者主語化（v1.1）の前の文のままで、学習者には見えない・確定は教員だけと答え、学習者が作って共有した実行の扱いを説明しない"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "教員が学習者の共有した実行について、扱い方を Copilot に尋ねる"
  layers: [admin_copilot, simulation_bridge_sb, docs]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [none]
    governance: [completion]
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
    確認: docs/admin_operations/materials.md の simulation-bridge 節は「学習者にはこの画面も結果も表示されません」「確定は教員だけが行えます」と書き、学習者が作って試し共有する v1.1（simulation_bridge_design.md §14・migration 089）を反映していない。Copilot はこの節を根拠に答え、確定場所を内部名で答えた。統制軸 completion = 機能の改訂で KB の節を更新する完了条件が満たされていない。構造軸 aggregation = 操作 KB と設計書・マニュアルが別の正本として並走する。処理軸 none。接続軸 none。
generalization:
  level: repo_pattern
  general_form: "機能の主体を変える改訂の後、案内用の知識源だけが改訂前の記述のまま残り、案内が現在の挙動と逆のことを言う"
pattern: doc-drifts-from-code
discovery:
  perspective: [reproduction, doc_code_diff]
  note: "第 21 周の教員場面（t-sim-review-shared）で、共有実行を見ているペルソナに Copilot が「学習者にはこの画面も結果も表示されません」と答えた。KB の節と設計書 §14 を突き合わせた。"
resolution:
  perspective: [doc_correction]
  note: "admin_operations/materials.md の simulation-bridge 節と capability materials.simulation_bridge の説明文を v1.1（学習者が作って試す・共有された実行を教員が見る）に直し、「学習者にはこの画面も結果も表示されません」を削除した。内部名 doubt-atlas は書かない。"
  landed_in:
    - docs/admin_operations/materials.md
    - backend/core/admin_assistant/capabilities.py
    - docs/manual/teacher/27-admin-simulation-bridge.md
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail]
    unverified:
      - "Copilot の実応答での確認（教員の再演では台帳候補の問いが道案内に振られ、説明が返らなかった）"
      - "capability に台帳候補の確定口が無い"
related: [IK-0623]
view_of: []
history: []
---

## 課題

症状: 学習者が共有した実行を見ている教員に、Copilot が学習者には表示されないと答え、学生の試行に承認ゲートが無いことと食い違う「確定は教員だけ」の説明をする。原因: 操作 KB の節が v1 の記述のまま更新されていない。

## 発見の観点

ペルソナの通し受講（reproduction）で答えの食い違いに気づき、KB の節と設計書を突き合わせた（doc_code_diff）。

## 解決の観点

KB と capability の説明を画面に合わせて直した。教員の再演で v1 の誤記述は出なかったが、問いが道案内に振られて説明自体が返っておらず、実応答での確認はできていない。

## 一般化

主体の変わる改訂では、案内の知識源が取り残されやすい。型は doc-drifts-from-code。確かめ方は、改訂の設計書が名指す不変条項の文を KB の節と機械照合し、矛盾する記述が残らないことを見ること。
