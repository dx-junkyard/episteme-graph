---
id: IK-1055
title: "学習者の地図 L2 の事実文が、いまここの無い学生にも「いま学んでいる場所…を優先しています」と言う（実際に効いた手がかりはコースのトピックの地図の対応で、L1 にいまここの印は無い）"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/field_atlas_overlay_spec.md"
feature_context:
  realizing: "学生が分野の地図の L2（コースの概念マップ）で、描かれた項目がどの手がかりで選ばれたかを事実文で読む"
  layers: [field_atlas_s, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
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
    処理軸 wording: routes/atlas_view.py は learning_priority（焦点のトピックの対応・いまここ・コースの全トピックの atlas_node_id・足跡）を
    1 つの手がかりにまとめ、どれか 1 つが効けば label_vocab.ATLAS_L2_HINT_LEARNING「いま学んでいる場所」と言う。足跡の無い学生（L1 に
    いまここの印が無い）では効いたのはコースのトピックの対応だけなのに、語は学生本人の現在位置を指して読まれる。接続軸 meaning: 同じ画面の
    L1 の「いまここ」と同じ語感の語が、別の入力（教員が結んだトピックの対応）を指している。構造軸 none（選出を 1 関数に寄せ、使った手がかり
    だけを言う構造は正しい）、統制軸 none。
generalization:
  level: repo_pattern
  general_form: "選出の理由を言う事実文が、複数の入力を 1 つの語に束ね、そのうち利用者本人の状態を指すように読める語で、本人の状態ではない入力が効いた場合も言う"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, trace_walk]
  note: "足跡なしの st-01（run uxsim/runs/c-astro-r25-atlas-r8/20261027T224409Z-99225 seq 5〜6）で L1 にいまここの印が無いのに、L2 の事実文の括弧が「いま学んでいる場所と、…」から始まった。足跡ありの st-03（同 20261027T224229Z-98423 seq 6）と同じ文で、atlas_view の learning_priority の合成と突き合わせた。"
resolution:
  perspective: [single_point_fix]
  note: >-
    routes/atlas_view.py が学習者本人の状態（焦点・いまここ・足跡 = learner_state_ids）と、教員がコースのトピックに結んだ場所
    （course_bound）を分けて持ち、前者が描いた項目に効いたときだけ ATLAS_L2_HINT_LEARNING「いま学んでいる場所」、後者は新しい語
    ATLAS_L2_HINT_COURSE_TOPICS「このコースのトピックが結ばれている場所」を言う（label_vocab.ATLAS_L2_HINTS に並び順で追加）。
  landed_in:
    - "backend/api/routes/atlas_view.py"
    - "backend/core/label_vocab.py"
    - "docs/manual/student/02-student.md"
    - "backend/tests/test_atlas_review_round25_r8.py"
  verification:
    methods: [guardrail]
    unverified:
      - "足跡なし × 固定コースの学生（st-01）で括弧が「いま学んでいる場所」から始まらないこと（次周の再演）"
related: [IK-1017, IK-1045]
view_of: []
history: []
---

## 課題

症状: 足跡の無い学生 st-01 が固定コース 1bad7d9f の地図を開くと、L1 にいまここの印は無い（全概念「未訪問」）。L2 の事実文は
「この画面では、分野の地図の項目の一部だけを描いています（いま学んでいる場所と、このコースの論文が置かれている場所と、地図の版の改訂で
加わった関係と、そのつながりを優先しています）」で、足跡のある st-03 と同じ。学生は「いま学んでいる場所」を L1 の「いまここ」と同じものと読む。

原因: `select_l2_concept_ids` の priority に、焦点のトピックの対応・いまここ・コースの全トピックの `atlas_node_id`・足跡が 1 本の列で渡り、
事実文はそのどれかが描いた項目に効けば一律に `ATLAS_L2_HINT_LEARNING` を言う。足跡なしの学生で効いたのは、教員が結んだトピックの対応。

## 発見の観点

第 8 周で IK-1017 の本来の確かめ方の条件（足跡なし × 固定コース）を初めて歩いた。選出は骨格順の先頭に戻らず（IK-1017 主部 GONE）、事実文の
語だけが学生の状態とずれた。

## 解決の観点

学習者本人の状態と教員が結んだトピックの対応を別の語にし、効いたものだけを言う（第 8 波）。手がかりの語を入力の種類ごとに分け、学生本人の状態（焦点・いまここ・足跡）と教員が結んだトピックの対応を別の語で言う。
語を足すときは事実文の上限（括弧の長さ）とガードレール（使った手がかりだけを言う）を保つ。

## 一般化

型 wording-mismatch: 理由を言う文が、入力を束ねた語で言うため、本人の状態を指すように読める語が別の入力の効果を言う。
確かめ方: 足跡なし × トピックの対応ありの学生で事実文が「いま学んでいる場所」を言わないこと、足跡ありでは言うこと。

## 第 9 周の再演（2026-10-31・judge_atlas_r9）

**GONE**。足跡なしの st-01（run uxsim/runs/c-astro-r25-atlas-r9/20261031T015432Z-39319 seq 6）の括弧は「（このコースのトピックが結ばれている場所と、このコースの論文が置かれている場所と、地図の版の改訂で加わった関係と、そのつながりを優先しています）」で、「いま学んでいる場所」から始まらない。前後比較: 第 8 周の同学生・同コースは「（いま学んでいる場所と、…）」。足跡ありの st-03（20261031T015252Z-38248 seq 6）は「いま学んでいる場所と、このコースのトピックが結ばれている場所と、…」で言い分けが効いている。L1 にいまここが無いこと自体を地図が言わない点は IK-1073。
