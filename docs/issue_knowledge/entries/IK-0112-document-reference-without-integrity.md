---
id: IK-0112
title: 文書への参照が型も制約も持たず、掃除の責務が二重で孤児が滞留する
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-13
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12/B_storage.md S-8
  - docs/architecture/knowledge_structure_review_2026-09-12/E_concepts.md K-9
  - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 1 P1-7
  - docs/features/knowledge_objects_design.md §8
feature_context:
  realizing: 教材を削除したときに、その教材に属する成果物とファイルを残さず片づける
  layers: [migrations_db, knowledge_objects, versioning_v]
classification:
  axes:
    processing: [none]
    structure: [responsibility, representation]
    connection: [none]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    「この教材に属する行の集合」という同じ事実の定義が 2 つの削除経路に別々に書かれ、片方が
    成果物・図・解析実行を掃除しなかった。参照列の型も揃わず外部キーも無いため、DB 側では
    何も保証されない。実測で成果物の 71.8%・図の 82.8% が存在しない教材を指し、孤児の解析実行が
    保存領域の 94% を占めていた。どちらの経路が正しいかを決めないと直せない点が構造の定義に当たる。
generalization:
  level: general
  general_form: >-
    同じ「所属の範囲」を複数の削除経路が別々に定義し、参照側に制約も無いため、片方の経路を
    通った対象の関連行が残り続ける
pattern: duplicate-canonical-sources
discovery:
  perspective: [data_inspection, inventory]
  note: >-
    参照列の型と外部キーを表単位で棚卸しし、存在しない教材を指す行の比率と、それが占めるバイト数を
    実測した。削除の 2 経路を並べて、掃除する表の集合が食い違うことを確かめた。
resolution:
  perspective: [representation_change, canonical_source]
  note: >-
    参照列を 1 つの型に揃えて外部キーを張り、DB 側で連鎖が保証されるようにしたうえで、削除経路を
    1 本に委譲した。適用時に到達不能な孤児だけを 1 回掃除した（本 Phase で唯一の破壊的操作として
    設計書に明記）。
  landed_in:
    - backend/db/080_document_id_uuid.sql
    - backend/core/versioning/deletion.py
related: [IK-0105, IK-0109]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.responsibility, structure.representation]
    to: axes=processing=[none]; structure=[responsibility, representation]; connection=[none];
      governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 教材を消しても解析結果・図の実体・台帳が残る。コーパスの「概念の数」も信用できない。
保存領域の大半を、どの教材のものでもない解析結果が占める。

**原因**: 文書への参照が表ごとに別の型で、外部キーも無かったため DB では何も保証されない。
そのうえで削除が 2 経路あり、片方は成果物・図・解析実行を掃除しない。どちらが正しい掃除範囲かが
決まっていなかった。

## 発見の観点

参照列の型と外部キーを表単位で棚卸しし（`inventory`）、存在しない教材を指す行の比率と占有量を
実測した（`data_inspection`）。

## 解決の観点

参照の表現を揃えて連鎖を DB に持たせ（`representation_change`）、掃除範囲の定義を 1 本に寄せた
（`canonical_source`）。片方の経路に書き足していく方針は、次に経路が増えたときに同じ状態へ戻る。

## 一般化

「この対象に属するもの」を複数の場所が別々に定義していると、必ず片方が取り残す。所属を
データベースの制約で表せるなら表し、表せない（ポリモーフィックな）場合は掃除の入口を 1 本に
決めて他は委譲させるしかない。
