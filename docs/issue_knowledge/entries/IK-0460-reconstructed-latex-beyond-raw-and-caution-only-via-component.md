---
id: IK-0460
title: "原文が「Mmax = 2」「,(6)」だけの式に復元 LaTeX が全文で付いても原文と並べず、復元式の注意点は部品の注意書きがあるトピック（t12〜t14）でしか付かなかった"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、AI が文脈から復元した式を授業用ドラフトへ差し出し、注意点に復元の事実を載せる
  layers: [course_builder]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸・接続軸: _ensure_reconstruction_caution は部品の注意書き（component_cautions）の equation_ids と復元式の交差だけで発火し、トピックが復元式そのものを差し出していることは条件にしていなかった。部品の注意書きを持つのは3トピックだけだった。復元の条件（式の reconstructed）が注意点の段へ渡っていない。原文との差もプロンプトで並べていなかった。
generalization:
  level: repo_pattern
  general_form: 前段で立った条件（この式は復元）が、別の経路（部品の注意書き）を通ったときだけ後段に届く
pattern: condition-not-propagated
discovery:
  perspective: [data_inspection, trace_walk]
  note: 再生成プロンプトで component_cautions が付くトピックと、復元式を差し出すトピックを突き合わせた。
resolution:
  perspective: [carry_through, single_point_fix]
  note: >-
    reconstruction_beyond_raw（原文が式番号を除いて6文字未満・= で終わる・LaTeX の数字が原文に無い・[unknown …] の穴）を決定論で判定し、参照一覧と content_blocks の写しに raw_text と reconstruction_note を添える。topic_reconstructed_equation_ids（式として読める復元式）を reconstructed_equation_ids としてプロンプトに渡し、_ensure_reconstruction_caution はそれか部品の注意書きのどちらかで発火する。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0459_0465_course_draft_regen11.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（注意点が全トピックに付くか・LaTeX を原文として断定しないか）
      - PDF 経路では大半の式が復元なので、式を持つトピックのほぼ全てに固定文が付く（事実どおりだが教員には冗長に見え得る）
related: [IK-0429, IK-0438, IK-0440, IK-0443, IK-0454]
view_of: []
history: []
---

## 課題

復元した式を配るトピックの大半で、復元の注意点も原文との差も示されていなかった。

## 発見の観点

部品の注意書きの有無と復元式の有無をトピックごとに突き合わせた。

## 解決の観点

復元の条件を式そのものから注意点の段へ直接渡し、原文が短い式には原文を並べる。

## 一般化

条件は発生源（式の属性）から直接後段へ渡し、別の経路の副産物として届くことに頼らない。
