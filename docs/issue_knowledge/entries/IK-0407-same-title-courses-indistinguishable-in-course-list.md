---
id: IK-0407
title: "別々の教員が作った同じ題名のコースが、学習画面のコース選択で題名だけの選択肢として並び、どちらがどちらか見分けられない（一覧 DTO が見分けの材料を description しか持たない）"
status: open
recorded_at: 2026-09-28
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が受講したいコースを一覧から選ぶ
  layers: [frontend_learning_ui, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [representation]
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
    構造軸: 選択肢の表示が題名だけで、人が見分けるための区別（作成者・出典の論文・説明）を表せない
    （representation）。一覧 DTO（LearningCourseOut）も id / title / 公開状態 / description しか持たない。
    接続軸: description は DTO にあるが、select の選択肢には title 属性（ホバー）としてしか運ばれて
    いなかった（information。medium: 説明が空のコースどうしでは運んでも区別できない）。処理・統制は none。
generalization:
  level: general
  general_form: 人向けの呼称（題名）だけで一覧を並べ、同じ呼称の別物を見分ける材料が一覧に無い
pattern: same-name-different-referents
discovery:
  perspective: [reproduction]
  note: 第 8 周の学生が、別々の教員が作った同名コース 2 件を一覧で見分けられなかった。
resolution:
  perspective: [single_point_fix, pending]
  note: >-
    フロント側は、題名が一覧の中で重複する選択肢にだけ description の冒頭（40 字まで・空白を畳む）を
    「題名 — 説明」の形で添えた（重複しない選択肢は題名だけのまま・数値は描かない）。説明が空のコース
    どうしは依然として見分けられない。残りは一覧 DTO に見分けの材料を足すこと（例: 出典の論文の題名や
    作成者の表示名）で、backend/api/routes/learning.py の list_courses（own / enrolled / public / group の
    4 つの SELECT）と backend/api/schemas.py の LearningCourseOut を持つ担当の範囲。
  landed_in:
    - frontend/public/js/app.js
    - docs/manual/student/02-student.md
    - backend/tests/test_learner_facts_wave6_ui_static.py
related: []
view_of: []
history: []
---

## 課題

同じ題名のコースがコース選択に題名だけで並び、見分けられない。

## 発見の観点

ペルソナの操作の再現で、同名コース 2 件を選び分けられないことが見えた。

## 解決の観点

画面側は重複する題名にだけ説明の冒頭を添えた。説明が空のときの区別には一覧 DTO の拡張が要る。

## 一般化

人向けの呼称だけで一覧を作ると、同じ呼称の別物が区別できない。
