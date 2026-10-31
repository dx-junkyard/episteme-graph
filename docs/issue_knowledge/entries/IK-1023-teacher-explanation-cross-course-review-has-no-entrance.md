---
id: IK-1023
title: "教員の書いた説明がほかのコースの学生に届くには「著者以外の査読承認」が要ると説明カードは言うのに、著者以外の教員（コースの editor）のカードには査読の手が無く「この説明の公開・共有の設定は著者だけが変えられます」と出る — 第 5 波のもう一人の査読者の入口は標準の説明だけで、教員の説明に著者以外が査読する経路がどこにも無い（引用の知らせも引用先の学生に届くかを言わない）"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/component_evidence_redesign.md"
feature_context:
  realizing: "別の教員が書いた説明を支持・引用し、自分のコースの学生にも届けられるようにする"
  layers: [endorsement_c, lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [none]
    governance: [assignment]
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
    統制軸 assignment: core/explanation_governance.py の規則 2 は「共有 ON の説明が書いたコース以外の学生に届くのは、最新の査読承認の記帳者が著者でないときだけ」とし、delivery_note EXPLANATION_DELIVERY_OWN_COURSE_ONLY がそう言う。しかし routes/theory_components.py の can_review は can_edit（著者）か標準の説明の second review だけで（:4809）、教員の説明に著者以外が査読する経路は管理者以外に無い。実測: te-01（コース 1bad7d9f の editor）のカードは［支持する］［別コースで引用］だけで edit_note「この説明の公開・共有の設定は著者だけが変えられます。」（uxsim/runs/c-astro-r25-share-r6/20261027T123545Z-11458 seq3-6）。構造軸 responsibility: 規則（誰の承認で広がるか）と操作の置き場（誰が査読できるか）が別々に決まり、規則が要求する主体に操作が割り当てられていない。処理軸・接続軸 none: 各判定は規則どおり。
generalization:
  level: repo_pattern
  general_form: "配信の条件に「本人以外の承認」を要求する規則を置いたのに、本人以外がその承認を記帳できる操作を用意しておらず、条件が永久に満たせない"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, invariant_audit]
  note: >-
    te-01 が te-04 の説明を支持・引用し、カードの「ほかのコースの学生に届くのは、著者以外の査読承認を受けた後です」と自分のカードに査読の手が無いことを並べて読んだ（uxsim/runs/c-astro-r25-share-r6/20261027T123545Z-11458）。explanation_governance の規則 2 と can_review の条件を突き合わせた。
resolution:
  perspective: [state_transition, explicit_contract]
  note: >-
    オーナー判断（推奨どおり）: 著者以外の査読者は、標準の説明と同じくその部品の論文を編集できる教員。著者が査読して共有 ON にした教員の説明にだけ、編集できる著者以外の教員へ「もう一人の査読者として査読する」を出し（_row_to_explanation_out の second_review_available・personal_reviewers）、PATCH は本文・題名・共有を触らず review_status の記帳だけを許す（EXPLANATION_PERSONAL_SECOND_REVIEW_ONLY・状態は変えず action=additional_review・changed_by が査読者 = 監査に残る）。既存の NON_AUTHOR_REVIEWED_SQL がそれを著者以外の査読承認として読む。引用は届く理由にせず、引用の応答の outcome_fact が引用先のコースの学生に届くかを LEARNER_EXPLANATION_SCOPE_SQL で評価して言う（citation_delivery_fact）。カードはもう一人の査読者に共有の切り替えを出さない。
  landed_in:
    - "backend/core/explanation_governance.py"
    - "backend/api/routes/theory_components.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "docs/features/concept_registry_design.md"
    - "backend/tests/test_share_round25_r6_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "2 人目の査読で他コースの学生に届くことの砂場での通し（第 7 周 te-02 と te-04）"
      - "教員の説明の査読を取り消す遷移は作っていない"
related: [IK-0926, IK-0984, IK-0924]
view_of: []
history: []
---

## 課題

症状: te-04 の説明（共有 ON・自己査読済み）は「書いたコースの学生に届きます。ほかのコースの学生に届くのは、著者以外の査読承認を受けた後です。」と言う。著者以外の te-01 が同じカードを開くと、査読の手は無く「この説明の公開・共有の設定は著者だけが変えられます。」。te-01 は支持して自分のコースで引用したが、引用の知らせは引用先の学生に届くかを言わず、届けるための操作もどこにも無い。

原因: 第 3 波で配信の規則に「著者以外の査読承認」を足し（IK-0926）、第 5 波で標準の説明にだけ「もう一人の査読者」の操作を足した（IK-0984）。教員の説明に著者以外が査読する操作は作られていない。

## 発見の観点

再現（reproduction）: (b2) で別の教員が支持・引用・支持者一覧まで到達した最初の周で、他コースへの広がりの手が無いことが見えた。不変条項の照合（invariant_audit）: C層の「確定は教員」と配信の規則の主体を突き合わせた。

## 解決の観点

是正済み（第 25 周・面 A 第 6 波・2026-10-27）。

旧案: 第 5 波の標準の説明と同じ形で、その部品の論文を編集できる教員（著者以外）に［もう一人の査読者として査読する］を出す（記帳者は監査行に残る）。引用の知らせは「引用先のコースの学生には、著者以外の査読承認の後に届きます」を言う。誰を著者以外の査読者として認めるか（編集者・コース所有者・同じグループ）は人の権利の判断を含む。

## 一般化

承認の規則（誰の承認で広がるか）と承認の操作（誰が押せるか）を別の段で設計すると、規則が要求する主体に操作が無い。標準の説明（IK-0984）と教員の説明で同じ欠けが別々に出た。型 last-mile-missing。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: GONE（到達）。te-02 が前提で 2 人目の査読（PATCH review_status=teacher_approved・200）を送った後、著者 te-04 の説明カードに「学生への公開: 公開可（査読済み） / 共有: ON」「書いたコースと、この部品を使うほかのコースの学生に届きます（著者以外の査読承認あり）」（uxsim/runs/c-astro-r25-share-r7/20261027T173130Z-60105 seq3）。残り（軽）: 誰が査読したか（名前・日付）がカードに出ない（支持・引用は名前と日付付き）。引用の outcome_fact と、他コース 6be229e1 の学生に実際に届くかは未評価（引用はハーネスの選択で送られなかった — H43。届くかを見る学生の場面も無い）。

## 第 7 波の是正（第 25 周・面 A、2026-10-28）

第 7 波の是正（2026-10-28・残り）: 説明カードに review_line「査読: 名前・日付（著者）／ 名前・日付」（いまの承認の期間の記帳・profile_name）。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

GONE（査読者名）。説明カードに「査読: 江口 大地・2026-10-27（著者） ／ 堀 正和・2026-10-27」（uxsim/runs/c-astro-r25-share-r8/20261027T224703Z-1148 seq3）。引用の outcome_fact も初めて読めた（seq5）が、「届きます」は引用先が部品を含むかを見ていない（NEW IK-1061）。
