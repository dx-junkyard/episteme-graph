---
id: IK-1142
title: "選択箇所について尋ねても、選んだ一節に出てくる式（定義式）の LaTeX が材料に無く、出典の抜粋に式が無いと AI は式を確かめられない"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/assistant_screen_adapter_design.md"
feature_context:
  realizing: "学習者が教材の一節を選んで尋ねたとき、その一節の式を AI が原文の式で確かめる"
  layers: [screen_adapter_sa, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    選択ブロックは逐語テキストだけで、KaTeX の選択は記号が崩れて写る。表示中の教材の式の記録（content_blocks の式）は文脈の事実行（印字番号・復元の印）にしか使われず、式の本文は選択に結ばれていなかった。接続軸 information。
generalization:
  level: repo_pattern
  general_form: "画面で選んだ範囲に含まれる構造（式）の記録がサーバにあるのに、選択の材料には逐語だけが渡る"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: >-
    製品頭脳 A 4313: 選択箇所の要の L_TOV の定義式が出典の抜粋に無く、AI は「抜粋に無く未確認」と答えた。run uxsim/runs/c-r25-share-r10（st-05 選択質問 2）。
resolution:
  perspective: [carry_through]
  note: >-
    第 25 周 第 10 波（2026-10-31）: selection.py の selection_formula_lines が、教材の式の記録のうち左辺の記号が選択テキストに現れるもの（英数字キー・2 文字以上・最大 3 件・1 行 200 字）を選び、render_selection_block が逐語の後に添える（逐語の 600 字上限は不変）。route は _topic_selection_formulas で印を付け直した式を渡す。
  landed_in:
    - "backend/core/assistant_context/selection.py"
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave10_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "左辺に記号の無い式・選択に記号が写らない式は添わない"
      - "実画面の KaTeX 選択の写り方での照合（再演）"
related: [IK-0577]
view_of: []
history: []
---

## 課題

選択箇所について尋ねても、選んだ一節に出てくる式（定義式）の LaTeX が材料に無く、出典の抜粋に式が無いと AI は式を確かめられない。

## 発見の観点

製品頭脳 A 4313: 選択箇所の要の L_TOV の定義式が出典の抜粋に無く、AI は「抜粋に無く未確認」と答えた。run uxsim/runs/c-r25-share-r10（st-05 選択質問 2）。

## 解決の観点

selection.py の selection_formula_lines が、教材の式の記録のうち左辺の記号が選択テキストに現れるもの（英数字キー・2 文字以上・最大 3 件・1 行 200 字）を選び、render_selection_block が逐語の後に添える（逐語の 600 字上限は不変）。route は _topic_selection_formulas で印を付け直した式を渡す。

## 一般化

画面で選んだ範囲に含まれる構造（式）の記録がサーバにあるのに、選択の材料には逐語だけが渡る。型 available-but-unwired。
