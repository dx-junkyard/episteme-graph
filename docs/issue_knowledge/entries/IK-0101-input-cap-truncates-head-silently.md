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
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [budget, completion]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals:
    - kind: value
      target_axis: governance
      neighbor_of: [governance.completion, connection.information]
      statement: >-
        機械が何を処理し何を処理しなかったか、またその操作で何が失われるかを、判断する人へ開示する義務が置かれているかを区別する値
      confidence: medium
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 入力を組む処理そのものは書かれたとおり動き、選抜の規則を差し替えれば同じ処理で正しく動くため要素を置かない（上限内での入力の取り扱いと読む余地は残る）。構造:
    何を処理し何を捨てたかを書ける場所がどこにも無く、打ち切りの事実を表せる表現を持たないため、規則を直しても次の上限で同じことが起きる。接続:
    選抜順が前段パーサの頁番号に依存し、その値が多数のブロックで同一に潰れていたため、順序という意味が段をまたいで壊れていた。統制:
    費用の上限を「先頭から順に取り、超えた分は捨てる」配分で使い（予算）、捨てた範囲を残さないまま「これで全部」と読める状態にした（完了判定）。実測は注釈がちょうど上限値で、ソート順の先頭と集合が完全に一致することで確認した。開示の欠落がどの値にも当たらないため統制の確信は中とし、新設を
    1 件提案する。
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
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.budget, governance.completion, connection.meaning]
    to: axes=processing=[none]; structure=[none]; connection=[meaning]; governance=[budget,
      completion]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: structure=[none]
    to: structure=[representation]
    reason: >-
      軸ごとの再判定で、捨てた範囲を書ける場所が無いことを構造の要素として置いた（旧座標の他の軸は不変）
---

## 課題

**症状**: 190 頁の博士論文から採択された主張 17 件・部品 14 件がすべて第 3 章由来で、本研究の
成果（同時共振の実現・感度の向上・上限値）は構造化成果に 1 件も入らない。20 頁の論文でも
「限界」を述べた末尾段落がちょうど落ちる。

**原因**: 役割判定の入力を組む段が、費用の上限を「ソート順の先頭 64 ブロックを取り、残りを
捨てる」形で使っていた。捨てたことはどの成果物にも現れず、下流は「この論文にはこれしか
無い」と読む。さらにソートキーが前段パーサの頁番号で、その値が多数のブロックで同一に
潰れていたため、「どの 64 件か」が内容と無関係に決まっていた。

**軸ごとの判断（2026-09-19）**: 処理は、選抜規則を差し替えれば同じ処理で正しく動くため要素を置かない。
構造は、打ち切りの事実を書ける場所が無いことを要素として置いた（規則を直しても次の上限で再発する）。
接続は壊れた頁番号に順序の意味が依存していた点。統制は上限の配分と「これで全部」と読める完了判定の両方。

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
