---
id: IK-0110
title: 束の取り込みの置き換え指定が、教員が確定した行まで表示対象から外す
status: resolved
recorded_at: 2026-09-13
resolved_at: 2026-09-13
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §9.2 P4-R2
  - docs/features/knowledge_transfer_design.md §14.1
feature_context:
  realizing: 他インスタンスで作られた知識の束を取り込み、こちらの教員の確定を保つ
  layers: [knowledge_transfer, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [review, resume]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals:
    - kind: value
      target_axis: governance
      neighbor_of: [governance.completion, connection.information]
      statement: >-
        機械が何を処理し何を処理しなかったか、またその操作で何が失われるかを、判断する人へ開示する義務が置かれているかを区別する値
      confidence: medium
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 同期の規則そのものは書かれたとおり動く。構造: 人が確定した行という区別は既に列として存在しており、新しい表現を足さずに入力を整えるだけで守れた（確定を表す表現の粒度という読み方は残る）。接続:
    束の作り手と取り込み側の約束（束に無いものは外す）は宣言どおりで、段をまたいで情報・意味・条件・対象・版は失われていない。確定の前に失う範囲が示されない点は、段の間の情報ではなく人への開示の欠落として別に扱うべきと判断した。統制:
    置き換えの差分が人間の判断を経た行を区別せず一律に倒し（レビュー）、再取り込みという再実行の規律の中で起きている（再開）。失う範囲を確定の前に示す義務がどの値にも当たらないため新設を 1 件提案する。
generalization:
  level: general
  general_form: >-
    再取り込み・同期の「置き換え」が、前回の人間の判断を経た対象まで一律に対象化し、事前確認が
    その範囲を示さない
pattern: reexecution-overwrites-human-decision
discovery:
  perspective: [adversarial_review, invariant_audit]
  note: >-
    「情報を落とさない」条項に対し、supersede は削除でないから安全と言えるかを問い直した。
    人間の確定を倒すなら喪失である、という解釈で読み直したときに範囲の広さと開示の欠落が見えた。
resolution:
  perspective: [state_transition, explicit_contract]
  note: >-
    人が確定した行を同期に渡す前に取り込み側へ合流させ、内容列を 1 つも更新せずに残す形にした
    （同期そのものは非改変）。事前確認は「外れる / 外さない」の内訳と対象の列挙を返し、
    事実文も「教員が確定した項目は外しません」に直した。
  landed_in:
    - backend/core/knowledge_import/apply.py
    - docs/features/knowledge_transfer_design.md §14.1
related: [IK-0108, IK-0111]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.review, governance.resume, connection.information]
    to: axes=processing=[none]; structure=[none]; connection=[information]; governance=[review,
      resume]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: connection=[information]
    to: connection=[none]
    reason: >-
      軸ごとの再判定で、束と取り込みの約束は宣言どおり成立しており段の間で情報は落ちていないと判断した。示されない範囲の開示は提案へ回した
---

## 課題

**症状**: 取り込みで置き換えを指定すると、束に含まれない既存の行が表示対象から外れる。
その中に教員が承認・却下した行が含まれ、回復は手作業になる。事前確認の画面には対象の範囲が
出ない。

**原因**: 置き換えの規律が「束に無いものは古い」という単純な差分で、人間の判断を経た行を
区別していなかった。加えて、事前確認が件数も対象も示さないため、確定の時点で失うものが
分からなかった。

**軸ごとの判断（2026-09-19）**: 接続は、置き換えの約束が宣言どおりで段の間の情報の落ちではないため要素を外した。
統制は人の判断を一律に倒す差分（レビュー）と再取り込みの規律（再開）。
確定の前に失う範囲が示されない点は、既存のどの値にも当たらないため新設の提案として残した。

## 発見の観点

「削除していないのだから情報は落ちていない」と言えるか、という不変条項の読み直しから入った
（`invariant_audit`）。そのうえで置き換え経路を壊すつもりで辿り、確定前に開示される情報の
不足を確かめた（`adversarial_review`）。

## 解決の観点

同期の規則自体は正しいので触らず、その入力を整える側で人の確定を守った（`state_transition`）。
何が外れるかを事前確認の契約に足し（`explicit_contract`）、安心させるだけの文言は削った。

## 一般化

「同期」「置き換え」「リセット」と名の付く操作は、相手側に人間の判断が載っているかどうかで
意味が変わる。判断が載る場所では、差分の既定を「一律に倒す」ではなく「人の判断は残す」に
置き、事前確認は必ず失われる範囲を示す必要がある。
