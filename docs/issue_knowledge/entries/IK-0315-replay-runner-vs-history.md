---
id: IK-0315
title: 毎起動の全件再実行方式では、過去の適用の意味と最終状態を両立できない
status: resolved
recorded_at: 2026-07-13
resolved_at: 2026-07-13
sources:
  - docs/architecture/consolidation_survey_2026-07.md
feature_context:
  realizing: スキーマ変更の履歴を保ちながら、どの環境でも同じ最終状態へ収束させる
  layers: [migrations_db]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [resume]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 各ファイルの内容はそれ自体として正しい。

    構造: 同じファイル群が「履歴としての記録」と「毎回実行される手続」を兼ねる点が表現に当たる。再実行の統制と表裏なので中。

    接続: 段階間で情報・意味・条件・版が失われる要素は見当たらない。

    統制: 適用済みを記録せず全件を番号順に再実行する方式が、過去の変更の取り消し方を決められなくしている点が停止再開に当たる。
generalization:
  level: general
  general_form: 全件を再実行する運用のもとで、過去の手続が履歴でもあるため、変更の取り消し方が決まらない
pattern: replay-conflicts-with-history
discovery:
  perspective: [boundary_walk, guardrail_failure]
  note: >-
    複数の旧テーブルを 1 枚へ統合する変更を実装する段で、「再実行方式」と「過去の適用済み
    ファイル」の境界を歩いたときに、残す・消すのどちらも成立しないことが分かった。
resolution:
  perspective: [state_transition, explicit_contract]
  note: >-
    統合元のファイルは削除せず、実 DDL をコメントだけのスタブへ書き換える（最終状態への
    巻き戻し）。以前の列削除で同じ形を採っていたので、それを標準の型として明文化した。
    全ファイルが冪等でなければならない制約も併せて規約にし、検査で固定している。
  landed_in:
    - backend/core/migrations.py
    - backend/db/035_document_group_permissions.sql
    - backend/tests/test_migrations_runner.py
    - docs/architecture/consolidation_survey_2026-07.md
related: [IK-0314]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.resume, structure.representation]
    to: axes=processing=[none]; structure=[representation]; connection=[none]; governance=[resume]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

スキーマ変更の正本をファイル群に一本化し、毎起動で番号順に全件を再実行する方式を採った。
この方式では、複数の旧テーブルを 1 枚に統合するような変更で行き詰まる。統合元のファイルを
そのまま残せば、再起動のたびに旧テーブルが再作成され統合が繰り返される（作って即壊す往復）。
かといって削除すれば、過去に適用済みという事実の意味が変わってしまう。

原因は、同じファイル群が「履歴としての記録」と「毎回実行される手続」を兼ねていることにある。
どちらか一方なら素直に決まるが、両方であるために取り消し方が決まらない。

## 発見の観点

統合系の変更を実装する段で、再実行の仕組みと過去のファイルの境界を意図的に歩いた
（`boundary_walk`）。冪等でない変更が次回起動で壊れるという形で、検査にも現れた
（`guardrail_failure`）。

## 解決の観点

「削除でも放置でもなく、最終状態へ巻き戻す」という状態遷移を型にした（`state_transition`）。
実 DDL をコメントだけのスタブへ書き換えることで、履歴としての存在は残しつつ再実行では
何も起こらない。全ファイルが冪等であるという制約も規約として明示した（`explicit_contract`）。

## 一般化

「全部を毎回やり直す」運用（マイグレーション・シード・再解析・再構築）は再現性を得る代わりに、
過去の手続を消せなくする。取り消しは削除ではなく、最終状態への巻き戻しとして設計する。
新パターン `replay-conflicts-with-history` として辞書へ提案する。
