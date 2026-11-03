---
id: IK-0978
title: "編集を共有された教員が［案をコピー］で写す中身は教材欄・読み上げ原稿・注意点だけで、AI の申し送り（「偽の式を外すかご判断ください」）と確認問題は写らず、知らせも「案をクリップボードに写しました。所有者に渡してください。」だけで何を写したか・誰（所有者）に渡すかを言わない — コース一覧にも原稿スタジオにも所有者の名前が無い"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "保存できない共有教員が AI の書き換えで作った案と判断材料を、所有者に渡して反映を依頼する"
  layers: [lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: frontend/public/js/admin-lecture-studio.js::lsCopyRewriteProposal（9538 行〜）は編集欄 3 つ
    （教材・読み上げ・注意点）の値だけを写す。申し送り（handoff_notes）は書き換えの応答から知らせの 1 行に出るだけで
    （9516〜9524 行）どこにも保持されず、確認問題も写さない。所有者の名前は course-structure（core/lecture_studio_state.py の
    share_mark は役割の語だけ）にも管理のコース一覧にも無い。処理軸 wording: 知らせが写した中身と渡し先を言わない。
    構造軸 none。統制軸 none（medium: 依頼の手段そのものはオーナー判断で「クリップボードのみ」に留めた — IK-0950）。
generalization:
  level: repo_pattern
  general_form: "人に渡すための持ち出しが、その場にしか出ない判断材料と渡し先を落とし、持ち出した後に誰に何を渡すかを利用者の記憶に頼る"
pattern: last-mile-missing
discovery:
  perspective: [reproduction]
  note: >-
    共有教員 te-05 が固定コース 6be229e1 の t0 で書き換えを試し、知らせの「申し送り: …教材から外すかどうかご判断ください」を読んだあと
    ［案をコピー］を押し、「何が写ったのか、所有者が誰なのかが画面に出ていない。一覧にも所有者の名前が無かった」と記した。同名の
    共有コースが 2 行並び、どちらが誰のコースかも見分けられなかった。
    run: uxsim/runs/c-astro-r25-studio-r4/20261024T233408Z-44969（seq2・6・8）。
resolution:
  perspective: [carry_through, explicit_contract]
  note: "［案をコピー］が教材欄・読み上げ原稿・注意点に加えて確認問題と申し送り（書き換えの応答の handoff_notes を案と一緒に lsState.rewriteHandoff に保持）を区画見出し付きで写す。course-structure は保存できない教員にだけ owner_display_name / owner_notice（「このコースの所有者: ◯◯」）/ proposal_copy_notice（{sections} を画面が埋める・渡す相手は所有者の表示名）を返す。オーナー判断候補 1 は推奨どおり（教員間だけ見せる）で、disclosure_axes の course_materials の name_disclosure に 1 行足した。"
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "backend/core/disclosure_axes.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "backend/tests/test_lecture_studio_round25_r4.py"
  verification:
    methods: [guardrail]
    unverified:
      - "共有教員の画面で所有者名と写した中身が出るかは実機で見ていない（第 5 周 te-03）"
      - "同名の共有コースが並ぶ一覧（IK-0407）には所有者名を足していない"
related: [IK-0950, IK-0407, IK-0948]
view_of: []
history: []
---

## 課題

症状: 案をコピーしても、AI が所有者の判断を求めた申し送りと確認問題は写らず、知らせは写した中身も渡し先も言わない。所有者の名前は
画面のどこにも無く、同名の共有コースが並ぶとどちらの所有者に渡すかも分からない。

原因: 持ち出しが編集欄の 3 つだけを読み、申し送りは知らせの一行にしか無い。所有者の名前を共有教員の画面へ運ぶ値が無い。

## 発見の観点

共有教員の通し受講（reproduction）で、書き換え → 保存の試み → コピーの順に歩いたときに記された。

## 解決の観点

第 25 周 第 4 波で是正（2026-10-25）。［案をコピー］が教材欄・読み上げ原稿・注意点に加えて確認問題と申し送り（書き換えの応答の handoff_notes を案と一緒に lsState.rewriteHandoff に保持）を区画見出し付きで写す。

## 一般化

人に渡す操作は「何を・誰に」を言い切る。確かめ方: 共有教員で書き換え → コピーし、写した文に申し送りと確認問題が入り、知らせに
所有者の名前が出ることを見る。

第 5 周の再演（2026-10-27）: **中身と宛先は GONE**。共有教員 te-03 の［案をコピー］の知らせは「教材欄・読み上げ原稿・注意点・確認問題・申し送りをクリップボードに写しました。所有者（…）に渡してください。」、見出しには「このコースの所有者: …」。ただし出た「名前」はログイン ID（users.display_name がログインの照合列を兼ねる）— IK-1007 に分けた。run: uxsim/runs/c-astro-r25-studio-r5/20261027T071438Z-61813 seq3・6・8。

第 7 周の再演（第 25 周・面 D 第 7 周、2026-10-28）: **GONE 継続**。根拠ありの t0 で共有教員 te-05 が書き換え →［案をコピー］すると、知らせは「教材欄・読み上げ原稿・注意点・確認問題・申し送りをクリップボードに写しました。所有者（水野 早紀）に渡してください。」（中身と宛先とも）。run: uxsim/runs/c-astro-r25-studio-r7/20261027T171857Z-53968 seq3・8。

第 8 周の再演（2026-10-28）: **GONE 継続**。te-05 の［案をコピー］は 5 区画を写し「所有者（水野 早紀）に渡してください」。一方、同じ画面の音声の依頼の出口は宛先も中身も持たない → IK-1071。run: uxsim/runs/c-astro-r25-studio-r8/20261027T223730Z-95543 seq8。
