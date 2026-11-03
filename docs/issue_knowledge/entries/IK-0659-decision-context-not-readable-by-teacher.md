---
id: IK-0659
title: "一括確定で何を見て何を選べたか（decision_context）を、教員が後から読む画面が無い"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/decision_context_design.md"
  - "docs/features/release_review_flow_design.md"
feature_context:
  realizing: "教員が公開後に、リリース前の確認でどの配置を見てどれを確認したかを振り返る"
  layers: [decision_context, release_review, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [review]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: decision_context は監査の metadata に記帳されるが、それを読む画面は無い（JS で読むのはリリース前の確認の一致判定だけ。教材管理の位置づけモーダルは一括確認のメモだけを出す）。接続軸 information = 記帳した提示・適用・代替・再審経路が画面まで届かない。統制軸 review = 再構成可能な手続（改訂原則1）の「後から読む」段が無い。処理軸 none。構造軸 none。
generalization:
  level: repo_pattern
  general_form: "後から再構成するために記録した文脈を、再構成する当事者が読む経路を持たない"
pattern: available-but-unwired
discovery:
  perspective: [invariant_audit]
  note: "第 23 周の机上レビューで、decision_context の設計（再構成・異議申立のための記帳）と、それを読む画面の有無を読み合わせて見えた。"
resolution:
  perspective: [pending]
  note: "未解決。記帳は既にあるので、位置づけの行か確認の履歴から監査の decision_context を事実文で読ませれば解ける。"
related: [IK-0649]
view_of: []
history: []
---

## 課題

症状: 一括確認した配置について、何が提示され何を開き何が選べたかを教員が画面で確かめられない。原因: 記帳は監査にだけあり、読む経路が無い。

## 発見の観点

設計書の目的（再構成可能な手続）× 画面の読み元の読み合わせ（invariant_audit）。

## 解決の観点

未解決。新しい記帳は要らず、既存の記帳を読む経路を足すだけで足りる見込み。

## 一般化

再構成のための記録は、当事者が読めなければ再構成の手続にならない。型は available-but-unwired。確かめ方は、一括確認の後に教員の画面から提示と適用の内容を読めることを見ること。
