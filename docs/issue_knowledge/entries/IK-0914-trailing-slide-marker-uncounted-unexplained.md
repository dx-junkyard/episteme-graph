---
id: IK-0914
title: "読み上げ欄の末尾（や連続）にある区切り（===）は空のスライドとして黙って捨てられ、教員には欄に見えている区切りの数と、インジケータの言う枚数が合わない理由が分からない（区切り 2 つ・読み上げ 2 枚／教材は区切り 2 つ・3 枚で「統合」）"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/lecture_slide_sync_design.md"
feature_context:
  realizing: "教員が教材欄と読み上げ欄の区切りを揃えて、トピックを複数枚に分けて保存する"
  layers: [lecture_studio, lecture_player]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
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
    処理軸 wording: backend/core/lecture.py の _split_marker_segments は空の区画を捨て（split_slides / count_slide_marker_segments が使う）、slide_split_notes は「教材欄は N 枚・読み上げ欄は M 枚」とだけ言う。末尾・先頭・連続の区切りは枚数にならないことを画面もマニュアルも言わない。挙動（空のスライドを作らない）は妥当で、説明だけが欠ける。
generalization:
  level: instance
  general_form: "入力の一部を規則で黙って無視するとき、無視したこと自体を言わず、入力の見た目と結果の数が食い違う"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: >-
    te-01 が t16 の読み上げ欄に区切りを足しても統合が解けず「末尾にもともと === が付いているのも、数に入っているのか分からない」と記した。末尾の区切りは第 1 周のハーネスの近似が残した砂場の残渣（harness_fixes_studio.md 第 2 周 H12）だが、教員が手で入れても同じことが起きる。run: uxsim/runs/c-astro-r25-studio-r2/20261023T020312Z-65722（seq9〜11）。
resolution:
  perspective: [explicit_contract]
  note: "slide_split_notes に教材欄・読み上げ欄の本文を渡し、先頭・末尾・連続の区切りがあれば「スライドにならず、枚数に数えていません」の事実文（SLIDE_MARKER_IGNORED_NOTE）を足す（判定は has_ignored_slide_markers。空のスライドを作らない分割の挙動は不変）。 第 3 周（2026-10-23）の追記: 読み上げ欄が空の分岐（spokenCount === 0）でも区切りの事実文（split.notes）を出す（IK-0946 の原稿スタジオ側）。"
  landed_in:
    - "backend/core/lecture.py"
    - "backend/api/routes/lecture_studio/scripts.py"
    - "frontend/public/js/admin-lecture-studio.js"
  verification:
    methods: [guardrail]
    unverified:
      - "インジケータの表示は実画面で見ていない"
related: [IK-0878, IK-0913]
view_of: []
history:
  - date: '2026-10-23'
    field: resolution
    from: 第 2 周の是正
    to: 第 3 周の追記
    reason: "第 3 周（2026-10-23）の追記: 読み上げ欄が空の分岐（spokenCount === 0）でも区切りの事実文（split.notes）を出す（IK-0946 の原稿スタジオ側）。"
---

## 課題

症状: 区切りの数を揃えたつもりでも統合が解けず、どの区切りが数に入らないのか分からない。

原因: 空の区画を捨てる規則を言わない。

## 解決の観点

解決（2026-10-23・第 25 周 面 D 第 2 波）: slide_split_notes に教材欄・読み上げ欄の本文を渡し、先頭・末尾・連続の区切りがあれば「スライドにならず、枚数に数えていません」の事実文（SLIDE_MARKER_IGNORED_NOTE）を足す（判定は has_ignored_slide_markers。空のスライドを作らない分割の挙動は不変）。

当初の是正案（記録）:

- slide_split_notes に、末尾・先頭・連続の区切りがあるときの事実文（「欄の先頭・末尾・連続した区切りはスライドになりません」）を足す（正本は core/lecture.py）。
- マニュアル（IK-0913 で足す節）に同じ規則を書く。

## 発見の観点

第 25 周 面 D 第 2 周（原稿スタジオ → 学生の受講）の通し受講で、ペルソナの観察と HTTP の生応答・製品コードを突き合わせた。

## 一般化

無視した入力は無視したと言う。確かめ方は、読み上げ欄の末尾に区切りを置いたプレビューで、その区切りが数えられないことをインジケータが言うのを見ること。

第 3 周の再演: 残存（preview-split は「先頭・末尾にある区切り…枚数に数えていません」を notes に返すが、admin-lecture-studio.js lsRenderSlideIndicatorEl（8171 行）は読み上げ欄が空の分岐（spokenCount === 0）で notes を描かず「表示 1 枚 / 読み上げ未入力」だけを出す。ok の分岐でも notes は autoPaginateSuppressed のときだけ。te-03 は見出し 1 行の t0 の末尾に区切りを入れ「末尾の区切りでは枚数が増えないのか、インジケータが更新されていないのか区別できない」と書いた。run: uxsim/runs/c-astro-r25-studio-r3/20261023T071024Z-19157 seq8・9。その末尾の区切りは学生の読む表示に生で届いた — IK-0946）。
