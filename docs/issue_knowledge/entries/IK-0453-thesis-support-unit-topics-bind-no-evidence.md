---
id: IK-0453
title: "中心命題・支持構造の単位（thesis_support）だけを選んだ「主結果」「確かめられていない点」のトピックに根拠が1つも付かず、⚓ も出典チャンクも無い"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コースの freeze が、教員が選んだ学ぶ単位（topic.units）から主張・式・図・出典チャンクを束ねる
  layers: [learning_units, course_builder]
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
    接続軸: thesis_support の単位は component を束ねず（agent_payload は thesis_kind だけ）、linked_claim_ids は DB UUID
    で artifact の claim 名前空間とは突合できない。IK-0388 は section_block だけに実所在の読み（_section_unit_refs）を足し、
    thesis_support の単位が参照する主張（thesis ノードの claim:{block}:{span} 参照）へ届く読みはどこにも無かった
    （information）。構造軸: 単位の行は thesis 側の claim 参照そのものを持たず、単位 → thesis ノードの対応は stable_key の
    材料からしか復元できない表現だった（representation。medium: 単位に agent 側の参照を持たせる直し方もあり得る）。
    確認: 第 10 周のコース（20 トピック）で主結果 3 本・確かめられていない点 3 本のトピックだけ evidence_links が空で、
    手組みの thesis_support 単位で _enrich_topics を通すと主張・式・図・出典チャンクがすべて空になることを再現した。
generalization:
  level: repo_pattern
  general_form: 同じ種類の欠落を1つの種別だけで直し、隣の種別には同じ読みが無いまま残る
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: 第 10 周で学生が「著者が確かめていない前提は何ですか」と尋ね、トピックから何も引けなかった。
resolution:
  perspective: [carry_through]
  note: >-
    _collect_structured_content が thesis_reconstruction artifact のノードを (document, 単位の stable_key) と
    (document, 本文) で索引化し、_thesis_support_unit_refs が単位 → thesis ノード → claim 参照（出典 block ×
    source span、または claim_id 一致）と単位の source_block_ids から、単位の論文の中でだけ主張を引く（atomic 子が当たった
    親は子で代表・章全体へは広げない・上限は章立ての単位と同じ）。採った主張の式・図・出典チャンクは既存の経路で付く。
    stable_key の導出は learning_units.thesis_support_nodes / thesis_support_stable_key を正本にし、単位の導出と共有した。
    parent_component の単位は従来どおり linked_component_agent_ids で束ねることをテストで固定した。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/core/knowledge_objects/learning_units.py
    - backend/tests/test_ik0453_thesis_support_evidence.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（第 10 周のコースで、主結果・確かめられていない点のトピックに根拠と出典チャンクが付くか）
      - 実 artifact の thesis 参照が claim_object_builder の出典 block × source span と一致する割合は測っていない
related: [IK-0388, IK-0377]
view_of: []
history: []
---

## 課題

中心命題・支持構造の単位だけを選んだトピックが、根拠ゼロのまま配信された。

## 発見の観点

学生の問いがトピックから何も引けず、根拠のあるトピックと無いトピックを単位の種別で分けて見た。

## 解決の観点

単位に対応する thesis ノードを stable_key と本文で引き、その claim 参照から論文の中でだけ主張を束ねる。

## 一般化

1つの種別だけで直した欠落は、同じ形の隣の種別に残る。
