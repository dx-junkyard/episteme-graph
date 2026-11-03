---
id: IK-0819
title: "equation_semantics の生成要約に内部のブロック ID「(tex_b80)」が混入し、二層説明の材料（local_text）を経て学習者・教員向けの説明に流れうる"
status: resolved
recorded_at: 2026-10-17
resolved_at: 2026-10-17
sources:
  - "docs/features/display_projection_design.md"
  - "docs/features/hierarchical_context_explanation_design.md"
feature_context:
  realizing: "式の意味の要約を生成し、二層説明と表示の材料に使う"
  layers: [pipeline_a, hierarchical_explanation]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [review]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: equation_semantics の材料が近傍ブロックを「[tex_b80] …」のようにブロック ID 付きで示すため、LLM が要約の本文にブロック ID を
    書き写す。生成要約は後段の local_text・式の説明に本文として渡り、表示投影の内部 ID の語彙（INTERNAL_ID_TOKEN_RE）が tex_bNN の形を
    遮断するかは未確認。統制軸 review = 内部 ID 遮断の検査が生成文の中の新しい ID の形を覆っているか確かめられていない。接続軸
    information（low）= 内部 ID が表示の材料に入る。処理軸 none / 構造軸 none。確定には INTERNAL_ID_TOKEN_RE が tex_bNN を含むかと、
    二層説明の validator が local_text の内部 ID を検査するかを読む。
generalization:
  level: general
  general_form: "材料に内部 ID を付けて LLM に見せるため、生成文に内部 ID が書き写され、表示の遮断が覆わない形のまま人向けの面に流れうる"
pattern: guardrail-does-not-cover-new-path
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 X0 が contextual_explanation（2610.08751・是正前の run・seq 3294・3297）を読み、eq_tex_b78 の local_text と comp_009__r2 の式要約に「(tex_b80)」が入っていることに気付いた（応答では外した。出所: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md）。"
resolution:
  perspective: [explicit_contract, canonical_source]
  note: "表示投影の統合語彙 INTERNAL_ID_TOKEN_RE は tex_ プレフィックスで tex_b80 /（tex_b80）/ eq_tex_b78 を既に覆っていた（eq_12 / eq_2_7 / プレースホルダの tex_bN は当たらない）ので表示側は回帰テストだけ足した。上流は 3 点: ①A層の正本 src/episteme_graph/agents/internal_id_text.py（find / contains / strip_internal_ids・括弧に単独で入った ID は括弧ごと外す・stdlib のみ）を新設。backend は SA層の推移的純粋性のため A層を module level で import できないので正規表現は共有せず、A層が捕まえる ID は表示側にも必ず当たる（A ⊆ 表示）ことを test_display_projection_guardrails が見本で固定。②equation_semantics の prompt の要約の指示に「block_id / span_id / equation_id 等の内部 ID を summary に書かない」を 1 文、validator scrub_internal_ids_from_summary（agent が restrict_* の隣で呼ぶ）が要約から ID を外し warning + review flag internal_id_in_summary（REVIEW_FLAGS に追加・外した ID は message に列挙 = P4。外して空になるなら元を残す）、_check_record も残存を warning。③contextual_explanation の input_builder が local_text・上下文脈の text・ライブラリ抜粋の散文から ID を外してから切り詰める（element_id は番地なので残す）。"
  landed_in:
    - "src/episteme_graph/agents/internal_id_text.py"
    - "src/episteme_graph/agents/equation_semantics/prompt.py"
    - "src/episteme_graph/agents/equation_semantics/schema.py"
    - "src/episteme_graph/agents/equation_semantics/validator.py"
    - "src/episteme_graph/agents/equation_semantics/agent.py"
    - "src/episteme_graph/agents/contextual_explanation/input_builder.py"
    - "src/tests/agents/equation_semantics/test_internal_id_summary.py"
    - "src/tests/agents/contextual_explanation/test_input_builder.py"
    - "backend/tests/test_display_projection_core.py"
    - "backend/tests/test_display_projection_guardrails.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "seq 3294・3297 の論文（2610.08751）の再解析で要約・local_text から ID が消えることの実測"
      - "既存 run に保存済みの要約（再解析までは旧本文のまま・表示側の遮断が受け持つ）"
related: [IK-0789, IK-0755, IK-0517]
view_of: []
history: []
---

## 課題

症状: eq_tex_b78 の local_text と comp_009__r2 の式要約に内部のブロック ID「(tex_b80)」が入る。二層説明の材料として渡り、学習者・教員向けの説明に流れうる。
原因（仮説）: 材料の近傍ブロックが ID 付きで示され、LLM が要約に書き写す。表示投影がこの形を遮断するかは未確認。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3294・3297。是正前の run）。

## 発見の観点

式の要約と local_text の本文を、表示投影の内部 ID 遮断の対象と突き合わせた（data_inspection）。

## 解決の観点

表示側の語彙は既に tex_bNN を覆っていた。上流で生成要約に ID を書かせない指示を足し、要約と二層説明の材料から A層の正本で ID を外し、外した事実を review flag に残した。A層と表示側の語彙の食い違いはガードレールの見本で固定した。

再解析での観察（reproduction_rerun・頭脳 Y0・再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md（seq 3531 — equation_semantics の指示に「summary に内部 ID を書かない・印字番号か内容で参照」が入っている）。要約・local_text から ID が実際に消えたかは、この run の材料では確かめていない。

## 一般化

材料に内部 ID を見せると生成文に写る。遮断の語彙が新しい形を覆っているかを確かめる。辞書の型 guardrail-does-not-cover-new-path。
