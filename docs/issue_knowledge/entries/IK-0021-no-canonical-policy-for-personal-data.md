---
id: IK-0021
title: 人に関するデータの扱いが機能ごとに決まり、横断の規約が一本も無い
status: deferred
recorded_at: 2026-09-10
resolved_at: null
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §6 D2
  - docs/architecture/six_lenses_2026-09-10/known_issues_architecture.md A-07
  - docs/architecture/six_lenses_2026-09-10/known_issues_architecture.md H-04
feature_context:
  realizing: 学習者と教員の記録を保持しながら、本人の権利と保護の要請を両立させる
  layers: [learner_experience_b, account_lifecycle, trace_registry]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [none]
    governance: [assignment]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「人に関するデータの保持・消去・開示の規約が機能ごとに別々に決まっていて、
    横断の正本が無い」こと。各機能の実装はそれぞれの規約に従って正しく動いており、条件や
    版が落ちているわけでもない。正本を一本にしないかぎり、機能が増えるたびに別の規約が
    増え、既存の規律と衝突する。確認手段は、行を消さない規律が多くのモジュールで敷かれて
    いる一方で、本人による消去の経路が一つも無いこと。
generalization:
  level: general
  general_form: 同じ対象についての規約が機能ごとに別々に決まり、横断で矛盾したまま並ぶ
pattern: duplicate-canonical-sources
discovery:
  perspective: [inventory, invariant_audit]
  note: >-
    人に関するデータを扱う経路を棚卸しし、保持・消去・開示の三点で突き合わせた。行を消さない
    規律は広く敷かれているのに、本人が自分の記録の本文を消す経路は無い。個票をそのまま
    見せる画面と、集約しか見せない画面が同じ根拠で説明されていた。
resolution:
  perspective: [deferred_decision]
  note: >-
    保留。法と倫理の領域に属するため、技術的な最適解が定まらない。推奨としては、個票では
    名前を伏せる・変更できない記録を新たに作らない・本人の痕跡は墓標を残して消せるようにする、
    の三点が挙がっている。どれを採るかは制度の前提に依存する。
  landed_in: []
related: [IK-0014, IK-0022]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.aggregation, governance.assignment]
    to: axes=processing=[none]; structure=[aggregation]; connection=[none]; governance=[assignment]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は、人に関するデータの扱いが画面ごとに違い、しかもその違いを説明する一本の規約が無い
ことである。行を消さないという規律が多くのモジュールで敷かれている一方、本人が自分の記録の
本文を消す経路は存在しない。個票をそのまま見せる画面と、集約しか見せない画面が並んでいる。

原因は、規約の正本が無く、機能ごとに別々に決まってきたことにある。個々の実装はそれぞれの
規約に忠実で、単体では何も壊れていない。横断で見たときに矛盾が現れる。

## 発見の観点

人に関するデータを扱う経路を棚卸しし、保持・消去・開示の三点で突き合わせた。消さない規律と
消したい要請が正面から衝突しており、どちらが優先するかを決める文書がどこにも無い。

## 解決の観点

保留。法と倫理の領域なので、技術的な最適解が一意に定まらない。推奨として、個票では名前を
伏せる・変更できない記録を新たに作らない・本人の痕跡は墓標を残して消せるようにする、の
三点が挙がっている。どれを採るかは運用する制度の前提に依存するため、判断を待つ。

## 一般化

同じ対象について複数の機能がそれぞれの都合で規約を決める場所すべてで再発する。個々は正しく、
横断でだけ矛盾する。辞書の `duplicate-canonical-sources` に対応し、処方は「対象を主語にした
規約の正本を一本立て、機能はそれを参照する」。
