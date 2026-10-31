---
id: IK-0950
title: "editor 共有のコースで AI の書き換えを試すと、知らせが「提案を反映しました。保存すると確定します。」の定型文に「結果は保存されません」を足して 1 行に並べ自己矛盾し、試した案を所有者へ渡す手段（写す・送る）が無く、原稿スタジオの見出しにも共有・保存不可の印が無い（保存不可はトピックを開いて初めて出る）"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "共有された教員が原稿スタジオで書き換えを試し、保存できないことを前提に案を所有者へ依頼する"
  layers: [lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [condition]
    governance: [none]
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
    処理軸 wording: frontend/public/js/admin-lecture-studio.js 9471〜9473 行が data.notice の無いとき既定の「提案を反映しました。保存すると確定します。」を置き、
    その後ろに save_notice（「…結果は保存されません。変更は所有者に依頼してください。」— topics.py 917 行）を足す。接続軸 condition（medium）: can_save が偽で
    あることは course-structure にあるのに、知らせの組み立てとコースの見出し（lsRenderCourseTree 1060 行〜）が読まない。保存不可の事実はトピックの区画にだけ
    出る（IK-0908 の是正の範囲）。所有者への依頼は文で促すが、案を持ち出す操作が無い。構造軸 none。統制軸 none（medium: 依頼の手段の欠落は機能の欠け）。
generalization:
  level: repo_pattern
  general_form: "権限で結果が変わる操作の知らせを、既定の成功文に例外の文を継ぎ足して作るため、権限の無い利用者には成功と不成立が同じ行で並ぶ"
pattern: permission-and-affordance-asymmetric
discovery:
  perspective: [reproduction]
  note: >-
    te-04 が editor 共有の 6be229e1 で書き換えると「提案を反映しました。保存すると確定します。 このコースのトピックの保存は…結果は保存されません」と出て
    「前半と後半が食い違って見える」、保存を押して「試した案を所有者に渡す手段が見当たらない」と書いた。スタジオを開いた直後は共有の表示が無かった。
    run: uxsim/runs/c-astro-r25-studio-r3/20261023T071920Z-61773（seq12・15・16）。
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: "can_save が偽なら書き換えの知らせを REWRITE_PROPOSAL_UNSAVABLE_NOTICE に置き換え（継ぎ足さない・「確定」を言わない）、course-structure に share_mark（COURSE_ROLE_LABELS[editor]）を返して見出しに出す。案の持ち出しは［案をコピー］（クリップボードのみ・DB 非変更、アンカー lecture-studio.rewrite-copy・マニュアル・ADMIN_UI_ANCHORS の 3 点）。所有者への通知に案を添える案は教材本文を通知に載せる判断が要るので採らない。"
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "frontend/public/admin.html"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
  verification:
    methods: [guardrail]
    unverified:
      - "クリップボードへの書き込みは実ブラウザで見ていない"
related: [IK-0908, IK-0911]
view_of: []
history: []
---

## 課題

症状: 共有された教員の書き換えの知らせが「保存すると確定します」と「保存されません」を並べる。案を所有者に渡せない。スタジオを開いた時点では共有の
コースだと分からない。

原因: 知らせは既定の成功文に権限の文を継ぎ足して作り、見出しは保存の可否を読まない。

## 発見の観点

第 2 周の IK-0908 の是正後の画面を、editor の教員で端から歩いた。保存不可の理由そのものは出るようになった（IK-0908 は解消）。

## 解決の観点

第 25 周 面 D 第 3 周の是正（2026-10-23）で解決。can_save が偽なら書き換えの知らせを REWRITE_PROPOSAL_UNSAVABLE_NOTICE に置き換え（継ぎ足さない・「確定」を言わない）、course-structure に share_mark（COURSE_ROLE_LABELS[editor]）を返して見出しに出す。（以下は起票時の見立て）can_save を知らせの組み立てと見出しでも読む。案の持ち出しは最小の手段（コピー）から。

## 一般化

権限の例外は既定文の上書きで言う（継ぎ足さない）。確かめ方は、editor で書き換えて、知らせに「確定」の語が出ないことを見ること。

第 4 周の再演: 知らせの自己矛盾・見出しの共有の印・保存を押したときの理由は GONE（共有教員 te-05 の知らせは「案は保存されません。［案をコピー］で写して、所有者に渡せます」に一本化され、見出し直下に〔共有（編集可・トピックの保存は所有者）〕が出た）。残りは持ち出しの中身と渡し先 — 写す中身に申し送り・確認問題が入らず、知らせが何を写したか・所有者が誰かを言わない（IK-0978 に分けた）。run: uxsim/runs/c-astro-r25-studio-r4/20261024T233408Z-44969 seq3・6〜8。

第 4 波で是正（2026-10-25）: 残りの持ち出しの中身と渡し先は IK-0978 で是正した — ［案をコピー］が確認問題と申し送りも区画見出し付きで写し、知らせが写した区画名と「所有者（◯◯）に渡してください」を言う。見出しの共有の印の隣に「このコースの所有者: ◯◯」（教員間だけ・オーナー判断候補 1 を推奨どおり実装）。ガードレール `backend/tests/test_lecture_studio_round25_r4.py`。

第 5 周の再演（2026-10-27）: **GONE**（持ち出しまで）。te-03 は書き換え → 保存不可の理由 → ［案をコピー］（中身 5 区画と宛先）まで 1 操作ずつ読めた。宛先の値がログイン ID である件は IK-1007。共有教員に音声生成が押せる非対称は今周も行為が無く未確認。run: uxsim/runs/c-astro-r25-studio-r5/20261027T071438Z-61813。
