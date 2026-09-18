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
  primary: governance
  facets: [governance.review, governance.resume, connection.information]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    取り込みの置き換え指定が「束に無い既存の行を superseded にする」規律で、承認・却下という
    人間の判断を経た行も対象に含めていた。しかも事前確認の画面がその範囲を開示しなかったため、
    教員は何を失うか知らずに確定できた。削除ではなく遷移であっても、人の判断を倒すなら情報の
    喪失であるという扱い方を決め直さなければ再発する点が統制の定義に当たる。
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
history: []
---

## 課題

**症状**: 取り込みで置き換えを指定すると、束に含まれない既存の行が表示対象から外れる。
その中に教員が承認・却下した行が含まれ、回復は手作業になる。事前確認の画面には対象の範囲が
出ない。

**原因**: 置き換えの規律が「束に無いものは古い」という単純な差分で、人間の判断を経た行を
区別していなかった。加えて、事前確認が件数も対象も示さないため、確定の時点で失うものが
分からなかった。

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
