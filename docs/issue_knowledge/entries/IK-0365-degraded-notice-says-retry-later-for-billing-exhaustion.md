---
id: IK-0365
title: LLM 提供元の課金残高切れ（恒常的な失敗）でも、コースビルダー・学習チャットの縮退文は「しばらくしてからもう一度」と一時的な障害の文面を返し、教員・学習者は待てば直ると読む
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: LLM を呼ぶ対話機能が失敗したときに事実を伝える
  layers: [course_builder, rag_chat, shared_infra]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [meaning]
    governance: [none]
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
    接続軸: 提供元の失敗種別（insufficient_quota / credit_balance_exhausted は恒常、timeout は一時）が縮退文に
    運ばれず、全て同じ文になる（meaning）。処理軸: 文面が「しばらくして」と一時性を断定する（wording）。
    構造軸: 縮退文は固定文 1 つで失敗の種類を表す表現が無い（representation。medium: 学習者には種別を出さず
    教員側の計器だけに出す設計もあり得る）。確認: run 20260927T033148Z のステップ 7〜12（course-builder chat）と
    34・50（document discuss）の応答 `degraded: true` の文面、同時刻の `llm_usage_events.error_type=RateLimitError`。
generalization:
  level: general
  general_form: 失敗の種類（一時／恒常）を捨てて 1 つの縮退文に畳むため、利用者が取るべき行動（待つ／管理者に知らせる）を誤る
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: 課金切れの状態でペルソナが台本どおりコースビルダーとチャットを叩いた応答を読んだ。
resolution:
  perspective: [explicit_contract]
  note: >-
    コースビルダー（教員向け）だけ、提供元の上限・残高の失敗（RateLimit / insufficient_quota / credit / billing）を判別して別の事実文を返す。学習者向けの文は変えない。
  landed_in:
    - backend/api/routes/admin.py
    - backend/tests/test_ik0365_provider_quota_message.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再現（残高ゼロの状態で course builder を叩いていない）
      - 一過性の RateLimit も同じ文になること
related: [IK-0362]
view_of: []
history: []
---

## 課題

課金切れでもコースビルダー等の縮退文は「しばらくしてからもう一度お試しください」。恒常的な失敗を一時的と読ませる。

## 発見の観点

台本モードの通し受講で縮退応答を読んだ。

## 解決の観点

未解決。失敗種別を教員側の文面・計器に運ぶ。

## 一般化

失敗の種類を表現できず 1 文に畳む型。
