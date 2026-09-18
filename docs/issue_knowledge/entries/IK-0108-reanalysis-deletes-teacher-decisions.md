---
id: IK-0108
title: 再解析が成果を削除して作り直すため、教員の確定がその都度消える
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-13
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 1 P1-5
  - docs/architecture/knowledge_structure_review_2026-09-12/B_storage.md S-6 / S-7
  - docs/architecture/knowledge_structure_review_2026-09-12/E_concepts.md K-9
  - docs/features/knowledge_objects_design.md §5.2 / §5.3
feature_context:
  realizing: 解析をやり直して成果を差し替えつつ、教員が確定した承認・訂正を保つ
  layers: [knowledge_objects, pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [representation]
    connection: [none]
    governance: [resume, review]
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
    処理: 抽出結果が空のときに早期に戻って同期が走らず、「消える」か「古いまま残る」かが結果次第で決まっていた。空入力でも同期を走らせることが是正の一部だった（冪等性の統制と読む余地は残る）。構造:
    前回の判断を引き継ぐ手掛かり（安定した同一性）と、人が触れた列を保護するという表現を持たなかった（同一性そのものは別の課題と重なる）。接続:
    段をまたいで情報・意味・条件・対象が失われてはいない（版の扱いと読む余地は残る）。統制:
    再実行の規律が「対象を全部消して入れ直す」であり（再開）、書き戻しが審査状態を固定値で上書きするため承認・却下が毎回失われた（レビュー）。実データでも全行が初期状態に戻っていた。
generalization:
  level: general
  general_form: >-
    再実行が対象を削除して作り直す設計のため、前回の人間の判断が毎回失われ、やり直すこと自体が
    人の仕事を消す操作になる
pattern: reexecution-overwrites-human-decision
discovery:
  perspective: [boundary_walk, data_inspection]
  note: >-
    「再解析 × 教員の確定」という境界を意図的に歩き、削除文と固定値の書き戻しを読んだうえで、
    実データの審査状態が全行初期値であることを確認した。「情報を落とさない」原則との照合でもある。
resolution:
  perspective: [state_transition, explicit_contract]
  note: >-
    削除を撤去し、同じ対象は同じ行のまま更新・異なるものは superseded 遷移へ。人が触れた列
    （審査状態・訂正した本文）は一致時に書き換えない保護列として明示した。空入力でも同期を
    走らせ「消えるか残るか」が結果次第にならないようにした。
  landed_in:
    - backend/core/knowledge_objects/sync.py
    - docs/features/knowledge_objects_design.md §12.1
related: [IK-0106, IK-0109, IK-0110]
view_of: [IK-0002, IK-0109]
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.resume, governance.review, structure.representation]
    to: axes=processing=[none]; structure=[representation]; connection=[none]; governance=[resume,
      review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: processing=[none]
    to: processing=[input_handling]
    reason: >-
      軸ごとの再判定で、空入力のときに同期が走らず結果次第で挙動が二通りに分かれた点を処理の要素として置いた
---

## 課題

**症状**: 教員が承認・訂正した主張と部品が、再解析のたびに初期状態の別の行に置き換わる。
結果として教員は再解析を避け、コーパスが古い解析に固定される。

**原因**: 永続化が「この文書の行を全部削除 → 新しい行を挿入」で、挿入時に審査状態を固定値で
書いていた。前回の判断を引き継ぐ手掛かり（安定した同一性）も無かった。さらに抽出結果が空の
ときは早期に戻って削除すら走らないため、状態が結果次第で二通りに分かれた。

**軸ごとの判断（2026-09-19）**: 処理は、抽出結果が空のときの早期復帰で「消える」か「古いまま残る」かが分かれた点を要素として置いた（再実行の規律を直すだけでは残る）。
構造は同一性と保護列という表現の不在、統制は削除して入れ直す再開の規律と確定の上書き。接続は要素なし。

## 発見の観点

「再実行 × 人間の確定」という境界を歩き（`boundary_walk`）、削除と固定値書き戻しを読んでから、
実データの審査状態を数えた（`data_inspection`）。同型の規則（説明・配置）が既に存在することも、
原則との照合で見えた（`invariant_audit`）。

## 解決の観点

規則を発明せず、同じリポジトリ内で既に使われている supersede 遷移を移植した
（`state_transition`）。加えて「人が触れた列は上書きしない」ことを保護列として契約に書き
（`explicit_contract`）、削除文の再導入を検査で塞いだ。

## 一般化

作り直しのある生成物に人間の判断を載せる仕組みは、必ずこの型に出会う。判断を保つには
①作り直しても対象を同じものと認識できる同一性 ②削除ではない遷移 ③人が触れた部分の保護、の
3 つが揃っている必要があり、どれか 1 つでも欠けると判断は静かに消える。
