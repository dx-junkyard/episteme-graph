---
id: IK-0314
title: 同じ事実の正本が複数あり、片方だけが更新されて食い違う
status: resolved
recorded_at: 2026-07-12
resolved_at: 2026-07-13
sources:
  - docs/architecture/consolidation_survey_2026-07.md
  - docs/architecture/agent_inventory_and_refactoring_2026-09-10.md §2.4
feature_context:
  realizing: 同じ判定・同じ定義を、複数の画面と経路で一致させたまま提供する
  layers: [shared_infra, migrations_db, status_notification]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [version]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「同じ事実（スキーマ定義・教材の状態・スライドの分割・k-匿名の基準・監査の
    記帳規則）に対して正本が複数あり、どれが正かを決めていないこと」。個々の実装は
    それぞれ妥当で、片方だけを直しても分裂そのものは残り再発するため構造。調査記録が
    「同じものが N 回」の一覧として、二重管理・同名別実装・並行実装を実測で列挙している。
    最も影響が大きい例は、全マイグレーションがファイルと起動時コードの二重管理で、
    新しい 1 本が片方に欠落していた（該当層が本番で機能しない可能性）。
generalization:
  level: repo_pattern
  general_form: 同じ事実の定義が複数箇所にあり、片方だけが更新されて挙動が食い違う
pattern: duplicate-canonical-sources
discovery:
  perspective: [inventory, doc_code_diff]
  note: >-
    機能とコードの対応を全件棚卸しし、「同じものが N 回」を数えた。二重管理の片方欠落は、
    ファイル一覧と起動時コードの差分を突き合わせて見つかった。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    正本を 1 つに決め、他は委譲・再エクスポート・薄いシムにする方針を取った（スキーマ定義は
    ファイル側、教材の状態は投影、スライド分割はサーバ側、k-匿名は共有モジュール、監査は
    共通ヘルパ）。ただし「同型に見えて別概念」のものは統合せず独立のまま残す判断もしており、
    重複の数だけで機械的に畳まない。正本へ戻る規律はテストで固定した。
  landed_in:
    - backend/core/migrations.py
    - backend/core/privacy.py
    - backend/core/status/projector.py
    - docs/architecture/consolidation_survey_2026-07.md
related: [IK-0315, IK-0321]
view_of: [IK-0222]
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.aggregation, connection.version]
    to: axes=processing=[none]; structure=[aggregation]; connection=[version]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は多様に出る。新しい層が本番で機能しない、教材の状態が一覧と詳細で食い違う、教員が
プレビューで見た分割と学習者に配信される分割が違う、同じ匿名化の基準が場所ごとに違う。

遡ると原因は 1 つで、**同じ事実の正本が複数ある**こと。スキーマ定義はファイル群と起動時
コードの二重管理、教材の状態は投影モジュールと独自の結合の二か所、スライド分割はサーバと
クライアントの並行実装、監査の記帳は共通ヘルパがあるのに個別実装が多数、という具合に、
どれが正かを決めないまま両方が育っていた。

## 発見の観点

機能とコードの対応を全件棚卸しし（`inventory`）、「同じものが N 回」を数えた。二重管理の
片方欠落は、ファイル一覧と起動時コードの突き合わせ（`doc_code_diff`）で
見つかった。症状が出ていない重複も同じ表に並べたことで、次に壊れる場所が予測できた。

## 解決の観点

正本を 1 つに決め、他は委譲・薄いシムにする（`canonical_source`）。ただし「同型に見えて
別概念」（別ドメインの権限、目的の違う判定）は統合せず独立のまま残した — 重複の数だけで
機械的に畳むと、かえって条件が絡まるため。正本へ戻る規律はテストで固定した
（`guardrail_fix`）。

## 一般化

正本の分裂は、片方だけが更新された瞬間に初めて症状として出るので、症状が出る前に数えるしか
ない。棚卸しの表を作り、「同じ事実か、似ているだけの別概念か」を 1 件ずつ判定する。
辞書の型 `duplicate-canonical-sources` に対応する。
