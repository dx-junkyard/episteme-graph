---
id: IK-1141
title: "記録の有無を問う往復でも、台帳の事実が画面文脈に載らないと閉世界の拘束が掛からず、開発者指示の「一般知識で補う」が無い記録を埋める誘因になる"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/assistant_screen_adapter_design.md"
feature_context:
  realizing: "学習者が検証の記録の有無を尋ねたとき、AI がコーパスの記録の範囲だけで答える"
  layers: [rag_chat, screen_adapter_sa]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    LEARNING_VERIFICATION_OUTPUT_CONSTRAINT は台帳由来の事実がブロックへ実際に載ったときだけ system に足されていた。後継の決まらない部品・地図の場所だけの往復では台帳の行が無く、記録を問う発話でも拘束が掛からない。接続軸 condition（発話の種類が拘束の条件に渡らない）。
generalization:
  level: repo_pattern
  general_form: "出力側の拘束が材料の有無だけで決まり、問いの種類では掛からない"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    製品頭脳 A 4302（BRAIN_NOTES_product_A_r10.md）: 部品の検証の記録を尋ねた往復の system に拘束が無く、開発者指示の補完規則だけがあった。run uxsim/runs/c-r25-doubt-r10/20261031T053655Z-69809。
resolution:
  perspective: [carry_through]
  note: >-
    第 25 周 第 10 波（2026-10-31）: _asks_about_verification_record（検証・確かめられ・記録は/が・verified 等の決定論の語の一致）に当たる往復では、台帳の事実の有無にかかわらず LEARNING_VERIFICATION_OUTPUT_CONSTRAINT（第 9 波の優先 1 文を含む）を system に足す。設計書 SA層 §11.22。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave10_chat.py"
    - "docs/features/assistant_screen_adapter_design.md"
  verification:
    methods: [guardrail]
    unverified:
      - "実 LLM の往復で一般知識の補完が抑えられること（第 11 周の再演）"
      - "語の一致の取りこぼし（言い換えた問い）"
related: [IK-1115]
view_of: []
history: []
---

## 課題

記録の有無を問う往復でも、台帳の事実が画面文脈に載らないと閉世界の拘束が掛からず、開発者指示の「一般知識で補う」が無い記録を埋める誘因になる。

## 発見の観点

製品頭脳 A 4302（BRAIN_NOTES_product_A_r10.md）: 部品の検証の記録を尋ねた往復の system に拘束が無く、開発者指示の補完規則だけがあった。run uxsim/runs/c-r25-doubt-r10/20261031T053655Z-69809。

## 解決の観点

_asks_about_verification_record（検証・確かめられ・記録は/が・verified 等の決定論の語の一致）に当たる往復では、台帳の事実の有無にかかわらず LEARNING_VERIFICATION_OUTPUT_CONSTRAINT（第 9 波の優先 1 文を含む）を system に足す。設計書 SA層 §11.22。

## 一般化

出力側の拘束が材料の有無だけで決まり、問いの種類では掛からない。型 condition-not-propagated。
