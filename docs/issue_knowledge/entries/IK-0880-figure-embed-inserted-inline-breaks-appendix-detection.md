---
id: IK-0880
title: "教材図スタジオの採用は図の埋め込みを改行なしでカーソル位置に差し込むため、欄の末尾（付録「この節で使う数式」の最後の式）に入ると「![[equation:…]]![[figure:…]]」と同じ行に連結され、付録の判定（strip_generated_reference_appendix）が外れて次の生成で古い付録が残る"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/teaching_figure_studio_design.md"
feature_context:
  realizing: "教員が AI で作った説明図を授業用ドラフトに挿入し、後で AI 書き換え・再生成しても図と付録が正しく保たれる"
  layers: [teaching_figures, lecture_studio, course_builder]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [contract]
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
    処理軸 input_handling: frontend/public/js/admin-lecture-studio.js の lsApplyFigureInsert（2927 行）→ lsInsertTextAtCursor（8055 行）は
    埋め込みの前後に空行を足さず、教材欄を一度もクリックしていなければカーソルは末尾（2890〜2897 行の既定）。接続軸 contract:
    backend/core/course_content_builder.py の strip_generated_reference_appendix は付録の各行が「- 説明」か埋め込み 1 つだけの行であることを
    前提にするが、連結行は _GENERATED_APPENDIX_LINE_RE に当たらず付録全体を外さない（PYTHONPATH=../src で関数を直接呼んで確かめた:
    連結行では付録が残り、改行で分けると外れる）。採用時の linked_figure_ids による復元は効くので図は消えないが、古い付録が残り、
    図は付録の中に置かれる。構造・統制 none。
generalization:
  level: repo_pattern
  general_form: "後段が行の形で認識するブロックへ、前段が区切りなしで別の要素を差し込み、後段の認識が外れる"
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員ペルソナが採用後の編集欄で図の埋め込みが式の埋め込みの直後に改行なしで連結されているのを記した。挿入の実装と付録の判定を辿り、
    関数を直接呼んで確かめた。挿入位置が末尾だったのはハーネスの近似（カーソルを置かない）だが、製品もクリックしなければ末尾になる。
    run: uxsim/runs/c-astro-r25-studio-r1/20261022T123418Z-31804（seq19・20）。
resolution:
  perspective: [explicit_contract, single_point_fix]
  note: "図の埋め込みは段落として差し込み（lsAsParagraph）、カーソルが決定論付録の中・後ろなら付録の見出しの直前へ寄せる（lsMoveCursorBeforeGeneratedAppendix。見出しの文字列は course-structure の generated_appendix_headings でサーバから受け、JS にミラーしない）。strip_generated_reference_appendix は 1 行に埋め込みが複数ある行も付録の行と認め、その行の図は付録と一緒に消さず本文の段落として残す。"
  landed_in:
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/core/course_content_builder.py"
    - "backend/api/routes/lecture_studio/topics.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "根拠の状態・トピックの論文・音声の有無をサーバの 1 箇所（core/lecture_studio_state と course-structure）で決め、画面は返った値と文を描くだけにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "JS の挿入位置の挙動は node が無い環境で評価テストを走らせていない（静的検査のみ）"
related: [IK-0872, IK-0427]
view_of: []
history: []
---

## 課題

症状: 図を採用して保存すると、付録の最後の式の埋め込みと図の埋め込みが同じ行に連結される。説明したい節の近くではなく付録の中に図が
入る。原因: 挿入が区切りを足さず、付録の判定が行の形に依る。

## 発見の観点

ペルソナの通し受講（reproduction）と挿入・判定の追跡（trace_walk）。

## 解決の観点

解決済み（2026-10-23）。段落として差し込み、付録の外へ寄せる。

## 一般化

行の形を前提にする認識と、区切りなしの挿入の衝突。確かめ方は、欄の末尾で図を採用し、保存後に再生成して付録が 1 つだけになり、
図が付録の外に残ることを見ること。

第 2 周の再演: 残存（第 1 周に連結された `![[equation:…]]![[figure:cc85eadb…]]` が保存済みのまま t0 に残り、学生の教材に「J=3〔図〕…」と同じ行で届く。書き換えで付け直すと図は独立した段落に残った = strip の是正は効く。新しい採用の挿入は API runner の近似で製品 JS は通っていない。run: uxsim/runs/c-astro-r25-studio-r2/20261023T020907Z-69975 seq6・20261023T021041Z-70961 seq11）。

第 2 波で是正（2026-10-23）: 保存済みの連結は読み時に直さず、IK-0872 と同じ [付録を付け直す] が付録の行に連結された図を本文の段落として残したまま付録を作り直す（`reattach_generated_appendix` → `strip_generated_reference_appendix` の既存の規則）。採用の後に根拠リンクを取り直す（IK-0910）。製品 JS の挿入位置は引き続き静的検査のみ。

第 3 周の再演: 残存（予定どおり — 6be229e1 の t0 の付録の行に `![[equation:…]]![[figure:cc85eadb…]]` の連結が残る。付け直しは所有者の操作で、editor には押せない。新しい採用（te-03）は見出しの直後に独立の段落として入ったが、挿入位置は runner の近似（H15）で製品 JS は通っていない。run: uxsim/runs/c-astro-r25-studio-r3/20261023T071920Z-61773 seq13 / 20261023T071024Z-19157 seq19）。

第 4 周の再演: 残存（予定どおり — 保存済みのデータ）。固定コース 6be229e1 の t0 の付録の行は `![[equation:…]]![[figure:cc85eadb…]]` のまま（共有教員 te-05 の編集欄）で、学生 st-10 には「J=3〔図〕 ダストの偏光観測で…」と式と図が 1 行に連結して届き、図は本文の参照位置から離れた講義の最後にだけ出た。所有者が［付録を付け直す］で保存するまで残る。run: uxsim/runs/c-astro-r25-studio-r4/20261024T233408Z-44969 seq4 / uxsim/runs/c-astro-r25-studio-r4/20261024T234030Z-30759 seq6・11。

第 5 周の再演（2026-10-27）: **STILL（予定どおり）**。t0 の付録の式と教員図の埋め込みの連結が残る（共有教員 te-03 の画面。所有者が付け直して保存するまで）。run: uxsim/runs/c-astro-r25-studio-r5/20261027T071438Z-61813 seq4。

第 6 周の再演（2026-10-27）: STILL（予定どおり・保存済みの t0）。講義の最後の枚が「…中心的な関係式です。⏎J=3[[FIGURE_1]]」で、図が付録の式の行に連なり、学生は「図がどの節の図か分からない」と記した。run: uxsim/runs/c-astro-r25-studio-r6/20261027T123457Z-10833 seq11・13。

第 7 周の再演（2026-10-28）: STILL（予定どおり・保存済みの t0）。教員の説明図が付録の「J=3」の行に連なる。run: uxsim/runs/c-astro-r25-studio-r7/20261027T172130Z-55411 seq11・15。

第 8 周の再演（2026-10-28）: **STILL（保存済み）**。t0 の付録で「J=3![[figure:…]]」と図が付録の行に連なり、教材・講義 4 / 4 とも式と図の境が読めない。run: uxsim/runs/c-astro-r25-studio-r8/20261027T224552Z-353 seq6・11。
