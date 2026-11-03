---
id: IK-0921
title: "Admin Copilot がナレッジライブラリのタブで受けた「これを確定したら学生の画面には何が出る？」に、分野の地図の公開（凍結）の手順で答える — 意図の解決の語表（intent._KEYWORDS）に概念レジストリの項目が無く「確定」が地図の凍結に当たり、KB 検索も開いているタブを見ない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が画面の区画について案内役に尋ね、その区画の手順と学生への届き方を知る"
  layers: [admin_copilot, concept_registry, docs]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
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
    接続軸 target: core/admin_assistant/intent.py の _KEYWORDS に library.* の行が無く（atlas.freeze_skeleton は「確定」を持つ
    :65）、resolve_capability（:116-145）のタブの加点はキーワードが当たった項目にしか効かない。IK-0865 で capabilities.py:822-885 に概念レジストリの 4
    項目を足したが、語表は同じ時に足されなかった（同じ項目の語彙の正本が 2 箇所）。routes/admin_assistant.py:294-300 _guidance_response の
    kb.search（core/admin_assistant/knowledge.py:81-118）は screen を受けず、タブの加点が無い。学生への届き方を書いた節は
    docs/admin_operations/library.md#identity-candidates の 1 行だけ。
generalization:
  level: repo_pattern
  general_form: "案内の項目を足したのに、項目を言葉から選ぶ語表が別の場所にあって追随せず、隣の機能の項目が答える"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-02 がライブラリのタブから問い、回答の出所が admin_operations/atlas.md#freeze だった（面の審判 A10・A11）。run:
    uxsim/runs/c-astro-r25-share-r3/20261023T032742Z-99528 seq14。語表と登録簿を突き合わせた。
resolution:
  perspective: [canonical_source]
  note: >-
    語表の正本を登録簿に移した: core/admin_assistant/schema.py の Capability に keywords を足し、intent.py の _KEYWORDS を撤去して resolve_capability は cap.keywords を読む（既存 19 項目は語をそのまま移設、library.* の 5 項目に同一性・結びつけ・共通部品・確定・学生・別名・関係・地図との対応等の語）。knowledge.search に screen を足し、語の重なりがある項目のうち開いているタブの項目に加点し、_guidance_response がタブを渡す。docs/admin_operations/library.md に「確定すると学生に何が見えるか」（#what-students-see）の節を足し、同一性の候補の節から結んだ。
  landed_in:
    - "backend/core/admin_assistant/schema.py"
    - "backend/core/admin_assistant/capabilities.py"
    - "backend/core/admin_assistant/intent.py"
    - "backend/core/admin_assistant/knowledge.py"
    - "backend/api/routes/admin_assistant.py"
    - "docs/admin_operations/library.md"
    - "backend/tests/test_share_round25_r3_fixes.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "語彙を登録簿 1 箇所に置き、intent は登録簿から読むだけにした。新しい項目を足せば語も同じ所に足すことになる。固有の差分: KB 検索のタブ加点は語の重なりがある項目に限り、関係の無い節をタブだけで選ばない。"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面の Copilot でライブラリのタブから尋ねたときの回答（次周の te-05 × (a') の最後の 1 手）"
      - "LLM の意図リファインを有効にした経路"
related: [IK-0865]
view_of: []
history: []
---

## 課題

症状: ライブラリのタブから「確定すると学生に何が出るか」を尋ねると、分野の地図の公開手順が返る。原因: 意図の解決が語表で行われ、語表に概念レジストリの項目が無く、検索もタブを見ない。

## 発見の観点

教員ペルソナの通し受講（reproduction）。語表・登録簿・検索の 3 箇所を辿った（trace_walk）。

## 解決の観点

是正済み（第 3 波）。意図の解決の語を登録簿（Capability.keywords）に移して正本を 1 つにし、KB 検索に開いているタブを渡し、学生に何が見えるかの節を KB に足した。

## 一般化

同じ項目の語彙が 2 箇所にあって片方だけ足される型。確かめ方は、ライブラリの各区画のタブから代表の問いを 1 つずつ送り、回答の出所（citations の先頭）がそのタブの節であることを見ること。
