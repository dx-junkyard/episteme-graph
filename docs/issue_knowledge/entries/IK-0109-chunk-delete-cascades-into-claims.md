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
  primary: structure
  facets: [structure.representation, governance.resume]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    再解析を状態遷移に直したあとも、本文区画の作り直しが削除で行われ、主張から区画への外部キーが
    連鎖削除だったため、承認済みの主張の行が物理的に消えた。削除しない規律を上の層に置いても、
    参照の張り方（連鎖削除）という表現を変えなければ迂回される点が構造の定義に当たる。実データで
    再解析後に主張が消えることを再現した。
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
history: []
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
