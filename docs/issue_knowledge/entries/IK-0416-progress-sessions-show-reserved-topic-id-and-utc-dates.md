---
id: IK-0416
title: "学習の進捗（GET .../progress）の学習履歴が予約疑似トピックの内部 id「_discussion」をそのまま見出しにし、日付を UTC の暦日で作り、コースの論文に直付けした議論（_doc:*）を並べない（あわせて引っかかり候補の context_label も旧行の _discussion を素通しする）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者がコースの進捗タブで最近の学習の履歴を見る（B層）
  layers: [learner_experience_b, discuss]
classification:
  axes:
    processing: [wording, logic]
    structure: [none]
    connection: [target]
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
    処理軸: calculate_progress は topic_id がコースのトピックに見つからないとき topic_id を見出しに使い（wording）、日付を timestamptz の月日から直接作っていた（UTC。logic）。get_tension_digest も payload の context_label を素通ししていた。接続軸: document 直付けの議論は会話コンテキストの course_id がセンチネル（_doc:{id}）で、コースの進捗の SQL の対象に入らなかった（target。medium: コース外の会話をコースの履歴に含めるかは設計判断で、ここではコースのソース論文に限った）。構造軸: 予約 id の表示名の正本（core.topic_labels）はある（none）。確認: 第 8 周の学生が「_discussion という変な名前」「今日やったはずが 9/27」と記録。同じ観測の「記録には誤解が 1 つあるのに misconceptions: 0」は、進捗が本人確定の誤解メモだけを数える是正 F5 の設計どおり（記録側の誤解は AI 検出の記録）で、数え方は変えていない（IK-0415 で記録側の表示を「あなたの確認前」にした）。
generalization:
  level: repo_pattern
  general_form: 内部の予約 id と UTC の暦日が、表示名と利用者の暦日に変換されないまま学習者の見出しになる
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: 学生ペルソナが進捗タブを読み、内部名と前日の日付を記録した。
resolution:
  perspective: [canonical_source, carry_through]
  note: >-
    見出しは _progress_session_topic_label（実在トピックの題名 → core.topic_labels.reserved_topic_label の表示名 + 直付け議論なら論文の題名）で作り、日付は learner_date_label（core/learner_time.py の日本時間・固定オフセット・新しい env なし）で作る。コースのソース論文の _doc:{id} の会話も同じ SQL で引いて並べる。get_tension_digest の context_label も読み時に表示名へ変換（行は書き換えない）。誤解の数は是正 F5 のまま。
  landed_in:
    - backend/api/services.py
    - backend/core/learner_time.py
    - backend/tests/test_learner_progress_labels.py
  verification:
    methods: [guardrail]
    unverified:
      - 実 Postgres での ANY(:doc_context_ids) の実行（docker 未達）
      - 砂場での再演
      - 進捗の他の日付表示（フロント側の整形）
related: [IK-0403, IK-0415, IK-0417]
view_of: []
history: []
---

## 課題

進捗の履歴に内部名と前日の日付が出て、直付けの議論が無い。

## 発見の観点

実ペルソナの進捗タブの読み（第 8 周）。

## 解決の観点

予約 id の表示名の正本と日本時間の正本を通す。

## 一般化

内部 id と UTC の暦日は、表示の直前で正本を通さないとそのまま見出しになる。
