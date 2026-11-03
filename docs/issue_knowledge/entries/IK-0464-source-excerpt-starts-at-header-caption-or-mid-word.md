---
id: IK-0464
title: "トピックの原文抜粋が著者・所属の区画、図のキャプション（「mJy/beam Figure 3.」）、語の途中（「resentative seed.」）から始まっていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、トピックの出典チャンクから原文抜粋を作り、授業用ドラフトと学習画面の ![[source:…]] に使う
  layers: [course_builder]
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
    処理軸: 原文抜粋は出典チャンクの論文順の先頭の冒頭を切るだけで、先頭チャンクが本文かどうか（所属・書誌・キャプション）も、区画の境目が文の途中かどうかも確かめていなかった。どのチャンクを使うかを並び順（位置）で決めていた。
generalization:
  level: repo_pattern
  general_form: 抜粋の元を並び順の先頭で決め、本文として読めるかを中身で確かめない
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: 20 トピックの source_excerpt の書き出しを読んだ。
resolution:
  perspective: [single_point_fix]
  note: >-
    topic_source_excerpt がチャンクを「トピックの根拠 block の並びで最初に当たる位置」の順に見て、所属（ABSTRACT の前に所属の語）・書誌・図のキャプションで始まるものを飛ばし、所属の後の ABSTRACT から・書きかけの文を落としてから切る。どれも読めなければ先頭チャンクの書きかけの文だけを落とす。判定は api/services.py::non_content_chunk_reason と同じ発想の最小の写し（core から api を import できず、あちらは tests/test_search_visibility.py が所在を固定しているので移していない — 重複はここに記録）。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0459_0465_course_draft_regen11.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成
      - 学習チャットの RAG が使うチャンクの冒頭（「s > 0 and Dkin > 0.」）は別経路で、ここでは直していない
      - 所属の判定は1語で当たる（本文が冒頭に大学名を含む段落は飛ばされる）
related: [IK-0429, IK-0438, IK-0440, IK-0443, IK-0454]
view_of: []
history: []
---

## 課題

原文抜粋が本文でない区画や語の途中から始まっていた。

## 発見の観点

source_excerpt の書き出しを全トピック分読んだ。

## 解決の観点

抜粋の元を根拠の並びで選び、本文として読めるものの文の始まりから切る。

## 一般化

抜粋は本文として読めることを中身で確かめてから、文の境界で切る。
