---
id: IK-0883
title: "コースビルダーの教材コンテキストが部品の前提・注意・入出力を Python の辞書表記（{'text': …, 'claim_ids': [UUID…], 'equation_ids': ['eq_tex_b46', …]}）のまま並べ、式を内部 ID でしか示さないため、AI は「どの式が主結果を支えるか」に式番号で答えられず、回答本文に学ぶ単位の handle（U141 等）を書いて教員に見せる"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/learning_units_design.md"
feature_context:
  realizing: "教員がコースビルダーに論文のどの節・どの式で章を組むかを相談する"
  layers: [course_builder, learning_units, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording: backend/api/routes/admin.py の _build_material_context（2586〜2592 行）は inputs / outputs / preconditions / cautions を
    str(x) で連結し、dict 要素が Python の repr のまま出る（製品頭脳への要求 uxsim/runs/mailbox/req-03991.json で確認）。接続軸 information:
    式の参照は equation_ids（内部 ID）だけで、印字番号（式 (12)）が材料に載らない。構造軸 representation（medium）: handle（U1…）は
    units を選ばせるための材料上の記号だが、プロンプト（2226 行付近）は「回答本文に handle を書かない」を言わず、admin.js は構成案の
    プレビューでだけ handle を名前に置き換える（5376 行の候補表）ので、本文に書かれた handle はそのまま教員に見える。統制 none。
generalization:
  level: repo_pattern
  general_form: "AI に渡す材料を内部の表記のまま組み立て、人の語彙（式番号）を落とし、材料上の記号が応答本文から人に漏れる"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    教員ペルソナがコースビルダーの初回回答の「U141・U144」を何かと問い、AI が「式は内部の識別子でしか示されず式番号が分からない」と
    書いた。製品頭脳の所見（BRAIN_NOTES_product_P0.md 3978・P1 3991・3993）と要求の材料を読んだ。
    run: uxsim/runs/c-astro-r25-studio-r1/20261022T125145Z-40602（seq5・6）。
resolution:
  perspective: [representation_change, guardrail_fix]
  note: "教材コンテキストの部品の属性を本文（text）だけを「・」で並べる形にし（_material_field_text）、式は採用 run の equation_semantics から印字番号と短い原文で添える（_material_equation_names・内部 ID を名前にしない）。プロンプトに「回答本文では handle を書かず単位の名前で言う・式は印字番号か短い原文で言う・番地を書かない」を足し、置換の後ろ盾として回答本文に残った handle をこのターンの候補表の名前「…」へサーバで置き換える（_replace_unit_handles_in_answer。admin.js は触らない）。"
  landed_in:
    - "backend/api/routes/admin.py"
    - "backend/tests/test_lecture_studio_round25.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "handle の置換を画面ごとにせず、回答本文が作られる 1 箇所（コースビルダーの応答）で行う。"
  verification:
    methods: [guardrail]
    unverified:
      - "実際のコースビルダー応答で handle が名前に置き換わることは LLM を通して見ていない"
related: [IK-0859, IK-0872]
view_of: []
history: []
---

## 課題

症状: コースビルダーの回答に「（U141・U144）」のような記号が出て、教員が意味を尋ねる。AI は式番号で示せないと断る。原因: 材料が
Python の辞書表記と内部 ID で組まれ、印字番号が無く、handle を本文に書かない指示も置き換えも無い。

## 発見の観点

ペルソナの通し受講（reproduction）と製品頭脳への要求の読み（data_inspection）。

## 解決の観点

解決済み（2026-10-23）。材料を人の表記で組み、handle は JSON にだけ書かせる。

## 一般化

LLM の材料を内部表記で組む型。確かめ方は、教材コンテキストを組み立てて repr・内部 ID の正規表現で走査し、式に印字番号が付くことを見ること。
