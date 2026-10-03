---
id: IK-0608
title: "理論モジュールの入力・出力を「外枠の境界をまたぐ式」だけで定義しているため、孤立したモジュールと吸い込み口へ渡すだけのモジュールは何を受け取り何を返すかを表せない"
status: open
recorded_at: 2026-10-03
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "学習者が理論モジュールを選び、その入力と出力を読んで動かす対象を決める"
  layers: [simulation_bridge_sb, graph_review, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: backend/core/theory_modules/builder.py の interface() は成員の外から受ける式と外へ出す式（境界をまたぐ式）だけを返し、境界をまたがないモジュール・結果を吸い込み口へ渡すだけのモジュールでは入力・出力の両方が空になる。学習者向けのモジュール一覧 DTO には成員の式・定義記号が載らない。構造軸 representation = 「このモジュールの中にある式」「吸い込み口へ渡す」を表す居場所が DTO に無い。接続軸 information = 境界で計算した集合しか後段へ渡らない。処理軸 none（計算は定義どおり）。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "部分系の入出力を外との境界で定義し、境界の無い部分系や外へ結果を流すだけの部分系では入出力が空になって、中身を読む手がかりが利用者に届かない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, trace_walk]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、複数のペルソナが一覧の「入力の式 → 出力の式」が空表示で詰まった。DTO と builder の interface を辿り、空は計算の失敗ではなく定義の結果だと分かった。"
resolution:
  perspective: [pending]
  note: "部分着地（第 21 波・2026-10-03）: builder がモジュールに成員の式（member_equations）と吸い込み口へ渡す式（to_sink）を足し、service.module_items が学習者 DTO の equations / to_sink へ運び、simulation-lab.js が入出力の空いたモジュールに「この手順の式」と「→ 論文全体のまとめへ」を出す。成員の式は出るが入出力の記号は出ない。再演 (a) では初見の学生がなお何を受け取り何を出すかを読めず諦めたため open のまま残す。入出力の記号（io_contract 相当）を生成前に出す案は次周。"
  landed_in:
    - backend/core/theory_modules/builder.py
    - backend/core/simulation_bridge/service.py
    - frontend/public/js/simulation-lab.js
    - "docs/features/simulation_bridge_design.md §12.4"
related: [IK-0624]
view_of: []
history: []
---

## 課題

症状: 学習画面の理論モジュール一覧で、入力の式と出力の式が空の記号だけになる行があり、選んでも何を受け取り何を出すか読めない。原因: モジュールの入出力を外との境界をまたぐ式だけで定義しており、孤立したモジュールや結果を吸い込み口へ渡すだけのモジュールでは両方が空集合になる。成員の式・定義記号は一覧の DTO に載らないため、空の代わりに見せる材料も無い。

## 発見の観点

ペルソナの通し受講（reproduction）で詰まりを観測し、一覧の DTO から builder の境界計算まで経路を辿った（trace_walk）。

## 解決の観点

部分着地（第 21 波）。core の一覧 DTO に成員の式と吸い込み口へ渡す式を additive に足し、UI は入出力が空のまとまりに「この手順の式」「→ 論文全体のまとめへ」を出す。境界の定義（接点の狭さ）は変えていない。成員の式は並ぶが、何を受け取り何を出すかの**記号**は出ないため、再演 (a) の初見の学生はなお読めず諦めた。入出力の記号を生成前に示す案は次周に残す。

## 一般化

境界で入出力を定義する表現は、境界の無い・外へ流すだけの部分を空として見せる。型は information-dropped-as-unrepresentable。確かめ方は、孤立・吸い込み口のみのモジュールを含む教材で一覧を出し、空の行が残らないことを見ること。
