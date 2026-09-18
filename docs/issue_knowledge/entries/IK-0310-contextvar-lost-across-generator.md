---
id: IK-0310
title: 実行文脈がジェネレータのスレッド跨ぎで失われ、帰属とモデル上書きが効かなくなる
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-12
sources:
  - docs/features/llm_response_streaming_design.md §3.2
  - docs/features/llm_response_streaming_design.md §3.3
feature_context:
  realizing: 生成中の応答を逐次配信しつつ、使用量の帰属とコース単位のモデル指定を保つ
  layers: [llm_streaming, usage_metering_u, model_selection_m]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 同じ書き方が同期経路では正しく動いており、処理そのものの誤りではない。

    構造: 条件と帰属を暗黙の実行文脈で運ぶという設計の置き場所が責務に当たる。運び方という表現の問題とも読めるため中。

    接続: 帰属とモデル指定という条件が、境界をまたいだときにだけ後段へ伝わらない点が条件に当たる。

    統制: 帰属が落ちること自体は結果で、順序・予算・停止再開の設計は崩れていない。
generalization:
  level: general
  general_form: 暗黙の実行文脈で運んでいる条件が、スレッド・ジェネレータ・プロセスの境界で失われる
pattern: context-lost-across-execution-boundary
discovery:
  perspective: [boundary_walk, doc_code_diff]
  note: >-
    「逐次配信」と「使用量の帰属・モデル解決」の境界を設計段階で歩き、配信の仕組みが
    ジェネレータをどう回すかと、既存の文脈の張り方を突き合わせた。
resolution:
  perspective: [required_argument, carry_through]
  note: >-
    ①実効モデルは文脈の内側で 1 回だけ確定し、以降は具体名を引数で渡す ②帰属は値で
    渡し、観測の直前に 1 回の再開の内側で文脈を開いて閉じる ③文脈ブロックの内側で
    yield しないことを構文木の検査で固定する。生成を専用スレッドへ追い出して待ち受ける
    案は、この用途でのスレッド用法が既存の背景処理と性格が違い、切断・例外・上限の面が
    増えるため採らなかった。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_llm_streaming_guardrails.py
    - docs/features/llm_response_streaming_design.md §3.2
related: [IK-0311]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.condition, structure.responsibility]
    to: axes=processing=[none]; structure=[responsibility]; connection=[condition]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

既存の同期経路は、使用量の帰属先とコース単位のモデル指定を暗黙の実行文脈で運んでいる。
これを逐次配信へ広げると、配信の仕組みがジェネレータを再開するたびに別スレッド・複製
された文脈になりうるため、**文脈をまたいで yield する書き方が壊れる**。壊れ方は二通りで、
帰属が未帰属に落ちて使用量が誰のものか分からなくなるのと、文脈の復元で例外になる
（利用者から見れば応答が落ちる）。

## 発見の観点

実装前に「逐次配信」と「計測・モデル解決」の境界を歩き（`boundary_walk`）、配信の仕組みの
再開規則と既存の文脈の張り方を突き合わせた（`doc_code_diff`）。動かしてから気づくと、
帰属の欠落は静かなので長く残る種類の不具合である。

## 解決の観点

暗黙の文脈に頼るのをやめ、境界を越える値は**引数で渡す**（`required_argument`）ことで
後段まで運ぶ契約にした（`carry_through`）。文脈が必要な処理は、1 回の再開の内側で開いて
閉じる。文脈ブロックの内側に yield が無いことを構文木で検査して再発を禁じた。

## 一般化

「暗黙に持ち回る条件」（実行文脈・スレッドローカル・リクエストスコープ）は、非同期化・
ストリーミング・並列化のたびに境界で失われる。境界を作る変更では、暗黙に運んでいるものを
先に数える。新パターン `context-lost-across-execution-boundary` として辞書へ提案する。
