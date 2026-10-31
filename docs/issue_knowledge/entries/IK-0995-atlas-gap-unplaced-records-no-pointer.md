---
id: IK-0995
title: "候補の区画が「置けなかった主題の記録はありますが…候補にしていません」と言うのに、その主題が何でどこで読めるかを言わない — 1 本だけの主題を教員が自分で見て項目にするか決める入口が無い"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/category_gap_candidates_design.md §4.1"
feature_context:
  realizing: "教員が候補ゼロの理由を読み、反復しない主題を自分で見て地図の項目にするか判断する"
  layers: [category_gaps, knowledge_landscape, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 target: gap-candidates の empty_notes は『置けなかった主題の記録はあります』と記録の存在を言うが、記録の所在（教材管理の［位置づけ（分野マップ）…］モーダルの unplaced の行）を指さない。設計 §4.1 は 1 論文の画面に地図を直すボタンを置かない裁定だが、記録を読みに行く道案内まで禁じてはいない。構造・処理・統制軸 none。
generalization:
  level: repo_pattern
  general_form: "記録の存在だけを言い、記録を読める場所を指さないので、利用者は存在を知っても確かめられない"
pattern: last-mile-missing
discovery:
  perspective: [reproduction]
  note: "5 周続けて候補ゼロの砂場で、教員が『その主題が何か（どの論文の何か）は区画から辿れない』と記した。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T071308Z-52116 seq 9。"
resolution:
  perspective: [carry_through]
  note: >-
    候補ゼロの理由 EMPTY_BELOW_THRESHOLD に記録の所在（教材管理の［⋯］→［位置づけ（分野マップ）…］）を足し、位置づけモーダルの「配置できなかった分野」の行に gap_topics（記録した主題の名前・件数なし。routes/landscape.py _gap_signal_topics）を出す。§4.1 の裁定どおり 1 論文の画面に地図を直すボタンは置かない。
  landed_in:
    - "backend/core/atlas_review_display.py"
    - "backend/api/routes/landscape.py"
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/17-admin-atlas.md"
    - "backend/tests/test_atlas_review_round25_r5.py"
  verification:
    methods: [guardrail]
    unverified:
      - "分野の地図タブから論文の題名を直接列挙することはしていない（所在の一文と論文側の名前で辿る）"
      - "配置のある分野で記録された主題（unplaced に出ない分野）は位置づけモーダルに出ない"
related: [IK-0943, IK-0851, IK-0846]
view_of: []
history: []
---

## 課題

症状: 候補ゼロの区画が「置けなかった主題の記録はありますが、2 本以上の論文で同じ主題が繰り返されていないため、候補にしていません」と言うが、
主題も論文も行き先も言わない。

原因: 空の理由の文が記録の所在を指さない。

## 発見の観点

通し受講で教員が記した（reproduction）。

## 解決の観点

第 5 波（2026-10-27）で解決。記録の所在を言い、論文の画面で主題を名前で読める。

## 一般化

存在を言う事実文は、その対象を読める場所を添える。確かめ方: 候補ゼロの区画から 1 操作で置けなかった主題に着けるかを見る。



## 第 6 周の再演（第 25 周・面 C 第 6 周、2026-10-27・campaign c-astro-r25-atlas-r6）

GONE。候補ゼロの区画に「各論文の置けなかった主題は、教材管理で論文の行の［⋯］→［位置づけ（分野マップ）…］を開くと読めます。」が出て、te-03 は行き止まりの理由と所在を 1 画面で読んだ。残り（軽）: この画面から教材管理への直接の導線（リンク）は無い（文で場所を示すだけ）。位置づけモーダルの「置けなかった主題: …」は第 6 周では開いていない（未観測）。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T122226Z-3516 seq 2 / 3。

## 第 7 周の再演（第 25 周・面 C 第 7 周、2026-10-28・campaign c-astro-r25-atlas-r7）

STILL（軽・残り）。所在の一文は読めたが、教材管理への直接の導線は無い（te-04 が「ここからそこへ飛ぶボタンは無い」と記した）。run: uxsim/runs/c-astro-r25-atlas-r7/20261027T171357Z-51096 seq 3。
