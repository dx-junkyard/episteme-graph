---
id: IK-0381
title: "学習チャットの出典チップは source_title に文書の題名（arXiv 番号のファイル名）だけを持ち、節や段落の手がかりが無いため、同じ論文からの出典 8 件が全て同じ表示になり、どれがどの箇所か分からない"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者がチャットの回答の出典を見分けて原文を開く
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
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
    接続軸で、チャンクが持つ節・ページの情報が SourceTierItem（index / chunk_id / source_title / quote）へ
    運ばれていない（information）。構造軸で、出典の表示単位が「文書」で、箇所を表す欄が無い（representation。
    medium: quote があるので欄を足すかラベルに含めるかは設計判断）。確認: 第 7 周で学生 2 名が「出典 1〜8 が全部
    2605.26810v1 2605.26810v1.pdf で、どれがどの段落か分からない」と記録。IK-0366（題名）が直っても箇所の区別は
    付かない。原因の確認（2026-09-28）: chunks は source_metadata.section_title に節の見出しを持つが、
    search_chunks_with_metadata は題名とファイル名しか SELECT せず、learning.py の 2 箇所は meta に
    ファイル名を入れていた（情報は行にあり、運ばれていなかった）。
generalization:
  level: repo_pattern
  general_form: 参照の表示ラベルを上位（文書）だけで作り、同じ文書内の箇所を区別する情報を運ばない
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: 実ペルソナ 2 名の同一観測（出典チップの読み分け不能）。
resolution:
  perspective: [carry_through]
  note: >-
    chunk 行が既に持つ節の見出し（source_metadata.section_title）を検索の同じ SELECT で読み、
    節と区画の書き出しから箇所の手がかりを作って SourceTierItem.meta に運んだ（追加クエリなし・数値なし）。
    学習チャット本体と前提説明の 2 つの組み立て箇所は同じ関数を通し、手がかりが無いときだけファイル名に縮退する。
    出典タブの根拠の並びにも meta を添え、出典ポップアップの節も同じ出所で補った。
  landed_in:
    - backend/api/services.py
    - backend/api/routes/learning.py
    - frontend/public/js/app.js
    - frontend/public/index.html
    - backend/tests/test_search_visibility.py
    - docs/backend/rag-chat.md
    - docs/manual/student/02-student.md
  verification:
    methods: [guardrail]
    unverified:
      - 砂場・ブラウザでの出典タブの表示（第 7 周の論文での再操作）
      - 節の見出しを持たない旧データの chunk（冒頭だけになる）と GROBID 経路で見出しが誤っている論文
related: [IK-0366]
view_of: []
history: []
---

## 課題

同じ論文からの出典が全て同じ表示。

## 発見の観点

実ペルソナ 2 名の同一観測。

## 解決の観点

chunk 行にある節の見出しと区画の書き出しを、出典の meta として画面まで運ぶ。

## 一般化

参照ラベルが文書単位で箇所を持たない。
