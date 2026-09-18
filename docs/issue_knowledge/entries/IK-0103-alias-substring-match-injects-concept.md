---
id: IK-0103
title: 別名の照合が部分文字列一致で、原本に無い概念が全体に注入される
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-12
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 0 P0-2
  - docs/architecture/knowledge_structure_review_2026-09-12/A_fidelity.md F-7
  - docs/architecture/knowledge_structure_review_2026-09-12/E_concepts.md K-3
feature_context:
  realizing: 分野カートリッジの語彙で抽出結果に概念名を補う
  layers: [pipeline_a, cartridges]
classification:
  axes:
    processing: [logic]
    structure: [aggregation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 照合規則が部分文字列の包含で、短い別名が無関係な語の内側に当たる。規則を直せば周囲に波及しない。構造: 同じ照合規則が検証・補完・精製・主張組み立ての 4 箇所に写されており、1
    箇所を直しても残りが同じ偽陽性を作り続ける（増幅の要因であって発生の要因ではない、という読み方も残る）。接続:
    誤って付いた概念は段をまたいでそのまま忠実に運ばれており、情報・意味・条件・対象・版のいずれも失われていない。統制:
    上限・順序・完了・担当の扱いはいずれも成立している（学習者へ届く前の確認という論点は残るが、本件の原因ではない）。
generalization:
  level: general
  general_form: >-
    語の照合を部分文字列一致で行うため、短い語が無関係な語の内側に当たり、存在しない対応が
    下流に伝播する
pattern: substring-match-false-positive
discovery:
  perspective: [data_inspection, trace_walk]
  note: >-
    原本に一度も現れない語が成果の大半に付いていることを数え、語の出どころを照合コードまで
    遡った。論文 2 本の「共通概念」が全部 1 文字記号だったことも同じ機構の帰結。
resolution:
  perspective: [single_point_fix, canonical_source]
  note: >-
    照合を語境界つきに変え、3 文字以下の別名は大小を区別した単語完全一致だけに限定した。
    4 箇所に散っていた同型実装は 1 つの正本に寄せ、部分一致の再導入をテストで禁じた。
  landed_in:
    - src/episteme_graph/agents/alias_matching.py
    - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 0 実装記録
related: [IK-0104, IK-0116]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=local facets=[local.logic, structure.aggregation]
    to: axes=processing=[logic]; structure=[aggregation]; connection=[none]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 宇宙論の論文の成果に、原本へ一度も出てこない素粒子物理の概念が前提知識として
付き、それが学習者に届くコース教材まで伝わる。

**原因**: 別名の照合が正規化した本文への部分文字列包含だった。2 文字の別名が別の単語の
内側に一致し、一致した事実は「概念が出現した」として下流に書き戻される。しかも照合規則は
検証・補完・精製・主張組み立ての 4 箇所に同じ形で写されていた。

## 発見の観点

実データで「原本に 0 回の語が成果の大半に付く」件数を確認し（`data_inspection`）、語の注入点を
コードまで遡った（`trace_walk`）。論文横断の「共通概念」が全部偽陽性だった事実が、この機構の
上に共通化を積むと誤りが増幅することを示した。

## 解決の観点

規則そのものは 1 箇所を直せば足りる（`single_point_fix`）が、同型が 4 箇所にあるため正本を
立てて委譲させ（`canonical_source`）、部分一致の再導入は機械検査で塞いだ。

## 一般化

短い記号的な語を含む語彙表で部分一致を使うと、必ず偽の対応が生まれる。検索・タグ付け・
リンク候補・禁止語検査など、語彙表と本文を突き合わせるあらゆる場所で同型が出る。
