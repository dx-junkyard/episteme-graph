---
id: IK-0115
title: 承認の弁を通らない経路だけが実際に機能し、承認ゼロのまま学習者へ届く
status: deferred
recorded_at: 2026-09-12
resolved_at: null
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §2 D5
  - docs/architecture/knowledge_structure_review_2026-09-12/C_consumers.md C-6
  - docs/architecture/knowledge_structure_review_2026-09-12.md §6 O-3 / O-5
  - docs/features/learning_units_design.md §12.1
feature_context:
  realizing: 教員の確認を経た知識だけを教材として学習者に届ける
  layers: [course_builder, decision_context, guidance_g]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [review, completion]
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 承認・疑義・承認済み説明が実データでいずれもゼロなのに、教材化の経路（コース登録）は
    承認を経由せず生成物から直接作って配信している、という事実は確認済み。ただし「承認が
    使われない」原因が、承認の語彙・粒度が実態（段落単位の主張・機械生成の部品名）に合っていない
    ためなのか、単に運用がまだ立ち上がっていないためなのかは未確定。確認するには、単位と名前が
    整った状態で一定期間の承認操作の有無を観測すればよい。原因が前者なら承認対象の粒度という
    表現の問題（structure）に分類が移る可能性がある。
generalization:
  level: repo_pattern
  general_form: >-
    確認を求める弁を通らない別経路が利用者に届く唯一の実用経路になっており、弁は空のまま、
    実質の確定がどこにも記録されない
pattern: unaudited-write-path
discovery:
  perspective: [data_inspection, invariant_audit]
  note: >-
    承認・推薦・監査記録の件数を実データで数え（すべてゼロ）、一方で学習者に届いている教材が
    どの経路で作られたかを辿った。「確定は再構成可能な手続にのみ」という改訂された原則との照合。
resolution:
  perspective: [deferred_decision]
  note: >-
    教材化を一括確定として確定文脈に記帳し、承認ゼロのまま配信されている事実を教員へ事実文で
    見せるところまでは着地した（配信は止めない）。承認の語彙・粒度を実態に合わせるかどうかは
    オーナー判断待ちで、それが決まるまで分類は仮説のまま置く。
  landed_in:
    - docs/features/learning_units_design.md §12.1
related: [IK-0113, IK-0116, IK-0111]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.review, governance.completion, governance.assignment]
    to: axes=processing=[none]; structure=[none]; connection=[none]; governance=[review,
      completion]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持。軸あたり最大 2 のため governance.assignment
      は座標から外し、ここに残す
---

## 課題

**症状**: 部品・主張・説明の承認がいずれもゼロ件、監査記録もゼロ件。それでも学習者には教材が
届いている。

**原因**: 教材化の経路が承認を経由せず生成物から直接作るため、「コース登録」という 1 操作が
実質の一括確定になっている。一方、承認の弁はその経路の外にあり、通らなくても配信できる。
なぜ弁が使われないのか（語彙と粒度が実態に合っていないのか、運用がまだなのか）は未確定。
原因は**仮説**の段階で、分類（統制）も仮説としての分類である。

## 発見の観点

承認・推薦・監査の件数をすべて実データで数え（`data_inspection`）、届いている教材の生成経路と
突き合わせた。改訂された「確定は再構成可能な手続にのみ」という原則に照らすと、記録の無い
一括確定が浮かび上がった（`invariant_audit`）。

## 解決の観点

配信を止める方向は「押し付けない」「リリースを止めない」条項と衝突するため取らない。まず
一括確定の記帳と、承認ゼロで配信されている事実の提示を入れた。残るのは承認の語彙・粒度を
実態に合わせるかどうかで、これは制度の設計に触れるためオーナー判断待ちとする
（`deferred_decision`）。

## 一般化

確認の弁を作っても、それを通らない経路が実用上いちばん楽なら、弁は空のまま残る。弁が空である
こと自体を可視化しないと「承認制度がある」という見かけだけが残り、実質の確定は記録されない。
