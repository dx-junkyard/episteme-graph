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
    structure: [none]
    connection: [none]
    governance: [assignment]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 永続化が常に空を書くこと自体は、候補が供給されない結果であって単一処理の不良ではない。構造:
    候補を受ける列と表は最初から存在し、表現を変えずに供給の担当を足すだけで動いた（候補生成の置き場所を責務と読む余地は残り、そこが確信を下げている）。接続:
    供給する段が存在しないため、段どうしで情報・条件・対象・版が噛み合わないという形にはなっていない。統制:
    同一性を確定する権限は人間に置かれている一方、候補を作る担当がどこにも割り当てられておらず、人間は見るものが無かった。実データで同一性リンク・別名・共通部品の凍結版がいずれもゼロ件。
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
  - date: '2026-09-19'
    field: classification.axes
    from: structure=[representation]
    to: structure=[none]
    reason: >-
      軸ごとの再判定で、候補を受ける表現は最初から存在していたため構造の要素を外した（責務と担当の境界は統制側に置く）
---

## 課題

**症状**: 論文をまたいだ「同じ部品」「同じ概念」のリンクが 1 件も存在しない。共通化を前提にした
下流の機能（標準化の判定・共通部品の表示・旅の経路）がすべて空回りする。

**原因**: 確定の権限は人間に置かれているが、候補を作る担当が誰にも割り当てられていなかった。
候補を受ける列は最初から存在し、永続化は常に空を書いていた。人間は確定しようにも、見るものが無い。

**軸ごとの判断（2026-09-19）**: 構造は、候補を受ける列と表が最初から存在していたため要素を外した（候補生成の置き場所を責務と読む余地は残る）。
統制は候補を作る担当が誰にも割り当てられていない点。処理と接続は要素なし。

## 発見の観点

パイプラインの全段を列挙して提案を出す段の不在を確かめ（`inventory`）、受け皿の実データが
常に空であることを読んだ（`data_inspection`）。

## 解決の観点

「確定は人間」を変えずに、候補を出す担当だけをパイプラインへ移した（`responsibility_move`）。
候補は候補の状態にとどまり、確定でのみ先へ進む（`state_transition`）ので、既存の条項に触れない。

## 一般化

人間の確定を要する設計は、確定の入口だけでは動かない。**誰がいつ候補を作るか**を同時に決めて
おかないと、装置は完成しているのに一度も使われない状態で放置される。
