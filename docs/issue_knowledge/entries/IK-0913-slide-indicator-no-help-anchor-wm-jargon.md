---
id: IK-0913
title: "授業用ドラフトの分割インジケータ（「⚠ 表示 N / 読み上げ M — 1枚に統合して配信されます」）には「❓ 使い方」で引ける節が無く、教員が近くの部品を探ると負荷ラベルの節（見出しが内部語「WMレンズ」）に行き、統合の意味と揃え方に届かない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/manual/teacher/14-admin-lecture-studio.md"
feature_context:
  realizing: "教員が分割の警告の意味と直し方を使い方の案内で確かめる"
  layers: [lecture_studio, help_kb, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: admin-lecture-studio.js 2801 行の #ls-course-slides-indicator に data-ui-anchor が無く、ADMIN_UI_ANCHORS（backend/core/help_kb/admin_ui_anchors.py）にもインジケータの id が無い。統合の説明は manual の #insert-slide-marker-btn の節の中にしか無い。処理軸 wording（medium）: 近くで引ける節の見出し「スライドの負荷ラベル（WMレンズ）」（docs/manual/teacher/14-admin-lecture-studio.md 214 行）は内部の呼び名を含む。
generalization:
  level: instance
  general_form: "警告を出す部品に使い方の係留が無く、利用者が隣の部品の説明（内部の呼び名の見出し）に行き着く"
pattern: last-mile-missing
discovery:
  perspective: [reproduction]
  note: >-
    te-01 が警告の意味を ❓ で調べ、負荷ラベルの節に行き着いた。run: uxsim/runs/c-astro-r25-studio-r2/20261023T020312Z-65722（seq7）。インジケータ自体が事実文（notes）を描くようになったのは IK-0878 の是正。
resolution:
  perspective: [explicit_contract]
  note: "分割インジケータに data-ui-anchor=\"lecture-studio.slides-indicator\"、マニュアル teacher/14-admin-lecture-studio.md#slides-indicator（統合の規則・揃え方・先頭末尾連続の区切りを数えないこと）、ADMIN_UI_ANCHORS の 3 点を揃えた。負荷ラベルの節の見出しから内部の呼び名を外した。"
  landed_in:
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
  verification:
    methods: [guardrail]
    unverified:
      - "❓ でインジケータに合わせたときの表示は実画面で見ていない"
related: [IK-0878]
view_of: []
history: []
---

## 課題

症状: 警告の意味を ❓ で引けない。引ける近くの節は別の話題で、見出しが内部語。

原因: インジケータに係留が無い。

## 解決の観点

解決（2026-10-23・第 25 周 面 D 第 2 波）: 分割インジケータに data-ui-anchor="lecture-studio.slides-indicator"、マニュアル teacher/14-admin-lecture-studio.md#slides-indicator（統合の規則・揃え方・先頭末尾連続の区切りを数えないこと）、ADMIN_UI_ANCHORS の 3 点を揃えた。負荷ラベルの節の見出しから内部の呼び名を外した。

当初の是正案（記録）:

- インジケータに data-ui-anchor（例 `lecture-studio.slides-indicator`）を付け、マニュアルに 1 節（統合の規則・揃え方・末尾の区切りは数えないこと — IK-0914）を足し、ADMIN_UI_ANCHORS に登録する（3 点セット）。
- 負荷ラベル節の見出しから「WMレンズ」を外す（本文で触れるなら括弧の補足に）。

## 発見の観点

第 25 周 面 D 第 2 周（原稿スタジオ → 学生の受講）の通し受講で、ペルソナの観察と HTTP の生応答・製品コードを突き合わせた。

## 一般化

警告を出す部品には、その警告の意味の節を係留する。確かめ方は、❓ でインジケータにカーソルを合わせ、統合と揃え方の節が出ることを見ること。
