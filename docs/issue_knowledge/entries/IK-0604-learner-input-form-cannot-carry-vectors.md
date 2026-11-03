---
id: IK-0604
title: "入力の表現が数値一つしか運べず、ベクトルを入力に期待する生成コードが実行時に型の誤りで落ちる"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/features/simulation_bridge_design.md §12.2
feature_context:
  realizing: "学習者が理論プログラムに波数ベクトルのような分野で自然な形の値を入れて動かす"
  layers: [simulation_bridge_sb, frontend_learning_ui]
classification:
  axes:
    processing: [input_handling]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 生成コードは k1 を 3 成分のベクトルとして反復したが、学習者の入力フォームと API は値をスカラーとしてしか
    受け付けず、sandbox で「float は反復できない」型の誤りが出た。構造軸 representation = 入力値の表現に列の居場所が
    無かった。接続軸 information = 分野で自然な値の形（ベクトル）が入力の段で運べず落ちた。処理軸 input_handling = 受け口が
    数値以外を数値に丸めていた側面。統制軸は順序・確定の問題ではないので none。
generalization:
  level: general
  general_form: "入力の表現が想定した単純な形しか持てず、扱う対象に自然な形（列・組）の値が入口で運べないまま、下流が期待した形と食い違って落ちる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "実機でテスト用の学生として実行したところ型の誤りで止まり、生成コードがベクトルを期待していることと、入力欄・API がスカラーしか受けないことを突き合わせた。"
resolution:
  perspective: [representation_change]
  note: >-
    値の表現を「数値、または短い数値の列」に広げた。学習者 route の `_value_pairs` が両方を受け、教員側の
    `RunRequest.values` も同じ形に広げ、パネルの入力欄はテキストにしてカンマ区切りを数値の列として読む（書き方のヒントを添える）。
    長さの上限は残し、数値でない要素は受けない。
  landed_in:
    - backend/api/routes/simulation_lab.py
    - backend/api/routes/simulation_bridge.py
    - frontend/public/js/simulation-lab.js
    - backend/tests/test_simulation_lab_api.py
    - df0f14c
    - docs/features/simulation_bridge_design.md §12.2
  verification:
    methods: [docker_e2e, guardrail]
    unverified:
      - "理論出力がベクトルのときの比較器（比較器は出力をスカラーに縮めて比べたまま）"
related: [IK-0603]
view_of: []
history: []
---

## 課題

生成された理論プログラムが入力の一つをベクトルとして扱っていたが、学習者の入力欄と API はスカラーしか運べなかったため、
実行時に型の誤りで落ちた。分野で自然な値の形が、入口の表現に居場所を持っていなかった。

## 発見の観点

実機で学生として実行し、型の誤りから、生成コードが期待する形と入力の受け口の形を突き合わせた。

## 解決の観点

生成側にスカラーだけを使わせる案は、理論の自然な表現を歪めるので採らず、入力の表現を短い数値の列まで広げた。広げる範囲は
数値の列に限り、上限を残した（任意の構造を受けると検査の効かない入れ物になる）。

## 一般化

入口の表現を最も単純な形で始めると、扱う対象の自然な形がそこで削られ、下流の期待との食い違いとして現れる。辞書の型は
`information-dropped-as-unrepresentable`。出力側（比較器）にも同じ縮約が残っている。
