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
    structure: [unknown]
    connection: [none]
    governance: [review, completion]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: medium
  proposals:
    - kind: value
      target_axis: governance
      neighbor_of: [governance.completion, connection.information]
      statement: >-
        機械が何を処理し何を処理しなかったか、またその操作で何が失われるかを、判断する人へ開示する義務が置かれているかを区別する値
      confidence: medium
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 承認・疑義・承認済み説明が実データでいずれもゼロなのに、教材化の経路は承認を経由せず生成物から直接作って配信している、という事実は確認済み。処理: 単一処理の不良は見当たらない。構造:
    承認の対象の粒度と名前（段落単位の主張・機械生成の部品名）が実態に合わないために弁が使われないのか、運用がまだ立ち上がっていないだけなのかを見ていないため unknown
    とする。単位と名前が整った状態で一定期間の承認操作の有無を観測すれば確定する。接続:
    承認済みかどうかという条件が配信の経路に渡っていないと読む余地はあるが、そもそも弁の外に経路がある設計なので条件の伝達の問題ではないと判断した。統制: 確認を求める弁を通らない経路が唯一の実用経路になっており（レビュー）、1
    操作が実質の一括確定として「済み」を作っている（完了判定）。弁が空であること自体が誰にも見えない点はどの値にも当たらないため新設を提案する。
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
  - date: '2026-09-19'
    field: classification.axes
    from: structure=[none]
    to: structure=[unknown]
    reason: >-
      軸ごとの再判定で、承認の粒度が実態に合わないのか運用が未成立なのかを見ていないため unknown に倒した（仮説の課題として整合）
---

## 課題

**症状**: 部品・主張・説明の承認がいずれもゼロ件、監査記録もゼロ件。それでも学習者には教材が
届いている。

**原因**: 教材化の経路が承認を経由せず生成物から直接作るため、「コース登録」という 1 操作が
実質の一括確定になっている。一方、承認の弁はその経路の外にあり、通らなくても配信できる。
なぜ弁が使われないのか（語彙と粒度が実態に合っていないのか、運用がまだなのか）は未確定。
原因は**仮説**の段階で、分類（統制）も仮説としての分類である。

**軸ごとの判断（2026-09-19）**: 構造は、承認の粒度と名前が実態に合わないのか運用が未成立なだけなのかを見ていないため unknown とした（前者なら構造に値が立つ）。
統制は弁を通らない経路と実質の一括確定。弁が空であること自体が見えない点は新設の提案として残した。

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
