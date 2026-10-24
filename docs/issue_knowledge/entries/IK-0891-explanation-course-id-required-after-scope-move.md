---
id: IK-0891
title: "説明バージョン（C層）の権限を部品の document 単位へ移した後も、component_explanations.course_id が NOT NULL かつ learning_courses への FK のまま残り、パイプライン由来の部品（course_id 空）では標準の説明の遅延生成が黙って失敗し続け、コースを添えない独自解釈の追加は 500「Failed to create explanation」になり、画面はそれを「追加に失敗しました」の固定文にするので、説明が1件も作れず共有・承認・引用の経路が全部通らない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が部品の説明（標準の説明・独自解釈）を作り、承認・共有し、学生がその説明を読む"
  layers: [endorsement_c, migrations_db, lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 contract: backend/db/021_endorsement_sharing.sql:19 は course_id TEXT NOT NULL REFERENCES learning_courses(id)。IK-0861 の是正で route 側（backend/api/routes/theory_components.py の _ensure_component_viewable :4099・create_component_explanation :4788）は「course_id は範囲ではなく出所・任意」と扱うようになり、body.course_id が空なら component.course_id（パイプライン部品では空文字）を入れる（:4802-4804）が、表の契約は変わっていない。空文字は FK を満たさず INSERT が失敗し、:4830-4833 が 500「Failed to create explanation」にする。標準の説明の遅延生成 _ensure_standard_explanation（:4710-4736）も component.course_id を入れて同じく失敗し、warning だけ残して握りつぶすので、一覧は「説明バージョンがありません。」になる（te-04 (b2) が他教員の共有を見られなかった直接の原因）。W層の解釈のコミット（backend/core/deliberation/annotations.py:258-263）も course_id 空で unsupported を返す。構造軸 representation: 「出所」として任意にしたい値を、表は必須の参照として表現している。原稿スタジオの画面（admin-lecture-studio.js:7425-7428）は lsState.courseId を送るので独自解釈の追加はコースが選べていれば通るが、標準の説明はどの経路でも作られない。画面は応答の detail を捨てて固定文を出す（:7430）。
generalization:
  level: repo_pattern
  general_form: "所有・範囲の単位を移すとき、ルートの判定だけを変えて表の制約（必須・参照）を旧い単位のまま残すと、新しい単位の行だけが書き込みで失敗し、失敗が握りつぶされる"
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-05 が原稿スタジオで独自解釈を追加すると 500「Failed to create explanation」で、画面は「追加に失敗しました」だけ（blocked）。続けて te-04 が同じ部品の説明を開くと「説明バージョンがありません。」で承認・引用に進めなかった。500 の分岐から INSERT の列と表の制約を突き合わせた。run: uxsim/runs/c-astro-r25-share-r2/20261022T220752Z-79835 seq17・20261022T221145Z-82427 seq3。ハーネスは course_id を送っていない（画面は送る）ので、500 そのものはハーネスの差分で起きたが、標準の説明の失敗は画面の経路でも起きる。
resolution:
  perspective: [representation_change, responsibility_move]
  note: >-
    migration 091 で component_explanations.course_id の NOT NULL を外し、FK を ON DELETE SET NULL に張り替えた（既存行の値は不変・DELETE なし）。独自解釈の追加・標準の説明の遅延生成・W層の解釈のコミットは出所が空なら NULL で入れる。_ensure_standard_explanation は成否を返し、失敗は一覧の応答ヘッダ X-Explanation-Fact（固定文 STANDARD_EXPLANATION_UNAVAILABLE_FACT・一覧の形は不変）で事実文にする。追加の失敗は英語の内部文ではなく日本語の事実文 detail。原稿スタジオは lsResponseJsonOrDetail で detail とヘッダの事実文を描く。
  landed_in:
    - "backend/db/091_component_explanations_course_optional.sql"
    - "backend/api/routes/theory_components.py"
    - "backend/core/deliberation/annotations.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/architecture/layer_registry.md"
    - "docs/architecture/data-model.md"
    - "backend/tests/test_share_round25_r2_fixes.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "出所のコースが消えても説明・承認の履歴を消さない（SET NULL）側を選んだ。"
  verification:
    methods: [guardrail]
    unverified:
      - "migration 091 の実 DB への適用（scratch DB では未実測）"
      - "実 DB での独自解釈の追加 → 共有 → 他教員の承認・引用の端から端まで"
related: [IK-0861]
view_of: []
history: []
---

## 課題

症状: パイプライン由来の部品には標準の説明が作られず、コースを添えない独自解釈の追加は 500 になる。画面は理由を出さない。
説明が 1 件も無いので、共有・承認・引用が全部通らない。原因: 説明の権限・範囲を document 単位へ移したのに、表の course_id は
必須の参照のまま。

## 発見の観点

教員ペルソナの通し受講（reproduction）。500 の分岐から INSERT と表の制約を辿った（trace_walk）。

## 解決の観点

第 2 波（2026-10-23）で是正。表の契約をルートの意味（出所・任意）に揃え、失敗を握りつぶさず事実文で返す。

## 一般化

単位の移行で判定と制約の片方だけを変える型（contract-changed-one-side）。確かめ方は、course_id の空の部品で標準の説明の一覧・
コースを添えない追加・W層の解釈のコミットの 3 つを行い、どれも行が作られること、作れないときは理由が画面に出ることを見ること。
