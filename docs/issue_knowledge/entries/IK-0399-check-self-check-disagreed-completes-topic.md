---
id: IK-0399
title: "確認問題の自己確認で「違っていた」を押すと、その場でトピックが完了（topic_completed: true）になり、本人が見立ての食い違いを申告した直後に「この確認を終えた記録を残しました」と表示されていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が確認問題の並置を見て、自分の見立てが合っていたかを申告する
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [meaning]
    governance: [completion]
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
    統制軸: 完了の確定に使う自己確認の集合（check_review.SELF_CHECK_ADVANCING）に disagreed が入っていた（completion）。接続軸: 是正 F1
    の設計意図（「違っていたが見比べて先へ進むと決めた」）と、ボタンの文言「違っていた」が学習者に伝える意味（見立てが合わなかった）がずれていた（meaning。medium:
    設計判断の誤りとも読める）。構造軸・処理軸は none。確認: §17.7 に「確認問題の『同意しない』で完了」、コードは self_check ∈ ('agreed','disagreed') で
    record_topic_check_pass を呼んでいた。
generalization:
  level: repo_pattern
  general_form: 選択肢の文言が伝える意味と、その選択肢に結びつけた状態遷移が食い違う
pattern: wording-mismatch
discovery:
  perspective: [reproduction, invariant_audit]
  note: 第 8 周の自己確認の往復で、押したボタンと完了フラグを突き合わせた。
resolution:
  perspective: [single_point_fix]
  note: >-
    SELF_CHECK_ADVANCING を ('agreed',) にし、disagreed は記録のみで完了にしない。応答に
    notice（label_vocab.CHECK_SELF_CHECK_DISAGREED_NOTICE・数字なし）を足し、完了にしなかった事実を返す。進行は止めない（トピックはロック表示でも開ける）。既存の固定テストを新しい集合に更新した。
  landed_in:
    - backend/core/check_review.py
    - backend/api/routes/learning.py
    - backend/api/schemas.py
    - backend/core/label_vocab.py
    - backend/tests/test_check_review_core.py
    - backend/tests/test_learning_chat_wave6_turns.py
    - docs/features/learning.md
    - frontend/public/js/app.js
    - backend/tests/test_learner_decision_notice_ui_static.py
    - docs/manual/student/02-student.md
  verification:
    methods: [guardrail]
    unverified:
      - 学習画面（app.js）の描画はブラウザで確かめていない（完了表示を応答の topic_completed に合わせ、notice を textContent で出す是正は静的検査のみ）
      - 是正 F1 の当初の意図（違っていたが先へ進む）を支持する判断があるか（オーナー確認はしていない）
      - 砂場での再演
related: [IK-0398]
view_of: []
history: []
---

## 課題

「違っていた」でトピックが完了する。

## 発見の観点

押したボタンと完了フラグの突き合わせ。

## 解決の観点

完了に使うのは「合っていた」だけにし、違っていた事実を文で返す。

## 一般化

選択肢の文言と状態遷移の食い違い。
