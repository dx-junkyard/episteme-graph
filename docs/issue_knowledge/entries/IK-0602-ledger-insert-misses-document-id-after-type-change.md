---
id: IK-0602
title: "台帳列の型を締めた migration に台帳行を作る書き手が追随せず、対象だけを渡す新規作成がすべて NOT NULL 違反で落ちる"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/features/simulation_bridge_design.md §12.2
feature_context:
  realizing: "学習者が自分の検証シミュレーションの実行を共有し、その結果を認識的地位台帳の候補として残す"
  layers: [simulation_bridge_sb, doubt_d, migrations_db]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 共有 API が 500 になり、api-server のログに epistemic_ledger.document_id の NotNullViolation が出ていた。
    migration 080 がこの列を「既定値つきの文字列」から「既定値なしの UUID NOT NULL」に変えたが、台帳行を
    (target_id, target_type) だけで作る書き手 — 新設の検証シミュレーションの store と、既存の D層 route の
    `_get_or_create_ledger_row` — は列を渡さないままだった。D層側は ledger_builder が先に行を作るため滅多に通らず、
    080 以降ずっと潜在していた。接続軸 contract = スキーマ側の書き込み契約だけが変わり、書き手の契約は変わらなかった。
    統制軸 completion = 型を締める変更の完了条件に「その表へ INSERT する全経路の追随」が含まれていなかった。
    処理軸は単一処理の誤りではないので none。構造軸は表現の置き場所の問題ではないので none。
generalization:
  level: general
  general_form: "保存先の列の制約を締める変更が、その保存先へ書く既存の書き手を洗い出さずに入り、まれにしか通らない書き手が潜在的に壊れたまま残る"
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction, data_inspection]
  note: "実機の dev スタックでテスト用の学生として実行を共有したところ 500 になり、api-server のトレースバックの NOT NULL 違反から migration 080 の列変更と、同じ表へ行を作る既存の D層の経路まで辿った。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    台帳行の新規作成を一つの正本に寄せた。対象の種別ごとに live ビューから所属する document を引く
    `resolve_target_document_id` と、document を含めて行を作る `ensure_ledger_row` を新設し、検証シミュレーションの
    store と D層 route の両方がこれを通る。所属を引けない対象は行を作らない（推測で document を埋めない）。
  landed_in:
    - backend/core/doubt/ledger_targets.py
    - backend/core/simulation_bridge/store.py
    - backend/api/routes/doubt.py
    - backend/tests/test_doubt_ledger_targets.py
    - 48f7cb1
    - docs/features/simulation_bridge_design.md §12.2
  verification:
    methods: [docker_e2e, guardrail]
    unverified:
      - "migration 080 が型を締めた他の表・列へ INSERT する経路（080 が変換した表と列の組ごとに、その表への INSERT が当該列を渡しているかを検査するガードレールを後続で置く提案）"
related: [IK-0599]
view_of: []
history: []
---

## 課題

学習者が検証シミュレーションの実行を共有すると 500 になった。原因は、migration 080 が台帳の document 列を既定値なしの
NOT NULL に締めたのに、台帳行を対象（target_id / target_type）だけで作る書き手がその列を渡していなかったこと。
新設の store だけでなく、既存の D層 route の作成ヘルパーも同じ形で、080 以降ずっと潜在していた（D層側は先行する
バックフィルが行を作るため、新規作成の経路がほとんど通らなかった）。

## 発見の観点

実機で共有を押した再現から、トレースバックの制約違反を起点に、列を変えた migration と、同じ表へ行を作る全経路を探した。
新設の経路だけを直すと既存の潜在経路が残るので、書き手の洗い出しまで行った。

## 解決の観点

台帳行の作成を正本の一関数に寄せ、所属 document を live ビューから引いて渡すようにした。所属が引けない対象では行を作らない
（推測で埋めると誤った論文に台帳が付く）。080 が変換した他の表についての網羅検査は範囲を超えるので後続の提案に留めた。

## 一般化

保存先の制約を締める変更は、その保存先へ書く全経路を相手にした契約変更である。まれにしか通らない経路ほど長く潜在する。
辞書の型は `contract-changed-one-side`。確かめ方は、制約を締めた表への INSERT を全件洗い出し、各経路を実際に流すこと。
