---
id: IK-0011
title: 分野の指定が後段の正規化へ伝わらず、既定の語彙が概念名を上書きする
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §2 F9
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §5 C2
  - docs/architecture/six_lenses_2026-09-10/06_coldstart.md
feature_context:
  realizing: 抽出した概念の表記を揺れの無い形に整え、教員が決めた別名も解析に効かせる
  layers: [pipeline_a, cartridges]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [condition, meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 正規化も語彙の読み込みも単体では正しく、既定へ落ちる分岐も所定の挙動である。
    構造: 元の表記と正規形を併記できず、上書きという不可逆な形しか表せない。
    条件が渡っていても、この上書きは表記を落とし続ける。
    接続: 分野という条件が呼び出し先へ渡らず、渡らないまま既定の語彙で概念名の意味が置き換わる。
    統制: 誰がいつ分野を決めるかは入口の課題として別に立てており、この経路には要素が無い。
generalization:
  level: general
  general_form: 上流で決めた条件が下流の呼び出しに渡らず、下流が黙って既定にフォールバックする
pattern: condition-not-propagated
discovery:
  perspective: [trace_walk, data_inspection]
  note: >-
    取り込みで選んだ分野が、どこまで届いているかを一本辿った。正規化の呼び出しには分野が
    渡っておらず、渡らないときは既定の語彙を読む実装になっていた。結果として、別分野の
    概念名が既定分野の言い方に書き換えられる。教員が確定した別名は逆に解析へ届いていなかった。
resolution:
  perspective: [carry_through, representation_change]
  note: >-
    分野の指定を取り込みから解析まで通し、指定が無いときは語彙を読まない規律を敷いた。
    正規化は元の名前を上書きせず、正規形を併記する形に変えて、書き換えという不可逆な
    操作をやめた。教員が確定した別名を第二の供給源として受ける経路も同時に張った。
  landed_in:
    - backend/core/concept_normalizer.py
    - backend/core/document_pipeline/orchestrator.py
    - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 7
related: [IK-0010, IK-0021]
view_of: [IK-0010]
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.condition, connection.meaning]
    to: axes=processing=[none]; structure=[none]; connection=[condition, meaning]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: processing=[none]; structure=[none]; connection=[condition, meaning]; governance=[none]
    to: processing=[none]; structure=[representation]; connection=[condition, meaning]; governance=[none]
    reason: 軸ごとの再判定で、正規化が上書きしか表せず元の表記を残せない点を構造の要素として追加した（条件が渡っても残る）
---

## 課題

症状は、別分野の論文を取り込んだときに、抽出された概念の名前が見慣れない分野の言い方に
書き換わることである。正規化の呼び出しに分野の指定が渡っておらず、渡らないときは既定の
語彙を読む実装になっていた。逆方向として、教員が確定した別名は解析へ届いていなかった。

原因は、上流で決めた分野という条件が下流まで運ばれず、下流が黙って既定へ落ちることにある。
各段は単体では正しい。落ちたことを知らせる仕組みが無いので、結果だけを見ると正しい正規化に
見える。

軸ごとに読み直すと、接続のほかに構造の要素がある。正規化が元の名前を上書きする形しか持たず、
正規形と元の表記を併記できない。分野の条件が正しく渡ったとしても、この不可逆さは残る。

## 発見の観点

入口で選んだ分野がどこまで届くかを一本辿った。届いていない箇所では既定にフォールバック
していて、例外も警告も出ない。名前が上書きされるため、元の表記が残らず、あとから気づく
手がかりも消えていた。

## 解決の観点

フォールバックの既定を賢くする案は採らなかった。渡っていないこと自体が問題だからである。
条件を取り込みから解析まで通し、指定が無いときは語彙を読まない規律にした。あわせて
正規化を上書きから併記に変え、元の表記を残した。教員の別名を解析へ返す経路も張った。

## 一般化

任意引数として条件を受け取り、無ければ既定を使う関数すべてで再発する。既定が妥当に
見えるほど、渡し忘れが長く残る。辞書の `condition-not-propagated` に対応し、処方は
「条件が無いときの既定を用意するのではなく、無いことを表せる値で処理を分ける」。
