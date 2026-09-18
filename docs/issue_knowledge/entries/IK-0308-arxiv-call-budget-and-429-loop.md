---
id: IK-0308
title: 外部 API を操作のたびに引き直し、制限を受けたあとも人の操作で窓が延び続ける
status: resolved
recorded_at: 2026-09-14
resolved_at: 2026-09-15
sources:
  - docs/features/paper_radar_design.md §14
  - docs/features/paper_discovery_design.md
feature_context:
  realizing: 外部の論文検索 API を使って候補を探しつつ、相手への問い合わせを最小に保つ
  layers: [paper_radar, paper_discovery]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [budget, resume]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「外部への問い合わせ回数に予算の設計が無く、画面を開き直すたび・操作するたびに
    取り直していたこと」。処理も表現も正しいが、いつ・何回呼ぶかの統制が無いため統制。
    設計書 §14.3 が操作ごとの呼び出し予算を表として定め、§14.1 が制限中に人が操作する
    たびにブロック窓が延びる循環を記録している。
generalization:
  level: general
  general_form: 外部資源への問い合わせに予算と抑制の設計が無く、失敗のたびの再試行が制限を長引かせる
pattern: external-budget-exceeded
discovery:
  perspective: [external_constraint, data_inspection]
  note: >-
    レート制限の事実文を足す作業（別件）の途中で、制限中に人が操作するほど窓が延びる
    循環が見えた。外部 API の制限という制約から、操作ごとの呼び出し回数を数え直した。
resolution:
  perspective: [order_and_budget, representation_change]
  note: >-
    ①外部が公開している事実（論文メタデータ）の写しを持ち、新鮮なら再取得しない
    ②制限を受けたあと一定時間は呼ばずに事実として返す（再試行ではなく抑制）。
    保存するのは外部事実だけで、候補・判断・失敗は保存しない。待ち時間や残り回数は
    画面に出さない。自動再試行・バックオフは「待つかどうかは人が決める」条項により採らない。
  landed_in:
    - backend/core/paper_discovery/metadata_cache.py
    - backend/core/paper_discovery/arxiv_client.py
    - backend/db/085_paper_discovery_arxiv_metadata_cache.sql
    - docs/features/paper_radar_design.md §14.4
related: [IK-0307, IK-0318]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.budget, governance.resume]
    to: axes=processing=[none]; structure=[none]; connection=[none]; governance=[budget,
      resume]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は「外部の論文 API から繰り返し制限を受け、レーダーが使えない」。

原因は、問い合わせ回数に予算の設計が無かったこと。モーダルを開き直すたびに起点論文の
メタデータを引き、出所の登録では同じ解決を二度引き、比較では候補の要旨を引き直していた。
さらに制限を受けたあとも呼び続けるため、人が操作するほど制限の窓が延びるという循環が
できていた。

## 発見の観点

外部 API の制限という制約から逆算し（`external_constraint`）、教員の一続きの操作で何回
出ていくかを操作ごとに数え直した（`data_inspection`）。制限の事実文を画面に届ける別件の
作業中に、その循環が見えた。

## 解決の観点

呼ばずに済ませる（外部事実の写しを持つ）と、制限後は呼ばない（抑制の窓を持つ）の二段で
統制した（`order_and_budget`）。写しに保存するのは外部が公開している事実だけで、候補・
判断・失敗は保存しない（保存対象の線引きを表現として明示した＝`representation_change`）。
自動再試行やバックオフは、待つかどうかを人が決めるという既存条項に反するため採らなかった。

## 一般化

外部資源（API・LLM・課金枠）を使う機能では、失敗時の振る舞いが失敗そのものを増幅しうる。
「1 操作あたり何回呼ぶか」を表として決め、失敗後は再試行ではなく抑制に倒す。
辞書の型 `external-budget-exceeded` に対応する。
