---
id: IK-0571
title: "A層由来の英語の生成文（⚓ 本文・summary・teaching_takeaway・図キャプション）が日本語の学習画面にそのまま出る"
status: open
recorded_at: 2026-09-30
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [pipeline_a, learner_experience_b]
classification:
  axes:
    processing: [unknown]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: low
    structure: low
    connection: low
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 構造課題。表示側で隠すか A層で日本語を生成するか、翻訳の置き場が未定。 原因の性質で座標を置いた（症状の場所ではなく）。 未解決のため座標は暫定。
generalization:
  level: general
  general_form: "生成物の言語を表示先の言語と照合する段が無い"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [pending]
  note: >-
    一部着手（2026-10-01）。run options `language` を paper_skeleton / thesis_reconstruction /
    narrative_annotator / component_assembly の生成文に運んだ（未指定なら prompt 不変・LLM 回数不変）。
    component の label / summary と equation_semantics の summary は、決定論の後段が英語キーワードで
    構造を導くため対象外（generation_language_design.md §2 GL4）。既存教材は再解析が要り、砂場での
    確認は未実施のため open のまま。
    追補（2026-10-02）: component の summary を英語キーワードで読んで構造を導いていた後段 3 箇所
    （internal_flow の relation / operation の補完 / support_role の補完）を、同じコールで宣言される
    語彙値（operation / primary_operation / responsibility_type）を先に読む形に寄せ、summary を
    言語指定の対象に加えた（label は stable_key と名前照合の材料なので不変）。図表の teaching_takeaway
    （決定論テンプレート）も言語に合わせた。式の summary は導出 step の operation を決める後段に
    代わる語彙値が無いため対象外のまま（generation_language_design.md §6）。
  landed_in:
    - src/episteme_graph/agents/generation_language.py
    - backend/core/document_pipeline/orchestrator.py
    - docs/features/generation_language_design.md
    - src/episteme_graph/agents/component_assembly/enrichment.py
    - src/episteme_graph/agents/component_assembly/component_refiner.py
    - src/episteme_graph/agents/component_assembly/claim_centered_planner.py
    - src/episteme_graph/agents/figure_table_semantics/agent.py
    - src/tests/agents/test_generation_language_structure.py
related: [IK-0546, IK-0540]
view_of: []
history:
  - date: '2026-10-01'
    field: resolution.landed_in
    from: "[]"
    to: >-
      src/episteme_graph/agents/generation_language.py / {paper_skeleton,thesis_reconstruction,
      narrative_annotator,component_assembly}/{prompt,agent}.py / orchestrator._run_language・
      _language_run_kwargs
    reason: >-
      生成言語を 4 agent に運んだ。component の summary / label・式の summary は構造を導く後段が読むため
      未対応で、砂場の再解析で確かめるまで open のまま
  - date: '2026-10-02'
    field: resolution.landed_in
    from: "generation_language.py / orchestrator / generation_language_design.md"
    to: >-
      + component_assembly/{enrichment,component_refiner,claim_centered_planner}.py（語彙値を先に読む）/
      figure_table_semantics/agent.py（teaching_takeaway の言語）/ test_generation_language_structure.py
    reason: >-
      後段を語彙値に寄せて component の summary を言語指定の対象に加えた。式の summary は置き換える
      語彙値が無く対象外のまま。砂場の再解析は未実施なので open のまま
---

## 課題

A層由来の英語の生成文（⚓ 本文・summary・teaching_takeaway・図キャプション）が日本語の学習画面にそのまま出る。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

未解決。構造課題。表示側で隠すか A層で日本語を生成するか、翻訳の置き場が未定。

2026-10-02: 後段の依存表を作り（`generation_language_design.md` §6.1）、語彙値に置き換えられる 3 箇所を置き換えて component の summary を対象に加えた。置き換えられないのは式の summary（導出 step の operation を決める）と概念照合（意味の照合）。

2026-10-01: A層で生成言語を指定する側を選び、run options `language` を 4 agent（paper_skeleton / thesis_reconstruction / narrative_annotator / component_assembly）の生成文へ運んだ。component の summary / label と式の summary は後段の英語キーワード照合が構造を決めるため対象外で、残る（`generation_language_design.md` §5）。

## 一般化

原因は仮説（未確定）。
生成物の言語を表示先の言語と照合する段が無い。

## 第 3 周の再演（第 25 周・面 A 第 3 周、2026-10-23）

残存。学生の部品の文脈の「この論文での位置づけ」が英語の定型文（「Connects theory quantities to the observables the thesis uses.」）、
主張の excerpt が英語、記号 F_EOS の定義が英語の切れ端（「the EOS feature vector ... to stellar profiles」）。
run: uxsim/runs/c-astro-r25-share-r3/20261023T033153Z-19784 seq9・seq11。
