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
  primary: local
  facets: [local.logic, structure.aggregation]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    入力（カートリッジの別名表と本文）も前後の契約も妥当で、照合規則が `alias in text` の
    部分文字列一致であることだけが原因。2 文字の別名が無関係な単語の内側に当たり、原本に
    0 回しか現れない概念が 28 ノード中 25 ノードの前提知識に入った。規則を直せば周囲に波及
    しない一方、同じ規則が 4 箇所に複製されていた点は副分類（集約）。
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
history: []
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
