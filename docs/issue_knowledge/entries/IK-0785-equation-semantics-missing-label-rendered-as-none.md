---
id: IK-0785
title: "ラベルの無い式で equation_semantics のプロンプトに「label: None」と Python の None が文字列のまま載り、LLM がラベルらしき値として読む"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "式ごとの文脈（ID・ラベル・抽出の状態）をプロンプトに整形して意味役割の判定に渡す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説（コードの読み合わせで確度は高い）: equation_semantics/prompt.py は section_title・backbone_block_type は値があるときだけ載せるのに、
    label は無条件に f"label: {llm_input.label}" で整形するため、ラベル無し（None）の式で「label: None」になる。処理軸 input_handling =
    欠損値の整形の不備。接続軸 contract = 「値が無い」ことの表し方がプロンプトの契約として決まっておらず、欠損が文字列の値に化ける。
    構造軸 none。統制軸 none。確定には、ラベル無しの式（eq_tex_b41 等）の要求本文で「label: None」を確かめる。
generalization:
  level: general
  general_form: "任意項目の欠損をそのまま文字列化して指示文に埋め、欠損がその言語の空値の綴りという値として生成側に渡る"
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 S0 が 2609.35757（TeX 経路）の equation_semantics を読み、ラベル無しの式で label が文字列「None」として渡ることを見た（seq 2847 ほか後半の equation_semantics 20 件）。出所: uxsim/runs/radar_loop/BRAIN_NOTES_S0.md。"
resolution:
  perspective: [single_point_fix]
  note: "ラベルの無い式は input_builder が空として渡し、prompt は空のラベルを「None」と文字列化しない。rhetorical_role の preceding_equation も同じ規則で渡す。"
  landed_in:
    - "src/episteme_graph/agents/equation_semantics/input_builder.py"
    - "src/episteme_graph/agents/equation_semantics/prompt.py"
    - "src/episteme_graph/agents/rhetorical_role/input_builder.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実 LLM での 2609.35757 の再演（ラベル無しの式の要求本文の実物）"
related: [IK-0757, IK-0766]
view_of: []
history: []
---

## 課題

症状: equation_semantics の入力で、ラベル無しの式に「label: None」が載る。LLM はこれを値として扱い得る（ラベル由来の ID 推測の誘因にもなる）。

原因（仮説）: プロンプトの label 行だけが欠損の有無を見ずに整形される。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_S0.md（seq 2847 ほか）。

## 発見の観点

要求本文の文脈欄を実データで読んだ（data_inspection）。

## 解決の観点

欠損のラベルは空で渡し、文字列「None」を作らない整形にした（single_point_fix）。新しく足した preceding_equation にも同じ規則を当てた。

## 一般化

任意項目を無条件に文字列化すると、欠損が値に化けて生成側を誤誘導する。確かめ方は、ラベル無しの式の要求本文に「None」が現れないことを見る。
辞書の型 unchecked-type-contract-at-boundary。
