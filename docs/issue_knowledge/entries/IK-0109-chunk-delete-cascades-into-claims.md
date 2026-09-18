---
id: IK-0109
title: 再解析での区画の作り直しが外部キーの連鎖で主張の行ごと消す
status: resolved
recorded_at: 2026-09-13
resolved_at: 2026-09-13
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §9.2 P1-R1 / V-10
  - docs/features/knowledge_objects_design.md §12.2
feature_context:
  realizing: 解析をやり直して本文の区画を作り直しつつ、区画を出典とする主張と教員の確定を保つ
  layers: [knowledge_objects, migrations_db]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [resume]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals:
    - kind: value
      target_axis: connection
      neighbor_of: [connection.target, structure.representation]
      statement: >-
        ある操作の影響が、呼び出した側が宣言した対象の外へ、参照の連鎖によって自動的に広がることを区別する値
      confidence: low
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 区画を作り直す処理も主張を書く処理も、それぞれは設計どおり動く。構造: 主張から区画への参照が連鎖削除に設定されており、削除しない規律を上の層に置いても参照の張り方が削除を伝播させる。接続:
    段をまたいで情報・意味・条件・版が失われてはいない。ただし呼び出し側が意図した対象（区画）より広い範囲が消える点は対象のずれとして読む余地があり、そこが確信を下げている。この波及の広がりを表す値が無いため新設を 1
    件提案する。統制: 再解析という再実行の規律の中で起きており、作り直しの手順が削除を前提にしていた。
generalization:
  level: general
  general_form: >-
    ある対象を作り直すための削除が、参照関係の連鎖によって独立に保つべき記録まで巻き込んで消す
pattern: delete-cascade-loses-derived-records
discovery:
  perspective: [adversarial_review, reproduction]
  note: >-
    「削除をやめた」と宣言した層の外側に削除経路が残っていないかを壊すつもりで読み、参照の連鎖を
    たどって発見した。新しい DB に実論文を通す再現で、承認ごと消えることを確認した。
resolution:
  perspective: [representation_change, state_transition]
  note: >-
    連鎖削除を「参照を空にする」へ張り替え、区画の作り直し自体を位置キーによる更新（余剰行だけ
    削除）にした。区画の識別子が保たれるので学習者の痕跡も切れない。
  landed_in:
    - backend/db/084_claim_chunk_fk_set_null.sql
    - backend/core/document_pipeline/persistence.py
related: [IK-0108, IK-0112]
view_of: [IK-0002, IK-0108]
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, governance.resume]
    to: axes=processing=[none]; structure=[representation]; connection=[none]; governance=[resume]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 削除をやめて状態遷移にしたはずの再解析で、承認済みの主張が物理的に消える。

**原因**: 本文の区画（チャンク）は別経路で「全部削除して入れ直す」ままで、主張から区画への
外部キーが連鎖削除に設定されていた。上位の層が削除をやめても、下位の参照が削除を伝播させる。

## 発見の観点

「削除をやめた」という宣言の外側に削除が残っていないかを壊すつもりで探し（`adversarial_review`）、
参照の連鎖を列挙して当たりを付け、実データで再現した（`reproduction`）。

## 解決の観点

削除を呼ぶ側を 1 か所ずつ直す方針では漏れるため、参照の張り方そのものを変えた
（`representation_change`）。あわせて区画の作り直しを位置キーの更新に変え、削除される行を
「余ったものだけ」に限定した（`state_transition`）。

## 一般化

「消さない」という規律は、参照の連鎖まで見ないと守れない。ある表の削除を許すなら、その表を
参照している表を全部列挙し、連鎖の設定が意図どおりかを確認する必要がある。列挙できない状態は、
連鎖が把握されていないのと同じ。
