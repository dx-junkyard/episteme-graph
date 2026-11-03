---
id: IK-1073
title: "足跡の無い学生の分野の地図には「いまここ」の印が無く、その理由を地図が言わない — 同じ学生のコースの目次は最初のトピックを「学習中」と出すので、学生は「学習中なのに自分がどこにいるか分からない」と読む"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/field_atlas_overlay_spec.md"
feature_context:
  realizing: "学生が分野の地図で、自分がいまどこを学んでいるか（いまここ）を読む"
  layers: [field_atlas_s, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 meaning: 「学習の現在地」を 2 つの導出が別の意味で持つ。目次の「学習中」は routes/learning.py が推奨の順番（最初の
    locked でないトピック = in_progress）から導き、学習の行為が無くても付く。地図の「いまここ」は routes/atlas_view.py の
    _personal_layer が interest_traces の atlas 痕跡（足跡）からだけ導く。足跡ゼロでは地図にいまここが無いが、その不在を言う
    事実文も凡例も無い（「未訪問」の札の説明だけ）。処理軸 none（各導出は仕様どおり）、構造軸 none、統制軸 none。
generalization:
  level: repo_pattern
  general_form: "同じ利用者の同じ概念（現在地）を 2 つの面が別の根拠から導き、片方が空のとき空である理由を言わないので、もう片方の表示と食い違って読める"
pattern: same-name-different-referents
discovery:
  perspective: [reproduction, boundary_walk]
  note: "足跡なしの st-01（run uxsim/runs/c-astro-r25-atlas-r9/20261031T015432Z-39319 seq 4〜5）で、目次の最初のトピックが（学習中）なのに L1 にいまここの印が無く、学生が「最初のトピックを学習中なのに、自分がどこにいるか分かりません」と記した（審判 C）。第 8 周の st-01 にも同じ状態があった（judge_atlas_r8 §1）。目次の status 導出と _personal_layer を突き合わせた。"
resolution:
  perspective: [single_point_fix, explicit_contract]
  note: >-
    いまここ（足跡）が無いとき、GET /api/atlas の levels["1"] / levels["2"] に now_note（label_vocab.ATLAS_NOW_ABSENT_NOTE —
    「まだ地図の場所に結びついた学習の記録がないため、「いまここ」は表示していません（コースの目次の「学習中」は…別に付きます）」）を
    載せ、atlas-overlay.js が層の事実文の行（omitted_note と同じ行）の先頭に出す。目次の in_progress からいまここを推定して付けない
    （UC5）。目次側の語（routes/learning.py・面 B が触る）は変えていない。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/api/routes/atlas_view.py"
    - "frontend/public/js/atlas-overlay.js"
    - "backend/tests/test_atlas_review_round25_r9.py"
  verification:
    methods: [guardrail]
    unverified:
      - "足跡なしの学生（st-01 / st-05）で目次と L1 を続けて開き、不在の理由が読めること（第 10 周の再演）"
related: [IK-1055, IK-0689]
view_of: []
history: []
---

## 課題

症状: 足跡の無い学生が固定コースを開くと、目次の最初のトピックは「（学習中）」。分野の地図 L1 は全概念が「未訪問」で、いまここの印はどこにも無く、
無いことの説明も無い。L2 の事実文は第 8 波で「いま学んでいる場所」を言わなくなった（IK-1055 GONE）ので、地図のほうは一貫しているが、
目次との食い違いを学生が自分で解く手がかりが無い。

原因: 目次の「学習中」は推奨の順番（行為が無くても最初のトピックに付く）、地図の「いまここ」は学習の痕跡からだけ。2 つの「現在地」があり、
地図は空のときに黙る。

## 発見の観点

足跡なしの学生で地図を再演し（reproduction）、同じ学生のコースの目次と並べて読んだ（boundary_walk）。

## 解決の観点

第 25 周 第 9 波で解消（frontmatter の resolution）。是正前の案: いまここを推定で付けるのではなく、無いことを事実で言う（不在の明示）。

## 一般化

型 same-name-different-referents: 「現在地」の 2 つの導出。確かめ方: 足跡なしの学生で目次と地図 L1 を続けて開き、いまここが無い理由が地図の側で
読めること。

## 第 10 周の再演（2026-10-31・judge_atlas_r10）

**GONE**。足跡なしの st-01（同じ固定コース 1bad7d9f、run uxsim/runs/c-astro-r25-atlas-r10/20261031T053658Z-69836）。前後比較: 第 9 周の同じ学生（uxsim/runs/c-astro-r25-atlas-r9/20261031T015432Z-39319 seq 5）は L1 にいまここが無く理由も無い → 第 10 周 seq 5 の L1 末尾に「まだ地図の場所に結びついた学習の記録がないため、「いまここ」は表示していません（コースの目次の「学習中」は、学ぶ順番や開いたトピックから付く印で、地図の「いまここ」とは別に付きます）。」、seq 6・8 の L2 では事実文の行の先頭。目次（seq 4）の「問題設定と観測データ（学習中）」と並べて頭脳は「なぜ無いのかは分かりました」。足跡ありの st-03（uxsim/runs/c-astro-r25-atlas-r10/20261031T053524Z-69161 seq 5）は「星間磁場（いまここ）」で now_note は出ない（言い分けが効く）。

## 第 11 周の再演（2026-10-31・judge_atlas_r11）

**GONE（維持）**。st-01 の L1・L2 の先頭にいまここ不在の事実文と目次の「学習中」との言い分け（run uxsim/runs/c-astro-r25-atlas-r11/20261031T102321Z-3474 seq 5・6）。
