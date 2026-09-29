---
id: IK-0390
title: "学習チャットの全域ベクトル検索が、参考文献の列・データの所在・謝辞・脚注の URL・一語一行に割れた図の目盛りの区画を本文と区別せずに返し、学習者に出典1〜8として見せ [出典N] の引用先にしていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者の質問に、論文の本文を根拠にして答える
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
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
    処理軸で、検索は埋め込みの近さだけで区画を選び、その区画が本文かどうかを見ていなかった
    （backend/api/services.py search_chunks_with_metadata の SQL は embedding IS NOT NULL と可視性だけが条件）。
    接続軸で、チャンカーが source_metadata.section_title に書いた「References」「Acknowledgements」の見出しは
    行にあるのに、検索側で「本文ではない」という意味に使われていなかった（meaning。medium: 見出しの無い区画
    ＝図の目盛り・書誌の続きは見出しでは捕まらず、本文の性質で判定する必要があった）。確認: 第 8 周（埋め込み
    有効・実 pgvector）の学習チャットで、取得 8 区画に参考文献だけの区画・DATA AVAILABILITY・脚注 URL・
    ACKNOWLEDGEMENTS・目盛りの数値が一語一行に並ぶ図の区画が常に混ざっていた。
generalization:
  level: repo_pattern
  general_form: 近さの指標だけで根拠の候補を選び、候補が根拠になりうる種類の中身かどうかを確かめずに提示する
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: 第 8 周の実プロンプトに載った出典の中身を読んだ。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    同じ 1 本の SQL に、見出しを正規化（前後空白・先頭の節番号を除去・小文字化）した完全一致の除外述語
    （NON_CONTENT_SECTION_TITLES。参考文献・データの所在・謝辞・日本語の参考文献/謝辞。付録は残す・見出し無しは
    落とさない）と acknowledg / data availability の前方一致を足した。見出しで捕まらない区画は
    non_content_chunk_reason（書誌の列 = 年の密度 + 巻・ページ形 / 目盛り = 数値だけの語が半数以上 /
    URL だけ）で後段で落とし、そのために top_k の 2 倍を引いて top_k に詰める。material_id の SELECT と
    allowed_document_ids の fail-closed は不変。
  landed_in:
    - backend/api/services.py
    - backend/tests/test_search_visibility.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場（実 pgvector）での取得結果の再観測（第 8 周の論文で出典に参考文献・謝辞が出ないこと）
      - 実 Postgres での除外述語（regexp_replace・LIKE）の実行（テストは SQL 文字列と params の検査のみ）
      - GROBID 経路で見出しが誤っている論文（参考文献の区画に本文の見出しが付いている場合は書誌判定に頼る）
related: [IK-0381, IK-0391]
view_of: []
history: []
---

## 課題

検索が本文ではない区画（参考文献・謝辞・データの所在・脚注 URL・図の目盛り）を出典として返していた。

## 発見の観点

実プロンプトに載った 8 区画の中身を読んだ。

## 解決の観点

見出しによる除外を同じ SQL に入れ、見出しの無い非本文は中身の性質で後段で落とす。

## 一般化

近さの指標だけで根拠を選び、根拠になりうる中身かを確かめない。
