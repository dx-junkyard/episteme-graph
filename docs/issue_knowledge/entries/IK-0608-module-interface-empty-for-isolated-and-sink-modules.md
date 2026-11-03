---
id: IK-0608
title: "理論モジュールの入力・出力を「外枠の境界をまたぐ式」だけで定義しているため、孤立したモジュールと吸い込み口へ渡すだけのモジュールは何を受け取り何を返すかを表せない"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-04
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
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
  perspective: [representation_change, carry_through]
  note: "第 22 波: builder がモジュールに受け取る記号（成員の式が使うが成員のどれも定義しない）と定める記号（成員の式が定義する・出力の式を先頭に）を決定論で足し（input_symbols / output_symbols）、記号の雑音は clean_symbol_name、照合キーは symbol_match_key に一本化した。service.module_items が input_symbols / output_symbols / output_symbols_note の 3 キーを学習者 DTO へ運び、学習画面は「受け取る記号 → 定める記号」で出す。関係式だけのまとまりの空は IK-0636 に分けた。"
  landed_in:
    - "backend/core/theory_modules/builder.py"
    - "backend/core/theory_modules/schema.py"
    - "backend/core/simulation_bridge/service.py"
    - "frontend/public/js/simulation-lab.js"
    - "docs/features/simulation_bridge_design.md §12.5"
  verification:
    methods: [guardrail, scratch_db, reproduction_rerun]
    unverified:
      - "第 21 周 (a) の再演の初見の学生はなお諦めた（理由は空の表示から、受け取る記号が主題とずれる・定める記号が空へ変わった = 部分的に消えた。関係式は IK-0636・束ね方は IK-0647）"
      - "(a') は到達したがチャット経由で、一覧だけで入出力を言えたかは見ていない"
related: [IK-0624, IK-0636, IK-0647]
view_of: []
history:
  - date: 2026-10-04
    field: status
    from: open
    to: resolved
    reason: "第 22 波で受け取る記号・定める記号を一覧に載せた"
  - date: 2026-10-04
    field: resolution.verification
    from: 記録時の範囲
    to: 第 22 周の再演で更新
    reason: "第 22 周の再演で (c) は消えた・(a) は部分的に消えた"
---

## 課題

症状: 学習画面の理論モジュール一覧で、入力の式と出力の式が空の記号だけになる行があり、選んでも何を受け取り何を出すか読めない。原因: モジュールの入出力を外との境界をまたぐ式だけで定義しており、孤立したモジュールや結果を吸い込み口へ渡すだけのモジュールでは両方が空集合になる。成員の式・定義記号は一覧の DTO に載らないため、空の代わりに見せる材料も無い。

## 発見の観点

ペルソナの通し受講（reproduction）で詰まりを観測し、一覧の DTO から builder の境界計算まで経路を辿った（trace_walk）。

## 解決の観点

部分着地（第 21 波）。core の一覧 DTO に成員の式と吸い込み口へ渡す式を additive に足し、UI は入出力が空のまとまりに「この手順の式」「→ 論文全体のまとめへ」を出す。境界の定義（接点の狭さ）は変えていない。成員の式は並ぶが、何を受け取り何を出すかの**記号**は出ないため、再演 (a) の初見の学生はなお読めず諦めた。入出力の記号を生成前に示す案は次周に残す。

第 22 波で解決。成員の式の defined_symbols / used_symbols から受け取る記号と定める記号を決定論で導き、一覧に出す。砂場のモジュールで受け取る記号が空のものが無くなったことを SELECT で確かめた。定める記号が空のまとまり（関係式だけ）は IK-0636 に分けた。

第 22 周の再演: (c) で σ_ξ のまとまりは受け取る記号 → 定める記号が読め、生成前に出力が分かった（GONE）。(a') は到達（チャット経由）。(a) は諦めたままだが理由が変わった（部分 GONE）。

## 一般化

境界で入出力を定義する表現は、境界の無い・外へ流すだけの部分を空として見せる。型は information-dropped-as-unrepresentable。確かめ方は、孤立・吸い込み口のみのモジュールを含む教材で一覧を出し、空の行が残らないことを見ること。
