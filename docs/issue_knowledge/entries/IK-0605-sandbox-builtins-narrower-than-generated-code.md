---
id: IK-0605
title: "隔離実行の組み込み関数の許可が生成コードの前提より狭く、正当なプログラムが未定義名で落ちる"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/features/simulation_bridge_design.md §12.2
feature_context:
  realizing: "生成された理論プログラムを、組み込み関数を絞った隔離環境で実行する"
  layers: [simulation_bridge_sb, deployment]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals:
    - kind: value
      target_axis: connection
      neighbor_of: [connection.contract, connection.condition]
      statement: 前段が前提にした実行環境の能力の範囲と、後段が実際に提供する能力の範囲が食い違い、値の形ではなく使える操作の有無で処理が落ちる
      confidence: medium
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 生成コードは `callable` を使っており、sim-runner の harness が許可した組み込み関数の一覧に無かったため NameError で
    止まった。生成側は通常の Python を前提に書き、実行側はその前提を満たさない狭い組み込みだけを提供していた。接続軸
    contract = 生成側と実行側が共有すべき「使える組み込み」の契約が片側（実行側）でだけ絞られ、生成側に伝わっていなかった。
    確信度 medium は、これが値の形の契約ではなく能力の範囲の契約で、contract の定義の端にあるため。処理軸・構造軸・統制軸は
    単一処理の誤り・表現・順序の問題ではないので none。
generalization:
  level: general
  general_form: "生成する側は標準的な実行環境を前提に書き、実行する側は安全のため能力を絞るが、絞った範囲を生成側と突き合わせないため、無害な正当な処理が未定義で落ちる"
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction]
  note: "実機でテスト用の学生として実行したところ NameError で止まり、生成コードが使う名前と harness の許可一覧を突き合わせた。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    許可一覧に読み取り専用で副作用の無い組み込み（`callable` / `any` / `all` / `divmod` / `iter` / `next` / `frozenset` /
    `slice` と一般的な例外型）を足し、`print` は何もしない関数にした（標準出力は結果の JSON 行に予約されているため）。
    入出力・import・属性操作に関わる組み込みは足していない。harness のテストで固定した。
  landed_in:
    - simrunner/server.py
    - backend/tests/test_simrunner_harness.py
    - 7c1440b
    - docs/features/simulation_bridge_design.md §12.2
  verification:
    methods: [guardrail, docker_e2e]
    unverified:
      - "生成コードが実際に使う組み込みの体系的な洗い出し（実行の事実文に残る NameError を後から集めて許可の過不足を見る提案）"
related: [IK-0603]
view_of: []
history: []
---

## 課題

生成された理論プログラムが `callable` を使っていたが、隔離実行の harness が許可する組み込みにそれが無く、正当なプログラムが
NameError で落ちた。生成側は通常の Python を前提にし、実行側は安全のため組み込みを絞っていたが、絞った範囲が生成側の前提と
突き合わされていなかった。

## 発見の観点

実機で学生として実行し、未定義名の失敗から生成コードの使う名前と許可一覧を突き合わせた。

## 解決の観点

安全の境界（入出力・import・属性操作を渡さない）は変えずに、読み取り専用で副作用の無い組み込みだけを足した。`print` は
標準出力の契約を壊さないよう捨てる実装にした。生成側のプロンプトに許可一覧を書き込む案は、生成が守らない場合の失敗を残すので
単独では採らなかった。

## 一般化

生成と隔離実行を組み合わせると、実行側の絞り込みが生成側の暗黙の前提と食い違う。辞書の型は `contract-changed-one-side`
（実行側だけが標準環境から能力を絞った）。契約が値の形ではなく実行環境の能力である点は、新しい値の提案として残した。
