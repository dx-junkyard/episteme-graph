---
id: IK-0415
title: "わたしの記録（GET /api/me/records）が status の open を種類を問わず「未解決」と表示し、誤解の記録や学習の意図まで未解決に見せ、系統には内部フラグ dead、来歴注記には「（実装予定）」の先取りが出る"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が自分の学習痕跡を種類ごとに一望する（主権台帳 v1）
  layers: [trace_registry]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: status_label を TRACE_STATUS_LABELS（status だけで決まる共通表）から引き、open を一律「未解決」にしていた（wording）。構造軸: kind ごとに open の意味が違う（誤解の記録 = 回答が訂正を示した AI 検出の記録 / 学習の意図 = 残してある / 軽量アンカー = 付けた印）のに、語彙表が (kind, status) の区別を持たなかった（representation）。接続軸: 登録簿の dead（書き込み経路が無い）という内部の意味が学習者 DTO へそのまま運ばれた（meaning。medium）。統制軸: 「実装予定」は TR5「先取りの約束を出さない」に反していた（none とした — 手続の不備ではなく文言。medium）。確認: 第 8 周の学生が「訂正されたはずの誤解も、ゼミの意図も未解決」「dead: true が何か分からない」「実装予定がよく分からない」と記録。
generalization:
  level: repo_pattern
  general_form: 状態語の日本語表を状態語だけで引き、種類によって違う意味を同じ訳語に潰す
pattern: same-name-different-referents
discovery:
  perspective: [reproduction, invariant_audit]
  note: 学生ペルソナが記録の一覧を読み、種類の違う記録が同じ「未解決」に並ぶのを記録した。
resolution:
  perspective: [vocabulary_table]
  note: >-
    label_vocab に kind 別の上書き表 TRACE_STATUS_LABELS_BY_KIND と学習の意図の役割ラベル INTENTION_ROLE_LABELS を足し、trace_ledger.status_label_for が上書き → 役割 → 共通表の順に引く。誤解の記録の open は「回答で訂正の指摘あり（あなたの確認前）」— 本人が確定する誤解メモ（是正 F5）とは別物なので「訂正済み」とは言わない。系統 DTO から dead を外し、書き込み経路の無い系統は行が無ければ出さず、行があれば事実文を添えて出す（P4）。登録簿に無い kind の見出しに内部名を出さない。来歴注記から「実装予定」を外し閉世界の事実文にした。旧行の予約 topic id の context_label は読み時に表示名へ（行は書き換えない）。item の id / kind は結合キーとして残した（表示されない）。
  landed_in:
    - backend/core/trace_ledger.py
    - backend/core/label_vocab.py
    - backend/tests/test_trace_ledger_core.py
    - docs/features/trace_registry_sovereignty_ledger_design.md
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 前提確認への返事（「はい、理解しています。」）が問いの痕跡として記録される経路（routes/learning.py・別担当）
related: [IK-0416]
view_of: []
history: []
---

## 課題

記録の種類が違っても open が全部「未解決」と読まれる。

## 発見の観点

実ペルソナの記録の一覧の読み（第 8 周）と TR5 の照合。

## 解決の観点

(kind, status) で訳語を引く語彙表を置く。

## 一般化

状態語の訳語を状態語だけで決めると、種類ごとの意味の違いが潰れる。
