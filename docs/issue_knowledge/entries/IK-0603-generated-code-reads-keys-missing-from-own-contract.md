---
id: IK-0603
title: "生成した理論プログラムが自分の入出力契約に無いキーを読み、入力欄が作られないまま実行時に落ち、その理由も学習者に見えない"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/features/simulation_bridge_design.md §12.2
feature_context:
  realizing: "学習者が理論モジュールから生成された理論プログラムに値を入れて動かし、結果を確かめる"
  layers: [simulation_bridge_sb, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 生成コードは `params["b_1"]` を読んでいたが、同じ生成物の io_contract に b_1 が無く、学習者の入力フォームは
    契約からしか欄を作らないため値を求めず、sandbox が KeyError で止まった。runner のエラーは内部情報として隠され、
    画面には「実行は完了しませんでした」だけが出た。接続軸 contract = コードが要求する入力と、宣言された契約の一致が
    境界で一度も検査されていなかった。構造軸 representation = 入力の宣言がコードとは別の表現として持たれ、コードから
    導かれていなかった。処理軸 wording（確信度 low）= 失敗の理由を伏せた文言が症状を「原因の分からない失敗」にした
    側面で、主因ではない。統制軸は順序・確定の問題ではないので none。
generalization:
  level: general
  general_form: "生成物の本体と、本体が要求する入力の宣言が別々に作られ、両者の一致を確かめないため、宣言に無い入力を本体が要求して実行時にだけ落ちる"
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [reproduction, trace_walk]
  note: "実機でテスト用の学生として実行したところ理由の無い失敗文だけが出た。runner の記録の KeyError から、生成コードが読むキーと契約の欄を突き合わせて欠落を見つけた。"
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: >-
    契約をコードから補完し、実行前に検査する形にした。`ast_guard.required_keys` がコードの `inputs[...]` /
    `params[...]` / `.get(...)` のキーを拾い、`codegen.complete_contract_from_source` が契約に無いキーを足す。
    実行前に `service.missing_required_inputs` が足りない入力を名指す事実文を返して sandbox に触れない。
    実行時エラーは学習者自身のコードの失敗なので、衛生化した 1 行を `service.error_facts` で見せる
    （失敗を伏せて成功・不明に見せない — `failure-reported-as-success` の側面）。
  landed_in:
    - backend/core/simulation_bridge/ast_guard.py
    - backend/core/simulation_bridge/codegen.py
    - backend/core/simulation_bridge/service.py
    - backend/tests/test_simulation_bridge_service.py
    - 907e9ce
    - docs/features/simulation_bridge_design.md §12.2
  verification:
    methods: [guardrail, docker_e2e]
    unverified:
      - "修正前に生成された理論プログラムの契約（遡って補完していない。学習者がコードを編集すると再補完される経路はある）"
related: [IK-0604, IK-0605]
view_of: []
history: []
---

## 課題

生成された理論プログラムが、自分の入出力契約に載っていないキーを読んでいた。入力フォームは契約から作られるのでその値を求めず、
sandbox で KeyError になった。さらに runner のエラーは内部情報として隠され、学習者には理由の無い失敗文だけが見えた。

## 発見の観点

実機で学生として実行し、理由の無い失敗から runner の記録へ降り、コードが読むキーと契約の欄を突き合わせた。

## 解決の観点

宣言（契約）を本体（コード）から補完し、実行前に足りない入力を名指す検査を置いた。生成側のプロンプトを強めるだけの案は、
生成が守らなかったときに同じ失敗が残るので採らなかった。エラーの表示は、学習者自身のコードの失敗であり内部情報ではないため、
衛生化して 1 行見せることにした。

## 一般化

本体と宣言を別々に作る生成物（コードと入出力仕様、テンプレートと変数一覧）は、両者の一致を境界で検査しない限り宣言の
欠落が実行時にだけ現れる。辞書の型は `unchecked-type-contract-at-boundary`（最初から境界の検査が無い）。失敗理由を伏せる
表示は `failure-reported-as-success` に近い副症状で、原因の切り分けを遅らせる。
