---
id: IK-0388
title: "章立ての学ぶ単位（section_block）だけで束ねたトピックに、解析済みの論文の主張・式・図が1つも結びつかず、「根拠はまだ用意されていません」「図・式・主張は紐づいていません」と表示される"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 教員が学ぶ単位（章立て）でコースを組み、学習者がトピックの根拠（⚓ チップ）を見る
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
    接続軸: section_block の単位行は section_ids / source_block_ids を持つが、コース側の読み（course_units の
    _UNIT_ROW_COLUMNS）がこの2列を SELECT しておらず、freeze は単位の章・出典 block を知らなかった（information）。
    構造軸: section_block の単位は component を束ねず（agent_payload は block_type だけ）、linked_claim_ids は DB UUID で
    artifact の claim 名前空間とは突合できないため、component 経由・claim ID 経由のどちらでも根拠に届かない表現だった
    （representation。medium: 単位に agent ID を持たせる直し方もあり得る）。加えて component が0件だと unlinked 扱いに
    なり material_chunk_ids も空にされていた。確認: course_content_builder._enrich_topics を手組みの section_block 単位で
    通すと、主張・式・図・出典チャンクがすべて空になることをテストで再現した。
generalization:
  level: repo_pattern
  general_form: 素材（章・出典 block）は行に保存されているが、それを使うべき読み手が SELECT しておらず到達しない
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: 20 トピックのコースで、先頭トピックと主結果トピックが根拠ゼロのまま配信された。
resolution:
  perspective: [carry_through]
  note: >-
    course_units の単位行に section_ids / source_block_ids を読み（旧 12 列の行も読める）、freeze（_enrich_topics）は
    section_block の単位について、その単位の論文の中でだけ（IK-0377）実所在で主張・式・出典 block を引く:
    主張 = evidence の source.block_id が単位の出典 block にあるもの（先）→ section_id が単位の章にあるもの（後）、
    atomic 子を持つ親は落とす・上限 8。式 = source_location の block / section が単位に入るもの + 採った主張の
    equation_ids・上限 6。図は既存の claim → 図の逆引き。出典チャンクは単位の出典 block を交差に足す。
    何か引けたトピックは unlinked にしない。LLM は呼ばない。linked_equation_ids に入れた式は content_blocks の
    チャンク式の許可にも入る。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/core/course_units.py
    - backend/tests/test_ik0388_section_block_evidence.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース再生成（第 8 周の 20 トピックのコースで、先頭トピックと主結果トピックに ⚓ が付くか）
      - 章の section_id が主張・式の section_id と同じ語彙で書かれているか（実論文の artifact では確かめていない）
related: [IK-0377, IK-0371]
view_of: []
history: []
---

## 課題

章立ての単位だけで束ねたトピックに根拠が付かない。

## 発見の観点

実ペルソナの受講で、根拠ゼロのトピックが配信された（第 8 周）。

## 解決の観点

単位行が持つ章と出典 block を読み手まで運び、実所在で主張・式・図を引く。

## 一般化

保存した素材を読み手が SELECT していなければ、どれだけ正しく保存しても使われない。
