---
id: IK-0615
title: "コード生成のプロンプトに渡した内部の語（role=output など）を LLM が事実文に写し、事実文の整形が衛生処理だけのため学習者に内部語が出る"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "理論プログラムの生成時に、学習者が読む事実文（何を実装したか・何が足りないか）を添える"
  layers: [simulation_bridge_sb, frontend_learning_ui, shared_infra]
classification:
  axes:
    processing: [input_handling]
    structure: [responsibility]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 第 21 周の時点の backend/core/simulation_bridge/codegen.py は資料に role=input / role=output の語を書いて LLM に渡し、生成された facts は _normalize_facts で制御文字の除去と重複除去だけを通った。表示投影の内部 ID の語彙は role= の形を遮断しない。接続軸 meaning = プロンプト用の語彙が利用者向けの文に移る。構造軸 responsibility = 内部語の遮断を誰が持つか（生成の後処理か表示投影か）が決まっていない。処理軸 input_handling = LLM 出力の整形の不足。統制軸 none。
generalization:
  level: general
  general_form: "生成モデルに渡した内部の語彙が生成文を通って利用者向けの文に移り、出口の遮断がその語彙を覆っていない"
pattern: guardrail-does-not-cover-new-path
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、生成の事実文にほぼ全場面で role=output / role=member が出た。"
resolution:
  perspective: [single_point_fix, explicit_contract]
  note: "codegen._normalize_facts（_humanize_fact）が role=input|output|member|foundation を日本語（入力の式 / 出力の式 / 手順の中の式 / 共有の基礎の式）に置換し、置換し切れない role= を含む文は落とす。プロンプトに「facts は学習者が読む文。内部の語・英語の型名を書かない」の 1 行。"
  landed_in:
    - backend/core/simulation_bridge/codegen.py
    - backend/tests/test_simulation_bridge_codegen.py
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail, reproduction_rerun, docker_e2e]
    unverified:
      - "生成の言語（説明は日本語のみで、英語話者への説明は無い）"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "生成物の形の派生形（生成後・保存前の 1 点でサーバが整える）を採った。プロンプトの指示は従で、正しさは置換側が持つ。"
related: [IK-0627]
view_of: []
history: []
---

## 課題

症状: 生成された理論プログラムの説明文に role=output などの内部語が出る。原因: プロンプトの資料にこの語彙を書いて渡しており、LLM が事実文にそのまま写す。事実文の整形は衛生処理だけで、表示投影の遮断語彙もこの形を覆わない。

## 発見の観点

ペルソナの通し受講（reproduction）で観測し、生成の事実文の整形経路を読んだ。

## 解決の観点

生成後・保存前の 1 点で内部語を置換し、置き換えられない文は落とす。プロンプトにも指示を 1 行足した。再演で role= の内部語は消えた（GONE）。

## 一般化

プロンプトの語彙は生成文に漏れる。出口の遮断は既知の ID 形だけを覆いがちで、プロンプト語彙の新しい形は通る。型は guardrail-does-not-cover-new-path。確かめ方は、内部語を含む資料で生成し、出口の文に内部語が残らないことを生成結果の全件で見ること。
