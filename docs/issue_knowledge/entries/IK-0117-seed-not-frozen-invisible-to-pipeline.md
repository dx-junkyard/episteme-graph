---
id: IK-0117
title: 供給側が下書きのまま凍結せず、凍結版だけを読む消費側から構造的に見えない
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-12
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12/E_concepts.md K-4
  - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 0 P0-7
  - docs/features/image_pipeline_knowledge_library_design.md
feature_context:
  realizing: 分野の共通部品ライブラリを、解析パイプラインの参照先として使えるようにする
  layers: [image_library_l, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract, version]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    供給側（同梱シードの取り込み）は下書きを作るだけ、消費側（パイプラインの検索）は凍結版だけを
    読む、という両立しない契約が原因。どちらの段も単体では設計どおりで、間の版の扱いが噛み合って
    いない。実データでエントリ 3 件に対し凍結版 0 件を確認し、それゆえ同一性リンクも標準化判定も
    起動しないことが分かった。
generalization:
  level: general
  general_form: >-
    供給側が出す状態と、消費側が読む状態の条件が噛み合わず、素材は存在するのに消費側から到達
    できない
pattern: available-but-unwired
discovery:
  perspective: [data_inspection, trace_walk]
  note: >-
    共通部品が 1 件も使われていない理由を、消費側の検索条件から供給側の取り込みまで辿った。
    件数（エントリ 3 / 凍結版 0）と検索条件（凍結版のみ）を並べた時点で確定した。
resolution:
  perspective: [explicit_contract, carry_through]
  note: >-
    「パイプラインが読むのは凍結版のみ」という条項は崩さず、供給側が取り込み直後に初版を凍結
    する形にした（既存の版ゼロの行は起動時に冪等でバックフィル）。消費側の条件を緩める案は、
    下書きが見えてしまうため採らなかった。
  landed_in:
    - backend/core/library/seed.py
    - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 0 実装記録
related: [IK-0116]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.contract, connection.version]
    to: axes=processing=[none]; structure=[none]; connection=[contract, version]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 共通部品ライブラリが一度も参照されない。ライブラリを前提にした下流（同一性リンク・
標準化の判定）が全部起動しない。

**原因**: シードの取り込みは下書きを作るところで止まり、パイプラインの検索は凍結版だけを読む。
両者の版に関する条件が噛み合っておらず、素材はあるのに到達できない状態だった。

## 発見の観点

「なぜ 1 件も使われないのか」を消費側の検索条件から供給側へ辿り（`trace_walk`）、件数を実データで
確かめた（`data_inspection`）。

## 解決の観点

条件を緩める（下書きも読む）と、確定していないものが下流へ流れるため、供給側が契約を満たす
ように直した（`explicit_contract`）。既存データは起動時の冪等なバックフィルで揃えた。

## 一般化

「作ったのに使われない」の多くは、供給側と消費側が見ている状態・版・可視性の条件が一段ずれて
いるだけで起きる。新しい供給経路を足すときは、消費側がどの状態を読むかを先に確認する。
