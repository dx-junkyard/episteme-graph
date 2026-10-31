---
id: IK-0657
title: "配置の行の「再検討」ボタンを押した結果の状態名が「確認待ち（AI推定）」と別の語で出る"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-09
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/release_review_flow_design.md"
feature_context:
  realizing: "教員がリリース前の確認と教材管理の位置づけで、配置の行を再検討に回し、その状態を読む"
  layers: [release_review, knowledge_landscape, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [aggregation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: サーバの教員向け状態名は review_required を「再検討中」としていたが、管理画面の JS にある状態名のフォールバック表は「確認待ち（AI推定）」（学習者向けの出所ラベルの語）を持ち、サーバの表と照合されていなかった。構造軸 aggregation = 状態名の表が正本とフォールバックに分かれ、一致が固定されていない。処理軸 wording = 押したボタンと結果の語が違う。接続軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "画面のフォールバック表が正本の語彙表と照合されず、別の宛先の語が混ざって正本と食い違う"
pattern: duplicate-canonical-sources
discovery:
  perspective: [invariant_audit, inventory]
  note: "第 23 周の机上レビューで、行のボタンの語と状態名、サーバの状態名表と JS のフォールバック表を並べて見えた。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "教員向けの状態名の正本を label_vocab に置き（landscape/schema の表はその別名）、管理画面の 2 か所のフォールバック表を逐語ミラーとして一致検査で固定した。"
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/core/landscape/schema.py"
    - "frontend/public/js/admin-release-review.js"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_landscape_status_vocab_mirror.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場・ブラウザで再検討を押した行の状態名"
related: [IK-0649]
view_of: []
history: []
---

## 課題

症状: 「再検討」を押した行が「確認待ち（AI推定）」と表示される。原因: JS のフォールバック表がサーバの状態名表と別に書かれ、学習者向けの出所ラベルの語が混ざっていた。

## 発見の観点

ボタンの語 × 状態名 × 2 つの表の読み合わせ（invariant_audit）と、状態名の表の棚卸し（inventory）。

## 解決の観点

正本を 1 つにして JS は逐語ミラーにした（canonical_source）。ミラーの一致を検査で固定した。

## 一般化

フォールバック表は正本が届かないときにだけ使われるため、食い違いが気づかれにくい。型は duplicate-canonical-sources。確かめ方は、正本の語を変えたときにミラーの一致検査が赤になることを見ること。
