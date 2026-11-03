---
id: IK-0646
title: "生成コードが全出力を計算不能として空を返すと入出力欄が空のまま並び、学習者が何を自分で決めれば動かせるかの案内が無く、採用の意味も押す前には出ない"
status: open
recorded_at: 2026-10-04
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "学習者が生成された理論プログラムを読み、採用して入力を決めて動かす"
  layers: [simulation_bridge_sb, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [completion]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: cannot_compute で全出力が計算不能のとき、生成コードは空の dict を返し、入出力欄は「—」だけになる。次に学習者が決めるべきこと（計算不能の記号に値を与える等）の導線が無く、採用の意味の事実文は採用後にしか出ない。接続軸 information = 計算不能の理由はあるが次の操作の案内へ渡らない。統制軸 completion = 生成の完了を動かせる状態と取り違える。処理軸 none。構造軸 none。
generalization:
  level: repo_pattern
  general_form: "生成物が実質空でも生成が完了した扱いで次の操作へ進ませ、利用者が何を補えば使えるかの導線が無い"
pattern: last-mile-missing
discovery:
  perspective: [reproduction]
  note: "第 21 周 (b) の再演で、学生が空の入出力を前に何をすればよいか分からず、採用を押す前に意味を読めなかった。"
resolution:
  perspective: [pending]
  note: "未解決（軽微）。全出力が計算不能のときは計算不能の記号と次の操作を案内し、採用の意味の文を押す前にも出せば解ける。"
related: [IK-0620, IK-0627]
view_of: []
history: []
---

## 課題

症状: 入出力が空のプログラムを前に次の手が分からない。原因: 全出力が計算不能のとき導線が無く、採用の意味は押した後にしか出ない。

## 発見の観点

ペルソナの通し受講の再演（reproduction）で見えた。

## 解決の観点

未解決。計算不能の事実文を次の操作の案内へつなぐ。

## 一般化

中身の無い生成物を完了として見せると、利用者は装置の手前で止まる。型は last-mile-missing。確かめ方は、全出力が計算不能になる式で生成し、画面だけで次に何を決めればよいか読めることを見ること。
