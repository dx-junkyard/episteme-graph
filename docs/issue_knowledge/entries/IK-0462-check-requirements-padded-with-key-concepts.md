---
id: IK-0462
title: "確認問題の answer_requirements が毎回トピックの重要概念・学習目標で埋められ、講評が問いと無関係な要素を「触れていない」と返していた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が確認問題の答えの要件を組み、確認問題の講評がその要件を閉世界として照合する
  layers: [course_builder, rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
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
    処理軸: _fill_check_question_detail が要件の末尾に key_concepts と learning_objectives を無条件に足していた（上限5まで）。要件は「その問いの答えに含むべき要素」の意味なのに、トピックの概念一覧で代替していた。講評のプロンプトは要件のすべてに1件ずつ判定を求めるので、無関係な要件が「触れていない」になる。
generalization:
  level: repo_pattern
  general_form: 欠けた項目を近くの一覧（トピックの重要概念）で埋め、代替であることが後段に伝わらない
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [data_inspection, trace_walk]
  note: 20 本の下書きの要件と重要概念を突き合わせ、学習者の確認問題の講評プロンプトまで辿った。
resolution:
  perspective: [single_point_fix]
  note: >-
    要件の後付けをやめる（数式の要件は問いが式を参照するときだけ・要件が空なら固定文1つ）。生成の下書きを次の生成へ渡すときは、重要概念・学習目標と一致する要件を外す。既存テスト 2 件（test_course_content_builder.py の legacy / partial）は旧挙動（埋める）を固定していたので、意図した変更として期待値を更新した。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0459_0465_course_draft_regen11.py
    - backend/tests/test_course_content_builder.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成
      - 保存済みの確認問題（配信中）の埋められた要件は再生成まで残る — 配信側（routes/learning.py）では外していない
related: [IK-0429, IK-0438, IK-0440, IK-0443, IK-0454]
view_of: []
history: []
---

## 課題

確認問題の要件がトピックの概念一覧で埋められていた。

## 発見の観点

下書きの要件と重要概念の重なりを数え、講評の照合まで辿った。

## 解決の観点

要件は問いの答えの要点だけにし、埋める処理をやめる。

## 一般化

欠けた項目を別の一覧で埋めない。空なら空である事実を渡す。
