---
id: IK-0863
title: "学生の部品の文脈で「共通部品として」のタブが出ないとき、理由の事実文が何も無い — 共通部品が無いのか（確定した結び付きが無い・候補はある・エントリが廃止）を DTO が表さず、画面はタブを消すだけなので「他の論文で使われていない」と「まだ確かめていない」が区別できない"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/component_evidence_redesign.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "学生が部品を開いて、その部品がほかの論文でも共通の部品として使われているかを確かめる"
  layers: [frontend_learning_ui, concept_registry, deliberation_w]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: backend/core/component_context.py:446-456 _build_shared_part は「確定した同一性リンクが無い」
    「shared_part_id が空」「エントリが active でない」のどれでも None を返し、理由を表す値を持たない。
    frontend/public/js/app.js:8352 / 8358 は shared_part が truthy のときだけタブと区画を描き、無いときは何も出さない
    （「この論文では」だけが残る）。接続軸 information: 「結び付けを確かめているのは教員で、確定したものだけが出る」という
    層の規律（CR・KR2 の閉世界）が学生側の画面に届かず、不在が意味を持たない。統制軸は確定の手続そのものは正しく
    閉じている（候補は学生に出ない）ので none。
generalization:
  level: repo_pattern
  general_form: "条件を満たしたときだけ区画を描き、満たさないときは区画ごと消すため、『該当が無い』と『まだ判断していない』と『判断して外した』が利用者から区別できない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: >-
    学生 3 人が全員「タブが無いだけで理由が無い」と記した。DTO の組み立てと画面の分岐を突き合わせた。run:
    uxsim/runs/c-astro-r25-share-r1/20261022T123418Z-31803 seq8・20261022T124951Z-39393 seq8・20261022T125633Z-43511 seq8。
resolution:
  perspective: [first_class_state, doc_correction]
  note: >-
    component_context の DTO に shared_part_note（閉世界の一文・候補の有無は出さない）を足し、app.js は「共通部品として」が無いとき「この論文では」の末尾に描く。学生マニュアルに「この論文では / 共通部品として」と説明バージョンの節を足した。
  landed_in:
    - "backend/core/component_context.py"
    - "frontend/public/js/app.js"
    - "docs/manual/student/02-student.md"
    - "backend/tests/test_share_round25_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "画面での見え方（実機）"
related: []
view_of: []
history: []
---

## 課題

症状: 「共通部品として」のタブが出ないとき、画面に何も書かれない。学生は「ほかで使われていない」のか「まだ確かめていない」
のかを判断できない。原因: 共通部品の不在の理由を DTO が持たず、画面は区画ごと消す。

## 発見の観点

学生ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。不在を表す事実文を DTO に 1 行持たせ、学生には「教員が確かめた記録の有無」だけを言う（候補の存在は KR2 で出さない）。

## 一般化

確定したものだけを見せる層では、見せないときに「確かめた記録が無い」を一文で言わないと不在が誤読される。確かめ方は、
確定リンクなし・候補あり・エントリ廃止の 3 状態で部品を開き、どれも同じ閉世界の一文が出て候補の存在が漏れないことを見ること。
