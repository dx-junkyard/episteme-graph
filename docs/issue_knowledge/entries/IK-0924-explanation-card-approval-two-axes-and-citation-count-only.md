---
id: IK-0924
title: "原稿スタジオの説明カードが、査読の状態（「状態: 承認済み」）と承認の厚み（見出しの「未承認」「暫定的に1名が承認」）を同じ「承認」の語で並べて矛盾して読め、引用は「引用: 1」の件数だけで、どのコースへどんな意図で引用したかが見えない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が説明を承認・引用し、その結果が誰に何として届いたかを確かめる"
  layers: [endorsement_c, lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: frontend/public/js/admin-lecture-studio.js:7314-7343 lsRenderExplanationCard は
    endorsement_label（承認の厚み）を見出しの右に、review_status の訳（承認済み・査読待ち）を「状態:」に描き、ボタンも［承認する］と［査読承認］が並ぶ — 2 つの軸に同じ語を使う。接続軸
    information: 引用は citation_count だけを描き、引用先コース・意図（cite の応答には citing_course_id・citation_intent_label がある）を一覧の応答にも画面にも持たない。
generalization:
  level: repo_pattern
  general_form: "2 つの別の判断の軸を同じ語で並べ、記録した関係の行き先を件数だけで示す"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-02 が査読承認の後に「見出しはまだ未承認、どっちなの」と記し（uxsim/runs/c-astro-r25-share-r3/20261023T033629Z-52380 seq5）、te-05
    が引用後に引用先も意図も出ないと記して諦めた（uxsim/runs/c-astro-r25-share-r3/20261023T033729Z-59479 seq5-6・gave_up）。
resolution:
  perspective: [vocabulary_table]
  note: >-
    2 つの軸を語で分けた。語彙の正本 core/label_vocab.py（EXPLANATION_AXIS_LABELS / EXPLANATION_REVIEW_STATUS_LABELS / EXPLANATION_ACTION_LABELS / EXPLANATION_SUPPORT_LEVEL_LABELS / explanation_support_label）: 公開の査読は「学生への公開: 公開可（査読済み）/ 査読待ち …」、承認の厚みは「教員の支持: …」、ボタンは［支持する］［支持を取り消す］［学生に公開してよいと査読する］。admin-lecture-studio.js は逐語ミラー（一致はテスト）。説明一覧の応答に citations（引用先コースの題名・意図ラベル・引用者・日時）を同梱し、カードは件数ではなく列挙する。学習者向けの段階ラベルも「承認」から「支持」の語になった。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/api/routes/theory_components.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/tests/test_share_round25_explanations.py"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "backend/tests/test_share_round25_r4_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面のカード（node が無く JS の構文検査も未実施）"
      - "atlas_state の「承認: N名の教員が承認」の事実行は別の文言で、今回は直していない"
related: [IK-0925, IK-0926]
view_of: []
history: []
---

## 課題

症状: 査読承認しても見出しが「未承認」のままに見え、引用は件数だけ。原因: 2 つの軸に同じ語、引用の記録の中身を一覧に運ばない。

## 発見の観点

教員ペルソナ 2 人の通し受講（reproduction）。

## 解決の観点

是正済み（第 3 波）。査読（学生への公開）と支持（教員の支持）を別の語にし、引用は引用先・意図の列挙にした。

## 一般化

語の衝突と件数だけの記録の型。確かめ方は、査読・支持・引用を 1 回ずつ行い、カードの文が 3 つの操作の結果をそれぞれ別の語で、引用先の題名付きで言うことを画面で見ること。

## 第 4 周の再演（第 25 周・面 A 第 4 周、2026-10-25）

第 4 周の再演: 一部残存（カードの軸の語は「教員の支持」「学生への公開: 査読待ち」に分かれた。モーダルの見出し「説明バージョンと承認」と説明文「承認は説明バージョン単位。承認の厚みは段階ラベルで表示」が旧語のままで、几帳面型の te-03 が「承認と支持が同じことなのか書かれていない」と記した — admin-lecture-studio.js:7710-7713。引用の列挙は引用が行われず未評価。run: uxsim/runs/c-astro-r25-share-r4/20261024T233620Z-65949 seq3）

## 第 4 波の是正（第 25 周・面 A、2026-10-25）

モーダルの見出しを「説明と、その支持・学生への公開 — 部品名」に、説明文を「学生に公開してよいかの査読（著者が設定）と、ほかの教員の支持を別々に記録」に揃えた（admin-lecture-studio.js・マニュアル 14-admin-lecture-studio#component-endorse）。部品カードのボタン「説明・承認の共有」はハーネスが語で引くため据え置いた（残り）。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 消滅（モーダルの見出し・説明文が「説明と、その支持・学生への公開」に、カードは「教員の支持」「学生への公開」の 2 軸）。引用の列挙は (b2) が別コースを開いて止まり未評価（H36）。run: uxsim/runs/c-astro-r25-share-r5/20261027T070623Z-23499 seq2

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: 引用の列挙は GONE。te-01 が別コースで引用すると「引用しました」+ カードに「引用: 『宇宙物理 論文紹介ゼミ：…』（根拠として使う） 水野 早紀・2026-10-27」— 誰・いつ・意図が 1 操作で読め、開き直しても残った。引用先が同題名で自分のどのコースか読めないのは IK-0407、引用先の学生に届くかを言わないのは IK-1023。run: uxsim/runs/c-astro-r25-share-r6/20261027T123545Z-11458 seq5・6

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

初評価で到達（引用の列挙）。「引用: 『…』（根拠として使う） 水野 早紀・2026-10-27」に te-02 の引用行が加わった（uxsim/runs/c-astro-r25-share-r8/20261027T224703Z-1148 seq3・5）。引用先の 2 行が同じ題名で、どちらのコースか読めない（IK-0407）。
