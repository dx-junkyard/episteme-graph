---
id: IK-0222
title: 同型のワークフローと判定が層ごとに再実装され、片方だけ更新されて食い違う
status: resolved
recorded_at: 2026-08-13
resolved_at: 2026-08-14
sources:
  - docs/architecture/feature_consolidation_proposals_2026-08-13.md §2-1
  - docs/architecture/feature_consolidation_proposals_2026-08-13.md §2-2
  - docs/architecture/feature_consolidation_proposals_2026-08-13.md §2-9
  - docs/architecture/doc_review_findings_2026-08-13.md §7
feature_context:
  realizing: 層を積み増しながら、候補から確定までの流れ・段階ラベル・ステージの性質を各層で扱う
  layers: [shared_infra, pipeline_a, doubt_d]
classification:
  primary: structure
  facets: [structure.aggregation, connection.meaning]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    各層の実装はそれぞれ単体で正しく、テストも通る。原因は、同じ関心（候補から確定への流れ・
    数値から段階ラベルへの変換・あるステージが外部を呼ぶかどうか）の正本が無く、層ごとに
    書き写されていること。個別に直しても次の層が同じものを書き写すため再発する。同じ語が
    場所によって別の意味になる分裂は副次の接続の問題。
generalization:
  level: repo_pattern
  general_form: >-
    同じ関心の判定・語彙・手順が層ごとに書き写され、片方だけ更新されて意味が分裂する
discovery:
  perspective: [inventory, doc_code_diff]
  note: >-
    層をまたいで同型の実装を全件棚卸しした。候補から確定への流れが八系統以上、数値から
    段階ラベルへの変換がサーバと画面の二重管理、あるステージが外部を呼ぶかどうかの判定が
    三〜四箇所で食い違っていた。食い違いは実害も出しており、同じ語が場所によって別の訳語・
    別の意味になっていた（ある語は「実験で確認」とも「原文に裏づけ」とも読める状態だった）。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    制御の流れだけを共通の部品に切り出し、語彙・粒度・引き金は各層に残す方針を採った
    （全部を共通化すると層ごとの意味の違いを潰すため）。段階ラベルは正本を作って委譲し、
    画面側に必要な表は逐語の写しとして一致をテストで固定した。ステージの性質は宣言から
    導出する形にし、三者の一致を検査で固定した。既存の系統の巻き取りは急がず、次の新しい
    系統から接続を義務づけるという段階的な着地にしている。
  landed_in:
    - backend/core/candidate_flow.py
    - backend/core/label_vocab.py
    - backend/core/document_pipeline/orchestrator.py
    - docs/features/candidate_flow_design.md
related: [IK-0218, IK-0220]
view_of: [IK-0314]
pattern: duplicate-canonical-sources
history: []
---

## 課題

**症状**: 同じ関心の実装が層の数だけ増えていた。①「候補を作り、人が確定し、却下も状態として
残し、監査に記帳する」という流れが八系統以上で個別に再実装 ②数値を段階ラベルへ変える表が
サーバと画面で二重管理され、同じ語の訳語が場所によって違う ③あるステージが外部の呼び出しを
行うかどうかの判定が三〜四箇所にあり食い違う（その集合に依存する機能まで巻き込む）。

**原因**: 呼び出しの共通基盤は整備されている一方、確定側のワークフロー・語彙表・ステージの
性質といった「判断の作法」には正本が無く、新しい層が既存の層から書き写して増える。書き写した
時点では正しいので、分裂は更新のたびに進む。

## 発見の観点

`inventory`（層をまたいだ同型実装の全件棚卸し）と `doc_code_diff`。一系統ずつ見ている限り
どれも妥当なので、横に並べて数えることでしか増殖は見えない。棚卸しの副産物として、同じ語が
場所によって別の意味になっている実害も表に出た。

## 解決の観点

`canonical_source` を制御の流れと語彙表に限って適用し、語彙・粒度・引き金は各層に残した
（全部を共通化すると層ごとの意味の違いを潰すため）。画面側に構造上必要な表は削除せず逐語の
写しとし、一致をテストで固定した（`guardrail_fix`）。既存系統の巻き取りは費用が見合わないため
行わず、次の新しい系統から接続を義務づける段階的な着地にしている。

## 一般化

層・モジュール・サービスを並べて増やす開発では、共通化されるのは「呼び出しの技術」であり
「判断の作法」は各所に書き写されやすい。辞書の型は `duplicate-canonical-sources`。
