---
id: IK-0335
title: 参照健全性の検査が「存在しない参照先」しか数えず、層が空・リンク自体が無い・上流で打ち切られた事実を ok と報告する
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
  - docs/features/knowledge_transfer_design.md
feature_context:
  realizing: 教材の構造化成果が壊れていないかを教員が一目で読めるようにする
  layers: [knowledge_transfer, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [none]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    統制軸: 「ok」の定義が「検査した 6 種の参照が全部解決」で、健全さの定義として狭い
    （完了・正常の定義が代理指標）。構造軸: 参照切れの検査と存在の検査を同じ計器で扱う
    分割になっておらず、後者が無かった。接続軸: 事実は各 stage_outputs に存在し、運ぶ経路が
    無いのではなく読む側の定義の問題なので none。処理軸: 要素なし。
generalization:
  level: general
  general_form: 計器の「正常」が検査した項目の範囲でしか定義されておらず、範囲外の欠落を正常と表示する
pattern: completion-defined-by-proxy
discovery:
  perspective: [data_inspection, boundary_walk]
  note: >-
    同じ run で export_validation が failed_validation・completeness が complete=false なのに
    reference_health が ok であることを並べ、検査種別の集合の狭さに行き着いた。
resolution:
  perspective: [explicit_contract, first_class_state]
  note: >-
    「検査した参照はすべて解決しています」と範囲どおりの文言にし、検査種別を 10 に増やし、
    空層・打ち切り・skip・完全性理由を別区画の事実文で出す（status の語彙は増やさない）。
    旧文言のスナップショットは未確認扱いで再検査へ落とす。
  landed_in:
    - backend/core/reference_health.py
    - backend/core/coverage_facts.py
    - backend/api/routes/admin.py
  verification:
    methods: [guardrail]
    unverified:
      - docker で組み上げた実機での E2E
related: [IK-0333, IK-0334]
view_of: []
history: []
---

## 課題

**症状**: 12/12 本で「参照の切れはありません。」。同時に detail ノードの親が全件空、
式リンク 0、学ぶ単位 0 の論文がある。

**原因**: DETAIL_KINDS が 6 種で、「無い」は検査対象外。

## 発見の観点

同一 run の 3 つの計器の矛盾（`data_inspection`）と、計器の境界を歩いた（`boundary_walk`）。

## 解決の観点

文言を範囲どおりに（`explicit_contract`）、存在の検査を一級の区画に（`first_class_state`）。

## 一般化

計器は「何を検査していないか」を言えなければ、検査範囲の外側を正常として塗りつぶす。
