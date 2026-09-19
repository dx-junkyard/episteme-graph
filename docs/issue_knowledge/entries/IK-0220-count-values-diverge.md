---
id: IK-0220
title: 同じ事実の件数が複数の文書へ書き写され、版ごとに分裂する
status: resolved
recorded_at: 2026-08-13
resolved_at: 2026-08-14
sources:
  - docs/architecture/doc_review_findings_2026-08-13.md §2
  - docs/architecture/feature_consolidation_proposals_2026-08-13.md §3
  - docs/development_checklist.md §5-6
feature_context:
  realizing: 実装の全体像（ステージ数・ルーター数・目印の件数・採番の範囲）を文書から把握できるようにする
  layers: [docs, tests_guardrails]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [none]
    governance: [review]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 各文書の記述は、それぞれ書かれた時点では正しかった。
    構造: 同じ事実の正本が定まらないまま複数箇所へ書き写され、更新のたびに分裂する。等値で
    固定された件数だけが分裂していないという対照が、集約の不在を示す。
    接続: 段階の間で情報・条件・対象・版が失われる場面は無い。
    統制: 人の手で写した値を更新のたびに確かめる手続が無い。集約を一本化すれば残らないとも
    読めるため確信は中。
generalization:
  level: general
  general_form: >-
    同じ事実の数値が複数箇所へ書き写され、更新のたびに分裂して、どれが正しいか決められなくなる
discovery:
  perspective: [doc_code_diff, data_inspection]
  note: >-
    文書に書かれた件数と実装の実測値を機械的に突き合わせた。同一の件数について四つの値が
    併存している箇所、実装が進んでも止まったままの採番の案内、自らを正本と宣言しながら
    多数の経路が欠落した一覧が見つかった。一方で、テストが等値で固定している件数だけは
    ずれていなかった。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    一覧・採番・ステージの網羅を機械検査で固定し、文書側が落ちたら文書を直す（検査を緩めない）
    向きを明文化した。裸の数値を書かず、テストが正であると参照するか時点を添えるかのどちらか
    にする記法も規約にした。全ての数値を機械検査する案は誤検知が多いため採らず、記法の規約に留めた。
  landed_in:
    - backend/tests/test_docs_registry_guardrails.py
    - docs/development_checklist.md §5-6
    - docs/architecture/data-model.md
related: [IK-0221, IK-0219]
view_of: []
pattern: duplicate-canonical-sources
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.aggregation, governance.review]
    to: axes=processing=[none]; structure=[aggregation]; connection=[none]; governance=[review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 同じ件数が文書によって違う。画面の目印の件数は四つの値が併存し、子ルーターの本数は
実装より少ない値でコメントごと凍結され、パイプラインの段数は文書間で三種類、採番の一覧は
十数個ぶん手前で止まり、「次の空き番号」の案内どおりに採番すると既存と衝突する状態だった。

**原因**: 同じ事実が複数の文書へ書き写され、正本が定まっていない。書き写しは書いた時点では
正しいため、分裂は更新のたびに静かに進む。テストが等値で固定している件数だけがずれていない
ことが、原因の所在を示していた。

## 発見の観点

`doc_code_diff` と `data_inspection`。文書の数値と実装の実測値を機械的に突き合わせる。
読解だけでは「どれが正しいか」を決められないため、実測との突合が要る。

## 解決の観点

`canonical_source` — 実装（実ファイル・登録・定義）を正本とし、文書側は網羅を機械検査で
固定する。`guardrail_fix` として、落ちたら文書を直す（検査を緩めない）向きを明示した。
全ての数値を機械検査する案は誤検知が多いため退け、代わりに記法の規約（テストが正だと参照するか、
時点を添えるか）を置いた。

## 一般化

同じ事実を複数箇所に書く限り、文書でもコードでも設定でも再発する。書き写した時点では
正しいため、書いた人には問題が見えないのが特徴。辞書の型は `duplicate-canonical-sources`。
