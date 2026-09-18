---
id: IK-0116
title: 人の確定を要する仕組みに候補を供給する経路が無く、受け皿が空のまま死んでいた
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-13
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12/E_concepts.md K-5 / K-12
  - docs/architecture/knowledge_structure_review_2026-09-12/C_consumers.md C-12
  - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 3 P3-6
  - docs/features/concept_registry_design.md
feature_context:
  realizing: 論文をまたいで同じ部品・概念であることを教員が確定できるようにする
  layers: [concept_registry, pipeline_a, deliberation_w]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [assignment]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    同一性を確定する権限は人間に置かれている（それ自体は守られている）のに、候補を作る担当が
    どこにも割り当てられていないため、教員に見せるものが 1 件も生まれない。受け皿の列は存在し、
    永続化が常に空配列を書いていた。確定の手順を改善しても、候補を出す担当を決めなければ
    受け皿は空のまま、という点が統制の定義に当たる。実データで同一性リンク・別名・共通部品の
    凍結版がいずれもゼロ件であることを確認。
generalization:
  level: repo_pattern
  general_form: >-
    人間の確定を要する装置はあるが、そこへ候補を供給する担当が割り当てられておらず、装置が
    空のまま動かない
pattern: last-mile-missing
discovery:
  perspective: [inventory, data_inspection]
  note: >-
    パイプラインの全段を列挙して同一性の提案を出す段が無いことを確かめ、受け皿の列に何が
    書かれているかを実データで読んだ（常に空）。概念を持つ系統が 10 あって系統間の参照が
    0 本という棚卸しとも一致した。
resolution:
  perspective: [responsibility_move, state_transition]
  note: >-
    候補生成をパイプラインの担当として末尾に 1 段置き、決定論だけで（外部呼び出しを増やさずに）
    候補を作る。確定は人間のままで、候補は候補の状態から動かない。
  landed_in:
    - backend/core/library/identity_candidates.py
    - docs/features/concept_registry_design.md §6.2
related: [IK-0115, IK-0117, IK-0121]
view_of: [IK-0121]
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.assignment, structure.representation]
    to: axes=processing=[none]; structure=[representation]; connection=[none]; governance=[assignment]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 論文をまたいだ「同じ部品」「同じ概念」のリンクが 1 件も存在しない。共通化を前提にした
下流の機能（標準化の判定・共通部品の表示・旅の経路）がすべて空回りする。

**原因**: 確定の権限は人間に置かれているが、候補を作る担当が誰にも割り当てられていなかった。
候補を受ける列は最初から存在し、永続化は常に空を書いていた。人間は確定しようにも、見るものが無い。

## 発見の観点

パイプラインの全段を列挙して提案を出す段の不在を確かめ（`inventory`）、受け皿の実データが
常に空であることを読んだ（`data_inspection`）。

## 解決の観点

「確定は人間」を変えずに、候補を出す担当だけをパイプラインへ移した（`responsibility_move`）。
候補は候補の状態にとどまり、確定でのみ先へ進む（`state_transition`）ので、既存の条項に触れない。

## 一般化

人間の確定を要する設計は、確定の入口だけでは動かない。**誰がいつ候補を作るか**を同時に決めて
おかないと、装置は完成しているのに一度も使われない状態で放置される。
