---
id: IK-0417
title: "「今日のあなたの言葉」（GET .../cycle/todays-words）が、会話履歴の行の更新時刻（直近24時間）で「今日」を判定して行内の前日の発話まで並べ、全発話に同じ時刻を付け、topic_id を素通しし、「はい、理解しています。」のような返事まで含める"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が議論の着地で、今日の自分の発話の逐語を書き置きに引用する（理解サイクル・帰還の扉）
  layers: [understanding_cycle]
classification:
  axes:
    processing: [logic, input_handling]
    structure: [representation]
    connection: [none]
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
    構造軸: learning_chat_history は (user, course, topic) ごとに履歴全体を 1 行の JSONB で持ち、各メッセージに時刻が無い（representation）。処理軸: その代わりに行の updated_at を全メッセージの時刻とし、直近24時間の窓で「今日」を決めていた（logic — 行内の前日の発話が混ざり、同じ時刻が並ぶ）。相づち・了解だけの発話を除く規則が無かった（input_handling）。接続軸: 発話の時刻は interest_traces.payload.message_id の痕跡に既にあった（none）。確認: 第 8 周の学生が「今日の、と書いてあるのに昨日の言葉」「時刻がいくつか全く同じ」「はい、理解していますまで入っている」と記録。
generalization:
  level: repo_pattern
  general_form: 個々の要素に時刻が無い集約行の更新時刻を、要素の時刻として使う
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction]
  note: 学生ペルソナが翌日に扉とトレイを開き、前日の発話と同じ時刻の並びを記録した。
resolution:
  perspective: [single_point_fix, carry_through]
  note: >-
    発話の時刻を、その発話が記録した痕跡（interest_traces.payload.message_id）の created_at にし（JOIN LATERAL）、痕跡の無い発話は時刻が分からないので載せない。「今日」は日本時間の 0 時以降（core/learner_time.learner_today_start_utc）。相づち・了解だけの発話は core/cycle/schema.py の FILLER_* で外す（保守的 — 問いを含む・長い発話は残す）。DTO は topic_id を出さず topic_label（題名 / 予約疑似トピックの表示名）。会話履歴のメッセージに時刻を足す案は、クライアントが送り返す履歴で上書きされるため採らなかった。
  landed_in:
    - backend/core/cycle/queries.py
    - backend/core/cycle/derive.py
    - backend/core/cycle/schema.py
    - backend/api/routes/cycle.py
    - backend/core/learner_time.py
    - backend/tests/test_return_door_core.py
    - backend/tests/test_return_door_api.py
    - docs/features/return_door_design.md
  verification:
    methods: [guardrail]
    unverified:
      - 実 Postgres での JOIN LATERAL（interest_traces の message_id 照合）の実行と性能（docker 未達）
      - 痕跡を記録しない発話（楽屋・一部の経路）がトレイから消えること
      - 砂場での再演
related: [IK-0416]
view_of: []
history: []
---

## 課題

「今日の言葉」に前日の発話と返事が同じ時刻で並ぶ。

## 発見の観点

実ペルソナの翌日の再訪（第 8 周）。

## 解決の観点

要素自身の時刻（痕跡）で判定し、日本時間の今日で切る。

## 一般化

集約行の更新時刻は要素の時刻ではない。
