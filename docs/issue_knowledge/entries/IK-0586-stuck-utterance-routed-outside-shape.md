---
id: IK-0586
title: "詰まりの短い発話（「まだわからない」「どこから読めばいい？」）が前提ゲートの否定・学習相談テンプレートに取られ、焦点論文と応答の骨格の外で長文になる"
status: open
recorded_at: 2026-10-01
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.9
feature_context:
  realizing: "ペルソナ通し受講 第 17 周（4 つの根の是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [target]
    governance: [ordering]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 第 17 周 s-stuck-scaffold で、短い「まだわからない」が前提ゲートの「いいえ」と解釈されて前提トピックの長い説明にすり替わり、「どこから読めばいい？」は学習相談の固定構成（【歓迎と目標】【構成要素】…）で別論文の概念とボタンまで並べた。根 4 の応答の骨格と根 2 の焦点論文は最終 return の RAG 応答だけが対象で、早期 return（学習相談・前提説明）は対象外（root_cause_consolidation §6）。経路の判定順（統制軸 ordering）と、学習相談の材料がコース全体であること（接続軸 target）が原因候補。前提ゲート後の否定の判定（IK-0560）の当たり方と、学習相談テンプレートの起動条件を確かめれば確定する。
generalization:
  level: repo_pattern
  general_form: "応答の形と対象の規則を主経路だけに置き、早期に返す分岐が同じ規則を通らない"
pattern: guardrail-does-not-cover-new-path
discovery:
  perspective: [reproduction, invariant_audit]
  note: "第 17 周の頭脳メモ（短い「わからない」ほど長い回答・テンプレ回答）を、根の統合記録の「早期 return は対象外」と突き合わせた。"
resolution:
  perspective: [responsibility_move, pending]
  note: '一部是正（第 18 波）。「か。」で終わる問いの検出と詰まりの区画（足場 1 つ・問い返しなし・前提ゲートは次の往復へ）をサーバが組み立て、学習相談の概念・トピックをトピック範囲に絞った。早期 return 経路で骨格が効くのは詰まりの区画だけで、他の区画（先に答える・確認・鏡）は未適用。'
  landed_in:
    - backend/core/dialogue_shape.py
    - backend/tests/test_dialogue_shape_stuck.py
    - backend/api/routes/learning.py
    - docs/features/dialogue_response_shape_design.md
    - 7b90670
    - 3123f2d
related: [IK-0582, IK-0580, IK-0560]
view_of: []
history:
  - date: '2026-10-01'
    field: resolution.landed_in
    from: '[]'
    to: '第 18 波の一部是正'
    reason: '詰まりの区画と学習相談のトピック範囲は入った。残り: 早期 return 経路の骨格は詰まりの区画のみ（先に答える・確認・鏡の区画は未適用）。（persona_enactment_testing_design.md §18.10）'
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'open'
    reason: '第 18 周の再演（st-05 s-stuck-scaffold）で、詰まりの 1 往復目は足場 1 つ・問い返しなし・前提ゲートなし（GONE）、ゲートは次の往復へ繰り延べられた。残り: ゲート表示中の「むずい」が前提トピックの長い説明になる・「どこから？」が学習相談の固定構成（【歓迎と目標】…）になる、は STILL。（persona_enactment_testing_design.md §18.11）'
---

## 課題

詰まった学習者が短く「わからない」と言うたびに回答が長くなり、一点の足場にならない。「どこから読めばいい？」には表示中の論文と無関係な概念とボタンが並ぶ。

## 発見の観点

第 17 周の頭脳メモ（短い「わからない」ほど長い回答・テンプレ回答）を、根の統合記録の「早期 return は対象外」と突き合わせた。

## 解決の観点

一部是正（7b90670・3123f2d）。詰まりの区画と学習相談のトピック範囲への絞り込みは入った。早期 return 経路の骨格は詰まりの区画のみで、残りは未解決。
原因は起票時の仮説のまま（classification.basis）で、是正後の再演で確かめる。

## 一般化

応答の形と対象の規則を主経路だけに置き、早期に返す分岐が同じ規則を通らない。
