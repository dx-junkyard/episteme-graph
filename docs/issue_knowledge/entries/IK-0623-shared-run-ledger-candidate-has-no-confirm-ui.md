---
id: IK-0623
title: "学習者が共有した実行から立った台帳の候補を確定・見送る API はあるが、教員の画面と操作 KB にその導線が無く、教員は候補に到達できない"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "教員が学習者の共有した実行を読み、台帳の候補を確定するか見送るかを決める"
  layers: [simulation_bridge_sb, doubt_d, frontend_admin_ui, admin_copilot]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [information]
    governance: [assignment]
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
    確認: 確定・見送りは backend/api/routes/doubt.py の simulation-candidates の confirm / dismiss にあるが、frontend/public/js/admin-simulation-bridge.js と doubt-atlas.js に呼び出しが無く、共有実行の詳細にも対象主張と候補文が出ない。Copilot の capability にも候補の確定口が無く、確定場所を内部名で答えた。構造軸 responsibility = 確定の装置と導線の置き場が分かれている。接続軸 information = 候補の中身が教員の画面に届かない。統制軸 assignment = 人が確定すべき候補に人が到達できない。処理軸 none。
generalization:
  level: repo_pattern
  general_form: "確定の装置は作ったが、確定すべき人の画面と案内から装置への導線が無く、候補が人に届かない"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, trace_walk]
  note: "第 21 周の教員場面（t-sim-review-shared）で、共有実行の詳細から確定の操作を探したペルソナが見つけられず Copilot に尋ねた。フロントの呼び出しを辿ると確定 API への経路が無かった。"
resolution:
  perspective: [carry_through, state_transition]
  note: "管理画面の区画 4 に「台帳の候補（学習者が共有した実行）」を置き、主張ごとに本文・候補の statement と facts・確定する／見送る（理由必須）を出す。候補は台帳 GET の simulation_candidates から run_id 一致で引き、判断は既存の confirm / dismiss API を呼ぶ。dismiss は body の reason を候補に残す（dismiss_reason）。アンカー simulation.ledger-candidate-decide とマニュアル teacher/27 の節を揃えた。"
  landed_in:
    - frontend/public/js/admin-simulation-bridge.js
    - backend/api/routes/doubt.py
    - backend/core/help_kb/admin_ui_anchors.py
    - docs/manual/teacher/27-admin-simulation-bridge.md
    - backend/tests/test_simulation_bridge_ui_static.py
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail, docker_e2e]
    unverified:
      - "候補がある実行での確定・見送りの操作（後の教員再演では候補なしの事実文までを見た）"
      - "教員の再演での確認は進行中"
related: [IK-0625, IK-0626]
view_of: []
history: []
---

## 課題

症状: 教員は学生の共有実行を一覧で見られるが、それがどの主張の台帳候補で、どこで確定・見送れるかが画面から分からない。原因: 確定・見送りは API だけで、教員の画面にも Copilot の操作 KB にも導線が無い。

## 発見の観点

ペルソナの通し受講（reproduction）で教員の操作が止まったことから、フロントの呼び出しを辿った（trace_walk）。

## 解決の観点

既存の確定 API に至る画面の導線を足し、見送りの理由を候補に残すようにした。2 回目の教員再演で区画と候補なしの理由の文が出たことを見た。候補ありの操作は未確認。

## 一般化

学習者主語化のように主体が変わる改訂では、反対側の人の導線が後回しになる。型は last-mile-missing。確かめ方は、教員の操作列を共有実行の一覧から確定まで実際に歩き、既存 API が呼ばれることを見ること。
