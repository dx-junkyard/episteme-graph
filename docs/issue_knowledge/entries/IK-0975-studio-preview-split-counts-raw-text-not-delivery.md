---
id: IK-0975
title: "原稿スタジオのスライド枚数のプレビュー（preview-split）は編集欄の生の本文を自動ページ分割にかけ、配信の正本 build_topic_slides が先に行う式・図の埋め込みの解決（式 1 個・図 1 個を一定の長さに数える）を通さないため、教員の見る「表示 3 枚」と学生に届く「スライド 4 / 4」が違い、スライドの境目も違う"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/lecture_slide_sync_design.md"
feature_context:
  realizing: "教員が原稿スタジオで見たスライドの分割を、そのまま学生の講義と読む表示に届ける"
  layers: [lecture_studio, lecture_player, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
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
    構造軸 aggregation: 分割の規則（auto_paginate_slides）は 1 つだが、その入力の作り方（$…$ を [[FORMULA_N]] に置き換え、
    ![[equation:…]] / ![[figure:…]] を解決してから数える）は backend/core/lecture.py::build_topic_slides（1264 行〜）だけが持ち、
    backend/api/routes/lecture_studio/scripts.py::preview_split_slides（600 行〜）は body.display_text をそのまま
    auto_paginate_slides に渡す。_display_length は [[FORMULA_N]] を一定の長さ・図を一定の長さに数えるので、生の `$^{13}$` を
    数文字と数えるプレビューは配信より短く見積もる。接続軸 information: プレビューに渡る本文に「式・図として数える」情報が
    無い。処理軸 none（どちらの計算もそれ自体は仕様どおり）。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "同じ分割の規則を通していても、規則にかける前の正規化を片方の経路だけが行うため、プレビューと配信の結果が食い違う"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    固定コース 6be229e1 の t0 を、共有教員 te-05 の原稿スタジオでは preview-split が「表示 3 枚（自動ページ分割）」と返し
    （3 枚目は付録だけ）、同じ保存済みの本文を学生 st-10 は audio-status / sequence / 教材の区画で 4 枚と受け取り、境目も違った
    （2 枚目が箇条書き 2 行だけ）。審判 S1・S2（学生の側の 3 者）は一致していたので、食い違いは教員のプレビューの側。
    run: uxsim/runs/c-astro-r25-studio-r4/20261024T233408Z-44969（seq4・6）/ uxsim/runs/c-astro-r25-studio-r4/20261024T234030Z-30759（seq7〜11）。
resolution:
  perspective: [canonical_source, carry_through]
  note: "分割にかける前の入力の正規化（$…$ → [[FORMULA_N]]・式と図の埋め込みの解決）を core/lecture.py::prepare_topic_slide_input に切り出し、build_topic_slides と preview-split（auto_paginate）の両方がそれを通す。プレビューは optional course_id / topic_id で保存済みトピックの文脈（evidence_links / content_blocks）を借り、編集欄の未保存の本文を重ねた疑似トピックを作る（閲覧できなければ文脈なし）。原稿スタジオのコーストピックのインジケータが番地を送る。"
  landed_in:
    - "backend/core/lecture.py"
    - "backend/api/routes/lecture_studio/scripts.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/tests/test_lecture_studio_round25_r4.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "プレビューと配信が同じ分割の関数を呼ぶだけでなく、分割にかける前の材料の作り方も 1 関数にして両方が通る形を採った。固有の差分: プレビューは未保存の本文なので、保存済みトピックの文脈だけを借りて重ねる。"
  verification:
    methods: [guardrail]
    unverified:
      - "固定コース 6be229e1 の t0 で教員のプレビューと学生の枚数・境目が一致するかは実機で見ていない（第 5 周 te-04 と周またぎの X1）"
      - "チャンク編集のプレビュー（auto_paginate なし）は対象外で従来どおり"
related: [IK-0878, IK-0914, IK-0944, IK-0946]
view_of: []
history: []
---

## 課題

症状: 教員は原稿スタジオで「表示 3 枚 / 読み上げ 3 区切り ✓」と見て保存・確認したのに、学生の講義は 4 枚で、境目の位置も違う。
教員が音声を作る段（build_topic_slides を通る）とも境目がずれる。

原因: プレビューは生の本文を分割にかけ、配信は式・図を一定の長さに数える形へ直してから分割にかける。分割の規則は共有しているが、
規則にかける前の正規化が配信の経路にしか無い。

## 発見の観点

通し受講（reproduction）で教員と学生が同じトピックの枚数を言い、プレビューと配信の入力の作り方を辿った（trace_walk）。

## 解決の観点

第 25 周 第 4 波で是正（2026-10-25）。分割にかける前の入力の正規化（$…$ → [[FORMULA_N]]・式と図の埋め込みの解決）を core/lecture.py::prepare_topic_slide_input に切り出し、build_topic_slides と preview-split（auto_paginate）の両方がそれを通す。

## 一般化

「プレビューと配信が同じ関数を使う」だけでは足りず、その関数にかける前の材料の作り方まで同じでなければ一致しない。確かめ方: 式・図を
多く含むトピックで、プレビューの枚数と境目を配信の sequence と突き合わせる。

第 5 周の再演（2026-10-27）: **GONE**。固定コース 6be229e1 の全 20 トピックで、所有者 te-01 の preview-split（course_id / topic_id 付き）の `slides` の枚数と学生 st-09 の講義の `total_slides` が一致した（読み取りの API で突き合わせ）。番地を送らない旧い呼び方では t6 / t7 / t8 / t10 の 4 トピックが 1 枚ずつ少なく、是正の効き目は番地を送る経路だけに出る（原稿スタジオの画面は送る）。t16 は教材欄 3 / 読み上げ 2 の不一致で「1 枚に統合」— プレビューの `slides` も 1 枚・学生も 1 / 1 で一致。周またぎの審判 `campaign_studio_cross.jsonl` の 8 組の食い違いは、審判がプレビューの `display_segment_count`（統合前の教材欄の枚数）を配信の枚数として比べた偽所見（harness_fixes_studio.md H27）。run: uxsim/runs/c-astro-r25-studio-r5/20261027T070623Z-23494 seq11・20、uxsim/runs/c-astro-r25-studio-r5/20261027T072111Z-9659 seq8。
