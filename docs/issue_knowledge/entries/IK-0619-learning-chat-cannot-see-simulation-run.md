---
id: IK-0619
title: "画面文脈アダプターに検証シミュレーションの解決器が無いため、学習チャットが学習者の見ている実行結果を知らず、画面の数値や段階の言葉について答えられない"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "学習者が実行結果を見ながら、その結果についてチャットで尋ねる"
  layers: [screen_adapter_sa, simulation_bridge_sb, rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: backend/core/assistant_context/ に simulation を扱う解決器が無く（登録 kind は element / verification / placement / view / retrieved_structure）、simulation-lab.js の画面文脈にも選択中の実行が載らない。チャットの grounding に実行した理論プログラム・入力・比較の結果が入らず、回答は一般論になった。接続軸 information = 画面にある実行の事実がチャットの入力に渡らない。構造軸 none（画面文脈の仕組み自体はある）。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "画面の状態をチャットへ渡す仕組みはあるが、新しい画面にその解決器が配線されず、チャットが利用者の見ているものを知らないまま答える"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、結果画面について尋ねた学生に、画面の内容が含まれていないという趣旨の答えが返った。grounding を辿ると実行の事実が無かった。"
resolution:
  perspective: [carry_through, explicit_contract]
  note: "SA層の学習者解決器に simulation（resolvers/learning.py::resolve_simulation）を足し、app.js の getScreenContext が「動かして確かめる」で選んでいる自分の実行を selection.simulation_run_id で送る（開いていなければキーごと送らない）。事実文に SB1 の行が載ったときだけ system 末尾に LEARNING_SIMULATION_OUTPUT_CONSTRAINT。"
  landed_in:
    - backend/core/assistant_context/resolvers/learning.py
    - frontend/public/js/app.js
    - frontend/public/js/simulation-lab.js
    - backend/core/simulation_bridge/schema.py
    - backend/tests/test_assistant_context_learning_simulation.py
    - "docs/features/assistant_screen_adapter_design.md §11.17"
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail]
    unverified:
      - "再演では学習者の問いが使い方の問いに分類され、実行の事実が応答に効いたかは見ていない"
      - "前提の逆質問との同居"
related: [IK-0611]
view_of: []
history: []
---

## 課題

症状: 結果を見ながら尋ねると、チャットは画面の数値・段階の言葉・改訂案を知らずに一般論で答える。原因: 画面文脈アダプターに実行を解決する kind が無く、学習画面も選択中の実行を送らない。

## 発見の観点

ペルソナの通し受講（reproduction）で観測し、チャットの grounding を辿って実行の事実が入っていないことを確かめた（trace_walk）。

## 解決の観点

画面で選んでいる実行の参照を送り、サーバの解決器が事実文にして当該ターンの入力に足す。検証・実証と言い換えない制約を添える。ガードレールで確かめたが、再演の問いは使い方の HELP 経路に振られ、効果は画面で見ていない。

## 一般化

画面を新設するとき、AI 対話への文脈の配線が後回しになりやすい。型は available-but-unwired。確かめ方は、実行結果の画面でチャットに結果の値を尋ね、grounding に実行の事実文が載ることを見ること。
