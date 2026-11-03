---
id: IK-0593
title: "ゼミ前ブリーフの「脆い前提」「一点吊り」に同じ文面の項目が並び、別の対象なのか重複なのか区別できない"
status: resolved
recorded_at: 2026-10-01
resolved_at: 2026-10-01
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.11
feature_context:
  realizing: "ゼミ前ブリーフで、負荷の高い前提と支持線が一本の項目を教員に列挙する"
  layers: [seminar_brief, doubt_d]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 項目の文面を「操作: 代表主張の本文」で組むため（IK-0563 の置き換え）、同じ主張を代表にもつ別の対象（式の詳細層の別の手順）が
    同じ文面になり、対象を区別する手がかり（式の印字番号・節）が文面に無い（処理軸 wording・構造軸 representation）。
    単一支持線でも「定義: Equation (1) defines N_EOS」が「正規化: Equation (1) defines N_EOS」に支えられ、自己支持に見える。
    項目の対象（target）が別々であることを DTO で確かめれば確定する。
generalization:
  level: repo_pattern
  general_form: "別々の対象を、対象を区別しない同じ表示文で列挙する"
pattern: same-name-different-referents
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 18 周 te-05（t-seminar-brief）で、脆い前提 (1)(2) が同一の statement、(4)(5) が同じ本文で操作だけ違うことを画面で見た。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: '同じ文面の項目を群ごとに、ノードが使う式の名前（「式 (3)」）で見分け、見分けられない同じ対象は 1 行に畳んで事実文を添える（件数・内部 ID なし）。支持経路は対象と同じ主張を根拠にもつノードを root・cut に数えない（自己支持を消し、残らなければ既存の閉世界の事実文へ縮退）。学習者の問いの区画に k-匿名の素朴な問いの集約（名前とレンジのみ）を出す。'
  landed_in:
    - backend/core/doubt/seminar_brief.py
    - backend/core/doubt/support_paths.py
    - backend/core/doubt/naive_signal.py
    - backend/tests/test_seminar_brief_dedupe.py
    - frontend/public/js/admin.js
    - docs/features/seminar_brief_mirroring_design.md
    - 823eeda
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（te-05 t-seminar-brief）"
      - "式の名前でも見分けられない別の対象が残る場合の読みやすさ"
related: [IK-0563]
view_of: []
history:
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '第 19 波で、同文面の項目を式名で区別するか畳み、自己支持を数えないようにした（ガードレールで確認・再演は次の周）。（persona_enactment_testing_design.md §18.12）'
---

## 課題

ゼミ前ブリーフの脆い前提に、まったく同じ statement（「中間主張を導出: The method does not use an external TOV solver…」）が 2 行並ぶ。
本文が同じで操作名だけ違う行もある。単一支持線では「定義: Equation (1) defines N_EOS」が「正規化: Equation (1) defines N_EOS」に支えられると
読め、自己支持に見える。教員はどれが別の対象でどれが重複か判断できない。

証拠: run 20261001T053754Z-69305 seq=3（te-05）。

## 発見の観点

再演で画面の列挙を読んだ。IK-0563 で内部 ID を主張本文に置き換えたことで、対象の区別が文面から消えた可能性がある。

## 解決の観点

823eeda。ID を読める文に置き換えたときに落ちた「別物である」情報を、式の名前という読める区別で文面に戻し、戻せない同一物は畳む見立て。
自己支持は支持経路の計算から同じ主張を外して消した。原因は起票時の仮説のまま（classification.basis）で、是正後の再演で確かめる。

## 一般化

ID を読める文に置き換えるとき、ID が持っていた「別物である」情報も落ちる型。
