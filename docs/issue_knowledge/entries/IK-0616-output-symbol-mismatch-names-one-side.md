---
id: IK-0616
title: "生成コードの出力記号とモジュールの出力の式の記号が重ならないときの事実文が片側しか名指さず、モジュールが別の記号を定義する式を出力に持つ場合に学習者が食い違いを読めない"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "生成した理論プログラムが、選んだモジュールの出力を計算しているかを学習者に確かめさせる"
  layers: [simulation_bridge_sb, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 第 21 周の時点の backend/core/simulation_bridge/codegen.py の _output_symbols はモジュールの出力の式が定義する記号を期待値とし、生成コードの出力と重ならないと「重なっていません」とだけ書く。出力の式が別の記号（例: 長さの記号）を定義し、生成コードがそれから導いた量（例: 質量の記号）を出す場合、学習者には正しく計算しているのに否定されたように読める。接続軸 meaning = 期待の記号と出力の記号の関係が文に出ない。処理軸 wording。構造軸 none（判定は定義どおり）。統制軸 none。
generalization:
  level: instance
  general_form: "2 つの集合の不一致を告げる文が片側の集合しか名指さず、受け手が何と何が食い違ったかを再構成できない"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、質量を出力したペルソナに「出力の記号がモジュールの出力と重なっていません」が出た。モジュールの出力の式は長さの記号を定義していた。"
resolution:
  perspective: [carry_through, representation_change]
  note: "出力記号の不一致の事実文が両方の記号を名指しする（schema.outputs_not_matching_fact）。照合は codegen.normalize_symbol（TeX の修飾・波括弧・バックスラッシュを落として小文字）と symbols_overlap で行い、表記ゆれを同じ記号とみなす。再演 1 回目で理論側に記号レコードの辞書表記が出たため、_output_symbols が論文層の定義記号から名前だけを取るよう追補した。"
  landed_in:
    - backend/core/simulation_bridge/schema.py
    - backend/core/simulation_bridge/codegen.py
    - backend/tests/test_simulation_bridge_codegen.py
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "名前だけを取る追補の後の画面での再確認（追補の後の学生の再演はしていない）"
related: [IK-0608]
view_of: []
history: []
---

## 課題

症状: 生成コードがモジュールの目的の量を出しているのに、出力の記号が重ならないという事実文が出る。原因: 判定はモジュールの出力の式が定義する記号を期待値にしており、文は生成コード側の記号だけを名指す。

## 発見の観点

ペルソナの通し受講（reproduction）で観測し、期待の記号の集め方を読んだ。

## 解決の観点

不一致の文に両側の記号を運び、照合を記号の正規化で行うようにした。再演で両方を名指しする文になったことを見たが、理論側に辞書表記が出たため名前だけを取る追補を入れた。追補はガードレールで確かめた。

## 一般化

不一致の告知は両側を見せないと受け手が判断できない。型は wording-mismatch。確かめ方は、出力の式と目的の量の記号が異なるモジュールで生成し、文に両方の記号が出ることを見ること。
