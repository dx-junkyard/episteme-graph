---
id: IK-0456
title: "図の学ぶ単位は linked_figure_ids を一度も読まれず、主張の無い章の単位は黙って「根拠あり」扱いになり、主結果のトピックの evidence_links が空になる"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 教員が選んだ学ぶ単位（topic.units）から、コースのトピックの根拠リンク・出典チャンクを決定論的に束ねる
  layers: [course_builder, learning_units]
classification:
  axes:
    processing: [none]
    structure: [none]
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
    接続軸: section_block（IK-0388）と thesis_support（IK-0453）には単位から主張・式・出典 block を引く経路があるが、figure の
    単位には無かった。figure の単位は component を束ねず、linked_claim_ids は DB UUID で artifact の claim 名前空間と突合できず、
    linked_figure_ids（FigureRecord.figure_id）はどこからも読まれていなかった（information）。構造軸 medium: section_block の章
    （sec_47 の本文段落 2 つ）には主張も evidence も無く、これは解析側（claim_qualification が採択しなかった）の事実で、
    コース側では原文チャンクしか結べない。その事実が grounding_note にも coverage にも残っていなかった。
generalization:
  level: repo_pattern
  general_form: 単位の種類ごとに束ね方を足していくうちに、一部の種類だけ参照を読む経路が無いまま残る
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: t18 の units（section_block 1 + figure 2）の learning_units_live 行を読み、fig_6 に FigureRecord.linked_claim_ids が 6 件あるのに evidence_links が空だった。
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    _figure_unit_refs が figure の単位の linked_figure_ids と、FigureRecord.linked_claim_ids（figure_claim_links）でその図に
    結びついた主張（atomic 子が当たった親は落とす）・caption block を単位の document の中でだけ引き、_topic_evidence_links は
    extra_figure_refs で図そのものを figures_index から解決する（抽出されていない図は落ちる）。単位のうち主張・式・部品・図の
    どれにも結びつかないものがあれば UNITS_WITHOUT_KNOWLEDGE_NOTE を grounding_note / coverage（根拠リンクがあれば weak、
    無ければ missing）に残す（生成のたびに付け直す事実文に登録）。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0456_unit_binding_gap.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコースの再 freeze（主結果のトピックに図と主張が付き、事実文が出るか）。抽出されていない図は document_figures に抽出されていないので図は付かない
      - sec_47 に主張が無いこと自体（claim_qualification が結果の段落を採択しなかった理由）は調べていない
related: [IK-0388, IK-0453, IK-0455]
view_of: []
history: []
---

## 課題

図の学ぶ単位が図も主張も連れてこず、主張の無い章の単位は根拠が無いことを言わなかった。

## 発見の観点

トピックが選んだ単位の行と、その図・章に実在する主張を突き合わせた。

## 解決の観点

図の単位の参照を後段の根拠リンクまで運び、結びつかない単位は事実文で残す。

## 一般化

単位の種類を足したら、その種類の参照を読む経路も同時に足す。
