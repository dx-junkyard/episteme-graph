---
id: IK-0928
title: "深く検討の「共通部品と結びつける」の検索が行う embedding の呼び出しが U層で機能に帰属されず（feature = unattributed）、教員の検索のたびに未帰属の消費として記録される"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が共通部品を検索し、その LLM・embedding の消費が機能ごとに計上される"
  layers: [usage_metering_u, deliberation_w, concept_registry]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    接続軸 information: routes/deliberation.py:714 の shared-part-candidates は core/library/search.py:27-37 _embed_query の
    generate_embeddings を usage_context の外で呼ぶ（同じ route ファイルで usage_context を張るのは音声の 2 箇所 :1642・:1668 だけ）。帰属の文脈が呼び出しに運ばれない。
generalization:
  level: repo_pattern
  general_form: "計測の帰属を呼び出し元が張る規約なのに、新しい入口が張らずに外部呼び出しをする"
pattern: gate-position-wrong
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    面の審判 E が llm_usage_events に operation=embedding・feature=unattributed の行を記録した（共通部品の検索 2 回と対応）。run:
    uxsim/runs/c-astro-r25-share-r3/20261023T032742Z-99528 findings f-…-0012。
resolution:
  perspective: [carry_through]
  note: >-
    core/library/search.py の _embed_query が feature を受けると llm_worker.embedding.embed_with_context で U層の帰属を張る（未指定は外側の usage_context を引き継ぐ）。find_similar_entries は feature を既定 embedding:library_search で必ず張り、routes/deliberation.py の共通部品の検索は embedding:shared_part_search（KNOWN_FEATURES に追加・embedding: 接頭辞で scene 対象外・allowlist 2 箇所のテストを更新）、routes/library.py の類似検索は embedding:library_search を user_id 付きで渡す。
  landed_in:
    - "backend/core/library/search.py"
    - "backend/api/routes/deliberation.py"
    - "backend/api/routes/library.py"
    - "backend/core/llm_usage/schema.py"
    - "backend/tests/test_share_round25_identity_links.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB の llm_usage_events に feature が記録されること"
related: [IK-0364]
view_of: []
history: []
---

## 課題

症状: 共通部品の検索のたびに未帰属の embedding が記録される。原因: 新しい入口が帰属の文脈を張らない。

## 発見の観点

面の審判 E（U層の帰属）の記録と route の点検（data_inspection）。

## 解決の観点

是正済み（第 3 波）。教員の検索の埋め込みを embed_with_context で機能に帰属させた。

## 一般化

IK-0364 と同型。確かめ方は、共通部品を検索した後に llm_usage_events の当該行の feature が登録済みの名前であることを砂場で見ること。
