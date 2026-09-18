---
id: IK-0101
title: 入力上限の超過を先頭切り捨てで処理し、捨てた範囲をどこにも報告しない
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-12
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §2 D1
  - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 0 P0-1 / P0-10
  - docs/architecture/knowledge_structure_review_2026-09-12/A_fidelity.md F-1 / F-18
feature_context:
  realizing: 論文本文のブロックに論理役割を付け、どこまで処理したかを正直に残す
  layers: [pipeline_a]
classification:
  primary: governance
  facets: [governance.budget, governance.completion, connection.meaning]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理も入力形式も妥当だが、費用の上限を「先頭から順に取り、超えた分は捨てる」という配分規律で
    使い、捨てた量を残さないため、190 頁の論文で母集合の 6.8% しか役割判定されず、成果が全部
    1 つの章に偏った。上限の使い方（どれを選ぶか・残りをどう報告するか）を直さなければ処理を
    いくら直しても再発する点が統制の定義に当たる。選抜順が前段パーサの壊れた頁番号に依存して
    いた点（同じ値が 212 ブロックに付く）は副分類の「段階間で意味がずれる」。実測は
    role_annotations がちょうど 64 件で、ソート順の先頭 64 件と集合が完全一致することで確認。
generalization:
  level: general
  general_form: >-
    予算の上限を超えた入力を先頭から捨て、捨てた範囲を記録しないため、処理された部分が内容の
    重要度と無関係に決まる
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection, trace_walk]
  note: >-
    原本 PDF / TeX と全 artifact を章単位で突き合わせ、「成果が全部第3章由来」という偏りから
    入力側へ遡った。段階ごとの件数（936 ブロック → 64 注釈 → 17 主張）を並べたときに律速段が見えた。
resolution:
  perspective: [order_and_budget, canonical_source]
  note: >-
    上限を既定で撤廃して環境変数に逃がし、上限を使う場合は先頭切り捨てではなく節単位の層化
    サンプリングにした。あわせて「母集合 / 処理数 / 打ち切り数 / 理由」の共通報告形式を 1 つの
    正本に置き、打ち切りの有無を導出値として各ステージに付けた（申告させない）。
  landed_in:
    - src/episteme_graph/agents/rhetorical_role/input_builder.py
    - src/episteme_graph/agents/coverage_report.py
    - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 0 実装記録
related: [IK-0102, IK-0113]
view_of: []
history: []
---

## 課題

**症状**: 190 頁の博士論文から採択された主張 17 件・部品 14 件がすべて第 3 章由来で、本研究の
成果（同時共振の実現・感度の向上・上限値）は構造化成果に 1 件も入らない。20 頁の論文でも
「限界」を述べた末尾段落がちょうど落ちる。

**原因**: 役割判定の入力を組む段が、費用の上限を「ソート順の先頭 64 ブロックを取り、残りを
捨てる」形で使っていた。捨てたことはどの成果物にも現れず、下流は「この論文にはこれしか
無い」と読む。さらにソートキーが前段パーサの頁番号で、その値が多数のブロックで同一に
潰れていたため、「どの 64 件か」が内容と無関係に決まっていた。

## 発見の観点

原本と全 artifact を章単位で照合し（`data_inspection`）、成果の偏りから入力段へ 1 本の経路を
遡った（`trace_walk`）。注釈数が両論文ともちょうど上限値に一致したことが決め手。別ステージが
取りこぼしを正直に報告しているのに、この段だけ何も残していない非対称も、同じ突合で見えた。

## 解決の観点

「上限をやめる」だけでは再発する（費用の都合でいつか戻る）ため、上限を使うときの配分規律を
仕様にした（`order_and_budget`）。取りこぼしの申告は層ごとに書き方が割れていたので、報告形式の
正本を 1 つ置き、打ち切りの有無を申告ではなく導出値にした（`canonical_source`）。

## 一般化

同じ型は「件数上限つきの一括処理」「トークン上限に収める文脈組み立て」「表示件数の切り詰め」
すべてに現れる。捨てる判断そのものより、**どれを捨てたかが内容と無関係に決まること**と
**捨てた事実に居場所が無いこと**が損害を生む。
