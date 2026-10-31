---
id: IK-0606
title: "比較器が比べられない条件（配線なし・理論出力がゼロ）でも同じ相対差の規則を当て、空疎な「一致」「乖離」を出す"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/features/simulation_bridge_design.md §12.2
feature_context:
  realizing: "理論プログラムの出力と実験機の観測を比べ、一致・乖離を事実として学習者と教員に見せる"
  layers: [simulation_bridge_sb, frontend_learning_ui, frontend_admin_ui]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 配線が無いと観測値は理論値そのものになり、比較器は相対差ゼロから「一致」を出していた。理論出力がちょうどゼロの
    とき（直交する波数ベクトル）は相対差の分母が極小値になり、雑音だけで「乖離」を出していた。接続軸 meaning = 相対差という
    尺度は「独立な観測がある・理論値がゼロから離れている」ときにだけ意味を持つが、その成立条件を確かめずに適用していた。
    構造軸 representation = 判定に「測れなかった」の居場所が無く、どちらかのラベルに押し込まれていた。処理軸 logic =
    分母の扱いの誤り。あわせて事実文が実行と比較の両方から出て二重に描かれていた（表示の重複は副次）。統制軸は none。
generalization:
  level: general
  general_form: "比率で判定する規則を、比率が意味を持たない条件（比べる相手が無い・分母がゼロ近傍）でも当て、判定が常に片側に倒れて空疎なラベルを出す"
pattern: threshold-reused-across-regimes
discovery:
  perspective: [reproduction, data_inspection]
  note: "実機で配線なしの実行と、直交するベクトルを入れた実行を試し、出た「一致」「乖離」を入力と突き合わせて、どちらも測っていない判定だと分かった。"
resolution:
  perspective: [representation_change, single_point_fix]
  note: >-
    「未測定」を一級の判定として持たせた。配線が無ければ一致度は未測定、理論出力の絶対値が極小値を下回れば相対差を None にして
    事実文 `FACT_ZERO_THEORY_OUTPUT` を添える。古い意味を固定していたテストを新しい意味に合わせ、両 UI の事実文の重複も除いた。
  landed_in:
    - backend/core/simulation_bridge/comparator.py
    - backend/tests/test_simulation_bridge_comparator.py
    - frontend/public/js/simulation-lab.js
    - frontend/public/js/admin-simulation-bridge.js
    - 3e28365
    - 13bc79b
    - 00698de
    - docs/features/simulation_bridge_design.md §12.2
  verification:
    methods: [guardrail, docker_e2e]
    unverified:
      - "理論出力がベクトルのときの比較（比較器は出力をスカラーに縮めたまま）"
related: [IK-0604]
view_of: []
history: []
---

## 課題

比較器が、比べる相手が無い（配線なし）ときに「一致」、理論出力がゼロのときに「乖離」を出していた。前者は観測が理論値そのもの
なので相対差が必ずゼロになり、後者は分母が極小値になって雑音だけで差が大きく出る。どちらも何も測っていない判定だった。
加えて、事実文が二箇所から出て画面に二重に描かれていた。

## 発見の観点

実機で配線なしの実行と直交するベクトルの実行を試し、出たラベルを入力と突き合わせて、判定の成立条件が満たされていないことを
見た。

## 解決の観点

閾値を調整する案は成立条件の外という本質を変えないので採らず、「未測定」を判定の値として持たせ、条件の外ではそれと事実文を
返すようにした。

## 一般化

比率・相対差の判定は、比べる相手の独立性と分母の大きさという成立条件を持つ。条件の外で同じ規則を当てると判定が片側に張り付く。
辞書の型は `threshold-reused-across-regimes`。確かめ方は、条件の内と外の入力をそれぞれ与え、外では未測定になることを見ること。
