---
id: IK-0469
title: "逆質問への「はい、主結果のトピックは読みました」が前提の確認として記帳されず（「確認はまだ記録していません」）、同じ前提を持つ別のトピックで逆質問が再び出た"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "本人が明示的に答えた前提の確認を、同じ前提を持つトピックすべてで尊重する"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [input_handling]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: _is_explicit_prerequisite_acknowledgement は「理解しています」「わかっています」等の現在形の語だけを受け、「読みました」「学びました」の完了形と英語の "I have
    read / I studied" を受けなかった（input_handling）。構造軸: 記帳キーは前提の名前だけで、topic_id
    で結ばれた同じトピックを別の名前で前提にする別トピックでは、記帳が当たらない（representation, medium: 砂場では前提名が同じだったかは確かめられていない）。
generalization:
  level: general
  general_form: "本人の明示的な肯定を語の一覧で受けるが、一覧が現在形の言い方だけで、同じ意味の完了形の返事を拾わない"
pattern: wording-mismatch
discovery:
  perspective: [data_inspection]
  note: "req-00029 / 00036 / 00044 で、逆質問の直後の学習者発話と回答冒頭の事実文を並べた。"
resolution:
  perspective: [single_point_fix, carry_through]
  note: >-
    肯定の返事（はい / ええ / うん /
    yes）で始まる発話に限って「読みました・学びました・理解しました」等の完了形を受ける（「私は…と読みました。合っていますか？」の解釈の表明は受けない・否定形は従来どおり記帳しない）。英語の完了形（I have read
    / I have studied）は単独で、I read / I studied は yes で始まるときだけ受ける。記帳は前提の名前に加えて表示名と topic:{topic_id}
    の鍵も残し、判定は3つのどれかで当たれば聞き直さない。
  landed_in:
    - backend/api/services.py
    - backend/tests/test_ik0466_0478_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（別トピックの逆質問が出なくなるか）"
      - "記帳済みの旧データ（名前だけの鍵）は topic 鍵を持たない — 次に確認したときから効く"
related: [IK-0396, IK-0424]
view_of: []
history: []
---

## 課題

逆質問への「はい、主結果のトピックは読みました」が前提の確認として記帳されず（「確認はまだ記録していません」）、同じ前提を持つ別のトピックで逆質問が再び出た

## 発見の観点

req-00029 / 00036 / 00044 で、逆質問の直後の学習者発話と回答冒頭の事実文を並べた。

## 解決の観点

肯定の返事（はい / ええ / うん / yes）で始まる発話に限って「読みました・学びました・理解しました」等の完了形を受ける（「私は…と読みました。合っていますか？」の解釈の表明は受けない・否定形は従来どおり記帳しない）。英語の完了形（I have read / I have studied）は単独で、I read / I studied は yes で始まるときだけ受ける。記帳は前提の名前に加えて表示名と topic:{topic_id} の鍵も残し、判定は3つのどれかで当たれば聞き直さない。

## 一般化

本人の明示的な肯定を語の一覧で受けるが、一覧が現在形の言い方だけで、同じ意味の完了形の返事を拾わない。
