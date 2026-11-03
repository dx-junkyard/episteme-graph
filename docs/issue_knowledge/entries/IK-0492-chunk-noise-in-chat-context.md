---
id: IK-0492
title: "学習チャットの文脈に置いたチャンク本文に置換文字（U+FFFD）と arXiv の版の刻印（arXiv:2606.00411v1 [astro-ph.CO] 28 May 2026）がそのまま入っていた"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17.10
feature_context:
  realizing: "学習チャットの回答が、資料本文として読める部分だけを根拠に組み立てられる"
  layers: [rag_chat]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
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
    処理軸: routes/learning.py の本体 RAG・前提知識の文脈組み立ては検索結果の text をそのまま `[出典N]` の区画に置いていた（旧 learning.py の
    cited_chunks.append）。PDF の文字層由来の本文に残る U+FFFD（字形の無い記号の痕）と、各ページ余白の arXiv の版の刻印を落とす処理が文脈の直前に無かった
    （input_handling）。第 12 周の実プロンプト 74 件中 24 件に U+FFFD。構造軸 none（保存データを変える必要は無く、写しの整形で足りる）。接続軸 none: 値は
    落ちておらず、余計な値が素通りした。
generalization:
  level: general
  general_form: "抽出した外部テキストの雑音（置換文字・版の刻印）を、推論器の入力へ置く直前に取り除いていない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "第 12 周の mailbox の実プロンプト（req-*.json）を U+FFFD と arXiv の刻印で走査した。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    core/text_hygiene.py に sanitize_source_text_for_prompt（制御列の除去 + U+FFFD の除去 + 「ID + 分類 + 日付」が揃う arXiv の刻印だけの除去 + 空行の詰め）を足し、
    学習チャットの文脈に置く3箇所（検索チャンク・表示中のトピック教材・前提知識の抜粋）と出典の抜粋（quote）に掛けた。日付の無い参考文献の
    arXiv:ID [分類] は本文として残す。保存データ（chunks）は変えない。
  landed_in:
    - backend/core/text_hygiene.py
    - backend/api/routes/learning.py
    - backend/tests/test_ik0492_0496_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（実プロンプトから U+FFFD と刻印が消えたことは実データで見ていない）"
      - "U+FFFD が落とした記号（積分記号など）の意味は戻らない（取り除くだけ）"
      - "刻印が縦書き（1 字ずつの改行）で抽出された本文は正規表現に当たらない"
related: [IK-0464]
view_of: []
history: []
---

## 課題

学習チャットの文脈に置いたチャンク本文に置換文字（U+FFFD）と arXiv の版の刻印がそのまま入っていた。

## 発見の観点

第 12 周の実プロンプトを走査した。

## 解決の観点

文脈に置く直前の写しを整える関数を text_hygiene に置き、学習チャットの文脈組み立てに掛けた（保存データは不変）。

## 一般化

抽出した外部テキストの雑音を、推論器の入力へ置く直前に取り除いていない。
