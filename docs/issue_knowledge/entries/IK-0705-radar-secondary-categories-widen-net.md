---
id: IK-0705
title: "論文レーダーが起点の副カテゴリまで等価に OR するため検索範囲が黙って広がり、主カテゴリと離れた分野の新着が半数を占める"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §19"
feature_context:
  realizing: "起点の論文の arXiv カテゴリで近い論文の検索範囲を決める"
  layers: [paper_radar, paper_discovery]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [assignment]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（検索記録のクエリとカテゴリ候補の組み立て）: 起点の全カテゴリ（主カテゴリと副カテゴリ）を等価に有効にして OR していた。
    接続軸 condition = arXiv が区別している主カテゴリという条件が、検索条件の段で落ちて範囲が広がった。統制軸 assignment =
    範囲を広げる判断は教員がするはずが、既定でシステムが広げていた。処理軸 none = OR の組み立て自体は仕様どおり。
    構造軸 none = チップの有効・無効の表現は既に在った。
generalization:
  level: general
  general_form: "入力が持つ主従の区別を落として全要素を等価に条件へ入れ、対象の範囲が利用者の操作なしに広がる"
pattern: scope-widened-silently
discovery:
  perspective: [data_inspection]
  note: "宇宙論の起点で、副カテゴリ由来の素粒子現象論・ホログラフィーの新着が候補の半数以上を占めることを検索記録から読んだ（出所: uxsim/runs/radar_loop/judge-scan-01.md §4 仮説 4・scan-01.json）。"
resolution:
  perspective: [carry_through, explicit_contract]
  note: "arXiv 由来のカテゴリは主カテゴリだけを既定で有効にし、副カテゴリは無効のチップ（打ち消し表示・クリックで足す）として保持する。検索は有効なチップだけを送る。購読由来は従来どおり全件有効。"
  landed_in:
    - backend/core/paper_discovery/radar.py
    - frontend/public/js/admin-paper-radar.js
    - "docs/features/paper_radar_design.md §19"
  verification:
    methods: [guardrail]
    unverified:
      - "実機の検索で主カテゴリだけの網が主題の候補を増やすこと"
      - "ブラウザでの副カテゴリのチップ操作"
related: [IK-0703]
view_of: []
history: []
---

## 課題

症状: astro-ph.CO の論文を起点にすると、副カテゴリ hep-ph / hep-th の新着が候補の半数を占めた。
原因: 起点の全カテゴリを等価に OR しており、主カテゴリと副カテゴリの区別が検索条件に届いていなかった。

出所: uxsim/runs/radar_loop/judge-scan-01.md §4 仮説 4、検索記録 scan-01.json。

## 発見の観点

実データの検索記録で、候補の分類が起点の主題から離れていることを読んだ（data_inspection）。

## 解決の観点

主カテゴリという条件を検索条件まで運び（carry_through）、範囲を広げるのは教員のクリックだけにした。外した副カテゴリは
打ち消し表示で保持する（情報を落とさない）。外部 API の呼び出し回数は変わらない。

## 一般化

主従・優先度を持つ入力を全部等価に条件へ入れると、範囲が黙って広がる。型 scope-widened-silently。
