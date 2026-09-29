---
id: IK-0455
title: "![[source:topic_summary]] は生成側で「原文抜粋」、配信側で「トピック概要」を指し、閉世界の外の ![[source|claim|component:…]] は学習画面に内部 ID の「未解決」カードで、チャットのプロンプトには生の埋め込み記法で届く"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が教材本文に根拠の埋め込みを書き、受講画面・学習チャットがその本文を学習者とモデルへ渡す
  layers: [course_builder, frontend_learning_ui, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 生成の閉世界（_topic_available_references）は source_excerpt の本文を text に載せて id=topic_summary で渡し、
    プロンプトも「原文抜粋を指す ![[source:topic_summary]]」と書いていた。配信（build_topic_evidence_items）と原稿スタジオは
    同じ id を topic.summary（英語の要約文のことがある）の「トピック概要」に解決する。同じ名前が生成と配信で別の中身を指して
    いた（meaning）。閉世界の外の id を後段で検査する処理が無く、学習画面は引けない埋め込みの kind:id をそのまま出す。
    学習チャット・確認問題の「現在表示中の教材」は student_material を素のまま注入していた。処理軸 medium: 砂場の 20 トピックでは
    閉世界の外の id は 0 件で、生の記号は API で本文を読む経路（ハーネス）に見えていた。
generalization:
  level: repo_pattern
  general_form: 生成に渡す参照の一覧と、配信がその参照を解決する規則が、同じ id に別の中身を割り当てている
pattern: same-name-different-referents
discovery:
  perspective: [data_inspection, trace_walk]
  note: 砂場コースの student_material で ![[source:topic_summary]] の直前が「論文は…」と原文の引用を予告しているのに、配信は英語の要約文を出していた。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    原文抜粋の id を source_excerpt_evidence_id（linked_chunk_ids 先頭 / excerpt）に一本化し、閉世界にも配信と同じ id で渡す
    （プロンプトから topic_summary を外す。保存済みの topic_summary は従来どおりトピック概要に解決する）。
    drop_unresolved_evidence_embeds が学習画面と同じ引き方（kind:正規化ID → 同じ ID の別 kind）で引けない
    component / claim / source の埋め込みを外し、生成直後（_sanitize_topic_evidence_embeds・grounding_note に事実文）と
    配信時（topic_material_delivery_segments）の両方で通す。プロンプトへは material_text_for_prompt（式は $latex$ か短い原文、
    図は「（図）」、根拠の埋め込みは外す）を通した本文を載せる。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/core/lecture.py
    - backend/core/label_vocab.py
    - backend/api/routes/learning.py
    - backend/tests/test_ik0455_source_embed_delivery.py
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - 砂場でのコース内容の再生成（新しい閉世界で生成モデルが原文抜粋を配信と同じ id で書くか）
      - 保存済みの ![[source:topic_summary]] は再生成まで英語の要約文のトピック概要カードのまま
      - レクチャー（スライド）表示は evidence_items を持たないため、引ける ![[claim|component|source:…]] も「未解決」カードになる（IK-0458）
related: [IK-0438, IK-0457, IK-0458]
view_of: []
history: []
---

## 課題

同じ埋め込み id が生成と配信で別の中身を指し、引けない埋め込みは内部 ID のまま学習者とモデルに届いた。

## 発見の観点

保存済みの本文で埋め込みの前後の文脈と、配信が解決する中身を突き合わせた。

## 解決の観点

原文抜粋の id を配信の規則に揃えて一本化し、引けない埋め込みは画面と同じ規則で外す。

## 一般化

生成に渡す参照の一覧は、配信がその参照を解決する規則と同じ関数から作る。
