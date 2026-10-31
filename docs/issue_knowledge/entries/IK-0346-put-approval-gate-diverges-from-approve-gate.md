---
id: IK-0346
title: 承認ゲートの読み替えが approve 経路にしか入らず PUT 経路は旧ゲートのままで非対称が逆向きに復活し、equation_ids だけの出典に復元式が数えられていた
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §7.1
feature_context:
  realizing: パイプラインが作った component を、出典必須の弁を緩めずに教員が承認する
  layers: [theory_artifacts, graph_review, endorsement_c]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [condition]
    governance: [review]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: 同じ承認可能性の判定が _validate_for_review（PUT）と _component_approval_problems（approve）の2 箇所に実装され、読み替え（claim_ids / equation_ids を出典に数える・source_chunks を根拠 claim から導く）が片方にだけ入った。接続軸: equation_ids を出典に数える際に、式が復元由来か（equation_semantics artifact のconfidence_policy）という条件を参照していなかった。統制軸: 承認という人の確定手続の弁が経路で違った。確認手段: routes/theory_components.py の両関数のソース差。
generalization:
  level: repo_pattern
  general_form: 同じ判定を複数の経路が別々に実装しているため、判定の改訂が片方にだけ入り、経路によって結論が変わる
pattern: duplicate-canonical-sources
discovery:
  perspective: [adversarial_review, boundary_walk]
  note: >-
    第 1 波で approve 経路のゲートを広げた直後に、同じ判定を持つ PUT 経路を並べて読んだ。さらに「出典に数える式」の条件に復元由来が含まれていないことを式の信頼連鎖と突き合わせた。
resolution:
  perspective: [canonical_source, carry_through]
  note: >-
    出典キーの読み（_refs_present / _item_source_present）・根拠 claim からのチャンク導出・復元式の除外を共通の補助関数に寄せ、両経路が同じ読みを通る。復元式の ID は採用 run の equation_semantics artifact から引く（列は書かない）。
  landed_in:
    - backend/api/routes/theory_components.py
    - backend/tests/test_pipeline_component_approval.py
  verification:
    methods: [guardrail]
    unverified:
      - docker で組み上げた実機での E2E
related: [IK-0342, IK-0328]
view_of: []
history: []
---

## 課題

**症状**: approve からは承認できる component が PUT（原稿スタジオ）からは 422 になり、逆に復元式だけを出典に持つ項目が出典ありと数えられた。

**原因**: 判定が 2 箇所にあり読み替えが片方にだけ入った。復元由来の条件を出典判定が参照していなかった。

## 発見の観点

敵対的レビュー（`adversarial_review`）で 2 経路の境界を歩いた（`boundary_walk`）。

## 解決の観点

読みの正本を補助関数に一本化し（`canonical_source`）、復元由来の条件を判定まで運ぶ（`carry_through`）。

## 一般化

人の確定手続の弁は 1 つの関数に置き、入口ごとに写さない。弁を広げるときは「何を根拠に数えるか」の条件も同時に運ぶ。
