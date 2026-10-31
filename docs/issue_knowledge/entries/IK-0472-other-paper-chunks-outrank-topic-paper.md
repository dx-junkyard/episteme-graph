---
id: IK-0472
title: "トピック内の BAO の問いで、コースの別の論文（磁場フィラメント）のチャンクがトピックの論文より上位に並び、出典に入った"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "トピックの中の問いには、そのトピックが扱う論文を先に根拠として示す"
  layers: [rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: 検索は本人が閲覧できる document 全体を類似度だけで並べ、トピックが束ねる論文かどうかを並びに使っていなかった（logic）。接続軸 medium: トピックの論文の情報（units /
    evidence_links / material_chunk_ids）は topic にあったが検索の後段に渡っていなかった（information）。
generalization:
  level: general
  general_form: "範囲の中の優先度（いま扱っている対象）を持っているのに、検索結果の並べ替えに渡していない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "req-00004 / 00066 / 00070 の出典ブロックで各チャンクの論文を確かめた。"
resolution:
  perspective: [carry_through]
  note: >-
    検索結果に document_id を足し、topic_source_document_ids(topic) の論文のチャンクを先に置く（(score < 0.30, トピックの論文でない, -score)
    の安定ソート、検索は1回のまま 12 件引いて 8 件に切る）。discuss と document 直付けの経路には掛けない。採用の下限と content_grounding は変えない。
  landed_in:
    - backend/api/routes/learning.py
    - backend/api/services.py
    - docs/backend/rag-chat.md
    - backend/tests/test_ik0466_0478_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（実コースの topic に document_id / units が入っているかは DB で確かめていない — material_chunk_ids 経由の解決に頼る可能性）"
      - "discuss（course_sources）での論文混在は対象外（00066 / 00070 は discuss の all_visible / course_sources の範囲どおり）"
related: []
view_of: []
history: []
---

## 課題

トピック内の BAO の問いで、コースの別の論文（磁場フィラメント）のチャンクがトピックの論文より上位に並び、出典に入った

## 発見の観点

req-00004 / 00066 / 00070 の出典ブロックで各チャンクの論文を確かめた。

## 解決の観点

検索結果に document_id を足し、topic_source_document_ids(topic) の論文のチャンクを先に置く（(score < 0.30, トピックの論文でない, -score) の安定ソート、検索は1回のまま 12 件引いて 8 件に切る）。discuss と document 直付けの経路には掛けない。採用の下限と content_grounding は変えない。

## 一般化

範囲の中の優先度（いま扱っている対象）を持っているのに、検索結果の並べ替えに渡していない。
