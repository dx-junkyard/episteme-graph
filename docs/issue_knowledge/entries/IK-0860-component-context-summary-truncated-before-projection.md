---
id: IK-0860
title: "学習者の部品の文脈で、グラフの要約（graph_summary_excerpt）が表示投影の前に 200 字で切られるため、要約が参照するノード ID は「（本文を特定できない要素）」に置き換わって文が読めなくなり、切れ目にかかった ID は数字を失って投影の正規表現を逃れ「theory_op」の断片が学習者に出る"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/display_projection_design.md"
  - "docs/features/component_evidence_redesign.md"
feature_context:
  realizing: "学生が教材の部品チップから部品の文脈を開き、この論文の中での位置づけを読む"
  layers: [shared_infra, frontend_learning_ui, graph_review]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [information]
    governance: [ordering]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    統制軸 ordering: `backend/core/component_context.py`（917）が `str(narrative.get("graph_summary") or "").strip()[:200]` で先に
    切り、`LearnerDisplayRoute`（`core/display_projection.py`）がその後で内部 ID を置き換える。`INTERNAL_ID_TOKEN_RE`（119-）は
    数字を含むトークンだけを ID とみなすので、切れ目で「theory_op_0003」が「theory_op」になると素通りする。処理軸 input_handling:
    narrative の graph_summary は「まず theory_op_0001 が…ついで theory_op_0002 と…」とノード ID で文を組むので、置き換え後は
    「まず （本文を特定できない要素） が…」の文になる（ID を表示名へ写す材料が要約の側に無い）。接続軸 information: ノードの
    表示名（display_label）は同じグラフにあるのに要約の生成・投影へ渡っていない。構造軸 none。
generalization:
  level: general
  general_form: "表示用の遮断（内部 ID の置換）を文字数の切り詰めの後に掛けるため、切れ目で形の崩れた識別子が遮断を逃れ、遮断できたものも置き換え語だけが残って文が読めない"
pattern: gate-position-wrong
discovery:
  perspective: [reproduction, data_inspection]
  note: "学生 st-02 が DCF 仮定の部品の文脈を開くと、グラフの要約に「（本文を特定できない要素）」が 2 度出て途中に theory_op という語が切れて入っていた。応答の graph_summary_excerpt と component_context の切り詰め位置・投影の正規表現を突き合わせた。 run: uxsim/runs/c-r25-doubt-r1/20261022T125655Z-43791（seq10）。頭脳の所見 BRAIN_NOTES_doubt.md 00053。"
resolution:
  perspective: [responsibility_move]
  note: >-
    component_context は graph_summary を同じグラフのノード表示名（main 層は日本語の段名）へ写してから text_excerpt.excerpt で語境界に切る（readable_graph_summary・_load_graph_node_labels）。ID の途中で切れた断片（theory_op）を作らず、置き換え語で文を埋めない。写せない id は残し後段の表示投影が遮断する。
  landed_in:
    - "backend/core/component_context.py"
    - "backend/tests/test_doubt_course_scope_round25.py"
  verification:
    methods: [guardrail]
    unverified:
      - "学習者 DTO 全体に接頭辞だけの断片が出ないことの走査ガードレール（是正案③）は入れていない"
related: []
view_of: []
history: []
---

## 課題

症状: 部品の文脈のグラフの要約に「（本文を特定できない要素）」と切れた「theory_op」が出る。原因: 200 字の切り詰めが表示投影の
前にあり、要約はノード ID で文を組んでいる。

## 発見の観点

ペルソナの通し受講（reproduction）と DTO の読み取り（data_inspection）。

## 解決の観点

解決済み（2026-10-23）。切り詰めと遮断の順序を入れ替え、ID を表示名に写してから返す。

## 一般化

遮断・投影の前に文字数で切ると、切れ目の識別子が遮断を逃れる。確かめ方は、ID が切れ目にかかる長さの要約を作り、
学習者 DTO に接頭辞の断片が出ないことを見ること。
