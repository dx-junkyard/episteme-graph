---
id: IK-0122
title: 知識の永続化が複数のトランザクションに分かれ、中途半端な世代が残り得る
status: open
recorded_at: 2026-09-13
resolved_at: null
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §9.5
  - docs/features/knowledge_objects_design.md §12.2
feature_context:
  realizing: 解析結果の主張・部品・派生オブジェクト・学ぶ単位を、一貫した 1 世代として保存する
  layers: [knowledge_objects, pipeline_a]
classification:
  primary: structure
  facets: [structure.decomposition, governance.resume]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    主張・部品・派生オブジェクト・学ぶ単位の保存が 3〜4 のトランザクション境界に分かれているため、
    途中で失敗すると「主張だけ新しく、部品は前の世代」という一貫しない状態が残る。個々の保存は
    正しく、境界の引き方（何を 1 つの単位として確定させるか）という分割の設計だけが原因。
    境界を 1 つにするには永続化の段全体の再設計が要るため、別課題として残している。
generalization:
  level: general
  general_form: >-
    ひとまとまりにすべき書き込みが複数の確定単位に分かれ、途中失敗で一貫しない世代が残る
pattern: unit-of-work-undefined
discovery:
  perspective: [adversarial_review, trace_walk]
  note: >-
    実装後のレビューで、保存の各段がどこで確定するかを順に辿り、失敗したときに何が残るかを
    段ごとに書き出した。派生表の失敗が解析全体を失敗扱いにしていた問題（別途是正済み）と同じ
    場所から出ている。
resolution:
  perspective: [pending]
  note: >-
    1 トランザクションへの統合は永続化の段全体の再設計になるため着手していない。何が分かれば
    解けるか — 実運用で途中失敗がどの頻度でどの段に出るかの観測と、各段の所要時間（長い
    トランザクションを保持してよいか）の実測。
  landed_in: []
related: [IK-0105, IK-0113]
view_of: []
history: []
---

## 課題

**症状（想定）**: 解析の永続化が途中で失敗すると、主張は新しい世代・部品は前の世代という
一貫しない状態が残る。読み手はそれを「現在の知識」として読む。

**原因**: 保存が種別ごとに別々のトランザクションで確定する分割になっている。個々の保存処理は
正しく、どこまでを 1 つの確定単位にするかが決まっていないことだけが原因。

## 発見の観点

実装直後のレビューで、保存の各段がどこで確定するかを順に辿り（`trace_walk`）、途中失敗で何が
残るかを段ごとに書き出した（`adversarial_review`）。

## 解決の観点

未解決。1 つのトランザクションにまとめるには永続化の段全体を組み直す必要があり、影響範囲が
本課題の外に出る。解くために必要なのは、①途中失敗の実際の頻度と発生段 ②各段の所要時間
（長いトランザクションを保持してよいか）の 2 点。

## 一般化

「どこまでを 1 回の確定とするか」が決まっていない書き込みは、失敗したときに初めて不整合として
現れる。段を分けて実装したあとで境界を引き直すのは高くつくので、書き込みの単位は分割の設計
段階で決める。
