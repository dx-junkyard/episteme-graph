---
id: IK-0466
title: "書き直し（replace_message_id）で取り除いた往復が出した [出典18] が、次の往復で別のチャンクに振り直された（切り詰めた履歴に取り除いた往復の番号の控えが無く、採番器は残った履歴の最大番号から数え直した）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "学習者が会話の前の回答に付いた出典番号を、あとの往復でも同じ資料として参照できる"
  layers: [rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [information]
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
    処理・接続軸: 第 11 周 st-04 の seq 19 は learning.chat.rewrite で、truncate_chat_and_supersede
    がファントム質問の往復（[出典18]=f428f4e1 を出した）を取り除き、切り詰めた履歴で body.history を上書きした。書き直しの経路は保存済み履歴を読まず、残った最後の assistant ターンの
    citation_map には 18 が無いので、採番器は 17 の次から数えて a89d43a8 に 18 を振った（学習者の感想「source 18 now points to a different
    passage」）。構造軸: 番号の予約が「残っている履歴」に閉じ、会話で一度出した番号の集合を持っていなかった（representation）。
generalization:
  level: general
  general_form: "履歴を切り詰めると、切り詰めた部分で払い出した識別子の予約も一緒に消え、同じ識別子が別の対象に払い出される"
pattern: id-not-stable-across-versions
discovery:
  perspective: [trace_walk, data_inspection]
  note: "req-00028 / 00035 / 00043 の history ブロックと transcript の action_id（learning.chat.rewrite）を並べた。"
resolution:
  perspective: [representation_change, carry_through]
  note: >-
    truncate_chat_and_supersede が切り詰める前の全履歴から merged_citation_map（番号→チャンクの一対一）を取り、残る最後の assistant ターンの
    citation_map に載せて保存し、取り除いた往復を removed_history として返す。_SessionCitationNumbers は removed_history
    も予約し、読み込んだ番号の最大値を次の起点にし、別のチャンクがすでに持つ番号は割り当てない（_owner）。
  landed_in:
    - backend/api/routes/learning.py
    - backend/api/services.py
    - backend/tests/test_ik0466_0478_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（第 11 周の書き直しの往復を修正後に再実行していない）"
      - "切り詰めた結果 assistant ターンが1つも残らない書き直し・削除（控えを載せる先が無い）は、次の往復の採番器が removed_history から予約する書き直し経路だけが守られる（DELETE 経路のあとの新しい往復は 1 から）"
related: [IK-0432, IK-0444]
view_of: []
history: []
---

## 課題

書き直し（replace_message_id）で取り除いた往復が出した [出典18] が、次の往復で別のチャンクに振り直された（切り詰めた履歴に取り除いた往復の番号の控えが無く、採番器は残った履歴の最大番号から数え直した）

## 発見の観点

req-00028 / 00035 / 00043 の history ブロックと transcript の action_id（learning.chat.rewrite）を並べた。

## 解決の観点

truncate_chat_and_supersede が切り詰める前の全履歴から merged_citation_map（番号→チャンクの一対一）を取り、残る最後の assistant ターンの citation_map に載せて保存し、取り除いた往復を removed_history として返す。_SessionCitationNumbers は removed_history も予約し、読み込んだ番号の最大値を次の起点にし、別のチャンクがすでに持つ番号は割り当てない（_owner）。

## 一般化

履歴を切り詰めると、切り詰めた部分で払い出した識別子の予約も一緒に消え、同じ識別子が別の対象に払い出される。
