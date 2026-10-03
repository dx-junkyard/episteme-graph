---
id: IK-0620
title: "学習者の「採用」が本人の選択であって教員の承認ではないことを画面に出さないため、管理側の採用・承認の語と同じ意味に読まれる"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "学習者が生成された理論プログラムを自分で採用して実行に進む"
  layers: [simulation_bridge_sb, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
    governance: [assignment]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 学習者向けの採用 API の応答に事実文が無く、画面は「採用」とだけ出す。管理側では同じ系統の語が教員の確定を指すため、学習者が教員の承認を得たと読む。接続軸 meaning = 同じ語が面によって主体を変える。統制軸 assignment = 誰の判断かが表示で区別されない。処理軸 wording。構造軸 none。確定には、採用ボタンと応答の文言を読んで主体が書かれていないこと、ペルソナの解釈が承認と読んだ発話を transcript で確かめる。
generalization:
  level: repo_pattern
  general_form: "同じ操作語が面ごとに異なる主体の判断を指し、主体を書かないため、本人の選択が他者の承認と読まれる"
pattern: same-name-different-referents
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、採用した学生が、それで内容が確かめられたかのように受け取った。"
resolution:
  perspective: [explicit_contract]
  note: "採用（confirmed）の版の facts と adopt 応答に FACT_ADOPTED（採用は本人の選択で教員の承認ではない = SB11）を載せる。"
  landed_in:
    - backend/core/simulation_bridge/schema.py
    - backend/core/simulation_bridge/service.py
    - backend/tests/test_simulation_lab_api.py
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail, reproduction_rerun, docker_e2e]
    unverified:
      - "英語話者への固定文（i18n・既知の構造課題）"
related: [IK-0626]
view_of: []
history: []
---

## 課題

症状: 学習者が採用を押すと、教員が確認した内容になったかのように読む。原因（仮説）: 採用の応答と画面に「本人の選択で、教員の承認ではない」という事実文が無く、管理側の同系統の語の意味が持ち込まれる。

## 発見の観点

ペルソナの通し受講（reproduction）で採用後の発話から観測した。

## 解決の観点

採用の意味を事実文で宣言した。再演で採用後の画面にこの文が出て、承認との誤読は消えた（GONE）。

## 一般化

主体が違う確定は語を変えるか主体を書く。型は same-name-different-referents。確かめ方は、採用後の画面だけを読んで「誰が確かめたか」を答えさせ、教員と読まれないことを見ること。
