---
id: IK-0445
title: "参考文献の連なり・表題と所属機関の区画が引き続き出典として採用された（節見出しが本文の節のままで見出しフィルタを抜け、書誌の検出は年の密度の閾値が厳しすぎた）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者の問いに対する出典が論文の本文になる
  layers: [rag_chat]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [meaning]
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
    処理軸: IK-0390 の NON_CONTENT_SECTION_TITLES は section_title による判定で、PDF 経路では書誌が付録や本文の節（Parameterization
    等）に吸収され見出しで捕まらない。後段の non_content_chunk_reason は年の表記が語数の 5% 以上を条件にしていたが、番号付き書誌（[26] A. Amon …, 516, 5355
    (2022), arXiv:…）は著者名と題名で語数が多く密度が 4% 前後に留まった。表題・所属機関の区画と、本文先頭に残った ACKNOWLEDGMENTS / DATA AVAILABILITY
    の見出しには判定が無かった（input_handling）。接続軸: 見出しという代理の手がかりで区画の意味を決めていた（meaning、medium）。
generalization:
  level: general
  general_form: 区画の性質を見出しなどの近くの手がかりで決め、中身の形で確かめない
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: 第 10 周（c-astro-verify-wave456）の transcript の往復ごとの sources と mailbox の実プロンプト（req-*.json）を並べた。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    non_content_chunk_reason に 3 つの判定を足す。①書誌の連なり: 行頭の [N] 項目が 2 つ以上・括弧年または arXiv ID が 3
    つ以上・最初の項目から後ろが過半（付録の段落や図の説明の後ろに書誌がつながった区画も書誌が大半なら落とす）、項目の頭が無い区画は括弧年・arXiv ID の密度で判定。②先頭 3
    行の見出しが参考文献・データの所在・謝辞・Software などなら落とす。③所属機関の行が 3 つ以上かつ行の 4 分の 1 以上なら表題の区画として落とす。本文の [1–6] 型・(Houde et al.
    2009) 型の引用は括弧年・arXiv ID に当たらないので残る。第 10 周の実プロンプトの区画に当て、報告のあった区画はすべて落ち、本文は残ることを確かめた。
  landed_in:
    - backend/api/services.py
    - backend/tests/test_ik0444_0451_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 見出しも項目番号も持たない著者年方式の書誌が本文段落と混ざった区画
related: [IK-0390]
view_of: []
history: []
---

## 課題

書誌・所属機関の区画が出典として採用される。

## 発見の観点

実プロンプトの出典区画を判定に当て、抜けた理由を数えた。

## 解決の観点

中身の形（項目の頭・括弧年・arXiv ID・所属機関の行）で判定を足す。

## 一般化

区画の性質を近くの手がかりで決める型。
