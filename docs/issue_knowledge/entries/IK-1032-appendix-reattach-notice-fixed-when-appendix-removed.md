---
id: IK-1032
title: "原稿スタジオの［付録を付け直す］が、いまの根拠から足す式が無いため付録「この節で使う数式」を見出しごと取り除いたのに、知らせは固定文「付録を付け直して編集欄に入れました。保存すると確定します。」のまま — 教員は付録が消えたのか空になったのか分からず、そのまま保存して受講者に届ける"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員が保存済みトピックの古い付録を、いまの根拠から作り直して確かめてから保存する"
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
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording: backend/core/lecture_studio_state.py の APPENDIX_REATTACH_APPLIED_NOTICE は 1 つの固定文で、付け直しの結果
    （付録を差し替えた / 付録を取り除いた / 変化なし）を区別しない。第 5 波の IK-0872 ② の是正で「足す式が無ければ付録の見出しごと出ない」
    が起こりうるようになったが、知らせはその分岐を受け取っていない。接続軸 information: reattach の応答は changed: true と本文だけを返し、
    「取り除いた」という事実を持たないので画面が言い分けられない。構造軸 none（本文の表現は足りている）。統制軸 none（保存前の確認・届き方の告知は働いた）。
generalization:
  level: repo_pattern
  general_form: "操作の結果に複数の分岐（置き換え・取り除き・変化なし）があるのに、完了の知らせが 1 つの固定文で、利用者が何が起きたかを画面から確かめられない"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    教員 te-01 が t16 で［付録を付け直す］を押すと、編集欄の末尾の「### この節で使う数式 / ![[equation:eq_blk_003_0074]]」が消えて何も入らず、
    「付録を付け直して編集欄に入れました」と出た。頭脳は「何か新しく入ったようには見えず、付録が空になったのかどうかは分からない」と記して保存した。
    応答の生値（changed: true・本文の末尾に付録なし・notice は固定文）で確かめた。run: uxsim/runs/c-astro-r25-studio-r6/20261027T122226Z-3522 seq9・10。
resolution:
  perspective: [first_class_state, canonical_source]
  note: >-
    course_content_builder.reattach_generated_appendix が結果の語 outcome（replaced / removed / added / unchanged — 語彙の正本は
    lecture_studio_state.APPENDIX_REATTACH_OUTCOMES）を返し、知らせは lecture_studio_state.appendix_reattach_notice の 1 箇所で
    分岐ごとに言う（取り除いたときは「いまの根拠からは付録に載せる式・図が無いため、付録を外しました。保存すると確定します。」）。
    reattach の応答は outcome と notice を返し、原稿スタジオはサーバの文を描く。付録を取り除く挙動そのものは変えない。
  landed_in:
    - "backend/core/course_content_builder.py"
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "backend/tests/test_lecture_studio_round25_r6.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "知らせの文はサーバの 1 箇所で結果の語から決め、画面は語と文を受け取るだけ。固有の差分: 分岐の語を応答に一級で持たせた（changed: true だけでは言い分けられない）。"
  verification:
    methods: [guardrail]
    unverified:
      - "固定コースの t16 で付け直し、知らせが「外しました」と言うかは実機で見ていない"
related: [IK-0872, IK-0880]
view_of: []
history: []
---

## 課題

症状: 付け直しで付録が丸ごと消えたのに「入れました」と言われ、教員は結果を確かめられないまま保存した。

原因: 付け直しの結果の分岐（取り除き）を知らせが持たない。IK-0872 ② の是正（名前の付かない式は付録に足さない）の後に生じた分岐。

## 発見の観点

教員の通し受講（reproduction）の戸惑いを、reattach の応答の生値と照らした（data_inspection）。

## 解決の観点

第 6 波（2026-10-27）で是正。応答に結果の語（outcome）を持たせ、知らせをサーバの 1 箇所で分岐ごとに言う。付録を取り除く挙動そのものは変えない（足す式が無いなら付録を出さないのは IK-0872 の是正の意図どおり）。

## 一般化

完了の知らせは結果の分岐ごとに事実を言う（wording-mismatch）。確かめ方: 足す式が無いトピックで付け直し、知らせが「取り除いた」と言うことを見る。

第 7 周の再演（2026-10-28）: **未観察**。te-01 の t16 には付録も［付録を付け直す］も出ず（付録の古いトピックの列挙にも t16 は無い）、付け直しの行為の前提が成立しなかった。所有者の［付録をまとめて付け直す］は画面に出たが場面に押す手が無い。審判の読み取り（付け直しの API は DB 非変更だが POST）は実行を見送った。run: uxsim/runs/c-astro-r25-studio-r7/20261027T171357Z-51101 seq4・8。

第 8 周（2026-10-28）: **未観察**（t16 / t17 は付録が古い一覧に無く、［付録を付け直す］も［付録をまとめて付け直す］も押されていない）。

## 第 9 周（第 25 周・面 D 第 9 周、2026-10-31・judge_studio_r9）

1 トピックの経路は未観察。まとめての経路（［付録をまとめて付け直す］）は outcome を返しながら固定文で全件を言う — IK-1080 に分けた。run: uxsim/runs/c-astro-r25-studio-r9/20261031T012109Z-34801 seq5。
