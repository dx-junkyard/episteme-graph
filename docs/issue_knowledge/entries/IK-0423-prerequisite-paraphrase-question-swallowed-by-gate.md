---
id: IK-0423
title: "前提「GRとMGでの観測量への影響」を言い換えた問い（「GR と修正重力（MG）で、観測量への音速の影響はどう違うのか」）が、前提の説明ではなく逆質問に吸い込まれていた（前提への言及を名前全体か名前の頭の語でしか見ていなかった）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が前提そのものについて尋ねたとき、逆質問ではなく前提の説明で答える
  layers: [rag_chat]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
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
    処理軸: 前提への言及判定（_prerequisite_mentioned）は前提名全体の部分文字列か、助詞で切った頭の語（3文字以上）
    しか見ず、空白・括弧・語順の入れ替わった言い換えでは当たらなかった（「GR」は3文字未満で頭の語にもならない）
    （input_handling）。構造軸は none（前提名は表現として足りている。medium: 言い換えを表す別名の居場所が無いとも読める）。
    接続軸・統制軸は none。確認: 第 9 周の transcript で、前提の中身を尋ねた問いに逆質問が返った。
generalization:
  level: repo_pattern
  general_form: 利用者の言い換えを、登録名の完全一致に近い照合でしか拾わず、同じ対象への問いを別物として扱う
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction]
  note: 第 9 周の transcript で、逆質問が返った問いと前提名の表記を並べた。
resolution:
  perspective: [single_point_fix]
  note: >-
    前提名の内容語（漢字・カタカナの2字以上の連なりと英字2字以上の語）が発話に2つ以上現れ、発話が説明要求か問いの形なら、
    前提そのものへの問いとして説明（是正 F4 の3段解決）へ流す（_prerequisite_content_overlap）。英字の語は
    alias_matching の語境界付き照合（GR が gravity の途中に当たらない）。略語の展開（MG ≈ 修正重力）は見ない。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0422_prerequisite_gate_once.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 前提名の内容語が一つしかない前提（言い換えを重なりでは拾えない）
      - 現在のトピックの問いが前提名と内容語を二つ以上共有するとき、前提の説明へ流れる
related: [IK-0383, IK-0422]
view_of: []
history: []
---

## 課題

前提の言い換えが前提への問いとして読まれない。

## 発見の観点

逆質問が返った問いと前提名の表記を並べた。

## 解決の観点

前提名の内容語の重なりで前提への問いを読む。

## 一般化

言い換えを登録名の完全一致に近い照合でしか拾わない型。
