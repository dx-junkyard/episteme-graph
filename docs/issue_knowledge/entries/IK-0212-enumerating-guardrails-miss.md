---
id: IK-0212
title: 検査が名前の列挙と文字列の存在確認に依るため、後から増えた実装と実行時の不整合を覆えない
status: resolved
recorded_at: 2026-07-17
resolved_at: 2026-07-18
sources:
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §2 P3
  - docs/architecture/issue_494_implementation_review_2026-07-16.md
  - docs/development_checklist.md §4
feature_context:
  realizing: 一度直した規律を、後からの変更でも壊れないよう仕組みで守る
  layers: [tests_guardrails, admin_copilot, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    検査対象の集合を人が列挙し、その名前や文字列が存在するかだけを見る確認手続きのため、
    後から増えた実装と、実行時にしか現れない不整合が確認の網から外れる。処理も表現も正しい
    のに「確かめたことになる範囲」の決め方が崩れているので統制とする。集合を導出できる形で
    持っていない点は副次の表現の問題。
generalization:
  level: repo_pattern
  general_form: >-
    検査が対象を手で列挙し存在確認だけを行うため、後から増えた経路と実行時の不整合を覆えず、緑のまま規律が破れる
discovery:
  perspective: [guardrail_failure, adversarial_review]
  note: >-
    直した翌日に追加された関数だけが同じ防御を持たないことに気づき、検査の書き方を読んだ。
    検査が関数名を列挙していたため、新しい関数は最初から対象外だった。別のレビューでも、
    静的な検査が覆えない不整合が五つの類型として整理されていた。
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: >-
    検査を対象の列挙から集合の検査へ寄せ（双方向の網羅・画面単位の照合）、加えて新機能の
    変更で案内の目印と画面側の登録を同じ変更で更新する規律を置いた。検査を緩める方向には
    倒さず、落ちたら実装か文書を直すという向きを明文化した。
  landed_in:
    - backend/tests/test_admin_assistant.py
    - backend/tests/test_admin_help_inspect_ui_static.py
    - frontend/public/js/personal-map.js
    - docs/development_checklist.md §4
related: [IK-0211, IK-0203]
view_of: []
pattern: guardrail-does-not-cover-new-path
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.review, structure.representation]
    to: axes=processing=[none]; structure=[representation]; connection=[none]; governance=[review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 一度直した規律が、静かに破れている。①コース切替の競合を防ぐ処理を三つの関数へ
入れた**翌日**に追加された四つ目の関数だけ防御が無い ②再オープン時に直前の状態を復元する
処理は、全ての呼び出し元が状態を明示するため一度も発火しないデッドコードだった
③案内の目印が実在するかの検査が文字列の部分一致で、画面との対応までは見ていなかった。

**原因**: 検査が対象を手で列挙し、その名前や文字列が存在するかだけを見ている。新しく増えた
実装は最初から対象外であり、存在するが呼ばれない・存在するが食い違うといった実行時の性質は
検査できない。別のレビューは、この方式で検出できないものを五つの類型として挙げている
（状態の汚染・集計漏れ・失敗応答を成功として扱う誤り・描画側と検索側の名前の不一致・
裏づけの無い完了表示）。

## 発見の観点

`guardrail_failure` と `adversarial_review`。「検査が緑であること」を根拠にせず、検査そのものを
読んで**何を確かめていないか**を問う視点で見つかる。緑であることが安心の根拠になっている
ぶん、気づきにくい。

## 解決の観点

`explicit_contract` — 検査対象を人の列挙ではなく登録簿という宣言から導く形にし、片側だけの
変更を禁じる規律を運用チェックリストに置いた。そのうえで `guardrail_fix` として、列挙を集合の
検査へ寄せ、双方向の網羅（登録簿の全件に担い手があること／担い手の全件が登録簿にあること）へ変えた。実行系のテストを増やす方向は残課題として記録されている。

## 一般化

「存在するか」を見る検査は、存在するが死んでいる・存在するが食い違う・後から増えたものを
覆えない。静的な検査全般、設定の lint、文書の網羅テストで同型が再発する。辞書の型は
`guardrail-does-not-cover-new-path`。
