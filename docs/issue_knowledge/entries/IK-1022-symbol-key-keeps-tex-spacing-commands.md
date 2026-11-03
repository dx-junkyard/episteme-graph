---
id: IK-1022
title: "記号の照合キー（symbol_key）が TeX の間隔命令（\\! / \\, など）を残すため、本文・主張に $\\mathbf{F}_{\\!\\mathrm{EOS}}$ と書かれた記号が画面の送る F_EOS と一致せず、吹き出しが「この論文には、この記号の登録がありません」の 1 行だけになり使用例も出ない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "学生が教材の式の記号をタップし、その記号の定義・使われ方を読む"
  layers: [concept_registry, learner_experience_b]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
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
    処理軸 input_handling: core/symbol_lookup.py symbol_key は書体命令（mathbf 等）と英字の命令（\[A-Za-z]+）を処理するが、英字でない間隔命令（\! \, \; \:）を残す。実測: symbol_key('\mathbf{F}_{\!\mathrm{EOS}}') = 'F_\!EOS'、symbol_key('F_EOS') = 'F_EOS'、_mentions_symbol(主張の文, 'F_EOS') = False（backend/.venv で関数を直接呼んだ）。画面とハーネスは記号を基底 + _ + 添字の字（F_EOS）で送るので、主張の言及・式の左辺のどちらにも当たらない（st-01 seq9・審判 A「教材に定義の式が見えている記号で定義が見つからない」）。構造軸・接続軸 none: 照合の入力の正規化だけの問題で、段階間の受け渡しは正しい。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "記号の照合キーの正規化が一部の表記ゆれ（見た目に影響しない組版命令）を落とさず、同じ記号が別のキーになる"
pattern: substring-match-false-positive
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    st-01 が F_EOS をタップして 1 行の吹き出しだけが返った（uxsim/runs/c-astro-r25-share-r6/20261027T123326Z-10141 seq9）。同じ論文の主張 The EOS feature vector is $\mathbf{F}_{\!\mathrm{EOS}}=…$ が st-08 の部品の文脈に出ていたので、symbol_key と _mentions_symbol を手元で呼んで不一致を確かめた。
resolution:
  perspective: [canonical_source]
  note: >-
    記号の照合キーから TeX の間隔命令（\! \, \; \: \> \ と \quad \qquad ほか）を落とす。正本は core/theory_modules/schema.py の strip_tex_spacing で、symbol_match_key / clean_symbol_name と core/symbol_lookup.symbol_key の両方がそれを通す（語の命令 \quadrant のような前方一致は食わない）。テストで F_{\!EOS} と F_EOS のキー一致を固定した。
  landed_in:
    - "backend/core/theory_modules/schema.py"
    - "backend/core/symbol_lookup.py"
    - "backend/tests/test_share_round25_r6_fixes.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "照合キーの正規化を 2 本の照合キーで別々に足さず、theory_modules の 1 関数に集めた。"
  verification:
    methods: [guardrail]
    unverified:
      - "left / right などサイズ命令の正規化は対象にしていない"
      - "砂場の st-01 で F_EOS の吹き出しに定義と使用例が出ることの再演"
related: [IK-0983, IK-0869, IK-0893]
view_of: []
history: []
---

## 課題

症状: 教材の式の左辺に立つ記号 F_EOS をタップすると「この論文には、この記号の登録がありません。」の 1 行だけで、同じ論文の主張にある定義の文（The EOS feature vector is …）も使用例も出ない。第 5 周（再解析前）は英語の切れ端の定義が出ていた。

原因: 照合キーの正規化が TeX の間隔命令 \! を残す。原文の記号は F_{\!EOS} の形でキー F_\!EOS、画面が送る記号は F_EOS で、完全一致の照合も言及の照合も外れる。

## 発見の観点

再現（reproduction）: 吹き出しの応答（available:false・mentions なし）。データの確認（data_inspection）: 主張の本文とキー関数の出力を手元で突き合わせた。

## 解決の観点

是正済み（第 25 周・面 A 第 6 波・2026-10-27）。

旧案: symbol_key で非英字の間隔命令（\! \, \; \: \␣）と \quad / \qquad を空に落とす（見た目にしか効かない命令）。照合のテストに F_{\!EOS} と F_EOS の一致を足す。

## 一般化

表記の照合キーを作るとき、意味を持たない組版命令（間隔・サイズ）を正規化から漏らすと、同じ記号が書き手の癖で別物になる。型 substring-match-false-positive の逆側（一致すべきものが一致しない照合の不備）。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: GONE。st-04 の F_EOS（画面の F_EOS と LaTeX の \mathbf{F}_{\!\mathrm{EOS}} の両方）で「この式の中で定義されています」+ 英語原文の定義と式が出た（uxsim/runs/c-astro-r25-share-r7/20261027T172824Z-59013 seq8・9 — 応答の symbol は F_EOS・definition あり）。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

GONE（再確認）。\mathcal{L}_TOV と L_TOV が同じ吹き出し（uxsim/runs/c-astro-r25-share-r8/20261027T224425Z-99386 seq9）。
