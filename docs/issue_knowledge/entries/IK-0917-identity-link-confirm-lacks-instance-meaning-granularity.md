---
id: IK-0917
title: "同一性リンクの確定画面に部品側の意味（要約・入出力）が無く、粒度の違う対（論文の部品「TOV を課す構造ネットワーク」⇄ 共通部品「TOV 方程式」）を同じものとして確かめずに確定でき、その確定が学生の「共通部品として」にそのまま届いて意味がずれる"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が論文の部品と共通部品を同じものとして結び、学生が部品の文脈から共通部品をたどる"
  layers: [deliberation_w, concept_registry, learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [review]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 meaning: 同一性リンク（element_identity_links）は「同じもの」の 1 つの意味しか持たない（KN-2）。確定画面（deliberation.js
    _identityLinkRowHtml）は共通部品の名前・要約だけを描き、部品側の要約・入出力（overview の decomposition にある）を並べない。学生側は
    core/component_context.py:574-605 の _build_shared_part が確定リンクをそのまま「共通部品として」に出す。構造軸 representation:
    「同じもの」と「使う・課す」を区別する表現が無い。統制軸 review: 判断の弁はあるが、弁を通るときに判断材料（両側の意味）が並ばない。
generalization:
  level: repo_pattern
  general_form: "2 つのものを同一と記録する手続に、両側の意味を並べて照合する材料が無く、粒度の違う対が同一として確定されて下流の利用者に届く"
pattern: unit-of-work-undefined
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-02 は部品の説明を見ずに確定し（uxsim/runs/c-astro-r25-share-r3/20261023T032742Z-99528 seq6-7）、続く学生 st-09・st-04 が「この部品は TOV
    方程式を満たすように学習するネットワークで方程式そのものではない」と記した（uxsim/runs/c-astro-r25-share-r3/20261023T033153Z-19784
    seq11・uxsim/runs/c-astro-r25-share-r3/20261023T033353Z-33782 seq8）。確定の画面と学生の DTO を突き合わせた。
resolution:
  perspective: [representation_change]
  note: >-
    確定の判断材料を弁の位置に置いた。未確定の行の応答に confirm_note（identity_links.CONFIRM_NOTE「確定すると、この 2 つを同じものとして扱います。学生の画面では…共通部品に左の共通部品が出ます。部品が共通部品を使う・満たすだけの関係なら…確定しないでください」）を載せ、deliberation.js は共通部品の名前・要約と部品側の名前・要約・入出力を並べた直後、確定ボタンの直前にこの文を描く。学生側は component_context の shared_part.heading「教員がこの部品と同じものとして確かめた共通部品」を app.js が素通しで描く。リンクの種類は足さない（指揮者の判断・KN-2 の「同じもの」を保つ）。
  landed_in:
    - "backend/core/deliberation/identity_links.py"
    - "backend/api/routes/deliberation.py"
    - "frontend/public/js/deliberation.js"
    - "backend/core/component_context.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_share_round25_identity_links.py"
    - "backend/tests/test_share_round25_identity_consumers.py"
    - "backend/tests/test_share_round25_r4_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "粒度の違う対を確定しようとしたときの実画面（次周の te-05 × (a')）"
      - "使う・満たす関係の記録（v1 では記録しない）"
related: [IK-0916, IK-0920]
view_of: []
history: []
---

## 課題

症状: 「TOV network map」（構造ネットワーク）を共通部品「TOV 方程式（静水圧平衡）」に結んで確定でき、学生の部品の文脈に「共通部品として: TOV 方程式」が出て、学生が意味のずれに気づく。原因: 同一性リンクは同じものの 1 意味しか持たず、確定の画面に部品側の意味が並ばない。

## 発見の観点

教員と学生のペルソナを同じ周で続けて通し（reproduction）、教員の確定から学生の表示まで辿った（trace_walk）。

## 解決の観点

是正済み（第 3 波）。確定の直前に両側の意味と「同じものとして扱う・学生に何が出るか」の事実文を並べ、学生側の見出しを「教員がこの部品と同じものとして確かめた共通部品」にした。リンクの種類は足していない。

## 一般化

照合の材料が弁の位置に無い型。確かめ方は、粒度の違う対（部品 ⇄ それが使う方程式）を確定しようとしたとき、確定の直前に両側の意味が並び、学生側の見出しが「同じものとして確かめた」と言うことを画面で見ること。

## 第 4 周の再演（第 25 周・面 A 第 4 周、2026-10-25）

第 4 周の再演: 残存（是正の部分は効いた — 確定の直前に両側の名前・要約・入出力と CONFIRM_NOTE が並んだ。それでも委任型の te-05 は「同じ論文の TOV の話なので同じでいいだろう」と注意文を流し読みして作成直後に確定し、学生 st-08・st-01 が「ネットワークそのものが TOV 方程式と同じと言われると混乱する」と記した。照合の材料を並べるだけでは止まらず、確定を戻す経路も無い — IK-0953 として起票。run: uxsim/runs/c-astro-r25-share-r4/20261024T232812Z-98848 seq6-7・uxsim/runs/c-astro-r25-share-r4/20261024T233212Z-21482 seq11・uxsim/runs/c-astro-r25-share-r4/20261024T233359Z-43631 seq7）

## 第 4 波の是正（第 25 周・面 A、2026-10-25）

確定の直前に両側を名指しして「同じものですか（一方が他方を使う・満たすだけではありませんか）」と問う 1 文（identity_links.confirm_question → 応答 confirm_question）と、部品の種別（component_type を entry_type に写したもの）と共通部品の種別が違うときの一段強い確認の事実文（granularity_note）を足した。deliberation.js は種別が違う対の［確定］で、問いと種別の違いを並べた window.confirm をもう一度出す。誤って確定した組は IK-0953 の［確定を取り消す］で戻せる。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 残存（エントリ単位）。行動型の te-02 はリンクの弁（問い・種別の文）を通らない［確定にする］（エントリの確定）で、名前の違う対 Boundary constraints ⇄ Softening interpretation を確認ダイアログ無しに確定した。リンクの確定は runner がリンクを引けず未実行（H35）なので、リンクの弁そのものは未評価。エントリの確定とリンクの確定の分離は IK-0982、候補の組の質は IK-0980。run: uxsim/runs/c-astro-r25-share-r5/20261027T071135Z-43804 seq4・5

## 第 5 波の是正（第 25 周・面 A、2026-10-27）

エントリ単位の確認を足した。概念（エントリ）の［確定］の前に「確定するのは概念を置くことだけで、論文の部品との結び付き（各行）は確定されません」と問い（routes/library.py ENTRY_CONFIRM_QUESTION・一覧は confirm_question・詳細は review_confirm_question）、確定後に事実文（ENTRY_CONFIRMED_FACT）。ライブラリの一覧と詳細のリンクの［確認］も、深く検討と同じ confirm_question / granularity_note の確認を通る（admin.js _libraryConfirmIdentityLinkDialog）。エントリの確定とリンクの判断の分離そのものは IK-0982。landed_in: backend/api/routes/library.py・frontend/public/js/admin.js・backend/tests/test_share_round25_r5_fixes.py。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: GONE（リンクの弁）。一覧の［確認］の前に「（一方が他方を使う・満たすだけではありませんか）。 — 同じものとして確定しますか」が出た。ただし問いの両端が同じ名前（部品「TOV residual loss at collocation points」と共通部品「TOV residual loss at collocation points」）で、対の相手（PDF 版の TOV residual constraint）が問いに出ず弁が働かない → IK-1026。run: uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq3
