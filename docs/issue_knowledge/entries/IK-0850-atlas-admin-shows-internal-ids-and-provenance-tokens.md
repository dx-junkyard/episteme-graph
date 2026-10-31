---
id: IK-0850
title: "分野の地図タブが内部の識別子をそのまま教員に見せる — 取り込みの確認に「id: ism_star_formation_c5」「親領域: ism_star_formation」（同じ確認の中で日本語名と英字キーの二重表記）、版の出所に「作成方法: reference_map:astrophysics_v0.1_spec_…」「確認者: faculty:reference_import」"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/display_projection_design.md"
feature_context:
  realizing: "教員が分野の地図の版の出所と、次版の下書きへ取り込む項目の確認を読んで判断する"
  layers: [field_atlas_s, category_gaps, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [responsibility]
    connection: [none]
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
    処理軸 wording: 取り込み確認の要約文は core/atlas_gaps/patching.py:243 / 289-290 が「（id: {node_id}）」を文に埋め込み、
    admin.js:7505-7508 openGapIncorporateConfirm が「追加する項目のid: …」「親領域: {parent_region_id}」を英字キーのまま
    行にする（要約文は親領域を日本語名で言うので、同じ確認で二重表記になる）。版の出所は admin.js:6543-6544 renderState が
    skeleton.generated_by と reviewed_by（"reference_map:…" "faculty:reference_import" のような取込経路の内部トークン）を
    素通しする。構造軸 responsibility（medium）: 教員向けの表示投影（display_projection の DP7）は名指しの面だけに掛かり、
    分野の地図タブはその 1 点を通らないため、面ごとに文言を直すしかない。接続軸 none（値は正しく届いている）。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "内部の識別子・取込経路のトークンを、人向けの確認文と出所表示の材料にそのまま使い、表示の投影を通らない面で利用者に見せる"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: "教員ペルソナが取り込み確認の英字キーと、版の出所の「誰が確認したか読めない」トークンを記した。確認文の組み立て（patching.py → admin.js）と renderState を辿った。 run: uxsim/runs/c-astro-r25-atlas-r1/20261022T123912Z-34807・uxsim/runs/c-astro-r25-atlas-r1/20261022T123418Z-31802（頭脳の所見 uxsim/runs/round25/BRAIN_NOTES_atlas.md）。"
resolution:
  perspective: [responsibility_move]
  note: >-
    atlas_gaps/patching.py の summary から「（id: …）」を外し、preview に parent_region_label を足して admin.js の確認は名前と親領域の名前だけを行にする。版の出所は GET .../atlas/skeleton の frozen.provenance（core/atlas_review_display.skeleton_provenance が generated_by / reviewed_by の取込経路トークン・UUID を表示語に写す）を描く。分野の地図タブを教員向け投影（DP7）の名指しの面に加えるかは判断していない（面の表示語はこの 1 モジュールが持つ）。
  landed_in:
    - "backend/core/atlas_gaps/patching.py"
    - "backend/api/routes/atlas_gaps.py"
    - "backend/api/routes/atlas.py"
    - "backend/core/atlas_review_display.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_atlas_gaps_patching.py"
    - "backend/tests/test_atlas_review_round25.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "分野の地図タブの表示語（帰属・出所・空の事実文）を atlas_review_display の 1 モジュールに集めた。DP7 の適用範囲の拡大は見送った。"
  verification:
    methods: [guardrail]
    unverified:
      - "分野の地図タブの全文言を内部 ID の正規表現で走査する網羅検査は作っていない（確認文と出所の 2 箇所だけを固定した）"
      - "骨格エディタの JSON 編集表示には generated_by / reviewed_by の生値が残る（編集の材料なので意図どおり）"
related: [IK-0413]
view_of: []
history: []
---

## 課題

症状: 次版の下書きに取り込む確認に「id: ism_star_formation_c5」「追加する項目のid」「親領域: ism_star_formation」が並び、
親領域が同じ確認の中で日本語名と英字キーの二重表記になる。版の出所は「作成方法: reference_map:astrophysics_v0.1_spec_2026-08-04
／ 確認者: faculty:reference_import」で、誰がいつ確認したのかが読めない。原因: 確認文と出所の表示が内部の値をそのまま材料にし、
教員向けの表示投影を通らない。

## 発見の観点

ペルソナの通し受講（reproduction）と確認文の組み立ての追跡（trace_walk）。

## 解決の観点

確認文から id を外して名前で言い、版の出所のトークンは route 層で表示語に写す。

## 一般化

投影を面ごとに掛ける運用では、投影の外の面に内部値が残る。確かめ方は、分野の地図タブの全文言を内部 ID の正規表現
（display_projection の語彙）で走査し、当たらないことを見ること。

## 第 2 周の再演（2026-10-23・campaign c-astro-r25-atlas-r2）

- 画面では未確認。ペルソナは「作成方法: reference_map:… ／ 確認者: faculty:reference_import」を読んだが、これはハーネスの
  投影（`atlas_exec.overview_lines`）が `frozen.generated_by` / `reviewed_by` の生値を描いているためで、製品の admin.js は
  `frozen.provenance` の表示語を描く（偽所見 — 「harness_fixes_atlas.md」 第 2 周 H10）。transcript の応答の抜粋は
  上限で切れて frozen 部分まで届かず直接は見えないが、同じ是正の束（atlas_review_display）の `decision_line` /
  `provenance_line` と凍結の影響ゼロの一文は同じ API から返っており、`routes/atlas.py` は frozen に provenance を足す。取り込みの確認（patching の summary）は
  ギャップ候補が無く操作されなかったので未観測。
