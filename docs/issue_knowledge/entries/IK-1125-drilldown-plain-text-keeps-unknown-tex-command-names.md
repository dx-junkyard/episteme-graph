---
id: IK-1125
title: "学習チャットのドリルダウンのボタンの文字が「構造ネットワークの入力 F_\\!EOS の選び方について詳しく聞く」— 平文化（_latex_to_plain）は既知の制御綴りだけを訳し、未知の制御綴りは名前だけ残し（\\hat\\varepsilon_c → hatε_c、\\mathcal{N} → mathcalN）、記号でない制御綴り（\\! の細い空白）はそのまま残す"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "学生が回答の続きを、読めるボタンの文字で 1 タップで尋ねる"
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 input_handling: core/learning_support_agent.py _latex_to_plain は _LATEX_CMD_RE（英字の制御綴り）だけを置き換え、_LATEX_PLAIN に無い名前は
    名前をそのまま返し（hat・mathcal・mathbf の一部）、英字でない制御綴り（\!・\,・\;）は対象外。関数を直接呼んで
    「$\mathbf{F}_{\!\mathrm{EOS}}$」→「F_\!EOS」、「$\hat\varepsilon_c$ と $\mathcal{N}_\mathrm{TOV}$」→「hatε_c と mathcalN_TOV」を確かめた。
generalization:
  level: repo_pattern
  general_form: "表示用の平文化が既知の表だけを訳し、表に無い記法を名前の断片のまま残す"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 25 周 面 A 第 10 周 st-05 (d) の選択質問 3 の回答のボタン（uxsim/runs/c-astro-r25-share-r10/20261031T054227Z-72202 seq13）。頭脳ノート
    「ドリルダウンのボタン名に生 TeX『F_\!EOS』」。IK-0451 / IK-0478 の是正（区切りの除去・既知の制御綴りの平文化）の先に残った分。
resolution:
  perspective: [single_point_fix]
  note: >-
    _latex_to_plain の前に英字でない間隔の制御綴り（\! は詰める・\, \; \: \> は空白）を処理し、修飾・書体の綴り（hat・bar・vec・mathcal・mathbb・boldsymbol・operatorname 等）を _LATEX_DROP に足して引数の記号だけを残す。表に無い他の綴りの扱い（名前を残す）は変えていない。
  landed_in:
    - "backend/core/learning_support_agent.py"
    - "backend/tests/test_share_round25_r10_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "表に無い制御綴りが残るボタンの文字（名前を残す既存の扱い）"
related: [IK-0451, IK-0478]
view_of: []
history: []
---

## 課題

症状: ボタンの文字に「F_\!EOS」。他の記号（\hat・\mathcal）でも名前の断片（hatε・mathcalN）が残る。

原因: 平文化は既知の表だけを訳し、未知の制御綴りは名前を残し、英字でない制御綴りは触らない。

## 発見の観点

再現: st-05 の (d)。経路の追跡: extract_inline_actions → _strip_math_delimiters → _latex_to_plain。関数の直接呼び出しで再現。

## 解決の観点

第 25 周 第 10 波で是正（resolution.note）。

## 一般化

表で訳す平文化は、表に無い入力の扱い（落とすか・ボタンにしないか）を決めておく。型 wording-mismatch。確かめ方は、\hat・\mathcal・\mathbf・\! を含むボタンの文字を関数に通すこと。
