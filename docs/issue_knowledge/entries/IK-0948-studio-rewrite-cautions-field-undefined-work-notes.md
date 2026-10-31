---
id: IK-0948
title: "原稿スタジオの AI 書き換えのプロンプトは「cautions: [注意点]」と書くだけで欄の用途を言わず、コース内容生成のプロンプトが持つ規則（内部の番地を書かない・根拠の有無の注記を食い違わせない）も持たないため、AI が教員宛ての作業メモ（「内部ラベルを外した」「evidence_links に内容が無い」「教員が確認してほしい」・式の内部 ID・claim の id）を「誤解しやすい点や適用条件」の欄に書き込む"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員が原稿スタジオで AI に授業用ドラフトの書き換えを頼み、注意点の欄に学習上の注意だけが入った案を受け取る"
  layers: [lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 meaning: backend/api/routes/lecture_studio/topics.py _COURSE_TOPIC_DRAFT_PROMPT（700〜745 行）は形式に "cautions": ["注意点"] とあるだけで、画面の
    placeholder「誤解しやすい点や適用条件を1行ずつ入力」（admin-lecture-studio.js 2876 行）の意味が LLM に渡らない。AI は欄を「教員への申し送り」と解釈した。
    構造軸 aggregation: 同じ授業用ドラフトを作る core/course_content_builder.py のプロンプト（4331〜4336 行）は「注意点・本文説明で内部の番地を書かない」
    「根拠の有無の注記を grounding_facts と食い違わせない」を持つが、書き換えのプロンプトには無い（2 つのプロンプトに規則が分かれている）。処理軸 none
    （medium: 欄の値は検査されず保存される）。統制軸 none。学習画面は topic の cautions を描かない（app.js に表示経路なし）ので学生への漏れは無い。
generalization:
  level: repo_pattern
  general_form: "同じ成果物を作る 2 つの生成指示のうち片方だけが欄の用途と禁止事項を持つため、もう片方の経路では欄に宛先の違う文が入る"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction, inventory]
  note: >-
    te-04 が自分のコースで書き換えると注意点に「内部ラベル（eq:modified_dcf など）は…外した」「eq_ne_density は evidence_links に内容が無い…教員が確認して補って
    ほしい」が入り、共有コースの t0 の注意点にも過去の書き換えの「claim の id が並んでいますが…教員が本文を確認し…」が残っていた。run:
    uxsim/runs/c-astro-r25-studio-r3/20261023T071920Z-61773（seq8・13・15）。
resolution:
  perspective: [canonical_source, representation_change]
  note: "注意点の欄の定義と禁止事項を course_content_builder.TOPIC_DRAFT_FIELD_RULES の 1 つの規則表にし、コース内容生成と AI 書き換えの両方の生成指示が読む。教員への申し送りは TOPIC_DRAFT_HANDOFF_RULE で応答の別キー handoff_notes に分け、モデルが注意点に混ぜた作業メモ（内部 ID・evidence_links 等の語）は split_cautions_and_handoff が決定論で申し送りへ移す。画面は申し送りを書き換えの知らせ「申し送り:」に出し、欄には入れない。"
  landed_in:
    - "backend/core/course_content_builder.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/tests/test_lecture_studio_round25_r3.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実モデルの書き換えで注意点から作業メモが消えるかは実機で見ていない（第 4 周 te-04）"
      - "過去の書き換えで注意点に残った作業メモは掃除していない（次の書き換え・教員の編集で消える）"
related: [IK-0909, IK-0873]
view_of: []
history: []
---

## 課題

症状: 書き換えの案の注意点に、AI の作業メモと内部 ID が並ぶ。教員は学生に見えるのかを疑い、消して回る必要がある。

原因: 書き換えの指示が注意点の用途を言わず、コース内容生成の指示にある規則を持たない。

## 発見の観点

自分のコースと共有コースで書き換えを 2 回走らせ、注意点の欄を読んだ。コース内容生成のプロンプトと並べた。

## 解決の観点

第 25 周 面 D 第 3 周の是正（2026-10-23）で解決。注意点の欄の定義と禁止事項を course_content_builder.TOPIC_DRAFT_FIELD_RULES の 1 つの規則表にし、コース内容生成と AI 書き換えの両方の生成指示が読む。（以下は起票時の見立て）欄の定義と禁止事項を 1 つの規則表にして両方の生成指示が読む。申し送りは別の出口にする。

