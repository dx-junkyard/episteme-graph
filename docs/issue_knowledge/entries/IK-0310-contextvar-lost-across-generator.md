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
  primary: connection
  facets: [connection.condition, structure.responsibility]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「帰属とモデル上書きを実行文脈（contextvar）で運んでいるのに、配信の仕組みが
    ジェネレータを別スレッドで再開しうること」。前段（文脈を張る側）も後段（値を読む側）も
    単体では正しく、境界をまたいだときだけ条件が伝わらない（かつ復元時に例外にもなる）
    ため接続。設計書 §3.2 が、帰属が未帰属に落ちコース単位の上書きが効かなくなることを
    明記している。
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
history: []
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
