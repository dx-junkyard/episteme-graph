---
id: IK-0494
title: "回答が引用していない別論文のチャンク（BAO の問いに Cep B のチャンク）が、学習者に見せる出典の一覧に並んでいた"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17.10
feature_context:
  realizing: "学習者に見せる出典の一覧が、回答が実際に根拠にした箇所だけになる"
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [representation]
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
    処理軸: 応答の sources と履歴の焼き込みは「文脈に採用した出典」（類似度 0.30 以上）をそのまま返していた（routes/learning.py の LearningChatResponse(sources=cited_sources)
    と assistant_meta.sources）。IK-0472 の並べ替えでトピックの論文が先に来ても、採用の下限を満たす別論文のチャンクは一覧に残り、回答自身が「無関係」と言う
    チャンクが出典として見えた（logic）。構造軸 medium: 1 つの cited_sources が「文脈に置いたもの」と「根拠として見せるもの」の2つの意味を兼ねていた
    （representation）。
generalization:
  level: general
  general_form: "根拠として見せる一覧を、実際に使われたかではなく、候補に入ったかどうかで決める"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "第 12 周の頭脳（persona / product）のメモと、応答の sources・回答本文の [出典N] を突き合わせた。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    _sources_cited_in_answer で、回答本文の [出典N] が指す出典だけを応答の sources・履歴の sources に載せる（本体 RAG と前提知識の経路。ストリーミングの final は
    同じ応答なので同じ）。番号は振り直さない — 採番器の対応表（citation_map）は採用した全チャンクの番号を控え続けるので、次の往復で別チャンクへ同じ番号を
    振らない（IK-0444 / IK-0466）。tier・content_grounding は文脈に採用した出典から決めたまま（未踏ガードの判定と揃える）。
    本文が1つも引用していないのに出所が course_material / other_material の往復は、その出所を決めた出典（origin が出所と同じもの）を並べる
    （_displayed_sources_for）。出所の分類を引用の集合から決め直す案は採らなかった — 出所の判定（IK-0382）は「文脈に置き、問いに関わった資料」で
    決まり引用の有無では決まらず、未踏ガードも同じ判定で生成前に掛かるので、引用から決め直すと帯（出典を追えない AI の説明）とガード（掛けていない）が
    食い違う。帯が「教材に基づく」で出典の一覧が空、という食い違い（第 5 波で除いたもの）も作らない。トピック教材だけで course_material になった往復は
    番号付き出典が無いので従来どおり空。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0492_0496_chat_turn_fixes.py
    - backend/tests/test_ik0432_0437_chat_turn_fixes.py
    - backend/tests/test_ik0444_0451_chat_turn_fixes.py
    - backend/tests/test_ik0466_0478_chat_turn_fixes.py
    - backend/tests/test_prerequisite_resolution.py
    - backend/tests/test_retrieved_structure_route.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演"
      - "画面の出典タブ・チップ（app.js は応答の sources を描くだけであることは確かめていない）"
      - "回答が 1 つも引用しないときは出所を決めた出典が並ぶので、別論文の近いチャンクがその往復だけは一覧に残り得る"
related: [IK-0472, IK-0444, IK-0466]
view_of: []
history: []
---

## 課題

回答が引用していない別論文のチャンクが出典の一覧に並んでいた。

## 発見の観点

頭脳のメモと応答の出典・本文の引用番号を突き合わせた。

## 解決の観点

見せる出典は本文が引用したものだけにし、番号の対応表は採用した全チャンクを控え続ける。

## 一般化

根拠として見せる一覧を、使われたかではなく候補に入ったかで決める。
