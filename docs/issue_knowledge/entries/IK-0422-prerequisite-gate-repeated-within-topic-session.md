---
id: IK-0422
title: "前提確認の逆質問が、同じトピックの会話で2問目以降にもまた出て、逆質問の往復を書き直すと同じ逆質問が繰り返し返っていた（逆質問済みかを直前の往復だけで判定し、書き直しは逆質問の往復を履歴から切り詰めていた）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習チャットが前提確認をトピックの会話で一度だけ挟み、その後は問いに答える
  layers: [rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [resume]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: 「逆質問を出したか」を履歴の直前の assistant ターンだけで判定していた（routes/learning.py
    _previous_turn_was_prerequisite_gate）。逆質問のあとに1往復挟むと判定が外れ、前提の確認が記帳されていない限り
    check_prerequisites が同じ逆質問を返した（logic）。接続軸: 書き直し（replace_message_id）は逆質問の往復ごと
    サーバの履歴を切り詰めるので、「逆質問を出した」事実が次の処理へ渡らなかった（information）。統制軸:
    書き直しは同じ問いの再実行で、逆質問は再実行に対して一度きりになっていなかった（resume。medium: ordering とも読める）。
    構造軸は none（履歴に逆質問の本文が残るので、状態を足さずに履歴全体から読めた）。確認: 第 9 周の transcript
    で、書き直した問いに同じ逆質問が返り、別の往復のあとにもまた逆質問が返った。
generalization:
  level: repo_pattern
  general_form: 一度だけ挟むべき確認の「済み」を、会話全体ではなく直前の一手から判定する
pattern: completion-defined-by-proxy
discovery:
  perspective: [reproduction]
  note: 第 9 周の transcript で、逆質問が返った往復の前後（書き直し・次の問い）を並べて読んだ。
resolution:
  perspective: [single_point_fix, carry_through]
  note: >-
    逆質問は (トピック, セッション) につき1回。履歴のどこかの assistant ターンにこのトピックの逆質問があれば
    （_prerequisite_gate_asked_in_history）、また書き直し（replace_message_id）の往復では、逆質問を出し直さず
    通常どおり答え、先頭に label_vocab.PREREQUISITE_GATE_SKIPPED_NOTICE（確認はまだ記録していない事実文・数値なし）を
    添える。check_prerequisites は呼んだまま（「理解している」の記帳の責務は変えない）。
  landed_in:
    - backend/api/routes/learning.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0422_prerequisite_gate_once.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 書き直しの元の問いが逆質問ではなく通常の回答だった往復でも、書き直しでは逆質問を出さない（切り詰めた往復の中身を見ていない）
related: [IK-0383, IK-0396]
view_of: []
history: []
---

## 課題

前提確認の逆質問が同じトピックの会話で何度も出る。

## 発見の観点

書き直しと次の問いの往復を並べて読んだ。

## 解決の観点

履歴全体から「逆質問を出したか」を読み、書き直しも出し直しの対象にしない。

## 一般化

一度だけ挟むべき確認の「済み」を直前の一手だけで判定する型。
