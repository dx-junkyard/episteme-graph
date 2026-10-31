---
id: IK-0800
title: "論文レーダーのキーフレーズ供給が題名の複合語「Fingers of God」を単語（Fingers / God）に割り、過去分詞（Resummed）と修飾語の固有名（Doppler）を足して網を主題外へ広げ、論文が定義した語（single-particle characteristic function）は略語でしか供給しなかった"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §23"
feature_context:
  realizing: "起点の論文の主題を表す語で arXiv を検索し、近い論文の網を取る"
  layers: [paper_radar, paper_discovery]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（実 DB の起点 3 本で供給関数を呼んだ）: 段④の句は機能語で区切った連なりで、of を挟む複合語を割り、「フル表記 (略語)」の
    定義を知らなかった。処理軸 input_handling = 題名・要旨という入力を検索語へ切り出す段で単位を誤った。接続軸 none = 供給した語は
    そのままクエリに渡っており、伝達は正しい。構造・統制は none。
generalization:
  level: general
  general_form: "テキストから検索語を決定論で切り出すとき、機能語で単純に区切ると内側に機能語を含む複合語が意味のない断片に割れ、断片の 1 語が OR 検索の網を広げる"
pattern: upstream-loss-masked-downstream
discovery:
  perspective: [data_inspection]
  note: "自律周回の記録 scan-08.json で、供給語 Fingers / God / Resummed / Doppler と、候補 10 件中 7 件が Doppler 一致の主題外であることを読んだ（出所: uxsim/runs/radar_loop/scan-08.json）。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: "of だけを挟む複合語を 1 句として保ち（両側 1 語か他の材料に同じ表記があるとき）、過去分詞と題名にしか無い大文字 1 語を落とし、略語はフル表記の句を先・略語を別枠の 2 本目にした。略語抽出の規則は figure_context から core/abbreviations.py に切り出して共有した。小文字の名詞を修飾する固有名（Doppler shifts）は 1 語にしない。中心命題の材料に paper_goal を足した。"
  landed_in:
    - backend/core/paper_discovery/radar.py
    - backend/core/abbreviations.py
    - backend/core/document_pipeline/figure_context.py
    - backend/tests/test_paper_radar_core.py
    - "docs/features/paper_radar_design.md §23"
  verification:
    methods: [guardrail, scratch_db]
    unverified:
      - "是正後の供給語で arXiv を検索したときの網（arXiv は呼んでいない）"
      - "ブラウザでのチップ表示"
      - "of 以外の前置詞を挟む複合語"
related: [IK-0703, IK-0760]
view_of: []
history: []
---

## 課題

症状: 起点 2609.35757「Can Fingers of God be Resummed?」の near で、供給語が Fingers / God / FoG / SPCF / Resummed / Doppler になり、
`all:"God"` や `all:"Doppler"` が網を広げて候補 10 件中 7 件がパルサー・ニュートリノ・中性子星など主題外だった。
原因: 題名と中心命題の句を機能語で区切った連なりとして切り出しており、of を内側に含む複合語が単語に割れた。略語の定義
（single-particle characteristic function (SPCF)）を知らず略語だけを供給し、要旨の修飾語の固有名（Doppler shifts の Doppler）を
1 語として採った。

出所: uxsim/runs/radar_loop/scan-08.json。

## 発見の観点

自律周回の記録の供給語・クエリ・候補の一致語を読んだ（data_inspection）。

## 解決の観点

検索語の表し方を単語から句の単位に変えた（representation_change）。実データの形を写したテストで固定した（guardrail_fix）。実 DB の起点 3 本
（c795b2cf / a8d731be / 842bd9cb）で供給関数を呼び、before / after を設計書 §23.1 に残した。

## 一般化

検索語の切り出しで複合語を断片にすると、断片が OR 検索の網を広げ、下流では「近い論文」の主題外として現れる。型 upstream-loss-masked-downstream。
