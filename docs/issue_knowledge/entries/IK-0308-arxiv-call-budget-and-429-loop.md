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
    structure: [representation]
    connection: [none]
    governance: [budget, resume]
  axis_confidence:
    processing: high
    structure: medium
    connection: low
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 個々の問い合わせは正しく組み立てられ、単一処理の不良は無い。

    構造: 外部が公開している事実の写しを持つ表現が無く、呼ばずに済ませる選択肢そのものが存在しなかった点が表現に当たる。予算のための手段とも読めるため中。

    接続: 同じ解決を二度引く重複はあるが、段階間で情報・条件・版が失われているわけではないため無いと判断した。重複を情報の落ちと読む余地は残る。

    統制: 一操作あたり何回呼ぶかの予算が無い点が予算に、制限を受けたあとも呼び続けて窓が延びる点が停止再開に当たる。
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
  - date: '2026-09-19'
    field: classification.axes
    from: axes=processing=[none]; structure=[none]; connection=[none]; governance=[budget, resume]
    to: axes=processing=[none]; structure=[representation]; connection=[none]; governance=[budget, resume]
    reason: 軸ごとの再判定で、外部事実の写しを持つ表現が無かった点を構造軸の要素として認めた
---

## 課題

症状は「外部の論文 API から繰り返し制限を受け、レーダーが使えない」。

原因は、問い合わせ回数に予算の設計が無かったこと。モーダルを開き直すたびに起点論文の
メタデータを引き、出所の登録では同じ解決を二度引き、比較では候補の要旨を引き直していた。
さらに制限を受けたあとも呼び続けるため、人が操作するほど制限の窓が延びるという循環が
できていた。

4 軸で見直すと、構造軸にも要素がある。外部が公開している事実の写しを持つ表現が無かったため、呼ばずに済ませるという選択肢が最初から存在しなかった。予算の表を決めるだけでは同じ回数に戻る。

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
