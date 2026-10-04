---
id: IK-0639
title: "台帳候補の確定・見送りは教材の編集権限を要するが、コース所有者や担当の教員に編集権限が無いと操作が見えたまま 404 になる"
status: open
recorded_at: 2026-10-04
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "教員が学習者の共有した実行から立った台帳候補を確定・見送りする"
  layers: [simulation_bridge_sb, doubt_d, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [assignment]
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
    確認: 候補の確定・見送りは対象教材の編集権限で守られ、砂場ではコース所有者の教員も担当の教員も教材の編集権限が無く 404 になった。一方で画面は操作を出していた。接続軸 condition = 権限の条件が画面の操作の出し分けに届いていない。統制軸 assignment = 「担当教員」を教材の編集者とするかコース所有者を含めるかが未定義。処理軸 none。構造軸 none。
generalization:
  level: repo_pattern
  general_form: "操作の権限を判定する単位（教材）と利用者が担当する単位（コース）が異なり、担当者に操作が見えたまま拒否される"
pattern: permission-and-affordance-asymmetric
discovery:
  perspective: [reproduction, boundary_walk]
  note: "第 22 周の教員場面 (g') の準備で、コース所有者と担当の教員の両方が確定で 404 になった（運転では所有者の共有 API で編集権限を付けて回避）。"
resolution:
  perspective: [pending]
  note: "未解決（オーナー判断の候補）。推奨は、確定は教材の編集者のまま、権限の無い教員にはボタンを無効化して理由の事実文を出す（第 2 波で実装予定）。コース所有者にも認めるかは権限モデルの変更でオーナー判断。"
related: [IK-0623]
view_of: []
history: []
---

## 課題

症状: 担当の教員が候補を確定しようとすると 404 になる。原因: 確定の権限は教材の編集権限で、コースの担当とは別の単位で、画面はその条件を見ずに操作を出していた。

## 発見の観点

ペルソナの通し受講の準備（reproduction）で、権限の境界を歩いて見えた（boundary_walk）。

## 解決の観点

未解決。依頼の際は、指示の型 condition-not-propagated より、権限と操作の見え方の非対称（permission-and-affordance-asymmetric）が是正（無効化と理由の事実文）に直結するためこちらを採った。誰に確定を認めるかは人の権利の判断。

## 一般化

権限の単位と担当の単位が違うと、担当者に拒否される操作が見える。型は permission-and-affordance-asymmetric。確かめ方は、権限のある教員・ない教員の両方で画面を開き、見える操作と実行できる操作が一致することを見ること。
