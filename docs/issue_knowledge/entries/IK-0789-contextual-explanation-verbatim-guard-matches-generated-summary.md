---
id: IK-0789
title: "二層説明と discuss 開幕の材料で、式の local_text が前段 equation_semantics の生成要約で、逐語の evidence_quote 検査が論文原文ではなく生成文への一致になる"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/features/hierarchical_context_explanation_design.md"
  - "docs/features/discuss_opening_authoring_design.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "式・部品の二層説明と discuss 開幕の議論のきっかけを、論文原文の逐語引用で裏付けて生成する"
  layers: [hierarchical_explanation, discuss, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 式要素の local_text（説明の材料の本文）が、前段の equation_semantics で LLM が書いた英語の summary から組まれており、
    contextual_explanation と discuss_opening の validator はその local_text・素材に対して evidence_quote の逐語包含を検査する。
    捏造ガード（verbatim 検査）は「論文に書いてある」ことの代理として働くはずが、照合先が生成文なので生成文の言い換えを
    逐語の根拠として通す。構造軸 representation = 材料の本文に、論文原文か生成文かの出所を表す欄が無い。接続軸 meaning =
    前段の生成要約が後段で「原文」として読まれる（逐語の意味がずれる）。処理軸 none / 統制軸 none。確定には
    contextual_explanation の input_builder が式要素の local_text を何から組むか（equation_semantics の summary か、
    式ブロック近傍の本文か）と、discuss_opening の素材の reason の出所を読む。
generalization:
  level: general
  general_form: "前段の生成文を後段の材料の本文に入れたまま逐語検査を掛けるため、捏造ガードが原文ではなく生成文に対して効き、言い換えが根拠として通る"
pattern: projection-mistaken-for-source
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 T1 が contextual_explanation（seq 2944・2947・2950・2953）と discuss_opening（seq 2959）の材料を読み、local_text が equation_semantics の英語 summary で、evidence_quote の逐語源が生成文になっていることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md）。"
resolution:
  perspective: [pending]
  note: "未解決。式要素の材料本文は式ブロックと近傍の本文（論文原文）から組み、前段の生成要約は出所を明示した別欄（『AI の要約』）として渡す。逐語検査は原文の欄に対してだけ掛ける案。"
  landed_in:
    - "docs/features/hierarchical_context_explanation_design.md"
related: [IK-0677, IK-0609, IK-0717, IK-0752]
view_of: []
history: []
---

## 課題

症状: contextual_explanation の式 8 件で、local_text の要約文が前段 equation_semantics で LLM が書いた英語 summary であり、
evidence_quote の「逐語」はその生成文への一致になっている。discuss_opening（seq 2959）でも author_choices の reason が同じ生成要約で、
evidence_quote が原文ではなく生成要約に一致する。捏造ガードは生成文に対してしか効いていない。
原因（仮説）: 材料の本文に原文と生成文の出所の区別が無く、逐語検査の照合先が生成文になっている。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2944・2947・2950・2953・2959）。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2949 — local_text の先頭が equation_semantics の LLM 生成の英文 summary で、説明が要約の言い換えに寄る。原文の引用は記号定義の lower_context だけ）。

追加の出所（頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2867 → 2951 — 頭脳が 2867 で書いた英語 summary が eq_tex_b106 の local_text としてそのまま下流の入力に戻り、曖昧な表現まで伝播した）。

追加の出所（頭脳 X1・是正前の run（seq 3289〜3310・2026-10-17 03:00〜03:11））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3292 — local_text の先頭が equation_semantics の英語要約で、evidence_quote が頭脳生成文の引用になる）。

追加の出所（頭脳 X2・是正前の run（seq 3290〜3314））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3293 — local_text の 1 行目が equation_semantics の英語要約で、原文は lower_context の記号行にだけある）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z0/Z1）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3654 — 二層説明の local_text が equation_semantics の要約（前段で頭脳が書いた英語）そのもので、LLM の出力が次の LLM の唯一の材料になり、原文は記号定義の断片だけ）、uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3649 — evidence_quote が原文でなく AI 要約の逐語になる）。

## 発見の観点

説明材料の local_text と evidence_quote の照合先を、前段 equation_semantics の出力と論文の式ブロック近傍の本文に突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

生成文を材料の本文にしたまま逐語検査を掛けると、ガードが原文でなく生成文を守る。辞書の型 projection-mistaken-for-source（生成した写しを原文として扱う）。
