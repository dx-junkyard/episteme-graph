---
id: IK-0871
title: "部品の文脈の上位レーンが「（中心命題）を支持する 中心命題」と同じ語を繰り返す — 関係の語に相手の名前「（中心命題）」を埋め込み、項目の名前も「中心命題」なので、語順「項目 → 関係語」で読むと同語反復になり、何を支持しているのか（中心命題の本文）が出ない"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/element_context_lens_design.md"
  - "docs/features/element_context_presentation_redesign.md"
feature_context:
  realizing: "学生が部品の文脈で、その部品がどの主張を支えているかを読む"
  layers: [deliberation_w, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
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
    処理軸 wording: backend/core/deliberation/context_lens.py:149 の関係語 "supports_thesis" は「（中心命題）を支持する」で
    相手の種類名を語に含む。項目の label は中心命題の本文ではなく種類名「中心命題」なので、カード（項目 → 関係語の語順）では
    「中心命題 （中心命題）を支持する」の繰り返しになる。支持構造の行（「（中心命題）を支持する 支持構造「直接支持」: 提案枠組みは…」）
    は本文を持つので読める。同じレーンの下位「を根拠として持つ 主張」も本文の無い種類名だけの項目で、IK-0622 と同じ族。
generalization:
  level: instance
  general_form: "関係の語に相手の種類名を埋め込み、項目の名前も種類名に縮退すると、関係の行が同語反復になり相手の中身が出ない"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: >-
    学生 st-02 が上位の「（中心命題）を支持する 中心命題」の同語反復と、下位の中身の無い「主張」を記した。run:
    uxsim/runs/c-astro-r25-share-r1/20261022T124951Z-39393 seq7・20261022T125633Z-43511 seq7。
resolution:
  perspective: [vocabulary_table]
  note: >-
    RELATION_LABELS の supports_thesis を「を支持する」にし（相手の種類名を関係語に入れない）、中心命題の項目名を _central_thesis_label（見出し → 中心命題の本文の要約「中心命題「…」」→ 種類名）にした。本文の無い項目をレーンから外して事実文に畳む案は採らなかった（種類名の項目 + 関係語で同語反復は解消する）。
  landed_in:
    - "backend/core/deliberation/context_lens.py"
    - "backend/tests/test_share_round25_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "教員側の W層の上位レーンの見え方の確認"
related: [IK-0622]
view_of: []
history: []
---

## 課題

症状: 上位レーンが「（中心命題）を支持する 中心命題」と読める。原因: 関係語に相手の種類名を埋め込み、項目の名前も種類名。

## 発見の観点

学生ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。関係語から相手の名前を外し、項目に本文を持たせる。

## 一般化

確かめ方は、中心命題を支持する部品を開き、上位の行が「（中心命題の要約） を支持する」と 1 回だけ相手を名指すことを見ること。

## 第 2 周の再演（第 25 周・面 A 第 2 周、2026-10-23）

一部残存。中心命題の本文が引ける項目は「を支持する （中心命題の本文）」と中身を名指すようになった。本文の引けない中心命題の
項目が、`context_lens.py` の項目名の最後の段（:740 `return "中心命題"`）で「を支持する 中心命題」のまま上位レーンに残る
（st-06 の EOS network map・TOV network map の両方）。是正案の後半（本文の無い項目はレーンから外し事実文に畳む）が
未適用。下位の「主張」だけの項目は IK-0898 と同じ根。

第 2 波（2026-10-23）で是正: 本文の引けない中心命題の項目名は、論文の問い（`central_question`）があれば「問い「…」に答える中心命題」、それも無ければ「この論文の中心命題（本文の記録なし）」（`CENTRAL_THESIS_WITHOUT_TEXT_LABEL`）とし、種類名だけの「中心命題」にしない。下位の「主張」だけの項目は IK-0898 で是正。
