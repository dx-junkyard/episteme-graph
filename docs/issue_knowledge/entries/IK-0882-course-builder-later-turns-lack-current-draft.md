---
id: IK-0882
title: "（IK-0859 の別視点）原稿スタジオの面の種まきで、コースビルダーの 2 往復目以降に AI が現在の構成案を見ずに章立ての組み直し・一覧の提示・登録の確認に答えた"
status: open
recorded_at: 2026-10-23
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員がコースビルダーで構成案を何度か直し、確認してから登録し、そのコースを原稿スタジオで仕上げる"
  layers: [course_builder, frontend_admin_ui]
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
    原因は IK-0859 と同じ（admin.js 5343 行の chatHistory.length <= 1 のときだけ構成案を注入し、5386 行で JSON を剥がした本文だけを履歴に積む）。
    座標は IK-0859 に従う。
generalization:
  level: repo_pattern
  general_form: "画面が持つ現在の状態を、対話の 2 往復目以降の AI の入力に渡さない"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    studio の run で見えた固有の症状: 種まき（t-v5-register-own-course）の 3 往復目「最後に章とトピックの一覧を見せて」に AI は
    「構成は変えていません」と一覧を返したが、製品頭脳の所見（BRAIN_NOTES_product_P1.md 3995・P0 3988）は前案の構造が材料に無く記憶で
    答えたと記す。登録されたコースの先頭トピックは units を持つが、続く書き換え（IK-0874）の根拠は空だった — 構成案が会話の外に
    あることと、書き換えが units を読まないことが同じ場面で重なった。run: uxsim/runs/c-astro-r25-studio-r1/20261022T125145Z-40602（seq5〜8）。
resolution:
  perspective: [pending]
  note: "IK-0859 で扱う。"
related: [IK-0874]
view_of: [IK-0859]
history: []
---

## 課題

IK-0859 の別視点。原稿スタジオの面の種まきで、コースビルダーの後半の往復が現在の構成案を見ずに答えた。

## 発見の観点

ペルソナの通し受講（reproduction）と製品頭脳の要求の読み（data_inspection）。

## 解決の観点

IK-0859 で扱う。

## 一般化

IK-0859 を見ること。
