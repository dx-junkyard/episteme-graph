---
id: IK-0597
title: "ライブラリ store の単体テストが同梱シードの初版凍結を経由して embedding API を実際に呼ぶ（シードの走査先が実リポジトリのデータを指したまま）"
status: resolved
recorded_at: 2026-10-02
resolved_at: 2026-10-02
sources:
  - docs/features/simulation_bridge_design.md §12.1
feature_context:
  realizing: "分野別ナレッジライブラリに同梱シードを取り込み、初版を凍結して retrieval から見えるようにする（検証シミュレーション層の部品シードを足す）"
  layers: [image_library_l, tests_guardrails]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [condition]
    governance: [budget]
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
    確認: `backend/core/library/seed.py` の `LIBRARY_COMMON_DIR` はモジュール定数で実リポジトリの `backend/library_common/`
    を指し、取込直後の初版凍結（P0-7）が embedding を計算する。`tests/core/test_library_store.py` の 2 件はこの経路を
    embedding のモックなしで通り、走査先が空か不在であることに暗黙に頼っていた。部品シード
    `library_common/instrument_parts.json` を足した時点で実呼び出しに到達した（CI のダミーキーで 401）。
    統制軸 budget = 単体テストの外部呼び出し予算（ゼロ）が強制されていない。構造軸 responsibility = シード取込が
    凍結と embedding という外部副作用を内側に抱え、呼び出し側から見えない。接続軸 condition = テストの前提
    （外部に出ない）がシード走査の経路まで伝わらず、走査先はテスト側から差し替えられていなかった。処理軸は判定や
    計算の誤りではないので none。
generalization:
  level: general
  general_form: "単体テストが実リポジトリのデータ置き場に暗黙に依存し、そこへデータが足されると、内側に隠れた外部呼び出しの経路に到達する"
pattern: external-budget-exceeded
discovery:
  perspective: [guardrail_failure, trace_walk]
  note: "部品シードを足したあとにスイートを流すと、既存のライブラリ store テストが embedding の認証失敗で落ちた。失敗から seed の走査先と初版凍結の embedding まで辿った。"
resolution:
  perspective: [order_and_budget, guardrail_fix]
  note: >-
    テスト側で外部呼び出しの予算をゼロに保つ見立て。autouse fixture で `library_seed.LIBRARY_COMMON_DIR` を tmp_path の
    不在ディレクトリに差し替え、同梱シードの有無にテストの挙動が依存しないようにした。走査先そのものを使う検査は
    明示的に別の tmp ディレクトリを渡す。シード取込から embedding を外す案は、凍結版しか読まない retrieval に初版を
    見せる P0-7 の目的を崩すので採らなかった。該当する解決原理は無い。
  landed_in:
    - backend/tests/core/test_library_store.py
    - backend/tests/core/test_library_seed.py
    - docs/features/simulation_bridge_design.md §12.1
  verification:
    methods: [reproduction_rerun, guardrail]
    unverified:
      - "CI での実行"
      - "ライブラリ以外で同梱データの走査を経由して外部に出る単体テスト"
related: []
view_of: []
history: []
---

## 課題

ライブラリ store の既存の単体テストが、同梱シードの初版凍結を経由して embedding API を実際に呼んでいた。シードの走査先は
モジュール定数で実リポジトリのデータ置き場を指したままで、テストは「そこが空か不在である」ことに暗黙に頼っていた。
検証シミュレーション層の部品シードを足したことで走査先にデータが入り、隠れていた外部呼び出しに到達した。CI のダミーキー
なので認証失敗で止まり課金は無かったが、本物のキーがある環境では単体テストが課金される。

## 発見の観点

部品シードを足したあとのスイート実行で既存テストが落ち、その失敗から走査先の定数と初版凍結の embedding まで経路を辿った。

## 解決の観点

単体テストの外部呼び出しの予算をゼロに保つ。走査先をテスト側で必ず差し替え、同梱データの有無でテストの経路が変わらないように
した。シード取込から embedding を外す案は、初版を retrieval に見せる目的を崩すため採らなかった。

## 一般化

テストが実データの置き場所に暗黙に依存していると、データの追加が外部副作用への経路を開く。外部を呼ぶ処理を内側に持つ取込・
初期化は、テストから置き場所を差し替えられるか、外部呼び出しをモックしているかを問う。辞書の型は `external-budget-exceeded`
（操作あたりの外部呼び出し予算 — ここでは単体テストの予算ゼロ — が強制されていない）。設計書の pre-mortem はこの型を
「CostGate 日次上限で既に塞ぐ」としていたが、CostGate を通らないテスト経路は覆っていなかった。
