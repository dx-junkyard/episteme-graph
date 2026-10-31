---
id: IK-0385
title: "受講登録（POST /courses/{id}/enroll）の応答はコースの列（is_template / is_published / is_enrollable / visibility）だけで登録が成立した事実を持たず、登録後に false になる is_enrollable を学生 4 名全員が「受講できなかった」と読んだ"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が受講開始の操作が成立したことを応答から読める
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
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
    構造軸で、応答型 LearningCourseOut が一覧行の投影で、「この操作が成立した」を表す欄を持たなかった
    （representation）。接続軸で、is_enrollable の意味（まだ受講していない公開コースか）が、登録操作の応答の
    文脈では「受講できるか」と読まれた（meaning）。IK-0369 で一覧行と同じ投影に揃えた結果、登録後は
    is_enrollable=false が必ず出るようになり、読み違いが顕在化した。処理・統制は none。
generalization:
  level: repo_pattern
  general_form: 操作の応答を対象の状態の投影だけで返し、操作が成立した事実を表す欄を持たない
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: ペルソナ通し受講 第 8 周の学生 4 名が同じ読み違い。
resolution:
  perspective: [explicit_contract]
  note: >-
    応答型を LearningEnrollOut（LearningCourseOut の拡張）にし、enrolled: true と事実文 notice
    （正本 core/label_vocab.py::COURSE_ENROLLED_NOTICE・数字なし）を追加した。既存フィールドは不変。
  landed_in:
    - backend/api/routes/learning.py
    - backend/api/schemas.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0369_enroll_response.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での実応答と、学習画面が notice を描くか（app.js は応答の notice を読まない。画面は受講後の遷移で成否が分かる）
related: [IK-0369]
view_of: []
history: []
---

## 課題

受講登録の応答が失敗に読める。

## 発見の観点

学生 4 名の同じ読み違い。

## 解決の観点

操作の成立を表す欄と事実文を足す。

## 一般化

操作の応答に成立の事実が無い。
