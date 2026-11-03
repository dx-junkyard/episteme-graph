---
id: IK-1033
title: "記号の吹き出しが m(r) の定義として「where m(r) is the stellar mass encompassing r.」を出し、それに添える式に別の式（境界条件 P(R⋆)=0・AI復元の印付き）を描く — 定義の文と添えた式が同じ式の記録から来ていない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "学生が教材の記号をタップして、論文での定義の文とその式を確かめる"
  layers: [learner_experience_b, concept_registry, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: backend/core/symbol_lookup.py の定義の組み立て（definition["latex"] = chosen["latex"] の付近）が、定義の文を引いた記録と、
    添える式を選ぶ記録を別々に決めている（式は記号の登場する式の候補から選ぶ）。m(r) の定義文は「where m(r) is the stellar mass」の
    主張・本文から、式は同じ論文で m(r)・P を含む別の式の記録から来た。接続軸 target: 定義の文と式が同じ対象（同じ式の記録）を指していない。
    処理軸 logic: 選び方の規則が組の一致を確かめない。確定には symbol_lookup の chosen の決め方と、t11 の論文の記号表で m(r) が
    defined_here の式（eq_2: N_TOV の写像）を持つかを読めばよい。構造軸は low（記号表の表現の不足の可能性を残す）。
generalization:
  level: repo_pattern
  general_form: "説明文と添える具体例を別々の候補集合から選び、両者が同じ対象を指すかを確かめないため、文と例が食い違う"
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [data_inspection]
  note: >-
    審判が学生 st-05 の「P の定義が見つからない」（seq13）を確かめるため、読み取りの API で同じトピック t11 の記号 P / P(r) / m / m(r) / N_c を
    引いたところ、m(r) だけ定義が返り、その latex が P(R_{\star}) = 0 だった。run: uxsim/runs/c-astro-r25-studio-r6/20261027T123926Z-13979 seq13
    （ペルソナが引いたのは P）。
resolution:
  perspective: [single_point_fix, fail_closed]
  note: >-
    symbol_lookup.equation_defines_symbol（決定論）が、式の左辺（最初の = より前）に記号の本体（引数 (r) を外し、\\frac{dm}{dr} の
    d は外して見る）が無いことが確かなときだけ偽を返す。_definition_candidates は左辺に記号の無い式を定義の文に添えない（文は残し、
    式の名前も外す）。文も無い式は候補にしない。= の無い式・記号名の取れない式は判定しない（落とさない側）。
  landed_in:
    - "backend/core/symbol_lookup.py"
    - "backend/tests/test_lecture_studio_round25_r6.py"
  verification:
    methods: [guardrail]
    unverified:
      - "固定コースの m(r) の吹き出しで境界条件 P(R⋆)=0 が添えられなくなるかは実機で見ていない"
      - "記号表の defining_equation_ids に別の式が混ざる上流（symbol_registry）の原因は直していない — 読み時に添えないだけ"
related: [IK-0380, IK-0869, IK-1008]
view_of: []
history: []
---

## 課題

症状: 記号 m(r) の吹き出しで、定義の文と描かれる式が別の式（境界条件）。原因（仮説）: 定義の文と式を別々の候補から選ぶ。

## 発見の観点

学生の疑問の裏を取るために、読み取りの API で同じトピックの記号を並べて引いた（data_inspection）。

## 解決の観点

第 6 波（2026-10-27）で是正。記号を左辺で定める式だけを定義の文に添え、それ以外は文だけ残す（推測で結ばない）。

## 一般化

文と例を別々に選ぶと食い違う。確かめ方: 定義のある記号を引き、添えた式の左辺・記号表にその記号が現れることを見る。

第 7 周の再演（2026-10-28・審判の読み取り）: **GONE**。固定コースの t11 で記号 m(r) の吹き出しが定義の文「where m(r) is the stellar mass encompassing r.」だけを返し、境界条件 P(R⋆)=0 を添えなくなった（`definition` に latex なし）。ペルソナ st-07 が引いたのは P（定義の記述なし・評価外）。run: uxsim/runs/c-astro-r25-studio-r7/20261027T173357Z-61213 seq13（場面）+ 読み取りの GET `/api/learning/courses/6be229e1/symbols/lookup?symbol=m(r)&topic_id=t11`。
