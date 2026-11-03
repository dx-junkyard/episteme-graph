---
id: IK-0607
title: "ブラウザで役割を切り替える実機検証が前の役割のトークンを残したまま操作し、教員の行を学生の行と取り違えて漏洩に見える"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/features/simulation_bridge_design.md §12.2
  - docs/architecture/improvement_cycle.md §2.5
feature_context:
  realizing: "検証シミュレーション層の学習者と教員の画面を、同じブラウザで役割を切り替えながら実機で確かめる"
  layers: [cycle_verification, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [ordering]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 学習画面を操作したとき、ブラウザには先に管理画面でログインした教員のトークンが同一オリジンの保存領域に残っていた。
    そのため「学生として」作った行は教員の所有で、教員の画面に共有していない実行が見えるのが漏洩のように見えた。DB の所有者の
    列を読んで取り違えと分かり、ログアウトして学生でログインし直した再実行では漏洩は起きなかった。接続軸 condition = 「いまどの
    役割で操作しているか」という条件が、検証者の判断の段に渡っていなかった（画面のアカウント名を確かめていなかった）。統制軸
    ordering = 役割の切り替えは「ログアウト → ログイン → 確認」の順で行うべき手続だった。処理軸・構造軸は製品のコードの誤りでは
    ないので none。
generalization:
  level: general
  general_form: "同じ実行環境で利用者の立場を切り替えて確かめるとき、前の立場の認証状態が残ったまま操作し、観察した結果を別の立場のものと取り違える"
pattern: condition-not-propagated
discovery:
  perspective: [data_inspection]
  note: "教員画面に共有していない実行が見えたので漏洩を疑い、DB の所有者の列を読んだところ、学生として作ったはずの行が教員の所有だった。"
resolution:
  perspective: [explicit_contract]
  note: >-
    サイクルの検証段の規律として明文化した。ブラウザで役割を切り替える検証は、ログアウト → ログイン → 画面のアカウント名
    （または `/api/auth/me`）を確認してから操作する。役割ごとにブラウザのプロファイルを分けられるなら分ける。製品側に変更は無く、着地先のコードは規律が確認に使う
    既存のエンドポイント（`/api/auth/me`）である。
  landed_in:
    - docs/architecture/improvement_cycle.md §2.5
    - backend/api/routes/auth.py
    - docs/features/simulation_bridge_design.md §12.2
  verification:
    methods: [docker_e2e]
    unverified: []
related: [IK-0601]
view_of: []
history: []
---

## 課題

実機検証で、管理画面に教員としてログインした後に同じブラウザで学習画面を操作したため、同一オリジンの保存領域に教員のトークンが
残ったままだった。学生として作ったつもりの行は教員の所有になり、教員の画面に共有していない実行が見えて漏洩のように見えた。

## 発見の観点

漏洩を疑って DB の所有者の列を読み、操作した役割と記録の所有者が食い違っていることを見つけた。

## 解決の観点

製品の誤りではなく検証者の手続の問題なので、改善サイクルの検証段に、役割を切り替える前のログアウトと、操作前のアカウント名の
確認を規律として足した。ログアウトして学生でやり直した再実行で、所有者が正しく分かれることを確かめた。

## 一般化

立場を切り替えて確かめる検証では、前の立場の認証状態が残ることを前提に、操作の前に「いまの立場」を画面で確かめる。辞書の型は
`condition-not-propagated`（立場という条件が検証者の判断に渡っていない）。取り違えると、製品の正しい挙動が漏洩や誤動作に見える。
