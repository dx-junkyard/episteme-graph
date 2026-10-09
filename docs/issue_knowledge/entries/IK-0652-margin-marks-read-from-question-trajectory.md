---
id: IK-0652
title: "教材で付けた印が欄外の印に出ず、付けた印や確定した引っかかりもコースを切り替えるまで欄外に現れない"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-09
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/return_door_design.md"
  - "docs/features/trace_registry_sovereignty_ledger_design.md"
  - "docs/features/understanding_cycle_design.md"
feature_context:
  realizing: "学習者が教材に印を付け、教材区画の欄外で自分の印・問い・引っかかりを見返す"
  layers: [understanding_cycle, trace_registry, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [target, version]
    governance: [none]
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
    確認: 欄外の印は「問いの軌跡」の一覧 API を読み、その一覧はサーバが印（anchor_mark）を除外していた（services.py の kind 除外）。キャッシュはコース切替でしか捨てなかった（app.js）。構造軸 representation = 登録簿に欄外の印という読み手の宣言が無く、母集合を表す場所が無い。接続軸 target = 読み手が別の読み手の母集合を流用した / version = 付けた後の更新が届かない。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "ある読み手のために絞った一覧を別の読み手の正本として流用し、前者の除外が後者で黙って効く"
pattern: projection-mistaken-for-source
discovery:
  perspective: [invariant_audit, trace_walk]
  note: "第 23 周の机上レビューで、帰還の扉の設計（欄外の印は本人の言葉）と、印の書き込みから欄外の表示までの読み元を辿って見えた。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "登録簿に欄外の印の宣言（margin_marks）を足し、専用の読み取り API がその宣言で母集合を決めるようにした。印を付けた直後・帰属を確定した直後に欄外を取り直す。問いの軌跡の除外は変えていない。"
  landed_in:
    - "backend/core/trace_registry.py"
    - "backend/core/cycle/derive.py"
    - "backend/core/cycle/queries.py"
    - "backend/api/routes/cycle.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_round23_learner_cycle.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場・ブラウザで印を付けた直後の欄外の表示"
      - "欄外の印の縦並びと教材の位置の対応（v1 は近似のまま）"
related: [IK-0662]
view_of: []
history: []
---

## 課題

症状: 「気になる」「まだ分からない」等の印が欄外に一度も出ず、同じセッションで確定した引っかかりも再読み込みまで出ない。原因: 欄外が問いの軌跡の一覧を流用し、その一覧が印を除外していた。キャッシュの更新の契機も無かった。

## 発見の観点

設計書（欄外の印 = 本人の言葉）× 読み元の SQL の読み合わせ（invariant_audit）と、書き込みから表示までの経路（trace_walk）。

## 解決の観点

読み手ごとの母集合を登録簿の宣言として正本化した（canonical_source）。宣言と読み元の一致を検査で固定した。

## 一般化

一覧を流用すると、流用元の除外規則が流用先の意図と無関係に効く。型は projection-mistaken-for-source。確かめ方は、流用元からだけ除外される種類の行を作り、流用先の読み手に出ることを見ること。
