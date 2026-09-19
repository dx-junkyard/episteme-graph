---
id: IK-0221
title: 設計書だけに追記の運用があり、機能解説と索引には無いため、大型機能が文書から丸ごと欠ける
status: resolved
recorded_at: 2026-08-13
resolved_at: 2026-08-14
sources:
  - docs/architecture/doc_review_findings_2026-08-13.md §3
  - docs/architecture/doc_review_findings_2026-08-13.md §4
  - docs/architecture/doc_review_findings_2026-08-13.md §5
  - docs/architecture/feature_consolidation_proposals_2026-08-13.md §1
  - docs/development_checklist.md §5
feature_context:
  realizing: 実装の現行の姿を、設計の歴史記録とは別に読める形で保つ
  layers: [docs]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [review, completion]
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
    処理: 記述の内容にも、参照する仕組みにも誤りは無い。
    構造: 歴史記録と現行の姿を別の系統で持つ二層構造そのものは妥当で、表現にも責務の置き場所にも
    不足は無いと判断した。どこに何を書くかの正本が無いと読む余地は残る。
    接続: 段階の間で情報・条件・対象・版が失われる場面は無い。
    統制: 実装が終わったときにどの文書を誰が更新するかの手続が一方の系統にしか無く、完了や解消を
    文書へ反映する取り決めも無い。
generalization:
  level: general
  general_form: >-
    文書の系統ごとに更新の手続が偏り、手続の無い側だけが実装から取り残される
discovery:
  perspective: [doc_code_diff, inventory]
  note: >-
    全ての文書と実装を突き合わせた総点検で、設計書には実装記録が追記されているのに、
    現行の姿を説明する文書と索引には大型機能が一言も出てこないことが分かった。実装済みなのに
    未実装と書かれた見出し、解消済みなのに未修正の体裁で残るレビュー、想定で書いた採番が
    実装とずれた記述も、同じ偏りの現れだった。
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: >-
    変更の種類ごとに同時更新すべき文書を表で固定し（学習者向け機能・ステージ追加・ルーター
    追加・採番の追加）、状態の見出しの語彙を統一し、解消したらレビュー文書側に注記を追記する
    規約を置いた。採番は実装後にのみ書く（一次情報は実ファイル名）。網羅は機械検査が担い、
    規約は変更のたびに照合する。無言で飛ばすことだけを禁じ、更新しない判断は書けばよいとした。
  landed_in:
    - docs/development_checklist.md §5
    - backend/tests/test_docs_registry_guardrails.py
    - docs/features/learning.md
    - docs/backend/rag-chat.md
related: [IK-0220, IK-0219, IK-0203]
view_of: [IK-0016]
pattern: doc-drifts-from-code
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.review, governance.completion]
    to: axes=processing=[none]; structure=[none]; connection=[none]; governance=[review,
      completion]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 大型の対話機能が、現行の姿を説明する文書のどこにも登場しない。実装済みの機能が
「未実装」「Draft」の見出しのまま残り、解消済みのレビューが「修正実施なし」の体裁で残って
既知のバグと誤読される。設計書に想定で書いた採番が実装とずれ、二つの文書が互いに矛盾する
因果説明を持っていた。

**原因**: 設計書には「実装後に実装記録を追記する」運用が定着している一方、現行の姿を説明する
文書と索引には更新の規約が無い。二層構造のうち、手続の無い側だけが取り残された。完了や
解消を文書へ反映する取り決めも無かった。

## 発見の観点

`doc_code_diff` と `inventory`。文書と実装を全件突き合わせる総点検で初めて、欠落の大きさ
（機能が丸ごと無い）が見える。個別の文書を読んでいる限り、書かれていないものには気づけない。

## 解決の観点

`explicit_contract` — 変更の種類ごとに同時更新する文書を表で固定し、状態の見出しの語彙と、
解消時の注記の書き方を規約にした。`guardrail_fix` で網羅を機械検査へ移した。全ての文書を
自動生成する案は採らない（判断の記録は機械導出できない）。更新しない判断は書けばよく、
無言で飛ばすことだけを禁じている。

## 一般化

歴史記録として凍結する文書と、現行の姿を示す文書が混在するあらゆるプロジェクトで再発する。
片方にだけ運用が定着すると、定着していない側の欠落は誰も報告しない。辞書の型は
`doc-drifts-from-code`。
