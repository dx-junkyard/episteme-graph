---
id: IK-0384
title: "学習者向けのコース詳細（GET /api/learning/courses/{id}）が course_content_status をそのまま返し、件数（式・部品・対応トピック）・document_id・draft_errors・uncovered_sections(_dropped)（PDF の柱や図軸ラベルの断片）が学習者に届いていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者にはコースの内容だけを見せ、生成過程の内部記録（件数・内部 ID）を見せない
  layers: [rag_chat, course_builder]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [condition]
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
    構造軸で、LearningCourseDetail.course_content_status が型なしの dict で、_set_content_status が任意キーを
    足す内部記録をそのまま運べる入れ物だった（学習者 DTO はホワイトリストのはずが、この欄だけ素通し）。
    接続軸で、「学習者向けである」という宛先の条件が get_course の射影（_project_topics_for_learner）で
    この欄に適用されていなかった（condition。medium: 表現の問題とも読める）。処理・統制は none。
    確認: app.js は course_content_status を一切読まない（学習画面の表示に要らない）。KO10 / 数値非表示に反する。
generalization:
  level: repo_pattern
  general_form: 宛先別の射影をホワイトリストで組んだのに、型なしの入れ物の欄だけ中身ごと素通しする
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [reproduction]
  note: ペルソナ通し受講 第 8 周の学生が受け取ったコース詳細の応答。
resolution:
  perspective: [fail_closed]
  note: >-
    学習者向けの射影 _project_topics_for_learner で course_content_status を状態の語だけ
    （course_content_state = status の trim 済み文字列）に落とした。status が無ければ空 dict。
    教員向けの経路（PUT の応答・原稿スタジオの course-structure）と保存データは変えない。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0384_learner_course_status_projection.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での GET /api/learning/courses/{id} の実応答
      - course_content_status 以外の型なし欄（現時点で学習者 DTO に他は無いことをコードで確認したのみ）
related: [IK-0370]
view_of: []
history: []
---

## 課題

学習者に生成過程の件数と内部 ID が届く。

## 発見の観点

学生が受け取った応答の中身。

## 解決の観点

学習者向けの射影で状態の語だけを残す。

## 一般化

型なしの欄だけ宛先の射影をすり抜ける。
