---
id: IK-0311
title: テストが関数本体の字面を検査対象にしているため、正しい再分割ができない
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-12
sources:
  - docs/features/llm_response_streaming_design.md §3.3
feature_context:
  realizing: 前処理と後処理を二重に持たずに、同期応答と逐次配信の両方を通す
  layers: [llm_streaming, tests_guardrails, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [none]
    governance: [review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「不変条項の検査を、関数本体のソース文字列を切り出して行っていること」。
    検査対象が実装の形そのものなので、意味を変えない再分割でも落ちる。個々のテストを
    直すのではなく、検査が何に依存するかという責務の置き方を変えない限り再発するため構造。
    設計書 §3.3 が、本体を切り出す検査 7 本と逐語で固定された 3 点を列挙している。
generalization:
  level: repo_pattern
  general_form: 不変条項の検査が実装の字面に依存し、意味を変えない再構成を妨げる
pattern: test-pins-implementation-text
discovery:
  perspective: [guardrail_failure, inventory]
  note: >-
    起草時に想定した 3 分割の実装可能性を確かめるため、対象関数の本体を検査している
    テストを全件洗い出したところ、本体切り出しが 7 本・逐語固定が 3 点あった。
resolution:
  perspective: [responsibility_move, explicit_contract]
  note: >-
    関数を割らずに生成器へ変える方式を採り、本体の字面を保ったまま継ぎ目だけを足した。
    検査側を書き換える案は、その検査が守っている条項（判定順・保存順・足場の文言）の
    再確認が必要で、変更の危険が実装より大きいため今回は採らなかった。新しい経路を
    どこに置くか（既存経路と本体の間に挟まない）を設計上の制約として明記した。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_llm_streaming_guardrails.py
    - docs/features/llm_response_streaming_design.md §3.3
related: [IK-0310]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.responsibility, governance.review]
    to: axes=processing=[none]; structure=[responsibility]; connection=[none]; governance=[review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

同じ前処理・後処理を二つの経路が別々に持たないようにするには、対象の関数を分割するのが
自然である。しかし対象関数には、本体のソース文字列を切り出して語句の順序や存在を検査する
テストが複数あり、意味を変えない再分割でも検査対象から外れて落ちる。

原因は、不変条項（判定の順序・保存と抽出の前後関係・足場の文言）の検査を、**実装の字面**
に対して行っていること。条項そのものは正しく、検査したい性質も妥当だが、依存先が形なので
構造の改善を妨げる。

## 発見の観点

分割方式が成立するかを確かめるために、対象関数の本体を検査しているテストを全件棚卸しした
（`inventory`）。実際に分割すれば赤になることが分かる形の依存であり、ガードレール自身が
設計の制約として現れた（`guardrail_failure`）。

## 解決の観点

実装の形を保つ方式（関数を割らずに生成器にする）へ寄せ、継ぎ目だけを足した
（`responsibility_move` の適用先を「分割」から「継ぎ目」へ変えた）。新しい経路を既存経路と
本体の間に挟まないことを設計上の制約として明記した（`explicit_contract`）。検査側を先に
書き換える案は、その検査が守っている条項の再確認を伴い危険が大きいため見送っている。

## 一般化

「ソースを読む検査」は、設計原則をコードの外から縛れる強力な手段だが、字面に依存した分だけ
将来の再構成の自由度を奪う。守りたい性質を、字面ではなく観測可能な振る舞いで書けるかを
毎回問う。新パターン `test-pins-implementation-text` として辞書へ提案する。
