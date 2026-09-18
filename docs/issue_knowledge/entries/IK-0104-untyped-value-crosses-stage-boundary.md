---
id: IK-0104
title: 形の契約が検査されない値が段をまたぎ、文字列が1文字ずつ概念として学習者まで届く
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-12
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §1.3 F0-6
  - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 0 P0-3
  - docs/architecture/knowledge_structure_review_2026-09-12/A_fidelity.md F-6
  - docs/architecture/knowledge_structure_review_2026-09-12/B_storage.md S-13
feature_context:
  realizing: 抽出した部品に前提概念の一覧を付け、コースの前提知識として学習者に示す
  layers: [pipeline_a, course_builder]
classification:
  primary: connection
  facets: [connection.contract, structure.representation]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    上流が概念一覧を文字列で返すことがあるのに、下流がそれを列として反復するため、1 文字ずつが
    概念として展開される。各段は単体では正しく見え、前後の契約が両立しないことだけが原因で、
    しかも値が JSONB の中にあるため型も制約も検査されない。実測で凍結コースの前提知識が
    1 文字トークンの列になっていることを確認。壊れた値は誰にも止められず学習者向け DTO まで貫通した。
generalization:
  level: general
  general_form: >-
    値の形（単一値か列か）の契約が境界で検査されないため、取り違えた形のまま下流で展開され、
    壊れた値が最終利用者まで到達する
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [data_inspection, trace_walk]
  note: >-
    学習者に届く凍結コースの前提知識を実データで読み、1 文字の並びを見つけてから、値の生成点まで
    段を遡った。検証の厳しい出力ゲートが 288 件の指摘を出しながらこの値は素通りしていた点が、
    「列でないものは検証されない」という表現側の問題も示していた。
resolution:
  perspective: [explicit_contract, representation_change]
  note: >-
    概念一覧の正規化を 1 つの関数に固定して単一文字列を 1 要素として扱い、記号らしい名前は
    概念の一覧から外して記号レジストリ側に閉じた。あわせて学習単位として使う属性を列へ昇格させ、
    検証が効く場所に置いた。
  landed_in:
    - src/episteme_graph/agents/component_assembly/schema.py
    - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 0 実装記録
related: [IK-0103, IK-0105]
view_of: []
history: []
---

## 課題

**症状**: コースのトピックに付く前提知識が `r a p i s t` のような 1 文字の並びになり、そのまま
学習者に配信される。

**原因**: 概念一覧を組み立てる側が、文字列としても列としても渡ってくる値を、列だけを前提に
反復していた。文字列を反復すれば 1 文字ずつが要素になる。値は JSONB の中を流れるため、
DB の制約も出力ゲートの検証も効かず、壊れた形のまま最終利用者まで運ばれた。

## 発見の観点

学習者に届く実データを読んで異常な値を見つけ（`data_inspection`）、そこから生成点へ経路を遡った
（`trace_walk`）。壊れた値が途中の厳しい検証を素通りしていたことが、「列に無い属性は誰にも
検証されない」という別の問題と同じ根を持つことを示した。

## 解決の観点

境界で形を正規化する関数を 1 つ置き、単一文字列は 1 要素として扱うことを契約にした
（`explicit_contract`）。さらに、検証されない入れ物に置き続けないよう、学習に使う属性を列へ
移した（`representation_change`）。

## 一般化

同じ型は「文字列か配列か」「単一かリストか」「ID か ID の集合か」を取り違えたまま境界を越える
あらゆる場所に出る。とくに JSONB・自由形式の設定・LLM 出力のように**スキーマ検査の効かない
入れ物**を通るとき、壊れた値は止まらずに最終利用者まで届く。
