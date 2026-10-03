---
id: IK-0612
title: "実行結果の段階ラベルを軸名なしの値の並びで返し、比較の文と同じ文を事実文にも重ねるため、学習者は何の段階か読めず同じ文を二度読む"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "理論の出力と模擬観測・論文の記載値を比べた結果を、段階の言葉で学習者に示す"
  layers: [simulation_bridge_sb, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [information]
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
    確認: backend/core/simulation_bridge/service.py の run_pipeline は result.labels を agreement / separation / reference の内部キーと日本語の値だけで返し、軸の表示名を持たない。画面は値だけを並べるため「未測定 / 未測定 / 未測定」になる。また statement と比較器の事実文が同じ文を持ち、画面で二重に出た。構造軸 representation = 軸の表示名の居場所が DTO に無い。接続軸 information = 軸名が後段の画面へ渡らない。処理軸 wording = 重複した文。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "複数の軸の判定を内部キーと値だけで返し、軸の表示名を運ばないため、画面が値を並べると何の判定か読めない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、複数の場面で段階ラベルが同じ語の並びになり、学生が何の 3 つかを尋ねた。DTO を見ると軸名が無かった。"
resolution:
  perspective: [vocabulary_table, guardrail_fix]
  note: "軸名の正本を label_vocab.SIMULATION_AXIS_LABELS に置き、service.axis_labels が result.labels を「軸名: 段階」で返す（段階の表は不変）。statement と同文の fact は service.without_statement_duplicate が落とす。"
  landed_in:
    - backend/core/label_vocab.py
    - backend/core/simulation_bridge/service.py
    - backend/tests/test_simulation_bridge_service.py
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail, reproduction_rerun, docker_e2e]
    unverified:
      - "行頭で比較対象の語が二重に出る読みにくさ（「理論の出力と模擬観測: 一致（理論の出力と模擬観測）: 未測定」— 再演で新たに見えた）"
      - "記載値を入れた実行での表示"
related: [IK-0606, IK-0611]
view_of: []
history: []
---

## 課題

症状: 結果に「段階ラベル: 未測定 / 未測定 / 未測定」と出て、どれが一致度・分離・記載値との比較か分からない。比較の文と事実文に同じ文が二度出る。原因: 結果の段階ラベルを軸名なしで返し、画面は値だけを並べる。比較の文と事実文の出所が別で、重複を除く箇所が無い。

## 発見の観点

ペルソナの通し受講（reproduction）で観測し、結果 DTO の labels を読んだ。

## 解決の観点

軸名を語彙表に置き、段階ラベルを軸名付きで返すようにした。statement と同文の fact は落とす。再演で軸名の無い 3 値の並びと一致の文の重複は消えた（GONE）。

## 一般化

多軸の判定は軸名とセットで運ばないと、表示面ごとに軸名を推測して書くことになる。型は information-dropped-as-unrepresentable。確かめ方は、全軸が同じ値になる実行を作り、画面で各値に軸名が付くこと・同じ文が一度だけ出ることを見ること。
