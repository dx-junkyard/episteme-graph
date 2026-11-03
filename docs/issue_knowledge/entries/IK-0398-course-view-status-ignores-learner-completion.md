---
id: IK-0398
title: "確認問題でトピックを完了（completed_topic_ids に記録）しても、コース画面（GET /api/learning/courses/{id}）はマスターコースの作成時の状態（in_progress / locked / progress_pct 0）をそのまま返し、次のトピックの前提ゲートも完了済みのトピックを前提として聞き直していた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習画面が本人の進み具合をコースの状態として示し、終えた前提を聞き直さない
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [completion]
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
    統制軸: トピックの「済み」の正本は本人の完了記録（learning_states.progress_data.completed_topics）なのに、コース画面と前提ゲートは別の値（マスターの
    status / 前提の明示的な理解申告）を完了とみなしていた（completion）。接続軸: 完了記録がコース画面の投影と前提ゲートの判定へ渡っていなかった（information。medium:
    読み手が正本を読んでいない、と読む方が近い）。構造軸は none（完了記録の表現は足りている）。処理軸は none。確認: 確認問題で t0 を完了した直後の GET /courses/{id} で
    t0 in_progress・t1 locked・progress_pct 0、t1 の前提ゲートが t0 を再度聞いた。
generalization:
  level: repo_pattern
  general_form: 完了の正本とは別の値を画面の状態・判定に使い、本人が終えたことが反映されない
pattern: completion-defined-by-proxy
discovery:
  perspective: [reproduction]
  note: 第 8 周で確認問題の完了直後にコースを開き直した。
resolution:
  perspective: [canonical_source]
  note: >-
    GET /courses/{id} で本人の完了記録から読み時に状態を導出して重ねる（_overlay_learner_progress: 完了 → completed、その直後の locked →
    in_progress、章は全完了 → completed / 一部 → in_progress、progress_pct
    は章内の完了割合。保存データは変えず取得失敗はマスターの値のまま）。前提ゲート（services.check_prerequisites）は、前提が同コースのトピックで本人が完了していれば聞き直さない（読み時の判定・記帳キーは不変）。
  landed_in:
    - backend/api/routes/learning.py
    - backend/api/services.py
    - backend/tests/test_learning_chat_wave6_turns.py
    - docs/backend/rag-chat.md
  verification:
    methods: [guardrail]
    unverified:
      - 砂場・ブラウザでの再演（学習画面のサイドバーに反映されるか）
      - locked 表示でも API はトピックを開ける（UI のみのロック・§17.7 の起票しない観測）
      - GET /courses 一覧側の進捗表示
related: [IK-0399]
view_of: []
history: []
---

## 課題

完了したのにコース画面と前提ゲートに反映されない。

## 発見の観点

完了直後にコースを開き直した。

## 解決の観点

完了記録を正本として読み時に導出する。

## 一般化

完了の正本以外の値で状態を決める型。
