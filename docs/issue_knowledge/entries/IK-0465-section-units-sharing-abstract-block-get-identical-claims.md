---
id: IK-0465
title: "章立ての単位が要旨の block を共有しているため、別の章を選んだトピック（t0 と t1）が同じ 8 件の要旨の主張になっていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、教員の選んだ章立ての単位（section_block）からその章に載る主張を引いてトピックに結びつける
  layers: [course_builder, pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [decomposition]
    connection: [none]
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
    処理軸: _section_unit_refs は単位の出典 block に載る主張を artifact の並びで取り上限 8 件で切っていた。解析は要旨の block（blk_001_0005）を同じ論文の 5 つの章立ての単位に含めるので、要旨の主張が上限を先に埋め、単位に固有の block の主張が落ちていた。構造軸 medium: 単位どうしが block を共有すること自体は解析の分け方（内容）で、束ねの誤りではない。
generalization:
  level: repo_pattern
  general_form: 複数の単位に共通する材料が上限を先に埋め、単位ごとの違いが消える
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection, trace_walk]
  note: t0 と t1 の参照一覧が同一なのを見て、learning_units_live の source_block_ids と claim の出典 block を突き合わせた。
resolution:
  perspective: [order_and_budget]
  note: >-
    section_block_share（(document, block) を載せる章立ての単位の数）を1回数え、単位に固有の block の主張 → 節で当たる主張 → 共有 block の主張（少ない順）に並べてから上限で切る。出典 block も固有の順に並べ、原文抜粋の選択（IK-0464）に使う。砂場の Cep B で t0 / t1 の主張が別の並びになることを実データで確かめた。t2 / t4 は t4 が t2 と同じ単位を選び、もう1つの単位の block もすべて別の単位と共有しているため同じ並びのまま（内容）。結果の式が方法のトピック（t7）に行くのは、その章立ての単位が結果の節（3.1 High-Z SFR）まで含むため（解析の単位の分け方・内容）。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0459_0465_course_draft_regen11.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（全トピックの参照一覧）
      - 単位の分け方そのもの（方法の単位が結果の節を含む）は解析側の内容で、ここでは直していない
related: [IK-0429, IK-0438, IK-0440, IK-0443, IK-0454]
view_of: []
history: []
---

## 課題

共有された要旨の主張が、別の章のトピックの主張の枠を埋めていた。

## 発見の観点

同一の参照一覧になった2トピックの単位の block を突き合わせた。

## 解決の観点

単位に固有の材料を先に並べてから上限で切る。

## 一般化

上限で切る前に、単位に固有のものを共通のものより前に並べる。
