---
id: IK-0732
title: "付録の式約 38 件が equation_semantics で semantic_status=unknown / can_support_claim=false のまま後段に渡る"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/architecture/knowledge_reproduction_review_2026-09-19.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "論文の式ブロックすべてに意味役割を付け、部品組み立て・導出チェーン・説明の材料にする"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version, information]
    governance: [budget]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説は 2 つで未切り分け。(a) 統制軸 budget: 周回中に EQUATION_SEMANTICS_MAX_EQUATIONS を 96 に絞った運転で、
    付録の式が層化サンプリングの外に落ち、母集合にはあるが意味付けされないまま unknown で後段に渡った。
    (b) 接続軸 version / information: 同一 document の解析 run が並行して走り（IK-0715 系の並行 run）、頭脳が応答した
    式の意味付け（eq_BTcov・eq_tex_b446）が、component_assembly が読む run の artifact に反映されていない。
    いずれの場合も、unknown の式は受け皿に残っている（情報は落ちていない）ので構造軸 none、処理軸 none。
    確定には、当該 run の equation_semantics artifact の coverage（population / processed / truncated）と、
    component_assembly の入力が指す run_id を突き合わせる。
generalization:
  level: repo_pattern
  general_form: "意味付けの母集合と上限・実行の版が後段から見えないため、意味付けされなかった項目と意味付け済みの項目が区別されずに後段へ渡る"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 M0 が component_assembly（seq 2055・2079）の available_equations を読み、付録の共分散・B–T 共分散・検出有意度の式（seq 2031・2037 で応答済みの eq_BTcov・eq_tex_b446 を含む）が role=unknown / confidence 0 のままであることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_M0.md）。同じ材料で eq_gaussian_fog が TeX source_backed なのに must_not_treat_as_source_extracted=true の矛盾も見えた。"
resolution:
  perspective: [pending]
  note: "未解決。まず (a) 上限による打ち切りか (b) 並行 run の版ずれかを coverage と run_id で切り分ける。(a) なら上限運転時も付録の式を層化の 1 層として残すか、打ち切りを coverage と後段の事実文で明示する。(b) なら IK-0715 の並行 run の是正に寄せる。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0715, IK-0722]
view_of: []
history: []
---

## 課題

症状: component_assembly の available_equations 後半の付録の式約 38 件が semantic_status=unknown / can_support_claim=false /
role=unknown / confidence 0。うち eq_BTcov・eq_tex_b446 は同じ周回で頭脳が意味付けに応答済みだった。2 つの別 run（seq 2055・2079）で同じ。
原因（仮説）: 周回中の EQUATION_SEMANTICS_MAX_EQUATIONS=96 による打ち切り、または並行 run の版ずれ。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_M0.md（seq 2031・2037・2055・2079）。

## 発見の観点

意味付けに応答した式と、後段の入力に現れた式の意味役割を突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

意味付けした結果が後段に届かない、または意味付けしなかった事実が後段から見えない。辞書の型 available-but-unwired
（打ち切りが原因と確定したら information-dropped-as-unrepresentable の側へ寄せ直す）。
