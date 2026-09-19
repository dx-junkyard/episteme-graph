---
id: IK-0323
title: 衝突解消の結果を agent ID で引く写像で配るため、同名の別項目が一つに潰れ永続化が落ちる
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-17
sources:
  - docs/features/knowledge_objects_design.md §12.4
  - docs/architecture/knowledge_structure_review_2026-09-12.md §9
feature_context:
  realizing: 解析成果（式・主張・導出）を内容由来の安定キーを持つ一級の行として永続化し、再解析で差し替える
  layers: [knowledge_objects, pipeline_a, knowledge_transfer]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: 衝突解消の結果を `{agent_id: 最終キー}` の写像で表していた。写像は ID 1 つに値 1 つしか
    持てないので、同じ ID を名乗る別項目（別ブロックに印字された同じ式番号）を区別できず、両方の行が
    同じ最終キーを受け取る。表現を項目ごとの列に変えなければ、どの呼び出し側でも再発する。
    接続軸: 生成側（A層）は agent ID の一意性を約束しておらず、永続化側はそれを前提にしていた。
    前後の契約が両立していない。ただし直したのは前提を置かない書き手側で、契約そのものは
    文書に宣言しただけなので medium。処理軸: 写像を引く処理自体は、ID が一意という前提の下では
    正しく、単一処理の不良ではない。統制軸: 順序・担当・完了判定の要素は無い。
generalization:
  level: general
  general_form: 内側のスコープでしか一意でない識別子をキーにした写像で処理結果を配るため、同名の別項目が一つに潰れて後段の一意制約に当たる
pattern: id-unique-only-within-inner-scope
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    実際の論文 PDF を解析したところ、永続化の途中で式の live 行に対する部分一意索引違反が出て run が
    落ちた。同じ stable_key を受け取った 2 行を起点に、キーがどこで配られたかを遡り、衝突解消の
    結果が写像で返され、呼び出し側が agent ID で引いていることに行き着いた。
resolution:
  perspective: [representation_change, explicit_contract]
  note: >-
    衝突解消の結果を「ID で引く写像」から「入力と同じ並びの最終キーの列」（位置対応）に変えた。
    ID の一意化を上流（生成側）の責務にせず、書き手側が項目単位で防御する。旧 API は ID の一意性が
    保証される呼び出し側（起動時バックフィルの行 ID）専用として残し、「行に配るキーを写像で
    配らない」を規律として宣言した。回帰テストは是正前のコードで落ちることを確認してから入れた。
  landed_in:
    - backend/core/knowledge_objects/stable_key.py
    - backend/core/document_pipeline/persistence.py
    - backend/core/knowledge_objects/sync.py
    - backend/tests/test_knowledge_objects_persist.py
    - docs/features/knowledge_objects_design.md §12.4
related: [IK-0106, IK-0109]
view_of: [IK-0107]
history: []
---

## 課題

**症状**: 実論文の解析が永続化段で失敗し、run 全体が落ちる。エラーは式の live 行に対する
部分一意索引（stable_key の一意）違反。

**原因**: 同一 run 内で stable_key が衝突したとき `#2` などで解消する処理が、結果を
`{agent_id: 最終キー}` の写像で返していた。式の agent ID は印字番号から作られる（ラベル `7` →
`eq_7`）ので、同じ番号が別ブロックにも印字されていれば `eq_7` を名乗る項目が 2 件できる。写像は
1 件に潰れ、両方の行が同じ最終キーを受け取り、2 本目の INSERT が索引に当たった。式キーの
別の写像も同じ構造で、後勝ちのキーを両方の行に配っていた。

軸ごとの判断: 構造軸は写像という表現が同名項目の区別を表せないこと。接続軸は生成側が
ID の一意性を約束していないのに永続化側が前提にしていたこと。処理と統制には要素が無い。

## 発見の観点

実 PDF での再現（`reproduction`）が起点で、単体テストでは出なかった。同じキーを持つ 2 行から
キーの配られ方を遡る経路追跡（`trace_walk`）で、写像による潰れに行き着いた。合成データでは
同じ式番号が 2 ブロックに現れるケースを作っておらず、実データの偏りが検出条件だった。

## 解決の観点

**表現の変更**が主。衝突解消の結果を写像でなく「入力と同じ並びの列」で返すことで、同名の別項目が
別々のキーを受け取る。**契約の明示**が従。ID の一意化は上流の責務にせず（A層は非改変）、書き手側が
項目単位で防御する規律を文書に宣言し、旧 API は一意性が保証される呼び出し側専用に残した。

解消方法として残す発見:
- 「ID で引いて配る」処理は、ID の一意性を暗黙の前提にしている。前提を上流に要求するより、
  **位置対応（入力順の列）に変えれば前提そのものが消える**。
- 同じ構造の写像が複数箇所（5 経路・sync の最後の砦・式キーの写像）にあり、1 箇所直しても残る。
  **配り方の関数を 1 つにして全経路を通す**ことで、次に写像が増えるのを防ぐ。
- 回帰テストは是正前のコードで落ちることを先に確認する。落ちないテストは再発を防がない。

## 一般化

「内側でしか一意でない識別子を外側のキーに使う」型（`id-unique-only-within-inner-scope`）の
一例で、IK-0107（導出 step の ID がチェーン内でしか一意でない）と同じ原因の別視点。あちらは
キーの材料に外側の文脈を足して直し、こちらは結果の配り方を位置対応に変えて直した。同じ型に
2 つの処方があることを辞書の見分け方に足す価値がある。ID を辞書のキーに使う処理すべてで、
「その ID はどのスコープで一意か」を問えば再発を止められる。