## 一般化

自由記述の欄を AI に埋めさせるときは、欄の宛先を指示に入れる。確かめ方は、根拠の材料が欠けるトピックで書き換え、注意点に作業メモ・内部 ID が
入らず、申し送りが知らせに出ることを見ること。

第 4 周の再演: 書き換えの経路は GONE（共有教員 te-05 の書き換えで AI の作業メモが「申し送り」として知らせに分かれ、注意点は学習者向けの 3 項目になった）。保存済みの注意点は残存（予定どおり — 書き換え・再生成の前の旧い注意点）: 6be229e1 の t0 の書き換え前は 5 項目すべてが教員宛て（「claim の id が並んでいますが…」）、0d70b5d0 の t0・t3 も「原文とは未照合なので使っていません」「このトピックには図が供給されていないため、図は使っていません」など教員宛てで、後者は図を採用した後も残った（生成時のメモが編集で古くなっても更新されない）。run: uxsim/runs/c-astro-r25-studio-r4/20261024T233408Z-44969 seq4・6 / uxsim/runs/c-astro-r25-studio-r4/20261024T232812Z-98850 seq6・14・18。是正案: 保存済みの注意点に split_cautions_and_handoff の決定論の判定をかけた結果を、原稿スタジオで「申し送りに移せる項目」の事実文として出す（学生への表示は教員が保存するまで変えない）。

第 5 周の再演（2026-10-27）: 書き換えの作業メモは申し送りへ分かれたまま（GONE 継続）。保存済みの注意点は教員宛てのまま（STILL・予定どおり）— t16 の注意点は eq_blk_* / claim_span_* を挙げ、付け直した付録と食い違う。書き換えの申し送りが注意点を取り下げた根拠が画面に無い件は IK-1011。run: uxsim/runs/c-astro-r25-studio-r5/20261027T070623Z-23494 seq6・21。

第 6 周の再演（2026-10-27）: 書き換えの出力は GONE 継続（te-04 の申し送りは根拠の表示の有無を言う）。保存済みの注意点は STILL: t16「eq_blk_003_0065 と eq_blk_003_0066 は…」「Claim（claim_span_*）は…」、t17「図 d687f713-…」、t0「eq_eqcand_inline_blk_001_0005_110_d4930cfb」が教員に出る（所有者が書き換えて保存するまで残る）。run: uxsim/runs/c-astro-r25-studio-r6/20261027T122226Z-3522 seq6・12、uxsim/runs/c-astro-r25-studio-r6/20261027T122747Z-6889 seq4。

第 7 周の再演（2026-10-28）: 書き換えは GONE 継続（te-05 の案で注意点から内部 ID が消え、申し送りへ）。保存済みの注意点は STILL: t16「eq_blk_003_0065…」「claim_span_*」、t17「図 d687f713-…」、t0「eq_eqcand_inline_blk_001_0005_110_d4930cfb」。run: uxsim/runs/c-astro-r25-studio-r7/20261027T171357Z-51101 seq6・11、uxsim/runs/c-astro-r25-studio-r7/20261027T171857Z-53968 seq4・6。

第 8 周の再演（2026-10-28）: **保存済みの側は STILL / 書き換えの案の側は GONE 継続**。te-01 の t16・t17、te-05 の t0 の保存済み注意点に eq_blk_003_0065・claim_span_*・図の UUID（A 所見 4f5f2001…）。te-05 の AI 書き換えの案では注意点から内部 ID が消えた。run: uxsim/runs/c-astro-r25-studio-r8/20261027T223243Z-92722 seq6・12、uxsim/runs/c-astro-r25-studio-r8/20261027T223730Z-95543 seq4・6。

## 第 9 周（第 25 周・面 D 第 9 周、2026-10-31・judge_studio_r9）

**保存済みの側が STILL**（書き換えの案の側は GONE 継続 — te-05 の書き換えの案の注意点に内部 ID なし・申し送りへ分離）。te-01 の t16 の保存済みの注意点に eq_blk_003_0065 / eq_6 / claim_span_* がそのまま（A 所見）。是正の前に保存された物を見分ける段が無いこと（本文の作業メモを含む）は IK-1081 に分けた。run: uxsim/runs/c-astro-r25-studio-r9/20261031T015111Z-37232 seq6、uxsim/runs/c-astro-r25-studio-r9/20261031T003210Z-30686 seq4・6。
