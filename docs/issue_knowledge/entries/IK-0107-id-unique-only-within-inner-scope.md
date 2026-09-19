---
id: IK-0107
title: 内側のスコープでしか一意でない識別子を、外側のキーとして使っている
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-17
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12/B_storage.md S-3
  - docs/architecture/knowledge_structure_review_2026-09-12/A_fidelity.md F-4
  - docs/features/knowledge_objects_design.md §12.2 V-2 / V-5
  - docs/features/knowledge_objects_design.md §12.4
feature_context:
  realizing: 抽出した主張・式・導出ステップを文書全体で一意に指し、逆引きできるようにする
  layers: [knowledge_objects, pipeline_a, knowledge_transfer]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: キーを「識別子から引く写像」で配ったため、同じ識別子を持つ 2 行が同じキーを受け取り、衝突が解消されないまま書き込まれた（配り方も表現の一部という読み方が残る）。構造:
    識別子の一意性の範囲が、それを使うキーの範囲より狭い。区画ごとに振り直される番号、鎖の中でだけ一意なステップ番号、印字された式番号が、いずれも文書全体のキーに使われていた。接続:
    段をまたいで情報・意味・条件・対象・版が失われてはいない（外側のスコープへ持ち出す場面で露見するため、境界の読み方は残る）。統制: 順序・予算・再開・担当・レビュー・完了の扱いに崩れは無い。
generalization:
  level: general
  general_form: >-
    内側のスコープでのみ一意な識別子を外側のキーに流用するため、別スコープの対象どうしが同じ
    キーに衝突し、逆引きが曖昧になるか一意制約で処理が落ちる
pattern: id-unique-only-within-inner-scope
discovery:
  perspective: [data_inspection, reproduction]
  note: >-
    逆引きキーの実データが全行同じ値であることを数えた段で最初に見え、実論文 2 本を新しい DB に
    通したときに一意制約違反として再現した。さらに実 PDF の解析失敗（2026-09-17）で、同じ型が
    式 ID にも存在することが分かった。
resolution:
  perspective: [representation_change, single_point_fix]
  note: >-
    キーの材料に外側の文脈（親の識別子・文書内の位置）を足して一意性の範囲を揃え、キーを配る側は
    「識別子から引く写像」ではなく「項目ごとに割り当てる列」に変えた。書き手と取り込み側が同じ
    関数を使うことをテストで固定した。
  landed_in:
    - backend/core/knowledge_objects/stable_key.py
    - backend/core/document_pipeline/persistence.py
related: [IK-0106]
view_of: [IK-0323]
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, local.logic]
    to: axes=processing=[logic]; structure=[representation]; connection=[none]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 同じ主張を指すはずの逆引きキーが 9 通りに曖昧化する。導出 72 ステップが 9 件に潰れて
一意制約に当たり、解析実行そのものが失敗する。同じ式番号が 2 か所に印字された論文で、
永続化が丸ごと落ちる。

**原因**: 識別子の一意性の範囲が、それを使う側のキーの範囲より狭かった。区画の中でしか一意でない
番号、導出鎖の中でしか一意でないステップ番号、印字番号から作る式 ID を、どれも文書全体のキーに
使っていた。さらにキーを「識別子 → キー」の写像で配っていたため、重複した識別子を持つ 2 行が
同じキーを受け取り、衝突が解消されないまま書き込まれた。

## 発見の観点

逆引きキーの実データを数えた段階で曖昧さが見え（`data_inspection`）、実論文を新しい DB に通す
再現で一意制約違反として現れた（`reproduction`）。同型が別の識別子にも潜んでいることは、
実ファイルの解析失敗という形で後から分かった。

## 解決の観点

衝突検出を強くするだけでは「潰れて 1 件になる」方は直らないため、キーの材料に外側の文脈を
足して一意性の範囲を合わせた（`representation_change`）。配り方は写像から項目ごとの割り当てに
変え（`single_point_fix`）、書き手と取り込み側が同じ規則を使うことを検査で固定した。

## 一般化

「章の中で 1 番」「チェーンの中で 3 番目」「ページに印字された番号」のような**局所の名前**は、
外に出した瞬間にキーとして機能しなくなる。取り込み・エクスポート・全体索引など、
スコープを 1 段広げる場所は必ずこの型の候補になる。
