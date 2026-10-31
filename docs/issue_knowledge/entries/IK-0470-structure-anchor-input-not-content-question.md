---
id: IK-0470
title: "構造帰属（方法B）の入力に、お礼・予想の表明の発話が問いとして入り、cited_chunks は回答が引用した出典ではなく検索上位3件だった"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "学習者の問いを、回答が実際に示した箇所に結び付けて構造へ帰属する"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
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
    処理軸: 痕跡は kind=question で全往復に記録され、worker
    の未帰属クエリは発話の種類を見ないので、お礼（「ありがとうございました」）・理解サイクルの予想の表明も帰属バッチに入った（logic）。接続軸: payload の cited_chunk_ids は
    cited_sources[:3]（検索順の上位3件）で、回答本文の [出典N] が指したチャンクではなかった（information）。
generalization:
  level: general
  general_form: "後段の解析に渡す根拠の一覧を、回答が実際に使ったものではなく候補の上位から作る"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "req-00030 / 00046 / 00054 / 00070 / 00078 の帰属バッチで、回答の引用番号と cited_chunks を突き合わせた。"
resolution:
  perspective: [carry_through, single_point_fix]
  note: >-
    _chunk_ids_cited_in_answer が回答本文の [出典N] ∩ cited_sources を引用順に返し（引用が無ければ空）、payload の cited_chunk_ids
    にする。雑談（CHIT_CHAT）・お礼で始まり問いを含まない発話（_is_closing_led_statement）・cycle_mode=elicit の往復は payload に
    anchor_skip_reason を残し、worker の未帰属クエリがそれを除外する（痕跡そのものは残す — P4）。同じ往復は tension_hint も立てない。
  landed_in:
    - backend/api/routes/learning.py
    - backend/core/structure_anchor/worker.py
    - backend/tests/test_ik0466_0478_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演"
      - "discuss の解釈の表明（問いの形でない発話）は帰属の対象のまま（疑いの帰属に使えるため）"
related: [IK-0474]
view_of: []
history: []
---

## 課題

構造帰属（方法B）の入力に、お礼・予想の表明の発話が問いとして入り、cited_chunks は回答が引用した出典ではなく検索上位3件だった

## 発見の観点

req-00030 / 00046 / 00054 / 00070 / 00078 の帰属バッチで、回答の引用番号と cited_chunks を突き合わせた。

## 解決の観点

_chunk_ids_cited_in_answer が回答本文の [出典N] ∩ cited_sources を引用順に返し（引用が無ければ空）、payload の cited_chunk_ids にする。雑談（CHIT_CHAT）・お礼で始まり問いを含まない発話（_is_closing_led_statement）・cycle_mode=elicit の往復は payload に anchor_skip_reason を残し、worker の未帰属クエリがそれを除外する（痕跡そのものは残す — P4）。同じ往復は tension_hint も立てない。

## 一般化

後段の解析に渡す根拠の一覧を、回答が実際に使ったものではなく候補の上位から作る。
